# WP00 数值与期间约定

## 1. 精度和缺失

- 财务金额、股数、价格、EPS、比率和所有审计计算的权威内部表示为 `decimal.Decimal`。
- 不能用 `float`、NaN、Infinity 或 0 表示缺失、不可适用或验证失败。
- `Decimal` 只在图表或量化库边界显式转换为 `float64`；转换前保存原始 Decimal 和转换说明。
- 计算结果必须带单位、币种、期间、来源 Evidence ID、公式版本和状态。

## 2. 单位和公式

金额以来源声明的单位保存；不得在没有单位证据时自动乘除一千或一百万。常见公式为：

- `free_cash_flow = operating_cash_flow - capex`；CapEx 必须先按契约转为正的投入金额。
- `market_cap = price × shares`；价格和股数必须有时间戳与类别证据。
- `P/E = price / diluted_eps`；EPS 非正或期间不匹配时为 `unavailable`。
- `FCF_yield = free_cash_flow / market_cap`；报告必须显示期间，九个月数据不冒充全年或 TTM。

除非某个工具的契约明确指定，计算不自动年化、不回填、不混合币种。负自由现金流可以是合法结果，但不能强行解释为正值。

## 3. 时间和舍入

所有 point-in-time 数值要同时满足 `filed_at <= as_of` 和观察日不晚于市场价格时间戳。期间必须保留 `start`、`end`、`fiscal_year`、`fiscal_period` 和是否审计的信息。内部计算尽量不舍入；显示层按指标类型统一舍入，显示舍入不得回写原始值。

## 4. 失败语义

除法分母为零、单位未知、币种冲突、期间冲突、价格缺少时间戳或 Evidence 未验证时返回 typed `unavailable`/`not_ready`，并附稳定 `reason_code`。不能通过填 0 或最近值让 Gate 假装通过。

## 5. WP07 因子公式契约（factor-formulas-v1）

本节是 WP07 因子引擎的唯一公式来源。实现必须使用表中固定的 `factor_id`、输入键、方向和失败语义；不得用自然语言判断“数据看起来合理”，不得自动补值、年化、回填或跨 Profile 混算。输入来自同一个 `PointInTimeSnapshot`：`financial_features` 和 `market_features` 的数值必须是有限 `Decimal`，`data_quality["industry"]` 必须提供非空行业标识。

### 5.1 输入键与期间

| 输入域 | 固定键 | 期间/时间要求 |
|---|---|---|
| 财务 | `price`, `diluted_eps`, `free_cash_flow`, `book_value_per_share`, `enterprise_value`, `ebitda` | 与 snapshot `price_cutoff` 一致；价格为该时点原始价格 |
| 财务 | `net_income`, `average_equity`, `nopat`, `invested_capital`, `operating_income`, `revenue`, `cash_from_operations`, `total_debt`, `total_equity` | 同一财务期间；期间端点必须已在上游 Evidence/Calculation 中固定 |
| 财务历史 | `revenue_3y_ago`, `eps_3y_ago`, `fcf_3y_ago` | 与当前值相隔完整 3 年；不能以 3 个季度或 3 个可用点替代 |
| 市场 | `return_12m`, `return_1m`, `volatility_12m`, `beta_12m`, `max_drawdown_12m` | 只使用 `price_cutoff` 之前的价格；不把未来价格带入 snapshot |

因子引擎只读取上述键。缺少键、键值为 `None`、期间/币种未验证、分母为零或违反正值要求时，按 5.3 返回 typed 状态；不寻找同名别名。

### 5.2 因子、公式、Profile 适用性与方向

`方向=high` 表示原始值越高，标准化后的“更优”分数越高；`方向=low` 表示原始值越低，标准化后的“更优”分数越高。方向由实现中的固定 registry 提供，不能由 Agent 或输入数据改变。

