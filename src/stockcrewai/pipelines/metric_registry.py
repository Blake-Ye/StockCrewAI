from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, NamedTuple

from stockcrewai.models.evidence import (
    CalculationRecord,
    EvidenceRecord,
    ValidationStatus,
)
from stockcrewai.models.policy import (
    Applicability,
    GateEffect,
    MetricPolicy,
    PolicyDecision,
)
from stockcrewai.models.profile import (
    CoverageLevel,
    IssuerProfile,
    ProfileResult,
    ReportingProfile,
    SecurityProfile,
)
from stockcrewai.profiles.commodity_producer import POLICY_VERSION as COMMODITY_POLICY_VERSION
from stockcrewai.profiles.foreign_issuer import POLICY_VERSION as FOREIGN_POLICY_VERSION
from stockcrewai.profiles.holding_company import (
    POLICY_VERSION as HOLDING_COMPANY_POLICY_VERSION,
)
from stockcrewai.profiles.spac import POLICY_VERSION as SPAC_POLICY_VERSION


POLICY_VERSION = "metric-policy:v1"
_REIT_POLICY_VERSION = "metric-policy:v2"
BANK_POLICY_VERSION = "metric-policy:bank:v1"
INSURANCE_POLICY_VERSION = "metric-policy:insurance:v1"
UTILITY_POLICY_VERSION = "metric-policy:utility:v1"


class _MetricSpec(NamedTuple):
    metric_id: str
    applicability: Applicability
    required_evidence: tuple[str, ...]
    formula_id: str
    period_basis: str
    unit_policy: str
    gate_effect: GateEffect
    reason_code: str
    policy_version: str = POLICY_VERSION


def _spec(
    metric_id: str,
    applicability: Applicability,
    required_evidence: tuple[str, ...],
    *,
    gate_effect: GateEffect,
    reason_code: str,
    formula_id: str | None = None,
    period_basis: str = "ttm_or_latest_fiscal_period",
    unit_policy: str = "decimal_ratio_or_per_share",
    policy_version: str = POLICY_VERSION,
) -> _MetricSpec:
    return _MetricSpec(
        metric_id,
        applicability,
        required_evidence,
        formula_id or f"{metric_id}:v1",
        period_basis,
        unit_policy,
        gate_effect,
        reason_code,
        policy_version,
    )


_REQUIRED = Applicability.REQUIRED
_OPTIONAL = Applicability.OPTIONAL
_NOT_APPLICABLE = Applicability.NOT_APPLICABLE
_BLOCKING = GateEffect.BLOCKING
_NON_BLOCKING = GateEffect.NON_BLOCKING


