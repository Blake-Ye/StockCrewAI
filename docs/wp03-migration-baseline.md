# WP03 迁移前基线

生成日期：2026-08-10

本基线用于验证 WP03“共享热点文件拆分”不改变现有行为。基线提交为
`1e14790e3c1ef74373da3471172ec59cef31685d`（`wp02/profile-aware-gates`）。

## 固定输入

- 离线 fixture：`tests/test_crew_configuration.py` 中
  `ReportContractTests._reader_focused_context_inputs()`。
- Report draft fixture：同文件的 `VALID_REPORT_DRAFT`。
- 生成链路：`build_report_context()` → `parse_report_draft()` →
  `render_validated_report()`；图表通过 `build_report_visuals(context=...)` 生成。
- JSON hash 对规范化 JSON 使用 `ensure_ascii=False`、排序键和紧凑分隔符后计算
  SHA-256。
- Markdown hash 对 UTF-8 字节计算 SHA-256；图表 hash 对内嵌 PNG 解码后的字节计算
  SHA-256。

## 文件规模与导入基线

| 文件 | 行数 |
| --- | ---: |
| `src/stockcrewai/main.py` | 1847 |
| `src/stockcrewai/pipeline_support.py` | 2189 |
| `src/stockcrewai/crews/report/crew.py` | 1715 |

三文件导入语句集合的 SHA-256：
`de9b768aa3fd3ed9be34bb1b58ff0c25ddd57d39e769c43b809dafc5e8feb6c6`。

## Artifact 基线

| Artifact | 值 |
| --- | --- |
| canonical context JSON SHA-256 | `0ba60355467512f84837c34ce3118f19cbe272433532bf2004c4c6aa0d087582` |
| canonical context JSON 字节数 | 10570 |
| rendered Markdown SHA-256 | `0031e06c12489977736cf4e9bc61c4dae4b6f05acc763303ca58fa19dc620e28` |
| rendered Markdown 字节数 | 173050 |
| 图表数量 | 3 |
| `financial_kpis` PNG SHA-256 | `9f68282d2f87c3e3b064e78a26da0fd2aed9dd76f0ab5bb7f1b02ba237038175` |
| `ttm_scale` PNG SHA-256 | `82305a87783b8c81366bceacb561f029645b28539e52977d2da788f0eabd88b3` |
| `historical_pe` PNG SHA-256 | `46b35af391ddee0f01a2193ba444d8acac5a9f1924b2744e51313e6715b283cb` |

## 运行环境

- Python `3.12.13`
- CrewAI `1.15.11`
- Matplotlib `3.11.1`

WP03 每次迁移后必须用同一 fixture 重新计算上述 hash。任何 hash 变化都必须先
定位为序列化、文字、公式、图形或环境差异；不能用更新基线掩盖行为变化。
