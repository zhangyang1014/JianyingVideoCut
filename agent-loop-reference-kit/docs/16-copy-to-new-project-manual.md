# 复制到其他项目的操作手册

本文说明如何把 `agent-loop-reference-kit/` 复制到其他项目中，作为 Agent 架构知识库、提示词模板和参考代码。

## 适用目标

适合这些场景：

- 想在新项目中引入 Agent 自主 loop。
- 想快速搭建 SWE Agent、Research Agent、客服 Agent 或内部工具 Agent。
- 想把 Agent prompt、schema、memory、evals 做成工程规范。
- 想让 AI 编程助手基于统一知识库设计 Agent 系统。

不适合这些场景：

- 直接当生产框架使用。
- 不做安全边界就开放 shell、数据库、外部 API。
- 一开始就做复杂多 Agent 平台。

## 推荐复制方式

在源项目中复制整个文件夹：

```bash
cp -R agent-loop-reference-kit /path/to/target-project/
```

复制后目标项目结构类似：

```text
target-project/
  agent-loop-reference-kit/
  ...
```

建议先保留原文件夹名，不要拆散。等你确定哪些内容长期使用后，再迁移到目标项目自己的 `agent/`、`docs/`、`schemas/` 目录。

## 第一步：让 AI 先读迁移提示词

复制 `prompts/copy_kit_to_project_prompt.md` 的内容给 AI 编程助手。

填入：

- 项目名称
- 项目目标
- 技术栈
- 想让 Agent 完成的任务类型
- 可用工具
- 安全边界
- 成功标准

让 AI 先做只读分析，不要立刻修改项目。

## 第二步：选择阅读路径

### Path A：基础 Agent Loop

适合：先跑通一个单 Agent。

阅读：

1. `docs/01-core-handbook.md`
2. `docs/05-autonomous-loop-blueprint.md`
3. `docs/08-how-to-design-an-agent.md`
4. `examples/01_minimal_streaming_agent.py`

### Path B：企业级多 Agent

适合：需要 Orchestrator、Worker、Validator、Handoff、Schema。

阅读：

1. `docs/10-enterprise-agent-system.md`
2. `docs/09-role-prompt-patterns.md`
3. `prompts/roles/`
4. `schemas/`
5. `docs/14-production-readiness-checklist.md`

### Path C：Memory、编排和评测

适合：需要长期记忆、可靠编排、evals。

阅读：

1. `docs/12-memory-rag-state.md`
2. `docs/06-orchestration-patterns.md`
3. `docs/11-orchestration-reliability.md`
4. `docs/13-runtime-harness.md`
5. `docs/15-evals-system.md`

## 第三步：选择融合深度

### 方案 1：只作为知识库

适合早期调研。

做法：

- 保留 `agent-loop-reference-kit/`。
- 不复制源码到项目运行目录。
- 只让 AI 阅读 docs、prompts、schemas。

优点：安全、低成本。

### 方案 2：引入轻量单 Agent Loop

适合 MVP。

做法：

- 复制或引用 `src/agent_loop_ref/`。
- 从 `examples/01_minimal_streaming_agent.py` 开始。
- 只开放低风险工具。
- 使用 `TaskStatusTool` 和 `CompletionCheckHook`。

建议目标结构：

```text
target-project/
  agent/
    src/agent_loop_ref/
    prompts/
    memory_template/
    examples/
```

### 方案 3：企业级多 Agent

适合正式系统。

做法：

- 复制 `prompts/roles/`。
- 复制 `schemas/`。
- 建立 `route_policy`、`trace_log`、`validation_contract`。
- 先实现 Orchestrator + Worker + Validator + Handoff Writer。

建议目标结构：

```text
target-project/
  agent-system/
    agents/
    schemas/
    tools/
    policies/
    state/
    logs/
    tests/
    evaluation/
```

## 第四步：定制项目上下文

复制 `memory_template/` 到目标项目，例如：

```text
target-project/
  agent_memory/
    project_context.md
    current_task.md
    patterns/
    decisions/
```

