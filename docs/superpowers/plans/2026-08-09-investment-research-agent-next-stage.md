# StockCrewAI 下一阶段投研能力 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前“可审计财务快照 + 简化估值”升级为报告产物一致、覆盖完整 SEC 核心章节、补齐 V1 指标，并能按公司质量、趋势、风险和投资期限给出确定性解释的单公司投研系统。

**Architecture:** 保持现有 `ResearchFlow` 为唯一编排入口。SEC 选源、期间选择、财务计算、历史对齐、评分和 Verdict 全部由确定性 Python 完成；LLM Agent 只解释已验证的结构化输入并生成带 Evidence ID / Calculation ID 的 Claims。所有新增能力必须先进入 Evidence/Calculation/Claim Gate，再进入 Report Renderer。

**Tech Stack:** Python 3.12、CrewAI 1.15.x、Pydantic 2、Decimal、edgartools、yfinance、Matplotlib、SQLite Flow persistence、`unittest`、uv。

## Global Constraints

- 执行任何 CrewAI 代码修改前，先运行 `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -c "import crewai; print(crewai.__version__)"`，再核对 PyPI、CrewAI changelog 和相关官方文档。
- 所有命令使用当前 uv 项目环境；不创建额外虚拟环境，不执行无关依赖升级。
- 默认测试不得访问 SEC、Yahoo、FRED、付费 LLM 或其他实时网络服务。
- 所有金额和核心比率使用 `Decimal`，precision=28、`ROUND_HALF_EVEN`；JSON 中保存字符串，禁止 NaN/Infinity。
- LLM 不选择数据源、不计算指标、不验证数字、不决定评级。
- SEC Evidence 必须包含 `evidence_id`、期间、单位、filed_at、accession number 和 source reference；必须继续执行 `filed_at <= as_of`。
- rejected Claim 永远不能进入 Verdict 或报告。
- 报告中出现的数字必须来自已验证 `ReportMetric` 或确定性 Renderer，不能直接复制 Agent 自由文本数字。
- 保持 `crewai run` 为唯一正常运行命令；不得要求用户额外指定输出文件。
- 保留现有 `investment-report.md`、`run-output.md`、`run-result.json` 公共文件名和兼容字段。
- 每项任务严格执行 RED → GREEN → REFACTOR；每项任务独立提交，禁止夹带用户现有输出文件修改。

---

## 1. 当前基线与已确认问题

### 已具备

- Request Parser Agent，可输出公司候选、ticker、关注方向和投资期限。
- 确定性 SEC 公司身份、Facts、filing 元数据和 Evidence ID。
- 10 个当前财务公式、7 个 TTM 指标和批量验证。
- 市场价格、市值、P/E TTM、FCF Yield。
- 五年月末历史 P/E、中位数、25/75 分位和当前百分位。
- 10 年简化反向 DCF 与三组敏感性场景。
- Financial Quality Agent、Risk Analysis Agent、Report Writer Agent。
- Analysis Gate、Claim Gate、确定性 Verdict 和 Final Report Validator。
- Markdown 报告、三张图表、紧凑终端阶段输出和完整 JSON 结果。

### 当前工作区已确认的缺口

1. `investment-report.md` 与 `run-result.json["report"]` 当前不一致：正文长度不同，前者显示 1 条风险，后者包含 3 条风险。正式报告缺少单一权威产物保证。
2. `EdgarToolInput.max_text_chars` 默认 12,000；10-K/10-Q 文本被截断后，`_extract_risk_sections` 直接拒绝提取，因此 Item 1A / 10-Q 风险更新没有稳定进入 Risk Agent。
3. V1 财务公式缺少：`revenue_cagr_3y`、`gross_margin`、`operating_cash_flow_margin`。
4. V1 当前估值缺少：`earnings_yield`、`price_to_sales`、`net_cash_per_share`。
5. 当前“财务趋势”主要是一组 TTM 快照和单次同比增长，不是连续 3～5 年趋势。
6. `_verdict_risk_input` 只要存在风险 Claim 就固定返回 `medium`；风险等级不是实际严重度评分。
7. `VerdictResult.business_quality` 和 `financial_trend` 只返回 `available`，没有质量或趋势等级。
8. 已解析 `investment_horizon`，但 Verdict 阈值和报告解释没有使用投资期限。
9. 报告没有独立的业务模式、分部、地区、管理层解释和资本配置章节。

## 2. 权威数据流

