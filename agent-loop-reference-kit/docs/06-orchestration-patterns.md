# 多 Agent 编排模式

多 agent 编排是在稳定单 agent loop 之上的扩展。不要用多个 agent 掩盖单个 agent 的状态和工具边界问题。

## Universal Orchestration Loop

一个编排器通常包含：

1. 共享消息池。
2. 选择下一个 agent。
3. 为该 agent 准备上下文。
4. 执行 agent。
5. 更新共享状态。
6. 检查终止条件。

## Round Robin

固定顺序轮流执行，例如 writer -> reviewer -> writer。

适合：

- 角色分工稳定。
- 需要批评和修改循环。
- 不希望 LLM 自己决定调度。

缺点：

- 容易让不需要发言的 agent 也发言。
- 对动态任务适应性弱。

## AI-Driven Selection

让一个 LLM 根据上下文选择下一个 agent。

适合：

- 角色多。
- 下一步不确定。
- agent 能力差异明显。

风险：

- 调度本身会消耗 token。
- 可能反复选择同一个 agent。
- 需要 fallback 和最大轮次。

## Plan-Based Orchestration

先生成计划，再按步骤分配 agent，执行后评估步骤完成情况。

适合：

- 复杂研究、写作、代码迁移。
- 希望可解释地追踪步骤进度。
- 需要失败重试和局部 replan。

## 什么时候不要上多 Agent

如果只有一个工具执行者，或者任务只需要简单问答，多 agent 会增加成本和不确定性。

优先把单 agent 的 memory、tools、termination、completion check 做稳，再考虑编排。

## Choreography vs Orchestration

| 维度 | Choreography（编排） | Orchestration（调度） |
| --- | --- | --- |
| 协调方式 | Agent 通过事件自主协调 | 中央协调器管理工作流 |
| 结构 | 去中心化 | 集中式 |
| 适合 | 简单工作流 + 高自主性 | 复杂工作流 + 低自主容忍 |
| 风险 | 调试难、事件重复消费 | 协调器成为瓶颈 |

复杂工作流 + 高自主性需求时，用混合模式（例如 Saga 补偿 + 中央调度器）。

## 状态维护原则

- **Append-only**：状态以版本日志追加，不要原地覆盖。避免多 agent 并发写同一记录导致丢失更新。
- **Data Contract**：每个 agent 输出必须符合 schema，调度器验证后才接受。
- **单一事实来源**：调度器维护 shared state，agent 之间不直接互调。

可靠性模式详见 `docs/11-orchestration-reliability.md`。

