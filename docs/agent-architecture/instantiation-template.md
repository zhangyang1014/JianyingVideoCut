# Domain Profile 实例化模板

把这套通用 Agent 系统落地到一个具体业务时，只需要填写一份 **domain profile**。角色层 prompt、schema、运行时全部复用，不允许为单个业务修改（见 extensibility.md §4、§7）。

一份 domain profile = 下面这个 JSON 配置 + 一个业务层 prompt 目录。

## 1. Domain Profile Schema

```json
{
  "$id": "domain.profile",
  "type": "object",
  "required": ["domain_id", "business_goal", "constraints", "enabled_agents",
               "enabled_tools", "high_risk_actions", "human_review_rules"],
  "properties": {
    "domain_id": { "type": "string", "description": "业务域唯一标识，如 clip_pipeline / red_content" },
    "version": { "type": "string" },

    "business_goal": { "type": "string", "description": "一句话业务目标 + 成功判据" },
    "constraints": {
      "type": "array", "items": { "type": "string" },
      "description": "业务限制：合规边界、成本上限、时效要求、数据边界"
    },
    "data_sources": {
      "type": "array",
      "items": { "type": "object", "required": ["name", "access_tool"], "properties": {
        "name": { "type": "string" },
        "access_tool": { "type": "string", "description": "对应 tool manifest 中的 tool_name，数据只能经工具访问" },
        "trust": { "enum": ["trusted", "untrusted"], "description": "untrusted 数据自动进 untrusted_content_refs" },
        "contains_pii": { "type": "boolean" } } }
    },

    "enabled_agents": {
      "type": "array",
      "items": { "type": "object", "required": ["capability"], "properties": {
        "capability": { "type": "string", "description": "如 create.document / validate.compliance" },
        "preferred_agent": { "type": ["string", "null"], "description": "能力路由的优先选择，null 走默认解析" },
        "domain_overlay": { "type": ["string", "null"], "description": "业务层 prompt 文件路径" },
        "model_override": { "type": ["string", "null"] } } },
      "description": "本业务启用哪些能力。未启用的能力路由不到，Planner 也不会分配"
    },

    "enabled_tools": {
      "type": "array", "items": { "type": "string" },
      "description": "本业务开放的工具全集，是所有 Agent allowed_tools 的最外层交集"
    },

    "high_risk_actions": {
      "type": "array",
      "items": { "type": "object", "required": ["action", "tool"], "properties": {
        "action": { "type": "string", "description": "业务语言描述，如 发布笔记 / 覆盖导出文件" },
        "tool": { "type": "string" },
        "irreversible": { "type": "boolean" } } },
      "description": "逐条列出。列出的 tool 自动升为 requires_human_approval"
    },

    "immutable_field_rules": {
      "type": "array",
      "items": { "type": "object", "required": ["applies_to", "fields"], "properties": {
        "applies_to": { "type": "string", "description": "产物类型或 capability" },
        "fields": { "type": "array", "items": { "type": "string" } },
        "reason": { "type": "string" } } },
      "description": "业务关键不可变字段，如剪辑域的 cue.start/cue.end"
    },

    "human_review_rules": {
      "type": "array",
      "items": { "type": "object", "required": ["condition", "urgency"], "properties": {
        "condition": { "type": "string", "description": "触发条件，尽量可代码判定" },
        "urgency": { "enum": ["low", "medium", "high", "critical"] },
        "reviewer_role": { "type": "string" } } }
    },

    "auto_approve_whitelist": {
      "type": "array",
      "items": { "type": "object", "required": ["category", "sample_rate"], "properties": {
        "category": { "type": "string", "description": "低风险类别描述" },
        "sample_rate": { "type": "number", "description": "抽检比例 0-1，白名单也必须抽检" } } }
    },

    "budget_defaults": {
      "type": "object",
      "properties": {
        "max_tokens_per_mission": { "type": "integer" },
        "max_tool_calls_per_mission": { "type": "integer" },
        "mission_deadline_minutes": { "type": "integer" }
      }
    },

    "glossary": {
      "type": "object",
      "description": "业务术语 → 定义。注入业务层 prompt，保证各 Agent 用词一致",
      "additionalProperties": { "type": "string" }
    },

    "validation_contract_templates": {
      "type": "array",
      "description": "本业务常用产物的验收契约模板（agent.validation_contract），Planner 引用而非重造",
      "items": { "type": "object" }
    },

    "evaluation_goldens": {
      "type": "array",
      "description": "业务黄金测试集：输入 + 期望行为，Evaluator 回归用",
      "items": { "type": "object", "required": ["case_id", "input", "expected_behavior"], "properties": {
        "case_id": { "type": "string" },
        "input": { "type": "object" },
        "expected_behavior": { "type": "string" } } }
    }
  }
}
```

代码硬控制：

- profile 加载时校验：`enabled_tools`、`preferred_agent`、`capability` 必须在注册表中存在。
- `high_risk_actions` 列出的工具自动收紧为需人工审核，profile 无权放宽任何全局硬规则（只能收紧）。
- `immutable_field_rules` 由运行时转成 delegation 的 `immutable_fields`，业务方不需要每次手填。

## 2. 实例化步骤清单

1. **填 profile**：按上面 schema 写 `domains/<domain_id>/profile.json`。
2. **写业务层 prompt**：`domains/<domain_id>/overlays/*.md`，只写业务术语、领域规则、语气红线；不得复述基座/角色层已有内容，不得与之冲突。
3. **补工具**：业务特有工具按 tool manifest 注册（extensibility.md §3）。
4. **写验收契约模板**：本业务核心产物至少各 1 份 validation contract，code 断言优先。
5. **建黄金测试集**：至少覆盖——一个正常成功路径、一个必须拦截的高风险输入、一个信息不足需升级人工的输入、一个 prompt 注入探测输入。
6. **跑冒烟**：用 MVP 验收标准（README §7）在该业务域重跑一遍。
7. **上线前对照**：逐条过 README §8 风险清单，每条写"如何缓解"或"接受理由"。

## 3. 填写示例（节选，剪辑流水线域）

```json
{
  "domain_id": "clip_pipeline",
  "business_goal": "把口播视频自动完成 ASR、语义审计、片段筛选，产出待人工 review 的剪辑方案；成功判据：人工调整率低于 30%",
  "constraints": ["全程本地运行", "模型只用 ApiConfig 中已配置项", "单视频处理成本上限 X"],
  "data_sources": [
    { "name": "上传视频", "access_tool": "read_upload", "trust": "untrusted", "contains_pii": true },
    { "name": "领域词表", "access_tool": "read_glossary", "trust": "trusted", "contains_pii": false }
  ],
  "enabled_agents": [
    { "capability": "create.analysis", "preferred_agent": null, "domain_overlay": "overlays/semantic_audit.md" },
    { "capability": "validate.output", "preferred_agent": null, "domain_overlay": "overlays/audit_validation.md" }
  ],
  "high_risk_actions": [
    { "action": "覆盖已有导出文件", "tool": "ffmpeg_export", "irreversible": true }
  ],
  "immutable_field_rules": [
    { "applies_to": "translation_cue", "fields": ["start", "end"], "reason": "时间轴由 ASR 决定，模型改写会破坏音画对齐" }
  ],
  "human_review_rules": [
    { "condition": "audit_segments 删除比例 > 50%", "urgency": "medium", "reviewer_role": "剪辑师" }
  ]
}
```
