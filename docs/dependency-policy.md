# WP00 依赖政策

依赖必须由 `uv` 管理。除本表外，不在 WP00 增加包；allowlist 外依赖需要用户单独批准。当前 Python 为 3.12.13，CrewAI 为 1.15.11；CrewAI 当前约束为 `>=1.15.11,<2.0.0`，本阶段不升级它，以避免改变 Flow/Crew 行为。

| 包 | pyproject 约束 | 观测版本 | 用途 | 项目 URL | 许可证记录 | 退出条件 |
| --- | --- | --- | --- | --- | --- | --- |
| pytest | `>=8,<10` | 9.1.1 | 统一测试 runner | https://pytest.org | 上游元数据未提供标准字段，发布前核验 | 测试迁移完成且 runner 不再需要 |
| hypothesis | `>=6,<7` | 6.165.2 | Decimal 性质测试 | https://hypothesis.readthedocs.io | 上游元数据未提供标准字段，发布前核验 | 性质测试稳定且替代方案足够 |
| pytest-xdist | `>=3,<4` | 3.8.0 | 固定 3 worker 的离线并行 | https://github.com/pytest-dev/pytest-xdist | 上游元数据未提供标准字段，发布前核验 | 测试规模不再需要并行 |
| ruff | `>=0.12,<1` | 0.16.2 | 授权路径 lint | https://github.com/astral-sh/ruff | 上游元数据未提供标准字段，发布前核验 | 项目改用其他已审查 lint |
| mypy | `>=1.15,<3` | 2.3.0 | 新模块静态类型检查 | https://www.mypy-lang.org | 上游元数据未提供标准字段，发布前核验 | 类型边界由其他门禁覆盖 |
| crewai[tools] | `>=1.15.11,<2.0.0` | 1.15.11 | Flow、Crew、Agent 编排 | https://github.com/crewAIInc/crewAI | MIT；发布前以锁文件和上游 LICENSE 复核 | 不再使用 CrewAI |
| edgartools | `>=5.45.1,<6.0.0` | 5.45.1 | SEC/EDGAR 事实检索 | https://github.com/dgunning/edgartools | 上游元数据未提供标准字段，发布前核验 | SEC 适配层替换且完成验收 |
| matplotlib | `>=3.10.9` | 3.11.1 | 确定性报告图表 | https://matplotlib.org | Matplotlib License；发布前复核依赖字体许可证 | 报告不再生成图表 |
| yfinance | `>=1.5.2` | 1.5.2 | Yahoo 行情输入 | https://github.com/ranaroussi/yfinance | Apache-2.0；发布前核验 | 改用已审查市场数据源 |

WP00 的已批准安装命令是：

```bash
uv add --dev "pytest>=8,<10" "hypothesis>=6,<7" "pytest-xdist>=3,<4" "ruff>=0.12,<1" "mypy>=1.15,<3"
```

所有依赖都必须锁定在 `uv.lock`；不得因测试方便升级 CrewAI、Pydantic 或现有业务包。
