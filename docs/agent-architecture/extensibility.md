# 拓展性与通用性机制

本文件定义让这套 Agent 系统"加东西不改核心"的五个机制：

1. Agent 注册表 + 能力路由：新增/替换 Agent 不改编排代码。
2. Tool 注册表：新增工具只写 manifest，权限和风险由声明决定。
3. Prompt 三层结构：换业务域只写业务层，基座和角色层复用。
4. 版本化规则：schema、prompt、manifest 都带版本，兼容性有明确策略。
5. 运行时适配层：模型、消息、状态存储都是可替换接口。

核心原则：**核心运行时只认识 manifest 和 schema，不认识任何具体 Agent 的名字**。README §3 中的 15 个 Agent 是"标准库"，不是硬编码清单——它们同样通过注册表接入。

## 1. Agent 注册表（Agent Manifest）

每个 Agent 用一份 manifest 声明自己，运行时启动时加载全部 manifest 构建注册表。新增 Agent = 新增一份 manifest + 角色层 prompt + 测试用例，零核心代码改动。

```json
{
  "$id": "agent.manifest",
  "type": "object",
  "required": ["agent_name", "version", "agent_type", "capabilities",
               "input_schema", "output_schema", "prompt_layers", "risk_class"],
  "properties": {
    "agent_name": { "type": "string", "description": "注册表内唯一" },
    "version": { "type": "string", "description": "semver，如 1.2.0" },
    "agent_type": { "enum": ["orchestration", "execution", "validation", "support"] },
    "capabilities": {
      "type": "array", "minItems": 1,
      "items": { "type": "string" },
      "description": "能力标签，点分命名，如 research.web / create.document / validate.compliance"
    },
    "input_schema": { "type": "string", "description": "输入 payload 的 schema $id" },
    "output_schema": { "type": "string", "description": "输出 payload 的 schema $id" },
    "prompt_layers": {
      "type": "object",
      "required": ["role"],
      "properties": {
        "base": { "type": "string", "default": "prompts/base.md", "description": "通用基座，见 agents.md §通用基座" },
        "role": { "type": "string", "description": "角色层 prompt 文件路径" },
        "domain_overlay": { "type": ["string", "null"], "description": "业务层 prompt，由 domain profile 注入" }
      }
    },
    "allowed_tools": {
      "type": "array", "items": { "type": "string" },
      "description": "允许申请的工具上限集合，delegation 只能在此范围内进一步收窄"
    },
    "model_profile": {
      "type": "object",
      "properties": {
        "preferred": { "type": "string", "description": "逻辑模型名，由模型网关解析" },
        "fallback": { "type": "array", "items": { "type": "string" } },
        "temperature": { "type": "number" }
      }
    },
    "risk_class": {
      "enum": ["read_only", "produce_internal", "produce_external", "decision"],
      "description": "决定默认验证链：produce_external 强制走 compliance；decision 类输出默认 requires_validation"
    },
    "independence_requirements": {
      "type": "array", "items": { "type": "string" },
      "description": "必须与哪些 agent_name/capability 使用不同模型上下文，如 Validator 声明 ['create.*']"
    },
    "overrides": {
      "type": "object",
      "properties": {
        "timeout_seconds": { "type": "integer" },
        "max_retries": { "type": "integer", "maximum": 2, "description": "只能收紧，不能放宽全局上限" }
      }
    },
    "enabled": { "type": "boolean", "default": true }
  }
}
```

硬控制（代码层）：

- manifest 加载时校验 `input_schema`/`output_schema` 必须已在 schema 仓库注册。
- `capabilities` 与 `agent_type` 一致性检查（如 `validate.*` 能力只允许 validation 类型声明）。
- `overrides` 只能比全局默认更严格，不能放宽（重试上限、超时上限、预算）。
- 同名 Agent 多版本共存时，路由默认取最高版本，delegation 可锁定版本。

## 2. 能力路由（Capability Routing）

Orchestrator 和 Planner 产出的计划**引用能力，不引用 Agent 名字**。运行时按注册表把能力解析为具体 Agent。

- delegation 中 `required_capability` 为必填，`assigned_agent` 变为可选（留空由运行时解析，填了则视为锁定指派并校验其 manifest 确实声明了该能力）。
- 解析规则（代码实现，按序）：
  1. 过滤 `enabled: true` 且声明了该能力的 Agent。
  2. 过滤满足 `independence_requirements` 的（验证类任务不能路由给产物创建者同上下文的 Agent）。
  3. 多候选时按 domain profile 中的优先级表选择；无配置则取注册顺序第一个并记录决策。
  4. 无候选 → 产生 `error` 消息（`error_code: no_capable_agent`），Orchestrator 决定改计划或升级人工。

好处：换掉某个 Agent 的实现（如把 Research 从网页搜索版换成内部知识库版），计划、prompt、流程图全部不用动。

## 3. Tool 注册表（Tool Manifest）

工具与 Agent 解耦，独立注册。Agent manifest 的 `allowed_tools` 引用这里的 `tool_name`。

