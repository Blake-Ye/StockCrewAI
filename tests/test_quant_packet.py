from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import pytest

from stockcrewai.models.profile import CoverageLevel
from stockcrewai.models.quant import PointInTimeSnapshot, QuantResearchPacket


AS_OF = datetime(2026, 8, 10, tzinfo=timezone.utc)
INTEGRATION_FIXTURE = Path(__file__).parent / "fixtures" / "quant" / "integration" / "snapshots.json"
BACKTEST_FIXTURE = Path(__file__).parent / "fixtures" / "quant" / "backtest" / "backtest.json"


def _load_fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _factor_artifact() -> dict[str, Any]:
    try:
        from stockcrewai.quant.pipeline import build_quant_factor_artifact
    except (ImportError, ModuleNotFoundError) as exc:
        pytest.fail(f"quant pipeline fixture API is unavailable: {exc}", pytrace=False)

    snapshots = tuple(
        PointInTimeSnapshot.model_validate(item)
        for item in _load_fixture(INTEGRATION_FIXTURE)["snapshots"]
    )
    return build_quant_factor_artifact(
        snapshots,
        formula_version="factor-formulas-v1",
        winsor_lower=Decimal("0.10"),
        winsor_upper=Decimal("0.90"),
        normalization_version="cross-section-normalization-v1",
        composite_version="equal-weight-composite-v1",
    )


