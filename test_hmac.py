# test_hmac.py
# Skrypt testów bezpieczeństwa API bramy (Payment Gateway).
# Nie używa frameworka testowego — to ręczny smoke test: odpala kolejne
# scenariusze przez HTTP i wypisuje status + odpowiedź do oceny "na oko".
# WYMAGA działającej bramy pod BASE_URL (docker compose up).

import hmac          # liczenie podpisu HMAC-SHA256 (jak po stronie banku)
import hashlib       # funkcja skrótu SHA-256 dla HMAC
import time          # timestamp uniksowy do podpisu (ochrona przed replay)
import json          # serializacja body identyczna jak na serwerze
import requests      # klient HTTP do strzelania do endpointów

API_KEY = "bank-key-pl-a"          # klucz API banku POLISH_BANK_A (z seedu)
HMAC_SECRET = "secret-pl-a-hmac"   # sekret HMAC tego banku — musi zgadzać się z serwerem
BASE_URL = "http://localhost:8072" # adres bramy (port wystawiony w docker-compose)


def generate_signature(body: dict, secret: str) -> tuple[str, str]:
    """
    Tworzy podpis żądania DOKŁADNIE tak, jak robi to bank i jak weryfikuje brama.
    Kluczowe: kanonikalizacja body musi być identyczna po obu stronach, inaczej
    podpisy się nie zgodzą.
      - timestamp: bieżący czas uniksowy jako string (brama sprawdza wiek <30s),
      - body_json: JSON bez spacji (separators) i z posortowanymi kluczami (sort_keys),
        dzięki czemu kolejność pól w dict nie wpływa na wynik,
      - payload = timestamp + body_json — to nad tym liczony jest HMAC,
      - signature: HMAC-SHA256(payload, secret) w hex.
    Zwraca (signature, timestamp) — oba trafiają do nagłówków żądania.
    """
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
    """
    ŚCIEŻKA POZYTYWNA: poprawnie podpisane wydanie karty powinno przejść (200).
    Wywoływana dla VIRTUAL / PHYSICAL / PREPAID, żeby pokryć wszystkie typy.
    Zwraca card_token z odpowiedzi (używany potem w teście GET).
    """
    print(f"\n{'='*50}")
    print(f"TEST: Wydanie karty {card_type}")
    print('='*50)

    body = {
        "user_id": "test_user_1",
        "account_id": "test_acc_1",
        "card_type": card_type,
        "initial_balance": initial_balance,
    }

    # poprawny podpis prawdziwym sekretem banku
    signature, timestamp = generate_signature(body, HMAC_SECRET)

    print(f"Timestamp:  {timestamp}")
    print(f"Signature:  {signature[:20]}...")   # tylko fragment, dla czytelności logu

    response = requests.post(
        f"{BASE_URL}/api/v1/cards/issue",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": API_KEY,
            "X-Signature": signature,
            "X-Timestamp": timestamp,
        },
        json=body   # WAŻNE: to samo body, nad którym liczono podpis
    )

    print(f"Status:     {response.status_code}")
    print(f"Response:   {json.dumps(response.json(), indent=2)}")
    return response.json().get("card_token")


def test_invalid_api_key():
    """
    ŚCIEŻKA NEGATYWNA: nieznany X-API-Key.
    Podpis liczony jest losowym sekretem, ale to bez znaczenia — brama najpierw
    sprawdza, czy klucz w ogóle istnieje, i odrzuca na tym etapie. Oczekiwane: 401.
    """
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
            "X-API-Key": "zly-klucz-hakera",   # klucz spoza słownika -> odrzut
            "X-Signature": signature,
            "X-Timestamp": timestamp,
        },
        json=body
    )

    print(f"Status:     {response.status_code} (oczekiwany: 401)")
    print(f"Response:   {response.json()}")


def test_wrong_signature():
    """
    ŚCIEŻKA NEGATYWNA: prawidłowy klucz, ale podpis zmyślony.
    Dowodzi, że samo posiadanie klucza API nie wystarcza — bez znajomości
    sekretu HMAC nie da się podrobić podpisu. Oczekiwane: 401.
    """
    print(f"\n{'='*50}")
    print("TEST: Prawidłowy klucz API, zły podpis HMAC")
    print('='*50)

    body = {
        "user_id": "haker",
        "account_id": "haker_acc",
        "card_type": "VIRTUAL",
        "initial_balance": 999999,   # próba "wydania" karty z dużym saldem
    }

    timestamp = str(int(time.time()))
    # podpis wzięty z sufitu — nie pochodzi z żadnego sekretu
    fake_signature = "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899"

    response = requests.post(
        f"{BASE_URL}/api/v1/cards/issue",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": API_KEY,            # klucz poprawny...
            "X-Signature": fake_signature,   # ...ale podpis się nie zgodzi
            "X-Timestamp": timestamp,
        },
        json=body
    )

    print(f"Status:     {response.status_code} (oczekiwany: 401)")
    print(f"Response:   {response.json()}")


