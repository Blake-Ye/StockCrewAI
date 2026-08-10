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
