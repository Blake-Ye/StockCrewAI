from __future__ import annotations

import json

import pytest

from stockcrewai.evals import live_smoke


def test_live_smoke_cli_preserves_typed_external_error_without_data(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def failing_runner(ticker: str) -> dict[str, object]:
        assert ticker == "TSLA"
        return {
            "status": "error",
            "edgar": {
                "status": "error",
                "errors": [{"code": "sec_timeout", "message": "fixture failure"}],
            },
        }

    exit_code = live_smoke.main(["--ticker", "TSLA"], runner=failing_runner)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["status"] == "error"
    assert payload["error"]["category"] == "external_dependency"
    assert payload["error"]["reason_code"] == "sec_timeout"
    assert payload["error"]["message"] == "fixture failure"
    assert payload["data"] is None


@pytest.mark.live
def test_live_marker_can_run_only_with_injected_runner(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = live_smoke.main(
        ["--ticker", "AAPL"],
        runner=lambda ticker: {"status": "ok", "ticker": ticker},
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["data"] == {"status": "ok", "ticker": "AAPL"}
