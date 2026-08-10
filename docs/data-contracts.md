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

## 6. WP10 REIT 专用数据契约（`reit-profile:v1`）

本节只适用于 `issuer_profile=reit`，是 REIT 数据映射、Metric Policy、Gate 和报告的唯一输入契约。REIT Profile 的 `profile_version` 固定为 `reit-profile:v1`；本节定义的 REIT `MetricPolicy.policy_version` 固定为 `metric-policy:v2`，不改写前文共享模型或其他历史版本字段。

### 6.1 Profile envelope 与来源事实

REIT envelope 的最小字段形状如下。`ReitLine`、`Period`、`CalculationRecord` 和 `PolicyDecision` 均为下方定义的字段形状；尖括号是类型标记，不是可输出的数值。

```json
{
  "profile_version": "reit-profile:v1",
  "issuer_profile": "reit",
  "security_profile": "common_stock|multi_class|adr",
  "reporting_profile": "domestic_us_gaap|foreign_private_issuer_ifrs|unknown",
  "coverage_level": "full|partial|evidence_only|unsupported_security",
  "classification_evidence_ids": ["<validated evidence_id>"],
  "us_gaap_tags": ["<observed tag>"],
  "policy_version": "metric-policy:v2",
  "ffo_reconciliation": {
    "gaap_net_income": "<ReitLine>",
    "adjustments": ["<ReitLine with signed_amount>"],
    "disclosed_ffo_total": "<ReitLine>"
  },
  "metric_values": {
    "<metric_id>": {
      "value": "<decimal string>|null",
      "unit": "<unit>",
      "currency": "<currency>",
      "period": "<Period>|null",
      "policy_decision": "<PolicyDecision>"
    }
  },
  "calculation_records": ["<CalculationRecord>"]
}
```

`ReitLine` 是直接来源事实或 reconciliation 行，必须包含：

| 字段 | 约束 |
| --- | --- |
| `line_type` | `gaap_net_income`、`ffo_adjustment` 或 `disclosed_ffo_total` |
| `label` | 来源中的原始行名；不得由 Agent 改写成未披露的调整项 |
| `signed_amount` | 有限 Decimal 的 JSON 字符串，保留来源实际正负号；来源明确披露为零时才可为 `"0"` |
| `evidence_id` | 稳定 ID，必须已验证；未验证 ID 不得写入有效 provenance 列表 |
| `unit` | 来源声明的金额单位；无单位不可用 |
| `currency` | 来源声明的币种；货币事实不得与其他币种混算 |
| `period` | `period_start`、`period_end`、`fiscal_year`、`fiscal_period`、`audited` 和明确的 `basis` |
| `filed_at` / `as_of` | 分别为 filing 日期和带时区的观察时间，遵守 point-in-time 约束 |
| `source_reference` | 可访问的来源 URL；缺失或非来源 URL 时不可用 |
| `validation_status` | 只有 `valid` 的来源事实可以进入可用 FFO 或其计算 |

