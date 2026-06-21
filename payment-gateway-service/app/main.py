# payment-gateway-service/app/main.py
import os
import grpc
from fastapi import FastAPI, HTTPException, Header, Depends, Request
from contextlib import asynccontextmanager
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
import threading
from collections import OrderedDict
from sqlalchemy import select, func
from app.database import AsyncSessionLocal
from app.revenue_models import TransactionFee
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
OFFLINE_FILE = "/app/data/offline_transactions.json"
# Cache zużytych podpisów -> realna ochrona przed replay w obrębie okna ważności.
_seen_signatures: "OrderedDict[str, float]" = OrderedDict()
_seen_lock = threading.Lock()
OFFLINE_FLOOR_LIMIT = 50.00
def load_offline_transactions():
    try:
        with open(OFFLINE_FILE, "r") as f:
            return json.load(f)
    except:
        return []


def save_offline_transaction(tx: dict):
    os.makedirs("/app/data", exist_ok=True)

    transactions = load_offline_transactions()
    transactions.append(tx)

    with open(OFFLINE_FILE, "w") as f:
        json.dump(transactions, f, indent=2)

def clear_offline_transactions(transactions: list):
    os.makedirs("/app/data", exist_ok=True)

    with open(OFFLINE_FILE, "w") as f:
        json.dump(transactions, f, indent=2)

def _is_fresh_signature(signature: str) -> bool:
    """True = podpis nowy (OK). False = już użyty w oknie ważności (replay)."""
    now = time.time()
    with _seen_lock:
        # usuń przeterminowane podpisy z początku (OrderedDict trzyma kolejność wstawiania)
        while _seen_signatures:
            sig, ts = next(iter(_seen_signatures.items()))
            if now - ts > SIGNATURE_MAX_AGE_SECONDS:
                _seen_signatures.popitem(last=False)
            else:
                break
        if signature in _seen_signatures:
            return False
        _seen_signatures[signature] = now
        return True


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


async def authenticate_bank(request: Request, x_api_key, x_signature, x_timestamp) -> None:
    """
    Pełna weryfikacja żądania banku: klucz API + podpis HMAC + ochrona przed replay.
    Liczy podpis nad SUROWYM body żądania (te same bajty, które przyszły).
    """
    secret = BANK_HMAC_SECRETS.get(x_api_key)
    if not secret:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if not x_signature or not x_timestamp:
        raise HTTPException(status_code=401, detail="X-Signature and X-Timestamp required")
    raw_body = await request.body()
    try:
        body_dict = json.loads(raw_body) if raw_body else {}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    valid, reason = verify_bank_signature(body_dict, x_signature, x_timestamp, secret)
    if not valid:
        raise HTTPException(status_code=401, detail=f"Signature verification failed: {reason}")
    if not _is_fresh_signature(x_signature):
        raise HTTPException(status_code=401, detail="Replay detected: signature already used")


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
    if not x_admin_key or not hmac_lib.compare_digest(x_admin_key, ADMIN_API_KEY):
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
Każde żądanie banku zmieniające stan karty (issue, activate, topup, status)
musi zawierać:
- `X-API-Key` – klucz API banku
- `X-Signature` – podpis HMAC-SHA256 body + timestamp (generowany przez bank)
- `X-Timestamp` – Unix timestamp (żądanie ważne 30s)

### Ważne:
Tylko karty w statusie **ACTIVE** mogą realizować płatności.

