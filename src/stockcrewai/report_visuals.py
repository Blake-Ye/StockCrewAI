from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
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

import matplotlib

matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
from matplotlib import font_manager


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


def _png_uri(draw: Callable[[Any], None], *, size: tuple[float, float]) -> str:
    figure, axes = plt.subplots(figsize=size, dpi=120)
    try:
        draw(axes)
        buffer = BytesIO()
        figure.savefig(
            buffer,
            format="png",
            bbox_inches="tight",
            metadata={"Software": "StockCrewAI"},
        )
        return _PNG_PREFIX + base64.b64encode(buffer.getvalue()).decode("ascii")
    finally:
        plt.close(figure)


def _financial_kpi_png(records: Mapping[str, Mapping[str, Any]]) -> str | None:
    values = [_ratio_value(records[metric_id]) for metric_id in _FINANCIAL_KPI_IDS]
    if any(value is None for value in values):
        return None

    def draw(axes: Any) -> None:
        bars = axes.barh(
            [_FINANCIAL_KPI_LABELS[metric_id] for metric_id in _FINANCIAL_KPI_IDS],
            values,
            color="#3568a8",
        )
        data_min = min(0.0, min(values))
        data_max = max(0.0, max(values))
        span = max(data_max - data_min, 1.0)
        left_padding = span * (0.20 if data_min < 0 else 0.10)
        right_padding = span * 0.10
        axes.set_xlim(data_min - left_padding, data_max + right_padding)
        axes.axvline(0, color="#555555", linewidth=0.8)
        axes.set_xlabel("百分比（%）")
        axes.set_title(_FINANCIAL_KPI_TITLE)
        axes.grid(axis="x", alpha=0.25)
        axes.invert_yaxis()
        for metric_id, bar, value in zip(_FINANCIAL_KPI_IDS, bars, values):
            if metric_id == "cash_conversion":
                axes.text(
                    value - 0.5,
                    bar.get_y() + bar.get_height() / 2,
                    f"{value:.2f}%",
                    va="center",
                    ha="right",
                    color="white",
                    fontsize=8,
                )
            else:
                axes.text(
                    max(value + 0.5, 0.5) if value < 0 else value + 0.5,
                    bar.get_y() + bar.get_height() / 2,
                    f"{value:.2f}%",
                    va="center",
                    ha="left",
                    fontsize=8,
                )

    return _png_uri(draw, size=(8.0, 4.2))


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


def _percentile(values: list[float], fraction: Decimal) -> float:
    position = (len(values) - 1) * float(fraction)
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    weight = position - lower
    return values[lower] + (values[upper] - values[lower]) * weight


def _historical_tick_data(
    points: Sequence[tuple[str, float]],
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
    if not isinstance(raw_series, Sequence) or isinstance(raw_series, (str, bytes)):
        return None
    if not isinstance(current_date, str) or not current_date.strip():
        return None

    points: list[tuple[str, float]] = []
    for item in raw_series:
        if not isinstance(item, Mapping):
            continue
        point_date = item.get("date")
        value = _decimal(item.get("pe_ratio", item.get("value")))
        if isinstance(point_date, str) and point_date.strip() and value is not None:
            points.append((point_date.strip(), float(value)))
    points.sort(key=lambda item: item[0])
    if len(points) < 60:
        return None
    points = points[-60:]
    if points[-1][0] != current_date.strip():
        return None
    values = [value for _, value in points]
    percentile_25 = _percentile(values, Decimal("0.25"))
    median = _percentile(values, Decimal("0.50"))
    percentile_75 = _percentile(values, Decimal("0.75"))

    def draw(axes: Any) -> None:
        x_values = list(range(len(points)))
        tick_indices, tick_labels = _historical_tick_data(points)
        axes.plot(x_values, values, color="#3568a8", linewidth=1.5, label="P/E（倍）")
        axes.axhline(
            percentile_25,
            color="#d97706",
            linestyle="--",
            label=f"25分位 {percentile_25:.2f}x",
        )
        axes.axhline(
            median,
            color="#555555",
            linestyle="--",
            label=f"中位数 {median:.2f}x",
        )
        axes.axhline(
            percentile_75,
            color="#b91c1c",
            linestyle="--",
            label=f"75分位 {percentile_75:.2f}x",
        )
        axes.scatter([x_values[-1]], [values[-1]], color="#111827", zorder=3, label="最新")
        axes.annotate(
            f"最新 {values[-1]:.2f}x",
            (x_values[-1], values[-1]),
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

    return _png_uri(draw, size=(8.0, 4.2))


def build_report_visuals(
    financial_metrics: Any = None,
    ttm_metrics: Any = None,
    historical_payload: Mapping[str, Any] | None = None,
    *,
    calculations: Any = None,
    ttm: Any = None,
    historical_valuation: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
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

    financial_records = {
        metric_id: record
        for record in _records(
            financial_metrics, collection_keys=("metrics", "calculations")
        )
        if (metric_id := _record_id(record)) in _FINANCIAL_KPI_IDS and _is_verified(record)
    }
    ttm_records = {
        metric_id: record
        for record in _records(ttm_metrics, collection_keys=("metrics", "calculations"))
        if (metric_id := _record_id(record)) in _TTM_IDS and _is_verified(record)
    }
    visuals: dict[str, str] = {}
    if len(financial_records) == len(_FINANCIAL_KPI_IDS):
        if (uri := _financial_kpi_png(financial_records)) is not None:
            visuals["financial_kpis"] = uri
    if len(ttm_records) == len(_TTM_IDS):
        if (uri := _ttm_png(ttm_records)) is not None:
            visuals["ttm_scale"] = uri
    if isinstance(historical_payload, Mapping):
        if (uri := _historical_pe_png(historical_payload)) is not None:
            visuals["historical_pe"] = uri
    return visuals


__all__ = ["build_report_visuals"]
