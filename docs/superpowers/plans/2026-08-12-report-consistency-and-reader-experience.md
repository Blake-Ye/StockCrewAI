# StockCrewAI 报告一致性与可读性改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox syntax；每个任务完成后必须暂停，等待用户检查。

**Goal:** 让普通经营类美股报告做到“正文、图表、估值、反向 DCF 使用同一份数据”，并把程序日志式输出改造成普通投资者可以快速理解、可以追溯、但不构成买卖建议的研究报告。

**Architecture:** Python 继续拥有数据选择、期间口径、计算、图表、门禁与最终判断；LLM 只解释已验证 Claims，不读取图片、不生成数字、不决定数据源。新增统一的 `ReportContext` 口径字段和 `ReportConsistencyGate`，正文 Renderer、三张图和 Verdict 只能读取这一对象。风险概率与影响属于分析判断，由 Risk Agent 结构化输出，再由确定性 Gate 校验、评分和排序。

**Tech Stack:** Python 3.10+、CrewAI、Pydantic、Decimal、Matplotlib、pytest、uv；不新增运行时依赖。

## Global Constraints

- [ ] 所有实现必须从当前已确认基线新建分支，例如 `feat/report-trust-v2`；不得直接继续堆叠在现有修复分支。
- [ ] 当前工作树存在大量未提交修改和删除。开始实现前，先由父代理确认这些变更已经提交或保存；不得重置、覆盖或顺手清理用户改动。
- [ ] 每个代码任务只交给 `luna_coder`，每个子代理必须声明文件所有权；同一文件不得由两个子代理并行修改。
- [ ] 每个任务按“失败测试 → 最小实现 → 定向测试 → diff 审查”执行；任务完成后暂停，用户检查通过才进入下一任务。
- [ ] 实现前按项目 `AGENTS.md` 检查当前 CrewAI 版本、PyPI 最新版本、相关 changelog 和官方 Agent/Task/Flow 文档。除非本计划确实需要 CrewAI API 变更，否则不得升级依赖。
- [ ] 不增加 fallback，不把数据缺失伪装成成功，不允许 Renderer 或 LLM 临时重算指标。
- [ ] 不把图片、base64 或 OCR 结果交给 Report Agent。Report Agent 只接收受控、无数字的叙述上下文；图片由 Python 从同一 `ReportContext` 生成并插入 Markdown。
- [ ] 本轮只支持已经通过 SEC Scope/Profile Gate 的普通经营类公司；银行、保险、基金、ETF 等仍按 SIC/证券类型明确阻断，不扩大支持范围。
- [ ] 报告不得输出“买入、卖出、持有”等建议；可以输出“观察名单”和“重新评估条件”。
- [ ] 所有金额使用 `Decimal` 或既有确定性数值类型，不在 Renderer 中用浮点数重新计算财务指标。

## Definition of Done

- [ ] 同一份报告中的 TTM 自由现金流只出现一个数值；正文、TTM 图和反向 DCF 基础 FCF 完全一致。
- [ ] 历史 P/E 图中的当前值、25% 分位、中位数、75% 分位与正文完全一致，图表不再自行计算分位数。
- [ ] 第一张图改为一个 PNG 内的三个独立子图，各自自动缩放，指标不再共享不合理横轴。
- [ ] 执行摘要不再展示 `status=ready`、Profile、coverage、policy version；这些信息移动到“方法与审计元数据”。
- [ ] 摘要直接回答：经营质量如何、估值相对历史如何、市场隐含什么增长要求、主要风险是什么、何时重新评估。
- [ ] “估值合理”不能只凭默认区间生成；高历史百分位且隐含增长要求高时，输出“增长预期较高，列入观察名单”。
- [ ] 风险部分先展示最多五项风险矩阵，完整风险放附录，并能追溯到具体 SEC filing。
- [ ] 最终 Gate 能在写文件前检测正文与图表口径冲突，返回稳定 `reason_code`，不能生成自相矛盾的报告。
- [ ] 离线测试通过；真实 AAPL 运行通过；再用 MSFT、KO 做普通公司回归；不支持公司仍在 Scope/Profile Gate 清晰阻断。

