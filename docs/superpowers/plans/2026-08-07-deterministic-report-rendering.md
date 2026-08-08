# 确定性报告渲染 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Report Agent 只生成无数字叙述草稿，由 Python 从已验证数据生成最终报告。

**Architecture:** Report Crew 输出固定 `ReportDraft` JSON；本地 Draft Guardrail 校验结构和无数字约束；Flow 在 Draft Gate 通过后调用确定性 Renderer，把已通过 Claim Gate 的 Claims、Verdict 和来源元数据渲染成 Markdown。

**Tech Stack:** Python 3.12、CrewAI 1.15.11、Pydantic、YAML、unittest、uv。

## Global Constraints

- 使用当前 UV 环境，不运行 `uv sync`，不升级依赖。
- 测试不得调用真实 SEC、Yahoo 或 DeepSeek。
- 不修改 SEC、市场价格、计算器、估值、Claim Gate 或 Verdict 规则。
- Renderer 只能使用已验证输入和已通过 Claim Gate 的 Claims。
- 不提交 Git，不撤销工作区已有改动。

### Task 1: ReportDraft 契约与 Guardrail

**Files:**
- Modify: `src/stockcrewai/crews/report/crew.py`
- Modify: `src/stockcrewai/crews/report/config/agents.yaml`
- Modify: `src/stockcrewai/crews/report/config/tasks.yaml`
- Test: `tests/test_crew_configuration.py`

- [ ] 测试缺字段、额外字段、数字、代码围栏、买卖建议和有效无数字草稿。
- [ ] 运行测试确认旧实现失败。
- [ ] 新增 `ReportDraft`、解析函数和 `validate_report_draft`。
- [ ] 将 Report Task 改为 JSON-only、九字段、无数字叙述，并接入 Guardrail 与两次重试。

### Task 2: 确定性 Renderer 与 Flow 接入

**Files:**
- Modify: `src/stockcrewai/crews/report/crew.py`
- Modify: `src/stockcrewai/main.py`
- Modify: `tests/test_crew_configuration.py`
- Test: `tests/test_main_flow.py`

- [ ] 测试 Renderer 只输出已验证 Claim、Verdict status、估值和来源，不接收 rejected/raw Claims。
- [ ] 实现 `render_validated_report`，固定章节顺序并保留非投资建议声明。
- [ ] 将 `generate_report()` 改为 Draft Gate 通过后调用 Renderer；失败时仍返回 `report_output_invalid`。
- [ ] 确认最终 Markdown 仍可被 `run-output.md` 摘要识别。

### Task 3: 全量验证

- [ ] 运行 `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache CREWAI_STORAGE_DIR=/private/tmp/stockcrewai-flow-storage CREWAI_TRACING_ENABLED=false uv run --no-sync python -m unittest discover -s tests -p 'test_*.py'`。
- [ ] 运行 `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m compileall -q src`。
- [ ] 运行 `git diff --check`。
