# 统一估值口径与外行可读报告 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让当前估值、历史估值与反向 DCF 全部使用可审计的 TTM 口径，并把正式报告改造成外行能够直接理解数字变化和结论依据的中文报告。

**Architecture:** SEC/TTM/估值计算仍由确定性 Python 完成，LLM 只解释已验证数据。实施分为三个互不覆盖的组件任务和一个串行集成任务：TTM 与当前估值、历史 TTM P/E、报告展示；最后由 Flow 集成代理统一接线并运行真实链路。

**Tech Stack:** Python 3.12、Decimal、Pydantic v2、CrewAI 1.15.11、edgartools、yfinance、Matplotlib、unittest

## Global Constraints

- 固定 4 个 LLM Agent、3 个 Crew；不得新增 Agent、Crew、Planner、Manager 或 Validator Agent。
- 不新增依赖，不升级 CrewAI、Pydantic、Python、edgartools、yfinance 或 Matplotlib。
- 所有命令使用 `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync ...`；禁止 `uv sync`。
- LLM 不生成或修改数字、公式、Evidence、Calculation、评级、DCF 参数和图表。
- TTM 输入不足时 fail-closed；禁止回退到单季度、九个月累计、年化猜测或未验证数据。
- 所有财务计算使用 `Decimal`，禁止在计算路径使用二进制 `float`。
- 默认测试禁止访问 SEC、Yahoo、DeepSeek 或 CrewAI AMP。
- 保留工作区现有未提交修改；不得 reset、checkout 或覆盖其他 Agent 文件。
- 每个实现 Agent 必须遵循 RED → GREEN → REFACTOR，并在交付中给出测试命令和结果。
- 每个 Agent 只能修改其“文件所有权”中列出的文件；发现跨边界需求时停止并报告集成代理。

---

## Agent 分工与执行顺序

第一批可并行：Agent A、Agent B、Agent C。三者完成并通过独立审查后，才启动 Agent D。Agent D 是唯一允许修改 `main.py`、Gate 接线和跨组件集成测试的代理。

### Agent A：TTM EPS、当前 P/E、TTM FCF 估值

**文件所有权：**
- Modify: `src/stockcrewai/tools/ttm_tool.py`
- Modify: `src/stockcrewai/tools/valuation_tool.py`
- Modify: `src/stockcrewai/tools/reverse_dcf_tool.py`
- Modify: `tests/test_ttm_tool.py`
- Modify: `tests/test_valuation_tool.py`
- Modify: `tests/test_reverse_dcf_tool.py`

**禁止修改：** `main.py`、`edgar_tool.py`、`historical_valuation_tool.py`、Report Crew、任何 Gate。

**接口：**
- Consumes: `ttm_inputs["diluted_eps"]`，含 `latest_fy/current_ytd/prior_ytd` 三项已验证 SEC Evidence。
- Produces: `TTMMetricResult(metric_id="diluted_eps", calculation_id="calc_diluted_eps_ttm", formula_id="ttm_diluted_eps", unit="USD/share")`。
- Consumes: 当前估值输入新增 `ttm_diluted_eps`、`ttm_diluted_eps_evidence_ids`、`ttm_fcf`、`ttm_fcf_evidence_ids`。
- Produces: 当前 P/E 仅由 `market_price / ttm_diluted_eps` 计算；FCF Yield 仅由 `ttm_fcf / market_capitalization` 计算。
- Produces: Reverse DCF 的 `base_fcf` 只接受与 FCF Yield 相同的 TTM FCF Calculation/Evidence。

- [ ] **Step A1：先写 TTM EPS 失败测试**

新增测试必须断言：`10.00 + 7.00 - 6.00 = 11.00`；输出包含三个 Evidence ID、`period_basis="TTM"`，缺任一输入时 unavailable。

- [ ] **Step A2：运行 RED**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_ttm_tool -v
```

预期：因 `diluted_eps` 尚未受支持而失败。

- [ ] **Step A3：实现最小 TTM EPS 公式**

沿用现有 TTM metric 构造与 Decimal 规范，不建立第二套计算框架。

- [ ] **Step A4：先写当前估值口径失败测试**

测试必须同时提供九个月 EPS/FCF 与 TTM EPS/FCF，并断言计算只使用 TTM 值；删除 TTM 输入时 readiness 为 not_ready，原因分别为 `ttm_eps_required`、`ttm_fcf_required`。

- [ ] **Step A5：实现当前估值与反向 DCF 的 TTM 输入**

保留市场价格、股本和 Evidence 白名单逻辑；输出 calculation 的 `period_basis` 必须为 `TTM`。

- [ ] **Step A6：运行 Agent A 全部测试**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_ttm_tool tests.test_valuation_tool tests.test_reverse_dcf_tool -v
```

