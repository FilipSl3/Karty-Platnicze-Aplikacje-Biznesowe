# 💳 Moduł: Karty Płatnicze

> **Projekt grupowy – Aplikacja Bankowa**  
> Moduł odpowiedzialny za obsługę kart płatniczych, płatności internetowych kartą oraz autoryzację transakcji.

---

## 📋 Spis treści

1. [Opis modułu](#opis-modułu)
2. [Jak to działa w rzeczywistości – Model 4-stronny](#jak-to-działa-w-rzeczywistości)
3. [Przepływ płatności internetowej kartą](#przepływ-płatności-internetowej-kartą)
4. [Architektura systemu](#architektura-systemu)
5. [Schemat bazy danych](#schemat-bazy-danych)
6. [Technologie](#technologie)
7. [API – Kontrakt dla innych zespołów](#api--kontrakt-dla-innych-zespołów)
8. [Scenariusze wydawania kart ⚠️ DO USTALENIA](#scenariusze-wydawania-kart)
9. [Obsługa zwrotów](#obsługa-zwrotów)
10. [Routing – Tabela BIN](#routing--tabela-bin)
11. [Bezpieczeństwo](#bezpieczeństwo)
12. [Mock Server](#mock-server)
13. [Checklist – Plan działań](#checklist--plan-działań)
14. [Pytania do ustalenia z prowadzącym](#pytania-do-ustalenia-z-prowadzącym)

---

## Opis modułu

Moduł **Karty Płatnicze** pełni rolę **procesora płatności** (Acquirer/Processor) w całym ekosystemie bankowym projektu. Jego zadaniem jest:

- Wydawanie wirtualnych kart płatniczych powiązanych z kontami bankowymi
- Przyjmowanie żądań płatności internetowych (klient podaje numer karty, datę ważności, CVV)
- Rozpoznawanie, do którego banku należy dana karta (routing po numerze BIN)
- Komunikacja z bankami-wydawcami w celu autoryzacji transakcji
- Zarządzanie cyklem życia transakcji (`PENDING → AUTHORIZED → CAPTURED → SETTLED`)
- Obsługa zwrotów środków z poziomu panelu administratora
- Blokowanie i odblokowywanie kart

---

## Jak to działa w rzeczywistości

### Dlaczego skupiamy się na płatnościach internetowych, a nie fizycznych terminalach POS?

Fizyczne terminale POS w prawdziwym świecie komunikują się za pomocą protokołów **ISO 8583** lub **Nexo Retailer**. Są to bardzo rozbudowane, binarne protokoły telekomunikacyjne (ISO 8583 ma ponad 128 pól danych, specyficzne kodowanie bitowe, własne mechanizmy szyfrowania kluczy DEK/KEK), których prawidłowa implementacja to osobny, wielomiesięczny projekt certyfikacyjny. Wymagają też sprzętu HSM (Hardware Security Module) do zarządzania kluczami kryptograficznymi.

**Płatności internetowe** (e-commerce) działają natomiast przez standardowe REST API z JSON-em – czyli dokładnie ten sam mechanizm, którego używamy do budowy całej aplikacji. Od strony logiki biznesowej (autoryzacja, routing BIN, clearing) są **identyczne** z płatnościami terminalowymi, co oznacza, że projekt jest edukacyjnie pełnowartościowy i realistyczny.

**Płatności internetowe i fizyczne - Fizyczne Nie http ale możemy trochę uprościć ten ISO 8583 - Czy możemy to zrobić na socketach, np 8 pól zamiast 128**

> ⚠️ **Do ustalenia z prowadzącym:** Czy skupiamy się wyłącznie na płatnościach internetowych (klient wpisuje dane karty na stronie), czy jednak implementujemy też symulację terminala POS jako dodatkowy interfejs wejściowy? (patrz [Pytania do ustalenia](#pytania-do-ustalenia-z-prowadzącym))

---

### Model 4-stronny (Four-Party Scheme)

To fundament działania każdej płatności kartą na świecie – zarówno w internecie, jak i na terminalu.

```
┌─────────────────┐         ┌───────────────────────────┐
│    KLIENT       │         │    NASZ MODUŁ             │
│                 │  dane   │    (Payment Processor /   │
│  Wpisuje dane   │────────▶│     Acquirer)             │
│  karty na       │         │                           │
│  stronie        │◀────────│  - identyfikuje bank      │
│                 │ wynik   │    po BIN                 │
└─────────────────┘         │  - routuje zapytanie      │
                            └──────────┬────────────────┘
                                       │  zapytanie auth
                                       ▼
                            ┌───────────────────────────┐
                            │    BANK-WYDAWCA           │
                            │    (np. Polski Bank A)    │
                            │                           │
                            │  - weryfikuje saldo       │
                            │  - sprawdza limity        │
                            │  - blokuje środki         │
                            │  - odpowiada APPROVED /   │
                            │    DECLINED               │
                            └───────────────────────────┘
```

**Etap 1 – Autoryzacja** (real-time, milisekundy):
- Klient wpisuje dane karty → trafiają do naszego modułu
- My identyfikujemy bank-wydawcę po BIN i pytamy: *„Czy karta X może zapłacić 150 PLN?"*
- Bank blokuje środki (authorization hold) i odsyła `APPROVED` lub `DECLINED`
- Środki nie są jeszcze przelane – to tylko rezerwacja

**Etap 2 – Capture** (potwierdzenie, chwilę po autoryzacji):
- Merchant potwierdza, że transakcja doszła do skutku
- Bank zmienia status blokady – środki są przypisane do rozliczenia

**Etap 3 – Clearing/Settlement** (rozliczenie, zazwyczaj T+1):
- Faktyczny transfer środków między bankami
- W naszym projekcie implementujemy jako zmianę statusu `CAPTURED → SETTLED`

---

## Przepływ płatności internetowej kartą

Poniżej pełny, realistyczny przepływ płatności kartą w modelu e-commerce:

```
1. KLIENT wpisuje na stronie:
   ┌─────────────────────────────────────┐
   │  Numer karty:  4100 **** **** 1234  │
   │  Ważna do:     12/27                │
   │  CVV:          ***                  │
   │  Kwota:        150.00 PLN           │
   └─────────────────────────────────────┘
                    │
                    ▼ HTTPS POST /api/v1/payments/authorize
2. NASZ MODUŁ – Payment Gateway
   ├── Walidacja formatu danych (Luhn check na numerze karty)
   ├── Weryfikacja daty ważności
   ├── Weryfikacja CVV (sprawdź i zapomnij – NIE zapisuj!)
   ├── Sprawdzenie statusu karty w CMS (czy nie BLOCKED/EXPIRED)
   ├── Sprawdzenie limitów transakcyjnych
   ├── Lookup BIN → identyfikacja banku-wydawcy
   └── Tokenizacja PAN → wewnętrzny token
                    │
                    ▼ POST /api/v1/authorize (do banku-wydawcy)
3. BANK-WYDAWCA (np. Polski Bank A)
   ├── Weryfikacja salda konta
   ├── Blokada środków (authorization hold)
   └── Odpowiedź: { status: "APPROVED", auth_code: "AUTH-789" }
                    │
                    ▼
4. NASZ MODUŁ – Transaction Engine
   ├── Zapis transakcji ze statusem AUTHORIZED
   ├── Zapis do transaction_status_history
   └── Odpowiedź do klienta: { status: "AUTHORIZED", transaction_id: "uuid" }
                    │
                    ▼ POST /api/v1/payments/{id}/capture
5. CAPTURE (potwierdzenie transakcji)
   ├── Nasz moduł informuje bank: "Transakcja doszła do skutku"
   └── Status zmienia się: AUTHORIZED → CAPTURED
                    │
                    ▼ (asynchronicznie, T+1)
6. SETTLEMENT
   └── Status zmienia się: CAPTURED → SETTLED
```

**Algorytm Luhna** – weryfikacja numeru karty przed wysłaniem do banku:
```
Numer: 4 1 0 0 1 2 3 4 5 6 7 8 9 0 1 2
       ↓ podwój co drugą cyfrę od prawej, odejmij 9 jeśli > 9
       Suma cyfr % 10 == 0 → karta poprawna formatowo
```

---

## Architektura systemu

```
┌──────────────────────────────────────────────────────────────────────┐
│                       MODUŁ KARTY PŁATNICZE                          │
│                                                                      │
│  ┌─────────────────┐   ┌──────────────────┐   ┌──────────────────┐  │
│  │ Payment Gateway │   │ Card Management   │   │ Transaction      │  │
│  │  (REST API)     │──▶│ System (CMS)      │──▶│ Engine           │  │
│  │                 │   │                   │   │                  │  │
│  │ - Przyjmuje dane│   │ - Rejestr kart    │   │ - Routing po BIN │  │
│  │   karty         │   │ - Statusy kart    │   │ - Auth request   │  │
│  │ - Luhn check    │   │ - Limity          │   │ - Capture        │  │
│  │ - CVV verify    │   │ - Tokenizacja PAN │   │ - Settlement     │  │
│  │ - Rate limiting │   │ - Powiązanie      │   │ - Obsługa błędów │  │
│  └─────────────────┘   │   karta<->konto   │   │ - Timeouty       │  │
│                        └──────────────────┘   └────────┬─────────┘  │
│  ┌─────────────────┐                                   │            │
│  │  Admin Panel    │                                   │            │
│  │                 │                           Routing po BIN       │
│  │ - Transakcje    │                                   │            │
│  │ - Przycisk Zwróć│                                   │            │
│  │ - Blok./Odblok. │                                   │            │
│  └─────────────────┘                                   │            │
└───────────────────────────────────────────────────────┼────────────┘
                                                        │
          ┌─────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│    MOCK SERVER (fallback gdy bank niedostępny)                  │
│    lub rzeczywiste wywołanie do modułu banku                    │
└─────────┬───────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│              INNE MODUŁY (BANKI)                                │
│                                                                 │
│  Polski Bank A  |  Polski Bank B                               │
│  Bank Euro A    |  Bank Euro B                                 │
│  Bank UK A      |  Bank UK B                                   │
│  Bank USA A     |  Bank USA B                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Komponenty

#### A. Payment Gateway
Punkt wejściowy dla płatności internetowych.

**Odpowiedzialności:**
- Przyjmowanie danych karty od klienta (numer, expiry, CVV, kwota)
- Walidacja numeru karty algorytmem Luhna
- Weryfikacja daty ważności
- Weryfikacja CVV (weryfikuj, NIE przechowuj)
- Rate limiting – ochrona przed atakami brute-force na numery kart
- Przekazanie do Transaction Engine

#### B. Card Management System (CMS)
Centralna baza danych kart.

**Odpowiedzialności:**
- Przechowywanie zamaskowanych danych karty (token + masked PAN, nigdy pełny PAN)
- Zarządzanie statusem karty (`ACTIVE`, `BLOCKED`, `EXPIRED`, `CANCELLED`)
- Zarządzanie limitami (dzienny, per transakcja)
- Powiązanie karty z rachunkiem bankowym i bankiem-wydawcą

#### C. Transaction Engine
Serce systemu – logika biznesowa autoryzacji i rozliczenia.

**Odpowiedzialności:**
- Routing: identyfikacja banku-wydawcy po numerze BIN
- Wysłanie żądania autoryzacyjnego do odpowiedniego banku (lub Mock Servera)
- Obsługa cyklu życia transakcji: PENDING → AUTHORIZED → CAPTURED → SETTLED
- Obsługa timeoutów i fallback do Mock Servera
- Zapis pełnej historii statusów (audit log)

---

## Schemat bazy danych

```sql
-- Karty płatnicze
CREATE TABLE cards (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL,           -- powiązanie z użytkownikiem
    bank_id         VARCHAR(50) NOT NULL,    -- np. 'POLISH_BANK_A'
    account_id      UUID NOT NULL,           -- powiązany rachunek w banku
    token           VARCHAR(64) UNIQUE NOT NULL, -- token zamiast PAN
    masked_pan      VARCHAR(19) NOT NULL,    -- np. **** **** **** 1234
    expiry_month    SMALLINT NOT NULL,
    expiry_year     SMALLINT NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
                    -- ACTIVE | BLOCKED | EXPIRED | CANCELLED
    daily_limit     DECIMAL(12,2) NOT NULL DEFAULT 1000.00,
    single_tx_limit DECIMAL(12,2) NOT NULL DEFAULT 500.00,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Transakcje
CREATE TABLE transactions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    card_id             UUID NOT NULL REFERENCES cards(id),
    merchant_name       VARCHAR(100),            -- nazwa sklepu/usługi
    merchant_url        VARCHAR(255),            -- URL sklepu (e-commerce)
    amount              DECIMAL(12,2) NOT NULL,
    currency            CHAR(3) NOT NULL DEFAULT 'PLN',
    status              VARCHAR(20) NOT NULL,
                        -- PENDING | AUTHORIZED | CAPTURED | SETTLED
                        -- | DECLINED | REFUNDED | FAILED
    failure_reason      VARCHAR(100),            -- powód odrzucenia
    authorization_code  VARCHAR(20),             -- kod z banku-wydawcy
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Historia statusów transakcji (audit log)
CREATE TABLE transaction_status_history (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id  UUID NOT NULL REFERENCES transactions(id),
    old_status      VARCHAR(20),
    new_status      VARCHAR(20) NOT NULL,
    changed_by      VARCHAR(50),             -- 'system' lub admin_id
    reason          TEXT,
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Tabela routingu BIN
CREATE TABLE bin_routing (
    bin_prefix      VARCHAR(6) PRIMARY KEY,  -- pierwsze 4-6 cyfr karty
    bank_id         VARCHAR(50) NOT NULL,    -- do którego banku routować
    currency        CHAR(3) NOT NULL,
    country         CHAR(2) NOT NULL
);
```

---

## Technologie

| Warstwa | Technologia | Uzasadnienie |
|---|---|---|
| Backend | **Java 21 + Spring Boot 3** lub **Go (Gin/Fiber)** | Standard w fintechu, silne typowanie, wydajność |
| Baza danych | **PostgreSQL 16** | ACID – transakcyjność krytyczna przy płatnościach |
| Komunikacja async | **RabbitMQ** lub **Apache Kafka** | Zdarzenia settlement/clearing są z natury asynchroniczne |
| Komunikacja sync | **REST API (JSON)** | Autoryzacja musi być synchroniczna (real-time) |
| Bezpieczeństwo | **HTTPS / TLS**, tokenizacja PAN | Ochrona danych karty |
| Konteneryzacja | **Docker + Docker Compose** | Łatwe uruchomienie, integracja z innymi modułami |
| Dokumentacja API | **OpenAPI 3.0 / Swagger** | Kontrakt dla innych zespołów |

---

## API – Kontrakt dla innych zespołów

> ⚠️ **To jest najważniejsza sekcja dla pozostałych zespołów.**

### Co nasz moduł udostępnia (nasze API)

#### `POST /api/v1/cards/issue`
Wydanie nowej karty płatniczej.

```json
// Request
{
  "user_id": "uuid",
  "account_id": "uuid",
  "bank_id": "POLISH_BANK_A",
  "currency": "PLN"
}

// Response 201 Created
{
  "card_id": "uuid",
  "token": "tok_abc123xyz",
  "masked_pan": "**** **** **** 7890",
  "expiry_month": 3,
  "expiry_year": 2029,
  "status": "ACTIVE"
}
```

---

#### `POST /api/v1/payments/authorize`
Autoryzacja płatności internetowej kartą – **główny endpoint**.

> Klient podaje dane karty, nasz moduł waliduje je i pyta bank-wydawcę.

```json
// Request
{
  "card_number": "4100123456789012",   // pełny PAN – tylko tutaj, natychmiast tokenizowany
  "expiry_month": 12,
  "expiry_year": 27,
  "cvv": "123",                        // NIE jest przechowywany
  "amount": 150.00,
  "currency": "PLN",
  "merchant_name": "Sklep Online XYZ",
  "merchant_url": "https://sklep.example.com"
}

// Response 200 – Zatwierdzone
{
  "transaction_id": "uuid",
  "status": "AUTHORIZED",
  "authorization_code": "AUTH-789XYZ",
  "masked_pan": "**** **** **** 9012"
}

// Response 200 – Odrzucone
{
  "transaction_id": "uuid",
  "status": "DECLINED",
  "failure_reason": "INSUFFICIENT_FUNDS"
  // możliwe: CARD_BLOCKED | CARD_EXPIRED | LIMIT_EXCEEDED | INVALID_CVV | BANK_TIMEOUT
}
```

---

#### `POST /api/v1/payments/{transaction_id}/capture`
Potwierdzenie finalizacji transakcji.

```json
// Response 200
{
  "transaction_id": "uuid",
  "status": "CAPTURED"
}
```

---

#### `POST /api/v1/payments/{transaction_id}/refund`
Zwrot środków – wywoływany z panelu admina.

```json
// Request
{
  "initiated_by": "admin_uuid",
  "reason": "Customer request"
}

// Response 200
{
  "transaction_id": "uuid",
  "status": "REFUNDED",
  "refunded_amount": 150.00
}
```

---

#### `PATCH /api/v1/cards/{card_id}/status`
Blokowanie / odblokowanie karty.

```json
// Request
{
  "status": "BLOCKED",
  "reason": "Lost card"
}
```

---

#### `GET /api/v1/cards/{card_id}/transactions`
Historia transakcji danej karty.

---

### Czego my wymagamy od innych modułów (ich API)

> Każdy moduł **bankowy** musi implementować poniższe endpointy.

#### `POST /api/v1/authorize` ← wymagane od każdego banku

```json
// Request (my wysyłamy)
{
  "account_id": "uuid",
  "amount": 150.00,
  "currency": "PLN",
  "transaction_id": "uuid",
  "merchant_name": "Sklep Online XYZ"
}

// Response (oczekujemy)
{
  "authorization_code": "AUTH-789XYZ",
  "status": "APPROVED",
  "decline_reason": null
  // możliwe decline_reason: "INSUFFICIENT_FUNDS" | "ACCOUNT_BLOCKED" | itp.
}
```

---

#### `POST /api/v1/capture` ← wymagane od każdego banku

```json
// Request (my wysyłamy)
{
  "authorization_code": "AUTH-789XYZ",
  "transaction_id": "uuid"
}

// Response (oczekujemy)
{
  "status": "SETTLED"
}
```

---

#### `POST /api/v1/refund` ← wymagane od każdego banku

```json
// Request (my wysyłamy)
{
  "account_id": "uuid",
  "amount": 150.00,
  "currency": "PLN",
  "original_transaction_id": "uuid"
}

// Response (oczekujemy)
{
  "status": "REFUNDED"
}
```

---

## Wydawanie kart

### Karty wydawane przez banki na żądanie 

Gdy bank tworzy konto dla klienta, **bank** wywołuje nasz endpoint i zleca wydanie karty.

```
[Bank A tworzy konto] → POST /api/v1/cards/issue → [Nasz Moduł] → token karty → [Bank A]
```

---

## Obsługa zwrotów

Moduł implementuje **uproszczony proces zwrotu** (bez chargeback/sporu).

### Przepływ

```
Admin widzi transakcję w panelu
        │
        │ Klika [Zwróć]
        ▼
POST /api/v1/payments/{id}/refund
        │
        ├── Walidacja: status == CAPTURED lub SETTLED?
        ├── Wywołanie POST /api/v1/refund w banku-wydawcy
        ├── Zmiana statusu transakcji → REFUNDED
        └── Zapis w transaction_status_history (kto, kiedy)
```

### Zakres

| | |
|---|---|
| ✅ | Przycisk „Zwróć" w panelu administratora |
| ✅ | Zmiana statusu transakcji na `REFUNDED` |
| ✅ | Wywołanie endpointu `/refund` w banku-wydawcy |
| ✅ | Audit log – kto i kiedy zlecił zwrot |
| ✅ | Zwrot tylko dla transakcji w statusie `CAPTURED` lub `SETTLED` |
| ❌ | Proces chargeback (spór klient vs. sprzedawca) |
| ❌ | Wieloetapowe rozpatrywanie reklamacji |
| ❌ | Częściowe zwroty (tylko pełna kwota) |
| ❌ | Automatyczne zwroty po czasie |

---

## Routing – Tabela BIN

BIN (Bank Identification Number) to pierwsze 4–6 cyfr numeru karty. Identyfikujemy po nich, do którego banku należy karta i do kogo routować zapytanie autoryzacyjne.

**Propozycja podziału numerów w projekcie (do ustalenia ze wszystkimi zespołami):**

| Prefiks BIN | Bank | Waluta | Kraj |
|---|---|---|---|
| `4100` – `4199` | Polski Bank A | PLN | PL |
| `4200` – `4299` | Polski Bank B | PLN | PL |
| `4300` – `4399` | Bank Euro A | EUR | EU |
| `4400` – `4499` | Bank Euro B | EUR | EU |
| `4500` – `4599` | Bank Brytyjski A | GBP | GB |
| `4600` – `4699` | Bank Brytyjski B | GBP | GB |
| `4700` – `4799` | Bank Amerykański A | USD | US |
| `4800` – `4899` | Bank Amerykański B | USD | US |

> Wszystkie karty w projekcie zaczynają się od `4` – symulujemy jeden wewnętrzny procesor bez podziału na sieci (Visa/Mastercard).

---

## Bezpieczeństwo

### Tokenizacja PAN

Pełny numer karty (PAN) pojawia się w systemie **tylko raz** – w momencie przyjęcia żądania autoryzacji. Natychmiast jest zamieniany na token i nigdy nie jest zapisywany.

```
Wejście od klienta:  4100 1234 5678 9012
                             ↓ tokenizacja (SHA-256 + salt lub UUID v4)
Token w bazie:       tok_7f3a9c2b1e4d8f6a
Maska w bazie:       **** **** **** 9012
```

### Inne zasady

- **CVV** – weryfikowany przy autoryzacji, nigdy nie przechowywany (nawet zahashowany)
- **HTTPS/TLS** – cała komunikacja między modułami szyfrowana
- **Rate limiting** – max N prób autoryzacji z jednego IP / na jeden numer karty
- **Audit log** – każda zmiana statusu transakcji zapisana z `changed_by` i `timestamp`
- Logi nigdy nie zawierają pełnego PAN ani CVV

---

## Mock Server

Gdy bank-wydawca nie ma jeszcze gotowego API (albo jest niedostępny), Transaction Engine automatycznie przełącza się na **Mock Server**, który symuluje odpowiedź banku. Pozwala to rozwijać i testować nasz moduł niezależnie od postępów innych zespołów.

```java
// Pseudokod – logika fallback
public AuthorizationResponse authorize(AuthorizationRequest request) {
    try {
        return bankClient.authorize(request);              // prawdziwe wywołanie
    } catch (TimeoutException | ServiceUnavailableException e) {
        log.warn("Bank {} unavailable – switching to mock", request.getBankId());
        return mockServer.getResponse(request);            // fallback
    }
}
```

### Tryby Mock Servera

| Tryb | Zachowanie | Kiedy używać |
|---|---|---|
| `ALWAYS_APPROVE` | Zawsze `APPROVED` | Testowanie happy path |
| `ALWAYS_DECLINE` | Zawsze `DECLINED` | Testowanie obsługi odmowy |
| `INSUFFICIENT_FUNDS` | `DECLINED` – brak środków | Testowanie konkretnego błędu |
| `TIMEOUT` | Brak odpowiedzi przez N sekund | Testowanie obsługi timeoutów |
| `RANDOM` | Losowo APPROVED/DECLINED | Testy obciążeniowe |
| `REAL` | Przekierowanie do prawdziwego modułu | Integracja z gotowym bankiem |

> Tryb konfigurowany per `bank_id` – można mieć Polski Bank A na `REAL`, a resztę na `ALWAYS_APPROVE`.

### Scenariusze testowe

| Scenariusz | Numer karty | Oczekiwany wynik |
|---|---|---|
| Płatność zatwierdzona | `4100000000000001` | `AUTHORIZED` |
| Brak środków | `4100000000000002` | `DECLINED` – INSUFFICIENT_FUNDS |
| Karta zablokowana | `4100000000000003` | `DECLINED` – CARD_BLOCKED |
| Karta wygasła | `4100000000000004` | `DECLINED` – CARD_EXPIRED |
| Nieprawidłowy CVV | `4100000000000005` | `DECLINED` – INVALID_CVV |
| Przekroczony limit | `4100000000000006` | `DECLINED` – LIMIT_EXCEEDED |
| Timeout banku | `4100000000000007` | `DECLINED` – BANK_TIMEOUT |
| Nieprawidłowy numer (Luhn) | `4100000000000000` | `400 Bad Request` – przed zapytaniem do banku |

---

## Checklist – Plan działań

### Faza 1 – Podstawy

- [ ] Ustalenie prefiksów BIN z pozostałymi zespołami
- [ ] Ustalenie pytań z prowadzącym (patrz niżej)
- [ ] Uzgodnienie wspólnego formatu `user_id`, `account_id` w całym projekcie
- [ ] Schemat bazy danych + migracje (Flyway / Liquibase)
- [ ] Konfiguracja projektu: Spring Boot / Go + PostgreSQL + Docker Compose

### Faza 2 – Core

- [ ] Implementacja CMS (model `Card`, statusy, limity)
- [ ] Tokenizacja PAN
- [ ] Payment Gateway – endpoint autoryzacji z walidacją Luhna i CVV
- [ ] Transaction Engine – routing po BIN
- [ ] Mock Server – wszystkie tryby
- [ ] Cykl życia transakcji: PENDING → AUTHORIZED → CAPTURED → SETTLED

### Faza 3 – Integracje i panel

- [ ] Integracja z pierwszym bankiem (Polski Bank A lub mock)
- [ ] Admin Panel – lista transakcji + przycisk Zwróć
- [ ] Blokowanie / odblokowanie kart
- [ ] Endpoint `/refund` + wywołanie do banku
- [ ] OpenAPI / Swagger – dokumentacja dla innych zespołów

### Faza 4 – Testy i finalizacja

- [ ] Testy integracyjne ze wszystkimi modułami bankowymi
- [ ] Pokrycie wszystkich scenariuszy testowych (tabela wyżej)
- [ ] Testy timeoutów i fallback do Mock Servera
- [ ] Finalizacja dokumentacji README

---

## Pytania do ustalenia z prowadzącym

> Poniższe pytania dotyczą zakresu projektu, podziału odpowiedzialności między modułami i zgodności z rzeczywistymi standardami. Warto ustalić je przed startem implementacji.

---

### Zakres funkcjonalny

**1. Płatności internetowe vs. terminal POS**
Czy skupiamy się wyłącznie na płatnościach internetowych (klient wpisuje dane karty w formularzu), czy implementujemy też symulację terminala POS jako dodatkowy interfejs wejściowy? Jeśli POS – czy symulujemy go jako uproszczony REST API, czy implementujemy cokolwiek zbliżonego do protokołu ISO 8583?

**2. Etap Clearing/Settlement**
Czy implementujemy Settlement jako osobny etap asynchroniczny (zmiana statusu `CAPTURED → SETTLED` wyzwalana harmonogramem, np. co noc), czy upraszczamy do automatycznej zmiany statusu zaraz po Capture?

**3. Autoryzacja 3D Secure (3DS) - Bez autoryzacji**
W prawdziwym e-commerce transakcje kartą wymagają dodatkowej weryfikacji tożsamości (kod SMS / push z aplikacji bankowej – to właśnie 3D Secure). Czy implementujemy ten etap choćby w uproszczonej formie (np. dodatkowe pole `otp_code` w żądaniu weryfikowane przez bank), czy całkowicie go pomijamy?

**4. Częściowe zwroty**
Czy zwrot musi zawsze dotyczyć pełnej kwoty transakcji, czy implementujemy też zwroty częściowe (np. 50 PLN z transakcji na 150 PLN)? - 

**5. Limity transakcyjne**
Czy limity (dzienny, per transakcja) są zarządzane po naszej stronie (w CMS), czy każdy bank-wydawca zarządza limitami samodzielnie i to on odpowiada `DECLINED – LIMIT_EXCEEDED`? - Sprawdzić jak jest irl i tak zrobić.

---

### Architektura i integracja

**9. Wspólny standard identyfikatorów**
Czy w całym projekcie przyjmujemy jeden format `user_id` i `account_id` (np. UUID v4)? Który moduł jest „właścicielem" tych identyfikatorów?

**10. Komunikacja synchroniczna vs. asynchroniczna**
Czy autoryzacja ma być zawsze synchroniczna (REST, odpowiedź natychmiast), a Settlement asynchroniczny (Kafka/RabbitMQ), czy całość upraszczamy do synchronicznego REST dla wszystkich etapów?

**11. Autentykacja między modułami**
Jak moduły uwierzytelniają się wzajemnie? Wspólne tokeny API (`X-API-Key`), JWT, mTLS, czy zakładamy, że ruch jest w bezpiecznej sieci wewnętrznej bez autentykacji?
Wspólne tokeny 

---

### Sieć kart i waluty

**13. Przewalutowanie (FX)**
Czy obsługujemy płatność kartą PLN w sklepie rozliczanym w EUR? My rozliczamy w walucie w jakiej odbieramy.

**14. Waluty kart zagranicznych**
Karta Banku Brytyjskiego A jest w GBP. Jeśli właściciel tej karty zapłaci w PLN – jak to rozliczamy? - Karta w walucie taka jak waluta w kraju 

---

### Bezpieczeństwo i standardy

**15. Poziom zgodności z PCI-DSS**
PCI-DSS to standard bezpieczeństwa dla systemów kartowych (zakaz przechowywania CVV, maskowanie PAN, szyfrowanie transmisji). Czy chcemy formalnie wskazać, które wymagania PCI-DSS symulujemy, a które świadomie pomijamy jako poza zakresem?
Nie obchodzi nas

**16. Tokenizacja – własna implementacja**
Czy implementujemy własną tokenizację PAN (UUID v4 lub HMAC-SHA256 + salt), czy korzystamy z gotowej biblioteki? Jaki poziom bezpieczeństwa jest wymagany?

---

### Wymagania projektowe

**17. Panel administratora – frontend**
Czy panel admina ma być osobną aplikacją frontendową - Panel dla sklepu

**18. Dokumentacja API**
Czy wymagana jest dokumentacja OpenAPI/Swagger? Czy inne zespoły mają obowiązek dostarczenia Swaggera dla endpointów, z których my korzystamy?

---


Symulacja aby co 10 minut się synchronizował - konfigurowalne

Sprawdzić jak są rozdzielane prowizje i w jaki sposób ta prowizja później idzie do banków

Jak wygląda przewalutowanie płatności kartą

Częściowe zwroty odpuszczamy 

Bankomatów nie robimy wogóle

VisaNet 