---

## Phase 0：保存基线并固定真实样例

### Task 0.1：建立安全开发分支与基线证据

**Owner:** 父代理，只做 Git 范围确认；不交给并行子代理。

**Files:**

- Read: `git status`
- Read: `pyproject.toml`
- Read: `run-result.json`（若存在）
- Read: `investment-report.md`（若存在）
- Create: `tests/fixtures/reporting/aapl_report_context.json`

**Steps:**

- [ ] 确认当前修改已经处于可恢复的 commit；如果没有，停止并让用户决定先提交还是暂存。
- [ ] 从已成功的 AAPL 运行产物中只提取报告所需、已验证、无秘密的确定性数据，建立固定 fixture。
- [ ] Fixture 必须同时包含：普通计算值、TTM 指标、历史估值汇总与序列、反向 DCF、Claims、来源元数据、Verdict。
- [ ] 在 fixture 中明确保留当前已知冲突样例：普通期间 FCF 与 TTM FCF 不同；历史序列自行计算分位数与上游汇总值不同。测试应证明新架构选择明确的唯一口径，而不是把二者悄悄改成相同。
- [ ] 新建分支：

```bash
git switch -c feat/report-trust-v2
```

**Verification:**

```bash
uv run pytest tests/test_reporting_modules.py tests/test_report_visuals.py -q
git status --short
```

记录现有基线失败；不得把已有失败误报为本任务回归。

---

## Phase 1：建立唯一报告数据口径（P0，必须先完成）

### Task 1.1：扩展 Canonical ReportContext

**Owner:** Luna A

**Files:**

- Modify: `src/stockcrewai/reporting/context.py`
- Modify: `tests/test_reporting_modules.py`
- Add/Modify: `tests/fixtures/reporting/aapl_report_context.json`

**Required contract:**

在 `ReportContext` 中新增明确的、只读 JSON 字段；字段名可以按现有风格微调，但语义不得改变：

```python
class ReportBasis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_id: str
    period_basis: Literal["TTM", "FY", "YTD", "quarter", "point_in_time"]
    period_start: str | None = None
    period_end: str
    value: str
    unit: str
    evidence_ids: list[str]
    calculation_id: str | None = None


class HistoricalValuationVisual(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ok"]
    validation_status: Literal["valid"]
    period_basis: Literal["TTM"]
    current_date: str
    current_value: str
    percentile_25: str
    five_year_median: str
    percentile_75: str
    current_percentile: str
    sample_count: int
    frequency: str
    outlier_policy: str
    series: list[dict[str, object]]
```

`ReportContext` 至少增加：

```python
canonical_metrics: dict[str, ReportBasis]
historical_valuation: HistoricalValuationVisual | dict[str, object]
```

**Canonical selection policy:**

- [ ] 报告正文中的“过去十二个月收入、营业利润、净利润、经营现金流、自由现金流”只能来自 `ttm.metrics`。
- [ ] 反向 DCF 的 `base_fcf` 只能对应 canonical `free_cash_flow` 的 TTM 值。
- [ ] FY、YTD 或季度 FCF 可以保留在审计附录，但必须显示期间，不能再与 TTM FCF 使用同一个无期间标签。
- [ ] 历史估值图表对象必须保留上游已经验证的 `current_value`、三个分位数、当前百分位、样本数、频率和异常值规则。
- [ ] `_historical_visual_context()` 只投影，不计算任何分位数。
- [ ] 所有 canonical metric 必须带 `period_basis`、`period_end`、单位、Evidence IDs 和 Calculation ID。

**TDD steps:**

- [ ] 先添加失败测试：TTM FCF 与普通期间 FCF 不同时，canonical FCF 选择 TTM。
- [ ] 先添加失败测试：历史图表 context 保留上游四个汇总值，不根据 series 改写。
- [ ] 先添加失败测试：缺少期间、来源或验证状态的指标不能进入 canonical metrics。
- [ ] 完成最小实现。