### Agent B：SEC 历史 TTM EPS 与历史 P/E

**文件所有权：**
- Modify: `src/stockcrewai/tools/edgar_tool.py`
- Modify: `src/stockcrewai/tools/historical_valuation_tool.py`
- Create: `tests/test_edgar_tool.py`
- Modify: `tests/test_historical_valuation_tool.py`

**禁止修改：** `main.py`、当前估值工具、TTM 工具、Report Crew、任何 Gate。

**接口：**
- Produces: EDGAR `ttm_inputs["diluted_eps"]`，键固定为 `latest_fy/current_ytd/prior_ytd`。
- Produces: `historical_financial_snapshots` 每项包含 `filed_at`、`period_end`、`ttm_eps`、`period_basis="TTM"`、三个真实 Evidence ID。
- Consumes: HistoricalValuationTool 只接受 `period_basis="TTM"` 且 `filed_at <= price_date` 的快照。
- Produces: 60 项 `series`，每项增加 `ttm_eps` 和 `financial_evidence_ids` 以支持审计。

- [ ] **Step B1：写 EDGAR TTM EPS 输入失败测试**

离线 fixture 同时包含 FY、当前 YTD、上年同期 YTD diluted EPS；断言三类 Evidence 的 form、period_end、filed_at 和 accession 被保留。

- [ ] **Step B2：运行 RED 并实现最小采集**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_edgar_tool -v
```

- [ ] **Step B3：写历史快照失败测试**

测试至少覆盖：正常 TTM 组合、缺 FY、缺 prior YTD、`filed_at` 晚于价格日期、单季度 EPS 不能通过。

- [ ] **Step B4：实现 point-in-time TTM EPS 快照**

按 filed_at 排序，只组合在该 filed_at 已公开的三项输入；不能使用 period_end 代替 filed_at，不能使用未来 filing。

- [ ] **Step B5：写历史 P/E 同口径失败测试**

构造 60 个月价格与 TTM EPS 快照，断言最后一个历史 P/E 等于 `current_price / current_ttm_eps`；单季度 `eps` 字段必须返回 `historical_ttm_eps_required`。

- [ ] **Step B6：实现并运行 Agent B 全部测试**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_edgar_tool tests.test_historical_valuation_tool -v
```

### Agent C：报告单位、DCF 假设和外行图表

**文件所有权：**
- Modify: `src/stockcrewai/crews/report/crew.py`
- Modify: `src/stockcrewai/report_visuals.py`
- Modify: `src/stockcrewai/crews/report/config/tasks.yaml`
- Modify: `tests/test_crew_configuration.py`
- Modify: `tests/test_report_visuals.py`

**禁止修改：** `main.py`、所有财务工具、EDGAR、任何 Gate。

**接口：**
- Consumes: 已验证 `ReportContext` 中的 TTM metrics、current valuation、historical valuation、reverse DCF、Verdict。
- Produces: 所有图表中文化并直接内嵌 data URI；不在项目目录残留图片。
- Produces: Reverse DCF 参数表和三情景矩阵。

- [ ] **Step C1：写报告单位失败测试**

断言：流动比率 `1.00x`、债务权益比 `0.77x`、“股份变化率 -1.67%（负值表示股份减少）”、“自由现金流利润率”。

- [ ] **Step C2：写 DCF 展示失败测试**

断言报告显示基础 TTM FCF、10 年、9.00% 折现率、2.50% 永续增长率、15.33% 隐含增长率，以及 8%/9%/10% 三情景。

- [ ] **Step C3：写图表可读性失败测试**

断言三个 data URI 存在；图表函数输入/输出元数据含中文标题、中文单位、数据标签和固定读图说明。不得通过 OCR 测试 PNG；测试图表配置数据与 Markdown 文本。

- [ ] **Step C4：最小修改 Renderer 和图表模块**

报告固定增加“口径说明”；当前 P/E、历史 P/E、FCF Yield、Reverse DCF 均标注 TTM。每张图下方输出一句基于确定性数值的读图说明。

