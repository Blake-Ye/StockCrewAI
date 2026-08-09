# 统一估值口径与外行可读报告设计

## 目标

修复当前报告中 P/E、FCF Yield、反向 DCF 与历史 P/E 的期间口径不一致问题，并在不放松 Evidence、Calculation、Claim、Verdict Gate 的前提下，让外行读者能够看懂指标含义、数值变化和结论依据。

## 设计决策

1. 当前 P/E 只允许使用已验证的 TTM 稀释 EPS。
2. 当前 FCF Yield 与反向 DCF 只允许使用已验证的 TTM 自由现金流。
3. 历史 P/E 的每个月都必须使用该月价格日期前已经公开的 TTM 稀释 EPS，禁止使用单季度或 YTD EPS冒充 TTM。
4. TTM 稀释 EPS 使用固定公式：`latest_fy_diluted_eps + current_ytd_diluted_eps - prior_ytd_diluted_eps`。所有输入保留 Evidence ID、报告期和 filed_at；任一输入缺失则结果 unavailable，不做年化或猜测。
5. Report Writer Agent 继续只生成无数字叙述；所有数字、单位、结论、DCF 假设和图表由确定性 Python Renderer 注入。
6. 不增加 Crew、LLM Agent、依赖或外部数据源；继续使用 SEC、Yahoo、Decimal、Matplotlib 和现有 CrewAI Flow。

## 统一数据契约

### TTM 稀释 EPS

```json
{
  "metric_id": "diluted_eps",
  "calculation_id": "calc_diluted_eps_ttm",
  "formula_id": "ttm_diluted_eps",
  "raw_inputs": {
    "latest_fy": "...",
    "current_ytd": "...",
    "prior_ytd": "..."
  },
  "raw_result": "...",
  "unit": "USD/share",
  "period_basis": "TTM",
  "status": "available",
  "validation_status": "valid",
  "input_evidence_ids": ["..."]
}
```

### 历史 TTM EPS 快照

```json
{
  "as_of": "YYYY-MM-DD",
  "filed_at": "YYYY-MM-DD",
  "period_end": "YYYY-MM-DD",
  "ttm_eps": "...",
  "period_basis": "TTM",
  "evidence_ids": ["latest_fy", "current_ytd", "prior_ytd"]
}
```

历史估值工具只接受 `period_basis=TTM` 且 `filed_at <= price_date` 的快照。

## 报告展示规则

- 当前 P/E、历史 P/E、FCF Yield 与反向 DCF 必须在标题或指标旁显示 `TTM`。
- 流动比率显示为 `x`，债务权益比显示为 `x`，股份稀释率更名为“股份变化率”，负值解释为股份减少。
- 反向 DCF 固定展示基础 FCF、预测年限、折现率、永续增长率、隐含增长率和三情景矩阵。
- 图表全部使用中文标题、中文坐标轴、单位和数据标签。
- 每张图后附一条确定性“读图说明”，只解释相对位置和变化，不新增预测或投资建议。
- 正式报告不得出现互相矛盾的当前 P/E 与历史当前 P/E；同一价格日期的两者差异超过格式化舍入误差时，Final Report Gate 阻断。

## 错误处理

- TTM EPS、TTM FCF 或历史 TTM EPS 序列缺失时，保持 fail-closed，返回明确 `required_data`。
- 禁止回退到单季度、九个月累计、年化估算或未验证字段。
- 错误码必须区分：`ttm_eps_required`、`ttm_fcf_required`、`historical_ttm_eps_required`、`valuation_basis_mismatch`。

## 验收标准

1. 当前 P/E 使用 TTM EPS；FCF Yield 和反向 DCF 使用同一个 TTM FCF Calculation ID。
2. 60 个月历史 P/E 每一点均能追溯到当时已公开的 TTM EPS Evidence。
3. 报告中当前 P/E 与历史最后一个 P/E 口径一致。
4. DCF 假设完整显示，图表中文化并带读图说明。
5. 默认测试不访问 SEC、Yahoo 或 LLM；全量测试、compileall、diff-check 通过。