| factor_id | 固定公式 | 适用 `IssuerProfile` | 方向 |
|---|---|---|---|
| `value.earnings_yield` | `diluted_eps / price` | `standard_operating`, `utility`, `commodity_producer`, `holding_company` | high |
| `value.fcf_yield` | `free_cash_flow / (price × shares)`；其中 `shares` 必须存在于 `market_features` | `standard_operating`, `utility`, `commodity_producer`, `holding_company` | high |
| `value.price_to_book` | `price / book_value_per_share` | `standard_operating`, `bank`, `insurance`, `reit`, `utility`, `commodity_producer`, `holding_company` | low |
| `value.ev_to_ebitda` | `enterprise_value / ebitda` | `standard_operating`, `utility`, `commodity_producer`, `holding_company` | low |
| `quality.roe` | `net_income / average_equity` | `standard_operating`, `bank`, `insurance`, `reit`, `utility`, `commodity_producer`, `holding_company` | high |
| `quality.roic` | `nopat / invested_capital` | `standard_operating`, `utility`, `commodity_producer`, `holding_company` | high |
| `quality.operating_margin` | `operating_income / revenue` | `standard_operating`, `utility`, `commodity_producer`, `holding_company` | high |
| `quality.fcf_margin` | `free_cash_flow / revenue` | `standard_operating`, `utility`, `commodity_producer`, `holding_company` | high |
| `quality.cash_conversion` | `cash_from_operations / net_income` | `standard_operating`, `utility`, `commodity_producer`, `holding_company` | high |
| `quality.debt_to_equity` | `total_debt / total_equity` | `standard_operating`, `reit`, `utility`, `commodity_producer`, `holding_company` | low |
| `growth.revenue_cagr_3y` | `(revenue / revenue_3y_ago)^(1/3) - 1` | `standard_operating`, `utility`, `commodity_producer`, `holding_company` | high |
| `growth.eps_growth_3y` | `(diluted_eps / eps_3y_ago)^(1/3) - 1` | `standard_operating`, `utility`, `commodity_producer`, `holding_company` | high |
| `growth.fcf_growth_3y` | `(free_cash_flow / fcf_3y_ago)^(1/3) - 1` | `standard_operating`, `utility`, `commodity_producer`, `holding_company` | high |
| `market.momentum_12_1` | `return_12m - return_1m` | `standard_operating`, `bank`, `insurance`, `reit`, `utility`, `commodity_producer`, `holding_company` | high |
| `risk.volatility_12m` | `volatility_12m` | `standard_operating`, `bank`, `insurance`, `reit`, `utility`, `commodity_producer`, `holding_company` | low |
| `risk.beta_12m` | `beta_12m` | `standard_operating`, `bank`, `insurance`, `reit`, `utility`, `commodity_producer`, `holding_company` | low |
| `risk.max_drawdown_12m` | `max_drawdown_12m` | `standard_operating`, `bank`, `insurance`, `reit`, `utility`, `commodity_producer`, `holding_company` | low |

`value.fcf_yield` 的 `shares` 固定读取 `market_features["shares"]`，其单位必须与 `free_cash_flow` 和 `price` 的单位契约一致；缺失时不得退回 `market_cap` 或其他别名。`price_to_book`、`earnings_yield` 等比率不做百分数乘 100，内部结果保持小数形式。

非正输入规则固定如下：价格、shares、book value per share、EBITDA、invested capital、average equity、total equity、revenue 以及三年前对应的 CAGR 基数必须为正；EPS、FCF、净利润和 NOPAT 可以为负，但 CAGR 的两个基数必须同号且非零。违反规则返回 `unavailable` 与 `non_positive_input` 或 `growth_base_sign_mismatch`，不得把经济含义不一致的增长率算出来。

### 5.3 FactorObservation 状态与 provenance

每个 snapshot 必须按固定 registry 生成全部 factor observation。Profile 明确不适用时返回 `status="not_applicable"`、`reason_code="profile_not_applicable"`；适用但缺键/验证不足/分母非法时返回 `status="unavailable"` 和对应稳定 reason code；只有公式成功且输入均通过时间、单位、来源检查时返回 `status="available"`。`invalid` 只用于输入模型或公式结果违反有限 Decimal 约束的情况，不作为缺失的别名。

可用 observation 的 `evidence_ids` 和 `calculation_ids` 必须来自 snapshot 的 `available_evidence_ids` 与 `available_calculation_ids`，不得生成新 ID；不可用或不适用 observation 的两个列表为空。由于 v1 的 snapshot 模型按 snapshot 保存 allowlist 而不是逐字段保存来源，v1 使用该 snapshot allowlist 作为 provenance 边界，并在 `reason_code` 中明确缺失原因。