_POLICY_TABLE: dict[IssuerProfile, tuple[_MetricSpec, ...]] = {
    IssuerProfile.STANDARD_OPERATING: (
        _spec(
            "revenue_growth",
            _REQUIRED,
            ("revenue_current", "revenue_prior"),
            gate_effect=_BLOCKING,
            reason_code="required_revenue_growth",
        ),
        _spec(
            "operating_margin",
            _REQUIRED,
            ("operating_income", "revenue"),
            gate_effect=_BLOCKING,
            reason_code="required_operating_margin",
        ),
        _spec(
            "pe_ratio",
            _OPTIONAL,
            ("market_price", "diluted_eps"),
            gate_effect=_NON_BLOCKING,
            reason_code="optional_pe_ratio",
        ),
        _spec(
            "fcf_yield",
            _OPTIONAL,
            ("free_cash_flow", "market_cap"),
            gate_effect=_NON_BLOCKING,
            reason_code="optional_fcf_yield",
        ),
    ),
    IssuerProfile.BANK: (
        _spec(
            "bank_roa",
            _REQUIRED,
            ("net_income", "average_assets"),
            gate_effect=_BLOCKING,
            reason_code="bank_roa_missing",
            formula_id="bank-roa-v1",
            period_basis="same_fiscal_period",
            unit_policy="ratio",
            policy_version=BANK_POLICY_VERSION,
        ),
        _spec(
            "bank_roe",
            _REQUIRED,
            ("net_income", "average_equity"),
            gate_effect=_BLOCKING,
            reason_code="bank_roe_missing",
            formula_id="bank-roe-v1",
            period_basis="same_fiscal_period",
            unit_policy="ratio",
            policy_version=BANK_POLICY_VERSION,
        ),
        _spec(
            "net_interest_margin",
            _REQUIRED,
            ("net_interest_income", "average_earning_assets"),
            gate_effect=_BLOCKING,
            reason_code="net_interest_margin_missing",
            formula_id="bank-net-interest-margin-v1",
            period_basis="same_fiscal_period",
            unit_policy="ratio",
            policy_version=BANK_POLICY_VERSION,
        ),
        _spec(
            "efficiency_ratio",
            _REQUIRED,
            ("noninterest_expense", "net_interest_income", "noninterest_income"),
            gate_effect=_BLOCKING,
            reason_code="efficiency_ratio_missing",
            formula_id="bank-efficiency-ratio-v1",
            period_basis="same_fiscal_period",
            unit_policy="ratio",
            policy_version=BANK_POLICY_VERSION,
        ),
        _spec(
            "cet1_ratio",
            _OPTIONAL,
            ("cet1_ratio",),
            gate_effect=_NON_BLOCKING,
            reason_code="cet1_ratio_not_disclosed",
            formula_id="bank-cet1-ratio-v1",
            period_basis="company_disclosed_period",
            unit_policy="ratio",
            policy_version=BANK_POLICY_VERSION,
        ),
        _spec(
            "loan_to_deposit",
            _OPTIONAL,
            ("total_loans", "total_deposits"),
            gate_effect=_NON_BLOCKING,
            reason_code="loan_to_deposit_missing",
            formula_id="bank-loan-to-deposit-v1",
            period_basis="same_fiscal_period",
            unit_policy="ratio",
            policy_version=BANK_POLICY_VERSION,
        ),
        _spec(
            "nonperforming_loan_ratio",
            _OPTIONAL,
            ("nonperforming_loans", "total_loans"),
            gate_effect=_NON_BLOCKING,
            reason_code="nonperforming_loan_ratio_missing",
            formula_id="bank-nonperforming-loan-ratio-v1",
            period_basis="same_fiscal_period",
            unit_policy="ratio",
            policy_version=BANK_POLICY_VERSION,
        ),
        _spec(
            "provision_coverage",
            _OPTIONAL,
            ("allowance_for_credit_losses", "nonperforming_loans"),
            gate_effect=_NON_BLOCKING,
            reason_code="provision_coverage_missing",
            formula_id="bank-provision-coverage-v1",
            period_basis="same_fiscal_period",
            unit_policy="ratio",
            policy_version=BANK_POLICY_VERSION,
        ),
        _spec(
            "price_to_book",
            _OPTIONAL,
            ("market_price", "book_value_per_share"),
            gate_effect=_NON_BLOCKING,
            reason_code="price_to_book_missing",
            formula_id="bank-price-to-book-v1",
            period_basis="market_price_at_or_after_financial_as_of",
            unit_policy="multiple",
            policy_version=BANK_POLICY_VERSION,
        ),
        _spec(
            "pe_ratio",
            _OPTIONAL,
            ("market_price", "diluted_eps"),
            gate_effect=_NON_BLOCKING,
            reason_code="pe_ratio_missing",
            formula_id="bank-pe-ratio-v1",
            period_basis="market_price_same_point_in_time",
            unit_policy="multiple",
            policy_version=BANK_POLICY_VERSION,
        ),
        _spec(
            "fcf_yield",
            _NOT_APPLICABLE,
            (),
            gate_effect=_NON_BLOCKING,
            reason_code="bank_fcf_not_applicable",
            formula_id="bank-fcf-yield-not-applicable-v1",
            period_basis="not_applicable",
            unit_policy="not_applicable",
            policy_version=BANK_POLICY_VERSION,
        ),
    ),
    IssuerProfile.INSURANCE: (
        _spec(
            "loss_ratio",
            _REQUIRED,
            ("incurred_losses", "earned_premiums"),
            gate_effect=_BLOCKING,
            reason_code="loss_ratio_missing",
            formula_id="insurance-loss-ratio-v1",
            period_basis="same_fiscal_period",
            unit_policy="ratio",
            policy_version=INSURANCE_POLICY_VERSION,
        ),
        _spec(
            "expense_ratio",
            _REQUIRED,
            ("underwriting_expenses", "earned_premiums"),
            gate_effect=_BLOCKING,
            reason_code="expense_ratio_missing",
            formula_id="insurance-expense-ratio-v1",
            period_basis="same_fiscal_period",
            unit_policy="ratio",
            policy_version=INSURANCE_POLICY_VERSION,
        ),
        _spec(
            "combined_ratio",
            _REQUIRED,
            ("incurred_losses", "underwriting_expenses", "earned_premiums"),
            gate_effect=_BLOCKING,
            reason_code="combined_ratio_components_missing",
            formula_id="insurance-combined-ratio-v1",
            period_basis="same_fiscal_period",
            unit_policy="ratio",
            policy_version=INSURANCE_POLICY_VERSION,
        ),
        _spec(
            "insurance_roe",
            _REQUIRED,
            ("net_income", "average_equity"),
            gate_effect=_BLOCKING,
            reason_code="insurance_roe_missing",
            formula_id="insurance-roe-v1",
            period_basis="same_fiscal_period",
            unit_policy="ratio",
            policy_version=INSURANCE_POLICY_VERSION,
        ),
        _spec(
            "book_value_per_share",
            _OPTIONAL,
            ("common_equity", "diluted_weighted_average_shares"),
            gate_effect=_NON_BLOCKING,
            reason_code="book_value_per_share_missing",
            formula_id="insurance-book-value-per-share-v1",
            period_basis="same_fiscal_period",
            unit_policy="currency_per_share",
            policy_version=INSURANCE_POLICY_VERSION,
        ),
        _spec(
            "investment_income",
            _OPTIONAL,
            ("investment_income",),
            gate_effect=_NON_BLOCKING,
            reason_code="investment_income_not_disclosed",
            formula_id="insurance-investment-income-direct-v1",
            period_basis="company_disclosed_period",
            unit_policy="currency_amount",
            policy_version=INSURANCE_POLICY_VERSION,
        ),
        _spec(
            "solvency_ratio",
            _OPTIONAL,
            ("solvency_ratio",),
            gate_effect=_NON_BLOCKING,
            reason_code="solvency_ratio_not_disclosed",
            formula_id="insurance-solvency-ratio-direct-v1",
            period_basis="statutory_disclosed_period",
            unit_policy="ratio",
            policy_version=INSURANCE_POLICY_VERSION,
        ),
        _spec(
            "price_to_book",
            _OPTIONAL,
            ("market_price", "book_value_per_share"),
            gate_effect=_NON_BLOCKING,
            reason_code="price_to_book_missing",
            formula_id="insurance-price-to-book-v1",
            period_basis="market_price_at_or_after_financial_as_of",
            unit_policy="multiple",
            policy_version=INSURANCE_POLICY_VERSION,
        ),
        _spec(
            "pe_ratio",
            _OPTIONAL,
            ("market_price", "diluted_eps"),
            gate_effect=_NON_BLOCKING,
            reason_code="pe_ratio_missing",
            formula_id="insurance-pe-ratio-v1",
            period_basis="market_price_same_point_in_time",
            unit_policy="multiple",
            policy_version=INSURANCE_POLICY_VERSION,
        ),
        _spec(
            "fcf_yield",
            _NOT_APPLICABLE,
            (),
            gate_effect=_NON_BLOCKING,
            reason_code="insurance_fcf_not_applicable",
            formula_id="insurance-fcf-yield-not-applicable-v1",
            period_basis="not_applicable",
            unit_policy="not_applicable",
            policy_version=INSURANCE_POLICY_VERSION,
        ),
    ),
    IssuerProfile.REIT: (
        _spec(
            "ffo_total",
            _REQUIRED,
            ("ffo_reconciliation",),
            gate_effect=_BLOCKING,
            reason_code="required_ffo_total",
            formula_id="reit-ffo-reconciliation-v1",
            period_basis="same_fiscal_period",
            unit_policy="currency_amount",
            policy_version=_REIT_POLICY_VERSION,
        ),
        _spec(
            "ffo_per_share",
            _REQUIRED,
            ("ffo_total", "diluted_weighted_average_shares"),
            gate_effect=_BLOCKING,
            reason_code="required_ffo_per_share",
            formula_id="reit-ffo-per-share-v1",
            period_basis="same_fiscal_period",
            unit_policy="currency_per_share",
            policy_version=_REIT_POLICY_VERSION,
        ),
        _spec(
            "affo",
            _OPTIONAL,
            ("affo_reconciliation",),
            gate_effect=_NON_BLOCKING,
            reason_code="optional_affo",
            formula_id="company-disclosed-affo-reconciliation-v1",
            period_basis="same_fiscal_period",
            unit_policy="currency_amount",
            policy_version=_REIT_POLICY_VERSION,
        ),
        _spec(
            "same_store_noi",
            _OPTIONAL,
            ("same_store_noi",),
            gate_effect=_NON_BLOCKING,
            reason_code="optional_same_store_noi",
            formula_id="company-disclosed-same-store-noi-v1",
            period_basis="company_disclosed_period",
            unit_policy="currency_amount",
            policy_version=_REIT_POLICY_VERSION,
        ),
        _spec(
            "occupancy",
            _OPTIONAL,
            ("occupancy",),
            gate_effect=_NON_BLOCKING,
            reason_code="optional_occupancy",
            formula_id="company-disclosed-occupancy-v1",
            period_basis="company_disclosed_period",
            unit_policy="ratio_0_to_1",
            policy_version=_REIT_POLICY_VERSION,
        ),
        _spec(
            "net_debt_to_ebitda",
            _OPTIONAL,
            ("net_debt", "ebitda"),
            gate_effect=_NON_BLOCKING,
            reason_code="optional_net_debt_to_ebitda",
            formula_id="reit-net-debt-to-ebitda-v1",
            period_basis="explicit_observation_period",
            unit_policy="multiple",
            policy_version=_REIT_POLICY_VERSION,
        ),
        _spec(
            "dividend_coverage",
            _OPTIONAL,
            ("ffo_attributable_to_common", "common_dividends"),
            gate_effect=_NON_BLOCKING,
            reason_code="optional_dividend_coverage",
            formula_id="reit-dividend-coverage-v1",
            period_basis="same_fiscal_period",
            unit_policy="ratio",
            policy_version=_REIT_POLICY_VERSION,
        ),
        _spec(
            "price_to_ffo",
            _OPTIONAL,
            ("market_price", "ffo_per_share"),
            gate_effect=_NON_BLOCKING,
            reason_code="optional_price_to_ffo",
            formula_id="reit-price-to-ffo-v1",
            period_basis="market_price_at_or_after_financial_as_of",
            unit_policy="multiple",
            policy_version=_REIT_POLICY_VERSION,
        ),
        _spec(
            "pe",
            _NOT_APPLICABLE,
            (),
            gate_effect=_NON_BLOCKING,
            reason_code="not_applicable_reit_pe",
            formula_id="reit-pe-not-applicable-v1",
            period_basis="not_applicable",
            unit_policy="not_applicable",
            policy_version=_REIT_POLICY_VERSION,
        ),
        _spec(
            "fcf_yield",
            _NOT_APPLICABLE,
            (),
            gate_effect=_NON_BLOCKING,
            reason_code="not_applicable_reit_fcf_yield",
            formula_id="reit-fcf-yield-not-applicable-v1",
            period_basis="not_applicable",
            unit_policy="not_applicable",
            policy_version=_REIT_POLICY_VERSION,
        ),
    ),
    IssuerProfile.UTILITY: (
        _spec(
            "utility_operating_margin",
            _REQUIRED,
            ("operating_income", "revenue"),
            gate_effect=_BLOCKING,
            reason_code="utility_operating_margin_missing",
            formula_id="utility-operating-margin-v1",
            period_basis="same_fiscal_period",
            unit_policy="ratio",
            policy_version=UTILITY_POLICY_VERSION,
        ),
        _spec(
            "rate_base",
            _OPTIONAL,
            ("rate_base",),
            gate_effect=_NON_BLOCKING,
            reason_code="rate_base_not_disclosed",
            formula_id="utility-rate-base-direct-v1",
            period_basis="company_disclosed_period",
            unit_policy="currency_amount",
            policy_version=UTILITY_POLICY_VERSION,
        ),
        _spec(
            "capex_intensity",
            _OPTIONAL,
            ("capex", "revenue"),
            gate_effect=_NON_BLOCKING,
            reason_code="capex_intensity_missing",
            formula_id="utility-capex-intensity-v1",
            period_basis="same_fiscal_period",
            unit_policy="ratio",
            policy_version=UTILITY_POLICY_VERSION,
        ),
        _spec(
            "interest_coverage",
            _OPTIONAL,
            ("operating_income", "interest_expense"),
            gate_effect=_NON_BLOCKING,
            reason_code="interest_coverage_missing",
            formula_id="utility-interest-coverage-v1",
            period_basis="same_fiscal_period",
            unit_policy="ratio",
            policy_version=UTILITY_POLICY_VERSION,
        ),
        _spec(
            "utility_roe",
            _OPTIONAL,
            ("net_income", "average_equity"),
            gate_effect=_NON_BLOCKING,
            reason_code="utility_roe_missing",
            formula_id="utility-roe-v1",
            period_basis="same_fiscal_period",
            unit_policy="ratio",
            policy_version=UTILITY_POLICY_VERSION,
        ),
        _spec(
            "price_to_book",
            _OPTIONAL,
            ("market_price", "book_value_per_share"),
            gate_effect=_NON_BLOCKING,
            reason_code="price_to_book_missing",
            formula_id="utility-price-to-book-v1",
            period_basis="market_price_at_or_after_financial_as_of",
            unit_policy="multiple",
            policy_version=UTILITY_POLICY_VERSION,
        ),
        _spec(
            "pe_ratio",
            _OPTIONAL,
            ("market_price", "diluted_eps"),
            gate_effect=_NON_BLOCKING,
            reason_code="pe_ratio_missing",
            formula_id="utility-pe-ratio-v1",
            period_basis="market_price_same_point_in_time",
            unit_policy="multiple",
            policy_version=UTILITY_POLICY_VERSION,
        ),
        _spec(
            "fcf_yield",
            _OPTIONAL,
            ("free_cash_flow", "market_cap"),
            gate_effect=_NON_BLOCKING,
            reason_code="fcf_yield_missing",
            formula_id="utility-fcf-yield-v1",
            period_basis="market_price_same_point_in_time",
            unit_policy="ratio",
            policy_version=UTILITY_POLICY_VERSION,
        ),
    ),
    IssuerProfile.COMMODITY_PRODUCER: (
        _spec(
            "realized_price",
            _REQUIRED,
            ("realized_price",),
            gate_effect=_BLOCKING,
            reason_code="realized_price_missing",
            formula_id="commodity-realized-price-direct-v1",
            period_basis="company_disclosed_period",
            unit_policy="currency_per_production_unit",
            policy_version=COMMODITY_POLICY_VERSION,
        ),
        _spec(
            "production",
            _REQUIRED,
            ("production",),
            gate_effect=_BLOCKING,
            reason_code="production_missing",
            formula_id="commodity-production-direct-v1",
            period_basis="company_disclosed_period",
            unit_policy="production_quantity",
            policy_version=COMMODITY_POLICY_VERSION,
        ),
        _spec(
            "realized_price_change",
            _OPTIONAL,
            ("realized_price", "realized_price_prior"),
            gate_effect=_NON_BLOCKING,
            reason_code="realized_price_change_missing",
            formula_id="commodity-realized-price-change-v1",
            period_basis="comparable_fiscal_periods",
            unit_policy="ratio",
            policy_version=COMMODITY_POLICY_VERSION,
        ),
        _spec(
            "production_change",
            _OPTIONAL,
            ("production", "production_prior"),
            gate_effect=_NON_BLOCKING,
            reason_code="production_change_missing",
            formula_id="commodity-production-change-v1",
            period_basis="comparable_fiscal_periods",
            unit_policy="ratio",
            policy_version=COMMODITY_POLICY_VERSION,
        ),
        _spec(
            "proved_reserves",
            _OPTIONAL,
            ("proved_reserves",),
            gate_effect=_NON_BLOCKING,
            reason_code="proved_reserves_missing",
            formula_id="commodity-proved-reserves-direct-v1",
            period_basis="company_disclosed_period",
            unit_policy="reserve_quantity",
            policy_version=COMMODITY_POLICY_VERSION,
        ),
        _spec(
            "reserve_life_years",
            _OPTIONAL,
            ("proved_reserves", "annual_production"),
            gate_effect=_NON_BLOCKING,
            reason_code="reserve_life_years_missing",
            formula_id="commodity-reserve-life-years-v1",
            period_basis="same_fiscal_period",
            unit_policy="years",
            policy_version=COMMODITY_POLICY_VERSION,
        ),
        _spec(
            "impairment_charge",
            _OPTIONAL,
            ("impairment_charge",),
            gate_effect=_NON_BLOCKING,
            reason_code="impairment_charge_missing",
            formula_id="commodity-impairment-charge-direct-v1",
            period_basis="company_disclosed_period",
            unit_policy="currency_amount",
            policy_version=COMMODITY_POLICY_VERSION,
        ),
        _spec(
            "impairment_to_commodity_revenue",
            _OPTIONAL,
            ("impairment_charge", "commodity_revenue"),
            gate_effect=_NON_BLOCKING,
            reason_code="impairment_to_commodity_revenue_missing",
            formula_id="commodity-impairment-to-commodity-revenue-v1",
            period_basis="same_fiscal_period",
            unit_policy="ratio",
            policy_version=COMMODITY_POLICY_VERSION,
        ),
        _spec(
            "pe_ratio",
            _OPTIONAL,
            ("market_price", "diluted_eps"),
            gate_effect=_NON_BLOCKING,
            reason_code="pe_ratio_missing",
            formula_id="commodity-pe-ratio-v1",
            period_basis="market_price_same_point_in_time",
            unit_policy="multiple",
            policy_version=COMMODITY_POLICY_VERSION,
        ),
    ),
    IssuerProfile.HOLDING_COMPANY: (
        _spec(
            "attributable_holdings_value",
            _REQUIRED,
            ("holdings",),
            gate_effect=_BLOCKING,
            reason_code="holding_components_missing",
            formula_id="holding-attributable-holdings-value-v1",
            period_basis="same_point_in_time",
            unit_policy="currency_amount",
            policy_version=HOLDING_COMPANY_POLICY_VERSION,
        ),
        _spec(
            "holding_company_nav",
            _REQUIRED,
            ("holdings", "parent_net_debt", "other_adjustments"),
            gate_effect=_BLOCKING,
            reason_code="holding_parent_net_debt_missing",
            formula_id="holding-company-nav-v1",
            period_basis="same_point_in_time",
            unit_policy="currency_amount",
            policy_version=HOLDING_COMPANY_POLICY_VERSION,
        ),
        _spec(
            "holding_company_market_cap",
            _OPTIONAL,
            ("market_price", "parent_shares_outstanding"),
            gate_effect=_NON_BLOCKING,
            reason_code="market_price_missing",
            formula_id="holding-company-market-cap-v1",
            period_basis="market_price_same_point_in_time",
            unit_policy="currency_amount",
            policy_version=HOLDING_COMPANY_POLICY_VERSION,
        ),
        _spec(
            "holding_company_nav_discount",
            _OPTIONAL,
            ("holding_company_nav", "holding_company_market_cap"),
            gate_effect=_NON_BLOCKING,
            reason_code="holding_market_cap_unavailable",
            formula_id="holding-company-nav-discount-v1",
            period_basis="market_price_same_point_in_time",
            unit_policy="ratio",
            policy_version=HOLDING_COMPANY_POLICY_VERSION,
        ),
        _spec(
            "pe_ratio",
            _NOT_APPLICABLE,
            (),
            gate_effect=_NON_BLOCKING,
            reason_code="holding_company_pe_not_applicable",
            formula_id="holding-company-pe-not-applicable-v1",
            period_basis="not_applicable",
            unit_policy="not_applicable",
            policy_version=HOLDING_COMPANY_POLICY_VERSION,
        ),
        _spec(
            "fcf_yield",
            _NOT_APPLICABLE,
            (),
            gate_effect=_NON_BLOCKING,
            reason_code="holding_company_fcf_not_applicable",
            formula_id="holding-company-fcf-yield-not-applicable-v1",
            period_basis="not_applicable",
            unit_policy="not_applicable",
            policy_version=HOLDING_COMPANY_POLICY_VERSION,
        ),
        _spec(
            "historical_valuation",
            _NOT_APPLICABLE,
            (),
            gate_effect=_NON_BLOCKING,
            reason_code="holding_company_historical_valuation_not_applicable",
            formula_id="holding-company-historical-valuation-not-applicable-v1",
            period_basis="not_applicable",
            unit_policy="not_applicable",
            policy_version=HOLDING_COMPANY_POLICY_VERSION,
        ),
        _spec(
            "reverse_dcf",
            _NOT_APPLICABLE,
            (),
            gate_effect=_NON_BLOCKING,
            reason_code="holding_company_reverse_dcf_not_applicable",
            formula_id="holding-company-reverse-dcf-not-applicable-v1",
            period_basis="not_applicable",
            unit_policy="not_applicable",
            policy_version=HOLDING_COMPANY_POLICY_VERSION,
        ),
    ),
    IssuerProfile.PRE_REVENUE: (
        _spec(
            "revenue_growth",
            _NOT_APPLICABLE,
            ("revenue_current", "revenue_prior"),
            gate_effect=_NON_BLOCKING,
            reason_code="not_applicable_pre_revenue_growth",
        ),
        _spec(
            "pe_ratio",
            _NOT_APPLICABLE,
            ("market_price", "diluted_eps"),
            gate_effect=_NON_BLOCKING,
            reason_code="not_applicable_pre_revenue_pe_ratio",
        ),
        _spec(
            "cash_burn",
            _REQUIRED,
            ("cash_balance", "operating_cash_flow"),
            gate_effect=_BLOCKING,
            reason_code="required_cash_burn",
        ),
        _spec(
            "runway",
            _REQUIRED,
            ("cash_balance", "cash_burn"),
            gate_effect=_BLOCKING,
            reason_code="required_runway",
        ),
    ),
}