```text
用户请求
  -> RequestParserAgent（候选语义）
  -> EntityResolver（确定 CIK/ticker）
  -> SECDataPipeline（Facts + 完整章节 Evidence）
  -> EvidenceValidator
  -> TTMBuilder / TrendBuilder
  -> FinancialCalculator / ValuationCalculator
  -> CalculationValidator
  -> FinancialQualityAgent + BusinessAnalysisAgent + RiskAnalysisAgent
  -> Claim Gate
  -> BusinessQualityScore + TrendScore + RiskScore + ValuationScore
  -> HorizonAwareVerdict
  -> ReportWriterAgent（无数字叙述草稿）
  -> Deterministic Renderer
  -> FinalReportValidator
  -> Atomic Output Writer（报告、JSON、摘要同源）
```

---

### Task 1: 保证正式报告与 JSON 结果完全一致

**Files:**
- Modify: `src/stockcrewai/run_output.py:557-568`
- Modify: `src/stockcrewai/run_output.py:862-910`
- Modify: `src/stockcrewai/main.py:1616-1680`
- Test: `tests/test_run_and_save_output.py`
- Test: `tests/test_main_flow.py`

**Interfaces:**
- Consumes: Flow 返回的 `result: Mapping[str, Any]`，其中 `status="ok"`、`stage="report"`、`report: str`。
- Produces: `ArtifactManifest` JSON 字段：`report_path`、`report_sha256`、`report_bytes`；磁盘报告内容必须与持久化 JSON 中的 `report` 字段逐字一致（只允许统一末尾一个换行符）。

- [ ] **Step 1: 写出复现当前不一致的失败测试**

```python
def test_finalize_persists_one_authoritative_report(tmp_path):
    report = "# 正式报告\n\n- 风险一\n- 风险二\n"
    result = {"status": "ok", "stage": "report", "report": report}
    output_path = tmp_path / "run-output.md"
    result_path = tmp_path / "run-result.json"

    reporter = CompactRunReporter(StringIO())
    reporter.finalize(
        result=result,
        output_path=output_path,
        result_path=result_path,
        started_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        finished_at=datetime(2026, 8, 9, 0, 1, tzinfo=timezone.utc),
        exit_code=0,
    )

    persisted = json.loads(result_path.read_text(encoding="utf-8"))
    exported = (tmp_path / "investment-report.md").read_text(encoding="utf-8")
    assert exported == persisted["report"]
    assert persisted["artifacts"]["report_sha256"] == hashlib.sha256(
        exported.encode("utf-8")
    ).hexdigest()
```

- [ ] **Step 2: 运行目标测试确认 RED**

Run: `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_run_and_save_output`

Expected: FAIL，原因是当前 JSON 在正式报告写出前落盘，且没有 artifact hash 契约。

- [ ] **Step 3: 实现原子写入与 ArtifactManifest**

在 `run_output.py` 增加私有函数：

```python
def _normalized_report(report: str) -> str:
    return report.rstrip("\r\n") + "\n"


def _atomic_write_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)
```

`CompactRunReporter.finalize` 必须按照以下顺序执行：

1. 创建 `persisted_result = _json_ready(result)`。
2. 若结果为正式报告，先规范化并原子写入 `investment-report.md`。
3. 计算 UTF-8 内容的 SHA-256 和字节数。
4. 将 manifest 写入 `persisted_result["artifacts"]`。
5. 原子写入 `run-result.json`。
6. 最后写 `run-output.md`，摘要中的正式报告名称来自 manifest。

- [ ] **Step 4: 增加故障安全测试**

覆盖以下情况：

- `status="blocked"` 不覆盖已有正式报告。
- 报告为空不创建文件。
- 原子替换失败时 JSON 不声称报告已成功写出。
- 自定义 `output_path` 时三个产物写入同一目录。

- [ ] **Step 5: 运行回归测试**

Run: `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_run_and_save_output tests.test_main_flow`

Expected: 所有测试 PASS；磁盘报告、JSON report 和 hash 一致。

- [ ] **Step 6: 提交**

```bash
git add src/stockcrewai/run_output.py src/stockcrewai/main.py tests/test_run_and_save_output.py tests/test_main_flow.py
git commit -m "fix: make formal report artifact authoritative"
```

---

### Task 2: 在截断预览前提取完整 SEC 核心章节

**Files:**
- Modify: `src/stockcrewai/tools/edgar_tool.py:40-125`
- Modify: `src/stockcrewai/tools/edgar_tool.py:249-389`
- Modify: `src/stockcrewai/pipeline_support.py:920-980`
- Test: `tests/test_edgar_tool.py`
- Create: `tests/test_analysis_gate.py`

**Interfaces:**
- Consumes: 完整 filing HTML/text、本次研究 `as_of`、form、accession number。
- Produces: 每个章节独立的 `EdgarSectionEvidence`；传给 Agent 的是章节正文，不是 filing 开头预览。

