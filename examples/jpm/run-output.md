> 真实运行示例；运行开始：`2026-08-20T01:28:54.698076+08:00`。本文仅供技术演示，不构成投资建议。
>
> 阻断原因代码来源：policy_context.gate.reason_codes。

# StockCrewAI 运行输出

## 最终结果

- 业务状态（status）：`blocked`
- 退出码（exit_code）：`0`（退出码与状态独立）
- 阶段（stage）：`analysis`
- 域（domain）：`-`
- 原因（reason_code）：`-`
- 直接原因：-
- required_data：`unsupported_category_sic:sic=6021, unsupported_category_sic:issuer_profile=bank`
- 已完成（completed）：请求解析, SEC 证据与财务验证, 市场价格与估值, Analysis Gate
- 未执行（skipped）：Analysis Crew, Claim Gate, Verdict 与 Report
- 下一步（next_action）：补齐 required_data 后重新运行
- 完整结果：`run-result.json`

## 请求

- 公司：`JPMorgan Chase & Co.`
- Ticker：`JPM`
- 期限：`3年`
- Focus 数量：`1`

## 证据与验证摘要

- 事实数：18；Filing 数：20；风险章节数：10；计算数：10；已验证证据数：18；已验证计算数：5；验证状态：valid

## 估值摘要

- 

## Analysis 与报告

- 通过 Claim Gate Claims：0（财务 0；风险 0；估值 0）
- 报告：未生成

## 时间线

### 1. 请求解析 · completed
- 执行者：Crew/Agent：Request Parser Crew；输入：request=provided；输出：company=JPMorgan Chase & Co.; ticker=JPM; period=3年; focus=1
- 决策：-；原因：-；下一节点：SEC 证据与财务验证
### 2. SEC 证据与财务验证 · completed
- 执行者：确定性工具：Edgar + Calculator + Validation；输入：company=JPMorgan Chase & Co.; ticker=JPM; period=3年；输出：facts=18; filings=20; risk_sections=10; calculations=10; ttm=4/5; validation=valid (18 evidence/5 calculations)
- 决策：-；原因：-；下一节点：市场价格与估值
### 3. 市场价格与估值 · blocked
- 执行者：Python：SEC Scope/Profile Gate；输入：company=JPMorgan Chase & Co.; ticker=JPM; period=3年；输出：unsupported scope; reason_code=unsupported_category_sic; valuation=not_applicable
- 决策：SKIPPED；原因：reason_code=unsupported_category_sic；下一节点：Analysis Gate
### 4. Analysis Gate · blocked
- 执行者：Python Gate：Analysis Gate；输入：company=JPMorgan Chase & Co.; ticker=JPM; period=3年; facts=18; filings=20; calculations=10；输出：BLOCKED; domain=scope; reason_code=unsupported_category_sic; required_data=unsupported_category_sic:sic=6021, unsupported_category_sic:issuer_profile=bank
- 决策：BLOCKED；原因：domain=scope; reason_code=unsupported_category_sic；下一节点：最终阻断
### 7. 最终阻断 · blocked
- 执行者：Python Gate：Analysis Gate；输入：domain=scope；输出：BLOCKED; reason_code=unsupported_category_sic; required_data=unsupported_category_sic:sic=6021, unsupported_category_sic:issuer_profile=bank
- 决策：BLOCKED；原因：Analysis Gate 未通过；下一节点：补齐 required_data 后重新运行

## 运行时间

- 开始：`2026-08-20T01:28:54.698076+08:00`
- 结束：`2026-08-20T01:30:45.416026+08:00`
