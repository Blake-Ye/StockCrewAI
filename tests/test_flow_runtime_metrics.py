from __future__ import annotations

import json
import re

from stockcrewai.flow import ResearchFlow
from stockcrewai.run_output import RunStageEvent


def _stage_event(*, status: str = "completed", step: int = 1) -> RunStageEvent:
    return RunStageEvent(
        step=step,
        title="请求解析",
        actor="Crew/Agent：Request Parser Crew",
        status=status,
        input_summary="prompt=do-not-serialize input=provided",
        output_summary="api_key=do-not-serialize output=completed",
        decision="BLOCKED" if status == "blocked" else "READY",
        reason="Authorization=do-not-serialize",
        next_step="结束",
    )


def test_runtime_metrics_disabled_preserves_result_and_writes_no_artifact(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("STOCKCREWAI_RUNTIME_METRICS", raising=False)
    artifact = tmp_path / "runtime-metrics.json"
    monkeypatch.setenv("STOCKCREWAI_RUNTIME_METRICS_OUTPUT", str(artifact))

    flow = ResearchFlow(request="offline request")
    baseline = flow._flow_result()
    flow._emit_stage(_stage_event())

    assert flow._flow_result() == baseline
    assert not artifact.exists()


def test_runtime_metrics_enabled_writes_associated_stage_artifact(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("STOCKCREWAI_RUNTIME_METRICS", "1")
    artifact = tmp_path / "nested" / "runtime-metrics.json"
    monkeypatch.setenv("STOCKCREWAI_RUNTIME_METRICS_OUTPUT", str(artifact))

    flow = ResearchFlow(request="offline request")
    result_before = flow.state.model_dump(mode="json")
    flow._emit_stage(_stage_event())
    result_after = flow._flow_result()

    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["run_id"] == str(flow.state.id)
    assert payload["status"] == "completed"
    assert payload["details"]
    detail = payload["details"][0]
    assert detail["crew"]
    assert detail["agent"]
    assert detail["task"]
    assert payload["latency"]["valid"] is True
    assert isinstance(payload["latency"]["seconds"], float)
    assert re.fullmatch(r"[0-9a-f]{64}", payload["stable_hash"])
    assert result_after == result_before
    assert "runtime_metrics" not in result_after


def test_runtime_metrics_artifact_excludes_stage_summaries_and_secrets(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("STOCKCREWAI_RUNTIME_METRICS", "1")
    artifact = tmp_path / "runtime-metrics.json"
    monkeypatch.setenv("STOCKCREWAI_RUNTIME_METRICS_OUTPUT", str(artifact))

    flow = ResearchFlow(request="offline request")
    flow._emit_stage(_stage_event())
    result = flow._flow_result()

    serialized = artifact.read_text(encoding="utf-8")
    for forbidden in (
        "prompt",
        "api_key",
        "Authorization",
        "cookie",
        "do-not-serialize",
        "input_summary",
        "output_summary",
    ):
        assert forbidden not in serialized
    assert "runtime_metrics" not in result


def test_runtime_metrics_blocked_and_error_finalize_once_without_fake_numbers(
    monkeypatch, tmp_path
) -> None:
    for status, category in (("blocked", "gate"), ("error", "runtime")):
        artifact = tmp_path / f"{status}" / "runtime-metrics.json"
        monkeypatch.setenv("STOCKCREWAI_RUNTIME_METRICS", "1")
        monkeypatch.setenv("STOCKCREWAI_RUNTIME_METRICS_OUTPUT", str(artifact))

        flow = ResearchFlow(request="offline request")
        flow._emit_stage(_stage_event(status=status, step=7))
        flow._flow_result()
        first_payload = artifact.read_text(encoding="utf-8")
        flow._flow_result()

        assert artifact.read_text(encoding="utf-8") == first_payload
        payload = json.loads(first_payload)
        assert payload["status"] == "failed"
        assert payload["failure_category"] == category
        assert payload["details"]
        assert payload["tokens"]["input"] is None
        assert payload["tokens"]["output"] is None
        assert payload["tokens"]["total"] is None
        assert payload["cost"]["amount"] is None


def test_runtime_metrics_json_is_stable_and_disabled_repeat_stays_empty(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("STOCKCREWAI_RUNTIME_METRICS", "1")
    artifact = tmp_path / "runtime-metrics.json"
    monkeypatch.setenv("STOCKCREWAI_RUNTIME_METRICS_OUTPUT", str(artifact))

    flow = ResearchFlow(request="offline request")
    flow._emit_stage(_stage_event())
    flow._flow_result()
    first_payload = artifact.read_text(encoding="utf-8")
    flow._flow_result()
    assert artifact.read_text(encoding="utf-8") == first_payload

    monkeypatch.setenv("STOCKCREWAI_RUNTIME_METRICS", "off")
    disabled_artifact = tmp_path / "disabled.json"
    monkeypatch.setenv("STOCKCREWAI_RUNTIME_METRICS_OUTPUT", str(disabled_artifact))
    disabled_flow = ResearchFlow(request="offline request")
    disabled_flow._flow_result()
    disabled_flow._flow_result()
    assert not disabled_artifact.exists()