```json
{
  "$id": "tool.manifest",
  "type": "object",
  "required": ["tool_name", "version", "description", "params_schema", "risk_level"],
  "properties": {
    "tool_name": { "type": "string" },
    "version": { "type": "string" },
    "description": { "type": "string", "description": "给模型看的工具说明" },
    "params_schema": { "type": "object", "description": "参数 JSON Schema，调用前代码校验" },
    "returns_schema": { "type": "object" },
    "risk_level": {
      "enum": ["read_only", "write_internal", "write_external", "irreversible"],
      "description": "write_external 及以上默认要求 human_approval_token，见 README §5"
    },
    "requires_human_approval": { "type": "boolean", "description": "显式覆盖；只能在 risk_level 默认值基础上收紧" },
    "allowed_agent_types": {
      "type": "array",
      "items": { "enum": ["orchestration", "execution", "validation", "support"] },
      "description": "如验证类工具不开放给执行类 Agent"
    },
    "rate_limit": { "type": "object", "properties": { "per_mission": { "type": "integer" }, "per_minute": { "type": "integer" } } },
    "marks_output_untrusted": {
      "type": "boolean",
      "description": "true 表示返回内容来自外部世界（网页、用户输入），运行时自动写入消息的 untrusted_content_refs"
    }
  }
}
```

硬控制（代码层）：

- 调用前 params_schema 校验；`risk_level ≥ write_external` 无 token 直接失败。
- `marks_output_untrusted` 由运行时自动执行，不依赖 Agent 自觉标注。
- Agent 实际可用工具 = manifest.allowed_tools ∩ delegation.allowed_tools ∩ 该 tool 的 allowed_agent_types，三层白名单取交集。

## 4. Prompt 三层结构

每个 Agent 的最终 system prompt 由运行时拼装，三层各自独立演进：

```text
┌─ 第 1 层：通用基座（base）────────────── 全系统一份，见 agents.md §通用基座
│   JSON 输出契约、范围约束、untrusted 数据规则、失败上报规则
├─ 第 2 层：角色层（role）──────────────── 每个 Agent 一份，见 agents.md §1–§15
│   该角色的职责、禁止事项、输出 schema 特有要求
└─ 第 3 层：业务层（domain_overlay）────── 每个业务域一份，由 domain profile 注入
    业务术语表、领域规则、语气风格、领域特有的红线
```

规则：

- 下层不得与上层冲突；冲突时上层生效（基座 > 角色 > 业务）。这条写进基座 prompt 本身，同时 Evaluator 的测试集中包含冲突探测用例。
- 业务层是**唯一**允许按业务改动的层。给新业务实例化系统时，角色层 prompt 一个字都不用改。
- 运行时拼装时追加动态段：本次 delegation 的任务与约束、当前生效的 active_policies。这两段来自 shared state，不属于任何静态层。

## 5. 版本化规则

| 对象 | 版本载体 | 规则 |
|------|---------|------|
| message/payload schema | 信封 `schema_version` + schema `$id` 带版本后缀（如 `agent.delegation@2`） | 加可选字段 = 小版本，兼容；改必填字段/枚举 = 大版本，新旧并存一个过渡期 |
| Agent manifest | `version`（semver） | 输出 schema 大版本升级必须同步升 Agent 大版本 |
| prompt 三层 | 文件随 git 版本化，manifest 引用路径 | 角色层改动必须跑通该 Agent 的全部测试用例（agents.md 各节）才能启用 |
| validation contract | `contract_id` 内容不可变 | 修改验收标准 = 新 contract_id，旧的留档可回放 |
| tool manifest | `version` | params_schema 破坏性变更 = 大版本，运行时对旧调用报明确错误 |

回放兼容：audit_log 中每条消息都带 `schema_version`，回放工具按版本选择校验器，历史 mission 永远可回放。

## 6. 运行时适配层（可替换接口）

核心编排逻辑只依赖四个接口，默认实现和替换路径：

| 接口 | 职责 | 默认实现 | 可替换为 |
|------|------|---------|---------|
| ModelGateway | 逻辑模型名 → 实际调用；统计 token | 本地 Ollama / API 配置 | 任意 LLM 服务、多供应商路由 |
| MessageBus | 投递 agent.message；持久化 | 进程内队列 + JSON 落盘 | Redis/MQ/Kafka（分布式部署时） |
| StateStore | shared state 读写；乐观锁 | 本地 JSON/SQLite | Postgres/Redis |
| ApprovalQueue | 人工审核排队、token 签发 | 本地队列 + CLI/Web 确认 | 工单系统、IM 机器人审批 |

约束：Agent 代码（prompt + manifest）不允许直接 import 任何具体实现；所有副作用经 tool 注册表走。这保证同一套 Agent 定义可以在单机脚本、后端服务、分布式集群三种形态下运行。

## 7. 扩展点操作清单

| 想做的事 | 需要改什么 | 不需要改什么 |
|---------|-----------|-------------|
| 新增一个 Agent | 写 manifest + 角色层 prompt + 测试用例，注册 | 核心运行时、其他 Agent、schema |
| 新增一类 Validator（如"成本审计"） | 同上；capability 命名 `validate.cost`；在相关 contract 的 `required_validators` 引用 | Validator 基类逻辑、消息流 |
| 新增工具 | 写 tool manifest + 实现；在需要的 Agent manifest 的 allowed_tools 加名字 | 其他工具、Agent prompt |
| 换某个 Agent 的模型 | 改该 manifest 的 model_profile | prompt、schema、流程 |
| 接入新业务域 | 写一份 domain profile（见 instantiation-template.md） | 全部 15 个角色层 prompt、全部 schema |
| 收紧某业务的安全策略 | domain profile 的 policies + 业务层 prompt | 基座、角色层 |
| 从单机换成分布式 | 替换 MessageBus/StateStore 实现 | Agent 定义、schema、prompt |

## 8. 演进防退化

- 注册表加载器在 CI 中运行：所有 manifest、schema、prompt 引用完整性校验（引用不存在的 schema/tool/prompt 文件即失败）。
- agents.md 中每个 Agent 的测试用例是**回归集**：角色层 prompt 或模型变更后必须重跑。
- Evaluator（agents.md §15）负责从生产失败样本中持续补充测试用例，形成"失败一次，永远回归"。
