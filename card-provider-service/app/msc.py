from decimal import Decimal, ROUND_HALF_UP
import os


INTERCHANGE = Decimal(
    os.getenv("INTERCHANGE_FEE_PERCENT", "1.5")
)

SCHEME = Decimal(
    os.getenv("SCHEME_FEE_PERCENT", "0.3")
)

ACQUIRER = Decimal(
    os.getenv("ACQUIRER_FEE_PERCENT", "0.2")
)


def calculate_msc(amount: Decimal):
    interchange_fee = (
        amount * INTERCHANGE / Decimal("100")
    ).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )

    scheme_fee = (
        amount * SCHEME / Decimal("100")
    ).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )

    acquirer_fee = (
        amount * ACQUIRER / Decimal("100")
    ).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )

    total_fee = (
        interchange_fee
        + scheme_fee
        + acquirer_fee
    )

    return {
        "interchange_fee": interchange_fee,
        "scheme_fee": scheme_fee,
        "acquirer_fee": acquirer_fee,
        "total_fee": total_fee
    }