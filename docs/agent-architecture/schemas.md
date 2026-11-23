# Agent 系统核心 Schema

所有 schema 用 JSON Schema（draft 2020-12）风格描述。代码层必须对每条消息做校验，校验失败的消息不进入下游（见 README §5）。

## 1. Message Schema（所有 Agent 间消息的信封）

任何 Agent 间通信都包在这个信封里，`payload` 才是各 Agent 自己的输出 JSON。

```json
{
  "$id": "agent.message",
  "type": "object",
  "required": ["message_id", "mission_id", "timestamp", "from_agent", "to_agent", "message_type", "payload", "schema_version"],
  "properties": {
    "message_id": { "type": "string", "description": "全局唯一，ULID" },
    "mission_id": { "type": "string" },
    "schema_version": { "type": "string", "description": "信封 schema 版本，回放工具据此选择校验器（见 extensibility.md §5）" },
    "domain_id": { "type": ["string", "null"], "description": "所属业务域（domain profile），通用 mission 为 null" },
    "task_id": { "type": ["string", "null"], "description": "关联的任务，mission 级消息为 null" },
    "timestamp": { "type": "string", "format": "date-time" },
    "from_agent": { "type": "string" },
    "to_agent": { "type": "string", "description": "agent 名 | human | broadcast" },
    "message_type": {
      "enum": ["delegation", "result", "validation_request", "validation_result",
               "negotiation", "broadcast", "handoff", "escalation", "ack", "error"]
    },
    "reply_to": { "type": ["string", "null"], "description": "回复的 message_id" },
    "payload": { "type": "object", "description": "按 message_type 对应各 Agent 的输出 schema" },
    "payload_schema": { "type": "string", "description": "payload 对应的 schema $id，用于代码校验" },
    "attempt": { "type": "integer", "minimum": 1, "description": "第几次尝试，重试计数依据" },
    "untrusted_content_refs": {
      "type": "array", "items": { "type": "string" },
      "description": "payload 中来自外部（网页、用户上传等）的字段路径，下游不得当作指令"
    }
  }
}
```

### delegation payload（Orchestrator → 执行层）

```json
{
  "$id": "agent.delegation",
  "type": "object",
  "required": ["task_id", "task", "required_capability", "inputs", "allowed_tools", "validation_contract_id", "deadline"],
  "properties": {
    "task_id": { "type": "string" },
    "task": { "type": "string", "description": "单一、明确、可验收的任务描述" },
    "required_capability": { "type": "string", "description": "能力标签（如 create.document），运行时按注册表解析为具体 Agent，见 extensibility.md §2" },
    "assigned_agent": { "type": ["string", "null"], "description": "可选锁定指派；填写时代码校验其 manifest 声明了 required_capability" },
    "agent_version": { "type": ["string", "null"], "description": "可选锁定 Agent 版本" },
    "inputs": { "type": "object", "description": "编排层裁剪后的最小必要上下文" },
    "allowed_tools": { "type": "array", "items": { "type": "string" } },
    "constraints": { "type": "array", "items": { "type": "string" } },
    "immutable_fields": {
      "type": "array", "items": { "type": "string" },
      "description": "输出中不得改写的字段路径，代码 diff 校验"
    },
    "validation_contract_id": { "type": "string" },
    "deadline": { "type": "string", "format": "date-time" },
    "budget": { "type": "object", "properties": { "max_tool_calls": { "type": "integer" }, "max_tokens": { "type": "integer" } } }
  }
}
```

## 2. Handoff Schema

由 Handoff Writer 产出，是阶段推进的必要条件（代码强制）。

