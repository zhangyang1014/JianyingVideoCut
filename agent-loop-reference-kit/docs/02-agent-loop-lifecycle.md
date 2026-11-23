# Agent Loop 生命周期

一次 agent run 可以拆成七个阶段。

## 1. Normalize Task

把用户输入规范化成 `UserMessage`。如果是多轮会话，要把上次的 `AgentContext` 带进来，而不是修改全局状态。

关键原则：agent 实例可以复用，但每次 run 的上下文应当隔离。

## 2. Prepare Context

准备发给 LLM 的消息：

- system instructions
- memory context
- previous messages
- current user task
- planning hook 注入的任务规划要求

上下文过长时，用 sliding window、head-tail 或摘要压缩策略控制长度。

## 3. Model Call

调用模型时带上工具 schema。模型返回两类结果：

- 普通 assistant 文本：可能代表完成。
- tool calls：代表模型希望行动。

## 4. Tool Execution

执行工具时要做四件事：

- 校验工具名是否存在。
- 校验参数是否符合 schema。
- 检查是否需要人工审批。
- 捕获异常并以工具结果形式回填。

多个独立工具调用可以并发执行，但文件写入、shell 命令、数据库修改这类副作用工具应谨慎串行。

## 5. Observe

把工具结果追加为 `ToolMessage`。模型不能直接“知道”工具执行结果，必须通过消息回填。

这一步是 agent loop 的关键：观察结果改变下一轮模型推理。

## 6. Continue Or Stop

如果工具执行后需要模型总结或继续推理，就回到 model call。

如果模型没有工具调用，则进入完成检查：

- 是否满足明确目标。
- 是否调用了 `task_status`。
- 是否仍有未完成 todo。
- 是否达到最大轮次、最大 token、超时或失败上限。

## 7. Persist

可选地持久化：

- 完整消息轨迹
- 使用量统计
- 决策日志
- 可复用模式
- 当前任务状态

持久化不是 loop 的必要条件，但对长期 agent 和项目内自主工作非常重要。