分类证据必须进入已有 `classification_evidence_ids`；`us_gaap_tags` 只记录实际观察到的 tag，不能因为 tag 缺失而伪造 FFO reconciliation，也不能用 tag 代替公司披露的调整行。FFO/AFFO 的来源规则引用 [NAREIT FFO](https://www.reit.com/glossary/funds-operation-ffo)、[NAREIT AFFO](https://www.reit.com/glossary/adjusted-funds-operations-affo) 和 [SEC Non-GAAP Financial Measures](https://www.sec.gov/rules-regulations/staff-guidance/corporation-finance-interpretations/non-gaap-financial-measures)。

### 6.2 FFO reconciliation 与记录关系

核心 FFO 只有在完整、可审计的 NAREIT 或公司明确标注的 FFO reconciliation 存在时才可用，且必须同时包含：

- GAAP 净利润基数 `gaap_net_income`；
- 来源实际列出的每一个调整项 `adjustments`，每项保留实际带符号的 `signed_amount`；
- 来源披露的 FFO 总额 `disclosed_ffo_total`；
- 上述每一行自己的 `evidence_id`、期间、单位、币种、`filed_at`、验证状态和来源 URL。

调整项缺失不是零：不能把缺少的行补为 `0`，不能从 US-GAAP tag、普通企业字段或自然语言推断调整项，也不能为不完整 reconciliation 伪造 `CalculationRecord`。来源明确写出某一调整项为零时，该零值必须仍有自己的有效 `EvidenceRecord`。FFO 总额以披露事实为准；只有所有实际输入 Evidence 均有效、期间/单位/币种一致且算术身份得到验证时，才可创建 reconciliation `CalculationRecord`，该记录不能替代披露总额的 Evidence。

三类记录的关系固定为：

1. `EvidenceRecord` 保存 filing/公司披露的原始事实（包括每个 FFO reconciliation 行、稀释加权平均股数、股息和市场价格）。
2. `CalculationRecord` 只保存 Python 对已验证 Evidence 的确定性推导；`input_evidence_ids` 必须是真实输入，期间、单位和币种必须可复核。缺输入时不创建记录。
3. `PolicyDecision` 是唯一向 Gate 和报告暴露的指标结论，携带 `status`、`reason_code`、`blocking` 以及已验证的 `evidence_ids`/`calculation_ids`。LLM 不得创建、补全或替换上述 ID。

### 6.3 REIT Metric Policy 矩阵

所有可用数值都必须能沿 `PolicyDecision -> CalculationRecord（如有） -> EvidenceRecord` 回溯；直接披露指标至少带 Evidence，派生指标必须同时带输入 Evidence 和 Calculation。`ffo_total` 与 `ffo_per_share` 是核心 FFO 所需指标；其缺失可以阻断完整 REIT coverage，但任何缺失都必须保持 `value=null`。AFFO 和下表中的可选指标均为 non-blocking。

| `metric_id` | `applicability` | 固定 `formula_id` / 来源规则 | `gate_effect` | 不可用或不适用原因 |
| --- | --- | --- | --- | --- |
| `ffo_total` | `required` | `reit-ffo-reconciliation-v1`；完整 NAREIT/公司 reconciliation | `blocking` | `ffo_reconciliation_not_disclosed`、`ffo_adjustment_missing`、`ffo_period_mismatch`、`ffo_unit_mismatch`、`ffo_currency_mismatch` |
| `ffo_per_share` | `required` | `reit-ffo-per-share-v1`；同期间 FFO 总额 / 稀释加权平均股数 | `blocking` | `ffo_total_missing`、`diluted_weighted_average_shares_missing`、`ffo_per_share_period_mismatch`、`ffo_per_share_unit_mismatch` |
| `affo` | `optional` | `company-disclosed-affo-reconciliation-v1`；只接受公司明确披露且带 reconciliation/source 的 AFFO | `non_blocking` | `affo_reconciliation_not_disclosed` |
| `same_store_noi` | `optional` | `company-disclosed-same-store-noi-v1`；保留公司 same-store 定义和期间 | `non_blocking` | `same_store_noi_not_disclosed` |
| `occupancy` | `optional` | `company-disclosed-occupancy-v1`；保留 physical/economic 等原始定义 | `non_blocking` | `occupancy_not_disclosed` |
| `net_debt_to_ebitda` | `optional` | `reit-net-debt-to-ebitda-v1`；明确期间的 net debt / EBITDA，不自动年化 | `non_blocking` | `net_debt_to_ebitda_not_disclosed`、`net_debt_to_ebitda_period_ambiguous`、`net_debt_to_ebitda_no_annualization_basis` |
| `dividend_coverage` | `optional` | `reit-dividend-coverage-v1`；FFO attributable to common / common dividends | `non_blocking` | `dividend_coverage_not_disclosed`、`zero_common_dividends` |
| `price_to_ffo` | `optional` | `reit-price-to-ffo-v1`；market price / FFO per share | `non_blocking` | `market_price_missing`、`non_positive_ffo_per_share`、`price_to_ffo_currency_mismatch` |
| `pe` | `not_applicable` | 无 REIT 普通 P/E 计算 | `non_blocking` | `reit_primary_valuation_not_pe` |
| `fcf_yield` | `not_applicable` | 无 REIT 普通 FCF yield 计算 | `non_blocking` | `reit_primary_cash_metric_not_fcf` |

`available` 表示值非空、必要的 Evidence/Calculation 已验证且期间/单位/币种一致；`unavailable` 表示指标适用但字段、来源、验证、期间或分母条件不足，值必须为 `null`；`not_applicable` 表示 Policy 明确不适用，值同样为 `null`，不能用它掩盖缺失。REIT 的普通 P/E 和 FCF yield 应在报告中解释为不适用，并引导读者查看 FFO、公司披露的 AFFO 和 `price_to_ffo`；它们不得成为无条件阻断项。

REIT Policy 不把缺失、无来源、未验证或期间/单位不匹配标为 `invalid`；这些情况按上表使用 `unavailable`。`invalid` 仅保留给共享模型或有限 Decimal 结构校验失败。
