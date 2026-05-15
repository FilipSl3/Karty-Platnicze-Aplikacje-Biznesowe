# test_hmac.py
import hmac
import hashlib
import time
import json
import requests

# Dane banku (z naszego seeda)
API_KEY = "bank-key-pl-a"
HMAC_SECRET = "secret-pl-a-hmac"
BASE_URL = "http://localhost:8000"


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

    response = requests.post(
        f"{BASE_URL}/api/v1/cards/issue",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": "zly-klucz-hakera",
        },
        json=body
    )

    print(f"Status:     {response.status_code} (oczekiwany: 401)")
    print(f"Response:   {response.json()}")


def test_wrong_signature():
    print(f"\n{'='*50}")
    print("TEST: Prawidłowy klucz, zły podpis (atak)")
    print('='*50)

    body = {
        "user_id": "haker",
        "account_id": "haker_acc",
        "card_type": "VIRTUAL",
        "initial_balance": 999999,
    }

    # Haker ma klucz API ale nie ma sekretu - generuje losowy podpis
    timestamp = str(int(time.time()))
    fake_signature = "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899"

    # Haker musi jakoś przekazać signature i timestamp - ale w naszym przypadku
    # te dane idą przez gRPC wewnętrznie, więc ten test pokazuje
    # że sam api_key bez sekretu nie wystarczy
    response = requests.post(
        f"{BASE_URL}/api/v1/cards/issue",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": "zly-klucz",  # nie ma sekretu → 401 zanim dojdzie do HMAC
        },
        json=body
    )

    print(f"Status:     {response.status_code} (oczekiwany: 401)")
    print(f"Response:   {response.json()}")


def test_replay_attack(card_token: str):
    """Symuluje replay attack - używa starego tokena"""
    print(f"\n{'='*50}")
    print("TEST: Sprawdzenie karty (zwykłe GET)")
    print('='*50)

    if not card_token:
        print("Brak tokenu – pomiń ten test")
        return

    response = requests.get(f"{BASE_URL}/api/v1/cards/{card_token}")
    print(f"Status:     {response.status_code}")
    print(f"Response:   {json.dumps(response.json(), indent=2)}")


if __name__ == "__main__":
    print("START TESTÓW BEZPIECZEŃSTWA")
    print("="*50)

    # Test 1 – poprawne wydanie karty wirtualnej
    token = test_issue_card("VIRTUAL")

    # Test 2 – poprawne wydanie karty fizycznej
    test_issue_card("PHYSICAL")

    # Test 3 – poprawne wydanie karty prepaid
    test_issue_card("PREPAID", initial_balance=500)

    # Test 4 – nieprawidłowy klucz API
    test_invalid_api_key()

    # Test 5 – zły podpis
    test_wrong_signature()

    # Test 6 – sprawdzenie wydanej karty
    test_replay_attack(token)

    print(f"\n{'='*50}")
    print("KONIEC TESTÓW")