_SPECIAL_SECURITY_POLICIES: dict[SecurityProfile, _MetricSpec] = {
    SecurityProfile.MULTI_CLASS: _MetricSpec(
        "market_cap",
        _OPTIONAL,
        ("market_price", "shares_outstanding"),
        "market_cap:v1",
        "latest_market_period",
        "currency_amount",
        _NON_BLOCKING,
        "share_class_unreconciled",
    ),
    SecurityProfile.RECENT_LISTING: _MetricSpec(
        "historical_valuation",
        _NOT_APPLICABLE,
        ("market_price_history", "historical_eps"),
        "historical_valuation:v1",
        "historical_period",
        "valuation_ratio",
        _NON_BLOCKING,
        "insufficient_history",
    ),
}

_SPAC_SPECS = (
    _MetricSpec(
        "spac_trust_cash",
        _OPTIONAL,
        ("trust_cash",),
        "spac-trust-cash-direct-v1",
        "company_disclosed_period",
        "USD",
        _NON_BLOCKING,
        "spac_trust_cash_missing",
        SPAC_POLICY_VERSION,
    ),
    _MetricSpec(
        "spac_warrant_dilution_ratio",
        _OPTIONAL,
        ("warrants_outstanding", "basic_shares"),
        "spac-warrant-dilution-ratio-v1",
        "company_disclosed_period",
        "ratio",
        _NON_BLOCKING,
        "spac_warrant_dilution_ratio_missing",
        SPAC_POLICY_VERSION,
    ),
    _MetricSpec(
        "spac_pro_forma_shares",
        _OPTIONAL,
        ("basic_shares", "warrants_outstanding"),
        "spac-pro-forma-shares-v1",
        "company_disclosed_period",
        "shares",
        _NON_BLOCKING,
        "spac_pro_forma_shares_missing",
        SPAC_POLICY_VERSION,
    ),
    _MetricSpec(
        "spac_cash_per_pro_forma_share",
        _OPTIONAL,
        ("trust_cash", "basic_shares", "warrants_outstanding"),
        "spac-cash-per-pro-forma-share-v1",
        "company_disclosed_period",
        "USD/share",
        _NON_BLOCKING,
        "spac_cash_per_pro_forma_share_missing",
        SPAC_POLICY_VERSION,
    ),
)

