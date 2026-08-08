# Research Flow Native Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有命令式 `run_research()` 重构为 CrewAI 原生 `Flow[ResearchFlowState]`，让 CrewAI 负责顺序、状态和分支，同时保留确定性金融工具、Claim Gate、Verdict、CLI 与离线测试契约。

**Architecture:** `ResearchFlow` 使用 Pydantic 状态保存跨阶段 JSON 安全数据，使用 `@start()`、`@listen()` 和 `@router()` 描述请求解析、数据准备、Analysis Gate、Analysis Crew、Claim Gate、Verdict 与 Report Crew 的边。现有三个 Crew 不改职责，只由 Flow 节点调用；SEC 选择、计算、验证、阻断和 Verdict 仍由 Python 工具决定。`run_research()` 保留为兼容 façade，负责注入依赖、启动 Flow 并返回现有结果字典。

**Tech Stack:** Python 3.10-3.13、CrewAI 1.15.x Flow、Pydantic v2、现有 EDGAR/yfinance/确定性工具、SQLite Flow persistence、`unittest`。

## Global Constraints

- 必须使用 `luna_coder` 完成编码任务；每个子代理都必须显式加载相应 `superpowers` skill。
- 不把 SEC 来源选择、CIK/entity 解析、Decimal 计算、Evidence/Calculation 验证、Claim Gate、Verdict 或报告最终校验交给 LLM。
- 保留三个现有 Crew 和五个现有 Agent 的职责、YAML 配置和 Claims 契约。
- 关键跨阶段数据必须通过 Pydantic `ResearchFlowState` 传递，不通过 Agent 自由聊天传递。
- 允许启用 SQLite Flow persistence；不得使用裸字典默认值、共享可变 Pydantic 默认值或把密钥写入 State。
- 保留 `run_research()`、`main()`、`cli()`、`kickoff = stockcrewai.main:cli` 兼容入口和 `run-output.md` 双写行为。
- 任何 Analysis Gate 或 Claim Gate 阻断都必须停止下游 Crew，并将 `stage`、`required_data`、诊断和已完成确定性结果写入返回值。
- 每个新增或重构函数都要有详细中文 docstring；终端使用 CrewAI 原生 Flow 框体，并保留必要的安全摘要轨迹。
- 默认离线测试不能调用 SEC、Yahoo 或 DeepSeek 网络。

---

### Task 1: 建立 Flow 状态和路由契约测试（TDD RED）

**Files:**
- Create: `tests/test_research_flow.py`
- Modify: `tests/test_runtime_defaults.py` only if the existing docstring/entrypoint assertion needs a new Flow symbol

**Interfaces:**
- Consumes: 当前三个 Crew、`run_research()` 依赖注入边界、现有工具测试替身。
- Produces: 失败测试，锁定 `ResearchFlowState`、Flow 可实例化、`@start/@listen/@router` 分支、Pydantic 状态更新和 CLI 兼容契约。

- [ ] **Step 1: 编写状态契约失败测试**

测试应断言 `ResearchFlowState` 至少包含：`request`、`parsed_request`、`input_requirements`、`edgar`、`facts`、`filings`、`calculations`、`validation`、`market_price_data`、`valuation`、`historical_valuation`、`reverse_dcf`、`analysis`、`verdict`、`report`、`status`、`stage`、`required_data` 和 `analysis_diagnostics`，并且列表/字典默认值彼此独立。

- [ ] **Step 2: 编写 Flow 图和阻断路由失败测试**

测试使用不调用网络的注入替身，断言：

```python
flow = ResearchFlow(
    edgar_tool=fake_edgar,
    calculator_tool=fake_calculator,
    validation_tool=fake_validation,
    valuation_tool=fake_valuation,
    market_price_tool=fake_market_price,
    historical_valuation_tool=fake_historical,
    reverse_dcf_tool=fake_reverse_dcf,
    analysis_crew=fake_analysis_crew,
    report_crew=fake_report_crew,
)
result = flow.kickoff(inputs={"request": "分析苹果公司未来 3 年投资价值"})
```

当 Analysis Gate 不满足时，测试必须断言 `status == "blocked"`、`stage == "analysis"`、`report_crew` 未调用；当 Claim Gate 不满足时，断言 Verdict 和 Report 都未调用。

- [ ] **Step 3: 编写成功路径失败测试**

使用现有测试中的完整 fake 输出，断言成功路径通过 `AnalysisCrew → DeterministicVerdictTool → ReportCrew`，且最终结果保留现有 `status`、`analysis`、`verdict`、`report` 字段。

- [ ] **Step 4: 先运行测试并确认按预期失败**

