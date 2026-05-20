# card-provider-service/app/main.py
import asyncio
import logging
import uuid
import secrets
import os
import grpc
import hmac as hmac_lib
import hashlib
import time
import json
from datetime import datetime
from grpc import aio
from sqlalchemy import select
from cryptography.fernet import Fernet
import base64

from app import card_pb2_grpc
import app.card_pb2 as card_pb2
from app.database import AsyncSessionLocal, engine, Base, BANK_API_KEYS_SEED
from app.models import Card, CardType, CardStatus, CardStatusHistory, BankApiKey, Transaction, TransactionStatus
from iso8583 import encode, decode
from app.iso_spec import spec

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Czas aktywacji karty wirtualnej
VIRTUAL_CARD_ACTIVATION_DELAY = int(os.getenv("VIRTUAL_CARD_ACTIVATION_DELAY", "3600"))

SIGNATURE_MAX_AGE_SECONDS = 30

PAN_ENCRYPTION_KEY = os.getenv("PAN_ENCRYPTION_KEY", "karty-platnicze-key-2026")
CARD_VERIFICATION_KEY = os.getenv("CARD_VERIFICATION_KEY", "cvk-secret-key-2026")

# Maszyna stanów – dozwolone przejścia
ALLOWED_TRANSITIONS = {
    CardStatus.REQUESTED: [CardStatus.PRODUCING],
    CardStatus.PRODUCING: [CardStatus.SHIPPED],
    CardStatus.SHIPPED:   [CardStatus.ACTIVE, CardStatus.BLOCKED],
    CardStatus.ACTIVE:    [CardStatus.BLOCKED],
    CardStatus.BLOCKED:   [CardStatus.ACTIVE],
}


def get_fernet() -> Fernet:
    """Zwraca instancję Fernet do szyfrowania/deszyfrowania PAN."""
    key_bytes = PAN_ENCRYPTION_KEY.encode('utf-8')[:32].ljust(32, b'0')
    key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(key)


def encrypt_pan(full_pan: str) -> str:
    """Szyfruje pełny PAN przed zapisem do bazy."""
    return get_fernet().encrypt(full_pan.encode()).decode()


def decrypt_pan(encrypted_pan: str) -> str:
    """Odszyfrowuje PAN – tylko przy autoryzacji."""
    return get_fernet().decrypt(encrypted_pan.encode()).decode()


def generate_cvv(pan: str, expiry_month: int, expiry_year: int) -> str:
    """
    Generuje 3-cyfrowy CVV kryptograficznie.
    CVV nie jest przechowywany – odtwarzany przy weryfikacji z tego samego klucza.
    """
    expiry = f"{expiry_month:02d}{expiry_year:02d}"
    payload = f"{pan}{expiry}101"  # 101 = service code
    h = hmac_lib.new(
        CARD_VERIFICATION_KEY.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()
    digits = ''.join(filter(str.isdigit, h))
    return digits[:3].zfill(3)


def verify_cvv(pan: str, expiry_month: int, expiry_year: int, cvv_input: str) -> bool:
    """Weryfikuje CVV bez przechowywania go w bazie."""
    expected = generate_cvv(pan, expiry_month, expiry_year)
    return hmac_lib.compare_digest(expected, cvv_input)


def luhn_checksum(number: str) -> int:
    """Oblicza sumę kontrolną algorytmu Luhna."""
    digits = [int(d) for d in number]
    odd_digits = digits[-1::-2]
    even_digits = digits[-2::-2]
    total = sum(odd_digits)
    for d in even_digits:
        total += sum(divmod(d * 2, 10))
    return total % 10


def generate_pan(bin_prefix: str) -> tuple[str, str]:
    """
    Generuje poprawny 16-cyfrowy PAN:
    BIN(6) + środkowe(9) + cyfra_Luhna(1)
    Zwraca (full_pan, masked_pan).
    """
    middle = f"{secrets.randbelow(1_000_000_000):09d}"
    partial = bin_prefix + middle
    check = luhn_checksum(partial + "0")
    luhn_digit = (10 - check) % 10
    full_pan = partial + str(luhn_digit)
    masked_pan = f"{bin_prefix[:4]} {bin_prefix[4:]}** **** {full_pan[-4:]}"
    return full_pan, masked_pan


async def generate_unique_pan(bin_prefix: str, db) -> tuple[str, str]:
    """Generuje unikalny PAN – sprawdza kolizje w bazie. Max 10 prób."""
    for attempt in range(10):
        full_pan, masked_pan = generate_pan(bin_prefix)
        encrypted = encrypt_pan(full_pan)
        existing = await db.execute(
            select(Card).where(Card.pan_encrypted == encrypted)
        )
        if not existing.scalar_one_or_none():
            return full_pan, masked_pan
        logger.warning(f"PAN collision on attempt {attempt + 1}, retrying...")
    raise RuntimeError("Failed to generate unique PAN after 10 attempts")


def generate_expiry() -> tuple[int, int]:
    """Generuje datę ważności – 3 lata od teraz."""
    now = datetime.utcnow()
    expiry_year = (now.year + 3) % 100  # np. 2026 → 26
    expiry_month = now.month
    return expiry_month, expiry_year


def generate_token() -> str:
    return f"tok_{secrets.token_hex(16)}"


async def auto_activate_virtual_card(card_id: str, delay: int):
    """
    Automatyczna aktywacja karty wirtualnej po określonym czasie.
    W produkcji: 3600s (1h). W dev: 10s (zmienna VIRTUAL_CARD_ACTIVATION_DELAY).
    """
    logger.info(f"Virtual card {card_id} will auto-activate in {delay}s")
    await asyncio.sleep(delay)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Card).where(Card.id == uuid.UUID(card_id)))
        card = result.scalar_one_or_none()
        if card and card.status == CardStatus.REQUESTED:
            card.status = CardStatus.ACTIVE
            card.activated_at = datetime.utcnow()
            db.add(CardStatusHistory(
                card_id=card.id,
                old_status=CardStatus.REQUESTED.value,
                new_status=CardStatus.ACTIVE.value,
                changed_by="system_auto_activation",
            ))
            await db.commit()
            logger.info(f"Virtual card {card_id} auto-activated after {delay}s")


