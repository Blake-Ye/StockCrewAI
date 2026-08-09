# WP00 数据契约

本文件冻结 WP00 的边界。它描述当前代码已经使用的 JSON-safe 数据形状，以及后续 Profile、量化和报告模块必须遵守的最小契约。契约不把 LLM 输出当作事实来源。

## 1. 传输原则

- Flow state、Crew 输入和 Crew 输出必须可以被 Pydantic 序列化为 JSON。
- SEC、行情、计算器和验证工具创建 Evidence/Calculation 记录；Agent 只能引用这些记录，不能自行声明 ID 已验证。
- 缺失值用 `null` 或明确的 `status=unavailable` 表示，不能用 0、空字符串或 NaN 代替。
- `profile` 描述发行人、证券和报告制度的适用性；未知 Profile 进入 `evidence_only` 或 `unsupported_security`，不能猜测为普通企业。

## 2. 主要对象

| 对象 | 必要边界 | 责任方 |
| --- | --- | --- |
| `Request` | 原始请求、公司名或 ticker、语言、投资期限、关注点 | Request Parser Crew；只解析，不查事实 |
| `ResearchFlowState` | `request`、`profile`、`parsed_request`、`edgar`、`calculations`、`validation`、`analysis`、`gate`、`verdict`、`report`、`stage` | Flow；按事件保存状态 |
| `Evidence` | `evidence_id`、来源 URL/文件、`filed_at`、观察期间、单位、币种、原始值、验证状态 | SEC/行情工具及验证器 |
| `Calculation` | `calculation_id`、公式版本、输入 Evidence ID、结果、单位、期间、状态 | Python 计算器 |
| `Claim` | `claim_id`、`category`、事实陈述、`evidence_ids`、`calculation_ids`、置信度 | Financial/Risk Agent 解释已验证记录 |
| `Gate` | `status`、`required_data`、`reason_code`、适用性和可追溯诊断 | Python 确定性 Gate |
| `Report` | 仅使用 Gate 通过的 Claim 和确定性数字；包含状态、来源和免责声明 | Report Crew + Python renderer |

## 3. ID 和验证

`evidence_id` 必须存在于 `validated_evidence_ids`，`calculation_id` 必须存在于 `validated_calculation_ids`；验证器还要确认状态、单位、期间和公式输入一致。Claim 只要缺少必需字段、ID 未验证或类别与证据不匹配，就被拒绝并记录 `reason_code`，不传入报告。

## 4. 允许的 Agent 输出

FinancialQualityAgent 和 RiskAnalysisAgent 输出严格的 Claims JSON；不输出 `metric`、`value`、新计算、评级或买卖动作。Valuation 数字由 Python 计算后传入 Report context。Report Agent 只返回固定叙事字段，不新增数字或结论。

## 5. WP01 Shared Models（共享模型）

WP01 在 `src/stockcrewai/models/` 中冻结共享 Pydantic 模型。以下字段与当前代码一致；模型只负责结构、类型和边界校验，不实现 resolver、store、Gate 或 Flow/Crew 接入。

### 5.1 Request/CompanyIdentity

`CompanyIdentity` 是公司身份候选记录，不负责解析来源、消歧或选择最终证券。

| 模型 | 字段 | 类型与约束 |
| --- | --- | --- |
| `CompanyIdentity` | `company_name` | 非空字符串或 `None`；字符串去除首尾空白后长度至少为 1 |
|  | `ticker` | 非空字符串或 `None`；字符串去除首尾空白后长度至少为 1 |
|  | `cik` | 非空字符串或 `None`；字符串去除首尾空白后长度至少为 1 |
|  | `exchange` | 非空字符串或 `None`；字符串去除首尾空白后长度至少为 1 |
|  | `security_type` | 非空字符串或 `None`；字符串去除首尾空白后长度至少为 1 |
|  | `source_reference` | 非空字符串或 `None`；字符串去除首尾空白后长度至少为 1 |
|  | `status` | `resolved`、`ambiguous`、`unsupported` 或 `unavailable` |
|  | `reason_code` | 非空字符串；去除首尾空白后长度至少为 1 |

| `ParsedResearchRequest` | `company_mention` | 非空字符串；去除首尾空白后长度至少为 1 |
|  | `company_name_guess` | 非空字符串或 `None` |
|  | `ticker_guess` | 非空字符串或 `None` |
|  | `exchange_guess` | 非空字符串或 `None` |
|  | `request_type` | 非空字符串；去除首尾空白后长度至少为 1 |
|  | `investment_horizon` | 非空字符串或 `None` |
|  | `requested_focus` | 由非空字符串组成的列表；每一项去除首尾空白后长度至少为 1 |
|  | `language` | 非空字符串；去除首尾空白后长度至少为 1 |
|  | `confidence` | `StrictFloat`，范围为 0 到 1（含边界） |
| `ParsedRequest` | 与 `ParsedResearchRequest` 相同的九个字段 | 兼容类型；不接入现有 Request Parser Crew |

六个身份字段均只能是非空字符串或 `None`。`status=resolved` 时，六个字段必须全部存在；`ambiguous`、`unsupported` 或 `unavailable` 只保留已知字段，缺失字段使用 `null`，不得使用 `unknown` 或 `unavailable` 作为字段占位值。

