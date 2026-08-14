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

## Fix round 1：期间一致性

### RED

- 命令：`UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_report_visuals.py -k "mixed_period_basis or missing_period_basis"`
- 结果：`2 failed, 16 deselected`；混合 `period_basis` 及缺失 `period_basis`/`period_end`/`as_of` 时，修复前仍错误生成 `financial_kpis`。
- 图注行为测试在修复前也因图 1 使用硬编码“最新可用财务期间”而失败，未能从实际指标元数据生成期间。

### GREEN

- 命令：`UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_report_visuals.py tests/test_reporting_modules.py -k "mixed_period_basis or missing_period_basis or chart_captions"`
- 结果：`3 passed, 85 deselected, 2 subtests passed`。
- 命令：`UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_report_visuals.py tests/test_reporting_modules.py`
- 结果：`88 passed, 12 subtests passed in 6.76s`。
- 命令：`git diff --check`
- 结果：通过。

### 修复与 QA

- `financial_kpis` 在绘图前对所有实际使用指标校验统一且非空的 `period_basis`（或已有 `period`）以及一致的 `period_end`/`as_of`；缺失或混合时不生成图，不静默筛选。现有 normalized `ReportMetric` 没有 `period_basis` 字段，因此仅使用其已有 `as_of` 作为明确可比标记；原始记录仍要求显式期间字段，未修改公共 Context/Flow schema。
- 图 1 图注期间、截止日期和“数据不足”均由实际指标的共同元数据确定性生成；图注测试覆盖期间、单位、来源、截止、观察、投资含义、限制与反证，并验证不再出现硬编码最新期间。
- QA PNG 已重新生成至 `/private/tmp/stockcrewai-report-qa/`，未进入 Git：`financial_kpis.png` 1399×514、`annual_financial_trend.png` 855×658、`historical_pe.png` 768×423。目视观察：三图均成功输出；财务 KPI 标签和数值清晰、无裁切或重叠；五年趋势图为单一共享“首个财年=100”纵轴，图例、坐标轴和五个年度标签完整；P/E 图参考线、图例和最新点完整可见。

## Fix round 1 Commit

- `fix: validate chart period consistency`

## Fix round 1 Concerns

- 未继续进行非必要的字体、配色或刻度密度优化；当前修复聚焦期间口径一致性、标准图注和三张 PNG QA。
