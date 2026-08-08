# 确定性报告数据上下文 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已验证计算结果转换为可追溯的 ReportContext，由 Python 按指标 ID 插入最终报告，避免 Agent 伪造或改写数字。

**Architecture:** 保留现有 Request、Analysis 和 Report Crew 结构。主 Flow 在报告节点构造唯一 JSON-safe ReportContext；ReportWriterAgent 只输出无数字 ReportDraft；确定性 Renderer 使用 context 中的规范化指标渲染数字、单位、时间和来源。历史估值与反向 DCF 增加稳定 Calculation ID，接入同一指标注册表。

**Tech Stack:** Python 3.12、CrewAI 1.15.x、Pydantic v2、Decimal、unittest、uv。

## Global Constraints

- 只修改报告数据传递、历史估值/反向 DCF 的 Calculation ID 和相关离线测试；不改 SEC、Yahoo、Analysis Gate、Claim Gate 和 Verdict 规则。
- 数字只能来自已验证 facts/calculations/估值结果；Agent 不得成为数字来源。
- 所有跨 Crew 输入必须 JSON-safe；不把原始 Crew 输出、工具对象或密钥写入 Flow state。
- 保留现有 `ReportDraft` 九字段契约和 `report_output_invalid` 阻断码。
- 使用 TDD：先写会失败的测试，再实现最小改动。
- 使用项目既有 `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache`，测试不得调用实时 SEC 或 Yahoo。

---

### Task 1: 为历史估值和反向 DCF 增加稳定 Calculation ID

**Files:**
- Modify: `src/stockcrewai/tools/historical_valuation_tool.py`
- Modify: `src/stockcrewai/tools/reverse_dcf_tool.py`
- Test: `tests/test_historical_valuation_tool.py`
- Test: `tests/test_reverse_dcf_tool.py`

**Interfaces:**
- Historical result exposes a stable `calculation_id` for the returned historical metric.
- Reverse DCF result exposes a stable `calculation_id` for the base implied-growth result.
- Unavailable results keep the same ID so downstream code can report missing data without inventing an ID.

- [ ] **Step 1: Write failing tests** asserting the successful and unavailable result objects expose the expected stable IDs.
- [ ] **Step 2: Run the two focused test files and verify the new assertions fail because the fields are absent.
- [ ] **Step 3: Add default model fields and populate them in every result construction path without changing existing result values.
- [ ] **Step 4: Run the focused tests and verify they pass.
- [ ] **Step 5: Run `git diff --check` for the task files.

### Task 2: 构造规范化 ReportContext 与确定性指标 Renderer

**Files:**
- Modify: `src/stockcrewai/crews/report/crew.py`
- Modify: `src/stockcrewai/pipeline_support.py`
- Modify: `src/stockcrewai/main.py`
- Test: `tests/test_crew_configuration.py`
- Test: `tests/test_main_flow.py`

**Interfaces:**
- Add `build_report_context(...) -> dict[str, Any]` or an equivalent typed public helper in `report/crew.py`.
- The context contains JSON-safe `company`, `claims`, `verdict_status`, `metrics`, and `source_metadata`.
- Each metric contains `metric_id`, `display_value`, `unit`, `as_of`, `source_reference`, `evidence_ids`, and optional `calculation_id`.
- `render_validated_report(...)` consumes the context and the validated `ReportDraft`; it renders human-readable metric lines instead of relying on raw JSON dumps.

- [ ] **Step 1: Add failing tests for context creation from financial, current valuation, historical valuation, and reverse DCF payloads.
- [ ] **Step 2: Add a failing test proving the renderer uses the canonical metric value even if an accepted Claim statement contains a conflicting number.
- [ ] **Step 3: Add a failing test proving missing validation/source metadata raises `ValueError` and never creates a metric.
- [ ] **Step 4: Run the focused tests and verify they fail for the intended missing context/renderer behavior.
- [ ] **Step 5: Implement typed metric normalization using only existing `display_result`, result values, validation status, evidence IDs, calculation IDs and source metadata.
- [ ] **Step 6: Change `main.py` to construct one context and pass the same JSON-safe context to Report Crew and Renderer.
- [ ] **Step 7: Render fixed metric lines for financial, current valuation, historical valuation and reverse DCF sections; preserve the nine ReportDraft prose sections and deterministic status.
- [ ] **Step 8: Run focused report and Flow tests and verify they pass.

### Task 3: 对齐报告 Prompt、兼容旧调用并完成全量验证

**Files:**
- Modify: `src/stockcrewai/crews/report/config/agents.yaml`
- Modify: `src/stockcrewai/crews/report/config/tasks.yaml`
- Modify: `tests/test_crew_configuration.py`
- Modify: `tests/test_main_flow.py`

**Interfaces:**
- Report prompt names `report_context` as the sole structured data source.
- Prompt states that numeric values are read-only context and final insertion is Python-owned.
- Existing report tests use the new context contract and retain the old `ReportDraft` safety assertions.

- [ ] **Step 1: Add/update failing prompt and integration assertions for `report_context` and deterministic metric insertion.
- [ ] **Step 2: Run focused tests and confirm the old prompt/contract assertions fail.
- [ ] **Step 3: Update YAML and test fixtures without weakening the Draft Gate.
- [ ] **Step 4: Run all tests with the project uv command and verify zero failures.
- [ ] **Step 5: Run `compileall` and `git diff --check`.
