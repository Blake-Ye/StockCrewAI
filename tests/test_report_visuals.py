import base64
from copy import deepcopy
from datetime import date, timedelta
import importlib
from io import BytesIO
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image


def _financial_metrics():
    values = {
        "revenue_growth": "0.20",
        "operating_margin": "0.25",
        "net_margin": "0.20",
        "free_cash_flow_margin": "0.15",
        "cash_conversion": "1.50",
        "share_dilution": "-0.02",
    }
    return [
        {
            "metric_id": metric_id,
            "display_value": value,
            "unit": "ratio",
            "status": "available",
            "validation_status": "valid",
            "calculation_id": f"calc_{metric_id}",
            "evidence_ids": [f"ev_{metric_id}"],
        }
        for metric_id, value in values.items()
    ]


def _ttm_metrics():
    values = {
        "revenue": "100000000000",
        "operating_income": "25000000000",
        "net_income": "20000000000",
        "operating_cash_flow": "30000000000",
        "free_cash_flow": "22000000000",
    }
    return [
        {
            "metric_id": metric_id,
            "raw_result": value,
            "unit": "USD",
            "status": "available",
            "validation_status": "valid",
            "calculation_id": f"calc_{metric_id}_ttm",
            "input_evidence_ids": [f"ev_{metric_id}_ttm"],
        }
        for metric_id, value in values.items()
    ]


def _historical_payload():
    start = date(2021, 8, 31)
    series = [
        {
            "date": (start + timedelta(days=30 * index)).isoformat(),
            "pe_ratio": f"{15 + (index % 12) / 10:.2f}",
        }
        for index in range(60)
    ]
    return {
        "status": "ok",
        "validation_status": "valid",
        "series": series,
        "current_date": series[-1]["date"],
    }


_LEGACY_VISUAL_KEYS = {"financial_kpis", "ttm_scale", "historical_pe"}
_QUANT_VISUAL_KEYS = {
    "quant_factor_percentile",
    "quant_cagr_comparison",
    "quant_drawdown_comparison",
}
_PNG_PREFIX = "data:image/png;base64,"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _quant_packet(
    *,
    percentile="0.8889",
    strategy_cagr="0.1234",
    spy_cagr="0.0800",
    universe_cagr="0.1000",
    strategy_drawdown="-0.2100",
    spy_drawdown="-0.1800",
    universe_drawdown="-0.2000",
    target_ticker="AAPL",
    peer_group="standard_operating:technology",
):
    return {
        "ranking_summary": {
            "target_ticker": target_ticker,
            "peer_group": peer_group,
            "industry_percentile": percentile,
        },
        "backtest_summary": {
            "strategy_cagr": strategy_cagr,
            "strategy_cagr_status": "available",
            "strategy_max_drawdown": strategy_drawdown,
            "strategy_max_drawdown_status": "available",
        },
        "benchmark_summary": {
            "spy_cagr": spy_cagr,
            "universe_cagr": universe_cagr,
            "spy_max_drawdown": spy_drawdown,
            "universe_max_drawdown": universe_drawdown,
        },
    }


def _available_quant_context(**packet_kwargs):
    return {
        "metrics": _financial_metrics(),
        "ttm": _ttm_metrics(),
        "historical_valuation": _historical_payload(),
        "quant": {
            "status": "available",
            "reason_code": "quant_packet_validated",
            "packet": _quant_packet(**packet_kwargs),
        },
    }