### Emulator POS
Dostępny pod adresem: http://localhost:8072/pos
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
    card_number: str = Field(example="4100013395241296", description="16-cyfrowy numer karty")
    expiry_month: int = Field(example=5, description="Miesiąc ważności")
    expiry_year: int = Field(example=29, description="Rok ważności (YY)")
    cvv: str = Field(example="889", description="Kod CVV")
    amount: float = Field(example=50.00, description="Kwota transakcji")
    currency: str = Field(default="PLN", example="PLN")
    merchant_id: str = Field(default="SHOP_001", example="SHOP_001")
    merchant_name: str = Field(default="Test Shop", example="Test Shop")


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
        print("AUTHORIZE ERROR:", repr(e))

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


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

    **Wymagane nagłówki:** X-API-Key, X-Signature, X-Timestamp (podpis HMAC banku).
    """
    if body.card_type not in ("VIRTUAL", "PHYSICAL", "PREPAID"):
        raise HTTPException(status_code=400, detail="card_type must be VIRTUAL, PHYSICAL or PREPAID")

    await authenticate_bank(request, x_api_key, x_signature, x_timestamp)

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
        print("AUTHORIZE ERROR:", repr(e))

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


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
    request: Request,
    x_api_key: str = Header(None, alias="X-API-Key"),
    x_signature: str = Header(None, alias="X-Signature"),
    x_timestamp: str = Header(None, alias="X-Timestamp"),
    x_admin_key: str = Header(None, alias="X-Admin-Key"),
):
    """
    Zastrzega lub odblokowuje kartę.

    - **BLOCKED** – karta nie może być używana do płatności
    - **ACTIVE** – karta wraca do normalnego działania

    Bank: X-API-Key + X-Signature + X-Timestamp. Operator: X-Admin-Key.
    """
    if not x_api_key and not x_admin_key:
        raise HTTPException(status_code=401, detail="X-API-Key or X-Admin-Key required")

    if x_admin_key:
        if not hmac_lib.compare_digest(x_admin_key, ADMIN_API_KEY):
            raise HTTPException(status_code=403, detail="Invalid admin key")
    elif x_api_key:
        await authenticate_bank(request, x_api_key, x_signature, x_timestamp)

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
async def activate_card(
    card_token: str,
    body: ActivateCardBody,
    request: Request,
    x_api_key: str = Header(None, alias="X-API-Key"),
    x_signature: str = Header(None, alias="X-Signature"),
    x_timestamp: str = Header(None, alias="X-Timestamp"),
    x_admin_key: str = Header(None, alias="X-Admin-Key"),
):
    """
    Symuluje aktywację karty przez klienta w aplikacji mobilnej banku.

    - Karta musi być w statusie **SHIPPED**
    - Karty wirtualne aktywują się automatycznie

    Bank: X-API-Key + X-Signature + X-Timestamp. Operator (panel): X-Admin-Key.
    """
    if x_admin_key:
        if not hmac_lib.compare_digest(x_admin_key, ADMIN_API_KEY):
            raise HTTPException(status_code=403, detail="Invalid admin key")
    elif x_api_key:
        await authenticate_bank(request, x_api_key, x_signature, x_timestamp)
    else:
        raise HTTPException(status_code=401, detail="X-API-Key or X-Admin-Key required")

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
async def topup_prepaid(
    card_token: str,
    body: TopUpRequest,
    request: Request,
    x_api_key: str = Header(None, alias="X-API-Key"),
    x_signature: str = Header(None, alias="X-Signature"),
    x_timestamp: str = Header(None, alias="X-Timestamp"),
    x_admin_key: str = Header(None, alias="X-Admin-Key"),
):
    """
    Doładowanie salda karty przedpłaconej (PREPAID).

    - Tylko karty typu **PREPAID** w statusie **ACTIVE**
    - Kwota musi być dodatnia

    Bank: X-API-Key + X-Signature + X-Timestamp. Operator (panel): X-Admin-Key.
    """
    if x_admin_key:
        if not hmac_lib.compare_digest(x_admin_key, ADMIN_API_KEY):
            raise HTTPException(status_code=403, detail="Invalid admin key")
    elif x_api_key:
        await authenticate_bank(request, x_api_key, x_signature, x_timestamp)
    else:
        raise HTTPException(status_code=401, detail="X-API-Key or X-Admin-Key required")

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

@app.post("/api/v1/payments/authorize", tags=["Płatności"],
          summary="Autoryzacja płatności kartą (POS Terminal)",
          description="""
Endpoint symuluje działanie terminala płatniczego POS.

Mechanizm:
- waliduje numer karty algorytmem Luhna
- buduje wiadomość ISO8583
- wysyła żądanie autoryzacyjne do Card Provider
- odbiera odpowiedź autoryzacyjną
- zwraca wynik (APPROVED / DECLINED)

Dodatkowo:
- jeśli serwer autoryzacji jest niedostępny
- a kwota mieści się w floor limit (np. <= 50 PLN)
- transakcja zostaje zaakceptowana offline i zapisana lokalnie

Obsługuje:
- autoryzację online
- autoryzację offline (floor limit)

Ten endpoint służy do testowania terminala POS. Banki nie wywołują go bezpośrednio.
""")
async def authorize_payment(body: AuthorizeRequest):
    """
    Symuluje terminal płatniczy POS.
    """
    card_number = body.card_number.replace(" ", "")

    if not validate_luhn(card_number):
        raise HTTPException(
            status_code=400,
            detail="Invalid card number (Luhn failed)"
        )

    try:

        expiry_year = body.expiry_year % 100

        iso_message = {
            "t": "0100",
            "2": card_number,
            "4": str(int(body.amount * 100)).zfill(12),
            "14": f"{body.expiry_month:02d}{expiry_year:02d}",
            "41": "POS00001".ljust(8),
            "42": body.merchant_id[:15].ljust(15),
            "49": body.currency,
            "52": body.cvv,
        }

        raw_iso, encoded = encode(
            iso_message,
            spec
        )

        print("========== GATEWAY ISO ==========")
        print(iso_message)
        print(encoded)
        print(raw_iso)

        try:
            decoded = await send_iso(raw_iso)

            return {
                "approved": decoded["39"] == "00",
                "offline": False,
                "response_code": decoded["39"],
                "authorization_code": decoded.get("38", ""),
                "message":
                    "Approved"
                    if decoded["39"] == "00"
                    else "Declined"
            }
        except Exception:
            

            if body.amount <= OFFLINE_FLOOR_LIMIT:

                offline_tx = {
                    "card_number": card_number,
                    "amount": body.amount,
                    "currency": body.currency,
                    "merchant_id": body.merchant_id,
                    "merchant_name": body.merchant_name,
                    "created_at": str(time.time())
                }

                save_offline_transaction(
                    offline_tx
                )

                print("OFFLINE STORED LOCALLY")

                return {
                    "approved": True,
                    "offline": True,
                    "response_code": "OFFLINE",
                    "authorization_code": "OFF999",
                    "message": "Approved offline (floor limit)"
                }



            raise HTTPException(
                status_code=503,
                detail="Authorization server unavailable"
            )


    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )
# --- POS terminal (GUI) ---

@app.get("/pos", response_class=HTMLResponse, tags=["POS"],
         summary="Web POS Terminal Emulator",
         description="Przeglądarkowy emulator terminala płatniczego: http://localhost:8072/pos")
async def pos_terminal(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="pos.html"
    )
# --- batch offline ---
@app.post(
    "/api/v1/payments/replay-offline",
    tags=["Płatności offline"],
    summary="Przetworzenie zapisanych transakcji offline",
    description="""
