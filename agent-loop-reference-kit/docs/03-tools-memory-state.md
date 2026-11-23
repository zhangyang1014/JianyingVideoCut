# 工具、记忆与状态

自主 agent 的质量很大程度取决于工具边界和状态设计。

## 工具契约

每个工具都应有稳定契约：

- `name`：唯一、短、语义清晰。
- `description`：说明何时使用，而不只是说明做什么。
- `parameters`：JSON schema，字段越明确越好。
- `execute`：返回结构化 `ToolResult`。
- `approval_mode`：是否需要人类批准。

好的工具不是万能函数，而是小而清晰的行动接口。

## 工具分类

建议把工具分成四类：

- 只读工具：读文件、搜索、查数据库、查网页。
- 可逆写工具：写草稿、生成临时文件、更新 memory。
- 高风险工具：shell、删除文件、数据库写入、外部 API 修改。
- 元认知工具：`think`、`todo_write`、`task_status`。

高风险工具默认应需要审批或沙箱。

## 记忆分层

推荐文件型 memory 结构：

```text
memories/
  project_context.md
  current_task.md
  patterns/
  decisions/
```

含义：

- `project_context.md`：项目长期背景。
- `current_task.md`：当前任务计划和进度。
- `patterns/`：可复用方案、常见坑、工具使用模式。
- `decisions/`：按日期记录为什么这么做。

## 状态模型

不要只依赖 prompt 管状态。建议在代码中维护一个显式状态：

```python
state = {
    "goal": "...",
    "plan": [...],
    "completed_steps": [...],
    "failed_attempts": 0,
    "requires_approval": False,
    "finish_reason": None,
}
```

LLM 可以参与更新状态，但最终状态应由代码验证和持久化。

## Task Status

`task_status` 是自主 loop 的完成闸门。要求 agent 在结束前显式声明：

- `status`: `complete` 或 `incomplete`
- `rationale`: 为什么可以结束
- `requirements_met`: 已满足的需求
- `requirements_pending`: 未满足的需求

如果没有这个完成信号，外层 loop 不应轻易相信“我完成了”。

## 记忆类型速查

| 类型 | 存什么 | 典型存储 |
| --- | --- | --- |
| Working Memory | 当前任务临时上下文 | prompt context / state object |
| Short-term Memory | 一次 run 内的步骤历史 | agent_state / trace / checkpoint |
| Long-term Memory | 跨任务保留的信息 | 数据库 / 文件 / 向量库 |
| Episodic Memory | “发生过什么” | 事件表 / 日志 |
| Semantic Memory | 长期有效的知识 | 知识库 / 策略库 |

详细分层见 `docs/12-memory-rag-state.md`。

