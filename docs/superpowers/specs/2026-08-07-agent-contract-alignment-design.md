# Agent Prompt 与 Gate 契约对齐设计

## 目标

让 Request Parser、Analysis Crew 的三个分析 Agent、Report Writer 的 Prompt 与
Python 侧 Gate 使用同一组明确的输入/输出契约，避免 Agent 偶尔漏字段后只能在
流程末端被动阻断，也避免 Prompt 允许的字段和 Gate 接受的字段不一致。

## 设计

Request Parser 使用独立的 `ParsedRequest` 契约，固定包含公司提及、公司名候选、
Ticker 候选、交易所候选、请求类型、投资期限、关注方向、语言和置信度九个字段。
它只负责语义解析；解析 Gate 负责 JSON 形状、字段类型、置信度范围和禁止额外字段。

三个 Analysis Agent 共用 `AnalysisTaskOutput`/`AnalysisClaim` 契约。每条 Claim
必须且只能包含 `claim_id`、`category`、`statement`、`evidence_ids`、
`calculation_ids`、`confidence` 六个字段。三个域只在允许类别、Evidence 白名单、
Calculation 白名单和是否允许空 Calculation 列表上有差异：财务和估值必须引用
已验证 Calculation，风险必须使用 filing Evidence 且 Calculation 列表为空。
Prompt、Task Guardrail 和 Claim Gate 使用相同的域规则。

Report Writer 使用 Markdown 报告契约，不重新生成事实。Report Gate 检查输出非空、
保留确定性 Verdict 状态、不伪造评级或建议、不引入 rejected Claim，并在失败时
阻断最终报告状态。

所有 Agent 的 Prompt 都明确列出：输入来源、禁止的数据来源、完整字段、空输入行为、
禁止额外字段以及最终输出格式。Guardrail 只做本地结构检查并允许同一 Agent 重试；
Evidence/Calculation 白名单和业务路由仍由 Python Gate 决定。

## 不在范围内

- 不升级 CrewAI、DeepSeek、yfinance 或其他依赖。
- 不改变 SEC、Yahoo、计算器、估值公式或 Verdict 政策。
- 不让 LLM 选择来源、重新计算、修改数字或决定 Flow 路由。
- 不保存原始 Agent 输出到 SQLite；诊断仍遵守现有脱敏规则。

## 验收标准

- 任一 Agent 漏必填字段、出现额外字段或输出非 JSON 时，Guardrail 可在本地失败并重试。
- Risk Agent 的 `calculation_ids` 非空、Valuation Agent 缺 Calculation ID、Financial Agent
  引用非法 ID 时，Gate 给出对应域和原因码。
- 完整有效输入能通过所有 Gate，并继续执行 Verdict 与 Report。
- 离线测试不访问 SEC、Yahoo 或 DeepSeek。
