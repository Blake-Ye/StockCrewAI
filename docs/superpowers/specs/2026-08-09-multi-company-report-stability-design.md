# StockCrewAI 多公司报告稳定性设计

**日期：** 2026-08-09
**状态：** 已获用户批准，等待实施计划
**目标公司：** Apple Inc.（AAPL）、NVIDIA Corporation（NVDA）、Tesla, Inc.（TSLA）

## 1. 目标

修复当前多公司运行中的两个真实阻断点，使系统在存在完整、可验证 SEC 证据时，不再因为 Risk Agent 空输出或 Report Draft 格式不稳定而阻断正式报告。

完成后必须满足：

1. AAPL、NVDA、TSLA 的请求均能经过请求解析、SEC、计算、估值、Analysis Gate、Analysis Crew、Claim Gate、Verdict、Report Safety Gate。
2. 有合格风险证据时，模型空输出不能单独导致 `claims_empty` 阻断。
3. Report Agent 格式失败不能单独导致整条研究线路失效。
4. 不降低 Claim Gate、Final Report Validator 或 Evidence ID 校验标准。
5. 不编造风险、评级、数字、来源或未来预测。
6. 默认离线测试不调用 SEC、Yahoo 或 DeepSeek；真实三公司回归必须显式运行并分别保存产物。

## 2. 已确认根因

### 2.1 TSLA 风险 Claims 为空

当前 `edgar_tool.py` 将每份文本完整的 8-K 整体标记为 `8k_event` 风险章节。TSLA 本轮四份 8-K 只有 Item 2.02 和 Item 9.01 附件说明，正文未包含附件中的风险信息。

这些文件机械上满足“文本可用、未截断、risk_sections 非空”，因此 Analysis Gate 错误放行。Risk Agent 遵守 Prompt，不从无风险正文的附件外壳中推断风险，正确返回 `{"claims": []}`，随后 Claim Gate 以 `claims_empty` 阻断。

根因位于风险证据资格判断，不在 Claim Gate、Evidence ID 或 JSON Schema。

### 2.2 NVDA 报告阶段异常

NVDA 已形成 17 条通过 Claim Gate 的 Claims，Verdict 为 ready；canonical Report Context、Renderer 和最终 Markdown 安全检查均可离线通过。

CrewAI 1.15.11 在 Task guardrail 重试耗尽时抛出通用 `Exception`。当前 `_failure_summary()` 只保留异常类型，最终只能看到：

```text
report_kickoff:Exception
```

Report Agent 接收的 canonical Context 约 113,314 字符，其中包含 Renderer 才需要的历史序列、来源明细和完整指标。过大的输入增加九字段 Draft 违反格式规则的概率，但本轮没有保存固定 guardrail 错误码，因此不能确认具体违反哪条规则。

### 2.3 自定义输出目录

根目录 AAPL 三产物生成于 16:27；NVDA/TSLA 临时产物生成于 16:37 和 16:40。当前时间线排除了“自定义 `output_path` 回写根目录”的判断。

生产代码不因此修改，只增加负向指纹测试，防止未来出现真实回归。

## 3. 安全不变量

以下规则不可因“提高成功率”而放宽：

- SEC Evidence 必须包含 Evidence ID、表单类型、期间或 filed_at、accession number 和来源。
- 10-K/10-Q 风险证据必须来自完整 Item 1A 正文，不能来自目录或截断预览。
- 8-K 附件外壳不能当作风险正文。
- rejected Claim 永远不能进入 Verdict 或报告。
- LLM 不选择 SEC 文件、不计算数字、不验证数字、不决定评级。
- 确定性 Risk Claim 不推断概率、严重度、因果关系或未来影响。
- Report Agent 不直接提供报告数字；数字仍由确定性 Renderer 插入。
- 若真实 SEC 核心风险正文经过完整抓取后仍不可用，系统继续硬阻断，不生成缺少风险依据的正式报告。
- 不通过吞掉异常、伪造 Claim、删除 Gate 或输出空风险章节实现“成功”。

## 4. 目标架构