### 5.4 行业内标准化与排名输入

标准化只在相同 `(factor_id, as_of, peer_group)` 内进行；`peer_group` 固定为 `"{issuer_profile.value}:{industry}"`。行业缺失时不得把不同公司合并为可比组，observation 返回 `insufficient_peer_sample`。

- 默认 `normalization_version="winsor-percentile-v1"`；winsor 边界由调用者传入，必须满足 `0 <= lower < upper <= 1`。
- 先用固定线性分位数求下、上边界，再截断原始值；不得改变 raw value。
- 使用同组可用 observation 的平均秩百分位；并列值使用 mid-rank，最小值为 `0`、最大值为 `1`，只有一个样本时标准化状态为 `insufficient_peer_sample`。
- `direction=low` 使用 `1 - percentile`，最终 `normalized_value` 始终表示“越高越优”；不可用、不适用和小样本 observation 保持 typed 状态，不写 NaN/Infinity 或伪造 0。
- `peer_count` 是同组、同 factor 的可用样本数；标准化前后都必须保留该数值。

### 5.5 数值边界与复核

线性比率和加减法全程使用 `Decimal`。CAGR 使用 Decimal 的 `ln`/`exp` 在固定局部精度 28 下计算：`exp(ln(current / base) / 3) - 1`；不得把财务原值先转 float。winsor 分位数和 mid-rank 可在 NumPy `float64` 边界执行，转换前后必须保留 raw Decimal，转换回 Decimal 时使用字符串表示并接受绝对误差 `1e-12`。测试必须使用独立手算 Decimal fixture，比较容差为 `1e-12`；不通过时失败而不是放宽容差。

### 5.6 Composite score 与排名（composite-ranking-v1）

S05 只接受已经完成行业内标准化的 `FactorObservation`。在相同 `(as_of, peer_group)` 内按 ticker 汇总；每个 ticker 的 composite score 是其 `status="available"` 且 `normalized_value` 非空因子的等权算术平均。不可用或不适用因子不进入分子或分母，也不能把缺失当作 0；如果一个 ticker 没有任何可用因子，返回 `status="unavailable"`、`reason_code="no_available_factors"`，不参加排名。

排名输出必须带 `composite_version="composite-ranking-v1"`、ticker、as_of、peer_group、score、available_factor_count、factor_ids、status、reason_code。每个 `(as_of, peer_group)` 内按 `score` 降序，score 完全相同时按 ticker 的 ASCII 升序排序并分配唯一的 ordinal rank（不使用不稳定的并列顺序）。输入 observation 顺序不得影响 score、rank、输出 JSON 或 hash；不得跨行业、Profile 或 as_of 排名。所有 Decimal 平均值保持 Decimal，显示或序列化前不提前舍入。

## 6. WP08 回测协议（walk-forward-backtest-v1）

本节是 WP08 回测的唯一数值和时间协议。实现不得把下列常量、排序、缺失处理或统计定义暴露为可调参数；Agent、Report 或外部数据都不能修改它们。

### 6.1 固定 Universe 与时间轴

- 每次回测使用一个不可变的 `UniverseManifest`，成员数必须为 **50–100（含边界）**；每个成员必须是美国普通股（`security_profile=common_stock`），不得包含 ETF、基金、ADR 或其他证券结构。回测期间不重建、不按未来市值换入换出成员。
- 协议字段为 `membership_as_of`、`membership_source`、`known_biases`。当前共享 Pydantic 模型的 `selection_as_of` 是 `membership_as_of` 的既有字段名；WP08 只能使用这一日期，不得再引入第二个成员日期。
- `membership_source` 必须是非空、可追溯的 manifest/数据源引用；`known_biases` 必须是去重后的非空字符串列表，并且必须包含精确值 `survivorship_bias_known`。不得声称该固定股票池消除了幸存者偏差。
- 回测月度锚点固定为每个自然月最后一个 XNYS session；至少覆盖 60 个自然月。对每个锚点，在所有可用 snapshot 日期中取不晚于该锚点的最后一个**共享** `as_of`，记为 `signal_as_of`。不得为不同 ticker 选择不同的更晚 snapshot；没有可用日期时该 period 为 `unavailable/no_signal_snapshot`。
- `trade_date` 必须是严格晚于 `signal_as_of` 的下一 XNYS session，禁止 `trade_date == signal_as_of`、同日成交或使用 `previous` session。每个有收益的 period 还必须保存严格晚于 `trade_date` 的下一个月度 `trade_date`，记为 `next_trade_date`；末尾没有下一日期的 period 只保存终止状态，不进入收益统计。
- 选股只能读取 `signal_as_of` 及之前的 `PointInTimeSnapshot`、`FactorObservation` 和 `CompositeScore`；每个使用的 snapshot 的 `as_of`、`filing_cutoff`、`price_cutoff` 都不得晚于 `signal_as_of`。`trade_date` 之后的价格和收益只能作为结果，不得进入 score、rank、selected tickers 或权重。
- 个股和 SPY 均使用同一日期的已验证 `total_return_adjusted` level；period 收益固定为 `level(next_trade_date) / level(trade_date) - 1`，不计算 signal 日到 trade 日的收益，不使用同日未来收益。