Run:

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_research_flow -q
```

Expected: FAIL，原因是 `ResearchFlow` 和 `ResearchFlowState` 尚未实现，而不是导入错误或测试拼写错误。

### Task 2: 实现 Pydantic State 与 Flow 节点（Luna Max）

**Files:**
- Create: `src/stockcrewai/research_flow.py`

**Interfaces:**
- Consumes: `ResearchFlowState` 测试契约、现有 `main.py` 的 JSON/验证/输入构造辅助函数、三个 Crew 和确定性工具。
- Produces: `ResearchFlowState`、`ResearchFlow`、`@start/@listen/@router` 原生节点和依赖注入接口。

- [ ] **Step 1: 定义安全的 Pydantic 状态模型**

使用 `BaseModel`、`Field(default_factory=...)`、`Literal` 和 `Any` 的 JSON-safe 边界；不放 Crew、工具对象、Prompt 或密钥到 State。状态字段应覆盖现有确定性输出、Analysis 结果、阻断诊断和最终报告。

- [ ] **Step 2: 实现唯一 `@start()` 节点**

`parse_request` 负责读取 `self.state.request`，调用 RequestParser Crew，解析结构化请求并写入 State。解析错误转成统一错误状态；不要从多个无条件 start 并行修改同一状态。

- [ ] **Step 3: 实现 SEC/计算/验证节点**

用 `@listen(parse_request)` 执行实体候选检查、EDGAR、计算器、验证器和状态同步。复用现有确定性辅助函数，不能把网络来源选择交给 Agent。

- [ ] **Step 4: 实现市场估值节点**

用 `@listen(prepare_evidence)` 获取市场价格，执行当前估值、历史估值和反向 DCF，并将验证状态写入 State。

- [ ] **Step 5: 实现 Analysis Gate 路由**

使用 `@router(prepare_valuation)` 调用现有 `_analysis_gate`，只返回稳定标签 `analysis_ready` 或 `analysis_blocked`。阻断原因写入 `required_data`，不得让自然语言或 LLM 决定路由。

- [ ] **Step 6: 实现 Analysis Crew 与 Claim Gate 路由**

`@listen("analysis_ready")` 构造三个 role-scoped 输入并调用 Analysis Crew；随后用 `@router(run_analysis)` 执行现有 Claim Gate，返回 `claims_ready` 或 `claims_blocked`。任何域失败都清空下游可用 Claims 并保留脱敏诊断。

- [ ] **Step 7: 实现 Verdict、Report 和最终状态节点**

`@listen("claims_ready")` 先调用确定性 Verdict，再调用 Report Crew，最后把结果写入 State 并返回与旧 `run_research()` 兼容的字典。阻断分支返回统一的 `status/stage/required_data/next_action`。

- [ ] **Step 8: 为每个函数补中文 docstring**

每个 State/Flow 方法必须说明职责、输入、输出、State 变化、阻断条件和是否调用 Crew/确定性工具。

- [ ] **Step 9: 运行 Task 1 测试确认变绿**

Run:

```bash
CREWAI_STORAGE_DIR=/private/tmp/stockcrewai-flow-storage UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_research_flow -q
```

Expected: PASS。

### Task 3: 将兼容入口切换到原生 Flow（Luna Max）

**Files:**
- Modify: `src/stockcrewai/main.py`
- Modify: `tests/test_crew_configuration.py`
- Modify: `tests/test_runtime_defaults.py`
- Modify: `tests/test_run_and_save_output.py`

**Interfaces:**
- Consumes: Task 2 的 `ResearchFlow` 和 `ResearchFlowState`。
- Produces: `run_research()` 通过 Flow 执行，现有依赖注入和 CLI 行为保持不变。

- [ ] **Step 1: 保留 main.py 的通用序列化、脱敏、Claim Gate 辅助函数**

只移除或停用原 `run_research()` 中的手写总编排，不删除仍被 `research_flow.py` 使用的辅助函数。必要时用局部导入避免循环依赖。

- [ ] **Step 2: 重写 `run_research()` 为 Flow façade**

保持原函数签名和所有依赖注入参数，将它们传给 `ResearchFlow`，调用 `flow.kickoff(inputs={"request": request})`，并把最终输出转换为旧接口的 JSON-safe 字典。

- [ ] **Step 3: 让 `kickoff()` 和 `cli()` 继续工作**

`main()` 的请求优先级不变；`cli()` 继续通过 `_TeeTextStream` 将 Flow 原生框体、轨迹、最终 JSON 和异常复制到终端与 `run-output.md`。

- [ ] **Step 4: 处理 SQLite/运行目录**

使用 CrewAI 原生 Flow persistence 配置，但不要把外部 API 原文和密钥写入持久化 State。为测试提供 `CREWAI_STORAGE_DIR` 临时目录；为本地运行使用项目约定的 CrewAI 存储位置，并更新 `.gitignore` 避免提交数据库/向量存储。

- [ ] **Step 5: 更新兼容测试并运行回归**

Run:

```bash
CREWAI_STORAGE_DIR=/private/tmp/stockcrewai-flow-storage UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_crew_configuration tests.test_runtime_defaults tests.test_run_and_save_output -q
```

Expected: PASS，且已有工具测试不需要改动。

### Task 4: Flow 原生输出与全量验证（Luna Max review/fix）

**Files:**
- Modify: `src/stockcrewai/research_flow.py` only for review fixes
- Modify: `src/stockcrewai/main.py` only for review fixes
- Modify: `tests/test_research_flow.py` only for review fixes

**Interfaces:**
- Consumes: Task 1-3 的 Flow、状态、CLI 和测试。
- Produces: 官方 Flow 框体、完整中文 docstring、可定位阻断原因和稳定离线回归。

- [ ] **Step 1: 检查原生 Flow 框体**

用最小 fake Flow 运行验证终端出现 `Flow Execution`、`Flow Method Running`、`Flow Method Completed` 或等价 CrewAI 原生框体；自定义轨迹只补充数据摘要，不重复伪造框架生命周期事件。

- [ ] **Step 2: 检查成功、Analysis Gate 阻断、Claim Gate 阻断三条路径**

断言每条路径的状态、阶段、缺失项、下游调用次数和 State 结果一致。

- [ ] **Step 3: 检查每个新增函数的中文 docstring 和代码风格**

使用 AST 检查类/方法/函数文档字符串，使用 `git diff --check` 检查空白。

- [ ] **Step 4: 运行全部离线测试和编译检查**

Run:

```bash
CREWAI_STORAGE_DIR=/private/tmp/stockcrewai-flow-storage UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest discover -s tests -q
CREWAI_STORAGE_DIR=/private/tmp/stockcrewai-flow-storage UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m compileall -q src/stockcrewai
git diff --check
```

Expected: 全部通过；任何网络/API 不可用都不应影响离线测试结果。