def _month_start(value: date, offset: int) -> date:
    month = value.month - 1 + offset
    return date(value.year + month // 12, month % 12 + 1, 1)


def _backtest_artifact() -> dict[str, Any]:
    try:
        from stockcrewai.models.quant import UniverseManifest
        from stockcrewai.quant.backtest import (
            RebalanceSpec,
            SnapshotScore,
            TotalReturnLevel,
            WalkForwardInput,
            run_walk_forward,
        )
    except (ImportError, ModuleNotFoundError) as exc:
        pytest.fail(f"backtest fixture API is unavailable: {exc}", pytrace=False)

    fixture = _load_fixture(BACKTEST_FIXTURE)
    universe = UniverseManifest.model_validate(fixture["universe"])
    config = fixture["periods"]
    first_trade = date.fromisoformat(config["start_trade_date"])
    period_dates = [
        (
            _month_start(first_trade, index).replace(day=1),
            _month_start(first_trade, index).replace(day=3),
            (
                _month_start(first_trade, index + 1).replace(day=3)
                if index + 1 < config["count"]
                else None
            ),
        )
        for index in range(config["count"])
    ]
    periods = tuple(
        RebalanceSpec(
            rebalance_anchor=anchor,
            trade_date=trade,
            next_trade_date=next_trade,
            period_id=f"P{index:03d}",
        )
        for index, (anchor, trade, next_trade) in enumerate(period_dates)
    )

    scores: list[Any] = []
    for snapshot in fixture["snapshots"]:
        high_tickers = set(snapshot["high_tickers"])
        for ticker in universe.tickers:
            scores.append(
                SnapshotScore(
                    snapshot_id=f"{snapshot['snapshot_id_prefix']}-{ticker}",
                    ticker=ticker,
                    as_of=datetime.fromisoformat(snapshot["as_of"]),
                    filing_cutoff=datetime.fromisoformat(snapshot["filing_cutoff"]),
                    price_cutoff=datetime.fromisoformat(snapshot["price_cutoff"]),
                    score=Decimal(
                        snapshot["high_score"] if ticker in high_tickers else snapshot["low_score"]
                    ),
                    score_version=snapshot["score_version"],
                )
            )

    returns = fixture["levels"]["monthly_returns"]
    levels: list[Any] = []
    level_by_ticker = {
        ticker: Decimal(fixture["levels"]["initial"])
        for ticker in ("SPY", *universe.tickers)
    }
    for _, trade, _ in period_dates:
        for ticker in level_by_ticker:
            levels.append(
                TotalReturnLevel(ticker=ticker, trade_date=trade, level=level_by_ticker[ticker])
            )
        for ticker in level_by_ticker:
            level_by_ticker[ticker] *= Decimal("1") + Decimal(
                returns.get(ticker, returns["default"])
            )

    result = run_walk_forward(
        WalkForwardInput(universe, tuple(scores), periods, tuple(levels))
    )
    return result.to_artifact_dict()


@pytest.fixture(scope="module")
def valid_artifacts() -> tuple[dict[str, Any], dict[str, Any]]:
    """Build both inputs from the existing offline WP07/WP08 fixtures."""

    return _factor_artifact(), _backtest_artifact()


def _packet_api() -> tuple[Any, Any]:
    try:
        from stockcrewai.quant.packet import (
            build_quant_research_packet,
            quant_packet_hash,
        )
    except (ImportError, ModuleNotFoundError) as exc:
        pytest.fail(f"WP09-S02 packet API is not implemented: {exc}", pytrace=False)
    return build_quant_research_packet, quant_packet_hash


def _build_packet(
    factor_artifact: Mapping[str, object] | None,
    backtest_artifact: Mapping[str, object] | None,
    **overrides: object,
) -> QuantResearchPacket:
    build_quant_research_packet, _ = _packet_api()
    return build_quant_research_packet(
        factor_artifact,
        backtest_artifact,
        as_of=AS_OF,
        **overrides,
    )


def _rehash(artifact: dict[str, Any]) -> None:
    body = {key: value for key, value in artifact.items() if key != "artifact_hash"}
    artifact["artifact_hash"] = hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _assert_scalar_mapping(value: Mapping[str, object]) -> None:
    assert value
    assert all(isinstance(key, str) and key.strip() for key in value)
    assert all(
        item is None or isinstance(item, (str, bool, Decimal))
        for item in value.values()
    )


def _nested_keys(value: object) -> set[str]:
    if isinstance(value, Mapping):
        return {
            str(key)
            for key in value
        } | {
            nested
            for child in value.values()
            for nested in _nested_keys(child)
        }
    if isinstance(value, (list, tuple)):
        return {nested for child in value for nested in _nested_keys(child)}
    return set()


def test_complete_offline_artifacts_build_an_auditable_partial_packet(
    valid_artifacts: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    factor, backtest = valid_artifacts
    packet = _build_packet(factor, backtest)
    baseline = backtest["baseline_summary"]
    strategy = baseline["strategy"]
    benchmarks = baseline["benchmarks"]

    assert isinstance(packet, QuantResearchPacket)
    assert packet.as_of == AS_OF
    assert packet.universe_id == backtest["universe_id"]
    assert packet.strategy_version == backtest["strategy_version"]
    # The fixture has an unavailable ranking and a disclosed survivorship bias.
    assert packet.coverage is CoverageLevel.PARTIAL
    assert packet.limitations == sorted(backtest["known_biases"])
    assert packet.artifact_ids == sorted([factor["artifact_hash"], backtest["artifact_hash"]])

    assert packet.factor_summary == {
        "artifact_schema_version": factor["artifact_schema_version"],
        "formula_version": factor["formula_version"],
        "normalization_version": factor["normalization_version"],
        "snapshot_count": _decimal(factor["row_counts"]["snapshots"]),
        "observation_count": _decimal(factor["row_counts"]["observations_normalized"]),
    }
    assert packet.ranking_summary == {
        "composite_version": factor["composite_version"],
        "ranking_count": _decimal(factor["row_counts"]["rankings"]),
    }
    assert packet.backtest_summary == {
        "artifact_schema_version": backtest["artifact_schema_version"],
        "backtest_version": backtest["backtest_version"],
        "complete_period_count": _decimal(baseline["complete_period_count"]),
        "net_cost_bps": _decimal(baseline["net_cost_bps"]),
        "strategy_cagr": _decimal(strategy["cagr"]["value"]),
        "strategy_max_drawdown": _decimal(strategy["max_drawdown"]["value"]),
    }
    assert packet.benchmark_summary == {
        "spy_cagr": _decimal(benchmarks["SPY_total_return"]["cagr"]["value"]),
        "spy_max_drawdown": _decimal(benchmarks["SPY_total_return"]["max_drawdown"]["value"]),
        "universe_cagr": _decimal(benchmarks["Universe_equal_weight"]["cagr"]["value"]),
        "universe_max_drawdown": _decimal(
            benchmarks["Universe_equal_weight"]["max_drawdown"]["value"]
        ),
    }
    assert packet.data_quality == {
        "factor_snapshot_count": _decimal(factor["row_counts"]["snapshots"]),
        "factor_observation_count": _decimal(
            factor["row_counts"]["observations_normalized"]
        ),
        "complete_period_count": _decimal(backtest["data_quality"]["complete_period_count"]),
        "period_count": _decimal(backtest["data_quality"]["period_count"]),
        "survivorship_bias_known": backtest["data_quality"]["survivorship_bias_known"],
    }

    for summary in (
        packet.factor_summary,
        packet.ranking_summary,
        packet.backtest_summary,
        packet.benchmark_summary,
        packet.data_quality,
    ):
        _assert_scalar_mapping(summary)
    json.dumps(packet.model_dump(mode="json"), allow_nan=False)


def test_explicit_packet_identity_overrides_are_preserved(
    valid_artifacts: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    packet = _build_packet(
        *valid_artifacts,
        universe_id="offline-universe-v2",
        strategy_version="strategy-v2",
    )

    assert (packet.universe_id, packet.strategy_version) == (
        "offline-universe-v2",
        "strategy-v2",
    )


@pytest.mark.parametrize("missing", ["factor", "backtest"])
def test_missing_artifact_raises_value_error_until_typed_unavailable_is_modelled(
    valid_artifacts: tuple[dict[str, Any], dict[str, Any]], missing: str
) -> None:
    factor, backtest = valid_artifacts
    factor_input = None if missing == "factor" else factor
    backtest_input = None if missing == "backtest" else backtest

    with pytest.raises(ValueError):
        _build_packet(factor_input, backtest_input)


@pytest.mark.parametrize("artifact_name", ["factor", "backtest"])
@pytest.mark.parametrize("bad_value", [None, "", "  "])
def test_missing_or_blank_artifact_hash_is_rejected(
    valid_artifacts: tuple[dict[str, Any], dict[str, Any]],
    artifact_name: str,
    bad_value: object,
) -> None:
    factor, backtest = deepcopy(valid_artifacts)
    artifact = factor if artifact_name == "factor" else backtest
    if bad_value is None:
        artifact.pop("artifact_hash")
    else:
        artifact["artifact_hash"] = bad_value

    with pytest.raises(ValueError):
        _build_packet(factor, backtest)


@pytest.mark.parametrize("artifact_name", ["factor", "backtest"])
def test_tampered_artifact_hash_is_rejected(
    valid_artifacts: tuple[dict[str, Any], dict[str, Any]], artifact_name: str
) -> None:
    factor, backtest = deepcopy(valid_artifacts)
    artifact = factor if artifact_name == "factor" else backtest
    artifact["artifact_hash"] = "0" * 64

    with pytest.raises(ValueError):
        _build_packet(factor, backtest)


@pytest.mark.parametrize(
    ("artifact_name", "missing_field"),
    [
        ("factor", "provenance"),
        ("factor", "rankings"),
        ("backtest", "provenance"),
        ("backtest", "baseline_summary"),
    ],
)
def test_artifact_with_missing_required_field_is_rejected(
    valid_artifacts: tuple[dict[str, Any], dict[str, Any]],
    artifact_name: str,
    missing_field: str,
) -> None:
    factor, backtest = deepcopy(valid_artifacts)
    artifact = factor if artifact_name == "factor" else backtest
    artifact.pop(missing_field)
    _rehash(artifact)

    with pytest.raises(ValueError):
        _build_packet(factor, backtest)


@pytest.mark.parametrize("artifact_name", ["factor", "backtest"])
def test_nan_numeric_artifact_value_is_rejected(
    valid_artifacts: tuple[dict[str, Any], dict[str, Any]], artifact_name: str
) -> None:
    factor, backtest = deepcopy(valid_artifacts)
    if artifact_name == "factor":
        factor["row_counts"]["snapshots"] = "NaN"
        _rehash(factor)
    else:
        backtest["data_quality"]["period_count"] = "NaN"
        _rehash(backtest)

    with pytest.raises(ValueError):
        _build_packet(factor, backtest)


def test_packet_does_not_copy_verdict_decision_fields_or_mutate_inputs(
    valid_artifacts: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    factor, backtest = valid_artifacts
    before = deepcopy(valid_artifacts)
    packet = _build_packet(factor, backtest)
    packet_keys = _nested_keys(packet.model_dump(mode="json"))

    assert not packet_keys & {"verdict", "status", "rating", "recommendation"}
    assert (factor, backtest) == before


def test_packet_hash_is_stable_content_addressed_sha256(
    valid_artifacts: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    packet = _build_packet(*valid_artifacts)
    _, quant_packet_hash = _packet_api()

    first = quant_packet_hash(packet)
    second = quant_packet_hash(packet)
    assert first == second
    assert re.fullmatch(r"[0-9a-f]{64}", first)

    canonical = json.dumps(
        packet.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert first == hashlib.sha256(canonical).hexdigest()

    changed = packet.model_copy(update={"strategy_version": "different-strategy-v1"})
    assert quant_packet_hash(changed) != first