**Verification:**

```bash
uv run pytest tests/test_reporting_modules.py -q
uv run python -m compileall -q src/stockcrewai/reporting
git diff --check
```

完成后暂停，向用户展示 canonical AAPL FCF 和历史 P/E 五个关键值。

### Task 1.2：新增 ReportConsistencyGate

**Owner:** Luna B，在 Task 1.1 合并后开始，不可并行修改 `context.py`。

**Files:**

- Add: `src/stockcrewai/reporting/consistency.py`
- Modify: `src/stockcrewai/reporting/validator.py`
- Modify: `src/stockcrewai/flow.py`
- Modify: `tests/test_reporting_modules.py`
- Modify: `tests/test_main_flow.py`

**Required interface:**

```python
class ReportConsistencyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["valid", "blocked"]
    reason_codes: list[str]
    checked_metric_ids: list[str]


def validate_report_consistency(
    context: Mapping[str, Any],
) -> ReportConsistencyResult:
    ...
```

**Rules and stable reason codes:**

- [ ] `report_ttm_fcf_missing`：报告需要 TTM FCF，但 canonical 值不存在。
- [ ] `report_ttm_fcf_mismatch`：TTM FCF 与反向 DCF `base_fcf` 数值、币种或期间不同。
- [ ] `report_historical_pe_summary_missing`：历史图存在但汇总参考值缺失。
- [ ] `report_historical_pe_current_mismatch`：series 最新点与 `current_value/current_date` 不一致。
- [ ] `report_historical_pe_metric_mismatch`：正文 ReportMetric 与图表汇总值不一致。
- [ ] `report_metric_provenance_missing`：可见数字缺少 Evidence 或 Calculation 追溯。
- [ ] `report_risk_source_unresolved`：可见风险无法解析到 SEC filing 来源。

**Flow position:**

```text
ReportContext
  -> ReportConsistencyGate
      -> valid: 生成图表 -> Renderer -> 最终文本安全校验 -> 写文件
      -> blocked: 不调用 Report Crew，不生成报告文件，输出明确 reason_codes
```

- [ ] Gate 必须在图片和最终 Markdown 生成前执行。
- [ ] `validate_rendered_report()` 保留文本安全职责，不在 Markdown 字符串上反向解析数字。
- [ ] 现有 `确定性状态：status=...` 检查改为允许该标记位于“方法与审计元数据”，不要求出现在执行摘要。

**TDD steps:**

- [ ] 构造 FCF 不一致测试，断言流程在 Report Crew 前阻断。
- [ ] 构造历史 P/E 不一致测试，断言稳定 reason code。
- [ ] 构造完全一致测试，断言允许继续生成报告。
- [ ] 确认阻断路径不留下旧的 `investment-report.md`。

**Verification:**

```bash
uv run pytest tests/test_reporting_modules.py tests/test_main_flow.py -q
git diff --check
```

完成后暂停，向用户展示一条成功 Gate 和两条故意失败 Gate 的终端框输出。

---

## Phase 2：修复三张图（P0）

### Task 2.1：把第一张图改成三个语义独立的子图

**Owner:** Luna C

**Files:**

- Modify: `src/stockcrewai/reporting/visuals.py`
- Modify: `tests/test_report_visuals.py`
- Modify: `tests/test_reporting_modules.py`

**Design:** 保持报告总共三张 PNG。第一张仍为一个 PNG，但包含三个横向子图：

```text
子图 A 增长与资本配置：收入增长、拆股调整后股份变化
子图 B 盈利能力：营业利润率、净利率
子图 C 现金流质量：自由现金流率、现金转换率
```

**Rules:**

