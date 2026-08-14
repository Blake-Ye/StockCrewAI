# Task 3 完成报告：风险监控表、综合判断与完整回归

## RED

- 命令：`UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_reporting_modules.py -k "risk_monitoring_table or strict_lite_conclusion"`
- 结果：`2 failed, 75 deselected`。
- 失败原因：真实渲染结果仍为风险项目列表，没有 `| 风险 | 影响路径 | 监控指标 | 来源 |` Markdown 表头；最终章节也没有 `## 9. 非投资建议声明` 标记。

## GREEN

- 聚焦命令：`2 passed, 75 deselected`。
- 相关回归：`168 passed, 43 warnings, 92 subtests passed`。
- 全套离线测试：`943 passed, 136 warnings, 170 subtests passed in 17.07s`。
- `git diff --check`：通过。
- 警告：仅既有 CrewAI deprecation warnings。

## live run

- 命令：`STOCKCREWAI_REQUEST='分析苹果公司 AAPL 是否值得投资，投资期限为3到5年' UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync crewai run`
- 状态：`status=ok`，`stage=report`；请求解析、SEC 验证、估值、Analysis Gate、Analysis Crew、Claim Gate、Verdict/Report 均完成。
- Artifact：`investment-report.md`、`run-result.json`。
- 实际 artifact 检查：章节顺序为 0–8 后独立免责声明；FY/TTM 内容存在；PNG 数量为 2。
- 真实遗留风险：简报预期 live artifact 为 3 张 PNG，但本次真实运行只生成 2 张；未做 fallback，未修改 Flow/tools，父代理可重新运行 live。
- 进程检查：系统无法提供进程列表（`sysmond service not found` / `pgrep: Cannot get process list`），未执行扩大范围的进程终止。

## 修改文件

- `src/stockcrewai/reporting/renderer.py`
  - 风险正文改为前三条 validated risk Claim 的 Markdown 表；后续 Claim 保留在既有 `<details>` 附录。
  - 复用 `_RISK_IMPACT_RULES`，监控指标不增加概率、目标价影响或 LLM 推断。
  - 风险单元格转义竖线与换行，来源引用保留可读 URL。
  - 综合判断拆为已验证事实、确定性比较、确定性判断、重新评估条件四块；审计元数据与判断规则仍在 section 8。
- `tests/test_reporting_modules.py`
  - 增加真实最终报告的风险表、前三条/附录边界、单元格转义和结论章节行为测试。
- `tests/test_crew_configuration.py`
  - 补齐 reader-focused fixture 的 FY period basis，保持图表期间校验与真实输入一致。

## Commit

- `feat: finalize strict-lite report format`

## Concerns

- live 本次成功到 report，但实际只嵌入 2 张 PNG，未达到简报要求的 3 张；父代理应重新运行并继续记录真实结果。
