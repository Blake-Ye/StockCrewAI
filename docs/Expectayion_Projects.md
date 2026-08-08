你是 stockcrewai 项目的唯一直接开发执行者。任务 0 的架构设计已经完成，你需要严格遵循仓库中现有的架构文档与本提示词，逐个完成我后续单独发送的开发任务。

核心定位：
- 不要重新设计成自由自治的多 Agent 系统。
- 不要擅自增加 Agent，不要创建子代理。
- 所有实施计划、开发报告和问题说明请使用中文。

==================================================
一、开始任务前的强制读取
==================================================

每次收到具体开发任务后，必须先读取以下文件：
- AGENTS.md
- docs/architecture.md
- docs/implementation-plan.md
- docs/data-contracts.md
- pyproject.toml
- 与本任务直接相关的源码
- 与本任务直接相关的测试

如果存在以下文档，也必须读取：
- docs/numeric-conventions.md
- docs/error-model.md
- docs/testing-strategy.md

遇到冲突时的处理流程：
1. 不要直接实现；
2. 明确指出具体冲突点；
3. 给出最小修正建议；
4. 等待用户决定。
同时，不要擅自改变项目核心目标。

==================================================
二、当前开发环境
==================================================

项目使用 uv 管理 Python 环境、依赖和锁文件。本轮复用现有项目环境和已锁定依赖，命令统一通过 `uv run --no-sync` 执行。

例如：
  uv run --no-sync python --version
  uv run --no-sync python -m unittest discover -s tests -p 'test_*.py' -v
  uv run --no-sync python -m ruff check .
  uv run --no-sync python -m mypy src

本轮不得：
- 执行 `uv sync`、创建新的环境或创建 `.venv`
- 删除或重建现有项目环境
- 未经允许升级 CrewAI、Pydantic 或 Python 版本
- 未经允许批量升级现有依赖或新增依赖

如需新增依赖，必须先报告以下内容并等待批准：
1. 依赖名称
2. 使用原因
3. 生产依赖还是开发依赖
4. 与现有依赖的版本兼容性
5. 是否存在不增加依赖的实现方式
未经同意，不得安装新依赖。

==================================================
三、项目最终产品
==================================================

项目名称：stockcrewai

目标是构建一个基于以下组件的美国上市公司投资研究系统：
- CrewAI Flow
- SEC EDGAR
- DeepSeek API（模型 deepseek-v4-flash）
- Pydantic v2
- 确定性 Python 财务计算
- 确定性 Evidence 验证、Calculation 验证、Claim 验证
- 确定性最终评级
- 中文 Markdown 投资研究报告

用户输入示例：我想知道苹果公司是否值得投资

系统需要自动完成的 20 个步骤：
1. 理解用户输入
2. 识别公司名称与 ticker 候选
3. 使用 SEC 官方数据确认公司、ticker 和 CIK
4. 判断公司是否属于 V1 支持范围
5. 按固定流程获取 SEC 文件与 XBRL 数据
6. 规范化所有数据并生成 Evidence
7. 验证公司身份、单位、财务期间和数据来源
8. 构建并展示 TTM 数据（本轮不替换当前估值输入）
9. 计算财务指标
10. 获取带时间戳的市场价格
11. 计算当前估值
12. 计算历史估值区间
13. 执行简化反向 DCF
14. 独立验算所有计算
15. 由 Analysis Crew 解释已验证数据
16. 验证 Analysis Crew 生成的 Claims
17. 使用确定性规则生成评级
18. 生成中文研究报告
19. 验证报告中的每个数字与结论
20. 输出可审计的中间 Artifacts

核心原则：
- LLM 负责：理解、解释
- 普通 Python 程序负责：事实、数学、验证、评级
- 本轮由确定性 Python 生成并展示 TTM；当前估值、历史估值、反向 DCF 和 Verdict 继续使用现有输入。

==================================================
四、Agent 与 Crew 固定架构
==================================================

V1 版本固定为 4 个 LLM Agent 和 3 个 Crew，严禁擅自增加任何 Planner、Research、Validator、Manager 或自由自治 Agent。

Crew 1：Request Parser Crew
- 1 个 Agent：RequestParserAgent
- 1 个 Task：ParseInvestmentRequestTask
- 输出：ParsedRequest

Crew 2：Analysis Crew
- 2 个 Agent：FinancialQualityAgent、RiskAnalysisAgent
- 2 个 Task：FinancialQualityAnalysisTask、RiskAnalysisTask
- 输出：AnalysisClaims；估值 Claims 由确定性 Python 生成后合并，不设置估值 LLM Agent。
- 两个 Agent 可分析同一份已验证状态，但不得互相修改数据。

