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
