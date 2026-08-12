# Single-Call Chart Reasoning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让现有唯一一次 Report Agent 调用基于确定性的图表关系事实，生成“我们可以看到—这说明—由此判断”的中文推导，并由 Renderer 将推导放在对应图片后。

**Architecture:** Python 继续拥有数字、比较关系、图片和最终 Verdict。`build_narrative_context()` 在同一份输入中新增无数字的 `chart_context`；Report Agent 仍输出原有九字段无数字 `ReportDraft`；Renderer 根据图片是否真实生成，将三个既有字段分别渲染为“图表推导”或“数据解读”。不增加 Agent、Task、Crew kickoff 或 LLM 调用。

**Tech Stack:** Python 3.12、CrewAI 1.15.11、Pydantic、Pytest、YAML。

## Global Constraints

- Report Crew 每轮仍只调用一次，`report_crew.kickoff_calls == 1`。
- 不修改 `flow.py`、`crew.py`、`agents.yaml`、`validator.py`、`visuals.py` 或 ReportDraft 九字段 Schema。
- 不允许 Report Agent 输出阿拉伯数字、Claim ID、评级、status、买卖建议或自行计算。
- Python 只向 Agent 提供由已验证 ReportContext 确定的无数字关系事实。
- 图片、精确数字、日期、来源和最终 Verdict 继续由确定性 Python 注入。
- 不新增依赖，不升级 CrewAI，不修改 PNG 内容或图表计算。
- 当前工作区包含大量用户修改；不得清理、还原或覆盖任何无关改动，不得提交 Git。

---

### Task 1: 单次 Report Agent 图表推导

**Files:**
- Modify: `src/stockcrewai/reporting/renderer.py`
- Modify: `src/stockcrewai/crews/report/config/tasks.yaml`
- Test: `tests/test_reporting_modules.py`
- Test: `tests/test_crew_configuration.py`

**Interfaces:**
- Consumes: `build_narrative_context(report_context: Mapping[str, Any], max_bytes: int = 24 * 1024) -> dict[str, Any]`
- Produces: `narrative_context["chart_context"]`，固定包含 `financial_kpis`、`ttm_scale`、`historical_pe` 三个键；每项只含 `available: bool` 和 `observations: list[str]`。
- Preserves: `ReportDraft` 原九字段；`company_quality`、`financial_trend`、`historical_valuation` 作为三张图的自然语言推导。

- [ ] **Step 1: 写 narrative_context RED 测试**

在 `tests/test_reporting_modules.py` 增加测试，使用 `_reader_focused_inputs()` 构建 Context，并断言：

```python
narrative = build_narrative_context(build_report_context(**_reader_focused_inputs()))
chart_context = narrative["chart_context"]
assert tuple(chart_context) == ("financial_kpis", "ttm_scale", "historical_pe")
assert chart_context["financial_kpis"]["available"] is True
assert "收入同比保持正增长" in chart_context["financial_kpis"]["observations"]
assert "经营现金流高于净利润" in chart_context["ttm_scale"]["observations"]
assert "当前市盈率高于五年中位数" in chart_context["historical_pe"]["observations"]
assert not re.search(r"[0-9]", json.dumps(chart_context, ensure_ascii=False))
```

再增加缺失输入测试：删除对应指标或把历史估值设为 unavailable 后，相关项必须是 `available=False` 且 `observations=[]`，不得猜测。

- [ ] **Step 2: 运行 RED 测试**

运行：

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q \
  tests/test_reporting_modules.py -k 'chart_context'
```

预期：FAIL，因为当前 `narrative_context` 没有 `chart_context`。

- [ ] **Step 3: 最小实现 chart_context**

只在 `renderer.py` 增加一个私有 helper，并由 `build_narrative_context()` 调用。复用现有 `_decimal_from_text()`、`_normalized_amount()` 与 ReportContext 字段，不新增模型。

关系规则严格限定为：

- `financial_kpis`
  - `revenue_growth > 0`：`收入同比保持正增长`
  - `revenue_growth < 0`：`收入同比出现负增长`
  - `share_dilution < 0`：`股份数量同比减少`
  - `share_dilution > 0`：`股份数量同比增加`
  - `operating_margin > 0 and net_margin > 0`：`营业利润率和净利率均为正`
  - `cash_conversion > 100%`：`经营现金流高于净利润`
- `ttm_scale`
  - 只读取 `status=available`、`validation_status=valid`、`period_basis=TTM` 的指标。
  - `operating_cash_flow > net_income`：`经营现金流高于净利润`
  - `operating_cash_flow < net_income`：`经营现金流低于净利润`
  - `free_cash_flow > 0`：`自由现金流保持为正`
  - `free_cash_flow < operating_cash_flow`：`自由现金流低于经营现金流`
- `historical_pe`
  - 只在 `status=ok`、`validation_status=valid` 时可用。
  - `current_value > five_year_median`：`当前市盈率高于五年中位数`
  - `current_value < five_year_median`：`当前市盈率低于五年中位数`
  - `current_value >= percentile_75`：`当前市盈率位于或高于历史上四分位`
  - `percentile_25 < current_value < percentile_75`：`当前市盈率位于历史中间区间`
  - `current_percentile > 50`：`当前估值位于历史样本上半区`

任何必要输入缺失时，整个对应项返回 `available=False, observations=[]`。输出不得包含数字、单位、日期、来源或评级。

- [ ] **Step 4: 写 Prompt RED 测试**

在 `tests/test_crew_configuration.py` 扩充现有 Report Prompt 测试，断言任务描述包含：

```python
for phrase in (
    "chart_context",
    "我们可以看到",
    "这说明",
    "由此判断",
    "不得自行计算",
    "不得改变确定性 Verdict",
):
    assert phrase in prompt
