# payment-gateway-service/app/main.py
import os
import grpc
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
from pydantic import BaseModel
from app import card_pb2, card_pb2_grpc
import hmac as hmac_lib
import hashlib
import time
import json
from fastapi import Header

GRPC_URL = os.getenv("GRPC_SERVER_URL", "card-provider:50051")

BANK_HMAC_SECRETS = {
    "bank-key-pl-a": "secret-pl-a-hmac",
    "bank-key-pl-b": "secret-pl-b-hmac",
    "bank-key-eu-a": "secret-eu-a-hmac",
    "bank-key-eu-b": "secret-eu-b-hmac",
    "bank-key-uk-a": "secret-uk-a-hmac",
    "bank-key-uk-b": "secret-uk-b-hmac",
    "bank-key-us-a": "secret-us-a-hmac",
    "bank-key-us-b": "secret-us-b-hmac",
}

@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"Payment Gateway connecting to gRPC: {GRPC_URL}")
    yield


app = FastAPI(
    title="Payment Gateway – Karty Płatnicze",
    description="""
## API Systemu Kart Płatniczych

### Cykl życia karty fizycznej/prepaid:
`REQUESTED → PRODUCING → SHIPPED → ACTIVE → BLOCKED`

### Karta wirtualna:
`REQUESTED → (auto po max 1h) → ACTIVE`

### Ważne:
Tylko karty w statusie **ACTIVE** mogą realizować płatności.
    """,
    version="1.0.0",
    lifespan=lifespan
)


# --- Modele requestów ---

class IssueCardRequest(BaseModel):
    user_id: str
    account_id: str
    card_type: str  # VIRTUAL | PHYSICAL | PREPAID
    initial_balance: float = 0.0


class CardStatusRequest(BaseModel):
    status: str  # BLOCKED | ACTIVE
    reason: str = ""


class UpdateLifecycleRequest(BaseModel):
    new_status: str  # PRODUCING | SHIPPED
    changed_by: str = "bank_operator"


class ActivateCardBody(BaseModel):
    activated_by: str = "customer"


class TopUpRequest(BaseModel):
    amount: float
    currency: str = "PLN"


class AuthorizeRequest(BaseModel):
    card_number: str
    expiry_month: int
    expiry_year: int
    cvv: str
    amount: float
    currency: str = "PLN"
    merchant_id: str = ""
    merchant_name: str = ""


# --- Endpointy systemowe ---

@app.get("/", tags=["System"])
async def root():
    return {"service": "Payment Gateway", "status": "Running", "docs": "/docs"}


@app.post("/test-connection", tags=["System"])
async def test_grpc():
    """Testuje połączenie gRPC z Card Provider."""
    try:
        async with grpc.aio.insecure_channel(GRPC_URL) as channel:
            stub = card_pb2_grpc.CardProviderStub(channel)
            response = await stub.CreateCard(card_pb2.CreateCardRequest(
                user_id="test_user",
                account_id="test_acc",
                card_type="VIRTUAL",
                initial_balance=0.0,
                api_key="bank-key-pl-a",
            ))
            return {
                "status": "Connection OK",
                "card_token": response.card_token,
                "masked_pan": response.masked_pan,
                "card_status": response.status
            }
    except Exception as e:
        return {"error": str(e)}


# --- Karty ---

