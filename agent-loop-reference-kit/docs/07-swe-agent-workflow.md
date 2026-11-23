# SWE Agent 工作流

软件工程 agent 是自主 loop 的典型场景，因为它需要读代码、改代码、运行验证、记录决策，并根据错误继续调整。

## 五阶段流程

### 1. Memory Check

开始前先查看：

- `/memories/project_context.md`
- `/memories/patterns/`
- `/memories/decisions/`
- `/memories/current_task.md`

目标是理解项目约束和历史经验。

### 2. Planning

用 `think` 分析任务，再写入 `current_task.md`：

```markdown
# Task: ...

## Plan
- [ ] Step 1
- [ ] Step 2

## Validation
- ...

## Risks
- ...
```

### 3. Execution

按计划逐步执行：

- 读文件和搜索。
- 小范围编辑。
- 运行测试或局部验证。
- 更新 checkbox。
- 记录关键决策。

### 4. Learning

如果发现可复用模式，写入：

- `/memories/patterns/`
- `/memories/decisions/YYYY-MM-DD.md`

不要把所有日志都写进长期记忆，只保留未来会复用的知识。

### 5. Completion Gate

结束前必须调用 `task_status`：

- 完成：说明每个需求如何满足，并附验证证据。
- 未完成：说明阻塞点、已尝试方案和剩余事项。

## 最小工具集

SWE agent 的最小工具集：

- `think`
- `task_status`
- `memory`
- `read_file`
- `write_file`
- `list_directory`
- `search_files`
- `bash_execute`

`bash_execute` 和 `write_file` 应受 workspace 限制，并在生产环境中加审批。

## 适配到新项目

复制本参考包后，先改三处：

1. `prompts/swe_system_prompt.md`
2. `memory_template/project_context.md`
3. 工具的 workspace 路径和审批策略