- [ ] 每个子图使用自己的 x 轴范围，现金转换率不再压缩其他指标。
- [ ] 每个子图保留 0 轴；出现负数时左侧留白按负值绝对值和标签宽度动态扩展。
- [ ] 正数标签放柱体右侧，负数标签放柱体左侧；标签与边框至少保留一个稳定 padding。
- [ ] 现金转换率 115% 等大值不得被裁切；股份变化为负时百分比不得碰撞 y 轴标题。
- [ ] 股份变化只能读取已经拆股调整后的 canonical 指标；无法确认拆股调整时省略该柱并在图下注明“不可比”，不得展示原始 879.92%。
- [ ] 图标题与图注写清各组指标不能直接横向比较。
- [ ] 不改变 `build_report_visuals()` 的三个顶层键，避免不必要的调用方改动。

**TDD steps:**

- [ ] 新增负股份变化、现金转换率大于 100%、极端正增长三组边界 fixture。
- [ ] 测试生成 PNG、不裁切、三个 axes 存在、文本标签位于各自坐标范围内。
- [ ] 更新确定性图片 hash；hash 更新前必须人工查看生成图片。

**Verification:**

```bash
uv run pytest tests/test_report_visuals.py -q
uv run pytest tests/test_reporting_modules.py -q
```

将 AAPL 和 NFLX fixture 生成的第一张图输出到 `/private/tmp/stockcrewai-visual-review/`，父代理用图片查看工具检查后暂停。

### Task 2.2：统一 TTM 图、正文与反向 DCF

**Owner:** Luna D，在 Task 1.1 合并后开始。

**Files:**

- Modify: `src/stockcrewai/reporting/visuals.py`
- Modify: `src/stockcrewai/reporting/renderer.py`
- Modify: `tests/test_report_visuals.py`
- Modify: `tests/test_reporting_modules.py`

**Rules:**

- [ ] TTM 图只读取 `context.canonical_metrics` 中 period_basis 为 `TTM` 的五个规模指标。
- [ ] 正文“财务趋势”对应规模表也读取同一对象，不再读取普通 `calculations.free_cash_flow`。
- [ ] 反向 DCF 表中的基础 FCF 显示同一个 canonical 值、期间和币种。
- [ ] 如果报告还展示 YTD/FY FCF，标题必须明确写出“截至某日的九个月累计”或“财年”，不得称作 TTM。
- [ ] 金额统一显示：大额默认亿元/十亿美元，保留最多两位小数；底层原值仍保留完整精度。

**Acceptance example:**

```text
过去十二个月自由现金流：1,366.83 亿美元
TTM 图：136.7 十亿美元
反向 DCF 基础自由现金流：1,366.83 亿美元（TTM）
```

三个展示允许单位换算，但换算后的数学值必须一致。

**Verification:**

```bash
uv run pytest tests/test_report_visuals.py tests/test_reporting_modules.py -q
git diff --check
```

完成后暂停，展示测试中普通期间 FCF 与 TTM FCF 不同但报告只采用明确口径的断言。

### Task 2.3：历史 P/E 图禁止自行重算分位数

**Owner:** Luna E，在 Task 1.1 合并后开始；不得与 Task 2.1 同时修改 `visuals.py`。

**Files:**

- Modify: `src/stockcrewai/reporting/visuals.py`
- Modify: `tests/test_report_visuals.py`
- Modify: `tests/test_reporting_modules.py`

**Rules:**

- [ ] 删除或停止使用图表层 `_percentile()`。
- [ ] 折线只来自 `series`；四条/点参考信息只来自 canonical 历史估值汇总。
- [ ] 图中必须显示：25% 分位、中位数、75% 分位、当前 P/E；图例数值与正文相同。
- [ ] 图注显示统计窗口、频率、样本数和异常值处理方法。
- [ ] 当前日期必须等于序列最后一个日期；不一致由 Consistency Gate 阻断，而不是图表返回空图掩盖问题。

**TDD steps:**

- [ ] 构造“series 重算结果与上游汇总值不同”的 fixture。
- [ ] 断言图例采用上游 26.35x / 31.88x / 35.08x，而不是自行算出的 22.22x / 28.50x / 31.90x。
- [ ] 更新图片 hash，并人工查看。

**Verification:**

```bash
uv run pytest tests/test_report_visuals.py tests/test_reporting_modules.py -q
```

---

## Phase 3：重写面向用户的报告结构（P1）

