# Agent 架构核心手册

这个参考包关注一个问题：如何把 LLM 从“单次问答”组织成可以自主推进任务的 agent loop。

## 核心判断

Agent 不是“更长的 prompt”。Agent 是一个受控执行系统，至少包含：

- 目标：本轮要解决什么，什么算完成。
- 状态：已有消息、记忆、计划、工具结果、失败历史。
- 行动：模型可以调用哪些工具，以及工具的权限边界。
- 观察：工具结果如何回填给模型。
- 判断：是否完成、是否继续、是否重试、是否需要人类确认。

## 最小 Agent Loop

最小 loop 是：

1. 把用户任务和系统指令转成模型消息。
2. 调用模型。
3. 如果模型返回文本，输出并结束。
4. 如果模型请求工具，执行工具。
5. 把工具结果追加回上下文。
6. 继续调用模型，直到完成或达到上限。

这个参考包中的 `src/agent_loop_ref/minimal/streaming_agent.py` 就是这一层。

## 自主 Loop

自主 loop 在最小 loop 上增加四个控制层：

- 计划层：先分解任务，不让模型直接盲目行动。
- 记忆层：读取项目背景、历史决策、可复用模式。
- 评估层：每轮后判断目标是否真的推进。
- 安全层：限制工具权限、预算、失败次数和人工审批点。

## 推荐工程边界

复制到新项目时，不建议一开始就做完整多 agent 框架。更稳妥的顺序是：

1. 单 agent 自主 loop。
2. 文件型 memory 和 todo/task_status。
3. 工具权限与审批。
4. 任务完成检查 hook。
5. 多 agent orchestration。
6. 评估与可观测性。

## 什么时候用 Workflow，什么时候用 Agent Loop

Workflow 适合步骤固定、可预测、需要强确定性的任务，例如 ETL、报表生成、固定审批流。

Agent loop 适合路径不固定、需要根据观察结果调整行动的任务，例如代码修改、调研、复杂排障、多轮工具使用。

如果一个任务可以画成稳定 DAG，优先 workflow。如果任务每一步都依赖上一步观察结果，才用 agent loop。

## Agent 与其他模式

| 模式 | 核心特征 | 适用场景 |
| --- | --- | --- |
| Agent | 给定目标，自主决定调用哪些能力完成任务 | 路径不确定、需要多轮工具调用 |
| Skill | 声明“我能做某件事” | 能力注册、工具封装 |
| Workflow | 按固定流程执行 | 步骤稳定、可预测 |
| 自动化程序 | 按规则执行 | 逻辑完全确定 |

## Agentic Engineering 公式

```
Agentic Engineering = Agent + Tool + Context + Eval + Guardrail + Human-in-the-loop + Data Loop
```

## 三层架构

| 层 | 内容 | 参考文档 |
| --- | --- | --- |
| 设计层 | Agent 规格、Schema、Prompt、Eval 设计 | `docs/08-how-to-design-an-agent.md` |
| 运行层 | Harness：Tool Registry、Context Builder、Guardrails、Loop、Verify、Trace | `docs/13-runtime-harness.md` |
| 业务层 | 具体功能 Agent（SWE、Research、客服等） | `docs/07-swe-agent-workflow.md` |

## 关键概念区分

| 概念 | 含义 |
| --- | --- |
| Trace | 全量过程录像，用于审计和 debug |
| Handoff | 结构化交接摘要 |
| Memory | 可复用的经验和上下文 |
| State | 当前任务进度 |
| Knowledge Base | 长期知识仓库（RAG） |

