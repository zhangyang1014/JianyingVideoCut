# 企业级 Schema 参考

本 kit 提供 JSON Schema 模板，作为“企业级外层 schema”。它们不影响 `src/agent_loop_ref/` 中的轻量实现，但可用于工程化落地时的接口契约。

## Schema 清单

| Schema | 文件 | 用途 |
| --- | --- | --- |
| Agent Config | `schemas/agent_config.schema.json` | Agent 注册与配置 |
| Agent Message | `schemas/agent_message.schema.json` | Agent 间通信消息 |
| Agent Response | `schemas/agent_response.schema.json` | Agent 返回结果 |
| Tool Call | `schemas/tool_call.schema.json` | 工具调用记录 |
| Handoff | `schemas/handoff.schema.json` | 结构化交接 |
| Validation Contract | `schemas/validation_contract.schema.json` | 验收标准定义 |
| Validation Result | `schemas/validation_result.schema.json` | 验证结果 |
| Memory Item | `schemas/memory_item.schema.json` | 记忆条目 |
| Trace Log | `schemas/trace_log.schema.json` | 过程日志 |
| Route Policy | `schemas/route_policy.schema.json` | Agent 路由策略 |
| Human Escalation | `schemas/human_escalation.schema.json` | 人工升级 |
| Eval Case | `schemas/eval_case.schema.json` | 评测用例 |

## 与 kit 轻量类型的关系

| kit 轻量类型 | 企业级 Schema | 说明 |
| --- | --- | --- |
| `Message` | `agent_message.schema.json` | kit 版更简单，无 mission_id 等 |
| `AgentResponse` | `agent_response.schema.json` | kit 版无 evidence/risks 等 |
| `ToolResult` | `tool_call.schema.json` | kit 版无 side_effect 等 |
| `AgentContext` | shared state + memory | kit 版无分层 memory |

迁移路径：先用 kit 轻量类型跑通 loop，再逐步对齐企业级 schema。

## 推荐工程目录

```text
agent-system/
  agents/          # 各角色 prompt
  schemas/         # JSON Schema 定义
  tools/           # 工具注册与权限
  policies/        # 路由、安全、重试、预算策略
  state/           # mission_state、shared_state、memory_store
  logs/            # message_log、trace_log、handoffs、validations
  tests/           # agent_tests、regression_tests、safety_tests
  evaluation/      # eval_dataset、eval_results
```

## 使用方式

1. 复制 `schemas/` 到目标项目。
2. 用 JSON Schema validator 校验 Agent 输入输出。
3. 在 Orchestrator 中强制所有 Agent 输出符合 schema。
4. Schema 校验失败时触发 retry 或 escalation。
