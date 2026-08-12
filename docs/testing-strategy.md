# 当前测试策略

## 默认离线门禁

默认测试必须可重复，不调用 SEC、Yahoo、DeepSeek 或付费 API。使用 fixture、替身和依赖注入覆盖成功、部分数据、不可适用、输出契约错误和阻断路径。

```bash
UV_PROJECT_ENVIRONMENT=/Users/yeziqing/Projects/stockcrewai/.venv \
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache \
CREWAI_STORAGE_DIR=/private/tmp/stockcrewai-flow-storage \
CREWAI_TRACING_ENABLED=false OTEL_SDK_DISABLED=true \
uv run --no-sync pytest -q
```

提交前至少运行：

```bash
uv run --no-sync python -m compileall -q src tests
uv run --no-sync ruff check src tests
git diff --check
```

## 外部依赖测试

真实运行单独验证 DeepSeek、EDGAR 和 Yahoo。外部限流、TLS、权限和代理问题不能作为离线代码通过的证据；报告中应保留 `error.category`、`error.reason_code`、`stage` 和 `required_data`。

## 报告验收

- 只有 `status=ok`、`stage=report` 且正文非空时写入正式报告。
- 阻断或空报告不能覆盖上一次正式报告。
- 图片必须是可解码的 PNG Data URI，临时文件写入临时目录，不能污染仓库。
- 报告数字必须来自验证后的上下文，不能来自 Agent 自由生成的数字。