定义章节结构：

```python
class EdgarSectionEvidence(BaseModel):
    evidence_id: str
    form: str
    section_name: str
    text: str
    filed_at: str
    accession_number: str
    source_reference: str
    text_truncated: bool = False
    validation_status: Literal["unvalidated", "valid", "invalid"] = "unvalidated"
```

- [ ] **Step 1: 添加完整章节提取失败测试**

测试 fixture 必须让 Item 1A 出现在第 12,000 字符之后，并断言：

```python
assert filing.text_truncated is True
assert filing.risk_sections[0].section_name == "Item 1A"
assert "供应链风险正文" in filing.risk_sections[0].text
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_edgar_tool`

Expected: FAIL，因为当前先执行 `raw_text[:max_text_chars]`，并在 `text_truncated=True` 时拒绝章节提取。

- [ ] **Step 3: 调整提取顺序**

实现顺序必须固定为：

1. 获取完整 `raw_text`。
2. 按 form 从完整文本提取章节。
3. 对每个章节独立限制最大长度 30,000 字符，并记录该章节是否截断。
4. filing 的通用 `text` 字段继续只保存 12,000 字符预览。
5. 删除“只要通用 text 被截断就拒绝所有风险章节”的判断。

首阶段章节映射：

```python
SECTION_PLAN = {
    "10-K": ("Item 1", "Item 1A", "Item 7", "Item 7A"),
    "10-Q": ("Part I Item 2", "Part II Item 1A"),
    "8-K": ("Item 1.01", "Item 2.02", "Item 5.02", "Item 5.07", "Item 8.01"),
}
```

- [ ] **Step 4: 增加章节 Evidence 验证**

验证条件：form、section_name、filed_at、accession、source 和非空 text 必须存在；`filed_at > as_of` 必须拒绝；章节 Evidence ID 使用 form、accession、section_name 的稳定 hash。

- [ ] **Step 5: 增加门禁测试**

- 10-K Item 1A 可用时 Analysis Gate 为 READY。
- 只有通用 filing 预览但无章节时返回 `risk_sections_required`。
- 截断的是章节本身时允许传入，但必须携带 `text_truncated=True`，Risk Agent 不得声称风险清单完整。

- [ ] **Step 6: 运行测试并提交**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_edgar_tool tests.test_analysis_gate
git add src/stockcrewai/tools/edgar_tool.py src/stockcrewai/pipeline_support.py tests/test_edgar_tool.py tests/test_analysis_gate.py
git commit -m "feat: extract SEC sections before filing preview truncation"
```

---

### Task 3: 补齐 V1 财务公式

**Files:**
- Modify: `src/stockcrewai/tools/edgar_tool.py:15-42`
- Modify: `src/stockcrewai/tools/calculator_tool.py:8-145`
- Modify: `src/stockcrewai/tools/validation_tool.py`
- Modify: `src/stockcrewai/pipeline_support.py`
- Modify: `src/stockcrewai/crews/report/crew.py:87-160`
- Modify: `tests/test_financial_tools.py`
- Create: `tests/test_validation_tool.py`
- Create: `tests/test_report_crew.py`

**Interfaces:**
- Produces new formula IDs: `revenue_cagr_3y`、`gross_margin`、`operating_cash_flow_margin`。
- 所有结果继续使用现有 `CalculationResult` 契约。

固定公式：

```python
revenue_cagr_3y = (revenue_current_fy / revenue_three_years_ago_fy) ** (Decimal("1") / Decimal("3")) - Decimal("1")
gross_margin = gross_profit / revenue_current
operating_cash_flow_margin = operating_cash_flow / revenue_current
```

- [ ] **Step 1: 为三个公式写参数化失败测试**

使用手工推导 fixture：

```python
facts = {
    "revenue_current_fy": Decimal("172800000000"),
    "revenue_three_years_ago_fy": Decimal("100000000000"),
    "gross_profit": Decimal("72000000000"),
    "revenue_current": Decimal("180000000000"),
    "operating_cash_flow": Decimal("54000000000"),
}
```

期望：3 年 CAGR=20%、毛利率=40%、经营现金流利润率=30%。CAGR 不得使用 float 幂运算；使用 Decimal 上下文中的 `ln/exp` 或经测试的 Decimal root 实现。

- [ ] **Step 2: 运行测试确认 RED**

Run: `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_calculator_tool`

- [ ] **Step 3: 扩展 SEC Facts 计划**

新增规范化事实：

- `gross_profit`
- `revenue_current_fy`
- `revenue_three_years_ago_fy`

每个年度事实必须来自完整财年 10-K，不能用当前 YTD 冒充完整财年；若三年前财年不可比，`revenue_cagr_3y` 返回 unavailable。

- [ ] **Step 4: 实现公式和重新计算验证**

同步修改：

- `DEFAULT_FORMULAS`
- `FORMULA_INPUTS`
- `calculate_formula`
- Calculation Validator 的公式分支
- Report metric label、排序和百分比格式

- [ ] **Step 5: 添加缺失/零分母/期间不匹配测试**

三个公式分别覆盖：缺 Evidence ID、分母为零、单位不同、年度跨度不是三年、非有限 Decimal。

- [ ] **Step 6: 运行测试并提交**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_calculator_tool tests.test_validation_tool tests.test_report_crew
git add src/stockcrewai/tools/edgar_tool.py src/stockcrewai/tools/calculator_tool.py src/stockcrewai/tools/validation_tool.py src/stockcrewai/pipeline_support.py src/stockcrewai/crews/report/crew.py tests/test_calculator_tool.py tests/test_validation_tool.py tests/test_report_crew.py
git commit -m "feat: complete baseline financial metrics"
```