### 6.2 排名、Top 20% 与目标权重

- `score_version` 固定为 `composite-ranking-v1`。`eligible_count` 只统计在同一个 `signal_as_of` 有有限 `score` 且 `status="available"` 的成员。
- 先按 composite score 降序，再按 ticker 的 ASCII 升序稳定排序；完全同分也必须使用该 ticker 次序，禁止随机、哈希顺序或输入顺序。当 `eligible_count>=1` 时，`selected_count = ceil(Decimal("0.20") * eligible_count)` 且至少为 1；当 `eligible_count=0` 时不伪造选股，period 为 `unavailable/no_eligible_scores`。
- 正常 period 的 `target_weights` 必须包含所有 Universe ticker 和保留键 `CASH`。在固定 Decimal precision=28 下先令 `base_weight = Decimal("1") / selected_count`；按 score/ASCII 次序保存 selected tickers，除最后一只外均为 `base_weight`，最后一只为 `Decimal("1") - base_weight * (selected_count - 1)`，以吸收有限 Decimal 表示残差。未入选 ticker 和 `CASH` 均为 `Decimal("0")`，权重和必须精确等于 `Decimal("1")`；该残差规则是等权的唯一序列化规则。
- 第一 period 的 `previous_weights` 固定为所有 ticker 为 `Decimal("0")`、`CASH=Decimal("1")`，现金不产生收益。之后的 `previous_weights` 是前一 period 的 target weights，始终包含 CASH；不得省略现金或把缺失收益补成现金收益。没有 target weights 的 unavailable period 不更新上一份有效 target weights。

### 6.3 换手与交易成本

对每个有 target weights 的 period 固定使用：

```text
turnover = Decimal("0.5") * sum(abs(target_weight - previous_weight))
gross_return = sum(target_weight[ticker] * return[ticker] for ticker in selected_tickers)
cost_return = turnover * round_trip_cost_bps / Decimal("10000")
net_return = gross_return - cost_return
```

`round_trip_cost_bps` 是买卖双边合计成本，不是单边成本；基准值固定为 `10` bps。成本敏感性只运行 `[0, 5, 10, 20]` bps，四次运行复用完全相同的 signal、selected tickers、weights 和 gross returns，不做参数搜索。策略 cost 只从策略 gross return 扣除，SPY 和 Universe 等权基准不扣策略 cost。权重已知但下一期收益缺失时，turnover 和 cost_return 仍可保存；gross/net 按 6.4 返回 typed unavailable。

### 6.4 缺失收益与基准

