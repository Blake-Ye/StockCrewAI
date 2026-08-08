# StockCrewAI 紧凑运行输出实施计划

> 编码任务全部使用 `luna_coder`（Luna Max），必须遵循 TDD、验证优先和互斥文件
> 所有权。设计基线：`docs/superpowers/specs/2026-08-07-compact-run-output-design.md`。

## 任务 1：输出契约 RED 测试（与任务 2 并行）

唯一写入：`tests/test_compact_run_output.py`

- 测试 ANSI 清理；
- 测试七阶段事件渲染；
- 测试 blocked 摘要包含 Gate、域、reason、required_data、已完成与未执行；
- 测试 Markdown 行数上限、禁止字段和业务状态/退出码分离；
- 测试完整 JSON 保留 diagnostics；
- 测试 Reporter 输出不包含完整 Evidence ID 数组；
- 先确认因模块/接口未实现而 RED，不修改生产代码。

## 任务 2：独立渲染模块（与任务 1 并行）

唯一写入：`src/stockcrewai/run_output.py`

- 实现冻结的 `RunStageEvent`、`CompactRunReporter`、`strip_ansi`、
  `summarize_result`；
- 只依赖标准库和 Rich；
- Reporter 保存结构化事件，不保存原始 Crew 输出；
- Markdown 和 JSON 使用独立写入路径；
- 所有函数补中文 docstring；
- 不导入或修改 Flow、Crew、工具。

## 任务 3：Main Flow 集成（依赖任务 1、2）

唯一写入：`src/stockcrewai/main.py`

- 设置 `suppress_flow_events=True`；
- 将 reporter callback 放入 PrivateAttr；
- 七个逻辑阶段发送最小事件摘要；
- `kickoff()` 捕获原始 CrewAI stdout/stderr，Reporter 绕过捕获写真实终端；
- `run-output.md` 只写摘要，`run-result.json` 写完整结果；
- 保持 `run_research()` 输出和现有脚本入口兼容；
- 对成功、Analysis Gate 阻断、Claim Gate 阻断、异常路径运行测试。

## 任务 4：独立审查与修复

- 只读审查是否泄露密钥、是否丢失审计数据、是否误改业务 Gate、是否仍有重复框；
- 若有明确 finding，再用新的 `luna_coder` 做限定修复；
- 最终运行完整离线测试、编译、`git diff --check` 和紧凑输出模拟。