---

### Task 4: 补齐 V1 当前估值指标

**Files:**
- Modify: `src/stockcrewai/tools/valuation_tool.py`
- Modify: `src/stockcrewai/tools/validation_tool.py`
- Modify: `src/stockcrewai/pipeline_support.py`
- Modify: `src/stockcrewai/crews/report/crew.py`
- Test: `tests/test_valuation_tool.py`
- Modify: `tests/test_report_crew.py`
- Test: `tests/test_valuation_claim_stability.py`

**Interfaces:**
- Produces new formula IDs: `earnings_yield`、`price_to_sales`、`net_cash_per_share`。
- Consumes validated TTM net income、TTM revenue、net cash、current shares、market cap。

固定公式：

```python
earnings_yield = ttm_net_income / market_capitalization
price_to_sales = market_capitalization / ttm_revenue
net_cash_per_share = net_cash / common_shares_outstanding
```

- [ ] **Step 1: 写三个估值结果的失败测试**

fixture：market cap=500、TTM net income=20、TTM revenue=100、net cash=-10、shares=5。期望：Earnings Yield=4%、P/S=5x、每股净现金=-2。

- [ ] **Step 2: 运行测试确认 RED**

Run: `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_valuation_tool`

- [ ] **Step 3: 扩展 ValuationTool**

同步修改 `VALUATION_FORMULAS`、facts aliases、输入完备性、Evidence ID 聚合、显示单位和 `period_basis`。Earnings Yield 与 P/S 必须使用 TTM；每股净现金使用最新资产负债表时点。

- [ ] **Step 4: 扩展确定性估值 Claims 和报告**

每个新增指标必须具有独立 Calculation ID；Report Renderer 按以下顺序展示：价格、市值、P/E、Earnings Yield、P/S、FCF Yield、每股净现金。

- [ ] **Step 5: 增加不一致口径测试**

- TTM revenue 不可用时只使 P/S unavailable。
- net cash 为负时每股净现金保留负号。
- shares=0 时拒绝每股净现金。
- 市场价格过期时所有依赖市场价格的估值保持 not_ready。

- [ ] **Step 6: 运行测试并提交**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_valuation_tool tests.test_report_crew tests.test_valuation_claim_stability
git add src/stockcrewai/tools/valuation_tool.py src/stockcrewai/tools/validation_tool.py src/stockcrewai/pipeline_support.py src/stockcrewai/crews/report/crew.py tests/test_valuation_tool.py tests/test_report_crew.py tests/test_valuation_claim_stability.py
git commit -m "feat: complete baseline valuation metrics"
```

---

### Task 5: 建立多期间趋势和资本效率模块

**Files:**
- Create: `src/stockcrewai/tools/trend_tool.py`
- Modify: `src/stockcrewai/tools/__init__.py`
- Modify: `src/stockcrewai/main.py`
- Modify: `src/stockcrewai/pipeline_support.py`
- Modify: `src/stockcrewai/report_visuals.py`
- Modify: `src/stockcrewai/crews/report/crew.py`
- Create: `tests/test_trend_tool.py`
- Modify: `tests/test_report_visuals.py`

**Interfaces:**
- Consumes: 5 个完整财年和最多 8 个可比季度的已验证 Evidence。
- Produces: `TrendResult`，包含 series、CAGR、margin direction、ROIC、incremental ROIC、warnings 和 validation_status。

```python
class TrendPoint(BaseModel):
    period_end: date
    filed_at: date
    revenue: str | None
    gross_margin: str | None
    operating_margin: str | None
    fcf_margin: str | None
    shares_outstanding: str | None
    evidence_ids: list[str]


