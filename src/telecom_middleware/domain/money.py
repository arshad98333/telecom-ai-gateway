"""Money as integer minor units, never a float.

A telecom refund that is out by a cent is a support case of its own, and floating point
loses cents by design. Amounts are stored and transported as an integer count of the
currency's smallest unit, with the currency beside them, and are only ever formatted
for display at the edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Final


class Currency(StrEnum):
    GBP = "GBP"
    EUR = "EUR"
    USD = "USD"


#: Every currency this service handles has two decimal places. A currency with a
#: different exponent (JPY, KWD) must be added here deliberately, not assumed.
MINOR_UNIT_EXPONENT: Final[dict[Currency, int]] = {
    Currency.GBP: 2,
    Currency.EUR: 2,
    Currency.USD: 2,
}


@dataclass(frozen=True, slots=True, order=True)
class Money:
    """An exact amount. ``minor_units`` of ``currency``; 499 GBP minor units is £4.99."""

    minor_units: int
    currency: Currency

    def __post_init__(self) -> None:
        if not isinstance(self.minor_units, int) or isinstance(self.minor_units, bool):
            raise TypeError("minor_units must be an integer, never a float")

    @classmethod
    def from_decimal(cls, amount: Decimal | str, currency: Currency) -> Money:
        """Convert a decimal amount, rejecting anything with too many decimal places."""
        value = Decimal(amount)
        exponent = MINOR_UNIT_EXPONENT[currency]
        scaled = value.scaleb(exponent)
        if scaled != scaled.to_integral_value(rounding=ROUND_HALF_UP):
            raise ValueError(
                f"{value} has more precision than {currency} supports ({exponent} decimal places)"
            )
        return cls(int(scaled), currency)

    def to_decimal(self) -> Decimal:
        return Decimal(self.minor_units).scaleb(-MINOR_UNIT_EXPONENT[self.currency])

    def __str__(self) -> str:
        return f"{self.to_decimal():.{MINOR_UNIT_EXPONENT[self.currency]}f} {self.currency}"

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.minor_units + other.minor_units, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.minor_units - other.minor_units, self.currency)

    def _same_currency(self, other: Money) -> None:
        if self.currency is not other.currency:
            # Silently adding GBP to EUR is how a refund becomes wrong by 15 percent.
            raise ValueError(f"cannot combine {self.currency} with {other.currency}")


def sum_money(amounts: list[Money], currency: Currency) -> Money:
    """Total a list, returning zero in ``currency`` when the list is empty."""
    total = Money(0, currency)
    for amount in amounts:
        total = total + amount
    return total
