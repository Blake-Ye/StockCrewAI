# WP13-S04 显式 live coverage matrix

这是一次显式 live coverage smoke，不是离线测试。本次固定运行 30 个 ticker，
每个 ticker 只直接调用一次 `run_live_smoke(ticker)`，不重试（`retries=0`）。
网络、SEC、Yahoo、DeepSeek、限流和运行环境状态会影响结果；结果不能证明覆盖率
或投资收益，只用于定位每家公司最终 `stage`、`profile`、`coverage` 和 `reason_code`。

- `live_requested=true`
- `ticker_count=30`；实际 live 请求数：`30`
- `attempts_per_ticker=1`；`retries=0`；无 fallback、并行或旧结果补行
- `run_live_smoke` implementation: `src/stockcrewai/evals/live_smoke.py@5abe69b` （source sha256 前 12 位：`edf631118d3e`）
- 生成时间：`2026-08-11T12:56:03+08:00`

字段缺失统一记录为 `unavailable`。`error.category`/`error.reason_code` 保留 `run_live_smoke` 的 typed error；成功行的 `reason_code` 保留实际 profile reason codes（多个值用逗号连接）。表格只保留结构化字段。

## Matrix

| ticker | status | stage | issuer_profile | profile.coverage_level | error.category | error.reason_code | reason_code |
|---|---|---|---|---|---|---|---|
| AAPL | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| MSFT | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| NVDA | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| AMZN | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| GOOGL | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| META | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| TSLA | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| JPM | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| BAC | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| GS | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| V | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| MA | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| UNH | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| JNJ | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| PFE | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| XOM | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| CVX | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| CAT | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| GE | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| NEE | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| DUK | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| PLD | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| AMT | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| O | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| BABA | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| TSM | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| LLY | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| COIN | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| RIVN | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |
| SPY | error | unavailable | unavailable | unavailable | external_dependency | permissionerror | permissionerror |

## 聚合小结

### 按 typed category

| category | count |
|---|---:|
| ok | 0 |
| external_dependency | 30 |
| gate | 0 |
| runtime | 0 |
| input | 0 |
| 合计 | 30 |

### 按 category/reason_code

| category | reason_code | count |
|---|---|---:|
| external_dependency | permissionerror | 30 |
| 合计 | — | 30 |

本次所有 live 请求均失败；外部依赖路径对本次矩阵不可用。完整 30 行仍予保留，
具体失败只按实际 typed `category/reason_code` 记录。

## 限制

- 这是单次 live smoke 快照，不是离线测试，也不构成覆盖率或收益结论。
- 本矩阵不把网络失败、SEC/Yahoo/LLM 失败、Gate 阻断或 runtime 异常计为代码通过。
- 结果只反映本次请求实际返回的 typed 状态；没有猜测缺失的 stage、profile、coverage 或 reason_code。

## P1 代表性 live smoke

这是 2026-08-11 执行的代表性真实链路 smoke，独立于上面的 30 ticker 历史矩阵。每个 ticker 仅执行一次显式的 `python -m stockcrewai.evals.live_smoke --ticker ...` 调用；首次观察到的 `unable to open database file` 是运行目录配置问题，不是最终业务结果，也不记录为 SEC/Yahoo 限流。显式将 `CREWAI_STORAGE_DIR` 设置为临时目录后，才开始真实请求。

字段缺失统一记录为 `unavailable`；不根据结果推断 `profile` 或 `coverage`。

| ticker | status | stage | profile | coverage | error.category | error.reason_code |
|---|---|---|---|---|---|---|
| AAPL | ok | report | unavailable | unavailable | unavailable | unavailable |
| O | error | unavailable | unavailable | unavailable | gate | reverse_dcf_required |
| JPM | error | unavailable | unavailable | unavailable | gate | net_interest_margin_missing_required_evidence |

O 和 JPM 均没有 report。AAPL 证明普通企业至少有一条真实闭环；O/JPM 的阻断是 profile/Gate 的真实缺口。本节不是宣称所有美国股票都已覆盖。

可复现命令模板（不包含真实密钥；`<TICKER>` 替换为一个 ticker）：

```bash
CREWAI_STORAGE_DIR="$(mktemp -d)" \
  uv run --env-file .env python -m stockcrewai.evals.live_smoke --ticker <TICKER>
```
