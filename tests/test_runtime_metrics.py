from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from stockcrewai.services.runtime_metrics import RuntimeMetricsCollector


def _report(events, run_id: str = "run-1"):
    collector = RuntimeMetricsCollector(run_id=run_id)
    for event in events:
        collector.record(event)
    return collector.report()


def test_aggregates_identity_latency_tokens_retry_and_cost() -> None:
    report = _report(
        [
            {
                "event_type": "run_started",
                "run_id": "run-1",
                "crew": "research",
                "agent": "analyst",
                "task": "collect",
                "started_at": "2026-01-01T00:00:00Z",
            },
            {
                "event_type": "task_retry",
                "run_id": "run-1",
                "crew": "research",
                "agent": "analyst",
                "task": "collect",
                "retry_count": 1,
                "error_code": "provider_timeout",
            },
            {
                "event_type": "task_completed",
                "run_id": "run-1",
                "crew": "research",
                "agent": "analyst",
                "task": "collect",
                "ended_at": "2026-01-01T00:00:02.500000Z",
                "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
                "cost_usd": "0.125",
            },
        ]
    )

    payload = report.to_dict()
    detail = payload["details"][0]

    assert payload["run_id"] == "run-1"
    assert detail["crew"] == "research"
    assert detail["agent"] == "analyst"
    assert detail["task"] == "collect"
    assert detail["latency"]["seconds"] == 2.5
    assert detail["tokens"] == {
        "input": 10,
        "output": 4,
        "total": 14,
        "valid": True,
        "invalid_fields": [],
    }
    assert detail["retry"]["count"] == 1
    assert detail["cost"] == {
        "amount": "0.125",
        "currency": "USD",
        "valid": True,
        "invalid_fields": [],
    }


def test_accepts_lightweight_objects_and_explicit_elapsed() -> None:
    report = _report(
        [
            SimpleNamespace(
                event_type="task_completed",
                run_id="run-object",
                crew_name="reporting",
                agent_role="writer",
                task_name="render",
                elapsed_seconds=1.75,
                input_tokens=2,
                output_tokens=3,
            )
        ],
        run_id="run-object",
    )

    detail = report.to_dict()["details"][0]
    assert detail["crew"] == "reporting"
    assert detail["agent"] == "writer"
    assert detail["task"] == "render"
    assert detail["latency"] == {
        "seconds": 1.75,
        "source": "explicit_elapsed",
        "valid": True,
        "invalid_fields": [],
    }
    assert detail["tokens"]["total"] == 5


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"event_type": "failure", "failure_category": "input"}, "input"),
        (
            {"event_type": "failure", "error_code": "provider_timeout"},
            "external_dependency",
        ),
        ({"event_type": "gate_failed"}, "gate"),
        ({"event_type": "failure", "exception_type": "RuntimeError"}, "runtime"),
        ({"event_type": "failure"}, "unknown"),
    ],
)
def test_failure_category_uses_stable_codes_and_types(event, expected: str) -> None:
    event = {
        **event,
        "run_id": "run-failure",
        "crew": "research",
        "agent": "analyst",
        "task": "evaluate",
    }

    payload = _report([event], run_id="run-failure").to_dict()

    assert payload["failure_category"] == expected
    assert payload["status"] == "failed"


def test_json_and_hash_are_stable_when_event_order_changes() -> None:
    events = [
        {
            "event_type": "task_completed",
            "sequence": 2,
            "run_id": "run-stable",
            "crew": "research",
            "agent": "analyst",
            "task": "collect",
            "ended_at": "2026-01-01T00:00:02Z",
            "input_tokens": 8,
            "output_tokens": 2,
            "cost_usd": "0.10",
        },
        {
            "event_type": "run_started",
            "sequence": 1,
            "run_id": "run-stable",
            "crew": "research",
            "agent": "analyst",
            "task": "collect",
            "started_at": "2026-01-01T00:00:00Z",
        },
    ]

    first = _report(events, run_id="run-stable")
    second = _report(list(reversed(events)), run_id="run-stable")

    assert first.to_json() == second.to_json()
    assert first.stable_hash == second.stable_hash
    assert first.to_json() == first.json
    json.loads(first.to_json())


def test_sensitive_event_fields_are_not_retained_in_output() -> None:
    secret = "do-not-serialize-this-secret"
    report = _report(
        [
            {
                "event_type": "failure",
                "run_id": "run-safe",
                "crew": "research",
                "agent": "analyst",
                "task": "collect",
                "exception_type": "RuntimeError",
                "exception_message": f"Authorization: Bearer {secret}",
                "prompt": secret,
                "api_key": secret,
                "Authorization": f"Bearer {secret}",
                "cookie": secret,
                "tool_args": {"secret": secret},
            }
        ],
        run_id="run-safe",
    )

    serialized = report.to_json()
    for forbidden in (secret, "prompt", "api_key", "Authorization", "cookie", "tool_args"):
        assert forbidden not in serialized
    assert report.to_dict()["failure_category"] == "runtime"


def test_invalid_numbers_are_explicit_and_never_become_zero() -> None:
    payload = _report(
        [
            {
                "event_type": "task_completed",
                "run_id": "run-invalid",
                "crew": "research",
                "agent": "analyst",
                "task": "collect",
                "elapsed_seconds": "not-a-number",
                "input_tokens": "not-a-number",
                "output_tokens": -1,
                "total_tokens": "NaN",
                "retry_count": "not-a-number",
                "cost_usd": "NaN",
            }
        ],
        run_id="run-invalid",
    ).to_dict()
    detail = payload["details"][0]

    assert detail["latency"]["seconds"] is None
    assert detail["tokens"]["input"] is None
    assert detail["tokens"]["output"] is None
    assert detail["tokens"]["total"] is None
    assert detail["cost"]["amount"] is None
    assert detail["retry"]["count"] == 0
    assert detail["latency"]["invalid_fields"]
    assert detail["tokens"]["invalid_fields"]
    assert detail["cost"]["invalid_fields"]


def test_empty_failed_and_retry_runs_have_deterministic_json() -> None:
    empty = RuntimeMetricsCollector(run_id="empty").report()
    failed = _report(
        [
            {
                "event_type": "failure",
                "run_id": "failed",
                "crew": "research",
                "agent": "analyst",
                "task": "collect",
                "failure_category": "gate",
            }
        ],
        run_id="failed",
    )
    retried = _report(
        [
            {
                "event_type": "task_retry",
                "run_id": "retried",
                "crew": "research",
                "agent": "analyst",
                "task": "collect",
            }
        ],
        run_id="retried",
    )

    assert empty.to_dict()["status"] == "empty"
    assert failed.to_dict()["status"] == "failed"
    assert retried.to_dict()["details"][0]["retry"]["count"] == 1
    assert empty.to_json() == empty.to_json()
    assert failed.to_json() == failed.to_json()
    assert retried.to_json() == retried.to_json()
