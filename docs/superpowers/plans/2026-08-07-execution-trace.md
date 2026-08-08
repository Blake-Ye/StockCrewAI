# CrewAI Execution Trace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在每次终端运行时，用 CrewAI 风格的 Rich 框体显示 Crew、Agent、Task、数据传递和阻断决定，让用户能够快速定位流程停在哪个节点。

**Architecture:** 在现有 `main.py` 中增加轻量的运行轨迹输出适配器，所有轨迹从主编排函数显式发出。轨迹只展示经过脱敏和裁剪的摘要；LLM 任务标注实际 Crew/Agent/Task，确定性门禁和工具决策标注真实的 Python 决策者。轨迹继续经过现有 `_TeeTextStream`，因此终端和 `run-output.md` 保持一致。

**Tech Stack:** Python 标准库、已安装的 `rich`（由 CrewAI 依赖提供）、`unittest`、现有 CrewAI/工具输出对象。

## Global Constraints

- 不修改 SEC、Yahoo、计算器、验证、估值、Verdict 的业务逻辑。
- 不把 `main.py` 的确定性门禁伪装成 Agent 决策；输出必须明确区分 LLM Agent 与 Python 工具/门禁。
- 不在终端或 `run-output.md` 输出完整 SEC 文本、Prompt、环境变量值或 API 密钥。
- 仅显示字段摘要、数量、状态、Evidence ID、Calculation ID 和阻断原因。
- `crewai run`、`kickoff` CLI 和现有 `cli(output_path)` 必须使用同一套轨迹格式。
- 不新增数据库、SQLite 持久化或新的运行时依赖。
- 默认输出使用 Unicode/Rich 框体；终端不支持颜色时仍保留可读的纯文本框。

---

### Task 1: 为轨迹输出定义失败测试

**Files:**
- Modify: `tests/test_runtime_defaults.py`
- Modify: `tests/test_run_and_save_output.py`

**Interfaces:**
- Consumes: 现有 `stockcrewai.main._trace_panel`、`_trace_transfer`、`_trace_decision` 预期接口（实现前不存在）。
- Produces: 能证明 Crew/Agent/Task 归属、数据传输方向、阻断原因和 Markdown 同步输出的测试约束。

- [ ] **Step 1: 写框体渲染的失败测试**

```python
def test_trace_panel_contains_crewai_frame_and_attribution(self):
    from stockcrewai.main import _trace_panel

    rendered = _trace_panel(
        "CrewAI Execution Trace",
        {
            "Crew": "RequestParserCrew",
            "Agent": "request_parser_agent",
            "Task": "parse_investment_request_task",
            "决定": "解析公司身份",
        },
    )

    self.assertIn("╭", rendered)
    self.assertIn("Crew: RequestParserCrew", rendered)
    self.assertIn("Agent: request_parser_agent", rendered)
    self.assertIn("Task: parse_investment_request_task", rendered)
    self.assertIn("决定: 解析公司身份", rendered)
```

- [ ] **Step 2: 写数据传输和阻断轨迹的失败测试**

测试应调用新的 `_trace_transfer` 和 `_trace_decision`，并断言输出包含：

```text
RequestParserCrew → main.py
决策者: main.py / Analysis Gate
结果: BLOCKED
原因: risk_sections_required
```

- [ ] **Step 3: 运行测试确认失败**

Run:

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_runtime_defaults tests.test_run_and_save_output -q
```

Expected: FAIL，因为轨迹辅助函数尚未实现，或者现有 CLI 输出尚未包含新的轨迹字段。

### Task 2: 实现最小的安全轨迹渲染器

**Files:**
- Modify: `src/stockcrewai/main.py:145-365`

**Interfaces:**
- Consumes: `_json_safe`、`_redact_sensitive_value`、`_TeeTextStream`。
- Produces:
  - `_trace_panel(title: str, fields: Mapping[str, Any]) -> str`
  - `_trace_transfer(source: str, target: str, payload: Mapping[str, Any]) -> str`
  - `_trace_decision(decider: str, decision: str, reason: str | None = None) -> str`
  - `_trace_emit(console: Any, title: str, fields: Mapping[str, Any]) -> None`

- [ ] **Step 1: 实现安全摘要逻辑**

只对轨迹字段做摘要：

- `request` 截断为有限长度文本；
- 字典只保留键名、状态、ID、列表长度和少量身份字段；
- `facts`、`calculations`、`filings` 等大列表只显示数量和 ID；
- 所有字符串先经过 `_redact_sensitive_text`；
- 不能序列化的对象经过 `_json_safe`。

- [ ] **Step 2: 使用 Rich Panel 或等价纯文本框体**

`_trace_panel` 优先使用已安装 Rich 的 `Panel`/`Console` 渲染；若 Rich 不可导入，使用标准库生成 `╭─╮/│/╰─╯` 框体。返回字符串或直接发射的接口都必须保留 Crew、Agent、Task 和决定字段。

- [ ] **Step 3: 运行 Task 1 测试确认变绿**

Run:

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_runtime_defaults tests.test_run_and_save_output -q
```