class TrendResult(BaseModel):
    status: Literal["ok", "partial", "unavailable"]
    annual: list[TrendPoint]
    quarterly: list[TrendPoint]
    revenue_cagr_3y: str | None
    roic: str | None
    incremental_roic_3y: str | None
    validation_status: Literal["unvalidated", "valid", "invalid"]
    warnings: list[str]
```

首版 ROIC 口径固定为：

```text
effective_tax_rate = income_tax_expense / pretax_income
NOPAT = operating_income × (1 - effective_tax_rate)
invested_capital = average(total_debt + stockholders_equity - cash_and_short_term_investments)
ROIC = NOPAT / invested_capital
incremental_ROIC_3y = change_in_NOPAT / change_in_invested_capital
```

- [ ] **Step 1: 写期间排序和禁止前视偏差的失败测试**

测试 `filed_at > as_of` 的点被拒绝；年度和季度不得混排；53 周财年只在明确标记后参与比较。

- [ ] **Step 2: 写 ROIC 手算测试并确认 RED**

使用 NOPAT=75、平均投入资本=300，期望 ROIC=25%；投入资本变化为 50、NOPAT 变化为 10，期望增量 ROIC=20%。

- [ ] **Step 3: 实现 TrendTool 和验证逻辑**

TrendTool 不调用网络，只消费已验证的历史 Evidence。部分年份缺失时返回 partial，不用 0 填补。

- [ ] **Step 4: 新增两张报告图**

1. 5 年收入与 FCF 趋势：同一时间轴、双面板，不使用容易误导的双 Y 轴。
2. 5 年利润率趋势：毛利率、营业利润率、FCF 利润率折线。

图表必须动态设置坐标范围，标签不得遮挡，测试真实 renderer bbox。

- [ ] **Step 5: 将 TrendResult 送入 FinancialQualityAgent**

Agent 只能解释趋势方向和已验证转折点；每条 Claim 必须引用相关 Evidence ID 和 Calculation ID。

- [ ] **Step 6: 运行测试并提交**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_trend_tool tests.test_report_visuals
git add src/stockcrewai/tools/trend_tool.py src/stockcrewai/tools/__init__.py src/stockcrewai/main.py src/stockcrewai/pipeline_support.py src/stockcrewai/report_visuals.py src/stockcrewai/crews/report/crew.py tests/test_trend_tool.py tests/test_report_visuals.py
git commit -m "feat: add multi-period trends and capital efficiency"
```

---

### Task 6: 增加 Business/MD&A 分析域

**Files:**
- Modify: `src/stockcrewai/crews/analysis/config/agents.yaml`
- Modify: `src/stockcrewai/crews/analysis/config/tasks.yaml`
- Modify: `src/stockcrewai/crews/analysis/crew.py`
- Modify: `src/stockcrewai/pipeline_support.py`
- Modify: `src/stockcrewai/main.py`
- Modify: `src/stockcrewai/crews/report/config/agents.yaml`
- Modify: `src/stockcrewai/crews/report/config/tasks.yaml`
- Modify: `src/stockcrewai/crews/report/crew.py`
- Create: `tests/test_analysis_crew.py`
- Modify: `tests/test_report_crew.py`
- Modify: `tests/test_main_flow.py`

**Interfaces:**
- Consumes: 已验证 10-K Item 1、Item 7、财务附注章节 Evidence，以及确定性分部/地区表格。
- Produces Claim categories: `business_model`、`capital_allocation`。

- [ ] **Step 1: 修改 CrewAI 前执行版本与文档核对**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -c "import crewai; print(crewai.__version__)"
```

同时查看 CrewAI agents、tasks、structured output 和 guardrails 官方文档；若实时文档与根目录 `AGENTS.md` 冲突，以实时文档为准并记录差异。

- [ ] **Step 2: 为 business 域契约写失败测试**

扩展：

```python
ANALYSIS_DOMAIN_RULES["business"] = (
    frozenset({"business_model", "capital_allocation"}),
    False,
)
```

Business Claims 必须引用非空 Evidence ID，Calculation ID 必须为空；禁止未经来源的“护城河强”“行业领先”等结论。

- [ ] **Step 3: 添加 BusinessAnalysisAgent 和 Task**

职责必须限制为：

- Item 1：产品、服务、客户、市场、竞争和地区。
- Item 7：管理层解释的收入、利润和现金流驱动。
- 财务附注：分部、地区和资本配置披露。
- 不得搜索互联网，不得预测，不得评级，不得计算。

- [ ] **Step 4: 扩展 Claim Gate 白名单**

Business Agent 只能引用 `business_analysis_input.validated_section_ids`；任何不存在的 section ID 必须拒绝，且 rejected Claim 不进入 Report Context。

- [ ] **Step 5: 扩展 ReportDraft 和 Renderer**

新增固定字段 `business_overview`，置于执行摘要之后、公司质量之前。Renderer 展示：业务构成、管理层解释、资本配置、分部和地区数据；所有数字仍由 ReportMetric 注入。

- [ ] **Step 6: 运行测试并提交**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_analysis_crew tests.test_report_crew tests.test_main_flow
git add src/stockcrewai/crews/analysis src/stockcrewai/crews/report src/stockcrewai/pipeline_support.py src/stockcrewai/main.py tests/test_analysis_crew.py tests/test_report_crew.py tests/test_main_flow.py
git commit -m "feat: add evidence-bound business analysis domain"
```

