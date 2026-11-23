# 企业级 Agent 系统架构

## 总体架构

```text
User / Business Goal
  ↓
Orchestrator Agent
  - 理解目标、追问、拆任务、制定计划、分配任务
  ↓
Worker Agents
  - 执行子任务、调用工具、产出结构化结果、写 handoff
  ↓
Validator Agents
  - 检查结果、规则、风险
  ↓
Negotiation / Decision（可选）
  - 处理多目标冲突
  ↓
Tool / Execution Agents
  - 调用外部系统
  ↓
Memory Manager
  - 判断哪些信息要保存
  ↓
Handoff Writer
  - 生成结构化交接单
  ↓
Trace / Logs / Evaluation
  - 记录全过程、支持回放和评估
```

## 核心对象

### Mission

```json
{
  "mission_id": "string",
  "goal": "string",
  "status": "draft | planning | running | validating | completed | failed | paused",
  "constraints": [],
  "success_criteria": []
}
```

### Task

```json
{
  "task_id": "string",
  "mission_id": "string",
  "assigned_agent": "string",
  "objective": "string",
  "status": "pending | running | completed | failed | blocked",
  "validation_required": true
}
```

### Validation Contract

定义“完成”的标准。每个 assertion 指定验证方法和负责人。

### Shared State

所有相关 Agent 可读的任务状态，作为单一事实源。

## MVP 版本

第一版只实现：

1. Orchestrator Agent
2. 一个 Worker Agent
3. 一个 Validator Agent
4. 一个 Handoff Writer Agent
5. Shared State
6. Trace Log

## 进阶版本

第二阶段：Memory Manager、Tool Agent、Human Escalation、Broadcast。

第三阶段：Negotiation、Evaluator、多 Worker 并行、Mission Control 监控、自动回放。

## 相关文档

- 角色 Prompt：`docs/09-role-prompt-patterns.md`
- Schema 定义：`docs/09-enterprise-schemas.md`
- 上线检查：`docs/14-production-readiness-checklist.md`
