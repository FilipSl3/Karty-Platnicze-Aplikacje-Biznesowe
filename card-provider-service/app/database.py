from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://bank_user:bank_pass@localhost:5433/cards_db")

engine = create_async_engine(DATABASE_URL, echo=True)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
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
        "bin_prefix": "4100",
        "currency": "PLN",
    },
    {
        "bank_id": "POLISH_BANK_B",
        "api_key": "bank-key-pl-b",
        "hmac_secret": "secret-pl-b-hmac",
        "bin_prefix": "4200",
        "currency": "PLN",
    },
    {
        "bank_id": "EURO_BANK_A",
        "api_key": "bank-key-eu-a",
        "hmac_secret": "secret-eu-a-hmac",
        "bin_prefix": "4300",
        "currency": "EUR",
    },
    {
        "bank_id": "EURO_BANK_B",
        "api_key": "bank-key-eu-b",
        "hmac_secret": "secret-eu-b-hmac",
        "bin_prefix": "4400",
        "currency": "EUR",
    },
    {
        "bank_id": "UK_BANK_A",
        "api_key": "bank-key-uk-a",
        "hmac_secret": "secret-uk-a-hmac",
        "bin_prefix": "4500",
        "currency": "GBP",
    },
    {
        "bank_id": "UK_BANK_B",
        "api_key": "bank-key-uk-b",
        "hmac_secret": "secret-uk-b-hmac",
        "bin_prefix": "4600",
        "currency": "GBP",
    },
    {
        "bank_id": "US_BANK_A",
        "api_key": "bank-key-us-a",
        "hmac_secret": "secret-us-a-hmac",
        "bin_prefix": "4700",
        "currency": "USD",
    },
    {
        "bank_id": "US_BANK_B",
        "api_key": "bank-key-us-b",
        "hmac_secret": "secret-us-b-hmac",
        "bin_prefix": "4800",
        "currency": "USD",
    },
]