```

同时保留现有“不得输出数字”、固定九字段和本地 guardrail 断言。

- [ ] **Step 5: 运行 Prompt RED 测试**

运行：

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q \
  tests/test_crew_configuration.py -k 'report_prompt'
```

预期：FAIL，因为当前 Prompt 没有图表推导要求。

- [ ] **Step 6: 最小修改 tasks.yaml**

保持唯一九字段 JSON 示例和全部原有禁令，只补充：

- `company_quality` 只根据 `chart_context.financial_kpis.observations` 与对应已验证 Claim，组织“我们可以看到—这说明—由此判断”。
- `financial_trend` 只根据 `chart_context.ttm_scale.observations` 与对应已验证 Claim，使用同一结构。
- `historical_valuation` 只根据 `chart_context.historical_pe.observations` 与对应已验证 Claim，使用同一结构。
- 三个字段不得复制输入数字、不得自行计算、不得建立新评级、不得改变确定性 Verdict、不得声称已直接读取图片。
- `available=false` 时只基于已验证 Claim 做“数据解读”，不得提到图表。

不修改 `agents.yaml`，因为 Task 已提供完整、局部且可测试的输出规范。

- [ ] **Step 7: 写 Renderer 顺序 RED 测试**

在 `tests/test_reporting_modules.py` 增加测试，使用带有唯一标记的合法无数字 ReportDraft：

```python
draft.company_quality = "我们可以看到公司质量关系。这说明经营表现需要结合现金流验证。由此判断公司质量需要结合已验证事实。"
draft.financial_trend = "我们可以看到现金流关系。这说明利润获得现金支持。由此判断现金创造能力需要持续验证。"
draft.historical_valuation = "我们可以看到估值关系。这说明市场预期较高。由此判断后续表现更依赖基本面兑现。"
```

实际测试文本必须全部为合法中文、无数字、无评级、无建议。断言：

- 第一张图位于 `company_quality` 唯一标记之前；第一张图不再位于执行摘要。
- 第二张图位于 `financial_trend` 唯一标记之前。
- 第三张图位于 `historical_valuation` 唯一标记之前。
- 每个唯一标记只出现一次。
- 图片存在时紧邻段落前包含 `**图表推导：** 根据上图及其对应的已验证数据，`。

另用 monkeypatch 令 `build_report_visuals()` 返回 `{}`，断言三个字段仍被渲染，但前缀为 `**数据解读：** 根据已验证数据，`，报告中不出现 `根据上图`。

- [ ] **Step 8: 运行 Renderer RED 测试**

运行：

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q \
  tests/test_reporting_modules.py -k 'chart_reasoning or chart_context'
```

预期：FAIL，因为当前草稿段落位于图前，且第一张图仍在执行摘要。

- [ ] **Step 9: 最小调整 Renderer 排版**

在 `_render_report_from_context()` 中：

- 从 `execution_summary` 删除 `financial_kpis` 图片及静态图注，只保留摘要和口径说明。
- `company_quality`：保留 Claim 与指标；插入 `financial_kpis` 图片和静态口径图注；最后渲染 `report_draft.company_quality`。
- `financial_trend`：保留 Claim、指标和 TTM 列表；插入 `ttm_scale` 图片和静态图注；最后渲染 `report_draft.financial_trend`。
- `historical_valuation`：保留确定性指标；插入 `historical_pe` 图片和静态图注；最后渲染 `report_draft.historical_valuation`。
- 图片存在时使用 `**图表推导：** 根据上图及其对应的已验证数据，{draft}`。
- 图片不存在时使用 `**数据解读：** 根据已验证数据，{draft}`。
- 不改其他六个 ReportDraft 字段的渲染行为。
- 不修改 legacy renderer；当前 Flow 只走 `report_context` 渲染路径，避免扩大兼容代码改动。

- [ ] **Step 10: 运行定向测试与 Report Crew 单调用测试**

运行：

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q \
  tests/test_reporting_modules.py \
  tests/test_crew_configuration.py::AnalysisGateTests::test_complete_claims_call_verdict_then_report_without_limitations
```

预期：全部 PASS，且现有测试继续证明 `report_crew.kickoff_calls == 1`。

若现有 narrative/report 固定 SHA 测试仅因已批准的 `chart_context` 或段落顺序变化而失败，先确认全部语义断言通过，再把期望 SHA 更新为本次确定性输出；不得删除哈希测试。

- [ ] **Step 11: 全量验证**

运行：

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q
git diff --check -- \
  src/stockcrewai/reporting/renderer.py \
  src/stockcrewai/crews/report/config/tasks.yaml \
  tests/test_reporting_modules.py \
  tests/test_crew_configuration.py
```

预期：全量测试零失败；仅允许既有 CrewAI deprecation warnings；`git diff --check` 零错误。

- [ ] **Step 12: 真实单轮验收**

运行：

```bash
STOCKCREWAI_REQUEST='请分析 Apple Inc.（AAPL）的投资价值，投资期限为 3 年。' \
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache \
uv run crewai run
```

验收：最终状态 `ok`；Report Crew 只执行一次；三张实际存在的图后均出现自然的“我们可以看到—这说明—由此判断”推导；数字仍由 Renderer 单独注入；报告不出现数字冲突、无条件投资建议或新增 limitation。
