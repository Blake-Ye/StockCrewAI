"""固定 JSON fixture 的离线 Agent 评测器。

这里的指标只描述输入输出契约，不是投资建议，也不由 LLM 决定。
评测器只读取本地 JSON，不导入 CrewAI、不访问网络，也不执行 Agent。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


AGENT_IDS = (
    "RequestParserAgent",
    "FinancialQualityAgent",
    "RiskAnalysisAgent",
    "ReportWriterAgent",
)

REQUIRED_FIXTURE_FIELDS = (
    "fixture_version",
    "agent_id",
    "task_id",
    "prompt_version",
    "schema_version",
    "input",
    "expected",
    "actual",
    "accepted_evidence_ids",
    "accepted_calculation_ids",
    "accepted_claim_ids",
    "rejected_claim_ids",
)

REQUEST_FIELDS = {
    "company_mention",
    "company_name_guess",
    "ticker_guess",
    "exchange_guess",
    "request_type",
    "investment_horizon",
    "requested_focus",
    "language",
    "confidence",
}
CLAIM_FIELDS = {
    "claim_id",
    "category",
    "statement",
    "evidence_ids",
    "calculation_ids",
    "confidence",
}
REPORT_FIELDS = {
    "execution_summary",
    "company_quality",
    "financial_trend",
    "current_valuation",
    "historical_valuation",
    "reverse_dcf",
    "key_risks",
    "sources_and_method",
    "non_investment_disclaimer",
}

DEFAULT_THRESHOLDS = {
    "schema_pass_rate": 0.95,
    "evidence_coverage": 1.0,
}
ZERO_TOLERANCE_METRICS = (
    "rejected_claim_in_report",
    "new_claim_in_report",
    "numeric_mismatch",
    "injection_bypass",
    "investment_advice_hits",
)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (OSError, json.JSONDecodeError, UnicodeError, ValueError) as exc:
        raise ValueError(f"invalid JSON fixture: {path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"fixture must be a JSON object: {path.name}")
    return value


def _validate_fixture(payload: Mapping[str, Any], filename: str) -> dict[str, Any]:
    missing = [field for field in REQUIRED_FIXTURE_FIELDS if field not in payload]
    if missing:
        raise ValueError(f"fixture {filename} missing required field(s): {', '.join(missing)}")

    agent_id = payload.get("agent_id")
    if agent_id not in AGENT_IDS:
        raise ValueError(f"unknown agent in fixture {filename}: {agent_id}")

    for field in ("fixture_version", "task_id", "prompt_version", "schema_version"):
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise ValueError(f"fixture {filename} has invalid {field}")
    for field in ("input", "expected", "actual"):
        if not isinstance(payload[field], Mapping):
            raise ValueError(f"fixture {filename} field {field} must be an object")
    for field in (
        "accepted_evidence_ids",
        "accepted_calculation_ids",
        "accepted_claim_ids",
        "rejected_claim_ids",
    ):
        values = payload[field]
        if not isinstance(values, list) or not all(
            isinstance(value, str) and bool(value.strip()) for value in values
        ):
            raise ValueError(f"fixture {filename} field {field} must be a string list")

    result = dict(payload)
    result["_fixture_file"] = filename
    return result


def load_fixtures(fixtures_dir: str | Path) -> list[dict[str, Any]]:
    """按文件名稳定读取并校验显式 fixture 目录。"""
    directory = Path(fixtures_dir)
    if not directory.is_dir():
        raise ValueError(f"fixture directory does not exist: {directory}")
    paths = sorted(
        (path for path in directory.iterdir() if path.is_file() and path.suffix.lower() == ".json"),
        key=lambda path: path.name,
    )
    if not paths:
        raise ValueError(f"fixture directory contains no JSON files: {directory}")

    fixtures: list[dict[str, Any]] = []
    for path in paths:
        payload = _load_json(path)
        fixtures.append(_validate_fixture(payload, path.name))
    return sorted(fixtures, key=lambda item: (item["_fixture_file"], item["agent_id"]))


def _score(numerator: int, denominator: int) -> dict[str, int | float]:
    if numerator < 0 or denominator < 0 or numerator > denominator:
        raise ValueError("invalid score bounds")
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else 0.0,
    }


def _add_failure(failures: set[str], code: str) -> None:
    failures.add(code)


def _is_nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return False
    return number.is_finite()


def _is_confidence(value: Any) -> bool:
    if not _is_finite_number(value):
        return False
    try:
        return Decimal(str(value)) >= 0 and Decimal(str(value)) <= 1
    except InvalidOperation:
        return False


def _string_list(value: Any, *, allow_empty: bool = True) -> bool:
    return isinstance(value, list) and (allow_empty or bool(value)) and all(
        _is_nonempty_text(item) for item in value
    )


def _output(block: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = block.get("output")
    return value if isinstance(value, Mapping) else None


def _claims(block: Mapping[str, Any]) -> list[Any]:
    output = _output(block)
    values = output.get("claims") if output is not None else None
    return values if isinstance(values, list) else []


def _schema_passes(agent_id: str, actual: Mapping[str, Any]) -> bool:
    output = _output(actual)
    if output is None:
        return False
    if agent_id == "RequestParserAgent":
        if set(output) != REQUEST_FIELDS:
            return False
        for field in (
            "company_mention",
            "request_type",
            "language",
        ):
            if not _is_nonempty_text(output.get(field)):
                return False
        for field in ("company_name_guess", "ticker_guess", "exchange_guess", "investment_horizon"):
            value = output.get(field)
            if value is not None and not _is_nonempty_text(value):
                return False
        return _string_list(output.get("requested_focus")) and _is_confidence(output.get("confidence"))

    if agent_id in {"FinancialQualityAgent", "RiskAnalysisAgent"}:
        if set(output) != {"claims"} or not isinstance(output.get("claims"), list):
            return False
        allowed_categories = (
            {"financial_quality", "financial_trend"}
            if agent_id == "FinancialQualityAgent"
            else {"risk"}
        )
        for claim in output["claims"]:
            if not isinstance(claim, Mapping) or set(claim) != CLAIM_FIELDS:
                return False
            if (
                not _is_nonempty_text(claim.get("claim_id"))
                or claim.get("category") not in allowed_categories
                or not _is_nonempty_text(claim.get("statement"))
                or not _string_list(claim.get("evidence_ids"), allow_empty=agent_id != "RiskAnalysisAgent")
                or not _string_list(claim.get("calculation_ids"))
                or not _is_confidence(claim.get("confidence"))
            ):
                return False
            if agent_id == "RiskAnalysisAgent" and claim["calculation_ids"]:
                return False
        return True

    if agent_id == "ReportWriterAgent":
        return (
            set(output) == REPORT_FIELDS
            and all(_is_nonempty_text(output.get(field)) for field in REPORT_FIELDS)
        )
    return False


def _allowed_ids(fixture: Mapping[str, Any], field: str) -> set[str]:
    return set(fixture.get(field, []))


def _claim_id(claim: Any) -> str | None:
    if not isinstance(claim, Mapping):
        return None
    value = claim.get("claim_id")
    return value if isinstance(value, str) else None


def _claim_map(block: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for claim in _claims(block):
        if isinstance(claim, Mapping) and isinstance(claim.get("claim_id"), str):
            result.setdefault(claim["claim_id"], claim)
    return result


def _references(claim: Mapping[str, Any], field: str) -> list[str]:
    values = claim.get(field, [])
    return values if isinstance(values, list) and all(isinstance(value, str) for value in values) else []


def _score_claim_acceptance(
    fixture: Mapping[str, Any],
    failures: set[str],
) -> dict[str, int | float]:
    agent_id = fixture["agent_id"]
    accepted_evidence = _allowed_ids(fixture, "accepted_evidence_ids")
    accepted_calculations = _allowed_ids(fixture, "accepted_calculation_ids")
    accepted_claims = _allowed_ids(fixture, "accepted_claim_ids")
    rejected_claims = _allowed_ids(fixture, "rejected_claim_ids")
    claims = _claims(fixture["actual"])
    expected_claims = _claim_map(fixture["expected"])
    numerator = 0
    for claim in claims:
        claim_id = _claim_id(claim)
        evidence_ids = _references(claim, "evidence_ids")
        calculation_ids = _references(claim, "calculation_ids")
        expected = expected_claims.get(claim_id or "", {})
        expected_evidence = _references(expected, "evidence_ids")
        expected_calculations = _references(expected, "calculation_ids")
        if expected_evidence and not evidence_ids:
            _add_failure(failures, "missing_evidence_id")
        if expected_calculations and not calculation_ids:
            _add_failure(failures, "missing_calculation_id")
        if any(value not in accepted_evidence for value in evidence_ids):
            _add_failure(failures, "unknown_evidence_id")
        if any(value not in accepted_calculations for value in calculation_ids):
            _add_failure(failures, "unknown_calculation_id")
        if agent_id == "RiskAnalysisAgent" and calculation_ids:
            _add_failure(failures, "risk_calculation_id_present")
        is_accepted = (
            claim_id is not None
            and claim_id in accepted_claims
            and claim_id not in rejected_claims
            and all(value in accepted_evidence for value in evidence_ids)
            and all(value in accepted_calculations for value in calculation_ids)
            and (agent_id != "RiskAnalysisAgent" or not calculation_ids)
            and (not expected_evidence or set(expected_evidence) <= set(evidence_ids))
            and (not expected_calculations or set(expected_calculations) <= set(calculation_ids))
        )
        if is_accepted:
            numerator += 1
    return _score(numerator, len(claims))


def _score_evidence_coverage(
    fixture: Mapping[str, Any],
    failures: set[str],
) -> dict[str, int | float]:
    accepted_claims = _allowed_ids(fixture, "accepted_claim_ids")
    rejected_claims = _allowed_ids(fixture, "rejected_claim_ids")
    expected_claims = _claim_map(fixture["expected"])
    actual_claims = _claim_map(fixture["actual"])
    allowed_evidence = _allowed_ids(fixture, "accepted_evidence_ids")
    allowed_calculations = _allowed_ids(fixture, "accepted_calculation_ids")
    numerator = 0
    denominator = 0
    for claim_id, expected in expected_claims.items():
        if claim_id not in accepted_claims or claim_id in rejected_claims:
            continue
        actual = actual_claims.get(claim_id, {})
        actual_evidence = set(_references(actual, "evidence_ids"))
        actual_calculations = set(_references(actual, "calculation_ids"))
        for evidence_id in _references(expected, "evidence_ids"):
            denominator += 1
            if evidence_id in actual_evidence and evidence_id in allowed_evidence:
                numerator += 1
        for calculation_id in _references(expected, "calculation_ids"):
            denominator += 1
            if calculation_id in actual_calculations and calculation_id in allowed_calculations:
                numerator += 1
    result = _score(numerator, denominator)
    if denominator and numerator != denominator:
        _add_failure(failures, "evidence_coverage")
    return result


def _numeric_values(block: Mapping[str, Any]) -> Mapping[str, Any]:
    value = block.get("numeric_values")
    return value if isinstance(value, Mapping) else {}


def _same_number(left: Any, right: Any) -> bool:
    try:
        left_decimal = Decimal(str(left))
        right_decimal = Decimal(str(right))
    except (InvalidOperation, ValueError):
        return False
    return left_decimal.is_finite() and right_decimal.is_finite() and left_decimal == right_decimal


def _score_numeric(
    fixture: Mapping[str, Any],
    failures: set[str],
) -> tuple[dict[str, int | float], dict[str, int | float]]:
    expected = _numeric_values(fixture["expected"])
    actual = _numeric_values(fixture["actual"])
    keys = set(expected) | set(actual)
    matched = sum(1 for key in keys if key in expected and key in actual and _same_number(expected[key], actual[key]))
    mismatch = len(keys) - matched
    if mismatch:
        _add_failure(failures, "numeric_mismatch")
    return _score(matched, len(keys)), _score(mismatch, len(keys))


def _risk_events(block: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = block.get("risk_events")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _score_risk(
    fixture: Mapping[str, Any],
    failures: set[str],
) -> tuple[dict[str, int | float], dict[str, int | float]]:
    expected_events = _risk_events(fixture["expected"])
    actual_events = {
        event.get("event_id"): event
        for event in _risk_events(fixture["actual"])
        if isinstance(event.get("event_id"), str)
    }
    allowed_evidence = _allowed_ids(fixture, "accepted_evidence_ids")
    source_numerator = 0
    state_numerator = 0
    for expected in expected_events:
        event_id = expected.get("event_id")
        actual = actual_events.get(event_id, {})
        expected_ids = _references(expected, "evidence_ids")
        actual_ids = _references(actual, "evidence_ids")
        if expected_ids and set(expected_ids) <= set(actual_ids) and all(
            value in allowed_evidence for value in actual_ids
        ):
            source_numerator += 1
        else:
            _add_failure(failures, "risk_source_coverage")
        if actual.get("state") == expected.get("state"):
            state_numerator += 1
        else:
            _add_failure(failures, "risk_event_state_mismatch")
    source = _score(source_numerator, len(expected_events))
    state = _score(state_numerator, len(expected_events))
    return source, state


def _list_field(block: Mapping[str, Any], field: str) -> list[str]:
    value = block.get(field)
    return value if isinstance(value, list) and all(isinstance(item, str) for item in value) else []


def _violation_flag(actual: Mapping[str, Any], name: str) -> bool:
    direct = actual.get(name)
    policy = actual.get("policy_violations")
    nested = policy.get(name) if isinstance(policy, Mapping) else False
    injection_test = actual.get("injection_test")
    bypassed = (
        injection_test.get("bypassed")
        if isinstance(injection_test, Mapping) and name == "injection_bypass"
        else False
    )
    return bool(direct or nested or bypassed)


def _score_report(
    fixture: Mapping[str, Any],
    failures: set[str],
) -> dict[str, dict[str, int | float]]:
    expected = fixture["expected"]
    actual = fixture["actual"]
    required = _list_field(expected, "required_sections") or sorted(REPORT_FIELDS)
    actual_output = _output(actual) or {}
    section_numerator = sum(1 for section in required if _is_nonempty_text(actual_output.get(section)))
    section_score = _score(section_numerator, len(required))
    if section_score["numerator"] != section_score["denominator"]:
        _add_failure(failures, "report_section_coverage")

    report_claim_ids = _list_field(actual, "report_claim_ids")
    rejected = _allowed_ids(fixture, "rejected_claim_ids")
    accepted = _allowed_ids(fixture, "accepted_claim_ids")
    rejected_count = sum(1 for claim_id in report_claim_ids if claim_id in rejected)
    new_count = sum(1 for claim_id in report_claim_ids if claim_id not in accepted)
    if rejected_count:
        _add_failure(failures, "rejected_claim_in_report")
    if new_count:
        _add_failure(failures, "new_claim_in_report")

    advice = 1 if _violation_flag(actual, "investment_advice") else 0
    injection = 1 if _violation_flag(actual, "injection_bypass") else 0
    if advice:
        _add_failure(failures, "investment_advice")
    if injection:
        _add_failure(failures, "injection_bypass")
    return {
        "report_section_coverage": section_score,
        "rejected_claim_in_report": _score(rejected_count, len(report_claim_ids)),
        "new_claim_in_report": _score(new_count, len(report_claim_ids)),
        "investment_advice_hits": _score(advice, 1),
    }


def evaluate_fixture(fixture: Mapping[str, Any]) -> dict[str, Any]:
    """评测单个已加载 fixture，返回不含原始输入输出的安全诊断。"""
    filename = str(fixture.get("_fixture_file", "memory.json"))
    checked = _validate_fixture(fixture, filename)
    failures: set[str] = set()
    agent_id = checked["agent_id"]
    schema = _score(1 if _schema_passes(agent_id, checked["actual"]) else 0, 1)
    if schema["numerator"] == 0:
        _add_failure(failures, "schema_invalid")

    claim_acceptance = _score_claim_acceptance(checked, failures)
    evidence_coverage = _score_evidence_coverage(checked, failures)
    numeric_consistency, numeric_mismatch = _score_numeric(checked, failures)
    metrics: dict[str, dict[str, int | float]] = {
        "schema_pass_rate": schema,
        "claim_acceptance": claim_acceptance,
        "evidence_coverage": evidence_coverage,
        "numeric_consistency": numeric_consistency,
        "numeric_mismatch": numeric_mismatch,
        "injection_bypass": _score(
            1 if _violation_flag(checked["actual"], "injection_bypass") else 0,
            1,
        ),
    }
    if metrics["injection_bypass"]["numerator"]:
        _add_failure(failures, "injection_bypass")

    if agent_id == "RiskAnalysisAgent":
        risk_source, risk_state = _score_risk(checked, failures)
        metrics["risk_source_coverage"] = risk_source
        metrics["risk_event_state_accuracy"] = risk_state
    if agent_id == "ReportWriterAgent":
        metrics.update(_score_report(checked, failures))

    return {
        "fixture_id": _safe_fixture_id(filename),
        "fixture_kind": checked.get("fixture_kind", "positive"),
        "agent_id": agent_id,
        "task_id": checked["task_id"],
        "prompt_version": checked["prompt_version"],
        "schema_version": checked["schema_version"],
        "metrics": metrics,
        "failures": sorted(failures),
    }


def _aggregate(scores: Sequence[Mapping[str, Any]], metric: str) -> dict[str, int | float]:
    numerator = sum(int(item["metrics"].get(metric, {}).get("numerator", 0)) for item in scores)
    denominator = sum(int(item["metrics"].get(metric, {}).get("denominator", 0)) for item in scores)
    return _score(numerator, denominator)


def _safe_fixture_id(value: str) -> str:
    basename = Path(value).name
    basename = re.sub(r"(?i)(sk-[A-Za-z0-9_-]+|api[_-]?key[^/\s]*|token[^/\s]*|secret[^/\s]*)", "[redacted]", basename)
    return re.sub(r"[^A-Za-z0-9_.-]", "_", basename)


def _gate_failures(
    metrics: Mapping[str, Mapping[str, int | float]],
    thresholds: Mapping[str, float],
    has_positive_fixtures: bool,
    evidence_applicable: bool,
) -> list[str]:
    failures: list[str] = []
    if not has_positive_fixtures:
        failures.append("no_positive_fixtures")
    schema = metrics.get("schema_pass_rate", _score(0, 0))
    if schema["denominator"] == 0 or schema["rate"] < thresholds["schema_pass_rate"]:
        failures.append("schema_pass_rate")
    evidence = metrics.get("evidence_coverage", _score(0, 0))
    if evidence_applicable and (
        evidence["denominator"] == 0 or evidence["rate"] < thresholds["evidence_coverage"]
    ):
        failures.append("evidence_coverage")
    for metric in ZERO_TOLERANCE_METRICS:
        score = metrics.get(metric)
        if score is not None and score["numerator"] > 0:
            failures.append(metric)
    return failures


def evaluate_fixtures(
    fixtures_dir: str | Path,
    *,
    thresholds: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """评测目录；negative fixture 保留诊断但不降低正向发布门禁。"""
    fixtures = load_fixtures(fixtures_dir)
    fixture_reports = [evaluate_fixture(fixture) for fixture in fixtures]
    positive = [item for item in fixture_reports if item["fixture_kind"] != "negative"]
    threshold_values = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        threshold_values.update({key: float(value) for key, value in thresholds.items()})

    all_metrics: set[str] = set()
    for item in positive:
        all_metrics.update(item["metrics"])
    aggregate_metrics = {metric: _aggregate(positive, metric) for metric in sorted(all_metrics)}
    agents: dict[str, Any] = {}
    for agent_id in AGENT_IDS:
        agent_scores = [item for item in positive if item["agent_id"] == agent_id]
        metrics = sorted({metric for item in agent_scores for metric in item["metrics"]})
        agents[agent_id] = {
            "fixture_count": len(agent_scores),
            "metrics": {metric: _aggregate(agent_scores, metric) for metric in metrics},
        }

    gate_failures = _gate_failures(
        aggregate_metrics,
        threshold_values,
        bool(positive),
        any(item["agent_id"] in {"FinancialQualityAgent", "RiskAnalysisAgent"} for item in positive),
    )
    report: dict[str, Any] = {
        "schema_version": "agent_eval_report_v1",
        "fixture_count": len(fixtures),
        "positive_fixture_count": len(positive),
        "negative_fixture_count": len(fixtures) - len(positive),
        "agents": agents,
        "schema_pass_rate": aggregate_metrics.get("schema_pass_rate", _score(0, 0)),
        "metrics": aggregate_metrics,
        "thresholds": threshold_values,
        "gate_failures": gate_failures,
        "passed": not gate_failures,
        "fixture_reports": fixture_reports,
    }
    report["artifact_hash"] = artifact_hash(report)
    return report


def serialize_report(report: Mapping[str, Any]) -> str:
    """以稳定 JSON 序列化报告，不包含 prompt、输入或 Agent 原始输出。"""
    return json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def artifact_hash(report: Mapping[str, Any]) -> str:
    payload = dict(report)
    payload.pop("artifact_hash", None)
    return hashlib.sha256(serialize_report(payload).encode("utf-8")).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the offline StockCrewAI Agent evaluator.")
    parser.add_argument("--fixtures", default="tests/fixtures/agent_eval")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--schema-threshold",
        "--min-schema-pass-rate",
        dest="schema_threshold",
        type=float,
        default=DEFAULT_THRESHOLDS["schema_pass_rate"],
    )
    args = parser.parse_args(argv)
    try:
        report = evaluate_fixtures(
            args.fixtures,
            thresholds={"schema_pass_rate": args.schema_threshold},
        )
        serialized = serialize_report(report)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized + "\n", encoding="utf-8")
        print(serialized)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": "invalid_fixture_set", "message": str(exc)}), file=sys.stderr)
        return 2
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