然后填写 `project_context.md`：

- 项目目标
- 技术栈
- 目录结构
- 常用命令
- 测试命令
- 禁止修改的文件
- 高风险命令
- 外部系统审批规则

## 第五步：定制 Prompt

优先改这几个：

- `prompts/swe_system_prompt.md`
- `prompts/memory_policy.md`
- `prompts/completion_check_prompt.md`

如果做多 Agent，再改：

- `prompts/roles/orchestrator.md`
- `prompts/roles/worker.md`
- `prompts/roles/validator.md`
- `prompts/roles/handoff_writer.md`

定制原则：

- 明确“负责什么”和“不负责什么”。
- 工具列表要最小化。
- 输出格式要结构化。
- 高风险动作必须人工审批。
- 不能只靠 prompt 控制安全，要配合代码权限。

## 第六步：选择 Schema

MVP 阶段不需要全部 schema。

推荐顺序：

1. `agent_response.schema.json`
2. `tool_call.schema.json`
3. `validation_contract.schema.json`
4. `validation_result.schema.json`
5. `trace_log.schema.json`
6. `memory_item.schema.json`

多 Agent 后再引入：

- `agent_message.schema.json`
- `route_policy.schema.json`
- `handoff.schema.json`
- `human_escalation.schema.json`
- `eval_case.schema.json`

## 第七步：运行基础验证

如果目标项目使用 Python，可以先跑离线冒烟测试：

```bash
cd agent-loop-reference-kit
PYTHONPATH=src python3 examples/compatibility_smoke_test.py
```

如果要跑 OpenAI 示例：

```bash
cd agent-loop-reference-kit
OPENAI_API_KEY=... PYTHONPATH=src python3 examples/01_minimal_streaming_agent.py
```

如果目标项目不是 Python，也可以只使用 docs、prompts、schemas，把 `src/` 当参考实现。

## 第八步：设置安全边界

上线前至少确认：

- 文件工具只能访问 workspace。
- shell 工具有超时、工作目录和审批。
- 写文件、删文件、数据库写入、外部 API 修改需要人工确认。
- memory 不保存密钥、隐私和未经确认的敏感判断。
- 每次 run 有最大轮次、最大 token 或超时。
- Agent 结束前必须有 `task_status` 或 validation result。

参考：

- `docs/04-termination-and-safety.md`
- `docs/14-production-readiness-checklist.md`

## 第九步：建立评测集

复制并改造：

- `schemas/eval_case.schema.json`
- `prompts/roles/evaluator.md`
- `docs/15-evals-system.md`

至少准备 5 类 case：

- 正常案例
- 边界案例
- 高风险案例
- 对抗案例
- 回归案例

每次修改 prompt、tool、policy 后跑回归。

## 第十步：保留授权和来源说明

复制时保留：

- `LICENSES/`
- `NOTICE.md`
- `THIRD_PARTY_NOTICES.md`

如果你把 kit 内容拆到目标项目其他目录，也要保留这些声明。

## 推荐最小落地顺序

```text
第 1 天：只读分析目标项目，选择 Path A/B/C
第 2 天：填写 project_context.md 和 memory_policy
第 3 天：跑通单 Agent minimal loop
第 4 天：加入工具权限和 task_status
第 5 天：加入 validation contract 和 trace
第 6 天：加入 eval cases
第 7 天：再考虑多 Agent orchestration
```

## 常见错误

- 一开始复制全部角色，导致系统太重。
- 没有明确工具权限，直接开放 shell。
- 把 RAG 当 memory，用向量库保存所有状态。
- 没有 Validator，让 Creator 自己批准自己。
- 没有 evals，prompt 改坏了也不知道。
- 没有 trace，线上失败无法复盘。

## 最小成功标准

第一次迁移成功，不是“搭好完整平台”，而是：

- 一个 Agent 能读项目上下文。
- 能按计划调用低风险工具。
- 能在结束前声明 `task_status`。
- 能留下 trace 或 handoff。
- 能用一个 eval case 验证结果。

