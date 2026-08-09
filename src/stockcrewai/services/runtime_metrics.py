"""离线运行观测指标。

本模块只记录运行观测，不参与业务判断、估值或投资建议；它不监听 CrewAI
事件，也不保存 prompt、密钥、请求头、cookie 或原始工具参数。
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from collections.abc import Mapping
from typing import Any


_UNKNOWN = "unknown"
_CATEGORY_ORDER = (
    "input",
    "external_dependency",
    "gate",
    "runtime",
    "unknown",
)


class FailureCategory(str, Enum):
    """运行失败的稳定分类，不从异常消息或自然语言推断。"""

    INPUT = "input"
    EXTERNAL_DEPENDENCY = "external_dependency"
    GATE = "gate"
    RUNTIME = "runtime"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class _Moment:
    value: datetime | Decimal
    sort_key: str


@dataclass(frozen=True)
class _Event:
    run_id: str
    crew: str
    agent: str
    task: str
    kind: str
    start: _Moment | None
    end: _Moment | None
    elapsed: Decimal | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cost: Decimal | None
    currency: str | None
    retry_count: int | None
    retry_event: bool
    failure: bool
    failure_category: str | None
    latency_invalid: tuple[str, ...]
    token_invalid: tuple[str, ...]
    cost_invalid: tuple[str, ...]
    retry_invalid: tuple[str, ...]
    latency_present: bool
    input_present: bool
    output_present: bool
    total_present: bool
    cost_present: bool
    retry_present: bool


@dataclass(frozen=True)
class RuntimeMetricsReport:
    """可 JSON 序列化、字段稳定的运行指标报告。"""

    _payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """返回不暴露内部状态的 JSON-safe 字典。"""

        return copy.deepcopy(dict(self._payload))

    def to_json(self) -> str:
        """以稳定字段顺序生成 JSON 文本。"""

        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def json(self) -> str:
        """稳定 JSON 文本入口。"""

        return self.to_json()

    @property
    def stable_hash(self) -> str:
        """返回稳定的 SHA-256 JSON 摘要。"""

        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    @property
    def hash(self) -> str:
        """兼容性的稳定摘要别名。"""

        return self.stable_hash


class RuntimeMetricsCollector:
    """接收结构化运行事件并离线汇总安全指标。"""

    def __init__(self, run_id: str | None = None) -> None:
        self._run_id = _identity(run_id)
        self._events: list[_Event] = []

    def record(self, event: object) -> RuntimeMetricsCollector:
        """记录一个事件，只提取白名单观测字段。"""

        self._events.append(_normalize_event(event, self._run_id))
        return self

    def record_event(self, event: object) -> RuntimeMetricsCollector:
        """record 的显式别名，便于事件适配层调用。"""

        return self.record(event)

    def collect(self, event: object) -> RuntimeMetricsCollector:
        """record 的简短别名。"""

        return self.record(event)

    def report(self) -> RuntimeMetricsReport:
        """生成与事件输入顺序无关的汇总报告。"""

        if not self._events:
            payload = _empty_payload(self._run_id)
            return RuntimeMetricsReport(payload)

        groups: dict[tuple[str, str, str, str], list[_Event]] = {}
        for event in self._events:
            key = (event.run_id, event.crew, event.agent, event.task)
            groups.setdefault(key, []).append(event)

        details = []
        for key in sorted(groups):
            detail_events = groups[key]
            metrics = _metrics(detail_events)
            detail = {
                "run_id": key[0],
                "crew": key[1],
                "agent": key[2],
                "task": key[3],
                **metrics,
                "failure_category": _failure_category(detail_events),
            }
            details.append(detail)

        all_metrics = _metrics(self._events)
        categories = _failure_categories(self._events)
        payload = {
            "run_id": self._report_run_id(),
            "status": _status(self._events),
            **all_metrics,
            "failure_category": categories[0] if categories else None,
            "failure_categories": categories,
            "details": details,
        }
        return RuntimeMetricsReport(payload)

    def build_report(self) -> RuntimeMetricsReport:
        """report 的显式别名。"""

        return self.report()

    def to_dict(self) -> dict[str, Any]:
        """直接返回当前汇总的 JSON-safe 字典。"""

        return self.report().to_dict()

    def to_json(self) -> str:
        """直接返回当前汇总的稳定 JSON 文本。"""

        return self.report().to_json()

    @property
    def stable_hash(self) -> str:
        """直接返回当前汇总的稳定 SHA-256 摘要。"""

        return self.report().stable_hash

    def _report_run_id(self) -> str:
        if self._run_id != _UNKNOWN:
            return self._run_id
        run_ids = {event.run_id for event in self._events}
        return next(iter(run_ids)) if len(run_ids) == 1 else _UNKNOWN


def _empty_payload(run_id: str) -> dict[str, Any]:
    metrics = _metrics([])
    return {
        "run_id": run_id,
        "status": "empty",
        **metrics,
        "failure_category": None,
        "failure_categories": [],
        "details": [],
    }


def _read(value: object, *names: str) -> tuple[bool, Any]:
    """从 mapping 或轻量对象读取字段，不读取对象的原始 repr。"""

    for name in names:
        if isinstance(value, Mapping):
            if name in value:
                return True, value[name]
            continue
        try:
            return True, getattr(value, name)
        except AttributeError:
            continue
        except Exception:
            return True, None
    return False, None


def _identity(value: object) -> str:
    if value is None:
        return _UNKNOWN
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        text = str(value).strip()
        return text or _UNKNOWN
    return _UNKNOWN


def _kind(event: object) -> str:
    found, value = _read(event, "event_type", "type", "kind", "name")
    if not found or not isinstance(value, str):
        return ""
    return value.strip().casefold().replace("-", "_").replace(" ", "_")


def _lookup_metric(
    event: object,
    nested: object,
    names: tuple[str, ...],
    parser,
) -> tuple[Any, bool, bool, str | None]:
    value = None
    present = False
    invalid = False
    selected_name = None
    containers = (event, nested)
    for container in containers:
        if container is None:
            continue
        for name in names:
            found, raw = _read(container, name)
            if not found:
                continue
            present = True
            parsed = parser(raw)
            if parsed is None:
                invalid = True
                continue
            if value is None:
                value = parsed
                selected_name = name
        if value is not None:
            break
    return value, present, invalid, selected_name


def _decimal(value: object, *, integer: bool = False) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not number.is_finite() or number < 0:
        return None
    if integer and number != number.to_integral_value():
        return None
    return number


def _token(value: object) -> int | None:
    number = _decimal(value, integer=True)
    return int(number) if number is not None else None


def _cost(value: object) -> Decimal | None:
    return _decimal(value)


def _moment(value: object) -> _Moment | None:
    if isinstance(value, datetime):
        current = value
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
        return _Moment(current, f"d:{current.isoformat(timespec='microseconds')}")

    number = _decimal(value)
    if number is not None and not isinstance(value, str):
        return _Moment(number, f"n:{number}")

    if isinstance(value, str):
        text = value.strip()
        try:
            current = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
        return _Moment(current, f"d:{current.isoformat(timespec='microseconds')}")
    return None


def _duration(start: _Moment, end: _Moment) -> Decimal | None:
    if isinstance(start.value, datetime) and isinstance(end.value, datetime):
        return Decimal(str((end.value - start.value).total_seconds()))
    if isinstance(start.value, Decimal) and isinstance(end.value, Decimal):
        return end.value - start.value
    return None


def _is_start(kind: str) -> bool:
    return kind in {"start", "started"} or kind.endswith("_start") or kind.endswith("_started")


def _is_end(kind: str) -> bool:
    return (
        kind in {"end", "ended", "complete", "completed", "success", "succeeded"}
        or kind.endswith("_end")
        or kind.endswith("_ended")
        or kind.endswith("_complete")
        or kind.endswith("_completed")
        or kind.endswith("_success")
        or kind.endswith("_succeeded")
    )


def _is_failure(kind: str, event: object) -> bool:
    found, status = _read(event, "status")
    status_value = status.casefold() if isinstance(status, str) else ""
    return (
        kind in {"failure", "failed", "error", "exception"}
        or kind.endswith("_failure")
        or kind.endswith("_failed")
        or kind.endswith("_error")
        or kind.endswith("_exception")
        or status_value in {"failure", "failed", "error", "exception"}
        or (found and status_value == "blocked")
    )


def _category(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "input": "input",
        "input_error": "input",
        "invalid_input": "input",
        "validation_error": "input",
        "bad_request": "input",
        "external": "external_dependency",
        "external_dependency": "external_dependency",
        "network_error": "external_dependency",
        "connection_error": "external_dependency",
        "provider_error": "external_dependency",
        "provider_timeout": "external_dependency",
        "timeout": "external_dependency",
        "rate_limit": "external_dependency",
        "rate_limited": "external_dependency",
        "gate": "gate",
        "gate_failed": "gate",
        "validation_gate": "gate",
        "claim_gate": "gate",
        "quality_gate": "gate",
        "policy_gate": "gate",
        "runtime": "runtime",
        "runtime_error": "runtime",
        "execution_error": "runtime",
        "internal_error": "runtime",
        "exception": "runtime",
    }
    return aliases.get(value)


def _exception_category(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    exact = {
        "ValueError": "input",
        "TypeError": "input",
        "ValidationError": "input",
        "TimeoutError": "external_dependency",
        "ConnectionError": "external_dependency",
        "HTTPError": "external_dependency",
        "SSLError": "external_dependency",
        "RuntimeError": "runtime",
        "AssertionError": "runtime",
        "KeyError": "runtime",
        "IndexError": "runtime",
    }
    return exact.get(value.rsplit(".", 1)[-1])


def _failure_category_for_event(event: object, kind: str, failure: bool) -> str | None:
    explicit_found, explicit = _read(event, "failure_category", "failure_type", "category")
    if explicit_found:
        return _category(explicit) or "unknown"

    code_found, code = _read(event, "error_code", "reason_code", "failure_code")
    if code_found:
        category = _category(code)
        if category is not None:
            return category

    type_found, exception_type = _read(event, "exception_type", "error_type")
    if type_found:
        category = _exception_category(exception_type)
        if category is not None:
            return category

    event_category = _category(kind)
    if event_category is not None:
        return event_category
    if failure:
        return "unknown"
    return None


def _normalize_event(event: object, default_run_id: str) -> _Event:
    kind = _kind(event)
    run_id = _first_identity(event, ("run_id", "execution_id", "kickoff_id"), default_run_id)
    crew = _first_identity(event, ("crew", "crew_name"))
    agent = _first_identity(event, ("agent", "agent_name", "agent_role"))
    task = _first_identity(event, ("task", "task_name", "task_id"))

    usage_found, usage = _read(event, "usage", "token_usage", "usage_metrics")
    nested_usage = usage if usage_found else None

    start, start_invalid = _time_field(event, nested_usage, ("started_at", "start_time", "start"))
    end, end_invalid = _time_field(event, nested_usage, ("ended_at", "end_time", "end"))
    timestamp_found, timestamp = _read(event, "timestamp", "event_time", "time")
    timestamp_invalid = False
    if timestamp_found:
        parsed_timestamp = _moment(timestamp)
        if parsed_timestamp is None:
            timestamp_invalid = True
        elif start is None and _is_start(kind):
            start = parsed_timestamp
        elif end is None and _is_end(kind):
            end = parsed_timestamp

    elapsed, elapsed_present, elapsed_invalid, _ = _lookup_metric(
        event,
        nested_usage,
        ("elapsed_seconds", "elapsed", "latency_seconds", "duration_seconds"),
        _decimal,
    )
    latency_invalid = []
    if start_invalid:
        latency_invalid.append("latency.start")
    if end_invalid:
        latency_invalid.append("latency.end")
    if timestamp_invalid:
        latency_invalid.append("latency.timestamp")
    if elapsed_invalid:
        latency_invalid.append("latency.elapsed")

    input_tokens, input_present, input_invalid, _ = _lookup_metric(
        event,
        nested_usage,
        ("input_tokens", "prompt_tokens", "tokens_input"),
        _token,
    )
    output_tokens, output_present, output_invalid, _ = _lookup_metric(
        event,
        nested_usage,
        ("output_tokens", "completion_tokens", "tokens_output"),
        _token,
    )
    total_tokens, total_present, total_invalid, _ = _lookup_metric(
        event,
        nested_usage,
        ("total_tokens", "tokens_total"),
        _token,
    )
    token_invalid = []
    if input_invalid:
        token_invalid.append("tokens.input")
    if output_invalid:
        token_invalid.append("tokens.output")
    if total_invalid:
        token_invalid.append("tokens.total")

    cost, cost_present, cost_invalid, cost_name = _lookup_metric(
        event,
        nested_usage,
        ("cost_usd", "total_cost_usd", "cost", "total_cost", "estimated_cost"),
        _cost,
    )
    currency = _currency(event, nested_usage, cost_name)
    cost_invalid_fields = ["cost.amount"] if cost_invalid else []

    retry_count, retry_present, retry_invalid, _ = _lookup_metric(
        event,
        nested_usage,
        ("retry_count", "retries"),
        _token,
    )
    retry_invalid_fields = ["retry.count"] if retry_invalid else []
    retry_event = "retry" in kind
    failure = _is_failure(kind, event)
    failure_category = _failure_category_for_event(event, kind, failure)
    if failure_category is not None:
        failure = True

    return _Event(
        run_id=run_id,
        crew=crew,
        agent=agent,
        task=task,
        kind=kind,
        start=start,
        end=end,
        elapsed=elapsed,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cost=cost,
        currency=currency,
        retry_count=retry_count,
        retry_event=retry_event,
        failure=failure,
        failure_category=failure_category,
        latency_invalid=tuple(sorted(set(latency_invalid))),
        token_invalid=tuple(sorted(set(token_invalid))),
        cost_invalid=tuple(sorted(set(cost_invalid_fields))),
        retry_invalid=tuple(sorted(set(retry_invalid_fields))),
        latency_present=elapsed_present or start is not None or end is not None,
        input_present=input_present,
        output_present=output_present,
        total_present=total_present,
        cost_present=cost_present,
        retry_present=retry_present,
    )


def _first_identity(event: object, names: tuple[str, ...], default: str = _UNKNOWN) -> str:
    for name in names:
        found, value = _read(event, name)
        if found:
            value = _identity(value)
            if value != _UNKNOWN:
                return value
    return default if default != _UNKNOWN else _UNKNOWN


def _time_field(
    event: object,
    nested: object,
    names: tuple[str, ...],
) -> tuple[_Moment | None, bool]:
    for container in (event, nested):
        if container is None:
            continue
        for name in names:
            found, value = _read(container, name)
            if not found:
                continue
            parsed = _moment(value)
            return parsed, parsed is None
    return None, False


def _currency(event: object, nested: object, cost_name: str | None) -> str | None:
    if cost_name in {"cost_usd", "total_cost_usd"}:
        return "USD"
    for container in (event, nested):
        if container is None:
            continue
        found, value = _read(container, "cost_currency", "currency")
        if found and isinstance(value, str) and value.strip():
            return value.strip().upper()
    return None


def _metrics(events: list[_Event]) -> dict[str, Any]:
    latency = _latency(events)
    tokens = _tokens(events)
    retry = _retry(events)
    cost = _cost_report(events)
    return {
        "latency": latency,
        "tokens": tokens,
        "retry": retry,
        "cost": cost,
    }


def _latency(events: list[_Event]) -> dict[str, Any]:
    invalid = sorted({field for event in events for field in event.latency_invalid})
    explicit = [event.elapsed for event in events if event.elapsed is not None]
    seconds: Decimal | None = sum(explicit, Decimal(0)) if explicit else None
    source = "explicit_elapsed" if explicit else None
    if seconds is None:
        starts = [event.start for event in events if event.start is not None]
        ends = [event.end for event in events if event.end is not None]
        if starts and ends:
            start = min(starts, key=lambda moment: moment.sort_key)
            end = max(ends, key=lambda moment: moment.sort_key)
            seconds = _duration(start, end)
            source = "start_end"
            if seconds is None:
                invalid.append("latency.timestamp_type")
            elif seconds < 0:
                seconds = None
                invalid.append("latency.timestamp_order")

    if seconds is not None and seconds < 0:
        seconds = None
        invalid.append("latency.elapsed_negative")
    invalid = sorted(set(invalid))
    return {
        "seconds": float(seconds) if seconds is not None else None,
        "source": source,
        "valid": seconds is not None and not invalid,
        "invalid_fields": invalid,
    }


def _tokens(events: list[_Event]) -> dict[str, Any]:
    input_values = [event.input_tokens for event in events if event.input_tokens is not None]
    output_values = [event.output_tokens for event in events if event.output_tokens is not None]
    total_values = [event.total_tokens for event in events if event.total_tokens is not None]
    input_total = sum(input_values) if input_values else None
    output_total = sum(output_values) if output_values else None
    explicit_total = sum(total_values) if total_values else None
    derived_total = (
        input_total + output_total
        if input_total is not None and output_total is not None
        else None
    )
    invalid = sorted({field for event in events for field in event.token_invalid})
    total = explicit_total if explicit_total is not None else derived_total
    if explicit_total is not None and derived_total is not None and explicit_total != derived_total:
        total = None
        invalid.append("tokens.total_mismatch")
    invalid = sorted(set(invalid))
    has_value = input_total is not None or output_total is not None or total is not None
    return {
        "input": input_total,
        "output": output_total,
        "total": total,
        "valid": has_value and not invalid,
        "invalid_fields": invalid,
    }


def _retry(events: list[_Event]) -> dict[str, Any]:
    counts = [event.retry_count for event in events if event.retry_count is not None]
    count = max(counts, default=0)
    if any(event.retry_event for event in events):
        count = max(count, 1)
    invalid = sorted({field for event in events for field in event.retry_invalid})
    return {
        "count": count,
        "valid": not invalid,
        "invalid_fields": invalid,
    }


def _cost_report(events: list[_Event]) -> dict[str, Any]:
    values = [event.cost for event in events if event.cost is not None]
    invalid = sorted({field for event in events for field in event.cost_invalid})
    currencies = sorted({event.currency for event in events if event.currency is not None})
    amount = sum(values, Decimal(0)) if values else None
    currency = currencies[0] if len(currencies) == 1 else None
    if len(currencies) > 1:
        amount = None
        invalid.append("cost.currency_mismatch")
    invalid = sorted(set(invalid))
    return {
        "amount": _decimal_text(amount),
        "currency": currency,
        "valid": amount is not None and not invalid,
        "invalid_fields": invalid,
    }


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _failure_categories(events: list[_Event]) -> list[str]:
    categories = {event.failure_category for event in events if event.failure_category is not None}
    return sorted(categories, key=lambda category: _CATEGORY_ORDER.index(category))


def _failure_category(events: list[_Event]) -> str | None:
    categories = _failure_categories(events)
    return categories[0] if categories else None


def _status(events: list[_Event]) -> str:
    if any(event.failure for event in events):
        return "failed"
    if any(event.end is not None or _is_end(event.kind) for event in events):
        return "completed"
    if any(event.start is not None or _is_start(event.kind) for event in events):
        return "running"
    return "observed"


__all__ = ["FailureCategory", "RuntimeMetricsCollector", "RuntimeMetricsReport"]