---

### Task 7: 建立风险变化与确定性风险评分

**Files:**
- Create: `src/stockcrewai/tools/risk_scoring_tool.py`
- Modify: `src/stockcrewai/tools/__init__.py`
- Modify: `src/stockcrewai/pipeline_support.py:983-1024`
- Modify: `src/stockcrewai/main.py`
- Modify: `src/stockcrewai/crews/analysis/config/agents.yaml`
- Modify: `src/stockcrewai/crews/analysis/config/tasks.yaml`
- Create: `tests/test_risk_scoring_tool.py`
- Modify: `tests/test_analysis_crew.py`
- Modify: `tests/test_main_flow.py`

**Interfaces:**
- Consumes: Claim Gate 已接受的风险 Claims、section metadata、与上一期 Item 1A 的确定性文本 diff。
- Produces: `RiskScoreResult`，不依赖 LLM 自由给分。

```python
class RiskScoreResult(BaseModel):
    status: Literal["available", "unavailable"]
    risk_level: Literal["low", "medium", "high"] | None
    score: int | None
    categories: dict[str, int]
    new_risk_count: int
    escalated_risk_count: int
    claim_ids: list[str]
    evidence_ids: list[str]
    policy_version: str = "v2"
```

固定评分规则：

```text
已发生且具有量化财务影响：+3
新增重大 Item 1A 风险：+2
10-Q 明确升级既有风险：+2
重大诉讼、监管或持续经营事件：+3
仅前瞻性模板风险：+0
仅股东提案或投票分歧：+1

0-2 = low
3-5 = medium
6+ = high
```

- [ ] **Step 1: 写风险级别失败测试**

覆盖：只有股东提案为 low；一个已发生监管处罚为 medium；监管处罚加供应链停产为 high；没有完整 Item 1A 时 status=unavailable。

- [ ] **Step 2: 运行测试确认 RED**

Run: `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_risk_scoring_tool`

- [ ] **Step 3: 实现确定性 section diff**

按标准化标题和段落 hash 比较上一期/本期 Item 1A；输出 added、removed、changed，不让 LLM自行判断文本是否新增。

- [ ] **Step 4: 升级 Risk Agent 输出约束**

Risk Agent 只将确定性 diff 解释为 Claims；风险类别和是否“已发生”必须来自输入元数据，不允许 Agent 自由创造评分字段。

- [ ] **Step 5: 替换固定 medium 逻辑**

删除 `_verdict_risk_input` 中“存在 Claim 即 medium”的映射，改为只消费 `RiskScoreResult`。

