# Renderer Final Fixes Report

日期：2026-08-12

## 修复范围

- Important 1：Renderer 复用 `context._normalized_amount()` 的 `Decimal` 单位转换。TTM 记录和 reverse DCF payload 分别读取自己的 `unit`/`base_fcf_unit`，`USD`、`million USD`、`billion USD` 统一换算为美元后再显示；`22000 million USD` 显示为 `220.00 亿美元`。
- Important 4：正文的 `share_dilution` 只接受 `raw` 与 `split_adjusted`；`split_adjusted` 标签明确显示“拆分调整”，其它口径省略，与图表过滤边界一致。
- Important 5：SEC URL 使用 `urllib.parse.urlsplit` 精确校验 hostname，仅接受 `sec.gov`、`www.sec.gov`、`data.sec.gov`；伪造域名不再附加 SEC 来源标识。
- Minor 1：报告标题中的公司名和 ticker 清理换行、不可打印字符及 Markdown 控制字符，并压缩为空格分隔的单行普通文本。

## TDD 证据

- RED：新增回归测试首次运行 `5 failed, 1 passed, 24 deselected`，失败对应上述 renderer 行为。
- GREEN：新增回归测试运行 `6 passed, 24 deselected`。
- 定向回归：`tests/test_reporting_modules.py tests/test_report_visuals.py` → `41 passed, 10 subtests passed`。
- Ruff：目标 renderer、reporting 测试和 visuals 测试 `All checks passed!`。
- `git diff --check`：无输出。

## 文件与边界

- 修改：`src/stockcrewai/reporting/renderer.py`、`tests/test_reporting_modules.py`、本报告。
- 未修改：`src/stockcrewai/reporting/context.py`、`src/stockcrewai/reporting/validator.py`。
- 未提交 commit。

## 遗留风险

验证使用离线测试，没有执行实时 SEC、市场数据或付费 LLM 请求。工作区原有其它文件改动保留未动。
