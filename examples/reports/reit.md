## 样例 artifact 元数据

- synthetic=true
- offline=true
- no_network=true
- source fixture: `tests/fixtures/profiles/reit/complete.json`
- as_of: `2026-02-16T00:00:00Z`
- coverage: `full`
- limitations: synthetic fixture inputs only; no live SEC, Yahoo, LLM, or CrewAI access; REIT P/E and ordinary FCF Yield remain not_applicable by policy; not real-time data or investment advice
- 说明：这是可审计的离线演示样例，不是实时行情或投资建议。

## 证据与计算追溯

以下索引由最终 ReportContext 自动生成；`—` 表示固定输入没有该字段，不代表补零或可用。

| type | id | status | evidence_id(s) | calculation_id(s) | source_reference | reason_code |
|---|---|---|---|---|---|---|
| metric | ffo_total | available | ev_reit_complete_net_income, ev_reit_complete_depreciation, ev_reit_complete_other_adjustment, ev_reit_complete_ffo_total | calc_reit_ffo_reconciliation_v1 | fixture:sec-like/reit/complete/net-income | — |
| metric | ffo_per_share | available | ev_reit_complete_ffo_total, ev_reit_complete_diluted_shares | calc_reit_ffo_per_share_v1 | fixture:sec-like/reit/complete/ffo-total | — |
| metric | affo | available | ev_reit_complete_ffo_total, ev_reit_complete_affo_adjustment, ev_reit_complete_affo_total | calc_company_disclosed_affo_reconciliation_v1 | fixture:sec-like/reit/complete/ffo-total | — |
| metric | net_debt_to_ebitda | available | ev_reit_complete_net_debt, ev_reit_complete_ebitda | calc_reit_net_debt_to_ebitda_v1 | fixture:sec-like/reit/complete/net-debt | — |
| metric | dividend_coverage | available | ev_reit_complete_ffo_attributable, ev_reit_complete_common_dividends | calc_reit_dividend_coverage_v1 | fixture:sec-like/reit/complete/ffo-attributable | — |
| metric | price_to_ffo | available | price_reit_complete_20260216, ev_reit_complete_ffo_total, ev_reit_complete_diluted_shares | calc_reit_price_to_ffo_v1 | fixture:market/reit/complete/price | — |
| policy decision | same_store_noi | available | ev_reit_complete_same_store_noi | — | — | validated_evidence |
| policy decision | occupancy | available | ev_reit_complete_occupancy | — | — | validated_evidence |
| policy decision | pe | not_applicable | — | — | — | reit_primary_valuation_not_pe |
| policy decision | fcf_yield | not_applicable | — | — | — | reit_primary_cash_metric_not_fcf |
# 投资研究报告

## 执行摘要

确定性状态：status=ready

总体判断：数据不足
风险等级：数据不足
触发规则：无触发规则
行动参考：补齐已验证数据后再评估

报告由已验证研究结果生成。

Profile：issuer=reit; security=common_stock; reporting=domestic_us_gaap
覆盖范围：full
Policy version：metric-policy:v2

## 公司质量

公司质量部分由确定性 Renderer 注入已验证内容。

未提供可单独展示的文字 Claim。

- FFO 总额：150.00 USD（期间截至 2025-12-31；截至 2026-02-15T00:00:00Z；来源：fixture:sec-like/reit/complete/net-income）
- FFO/股：3.00 USD/share（期间截至 2025-12-31；截至 2026-02-15T00:00:00Z；来源：fixture:sec-like/reit/complete/ffo-total）
- AFFO：120.00 USD（期间截至 2025-12-31；截至 2026-02-15T00:00:00Z；来源：fixture:sec-like/reit/complete/ffo-total）
- 净债务/EBITDA：3.00x（期间截至 2025-12-31；截至 2026-02-15T00:00:00Z；来源：fixture:sec-like/reit/complete/net-debt）
- 股息覆盖：1.50x（期间截至 2025-12-31；截至 2026-02-15T00:00:00Z；来源：fixture:sec-like/reit/complete/ffo-attributable）

## 财务趋势

财务趋势部分由确定性 Renderer 注入已验证内容。

未提供可单独展示的文字 Claim。



## 当前估值

当前估值部分由确定性 Renderer 注入已验证内容。

未提供可单独展示的文字 Claim。

- P/FFO：10.00x（期间截至 2025-12-31；截至 2026-02-16T00:00:00Z；来源：fixture:market/reit/complete/price）

### REIT 估值与现金指标适用性
- P/E：not_applicable（reit_primary_valuation_not_pe）；REIT 主估值看 FFO/AFFO/P-FFO。
- FCF Yield：not_applicable（reit_primary_cash_metric_not_fcf）；不能用普通企业 FCF Yield 替代。

## 历史估值

历史估值部分由确定性 Renderer 注入已验证内容。

未提供可单独展示的文字 Claim。



## 反向 DCF

反向 DCF 部分由确定性 Renderer 注入已验证内容。

未提供可单独展示的文字 Claim。



反向 DCF：缺少已验证的 TTM 自由现金流或模型结果，未生成参数表。

## 主要风险

主要风险部分由确定性 Renderer 注入已验证内容。

未提供可单独展示的文字 Claim。

## 数据来源与方法

来源与方法部分由确定性 Renderer 注入已验证内容。

- fixture:sec-like/reit/complete/net-income
- fixture:sec-like/reit/complete/ffo-total
- fixture:sec-like/reit/complete/net-debt
- fixture:sec-like/reit/complete/ffo-attributable
- fixture:market/reit/complete/price

### 术语说明
- P/E（市盈率）：股价相对于每股收益的倍数，用于描述市场对盈利的定价。
- FCF Yield（自由现金流收益率）：自由现金流相对于市值的收益率。
- TTM（过去十二个月）：以最近连续十二个月为口径汇总经营数据。
- DCF（现金流折现）：将未来现金流折算到当前价值的估值方法。
- 反向 DCF（由市场价格倒推隐含增长）：从当前市场价格反推出模型所隐含的增长假设。
- FFO（运营资金）：REIT 用于补充说明物业经营表现的行业指标，不能替代 GAAP 净利润。
- AFFO（调整后运营资金）：仅采用公司明确披露并可追溯的 AFFO reconciliation，没有统一通用公式。
- P/FFO：市场价格相对于每股 FFO 的倍数，是 REIT 的主要估值参考之一。

## 非投资建议声明

本文不构成任何投资建议。

本文不构成任何投资建议。