- [ ] **Step 6: 运行测试并提交**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_risk_scoring_tool tests.test_analysis_crew tests.test_main_flow
git add src/stockcrewai/tools/risk_scoring_tool.py src/stockcrewai/tools/__init__.py src/stockcrewai/pipeline_support.py src/stockcrewai/main.py src/stockcrewai/crews/analysis/config/agents.yaml src/stockcrewai/crews/analysis/config/tasks.yaml tests/test_risk_scoring_tool.py tests/test_analysis_crew.py tests/test_main_flow.py
git commit -m "feat: add deterministic risk change scoring"
```

---

### Task 8: 将公司质量、趋势和投资期限纳入 Verdict v2

**Files:**
- Modify: `src/stockcrewai/tools/verdict_tool.py`
- Modify: `src/stockcrewai/pipeline_support.py`
- Modify: `src/stockcrewai/main.py`
- Modify: `src/stockcrewai/crews/report/crew.py`
- Test: `tests/test_verdict_tool.py`
- Test: `tests/test_main_flow.py`
- Test: `tests/test_report_crew.py`

**Interfaces:**
- Consumes: validated metrics、TrendResult、RiskScoreResult、估值、`investment_horizon`。
- Produces: `VerdictResult rules_version="v2"`，其中 business_quality、financial_trend、valuation、risk_level 都是实际等级，不再是 `available`。

确定性等级：

```text
business_quality: strong / adequate / weak / insufficient_data
financial_trend: improving / stable / deteriorating / insufficient_data
valuation: attractive / reasonable / expensive / insufficient_data
risk_level: low / medium / high / insufficient_data
overall_rating: attractive / reasonable / watchlist / expensive / insufficient_data
```

公司质量 v2 初始规则：

```text
strong：ROIC >= 20%，FCF margin >= 15%，cash conversion >= 90%，且净债务/FCF <= 2
adequate：ROIC >= 10%，FCF margin > 0，cash conversion >= 70%
weak：ROIC < 10%，或 FCF margin <= 0，或 cash conversion < 70%
```

趋势 v2 初始规则：

```text
improving：3 年收入 CAGR > 0，营业利润率未下降超过 2 个百分点，FCF 为正
deteriorating：3 年收入 CAGR < 0，或营业利润率下降超过 3 个百分点，或连续两年 FCF 为负
stable：其余已验证情况
```

期限只改变解释与安全边际要求，不改变历史事实：

```text
<=1 年：必须提示短期事件风险；不因 DCF 单独判 attractive
1-3 年：使用估值、趋势和已识别催化剂/风险
>3 年或长期：提高 ROIC、业务质量、反向 DCF 和风险持续性的权重
```

- [ ] **Step 1: 写 Verdict v2 表驱动失败测试**

至少覆盖 strong+improving+reasonable、weak、high risk、短期高估值、长期高质量但高估值、数据不足六种组合。

- [ ] **Step 2: 运行测试确认 RED**

Run: `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_verdict_tool`

- [ ] **Step 3: 扩展 Verdict 输入和结果模型**

所有阈值集中在 `verdict_tool.py` 的版本化常量中；报告只显示触发的规则代码及中文映射，不重新推导。

- [ ] **Step 4: 增加安全边际价格带**

确定性输出以下观察价格，不命名为买入价：

```text
historical_median_pe_price = ttm_eps × five_year_median_pe
fcf_yield_4pct_price = ttm_fcf / shares / 0.04
fcf_yield_5pct_price = ttm_fcf / shares / 0.05
```

报告称为“情景参考价格”，并明确不是投资建议。

- [ ] **Step 5: 运行测试并提交**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_verdict_tool tests.test_main_flow tests.test_report_crew
git add src/stockcrewai/tools/verdict_tool.py src/stockcrewai/pipeline_support.py src/stockcrewai/main.py src/stockcrewai/crews/report/crew.py tests/test_verdict_tool.py tests/test_main_flow.py tests/test_report_crew.py
git commit -m "feat: add horizon-aware verdict policy v2"
```

---

### Task 9: 建立端到端评估集和发布门禁

**Files:**
- Create: `tests/fixtures/evaluation/apple.json`
- Create: `tests/fixtures/evaluation/industrial.json`
- Create: `tests/fixtures/evaluation/negative_fcf.json`
- Create: `tests/fixtures/evaluation/missing_risk_section.json`
- Create: `tests/fixtures/evaluation/unsupported_bank.json`
- Create: `tests/test_research_evaluation.py`
- Modify: `README.md`
- Modify: `docs/Expectayion_Projects.md` completion ledger only after all tasks pass

**Interfaces:**
- Consumes: 完整 Flow 的可注入离线服务。
- Produces: 每次发布前可重复运行的研究质量基线。

- [ ] **Step 1: 创建五组不可联网 fixture**

每组固定包含 request、EntityRef、SEC Evidence、market evidence、expected gate status、expected metrics、expected Verdict 和禁止出现的 Claims。

- [ ] **Step 2: 写五类端到端测试**

断言：

- Apple 正常路径生成权威报告及 manifest。
- 工业公司能处理正常债务而不是错误判定净现金公司才优质。
- 负 FCF 公司不会生成 FCF Yield attractive 结论。
- 缺 Item 1A 时阻断风险评分或明确 unavailable，不能伪造 low risk。
- 银行请求由 Scope Gate 拒绝，不套用普通运营公司公式。

- [ ] **Step 3: 增加稳定性测试**

同一 fixture 连续运行 5 次；确定性指标、Gate、Verdict、报告数字和 artifact hash 必须一致。LLM 无数字叙述允许措辞变化，但 Claim IDs、引用 ID 集合和章节覆盖必须一致。

- [ ] **Step 4: 执行完整离线验证**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest discover -s tests -p 'test_*.py'
git diff --check
```

Expected: 0 failures；只允许已知 CrewAI deprecation warnings，不允许网络请求。

- [ ] **Step 5: 执行一次真实 Apple 验收**

关闭会阻断 SEC 的 VPN 路由，确保 Yahoo 路由可用后执行：

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync crewai run
```

