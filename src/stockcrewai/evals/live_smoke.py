"""显式启动的真实依赖冒烟入口。

默认测试不会导入或调用默认 runner；只有命令行显式传入 ticker 时才会
执行真实研究 Flow。外部失败统一输出带 ``category`` 和 ``reason_code``
的 typed error，不生成替代数据。
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class LiveSmokeError(BaseModel):
    """冒烟运行的稳定错误契约。"""

    model_config = ConfigDict(extra="forbid")

    category: Literal["input", "external_dependency", "gate", "runtime"]
    reason_code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class LiveSmokeResult(BaseModel):
    """显式 live runner 的 JSON-safe 结果。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "error"]
    ticker: str
    data: dict[str, Any] | None = None
    error: LiveSmokeError | None = None


def _reason_code(value: Any, default: str) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return normalized or default


def _error_from_mapping(payload: Mapping[str, Any]) -> LiveSmokeError | None:
    direct = payload.get("error")
    if isinstance(direct, Mapping):
        category = direct.get("category")
        reason_code = direct.get("reason_code")
        message = direct.get("message")
        if category in {"input", "external_dependency", "gate", "runtime"} and reason_code and message:
            return LiveSmokeError(
                category=category,
                reason_code=_reason_code(reason_code, "external_dependency_error"),
                message=str(message),
            )

    edgar = payload.get("edgar")
    errors = edgar.get("errors") if isinstance(edgar, Mapping) else None
    if isinstance(errors, Sequence) and not isinstance(errors, (str, bytes)) and errors:
        first = errors[0]
        if isinstance(first, Mapping):
            return LiveSmokeError(
                category="external_dependency",
                reason_code=_reason_code(first.get("reason_code") or first.get("code"), "external_dependency_error"),
                message=str(first.get("message") or "外部依赖返回结构化错误。"),
            )

    required_data = payload.get("required_data")
    if isinstance(required_data, Sequence) and not isinstance(required_data, (str, bytes)) and required_data:
        reason = next((item for item in required_data if str(item).strip()), "gate_blocked")
        return LiveSmokeError(
            category="gate",
            reason_code=_reason_code(reason, "gate_blocked"),
            message="研究 Gate 未通过；未生成 live 替代数据。",
        )
    return None


def _default_runner(ticker: str) -> Any:
    from stockcrewai.main import run_research

    return run_research(f"分析 {ticker} 未来 3 年投资价值")


def run_live_smoke(
    ticker: str,
    *,
    runner: Callable[[str], Any] | None = None,
) -> LiveSmokeResult:
    """运行一次显式 live smoke；失败时返回 typed error，不伪造成功。"""
    normalized_ticker = str(ticker or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", normalized_ticker):
        return LiveSmokeResult(
            status="error",
            ticker=normalized_ticker,
            error=LiveSmokeError(
                category="input",
                reason_code="ticker_invalid",
                message="ticker 必须是 1 到 10 位字母数字代码。",
            ),
        )

    try:
        raw = (runner or _default_runner)(normalized_ticker)
    except (ConnectionError, TimeoutError, OSError, ImportError) as exc:
        return LiveSmokeResult(
            status="error",
            ticker=normalized_ticker,
            error=LiveSmokeError(
                category="external_dependency",
                reason_code=_reason_code(type(exc).__name__, "external_dependency_error"),
                message=str(exc) or type(exc).__name__,
            ),
        )
    except Exception as exc:
        return LiveSmokeResult(
            status="error",
            ticker=normalized_ticker,
            error=LiveSmokeError(
                category="runtime",
                reason_code=_reason_code(type(exc).__name__, "runtime_error"),
                message=str(exc) or type(exc).__name__,
            ),
        )

    if not isinstance(raw, Mapping):
        return LiveSmokeResult(
            status="error",
            ticker=normalized_ticker,
            error=LiveSmokeError(
                category="runtime",
                reason_code="result_not_mapping",
                message="live runner 未返回 JSON 对象。",
            ),
        )
    if raw.get("status") == "ok":
        return LiveSmokeResult(
            status="ok",
            ticker=normalized_ticker,
            data={str(key): value for key, value in raw.items()},
        )

    error = _error_from_mapping(raw) or LiveSmokeError(
        category="runtime",
        reason_code="live_result_not_ok",
        message="live runner 未返回成功状态。",
    )
    return LiveSmokeResult(status="error", ticker=normalized_ticker, error=error)


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Callable[[str], Any] | None = None,
) -> int:
    """解析显式 live 参数、输出 JSON，并以非零码报告失败。"""
    parser = argparse.ArgumentParser(description="Run one explicit StockCrewAI live smoke.")
    parser.add_argument("--ticker", required=True)
    args = parser.parse_args(argv)
    result = run_live_smoke(args.ticker, runner=runner)
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
    return 0 if result.status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
