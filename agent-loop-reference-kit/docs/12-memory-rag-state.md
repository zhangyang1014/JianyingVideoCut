# Memory、RAG 与 State 的工程区分

## 五种存储类型

| 类型 | 解决什么 | 例子 | 常用存储 |
| --- | --- | --- | --- |
| RAG 知识库 | 通用知识 | SOP、合规政策、产品规则 | 文档库 + 向量库 |
| State 状态 | 当前任务进展 | 案件正在人工复核 | SQL / Postgres |
| History 历史 | 发生过什么 | 上次投诉、上次还款 | 事件表 / 日志表 |
| Memory 记忆 | 对未来有用的经验 | 用户拒绝电话沟通 | 结构化表 + 向量索引 |
| Feedback 反馈 | 人类纠正过什么 | AI 建议被主管拒绝 | 反馈表 / 评估集 |

## 记忆生命周期（7 步）

1. **观察**：Agent 看到一次对话、操作或结果。
2. **提取**：从原始信息中提取值得记住的内容。
3. **判断**：是否应该写入 memory（Memory Write Policy）。
4. **写入**：写入数据库，绑定实体、来源、时间、置信度。
5. **检索**：下次处理时先查相关 memory。
6. **注入**：把 memory 放进 Agent 上下文。
7. **更新**：根据结果修正、过期或撤销。

## 五层业务记忆

| 层级 | 名称 | 记什么 |
| --- | --- | --- |
| L1 | Session Memory | 当前对话临时状态 |
| L2 | Case Memory | 当前案件/任务状态 |
| L3 | Customer/User Memory | 用户长期偏好和风险 |
| L4 | Operator Memory | 操作者/坐席历史 |
| L5 | System Learning Memory | 系统级经验（模板效果、常见坑） |

## Memory Item 推荐结构

不要只存一段自然语言。每条 memory 应包含：

```json
{
  "memory_id": "string",
  "entity_type": "customer | case | operator | project | agent",
  "entity_id": "string",
  "memory_type": "string",
  "content": "string",
  "source_type": "agent | user | tool | system | human",
  "source_id": "string",
  "confidence": "low | medium | high",
  "status": "pending | confirmed | rejected | expired",
  "expires_at": "string | null",
  "permission_scope": "string",
  "tags": []
}
```

Schema 文件：`schemas/memory_item.schema.json`

## Memory Write Policy

### 应该记

- 会影响未来决策的信息
- 有合规风险的信息
- 人工确认过的信息
- 重复出现的模式
- 涉及权限/状态的信息

### 不应该记

- 一次性闲聊
- 不确定的事实（Agent 猜测）
- 高敏感但无授权的信息
- 很快过期的临时安排
- 没有来源的判断

## 检索策略

混合检索最佳：

1. 按 entity_id 精确查
2. 按 memory_type 过滤
3. 按时间和权限过滤
4. 用语义相似度找类似经验

## Vector Store 不是 Memory System

| 能力 | Vector Store | Memory System |
| --- | --- | --- |
| 找相似内容 | 强 | 需结合 |
| 记录当前状态 | 弱 | 强 |
| 支持事务 | 弱 | 强 |
| 支持权限 | 需额外设计 | 必须内置 |
| 支持审计 | 弱 | 强 |
| 支持更新/撤销 | 麻烦 | 必须支持 |

推荐架构：结构化数据库存状态和事实，向量库存可语义检索的文本，事件日志存完整历史。

## 事件溯源

记忆更新不要简单覆盖。保留完整链路：

```
6月1日：用户投诉联系第三方
6月2日：主管确认投诉成立
6月5日：系统标记为第三方触达敏感
6月20日：用户同意仅通过 WhatsApp 沟通
```

这样可审计、可回溯。