### 5.2 Profile/Policy

Profile 和 Policy 模型只表达适用性、覆盖范围、策略决定及 Gate 结果，不执行 Profile resolver 或确定性 Policy/Gate。

| Enum | 允许值 |
| --- | --- |
| `IssuerProfile` | `standard_operating`、`bank`、`insurance`、`reit`、`utility`、`commodity_producer`、`pre_revenue`、`holding_company`、`unknown` |
| `SecurityProfile` | `common_stock`、`multi_class`、`adr`、`spac`、`recent_listing`、`unsupported_fund_security`、`unknown` |
| `ReportingProfile` | `domestic_us_gaap`、`foreign_private_issuer_ifrs`、`investment_company_reporting`、`unknown` |
| `CoverageLevel` | `full`、`partial`、`evidence_only`、`unsupported_security` |
| `Applicability` | `required`、`optional`、`not_applicable` |
| `GateEffect` | `blocking`、`non_blocking` |

| 模型 | 字段 | 类型与约束 |
| --- | --- | --- |
| `ProfileResult` | `issuer_profile` | `IssuerProfile` |
|  | `security_profile` | `SecurityProfile` |
|  | `reporting_profile` | `ReportingProfile` |
|  | `coverage_level` | `CoverageLevel` |
|  | `classification_evidence_ids` | 由非空字符串组成的列表；默认 `[]` |
|  | `reason_codes` | 由非空字符串组成的列表；默认 `[]` |
|  | `registry_version` | 非空字符串 |
| `MetricPolicy` | `metric_id` | 非空字符串 |
|  | `issuer_profile` | `IssuerProfile` |
|  | `security_profile` | `SecurityProfile` |
|  | `reporting_profile` | `ReportingProfile` |
|  | `applicability` | `Applicability` |
|  | `required_evidence` | 由非空字符串组成的列表；默认 `[]` |
|  | `formula_id` | 非空字符串 |
|  | `period_basis` | 非空字符串 |
|  | `unit_policy` | 非空字符串 |
|  | `gate_effect` | `GateEffect` |
|  | `reason_code` | 非空字符串 |
|  | `policy_version` | 非空字符串 |
| `PolicyDecision` | `metric_id` | 非空字符串 |
|  | `status` | `available`、`unavailable`、`not_applicable` 或 `invalid` |
|  | `evidence_ids` | 由非空字符串组成的列表；默认 `[]` |
|  | `calculation_ids` | 由非空字符串组成的列表；默认 `[]` |
|  | `reason_code` | 非空字符串 |
|  | `blocking` | 布尔值 |
| `GateResult` | `status` | `ready`、`blocked`、`evidence_only` 或 `unsupported` |
|  | `coverage_level` | `CoverageLevel` |
|  | `blocking_decisions` | `PolicyDecision` 列表；默认 `[]` |
|  | `non_blocking_decisions` | `PolicyDecision` 列表；默认 `[]` |
|  | `reason_codes` | 由非空字符串组成的列表；默认 `[]` |
|  | `policy_version` | 非空字符串 |

### 5.3 Evidence/Calculation/Claim/MarketPrice

`ValidationStatus` 的允许值为 `unvalidated`、`valid`、`invalid`。

所有权威 `EvidenceRecord`、`CalculationRecord` 和 `MarketPriceRecord` 都必须带稳定 ID、`source_reference`、时间字段和 `validation_status`。当前代码中的稳定 ID 分别是 `evidence_id`、`calculation_id` 和 `evidence_id`；时间字段分别包括 `as_of`/期间日期、`as_of`/期间日期和 `price_timestamp`。其中所有 `datetime` 必须带时区。

| 模型 | 字段 | 类型与约束 |
| --- | --- | --- |
| `EvidenceRecord` | `evidence_id` | 非空字符串；稳定记录 ID |
|  | `source_reference` | 非空字符串 |
|  | `as_of` | 带时区的 `datetime` |
|  | `filed_at` | `date` |
|  | `period_start` | `date` |
|  | `period_end` | `date` |
|  | `unit` | 非空字符串 |
|  | `currency` | 非空字符串 |
|  | `value` | 有限 `Decimal` 或 `None` |
|  | `validation_status` | `ValidationStatus` |
| `CalculationRecord` | `calculation_id` | 非空字符串；稳定记录 ID |
|  | `formula_id` | 非空字符串 |
|  | `input_evidence_ids` | 至少一个非空字符串的列表 |
|  | `source_reference` | 非空字符串 |
|  | `as_of` | 带时区的 `datetime` |
|  | `result` | 有限 `Decimal` 或 `None` |
|  | `unit` | 非空字符串 |
|  | `period_start` | `date` |
|  | `period_end` | `date` |
|  | `validation_status` | `ValidationStatus` |
| `ClaimRecord` | `claim_id` | 非空字符串 |
|  | `category` | 非空字符串 |
|  | `statement` | 非空字符串 |
|  | `evidence_ids` | 由非空字符串组成的列表 |
|  | `calculation_ids` | 由非空字符串组成的列表 |
|  | `confidence` | `StrictFloat`，范围为 0 到 1（含边界） |
| `MarketPriceRecord` | `evidence_id` | 非空字符串；稳定行情记录 ID |
|  | `ticker` | 非空字符串 |
|  | `price` | 有限 `Decimal`，且必须大于 0 |
|  | `currency` | 非空字符串 |
|  | `price_timestamp` | 带时区的 `datetime` |
|  | `source_reference` | 非空字符串 |
|  | `adjustment_basis` | `raw`、`split_adjusted` 或 `total_return_adjusted` |
|  | `validation_status` | `ValidationStatus` |