### Task 3.1：执行摘要与审计元数据分离

**Owner:** Luna F

**Files:**

- Modify: `src/stockcrewai/reporting/renderer.py`
- Modify: `src/stockcrewai/reporting/validator.py`
- Modify: `src/stockcrewai/crews/report/config/agents.yaml`
- Modify: `src/stockcrewai/crews/report/config/tasks.yaml`
- Modify: `tests/test_reporting_modules.py`
- Modify: `tests/test_crew_configuration.py`

**Visible report order:**

```text
1. 一页结论
2. 公司经营质量
3. 财务趋势
4. 当前估值
5. 历史估值
6. 反向 DCF 与市场隐含预期
7. 主要风险矩阵
8. 重新评估条件
9. 数据来源与计算方法
10. 完整风险附录
11. 方法与审计元数据
12. 非投资建议声明
```

**One-page conclusion contract:**

- [ ] 第一段：经营质量，用一到两句解释增长、利润率和现金流质量。
- [ ] 第二段：估值位置，比较当前 P/E、五年中位数和历史百分位。
- [ ] 第三段：市场隐含预期，说明反向 DCF 要求的长期 FCF 增长以及是否已完成现实性验证。
- [ ] 第四段：行动参考，只能是“观察名单/等待重新评估”，并列出触发条件。
- [ ] `status`、Profile、coverage、policy version、reason code 全部移动到“方法与审计元数据”。
- [ ] 删除“由确定性 Renderer 注入已验证内容”“已具备确定性数据支撑”等重复模板句。
- [ ] “已验证”只在方法章节集中解释一次；正文用来源脚注和计算表证明可信度。

**Report Agent boundary:**

- [ ] Agent 不接收图片。
- [ ] Agent 不接收可让它复述的原始数字。
- [ ] Agent 输出仍为严格 `ReportDraft` JSON，负责自然语言衔接，不负责结论标签和条件阈值。
- [ ] Python Renderer 注入所有数字、评级标签、风险排序和观察条件。

**Verification:**

```bash
uv run pytest tests/test_reporting_modules.py tests/test_crew_configuration.py -q
```

人工检查执行摘要前 30 行，确保没有程序调试字段。

### Task 3.2：增加可执行的“重新评估条件”

**Owner:** Luna G，在 Task 3.1 合并后开始。

**Files:**

- Modify: `src/stockcrewai/reporting/renderer.py`
- Add/Modify: `tests/test_reporting_modules.py`

**Deterministic observation checklist:**

- [ ] P/E 是否回到五年中位数附近；“附近”阈值由 policy 明确给出，不由 LLM创造。
- [ ] FCF Yield 是否相对当前值改善，或者达到 policy 的重新评估阈值。
- [ ] 收入增长与 TTM FCF 增长是否支持反向 DCF 的隐含路径。
- [ ] 经营利润率或现金转换率是否显著恶化。
- [ ] 排名前三的风险是否出现新的 SEC 披露。
- [ ] 缺少相应数据时写“该条件暂不可验证”，不能生成虚构阈值。

措辞必须是：

```text
重新评估条件：当以下数据发生变化时重新运行报告……
```

不得是：

```text
达到以下条件即可买入……
```

**Verification:**

```bash
uv run pytest tests/test_reporting_modules.py -q
```

---

## Phase 4：Verdict Policy v2 与增长现实性（P1）

### Task 4.1：把“估值合理”改成可审计的中性/观察判断

**Owner:** Luna H

**Files:**

- Modify: `src/stockcrewai/tools/verdict_tool.py`
- Modify: `src/stockcrewai/reporting/renderer.py`
- Modify: `tests/test_verdict_tool.py`
- Modify: `tests/test_reporting_modules.py`
- Modify: `docs/data-contracts.md`

**Policy changes:**