- 不得用 0、前值、后值或任何未来值填补 missing return。任一策略持仓 ticker，或任一 benchmark 成分，在 `trade_date → next_trade_date` 缺少有效 total-return level/return 时，该 period 的 `gross_return`、`net_return` 和两条 `benchmark_return` 均必须为 `status="unavailable"`，并带 `reason_code="missing_next_period_return"`；不得写 NaN。
- 每个 period 保存 `coverage`（required count、available count、`ratio=Decimal(available_count)/required_count`）和按 ASCII 排序的 `missing_return_ids`。策略 coverage 的 required 成分是 target weight 非零的 ticker；Universe 等权 benchmark 的 required 成分是全部固定 ticker；SPY 的 required 成分是 `SPY`。缺少记录时 ID 固定为 `ticker@trade_date->next_trade_date`，已有源记录 ID 另存于 provenance，不得用空列表掩盖缺失。
- 回测 benchmark 固定为两条：`SPY_total_return` 和同一固定 Universe 的月度等权总收益。两者必须使用策略完全相同的 `trade_date`、`next_trade_date` 和 complete-period 过滤；不得各自选择日期或 nearest date。Universe 等权收益为全部 N 个成员下一期收益的等权平均，SPY 为 SPY total-return level 的端点比值；两者均不扣策略成本。
- `complete period` 要求策略持仓、SPY、Universe 等权三条序列的 required returns 全部 available。所有统计只使用 complete periods；任何被排除的 period 仍保留其 coverage 和 missing IDs，不能静默删除。`complete_period_count=0` 时所有依赖收益的统计均为 `unavailable/no_complete_periods`。

### 6.5 统计定义（`periods_per_year=12`）

统计主序列是 baseline `10` bps 的 `net_return`；同时保存 gross、SPY 和 Universe 等权序列。所有收益、权重、turnover、cost 和累计财富在统计库边界前均为有限 `Decimal`。

- 令 complete period 数为 `n`、月收益为 `r_i`。CAGR/annualized return 固定为 `(Π(1+r_i)) ** (12/n) - 1`；`n < 12` 返回 `insufficient_history`，累计增长因子非正返回 `invalid_return_factor`。`net_minus_benchmark_excess_cagr` 对每条 benchmark 固定为 `CAGR(net) - CAGR(benchmark)`，使用相同 complete periods。
- Annualized volatility 固定为 `sample_std(r_i, ddof=1) * sqrt(12)`；`n < 2` 返回 `insufficient_history`。样本标准差为零时 volatility 可保存有限值 `0` 并带 `zero_volatility`，Sharpe 必须为 `unavailable/zero_volatility`。
- Sharpe 版本固定为 `sharpe-zero-rf-period-v1`：每个 period 的 risk-free return 恒为 `Decimal("0")`，`Sharpe = mean(r_i - 0) / sample_std(r_i - 0, ddof=1) * sqrt(12)`；必须在结果中保存该版本。不得改用年化无风险率或外部利率。
- Max drawdown 使用 `wealth_0=1`、`wealth_t=wealth_(t-1)*(1+r_t)`，`drawdown_t=wealth_t/max(wealth_0..wealth_t)-1`，取最小值；无 complete period 时返回 `no_complete_periods`，不产生 NaN。
- IC 在每个 rebalance 用该 `signal_as_of` 的 score 与每个 eligible ticker 的下一期个股 total return 计算 Spearman；并列 rank 使用平均秩。少于 2 个有效 ticker 或 score/return rank 方差为零时，该 rebalance IC 为 `unavailable/insufficient_cross_section` 或 `unavailable/zero_rank_variance`，不得填 0。总 IC 是 complete rebalance 中可用 IC 的算术平均，不把不可用 period 纳入分母。
- 五分位组合固定按同一 score/ASCII 顺序切成连续五组，rank `k` 的组号为 `min(5, floor((k-1)*5/eligible_count)+1)`；Q1 为最高分组，Q5 为最低分组。每组收益是组内个股下一期 total return 的等权平均，不扣成本；空组或成员收益缺失返回 typed unavailable，不填 0。保存每期 Q1–Q5 收益及其 complete-period 平均收益/CAGR。
- 平均 turnover 固定为 complete periods 的算术平均；年化 turnover 固定为 `average_turnover * 12`。无 complete period 返回 `no_complete_periods`。所有零波动、空样本和历史不足都必须是显式 `status/reason_code/value=None`（或零波动定义允许的有限零值），绝不序列化 NaN/Infinity。

### 6.6 Decimal 边界、来源和 period artifact

