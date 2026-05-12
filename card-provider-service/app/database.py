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
        "bin_prefix": "4100",
        "currency": "PLN",
    },
    {
        "bank_id": "POLISH_BANK_B",
        "api_key": "bank-key-pl-b",
        "bin_prefix": "4200",
        "currency": "PLN",
    },
    {
        "bank_id": "EURO_BANK_A",
        "api_key": "bank-key-eu-a",
        "bin_prefix": "4300",
        "currency": "EUR",
    },
    {
        "bank_id": "EURO_BANK_B",
        "api_key": "bank-key-eu-b",
        "bin_prefix": "4400",
        "currency": "EUR",
    },
    {
        "bank_id": "UK_BANK_A",
        "api_key": "bank-key-uk-a",
        "bin_prefix": "4500",
        "currency": "GBP",
    },
    {
        "bank_id": "UK_BANK_B",
        "api_key": "bank-key-uk-b",
        "bin_prefix": "4600",
        "currency": "GBP",
    },
    {
        "bank_id": "US_BANK_A",
        "api_key": "bank-key-us-a",
        "bin_prefix": "4700",
        "currency": "USD",
    },
    {
        "bank_id": "US_BANK_B",
        "api_key": "bank-key-us-b",
        "bin_prefix": "4800",
        "currency": "USD",
    },
]