Crew 3：Report Crew
- 1 个 Agent：ReportWriterAgent
- 1 个 Task：GenerateValidatedReportTask
- 输出：ReportDraft（随后仍需经 FinalReportValidator 验证）

为什么没有其他 Agent：
- 查询哪些 SEC 文件 → 固定代码决定
- 获取数据 → Service 完成
- 财务计算 → 确定性 Python 完成
- 估值 Claims → 确定性 Python 完成
- 数据验证 → Validator 完成
- 最终评级 → 规则引擎完成
以上过程均不需要 LLM 参与。

==================================================
五、四个 Agent 的具体职责与约束
==================================================

Agent 1：RequestParserAgent
- 所属：Request Parser Crew
- 作用：理解自然语言，识别公司名称、ticker 候选、关注方向、报告语言、投资期限。
- 允许输出示例：{
  "company_mention": "苹果公司",
  "company_name_guess": "Apple Inc.",
  "ticker_guess": "AAPL",
  "exchange_guess": "NASDAQ",
  "request_type": "investment_analysis",
  "investment_horizon": "unspecified",
  "requested_focus": ["business_quality","financial_trend","valuation","risk"],
  "language": "zh-CN",
  "confidence": "9.90000E-1"
}
- 禁止：生成 CIK、查询 SEC 文件、输出财务数字或股价、进行财务计算、输出评级或买卖建议。输出必须使用 Pydantic 结构化模型。

Agent 2：FinancialQualityAgent
- 所属：Analysis Crew
- 作用：解释已验证的财务数据，分析收入趋势、利润率、自由现金流、现金流质量、股份稀释、资产负债表健康度。
- 允许读取：已验证的 EvidenceItems、CalculationResults、TTM 结果及带来源的 SEC filing 文本。
- 禁止：自行计算指标、添加或修改数字、引用无 Evidence ID 的数据、使用未验证 Evidence、生成最终评级。
- 输出为结构化 Claims，例如：{
  "claims": [{
    "claim_id": "claim_financial_001",
    "category": "financial_quality",
    "statement": "公司过去十二个月保持较高的营业利润率。",
    "evidence_ids": ["ev_operating_income_ttm","ev_revenue_ttm"],
    "calculation_ids": ["calc_operating_margin_ttm"],
    "confidence": "9.50000E-1"
  }]
}

Agent 3：RiskAnalysisAgent
- 所属：Analysis Crew
- 作用：分析 10-K Item 1A、10-Q 风险更新、8-K 重大事件，识别供应链、客户集中、监管、地区宏观等风险。
- 允许读取：已验证的 SEC filing 文本及其元数据（section, form, accession number, filed date, Evidence ID）。
- 禁止：使用无来源新闻、自行搜索互联网、添加 SEC 文件中不存在的风险、预测未来事件、生成最终评级。
- 输出为结构化 Claims，类似上述结构。

Agent 4：ReportWriterAgent
- 所属：Report Crew
- 作用：将已验证的 Claims 整理为中文 Markdown 报告，展示评级、财务表现、估值、风险、数据来源与局限性。
- 只能读取：已验证 Claims、Deterministic Verdict、CalculationResults、Evidence 来源元数据、市场价格时间戳、已知局限性。
- 禁止：添加或修改数字、新增 Claim、使用 rejected Claim、修改评级、隐藏估值时间、输出投资建议或未来源的未来预测。
- 报告至少包含：执行摘要、总体评级、公司质量、财务趋势、当前估值、历史估值、反向 DCF 隐含预期、主要风险、数据来源和方法、局限性、非投资建议声明。

==================================================
六、Crew 的目标目录结构
==================================================
src/stockcrewai/crews/
├── request_parser/
│   ├── __init__.py
│   ├── crew.py
│   └── config/ (agents.yaml, tasks.yaml)
├── analysis/
│   ├── __init__.py
│   ├── crew.py
│   └── config/ (agents.yaml, tasks.yaml)
└── report/
    ├── __init__.py
    ├── crew.py
    └── config/ (agents.yaml, tasks.yaml)

