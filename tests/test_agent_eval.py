from __future__ import annotations

import json
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

import pytest


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "agent_eval"
# 负向 fixture 用于验证结构化门禁诊断，不应被默认正向发布门禁静默吞掉。


def _api():
    from stockcrewai.evals import agent_eval

    return agent_eval


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _metric_fields(score: dict[str, Any]) -> None:
    assert set(score) >= {"numerator", "denominator", "rate"}
    assert isinstance(score["numerator"], int)
    assert isinstance(score["denominator"], int)
    assert score["denominator"] >= score["numerator"] >= 0
    assert 0 <= score["rate"] <= 1


def test_main_success_and_writes_json(tmp_path: Path) -> None:
    output = tmp_path / "agent-eval.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "stockcrewai.evals.agent_eval",
            "--fixtures",
            str(FIXTURE_DIR),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["artifact_hash"]


def test_default_fixtures_have_metadata_and_expected_actual() -> None:
    api = _api()
    fixtures = api.load_fixtures(FIXTURE_DIR)
    assert len(fixtures) >= 8
    assert [item["_fixture_file"] for item in fixtures] == sorted(
        item["_fixture_file"] for item in fixtures
    )
    assert {item["agent_id"] for item in fixtures} == set(api.AGENT_IDS)
    for item in fixtures:
        assert {
            "agent_id",
            "task_id",
            "prompt_version",
            "schema_version",
        } <= item.keys()
        assert isinstance(item["input"], dict)
        assert isinstance(item["expected"], dict)
        assert isinstance(item["actual"], dict)