- [ ] policy version 升级为 `metric-policy:v2` 或项目现有等价命名。
- [ ] `reasonable` 的用户显示名改为“估值处于中性区间”，不能翻译成“估值合理”。
- [ ] 新增稳定 summary code：`high_expectations_watchlist`。
- [ ] 当历史 P/E 百分位不低于 65%，且反向 DCF 隐含长期 FCF 增长不低于 10% 时，输出 `watchlist`。
- [ ] 现有昂贵规则保留：P/E ≥ 35、FCF Yield < 2%、或历史百分位 ≥ 75% 时，输出 `expensive`。
- [ ] 高风险仍进入 `watchlist`，但风险原因和估值原因必须分开记录。
- [ ] 阈值集中在一个不可变 policy 对象中，不散落在 Renderer。

**Important:** 上述阈值是项目政策，不是金融真理。必须在方法章节显示 policy version 和阈值，并通过测试固定。

**TDD cases:**

- [ ] Apple 类场景：P/E 34.97、历史百分位 72.88%、隐含增长 12.10% → `watchlist/high_expectations_watchlist`。
- [ ] 低历史百分位、适中隐含增长 → `reasonable`，显示“中性区间”。
- [ ] 百分位 ≥ 75% → `expensive`。
- [ ] 隐含增长缺失时不得自行判断其现实性。

**Verification:**

```bash
uv run pytest tests/test_verdict_tool.py tests/test_reporting_modules.py -q
```

### Task 4.2：加入历史 FCF CAGR 与 expectation gap

**Owner:** Luna I；这是独立增量，Task 4.1 通过后实施。

**Files:**

- Modify: `src/stockcrewai/pipelines/valuation_pipeline.py`
- Modify: `src/stockcrewai/reporting/context.py`
- Modify: `src/stockcrewai/tools/verdict_tool.py`
- Modify: `src/stockcrewai/reporting/renderer.py`
- Add/Modify: `tests/test_valuation_claim_stability.py`
- Modify: `tests/test_verdict_tool.py`

**Calculation:**

```python
fcf_cagr = (ending_fcf / beginning_fcf) ** (Decimal(1) / years) - Decimal(1)
expectation_gap = reverse_dcf_implied_growth - historical_fcf_cagr
```

**Rules:**

- [ ] 只在起止 FCF 都为正、期间完整、Evidence 已验证时计算 3 年和 5 年 CAGR。
- [ ] 优先使用 5 年 CAGR；不足五年时使用 3 年并清晰标注。
- [ ] 不完整或不可比时状态为 `unavailable`，不阻断基础报告。
- [ ] 隐含增长比历史 CAGR 高至少 3 个百分点时，增加 `expectation_gap_high` 规则。
- [ ] 报告必须明确区分“市场要求的增长”与“公司过去实现的增长”；不得把历史 CAGR 当预测。

**Verification:**

```bash
uv run pytest tests/test_valuation_claim_stability.py tests/test_verdict_tool.py tests/test_reporting_modules.py -q
```

---

## Phase 5：风险矩阵与完整来源链（P2）

### Task 5.1：扩展 Risk Agent 结构化输出，不污染通用 Claim

**Owner:** Luna J

**Files:**

- Modify: `src/stockcrewai/crews/analysis/crew.py`
- Modify: `src/stockcrewai/crews/analysis/config/agents.yaml`
- Modify: `src/stockcrewai/crews/analysis/config/tasks.yaml`
- Modify: `src/stockcrewai/validators/claim_gate.py`
- Modify: `src/stockcrewai/pipelines/analysis_pipeline.py`
- Modify: `tests/test_analysis_structured_output.py`
- Modify: `tests/test_crew_configuration.py`

**Do not add fields to every Claim.** 风险判断使用平行结构，财务和估值 Claim 契约保持六字段不变：

```python
class RiskAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    likelihood: Literal["low", "medium", "high", "unknown"]
    impact: Literal["low", "medium", "high"]
    horizon: Literal["near_term", "one_to_three_years", "long_term", "unknown"]
    affected_metrics: list[
        Literal[
            "revenue_growth",
            "operating_margin",
            "free_cash_flow",
            "capital_expenditure",
            "valuation",
        ]
    ]


class RiskAnalysisTaskOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[AnalysisClaim]
    risk_assessments: list[RiskAssessment]
```