_FOREIGN_ADR_SPECS = (
    _MetricSpec(
        "adr_ratio",
        _OPTIONAL,
        ("ordinary_shares_per_adr",),
        "foreign-adr-ratio-direct-v1",
        "company_disclosed_period",
        "ratio",
        _NON_BLOCKING,
        "adr_ratio_missing",
        FOREIGN_POLICY_VERSION,
    ),
    _MetricSpec(
        "adr_equivalent_shares",
        _OPTIONAL,
        ("ordinary_shares_outstanding", "ordinary_shares_per_adr"),
        "foreign-adr-equivalent-shares-v1",
        "company_disclosed_period",
        "shares",
        _NON_BLOCKING,
        "adr_equivalent_shares_missing",
        FOREIGN_POLICY_VERSION,
    ),
    _MetricSpec(
        "adr_market_cap",
        _OPTIONAL,
        ("adr_market_price", "ordinary_shares_outstanding", "ordinary_shares_per_adr"),
        "foreign-adr-market-cap-v1",
        "market_price_same_point_in_time",
        "USD",
        _NON_BLOCKING,
        "adr_market_cap_missing",
        FOREIGN_POLICY_VERSION,
    ),
)


def _policy(profile: ProfileResult, spec: _MetricSpec) -> MetricPolicy:
    return MetricPolicy(
        metric_id=spec.metric_id,
        issuer_profile=profile.issuer_profile,
        security_profile=profile.security_profile,
        reporting_profile=profile.reporting_profile,
        applicability=spec.applicability,
        required_evidence=list(spec.required_evidence),
        formula_id=spec.formula_id,
        period_basis=spec.period_basis,
        unit_policy=spec.unit_policy,
        gate_effect=spec.gate_effect,
        reason_code=spec.reason_code,
        policy_version=spec.policy_version,
    )


