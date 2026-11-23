# 编排可靠性模式

## Append-Only State

传统“读取-修改-写回”在多个 Agent 并发时会丢失更新。

推荐做法：

- 状态以版本日志形式追加（Append-only），不做原地 UPDATE。
- 每个 Agent 生成不可变的状态版本，交给调度器验证后写入。
- 调度器是唯一的状态写入者。

## Data Contract

每个 Agent 输出必须符合预定义 schema。调度器在接收前验证：

- 字段完整性
- 类型正确性
- 业务规则（例如 status 枚举值）

不符合 contract 的输出被拒绝，触发重试或升级。

## Circuit Breaker（断路器）

当某个 Agent 或工具连续失败时：

1. 记录失败次数。
2. 超过阈值后“打开”断路器，暂停调用。
3. 等待冷却期后尝试“半开”状态。
4. 成功则关闭断路器，失败则继续打开。

防止级联故障和资源浪费。

## Saga / Compensating Transaction

每个 Agent 有两个方法：

- `execute`：执行工作
- `compensate`：回滚或撤销

协调器维护已成功执行的 Agent 列表。如果某步失败，反向遍历列表，为每个 Agent 调用 compensate，回到初始状态。

关键原则：Agent 之间不直接互调，所有协调通过调度器。

## 调度器作为单一事实来源

调度器负责：

- 维护 shared state
- 选择下一个 Agent
- 验证 Data Contract
- 记录 trace
- 触发补偿
- 控制 retry 和 escalation

这是获得控制权、可观测性和回滚能力的基础。

## 故障处理策略

| 故障类型 | 策略 |
| --- | --- |
| Schema 错误 | 重试（带上次错误信息） |
| 工具超时 | 重试或 fallback |
| 验证失败 | 生成修复任务或升级人工 |
| 高风险动作 | 阻断并 escalate |
| 连续 N 次失败 | 打开断路器 |
