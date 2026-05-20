import asyncio
import secrets

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Card, CardStatus
from app.iso_spec import spec
from iso8583 import decode, encode
import hmac as hmac_lib
import hashlib
import base64
import os

from cryptography.fernet import Fernet


HOST = "0.0.0.0"
PORT = 9000

PAN_ENCRYPTION_KEY = os.getenv(
    "PAN_ENCRYPTION_KEY",
    "karty-platnicze-key-2026"
)

CARD_VERIFICATION_KEY = os.getenv(
    "CARD_VERIFICATION_KEY",
    "cvk-secret-key-2026"
)


def get_fernet() -> Fernet:
    key_bytes = PAN_ENCRYPTION_KEY.encode("utf-8")[:32].ljust(32, b"0")
    key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(key)


def decrypt_pan(encrypted_pan: str) -> str:
    return get_fernet().decrypt(
        encrypted_pan.encode()
    ).decode()


def generate_cvv(
    pan: str,
    expiry_month: int,
    expiry_year: int
) -> str:

    expiry = f"{expiry_month:02d}{expiry_year:02d}"

    payload = f"{pan}{expiry}101"

    h = hmac_lib.new(
        CARD_VERIFICATION_KEY.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()

    digits = ''.join(filter(str.isdigit, h))

    return digits[:3].zfill(3)


def verify_cvv(
    pan: str,
    expiry_month: int,
    expiry_year: int,
    cvv_input: str
) -> bool:

    expected = generate_cvv(
        pan,
        expiry_month,
        expiry_year
    )

    return hmac_lib.compare_digest(
        expected,
        cvv_input
    )
async def handle_client(reader, writer):

    try:
        data = await reader.read(4096)

        print("RAW SOCKET DATA:", data)

        decoded, _ = decode(bytes(data), spec)

        print("DECODED ISO:", decoded)

        card_number = decoded["2"]

        expiry = decoded["14"]
        expiry_month = int(expiry[:2])
        expiry_year = int(expiry[2:])

        cvv = decoded["52"]

        async with AsyncSessionLocal() as db:

            result = await db.execute(select(Card))
            cards = result.scalars().all()

            card = None

            for c in cards:
                full_pan = decrypt_pan(c.pan_encrypted)

                if full_pan == card_number:
                    card = c
                    break

            if not card:
                response_code = "05"

            else:
                full_pan = decrypt_pan(card.pan_encrypted)

                if not verify_cvv(
                    full_pan,
                    expiry_month,
                    expiry_year,
                    cvv
                ):
                    response_code = "05"

                elif card.status != CardStatus.ACTIVE:
                    response_code = "05"

                else:
                    response_code = "00"

        response_iso = {
            "t": "0110",
            "39": response_code,
            "38": secrets.token_hex(3).upper()
        }

        raw_response, _ = encode(response_iso, spec)

        writer.write(bytes(raw_response))
        await writer.drain()

    except Exception as e:
        print("SOCKET ERROR:", str(e))

    finally:
        writer.close()
        await writer.wait_closed()


async def start_socket_server():

    server = await asyncio.start_server(
        handle_client,
        HOST,
        PORT
    )

    print(f"ISO SOCKET SERVER RUNNING {HOST}:{PORT}")

    async with server:
        await server.serve_forever()