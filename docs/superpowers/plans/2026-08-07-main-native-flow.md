# Main.py 原生 CrewAI Flow 实施计划

> 所有编码子任务使用 `luna_coder`（GPT-5.6 Luna，Max）。每个代理必须先加载
> `superpowers:using-superpowers`、`superpowers:test-driven-development` 和
> `superpowers:verification-before-completion`，不得覆盖其他代理或用户的改动。

**目标：** 让 `src/stockcrewai/main.py` 直接展示并执行唯一的 CrewAI 原生 Flow，
同时保证 `crewai flow kickoff` 与 `crewai flow plot` 可用。

**设计基线：**
`docs/superpowers/specs/2026-08-07-main-native-flow-design.md`。

## 固定接口

```python
class ResearchFlowState(BaseModel): ...

@persist()
class ResearchFlow(Flow[ResearchFlowState]):
    @start()
    def parse_request(self): ...

    @listen(parse_request)
    def prepare_evidence(self, parsed_request): ...

    @listen(prepare_evidence)
    def prepare_valuation(self, evidence): ...

    @router(prepare_valuation)
    def route_analysis(self, valuation): ...

    @listen("analysis_ready")
    def run_analysis(self): ...

    @router(run_analysis)
    def route_claims(self, analysis): ...

    @listen("claims_ready")
    def generate_report(self): ...
```

```toml
[project.scripts]
kickoff = "stockcrewai.main:kickoff"
plot = "stockcrewai.main:plot"
```

## 任务 1：锁定 main Flow 与命令契约（可与任务 2 并行）

**唯一写入范围：**

- 新建 `tests/test_main_flow.py`

步骤：

1. 先写失败测试，断言 `ResearchFlowState` 和 `ResearchFlow` 直接定义于
   `stockcrewai.main`。
2. 断言只有一个无条件 `@start()`，顺序边使用方法引用 `@listen`，分支使用
   `@router` 和四个稳定标签。
3. 用离线 fake 覆盖成功、Analysis Gate 阻断、Claim Gate 阻断。
4. 断言 `kickoff()` 启动完整 Flow 并保留 `run-output.md`；断言 `plot()` 调用
   `ResearchFlow.plot("stockcrewai_flow")`。
5. 断言 `pyproject.toml` 的 `kickoff`、`plot` 脚本映射符合固定接口。
6. 运行该测试，确认失败原因是生产迁移尚未完成。

## 任务 2：机械提取确定性辅助逻辑（可与任务 1 并行）

**唯一写入范围：**

- 新建 `src/stockcrewai/pipeline_support.py`

步骤：

1. 从当前 `main.py` 提取 Flow 节点会调用的序列化、脱敏、SEC/计算/验证、估值、
   Analysis 输入/Claims Gate、Verdict 和 Crew 兼容辅助函数。
2. 保持函数参数和返回值；不得修改算法、Agent YAML、工具、网络策略或输出契约。
3. 将 Request Parser Crew 的原 `kickoff(request)` 辅助函数改名为语义明确的
   `run_request_parser(request)`，避免与整个 Flow 的 `kickoff()` 冲突。
4. 每个公开/内部函数保留或补充中文 docstring。
5. 只运行导入、编译和现有纯辅助函数测试；不修改 `main.py` 以接入新模块。

## 任务 3：实现 main.py 唯一原生 Flow（依赖任务 1、2）

**唯一写入范围：**

- 修改 `src/stockcrewai/main.py`
- 修改 `pyproject.toml`

步骤：

1. 在 `main.py` 直接定义 Pydantic State 和 `@persist()` Flow。
2. Flow 方法只负责读取上游结果、调用 Crew/`pipeline_support`、更新 State 和返回
   稳定路由；所有注入的工具/Crew 放在 `PrivateAttr`。
3. 保留 `run_research()` 的完整签名，内部只构造 Flow 并调用
   `flow.kickoff(inputs={"request": request})`。
4. 定义项目级 `kickoff()`：解析请求、执行完整 Flow、把终端输出同步保存到
   `run-output.md`；保留 `cli()` 兼容别名/薄包装。
5. 定义 `plot()`：只调用 `ResearchFlow().plot("stockcrewai_flow")`。
6. 更新 `pyproject.toml` 脚本为固定映射。
7. 运行 `tests/test_main_flow.py` 和受影响的现有测试。

## 任务 4：迁移测试并清理重复 Flow（依赖任务 3）

**唯一写入范围：**

- 修改 `tests/test_crew_configuration.py`
- 修改 `tests/test_runtime_defaults.py`
- 修改 `tests/test_run_and_save_output.py`
- 删除 `tests/test_research_flow.py`
- 删除 `src/stockcrewai/research_flow.py`
- 必要时修改 `README.md`

步骤：

1. 将旧模块导入和 patch 目标迁移到 `stockcrewai.main` 或
   `stockcrewai.pipeline_support`。
2. 不放宽任何业务断言，不把失败测试改成跳过。
3. 确认项目只剩一个 `ResearchFlow` 定义，且无生产代码引用旧模块。
4. 更新 README 中两条官方命令及生成文件说明。
5. 运行全部离线测试。

## 任务 5：独立审查与最小修复

先由只读审查代理检查：装饰器语法、事件图、确定性边界、SQLite State、入口映射、
终端双写和遗留重复定义。若有明确 finding，再给新的 `luna_coder` 分配严格限定的
文件修复范围。

## 最终验收

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache \
CREWAI_STORAGE_DIR=/private/tmp/stockcrewai-flow-storage \
uv run --no-sync python -m unittest discover -s tests -q

UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache \
uv run --no-sync python -m compileall -q src/stockcrewai

UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache \
uv run --no-sync crewai flow plot

git diff --check
rg -n "stockcrewai\.research_flow|class ResearchFlow" src tests
```

`crewai flow kickoff` 的真实网络运行可能受 SEC/Yahoo/DeepSeek 可用性影响，因此还需
先用离线替身验证命令确实进入完整 Flow，再在用户当前网络环境执行一次真实命令。
命令进入 Flow 但外部服务失败，不能误报为入口失败。