**Rules:**

- [ ] 每个 assessment 的 `claim_id` 必须对应同一输出中的 risk Claim。
- [ ] Risk Claim 仍必须引用至少一个已验证 filing Evidence ID。
- [ ] likelihood/impact 是 Agent 的分析判断，报告中必须标记为“分析判断”，不能伪装成公司披露事实。
- [ ] Agent 只能根据传入的完整 SEC 风险文本判断，不能联网添加新闻事件。
- [ ] Gate 校验枚举、claim 对应关系、Evidence 白名单；无效 assessment 被拒绝，不能进入报告。
- [ ] `_verdict_risk_input()` 不再固定写死 `risk_level="medium"`。

**Deterministic score:**

```text
likelihood: low=1, medium=2, high=3, unknown=0
impact: low=1, medium=2, high=3
priority_score = likelihood_score * impact_score
```

- [ ] 按 `priority_score` 降序，其次按 claim_id 排序，确保同样输入生成同样顺序。
- [ ] `unknown` 不得排在已知高优先级风险之前。
- [ ] 综合风险等级由代码根据已接受 assessment 计算，并记录规则，不由 Agent 直接决定。

**Verification:**

```bash
uv run pytest tests/test_analysis_structured_output.py tests/test_crew_configuration.py -q
```

### Task 5.2：报告风险矩阵与 SEC 来源附录

**Owner:** Luna K，在 Task 5.1 合并后开始。

**Files:**

- Modify: `src/stockcrewai/reporting/context.py`
- Modify: `src/stockcrewai/reporting/renderer.py`
- Modify: `src/stockcrewai/reporting/consistency.py`
- Modify: `tests/test_reporting_modules.py`

**Visible matrix:**

```text
| 风险 | 发生可能性* | 影响程度* | 时间范围* | 主要影响指标 | SEC 来源 |
```

脚注：`* 为模型基于已验证 SEC 原文作出的结构化分析判断，不是公司给出的概率预测。`

**Rules:**

- [ ] 主体最多展示五项最高优先级风险。
- [ ] 完整 accepted risk Claims 放在“完整风险附录”。
- [ ] `source_metadata.risk_filings` 必须解析出 form、filed_at、accession number、原文 URL。
- [ ] 来源部分分为“财务与估值数据源”和“风险原文来源”，不再只列 SEC Company Facts 与 Yahoo Finance。
- [ ] 报告中的 DMA、法院、关税调查等具体事件，只有在已接收 SEC 原文明确披露时才能出现；本阶段不新增网页搜索工具。
- [ ] 任一可见风险 Evidence ID 无法解析到 filing 时，由 Consistency Gate 以 `report_risk_source_unresolved` 阻断。

**Verification:**

```bash
uv run pytest tests/test_reporting_modules.py tests/test_main_flow.py -q
```

---

## Phase 6：集成、视觉验收与真实运行（最终 Gate）

### Task 6.1：离线全套回归

**Owner:** Luna L，仅测试和修复本计划引入的回归；不得顺手重构。

**Files:**

- Modify only if failing due to this plan: relevant tests/source files
- Read: all changed files

**Steps:**

- [ ] 运行定向报告测试。
- [ ] 运行主流程与普通公司集成测试。
- [ ] 运行全部离线测试。
- [ ] 对固定 hash 的更新逐项解释；不能为了绿灯无条件重写 snapshot。

```bash
uv run pytest \
  tests/test_reporting_modules.py \
  tests/test_report_visuals.py \
  tests/test_verdict_tool.py \
  tests/test_analysis_structured_output.py \
  tests/test_main_flow.py -q

uv run pytest -q
uv run python -m compileall -q src/stockcrewai
git diff --check
```

- [ ] 如果全套测试仍有基线失败，列出“实现前已存在”与“本次新增”两组，不得只报告通过数量。
- [ ] 本次新增失败必须修复到 0。

完成后暂停，等待用户批准真实网络运行。

### Task 6.2：真实 AAPL、MSFT、KO 验收

