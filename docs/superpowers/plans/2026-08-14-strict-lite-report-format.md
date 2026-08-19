# Strict-Lite Report Format Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不新增数据源、模型或 LLM 调用的前提下，把现有报告重排为清晰、约扩展 30% 有效正文的严格版精简报告。

**Architecture:** 继续以 `ReportContext` 为唯一数据对象。`renderer.py` 确定性生成章节、表格、判断与口径说明；`visuals.py` 只消费同一 Context 生成三张图；Report Agent 的九字段 Schema 和调用次数保持不变。

**Tech Stack:** Python 3.12、CrewAI 1.15.11、Pydantic、Decimal、Matplotlib、pytest、uv。

## Global Constraints

- 只扩展读者可见有效正文约 30%，不实现完整机构级模板。
- 不新增依赖、Crew、Agent、Task、LLM 调用、数据源或 fallback。
- 不修改 Flow、SEC/Yahoo 工具、Gate、估值公式或 Profile 路由。
- 不输出买入、卖出、持有、目标价或仓位建议。
- FY、YTD、TTM 和市场时点必须分开展示并明确标注。
- 缺少可选字段时隐藏对应行或显示“数据不足”，不得补造数据。
- 所有生产代码必须先有失败的行为测试，并记录 RED 与 GREEN 命令结果。
- 保留工作树中所有已有未提交改动，不得回滚或清理他人文件。

---

### Task 1: 确定性报告骨架与五年财务表

**Files:**
- Modify: `src/stockcrewai/reporting/renderer.py`
- Modify only if an existing field cannot express the table: `src/stockcrewai/reporting/context.py`
- Test: `tests/test_reporting_modules.py`

**Interfaces:**
- Consumes: `ReportContext.company`, `profile`, `horizon`, `metrics`, `annual_financial_history`, `annual_financial_summary`, `ttm`, `historical_valuation`, `reverse_dcf`, `source_metadata`.
- Produces: existing `render_validated_report(...) -> str`; no public signature change.
- Later tasks rely on headings `0. 封面与研究元数据` through `8. 数据来源、方法与技术附录` and on the five-year table being rendered from `annual_financial_history.periods`.

- [ ] **Step 1: Write failing report-contract tests**

Add behavior tests that render the real report fixture and assert:

```python
headings = [
    "## 0. 封面与研究元数据",
    "## 1. 一页结论",
    "## 2. 公司与研究范围",
    "## 3. 最新经营状态",
    "## 4. 历史经营与财务质量",
    "## 5. 估值",
    "## 6. 主要风险与监控条件",
    "## 7. 综合判断与重新评估条件",
    "## 8. 数据来源、方法与技术附录",
]
assert [report.index(value) for value in headings] == sorted(
    report.index(value) for value in headings
)
assert "| 公司名称 |" in report
assert "| 指标 | FY2021 | FY2022 | FY2023 | FY2024 | FY2025 |" in report
assert "收入 CAGR" in report
assert "TTM 数据与完整财年数据期间不同" in report
assert "status=ready" not in report.split("## 8. 数据来源", 1)[0]
```

The production break caught is a renderer that interleaves sections, omits the annual table, or leaks audit metadata into reader sections.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_reporting_modules.py -k "strict_lite or annual_financial_table"
```

Expected: FAIL because the numbered headings and annual table do not yet exist.

- [ ] **Step 3: Implement the minimal deterministic renderer helpers**

Add private helpers in `renderer.py` only as needed:

```python
def _research_metadata_markdown(context: Mapping[str, Any]) -> str: ...
def _annual_financial_table_markdown(context: Mapping[str, Any]) -> str: ...
def _decision_basis_markdown(context: Mapping[str, Any]) -> str: ...
def _scope_markdown(context: Mapping[str, Any]) -> str: ...
```

Use existing `_text`, `_currency_display`, `_percent_display`, `_formatted_metric_value`, and existing Context fields. Do not add a new model if the normalized periods already contain the required values. Render only available annual rows; do not insert invented zeros.

Reorder `render_validated_report` into the fixed structure while preserving the existing three image payloads and validator-compatible non-investment disclaimer.

- [ ] **Step 4: Run Task 1 tests and verify GREEN**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_reporting_modules.py tests/test_crew_configuration.py
git diff --check
```

Expected: all selected tests pass; no whitespace errors.

- [ ] **Step 5: Self-review scope**

Confirm the diff does not modify Flow, tools, Crew config, Gate logic or public report interfaces. Write RED/GREEN evidence and changed file list to the assigned task report.

---

### Task 2: 三张图表与标准图注

**Files:**
- Modify: `src/stockcrewai/reporting/visuals.py`
- Modify: `src/stockcrewai/reporting/renderer.py`
- Test: `tests/test_report_visuals.py`
- Test: `tests/test_reporting_modules.py`

**Interfaces:**
- Consumes: existing `build_report_visuals(report_context) -> dict[str, str]` and existing visual keys `financial_kpis`, `annual_financial_trend`, `historical_pe`.
- Produces: the same three keys and base64 PNG strings; no key or public signature changes.
- The annual trend image uses five complete FY periods normalized to first FY = 100 on one shared scale.

