"""Money is where a rounding bug becomes a refund dispute, so it is tested hard."""

from decimal import Decimal

import pytest

from telecom_middleware.domain.money import Currency, Money, sum_money


def test_minor_units_convert_to_the_expected_decimal() -> None:
    assert Money(499, Currency.GBP).to_decimal() == Decimal("4.99")
    assert str(Money(499, Currency.GBP)) == "4.99 GBP"


def test_a_decimal_amount_converts_without_loss() -> None:
    assert Money.from_decimal("4.99", Currency.GBP) == Money(499, Currency.GBP)
    assert Money.from_decimal(Decimal("0.01"), Currency.EUR).minor_units == 1


def test_zero_and_large_amounts_both_survive_the_round_trip() -> None:
    for minor in (0, 1, 999_999_999_999):
        assert Money.from_decimal(Money(minor, Currency.USD).to_decimal(), Currency.USD) == Money(
            minor, Currency.USD
        )


def test_an_amount_with_more_precision_than_the_currency_is_rejected() -> None:
    # Silently rounding here is how a customer is refunded the wrong number.
    with pytest.raises(ValueError, match="more precision"):
        Money.from_decimal("4.999", Currency.GBP)


def test_a_float_can_never_be_stored_as_minor_units() -> None:
    with pytest.raises(TypeError, match="never a float"):
        Money(4.99, Currency.GBP)


def test_a_boolean_is_not_accepted_as_an_amount() -> None:
    # bool is a subclass of int, which is exactly the kind of silent wrong answer
    # this type exists to prevent.
    with pytest.raises(TypeError):
        Money(True, Currency.GBP)


def test_amounts_of_the_same_currency_add_and_subtract_exactly() -> None:
    total = Money(1099, Currency.GBP) + Money(1, Currency.GBP)
    assert total == Money(1100, Currency.GBP)
    assert (total - Money(100, Currency.GBP)).to_decimal() == Decimal("10.00")


def test_mixing_currencies_raises_rather_than_producing_a_plausible_wrong_number() -> None:
    with pytest.raises(ValueError, match="cannot combine"):
        Money(100, Currency.GBP) + Money(100, Currency.EUR)


def test_summing_an_empty_list_gives_zero_in_the_stated_currency() -> None:
    assert sum_money([], Currency.EUR) == Money(0, Currency.EUR)


def test_summing_many_small_amounts_does_not_drift() -> None:
    # The classic float failure: 0.1 added a hundred times is not 10.
    hundred_pennies = [Money(10, Currency.GBP) for _ in range(100)]

    assert sum_money(hundred_pennies, Currency.GBP) == Money(1000, Currency.GBP)


def test_amounts_order_by_value() -> None:
    assert Money(100, Currency.GBP) < Money(101, Currency.GBP)