def policy_version_for_profile(profile: ProfileResult | IssuerProfile) -> str:
    """Return the fixed metric policy version for a resolved issuer profile."""
    security_profile = getattr(profile, "security_profile", None)
    if security_profile is SecurityProfile.SPAC or security_profile == SecurityProfile.SPAC.value:
        return SPAC_POLICY_VERSION
    reporting_profile = getattr(profile, "reporting_profile", None)
    if (
        reporting_profile is ReportingProfile.FOREIGN_PRIVATE_ISSUER_IFRS
        or reporting_profile == ReportingProfile.FOREIGN_PRIVATE_ISSUER_IFRS.value
    ):
        return FOREIGN_POLICY_VERSION
    issuer_profile = getattr(profile, "issuer_profile", profile)
    if issuer_profile is IssuerProfile.REIT or issuer_profile == IssuerProfile.REIT.value:
        return _REIT_POLICY_VERSION
    if issuer_profile is IssuerProfile.BANK or issuer_profile == IssuerProfile.BANK.value:
        return BANK_POLICY_VERSION
    if (
        issuer_profile is IssuerProfile.INSURANCE
        or issuer_profile == IssuerProfile.INSURANCE.value
    ):
        return INSURANCE_POLICY_VERSION
    if issuer_profile is IssuerProfile.UTILITY or issuer_profile == IssuerProfile.UTILITY.value:
        return UTILITY_POLICY_VERSION
    if (
        issuer_profile is IssuerProfile.COMMODITY_PRODUCER
        or issuer_profile == IssuerProfile.COMMODITY_PRODUCER.value
    ):
        return COMMODITY_POLICY_VERSION
    if (
        issuer_profile is IssuerProfile.HOLDING_COMPANY
        or issuer_profile == IssuerProfile.HOLDING_COMPANY.value
    ):
        return HOLDING_COMPANY_POLICY_VERSION
    return POLICY_VERSION