async def get_bank_by_api_key(db, api_key: str) -> BankApiKey | None:
    result = await db.execute(
        select(BankApiKey).where(
            BankApiKey.api_key == api_key,
            BankApiKey.is_active == True
        )
    )
    return result.scalar_one_or_none()


def verify_hmac_signature(
        body: dict,
        signature: str,
        timestamp: str,
        secret: str
) -> tuple[bool, str]:
    """
    Weryfikuje podpis HMAC-SHA256 żądania.

    Zwraca (True, "") jeśli OK lub (False, "powód") jeśli błąd.

    Algorytm:
    1. Sprawdź czy timestamp nie jest za stary (max 30s)
    2. Zbuduj payload: timestamp + JSON body (bez spacji)
    3. Oblicz HMAC-SHA256 z sekretem banku
    4. Porównaj z podpisem z nagłówka
    """
    # Weryfikacja timestamp - replay protection
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        return False, "Invalid timestamp format"

    age = abs(time.time() - ts)
    if age > SIGNATURE_MAX_AGE_SECONDS:
        return False, f"Request expired ({int(age)}s old, max {SIGNATURE_MAX_AGE_SECONDS}s)"

    # Buduj payload identycznie jak bank
    try:
        body_json = json.dumps(body, separators=(',', ':'), sort_keys=True)
    except Exception:
        return False, "Failed to serialize body"

    payload = timestamp + body_json

    # Oblicz oczekiwany podpis
    expected = hmac_lib.new(
        secret.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    # Bezpieczne porównanie (timing-safe)
    if not hmac_lib.compare_digest(expected, signature):
        return False, "Invalid signature"

    return True, ""

async def seed_bank_api_keys():
    """Wstawia predefiniowane klucze API banków jeśli nie istnieją."""
    async with AsyncSessionLocal() as db:
        for seed in BANK_API_KEYS_SEED:
            existing = await db.execute(
                select(BankApiKey).where(BankApiKey.bank_id == seed["bank_id"])
            )
            if not existing.scalar_one_or_none():
                db.add(BankApiKey(**seed))
                logger.info(f"Seeded API key for {seed['bank_id']}")
        await db.commit()

class CardProviderServicer(card_pb2_grpc.CardProviderServicer):
    
    async def CreateCard(self, request, context):
        """
        Tworzy nową kartę płatniczą.

        Logika per typ:
        - VIRTUAL:  startuje jako REQUESTED, automatycznie aktywuje się po max 1h
        - PHYSICAL: startuje jako REQUESTED, wymaga ręcznego przejścia przez cykl
        - PREPAID:  startuje jako REQUESTED, wymaga ręcznego przejścia przez cykl,
                    posiada własne saldo (initial_balance)
        """
        logger.info(f"CreateCard: user={request.user_id} type={request.card_type}")

        if request.card_type not in ("VIRTUAL", "PHYSICAL", "PREPAID"):
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT,
                                f"Unknown card_type: {request.card_type}")
            return

        async with AsyncSessionLocal() as db:
            # Krok 1 – weryfikacja klucza API
            bank = await get_bank_by_api_key(db, request.api_key)
            if not bank:
                await context.abort(grpc.StatusCode.UNAUTHENTICATED,
                                    "Invalid or inactive bank API key")
                return

            # Krok 2 – weryfikacja podpisu HMAC
            body_dict = {
                "user_id": request.user_id,
                "account_id": request.account_id,
                "card_type": request.card_type,
                "initial_balance": request.initial_balance,
            }
            valid, reason = verify_hmac_signature(
                body=body_dict,
                signature=request.signature,
                timestamp=request.timestamp,
                secret=bank.hmac_secret,
            )
            if not valid:
                logger.warning(f"HMAC verification failed for bank {bank.bank_id}: {reason}")
                await context.abort(grpc.StatusCode.UNAUTHENTICATED,
                                    f"Signature verification failed: {reason}")
                return

            logger.info(f"Bank {bank.bank_id} authenticated successfully")

            full_pan, masked_pan = await generate_unique_pan(bank.bin_prefix, db)
            expiry_month, expiry_year = generate_expiry()
            cvv = generate_cvv(full_pan, expiry_month, expiry_year)

            card = Card(
                id=uuid.uuid4(),
                user_id=request.user_id,
                account_id=request.account_id,
                bank_id=bank.bank_id,
                token=generate_token(),
                masked_pan=masked_pan,
                pan_encrypted=encrypt_pan(full_pan),
                expiry_month=expiry_month,
                expiry_year=expiry_year,
                card_type=CardType(request.card_type),
                status=CardStatus.REQUESTED,
                balance=float(request.initial_balance) if request.card_type == "PREPAID" else 0,
                daily_limit=1000.00,
                created_at=datetime.utcnow(),
                activated_at=None,
            )
            db.add(card)
            db.add(CardStatusHistory(
                card_id=card.id,
                old_status=None,
                new_status=CardStatus.REQUESTED.value,
                changed_by="system",
            ))
            await db.commit()
            await db.refresh(card)

            # Karta wirtualna: automatyczna aktywacja
            if request.card_type == "VIRTUAL":
                asyncio.create_task(
                    auto_activate_virtual_card(str(card.id), VIRTUAL_CARD_ACTIVATION_DELAY)
                )
                logger.info(f"Virtual card scheduled for auto-activation in {VIRTUAL_CARD_ACTIVATION_DELAY}s")

            logger.info(f"Card created: token={card.token} type={card.card_type} status={card.status}")

            return card_pb2.CreateCardResponse(
                card_token=card.token,
                masked_pan=card.masked_pan,
                full_pan=full_pan,
                cvv=cvv,
                expiry_month=expiry_month,
                expiry_year=expiry_year,
                status=card.status.value,
                card_type=card.card_type.value,
                bank_id=bank.bank_id,
            )

    async def GetCardStatus(self, request, context):
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Card).where(Card.token == request.card_token))
            card = result.scalar_one_or_none()
            if not card:
                await context.abort(grpc.StatusCode.NOT_FOUND, "Card not found")
                return
            return card_pb2.CardDetails(
                card_token=card.token,
                status=card.status.value,
                card_type=card.card_type.value,
                balance=float(card.balance),
                daily_limit=float(card.daily_limit),
                masked_pan=card.masked_pan,
                bank_id=card.bank_id,
                expiry_month=card.expiry_month,
                expiry_year=card.expiry_year,
            )

    async def ListCards(self, request, context):
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Card))
            cards = result.scalars().all()
            return card_pb2.ListCardsResponse(cards=[
                card_pb2.CardDetails(
                    card_token=c.token,
                    status=c.status.value,
                    card_type=c.card_type.value,
                    balance=float(c.balance),
                    daily_limit=float(c.daily_limit),
                    masked_pan=c.masked_pan,
                    bank_id=c.bank_id,
                    expiry_month=c.expiry_month,
                    expiry_year=c.expiry_year,
                ) for c in cards
            ])

    async def UpdateCardStatus(self, request, context):
        """
        Przesuwa kartę przez cykl życia (dla operatora banku).
        Dozwolone przejścia: REQUESTED→PRODUCING→SHIPPED
        """
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Card).where(Card.token == request.card_token))
            card = result.scalar_one_or_none()
            if not card:
                return card_pb2.UpdateCardStatusResponse(
                    success=False, message="Card not found", current_status=""
                )
            try:
                new_status = CardStatus(request.new_status)
            except ValueError:
                return card_pb2.UpdateCardStatusResponse(
                    success=False,
                    message=f"Unknown status: {request.new_status}",
                    current_status=card.status.value
                )
            allowed = ALLOWED_TRANSITIONS.get(card.status, [])
            if new_status not in allowed:
                return card_pb2.UpdateCardStatusResponse(
                    success=False,
                    message=f"Transition {card.status.value} -> {new_status.value} not allowed. Allowed: {[s.value for s in allowed]}",
                    current_status=card.status.value
                )
            old_status = card.status.value
            card.status = new_status
            db.add(CardStatusHistory(
                card_id=card.id,
                old_status=old_status,
                new_status=new_status.value,
                changed_by=request.changed_by or "bank_operator",
            ))
            await db.commit()
            logger.info(f"Card {card.token}: {old_status} -> {new_status.value}")
            return card_pb2.UpdateCardStatusResponse(
                success=True,
                message=f"Status updated: {old_status} -> {new_status.value}",
                current_status=new_status.value
            )

    async def ActivateCard(self, request, context):
        """
        Aktywuje kartę fizyczną/prepaid (symulacja aktywacji przez klienta w aplikacji banku).
        Karta musi być w statusie SHIPPED.
        """
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Card).where(Card.token == request.card_token))
            card = result.scalar_one_or_none()
            if not card:
                return card_pb2.ActivateCardResponse(success=False, message="Card not found")
            if card.status != CardStatus.SHIPPED:
                return card_pb2.ActivateCardResponse(
                    success=False,
                    message=f"Cannot activate. Status is {card.status.value}, must be SHIPPED"
                )
            card.status = CardStatus.ACTIVE
            card.activated_at = datetime.utcnow()
            db.add(CardStatusHistory(
                card_id=card.id,
                old_status=CardStatus.SHIPPED.value,
                new_status=CardStatus.ACTIVE.value,
                changed_by=request.activated_by or "customer",
            ))
            await db.commit()
            return card_pb2.ActivateCardResponse(
                success=True, message="Card activated successfully. Ready for payments."
            )

    async def TopUpPrepaid(self, request, context):
        """
        Doładowanie karty prepaid. Tylko dla kart typu PREPAID w statusie ACTIVE.
        """
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Card).where(Card.token == request.card_token))
            card = result.scalar_one_or_none()
            if not card:
                return card_pb2.TopUpResponse(success=False, message="Card not found", new_balance=0)
            if card.card_type != CardType.PREPAID:
                return card_pb2.TopUpResponse(
                    success=False, message="Only PREPAID cards can be topped up", new_balance=float(card.balance)
                )
            if card.status != CardStatus.ACTIVE:
                return card_pb2.TopUpResponse(
                    success=False, message=f"Card is {card.status.value}, must be ACTIVE", new_balance=float(card.balance)
                )
            if request.amount <= 0:
                return card_pb2.TopUpResponse(
                    success=False, message="Amount must be positive", new_balance=float(card.balance)
                )
            card.balance = float(card.balance) + request.amount
            await db.commit()
            logger.info(f"Prepaid card {card.token} topped up by {request.amount}. New balance: {card.balance}")
            return card_pb2.TopUpResponse(
                success=True,
                message=f"Top-up successful. Added {request.amount} {request.currency}",
                new_balance=float(card.balance)
            )

    async def BlockCard(self, request, context):
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Card).where(Card.token == request.card_token))
            card = result.scalar_one_or_none()
            if not card:
                return card_pb2.BlockCardResponse(success=False, message="Card not found")
            old_status = card.status.value
            card.status = CardStatus.BLOCKED
            db.add(CardStatusHistory(
                card_id=card.id,
                old_status=old_status,
                new_status=CardStatus.BLOCKED.value,
                changed_by=request.reason or "admin",
            ))
            await db.commit()
            return card_pb2.BlockCardResponse(success=True, message="Card blocked")

    async def UnblockCard(self, request, context):
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Card).where(Card.token == request.card_token))
            card = result.scalar_one_or_none()
            if not card:
                return card_pb2.UnblockCardResponse(success=False, message="Card not found")
            old_status = card.status.value
            card.status = CardStatus.ACTIVE
            db.add(CardStatusHistory(
                card_id=card.id,
                old_status=old_status,
                new_status=CardStatus.ACTIVE.value,
                changed_by="admin",
            ))
            await db.commit()
            return card_pb2.UnblockCardResponse(success=True, message="Card unblocked")

    async def AuthorizeTransaction(self, request, context):
        """
        Autoryzacja płatności kartą POS.
        """

        try:
            async with AsyncSessionLocal() as db:

                result = await db.execute(select(Card))
                cards = result.scalars().all()

                card = None
                for c in cards:
                    full_pan = decrypt_pan(c.pan_encrypted)

                    if full_pan == request.card_number:
                        card = c
                        break

                if not card:
                    return card_pb2.AuthorizationResponse(
                        response_code=1,
                        message="Card not found"
                    )

                full_pan = decrypt_pan(card.pan_encrypted)

                if not verify_cvv(
                    full_pan,
                    request.expiry_month,
                    request.expiry_year,
                    request.cvv
                ):
                    return card_pb2.AuthorizationResponse(
                        response_code=1,
                        message="Invalid CVV"
                    )

                authorization_code = secrets.token_hex(4).upper()

                return card_pb2.AuthorizationResponse(
                    authorization_code=authorization_code,
                    response_code=0,
                    message="Approved",
                    transaction_id="txn_test"
                )

        except Exception as e:
            import traceback
            traceback.print_exc()

            return card_pb2.AuthorizationResponse(
                response_code=1,
                message=str(e)
            )
    async def ProcessIsoMessage(self, request, context):

        decoded, _ = decode(
            bytes(request.payload),
            spec
        )

        card_number = decoded["2"]

        amount = int(decoded["4"]) / 100

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

            # karta nie istnieje
            if not card:
                response_code = "05"

            else:
                full_pan = decrypt_pan(card.pan_encrypted)

                # CVV
                if not verify_cvv(
                    full_pan,
                    expiry_month,
                    expiry_year,
                    cvv
                ):
                    response_code = "05"

                # expiry
                elif (
                    card.expiry_month != expiry_month or
                    card.expiry_year != expiry_year
                ):
                    response_code = "54"

                # status
                elif card.status != CardStatus.ACTIVE:
                    response_code = "05"

                # approved
                else:
                    response_code = "00"

        response_iso = {
            "t": "0110",
            "39": response_code,
            "38": secrets.token_hex(3).upper()
        }

        raw_response, _ = encode(response_iso, spec)

        return card_pb2.IsoResponse(
            payload=bytes(raw_response)
        )

    async def GetFullPan(self, request, context):
        """Zwraca odszyfrowany PAN – tylko dla admina w celach testowych."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Card).where(Card.token == request.card_token))
            card = result.scalar_one_or_none()
            if not card:
                await context.abort(grpc.StatusCode.NOT_FOUND, "Card not found")
                return
            if not card.pan_encrypted:
                await context.abort(grpc.StatusCode.NOT_FOUND, "PAN not available")
                return
            full_pan = decrypt_pan(card.pan_encrypted)
            cvv = generate_cvv(full_pan, card.expiry_month, card.expiry_year)
            return card_pb2.FullPanResponse(
                card_token=card.token,
                full_pan=full_pan,
                masked_pan=card.masked_pan,
                cvv=cvv,
                expiry_month=card.expiry_month,
                expiry_year=card.expiry_year,
            )

async def serve():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await seed_bank_api_keys()
    server = aio.server()
    card_pb2_grpc.add_CardProviderServicer_to_server(CardProviderServicer(), server)
    server.add_insecure_port('[::]:50051')
    logger.info("Card Provider Service RUNNING on port 50051 (gRPC)")
    await server.start()
    await server.wait_for_termination()


from app.iso_socket_server import start_socket_server


async def run_all():
    await asyncio.gather(
        serve(),
        start_socket_server()
    )


if __name__ == '__main__':
    asyncio.run(run_all())
#
#{
#  "card_number": "4100013395241296",
#  "expiry_month": 5,
#  "expiry_year": 29,
#  "cvv": "889",
#  "amount": 50,
#  "currency": "PLN",
#  "merchant_id": "SHOP_001",
#  "merchant_name": "Zabka"
#}
