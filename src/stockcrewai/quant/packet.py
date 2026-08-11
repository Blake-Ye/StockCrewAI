"""把已校验的量化 artifact 汇总为可审计的研究 packet。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
from typing import Any

from stockcrewai.models.profile import CoverageLevel
from stockcrewai.models.quant import QuantFieldProvenance, QuantResearchPacket


_ARTIFACT_HASH = re.compile(r"[0-9a-f]{64}")
_FACTOR_ARTIFACT_SCHEMA_VERSION = "quant-factor-artifact-v1"
_BACKTEST_ARTIFACT_SCHEMA_VERSION = "quant-backtest-artifact-v1"
_STATUSES = {"available", "unavailable", "invalid"}
_RANKING_STATUSES = {"available", "unavailable"}


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("quant artifact 必须是稳定 JSON mapping") from exc


def _validated_artifact(
    artifact: object, required_fields: tuple[str, ...], name: str
) -> Mapping[str, Any]:
    if not isinstance(artifact, Mapping):
        raise ValueError(f"{name} artifact 必须是 mapping")

    artifact_hash = artifact.get("artifact_hash")
    if not isinstance(artifact_hash, str) or _ARTIFACT_HASH.fullmatch(artifact_hash) is None:
        raise ValueError(f"{name} artifact_hash 必须是 64 位小写 sha256")
    body = {key: value for key, value in artifact.items() if key != "artifact_hash"}
    expected_hash = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    if expected_hash != artifact_hash:
        raise ValueError(f"{name} artifact_hash 校验失败")

    for field in required_fields:
        if field not in artifact:
            raise ValueError(f"{name} 缺少 {field}")
    return artifact


def _required(mapping: Mapping[str, Any], key: str, name: str) -> Any:
    if key not in mapping:
        raise ValueError(f"缺少 {name}")
    return mapping[key]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} 必须是 mapping")
    return value


def _required_mapping(mapping: Mapping[str, Any], key: str, name: str) -> Mapping[str, Any]:
    return _mapping(_required(mapping, key, name), name)


def _sequence(value: object, name: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} 必须是序列")
    return value


def _required_sequence(mapping: Mapping[str, Any], key: str, name: str) -> Sequence[Any]:
    return _sequence(_required(mapping, key, name), name)


def _nonempty_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    return value


def _finite_decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        raise ValueError(f"{name} 必须是有限数值")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{name} 不能是 NaN 或 Infinity")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是有限数值") from exc
    if not result.is_finite():
        raise ValueError(f"{name} 不能是 NaN 或 Infinity")
    return result


def _nonnegative_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} 必须是非负整数")
    return value


def _positive_int(value: object, name: str) -> int:
    result = _nonnegative_int(value, name)
    if result == 0:
        raise ValueError(f"{name} 必须是正整数")
    return result


def _required_int(mapping: Mapping[str, Any], key: str, name: str) -> int:
    return _nonnegative_int(_required(mapping, key, name), name)


def _required_decimal(mapping: Mapping[str, Any], key: str, name: str) -> Decimal:
    return _finite_decimal(_required(mapping, key, name), name)


def _required_bool(mapping: Mapping[str, Any], key: str, name: str) -> bool:
    value = _required(mapping, key, name)
    if not isinstance(value, bool):
        raise ValueError(f"{name} 必须是 bool")
    return value


def _string_sequence(value: object, name: str, *, allow_empty: bool = False) -> list[str]:
    values = _sequence(value, name)
    if not values and not allow_empty:
        raise ValueError(f"{name} 不能为空")
    return [
        _nonempty_string(item, f"{name}[{index}]")
        for index, item in enumerate(values)
    ]


def _target_observation_provenance(
    factor: Mapping[str, Any], ticker: str
) -> tuple[list[str], list[str]]:
    evidence_ids: set[str] = set()
    calculation_ids: set[str] = set()
    observations = _required_sequence(
        factor, "observations_normalized", "factor.observations_normalized"
    )
    for index, item in enumerate(observations):
        observation = _mapping(item, f"factor.observations_normalized[{index}]")
        if observation.get("ticker") != ticker:
            continue
        for field, target in (
            ("evidence_ids", evidence_ids),
            ("calculation_ids", calculation_ids),
        ):
            values = observation.get(field)
            if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
                continue
            target.update(
                value.strip()
                for value in values
                if isinstance(value, str) and value.strip()
            )
    return sorted(evidence_ids), sorted(calculation_ids)


def _numeric_field_provenance(
    summary_name: str,
    field_names: Sequence[str],
    artifact_hash: str,
    *,
    evidence_ids: Sequence[str] = (),
    calculation_ids: Sequence[str] = (),
) -> dict[str, QuantFieldProvenance]:
    return {
        f"{summary_name}.{field_name}": QuantFieldProvenance(
            artifact_ids=[artifact_hash],
            evidence_ids=list(evidence_ids),
            calculation_ids=list(calculation_ids),
        )
        for field_name in field_names
    }


def _typed_result(
    value: object, name: str
) -> tuple[Decimal | None, str, str]:
    result = _mapping(value, name)
    status = _nonempty_string(_required(result, "status", f"{name}.status"), f"{name}.status")
    if status not in _STATUSES:
        raise ValueError(f"{name}.status 无效")
    reason_code = _nonempty_string(
        _required(result, "reason_code", f"{name}.reason_code"),
        f"{name}.reason_code",
    )
    raw_value = _required(result, "value", f"{name}.value")
    if status == "available":
        return (
            _finite_decimal(raw_value, f"{name}.value"),
            status,
            reason_code,
        )
    if raw_value is not None:
        raise ValueError(f"{name}.value 在 {status} 状态必须为 None")
    return None, status, reason_code


def _validate_backtest_provenance(value: object) -> Mapping[str, Any]:
    provenance = _mapping(value, "backtest.provenance")
    if not provenance:
        raise ValueError("backtest.provenance 不能为空")
    for key, item in provenance.items():
        _nonempty_string(key, "backtest.provenance.key")
        if item is None or (isinstance(item, str) and not item.strip()):
            raise ValueError("backtest.provenance 的 key/value 不能为空")
    return provenance


def _validate_ranking(value: object, index: int) -> dict[str, Any]:
    ranking = _mapping(value, f"factor.rankings[{index}]")
    ticker = _nonempty_string(
        _required(ranking, "ticker", f"factor.rankings[{index}].ticker"),
        f"factor.rankings[{index}].ticker",
    )
    peer_group = _nonempty_string(
        _required(ranking, "peer_group", f"factor.rankings[{index}].peer_group"),
        f"factor.rankings[{index}].peer_group",
    )
    status = _nonempty_string(
        _required(ranking, "status", f"factor.rankings[{index}].status"),
        f"factor.rankings[{index}].status",
    )
    if status not in _RANKING_STATUSES:
        raise ValueError(f"factor.rankings[{index}].status 无效")
    reason_code = _nonempty_string(
        _required(ranking, "reason_code", f"factor.rankings[{index}].reason_code"),
        f"factor.rankings[{index}].reason_code",
    )
    raw_score = _required(ranking, "score", f"factor.rankings[{index}].score")
    score = None if raw_score is None else _finite_decimal(
        raw_score, f"factor.rankings[{index}].score"
    )
    raw_rank = _required(ranking, "rank", f"factor.rankings[{index}].rank")
    rank = None if raw_rank is None else _positive_int(
        raw_rank, f"factor.rankings[{index}].rank"
    )
    available_factor_count = _nonnegative_int(
        _required(
            ranking,
            "available_factor_count",
            f"factor.rankings[{index}].available_factor_count",
        ),
        f"factor.rankings[{index}].available_factor_count",
    )
    return {
        "ticker": ticker,
        "peer_group": peer_group,
        "status": status,
        "reason_code": reason_code,
        "score": score,
        "rank": rank,
        "available_factor_count": available_factor_count,
    }


def _validate_factor(
    artifact: object, ticker: str
) -> tuple[Mapping[str, Any], dict[str, Any], list[str], dict[str, Decimal]]:
    factor = _validated_artifact(
        artifact,
        (
            "artifact_schema_version",
            "formula_version",
            "normalization_version",
            "composite_version",
            "row_counts",
            "snapshot_ids",
            "observations_raw",
            "observations_normalized",
            "rankings",
            "provenance",
        ),
        "factor",
    )
    schema_version = _nonempty_string(
        _required(factor, "artifact_schema_version", "factor.artifact_schema_version"),
        "factor.artifact_schema_version",
    )
    if schema_version != _FACTOR_ARTIFACT_SCHEMA_VERSION:
        raise ValueError("factor.artifact_schema_version 不匹配")
    for field in ("formula_version", "normalization_version", "composite_version"):
        _nonempty_string(_required(factor, field, f"factor.{field}"), f"factor.{field}")

    provenance = _required_mapping(factor, "provenance", "factor.provenance")
    evidence_ids = _string_sequence(
        _required(provenance, "evidence_ids", "factor.provenance.evidence_ids"),
        "factor.provenance.evidence_ids",
    )
    calculation_ids = _string_sequence(
        _required(provenance, "calculation_ids", "factor.provenance.calculation_ids"),
        "factor.provenance.calculation_ids",
    )

    row_counts = _required_mapping(factor, "row_counts", "factor.row_counts")
    count_fields = (
        ("snapshots", "snapshot_ids"),
        ("observations_raw", "observations_raw"),
        ("observations_normalized", "observations_normalized"),
        ("rankings", "rankings"),
    )
    counts: dict[str, Decimal] = {}
    for count_field, sequence_field in count_fields:
        count = _required_int(row_counts, count_field, f"factor.row_counts.{count_field}")
        values = _required_sequence(factor, sequence_field, f"factor.{sequence_field}")
        if count != len(values):
            raise ValueError(f"factor.row_counts.{count_field} 与 {sequence_field} 长度不一致")
        counts[count_field] = Decimal(count)

    rankings = _required_sequence(factor, "rankings", "factor.rankings")
    validated_rankings = [_validate_ranking(item, index) for index, item in enumerate(rankings)]
    matches = [item for item in validated_rankings if item["ticker"] == ticker]
    if len(matches) != 1:
        raise ValueError("ticker 必须在 factor.rankings 中且仅出现一次")
    target = matches[0]
    peer_count = sum(
        item["peer_group"] == target["peer_group"] for item in validated_rankings
    )
    if target["status"] == "available" and (
        target["score"] is None or target["rank"] is None
    ):
        raise ValueError("available target ranking 必须有 score 和 rank")
    if target["rank"] is not None and target["rank"] > peer_count:
        raise ValueError("target rank 超出 peer_count")
    percentile = None
    if target["status"] == "available" and target["rank"] is not None:
        percentile = (
            Decimal("1")
            if peer_count == 1
            else Decimal(peer_count - target["rank"]) / Decimal(peer_count - 1)
        )

    target_summary = {
        "target_ticker": ticker,
        "peer_group": target["peer_group"],
        "score": target["score"],
        "rank": None if target["rank"] is None else Decimal(target["rank"]),
        "peer_count": Decimal(peer_count),
        "industry_percentile": percentile,
        "target_available_factor_count": Decimal(target["available_factor_count"]),
        "target_rank_status": target["status"],
        "target_rank_reason_code": target["reason_code"],
    }
    return factor, target_summary, evidence_ids + calculation_ids, counts


def _validate_backtest(
    artifact: object,
) -> tuple[Mapping[str, Any], dict[str, Any], list[str], bool]:
    backtest = _validated_artifact(
        artifact,
        (
            "universe_id",
            "strategy_version",
            "artifact_schema_version",
            "backtest_version",
            "data_quality",
            "known_biases",
            "baseline_summary",
            "provenance",
        ),
        "backtest",
    )
    schema_version = _nonempty_string(
        _required(backtest, "artifact_schema_version", "backtest.artifact_schema_version"),
        "backtest.artifact_schema_version",
    )
    if schema_version != _BACKTEST_ARTIFACT_SCHEMA_VERSION:
        raise ValueError("backtest.artifact_schema_version 不匹配")
    universe_id = _nonempty_string(
        _required(backtest, "universe_id", "backtest.universe_id"),
        "backtest.universe_id",
    )
    strategy_version = _nonempty_string(
        _required(backtest, "strategy_version", "backtest.strategy_version"),
        "backtest.strategy_version",
    )
    backtest_version = _nonempty_string(
        _required(backtest, "backtest_version", "backtest.backtest_version"),
        "backtest.backtest_version",
    )
    _validate_backtest_provenance(
        _required(backtest, "provenance", "backtest.provenance")
    )

    data_quality = _required_mapping(backtest, "data_quality", "backtest.data_quality")
    complete_period_count = _required_int(
        data_quality,
        "complete_period_count",
        "backtest.data_quality.complete_period_count",
    )
    period_count = _required_int(
        data_quality,
        "period_count",
        "backtest.data_quality.period_count",
    )
    if complete_period_count > period_count:
        raise ValueError("complete_period_count 不能大于 period_count")
    survivorship_bias_known = _required_bool(
        data_quality,
        "survivorship_bias_known",
        "backtest.data_quality.survivorship_bias_known",
    )
    known_biases = _string_sequence(
        _required(backtest, "known_biases", "backtest.known_biases"),
        "backtest.known_biases",
        allow_empty=True,
    )

    baseline = _required_mapping(backtest, "baseline_summary", "backtest.baseline_summary")
    baseline_complete_period_count = _required_int(
        baseline,
        "complete_period_count",
        "backtest.baseline_summary.complete_period_count",
    )
    net_cost_bps = _required_decimal(
        baseline,
        "net_cost_bps",
        "backtest.baseline_summary.net_cost_bps",
    )
    baseline_status = _nonempty_string(
        _required(baseline, "status", "backtest.baseline_summary.status"),
        "backtest.baseline_summary.status",
    )
    if baseline_status not in _STATUSES:
        raise ValueError("backtest.baseline_summary.status 无效")
    _nonempty_string(
        _required(baseline, "reason_code", "backtest.baseline_summary.reason_code"),
        "backtest.baseline_summary.reason_code",
    )

    strategy = _required_mapping(baseline, "strategy", "backtest.baseline_summary.strategy")
    strategy_cagr = _typed_result(
        _required(strategy, "cagr", "backtest.baseline_summary.strategy.cagr"),
        "backtest.baseline_summary.strategy.cagr",
    )
    strategy_max_drawdown = _typed_result(
        _required(
            strategy,
            "max_drawdown",
            "backtest.baseline_summary.strategy.max_drawdown",
        ),
        "backtest.baseline_summary.strategy.max_drawdown",
    )
    average_turnover = _typed_result(
        _required(baseline, "average_turnover", "backtest.baseline_summary.average_turnover"),
        "backtest.baseline_summary.average_turnover",
    )
    annualized_turnover = _typed_result(
        _required(
            baseline,
            "annualized_turnover",
            "backtest.baseline_summary.annualized_turnover",
        ),
        "backtest.baseline_summary.annualized_turnover",
    )

    benchmarks = _required_mapping(
        baseline,
        "benchmarks",
        "backtest.baseline_summary.benchmarks",
    )
    spy = _required_mapping(
        benchmarks,
        "SPY_total_return",
        "backtest.baseline_summary.benchmarks.SPY_total_return",
    )
    universe = _required_mapping(
        benchmarks,
        "Universe_equal_weight",
        "backtest.baseline_summary.benchmarks.Universe_equal_weight",
    )
    spy_cagr = _typed_result(
        _required(spy, "cagr", "backtest.baseline_summary.benchmarks.SPY_total_return.cagr"),
        "backtest.baseline_summary.benchmarks.SPY_total_return.cagr",
    )
    spy_max_drawdown = _typed_result(
        _required(
            spy,
            "max_drawdown",
            "backtest.baseline_summary.benchmarks.SPY_total_return.max_drawdown",
        ),
        "backtest.baseline_summary.benchmarks.SPY_total_return.max_drawdown",
    )
    universe_cagr = _typed_result(
        _required(
            universe,
            "cagr",
            "backtest.baseline_summary.benchmarks.Universe_equal_weight.cagr",
        ),
        "backtest.baseline_summary.benchmarks.Universe_equal_weight.cagr",
    )
    universe_max_drawdown = _typed_result(
        _required(
            universe,
            "max_drawdown",
            "backtest.baseline_summary.benchmarks.Universe_equal_weight.max_drawdown",
        ),
        "backtest.baseline_summary.benchmarks.Universe_equal_weight.max_drawdown",
    )

    metrics = {
        "strategy_cagr": strategy_cagr,
        "strategy_max_drawdown": strategy_max_drawdown,
        "average_turnover": average_turnover,
        "annualized_turnover": annualized_turnover,
    }
    summary = {
        "artifact_schema_version": schema_version,
        "backtest_version": backtest_version,
        "complete_period_count": Decimal(baseline_complete_period_count),
        "net_cost_bps": net_cost_bps,
    }
    for prefix, (value, status, reason_code) in metrics.items():
        summary[prefix] = value
        summary[f"{prefix}_status"] = status
        summary[f"{prefix}_reason_code"] = reason_code

    benchmark_summary = {
        "spy_cagr": spy_cagr[0],
        "spy_max_drawdown": spy_max_drawdown[0],
        "universe_cagr": universe_cagr[0],
        "universe_max_drawdown": universe_max_drawdown[0],
    }
    baseline_available = baseline_status == "available" and all(
        result[1] == "available"
        for result in (
            strategy_cagr,
            strategy_max_drawdown,
            average_turnover,
            annualized_turnover,
            spy_cagr,
            spy_max_drawdown,
            universe_cagr,
            universe_max_drawdown,
        )
    )
    return (
        backtest,
        {
            "universe_id": universe_id,
            "strategy_version": strategy_version,
            "summary": summary,
            "benchmark_summary": benchmark_summary,
            "complete_period_count": complete_period_count,
            "period_count": period_count,
            "survivorship_bias_known": survivorship_bias_known,
            "baseline_available": baseline_available,
        },
        known_biases,
        baseline_available,
    )


def build_quant_research_packet(
    factor_artifact: Mapping[str, object],
    backtest_artifact: Mapping[str, object],
    *,
    as_of: datetime,
    ticker: str,
    universe_id: str | None = None,
    strategy_version: str | None = None,
) -> QuantResearchPacket:
    """从两个完整、已验 hash 的 artifact 生成确定性 packet。"""

    target_ticker = _nonempty_string(ticker, "ticker")
    factor, target_summary, _, factor_counts = _validate_factor(
        factor_artifact, target_ticker
    )
    target_evidence_ids, target_calculation_ids = _target_observation_provenance(
        factor, target_ticker
    )
    backtest, backtest_details, known_biases, baseline_available = _validate_backtest(
        backtest_artifact
    )

    source_universe_id = backtest_details["universe_id"]
    source_strategy_version = backtest_details["strategy_version"]
    packet_universe_id = source_universe_id
    if universe_id is not None:
        packet_universe_id = _nonempty_string(universe_id, "universe_id")
        if packet_universe_id != source_universe_id:
            raise ValueError("universe_id override 必须与 backtest 来源一致")
    packet_strategy_version = source_strategy_version
    if strategy_version is not None:
        packet_strategy_version = _nonempty_string(strategy_version, "strategy_version")
        if packet_strategy_version != source_strategy_version:
            raise ValueError("strategy_version override 必须与 backtest 来源一致")

    rankings = _required_sequence(factor, "rankings", "factor.rankings")
    ranking_partial = any(
        _mapping(item, f"factor.rankings[{index}]")["status"] != "available"
        for index, item in enumerate(rankings)
    )
    coverage = (
        CoverageLevel.PARTIAL
        if ranking_partial
        or known_biases
        or backtest_details["survivorship_bias_known"]
        or not baseline_available
        else CoverageLevel.FULL
    )

    factor_summary = {
        "artifact_schema_version": _nonempty_string(
            factor["artifact_schema_version"], "factor.artifact_schema_version"
        ),
        "formula_version": _nonempty_string(
            factor["formula_version"], "factor.formula_version"
        ),
        "normalization_version": _nonempty_string(
            factor["normalization_version"], "factor.normalization_version"
        ),
        "snapshot_count": factor_counts["snapshots"],
        "observation_count": factor_counts["observations_normalized"],
    }
    ranking_summary = {
        "composite_version": _nonempty_string(
            factor["composite_version"], "factor.composite_version"
        ),
        "ranking_count": factor_counts["rankings"],
        **target_summary,
    }
    data_quality = {
        "factor_snapshot_count": factor_counts["snapshots"],
        "factor_observation_count": factor_counts["observations_normalized"],
        "complete_period_count": Decimal(backtest_details["complete_period_count"]),
        "period_count": Decimal(backtest_details["period_count"]),
        "survivorship_bias_known": backtest_details["survivorship_bias_known"],
    }
    factor_hash = _nonempty_string(factor["artifact_hash"], "factor.artifact_hash")
    backtest_hash = _nonempty_string(backtest["artifact_hash"], "backtest.artifact_hash")
    field_provenance = {
        **_numeric_field_provenance(
            "factor_summary",
            ("snapshot_count", "observation_count"),
            factor_hash,
        ),
        **_numeric_field_provenance("ranking_summary", ("ranking_count",), factor_hash),
        **_numeric_field_provenance(
            "ranking_summary",
            (
                "score",
                "rank",
                "peer_count",
                "industry_percentile",
                "target_available_factor_count",
            ),
            factor_hash,
            evidence_ids=target_evidence_ids,
            calculation_ids=target_calculation_ids,
        ),
        **_numeric_field_provenance(
            "backtest_summary",
            (
                "complete_period_count",
                "net_cost_bps",
                "strategy_cagr",
                "strategy_max_drawdown",
                "average_turnover",
                "annualized_turnover",
            ),
            backtest_hash,
        ),
        **_numeric_field_provenance(
            "benchmark_summary",
            ("spy_cagr", "spy_max_drawdown", "universe_cagr", "universe_max_drawdown"),
            backtest_hash,
        ),
        **_numeric_field_provenance(
            "data_quality",
            ("factor_snapshot_count", "factor_observation_count"),
            factor_hash,
        ),
        **_numeric_field_provenance(
            "data_quality",
            ("complete_period_count", "period_count"),
            backtest_hash,
        ),
    }

    return QuantResearchPacket(
        as_of=as_of,
        universe_id=packet_universe_id,
        strategy_version=packet_strategy_version,
        coverage=coverage,
        factor_summary=factor_summary,
        ranking_summary=ranking_summary,
        backtest_summary=backtest_details["summary"],
        benchmark_summary=backtest_details["benchmark_summary"],
        data_quality=data_quality,
        field_provenance=field_provenance,
        limitations=sorted(known_biases),
        artifact_ids=sorted(
            (
                factor["artifact_hash"],
                backtest["artifact_hash"],
            )
        ),
    )


def quant_packet_hash(packet: QuantResearchPacket) -> str:
    """按 packet 的 JSON 表示计算稳定的小写 sha256。"""

    if not isinstance(packet, QuantResearchPacket):
        raise ValueError("packet 必须是 QuantResearchPacket")
    canonical = json.dumps(
        packet.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


__all__ = ["build_quant_research_packet", "quant_packet_hash"]