```text
SEC Filing Plan
  -> Full Section Retriever
  -> Risk Evidence Eligibility Classifier
       -> eligible Item 1A / substantive 8-K event
       -> rejected shell / truncated / unsupported
  -> Analysis Gate
  -> Risk Agent（最多一次重试）
       -> valid Claims
       -> empty with eligible evidence
            -> Deterministic Risk Disclosure Builder
  -> Existing Claim Gate
  -> Verdict

Canonical Report Context
  -> Compact Narrative Projection -> Report Agent -> ReportDraft Guardrail
       -> valid nine-field draft
       -> exhausted -> Deterministic Safe Draft
  -> Full Canonical Context + accepted draft -> Deterministic Renderer
  -> Final Report Validator
  -> Atomic Output Writer
```

## 5. 风险证据资格设计

### 5.1 合格来源

风险来源分成两类：

1. `item_1a`
   - 10-K Item 1A 完整正文。
   - 10-Q Part II Item 1A 完整正文。
   - 必须能确认章节起止边界，且 `text_truncated=false`。

2. `substantive_8k_event`
   - 文件正文实际包含事件事实，不只是“附件已提交”或目录。
   - 初始允许的高确定性 Item：1.03、2.05、2.06、3.01、4.01、4.02、5.02、8.01。
   - Item 2.02 和 9.01 本身不构成风险资格；只有相关 Exhibit 正文被单独获取并验证后，Exhibit 才能作为候选 Evidence。

### 5.2 明确排除

- 只有封面、目录、前瞻性陈述通用免责声明的 filing。
- `text_truncated=true` 且未单独取得 Item 1A 的 filing。
- 仅说明 Exhibit 99.1 已附上的 Item 2.02/9.01 filing shell。
- 不在确定性 Item allowlist 内、且没有完整风险章节的 8-K。
- 没有稳定 Evidence ID 或 source reference 的文本。

### 5.3 资格结果契约

每个候选来源输出：

```json
{
  "evidence_id": "ev_filing_...",
  "eligibility": "eligible|rejected",
  "evidence_kind": "item_1a|substantive_8k_event|null",
  "reason_code": "eligible_item_1a|eligible_8k_event|attachment_shell|truncated|unsupported_item|missing_body",
  "section_title": "Item 1A. Risk Factors",
  "filed_at": "2026-01-01",
  "source_reference": "https://www.sec.gov/..."
}
```

只有 `eligibility=eligible` 的 Evidence ID 可以进入 Risk Agent allowlist。

## 6. 确定性 Risk Disclosure Builder

### 6.1 触发条件

Builder 只能在以下条件全部成立时运行：

1. Risk Agent 初次输出为空。
2. 使用相同 eligible packet 重试一次后仍为空。
3. 至少存在一个通过资格检查的风险 Evidence。
4. Evidence ID 位于当前运行 allowlist。

如果没有 eligible Evidence，应在 Analysis Gate 以 `risk_evidence_missing` 阻断，不能进入 Builder。

### 6.2 输出限制

Builder 只生成“披露事实 Claim”：

- Item 1A：陈述公司在指定 filing 中披露了对应风险因素章节或稳定提取的风险标题。
- 8-K：陈述公司在指定日期披露了对应 Item 的事实事件。
- `evidence_ids` 只能复制当前 eligible Evidence ID。
- `calculation_ids=[]`。
- `confidence` 使用固定确定性值，不由模型决定。
- 不生成风险高低、发生概率、损失金额、投资建议或未来影响。

Builder 输出继续经过现有 Claim Gate。任何 schema、Evidence ID 或类别错误仍必须被拒绝。

## 7. Report Agent 稳定性设计

### 7.1 双 Context 边界

完整 canonical Context 继续供 Renderer 使用，但不得整体传给 Report Agent。

Report Agent 只接收 `NarrativeContext`：

```json
{
  "company_name": "NVIDIA Corporation",
  "ticker": "NVDA",
  "investment_horizon": "3年",
  "verdict_labels": {
    "valuation": "expensive",
    "risk_level": "medium"
  },
  "claim_summaries": {
    "financial_quality": [],
    "financial_trend": [],
    "risk": [],
    "valuation": []
  },
  "claim_counts": {},
  "available_sections": []
}
```

