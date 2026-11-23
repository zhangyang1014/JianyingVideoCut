# Memory Policy

Memory 是用于复用项目知识的系统，不是原始对话 transcript。

## 存储原则

- 项目约定和架构决策
- 可复用的调试模式和解决方案
- 常用命令和验证步骤
- 已知陷阱和风险点
- 当前任务计划和状态

## 禁止存储

- 密钥或 token
- 完整命令日志（除非必要）
- 大段文件内容
- 临时猜测
- 敏感个人数据
- 与后续工作无关的用户私有内容

## 记忆分层

| 层级 | 名称 | 存什么 | TTL |
| --- | --- | --- | --- |
| L1 | Session / Working | 当前对话临时状态 | 任务结束即丢 |
| L2 | Case / Short-term | 当前任务/案件状态 | 任务关闭前 |
| L3 | User / Long-term | 用户偏好和风险 | 6-12 个月 |
| L4 | Operator | 操作者历史 | 1-3 个月 |
| L5 | System Learning | 系统级经验 | 到下次实验更新 |

## 写入门槛

写入前必须满足：

1. 会影响未来决策
2. 有明确来源（agent / user / tool / human）
3. 有置信度标注（low / medium / high）
4. 敏感信息需人工确认
5. 有 TTL 或过期机制

## 推荐路径

- `/memories/project_context.md`
- `/memories/current_task.md`
- `/memories/patterns/*.md`
- `/memories/decisions/YYYY-MM-DD.md`

详细工程指南见 `docs/12-memory-rag-state.md`。
Schema 定义见 `schemas/memory_item.schema.json`。

