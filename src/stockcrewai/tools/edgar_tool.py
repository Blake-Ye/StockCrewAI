from __future__ import annotations

import importlib
import os
import re
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
    section_type: Literal["10k_item_1a", "10q_item_1a", "8k_event"]
    text: str


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
    def _extract_risk_sections(
        form: str,
        text: str | None,
        text_retrieval_status: Literal["not_requested", "available", "unavailable"],
        text_truncated: bool,
    ) -> list[EdgarRiskSection]:
        if (
            text_retrieval_status != "available"
            or not text
            or not text.strip()
            or text_truncated
        ):
            return []

        normalized_form = form.strip().upper().split("/", 1)[0]
        if normalized_form == "8-K":
            return [EdgarRiskSection(section_type="8k_event", text=text)]

        if normalized_form == "10-K":
            pattern = (
                r"^[ \t]*item[ \t]+1a\b[^\n]*(?:\n|$).*?"
                r"(?=^[ \t]*item[ \t]+1b\b)"
            )
            section_type = "10k_item_1a"
        elif normalized_form == "10-Q":
            pattern = (
                r"^[ \t]*(?:part[ \t]+ii[ \t]*[,.:;-]?[ \t]*)?"
                r"item[ \t]+1a\b[^\n]*(?:\n|$).*?"
                r"(?=^[ \t]*(?:part[ \t]+[^,\n]+[ \t]*[,.:;-][ \t]*)?"
                r"item[ \t]+\d+[a-z]?\b)"
            )
            section_type = "10q_item_1a"
        else:
            return []

        matches = list(
            re.finditer(pattern, text, flags=re.IGNORECASE | re.MULTILINE | re.DOTALL)
        )
        if not matches:
            return []
        match = max(matches, key=lambda candidate: len(candidate.group(0)))
        return [EdgarRiskSection(section_type=section_type, text=match.group(0).strip())]

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
        if isinstance(items, str):
            items = [items]
        text = None
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
                        text = raw_text[:max_text_chars]
                        text_truncated = len(raw_text) > max_text_chars
                        text_retrieval_status = "available"
                    else:
                        warnings.append("申报文本不可用：返回内容为空")
                        text_retrieval_status = "unavailable"
            except Exception as exc:
                warnings.append(f"申报文本获取失败：{type(exc).__name__}")
                text_retrieval_status = "unavailable"
        risk_sections = self._extract_risk_sections(
            form,
            text,
            text_retrieval_status,
            text_truncated,
        )
        evidence_id = "ev_filing_{}_{}".format(
            _safe_id(form), _safe_id(accession or filed_at or "unknown")
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
        scope += (("8-K", 20, cutoff),)
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
            if not ytd_candidates:
                continue
            current_ytd = max(
                ytd_candidates,
                key=lambda item: _as_date(item.get("period_end")) or date.min,
            )
            prior_ytd = by_period.get(
                f"{latest_year}-{current_ytd['fiscal_period']}"
            )
            if prior_ytd is None:
                continue
            selected = {
                "latest_fy": latest_fy,
                "current_ytd": current_ytd,
                "prior_ytd": prior_ytd,
            }
            by_role: dict[str, EdgarFact] = {}
            for role in TTM_ROLES:
                metadata = selected[role]
                try:
                    value = _decimal_string(metadata.get("value"))
                except ValueError:
                    continue
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
                by_role[role] = EdgarFact(
                    metric_id=metric_id,
                    evidence_id=evidence_id,
                    value=value,
                    unit=str(metadata.get("unit")) if metadata.get("unit") else None,
                    period_type=metadata.get("period_type"),
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

        def enrich_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
            enriched = dict(metadata)
            get_fact = getattr(container, "get_fact", None)
            tag = enriched.get("tag_used")
            if not callable(get_fact) or not tag:
                return enriched
            try:
                fact = get_fact(tag, period=enriched.get("period"))
            except TypeError:
                try:
                    fact = get_fact(tag)
                except Exception:
                    return enriched
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

        for metric_id in DEFAULT_FACT_CONCEPTS:
            metadata = container.get_concept(metric_id, return_metadata=True)
            if not metadata:
                warnings.append(f"缺少 Company Fact：{metric_id}")
                continue
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
                exchange=[
                    str(exchange)
                    for exchange in (_call_if_needed(_read_attr(company, "get_exchanges")) or [])
                ],
                cik=company_cik,
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