==================================================
七、完整项目目标架构
==================================================
src/stockcrewai/
├── __init__.py
├── main.py
├── cli.py
├── flow.py
├── config.py
├── exceptions.py
├── models/          (enums, request, entity, evidence, calculation, claim, verdict, state)
├── services/        (sec_client, entity_resolver, filing_fetcher, filing_parser, market_data, cache)
├── pipelines/       (scope_gate, fixed_plan, metric_registry, evidence_normalizer, evidence_validator, ttm_builder, historical_valuation)
├── calculations/    (decimal_context, formatter, formulas, financial_metrics, current_valuation, reverse_dcf)
├── validators/      (calculation_validator, claim_validator, report_validator)
├── verdict/         (rules, engine)
├── crews/           (request_parser, analysis, report)
└── tools/           (edgar_tool, ttm_tool)

tests/
├── unit/
├── integration/
├── fixtures/        (apple/ …)
└── live/

docs/
outputs/<request_id>/

==================================================
八、现有目录迁移规则
==================================================
当前可能残留 CrewAI 自动生成的结构，迁移原则：
1. 不要一次性删除旧文件；
2. 先创建新模块并确保有测试；
3. 确认无旧入口依赖后再移除旧模板文件。
目标：将 content_crew.py 拆分为三个独立 Crew，配置移入各自目录，工具替换为薄包装 edgar_tool.py，main.py 仅保留 Flow 入口逻辑。

==================================================
九、Flow 职责（唯一总调度器）
==================================================
CrewAI Flow 按以下固定顺序执行（共 20 步）：
1. RequestParserCrew → 2. EntityResolver → 3. ScopeGate → 4. FixedResearchPlanBuilder → 5. SECDataPipeline → 6. MarketDataPipeline → 7. EvidenceNormalizer → 8. EvidenceValidator → 9. TTMBuilder → 10. FinancialCalculationEngine → 11. CurrentValuationEngine → 12. HistoricalValuationEngine → 13. ReverseDCFEngine → 14. CalculationValidator → 15. AnalysisCrew → 16. ClaimValidator → 17. DeterministicVerdictEngine → 18. ReportCrew → 19. FinalReportValidator → 20. OutputWriter

关键约束：
- Crew 之间不得通过自由聊天传递关键数据，跨阶段数据必须通过 Pydantic ResearchFlowState 传递。
- Flow 负责顺序、状态、分支、失败、重试、中间结果和输出 Artifacts。

==================================================
十、LLM 与确定性模块的明确划分
==================================================
LLM 阶段（仅这 4 个）：
- RequestParserAgent
- FinancialQualityAgent
- RiskAnalysisAgent
- ReportWriterAgent

确定性 Python 模块（绝不可改为 Agent）：
EntityResolver, ScopeGate, FixedResearchPlanBuilder, SECDataPipeline, MarketDataPipeline, EvidenceNormalizer, EvidenceValidator, TTMBuilder, FinancialCalculationEngine, CurrentValuationEngine, HistoricalValuationEngine, ReverseDCFEngine, build_deterministic_valuation_claims, CalculationValidator, ClaimValidator, DeterministicVerdictEngine, FinalReportValidator, OutputWriter.

当前估值、历史估值和反向 DCF 的 Claims 由 `build_deterministic_valuation_claims` 等确定性 Python 逻辑生成；Analysis Crew 中的 LLM 只解释允许进入该 Crew 的财务与风险 Claims，ReportWriterAgent 只整理已验证 Claims。

==================================================
十一、固定 SEC 数据获取流程
==================================================
核心文件范围由代码固定，不由 Agent 决定。必须获取：
- SEC submissions metadata
- SEC companyfacts
- 最近 3 份 10-K（固定关注 Item 1 Business, Item 1A Risk Factors, Item 7 MD&A）
- 最近 4 份 10-Q（关注 Financial Statements metadata, MD&A, Risk Factor Updates）
- 最近 180 天内最多 20 份 8-K（关注 Item number, Filing date, Event text, Accession number）

Agent 不得增加或删除核心文件类型。

==================================================
十二、运行时 DeepSeek 配置
==================================================
模型：deepseek-v4-flash，API Key 通过环境变量读取。
严禁：硬编码 Key、输出 .env、打印 Key、写入日志或 Artifacts。
测试原则：默认不调用真实 API，Mock 测试不产生费用，live 测试需显式开启。
Agent 策略建议：
- RequestParserAgent: thinking disabled, temperature 0, 结构化输出
- FinancialQualityAgent: thinking enabled, 低 temperature, 只读 validated 数据
- RiskAnalysisAgent: thinking enabled, 低 temperature, 只读带来源 SEC 文本
- ReportWriterAgent: thinking disabled / 低推理, 仅重组 validated Claims
- 估值 Claims：由确定性 Python 逻辑生成，不由 LLM 生成或计算。
实现时务必检查当前 CrewAI 版本支持的实际 LLM 配置方式，不要复制过时代码。

