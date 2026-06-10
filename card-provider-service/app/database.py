from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://bank_user:bank_pass@localhost:5433/cards_db")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

# Predefiniowane klucze API dla banków
BANK_API_KEYS_SEED = [
    {
        "bank_id": "POLISH_BANK_A",
        "api_key": "bank-key-pl-a",
        "hmac_secret": "secret-pl-a-hmac",
        "bin_prefix": "410001",
        "currency": "PLN",
    },
    {
        "bank_id": "POLISH_BANK_B",
        "api_key": "bank-key-pl-b",
        "hmac_secret": "secret-pl-b-hmac",
        "bin_prefix": "420001",
        "currency": "PLN",
    },
    {
        "bank_id": "EURO_BANK_A",
        "api_key": "bank-key-eu-a",
        "hmac_secret": "secret-eu-a-hmac",
        "bin_prefix": "430001",
        "currency": "EUR",
    },
    {
        "bank_id": "EURO_BANK_B",
        "api_key": "bank-key-eu-b",
        "hmac_secret": "secret-eu-b-hmac",
        "bin_prefix": "440001",
        "currency": "EUR",
    },
    {
        "bank_id": "UK_BANK_A",
        "api_key": "bank-key-uk-a",
        "hmac_secret": "secret-uk-a-hmac",
        "bin_prefix": "450001",
        "currency": "GBP",
    },
    {
        "bank_id": "UK_BANK_B",
        "api_key": "bank-key-uk-b",
        "hmac_secret": "secret-uk-b-hmac",
        "bin_prefix": "460001",
        "currency": "GBP",
    },
    {
        "bank_id": "US_BANK_A",
        "api_key": "bank-key-us-a",
        "hmac_secret": "secret-us-a-hmac",
        "bin_prefix": "470001",
        "currency": "USD",
    },
    {
        "bank_id": "US_BANK_B",
        "api_key": "bank-key-us-b",
        "hmac_secret": "secret-us-b-hmac",
        "bin_prefix": "480001",
        "currency": "USD",
    },
]