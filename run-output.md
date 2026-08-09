# StockCrewAI 运行输出

## 最终结果

- 业务状态（status）：`ok`
- 退出码（exit_code）：`0`（退出码与状态独立）
- 阶段（stage）：`report`
- 完整结果：`run-result.json`

## 请求

- 公司：`Apple Inc.`
- Ticker：`AAPL`
- 期限：`3年`
- Focus 数量：`3`

## 证据与验证摘要

- 事实数：16；Filing 数：11；风险章节数：4；计算数：10；已验证证据数：16；已验证计算数：10；验证状态：valid

## 估值摘要

- 价格：313.33；时间戳：2026-08-07T20:00:01Z；币种：USD；P/E：35.93x；FCF Yield：2.99%；历史百分位：83.05084745762711864406779661；Reverse DCF 隐含增长：0.1246485448131494163759129532

## Analysis 与报告

- 通过 Claim Gate Claims：16（财务 10；风险 3；估值 3）
- 报告：已生成
- 正式报告：investment-report.md

## 时间线

### 1. 请求解析 · completed
- 执行者：Crew/Agent：Request Parser Crew；输入：request=provided；输出：company=Apple Inc.; ticker=AAPL; period=3年; focus=3
- 决策：-；原因：-；下一节点：SEC 证据与财务验证
### 2. SEC 证据与财务验证 · completed
- 执行者：确定性工具：Edgar + Calculator + Validation；输入：company=Apple Inc.; ticker=AAPL; period=3年；输出：facts=16; filings=11; risk_sections=4; calculations=10; ttm=7/7; validation=valid (16 evidence/10 calculations)
- 决策：-；原因：-；下一节点：市场价格与估值
### 3. 市场价格与估值 · completed
- 执行者：确定性工具：Market Price + Valuation；输入：company=Apple Inc.; ticker=AAPL; period=3年；输出：price=313.33; timestamp=2026-08-07T20:00:01Z; currency=USD; PE=35.93x; FCF Yield=2.99%; historical percentile=83.05084745762711864406779661; reverse DCF growth=0.12464854481314941…
- 决策：-；原因：-；下一节点：Analysis Gate
### 4. Analysis Gate · completed
- 执行者：Python Gate：Analysis Gate；输入：company=Apple Inc.; ticker=AAPL; period=3年; facts=16; filings=11; calculations=10；输出：READY; domain=unavailable; reason_code=unavailable; required_data=none
- 决策：READY；原因：domain=unavailable; reason_code=unavailable；下一节点：Analysis Crew
### 5. Analysis Crew · completed
- 执行者：Crew/Agent：Analysis Crew；输入：company=Apple Inc.; ticker=AAPL; period=3年; financial/risk=validated input; valuation=deterministic builder；输出：agent_tasks=2; deterministic_valuation_claims=3; attempts=1; Claims=awaiting Claim Gate; facts=16; calculations=10
- 决策：-；原因：-；下一节点：Claim Gate
### 6. Claim Gate · completed
- 执行者：Python Gate：Claim Gate；输入：Analysis Crew 原始结果（仅内部传递）；输出：READY; financial_claims=10; risk_claims=3; valuation_claims=3; domain=unavailable; reason_code=unavailable; required_data=none
- 决策：READY；原因：domain=unavailable; reason_code=unavailable；下一节点：Verdict 与 Report
### 7. Verdict 与 Report · completed
- 执行者：Python：Deterministic Verdict + Crew/Agent：Report Crew；输入：financial_claims=10; risk_claims=3; valuation_claims=3；输出：Verdict=ready; Report=generated; draft_source=agent; PE=35.93x; FCF Yield=2.99%; historical percentile=83.05084745762711864406779661; reverse DCF growth=0.124648544813149416375912…
- 决策：READY；原因：Claim Gate 已通过；下一节点：结束

## 运行时间

- 开始：`2026-08-09T14:41:11.825687+08:00`
- 结束：`2026-08-09T14:43:01.801759+08:00`