==================================================
十三、公司身份解析
==================================================
- RequestParserAgent 只输出候选：company_mention, company_name_guess, ticker_guess, exchange_guess。
- EntityResolver 使用 SEC 映射确定 legal_name, ticker, exchange, 10-digit zero-padded CIK, resolution_status, match_method 等。
- LLM 生成的 CIK 永远不可信，只有 confirmed EntityRef 才能进入后续流程。

==================================================
十四、V1 支持范围
==================================================
支持：单家美国上市公司、SEC 申报企业、普通非金融运营公司、中文输入与中文报告。
暂不支持：银行、保险、ETF、基金、私营公司、REIT 专用估值、多公司比较、技术/期权分析、个性化投资建议。
若不支持，返回结构化 ScopeResult 且不得强行继续。

==================================================
十五、金融数据与 Decimal 规范
==================================================
所有核心金额和比率必须使用 Decimal，严禁使用 float。
统一规则：precision=28, ROUND_HALF_EVEN, 中间过程不舍入, JSON 中 Decimal 存为字符串, 禁止 NaN 和 Infinity。
比率内部使用 0~1，数据缺失不能变成 0。
科学计数法：6 位有效数字，大写 E，正指数显式带 +。例：391000000000 → 3.91000E+11, 0.310173... → 3.10174E-1。
最终展示：$403.00B、15.10B shares、31.02%、32.89x。
必须区分 raw_value、normalized_value 和 display_value。

==================================================
十六、固定财务语义
==================================================
- capital_expenditure 为正数形式的现金流出。
- FCF = operating_cash_flow - capital_expenditure。
- 市值使用 current_shares_outstanding；每股净现金使用 current_shares_outstanding；EPS 验算使用 diluted_weighted_average_shares；股份稀释趋势使用各期间 diluted_weighted_average_shares。
- 无法计算时返回 typed unavailable，不得将缺失当作 0。

==================================================
十七、Evidence 规则
==================================================
任何进入 Analysis Crew 的数字必须有 Evidence。EvidenceItem 至少包含：
evidence_id, entity_cik, evidence_type, metric_id, raw_source_value, economic_value, normalized_value, display_value, unit, currency, fiscal_year, fiscal_period, period_start/end, period_type, form, filed_at, accession_number, taxonomy, xbrl_tag, source_reference, validation_status, warnings。
禁止向 Agent 传递没有 Evidence ID 的裸数字。

==================================================
十八、TTM 规则
==================================================
固定公式：TTM = 最近完整财年 + 当前财年累计值 - 上年同期累计值。
只有在当前累计期与上年同期可比较、单位一致、期间长度兼容、财务日历对齐且输入 Evidence 已验证时才可构建。否则返回 unavailable 并记录原因。
本轮 TTM Builder 负责生成并在 Flow 阶段摘要和结果中展示 TTM 可用性；暂不切换当前估值输入，历史估值、反向 DCF 和 Verdict 也不因本轮 TTM 结果改变输入口径。

==================================================
十九、固定财务计算清单
==================================================
至少实现：Revenue Growth, 3-year Revenue CAGR, Gross Margin, Operating Margin, Net Margin, Operating Cash Flow Margin, Free Cash Flow, Free Cash Flow Margin, Cash Conversion, Share Dilution, Net Cash/Net Debt, Current Ratio, Debt-to-Equity。
每个 CalculationResult 必须保存：calculation_id, formula_id, formula_version, input_evidence_ids, exact raw inputs, exact raw result, normalized result, display result, unit, period, validation_status, warnings。

==================================================
二十、估值模块
==================================================
当前估值至少计算：Market Cap, P/E TTM, Earnings Yield, FCF Yield, Price-to-Sales, Net Cash per Share。
固定公式：
  market_cap = share_price × current_shares_outstanding
  pe_ttm = market_cap / ttm_net_income
  earnings_yield = ttm_net_income / market_cap
  fcf = operating_cash_flow - capital_expenditure
  fcf_yield = ttm_fcf / market_cap
  price_to_sales = market_cap / ttm_revenue
  net_cash = cash_and_marketable_securities - total_debt
  net_cash_per_share = net_cash / current_shares_outstanding