Endpoint techniczny służący do ponownego przesłania transakcji
zaakceptowanych lokalnie w trybie offline.

Nie jest to standardowy endpoint płatniczy.

Używany wyłącznie po odzyskaniu połączenia z systemem autoryzacji.

Mechanizm:
- odczytuje lokalną kolejkę transakcji offline
- ponawia autoryzację każdej transakcji
- usuwa poprawnie przetworzone rekordy
- pozostawia nieudane do kolejnej próby

Symuluje mechanizm batch upload / store-and-forward.
"""
)
async def replay_offline_transactions():
    transactions = load_offline_transactions()

    if not transactions:
        return {
            "success": True,
            "message": "No offline transactions"
        }

    remaining_transactions = []

    for tx in transactions:
        try:
            iso_message = {
                "t": "0100",
                "2": tx["card_number"],
                "4": str(int(tx["amount"] * 100)).zfill(12),
                "14": "0529",
                "41": "POS00001".ljust(8),
                "42": tx["merchant_id"][:15].ljust(15),
                "49": tx["currency"],
                "52": "889"
            }

            raw_iso, _ = encode(
                iso_message,
                spec
            )

            decoded = await send_iso(raw_iso)

            if decoded["39"] == "00":
                print(
                    "OFFLINE REPLAY SUCCESS:",
                    tx["card_number"]
                )
            else:
                print(
                    "OFFLINE REPLAY DECLINED:",
                    tx["card_number"]
                )
                remaining_transactions.append(tx)

        except Exception as e:
            print(
                "OFFLINE REPLAY FAILED:",
                str(e)
            )
            remaining_transactions.append(tx)

    clear_offline_transactions(
        remaining_transactions
    )

    return {
        "success": True,
        "processed":
            len(transactions)
            - len(remaining_transactions),
        "remaining":
            len(remaining_transactions)
    }
@app.get(
    "/api/v1/admin/revenue",
    tags=["Admin / Przychody"],
    summary="Podsumowanie przychodów z MSC",
    description="""
Endpoint administracyjny pokazujący przychód operatora kart
z opłat MSC (Merchant Service Charge).

Zawiera:
- łączny przychód
- interchange fee
- scheme fee
- acquirer fee
- liczbę transakcji
- średni przychód na transakcję
"""
)
async def get_revenue():
    async with AsyncSessionLocal() as db:

        result = await db.execute(
            select(
                func.count(TransactionFee.id),
                func.sum(TransactionFee.interchange_fee),
                func.sum(TransactionFee.scheme_fee),
                func.sum(TransactionFee.acquirer_fee),
                func.sum(TransactionFee.total_fee)
            )
        )

        (
            total_transactions,
            interchange_sum,
            scheme_sum,
            acquirer_sum,
            total_revenue
        ) = result.one()

        avg_revenue = (
            float(total_revenue) / total_transactions
            if total_transactions and total_revenue
            else 0
        )

        return {
            "transactions_count": total_transactions,
            "interchange_fee_total": float(interchange_sum or 0),
            "scheme_fee_total": float(scheme_sum or 0),
            "acquirer_fee_total": float(acquirer_sum or 0),
            "total_revenue": float(total_revenue or 0),
            "average_revenue_per_transaction": avg_revenue
        }
# docker exec -it cards_postgres psql -U bank_user -d cards_db
# SELECT * FROM transaction_fees;
#SELECT
#    COUNT(*) AS transactions_count,
#    SUM(interchange_fee) AS interchange_total,
#    SUM(scheme_fee) AS scheme_total,
#    SUM(acquirer_fee) AS acquirer_total,
#    SUM(total_fee) AS total_revenue
#FROM transaction_fees;
#