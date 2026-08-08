# DeepSeek 投资研究 Crew 配置设计

## 目标

把当前 CrewAI 自动生成的内容创作模板替换为项目预期的最小可运行 Crew 配置：3 个 Crew、5 个 Agent、5 个 Task，统一接入环境变量中的 DeepSeek API，并为后续 Flow、确定性计算和验证模块保留清晰边界。

## 范围

本次只实现 Crew 的配置和最小 Python 编排骨架：

- `Request Parser Crew`：1 个 Agent、1 个 Task。
- `Analysis Crew`：3 个 Agent、3 个 Task。
- `Report Crew`：1 个 Agent、1 个 Task。
- 每个 Agent 的 YAML 配置使用 `deepseek/deepseek-v4-flash`。
- API Key 从 `DEEPSEEK_API_KEY` 环境变量读取。
- 可选的 API 地址使用 `DEEPSEEK_BASE_URL`，默认值为 `https://api.deepseek.com/v1`。
- 通过离线测试验证 Crew 数量、Agent/Task 绑定和 DeepSeek 配置，不调用真实 API。
- 删除无关的 `content_crew`、`custom_tool.py` 模板和旧内容生成入口。

本次不实现 SEC 数据访问、公司解析、财务计算、Evidence/Claim 验证、确定性评级、报告验证或完整 Flow。

## 目录与职责

```text
src/stockcrewai/
├── crews/
│   ├── __init__.py
│   ├── request_parser/
│   │   ├── __init__.py
│   │   ├── crew.py
│   │   └── config/
│   │       ├── agents.yaml
│   │       └── tasks.yaml
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── crew.py
│   │   └── config/
│   │       ├── agents.yaml
│   │       └── tasks.yaml
│   └── report/
│       ├── __init__.py
│       ├── crew.py
│       └── config/
│           ├── agents.yaml
│           └── tasks.yaml
├── main.py
└── ...
```

YAML key 使用小写 snake_case，以匹配 `@agent`、`@task` 方法名；显示角色和任务名称使用预期文档中的业务名称。旧 `content_crew` 不保留兼容层，因为它不属于目标架构。

## Agent 约束

所有 Agent 都关闭 delegation 和 code execution，避免配置阶段出现自由自治行为：

- `request_parser_agent`：只识别用户请求中的公司、Ticker 候选、关注方向、语言和投资期限；不查询 SEC、不生成 CIK、不输出数字和评级。默认关闭规划。
- `financial_quality_agent`：只解释已验证的财务 Evidence 和 Calculation；不重新计算、不修改数字、不使用无来源数据。使用 `planning_config` 开启中等强度规划，并限制为 1 次规划尝试。
- `risk_analysis_agent`：只分析带来源的 SEC 风险文本；不搜索互联网、不添加文件外风险、不预测未来。使用与财务质量 Agent 相同的规划约束。
- `valuation_analysis_agent`：只解释已验证估值和反向 DCF 结果；不自行计算、不修改假设、不输出评级或买卖建议。使用与财务质量 Agent 相同的规划约束。
- `report_writer_agent`：只重组已验证 Claims、Verdict 和来源元数据，生成中文 Markdown；不新增数字、Claim、评级或未来预测。默认关闭规划。

本次直接使用 CrewAI 原生 DeepSeek 字符串配置，不额外创建 LLM 工厂或温度抽象；温度等 provider 级参数留给后续需要结构化输出的模型配置任务。

结构化 Pydantic 输出依赖后续 models 模块；本次先在 Task 的 `expected_output` 中固定输出契约，不引用尚不存在的模型类。

## Task 约束

- Request Parser Task 接收 `{request}`，输出 ParsedRequest 字段说明。
- 三个 Analysis Task 接收 `{validated_state}`，分别输出带 `evidence_ids` 和 `calculation_ids` 的 Claims。
- Report Task 接收 `{validated_claims}`、`{deterministic_verdict}`、`{calculation_results}`、`{source_metadata}` 和 `{limitations}`，输出不带围栏的中文 Markdown 报告。
- Analysis Task 不互相修改状态；后续 Flow 通过 Pydantic state 传递已验证数据。
- Report Task 不写文件，避免在配置阶段产生运行时 Artifacts；后续 OutputWriter 负责落盘。

## 环境变量

`.env` 只保存本地密钥和 DeepSeek 连接配置，`.env.example` 只保存占位符：

```dotenv
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
```

不在源码、YAML、日志、测试输出或 Artifacts 中写入真实密钥。测试使用进程内的 dummy key，不发起网络请求。

## 验证

离线测试将实例化三个真实 `CrewBase` 类，并验证：

1. 三个 Crew 分别包含 1、3、1 个 Agent。
2. Agent 和 Task 的 YAML 名称与绑定关系正确。
3. 所有 Agent 使用 DeepSeek provider 和 `deepseek-v4-flash` 模型。
4. API Key 来自环境变量，而不是源码常量。
5. 旧 `content_crew` 不再被入口引用。

验证命令使用仓库现有 `.venv` 的 `uv run --no-sync` 和 Python 标准库 `unittest`，不创建环境、不安装依赖、不调用真实 LLM。