历史估值使用五年月末价格，输出当前值、五年中位数、25/75 分位、当前百分位。必须防止前视偏差，历史日期只能使用当时已公开的财报数据。

==================================================
二十一、反向 DCF
==================================================
- 使用简化 FCF proxy（operating_cash_flow - capital_expenditure），并明确说明非完整机构级 FCFE。
- 固定预测期 10 年。
- 默认三种场景：(8%,2%), (9%,2.5%), (10%,3%)。
- 使用确定性二分法求隐含增长率，禁止 LLM 求解。
- 必须输出 base FCF, equity value, forecast years, discount rate, terminal growth, implied growth, iteration count, residual, convergence status, scenario matrix。

==================================================
二十二、验证体系（全部确定性）
==================================================
不创建 Validator Agent。
- CalculationValidator：重新读取 Evidence 并重新计算，普通公式绝对误差 ≤ 1.00000E-12，反向 DCF 相对误差 ≤ 1.00000E-8。
- ClaimValidator：验证数字、趋势、比较和定性 SEC 文本 Claim，无 Evidence ID 或 Calculation ID 的 Claim 必须拒绝。rejected Claim 不能进入 Report Crew。
- FinalReportValidator：检查报告中每个数字、Claim 状态、Verdict、市场价格时间戳、rejected Claim 和非投资建议声明。

==================================================
二十三、确定性评级
==================================================
最终评级不得由任何 Agent 决定。允许评级：attractive, reasonable, watchlist, expensive, insufficient_data。
VerdictResult 至少包含：business_quality, financial_trend, valuation, risk_level, overall_rating, summary_code, triggered_rules, rules_version。
所有阈值集中在 verdict/rules.py，不得散落在 Agent Prompt 或其他模块。

==================================================
二十四、输出 Artifacts
==================================================
每次运行在 outputs/<request_id>/ 下生成：
parsed_request.json, entity.json, scope.json, source_manifest.json, evidence.json, evidence_validation.json, ttm.json, calculations.json, calculation_validation.json, claims.json, claim_validation.json, verdict.json, report.md, run_summary.json。
严禁写入任何密钥。

==================================================
二十五、测试原则
==================================================
- 默认测试禁止：真实 DeepSeek 调用、真实 SEC/市场数据访问、付费 API 调用。
- 使用 Fixture、Fake、Mock 和 Dependency Injection。
- Apple 离线 Fixture 目标位于 tests/fixtures/apple/，包含 entity_mapping.json, submissions.json, companyfacts.json, filings/, market/, expected/，并注明 fixture_version、synthetic、purpose、source_note。
- 第一条垂直切片：Apple synthetic fixture → EntityRef → EvidenceItem → Evidence Validation → TTM → Financial Metrics → Calculation Validation → JSON Artifacts（暂不含真实 LLM、真实数据、历史估值、反向 DCF、Analysis Crew、Report Crew）。

==================================================
二十六、每次单独任务的执行流程
==================================================
收到任务后严格按以下步骤：
1. 读取 AGENTS.md 与相关文档
2. 检查 Git 状态
3. 确认当前 uv 项目环境
4. 列出允许修改文件
5. 检查依赖接口
6. 给出不超过 10 行的实施计划
7. 代码任务先写失败测试，再写最小实现；文档任务先运行文档契约
8. 运行测试
9. 重构
10. 审查完整 diff
11. 运行 ruff 和项目现有类型检查器
12. 使用中文汇报
13. 暂停，不自动开始下一个任务

禁止：
- 自动进入下一个任务
- 自动提交 Git commit
- 修改任务范围外文件
- 为通过测试而降低标准
- 删除与本任务无关的用户代码
- 回滚无关修改
- 未验证就声称完成

==================================================
二十七、任务完成后的汇报格式
==================================================
必须包含以下部分：
## 完成内容
## 修改文件
## 核心设计
## 新增或修改测试
## 实际执行命令
## 验证结果
## 范围外修改
## 未解决问题
必须报告真实执行结果。若测试未运行，须明确说明。完成后暂停。

==================================================
二十八、当前动作
==================================================
现在不要实现业务代码。请先：
1. 阅读仓库中的 AGENTS.md 和 docs 文档；
2. 查看当前目录与目标架构的差异；
3. 确认项目固定为 4 个 LLM Agent、3 个 Crew；
4. 确认 LLM 模块与普通 Python 模块的划分；
5. 确认当前使用 uv 工作流；
6. 用中文简要复述你对架构的理解；
7. 等待我发送第一个具体开发任务。
