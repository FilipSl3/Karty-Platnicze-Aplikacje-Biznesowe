# card-provider-service/app/main.py
import asyncio
import logging
import uuid
import secrets
from datetime import datetime, timedelta
from grpc import aio
from sqlalchemy import select

from app import card_pb2_grpc
import app.card_pb2 as card_pb2
from app.database import AsyncSessionLocal, engine, Base
from app.models import Card, CardType, CardStatus, CardStatusHistory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def generate_token() -> str:
    """Generuje unikalny token karty"""
    return f"tok_{secrets.token_hex(16)}"


def generate_masked_pan(card_type: str) -> str:
    """Generuje zamaskowany numer karty z prefiksem BIN"""
    bin_map = {
        "VIRTUAL":  "4100",
        "PHYSICAL": "4200",
        "PREPAID":  "4300",
    }
    prefix = bin_map.get(card_type, "4000")
    middle = secrets.randbelow(99999999)
    last4 = secrets.randbelow(9999)
    return f"{prefix} **** **** {last4:04d}"


async def activate_virtual_card_after_delay(card_id: str):
    """Aktywuje kartę wirtualną po 1 godzinie"""
    await asyncio.sleep(3600)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Card).where(Card.id == uuid.UUID(card_id)))
        card = result.scalar_one_or_none()
        if card and card.status == CardStatus.ACTIVE:
            card.activated_at = datetime.utcnow()
            await db.commit()
            logger.info(f"Virtual card {card_id} activated after 1h")


class CardProviderServicer(card_pb2_grpc.CardProviderServicer):

    async def CreateCard(self, request, context):
        logger.info(f"CreateCard request: user={request.user_id} type={request.card_type}")

        async with AsyncSessionLocal() as db:
            if request.card_type == "PHYSICAL":
                initial_status = CardStatus.ORDERED
                activated_at = None
            elif request.card_type == "VIRTUAL":
                initial_status = CardStatus.ACTIVE
                activated_at = datetime.utcnow()
            elif request.card_type == "PREPAID":
                initial_status = CardStatus.ACTIVE
                activated_at = datetime.utcnow()
            else:
                initial_status = CardStatus.ACTIVE
                activated_at = datetime.utcnow()

            card = Card(
                id=uuid.uuid4(),
                user_id=request.user_id,
                account_id=request.account_id,
                token=generate_token(),
                masked_pan=generate_masked_pan(request.card_type),
                card_type=CardType(request.card_type),
                status=initial_status,
                balance=float(request.initial_balance) if request.card_type == "PREPAID" else 0,
                daily_limit=1000.00,
                created_at=datetime.utcnow(),
                activated_at=activated_at,
            )
            db.add(card)

            history = CardStatusHistory(
                card_id=card.id,
                old_status=None,
                new_status=initial_status.value,
                changed_by="system",
            )
            db.add(history)

            await db.commit()
            await db.refresh(card)

            logger.info(f"Card created: token={card.token} status={card.status}")

            # Dla wirtualnej: zaplanuj aktywację po 1h (już jest aktywna, ale zapisz czas)
            # Jeśli chcesz żeby wirtualna startowała jako PENDING i aktywowała się po 1h,
            # odkomentuj poniższe i zmień initial_status dla VIRTUAL na ORDERED
            # asyncio.create_task(activate_virtual_card_after_delay(str(card.id)))

            return card_pb2.CreateCardResponse(
                card_token=card.token,
                masked_pan=card.masked_pan,
                status=card.status.value,
                card_type=card.card_type.value,
            )

    async def GetCardStatus(self, request, context):
        logger.info(f"GetCardStatus: token={request.card_token}")

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Card).where(Card.token == request.card_token)
            )
            card = result.scalar_one_or_none()

            if not card:
                await context.abort(aio.StatusCode.NOT_FOUND, "Card not found")

            return card_pb2.CardDetails(
                card_token=card.token,
                status=card.status.value,
                card_type=card.card_type.value,
                balance=float(card.balance),
                daily_limit=float(card.daily_limit),
            )

    async def BlockCard(self, request, context):
        logger.info(f"BlockCard: token={request.card_token}")

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Card).where(Card.token == request.card_token)
            )
            card = result.scalar_one_or_none()

            if not card:
                return card_pb2.BlockCardResponse(success=False, message="Card not found")

            old_status = card.status.value
            card.status = CardStatus.BLOCKED

            history = CardStatusHistory(
                card_id=card.id,
                old_status=old_status,
                new_status=CardStatus.BLOCKED.value,
                changed_by=request.reason or "admin",
            )
            db.add(history)
            await db.commit()

            return card_pb2.BlockCardResponse(success=True, message="Card blocked")

    async def UnblockCard(self, request, context):
        logger.info(f"UnblockCard: token={request.card_token}")

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Card).where(Card.token == request.card_token)
            )
            card = result.scalar_one_or_none()

            if not card:
                return card_pb2.UnblockCardResponse(success=False, message="Card not found")

            old_status = card.status.value
            card.status = CardStatus.ACTIVE

            history = CardStatusHistory(
                card_id=card.id,
                old_status=old_status,
                new_status=CardStatus.ACTIVE.value,
                changed_by="admin",
            )
            db.add(history)
            await db.commit()

            return card_pb2.UnblockCardResponse(success=True, message="Card unblocked")


async def serve():
    server = aio.server()
    card_pb2_grpc.add_CardProviderServicer_to_server(CardProviderServicer(), server)
    server.add_insecure_port('[::]:50051')
    logger.info("Card Provider Service RUNNING on port 50051 (gRPC)")
    await server.start()
    await server.wait_for_termination()


if __name__ == '__main__':
    asyncio.run(serve())