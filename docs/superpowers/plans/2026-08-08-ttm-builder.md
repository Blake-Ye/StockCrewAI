# TTM Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 StockCrewAI 增加可审计、确定性的 TTM 构建结果，并在 Flow 状态和命令行阶段摘要中显示其可用性。

**Architecture:** EDGAR 工具只提取最近 FY、当前 YTD、上年同期 YTD 三组 Evidence；独立 TTM Builder 使用 Decimal 和固定公式计算；Flow 在基础验证后调用 Builder 并保存 JSON-safe 结果。当前估值口径本轮不切换。

**Tech Stack:** Python 3.10+、Pydantic v2、CrewAI BaseTool、Decimal、unittest、uv

## Global Constraints

- 使用当前 uv 项目；不创建环境、不升级依赖、不修改 `.env`。
- 3 Crew、4 LLM Agent 架构不变；不得新增 Agent 或恢复 ValuationAnalysisAgent。
- LLM 不选择期间、不计算 TTM、不验证结果。
- 测试默认离线，不调用 DeepSeek、SEC 或 Yahoo。
- 保留 `run-output.md` 与 `run-result.json`，不得删除或提交。
- 子代理并行工作，不能回退其他代理或用户的修改。

---

### Task 1: 对齐架构文档

**Files:**
- Modify: `docs/Expectayion_Projects.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: `docs/superpowers/specs/2026-08-08-ttm-builder-design.md`
- Produces: 与当前 3 Crew、4 Agent、确定性估值 Claims、uv 运行方式一致的说明

- [ ] **Step 1:** 将所有“5 Agent/ValuationAnalysisAgent”描述改为当前 4 Agent 架构，并明确估值解释 Claim 由 Python 生成。
- [ ] **Step 2:** 将过期 Conda/禁止子代理说明对齐当前用户批准的 uv 工作流，但保留依赖升级需批准和安全边界。
- [ ] **Step 3:** 在 README 工具与链路中加入 TTM Builder，并明确本轮尚未切换估值口径。
- [ ] **Step 4:** 运行 `rg -n "5 个 LLM Agent|五个 Agent|ValuationAnalysisAgent|conda run -n AgentVest" docs/Expectayion_Projects.md README.md`，确认没有与当前架构冲突的陈述。

### Task 2: 提取 TTM Evidence 并实现 Builder

**Files:**
- Modify: `src/stockcrewai/tools/edgar_tool.py`
- Create: `src/stockcrewai/tools/ttm_tool.py`
- Modify: `src/stockcrewai/tools/__init__.py`
- Create: `tests/test_ttm_tool.py`
- Modify: `tests/test_edgar_tool.py`（若不存在则修改最接近的 EDGAR 单元测试文件）

**Interfaces:**
- Consumes: `EdgarFact` 与 `dict[str, dict[str, EdgarFact]]`
- Produces: `EdgarResult.ttm_inputs`；`TTMBuilderTool.run(company_name, ticker, metric_inputs)`

- [ ] **Step 1:** 先写 Builder 正常、缺失输入、单位不一致、invalid Evidence、期间不匹配的失败测试，并运行确认红灯。
- [ ] **Step 2:** 给 EDGAR 适配器写 FY/current YTD/prior YTD 提取测试并运行确认红灯。
- [ ] **Step 3:** 最小实现 Pydantic 输入/输出与 Decimal 公式；不接受股票、时点指标和 EPS。
- [ ] **Step 4:** 实现 EDGAR 三段 Evidence 提取，Evidence ID 必须包含指标、角色、期间或 accession，避免碰撞。
- [ ] **Step 5:** 运行 `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_ttm_tool -v` 以及相关 EDGAR 测试；失败则定位并修复直到通过。

### Task 3: 接入 Flow 和命令行可观测性

**Files:**
- Modify: `src/stockcrewai/main.py`
- Modify: `src/stockcrewai/pipeline_support.py`
- Modify: `tests/test_main_flow.py`
- Modify: `tests/test_crew_configuration.py`（只允许更新共享离线 fake）

**Interfaces:**
- Consumes: `EdgarResult.ttm_inputs`；`TTMBuilderTool.run(company_name, ticker, metric_inputs)`
- Produces: `ResearchFlowState.ttm: dict[str, Any]`；第 2 阶段摘要中的 `ttm=<available>/<total>`

- [ ] **Step 1:** 先写 Flow 测试：注入 TTM Builder fake 后，`state.ttm` 被保存且阶段摘要包含数量；运行确认红灯。
- [ ] **Step 2:** 在依赖注入列表和 PrivateAttr 中加入 `ttm_builder_tool`，不持久化工具对象。
- [ ] **Step 3:** 在基础 validation 完成后调用 Builder，写入 JSON-safe state；不改变 `_valuation_facts` 和估值工具输入。
- [ ] **Step 4:** 更新 stage snapshot 与命令行第 2 阶段输出，错误使用结构化 unavailable，不抛出未包装异常。
- [ ] **Step 5:** 运行 `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_main_flow tests.test_crew_configuration -v`；失败则修复直到通过。

### Task 4: 集成验证与真实运行

**Files:**
- Verify only: all changed files

**Interfaces:**
- Consumes: Tasks 1-3 的合并结果
- Produces: 可复现的命令行验证证据

- [ ] **Step 1:** 运行全部 unittest；失败时把完整失败分派回对应文件所有者修复。
- [ ] **Step 2:** 运行 compileall 和 `git diff --check`。
- [ ] **Step 3:** 运行一次 `crewai run`，检查七阶段框、`run-output.md`、`run-result.json` 和 TTM 状态。
- [ ] **Step 4:** 若真实运行出现代码错误，修复并重跑；若仅外部 SEC/Yahoo/DeepSeek 网络失败，保留结构化诊断并明确区分。
