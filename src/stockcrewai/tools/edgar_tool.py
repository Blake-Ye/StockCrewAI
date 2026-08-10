from __future__ import annotations

import importlib
import os
import re
import warnings as py_warnings
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr, model_validator


DEFAULT_FACT_CONCEPTS = (
    "revenue",
    "net_income",
    "operating_income",
    "operating_cash_flow",
    "capex",
    "cash_and_equivalents",
    "short_term_investments",
    "short_term_debt",
    "long_term_debt",
    "stockholders_equity",
    "total_current_assets",
    "total_current_liabilities",
    "common_shares_outstanding",
    "earnings_per_share_diluted",
)
OPTIONAL_DIRECT_FACT_CONCEPTS = (
    "ordinary_shares_per_adr",
    "ordinary_shares_outstanding",
)

BANK_FACT_CONCEPT_ALIASES = {
    "net_interest_income": (
        "us-gaap:NetInterestIncome",
        "us-gaap:InterestIncomeExpenseNonoperatingNet",
        "us-gaap:InterestIncomeExpenseNet",
    ),
    "noninterest_income": ("us-gaap:NoninterestIncome",),
    "noninterest_expense": ("us-gaap:NoninterestExpense",),
    "total_assets": ("us-gaap:Assets",),
    "stockholders_equity": ("us-gaap:StockholdersEquity",),
    "interest_earning_assets": ("us-gaap:InterestEarningAssets",),
}
_BANK_DURATION_FACTS = (
    "net_interest_income",
    "noninterest_income",
    "noninterest_expense",
)
_BANK_BALANCE_FACTS = (
    "total_assets",
    "stockholders_equity",
    "interest_earning_assets",
)

COMPARATIVE_FACT_CONCEPTS = {
    "revenue": "revenue_prior",
    "common_shares_outstanding": "shares_prior",
}

TTM_FACT_CONCEPTS = (
    "revenue",
    "operating_income",
    "net_income",
    "operating_cash_flow",
    "capex",
    "diluted_eps",
)
TTM_ROLES = ("latest_fy", "current_ytd", "prior_ytd")
DIRECT_TTM_ROLE = "direct_ttm"
SUBSTANTIVE_8K_ITEMS = frozenset(
    {"1.03", "2.05", "2.06", "3.01", "4.01", "4.02", "5.02", "8.01"}
)


class EdgarToolInput(BaseModel):
    company_name: str | None = Field(default=None, description="公司名称")
    ticker: str | None = Field(default=None, description="股票代码")
    include_filing_text: bool = Field(
        default=False,
        description="是否返回申报文本；风险分析需要时设置为 true",
    )
    max_text_chars: int = Field(
        default=12000,
        ge=1000,
        le=100000,
        description="每份申报文本的最大字符数",
    )

    @model_validator(mode="after")
    def require_company_identity(self) -> "EdgarToolInput":
        if not (self.company_name and self.company_name.strip()) and not (
            self.ticker and self.ticker.strip()
        ):
            raise ValueError("company_name 或 ticker 至少提供一个")
        if self.company_name:
            self.company_name = self.company_name.strip()
        if self.ticker:
            self.ticker = self.ticker.strip().upper()
        return self


class EdgarError(BaseModel):
    code: str
    message: str


class EdgarFact(BaseModel):
    metric_id: str
    evidence_id: str
    value: str
    unit: str | None = None
    period_type: str | None = None
    period_basis: Literal["TTM"] | None = None
    period: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    filed_at: str | None = None
    form: str | None = None
    accession_number: str | None = None
    taxonomy: str | None = None
    xbrl_tag: str | None = None
    source_reference: str
    validation_status: Literal["unvalidated", "valid", "invalid"] = "unvalidated"
    warnings: list[str] = Field(default_factory=list)


class EdgarRiskSection(BaseModel):
    section_type: Literal["10k_item_1a", "10q_item_1a", "20f_item_3d", "8k_event"]
    text: str
    section_title: str = ""
    complete: bool = True


class EdgarRiskEligibility(BaseModel):
    evidence_id: str
    eligibility: Literal["eligible", "rejected"]
    reason_code: Literal[
        "eligible_item_1a",
        "eligible_20f_item_3d",
        "eligible_8k_event",
        "attachment_shell",
        "truncated",
        "unsupported_item",
        "missing_body",
    ]
    source_reference: str
    evidence_kind: Literal["item_1a", "item_3d", "substantive_8k_event"] | None = None
    section_title: str | None = None
    filed_at: str | None = None


class EdgarFilingEvidence(BaseModel):
    evidence_id: str
    cik: str
    form: str
    filed_at: str | None = None
    period_end: str | None = None
    accession_number: str | None = None
    items: list[str] = Field(default_factory=list)
    source_reference: str
    text: str | None = None
    text_source_reference: str | None = None
    risk_sections: list[EdgarRiskSection] = Field(default_factory=list)
    risk_eligibility: EdgarRiskEligibility = Field(
        default_factory=lambda: EdgarRiskEligibility(
            evidence_id="",
            eligibility="rejected",
            reason_code="missing_body",
            source_reference="",
        )
    )
    text_retrieval_status: Literal["not_requested", "available", "unavailable"] = (
        "not_requested"
    )
    text_truncated: bool = False
    warnings: list[str] = Field(default_factory=list)
    validation_status: Literal["unvalidated", "valid", "invalid"] = "unvalidated"


class EdgarResult(BaseModel):
    status: Literal["ok", "partial", "error"]
    input_company_name: str | None = None
    input_ticker: str | None = None
    company_name: str | None = None
    ticker: str | None = None
    exchange: list[str] = Field(default_factory=list)
    cik: str | None = None
    sic: str | None = None
    sec_registrant_profile: str | None = None
    sec_security_profile: str | None = None
    sec_reporting_profile: str | None = None
    facts: dict[str, EdgarFact] = Field(default_factory=dict)
    ttm_inputs: dict[str, dict[str, EdgarFact]] = Field(default_factory=dict)
    filings: list[EdgarFilingEvidence] = Field(default_factory=list)
    historical_financial_snapshots: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[EdgarError] = Field(default_factory=list)


def _as_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None
    return None


def _read_attr(value: Any, *names: str) -> Any:
    for name in names:
        try:
            return getattr(value, name)
        except AttributeError:
            continue
    return None


