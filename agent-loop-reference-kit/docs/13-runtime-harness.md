# Runtime Harness（运行层）

Harness 是套在业务 Agent 外面的运行框架，把“黑盒模型”绑定到稳定环境中。

## 三层架构

```text
第 1 层：设计层
  - Agent 规格、Schema、Prompt、Eval 设计

第 2 层：运行层（Harness）
  - Tool Registry
  - Context Builder
  - Guardrails
  - Agent Loop
  - Verify Step
  - Deterministic Handler
  - Trace

第 3 层：业务层
  - 功能 Agent（SWE、Research、客服等）
```

## Harness 组件

| 组件 | 作用 |
| --- | --- |
| Tool Registry | 注册和管理所有可用工具及权限 |
| Context Builder | 组装 LLM 上下文（memory + state + RAG + history） |
| Guardrails | 限制 Agent 行为的规则（代码硬控制，不靠 prompt） |
| Agent Loop | 核心推理-行动循环 |
| Verify Step | 每步后验证是否成功，失败则刹车 |
| Deterministic Handler | Agent 某步总出错时，用确定性代码替代 LLM 自由发挥 |
| Trace | 记录全过程，支持回放和 debug |

## Deterministic Handler 使用场景

当 Agent 在某个环节反复出错（例如格式化输出、选择错误工具），不要继续让 LLM 自由发挥，而是：

- 用 Python 代码强制执行该步骤
- 通过 hook 注入确定性指令
- 限制 LLM 在该步骤的选择空间

## Verify Step

每轮 agent loop 结束后，运行验证：

- 工具调用是否成功？
- 输出是否符合 schema？
- 是否满足 validation contract？
- 是否需要人工审批？

验证失败时不进入下一轮，而是重试、修复或升级。

## 与当前 kit 的对应关系

| Harness 组件 | kit 中的实现 |
| --- | --- |
| Agent Loop | `src/agent_loop_ref/core/agent.py` |
| Tool Registry | `src/agent_loop_ref/tools/` |
| Context Builder | `Agent._prepare_messages()` + memory 注入 |
| Guardrails | `ApprovalMode`、workspace 限制、termination |
| Verify Step | `CompletionCheckHook`、`TaskStatusTool` |
| Deterministic Handler | `PlanningHook`、`CompletionCheckHook` |
| Trace | `AgentResponse.usage` + messages 列表（可扩展为 trace_log schema） |

## 扩展建议

复制到新项目后，按优先级扩展 Harness：

1. 加 Trace Log 持久化（`schemas/trace_log.schema.json`）
2. 加 Validation Contract 检查
3. 加 Route Policy 控制 agent 间通信
4. 加 Budget Policy 控制 token 成本
