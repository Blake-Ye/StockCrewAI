# WP00 测试策略

## 默认门禁

默认测试必须离线、可重复，不调用 SEC、Yahoo、DeepSeek 或任何付费 API，也不依赖 VPN。当前基线包括 274 个 unittest。WP00 新增基线测试覆盖 TSLA 文本误判、reverse DCF 不适用、Profile 传输和 typed 外部错误。

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q -n 3
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest discover -s tests -p 'test_*.py'
```

## 性质测试

`tests/conftest.py` 注册 Hypothesis `ci` profile：`max_examples=200`、`derandomize=True`、`deadline=None`、无数据库。Decimal 公式测试覆盖零、负数、极值、顺序和缺失，不允许用 NaN/Infinity 掩盖契约错误。

## live 边界

真实外部 smoke 测试使用 `@pytest.mark.live`，默认跳过；只有显式传入 `--run-live` 才能运行。live runner 只接受明确 ticker，成功返回真实结果，失败返回非零退出码及 typed error；live 失败不能改写离线基线，也不能被解释为代码通过。

## 并行和临时文件

可并行测试只写 `tmp_path` 或专用 `/private/tmp` 测试目录；不得写 `run-output.md`、正式报告或共享根目录 artifact。项目固定使用 3 个 xdist worker，并比较串行/并行的通过数和失败集合。提交前还要运行 `compileall`、授权路径 Ruff 和 `git diff --check`。
