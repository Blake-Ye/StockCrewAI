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

## Fix round 2：保留 period_basis

### RED

- 命令：`UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_report_visuals.py tests/test_reporting_modules.py -k "mixed_period_basis or same_as_of_basis_is_ytd or normalized_financial_kpis or report_metric_preserves_calculation_period_basis or strict_lite_chart_captions"`
- 结果：`4 failed, 2 passed, 86 deselected`；修复前确认 Context 丢失输入 `YTD`，normalized 指标缺 basis 仍生成图，缺 basis 图注仍将 `as_of` 当作期间口径，标准图注不能显示共同 `FY`。
- 为验证“截止日期独立于 basis”追加断言后：`UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_reporting_modules.py -k "normalized_financial_kpis_without_basis"` 结果 `1 failed, 71 deselected`，修复前截止显示为“数据不足”。

### GREEN

- 聚焦修复后：`6 passed, 86 deselected`；截止日期断言修复后：`1 passed, 71 deselected`。
- 命令：`UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_report_visuals.py tests/test_reporting_modules.py`
- 结果：`92 passed, 12 subtests passed in 6.81s`。
- 命令：`git diff --check`
- 结果：通过。

### 修复、schema 兼容与 QA

- `ReportMetric` 新增可选 `period_basis: StrictStr | None`；输入计算 payload 的已有 `period_basis` 以及 Evidence 元数据中的已有 `period_basis` 会被原样保留，缺失时保持空值并在 JSON 中省略；不根据 `as_of` 或日期格式猜测口径。
- `financial_kpis` 删除 `as_of` 作为 basis 的兜底；所有实际绘制指标必须有相同明确 `period_basis`/`period`，且 `period_end`/`as_of` 时点签名一致，否则整图不生成。图 1 期间缺 basis 输出“数据不足”；截止日期仍可从共同 `as_of`/`period_end` 独立确定。
- 标准 `_reader_focused_inputs()` fixture 补齐 `period_basis=FY`，真实 normalized Context 仍生成 exactly 三张图；未修改 Flow、tools、Gate 或 Crew。
- QA PNG 路径：`/private/tmp/stockcrewai-report-qa/`（未进入 Git）。重新生成结果为 `annual_financial_trend`、`financial_kpis`、`historical_pe` 三个 key；尺寸分别为 855×658、1399×514、768×423。目视观察：财务 KPI 三面板标签/数值清晰无裁切重叠；五年图保持单一共享指数纵轴、首年=100、图例和年度标签完整；P/E 图参考线、图例和最新点完整可见。

## Fix round 2 修改文件

- `src/stockcrewai/reporting/context.py`
- `src/stockcrewai/reporting/visuals.py`
- `src/stockcrewai/reporting/renderer.py`
- `tests/test_report_visuals.py`
- `tests/test_reporting_modules.py`

## Fix round 2 Commit

- `fix: preserve report metric period basis`

## Fix round 2 Concerns

- 未扩展公开 schema 以外的字段，也未进行非必要视觉优化；旧输入缺少 `period_basis` 时按不可比较处理，避免以日期或 `as_of` 猜测 FY/YTD/TTM。

## Fix round 3：evidence period basis 一致性

### RED

- 命令：`UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_reporting_modules.py -k "evidence_period_basis or calculation_period_basis"`
- 结果：`2 failed, 2 passed, 71 deselected`；混合 `FY/TTM` 及 `YTD/缺失` 时，修复前仍返回第一个 evidence 的 basis，证明顺序会影响结果。

### GREEN

- 聚焦命令同上：`4 passed, 71 deselected`，覆盖两证据同 `YTD`、`FY+TTM` 混合、`YTD+缺失`，以及 calculation 自带 `YTD`。
- 命令：`UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_reporting_modules.py tests/test_report_visuals.py`
- 结果：`95 passed, 12 subtests passed in 6.88s`。
- 命令：`git diff --check`
- 结果：通过。

### 修复与范围

- `_evidence_period_basis` 现在仅在每个 input evidence 都有非空 `period_basis` 且唯一集合大小为 1 时返回共同值；缺失或混合均返回 `None`。calculation 自带明确 basis 仍优先原样保留；不使用 first/nonempty fallback，不从日期或 `as_of` 推断。
- 本轮仅修改 `src/stockcrewai/reporting/context.py` 与 `tests/test_reporting_modules.py`；未修改图表生产代码，既有 `/private/tmp/stockcrewai-report-qa/` PNG 无需重生成。

## Fix round 3 Commit

- `fix: require consistent evidence period basis`

## Fix round 3 Concerns

- 无新增 concern；本轮严格限制为 evidence period basis 判据与真实 Context 行为测试。
