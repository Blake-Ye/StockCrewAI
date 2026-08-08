# Agent Prompt 与 Gate 契约对齐 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对齐所有 Agent 的 Prompt、结构 Guardrail 与 Python Gate，消除风险和估值 Agent 的输出契约漂移。

**Architecture:** Request Parser、Analysis Crew 和 Report Crew 保持各自职责，但每个边界都有显式结构契约。Analysis 三个 Agent 共用 `AnalysisClaim` 六字段模型，通过域规则参数区分允许类别、Evidence 和 Calculation 要求；Flow 仍由 Python 控制。

**Tech Stack:** Python 3.12、CrewAI 1.15.11、Pydantic、YAML、unittest、uv。

## Global Constraints

- 使用当前 UV 环境与 `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache`；不运行 `uv sync`。
- 不调用真实 SEC、Yahoo、DeepSeek；默认测试必须离线。
- 不改变外部数据工具、计算公式、Flow 路由或 Verdict 政策。
- 所有结构检查先由本地 Guardrail 执行，Evidence/Calculation 白名单和路由继续由 Python Gate 执行。
- 不覆盖或撤销工作区已有的用户改动，不提交 Git。

### Task 1: 统一 Analysis Agent 契约

**Files:**
- Modify: `src/stockcrewai/crews/analysis/crew.py`
- Modify: `src/stockcrewai/crews/analysis/config/agents.yaml`
- Modify: `src/stockcrewai/crews/analysis/config/tasks.yaml`
- Modify: `src/stockcrewai/pipeline_support.py`（仅在现有 Claim Gate 规则需要与 Guardrail 共用常量/辅助函数时）
- Test: `tests/test_crew_configuration.py`
- Test: `tests/test_analysis_structured_output.py`

**Interfaces:**
- 保持 `AnalysisClaim` 六字段模型不变：`claim_id`、`category`、`statement`、`evidence_ids`、`calculation_ids`、`confidence`。
- 新增或统一三个可调用 Guardrail：`validate_financial_analysis_output`、`validate_risk_analysis_output`、`validate_valuation_analysis_output`。
- 三个 Guardrail 返回 CrewAI 标准 `(bool, Any)`，且不检查运行时白名单。
- `AnalysisCrew` 的三个 Task 都配置 `guardrail_max_retries=2`。

- [ ] 为三个域补充失败测试：缺 `confidence`、出现额外字段、错误 category、风险含 Calculation、估值缺 Calculation。
- [ ] 运行新增测试确认修复前失败。
- [ ] 抽取最小共享结构校验并实现三个域规则。
- [ ] 更新三份 Agent/Task Prompt，逐项列出六字段、允许 category、空输入和 JSON-only 输出。
- [ ] 运行 Analysis 结构测试与配置测试。

### Task 2: 对齐 Request Parser 契约

**Files:**
- Modify: `src/stockcrewai/crews/request_parser/crew.py`
- Modify: `src/stockcrewai/crews/request_parser/config/agents.yaml`
- Modify: `src/stockcrewai/crews/request_parser/config/tasks.yaml`
- Modify: `src/stockcrewai/pipeline_support.py`（仅补充现有 `_parser_payload` 的确定性字段校验）
- Test: `tests/test_crew_configuration.py`
- Test: `tests/test_main_flow.py`

**Interfaces:**
- Parser 输出固定九字段：`company_mention`、`company_name_guess`、`ticker_guess`、`exchange_guess`、`request_type`、`investment_horizon`、`requested_focus`、`language`、`confidence`。
- `_parser_payload` 继续返回普通 JSON-safe `dict[str, Any]`，不改变其调用方签名。

- [ ] 为缺字段、额外字段、错误 `requested_focus` 类型和越界 `confidence` 增加失败测试。
- [ ] 先确认测试在现状下失败，再实现本地 Parser Guardrail 和 Gate 校验。
- [ ] 更新 Parser Agent/Task Prompt 与示例，明确候选值不是事实且不能生成投资结论。
- [ ] 验证有效请求仍能进入 SEC 阶段。

### Task 3: 对齐 Report Writer 契约

**Files:**
- Modify: `src/stockcrewai/crews/report/crew.py`
- Modify: `src/stockcrewai/crews/report/config/agents.yaml`
- Modify: `src/stockcrewai/crews/report/config/tasks.yaml`
- Modify: `src/stockcrewai/main.py`（仅接入报告输出校验，不改变 Verdict/Report 路由）
- Test: `tests/test_crew_configuration.py`
- Test: `tests/test_main_flow.py`

**Interfaces:**
- 报告仍以 Markdown `str` 传递；新增本地 `validate_report_output` 返回 `(bool, Any)`。
- Report Gate 至少验证非空、非代码围栏、包含确定性状态、未出现买入/卖出/持有建议词，并保持现有 Report Crew 输入字段。

- [ ] 为空报告、代码围栏、篡改状态和投资建议增加失败测试。
- [ ] 先确认测试失败，再接入 Report Task Guardrail 和重试。
- [ ] 更新 Report Prompt，明确只重组已验证输入，不生成新数字、Claim、评级或建议。
- [ ] 验证有效报告仍可生成，`insufficient_data` 状态原样保留。

### Task 4: 全链路验证

**Files:**
- Modify: only files required by failing tests from Tasks 1-3.

- [ ] 运行所有新增测试。
- [ ] 运行 `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache CREWAI_STORAGE_DIR=/private/tmp/stockcrewai-flow-storage CREWAI_TRACING_ENABLED=false uv run --no-sync python -m unittest discover -s tests -p 'test_*.py'`。
- [ ] 运行 `uv run --no-sync python -m compileall -q src`。
- [ ] 运行 `git diff --check`。
- [ ] 确认测试输出没有新增网络调用或密钥泄漏。