class ReportVisualsTests(unittest.TestCase):
    def _builder(self):
        try:
            legacy = importlib.import_module("stockcrewai.report_visuals")
            module = importlib.import_module("stockcrewai.reporting.visuals")
        except ImportError:
            module = None
        self.assertIsNotNone(module, "report_visuals 模块必须存在")
        builder = getattr(module, "build_report_visuals", None)
        self.assertIsNotNone(builder, "必须提供 build_report_visuals")
        self.assertIs(legacy.build_report_visuals, builder)
        return builder

    def test_financial_kpi_percentage_labels_render_outside_bars(self):
        module = importlib.import_module("stockcrewai.reporting.visuals")
        values = {
            "revenue_growth": 16.15,
            "operating_margin": 33.60,
            "net_margin": 27.85,
            "free_cash_flow_margin": 30.24,
            "cash_conversion": 115.31,
            "share_dilution": -1.67,
        }
        records = {
            metric_id: {
                "display_value": f"{value:.2f}%",
                "unit": "percentage",
            }
            for metric_id, value in values.items()
        }
        rendered = {}

        def inspect_png_uri(draw, *, size, **kwargs):
            figure, axes = module.plt.subplots(figsize=size, dpi=120)
            try:
                draw(axes)
                figure.canvas.draw()
                renderer = figure.canvas.get_renderer()
                percentage_texts = [
                    text for text in axes.texts if text.get_text().endswith("%")
                ]
                rendered["labels"] = [
                    text.get_text() for text in percentage_texts
                ]
                rendered["texts"] = percentage_texts
                rendered["extents"] = [
                    text.get_window_extent(renderer) for text in percentage_texts
                ]
                rendered["bars"] = [
                    bar.get_window_extent(renderer) for bar in axes.patches
                ]
                rendered["axes_bbox"] = axes.bbox
                rendered["zero_x"] = axes.transData.transform((0, 0))[0]
                return "captured"
            finally:
                module.plt.close(figure)

        with patch.object(module, "_png_uri", side_effect=inspect_png_uri):
            self.assertEqual(module._financial_kpi_png(records), "captured")

        self.assertEqual(len(rendered["labels"]), 6)
        self.assertEqual(
            rendered["labels"],
            ["16.15%", "33.60%", "27.85%", "30.24%", "115.31%", "-1.67%"],
        )
        axes_bbox = rendered["axes_bbox"]
        for label, extent in zip(rendered["labels"], rendered["extents"]):
            with self.subTest(label=label):
                self.assertGreaterEqual(extent.x0, axes_bbox.x0)
                self.assertLessEqual(extent.x1, axes_bbox.x1)

        by_label = {
            label: (text, extent, bar)
            for label, text, extent, bar in zip(
                rendered["labels"],
                rendered["texts"],
                rendered["extents"],
                rendered["bars"],
            )
        }

        maximum_text, maximum_extent, maximum_bar = by_label["115.31%"]
        self.assertGreater(maximum_extent.x0, maximum_bar.x1)
        self.assertGreaterEqual(
            axes_bbox.x1 - maximum_extent.x1,
            axes_bbox.width * 0.05,
        )
        self.assertEqual(maximum_text.get_color(), "black")

        negative_text, negative_extent, _ = by_label["-1.67%"]
        self.assertGreater(negative_extent.x0, rendered["zero_x"])
        self.assertNotEqual(negative_text.get_color(), "white")

        for label in ("16.15%", "33.60%", "27.85%", "30.24%"):
            _, extent, bar = by_label[label]
            self.assertGreater(extent.x0, bar.x1)

    def test_financial_kpi_axis_padding_follows_real_data_range(self):
        module = importlib.import_module("stockcrewai.reporting.visuals")

        def render(values):
            records = {
                metric_id: {
                    "display_value": f"{value:.2f}%",
                    "unit": "percentage",
                }
                for metric_id, value in values.items()
            }
            uri = module._financial_kpi_png(records)
            self.assertIsNotNone(uri)
            image = Image.open(
                BytesIO(base64.b64decode(uri.split(",", 1)[1]))
            ).convert("RGB")

            black = (0, 0, 0)
            gray = (85, 85, 85)
            blue = (53, 104, 168)
            axis_columns = [
                x
                for x in range(image.width)
                if sum(
                    image.getpixel((x, y)) == black
                    for y in range(image.height)
                )
                > image.height // 2
            ]
            self.assertGreaterEqual(len(axis_columns), 2)
            axes_left, axes_right = min(axis_columns), max(axis_columns)
            zero_columns = [
                x
                for x in range(image.width)
                if sum(
                    image.getpixel((x, y)) == gray
                    for y in range(image.height)
                )
                > image.height // 2
            ]
            self.assertEqual(len(zero_columns), 1)

            blue_rows = [
                y
                for y in range(image.height)
                if any(
                    image.getpixel((x, y)) == blue
                    for x in range(image.width)
                )
            ]
            row_groups = []
            group_start = previous_row = blue_rows[0]
            for row in blue_rows[1:]:
                if row != previous_row + 1:
                    row_groups.append((group_start, previous_row))
                    group_start = row
                previous_row = row
            row_groups.append((group_start, previous_row))
            self.assertEqual(len(row_groups), len(module._FINANCIAL_KPI_IDS))

            negative_left = min(
                x
                for y in range(row_groups[-1][0], row_groups[-1][1] + 1)
                for x in range(image.width)
                if image.getpixel((x, y)) == blue
            )
            axis_width = axes_right - axes_left
            return {
                "zero_fraction": (zero_columns[0] - axes_left) / axis_width,
                "negative_left_fraction": (negative_left - axes_left)
                / axis_width,
            }

        negative_values = {
            "revenue_growth": 10.0,
            "operating_margin": 20.0,
            "net_margin": 30.0,
            "free_cash_flow_margin": 40.0,
            "cash_conversion": 50.0,
            "share_dilution": -5.0,
        }
        negative_chart = render(negative_values)
        expected_negative_xlim = (-16.0, 61.0)
        expected_negative_zero_fraction = (
            -expected_negative_xlim[0]
            / (expected_negative_xlim[1] - expected_negative_xlim[0])
        )
        self.assertAlmostEqual(
            negative_chart["zero_fraction"],
            expected_negative_zero_fraction,
            delta=0.01,
        )
        self.assertGreaterEqual(negative_chart["negative_left_fraction"], 0.14)

        positive_values = {**negative_values, "share_dilution": 5.0}
        positive_chart = render(positive_values)
        expected_positive_xlim = (-5.0, 60.0)
        expected_positive_zero_fraction = (
            -expected_positive_xlim[0]
            / (expected_positive_xlim[1] - expected_positive_xlim[0])
        )
        self.assertAlmostEqual(
            positive_chart["zero_fraction"],
            expected_positive_zero_fraction,
            delta=0.01,
        )
        self.assertLess(
            positive_chart["zero_fraction"], negative_chart["zero_fraction"]
        )

    def test_builds_three_deterministic_png_data_uris_from_verified_inputs(self):
        builder = self._builder()

        visuals = builder(
            financial_metrics=_financial_metrics(),
            ttm_metrics=_ttm_metrics(),
            historical_payload=_historical_payload(),
        )

        self.assertEqual(
            set(visuals), {"financial_kpis", "ttm_scale", "historical_pe"}
        )
        for uri in visuals.values():
            self.assertTrue(uri.startswith("data:image/png;base64,"))
            self.assertTrue(
                base64.b64decode(uri.split(",", 1)[1]).startswith(b"\x89PNG\r\n\x1a\n")
            )

        self.assertEqual(
            visuals,
            builder(
                financial_metrics=_financial_metrics(),
                ttm_metrics=_ttm_metrics(),
                historical_payload=_historical_payload(),
            ),
        )

    def _assert_png_uris(self, visuals):
        for key, uri in visuals.items():
            with self.subTest(key=key):
                self.assertTrue(uri.startswith(_PNG_PREFIX))
                encoded = uri.split(",", 1)[1]
                payload = base64.b64decode(encoded, validate=True)
                self.assertTrue(payload.startswith(_PNG_SIGNATURE))
                with Image.open(BytesIO(payload)) as image:
                    image.load()
                    self.assertGreaterEqual(image.width, 720)
                    self.assertGreaterEqual(image.height, 360)
                    ratio = image.width / image.height
                    self.assertGreaterEqual(ratio, 1.2)
                    self.assertLessEqual(ratio, 3.5)

    def test_available_quant_adds_three_pngs_without_changing_legacy_uris(self):
        builder = self._builder()
        baseline = builder(
            financial_metrics=_financial_metrics(),
            ttm_metrics=_ttm_metrics(),
            historical_payload=_historical_payload(),
        )
        visuals = builder(context=_available_quant_context())

        self.assertEqual(set(baseline), _LEGACY_VISUAL_KEYS)
        self.assertEqual(set(visuals), _LEGACY_VISUAL_KEYS | _QUANT_VISUAL_KEYS)
        for key in _LEGACY_VISUAL_KEYS:
            self.assertEqual(visuals[key], baseline[key])
        self._assert_png_uris(visuals)

    def test_unavailable_quant_inputs_preserve_legacy_visuals_without_zero_fill(self):
        builder = self._builder()
        baseline = builder(
            financial_metrics=_financial_metrics(),
            ttm_metrics=_ttm_metrics(),
            historical_payload=_historical_payload(),
        )
        available_context = _available_quant_context()
        contexts = {
            "no_quant": {
                key: value
                for key, value in available_context.items()
                if key != "quant"
            },
            "unavailable_status": {
                **deepcopy(available_context),
                "quant": {
                    "status": "unavailable",
                    "reason_code": "quant_packet_unavailable",
                    "packet": deepcopy(available_context["quant"]["packet"]),
                },
            },
            "packet_none": {
                **deepcopy(available_context),
                "quant": {"status": "available", "packet": None},
            },
            "packet_non_mapping": {
                **deepcopy(available_context),
                "quant": {"status": "available", "packet": ["invalid"]},
            },
        }

        for name, context in contexts.items():
            with self.subTest(name=name):
                self.assertEqual(builder(context=context), baseline)

        missing_metric_cases = (
            ("quant_factor_percentile", ("ranking_summary", "industry_percentile")),
            ("quant_cagr_comparison", ("backtest_summary", "strategy_cagr")),
            (
                "quant_drawdown_comparison",
                ("backtest_summary", "strategy_max_drawdown"),
            ),
        )
        for chart_key, (section, field) in missing_metric_cases:
            context = _available_quant_context()
            context["quant"]["packet"][section][field] = None
            visuals = builder(context=context)
            with self.subTest(chart_key=chart_key):
                self.assertNotIn(chart_key, visuals)
                self.assertEqual(
                    set(visuals),
                    _LEGACY_VISUAL_KEYS | (_QUANT_VISUAL_KEYS - {chart_key}),
                )

    def test_quant_png_output_dir_is_scoped_and_embedded_uris_survive_cleanup(self):
        builder = self._builder()
        project_pngs_before = {
            path.resolve() for path in Path.cwd().rglob("*.png")
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            output_dir = tmp_path / "charts"
            visuals = builder(
                context=_available_quant_context(),
                output_dir=output_dir,
            )

            generated_pngs = {
                path.resolve() for path in output_dir.rglob("*.png")
            }
            self.assertEqual(len(visuals), 6)
            self.assertTrue(generated_pngs)
            self.assertTrue(
                all(
                    path.is_relative_to(output_dir.resolve())
                    for path in generated_pngs
                )
            )
            for path in generated_pngs:
                self.assertTrue(path.read_bytes().startswith(_PNG_SIGNATURE))
                with Image.open(path) as image:
                    image.load()
                    self.assertGreaterEqual(image.width, 720)
                    self.assertGreaterEqual(image.height, 360)
            self.assertEqual(
                {path.resolve() for path in Path.cwd().rglob("*.png")},
                project_pngs_before,
            )

            shutil.rmtree(output_dir)
            self.assertFalse(output_dir.exists())
            self._assert_png_uris(visuals)

    def test_extreme_quant_values_use_agg_and_keep_zero_data_and_padding(self):
        module = importlib.import_module("stockcrewai.reporting.visuals")
        builder = self._builder()
        self.assertEqual(module.plt.get_backend().lower(), "agg")
        captured = []

        def inspect_png_uri(draw, *, size, **kwargs):
            figure, axes = module.plt.subplots(figsize=size, dpi=120)
            try:
                draw(axes)
                figure.canvas.draw()
                captured.append((axes.get_xlim(), axes.get_ylim()))
                return "captured"
            finally:
                module.plt.close(figure)

        for percentile in ("0", "1"):
            captured.clear()
            context = _available_quant_context(
                percentile=percentile,
                strategy_cagr="-0.99",
                spy_cagr="2.5",
                universe_cagr="0.1",
                strategy_drawdown="-0.001",
                spy_drawdown="-0.5",
                universe_drawdown="-0.99",
            )
            with patch.object(module, "_png_uri", side_effect=inspect_png_uri):
                visuals = builder(context={"quant": context["quant"]})

            with self.subTest(percentile=percentile):
                self.assertEqual(set(visuals), _QUANT_VISUAL_KEYS)
                self.assertEqual(len(captured), 3)
                expected_values = (
                    (0.0, 1.0),
                    (-0.99, 2.5, 0.1),
                    (-0.001, -0.5, -0.99),
                )
                for (xlim, ylim), values in zip(captured, expected_values):
                    matching_ranges = []
                    for bounds in (xlim, ylim):
                        low, high = sorted(bounds)
                        if low <= min(values) and high >= max(values):
                            matching_ranges.append((low, high))
                    self.assertEqual(len(matching_ranges), 1)
                    low, high = matching_ranges[0]
                    self.assertLess(low, min(values))
                    self.assertGreater(high, max(values))
                    self.assertLess(low, 0)
                    self.assertGreater(high, 0)

    def test_long_quant_labels_fit_figure_bbox(self):
        module = importlib.import_module("stockcrewai.reporting.visuals")
        builder = self._builder()
        captured = []

        def inspect_png_uri(draw, *, size, **kwargs):
            figure, axes = module.plt.subplots(figsize=size, dpi=120)
            try:
                draw(axes)
                figure.canvas.draw()
                renderer = figure.canvas.get_renderer()
                figure_bbox = figure.get_window_extent(renderer)
                axes_bbox = axes.get_window_extent(renderer)
                artists = [
                    *axes.texts,
                    *axes.get_xticklabels(),
                    *axes.get_yticklabels(),
                    axes.title,
                    axes.xaxis.label,
                    axes.yaxis.label,
                ]
                legend = axes.get_legend()
                if legend is not None:
                    artists.extend(legend.get_texts())
                extents = [
                    artist.get_window_extent(renderer)
                    for artist in artists
                    if artist.get_visible() and artist.get_text()
                ]
                captured.append(
                    (
                        figure_bbox,
                        axes_bbox,
                        extents,
                        [artist.get_text() for artist in artists if artist.get_text()],
                    )
                )
                return "captured"
            finally:
                module.plt.close(figure)

        target_ticker = "超长中文股票代码-TICKER-ABCDEFGHIJKLMN"
        peer_group = "标准经营企业-科技行业-超长同行分组标签-2026"
        context = _available_quant_context(
            target_ticker=target_ticker,
            peer_group=peer_group,
        )
        with patch.object(module, "_png_uri", side_effect=inspect_png_uri):
            visuals = builder(context={"quant": context["quant"]})

        self.assertEqual(set(visuals), _QUANT_VISUAL_KEYS)
        self.assertEqual(len(captured), 3)
        rendered_text = "\n".join(
            text for _, _, _, texts in captured for text in texts
        )
        self.assertIn(target_ticker, rendered_text)
        self.assertIn(peer_group, rendered_text)
        for figure_bbox, axes_bbox, extents, _ in captured:
            with self.subTest(figure_bbox=figure_bbox):
                self.assertGreaterEqual(axes_bbox.x0, figure_bbox.x0)
                self.assertLessEqual(axes_bbox.x1, figure_bbox.x1)
                self.assertGreaterEqual(axes_bbox.y0, figure_bbox.y0)
                self.assertLessEqual(axes_bbox.y1, figure_bbox.y1)
                for extent in extents:
                    self.assertGreaterEqual(extent.x0, figure_bbox.x0 - 2)
                    self.assertLessEqual(extent.x1, figure_bbox.x1 + 2)
                    self.assertGreaterEqual(extent.y0, figure_bbox.y0 - 2)
                    self.assertLessEqual(extent.y1, figure_bbox.y1 + 2)

    def test_missing_input_omits_only_the_corresponding_chart(self):
        builder = self._builder()

        visuals = builder(
            financial_metrics=_financial_metrics(),
            ttm_metrics=_ttm_metrics(),
        )

        self.assertEqual(set(visuals), {"financial_kpis", "ttm_scale"})

    def test_missing_verification_fields_omit_all_charts(self):
        builder = self._builder()
        financial_metrics = _financial_metrics()
        ttm_metrics = _ttm_metrics()
        historical_payload = _historical_payload()

        for records in (financial_metrics, ttm_metrics):
            for record in records:
                record.pop("status")
                record.pop("validation_status")
        historical_payload.pop("status")
        historical_payload.pop("validation_status")

        self.assertEqual(
            builder(
                financial_metrics=financial_metrics,
                ttm_metrics=ttm_metrics,
                historical_payload=historical_payload,
            ),
            {},
        )

    def test_historical_pe_requires_top_level_verification_fields(self):
        builder = self._builder()

        for field in ("status", "validation_status"):
            with self.subTest(field=field):
                historical_payload = _historical_payload()
                historical_payload.pop(field)

                visuals = builder(
                    financial_metrics=_financial_metrics(),
                    ttm_metrics=_ttm_metrics(),
                    historical_payload=historical_payload,
                )

                self.assertEqual(set(visuals), {"financial_kpis", "ttm_scale"})

    def test_matplotlib_uses_agg_and_writable_project_temp_config(self):
        self._builder()
        import matplotlib

        self.assertEqual(matplotlib.get_backend().lower(), "agg")
        config_dir = Path(os.environ["MPLCONFIGDIR"])
        self.assertEqual(config_dir.parent, Path(tempfile.gettempdir()))
        self.assertTrue(config_dir.name.startswith("stockcrewai"))
        self.assertTrue(os.access(config_dir, os.W_OK))

    def test_amount_conversion_uses_actual_usd_billions(self):
        module = importlib.import_module("stockcrewai.reporting.visuals")
        converter = getattr(module, "_amount_in_billion_usd")

        self.assertAlmostEqual(
            converter({"raw_result": "466823000000", "unit": "USD"}),
            466.823,
            places=3,
        )
        self.assertAlmostEqual(
            converter({"raw_result": "466823", "unit": "million USD"}),
            466.823,
            places=3,
        )
        self.assertAlmostEqual(
            converter({"raw_result": "4668.23", "unit": "亿美元"}),
            466.823,
            places=3,
        )

    def test_chart_labels_and_historical_ticks_are_reader_friendly(self):
        module = importlib.import_module("stockcrewai.reporting.visuals")

        self.assertEqual(module._FINANCIAL_KPI_TITLE, "财务质量指标（已验证数据）")
        self.assertEqual(module._TTM_AXIS_LABEL, "金额（十亿美元）")

        points = [
            (
                date(2021 + index // 12, index % 12 + 1, 1).isoformat(),
                float(index),
            )
            for index in range(60)
        ]
        indices, labels = module._historical_tick_data(points)
        self.assertEqual(indices, [0, 12, 24, 36, 48, 59])
        self.assertEqual(
            labels,
            ["2021-01", "2022-01", "2023-01", "2024-01", "2025-01", "2025-12"],
        )
