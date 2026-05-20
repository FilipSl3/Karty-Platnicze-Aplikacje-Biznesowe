import hmac
import hashlib
import time
import json
import requests

API_KEY = "bank-key-pl-a"
HMAC_SECRET = "secret-pl-a-hmac"
BASE_URL = "http://localhost:8072"


def generate_signature(body: dict, secret: str) -> tuple[str, str]:
    timestamp = str(int(time.time()))
    body_json = json.dumps(body, separators=(',', ':'), sort_keys=True)
    payload = timestamp + body_json
    signature = hmac.new(
        secret.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature, timestamp


def test_issue_card(card_type: str, initial_balance: float = 0):
    print(f"\n{'='*50}")
    print(f"TEST: Wydanie karty {card_type}")
    print('='*50)

    body = {
        "user_id": "test_user_1",
        "account_id": "test_acc_1",
        "card_type": card_type,
        "initial_balance": initial_balance,
    }

    signature, timestamp = generate_signature(body, HMAC_SECRET)

    print(f"Timestamp:  {timestamp}")
    print(f"Signature:  {signature[:20]}...")

    response = requests.post(
        f"{BASE_URL}/api/v1/cards/issue",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": API_KEY,
            "X-Signature": signature,
            "X-Timestamp": timestamp,
        },
        json=body
    )

    print(f"Status:     {response.status_code}")
    print(f"Response:   {json.dumps(response.json(), indent=2)}")
    return response.json().get("card_token")


def test_invalid_api_key():
    print(f"\n{'='*50}")
    print("TEST: Nieprawidłowy klucz API")
    print('='*50)

    body = {
        "user_id": "haker",
        "account_id": "haker_acc",
        "card_type": "VIRTUAL",
        "initial_balance": 0,
    }

    signature, timestamp = generate_signature(body, "jakis-losowy-sekret")

    response = requests.post(
        f"{BASE_URL}/api/v1/cards/issue",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": "zly-klucz-hakera",
            "X-Signature": signature,
            "X-Timestamp": timestamp,
        },
        json=body
    )

    print(f"Status:     {response.status_code} (oczekiwany: 401)")
    print(f"Response:   {response.json()}")


def test_wrong_signature():
    print(f"\n{'='*50}")
    print("TEST: Prawidłowy klucz API, zły podpis HMAC")
    print('='*50)

    body = {
        "user_id": "haker",
        "account_id": "haker_acc",
        "card_type": "VIRTUAL",
        "initial_balance": 999999,
    }

    timestamp = str(int(time.time()))
    fake_signature = "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899"

    response = requests.post(
        f"{BASE_URL}/api/v1/cards/issue",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": API_KEY,
            "X-Signature": fake_signature,
            "X-Timestamp": timestamp,
        },
        json=body
    )

    print(f"Status:     {response.status_code} (oczekiwany: 401)")
    print(f"Response:   {response.json()}")


def test_replay_attack():
    print(f"\n{'='*50}")
    print("TEST: Replay attack – stary timestamp (>30s)")
    print('='*50)

    body = {
        "user_id": "haker",
        "account_id": "haker_acc",
        "card_type": "VIRTUAL",
        "initial_balance": 0,
    }

    old_timestamp = str(int(time.time()) - 60)
    body_json = json.dumps(body, separators=(',', ':'), sort_keys=True)
    payload = old_timestamp + body_json
    old_signature = hmac.new(
        HMAC_SECRET.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    response = requests.post(
        f"{BASE_URL}/api/v1/cards/issue",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": API_KEY,
            "X-Signature": old_signature,
            "X-Timestamp": old_timestamp,
        },
        json=body
    )

    print(f"Status:     {response.status_code} (oczekiwany: 401)")
    print(f"Response:   {response.json()}")


def test_get_card(card_token: str):
    print(f"\n{'='*50}")
    print("TEST: Sprawdzenie karty (GET)")
    print('='*50)

    if not card_token:
        print("Brak tokenu – pomiń")
        return

    response = requests.get(f"{BASE_URL}/api/v1/cards/{card_token}")
    print(f"Status:     {response.status_code}")
    print(f"Response:   {json.dumps(response.json(), indent=2)}")


if __name__ == "__main__":
    print("START TESTÓW BEZPIECZEŃSTWA")
    print("="*50)

    token = test_issue_card("VIRTUAL")
    test_issue_card("PHYSICAL")
    test_issue_card("PREPAID", initial_balance=500)
    test_invalid_api_key()
    test_wrong_signature()
    test_replay_attack()
    test_get_card(token)

    print(f"\n{'='*50}")
    print("KONIEC TESTÓW")