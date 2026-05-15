import uuid
import enum
from datetime import datetime
from sqlalchemy import Column, String, Enum, Numeric, DateTime, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base

class CardStatus(str, enum.Enum):
    REQUESTED  = "REQUESTED"
    PRODUCING  = "PRODUCING"
    SHIPPED    = "SHIPPED"
    ACTIVE     = "ACTIVE"
    BLOCKED    = "BLOCKED"
    EXPIRED    = "EXPIRED"
    CANCELLED  = "CANCELLED"

class CardType(str, enum.Enum):
    VIRTUAL = "VIRTUAL"
    PHYSICAL = "PHYSICAL"
    PREPAID = "PREPAID"

class TransactionStatus(str, enum.Enum):
    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    SETTLED = "SETTLED"
    DECLINED = "DECLINED"
    REFUNDED = "REFUNDED"
    CHARGEBACK_INITIATED = "CHARGEBACK_INITIATED"
    CHARGEBACK_WON = "CHARGEBACK_WON"
    CHARGEBACK_LOST = "CHARGEBACK_LOST"

class Card(Base):
    __tablename__ = "cards"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String, nullable=False)
    account_id = Column(String, nullable=False)
    token = Column(String, unique=True, nullable=False)
    masked_pan = Column(String, nullable=False)
    card_type = Column(Enum(CardType), nullable=False)
    status = Column(Enum(CardStatus), nullable=False, default=CardStatus.ACTIVE)
    balance = Column(Numeric(12, 2), default=0)
    daily_limit = Column(Numeric(12, 2), default=1000)
    created_at = Column(DateTime, default=datetime.utcnow)
    activated_at = Column(DateTime, nullable=True)
    bank_id = Column(String, nullable=False, default="UNKNOWN")

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    card_id = Column(UUID(as_uuid=True), ForeignKey("cards.id"), nullable=False)
    merchant_id = Column(String)
    merchant_name = Column(String)
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(3), default="PLN")
    status = Column(Enum(TransactionStatus), nullable=False, default=TransactionStatus.PENDING)
    authorization_code = Column(String, unique=True, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    settled_at = Column(DateTime, nullable=True)

class TransactionFee(Base):
    __tablename__ = "transaction_fees"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id = Column(UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=False)
    interchange_fee = Column(Numeric(10, 4), default=0)
    scheme_fee = Column(Numeric(10, 4), default=0)
    acquirer_fee = Column(Numeric(10, 4), default=0)
    total_fee = Column(Numeric(10, 4), default=0)

class CardStatusHistory(Base):
    __tablename__ = "card_status_history"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    card_id = Column(UUID(as_uuid=True), ForeignKey("cards.id"), nullable=False)
    old_status = Column(String, nullable=True)
    new_status = Column(String, nullable=False)
    changed_by = Column(String, default="system")
    changed_at = Column(DateTime, default=datetime.utcnow)

class Chargeback(Base):
    __tablename__ = "chargebacks"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id = Column(UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=False)
    status = Column(String, default="INITIATED")
    reason = Column(String)
    initiated_by = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

class BankApiKey(Base):
    __tablename__ = "bank_api_keys"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bank_id = Column(String, unique=True, nullable=False)
    api_key = Column(String, unique=True, nullable=False)
    bin_prefix = Column(String(4), nullable=False)
    currency = Column(String(3), nullable=False)
    is_active = Column(Boolean, default=True)
    hmac_secret = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