def test_unknown_agent_and_missing_metadata_are_rejected(tmp_path: Path) -> None:
    api = _api()
    unknown = _fixture("01_request_parser_ok.json")
    unknown["agent_id"] = "UnknownAgent"
    (tmp_path / "unknown.json").write_text(json.dumps(unknown), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown agent"):
        api.load_fixtures(tmp_path)

    missing = _fixture("01_request_parser_ok.json")
    del missing["schema_version"]
    (tmp_path / "missing.json").write_text(json.dumps(missing), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        api.load_fixtures(tmp_path)


def test_scorecards_have_stable_agent_order_and_explainable_scores() -> None:
    api = _api()
    report = api.evaluate_fixtures(FIXTURE_DIR)
    assert list(report["agents"]) == list(api.AGENT_IDS)
    assert report["schema_pass_rate"]["denominator"] > 0
    for scorecard in report["agents"].values():
        assert "schema_pass_rate" in scorecard["metrics"]
        for score in scorecard["metrics"].values():
            _metric_fields(score)
    for fixture_report in report["fixture_reports"]:
        for score in fixture_report["metrics"].values():
            _metric_fields(score)


def test_repeated_evaluation_is_json_and_hash_deterministic() -> None:
    api = _api()
    first = api.evaluate_fixtures(FIXTURE_DIR)
    second = api.evaluate_fixtures(FIXTURE_DIR)
    assert first["fixture_reports"] == second["fixture_reports"]
    assert first["agents"] == second["agents"]
    assert api.serialize_report(first) == api.serialize_report(second)
    assert api.artifact_hash(first) == api.artifact_hash(second)


def test_claim_acceptance_checks_allowed_ids_and_rejected_claims() -> None:
    api = _api()
    unknown = api.evaluate_fixture(_fixture("90_financial_unknown_evidence.json"))
    missing = api.evaluate_fixture(_fixture("91_financial_missing_evidence.json"))
    assert unknown["metrics"]["claim_acceptance"]["numerator"] == 0
    assert unknown["metrics"]["evidence_coverage"]["numerator"] < unknown["metrics"]["evidence_coverage"]["denominator"]
    assert "unknown_evidence_id" in unknown["failures"]
    assert missing["metrics"]["claim_acceptance"]["numerator"] == 0
    assert "missing_evidence_id" in missing["failures"]

    report = api.evaluate_fixture(_fixture("94_report_policy_violations.json"))
    assert report["metrics"]["rejected_claim_in_report"]["numerator"] > 0
    assert "rejected_claim_in_report" in report["failures"]


def test_bad_fixtures_hit_numeric_risk_report_and_injection_metrics() -> None:
    api = _api()
    numeric = api.evaluate_fixture(_fixture("92_financial_numeric_mismatch.json"))
    risk = api.evaluate_fixture(_fixture("93_risk_event_state_bad.json"))
    report = api.evaluate_fixture(_fixture("94_report_policy_violations.json"))
    assert numeric["metrics"]["numeric_mismatch"]["numerator"] > 0
    assert "numeric_mismatch" in numeric["failures"]
    assert risk["metrics"]["risk_source_coverage"]["rate"] < 1
    assert risk["metrics"]["risk_event_state_accuracy"]["rate"] < 1
    assert {"risk_source_coverage", "risk_event_state_mismatch"} <= set(risk["failures"])
    assert report["metrics"]["investment_advice_hits"]["numerator"] > 0
    assert report["metrics"]["new_claim_in_report"]["numerator"] > 0
    assert report["metrics"]["injection_bypass"]["numerator"] > 0
    assert {
        "investment_advice",
        "new_claim_in_report",
        "injection_bypass",
    } <= set(report["failures"])


def test_no_denominator_is_not_reported_as_perfect() -> None:
    api = _api()
    empty = _fixture("01_request_parser_ok.json")
    empty["actual"]["numeric_values"] = {}
    empty["expected"]["numeric_values"] = {}
    result = api.evaluate_fixture(empty)
    score = result["metrics"]["numeric_consistency"]
    assert score["denominator"] == 0
    assert score["rate"] != 1


def test_evaluation_does_not_need_network_or_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("offline evaluator attempted external access")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    report = _api().evaluate_fixtures(FIXTURE_DIR)
    assert report["passed"] is True


def test_threshold_failure_returns_nonzero_without_silent_downgrade(tmp_path: Path) -> None:
    bad = _fixture("94_report_policy_violations.json")
    bad.pop("fixture_kind", None)
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    (bad_dir / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
    result = _api().main(["--fixtures", str(bad_dir)])
    assert result != 0


def test_cli_output_contains_no_prompt_secret_or_environment_payload(tmp_path: Path) -> None:
    api = _api()
    fixture = _fixture("01_request_parser_ok.json")
    fixture["input"]["prompt"] = "do not emit sk-test-secret"
    fixture["input"]["OPENAI_API_KEY"] = "sk-test-secret"
    directory = tmp_path / "secret"
    directory.mkdir()
    (directory / "fixture.json").write_text(json.dumps(fixture), encoding="utf-8")
    output = tmp_path / "report.json"
    assert api.main(["--fixtures", str(directory), "--output", str(output)]) == 0
    serialized = output.read_text(encoding="utf-8")
    assert "do not emit" not in serialized
    assert "sk-test-secret" not in serialized
    assert "OPENAI_API_KEY" not in serialized


def test_fixture_order_uses_filename_before_agent_id(tmp_path: Path) -> None:
    api = _api()
    first = _fixture("01_request_parser_ok.json")
    second = _fixture("04_report_writer_ok.json")
    first["agent_id"], second["agent_id"] = second["agent_id"], first["agent_id"]
    (tmp_path / "a.json").write_text(json.dumps(first), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(second), encoding="utf-8")
    loaded = api.load_fixtures(tmp_path)
    assert [item["_fixture_file"] for item in loaded] == ["a.json", "b.json"]


def test_default_report_includes_negative_fixture_diagnostics_but_passes_positive_gate() -> None:
    report = _api().evaluate_fixtures(FIXTURE_DIR)
    negative = [item for item in report["fixture_reports"] if item["fixture_kind"] == "negative"]
    assert len(negative) >= 4
    assert report["negative_fixture_count"] == len(negative)
    assert report["passed"] is True