def test_replay_attack():
    """
    ŚCIEŻKA NEGATYWNA: atak typu replay przez PRZETERMINOWANY timestamp.
    Podpis jest tu POPRAWNY (liczony prawdziwym sekretem), ale nad timestampem
    sprzed 60 s. Brama odrzuca, bo żądanie jest starsze niż okno 30 s.
    Testuje "świeżość" żądania (jeden z dwóch mechanizmów anty-replay). Oczekiwane: 401.
    """
    print(f"\n{'='*50}")
    print("TEST: Replay attack – stary timestamp (>30s)")
    print('='*50)

    body = {
        "user_id": "haker",
        "account_id": "haker_acc",
        "card_type": "VIRTUAL",
        "initial_balance": 0,
    }

    # ręcznie budujemy podpis nad starym timestampem (generate_signature użyłby "teraz")
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
            "X-Timestamp": old_timestamp,   # za stary -> odrzut
        },
        json=body
    )

    print(f"Status:     {response.status_code} (oczekiwany: 401)")
    print(f"Response:   {response.json()}")


def test_get_card(card_token: str):
    """
    ŚCIEŻKA POMOCNICZA: odczyt statusu karty (GET nie wymaga podpisu).
    Korzysta z tokenu zwróconego przez pierwsze wydanie karty. Jeśli tokenu brak
    (np. wydanie się nie powiodło), test jest pomijany. Oczekiwane: 200.
    """
    print(f"\n{'='*50}")
    print("TEST: Sprawdzenie karty (GET)")
    print('='*50)

    if not card_token:
        print("Brak tokenu – pomiń")
        return

    response = requests.get(f"{BASE_URL}/api/v1/cards/{card_token}")
    print(f"Status:     {response.status_code}")
    print(f"Response:   {json.dumps(response.json(), indent=2)}")


def test_replay_same_signature():
    """
    ŚCIEŻKA NEGATYWNA: właściwy replay — TEN SAM ważny podpis wysłany dwa razy.
    Różni się od test_replay_attack: timestamp jest świeży (mieści się w oknie 30 s),
    więc check świeżości go przepuszcza. Odrzut musi pochodzić z DRUGIEGO mechanizmu:
    cache zużytych podpisów (_seen_signatures) po stronie bramy.
      - 1. żądanie: podpis nowy  -> 200, karta powstaje,
      - 2. żądanie: ten sam podpis w oknie -> 401 "Replay detected".
    Oba strzały lecą natychmiast po sobie, żeby zmieścić się w 30 s.
    """
    print(f"\n{'='*50}")
    print("TEST: Replay – ten sam ważny podpis wysłany 2x")
    print('='*50)

    body = {
        "user_id": "replay_user",
        "account_id": "replay_acc",
        "card_type": "VIRTUAL",
        "initial_balance": 0,
    }

    # jeden podpis policzony raz i użyty dwukrotnie (to jest sedno testu)
    signature, timestamp = generate_signature(body, HMAC_SECRET)
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": API_KEY,
        "X-Signature": signature,
        "X-Timestamp": timestamp,
    }

    r1 = requests.post(f"{BASE_URL}/api/v1/cards/issue", headers=headers, json=body)
    print(f"1. raz:  {r1.status_code} (oczekiwany: 200)")

    r2 = requests.post(f"{BASE_URL}/api/v1/cards/issue", headers=headers, json=body)
    print(f"2. raz:  {r2.status_code} (oczekiwany: 401 – replay)")
    print(f"Response: {r2.json()}")


def test_activate_requires_signature():
    """
    ŚCIEŻKA NEGATYWNA: dowód, że wymóg podpisu objął też /activate.
    Wysyłamy SAM X-API-Key, bez X-Signature/X-Timestamp -> brama musi odrzucić (401),
    zanim w ogóle dojdzie do logiki karty. Dlatego token może być nieistniejący
    ("tok_nieistnieje") — i tak nie powinniśmy dotrzeć do sprawdzania karty.
    Gdyby tu wpadło 404 (card not found), znaczyłoby, że auth NIE działa.
    """
    print(f"\n{'='*50}")
    print("TEST: Activate bez podpisu – musi odrzucić")
    print('='*50)

    response = requests.post(
        f"{BASE_URL}/api/v1/cards/tok_nieistnieje/activate",
        headers={"Content-Type": "application/json", "X-API-Key": API_KEY},  # brak podpisu
        json={"activated_by": "customer"},
    )
    print(f"Status:   {response.status_code} (oczekiwany: 401 – brak podpisu)")
    print(f"Response: {response.json()}")


if __name__ == "__main__":
    # Uruchamia wszystkie scenariusze po kolei. Czytasz logi i porównujesz
    # status z "oczekiwany: ..." w nawiasach. Wymaga wstającej bramy.
    print("START TESTÓW BEZPIECZEŃSTWA")
    print("="*50)

    token = test_issue_card("VIRTUAL")            # 200 + zapamiętany token
    test_issue_card("PHYSICAL")                   # 200
    test_issue_card("PREPAID", initial_balance=500)  # 200
    test_invalid_api_key()                        # 401 – zły klucz
    test_wrong_signature()                        # 401 – podrobiony podpis
    test_replay_attack()                          # 401 – stary timestamp (świeżość)
    test_get_card(token)                          # 200 – odczyt po tokenie
    test_replay_same_signature()                  # 200, potem 401 (cache podpisów)
    test_activate_requires_signature()            # 401 – activate wymaga podpisu

    print(f"\n{'='*50}")
    print("KONIEC TESTÓW")