约束：

- 不包含完整 Evidence 文本、filing 原文、历史价格序列或全部 source references。
- 不包含 Renderer 专用图表数据。
- 不包含 rejected Claims。
- JSON 序列化后默认上限为 24 KiB；超限时按确定性顺序截取每类 Claim 摘要，并记录计数，不随机截断。
- Renderer 仍接收完整 canonical Context，因此报告数字、图表和来源不会因 NarrativeContext 缩减而丢失。

### 7.2 Guardrail 固定错误码

Report Draft 校验必须输出固定 code，不保存被拒绝的原始文本：

```text
report_draft_not_json
report_draft_schema_invalid
report_draft_extra_fields
report_draft_forbidden_number
report_draft_forbidden_rating
report_draft_forbidden_advice
report_guardrail_retries_exhausted
report_provider_error
report_renderer_error
report_final_validation_error
```

诊断允许保存：模型名、尝试次数、Context 字符数/字节数、固定错误码。禁止保存 API Key、原始模型输出、完整 Prompt 或未通过 Gate 的 Claim 文本。

### 7.3 Deterministic Safe Draft

当且仅当：

- Claim Gate 已通过；
- Verdict 已完成；
- Report Agent 因 Draft guardrail 耗尽而失败；

系统可以构造固定九字段 `ReportDraft`。Safe Draft 只能根据以下状态选择预定义叙述：

- 各 Claim 类别是否存在；
- Verdict 的确定性标签；
- 风险 Evidence 是否完整；
- 投资期限是否存在。

Safe Draft 不包含任何数字、Evidence ID、Claim ID、评级代码或买卖建议。完整数字和来源仍由 Renderer 从 canonical Context 插入。

Provider 连接错误默认不自动伪装成 Draft guardrail 失败；应返回 `report_provider_error`。是否重试由现有模型调用策略负责，不在 Safe Draft 中吞掉网络异常。

## 8. 输出路径不变量

生产实现保持 `CompactRunReporter` 为唯一公共产物写入者。

新增离线测试：

1. 记录项目根目录 `investment-report.md`、`run-output.md`、`run-result.json` 的存在性、SHA-256 和 mtime。
2. 使用临时目录调用 `kickoff(output_path=...)`。
3. 断言临时目录三产物一致。
4. 断言项目根目录三文件指纹完全不变。

如果该测试在当前实现直接通过，只提交测试，不修改生产写入代码。

## 9. 数据流与阻断语义

### 9.1 风险线路

| 场景 | 行为 |
|---|---|
| 有完整 Item 1A，Risk Agent 正常输出 | Claims 进入 Claim Gate |
| 有 eligible Evidence，Risk Agent 两次为空 | 使用 Deterministic Risk Disclosure Builder，再进入 Claim Gate |
| 只有附件外壳或截断 filing | Analysis Gate 阻断：`risk_evidence_missing` |
| Builder 产生非法或越权 Claim | Claim Gate 阻断，不进入 Verdict/Report |

### 9.2 报告线路

| 场景 | 行为 |
|---|---|
| Report Agent Draft 合法 | Renderer 正常生成报告 |
| Draft guardrail 重试耗尽 | 使用 Deterministic Safe Draft，继续 Renderer 和 Final Validator |
| Provider/transport 失败 | `report_provider_error`，不冒充格式失败 |
| Renderer 失败 | `report_renderer_error` |
| Final Validator 失败 | `report_final_validation_error` |

## 10. 测试策略

### 10.1 离线 RED 测试