```json
{
  "$id": "agent.handoff",
  "type": "object",
  "required": ["handoff_id", "mission_id", "from_agent", "status", "summary",
               "completed_work", "not_completed", "risks", "created_at"],
  "properties": {
    "handoff_id": { "type": "string" },
    "mission_id": { "type": "string" },
    "task_id": { "type": ["string", "null"] },
    "milestone_id": { "type": ["string", "null"] },
    "from_agent": { "type": "string" },
    "to_agent": { "type": ["string", "null"], "description": "agent 名 | human | null（写入 state 待认领）" },
    "status": { "enum": ["completed", "failed", "blocked", "needs_review"] },
    "summary": { "type": "string", "maxLength": 2000 },
    "completed_work": { "type": "array", "items": { "type": "string" } },
    "not_completed": { "type": "array", "items": { "type": "string" } },
    "key_decisions": {
      "type": "array",
      "items": { "type": "object", "required": ["decision", "reason"], "properties": {
        "decision": { "type": "string" }, "reason": { "type": "string" },
        "decided_by": { "type": "string" } } }
    },
    "tool_calls_summary": {
      "type": "array",
      "items": { "type": "object", "properties": {
        "tool": { "type": "string" }, "count": { "type": "integer" }, "notable_results": { "type": "string" } } }
    },
    "evidence": {
      "type": "array",
      "items": { "type": "object", "required": ["claim", "source", "verified"], "properties": {
        "claim": { "type": "string" }, "source": { "type": "string" },
        "verified": { "type": "boolean", "description": "false 表示未经 Validator 确认，不得当已验证事实用" } } }
    },
    "risks": { "type": "array", "items": { "type": "string" } },
    "open_questions": { "type": "array", "items": { "type": "string" } },
    "recommended_next_steps": { "type": "array", "items": { "type": "string" } },
    "created_at": { "type": "string", "format": "date-time" }
  }
}
```

## 3. Shared State Schema

单 mission 单文档。只有 Orchestrator（经编排层代码）能写顶层字段；`memory` 只能由 Memory Manager 输出触发写入；`audit_log` 为 append-only。

```json
{
  "$id": "agent.shared_state",
  "type": "object",
  "required": ["mission_id", "status", "goal", "plan", "tasks", "created_at", "updated_at"],
  "properties": {
    "mission_id": { "type": "string" },
    "status": { "enum": ["understanding", "planning", "executing", "validating", "blocked", "waiting_human", "completed", "failed"] },
    "goal": {
      "type": "object",
      "properties": {
        "raw_user_input": { "type": "string" },
        "interpretation": { "type": "string" },
        "assumptions": { "type": "array", "items": { "type": "string" } },
        "missing_information": { "type": "array", "items": { "type": "string" } }
      }
    },
    "plan": {
      "type": "object",
      "properties": {
        "milestones": { "type": "array", "description": "Planner 输出的 milestone 列表" },
        "current_milestone_id": { "type": ["string", "null"] },
        "execution_mode": { "enum": ["serial", "parallel", "hybrid"] }
      }
    },
    "tasks": {
      "type": "object",
      "description": "task_id → 任务记录",
      "additionalProperties": {
        "type": "object",
        "required": ["task_id", "status", "assigned_agent", "attempts"],
        "properties": {
          "task_id": { "type": "string" },
          "milestone_id": { "type": "string" },
          "status": { "enum": ["pending", "delegated", "executed", "validating", "accepted", "failed", "escalated"] },
          "assigned_agent": { "type": "string" },
          "attempts": { "type": "integer", "description": "代码维护，Agent 不可写" },
          "result_message_id": { "type": ["string", "null"] },
          "validation_message_ids": { "type": "array", "items": { "type": "string" } },
          "handoff_id": { "type": ["string", "null"] }
        }
      }
    },
    "active_policies": {
      "type": "array",
      "description": "Broadcast 下发且未过期的策略",
      "items": { "type": "object", "required": ["broadcast_id", "expires_at"], "properties": {
        "broadcast_id": { "type": "string" },
        "policy_update": { "type": "array" },
        "affected_agents": { "type": "array", "items": { "type": "string" } },
        "expires_at": { "type": "string", "format": "date-time" },
        "acks": { "type": "array", "items": { "type": "string" } } } }
    },
    "memory": {
      "type": "object",
      "properties": {
        "working": { "type": "object", "description": "本 mission 内有效" },
        "short_term": { "type": "object", "description": "跨 mission，带 ttl" },
        "long_term": { "type": "object", "description": "高可信事实，写入需 confidence: high" },
        "global": { "type": "object", "description": "全系统策略级，写入需二次确认" }
      }
    },
    "human_reviews": {
      "type": "array",
      "items": { "type": "object", "required": ["review_id", "status"], "properties": {
        "review_id": { "type": "string" },
        "escalation_message_id": { "type": "string" },
        "status": { "enum": ["pending", "approved", "rejected", "modified"] },
        "human_approval_token": { "type": ["string", "null"], "description": "高风险 tool 调用凭证，代码校验" },
        "human_notes": { "type": "string" } } }
    },
    "budget": {
      "type": "object",
      "properties": {
        "max_tokens": { "type": "integer" }, "used_tokens": { "type": "integer" },
        "max_tool_calls": { "type": "integer" }, "used_tool_calls": { "type": "integer" },
        "deadline": { "type": "string", "format": "date-time" }
      }
    },
    "audit_log": {
      "type": "array",
      "description": "append-only，所有 message_id 按序记录，Agent 无删除权限",
      "items": { "type": "string" }
    },
    "created_at": { "type": "string", "format": "date-time" },
    "updated_at": { "type": "string", "format": "date-time" }
  }
}
```