- [ ] **Step 1: Write failing visual behavior tests**

Add tests using the real visual builder and decoded PNG/figure metadata where supported. Assert the consumer-visible report contains:

```python
assert report.count("data:image/png;base64,") == 3
assert "图 1：最新经营质量" in report
assert "图 2：五年核心财务趋势指数" in report
assert "基期=100" in report
assert "图 3：五年历史 P/E" in report
assert "研究问题：" in report
assert "限制与反证：" in report
```

For the annual visual data transformation, assert literal normalized series for a hand-checked five-year fixture, such as a first value of `100.0` and a final value computed from the literal fixture ratio. The production break caught is reintroduction of independent absolute y-scales or missing chart context.

- [ ] **Step 2: Run tests and verify RED**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_report_visuals.py tests/test_reporting_modules.py -k "normalized or chart_caption or strict_lite"
```

Expected: FAIL because the annual chart is still absolute/multi-scale and standard captions are absent.

- [ ] **Step 3: Implement minimal normalized annual chart and captions**

Reuse the validated Decimal annual periods. Normalize each positive series with:

```python
indexed_value = current_value / first_value * Decimal("100")
```

Convert to float only at the Matplotlib boundary. Use one shared y-axis labelled `指数（首个财年=100）`; preserve the same `annual_financial_trend` output key. Do not create extra image files or dependencies.

In `renderer.py`, add deterministic chart framing with research question, period, unit, source, cutoff, observation, investment meaning and limitation. Do not claim causal reasons unless an existing validated Claim provides them.

- [ ] **Step 4: Run Task 2 tests and verify GREEN**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_report_visuals.py tests/test_reporting_modules.py
git diff --check
```

Expected: all selected tests pass and exactly three images remain.

- [ ] **Step 5: Render visual fixtures for manual QA**

Run the existing fixture/report visual path, decode the three generated PNGs into `/private/tmp/stockcrewai-report-qa/`, and inspect that labels are not clipped, legends do not overlap, and the annual chart shares one indexed scale. Do not add QA PNGs to the repository.

---

### Task 3: 风险监控表、综合判断与完整回归

**Files:**
- Modify: `src/stockcrewai/reporting/renderer.py`
- Test: `tests/test_reporting_modules.py`
- Test: `tests/test_crew_configuration.py`
- Test only if required by changed visuals: `tests/test_report_visuals.py`

**Interfaces:**
- Consumes: existing validated risk Claims and `_RISK_IMPACT_RULES` output.
- Produces: top-three Markdown risk table plus existing folded appendix; no Claim schema change.

- [ ] **Step 1: Write failing final-format tests**

Add behavior tests asserting:

```python
risk_section = report.split("## 6. 主要风险与监控条件", 1)[1].split(
    "## 7. 综合判断", 1
)[0]
assert "| 风险 | 影响路径 | 监控指标 | 来源 |" in risk_section
assert risk_section.count("<tr>") == 0  # Markdown only, no custom HTML table
assert "### 风险附录" in risk_section
assert "## 7. 综合判断与重新评估条件" in report
assert "买入" not in report.split("## 9. 非投资建议声明", 1)[0]
assert "卖出" not in report.split("## 9. 非投资建议声明", 1)[0]
assert "持有" not in report.split("## 9. 非投资建议声明", 1)[0]
```

Also assert the first three validated risk statements appear in the main table and the fourth appears only in the folded appendix. The production break caught is duplication, risk overflow, or advice leakage.

- [ ] **Step 2: Run tests and verify RED**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_reporting_modules.py -k "risk_monitoring_table or strict_lite_conclusion"
```

Expected: FAIL because risks are currently rendered as bullets rather than the specified table.

- [ ] **Step 3: Implement the minimal risk and conclusion rendering**

Change only the rendering shape. Reuse the existing deterministic risk classification; do not add probabilities or target-price impacts. Escape Markdown table delimiters in risk text and source labels. Keep remaining Claims inside the existing `<details>` appendix.

Render the final conclusion as four short blocks: verified facts, comparison, deterministic judgment, re-evaluation conditions. Move audit metadata and rules into section 8.

- [ ] **Step 4: Run focused and full offline verification**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_reporting_modules.py tests/test_crew_configuration.py tests/test_report_visuals.py
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q
git diff --check
```

Expected: zero failures; only existing CrewAI deprecation warnings are allowed.

- [ ] **Step 5: Run one live Apple report and inspect the artifact**

```bash
STOCKCREWAI_REQUEST='分析苹果公司 AAPL 是否值得投资，投资期限为3到5年' \
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache \
uv run --no-sync crewai run
```

Verify exit code 0, `run-result.json` has `status=ok` and `stage=report`, `investment-report.md` has the fixed chapter order and exactly three embedded PNGs, and FY/TTM values remain internally consistent.

- [ ] **Step 6: Record final evidence**

Write exact test counts, warnings, live-run status, artifact path and any remaining limitations in the assigned task report. Do not commit or push runtime artifacts unless explicitly requested.