验收：

- 终端七阶段全部 completed。
- `status=ok`、`stage=report`。
- `investment-report.md == run-result.json["report"]`。
- artifact SHA-256 校验通过。
- 报告包含完整业务概览、3～5 年趋势、6 个新增 V1 指标、完整 Item 1A 风险、RiskScore v2 和 Horizon-aware Verdict。
- 图表无文字遮挡，最大标签与坐标框保留可见间距。

- [ ] **Step 6: 提交**

```bash
git add tests/fixtures/evaluation tests/test_research_evaluation.py docs/Expectayion_Projects.md
git commit -m "test: add investment research release gate"
```

---

## 3. 后续阶段建议（不并入本轮实现）

以下能力应分别建立独立设计和实施计划，避免扩大首阶段变更面。

### Phase B1：确定性同业比较

- 用户或静态行业配置提供同行名单；LLM 不自行选择同行。
- 统一 as_of 和期间口径，计算收入 CAGR、ROIC、利润率、P/E、P/S、EV/FCF、FCF Yield。
- 输出公司在同行中的分位数及差异解释。
- 首批行业模板：大型科技、消费品、工业；银行、保险、REIT 使用独立指标体系。

### Phase B2：正向情景 DCF

- 保守/基准/乐观三种收入、利润率、再投资率路径。
- 输出价值区间和敏感性矩阵，不输出单一精确目标价。
- 假设由版本化 Python 配置提供；LLM 只能解释假设及结果。

### Phase B3：内部人、机构持仓和事件监控

- SEC Form 4：内部人买卖及 10b5-1 标记。
- Form 13F：机构季度持仓变化，明确最多 45 天披露延迟和覆盖范围限制。
- 8-K：管理层变动、并购、业绩发布、重大合同和治理事件。
- 新 filing 到达时触发增量分析，而不是每次重跑全部历史。

### Phase B4：宏观敏感度

- FRED/ALFRED：政策利率、通胀、失业率、美元指数和行业相关序列。
- 使用 ALFRED vintage 防止历史回测读取后来修订的数据。
- 宏观数据只用于情景和敏感性，不直接决定个股 Verdict。

### Phase B5：市场数据可靠性

- 为 Yahoo source 增加 provider abstraction、缓存、过期规则和第二来源交叉检查。
- 历史价格统一复权策略；拆股、股息和币种转换必须留下 Evidence。
- 外部行情失败时只降级市场相关模块，不污染 SEC 财务核心。

## 4. 不建议增加的 Agent

- 不创建 Calculator Agent：公式必须继续由 Decimal Python 计算。
- 不创建 Validator Agent：Evidence、Calculation、Claim、Report 验证必须确定性执行。
- 不创建 Verdict Agent：最终状态必须由版本化政策决定。
- 不创建 Data Source Selection Agent：来源优先级必须静态配置并可审计。
- 不为每个指标创建独立 Agent：指标扩展应进入 Calculator/Trend/Valuation 工具。

建议新增或扩展的 LLM 职责只有：

1. `BusinessAnalysisAgent`：解释 Item 1、MD&A 和附注。
2. `RiskAnalysisAgent`：在确定性 section diff 和风险标签之上解释变化。
3. 未来的 `PeerSynthesisAgent`：只解释已经确定性计算的同业差异。

## 5. 完成定义

首阶段只有同时满足以下条件才算完成：

- 九项任务全部有 RED/GREEN 证据和独立提交。
- 全部离线测试通过且未访问实时服务。
- 正式报告与 JSON report 完全一致并有 SHA-256 manifest。
- V1 缺少的 6 个指标全部可计算、验证和展示。
- 10-K Item 1、Item 1A、Item 7 和 10-Q 风险更新按章节进入 Evidence。
- 公司质量、趋势和风险不再只显示 `available` 或固定 `medium`。
- 投资期限真正进入 Verdict 规则和报告解释。
- Report Agent 仍不产生任何报告数字。
- 至少一次真实 Apple 运行通过，报告图表经人工视觉检查。

## 6. 参考资料

- 项目预期：`docs/Expectayion_Projects.md`
- SEC 10-K/10-Q 章节说明：https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/how-read
- SEC EDGAR 数据 API：https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- SEC Forms 3/4/5：https://www.sec.gov/files/forms-3-4-5.pdf
- SEC Form 13F FAQ：https://www.sec.gov/rules-regulations/staff-guidance/division-investment-management-frequently-asked-questions/frequently-asked-questions-about-form-13f
- FRED API：https://fred.stlouisfed.org/docs/api/fred/overview.html
