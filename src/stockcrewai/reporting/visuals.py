from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from decimal import Decimal, InvalidOperation
from io import BytesIO
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable


_MPL_CONFIG_DIR = Path(tempfile.gettempdir()) / "stockcrewai-matplotlib"
_MPL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
os.environ["MPLCONFIGDIR"] = str(_MPL_CONFIG_DIR)

import matplotlib  # noqa: E402

matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402


def _configure_cjk_font() -> None:
    """优先使用系统中文字体，避免图表标签退化成方框。"""
    candidates = (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    )
    for path in candidates:
        if Path(path).exists():
            try:
                plt.rcParams["font.family"] = [
                    font_manager.FontProperties(fname=path).get_name()
                ]
                break
            except (OSError, RuntimeError):
                continue
    plt.rcParams["axes.unicode_minus"] = False


_configure_cjk_font()


_PNG_PREFIX = "data:image/png;base64,"
_PNG_OUTPUT_PATH: ContextVar[Path | None] = ContextVar(
    "stockcrewai_png_output_path", default=None
)
_FINANCIAL_KPI_IDS = (
    "revenue_growth",
    "operating_margin",
    "net_margin",
    "free_cash_flow_margin",
    "cash_conversion",
    "share_dilution",
)
_FINANCIAL_KPI_LABELS = {
    "revenue_growth": "收入增长",
    "operating_margin": "营业利润率",
    "net_margin": "净利率",
    "free_cash_flow_margin": "自由现金流利润率",
    "cash_conversion": "现金转换率",
    "share_dilution": "股份变化",
}
_TTM_IDS = (
    "revenue",
    "operating_income",
    "net_income",
    "operating_cash_flow",
    "free_cash_flow",
)
_TTM_LABELS = {
    "revenue": "收入",
    "operating_income": "营业利润",
    "net_income": "净利润",
    "operating_cash_flow": "经营现金流",
    "free_cash_flow": "自由现金流",
}
_FINANCIAL_KPI_TITLE = "财务质量指标（已验证数据）"
_TTM_AXIS_LABEL = "金额（十亿美元）"
_NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
_FINANCIAL_KPI_GROUPS = (
    ("增长与资本配置", ("revenue_growth", "share_dilution")),
    ("盈利能力", ("operating_margin", "net_margin")),
    ("现金流质量", ("free_cash_flow_margin", "cash_conversion")),
)
_FINANCIAL_REQUIRED_IDS = frozenset(
    metric_id
    for _, metric_ids in _FINANCIAL_KPI_GROUPS
    for metric_id in metric_ids
    if metric_id != "share_dilution"
)
_SHARE_ADJUSTMENT_BASES = frozenset({"raw", "split_adjusted"})


def _records(value: Any, *, collection_keys: tuple[str, ...]) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        for key in collection_keys:
            nested = value.get(key)
            if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
                return [item for item in nested if isinstance(item, Mapping)]
        if "metric_id" in value or "formula_id" in value:
            return [value]
        records = []
        for metric_id, item in value.items():
            if isinstance(item, Mapping):
                records.append({"metric_id": metric_id, **item})
            elif isinstance(item, (str, int, float, Decimal)):
                records.append({"metric_id": metric_id, "value": item})
        return records
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _record_id(record: Mapping[str, Any]) -> str | None:
    value = record.get("metric_id", record.get("formula_id"))
    return str(value).strip() if value not in (None, "") else None


def _is_verified(record: Mapping[str, Any]) -> bool:
    status = record.get("status")
    validation_status = record.get("validation_status")
    return (
        status in ("available", "ok")
        and validation_status == "valid"
        and not record.get("rejected")
    )


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    if text.startswith("Decimal("):
        text = text[8:].strip("()'\"")
    match = _NUMBER_RE.search(text)
    if match is None:
        return None
    try:
        result = Decimal(match.group(0))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _raw_value(record: Mapping[str, Any]) -> tuple[Any, str]:
    for key in ("raw_result", "display_value", "normalized_result", "value", "pe_ratio"):
        if record.get(key) not in (None, ""):
            return record[key], str(record.get("unit", ""))
    return None, str(record.get("unit", ""))


