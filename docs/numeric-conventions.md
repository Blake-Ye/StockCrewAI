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
