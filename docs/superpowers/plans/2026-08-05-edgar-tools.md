# EDGAR Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 AgentVest 环境中实现 EDGAR 数据、Decimal 财务计算和确定性验证工具，并接入现有 Analysis Crew。

**Architecture:** `EdgarTool` 延迟调用 `edgar.Company`，将公司身份、固定 SEC filings 和默认 Company Facts 归一化为 Pydantic Evidence。`FinancialCalculatorTool` 与 `FinancialValidationTool` 只处理结构化输入，不承担 LLM 推理；Analysis Crew 在 Python 中显式注册与职责匹配的工具。

**Tech Stack:** Python 3.12、Conda AgentVest、CrewAI 1.15.11 `BaseTool`、edgartools 5.45.1、Pydantic v2、`decimal.Decimal`、stdlib `unittest`。

## Global Constraints

- 运行命令使用 `conda run -n AgentVest`。
- 不创建虚拟环境，不安装新依赖，不自动提交 Git。
- 保持 5 个 Agent、3 个 Crew，不增加 Agent。
- LLM 只解释已验证结构化数据；事实、计算和验证由 Python 完成。
- 默认测试禁止真实 SEC、DeepSeek 和付费 API 调用。
- 缺失数据返回 unavailable，禁止以零代替。

---

### Task 1: 为工具契约写失败测试

**Files:**
- Create: `tests/test_financial_tools.py`
- Test: `tests/test_financial_tools.py`

**Interfaces:**
- Consumes: 尚不存在的 `stockcrewai.tools` 导出。
- Produces: 覆盖 `EdgarTool`、`FinancialCalculatorTool`、`FinancialValidationTool` 的可执行行为和测试 fake。

- [x] **Step 1: Write the failing test**

  添加以下行为测试：公司名解析为 CIK、固定 10-K/10-Q/8-K 数量和 Evidence 字段；计算收入增长与 FCF；缺失输入返回 unavailable；验证器拒绝错误计算结果。

- [x] **Step 2: Run test to verify it fails**

  Run: `PYTHONPATH=src conda run --no-capture-output -n AgentVest python -m unittest tests.test_financial_tools -v`

  Expected: FAIL because `stockcrewai.tools` and the three tools do not exist.

### Task 2: 实现 EDGAR 归一化工具

**Files:**
- Create: `src/stockcrewai/tools/edgar_tool.py`
- Create: `src/stockcrewai/tools/__init__.py`
- Modify: `.env.example`

**Interfaces:**
- Consumes: `company_name`/`ticker`、`EDGAR_IDENTITY` 和 edgartools `Company`/`find_company`。
- Produces: `EdgarTool.run(...) -> EdgarResult`，固定返回 3 份 10-K、4 份 10-Q、180 天内最多 20 份 8-K 以及默认 facts。

- [x] **Step 1: Implement only the input/output Pydantic models and lazy module loader**
- [x] **Step 2: Implement company resolution and structured error handling**
- [x] **Step 3: Implement filing and fact normalization with deterministic Evidence IDs**
- [x] **Step 4: Run the EDGAR tests**

  Run: `PYTHONPATH=src conda run --no-capture-output -n AgentVest python -m unittest tests.test_financial_tools.EdgarToolTests -v`

  Expected: PASS without network access.

### Task 3: 实现 Decimal 计算器和验证器

**Files:**
- Create: `src/stockcrewai/tools/calculator_tool.py`
- Create: `src/stockcrewai/tools/validation_tool.py`
- Modify: `src/stockcrewai/tools/__init__.py`

**Interfaces:**
- Consumes: `facts: dict[str, Any]`，事实值可为字符串或 `{value, evidence_id}` 对象。
- Produces: `CalculationBatch` 和 `ValidationResult`，所有数值以字符串输出，计算使用 Decimal。

- [x] **Step 1: Implement the formula registry and Decimal conversion**
- [x] **Step 2: Implement unavailable results for missing/non-finite inputs**
- [x] **Step 3: Implement formula recomputation and validation issues**
- [x] **Step 4: Run calculator and validator tests**

  Run: `PYTHONPATH=src conda run --no-capture-output -n AgentVest python -m unittest tests.test_financial_tools.CalculatorToolTests tests.test_financial_tools.ValidationToolTests -v`

  Expected: PASS with no network call.

### Task 4: 接入 Analysis Crew 和配置描述

**Files:**
- Modify: `src/stockcrewai/crews/analysis/crew.py`
- Modify: `src/stockcrewai/crews/analysis/config/agents.yaml`
- Modify: `src/stockcrewai/crews/analysis/config/tasks.yaml`
- Modify: `tests/test_crew_configuration.py`

**Interfaces:**
- Consumes: 三个工具类及其 typed outputs。
- Produces: FinancialQualityAgent 使用 EDGAR/计算/验证，RiskAnalysisAgent 使用 EDGAR，ValuationAnalysisAgent 使用计算/验证；任务文本约束 Evidence ID 和 Calculation ID。

- [x] **Step 1: Register tools in the three existing agent factory methods**
- [x] **Step 2: Update YAML output contracts to match tool payloads**
- [x] **Step 3: Add configuration tests for tool ownership and no tools on parser/report agents**
- [x] **Step 4: Run the full unit test suite**

  Run: `PYTHONPATH=src conda run --no-capture-output -n AgentVest python -m unittest discover -s tests -p 'test_*.py' -v`

  Expected: all tests pass without a real LLM kickoff.

### Task 5: 完成验证

**Files:**
- No additional files.

- [x] **Step 1: Compile the source tree**

  Run: `conda run --no-capture-output -n AgentVest python -m compileall -q src tests`

- [x] **Step 2: Check whitespace and stale template references**

  Run: `git diff --check` and `rg -n 'content_crew|custom_tool|OPENAI_API_KEY' src tests README.md pyproject.toml || true`

- [x] **Step 3: Confirm AgentVest package versions without printing secrets**

  Run: `conda run --no-capture-output -n AgentVest python -c 'import crewai, edgar; print(crewai.__version__, edgar.__version__)'`