def resolve_metric_policies(profile: ProfileResult) -> tuple[MetricPolicy, ...]:
    """Return the fixed metric policy rows applicable to one resolved profile."""
    issuer_specific_partial_profiles = {
        IssuerProfile.BANK,
        IssuerProfile.INSURANCE,
        IssuerProfile.UTILITY,
    }
    is_foreign_ifrs = (
        profile.reporting_profile is ReportingProfile.FOREIGN_PRIVATE_ISSUER_IFRS
    )
    if (
        profile.coverage_level
        in {CoverageLevel.EVIDENCE_ONLY, CoverageLevel.UNSUPPORTED_SECURITY}
        or profile.issuer_profile is IssuerProfile.UNKNOWN
        or profile.security_profile is SecurityProfile.UNSUPPORTED_FUND_SECURITY
        or (
            profile.security_profile is SecurityProfile.ADR
            and not is_foreign_ifrs
        )
        or (
            profile.security_profile is SecurityProfile.UNKNOWN
            and profile.issuer_profile not in issuer_specific_partial_profiles
        )
        or (
            profile.reporting_profile is ReportingProfile.UNKNOWN
            and profile.issuer_profile not in issuer_specific_partial_profiles
        )
    ):
        return ()

    if profile.security_profile is SecurityProfile.SPAC:
        if profile.coverage_level not in {CoverageLevel.FULL, CoverageLevel.PARTIAL}:
            return ()
        return tuple(_policy(profile, spec) for spec in _SPAC_SPECS)

    specs = _POLICY_TABLE.get(profile.issuer_profile, ())

    policies = [_policy(profile, spec) for spec in specs]
    if is_foreign_ifrs and profile.security_profile in {
        SecurityProfile.ADR,
        SecurityProfile.COMMON_STOCK,
    }:
        foreign_specs = tuple(
            spec._replace(
                applicability=(
                    _OPTIONAL
                    if profile.security_profile is SecurityProfile.ADR
                    else _NOT_APPLICABLE
                ),
                reason_code=(
                    spec.reason_code
                    if profile.security_profile is SecurityProfile.ADR
                    else "foreign_adr_not_applicable"
                ),
            )
            for spec in _FOREIGN_ADR_SPECS
        )
        policies.extend(_policy(profile, spec) for spec in foreign_specs)
    special_spec = _SPECIAL_SECURITY_POLICIES.get(profile.security_profile)
    if special_spec is not None and all(
        policy.metric_id != special_spec.metric_id for policy in policies
    ):
        policies.append(_policy(profile, special_spec))
    return tuple(policies)