**Owner:** 父代理负责监工，Luna M 只处理可复现的本计划缺陷。

**Commands:** 使用项目现有 ticker 输入方式；不得临时硬编码公司。若当前入口通过环境变量或 CLI 参数传入，应按 README 的真实命令执行。

至少验证：

- [ ] AAPL：历史估值和反向 DCF 均可用，重点检查 FCF 与 P/E 一致性。
- [ ] MSFT：普通经营公司完整报告。
- [ ] KO：传统消费公司完整报告。
- [ ] 任一不支持 SIC 样例：仍在 SEC Scope/Profile Gate 阻断，不能进入报告阶段。

**Manual report QA checklist:**

- [ ] 报告开头 30 行没有 debug 元数据。
- [ ] 第一张图三个子图无文字遮挡，且语义分组正确。
- [ ] TTM 图 FCF 与正文和 DCF 完全一致。
- [ ] 历史 P/E 图四个参考数字与正文一致。
- [ ] “一页结论”能在一分钟内回答贵不贵、市场要求什么、为什么观察。
- [ ] 风险矩阵不超过五项，来源可点击到 SEC 原文。
- [ ] 没有买入、卖出、持有建议。
- [ ] 报告文件公司名和 ticker 与本次运行一致，不复用上一家公司产物。

**Artifacts to keep:**

```text
investment-report.md
run-result.json
报告引用的持久化图表文件（若当前导出方式需要）
```

临时 QA 图片写入 `/private/tmp`，验收后不提交仓库。

---

## Multi-agent Execution Map

为了避免多个 Luna 同时改同一文件，实际并发最多三条：

```text
Phase 0
  -> Task 1.1 Context
      -> Task 1.2 Consistency Gate
      -> Task 2.1 First chart
      -> Task 2.2 TTM chart/text/DCF
      -> Task 2.3 Historical P/E
  -> Task 3.1 Summary
      -> Task 3.2 Observation checklist
  -> Task 4.1 Verdict v2
      -> Task 4.2 FCF CAGR
  -> Task 5.1 Risk schema/gate
      -> Task 5.2 Risk rendering/sources
  -> Task 6.1 Offline regression
      -> Task 6.2 Real runs
```

可安全并行的组合仅限：

- Task 2.1 与 Task 4.1：文件基本独立，但最终由父代理顺序合并。
- Task 3.1 与 Task 5.1：前者改报告，后者改 Analysis；合并后再执行 Task 5.2。
- 测试研究代理可以并行只读分析，但不得提交代码。

任何包含 `context.py`、`visuals.py`、`renderer.py` 或 `flow.py` 的任务都按上表串行执行。

## Explicit Non-goals

- 不让 LLM 看图或对图做视觉判断。
- 不新增新闻、法院、欧盟委员会或商务部网页检索工具。
- 不恢复 quant 模块，不新增回测、因子排名或同业比较。
- 不扩展银行、保险、REIT、基金、ETF 等非普通经营公司支持。
- 不重写整个 Flow，不更换 CrewAI 框架，不升级所有依赖。
- 不用新模板掩盖数据冲突；一致性 Gate 未通过时必须停止生成报告。

## Expected AAPL Summary Shape

最终文字应接近以下信息结构，但数字必须从本次 canonical context 注入，不能硬编码：

```markdown
## 一页结论

Apple 的盈利能力和现金流转化保持较强，收入仍在增长，拆股调整后的股份数量继续下降。

当前 P/E 高于自身五年中位数，并位于历史估值区间的较高位置；当前价格并不便宜。反向 DCF 显示，市场价格要求公司长期维持较高的自由现金流增长。若该要求明显高于公司过去可验证的增长水平，报告将其列入“高预期观察名单”，而不是笼统判断“估值合理”。

行动参考：暂时列入观察名单。重点观察估值是否回到历史中位区间、自由现金流是否沿隐含增长路径兑现，以及监管、供应链和资本开支风险是否出现新的 SEC 披露。
```

这段示例只定义阅读体验，不是固定报告模板，也不是投资建议。