- 只有进入统计库（NumPy/pandas 等）的序列才允许显式转换为 `float64`；转换协议固定为 `conversion_version="decimal-to-float64-v1"`、`tolerance=Decimal("1e-12")`。转换前保留 Decimal；统计结果立即恢复为有限 Decimal/typed outcome，误差超过 `1e-12` 必须失败而不是放宽容差。
- 默认运行只读本地 fixture/artifact，不联网；不做参数搜索、自动调参或结果导向的 Universe/成本选择。Agent 只能解释已验证结果，不能选择 source、修改 score、权重、收益、成本、统计值或 reason code。
- 每个 backtest period 必须保存：`signal_as_of`、`trade_date`、`next_trade_date`、`snapshot_ids`（ticker→snapshot ID）、`score_version`、`eligible_count`、`selected_tickers`、`previous_weights`、`target_weights`、`gross_return`、`net_return`、两条 `benchmark_return` 及其 status/reason_code、`turnover`、`round_trip_cost_bps`、`cost_return`、`coverage`、`missing_return_ids`。所有 ID、ticker、列表和映射使用稳定排序；不可用数值使用 `value=None`，不得用 0 代替。

## 7. WP10 REIT 专用数值口径（`reit-profile:v1`）

本节只覆盖 `issuer_profile=reit`，不改动 WP00 普通企业、WP07 因子或 WP08 回测的历史公式。REIT Profile 版本固定为 `reit-profile:v1`；REIT Metric Policy 版本固定为 `metric-policy:v2`。

### 7.1 FFO 与 FFO/share

- FFO 的审计身份只用于核对完整披露：`disclosed_ffo_total = gaap_net_income + Σ(signed_adjustment_i)`。`gaap_net_income`、每个实际调整项和 `disclosed_ffo_total` 都必须来自同一 NAREIT/公司 reconciliation，具有各自已验证 Evidence ID、来源 URL、期间、单位和币种；调整项按来源实际正负号进入求和。
- 缺少某个调整行时不回填 `0`；只有来源明确披露该项为零时才允许记录带 Evidence 的零值。不能从 US-GAAP tag、普通企业 FCF 或 Agent 文本补齐，也不能为不完整输入创建 reconciliation `CalculationRecord`。
- `ffo_per_share` 只能按 `ffo_total / diluted_weighted_average_shares` 计算。FFO 总额和稀释加权平均股数必须是同一 `period_start`、`period_end`、`fiscal_year`、`fiscal_period` 和明确 basis；金额与股数单位必须匹配，币种必须能得到明确的货币/股结果。期间或单位不匹配时返回 `unavailable`，不能使用公司单独披露的 FFO/share 替代计算值；该披露最多作为交叉核对 Evidence。
- FFO 总额和稀释股数均使用有限 `Decimal`。除非输入事实和计算记录完整且通过验证，不产生任何 `CalculationRecord`；不得用零、最近期间或年化股数让 FFO/share 看起来可用。

