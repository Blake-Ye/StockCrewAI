# EDGAR 工具、计算器与验证器设计

## 目标

在现有 5 个 Agent、3 个 Crew 的骨架上，增加可离线测试的 CrewAI 工具：使用 AgentVest 环境中的 `edgartools 5.45.1` 获取 SEC 公司与申报数据，使用 `Decimal` 执行财务计算，并对 Evidence 与 CalculationResult 做确定性验证。

## 约束

- 运行环境固定为 Conda `AgentVest`，不创建新环境，不安装新依赖。
- 不新增 Agent、Crew、Manager 或 Validator Agent。
- LLM 只解释工具产生的结构化结果；公司身份、事实、计算和验证由 Python 完成。
- SEC 文件范围固定为最近 3 份 10-K、最近 4 份 10-Q，以及最近 180 天最多 20 份 8-K。
- 默认测试不访问真实 SEC、不调用 DeepSeek；真实 API 通过 `EDGAR_IDENTITY` 显式配置。
- 不把 API key、SEC identity 或完整响应写入日志和 Artifacts。

## 工具契约

### `EdgarTool`

输入至少包含 `company_name` 或 `ticker`，可选 `include_filing_text`。Ticker 作为首选身份入口；只有公司名时使用 edgartools 的公司搜索结果取得 CIK，再由 CIK 构造 `Company`。工具固定抓取上述文件范围和默认财务概念。

输出为 `EdgarResult`，包含：

- `status`、输入值、官方公司名、规范化 ticker、10 位 CIK；
- `facts`：按 canonical metric id 映射的 `EdgarFact`，包含 `evidence_id`、值、单位、期间、表单、accession 和 SEC Company Facts 来源；
- `filings`：`EdgarFilingEvidence`，包含 `evidence_id`、form、filed date、period end、accession、items、来源 URL 和可选文本；
- `warnings` 和结构化 `errors`。

### `FinancialCalculatorTool`

输入为上游 `facts` 映射和公式名称，允许事实值为纯数值或包含 `value`/`evidence_id` 的对象。内部使用 `Decimal`，返回 `CalculationBatch`，每项包含 calculation id、formula id/version、精确输入、输入 Evidence ID、原始结果、规范化结果、单位、验证状态和 warnings。

V1 实现常用公式：收入增长、营业利润率、净利率、自由现金流、自由现金流率、现金转换率、净现金、流动比率、债务权益比和股份稀释。缺少输入返回 `unavailable`，不把缺失数据当成零。

### `FinancialValidationTool`

输入为 facts、calculations、company identity 和必需字段。验证身份完整性、Evidence 来源字段、数值有限性、资本开支正数约定，并按公式重新计算 CalculationResult。输出 `ValidationResult`，区分 `valid`、`invalid`、`unavailable`，并列出结构化 issues。

## Agent 接入

- `FinancialQualityAgent`：EDGAR、计算器、验证器；只能基于工具结果生成带 `evidence_ids` 与 `calculation_ids` 的 Claims。
- `RiskAnalysisAgent`：EDGAR；只能基于带 filing 元数据的 Evidence 生成风险 Claims。
- `ValuationAnalysisAgent`：计算器、验证器；只能解释已验证 CalculationResult，不创建估值假设。
- `RequestParserAgent` 与 `ReportWriterAgent` 不接入数据工具。

## 错误与网络边界

EDGAR 工具延迟导入 `edgar`，并从 `EDGAR_IDENTITY` 读取 SEC 身份。缺失身份、公司不存在、SEC 超时或解析失败均返回结构化错误，不伪造空数据。网络错误和数据缺失都保留原因，供后续 Flow 决定是否重试或停止。

## 测试策略

- 用 fake edgar module、fake Company、fake filings 和 fake facts 验证 EDGAR 归一化，不访问网络。
- 用固定 Decimal 输入验证公式、缺失输入和重算验证。
- 验证三个 Analysis Agent 的工具注册和配置输出约束。
- 使用 `conda run -n AgentVest python -m unittest`、`compileall` 和 `git diff --check`。

## 本次不实现

不实现完整 Flow、TTM 构建、市场价格、历史估值、反向 DCF、Verdict、ClaimValidator 或报告 Artifacts；这些仍按项目预期架构作为后续确定性模块。
