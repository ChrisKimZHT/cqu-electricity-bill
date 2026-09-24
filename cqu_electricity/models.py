from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


DEFAULT_ELECTRICITY_PRICE = Decimal("0.54")


def total_balance_yuan(
    balance_yuan: Decimal,
    subsidy_kwh: Decimal | None,
    electricity_price: Decimal = DEFAULT_ELECTRICITY_PRICE,
    subsidy_balance_yuan: Decimal | None = None,
) -> Decimal:
    """合计现金余额与按电量或金额给出的补助余额。"""
    return (
        balance_yuan
        + (subsidy_kwh or Decimal("0")) * electricity_price
        + (subsidy_balance_yuan or Decimal("0"))
    )


@dataclass(frozen=True, slots=True)
class MeterReading:
    captured_at: datetime
    room: str
    building: str
    balance_yuan: Decimal
    meter_reading_kwh: Decimal | None = None
    subsidy_kwh: Decimal | None = None
    subsidy_balance_yuan: Decimal | None = None
    unit_price_yuan_per_kwh: Decimal | None = None
    meter_address: str | None = None

    def total_balance_yuan(
        self, electricity_price: Decimal = DEFAULT_ELECTRICITY_PRICE
    ) -> Decimal:
        return total_balance_yuan(
            self.balance_yuan, self.subsidy_kwh, electricity_price, self.subsidy_balance_yuan
        )