- [ ] **Step C5：运行 Agent C 全部测试**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache MPLCONFIGDIR=/private/tmp/stockcrewai-matplotlib uv run --no-sync python -m unittest tests.test_crew_configuration tests.test_report_visuals -v
```

### Agent D：Flow 集成、Gate 一致性与真实验收

**启动条件：** Agent A、B、C 均完成，且各自测试通过；先审阅三者实际接口，禁止凭计划猜测签名。

**文件所有权：**
- Modify: `src/stockcrewai/main.py`
- Modify: `src/stockcrewai/pipeline_support.py`
- Modify: `src/stockcrewai/tools/verdict_tool.py`（仅当既有 high_valuation 规则需要读取统一 TTM 指标）
- Modify: `tests/test_main_flow.py`
- Modify: `tests/test_valuation_claim_stability.py`
- Modify: `tests/test_run_and_save_output.py`（若存在）

**禁止修改：** Agent A、B、C 已验收文件；如接口不一致，退回对应 Agent，不在集成层复制计算。

- [ ] **Step D1：写 Flow 接线失败测试**

断言 `main.py` 把验证后的 TTM EPS/FCF传给当前估值和 Reverse DCF；把 point-in-time TTM EPS 快照传给历史估值。

- [ ] **Step D2：写跨指标一致性 Gate 失败测试**

同一价格日期下，`current_pe` 与 `historical_series[-1].pe_ratio` 使用同一 TTM EPS；Calculation/Evidence 不一致时阻断并返回 `valuation_basis_mismatch`。

- [ ] **Step D3：实现最小接线与 Gate**

Gate 只比较 period basis、Calculation ID、Evidence ID 和可允许的 Decimal 舍入误差；不得重新计算业务指标或放宽现有 required_data。

- [ ] **Step D4：更新 Verdict 输入**

确保 `high_valuation` 规则只读取统一 TTM P/E、TTM FCF Yield、历史 TTM P/E percentile 与已验证风险；规则版本从 `v1` 升为 `v2`，测试固定触发条件。

- [ ] **Step D5：运行聚焦集成测试**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache MPLCONFIGDIR=/private/tmp/stockcrewai-matplotlib CREWAI_STORAGE_DIR=/private/tmp/stockcrewai-flow-storage CREWAI_TRACING_ENABLED=false uv run --no-sync python -m unittest tests.test_main_flow tests.test_valuation_claim_stability -v
```

- [ ] **Step D6：运行全量静态和单元验证**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache MPLCONFIGDIR=/private/tmp/stockcrewai-matplotlib CREWAI_STORAGE_DIR=/private/tmp/stockcrewai-flow-storage CREWAI_TRACING_ENABLED=false uv run --no-sync python -m unittest discover -s tests -p 'test_*.py'
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m compileall -q src tests
git diff --check
```

- [ ] **Step D7：真实运行验收**

```bash
STOCKCREWAI_REQUEST='分析苹果公司未来3年投资价值' \
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache \
MPLCONFIGDIR=/private/tmp/stockcrewai-matplotlib \
CREWAI_STORAGE_DIR=/private/tmp/stockcrewai-flow-storage \
CREWAI_TRACING_ENABLED=false \
uv run crewai run
```

验收 `run-result.json`：`status=ok`、`stage=report`、TTM EPS/FCF 全部 valid、历史序列 60 项、无 `valuation_basis_mismatch`。验收 `investment-report.md`：三个图、中文读图说明、DCF 假设表、无长 Decimal、无九个月口径冒充 TTM。

---

## 子代理统一 Prompt 前缀

每个 Luna Max 子代理收到任务时，必须在具体任务前附加以下要求：

```text
你是 StockCrewAI 的 Luna Max 实现代理。先完整阅读：
1. AGENTS.md
2. docs/Expectayion_Projects.md
3. docs/superpowers/specs/2026-08-08-consistent-valuation-report-design.md
4. docs/superpowers/plans/2026-08-08-consistent-valuation-report.md 中仅属于你的任务
5. 你的文件所有权范围内源码与测试

你不是仓库中唯一代理。不得回退、覆盖或格式化他人文件。只能修改任务列出的文件；遇到跨边界需求立即停止并报告。必须先写失败测试并实际观察 RED，再写最小实现并观察 GREEN。禁止真实网络请求、禁止新增依赖、禁止修改 .env、禁止放宽 Gate、禁止把单季度/YTD 数据标记为 TTM。完成后报告：修改文件、数据契约、RED 命令与失败原因、GREEN 命令与结果、遗留风险。
```

## 审查规范

每个实现任务完成后使用独立审查代理，仅做只读审查：

1. 检查是否严格遵守文件所有权。
2. 检查是否存在 `float`、未来数据、period_end 冒充 filed_at、YTD/季度冒充 TTM。
3. 检查 Calculation ID、Evidence ID、period_basis 是否完整。
4. 检查失败路径是否 fail-closed 且有明确 required_data。
5. 检查测试是否真实覆盖行为，而非只断言 mock 被调用。
6. 检查报告是否存在互相矛盾的 P/E、错误单位、英文图表或未展示 DCF 假设。

任何高优先级问题必须退回原实现 Agent 修复并重新审查；集成 Agent 不得替其他组件打补丁。
