# Task 2 完成报告：三张图表与标准图注

## RED

- 命令：`UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_report_visuals.py tests/test_reporting_modules.py -k "normalized or chart_caption or strict_lite"`
- 结果：`1 failed, 4 passed, 81 deselected`；报告端标准图注断言因缺少“图 1”标题失败。
- 独立年度行为测试：`tests/test_report_visuals.py::ReportVisualsTests::test_annual_trend_normalizes_series_on_one_shared_index_axis` 以 `AssertionError: 3 != 1` 失败，确认原实现仍是三块独立纵轴绝对金额子图。

## GREEN

- 命令：`UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_report_visuals.py tests/test_reporting_modules.py -k "normalized or chart_caption or strict_lite"`
- 结果：`5 passed, 81 deselected`。
- 命令：`UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_report_visuals.py tests/test_reporting_modules.py`
- 结果：`86 passed, 10 subtests passed`。
- 命令：`git diff --check`
- 结果：通过。

## 实现与 QA

- 年度趋势图保留 `annual_financial_trend` key 和 base64 PNG 输出，使用已验证 Decimal 年度值归一化为首年 100；首年非正或无效时不生成该图；Matplotlib 边界才转换为 float。
- 报告保留三张图和既有 visual keys，并从确定性 Context 生成图 1/2/3、研究问题、期间、单位、来源、截止、观察、投资含义及限制与反证；缺失值使用“数据不足”。
- QA PNG 路径：`/private/tmp/stockcrewai-report-qa/`（未进入 Git）。
- QA 观察：`financial_kpis.png` 1399×514、`annual_financial_trend.png` 855×658、`historical_pe.png` 768×423；标签未见裁切，图例未见重叠，年度图为单一共享指数纵轴。

## 修改文件

- `src/stockcrewai/reporting/visuals.py`
- `src/stockcrewai/reporting/renderer.py`
- `tests/test_report_visuals.py`
- `tests/test_reporting_modules.py`

## Commit

- `feat: standardize report charts and captions`

## Concerns

- 未继续调整非必要的字体、配色和历史图刻度密度；当前聚焦测试与 QA 未发现影响本任务要求的问题。