def _format_sic(value: Any) -> str | None:
    """将 SEC SIC 保留为规范化四位数字字符串。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return f"{value:04d}" if 0 <= value <= 9999 else None
    text = str(value).strip()
    if not re.fullmatch(r"\d{1,4}", text):
        return None
    return f"{int(text):04d}"


def _metadata_value(source: Any, *names: str) -> Any:
    if isinstance(source, Mapping):
        for name in names:
            if name in source:
                return source[name]
        return None
    try:
        return _read_attr(source, *names)
    except Exception:
        return None


def _company_sec_metadata(company: Any) -> dict[str, Any]:
    """读取当前 Company 已解析的 SEC metadata，不主动加载新资源。"""
    sources = [company]
    try:
        cached_data = vars(company).get("_data")
    except TypeError:
        cached_data = None
    if cached_data is not None:
        sources.append(cached_data)
    try:
        company_dict = vars(company)
    except TypeError:
        company_dict = {}
    for key in ("data", "submissions"):
        value = company_dict.get(key)
        if value is not None:
            sources.append(value)

    metadata: dict[str, Any] = {}
    for source in sources:
        for key, aliases in {
            "sic": ("sic", "sic_code"),
            "sec_registrant_profile": (
                "sec_registrant_profile",
                "registrant_profile",
            ),
            "sec_security_profile": ("sec_security_profile", "security_profile"),
            "sec_reporting_profile": (
                "sec_reporting_profile",
                "reporting_profile",
            ),
        }.items():
            if metadata.get(key) not in (None, ""):
                continue
            value = _metadata_value(source, *aliases)
            if value not in (None, ""):
                metadata[key] = value
    return metadata


def _safe_metadata_string(value: Any) -> str | None:
    value = getattr(value, "value", value)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _call_if_needed(value: Any) -> Any:
    return value() if callable(value) else value


def _safe_id(value: Any) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", str(value)).strip("_").lower()


def _decimal_string(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError("value is not numeric")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("value is not numeric") from exc
    if not parsed.is_finite():
        raise ValueError("value must be finite")
    return format(parsed, "f")


def _format_cik(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return f"{int(value):010d}"
    except (TypeError, ValueError):
        return str(value).zfill(10)


def _prior_comparable_period(period: Any) -> str | None:
    match = re.fullmatch(r"(\d{4})-(Q[1-4]|FY)", str(period or ""))
    if match is None:
        return None
    return f"{int(match.group(1)) - 1}-{match.group(2)}"


def _is_complete_fiscal_year_ttm(metadata: dict[str, Any]) -> bool:
    """检查单个完整财年是否足以作为直接 TTM Evidence。"""
    try:
        fiscal_year = int(metadata.get("fiscal_year"))
    except (TypeError, ValueError):
        return False
    if (
        str(metadata.get("period") or "") != f"{fiscal_year}-FY"
        or str(metadata.get("fiscal_period") or "").upper() != "FY"
        or str(metadata.get("period_type") or "").lower() != "duration"
    ):
        return False
    if any(
        metadata.get(key) in (None, "")
        for key in (
            "value",
            "unit",
            "tag_used",
            "filing_date",
            "form_type",
            "accession",
        )
    ):
        return False
    if str(metadata.get("form_type")).upper() not in {"10-K", "20-F"}:
        return False
    try:
        _decimal_string(metadata.get("value"))
    except ValueError:
        return False
    period_start = _as_date(metadata.get("period_start"))
    period_end = _as_date(metadata.get("period_end"))
    filed_at = _as_date(metadata.get("filing_date"))
    if not period_start or not period_end or not filed_at or period_start > period_end:
        return False
    duration = period_end - period_start
    return timedelta(days=300) <= duration <= timedelta(days=400)


class EdgarTool(BaseTool):
    name: str = "edgar_company_research"
    description: str = (
        "从 SEC EDGAR 获取公司身份、固定范围的 10-K/10-Q/8-K 申报和 Company Facts。"
        "必须提供 company_name 或 ticker；返回带 Evidence ID 的结构化结果。"
    )
    args_schema: Type[BaseModel] = EdgarToolInput

    _edgar_module: Any = PrivateAttr(default=None)
    _as_of: date = PrivateAttr(default_factory=date.today)

    def __init__(
        self,
        *,
        edgar_module: Any | None = None,
        as_of: date | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._edgar_module = edgar_module
        self._as_of = as_of or date.today()

    def _load_module(self) -> Any:
        return self._edgar_module or importlib.import_module("edgar")

    @staticmethod
    def _configure_http(module: Any) -> None:
        """Avoid Edgartools' file cache so SEC access does not require local writes."""
        if getattr(module, "__name__", None) != "edgar":
            return
        httpclient = importlib.import_module("edgar.httpclient")
        get_http_mgr = getattr(httpclient, "get_http_mgr", None)
        if callable(get_http_mgr):
            httpclient.HTTP_MGR = get_http_mgr(cache_enabled=False)

    @staticmethod
    def _error(code: str, message: str) -> EdgarError:
        return EdgarError(code=code, message=message)

    @staticmethod
    def _normalize_filing_items(items: Any) -> list[str]:
        if isinstance(items, str):
            items = [items]
        normalized: list[str] = []
        for item in items or []:
            if item is None:
                continue
            value = str(item).strip()
            match = re.search(r"(?:item[ \t]*)?(\d+\.\d+)\b", value, re.IGNORECASE)
            normalized.append(match.group(1) if match else value.upper())
        return normalized

    @staticmethod
    def _extract_risk_sections(
        form: str,
        raw_text: str | None,
        items: list[str],
    ) -> list[EdgarRiskSection]:
        if not raw_text or not raw_text.strip():
            return []

        normalized_form = form.strip().upper().split("/", 1)[0]
        if normalized_form == "8-K":
            allowed_items = set(items) & SUBSTANTIVE_8K_ITEMS
            if not allowed_items:
                return []
            pattern = (
                r"^[ \t]*(?P<title>item[ \t]+(?P<item>\d+\.\d+)\b[^\n]*)"
                r"(?:\n|$).*?"
                r"(?=^[ \t]*item[ \t]+\d+\.\d+\b|\Z)"
            )
            matches: dict[str, tuple[int, EdgarRiskSection]] = {}
            for match in re.finditer(
                pattern,
                raw_text,
                flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
            ):
                item = match.group("item")
                if item not in allowed_items:
                    continue
                section_text = match.group(0).strip()
                section_title = match.group("title").strip()
                section_body = section_text[len(section_title) :].strip()
                if not section_body or not re.search(
                    r"[^\s.·…,:;_|-]",
                    section_body,
                ) or re.fullmatch(
                    r"[\s.·…,:;_|-]*(?:page\s*)?\d+(?:\s+of\s+\d+)?[\s.·…,:;_|-]*",
                    section_body,
                    flags=re.IGNORECASE,
                ):
                    continue
                section = EdgarRiskSection(
                    section_type="8k_event",
                    section_title=section_title,
                    text=section_text,
                    complete=True,
                )
                current = matches.get(item)
                if current is None or len(section.text) > len(current[1].text):
                    matches[item] = (match.start(), section)
            return [
                section
                for _, section in sorted(matches.values(), key=lambda value: value[0])
            ]

        if normalized_form == "20-F":
            horizontal_ws = r"[\t \u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]"
            pattern = (
                rf"^{horizontal_ws}*(?P<title>(?:"
                rf"item{horizontal_ws}+3[.]?{horizontal_ws}*d\b[^\n]*?"
                rf"risk{horizontal_ws}+factors\b"
                rf"|d[.]?{horizontal_ws}+risk{horizontal_ws}+factors\b)[^\n]*)"
                r"(?:\n|$).*?"
                rf"(?=^{horizontal_ws}*item{horizontal_ws}+4\b|\Z)"
            )
            section_type = "20f_item_3d"
        elif normalized_form == "10-K":
            pattern = (
                r"^[ \t]*(?P<title>item[ \t]+1a\b[^\n]*)(?:\n|$).*?"
                r"(?=^[ \t]*item[ \t]+1b\b|\Z)"
            )
            section_type = "10k_item_1a"
        elif normalized_form == "10-Q":
            pattern = (
                r"^[ \t]*(?P<title>(?:part[ \t]+ii[ \t]*[,.:;-]?[ \t]*)?"
                r"item[ \t]+1a\b[^\n]*)(?:\n|$).*?"
                r"(?=^[ \t]*(?:part[ \t]+[^,\n]+[ \t]*[,.:;-][ \t]*)?"
                r"item[ \t]+\d+[a-z]?\b|\Z)"
            )
            section_type = "10q_item_1a"
        else:
            return []

        matches = []
        for match in re.finditer(
            pattern,
            raw_text,
            flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
        ):
            matches.append(match)

        if normalized_form == "20-F":
            key_information_pattern = (
                rf"^{horizontal_ws}*item{horizontal_ws}+3[.]?{horizontal_ws}+"
                rf"key{horizontal_ws}+information\b[^\n]*"
                r"(?:\n|$).*?"
                rf"(?=^{horizontal_ws}*item{horizontal_ws}+4\b|\Z)"
            )
            risk_factors_pattern = (
                rf"^{horizontal_ws}*(?P<title>risk{horizontal_ws}+factors)"
                rf"{horizontal_ws}*(?:\n|$).*"
            )
            for item_match in re.finditer(
                key_information_pattern,
                raw_text,
                flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
            ):
                matches.extend(
                    re.finditer(
                        risk_factors_pattern,
                        item_match.group(0),
                        flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
                    )
                )

        filtered_matches = []
        for match in matches:
            if normalized_form == "20-F":
                section_text = match.group(0).strip()
                section_title = match.group("title").strip()
                section_body = section_text[len(section_title) :].strip()
                body_lines = [line.strip() for line in section_body.splitlines() if line.strip()]
                directory_line_pattern = (
                    r"(?:"
                    r"[\s.·…,:;_|-]+"
                    r"|[\s.·…,:;_|-]*(?:page\s*)?\d+(?:\s+of\s+\d+)?[\s.·…,:;_|-]*"
                    r"|(?:table[ \t]+of[ \t]+contents|contents|risk[ \t]+factors)"
                    r"[\s.·…,:;_|-]*(?:page\s*)?\d*(?:\s+of\s+\d+)?[\s.·…,:;_|-]*"
                    r")"
                )
                if not body_lines or all(
                    re.fullmatch(
                        directory_line_pattern,
                        line,
                        flags=re.IGNORECASE,
                    )
                    for line in body_lines
                ):
                    continue
            filtered_matches.append(match)
        matches = filtered_matches
        if not matches:
            return []
        match = max(matches, key=lambda candidate: len(candidate.group(0)))
        return [
            EdgarRiskSection(
                section_type=section_type,
                section_title=match.group("title").strip(),
                text=match.group(0).strip(),
                complete=True,
            )
        ]

    @staticmethod
    def _build_risk_eligibility(
        evidence_id: str,
        form: str,
        items: list[str],
        raw_text: str | None,
        risk_sections: list[EdgarRiskSection],
        filed_at: str | None,
        source_reference: str,
    ) -> EdgarRiskEligibility:
        normalized_form = form.strip().upper().split("/", 1)[0]
        section = risk_sections[0] if risk_sections else None
        if section is not None:
            if section.section_type == "20f_item_3d":
                return EdgarRiskEligibility(
                    evidence_id=evidence_id,
                    eligibility="eligible",
                    evidence_kind="item_3d",
                    reason_code="eligible_20f_item_3d",
                    section_title=section.section_title,
                    filed_at=filed_at,
                    source_reference=source_reference,
                )
            if section.section_type in {"10k_item_1a", "10q_item_1a"}:
                return EdgarRiskEligibility(
                    evidence_id=evidence_id,
                    eligibility="eligible",
                    evidence_kind="item_1a",
                    reason_code="eligible_item_1a",
                    section_title=section.section_title,
                    filed_at=filed_at,
                    source_reference=source_reference,
                )
            return EdgarRiskEligibility(
                evidence_id=evidence_id,
                eligibility="eligible",
                evidence_kind="substantive_8k_event",
                reason_code="eligible_8k_event",
                section_title=section.section_title,
                filed_at=filed_at,
                source_reference=source_reference,
            )

        if not raw_text or not raw_text.strip():
            reason_code = "missing_body"
        elif normalized_form == "8-K" and set(items) == {"2.02", "9.01"}:
            reason_code = "attachment_shell"
        elif normalized_form == "8-K" and set(items) & SUBSTANTIVE_8K_ITEMS:
            reason_code = "truncated"
        else:
            reason_code = "unsupported_item"
        return EdgarRiskEligibility(
            evidence_id=evidence_id,
            eligibility="rejected",
            reason_code=reason_code,
            filed_at=filed_at,
            source_reference=source_reference,
        )

    def _resolve_company(self, module: Any, company_name: str | None, ticker: str | None) -> Any:
        if ticker:
            return module.Company(ticker)
        finder = getattr(module, "find_company", None)
        if finder is None:
            raise RuntimeError("当前 edgartools 不提供按公司名搜索功能")
        matches = finder(company_name, top_n=10)
        ciks = list(getattr(matches, "ciks", []) or [])
        if not ciks:
            raise LookupError(f"未找到公司：{company_name}")
        return module.Company(ciks[0])

    @staticmethod
    def _take(collection: Any, count: int) -> list[Any]:
        head = getattr(collection, "head", None)
        selected = head(count) if callable(head) else collection
        return list(selected)[:count]

    def _filing_evidence(
        self,
        filing: Any,
        company_cik: str,
        include_text: bool,
        max_text_chars: int,
    ) -> EdgarFilingEvidence:
        form = str(_read_attr(filing, "form", "form_type") or "unknown")
        filed_at = _as_iso(_read_attr(filing, "filing_date", "filed_at"))
        period_end = _as_iso(_read_attr(filing, "period_of_report", "report_date"))
        accession = _read_attr(filing, "accession_number", "accession_no")
        accession = str(accession) if accession is not None else None
        source = _read_attr(filing, "url", "homepage_url", "filing_url")
        source = str(_call_if_needed(source) or "")
        items = _read_attr(filing, "items") or []
        items = self._normalize_filing_items(items)
        evidence_id = "ev_filing_{}_{}".format(
            _safe_id(form), _safe_id(accession or filed_at or "unknown")
        )
        text = None
        raw_text = None
        text_source_reference = None
        text_retrieval_status: Literal[
            "not_requested", "available", "unavailable"
        ] = "not_requested"
        text_truncated = False
        warnings: list[str] = []
        if include_text:
            try:
                text_source = _read_attr(filing, "text_url", "text_source_reference")
                text_source = _call_if_needed(text_source) or source
            except Exception as exc:
                warnings.append(f"申报文本来源获取失败：{type(exc).__name__}")
                text_source = source
            text_source_reference = str(text_source) if text_source else None
            if text_source_reference is None:
                warnings.append("申报文本缺少来源引用")

            try:
                text_accessor = _read_attr(filing, "text")
                if text_accessor is None:
                    warnings.append("申报文本不可用：edgartools 未提供 text()")
                    text_retrieval_status = "unavailable"
                else:
                    content = _call_if_needed(text_accessor)
                    if content:
                        if isinstance(content, bytes):
                            content = content.decode("utf-8", errors="replace")
                        raw_text = str(content)
                        text_retrieval_status = "available"
                    else:
                        warnings.append("申报文本不可用：返回内容为空")
                        text_retrieval_status = "unavailable"
            except Exception as exc:
                warnings.append(f"申报文本获取失败：{type(exc).__name__}")
                text_retrieval_status = "unavailable"
        if raw_text is not None:
            text = raw_text[:max_text_chars]
            text_truncated = len(raw_text) > max_text_chars
        risk_sections = self._extract_risk_sections(
            form,
            raw_text,
            items,
        )
        risk_eligibility = self._build_risk_eligibility(
            evidence_id,
            form,
            items,
            raw_text,
            risk_sections,
            filed_at,
            source,
        )
        return EdgarFilingEvidence(
            evidence_id=evidence_id,
            cik=company_cik,
            form=form,
            filed_at=filed_at,
            period_end=period_end,
            accession_number=accession,
            items=[str(item) for item in items],
            source_reference=source,
            text=text,
            text_source_reference=text_source_reference,
            risk_sections=risk_sections,
            risk_eligibility=risk_eligibility,
            text_retrieval_status=text_retrieval_status,
            text_truncated=text_truncated,
            warnings=warnings,
        )

    def _collect_filings(
        self,
        company: Any,
        company_cik: str,
        include_text: bool,
        max_text_chars: int,
    ) -> tuple[list[EdgarFilingEvidence], list[EdgarError]]:
        filings: list[EdgarFilingEvidence] = []
        errors: list[EdgarError] = []
        scope = (("10-K", 3, None), ("10-Q", 4, None))
        cutoff = self._as_of - timedelta(days=180)
        scope += (
            ("8-K", 20, cutoff),
            ("20-F", 3, None),
            ("6-K", 20, cutoff),
        )
        for form, limit, cutoff_date in scope:
            request: dict[str, Any] = {
                "form": form,
                "amendments": False,
                "trigger_full_load": False,
            }
            if cutoff_date:
                request["filing_date"] = f"{cutoff_date.isoformat()}:"
            try:
                collection = company.get_filings(**request)
                for filing in self._take(collection, limit):
                    filed_date = _as_date(_read_attr(filing, "filing_date", "filed_at"))
                    if cutoff_date and (filed_date is None or filed_date < cutoff_date):
                        continue
                    filings.append(
                        self._filing_evidence(
                            filing,
                            company_cik,
                            include_text,
                            max_text_chars,
                        )
                    )
            except Exception as exc:
                errors.append(
                    self._error(
                        f"filings_{_safe_id(form)}_fetch_failed",
                        str(exc),
                    )
                )
        return filings, errors

    def _collect_historical_financial_snapshots(
        self,
        container: Any,
        company_cik: str,
        ticker: str | None,
    ) -> list[dict[str, Any]]:
        """按 filing date 组合可重算的历史 TTM EPS 快照。

        Company Facts 中的 diluted EPS 同时包含 FY、YTD 和单季度观察值。
        历史估值不能直接拿这些观察值除以价格；这里仅组合
        ``FY + current YTD - prior YTD``，并把三项原始 SEC Evidence 保留
        在快照中。快照的可用日期取三项 filing date 的最晚值，保证下游
        的价格日期匹配不会使用尚未公开的财务数据。
        """
        get_all_facts = getattr(container, "get_all_facts", None)
        if not callable(get_all_facts):
            return []

        source = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{company_cik}.json"
        try:
            all_facts = get_all_facts()
            records: list[dict[str, Any]] = []

            def fact_value(fact: Any, *names: str) -> Any:
                if isinstance(fact, dict):
                    for name in names:
                        value = fact.get(name)
                        if value is not None:
                            return value
                    return None
                for name in names:
                    value = _read_attr(fact, name)
                    if value is not None:
                        return value
                return None

            for fact in all_facts:
                try:
                    concept = " ".join(
                        str(fact_value(fact, name) or "").lower()
                        for name in ("concept", "concept_name", "label", "tag_used")
                    )
                    normalized_concept = re.sub(r"[^a-z0-9]", "", concept)
                    if not (
                        "earningspersharediluted" in normalized_concept
                        or "dilutedeps" in normalized_concept
                        or "epsdiluted" in normalized_concept
                    ):
                        continue

                    raw_eps = fact_value(fact, "numeric_value")
                    if raw_eps is None:
                        raw_eps = fact_value(fact, "value")
                    eps = Decimal(_decimal_string(raw_eps))
                    if eps <= 0:
                        continue

                    filing_date = _as_date(fact_value(fact, "filing_date", "filed_at"))
                    period_end = _as_date(fact_value(fact, "period_end"))
                    if (
                        filing_date is None
                        or period_end is None
                        or filing_date > self._as_of
                    ):
                        continue

                    form = fact_value(fact, "form_type", "form")
                    accession = fact_value(fact, "accession", "accession_number")
                    if form in (None, "") or accession in (None, ""):
                        continue
                    form = str(form).strip()
                    accession = str(accession).strip()
                    if not form or not accession:
                        continue
                    filed_at = filing_date.isoformat()
                    period_end_value = period_end.isoformat()
                    period = str(fact_value(fact, "period") or "").strip()
                    fiscal_year = fact_value(fact, "fiscal_year")
                    fiscal_period = fact_value(fact, "fiscal_period")
                    if period and (fiscal_year is None or fiscal_period is None):
                        match = re.fullmatch(r"(\d{4})-(FY|Q[1-3])", period.upper())
                        if match:
                            fiscal_year = fiscal_year or int(match.group(1))
                            fiscal_period = fiscal_period or match.group(2)
                    if fiscal_year is None or fiscal_period is None:
                        continue
                    try:
                        fiscal_year = int(fiscal_year)
                    except (TypeError, ValueError):
                        continue
                    fiscal_period = str(fiscal_period).upper()
                    if fiscal_period not in {"FY", "Q1", "Q2", "Q3"}:
                        continue
                    source_reference = str(
                        fact_value(fact, "source_reference", "source") or source
                    )
                    evidence_id = "ev_{}_historical_eps_{}_{}_{}".format(
                        _safe_id(ticker or company_cik),
                        _safe_id(filed_at),
                        _safe_id(period_end_value),
                        _safe_id(accession),
                    )
                    records.append(
                        {
                            "value": _decimal_string(raw_eps),
                            "evidence_id": evidence_id,
                            "filed_at": filed_at,
                            "period_end": period_end_value,
                            "source_reference": source_reference,
                            "form": form,
                            "accession_number": accession,
                            "fiscal_year": fiscal_year,
                            "fiscal_period": fiscal_period,
                        }
                    )
                except (AttributeError, InvalidOperation, TypeError, ValueError):
                    continue

            # 同一财务期间可能有修订或 amendment；对每个期间只使用截至
            # as_of 可见的最新 filing，避免重复观察值污染月度历史序列。
            by_period: dict[tuple[int, str], dict[str, Any]] = {}
            for record in records:
                key = (record["fiscal_year"], record["fiscal_period"])
                previous = by_period.get(key)
                if previous is None or (
                    record["filed_at"], record["period_end"]
                ) > (previous["filed_at"], previous["period_end"]):
                    by_period[key] = record

            snapshots: list[dict[str, Any]] = []
            for (fiscal_year, fiscal_period), current in sorted(by_period.items()):
                if fiscal_period not in {"Q1", "Q2", "Q3"}:
                    continue
                latest_fy = by_period.get((fiscal_year - 1, "FY"))
                prior_ytd = by_period.get((fiscal_year - 1, fiscal_period))
                if latest_fy is None or prior_ytd is None:
                    continue
                role_records = (latest_fy, current, prior_ytd)
                filed_at = max(item["filed_at"] for item in role_records)
                ttm_eps = (
                    Decimal(latest_fy["value"])
                    + Decimal(current["value"])
                    - Decimal(prior_ytd["value"])
                )
                if ttm_eps <= 0:
                    continue
                financial_evidence_ids = [item["evidence_id"] for item in role_records]
                snapshots.append(
                    {
                        "as_of": filed_at,
                        "filed_at": filed_at,
                        "period_end": current["period_end"],
                        "period_basis": "TTM",
                        "ttm_eps": format(ttm_eps.normalize(), "f"),
                        "financial_evidence_ids": financial_evidence_ids,
                        "source_reference": current["source_reference"],
                        "form": current["form"],
                        "accession_number": current["accession_number"],
                    }
                )
            snapshots.sort(
                key=lambda snapshot: (
                    snapshot["filed_at"],
                    snapshot["period_end"],
                    tuple(snapshot.get("financial_evidence_ids", [])),
                )
            )
            return snapshots
        except Exception:
            return []

    def _collect_ttm_inputs(
        self,
        container: Any,
        company_cik: str,
        ticker: str | None,
    ) -> dict[str, dict[str, EdgarFact]]:
        inputs: dict[str, dict[str, EdgarFact]] = {}
        get_concept = getattr(container, "get_concept", None)
        if not callable(get_concept):
            return inputs
        get_fact = getattr(container, "get_fact", None)

        def enrich_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
            enriched = dict(metadata)
            tag = enriched.get("tag_used")
            period = enriched.get("period")
            if not callable(get_fact) or not tag or not period:
                return enriched
            try:
                fact = get_fact(tag, period=period)
            except Exception:
                return enriched
            if fact is None:
                return enriched

            def fact_value(name: str) -> Any:
                if isinstance(fact, dict):
                    return fact.get(name)
                return _read_attr(fact, name)

            for metadata_key, fact_key in (
                ("period_start", "period_start"),
                ("period_end", "period_end"),
                ("period_type", "period_type"),
                ("fiscal_year", "fiscal_year"),
                ("fiscal_period", "fiscal_period"),
                ("filing_date", "filing_date"),
                ("form_type", "form_type"),
                ("accession", "accession"),
            ):
                if enriched.get(metadata_key) in (None, ""):
                    value = fact_value(fact_key)
                    if value is not None:
                        enriched[metadata_key] = value
            return enriched

        def build_fact(
            metric_id: str,
            role: str,
            metadata: dict[str, Any],
            *,
            period_basis: Literal["TTM"] | None = None,
        ) -> EdgarFact | None:
            try:
                value = _decimal_string(metadata.get("value"))
            except ValueError:
                return None
            period = str(metadata.get("period") or "unknown")
            accession = str(
                metadata.get("accession")
                or metadata.get("accession_number")
                or "unknown"
            )
            evidence_id = "ev_{}_{}_{}_{}_{}".format(
                _safe_id(ticker or "company"),
                _safe_id(metric_id),
                role,
                _safe_id(period),
                _safe_id(accession),
            )
            return EdgarFact(
                metric_id=metric_id,
                evidence_id=evidence_id,
                value=value,
                unit=str(metadata.get("unit")) if metadata.get("unit") else None,
                period_type=metadata.get("period_type"),
                period_basis=period_basis,
                period=period,
                period_start=_as_iso(metadata.get("period_start")),
                period_end=_as_iso(metadata.get("period_end")),
                fiscal_year=metadata.get("fiscal_year"),
                fiscal_period=metadata.get("fiscal_period"),
                filed_at=_as_iso(metadata.get("filing_date", metadata.get("filed_at"))),
                form=metadata.get("form_type", metadata.get("form")),
                accession_number=accession,
                taxonomy=(
                    str(metadata.get("tag_used")).split(":", 1)[0]
                    if metadata.get("tag_used")
                    else None
                ),
                xbrl_tag=metadata.get("tag_used"),
                source_reference=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{company_cik}.json",
            )

        for metric_id in TTM_FACT_CONCEPTS:
            # 内部统一使用 diluted_eps，但 edgartools/SEC Company Facts
            # 实际可能只接受 earnings_per_share_diluted。先按内部名查询，
            # 没有结果时再查询 SEC 原始概念名；下游仍只暴露统一 metric_id。
            concept_names = (
                ("diluted_eps", "earnings_per_share_diluted")
                if metric_id == "diluted_eps"
                else (metric_id,)
            )
            candidates: list[dict[str, Any]] = []
            concept_name = concept_names[0]
            latest = None
            for candidate_name in concept_names:
                try:
                    latest = get_concept(candidate_name, return_metadata=True)
                except Exception:
                    latest = None
                if latest:
                    concept_name = candidate_name
                    break
            if latest:
                latest = enrich_metadata(dict(latest))
                candidates.append(latest)
            latest_year = None
            if latest:
                latest_year = latest.get("fiscal_year")
                if latest.get("fiscal_period") != "FY":
                    latest_year = int(latest_year) - 1 if latest_year else None
            if latest_year:
                periods = [
                    f"{latest_year}-FY",
                    *(
                        f"{year}-Q{quarter}"
                        for year in (int(latest_year), int(latest_year) + 1)
                        for quarter in range(1, 5)
                    ),
                ]
                for period in periods:
                    if latest and period == latest.get("period"):
                        continue
                    try:
                        metadata = get_concept(
                            concept_name,
                            period=period,
                            return_metadata=True,
                        )
                    except Exception:
                        continue
                    if metadata:
                        candidates.append(enrich_metadata(dict(metadata)))

            by_period: dict[str, dict[str, Any]] = {}
            for metadata in candidates:
                period = str(metadata.get("period") or "")
                fiscal_period = str(
                    metadata.get("fiscal_period")
                    or (period.rsplit("-", 1)[-1] if "-" in period else "")
                ).upper()
                fiscal_year = metadata.get("fiscal_year")
                if fiscal_year is None and period[:4].isdigit():
                    fiscal_year = int(period[:4])
                if not period or fiscal_year is None:
                    continue
                filed_at = _as_date(metadata.get("filing_date", metadata.get("filed_at")))
                if filed_at and filed_at > self._as_of:
                    continue
                key = f"{fiscal_year}-{fiscal_period}"
                metadata["fiscal_year"] = int(fiscal_year)
                metadata["fiscal_period"] = fiscal_period
                by_period[key] = metadata
            fy_candidates = [
                item for item in by_period.values() if item["fiscal_period"] == "FY"
            ]
            if not fy_candidates:
                continue
            latest_fy = max(fy_candidates, key=lambda item: item["fiscal_year"])
            latest_year = latest_fy["fiscal_year"]
            ytd_candidates = [
                item
                for item in by_period.values()
                if item["fiscal_year"] == latest_year + 1
                and item["fiscal_period"] in {"Q1", "Q2", "Q3"}
            ]
            current_ytd = (
                max(
                    ytd_candidates,
                    key=lambda item: _as_date(item.get("period_end")) or date.min,
                )
                if ytd_candidates
                else None
            )
            prior_ytd = (
                by_period.get(f"{latest_year}-{current_ytd['fiscal_period']}")
                if current_ytd is not None
                else None
            )
            if current_ytd is None or prior_ytd is None:
                if _is_complete_fiscal_year_ttm(latest_fy):
                    direct_fact = build_fact(
                        metric_id,
                        DIRECT_TTM_ROLE,
                        latest_fy,
                        period_basis="TTM",
                    )
                    if direct_fact is not None:
                        inputs[metric_id] = {DIRECT_TTM_ROLE: direct_fact}
                continue
            selected = {
                "latest_fy": latest_fy,
                "current_ytd": current_ytd,
                "prior_ytd": prior_ytd,
            }
            by_role: dict[str, EdgarFact] = {}
            for role in TTM_ROLES:
                metadata = selected[role]
                fact = build_fact(metric_id, role, metadata)
                if fact is not None:
                    by_role[role] = fact
            if by_role:
                inputs[metric_id] = by_role
        return inputs

    def _collect_facts(
        self,
        company: Any,
        company_cik: str,
        ticker: str | None,
    ) -> tuple[
        dict[str, EdgarFact],
        list[str],
        list[dict[str, Any]],
        dict[str, dict[str, EdgarFact]],
    ]:
        warnings: list[str] = []
        facts: dict[str, EdgarFact] = {}
        container = company.get_facts()
        if container is None:
            return facts, ["SEC Company Facts 不可用"], [], {}
        source = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{company_cik}.json"
        sec_metadata = _company_sec_metadata(company)
        sec_registrant_profile = _safe_metadata_string(
            sec_metadata.get("sec_registrant_profile")
        )
        sec_sic = _format_sic(sec_metadata.get("sic"))
        is_bank_issuer = (
            sec_registrant_profile.casefold() == "bank"
            if sec_registrant_profile is not None
            else sec_sic is not None and 6020 <= int(sec_sic) <= 6022
        )
        net_income_metadata: dict[str, Any] | None = None

        def bank_fact_metadata(
            aliases: tuple[str, ...], period: str | None = None
        ) -> dict[str, Any] | None:
            get_fact = getattr(container, "get_fact", None)
            if not callable(get_fact):
                return None
            for alias in aliases:
                for requested_tag in (alias, alias.split(":", 1)[-1]):
                    with py_warnings.catch_warnings():
                        py_warnings.simplefilter("ignore", UserWarning)
                        try:
                            fact = get_fact(requested_tag, period=period)
                        except Exception:
                            continue
                    if fact is None:
                        continue

                    def fact_value(name: str) -> Any:
                        if isinstance(fact, Mapping):
                            return fact.get(name)
                        return _read_attr(fact, name)

                    raw_tag = (
                        fact_value("concept")
                        or fact_value("tag_used")
                        or fact_value("xbrl_tag")
                    )
                    actual_tag = str(raw_tag or alias)
                    actual_full_tag = (
                        actual_tag
                        if ":" in actual_tag
                        else f"us-gaap:{actual_tag}"
                    )
                    if actual_full_tag != alias:
                        continue

                    fiscal_year = fact_value("fiscal_year")
                    fiscal_period = fact_value("fiscal_period")
                    form_type = fact_value("form_type") or fact_value("form")
                    accession = fact_value("accession") or fact_value(
                        "accession_number"
                    )
                    period_type = fact_value("period_type")
                    period_start = fact_value("period_start")
                    period_end = fact_value("period_end")
                    if (
                        str(period_type or "").lower()
                        in {"instant", "point-in-time"}
                        and period_start in (None, "")
                        and period_end is not None
                    ):
                        period_start = period_end
                    value = fact_value("value")
                    if value is None:
                        value = fact_value("numeric_value")
                    if value is None:
                        continue
                    return {
                        "value": value,
                        "numeric_value": fact_value("numeric_value"),
                        "unit": fact_value("unit"),
                        "period": period
                        or (
                            f"{fiscal_year}-{fiscal_period}"
                            if fiscal_year is not None and fiscal_period
                            else None
                        ),
                        "period_start": period_start,
                        "period_end": period_end,
                        "period_type": period_type,
                        "filing_date": fact_value("filing_date"),
                        "filed_at": fact_value("filing_date"),
                        "form": form_type,
                        "form_type": form_type,
                        "accession": accession,
                        "accession_number": accession,
                        "concept": actual_full_tag,
                        "tag_used": actual_full_tag,
                        "taxonomy": fact_value("taxonomy")
                        or actual_full_tag.split(":", 1)[0],
                        "fiscal_year": fiscal_year,
                        "fiscal_period": fiscal_period,
                    }
            return None

        def enrich_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
            enriched = dict(metadata)
            get_fact = getattr(container, "get_fact", None)
            tag = enriched.get("tag_used")
            if not callable(get_fact) or not tag:
                return enriched
            with py_warnings.catch_warnings():
                py_warnings.simplefilter("ignore", UserWarning)
                try:
                    fact = get_fact(tag, period=enriched.get("period"))
                except TypeError:
                    try:
                        fact = get_fact(tag)
                    except Exception:
                        return enriched
                except Exception:
                    return enriched
                if fact is None and ":" in str(tag):
                    try:
                        fact = get_fact(
                            str(tag).split(":", 1)[-1],
                            period=enriched.get("period"),
                        )
                    except Exception:
                        fact = None
            if fact is None:
                return enriched

            def fact_value(name: str) -> Any:
                if isinstance(fact, dict):
                    return fact.get(name)
                return _read_attr(fact, name)

            for metadata_key, fact_key in (
                ("period_start", "period_start"),
                ("period_end", "period_end"),
                ("period_type", "period_type"),
                ("fiscal_year", "fiscal_year"),
                ("fiscal_period", "fiscal_period"),
                ("filing_date", "filing_date"),
                ("form_type", "form_type"),
                ("accession", "accession"),
            ):
                if enriched.get(metadata_key) in (None, ""):
                    value = fact_value(fact_key)
                    if value is not None:
                        enriched[metadata_key] = value

            if not enriched.get("period"):
                fiscal_year = enriched.get("fiscal_year")
                fiscal_period = enriched.get("fiscal_period")
                if fiscal_year is not None and fiscal_period:
                    enriched["period"] = f"{fiscal_year}-{fiscal_period}"
            if not enriched.get("tag_used"):
                concept = fact_value("concept")
                if concept:
                    enriched["tag_used"] = concept
            return enriched

        def add_fact(output_metric_id: str, metadata: dict[str, Any]) -> bool:
            metadata = enrich_metadata(metadata)
            try:
                value = _decimal_string(metadata.get("value"))
            except ValueError:
                warnings.append(f"Company Fact 非有限数值：{output_metric_id}")
                return False
            period = metadata.get("period")
            evidence_id = "ev_{}_{}_{}".format(
                _safe_id(ticker or company_cik),
                _safe_id(output_metric_id),
                _safe_id(period or metadata.get("period_end") or "unknown"),
            )
            facts[output_metric_id] = EdgarFact(
                metric_id=output_metric_id,
                evidence_id=evidence_id,
                value=value,
                unit=str(metadata.get("unit")) if metadata.get("unit") else None,
                period_type=metadata.get("period_type"),
                period=str(period) if period else None,
                period_start=_as_iso(metadata.get("period_start")),
                period_end=_as_iso(metadata.get("period_end")),
                fiscal_year=metadata.get("fiscal_year"),
                fiscal_period=metadata.get("fiscal_period"),
                filed_at=_as_iso(metadata.get("filing_date")),
                form=metadata.get("form_type"),
                accession_number=metadata.get("accession"),
                taxonomy=(
                    str(metadata.get("tag_used")).split(":", 1)[0]
                    if metadata.get("tag_used")
                    else None
                ),
                xbrl_tag=metadata.get("tag_used"),
                source_reference=source,
            )
            return True

        def optional_direct_fact_metadata(metric_id: str) -> dict[str, Any] | None:
            get_concept = getattr(container, "get_concept", None)
            if not callable(get_concept):
                return None
            with py_warnings.catch_warnings():
                py_warnings.simplefilter("ignore", UserWarning)
                try:
                    metadata = get_concept(metric_id, return_metadata=True)
                except Exception:
                    return None
            if not isinstance(metadata, Mapping):
                return None
            returned_name = next(
                (
                    metadata.get(key)
                    for key in ("concept_name", "metric_id", "concept")
                    if metadata.get(key) not in (None, "")
                ),
                None,
            )
            if str(returned_name).strip().casefold() != metric_id:
                return None
            if any(
                metadata.get(key) in (None, "")
                for key in (
                    "value",
                    "unit",
                    "period",
                    "period_type",
                    "period_start",
                    "period_end",
                    "filing_date",
                    "form_type",
                    "accession",
                    "tag_used",
                )
            ):
                return None
            try:
                _decimal_string(metadata.get("value"))
            except ValueError:
                return None
            return dict(metadata)

        for metric_id in DEFAULT_FACT_CONCEPTS:
            metadata = container.get_concept(metric_id, return_metadata=True)
            if not metadata:
                warnings.append(f"缺少 Company Fact：{metric_id}")
                continue
            if metric_id == "net_income":
                net_income_metadata = enrich_metadata(metadata)
            if not add_fact(metric_id, metadata):
                continue

            prior_metric_id = COMPARATIVE_FACT_CONCEPTS.get(metric_id)
            if prior_metric_id is None:
                continue
            prior_period = _prior_comparable_period(metadata.get("period"))
            if prior_period is None:
                warnings.append(f"无法确定上一可比期：{metric_id}")
                continue
            prior_metadata = container.get_concept(
                metric_id,
                period=prior_period,
                return_metadata=True,
            )
            if not prior_metadata:
                warnings.append(
                    f"缺少上一可比期 Company Fact：{metric_id}（{prior_period}）"
                )
                continue
            add_fact(prior_metric_id, prior_metadata)

        for metric_id in OPTIONAL_DIRECT_FACT_CONCEPTS:
            metadata = optional_direct_fact_metadata(metric_id)
            if metadata is not None:
                add_fact(metric_id, metadata)

        if is_bank_issuer and net_income_metadata is not None:
            target_period = str(net_income_metadata.get("period") or "")
            target_start = _as_date(net_income_metadata.get("period_start"))
            target_end = _as_date(net_income_metadata.get("period_end"))
            is_duration = (
                bool(target_period)
                and str(net_income_metadata.get("period_type") or "").lower()
                == "duration"
                and target_start is not None
                and target_end is not None
                and target_start <= target_end
            )
            if is_duration:
                for metric_id in _BANK_DURATION_FACTS:
                    metadata = bank_fact_metadata(
                        BANK_FACT_CONCEPT_ALIASES[metric_id], target_period
                    )
                    if metadata is None:
                        warnings.append(f"缺少银行 Company Fact：{metric_id}")
                        continue
                    metadata = enrich_metadata(metadata)
                    if not (
                        str(metadata.get("period") or "") == target_period
                        and str(metadata.get("period_type") or "").lower()
                        == "duration"
                        and _as_date(metadata.get("period_start")) == target_start
                        and _as_date(metadata.get("period_end")) == target_end
                    ):
                        warnings.append(f"银行 Company Fact 期间不匹配：{metric_id}")
                        continue
                    add_fact(metric_id, metadata)

                beginning_period = _prior_comparable_period(target_period)
                if beginning_period is None:
                    warnings.append("无法确定银行资产负债表 opening period")
                else:
                    for metric_id in _BANK_BALANCE_FACTS:
                        aliases = BANK_FACT_CONCEPT_ALIASES[metric_id]
                        for role, period in (
                            ("beginning", beginning_period),
                            ("ending", target_period),
                        ):
                            metadata = bank_fact_metadata(aliases, period)
                            output_metric_id = f"{metric_id}_{role}"
                            if metadata is None:
                                warnings.append(
                                    f"缺少银行 Company Fact：{output_metric_id}"
                                )
                                continue
                            metadata = enrich_metadata(metadata)
                            period_start = _as_date(metadata.get("period_start"))
                            period_end = _as_date(metadata.get("period_end"))
                            if not (
                                str(metadata.get("period") or "") == period
                                and str(metadata.get("period_type") or "").lower()
                                in {"instant", "point-in-time"}
                                and period_start is not None
                                and period_end is not None
                                and period_start == period_end
                            ):
                                warnings.append(
                                    f"银行 Company Fact 非 point-in-time：{output_metric_id}"
                                )
                                continue
                            add_fact(output_metric_id, metadata)
        return (
            facts,
            warnings,
            self._collect_historical_financial_snapshots(container, company_cik, ticker),
            self._collect_ttm_inputs(container, company_cik, ticker),
        )

    def _run(
        self,
        company_name: str | None = None,
        ticker: str | None = None,
        include_filing_text: bool = False,
        max_text_chars: int = 12000,
    ) -> EdgarResult:
        input_company_name = company_name.strip() if company_name else None
        input_ticker = ticker.strip().upper() if ticker else None
        try:
            module = self._load_module()
            self._configure_http(module)
            identity = os.getenv("EDGAR_IDENTITY", "").strip()
            if not identity:
                raise EnvironmentError("未配置 EDGAR_IDENTITY，无法访问 SEC EDGAR")
            module.set_identity(identity)
            company = self._resolve_company(module, input_company_name, input_ticker)
            company_cik = _format_cik(_read_attr(company, "cik"))
            if company_cik is None:
                raise RuntimeError("SEC 公司身份缺少 CIK")
            official_ticker = _call_if_needed(_read_attr(company, "get_ticker"))
            if not official_ticker:
                tickers = _read_attr(company, "tickers") or []
                official_ticker = tickers[0] if tickers else input_ticker
            official_ticker = str(official_ticker).upper() if official_ticker else None
            exchanges = _call_if_needed(_read_attr(company, "get_exchanges")) or []
            sec_metadata = _company_sec_metadata(company)
            sic = _format_sic(sec_metadata.get("sic"))

            facts: dict[str, EdgarFact] = {}
            filings: list[EdgarFilingEvidence] = []
            historical_financial_snapshots: list[dict[str, Any]] = []
            ttm_inputs: dict[str, dict[str, EdgarFact]] = {}
            warnings: list[str] = []
            errors: list[EdgarError] = []
            try:
                (
                    facts,
                    warnings,
                    historical_financial_snapshots,
                    ttm_inputs,
                ) = self._collect_facts(
                    company,
                    company_cik,
                    official_ticker,
                )
            except Exception as exc:
                errors.append(self._error("facts_fetch_failed", str(exc)))
            try:
                filings, filing_errors = self._collect_filings(
                    company,
                    company_cik,
                    include_filing_text,
                    max_text_chars,
                )
                errors.extend(filing_errors)
                warnings.extend(
                    warning
                    for filing in filings
                    for warning in filing.warnings
                )
            except Exception as exc:
                errors.append(self._error("filings_fetch_failed", str(exc)))

            has_sec_data = bool(facts or filings)
            status: Literal["ok", "partial", "error"] = "ok"
            if warnings or errors:
                status = "partial" if has_sec_data or company_cik else "error"
            return EdgarResult(
                status=status,
                input_company_name=input_company_name,
                input_ticker=input_ticker,
                company_name=str(_read_attr(company, "name") or input_company_name),
                ticker=official_ticker,
                exchange=[str(exchange) for exchange in exchanges],
                cik=company_cik,
                sic=sic,
                sec_registrant_profile=_safe_metadata_string(
                    sec_metadata.get("sec_registrant_profile")
                ),
                sec_security_profile=_safe_metadata_string(
                    sec_metadata.get("sec_security_profile")
                ),
                sec_reporting_profile=_safe_metadata_string(
                    sec_metadata.get("sec_reporting_profile")
                ),
                facts=facts,
                ttm_inputs=ttm_inputs,
                filings=filings,
                historical_financial_snapshots=historical_financial_snapshots,
                warnings=warnings,
                errors=errors,
            )
        except Exception as exc:
            return EdgarResult(
                status="error",
                input_company_name=input_company_name,
                input_ticker=input_ticker,
                errors=[self._error(type(exc).__name__, str(exc))],
            )
