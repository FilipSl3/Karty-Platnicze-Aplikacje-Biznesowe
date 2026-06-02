# payment-gateway-service/app/main.py
import os
import grpc
from fastapi import FastAPI, HTTPException, Header, Depends, Request
from contextlib import asynccontextmanager
from pydantic import BaseModel
from app import card_pb2, card_pb2_grpc
import hmac as hmac_lib
import hashlib
import time
import json
from iso8583 import encode, decode
from app.iso_spec import spec
from app.iso_socket_client import send_iso
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

GRPC_URL = os.getenv("GRPC_SERVER_URL", "card-provider:50051")
from app.grpc_client import (
    authorize_transaction
)

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

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "admin-secret-key-2026")
SIGNATURE_MAX_AGE_SECONDS = 30


def verify_bank_signature(body: dict, signature: str, timestamp: str, secret: str) -> tuple[bool, str]:
    """Weryfikuje podpis HMAC-SHA256 przysłany przez bank."""
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        return False, "Invalid timestamp format"

    age = abs(time.time() - ts)
    if age > SIGNATURE_MAX_AGE_SECONDS:
        return False, f"Request expired ({int(age)}s old, max {SIGNATURE_MAX_AGE_SECONDS}s)"

    try:
        body_json = json.dumps(body, separators=(',', ':'), sort_keys=True)
    except Exception:
        return False, "Failed to serialize body"

    payload = timestamp + body_json
    expected = hmac_lib.new(
        secret.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    if not hmac_lib.compare_digest(expected, signature):
        return False, "Invalid signature"

    return True, ""


def generate_hmac_signature(body: dict, secret: str) -> tuple[str, str]:
    """Generuje podpis HMAC-SHA256 – używane tylko w test-connection."""
    timestamp = str(int(time.time()))
    body_json = json.dumps(body, separators=(',', ':'), sort_keys=True)
    payload = timestamp + body_json
    signature = hmac_lib.new(
        secret.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature, timestamp


def validate_luhn(card_number: str) -> bool:
    card_number = card_number.replace(" ", "")
    if not card_number.isdigit():
        return False
    digits = [int(d) for d in card_number]
    checksum = 0
    parity = len(digits) % 2
    for i, digit in enumerate(digits):
        if i % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def verify_admin_key(x_admin_key: str = Header(None, alias="X-Admin-Key")):
    if x_admin_key != ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    return x_admin_key


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

### Uwierzytelnianie banków:
Każde żądanie banku musi zawierać:
- `X-API-Key` – klucz API banku
- `X-Signature` – podpis HMAC-SHA256 body + timestamp (generowany przez bank)
- `X-Timestamp` – Unix timestamp (żądanie ważne 30s)

### Ważne:
Tylko karty w statusie **ACTIVE** mogą realizować płatności.

### Emulator POS

Dostępny pod adresem:

http://localhost:8072/pos

Pozwala testować płatności kartowe z poziomu przeglądarki.
    """,
    version="1.0.0",
    lifespan=lifespan
)
templates = Jinja2Templates(directory="templates")

# --- Modele requestów ---

class IssueCardRequest(BaseModel):
    user_id: str
    account_id: str
    card_type: str
    initial_balance: float = 0.0


class CardStatusRequest(BaseModel):
    status: str
    reason: str = ""


class UpdateLifecycleRequest(BaseModel):
    new_status: str
    changed_by: str = "bank_operator"


class ActivateCardBody(BaseModel):
    activated_by: str = "customer"


class TopUpRequest(BaseModel):
    amount: float
    currency: str = "PLN"


class AuthorizeRequest(BaseModel):
    card_number: str = Field(
        example="4100013395241296",
        description="16-cyfrowy numer karty"
    )

    expiry_month: int = Field(
        example=5,
        description="Miesiąc ważności"
    )

    expiry_year: int = Field(
        example=29,
        description="Rok ważności (YY)"
    )

    cvv: str = Field(
        example="889",
        description="Kod CVV"
    )

    amount: float = Field(
        example=50.00,
        description="Kwota transakcji"
    )

    currency: str = Field(
        default="PLN",
        example="PLN"
    )

    merchant_id: str = Field(
        default="SHOP_001",
        example="SHOP_001"
    )

    merchant_name: str = Field(
        default="Test Shop",
        example="Test Shop"
    )


# --- Endpointy systemowe ---

@app.get("/", tags=["System"])
async def root():
    return {"service": "Payment Gateway", "status": "Running", "docs": "/docs"}


@app.post("/test-connection", tags=["System"])
async def test_grpc():
    """Testuje połączenie gRPC z Card Provider. Używa bank-key-pl-a."""
    try:
        test_api_key = "bank-key-pl-a"
        hmac_secret = BANK_HMAC_SECRETS[test_api_key]
        body_dict = {
            "user_id": "test_user",
            "account_id": "test_acc",
            "card_type": "VIRTUAL",
            "initial_balance": 0.0,
        }
        signature, timestamp = generate_hmac_signature(body_dict, hmac_secret)
        async with grpc.aio.insecure_channel(GRPC_URL) as channel:
            stub = card_pb2_grpc.CardProviderStub(channel)
            response = await stub.CreateCard(card_pb2.CreateCardRequest(
                user_id="test_user",
                account_id="test_acc",
                card_type="VIRTUAL",
                initial_balance=0.0,
                api_key=test_api_key,
                signature=signature,
                timestamp=timestamp,
            ))
            return {
                "status": "Connection OK",
                "card_token": response.card_token,
                "masked_pan": response.masked_pan,
                "card_status": response.status,
                "bank_id": response.bank_id,
            }
    except Exception as e:
        return {"error": str(e)}


# --- Karty ---

@app.get("/api/v1/cards", tags=["Karty"], summary="Lista wszystkich kart")
async def list_cards(_: str = Depends(verify_admin_key)):
    """
    Lista wszystkich kart w systemie.
    **Wymaga nagłówka X-Admin-Key.**
    """
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
                    "bank_id": c.bank_id,
                } for c in response.cards
            ]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/cards/issue", tags=["Karty"], summary="Wydaj nową kartę")
async def issue_card(
    request: Request,
    body: IssueCardRequest,
    x_api_key: str = Header(..., alias="X-API-Key"),
    x_signature: str = Header(..., alias="X-Signature"),
    x_timestamp: str = Header(..., alias="X-Timestamp"),
):
    """
    Wydaje nową kartę płatniczą dla klienta banku.

    - **VIRTUAL** – startuje jako REQUESTED, auto-aktywuje się w ciągu 1h
    - **PHYSICAL** – startuje jako REQUESTED, wymaga przejścia przez cykl produkcji
    - **PREPAID** – startuje jako REQUESTED, posiada własne saldo (initial_balance)

    **Wymagane nagłówki:**
    - `X-API-Key` – klucz API banku
    - `X-Signature` – podpis HMAC-SHA256 wygenerowany przez bank
    - `X-Timestamp` – Unix timestamp (żądanie ważne 30s)

    **Bezpieczeństwo:**
    - Bank sam generuje podpis HMAC-SHA256 ze swoim sekretem
    - Payment Gateway weryfikuje podpis – nie zna treści sekretu na poziomie logiki
    - Timestamp chroni przed replay attacks
    """
    if body.card_type not in ("VIRTUAL", "PHYSICAL", "PREPAID"):
        raise HTTPException(status_code=400, detail="card_type must be VIRTUAL, PHYSICAL or PREPAID")

    hmac_secret = BANK_HMAC_SECRETS.get(x_api_key)
    if not hmac_secret:
        raise HTTPException(status_code=401, detail="Invalid API key")

    raw_body = await request.body()
    try:
        raw_dict = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    valid, reason = verify_bank_signature(raw_dict, x_signature, x_timestamp, hmac_secret)
    if not valid:
        raise HTTPException(status_code=401, detail=f"Signature verification failed: {reason}")


    try:
        async with grpc.aio.insecure_channel(GRPC_URL) as channel:
            stub = card_pb2_grpc.CardProviderStub(channel)
            response = await stub.CreateCard(card_pb2.CreateCardRequest(
                user_id=body.user_id,
                account_id=body.account_id,
                card_type=body.card_type,
                initial_balance=body.initial_balance,
                api_key=x_api_key,
                signature=x_signature,
                timestamp=x_timestamp,
            ))
            return {
                "card_token": response.card_token,
                "masked_pan": response.masked_pan,
                "full_pan": response.full_pan,
                "cvv": response.cvv,
                "expiry_month": response.expiry_month,
                "expiry_year": response.expiry_year,
                "status": response.status,
                "card_type": response.card_type,
                "bank_id": response.bank_id,
                "message": "IMPORTANT: Save full_pan and cvv - they will never be shown again."
            }
    except HTTPException:
        raise
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
                "bank_id": response.bank_id,
            }
    except Exception as e:
        raise HTTPException(status_code=404, detail="Card not found")


@app.patch("/api/v1/cards/{card_token}/status", tags=["Karty"], summary="Zablokuj lub odblokuj kartę")
async def update_card_status(
    card_token: str,
    body: CardStatusRequest,
    x_api_key: str = Header(None, alias="X-API-Key"),
    x_admin_key: str = Header(None, alias="X-Admin-Key"),
):
    """
    Zastrzega lub odblokowuje kartę.

    - **BLOCKED** – karta nie może być używana do płatności
    - **ACTIVE** – karta wraca do normalnego działania

    Wymaga `X-API-Key` (bank) lub `X-Admin-Key` (operator).
    """
    if not x_api_key and not x_admin_key:
        raise HTTPException(status_code=401, detail="X-API-Key or X-Admin-Key required")

    if x_admin_key:
        if x_admin_key != ADMIN_API_KEY:
            raise HTTPException(status_code=403, detail="Invalid admin key")
    elif x_api_key:
        if x_api_key not in BANK_HMAC_SECRETS:
            raise HTTPException(status_code=401, detail="Invalid API key")

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

@app.patch("/api/v1/cards/{card_token}/lifecycle",
           tags=["Cykl życia karty"],
           summary="Przesuń kartę przez cykl produkcji (operator Card Provider)")
async def update_lifecycle(
    card_token: str,
    body: UpdateLifecycleRequest,
    _: str = Depends(verify_admin_key),
):
    """
    Tylko dla operatora **Card Provider** – nie dla banków.

    Dozwolone przejścia:
    - **REQUESTED → PRODUCING**
    - **PRODUCING → SHIPPED**

    **Wymaga nagłówka X-Admin-Key.**
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
    - Karty wirtualne aktywują się automatycznie
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


# --- DEV / Admin ---

@app.get("/api/v1/cards/{card_token}/full-pan",
         tags=["DEV / Admin"],
         summary="[ADMIN] Pokaż pełne dane karty – tylko do testów")
async def get_full_pan(
    card_token: str,
    _: str = Depends(verify_admin_key),
):
    """
    Zwraca odszyfrowany PAN, CVV i datę ważności.

    **Tylko dla admina i celów testowych.**
    Wymaga nagłówka `X-Admin-Key`.
    """
    try:
        async with grpc.aio.insecure_channel(GRPC_URL) as channel:
            stub = card_pb2_grpc.CardProviderStub(channel)
            response = await stub.GetFullPan(card_pb2.GetCardRequest(
                card_token=card_token
            ))
            return {
                "card_token": response.card_token,
                "full_pan": response.full_pan,
                "masked_pan": response.masked_pan,
                "cvv": response.cvv,
                "expiry_month": response.expiry_month,
                "expiry_year": response.expiry_year,
            }
    except Exception as e:
        raise HTTPException(status_code=404, detail="Card not found")


# --- Płatności ---

@app.post(
    "/api/v1/payments/authorize",
    tags=["Płatności"],
    summary="Autoryzacja płatności kartą (POS Terminal)",
    description="""
Symulacja terminala płatniczego POS.

Przebieg:
1. Walidacja numeru karty algorytmem Luhna
2. Budowa komunikatu ISO8583
3. Wysłanie komunikatu TCP Socket do Card Provider
4. Weryfikacja PAN, CVV, daty ważności i statusu karty
5. Zwrot decyzji APPROVED / DECLINED

Uwaga:
Ten endpoint służy do testowania terminala POS.
Banki nie wywołują go bezpośrednio.
"""
)
async def authorize_payment(body: AuthorizeRequest):
    """
    Symuluje terminal płatniczy POS.

    Flow:
    1. Walidacja numeru karty algorytmem Luhna
    2. Wywołanie gRPC AuthorizeTransaction do Card Provider
    3. Zwrot decyzji APPROVED / DECLINED
    """
    card_number = body.card_number.replace(" ", "")

    if not validate_luhn(card_number):
        raise HTTPException(status_code=400, detail="Invalid card number (Luhn failed)")

    try:
        async with grpc.aio.insecure_channel(GRPC_URL) as channel:

            stub = card_pb2_grpc.CardProviderStub(channel)

            iso_message = {
                "t": "0100",
                "2": card_number,
                "4": str(int(body.amount * 100)).zfill(12),
                "14": f"{body.expiry_month:02d}{body.expiry_year:02d}",
                "41": "POS00001".ljust(8),
                "42": body.merchant_id[:15].ljust(15),
                "49": body.currency,
                "52": body.cvv,
            }

            raw_iso, encoded = encode(iso_message, spec)

            print("========== GATEWAY ISO ==========")
            print(iso_message)
            print(encoded)
            print(raw_iso)

            decoded = await send_iso(raw_iso)

            return {
                "approved": decoded["39"] == "00",
                "response_code": decoded["39"],
                "authorization_code": decoded.get("38", ""),
                "message": (
                    "Approved"
                    if decoded["39"] == "00"
                    else "Declined"
                )
            }

            print("RAW RESPONSE:")
            print(response.payload)

            decoded, encoded = decode(
                bytes(response.payload),
                spec
            )

            print("DECODED:")
            print(decoded)

            return {
                "approved": decoded["39"] == "00",
                "response_code": decoded["39"],
                "authorization_code": decoded.get("38", ""),
                "message": (
                    "Approved"
                    if decoded["39"] == "00"
                    else "Declined"
                )
            }
    except grpc.RpcError as e:
        raise HTTPException(status_code=500, detail=f"gRPC error: {e.details()}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
"""terminal gui"""    
@app.get(
    "/pos",
    response_class=HTMLResponse,
    tags=["POS"],
    summary="Web POS Terminal Emulator",
    description="""
Przeglądarkowy emulator terminala płatniczego.

Pozwala wykonać testową płatność kartą bez używania Postmana
ani Swagger UI.

Adres:
http://localhost:8072/pos
"""
)
async def pos_terminal(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="pos.html"
    )