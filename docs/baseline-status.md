# WP00 基线状态

## 已完成证据

- 规划文档已在 `main` 推送；实现工作在 `wp00/trustworthy-baseline` 分支。
- Python 3.12.13、uv 0.12.1、CrewAI 1.15.11 已核验；官方最新文档版本为 1.15.14，本阶段不升级 CrewAI。
- 旧 unittest 基线为 274 个测试，全部通过。
- WP00 新增回归/性质/live 边界测试在当前阶段已观察到 7 passed、1 skipped。
- 全量 pytest 串行 `281 passed, 1 skipped`，固定 3 worker 的 xdist 也是 `281 passed, 1 skipped`；274 个 unittest 仍全部通过。
- 授权路径 Ruff、compileall 和 `git diff --check` 已通过；live 测试默认跳过且默认测试未触网。

## 已冻结的根因修复

1. Report draft 的建议/结论正则不再把普通公司事实文本中的孤立词语当成投资建议；真正的动作组合仍然拒绝。
2. reverse DCF 明确不适用时由 Policy 标成 `not_applicable`，不把该可选域当成缺失的必需数据。
3. Flow 将 Profile 通过 state、Gate、Analysis 输入和 Report context 传递，而不是依赖 Agent 猜测。
4. live smoke 将 SEC/Yahoo/运行错误转换为稳定 category/reason_code，不制造成功数据。

## 尚未宣称完成

WP00 的离线门禁已完成；外部 SEC/Yahoo/DeepSeek 真实线路不属于默认门禁，也未在本阶段宣称成功。仍需在提交前检查 staged diff，并在后续用户检查通过后再进入 WP01。