NAREIT 对 FFO 的行业定义和 SEC 对其 per-share 展示的适用说明见 [NAREIT FFO](https://www.reit.com/glossary/funds-operation-ffo)；FFO 仍是 GAAP 信息的补充，不替代 GAAP。

### 7.2 AFFO

AFFO 没有统一公式。只在公司明确命名、解释并披露 AFFO reconciliation 及其来源时使用公司披露值；必须保留公司实际调整项、期间、单位、币种、Evidence ID 和来源 URL。不能把 maintenance capex、straight-line rent、租赁成本或其他字段机械拼成通用 AFFO，也不能用普通 `free_cash_flow` 近似 AFFO。缺失或未披露时返回 `value=null`、`status=unavailable`、`reason_code=affo_reconciliation_not_disclosed`，不得把缺失变成零。规则依据 [NAREIT AFFO](https://www.reit.com/glossary/adjusted-funds-operations-affo)。

### 7.3 可选 REIT 指标

下列指标即使缺失也不阻断；但一旦为 `available`，必须有相应 Evidence/Calculation provenance，且保留来源定义和完整期间。

| 指标 | 固定口径 |
| --- | --- |
| `same_store_noi` | 只接受公司明确披露的 same-store NOI，保留物业池、same-store 定义、期间和来源；不能从收入/费用字段猜算。 |
| `occupancy` | 只接受公司明确披露的 occupancy，保留 physical/economic、期末/平均等定义；不同定义不可混比。 |
| `net_debt_to_ebitda` | `net_debt / EBITDA`。net debt、EBITDA 的观察时点、流量期间、LTM/季度/年度 basis 必须明确并可追溯；没有证据时禁止自动年化、把季度乘四或把其他期间拼接。 |
| `dividend_coverage` | `FFO attributable to common / common dividends`。分子和分母必须同期间、同币种并有来源；FFO 为负时保留负覆盖率，不能截断为零；共同股息为零时返回 `zero_common_dividends`。 |
| `price_to_ffo` | `market price / FFO per share`。价格须有已验证的行情 Evidence、时间戳和币种；FFO/share 必须是本节 7.1 的同期间计算结果。FFO/share `<= 0` 时返回 `unavailable/non_positive_ffo_per_share`，不得除零或人为改成正数。 |

`net_debt_to_ebitda` 的期间不明确或只有不具备年化依据的短期数据时，分别返回 `net_debt_to_ebitda_period_ambiguous` 或 `net_debt_to_ebitda_no_annualization_basis`；不输出无期间的比率。

### 7.4 REIT status、provenance 与 reason codes

每个 REIT metric 的 `PolicyDecision` 必须包含 `metric_id`、`status`、`evidence_ids`、`calculation_ids`、`reason_code` 和 `blocking`。`available` 的值必须为有限 Decimal，直接披露值至少有已验证 Evidence，派生值必须有全部输入 Evidence 和有效 Calculation；`unavailable` 和 `not_applicable` 的值必须为 `null`。未验证的 Evidence ID 不能作为有效 provenance 写出，只能由 PolicyDecision 以 reason code 表示。

REIT 采用以下稳定 reason code；缺失、无来源或未验证 Evidence 不得改写成数值：

| reason code | 适用情形 |
| --- | --- |
| `missing_required_field` | 必要字段缺失 |
| `missing_source_url` | 事实没有来源 URL |
| `unvalidated_evidence_id` | 引用的 Evidence ID 未通过验证 |
| `ffo_reconciliation_not_disclosed` | 没有完整 NAREIT/公司 FFO reconciliation |
| `ffo_adjustment_missing` | reconciliation 缺少实际调整行，不能默认为零 |
| `ffo_total_missing` | 没有披露的 FFO 总额 |
| `ffo_period_mismatch` / `ffo_unit_mismatch` / `ffo_currency_mismatch` | FFO reconciliation 行之间不一致 |
| `diluted_weighted_average_shares_missing` | 缺少稀释加权平均股数 |
| `ffo_per_share_period_mismatch` / `ffo_per_share_unit_mismatch` | FFO/share 输入期间或单位不一致 |
| `affo_reconciliation_not_disclosed` | 没有公司明确披露且可追溯的 AFFO reconciliation |
| `same_store_noi_not_disclosed` / `occupancy_not_disclosed` | 可选运营指标未披露 |
| `net_debt_to_ebitda_not_disclosed` | 可选杠杆指标未披露 |
| `net_debt_to_ebitda_period_ambiguous` / `net_debt_to_ebitda_no_annualization_basis` | 杠杆指标期间不明或不能合法年化 |
| `dividend_coverage_not_disclosed` / `zero_common_dividends` | 股息覆盖缺披露或分母为零 |
| `market_price_missing` / `price_to_ffo_currency_mismatch` | P/FFO 缺价格或币种不匹配 |
| `non_positive_ffo_per_share` | FFO/share 小于或等于零，P/FFO 不可用 |
| `reit_primary_valuation_not_pe` | REIT 不采用普通 P/E 作为主估值 |
| `reit_primary_cash_metric_not_fcf` | REIT 不采用普通 FCF yield 作为主现金指标 |

`pe` 和 `fcf_yield` 固定为 `status=not_applicable`、`blocking=false`，而不是 `unavailable`；报告必须用解释性文本把分析引导至 FFO/AFFO 和 `price_to_ffo`。普通企业的 P/E/FCF 规则不应被复制为 REIT 的无条件阻断条件。SEC 关于非 GAAP 指标应清晰标注、与最直接可比 GAAP 指标对照并避免误导性调整的要求见 [SEC Non-GAAP Financial Measures](https://www.sec.gov/rules-regulations/staff-guidance/corporation-finance-interpretations/non-gaap-financial-measures)。
