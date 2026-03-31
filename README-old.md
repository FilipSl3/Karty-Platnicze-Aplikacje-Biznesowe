# 💳 Moduł: Karty Płatnicze & Terminale

> **Projekt grupowy – Aplikacja Bankowa**  
> Moduł odpowiedzialny za obsługę kart płatniczych, terminali POS oraz autoryzację transakcji kartowych.

---

## 📋 Spis treści

1. [Opis modułu](#opis-modułu)
2. [Jak to działa w rzeczywistości – Model 4-stronny](#jak-to-działa-w-rzeczywistości)
3. [Architektura systemu](#architektura-systemu)
4. [Schemat bazy danych](#schemat-bazy-danych)
5. [Technologie](#technologie)
6. [API – Kontrakt dla innych zespołów](#api--kontrakt-dla-innych-zespołów)
7. [Scenariusze wydawania kart ⚠️ DO USTALENIA](#scenariusze-wydawania-kart)
8. [Obsługa zwrotów](#obsługa-zwrotów)
9. [Routing – Tabela BIN](#routing--tabela-bin)
10. [Bezpieczeństwo](#bezpieczeństwo)
11. [Emulator terminala (testy)](#emulator-terminala)
12. [Mock Server](#mock-server)
13. [Checklist – Plan działań](#checklist--plan-działań)

---

## Opis modułu

Moduł **Karty Płatnicze** pełni rolę **procesora płatności** (Acquirer/Processor) w całym ekosystemie bankowym projektu. Jego zadaniem jest:

- Wydawanie wirtualnych kart płatniczych powiązanych z kontami bankowymi
- Przyjmowanie żądań płatności z terminali POS (emulowanych)
- Rozpoznawanie, do którego banku należy dana karta (routing po numerze BIN)
- Komunikacja z bankami-wydawcami w celu autoryzacji transakcji
- Zarządzanie statusami transakcji (`PENDING → SUCCESS / FAILED`)
- Obsługa zwrotów środków z poziomu panelu administratora
- Blokowanie i odblokowywanie kart

---

## Jak to działa w rzeczywistości

### Model 4-stronny (Four-Party Scheme)

W prawdziwym świecie płatność kartą dzieli się na dwa etapy:

```
[Klient / Terminal POS]
        |
        | 1. Żądanie autoryzacji (numer karty, kwota, terminal_id)
        ↓
[NASZ MODUŁ – Acquirer/Processor]
        |
        | 2. Identyfikacja banku-wydawcy po numerze BIN
        | 3. Zapytanie autoryzacyjne → Bank-Wydawca
        ↓
[Bank-Wydawca – np. Polski Bank A]
        |
        | 4. Weryfikacja salda / limitów
        | 5. Blokada środków na koncie
        | 6. Odpowiedź: APPROVED / DECLINED
        ↓
[NASZ MODUŁ]
        |
        | 7. Odpowiedź do terminala
        ↓
[Terminal POS – potwierdzenie transakcji]
```

**Etap 1 – Autoryzacja** (real-time, dzieje się w ułamku sekundy):
- Terminal wysyła dane karty do naszego modułu
- My pytamy bank-wydawcę: *„Czy ta karta może wydać X PLN?"*
- Bank blokuje środki i odpowiada `APPROVED` lub `DECLINED`

**Etap 2 – Clearing/Settlement** (rozliczenie, dzieje się później, np. następnego dnia):
- Faktyczny przelew środków między bankami
- W naszym projekcie możemy to uprosić do zmiany statusu transakcji z `PENDING` na `SETTLED`

> **Zapytanie -** Na potrzeby projektu skupiamy się głównie na etapie autoryzacji. Clearing/Settlement implementujemy w formie uproszczonej (zmiana statusu).

---

## Architektura systemu

```
┌─────────────────────────────────────────────────────────────────┐
│                    MODUŁ KARTY PŁATNICZE                        │
│                                                                 │
│  ┌──────────────────┐   ┌──────────────────┐   ┌─────────────┐  │
│  │ Terminal Gateway │   │  Card Management │   │ Transaction │  │
│  │    (REST API)    │-->│   System (CMS)   │-->│   Engine    │  │
│  │                  │   │                  │   │             │  │
│  │ -Walidacja danych│   │ - Rejestr kart   │   │  - Routing  │  │
│  │ -CVV/Expiry check│   │ - Statusy kart   │   │  - Auth     │  │
│  │ -Rate limiting   │   │ - Limity         │   │  - Capture  │  │
│  └──────────────────┘   └──────────────────┘   └─────────────┘  │
│                                                       │         │
│  ┌─────────────────────┐                              │         │
│  │     Admin Panel     │                              │         │
│  │                     │                              │         │
│  │ -Podgląd transakcji │                              │         │
│  │ -Przycisk Zwróć     │                              │         │
│  │ -Blok./Odblok.      │                              │         │
│  └─────────────────────┘                              │         │
└───────────────────────────────────────────────────────┼─────────┘
                                                        │
                    ┌───────────────────────────────────┘
                    │  Routing po BIN
                    ▼
    ┌───────────────────────────────────────────┐
    │           INNE MODUŁY (BANKI)             │
    │                                           │
    │  Polski Bank A  │  Polski Bank B          │
    │  Bank Euro A    │  Bank Euro B            │
    │  Bank UK A      │  Bank UK B              │
    │  Bank USA A     │  Bank USA B             │
    └───────────────────────────────────────────┘
```

### Komponenty

#### A. Terminal Gateway
Interfejs przyjmujący żądania płatności z terminali (emulowanych jako REST API lub Postman).

**Odpowiedzialności:**
- Walidacja formatu danych wejściowych
- Weryfikacja daty ważności karty i formatu CVV (**nie przechowujemy CVV!**)
- Rate limiting (ochrona przed atakami brute-force na numery kart)
- Przekazanie żądania do Transaction Engine

#### B. Card Management System (CMS)
Centralna baza danych kart wydanych w systemie.

**Odpowiedzialności:**
- Przechowywanie zamaskowanych danych karty (nigdy pełny PAN w bazie!)
- Zarządzanie statusem karty (`ACTIVE`, `BLOCKED`, `EXPIRED`, `CANCELLED`)
- Zarządzanie limitami (dzienny, miesięczny, per transakcja)
- Powiązanie karty z rachunkiem bankowym i bankiem-wydawcą

#### C. Transaction Engine
Serce systemu – logika biznesowa autoryzacji.

**Odpowiedzialności:**
- Routing: identyfikacja banku-wydawcy po numerze BIN
- Wysłanie żądania autoryzacyjnego do odpowiedniego banku
- Obsługa odpowiedzi (`APPROVED` / `DECLINED`)
- Zapis transakcji do bazy z pełną historią statusów
- Obsługa timeoutów i fallback (Mock Server gdy bank nie odpowiada)

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
    card_network    VARCHAR(10) NOT NULL,    -- VISA / MASTERCARD (symulowane)
    expiry_month    SMALLINT NOT NULL,
    expiry_year     SMALLINT NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
                    -- ACTIVE | BLOCKED | EXPIRED | CANCELLED
    daily_limit     DECIMAL(12,2) NOT NULL DEFAULT 1000.00,
    monthly_limit   DECIMAL(12,2) NOT NULL DEFAULT 10000.00,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Transakcje
CREATE TABLE transactions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    card_id         UUID NOT NULL REFERENCES cards(id),
    terminal_id     VARCHAR(50),             -- identyfikator terminala
    merchant_name   VARCHAR(100),            -- nazwa sprzedawcy
    amount          DECIMAL(12,2) NOT NULL,
    currency        CHAR(3) NOT NULL DEFAULT 'PLN',
    status          VARCHAR(20) NOT NULL,
                    -- PENDING | AUTHORIZED | CAPTURED | SETTLED
                    -- | DECLINED | REFUNDED | FAILED
    failure_reason  VARCHAR(100),            -- powód odrzucenia
    authorization_code VARCHAR(20),          -- kod z banku-wydawcy
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Historia statusów transakcji (audit log)
CREATE TABLE transaction_status_history (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id  UUID NOT NULL REFERENCES transactions(id),
    old_status      VARCHAR(20),
    new_status      VARCHAR(20) NOT NULL,
    changed_by      VARCHAR(50),             -- system / admin_id
    reason          TEXT,
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Tabela routingu BIN
CREATE TABLE bin_routing (
    bin_prefix      VARCHAR(6) PRIMARY KEY,  -- pierwsze 4-6 cyfr karty
    bank_id         VARCHAR(50) NOT NULL,    -- do którego banku routować
    card_network    VARCHAR(10) NOT NULL,
    country         CHAR(2) NOT NULL
);
```

---

## Technologie

| Warstwa | Technologia | Uzasadnienie |
|---|---|---|
| Backend | **Java 21 + Spring Boot 3** lub **Go (Gin/Fiber)** | Standard w fintechu, silne typowanie, wydajność |
| Baza danych | **PostgreSQL 16** | ACID, transakcyjność krytyczna przy płatnościach |
| Komunikacja async | **RabbitMQ** lub **Apache Kafka** | Zdarzenia płatnicze są asynchroniczne z natury |
| Komunikacja sync | **REST API (JSON)** lub **gRPC** | Autoryzacja musi być synchroniczna (real-time) |
| Bezpieczeństwo | **HTTPS / TLS**, tokenizacja PAN | Ochrona danych karty |
| Konteneryzacja | **Docker + Docker Compose** | Łatwe uruchomienie i integracja z innymi modułami |
| Dokumentacja API | **OpenAPI 3.0 / Swagger** | Kontrakt dla innych zespołów |

> **Uwaga:** Protokoły ISO 8583 i Nexo (używane w prawdziwych terminalach) są poza zakresem projektu. Emulujemy terminal jako zwykłe REST API.

---

## API – Kontrakt dla innych zespołów

> ⚠️ **To jest najważniejsza sekcja dla pozostałych zespołów.**  
> Poniżej opisane są wszystkie endpointy, których inne moduły będą potrzebować lub które muszą dostarczyć.

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
Autoryzacja płatności z terminala POS (**główny endpoint – dla emulatorów terminali**).

```json
// Request
{
  "card_token": "tok_abc123xyz",
  "amount": 150.00,
  "currency": "PLN",
  "terminal_id": "POS-WARSAW-001",
  "merchant_name": "Sklep XYZ"
}

// Response 200 OK – Zatwierdzone
{
  "transaction_id": "uuid",
  "status": "AUTHORIZED",
  "authorization_code": "AUTH-789XYZ",
  "message": "Payment authorized"
}

// Response 200 OK – Odrzucone
{
  "transaction_id": "uuid",
  "status": "DECLINED",
  "message": "Insufficient funds"
}
```

---

#### `POST /api/v1/payments/{transaction_id}/capture`
Potwierdzenie finalizacji transakcji (po pomyślnej autoryzacji).

```json
// Response 200 OK
{
  "transaction_id": "uuid",
  "status": "CAPTURED"
}
```

---

#### `GET /api/v1/cards/{card_id}/transactions`
Historia transakcji danej karty (dla panelu bankowego).

---

#### `PATCH /api/v1/cards/{card_id}/status`
Blokowanie / odblokowanie karty.

```json
// Request
{
  "status": "BLOCKED",   // lub "ACTIVE"
  "reason": "Lost card"
}
```

---

#### `POST /api/v1/payments/{transaction_id}/refund`
Zwrot środków (**wywoływany z panelu admina**).

```json
// Request
{
  "initiated_by": "admin_id",
  "reason": "Customer request"
}

// Response 200 OK
{
  "transaction_id": "uuid",
  "status": "REFUNDED",
  "refunded_amount": 150.00
}
```

---

### Czego my wymagamy od innych modułów (ich API)

> Każdy moduł **bankowy** (Polski Bank A/B, Euro A/B, UK A/B, USA A/B) musi implementować poniższe endpointy, abyśmy mogli przeprowadzić autoryzację.

#### `POST /api/v1/authorize` ← wymagane od każdego banku
Zapytanie: *"Czy ta karta / konto może wydać X PLN?"*

```json
// Request (wysyłamy my)
{
  "account_id": "uuid",
  "amount": 150.00,
  "currency": "PLN",
  "transaction_id": "uuid",   // nasze ID do śledzenia
  "terminal_id": "POS-WARSAW-001"
}

// Response (oczekujemy)
{
  "authorization_code": "AUTH-789XYZ",
  "status": "APPROVED",        // lub "DECLINED"
  "decline_reason": null        // lub "INSUFFICIENT_FUNDS" / "CARD_BLOCKED" itp.
}
```

---

#### `POST /api/v1/capture` ← wymagane od każdego banku
Finalizacja – zdjęcie blokady i faktyczne obciążenie konta.

```json
// Request (wysyłamy my)
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
Zwrot środków na konto klienta.

```json
// Request (wysyłamy my)
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

## Scenariusze wydawania kart

> ⚠️ **DO USTALENIA**

### Scenariusz A – Karty wydawane wyłącznie z naszego modułu *(rekomendowany)*

- Klient banku wchodzi do **naszego panelu** i samodzielnie generuje kartę
- Nasz moduł tworzy kartę i zapisuje powiązanie z `account_id` danego banku
- Banki **nie wywołują** żadnego endpointu u nas – to klient inicjuje proces
- **Zalety:** prosta implementacja, brak zależności od gotowości innych zespołów
- **Wady:** mniej realistyczne (w prawdziwym banku to bank inicjuje wydanie karty)

```
Klient → [Nasz Panel] → POST /api/v1/cards/issue → [Nasza Baza]
```

### Scenariusz B – Karty wydawane przez banki (na żądanie banku)

- Gdy bank tworzy nowe konto dla klienta, **bank** wysyła żądanie do nas
- Nasz moduł generuje kartę i odsyła dane z powrotem do banku
- Wymaga, żeby zespoły od banków zaimplementowały wywołanie naszego API
- **Zalety:** zgodne z rzeczywistością
- **Wady:** uzależnienie od postępu innych zespołów → ryzyko opóźnień

```
[Bank A] → POST /api/v1/cards/issue → [Nasz Moduł] → zwraca token karty → [Bank A]
```

> 💡 **Sugestia:** Zaimplementuj **Scenariusz A** jako podstawę, a Scenariusz B jako opcjonalne rozszerzenie. Dzięki temu możesz prezentować działający system niezależnie od innych zespołów.

---

## Obsługa zwrotów

Moduł implementuje **uproszczony proces zwrotu** bez ścieżki chargeback (sporu).

### Jak działa zwrot w naszym systemie

```
Admin widzi transakcję w panelu
        │
        │ Klika przycisk "Zwróć"
        ↓
POST /api/v1/payments/{id}/refund
        │
        ├─► Walidacja: czy transakcja jest w statusie CAPTURED/SETTLED?
        ├─► Wywołanie POST /api/v1/refund w banku-wydawcy karty
        ├─► Zmiana statusu transakcji → REFUNDED
        └─► Zapis w transaction_status_history
```

### Czego **nie** implementujemy (poza zakresem)

- ❌ Proces chargeback (spór klient vs. sprzedawca)
- ❌ Wieloetapowe rozpatrywanie reklamacji
- ❌ Częściowe zwroty (refund częściowy kwoty)
- ❌ Automatyczne zwroty po określonym czasie

### Czego implementujemy (zakres projektu)

- ✅ Przycisk „Zwróć" w panelu administratora
- ✅ Zmiana statusu transakcji na `REFUNDED`
- ✅ Wywołanie endpointu `/refund` w banku-wydawcy
- ✅ Zapis historii zmiany statusu (kto i kiedy zlecił zwrot)
- ✅ Zwrot możliwy tylko dla transakcji w statusie `CAPTURED` lub `SETTLED`

---

## Routing – Tabela BIN

BIN (Bank Identification Number) to pierwsze 4–6 cyfr numeru karty. Na ich podstawie identyfikujemy, do którego banku należy karta.

**Propozycja podziału numerów w projekcie:**

| Prefiks karty | Bank | Waluta | Kraj |
|---|---|---|---|
| `4100` – `4199` | Polski Bank A | PLN | PL |
| `4200` – `4299` | Polski Bank B | PLN | PL |
| `4300` – `4399` | Bank Euro A | EUR | EU |
| `4400` – `4499` | Bank Euro B | EUR | EU |
| `4500` – `4599` | Bank Brytyjski A | GBP | GB |
| `4600` – `4699` | Bank Brytyjski B | GBP | GB |
| `4700` – `4799` | Bank Amerykański A | USD | US |
| `4800` – `4899` | Bank Amerykański B | USD | US |

> ⚠️ **Powyższe prefiksy to propozycja do ustalenia ze wszystkimi zespołami.** Każdy zespół bankowy powinien znać swój prefiks BIN.

---

## Bezpieczeństwo

### Tokenizacja PAN

Nigdy nie przechowujemy ani nie przesyłamy pełnego numeru karty (PAN) w postaci jawnej.

```
Prawdziwy PAN:  4123 4567 8901 2345
                        ↓ tokenizacja
Token:          tok_7f3a9c2b1e4d8f6a
Maska w bazie:  **** **** **** 2345
```

### Inne zasady bezpieczeństwa

- **CVV** – nigdy nie jest przechowywany, tylko weryfikowany i odrzucany
- **HTTPS** – cała komunikacja szyfrowana TLS
- **Rate limiting** – ograniczenie liczby prób autoryzacji (ochrona przed brute-force)
- Logi transakcji zawierają tylko zamaskowane dane karty

---

## Emulator terminala

Prosta aplikacja (lub kolekcja Postman) symulująca terminal POS. Pozwala testować nasz moduł bez fizycznego terminala.

### Przykładowe żądanie (Postman / curl)

```bash
curl -X POST http://localhost:8080/api/v1/payments/authorize \
  -H "Content-Type: application/json" \
  -d '{
    "card_token": "tok_abc123xyz",
    "amount": 150.00,
    "currency": "PLN",
    "terminal_id": "POS-TEST-001",
    "merchant_name": "Test Shop"
  }'
```

### Scenariusze testowe

| Scenariusz | card_token | Oczekiwany wynik |
|---|---|---|
| Płatność zatwierdzona | `tok_valid_funds` | `AUTHORIZED` |
| Brak środków | `tok_no_funds` | `DECLINED` – Insufficient funds |
| Karta zablokowana | `tok_blocked` | `DECLINED` – Card blocked |
| Karta wygasła | `tok_expired` | `DECLINED` – Card expired |
| Bank nie odpowiada | `tok_bank_timeout` | `DECLINED` – Timeout (Mock Server) |

---

## Mock Server

Gdy bank-wydawca nie ma jeszcze gotowego API (lub nie odpowiada), nasz system korzysta z **Mock Server**, który symuluje jego odpowiedź.

```java
// Pseudokod – logika fallback
public AuthorizationResponse authorize(AuthorizationRequest request) {
    try {
        return bankClient.authorize(request); // prawdziwe wywołanie
    } catch (TimeoutException | BankUnavailableException e) {
        log.warn("Bank {} unavailable, using mock response", request.getBankId());
        return mockServer.getDefaultResponse(request); // fallback
    }
}
```

**Tryby Mock Servera:**

| Tryb | Opis |
|---|---|
| `ALWAYS_APPROVE` | Zawsze zwraca `APPROVED` |
| `ALWAYS_DECLINE` | Zawsze zwraca `DECLINED` |
| `RANDOM` | Losowo zatwierdza / odrzuca (testy obciążeniowe) |
| `TIMEOUT` | Symuluje brak odpowiedzi banku |

---

## Checklist – Plan działań

### Faza 1 – Podstawy (tydzień 1–2)

- [ ] Ustalenie prefiksów BIN z pozostałymi zespołami
- [ ] Uzgodnienie scenariusza wydawania kart z prowadzącym (A czy B)
- [ ] Zaprojektowanie i migracja schematu bazy danych
- [ ] Konfiguracja projektu (Spring Boot / Go + PostgreSQL + Docker)
- [ ] Implementacja modelu `Card` i podstawowego CMS

### Faza 2 – Logika autoryzacji (tydzień 3–4)

- [ ] Terminal Gateway – endpoint przyjmujący płatności
- [ ] Walidacja karty (data ważności, status, limity)
- [ ] Transaction Engine – routing po BIN
- [ ] Mock Server – fallback dla brakujących modułów bankowych
- [ ] Zapis transakcji z historią statusów

### Faza 3 – Integracje i panel (tydzień 5–6)

- [ ] Integracja z pierwszym bankiem (np. Polski Bank A)
- [ ] Panel administratora – podgląd transakcji
- [ ] Implementacja zwrotów (przycisk „Zwróć" w panelu)
- [ ] Blokowanie / odblokowanie kart
- [ ] Dokumentacja OpenAPI / Swagger

### Faza 4 – Testy i finalizacja (tydzień 7–8)

- [ ] Testy integracyjne ze wszystkimi modułami bankowymi
- [ ] Emulator terminala – pełna kolekcja testów w Postmanie
- [ ] Testy scenariuszy błędów (bank niedostępny, karta zablokowana, itp.)
- [ ] Dokumentacja README (niniejszy dokument)

---

## Pytania do ustalenia

1. **Scenariusz wydawania kart:** Czy karty są wydawane tylko z naszego panelu (Scenariusz A), czy banki inicjują wydanie karty poprzez wywołanie naszego API (Scenariusz B)?
2. **Clearing/Settlement:** Czy implementujemy rozliczenie jako osobny etap (zmiana statusu `CAPTURED → SETTLED`), czy wystarczy sama autoryzacja?
3. **Sieć kart:** Czy symulujemy podział na Visa / Mastercard, czy pomijamy ten poziom szczegółowości?
4. **Waluty:** Czy obsługujemy przewalutowanie (np. płatność kartą PLN w sklepie w EUR), czy tylko transakcje w walucie karty?
5. **Wspólny format API:** Czy będzie jeden wspólny standard dla wszystkich modułów (np. ustalony format `account_id`, `user_id`)?

---

