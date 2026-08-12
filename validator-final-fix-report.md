# Validator Final Fix Report

## 变更范围

- src/stockcrewai/reporting/validator.py：最终 Markdown 非免责声明区域现在拒绝独立的英文 buy、sell、hold（不区分大小写）。
- 仅当整行符合 ticker/title 语境且 ticker 精确为大写 HOLD 时，才允许该英文词；不会通过大小写直接放行。
- tests/test_holding_company_report.py：补充裸英文词、HOLD ticker、持有公司和公司持有资产的回归覆盖。
- 未修改 renderer 或 context，未提交。

## TDD 证据

1. RED：新增裸词测试后运行：

   UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run pytest tests/test_holding_company_report.py -k 'rendered_report_allows_holding_facts_and_ticker_identifier or rendered_report_rejects_explicit_trading_advice' -q

   结果：4 failed, 12 passed, 4 deselected；失败均为裸 buy、sell、hold 或 HOLD 未被拒绝。

2. GREEN：最小 validator 实现后同一命令：

   结果：16 passed, 4 deselected。

3. 相关完整测试：

   - tests/test_holding_company_report.py：20 passed。
   - tests/test_reporting_modules.py -k 'rendered_report_validator'：1 passed, 23 deselected。
   - 联合运行两文件：41 passed, 3 failed；3 个失败是 reverse-DCF/context 断言（base_fcf_unit、缺失 base_fcf、TTM mismatch），不涉及本次 validator/holding 改动，遗留给 context/另一代理处理。
