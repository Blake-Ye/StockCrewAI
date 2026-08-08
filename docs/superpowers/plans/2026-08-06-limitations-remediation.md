# Limitations Remediation Implementation Plan

**Goal:** 将当前报告中的真实缺口变成可审计的确定性状态，并修复估值、风险 Claim、Verdict 与运行时存储链路。

**Architecture:** Python 负责输入门槛、市场价格 Evidence、历史估值、反向 DCF、验证、风险 Evidence 白名单和确定性 Verdict；CrewAI 只解释已验证输入。历史数据不足时返回结构化 `unavailable`，不使用最新数据冒充历史数据。

**Constraints:** 使用现有 uv 项目和依赖；默认测试离线；不增加 Agent、Crew、SQLite 持久化或市场数据供应商；不删除 rejected Claim、无来源预测和非投资建议声明。

## Tasks

### Task 0: Runtime startup without project persistence

**Files:** `src/stockcrewai/main.py`, `tests/test_crew_configuration.py`

- 在第一个 Crew 构造前关闭 CrewAI 默认任务输出持久化路径对项目运行的阻断。
- 运行时不得在仓库中创建 SQLite 文件；测试保留现有临时隔离。
- 增加回归测试，证明只构造 RequestParserCrew 时不会因默认存储目录不可写而失败。

### Task 1: Market-price provenance and valuation validation

**Files:** `src/stockcrewai/tools/valuation_tool.py`, `tests/test_valuation_tool.py`

- 为市场价格建立稳定 Evidence ID，绑定 ticker、价格、UTC 时间戳、币种和 Yahoo 来源 URL。
- 估值计算的 `input_evidence_ids` 必须包含价格 Evidence ID 与财务 Evidence ID。
- 在价格来源、时间戳、币种、财务单位和输入 Evidence 均有效时，将估值计算标记为 `valid`；否则保留 `unvalidated` 或 `unavailable` 并说明原因。

### Task 2: Historical valuation and reverse DCF

**Files:** 新增最少量的估值工具模块及对应离线测试；不修改 `main.py`

- 历史估值使用历史价格和 point-in-time 财务 Evidence；缺少历史财务快照时返回 typed `unavailable`。
- 反向 DCF 使用 FCF proxy、10 年预测期、场景 `(8%,2%)`、`(9%,2.5%)`、`(10%,3%)`，用 Decimal 二分法求隐含增长率。
- 输出输入 Evidence IDs、参数、迭代次数、残差和收敛状态。

### Task 3: Main orchestration, risk aggregation, and deterministic verdict

**Files:** `src/stockcrewai/main.py`, `src/stockcrewai/crews/report/config/tasks.yaml`, relevant tests

- 汇总 Analysis Crew 的财务、风险和估值三个任务输出，而不是只使用最后一个任务输出。
- 将有文本且元数据完整的 filing Evidence 纳入风险 Claim 白名单。
- 增加版本化确定性政策；数据不足时输出 `overall_rating=insufficient_data`，而不是 `policy_defined=False`。
- 仅当用户明确提供投资期限时进行期限匹配；不得给默认请求注入期限。
- 报告只展示结构化状态，不让 LLM 凭空补出历史估值、反向 DCF 或额外 limitation。

## Verification

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest discover -s tests -q
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m compileall -q src tests
git diff --check
```

真实运行只在用户已配置网络和 API 后执行；结果中的 SEC/Yahoo 外部失败必须与代码验证结果分开报告。