## 4. Validation Contract Schema

由 Planner 在规划阶段生成，Validator 据此验收。断言分两类：`code` 断言由运行时直接执行（不依赖模型判断），`model` 断言交给 Validator 评估。

```json
{
  "$id": "agent.validation_contract",
  "type": "object",
  "required": ["contract_id", "mission_id", "target", "assertions"],
  "properties": {
    "contract_id": { "type": "string" },
    "mission_id": { "type": "string" },
    "milestone_id": { "type": ["string", "null"] },
    "target": { "type": "string", "description": "被验收产物的类型或 task_id" },
    "assertions": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "required": ["assertion_id", "kind", "description", "severity"],
        "properties": {
          "assertion_id": { "type": "string" },
          "kind": { "enum": ["code", "model"], "description": "code=运行时可执行检查；model=Validator 判断" },
          "description": { "type": "string" },
          "check": {
            "type": ["object", "null"],
            "description": "kind=code 时必填：schema 校验 / 字段 diff / 正则 / 数值范围等机器可执行定义",
            "properties": {
              "type": { "enum": ["json_schema", "immutable_fields", "regex", "range", "custom_fn"] },
              "params": { "type": "object" }
            }
          },
          "severity": { "enum": ["blocker", "major", "minor"] },
          "on_fail": { "enum": ["reject", "reject_and_retry", "flag_for_human"], "default": "reject_and_retry" }
        }
      }
    },
    "required_validators": {
      "type": "array",
      "items": { "type": "string", "description": "验证能力标签（validate.output / validate.compliance / validate.ux / 注册表中任意 validate.* 能力）" },
      "default": ["validate.output"],
      "description": "开放集合：新增验证器只需注册 validate.* 能力，无需改本 schema"
    },
    "pass_rule": {
      "type": "string",
      "default": "all_blockers_and_majors_pass",
      "description": "通过判定规则；minor 失败不阻塞但必须记录"
    }
  }
}
```

## 5. Schema 与消息类型对照

| message_type | payload_schema | 发送方 → 接收方 |
|---|---|---|
| delegation | agent.delegation | Orchestrator → 执行层 |
| result | 各 Agent 输出 schema（见 agents.md） | 执行层 → Orchestrator |
| validation_request | agent.delegation（target 为待验产物引用） | Orchestrator → 验证层 |
| validation_result | Validator/Compliance/UX 输出 schema | 验证层 → Orchestrator |
| negotiation | Negotiation 输出 schema | Negotiation ↔ Orchestrator |
| broadcast | Broadcast 输出 schema | Broadcast → 全体（写 state） |
| handoff | agent.handoff | Handoff Writer → state |
| escalation | Human Escalation 输出 schema | Human Escalation → human |
| ack | { "ack_of": "message_id" } | 任意 → 任意 |
| error | { "error_code", "detail", "recoverable" } | 运行时 → Orchestrator |