`ClaimRecord` 是 LLM 生成的候选叙述，不是已验证事实；它没有自己的 `source_reference`、时间或 `validation_status`。这些信息由其引用的 Evidence/Calculation 记录和后续 Claim Gate 提供。`EvidenceRecord` 在 `validation_status=valid` 时必须有 `value`；`CalculationRecord` 在 `validation_status=valid` 时必须有 `result`。

### 5.4 Quant

| 模型 | 字段 | 类型与约束 |
| --- | --- | --- |
| `PointInTimeSnapshot` | `snapshot_id` | 非空字符串 |
|  | `as_of` | 带时区的 `datetime` |
|  | `cik` | 非空字符串 |
|  | `ticker` | 非空字符串 |
|  | `issuer_profile` | `IssuerProfile` |
|  | `security_profile` | `SecurityProfile` |
|  | `reporting_profile` | `ReportingProfile` |
|  | `filing_cutoff` | 带时区的 `datetime` |
|  | `price_cutoff` | 带时区的 `datetime` |
|  | `available_evidence_ids` | 由非空字符串组成的列表 |
|  | `available_calculation_ids` | 由非空字符串组成的列表 |
|  | `financial_features` | `非空字符串 -> 有限 Decimal 或 None` 的映射 |
|  | `market_features` | `非空字符串 -> 有限 Decimal 或 None` 的映射 |
|  | `data_quality` | JSON 标量映射 |
|  | `builder_version` | 非空字符串 |
| `FactorObservation` | `factor_id` | 非空字符串 |
|  | `formula_version` | 非空字符串 |
|  | `snapshot_id` | 非空字符串 |
|  | `as_of` | 带时区的 `datetime` |
|  | `ticker` | 非空字符串 |
|  | `raw_value` | 有限 `Decimal` 或 `None` |
|  | `normalized_value` | 有限 `Decimal` 或 `None` |
|  | `peer_group` | 非空字符串 |
|  | `peer_count` | `StrictInt`，大于等于 0 |
|  | `evidence_ids` | 由非空字符串组成的列表 |
|  | `calculation_ids` | 由非空字符串组成的列表 |
|  | `status` | `available`、`unavailable`、`not_applicable` 或 `invalid` |
|  | `reason_code` | 非空字符串 |
| `QuantResearchPacket` | `as_of` | 带时区的 `datetime` |
|  | `universe_id` | 非空字符串 |
|  | `strategy_version` | 非空字符串 |
|  | `coverage` | `CoverageLevel` |
|  | `factor_summary` | JSON 标量映射 |
|  | `ranking_summary` | JSON 标量映射 |
|  | `backtest_summary` | JSON 标量映射 |
|  | `benchmark_summary` | JSON 标量映射 |
|  | `data_quality` | JSON 标量映射 |
|  | `limitations` | 由非空字符串组成的列表 |
|  | `artifact_ids` | 由非空字符串组成的列表 |
| `UniverseManifest` | `universe_id` | 非空字符串 |
|  | `tickers` | 至少一项的非空字符串列表 |
|  | `selection_as_of` | 带时区的 `datetime` |
|  | `membership_source` | 非空字符串 |
|  | `membership_basis` | 非空字符串 |
|  | `known_biases` | 至少一项的非空字符串列表，且必须包含 `survivorship_bias_known` |
|  | `manifest_version` | 非空字符串 |

### 5.5 序列化、数值与时间边界

- 所有共享模型禁止额外字段（`extra="forbid"`），并应通过 Pydantic 的 `model_dump(mode="json")` 或 `model_dump_json()` 生成 JSON-safe 数据。
- Evidence 的 `value`、Calculation 的 `result`、MarketPrice 的 `price`、Quant 的金融数值映射以及 FactorObservation 的数值字段使用有限 `Decimal`；这些金融数值拒绝 Python `float`、`NaN` 和 `Infinity`。请求与 Claim 的 `confidence` 是代码明确的 `StrictFloat` 置信度字段，不是金融事实值；其 `NaN`/`Infinity` 也不能通过 0 到 1 的范围校验。
- 缺失值统一使用 `None`，不得补零、空字符串或 `NaN`；`valid` 的 Evidence/Calculation 仍分别要求实际 `value`/`result`。
- 所有 `datetime` 字段必须是 timezone-aware；无时区时间戳被拒绝。

WP01 本阶段只建立和公开共享数据契约，不接入 Flow/Crew，不新增 resolver/store/gate 行为，也不改变当前运行行为。