DecisionStatus = Literal["available", "unavailable", "not_applicable", "invalid"]


def _decision(
    policy: MetricPolicy,
    status: DecisionStatus,
    reason_code: str,
    *,
    evidence_ids: Sequence[str] = (),
    calculation_ids: Sequence[str] = (),
    blocking: bool = False,
) -> PolicyDecision:
    return PolicyDecision(
        metric_id=policy.metric_id,
        status=status,
        evidence_ids=list(evidence_ids),
        calculation_ids=list(calculation_ids),
        reason_code=reason_code,
        blocking=blocking,
    )


def _missing_decision(policy: MetricPolicy, status: DecisionStatus, reason_code: str) -> PolicyDecision:
    return _decision(
        policy,
        status,
        reason_code,
        blocking=policy.gate_effect is GateEffect.BLOCKING,
    )


def evaluate_policy_decisions(
    policies: Sequence[MetricPolicy],
    evidence: Sequence[EvidenceRecord],
    calculations: Sequence[CalculationRecord],
) -> tuple[PolicyDecision, ...]:
    """Evaluate policies from typed records without creating new provenance IDs."""
    evidence_allowlist = {
        record.evidence_id
        for record in evidence
        if record.validation_status is ValidationStatus.VALID
    }
    decisions: list[PolicyDecision] = []

    for policy in policies:
        if policy.applicability is Applicability.NOT_APPLICABLE:
            decisions.append(_decision(policy, "not_applicable", policy.reason_code))
            continue

        formula_matches = [
            calculation
            for calculation in calculations
            if calculation.formula_id == policy.formula_id
        ]
        valid_matches = [
            calculation
            for calculation in formula_matches
            if calculation.validation_status is ValidationStatus.VALID
            and calculation.result is not None
            and set(calculation.input_evidence_ids).issubset(evidence_allowlist)
        ]
        if valid_matches:
            calculation = min(valid_matches, key=lambda item: item.calculation_id)
            result = calculation.result
            assert result is not None
            if policy.metric_id == "pe_ratio" and result < 0:
                decisions.append(_decision(policy, "not_applicable", "negative_eps"))
            else:
                decisions.append(
                    _decision(
                        policy,
                        "available",
                        "validated_calculation",
                        evidence_ids=calculation.input_evidence_ids,
                        calculation_ids=[calculation.calculation_id],
                    )
                )
            continue

        if any(calculation.validation_status is ValidationStatus.INVALID for calculation in formula_matches):
            decisions.append(_missing_decision(policy, "invalid", "calculation_invalid"))
            continue
        if any(calculation.result is None for calculation in formula_matches):
            decisions.append(_missing_decision(policy, "invalid", "calculation_result_missing"))
            continue
        if any(
            calculation.validation_status is ValidationStatus.VALID
            for calculation in formula_matches
        ):
            decisions.append(
                _missing_decision(policy, "invalid", "calculation_evidence_unallowlisted")
            )
            continue
        if formula_matches:
            decisions.append(_missing_decision(policy, "unavailable", "calculation_not_validated"))
            continue

        reason_code = policy.reason_code if policy.reason_code == "share_class_unreconciled" else (
            "required_calculation_missing"
            if policy.applicability is Applicability.REQUIRED
            else "optional_calculation_missing"
        )
        decisions.append(_missing_decision(policy, "unavailable", reason_code))

    return tuple(decisions)


__all__ = [
    "BANK_POLICY_VERSION",
    "COMMODITY_POLICY_VERSION",
    "FOREIGN_POLICY_VERSION",
    "HOLDING_COMPANY_POLICY_VERSION",
    "INSURANCE_POLICY_VERSION",
    "POLICY_VERSION",
    "SPAC_POLICY_VERSION",
    "UTILITY_POLICY_VERSION",
    "evaluate_policy_decisions",
    "policy_version_for_profile",
    "resolve_metric_policies",
]
