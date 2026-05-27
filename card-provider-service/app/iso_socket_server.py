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

from decimal import Decimal

from app.models import (
    Card,
    CardStatus,
    Transaction,
    TransactionStatus
)

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
        length_bytes = await reader.readexactly(4)

        message_length = int.from_bytes(
            length_bytes,
            "big"
        )

        data = await reader.readexactly(
            message_length
        )

        print("RAW SOCKET DATA:", data)

        decoded, _ = decode(
            bytes(data),
            spec
        )

        print("DECODED ISO:", decoded)

        authorization_code = ""

        card_number = decoded["2"]

        amount = (
            Decimal(decoded["4"])
            / Decimal("100")
        )

        expiry = decoded["14"]
        expiry_month = int(expiry[:2])
        expiry_year = int(expiry[2:])

        cvv = decoded["52"]

        async with AsyncSessionLocal() as db:

            result = await db.execute(select(Card))
            cards = result.scalars().all()

            card = None

            try:
                for c in cards:

                    print("CHECK CARD:", c.id)

                    if not c.pan_encrypted:
                        print(
                            "CARD WITHOUT PAN:",
                            c.id
                        )
                        continue

                    print(
                        "PAN ENCRYPTED:",
                        c.pan_encrypted
                    )

                    full_pan = decrypt_pan(
                        c.pan_encrypted
                    )

                    print(
                        "FULL PAN:",
                        full_pan
                    )

                    if full_pan == card_number:
                        card = c
                        print("CARD MATCH")
                        break

            except Exception:
                import traceback

                print("CARD LOOP ERROR")
                traceback.print_exc()

            if not card:
                response_code = "05"

            else:

                full_pan = decrypt_pan(
                    card.pan_encrypted
                )

                available_balance = (
                    Decimal(str(card.balance))
                    - Decimal(
                        str(card.held_balance)
                    )
                )

                print(
                    "AVAILABLE:",
                    available_balance
                )

                print(
                    "AMOUNT:",
                    amount
                )

                if not verify_cvv(
                    full_pan,
                    expiry_month,
                    expiry_year,
                    cvv
                ):
                    response_code = "05"

                elif (
                    card.expiry_month
                    != expiry_month
                    or
                    card.expiry_year
                    != expiry_year
                ):
                    response_code = "54"

                elif (
                    card.status
                    != CardStatus.ACTIVE
                ):
                    response_code = "05"

                elif (
                    available_balance
                    < amount
                ):
                    response_code = "51"

                else:
                    response_code = "00"

                    authorization_code = (
                        secrets
                        .token_hex(3)
                        .upper()
                    )

                    card.held_balance = (
                        Decimal(
                            str(
                                card.held_balance
                            )
                        )
                        + amount
                    )

                    transaction = Transaction(
                        card_id=card.id,
                        merchant_id=
                            decoded["42"]
                            .strip(),

                        merchant_name=
                            decoded["42"]
                            .strip(),

                        amount=amount,

                        currency=
                            decoded.get(
                                "49",
                                "PLN"
                            ),

                        status=
                            TransactionStatus
                            .PENDING,

                        authorization_code=
                            authorization_code
                    )

                    db.add(transaction)

                    await db.commit()

                    print(
                        "AUTHORIZED"
                    )

                    print(
                        "HELD:",
                        card.held_balance
                    )

        response_iso = {
            "t": "0110",
            "39": response_code,
            "38":
                authorization_code
                if response_code == "00"
                else "000000"
        }

        raw_response, _ = encode(
            response_iso,
            spec
        )

        response_payload = bytes(
            raw_response
        )

        response_length = len(
            response_payload
        ).to_bytes(4, "big")

        writer.write(
            response_length
            + response_payload
        )

        await writer.drain()

    except Exception as e:
        import traceback

        print("SOCKET ERROR")
        traceback.print_exc()

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