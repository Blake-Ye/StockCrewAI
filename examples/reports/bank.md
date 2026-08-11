## 样例 artifact 元数据

- synthetic=true
- offline=true
- no_network=true
- source fixture: `tests/fixtures/profiles/bank/complete.json`
- as_of: `2026-03-02T21:00:00Z`
- coverage: `full`
- limitations: synthetic fixture inputs only; no live SEC, Yahoo, LLM, or CrewAI access; ordinary enterprise FCF is not applicable to this bank profile; not real-time data or investment advice
- 说明：这是可审计的离线演示样例，不是实时行情或投资建议。

## 证据与计算追溯

以下索引由最终 ReportContext 自动生成；`—` 表示固定输入没有该字段，不代表补零或可用。

| type | id | status | evidence_id(s) | calculation_id(s) | source_reference | reason_code |
|---|---|---|---|---|---|---|
| metric | bank_roa | available | ev_bank_net_income, ev_bank_average_assets | calc-bank_roa-v1 | derived:bank-roa-v1 | — |
| metric | bank_roe | available | ev_bank_net_income, ev_bank_average_equity | calc-bank_roe-v1 | derived:bank-roe-v1 | — |
| metric | net_interest_margin | available | ev_bank_nii, ev_bank_earning_assets | calc-net_interest_margin-v1 | derived:bank-net-interest-margin-v1 | — |
| metric | efficiency_ratio | available | ev_bank_noninterest_expense, ev_bank_nii, ev_bank_noninterest_income | calc-efficiency_ratio-v1 | derived:bank-efficiency-ratio-v1 | — |
| metric | cet1_ratio | available | ev_bank_cet1 | — | fixture:bank/complete/cet1-ratio | — |
| metric | loan_to_deposit | available | ev_bank_loans, ev_bank_deposits | calc-loan_to_deposit-v1 | derived:bank-loan-to-deposit-v1 | — |
| metric | nonperforming_loan_ratio | available | ev_bank_npl, ev_bank_loans | calc-nonperforming_loan_ratio-v1 | derived:bank-nonperforming-loan-ratio-v1 | — |
| metric | provision_coverage | available | ev_bank_allowance, ev_bank_npl | calc-provision_coverage-v1 | derived:bank-provision-coverage-v1 | — |
| metric | price_to_book | available | ev_bank_market_price, ev_bank_bvps | calc-price_to_book-v1 | derived:bank-price-to-book-v1 | — |
| metric | pe_ratio | available | ev_bank_market_price, ev_bank_eps | calc-pe_ratio-v1 | derived:bank-pe-ratio-v1 | — |
| policy decision | fcf_yield | not_applicable | — | — | — | bank_fcf_not_applicable |
# 投资研究报告

## 执行摘要

确定性状态：status=ready

总体判断：数据不足
风险等级：数据不足
触发规则：无触发规则
行动参考：补齐已验证数据后再评估

报告由已验证研究结果生成。

Profile：issuer=bank; security=common_stock; reporting=domestic_us_gaap
覆盖范围：full
Policy version：metric-policy:bank:v1

## 公司质量

公司质量部分由确定性 Renderer 注入已验证内容。

未提供可单独展示的文字 Claim。



### 银行专用指标
- 银行 ROA：1.00%（截至 2026-03-02T21:00:00Z；来源：derived:bank-roa-v1）
- 银行 ROE：10.00%（截至 2026-03-02T21:00:00Z；来源：derived:bank-roe-v1）
- NIM：3.60%（截至 2026-03-02T21:00:00Z；来源：derived:bank-net-interest-margin-v1）
- 效率比率：40.00%（截至 2026-03-02T21:00:00Z；来源：derived:bank-efficiency-ratio-v1）
- CET1：13.00%（截至 2026-03-02T21:00:00Z；来源：fixture:bank/complete/cet1-ratio；直接披露证据）
- 贷存比：90.00%（截至 2026-03-02T21:00:00Z；来源：derived:bank-loan-to-deposit-v1）
- 不良贷款率：1.00%（截至 2026-03-02T21:00:00Z；来源：derived:bank-nonperforming-loan-ratio-v1）
- 拨备覆盖率：66.67%（截至 2026-03-02T21:00:00Z；来源：derived:bank-provision-coverage-v1）
- P/B：1.25x（截至 2026-03-02T21:00:00Z；来源：derived:bank-price-to-book-v1）
- P/E：10.00x（截至 2026-03-02T21:00:00Z；来源：derived:bank-pe-ratio-v1）
- FCF Yield：not_applicable（bank_fcf_not_applicable）；银行不计算普通企业 FCF Yield。

## 财务趋势

财务趋势部分由确定性 Renderer 注入已验证内容。

未提供可单独展示的文字 Claim。



## 当前估值

当前估值部分由确定性 Renderer 注入已验证内容。

未提供可单独展示的文字 Claim。



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

- derived:bank-roa-v1
- derived:bank-roe-v1
- derived:bank-net-interest-margin-v1
- derived:bank-efficiency-ratio-v1
- fixture:bank/complete/cet1-ratio
- derived:bank-loan-to-deposit-v1
- derived:bank-nonperforming-loan-ratio-v1
- derived:bank-provision-coverage-v1
- derived:bank-price-to-book-v1
- derived:bank-pe-ratio-v1

### 术语说明
- P/E（市盈率）：股价相对于每股收益的倍数，用于描述市场对盈利的定价。
- FCF Yield（自由现金流收益率）：自由现金流相对于市值的收益率。
- TTM（过去十二个月）：以最近连续十二个月为口径汇总经营数据。
- DCF（现金流折现）：将未来现金流折算到当前价值的估值方法。
- 反向 DCF（由市场价格倒推隐含增长）：从当前市场价格反推出模型所隐含的增长假设。
- ROA（资产回报率）：净利润相对于平均资产的比例，衡量银行资产创造利润的效率。
- ROE（净资产收益率）：净利润相对于平均股东权益的比例，衡量股东资本回报。
- NIM（净息差）：净利息收入相对于平均生息资产的比例，反映银行核心息差。
- 效率比率：非利息费用相对于净利息收入与非利息收入之和的比例，通常越低表示效率越高。
- CET1（普通股权一级资本充足率）：直接披露的核心资本相对风险承担的监管比率。
- 贷存比：贷款总额相对于存款总额的比例。
- 不良贷款率：不良贷款相对于贷款总额的比例。
- 拨备覆盖率：信用损失准备相对于不良贷款的比例。
- P/B（市净率）：股价相对于每股账面价值的倍数。

## 非投资建议声明

本文不构成任何投资建议。

本文不构成任何投资建议。
