from sqlalchemy import Column, Numeric
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class TransactionFee(Base):
    __tablename__ = "transaction_fees"

    id = Column(UUID(as_uuid=True), primary_key=True)
    interchange_fee = Column(Numeric(10, 4))
    scheme_fee = Column(Numeric(10, 4))
    acquirer_fee = Column(Numeric(10, 4))
    total_fee = Column(Numeric(10, 4))