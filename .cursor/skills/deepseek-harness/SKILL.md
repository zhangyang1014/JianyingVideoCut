---
name: deepseek-harness
description: >-
  Apply DeepSeek Harness engineering standards to JianyingVideoCut (GoldenClip).
  Use before changing code, architecture, tests, documentation, comments, prompts,
  or product copy in this repository, and when the user mentions DeepSeek Harness,
  dsh, Cordis, or 能力缝.
---

# DeepSeek Harness（GoldenClip 开发规范）

沿用 CCO 项目采用的 DeepSeek Harness 编程规范。按下表把规则落实到 GoldenClip 的现有结构。使用本仓库的开发和测试命令。只采用编程规范，跳过 `dsh` 安装、构建、启动和 `dsh-code-review`，无需配置 DeepSeek API key。

## 能力定义、提供方和消费方

| Harness 角色 | GoldenClip 的落点 |
| --- | --- |
| Definition | Agent 能力的 manifest 和 JSON 契约在 `domains/goldenclip/`。任务与生产数据的 Python 模型在 `backend/models/`，前端共享类型在 `client/src/lib/api.ts`。 |
| Provider | `backend/services/` 执行业务逻辑，`backend/routers/` 暴露 API，`backend/agent_runtime/` 承担 Agent 运行和契约校验。 |
| Consumer | `client/src/` 的页面、组件和 API 客户端，以及读取结构化结果的下游流水线阶段。 |

新增或修改跨模块能力时，检查定义、提供方和消费方是否一致。新行为优先挂到现有扩展点。改变任务调度、Agent 路由或 API 契约时，同步更新受影响的业务文档、契约和调用方，保留 `docs/agent-architecture/` 的通用模板。

## 编码与验证

1. 在进入执行前解析配置和默认值。部署参数通过项目已有的可校验配置传入，协议常量保持固定。缺失依赖或无效配置在最早可判断的位置报错。
2. 在 HTTP、任务 JSON、模型结构化输出、文件和子进程等入口验证输入。沿用现有模型和类型，区分不同含义的跨模块 ID，例如任务 ID 与片段 ID。
3. 修改模型可见输入时，检查该阶段是否能从任务数据和已有的追踪记录重建所用配置与输入。按本仓库对 API key、视频和导出数据的约定处理记录内容。
4. 在真实入口验证行为。按改动运行相关 `backend/test_*.py` 或 `client/src/**/*.test.*`，前端类型变化运行 `pnpm check`。涉及用户可见行为、取消、重试或并发时，检查有关的成功和失败路径。
5. 分别报告超时、取消和退出等结果。生命周期、并发和 FFmpeg 子进程清理要等到相关进程或任务停稳。测试优先等待可观察的状态变化，避免依赖固定延时。
6. 代码注释和技术文档写清条件、责任方、兼容约定和失败行为。删除重复代码或简化抽象前，确认调用方依赖的行为及其验证路径。

## 按任务查阅原文

以下链接指向 CCO 使用的 DeepSeek Harness 原文版本。命令、目录、包名和发布要求以 GoldenClip 的 `AGENTS.md` 为准。

| 场景 | 原文 |
| --- | --- |
| 核对编程约定 | [Harness AGENTS.md](https://github.com/deepseek-ai/deepseek-harness/blob/7169660d330452d32c91bb2e4788a9b8c2f83a18/AGENTS.md) |
| 文档、注释、提示词、诊断和 UI 文案 | [dsh-prose-standard](https://github.com/deepseek-ai/deepseek-harness/blob/7169660d330452d32c91bb2e4788a9b8c2f83a18/.agents/skills/dsh-prose-standard/SKILL.md) |
| 文档结构和维护 | [dsh-doc](https://github.com/deepseek-ai/deepseek-harness/blob/7169660d330452d32c91bb2e4788a9b8c2f83a18/.agents/skills/dsh-doc/SKILL.md) |
| 异步、资源占用和易碎测试 | [dsh-ci-test-reliability](https://github.com/deepseek-ai/deepseek-harness/blob/7169660d330452d32c91bb2e4788a9b8c2f83a18/.agents/skills/dsh-ci-test-reliability/SKILL.md) |
| 简化抽象和删除冗余 | [dsh-find-simplifications](https://github.com/deepseek-ai/deepseek-harness/blob/7169660d330452d32c91bb2e4788a9b8c2f83a18/.agents/skills/dsh-find-simplifications/SKILL.md) |
| 生命周期、并发、子进程和清理 | [防御性模式](https://github.com/deepseek-ai/deepseek-harness/blob/7169660d330452d32c91bb2e4788a9b8c2f83a18/docs/defensive-patterns.zh.md) |