@app.get("/api/v1/cards", tags=["Karty"], summary="Lista wszystkich kart")
async def list_cards():
    """Zwraca listę wszystkich kart w systemie (dla operatora)."""
    try:
        async with grpc.aio.insecure_channel(GRPC_URL) as channel:
            stub = card_pb2_grpc.CardProviderStub(channel)
            response = await stub.ListCards(card_pb2.ListCardsRequest())
            return {"cards": [
                {
                    "card_token": c.card_token,
                    "masked_pan": c.masked_pan,
                    "status": c.status,
                    "card_type": c.card_type,
                    "balance": c.balance,
                    "daily_limit": c.daily_limit,
                } for c in response.cards
            ]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/cards/issue", tags=["Karty"], summary="Wydaj nową kartę")
async def issue_card(
    body: IssueCardRequest,
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """
    Wydaje nową kartę płatniczą dla klienta banku.

    - **VIRTUAL** – startuje jako REQUESTED, auto-aktywuje się w ciągu 1h
    - **PHYSICAL** – startuje jako REQUESTED, wymaga przejścia przez cykl produkcji
    - **PREPAID** – startuje jako REQUESTED, posiada własne saldo (initial_balance)

    Karta **nie może** być używana do płatności dopóki nie osiągnie statusu **ACTIVE**.

    **Wymagane nagłówki:**
    - `X-API-Key` – klucz API banku (np. `bank-key-pl-a`)

    **Bezpieczeństwo:**
    - Żądanie podpisywane HMAC-SHA256 przed przekazaniem do Card Provider
    - Timestamp chroni przed replay attacks (żądanie ważne 30s)
    """
    if body.card_type not in ("VIRTUAL", "PHYSICAL", "PREPAID"):
        raise HTTPException(status_code=400, detail="card_type must be VIRTUAL, PHYSICAL or PREPAID")

    hmac_secret = BANK_HMAC_SECRETS.get(x_api_key)
    if not hmac_secret:
        raise HTTPException(status_code=401, detail="Invalid API key")

    body_dict = {
        "user_id": body.user_id,
        "account_id": body.account_id,
        "card_type": body.card_type,
        "initial_balance": body.initial_balance,
    }
    signature, timestamp = generate_hmac_signature(body_dict, hmac_secret)

    try:
        async with grpc.aio.insecure_channel(GRPC_URL) as channel:
            stub = card_pb2_grpc.CardProviderStub(channel)
            response = await stub.CreateCard(card_pb2.CreateCardRequest(
                user_id=body.user_id,
                account_id=body.account_id,
                card_type=body.card_type,
                initial_balance=body.initial_balance,
                api_key=x_api_key,
                signature=signature,
                timestamp=timestamp,
            ))
            return {
                "card_token": response.card_token,
                "masked_pan": response.masked_pan,
                "status": response.status,
                "card_type": response.card_type,
                "bank_id": response.bank_id,
                "message": "Card issued. Status: REQUESTED. Must go through lifecycle before use."
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/cards/{card_token}", tags=["Karty"], summary="Pobierz status karty")
async def get_card(card_token: str):
    """Zwraca szczegóły karty: status, typ, saldo, limit dzienny."""
    try:
        async with grpc.aio.insecure_channel(GRPC_URL) as channel:
            stub = card_pb2_grpc.CardProviderStub(channel)
            response = await stub.GetCardStatus(card_pb2.GetCardRequest(card_token=card_token))
            return {
                "card_token": response.card_token,
                "masked_pan": response.masked_pan,
                "status": response.status,
                "card_type": response.card_type,
                "balance": response.balance,
                "daily_limit": response.daily_limit,
            }
    except Exception as e:
        raise HTTPException(status_code=404, detail="Card not found")


@app.patch("/api/v1/cards/{card_token}/status", tags=["Karty"], summary="Zablokuj lub odblokuj kartę")
async def update_card_status(card_token: str, body: CardStatusRequest):
    """
    Zastrzega lub odblokowuje kartę.

    - **BLOCKED** – karta nie może być używana do płatności
    - **ACTIVE** – karta wraca do normalnego działania
    """
    try:
        async with grpc.aio.insecure_channel(GRPC_URL) as channel:
            stub = card_pb2_grpc.CardProviderStub(channel)
            if body.status == "BLOCKED":
                response = await stub.BlockCard(card_pb2.BlockCardRequest(
                    card_token=card_token, reason=body.reason))
            elif body.status == "ACTIVE":
                response = await stub.UnblockCard(card_pb2.UnblockCardRequest(
                    card_token=card_token))
            else:
                raise HTTPException(status_code=400, detail="status must be BLOCKED or ACTIVE")
            return {"success": response.success, "message": response.message}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Cykl życia karty ---

@app.patch("/api/v1/cards/{card_token}/lifecycle", tags=["Cykl życia karty"],
           summary="Przesuń kartę przez cykl produkcji (operator banku)")
async def update_lifecycle(card_token: str, body: UpdateLifecycleRequest):
    """
    Tylko dla operatora banku / systemu produkcji kart.

    Dozwolone przejścia:
    - **REQUESTED → PRODUCING** – karta trafia do produkcji
    - **PRODUCING → SHIPPED** – karta wysłana do banku/klienta

    Po statusie SHIPPED klient aktywuje kartę przez `/activate`.
    """
    try:
        async with grpc.aio.insecure_channel(GRPC_URL) as channel:
            stub = card_pb2_grpc.CardProviderStub(channel)
            response = await stub.UpdateCardStatus(card_pb2.UpdateCardStatusRequest(
                card_token=card_token,
                new_status=body.new_status,
                changed_by=body.changed_by,
            ))
            if not response.success:
                raise HTTPException(status_code=400, detail=response.message)
            return {
                "success": True,
                "message": response.message,
                "current_status": response.current_status
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/cards/{card_token}/activate", tags=["Cykl życia karty"],
          summary="Aktywuj kartę (klient w aplikacji banku)")
async def activate_card(card_token: str, body: ActivateCardBody):
    """
    Symuluje aktywację karty przez klienta w aplikacji mobilnej banku.

    - Karta musi być w statusie **SHIPPED**
    - Po aktywacji karta przechodzi do **ACTIVE** i jest gotowa do płatności
    - Karty wirtualne aktywują się automatycznie – ten endpoint jest dla fizycznych i prepaid
    """
    try:
        async with grpc.aio.insecure_channel(GRPC_URL) as channel:
            stub = card_pb2_grpc.CardProviderStub(channel)
            response = await stub.ActivateCard(card_pb2.ActivateCardRequest(
                card_token=card_token,
                activated_by=body.activated_by,
            ))
            if not response.success:
                raise HTTPException(status_code=400, detail=response.message)
            return {"success": True, "message": response.message}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Prepaid ---

@app.post("/api/v1/cards/{card_token}/topup", tags=["Prepaid"],
          summary="Doładuj kartę prepaid")
async def topup_prepaid(card_token: str, body: TopUpRequest):
    """
    Doładowanie salda karty przedpłaconej (PREPAID).

    - Tylko karty typu **PREPAID** w statusie **ACTIVE**
    - Kwota musi być dodatnia
    """
    try:
        async with grpc.aio.insecure_channel(GRPC_URL) as channel:
            stub = card_pb2_grpc.CardProviderStub(channel)
            response = await stub.TopUpPrepaid(card_pb2.TopUpRequest(
                card_token=card_token,
                amount=body.amount,
                currency=body.currency,
            ))
            if not response.success:
                raise HTTPException(status_code=400, detail=response.message)
            return {
                "success": True,
                "message": response.message,
                "new_balance": response.new_balance
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def generate_hmac_signature(body: dict, secret: str) -> tuple[str, str]:
    """
    Generuje podpis HMAC-SHA256 dla żądania.
    Używane przez Payment Gateway przed przekazaniem do Card Provider.
    """
    timestamp = str(int(time.time()))
    body_json = json.dumps(body, separators=(',', ':'), sort_keys=True)
    payload = timestamp + body_json
    signature = hmac_lib.new(
        secret.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature, timestamp