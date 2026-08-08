# TTM Builder 设计说明

## 目标

在现有确定性 SEC 证据链中增加可审计的过去十二个月（TTM）构建能力，避免把九个月累计数据误当成全年或 TTM 数据。LLM 不参与期间选择、算术、验证或可用性判断。

## 本轮边界

- 继续采用当前 `uv` 项目和 CrewAI 1.15.11，不升级依赖。
- 保持 3 个 Crew、4 个 LLM Agent：RequestParser、FinancialQuality、RiskAnalysis、ReportWriter。
- 估值 Claims 继续由 Python 确定性生成，不恢复 ValuationAnalysisAgent。
- 本轮生成并展示 TTM 结果，但不替换当前估值、历史估值、反向 DCF 或 Verdict 的输入；切换估值口径属于下一阶段。
- 默认测试必须离线，不调用 DeepSeek、SEC 或 Yahoo。

## 数据来源与公式

每个可加总的流量指标需要三份 SEC Company Facts Evidence：

1. 最近完整财年 `latest_fy`
2. 当前财年累计期 `current_ytd`
3. 上一财年同长度累计期 `prior_ytd`

固定公式：

```text
TTM = latest_fy + current_ytd - prior_ytd
```

V1 支持 `revenue`、`operating_income`、`net_income`、`operating_cash_flow`、`capex`。`free_cash_flow` 由 TTM operating cash flow 减去 TTM capex 派生。股票数量、资产负债表时点值和 EPS 不允许直接套用加总公式。

## 固定接口

`EdgarResult` 新增：

```python
ttm_inputs: dict[str, dict[str, EdgarFact]]
```

内层键只能是 `latest_fy`、`current_ytd`、`prior_ytd`。EDGAR 适配器负责按 fiscal year/period 提取候选 Evidence；它不执行 TTM 算术。

新增 `TTMBuilderTool.run(company_name, ticker, metric_inputs)`，返回带以下字段的 Pydantic 结果：

- 顶层：`status`、`company_name`、`ticker`、`metrics`、`warnings`
- 单指标：`metric_id`、`calculation_id`、`formula_id`、`formula_version`、`input_evidence_ids`、`raw_inputs`、`raw_result`、`unit`、`period_start`、`period_end`、`status`、`validation_status`、`reasons`

只有三个 Evidence 均为 `valid`、字段完整、单位一致、期间关系可审计时，指标才可标记 `available/valid`。否则返回 `unavailable/unvalidated` 和稳定原因码，禁止补零或猜测。

Flow state 新增 `ttm` 字段。`prepare_evidence` 在基础 Evidence 验证后调用 TTM Builder，并把 TTM 可用指标数显示在第 2 阶段命令行框中。

## 验收标准

- 正常三段输入计算出确定性 Decimal 字符串，并保留全部 Evidence ID。
- 缺失可比期、单位不一致、无效 Evidence、期间不匹配均返回结构化 unavailable。
- Flow 离线测试能观察 `state.ttm` 和阶段摘要，不改变现有 Gate 结果。
- 全量单元测试、compileall、`git diff --check` 和一次真实 `crewai run` 均执行；真实外部网络失败应与代码回归分开报告。