Expected: PASS。

### Task 3: 在主流程接入节点、传输和决定轨迹

**Files:**
- Modify: `src/stockcrewai/main.py:1364-1697`
- Modify: `tests/test_crew_configuration.py`

**Interfaces:**
- Consumes: Task 2 的四个轨迹辅助接口，以及现有 `run_research` 中的确定性结果。
- Produces: 每次研究运行的完整节点轨迹。

- [ ] **Step 1: 为请求解析增加 Crew/Agent/Task 入口和输出轨迹**

在调用 Request Parser Crew 前后分别输出：

```text
Crew: RequestParserCrew
Agent: request_parser_agent
Task: parse_investment_request_task
```

解析完成后输出 `RequestParserCrew → main.py`，只展示公司名、ticker、期限和请求类型。

- [ ] **Step 2: 为确定性数据准备增加工具节点轨迹**

分别标注真实决策者：

```text
EDGARTool → main.py
FinancialCalculatorTool → main.py
FinancialValidationTool → main.py
MarketPriceTool → ValuationTool
HistoricalValuationTool → main.py
ReverseDCFTool → main.py
```

每个节点显示 `status`、结果数量和验证状态，不打印原始文本。

- [ ] **Step 3: 为 Analysis Gate 和 Analysis Crew 增加轨迹**

在 `_analysis_gate` 返回后显示：

```text
决策者: main.py / Analysis Gate
结果: READY 或 BLOCKED
原因: required_data
```

只有 READY 时，显示 Analysis Crew 的三个任务：

```text
AnalysisCrew / FinancialQualityAgent / financial_quality_analysis_task
AnalysisCrew / RiskAnalysisAgent / risk_analysis_task
AnalysisCrew / ValuationAnalysisAgent / valuation_analysis_task
```

Analysis 返回后显示 `AnalysisCrew → main.py / Claim Gate`，展示每个域的 Claim 数量和 ID 数量。

- [ ] **Step 4: 为 Claim Gate 阻断路径增加清晰轨迹**

当 `required_data` 非空时，显示：

```text
决策者: main.py / Claim Gate
结果: BLOCKED
原因: analysis_output_invalid 或具体 required_data
下游: DeterministicVerdictTool、ReportCrew 未执行
```

- [ ] **Step 5: 为 Verdict 和 Report Crew 增加成功路径轨迹**

显示：

```text
决策者: DeterministicVerdictTool
结果: verdict.status

AnalysisCrew → ReportCrew
ReportCrew / ReportWriterAgent / generate_validated_report_task
```

Report 输入只显示已验证 Claim 数量、Verdict 状态、计算数量和来源数量。

- [ ] **Step 6: 添加主流程成功/阻断测试**

在现有测试中增加断言：

- Analysis Gate 阻断结果含 `Analysis Gate` 和缺失项；
- Claim Gate 阻断结果含 `Claim Gate` 和下游未执行；
- 成功路径的 Report 输入轨迹含 `AnalysisCrew → ReportCrew` 和 `ReportWriterAgent`；
- 不改变原有 `status`、`analysis`、`verdict`、`report` 结构。

- [ ] **Step 7: 运行主流程测试确认变绿**

Run:

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_crew_configuration tests.test_runtime_defaults tests.test_run_and_save_output -q
```

Expected: PASS。

### Task 4: 完整回归与终端/Markdown 验证

**Files:**
- Modify: `tests/test_run_and_save_output.py` only if a regression assertion is needed

**Interfaces:**
- Consumes: 完整轨迹输出和现有 CLI 文件复制逻辑。
- Produces: 可证明终端和 `run-output.md` 包含同一轨迹的回归结果。

- [ ] **Step 1: 验证 CLI 同步输出**

运行现有 CLI 测试，断言终端捕获内容和保存的 Markdown 同时包含同一个 Crew、Agent 和阻断原因。

- [ ] **Step 2: 运行全部离线测试**

Run:

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest discover -s tests -q
```

Expected: 所有测试通过；不要求访问 SEC、Yahoo 或 DeepSeek 网络。

- [ ] **Step 3: 做语法和补丁检查**

Run:

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m compileall -q src/stockcrewai
git diff --check
```

Expected: 两条命令退出码均为 0。
