# 当前数据契约

本文件描述当前基本面研究 Flow 的最小 JSON-safe 数据形状。LLM 输出是候选解释，不是事实来源。

## 1. 传输原则

- Flow state、Crew 输入和 Crew 输出必须能被 Pydantic 序列化。
- EDGAR、行情、计算器和验证器创建 Evidence/Calculation；Agent 只能引用已经存在的 ID。
- 缺失值用 `null` 或明确状态表示，不能用 0、空字符串或 `NaN` 代替。
- Profile 先判断证券和指标适用性；未知或不支持证券进入结构化范围结果。

## 2. 主对象

| 对象 | 关键字段 | 创建者 |
| --- | --- | --- |
| `ParsedResearchRequest` | 公司提及、ticker 候选、关注点、语言、投资期限 | Request Parser Crew |
| `ResearchFlowState` | request、profile、edgar、calculations、validation、analysis、gate、verdict、report、stage | Flow |
| `EvidenceRecord` | evidence_id、source_reference、时间/期间、单位、币种、value、validation_status | 数据工具/验证器 |
| `CalculationRecord` | calculation_id、formula_id、输入 Evidence ID、result、状态 | Python 计算器 |
| `ClaimRecord` | claim_id、category、text、evidence_ids、calculation_ids、confidence | Analysis Crew |
| `PolicyDecision` | metric_id、status、来源 ID、reason_code、blocking | Profile/Policy 流水线 |
| `GateResult` | status、coverage、blocking_decisions、reason_codes | Python Gate |
| `ReportContext` | company、claims、valuation、policy、verdict 和可验证展示数据 | Flow/Renderer |

## 3. Claim 边界

Claim 必须引用本次运行 allowlist 中已验证的 Evidence/Calculation 或 filing section。字段缺失、ID 未验证、类别与证据不匹配时，Claim Gate 拒绝该 Claim，并返回稳定原因；不把被拒绝 Claim 传给报告。

FinancialQualityAgent 和 RiskAnalysisAgent 不输出新数字、评级、买卖动作或新来源。ReportWriterAgent 只组织叙述草稿；确定性 Renderer 负责数字、状态、来源和免责声明。

## 4. 状态语义

- `available`：所需来源和公式验证通过。
- `not_applicable`：Profile 明确该指标不适用，不等于 0，也不自动阻断。
- `unavailable`：该指标可能适用，但当前证据或外部数据不可用。
- `blocked`：当前阶段必需数据或输出契约未满足，不能继续生成正式报告。
