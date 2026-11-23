# 企业级 Agent 角色 Prompt 模式

本 kit 在 `prompts/roles/` 下提供常用角色的 system prompt 模板。所有角色输出建议使用 JSON，便于调度器解析和验证。

## 核心角色

| 角色 | 职责 | Prompt 文件 |
| --- | --- | --- |
| Orchestrator | 理解目标、拆任务、分配 Worker、汇总结果 | `prompts/roles/orchestrator.md` |
| Planner | 拆阶段、定义 milestone 和验收标准 | `prompts/roles/planner.md` |
| Worker | 执行单一明确任务、调用工具、返回结构化结果 | `prompts/roles/worker.md` |
| Validator | 独立检查输出是否满足 validation contract | `prompts/roles/validator.md` |

## 支撑角色

| 角色 | 职责 | Prompt 文件 |
| --- | --- | --- |
| Memory Manager | 判断哪些信息该写入哪层 memory | `prompts/roles/memory_manager.md` |
| Handoff Writer | 生成结构化交接文档 | `prompts/roles/handoff_writer.md` |
| Human Escalation | 判断是否需要人工介入 | `prompts/roles/human_escalation.md` |
| Evaluator | 评估 Agent 系统输出质量 | `prompts/roles/evaluator.md` |

## 通信模式

| 模式 | 定义 | 适合场景 |
| --- | --- | --- |
| Delegation | 一个 Agent 把子任务交给另一个 | 主任务拆解、专项分析 |
| Creator-Verifier | 一个生成，另一个独立验证 | 文案审核、代码 review |
| Direct Communication | 两个 Agent 直接问答 | 局部咨询、快速确认 |
| Negotiation | 多 Agent 协调冲突目标 | 效率 vs 质量、收益 vs 风险 |
| Broadcast | 全局信息同步 | 策略变更、系统故障 |
| Handoff | 阶段完成后结构化交接 | 多阶段任务、人工介入 |

## 使用方式

1. 复制 `prompts/roles/` 下对应角色的 prompt。
2. 替换业务占位符（目标、约束、工具列表等）。
3. 确保 Agent 输出符合 `schemas/` 下对应 JSON Schema。
4. 在 Orchestrator 的 route policy 中配置允许的消息模式。

## MVP 最小角色集

第一版只需 4 个角色：

```
Orchestrator + Worker + Validator + Handoff Writer
```

第二阶段再加 Memory Manager、Human Escalation、Evaluator。