1. TSLA-shaped 8-K Item 2.02/9.01 shell 不得成为 eligible risk Evidence。
2. 完整 10-K/10-Q Item 1A 必须成为 eligible Evidence。
3. eligible Evidence + Risk Agent 两次空输出必须生成受限确定性 Claim，并通过原 Claim Gate。
4. 不合格 Evidence、越权 ID 或非法 Builder Claim 必须保持阻断。
5. NVDA-shaped 17 Claims Context 投影后不超过 24 KiB。
6. Report Agent 连续返回非法 Draft 时，记录固定 guardrail code，并使用 Safe Draft 进入 Renderer。
7. Provider 错误不得被 Safe Draft 吞掉。
8. 自定义 output_path 不改变项目根目录三产物指纹。

### 10.2 回归测试

- 完整 `unittest` 离线套件。
- Claim Gate、Report Contract、Report Visuals、Final Report Validator 聚焦套件。
- `git diff --check`。
- 不允许新增实时网络默认测试。

### 10.3 真实三公司验收

按顺序运行，避免 SEC/Yahoo 限流：

1. AAPL，未来 3 年。
2. NVDA，未来 3 年。
3. TSLA，未来 3 年。

每次使用独立 `/private/tmp` 输出目录，并检查：

- `status=ok`
- `stage=report`
- Claim Gate READY
- Verdict ready
- `artifacts.report_status=complete`
- Markdown 等于 JSON `report`
- manifest SHA-256 和 bytes 正确
- 三家公司报告中的 ticker、价格来源和 SEC Evidence 不串写

如果外部 SEC、Yahoo 或 DeepSeek 明确不可用，应记录为外部验收未完成，不能把网络失败当成代码通过，也不能因此修改 Gate。

## 11. 多代理执行架构

实现采用顺序工作包，避免 `main.py`、`pipeline_support.py` 和测试文件冲突。

### 工作包 A：风险证据资格

- 实现者：Luna Max
- 所有权：`tools/edgar_tool.py`、风险提取聚焦测试
- 审查者：Terra High
- 输出：Item 1A / substantive 8-K eligibility 与 shell 排除

### 工作包 B：Risk Builder 与 Flow 接入

- 实现者：新的 Luna Max
- 所有权：`pipeline_support.py`、`main.py` 风险线路、对应测试
- 前置条件：A 的契约已冻结
- 审查者：Terra High

### 工作包 C：Report Narrative Context 与 Safe Draft

- 实现者：新的 Luna Max
- 所有权：Report Crew、`main.py` 报告线路、对应测试
- 前置条件：B 已合并并通过回归
- 审查者：Terra High

### 工作包 D：输出路径负向测试与三公司 fixtures

- 实现者：新的 Luna Max
- 所有权：测试和 fixtures；只有测试证明生产缺陷时才允许提交最小生产修复
- 审查者：Terra High

### Sol 责任

- 冻结接口和工作包顺序。
- 不替 Luna 编码。
- 检查每次 diff 是否越界。
- 运行完整离线测试和三公司真实验收。
- 任一工作包完成后先审查，不允许多个 Luna 同时修改共享核心文件。

## 12. 非目标

本轮不处理：

- 同业比较、正向 DCF、宏观敏感度。
- 风险概率预测或主观严重度评级。
- CrewAI、DeepSeek、edgartools 或 yfinance 依赖升级。
- SQLite 架构调整。
- 报告视觉样式重做。
- 降低 Claim Gate、Verdict 或 Final Report Validator 标准。

### 与既有下一阶段计划的关系

本规格优先于 `2026-08-09-investment-research-agent-next-stage.md` 中尚未执行的 Task 2，并吸收其中“完整 SEC 核心章节提取”的相关部分。完成本规格前，不应再独立执行旧 Task 2，避免两套风险章节契约重复修改 `edgar_tool.py` 和 `pipeline_support.py`。本规格不替代旧计划中的财务指标、估值指标、趋势或 Verdict v2 任务。

## 13. 完成定义

只有以下条件全部满足才可宣布完成：

- 四个工作包均有 Luna 实现报告和 Terra 审查结论。
- 所有 Critical/Important 审查问题已修复。
- 完整离线测试通过。
- AAPL、NVDA、TSLA 在外部服务可用时均生成正式报告。
- 没有 rejected Claim、未验证数字或附件外壳风险进入报告。
- 项目根目录无无关文件，用户既有运行产物不进入提交。
