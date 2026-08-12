# Context Final Fixes Report

范围严格限定为 `src/stockcrewai/reporting/context.py` 与
`tests/test_reporting_modules.py`，仅完成 final-review Important 1–3；未修改
renderer，未提交。

## 修复

- reverse DCF context 保留 `base_fcf_unit` 与 `unit`；TTM FCF 和 reverse DCF
  base FCF 通过同一 `Decimal` 单位归一化合同校验，支持 `million USD` 与
  `billion USD` 的等值输入。
- reverse DCF 只有在 canonical、已验证的 TTM FCF 存在，且 `base_fcf`/单位
  一致时才投影；缺少 `base_fcf` 时不输出 `implied_growth`。not-applicable
  profile 保持原行为。
- TTM metric 缺少 `period_basis` 时继续兼容提升为 `TTM`，并参与 reverse
  DCF consistency 校验；显式 `FY`/`YTD` 仍被拒绝。

## TDD 与验证

- 新增三个失败测试并先运行确认失败：单位字段保真、缺失 base、缺失
  `period_basis` 的 mismatch。
- `tests/test_reporting_modules.py tests/test_report_visuals.py`：35 passed，
  10 subtests passed。
- `python -m compileall -q src/stockcrewai/reporting`：通过。
- `git diff --check`：通过。

## 遗留风险

完整 context 相关集合为 297 passed、82 subtests passed、49 warnings、1 failed。
唯一失败是 `tests/test_crew_configuration.py::ReportContractTests::test_report_context_normalizes_all_metric_sections_and_is_json_safe`；该旧断言要求缺少 `base_fcf` 时仍生成 reverse DCF 指标，与本次 B 要求冲突。该文件不在授权修改范围内，因此未改动。
