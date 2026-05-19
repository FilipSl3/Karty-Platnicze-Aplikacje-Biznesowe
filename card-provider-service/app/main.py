# card-provider-service/app/main.py
import asyncio
import logging
import uuid
import secrets
import os
import grpc
from datetime import datetime
from grpc import aio
from sqlalchemy import select

from app import card_pb2_grpc
import app.card_pb2 as card_pb2
from app.database import AsyncSessionLocal, engine, Base, BANK_API_KEYS_SEED
from app.models import Card, CardType, CardStatus, CardStatusHistory, BankApiKey, Transaction, TransactionStatus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Czas aktywacji karty wirtualnej
VIRTUAL_CARD_ACTIVATION_DELAY = int(os.getenv("VIRTUAL_CARD_ACTIVATION_DELAY", "3600"))

# Maszyna stanów – dozwolone przejścia
ALLOWED_TRANSITIONS = {
    CardStatus.REQUESTED: [CardStatus.PRODUCING],
    CardStatus.PRODUCING: [CardStatus.SHIPPED],
    CardStatus.SHIPPED:   [CardStatus.ACTIVE, CardStatus.BLOCKED],
    CardStatus.ACTIVE:    [CardStatus.BLOCKED],
    CardStatus.BLOCKED:   [CardStatus.ACTIVE],
}


def generate_token() -> str:
    return f"tok_{secrets.token_hex(16)}"


def generate_masked_pan(bin_prefix: str) -> str:
    last4 = secrets.randbelow(9999)
    return f"{bin_prefix} **** **** {last4:04d}"

def generate_authorization_code() -> str:
    return f"AUTH-{secrets.token_hex(4).upper()}"

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
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"Unknown card_type: {request.card_type}")
            return


        async with AsyncSessionLocal() as db:
            # Weryfikacja klucza API banku
            bank = await get_bank_by_api_key(db, request.api_key)
            if not bank:
                await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid or inactive bank API key")
                return


            card = Card(
                id=uuid.uuid4(),
                user_id=request.user_id,
                account_id=request.account_id,
                bank_id=bank.bank_id,
                token=generate_token(),
                masked_pan=generate_masked_pan(bank.bin_prefix),
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
        Autoryzacja płatności kartą.
        """

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Card).where(
                    Card.token == request.card_token
                )
            )

            card = result.scalar_one_or_none()

            # 1. karta istnieje?
            if not card:
                return card_pb2.AuthorizationResponse(
                    response_code=card_pb2.AuthorizationResponse.DECLINED,
                    message="Card not found"
                )

            # 2. karta aktywna?
            if card.status == CardStatus.BLOCKED:
                return card_pb2.AuthorizationResponse(
                    response_code=card_pb2.AuthorizationResponse.CARD_BLOCKED,
                    message="Card is blocked"
                )

            if card.status != CardStatus.ACTIVE:
                return card_pb2.AuthorizationResponse(
                    response_code=card_pb2.AuthorizationResponse.DECLINED,
                    message=f"Card status is {card.status.value}"
                )

            # 3. limit dzienny
            if request.amount > float(card.daily_limit):
                return card_pb2.AuthorizationResponse(
                    response_code=card_pb2.AuthorizationResponse.LIMIT_EXCEEDED,
                    message="Daily limit exceeded"
                )

            # 4. prepaid balance
            if card.card_type == CardType.PREPAID:
                available_balance = (
                    float(card.balance) - float(card.held_balance)
                    )
                if available_balance < request.amount:
                    return card_pb2.AuthorizationResponse(
                        response_code=card_pb2.AuthorizationResponse.INSUFFICIENT_FUNDS,
                        message="Insufficient funds"
                    )

                # blokada środków
                card.held_balance = (
                    float(card.held_balance) + request.amount
                    )
            # 5. utworzenie transakcji
            authorization_code = generate_authorization_code()

            transaction = Transaction(
                id=uuid.uuid4(),
                card_id=card.id,
                merchant_id=request.merchant_id,
                merchant_name=request.merchant_name,
                amount=request.amount,
                currency=request.currency,
                status=TransactionStatus.AUTHORIZED,
                authorization_code=authorization_code,
                created_at=datetime.utcnow()
            )

            db.add(transaction)
            await db.commit()
            await db.refresh(transaction)

            logger.info(
                f"Transaction approved: "
                f"{transaction.id} "
                f"{request.amount} "
                f"{request.currency}"
            )

            return card_pb2.AuthorizationResponse(
                authorization_code=authorization_code,
                response_code=card_pb2.AuthorizationResponse.APPROVED,
                message="Approved",
                transaction_id=str(transaction.id)
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


if __name__ == '__main__':
    asyncio.run(serve())