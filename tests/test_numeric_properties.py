from __future__ import annotations

from decimal import Decimal

from hypothesis import given, strategies as st

from stockcrewai.tools.calculator_tool import FinancialCalculatorTool, calculate_formula


finite_decimal = st.decimals(
    min_value=Decimal("-1E12"),
    max_value=Decimal("1E12"),
    allow_nan=False,
    allow_infinity=False,
    places=6,
)


@given(operating_cash_flow=finite_decimal, capital_expenditure=finite_decimal)
def test_free_cash_flow_is_decimal_subtraction(
    operating_cash_flow: Decimal,
    capital_expenditure: Decimal,
) -> None:
    result, unit = calculate_formula(
        "free_cash_flow",
        {
            "operating_cash_flow": operating_cash_flow,
            "capex": capital_expenditure,
        },
    )

    assert isinstance(result, Decimal)
    assert result == operating_cash_flow - capital_expenditure
    assert unit == "currency"


@given(
    operating_cash_flow=finite_decimal,
    capital_expenditure=finite_decimal,
    revenue=st.decimals(
        min_value=Decimal("-1E12"),
        max_value=Decimal("1E12"),
        allow_nan=False,
        allow_infinity=False,
        places=6,
    ).filter(lambda value: value != 0),
)
def test_free_cash_flow_margin_keeps_decimal_sign(
    operating_cash_flow: Decimal,
    capital_expenditure: Decimal,
    revenue: Decimal,
) -> None:
    result, unit = calculate_formula(
        "free_cash_flow_margin",
        {
            "operating_cash_flow": operating_cash_flow,
            "capex": capital_expenditure,
            "revenue_current": revenue,
        },
    )

    expected = (operating_cash_flow - capital_expenditure) / revenue
    assert isinstance(result, Decimal)
    assert result == expected
    assert unit == "ratio"


def test_missing_or_nonfinite_financial_values_are_unavailable() -> None:
    tool = FinancialCalculatorTool()

    missing = tool.run(
        facts={"operating_cash_flow": "10"},
        formulas=["free_cash_flow"],
    ).calculations[0]
    nonfinite = tool.run(
        facts={"operating_cash_flow": "NaN", "capex": "2"},
        formulas=["free_cash_flow"],
    ).calculations[0]

    assert missing.status == "unavailable"
    assert missing.raw_result is None
    assert nonfinite.status == "unavailable"
    assert nonfinite.raw_result is None