def _ratio_value(record: Mapping[str, Any]) -> float | None:
    raw, unit = _raw_value(record)
    value = _decimal(raw)
    if value is None:
        return None
    text = str(raw)
    if "%" not in text and unit.lower() in {"ratio", "percent", "percentage"}:
        value *= Decimal("100")
    return float(value) if value.is_finite() else None


def _amount_in_billion_usd(record: Mapping[str, Any]) -> float | None:
    raw, unit = _raw_value(record)
    value = _decimal(raw)
    if value is None:
        return None
    normalized_unit = unit.lower().replace(" ", "")
    if any(token in normalized_unit for token in ("trillion", "万亿")):
        value *= Decimal("1000")
    elif any(token in normalized_unit for token in ("billion", "十亿")):
        pass
    elif "亿美元" in normalized_unit:
        value /= Decimal("10")
    elif any(token in normalized_unit for token in ("million", "百万")):
        value /= Decimal("1000")
    else:
        value /= Decimal("1000000000")
    return float(value) if value.is_finite() else None


def _png_uri(
    draw: Callable[[Any], None], *, size: tuple[float, float], dpi: int = 120
) -> str:
    figure, axes = plt.subplots(figsize=size, dpi=dpi)
    try:
        draw(axes)
        buffer = BytesIO()
        figure.savefig(
            buffer,
            format="png",
            bbox_inches="tight",
            metadata={"Software": "StockCrewAI"},
        )
        payload = buffer.getvalue()
        if output_path := _PNG_OUTPUT_PATH.get():
            output_path.write_bytes(payload)
        return _PNG_PREFIX + base64.b64encode(payload).decode("ascii")
    finally:
        plt.close(figure)


def _render_to_output_dir(
    key: str,
    output_dir: Path | None,
    renderer: Callable[[], str | None],
) -> str | None:
    if output_dir is None:
        return renderer()
    token = _PNG_OUTPUT_PATH.set(output_dir / f"{key}.png")
    try:
        return renderer()
    finally:
        _PNG_OUTPUT_PATH.reset(token)


def _financial_kpi_png(records: Mapping[str, Mapping[str, Any]]) -> str | None:
    verified_records = {
        metric_id: record
        for metric_id, record in records.items()
        if metric_id in _FINANCIAL_KPI_IDS and _is_verified(record)
    }
    if not _FINANCIAL_REQUIRED_IDS.issubset(verified_records):
        return None
    if (
        verified_records.get("share_dilution", {}).get("adjustment_basis")
        not in _SHARE_ADJUSTMENT_BASES
    ):
        verified_records.pop("share_dilution", None)

    panel_data: list[tuple[str, list[str], list[float]]] = []
    for title, metric_ids in _FINANCIAL_KPI_GROUPS:
        available_ids = [metric_id for metric_id in metric_ids if metric_id in verified_records]
        values = [
            _ratio_value(verified_records[metric_id]) for metric_id in available_ids
        ]
        if not available_ids or any(value is None for value in values):
            return None
        panel_data.append(
            (title, available_ids, [value for value in values if value is not None])
        )

    def draw(axes: Any) -> None:
        figure = axes.figure
        figure.clear()
        panel_axes = figure.subplots(1, len(panel_data), squeeze=False)[0]
        for panel, (title, metric_ids, values) in zip(panel_axes, panel_data):
            labels = []
            for metric_id in metric_ids:
                label = _FINANCIAL_KPI_LABELS[metric_id]
                if (
                    metric_id == "share_dilution"
                    and verified_records[metric_id].get("adjustment_basis")
                    == "split_adjusted"
                ):
                    label = "股份变化（拆分调整）"
                labels.append(label)
            bars = panel.barh(labels, values, color="#3568a8")
            reference = 100.0 if title == "现金流质量" else None
            lower = min(0.0, min(values), reference if reference is not None else 0.0)
            upper = max(0.0, max(values), reference if reference is not None else 0.0)
            span = max(upper - lower, 1.0)
            padding = max(span * 0.30, 3.0)
            label_offset = max(span * 0.05, 0.5)
            panel.set_xlim(lower - padding, upper + padding)
            panel.axvline(0, color="#555555", linewidth=0.8)
            if reference is not None:
                panel.axvline(
                    reference,
                    color="#b91c1c",
                    linestyle=":",
                    linewidth=1.0,
                    label="100%基准",
                )
            panel.set_xlabel("百分比（%）")
            panel.set_title(title)
            panel.grid(axis="x", alpha=0.25)
            panel.invert_yaxis()
            for bar, value in zip(bars, values):
                positive = value >= 0
                panel.text(
                    value + label_offset if positive else value - label_offset,
                    bar.get_y() + bar.get_height() / 2,
                    f"{value:.2f}%",
                    va="center",
                    ha="left" if positive else "right",
                    fontsize=8,
                )
        figure.suptitle(_FINANCIAL_KPI_TITLE)
        figure.subplots_adjust(left=0.08, right=0.98, bottom=0.22, top=0.80, wspace=0.45)

    return _png_uri(draw, size=(12.0, 4.8))


