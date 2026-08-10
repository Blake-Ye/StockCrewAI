# WP00 测试策略

## 默认门禁

默认测试必须离线、可重复，不调用 SEC、Yahoo、DeepSeek 或任何付费 API，也不依赖 VPN。当前基线包括 274 个 unittest。WP00 新增基线测试覆盖 TSLA 文本误判、reverse DCF 不适用、Profile 传输和 typed 外部错误。

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q -n 3
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest discover -s tests -p 'test_*.py'
```

## 性质测试

`tests/conftest.py` 注册 Hypothesis `ci` profile：`max_examples=200`、`derandomize=True`、`deadline=None`、无数据库。Decimal 公式测试覆盖零、负数、极值、顺序和缺失，不允许用 NaN/Infinity 掩盖契约错误。

## live 边界

真实外部 smoke 测试使用 `@pytest.mark.live`，默认跳过；只有显式传入 `--run-live` 才能运行。live runner 只接受明确 ticker，成功返回真实结果，失败返回非零退出码及 typed error；live 失败不能改写离线基线，也不能被解释为代码通过。

## 并行和临时文件

可并行测试只写 `tmp_path` 或专用 `/private/tmp` 测试目录；不得写 `run-output.md`、正式报告或共享根目录 artifact。项目固定使用 3 个 xdist worker，并比较串行/并行的通过数和失败集合。提交前还要运行 `compileall`、授权路径 Ruff 和 `git diff --check`。

## WP08 回测测试门禁与 future-signal RED 规则

### 默认门禁

- WP08 默认测试只读取 `tests/fixtures/quant/backtest/` 的离线数据；必须阻断 SEC、Yahoo、DeepSeek、付费 API、VPN 和任何隐式网络请求。默认测试不得执行参数搜索、自动调参或 Agent 数值改写。
- 目标门禁为：

  ```bash
  UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_quant_portfolio.py tests/test_quant_statistics.py tests/test_quant_backtest.py
  UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q -n 3 tests/test_quant_portfolio.py tests/test_quant_statistics.py tests/test_quant_backtest.py
  ```

  串行和并行必须有相同的通过数、失败集合和稳定 artifact hash；再运行完整离线 `pytest -q`、`compileall`、授权路径 Ruff、mypy 和 `git diff --check`。测试 artifact 只能写 `tmp_path`/专用临时目录。
- 必测断言包括：Universe 恰为 50–100 只普通股且 manifest 含 `membership_as_of` 语义、`membership_source` 和 `survivorship_bias_known`；trade_date 严格晚于 signal_as_of；ceil(20%)、ASCII 同分排序、权重和、初始 CASH、换手/双边成本公式、0/5/10/20 bps 敏感性、两条日期对齐基准、缺失收益 typed unavailable、coverage/missing IDs、统计 typed reason code、Decimal→float64 版本/容差和 period 字段完整性。

### future-signal fixture 必须先 RED

fixture 固定包含同一 Universe 在 `signal_as_of=T0` 的 score/snapshot、`T0` 之后把排名反转的 future snapshot，以及 `trade_date=T1`、`next_trade_date=T2` 的价格。测试先写断言再实现；在实现缺失或错误读取未来数据时必须 RED，不能把“fixture 能加载”当作 RED 证据。

1. 正确结果只能使用 `snapshot.as_of <= T0`：断言每个 `snapshot_ids` 对应 snapshot 都不晚于 T0，且 `selected_tickers`/weights 等于 T0 的固定 expected Decimal 值。若实现读取 future snapshot，排名反转、snapshot ID 或权重必然不同，测试必须失败。
2. 断言 `T1 > T0` 且 `T2 > T1`，收益只能由 `T1 → T2` 的端点计算。fixture 另放一个会改变 `T0 → T1` 结果的价格；使用 signal 日、同日或 T0→T1 的错误实现必须 RED，不能通过调整 expected 值绕过。
3. 断言 future score 只能影响未来 period 的 signal，不能影响 T0 period 的 selected tickers、target weights、turnover、cost 或 period hash；输入顺序打乱也必须保持同一结果。
4. 在 fixture 删除任一策略持仓、SPY 或固定 Universe 成分的 T2 return 后，断言该 period 的 gross/net/对应 benchmark 为 typed `unavailable`，`coverage` 与 `missing_return_ids` 明确记录，且统计分母只包含 complete periods；禁止 0、前值和未来值填补。

### RED→GREEN 之外的回归门禁

- `selected_count` 必须是 `ceil(0.20 * eligible_count)` 且 eligible 为 0 时 typed unavailable；同分按 ASCII 排序，重复运行和输入重排 hash 不变。
- 第一 period 必须从 CASH=1 计算换手；`turnover = 0.5 * sum(abs(target-previous))`、`cost_return = turnover*bps/10000`、`net = gross-cost` 必须与独立 Decimal 手算一致，基准成本恒为 0。
- 统计 golden fixture 必须独立复核 CAGR、`ddof=1` 年化波动、零 risk-free Sharpe、回撤、excess CAGR、Spearman IC、Q1–Q5 和平均/年化 turnover；空样本、零波动、少于规定历史或常数 rank 输入必须返回 typed reason code 且无 NaN/Infinity。
