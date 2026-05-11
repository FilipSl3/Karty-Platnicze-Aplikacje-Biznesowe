# payment-gateway-service/app/main.py
import os
import grpc
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
from pydantic import BaseModel
from app import card_pb2, card_pb2_grpc

GRPC_URL = os.getenv("GRPC_SERVER_URL", "card-provider:50051")


def get_stub():
    channel = grpc.aio.insecure_channel(GRPC_URL)
    return card_pb2_grpc.CardProviderStub(channel)


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"Connecting to gRPC: {GRPC_URL}")
    yield


app = FastAPI(
    title="Payment Gateway – Karty Płatnicze",
    description="REST API dla terminala płatniczego i zarządzania kartami",
    version="1.0.0",
    lifespan=lifespan
)

class IssueCardRequest(BaseModel):
    user_id: str
    account_id: str
    card_type: str   # VIRTUAL | PHYSICAL | PREPAID
    initial_balance: float = 0.0


class CardStatusRequest(BaseModel):
    status: str      # BLOCKED | ACTIVE
    reason: str = ""


class AuthorizeRequest(BaseModel):
    card_number: str
    expiry_month: int
    expiry_year: int
    cvv: str
    amount: float
    currency: str = "PLN"
    merchant_id: str = ""
    merchant_name: str = ""


# --- Endpointy ---

@app.get("/")
async def root():
    return {"service": "Payment Gateway", "status": "Running"}


@app.post("/test-connection")
async def test_grpc():
    try:
        async with grpc.aio.insecure_channel(GRPC_URL) as channel:
            stub = card_pb2_grpc.CardProviderStub(channel)
            request = card_pb2.CreateCardRequest(
                user_id="test_user",
                account_id="test_acc",
                card_type="VIRTUAL",
                initial_balance=0.0
            )
            response = await stub.CreateCard(request)
            return {
                "status": "Connection OK",
                "card_token": response.card_token,
                "masked_pan": response.masked_pan,
                "card_status": response.status
            }
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/v1/cards/issue", summary="Wydaj nową kartę")
async def issue_card(body: IssueCardRequest):
    """
    Wydaje nową kartę płatniczą.
    - **VIRTUAL** – aktywna od razu
    - **PHYSICAL** – status ORDERED, wysyłana do banku
    - **PREPAID** – z saldem początkowym
    """
    if body.card_type not in ("VIRTUAL", "PHYSICAL", "PREPAID"):
        raise HTTPException(status_code=400, detail="card_type must be VIRTUAL, PHYSICAL or PREPAID")

    try:
        async with grpc.aio.insecure_channel(GRPC_URL) as channel:
            stub = card_pb2_grpc.CardProviderStub(channel)
            response = await stub.CreateCard(card_pb2.CreateCardRequest(
                user_id=body.user_id,
                account_id=body.account_id,
                card_type=body.card_type,
                initial_balance=body.initial_balance,
            ))
            return {
                "card_token": response.card_token,
                "masked_pan": response.masked_pan,
                "status": response.status,
                "card_type": response.card_type,
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/cards/{card_token}", summary="Pobierz status karty")
async def get_card(card_token: str):
    try:
        async with grpc.aio.insecure_channel(GRPC_URL) as channel:
            stub = card_pb2_grpc.CardProviderStub(channel)
            response = await stub.GetCardStatus(card_pb2.GetCardRequest(
                card_token=card_token
            ))
            return {
                "card_token": response.card_token,
                "status": response.status,
                "card_type": response.card_type,
                "balance": response.balance,
                "daily_limit": response.daily_limit,
            }
    except Exception as e:
        raise HTTPException(status_code=404, detail="Card not found")


@app.patch("/api/v1/cards/{card_token}/status", summary="Zablokuj lub odblokuj kartę")
async def update_card_status(card_token: str, body: CardStatusRequest):
    try:
        async with grpc.aio.insecure_channel(GRPC_URL) as channel:
            stub = card_pb2_grpc.CardProviderStub(channel)

            if body.status == "BLOCKED":
                response = await stub.BlockCard(card_pb2.BlockCardRequest(
                    card_token=card_token,
                    reason=body.reason,
                ))
            elif body.status == "ACTIVE":
                response = await stub.UnblockCard(card_pb2.UnblockCardRequest(
                    card_token=card_token,
                ))
            else:
                raise HTTPException(status_code=400, detail="status must be BLOCKED or ACTIVE")

            return {"success": response.success, "message": response.message}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))