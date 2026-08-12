# 股票拆分可比性数据契约设计

## 目标

让 SEC/EDGAR 输出股票拆分等公司行动证据，让确定性计算器在计算跨期股份变化前判断是否存在拆分，并让验证器复算调整后的结果，避免把拆股误报为股份稀释。

## 边界

- 只处理 SEC filing 文本中明确披露的 forward stock split 和 reverse stock split。
- 不让 LLM 猜拆股比例，不以 Yahoo 作为主来源，不新增依赖。
- 保留 EDGAR 原始股数；只在派生计算中应用拆股调整。
- 本次不重构报告模板、不修改其他财务指标、不清理现有未提交改动。

## 数据流

```text
EdgarTool
  -> corporate_action_scan_status + corporate_actions
  -> Flow 将公司行动传给 Calculator/Validation
  -> Calculator 调整 prior shares 并计算可比变化
  -> Validation 验证公司行动证据、调整输入和公式结果
```

## 数据契约

`EdgarResult` 增加：

- `corporate_action_scan_status`: `checked`、`unavailable` 或 `not_requested`。
- `corporate_actions`: 结构化股票拆分记录列表。

每条公司行动包含 action ID、方向、旧股数、新股数、调整因子、生效日期、SEC filing Evidence ID、来源 URL 和验证状态。

## 判定规则

- `checked` 且无适用拆分：调整因子为 `1`，可按原口径计算。
- `checked` 且存在已验证拆分：历史股数乘以适用调整因子后计算。
- 未请求或无法读取 filing 文本：股份变化计算为 `unavailable`。
- 拆分比例、日期、来源或证据冲突：股份变化计算为 `unavailable`，不能猜测。

## Netflix 验收样例

```text
2025-Q2 raw shares = 424,926,346
10-for-1 factor = 10
prior comparable shares = 4,249,263,460
2026-Q2 shares = 4,163,939,676
share change = -2.01%
```

## 验证要求

验证器必须确认公司行动证据有效，并重新计算：

```text
shares_prior_comparable = shares_prior * adjustment_factor
result = (shares_current - shares_prior_comparable) / shares_prior_comparable
```

任何篡改调整因子、可比股数或结果的 Calculation 都必须失效。