def _ttm_png(records: Mapping[str, Mapping[str, Any]]) -> str | None:
    values = [_amount_in_billion_usd(records[metric_id]) for metric_id in _TTM_IDS]
    if any(value is None for value in values):
        return None

    def draw(axes: Any) -> None:
        bars = axes.bar(
            [_TTM_LABELS[metric_id] for metric_id in _TTM_IDS],
            values,
            color="#4c956c",
        )
        axes.set_ylabel(_TTM_AXIS_LABEL)
        axes.set_title("过去十二个月（TTM）财务规模")
        axes.tick_params(axis="x", rotation=20)
        axes.grid(axis="y", alpha=0.25)
        for bar, value in zip(bars, values):
            axes.text(
                bar.get_x() + bar.get_width() / 2,
                value,
                f"{value:.1f}",
                ha="center",
                va="bottom",
                fontsize=8,
            )

    return _png_uri(draw, size=(8.0, 4.2))


def _historical_tick_data(
    points: Sequence[tuple[str, Decimal]],
) -> tuple[list[int], list[str]]:
    """为历史序列生成少量真实日期刻度，避免读者看到无意义的 0..59。"""
    if not points:
        return [], []
    step = max(1, len(points) // 5)
    indices = list(range(0, len(points), step))
    if indices[-1] != len(points) - 1:
        indices.append(len(points) - 1)
    return indices, [points[index][0][:7] for index in indices]


def _historical_pe_png(payload: Mapping[str, Any]) -> str | None:
    if payload.get("status") != "ok" or payload.get("validation_status") != "valid":
        return None
    raw_series = payload.get("series")
    current_date = payload.get("current_date")
    current_value = _decimal(payload.get("current_value"))
    percentile_25 = _decimal(payload.get("percentile_25"))
    median = _decimal(payload.get("five_year_median"))
    percentile_75 = _decimal(payload.get("percentile_75"))
    if not isinstance(raw_series, Sequence) or isinstance(raw_series, (str, bytes)):
        return None
    if (
        not isinstance(current_date, str)
        or not current_date.strip()
        or current_value is None
        or percentile_25 is None
        or median is None
        or percentile_75 is None
    ):
        return None

    points: list[tuple[str, Decimal]] = []
    for item in raw_series:
        if not isinstance(item, Mapping):
            continue
        point_date = item.get("date")
        value = _decimal(item.get("pe_ratio", item.get("value")))
        if isinstance(point_date, str) and point_date.strip() and value is not None:
            points.append((point_date.strip(), value))
    points.sort(key=lambda item: item[0])
    if len(points) < 60:
        return None
    if points[-1][0] != current_date.strip():
        return None
    if points[-1][1] != current_value:
        return None
    points = points[-60:]
    values = [float(value) for _, value in points]

    def draw(axes: Any) -> None:
        x_values = list(range(len(points)))
        tick_indices, tick_labels = _historical_tick_data(points)
        axes.plot(x_values, values, color="#3568a8", linewidth=1.5, label="P/E（倍）")
        axes.axhline(
            float(percentile_25),
            color="#d97706",
            linestyle="--",
            label=f"25分位 {percentile_25:.2f}x",
        )
        axes.axhline(
            float(median),
            color="#555555",
            linestyle="--",
            label=f"中位数 {median:.2f}x",
        )
        axes.axhline(
            float(percentile_75),
            color="#b91c1c",
            linestyle="--",
            label=f"75分位 {percentile_75:.2f}x",
        )
        axes.scatter(
            [x_values[-1]], [float(current_value)], color="#111827", zorder=3, label="最新"
        )
        axes.annotate(
            f"最新 {current_value:.2f}x",
            (x_values[-1], float(current_value)),
            xytext=(-45, 10),
            textcoords="offset points",
            fontsize=8,
        )
        axes.set_xticks(tick_indices)
        axes.set_xticklabels(tick_labels, rotation=30, ha="right")
        axes.set_xlabel("日期（YYYY-MM）")
        axes.set_ylabel("P/E（倍）")
        axes.set_title("过去五年历史 P/E（TTM）")
        axes.legend(loc="best", frameon=False)
        axes.grid(alpha=0.25)

    return _png_uri(draw, size=(10.8, 5.1), dpi=84)


def build_report_visuals(
    financial_metrics: Any = None,
    ttm_metrics: Any = None,
    historical_payload: Mapping[str, Any] | None = None,
    *,
    calculations: Any = None,
    ttm: Any = None,
    historical_valuation: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, str]:
    """从已验证的确定性输入生成内嵌 PNG；缺一图输入只省略该图。"""
    if context is not None:
        financial_metrics = context.get("metrics", financial_metrics)
        ttm_metrics = context.get("ttm", context.get("ttm_metrics", ttm_metrics))
        historical_payload = context.get("historical_valuation", historical_payload)
    financial_metrics = calculations if financial_metrics is None else financial_metrics
    ttm_metrics = ttm if ttm_metrics is None else ttm_metrics
    historical_payload = (
        historical_valuation if historical_payload is None else historical_payload
    )
    output_path = Path(output_dir) if output_dir is not None else None
    if output_path is not None:
        output_path.mkdir(parents=True, exist_ok=True)

    financial_records = {
        metric_id: record
        for record in _records(
            financial_metrics, collection_keys=("metrics", "calculations")
        )
        if (metric_id := _record_id(record)) in _FINANCIAL_KPI_IDS and _is_verified(record)
    }
    if (
        financial_records.get("share_dilution", {}).get("adjustment_basis")
        not in _SHARE_ADJUSTMENT_BASES
    ):
        financial_records.pop("share_dilution", None)
    ttm_records = {
        metric_id: record
        for record in _records(ttm_metrics, collection_keys=("metrics", "calculations"))
        if (metric_id := _record_id(record)) in _TTM_IDS and _is_verified(record)
    }
    visuals: dict[str, str] = {}
    if _FINANCIAL_REQUIRED_IDS.issubset(financial_records):
        if (
            uri := _render_to_output_dir(
                "financial_kpis",
                output_path,
                lambda: _financial_kpi_png(financial_records),
            )
        ) is not None:
            visuals["financial_kpis"] = uri
    if len(ttm_records) == len(_TTM_IDS):
        if (
            uri := _render_to_output_dir(
                "ttm_scale",
                output_path,
                lambda: _ttm_png(ttm_records),
            )
        ) is not None:
            visuals["ttm_scale"] = uri
    if isinstance(historical_payload, Mapping):
        if (
            uri := _render_to_output_dir(
                "historical_pe",
                output_path,
                lambda: _historical_pe_png(historical_payload),
            )
        ) is not None:
            visuals["historical_pe"] = uri
    return visuals


__all__ = ["build_report_visuals"]
