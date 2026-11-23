# 将 Agent Loop Reference Kit 融合到新项目的提示词

把下面提示词复制给 AI 编程助手，并把目标项目的信息填入占位符。

```text
你是资深 Agent 系统架构师和工程落地顾问。

我已经把 `agent-loop-reference-kit/` 复制到了当前项目中。请你先只读分析当前项目，再基于这个 kit 帮我设计如何融合 Agent 自主 loop 能力。

## 当前项目背景

项目名称：
【填写项目名称】

项目目标：
【填写项目目标】

技术栈：
【填写语言、框架、后端、前端、数据库、部署方式】

我希望 Agent 完成的任务类型：
【例如：代码修改、文档生成、数据分析、客服回复、运营流程、内部工具自动化】

可用工具或系统：
【例如：文件系统、数据库、API、MCP、消息系统、CI、浏览器、企业微信】

安全边界：
【哪些文件不能改、哪些命令不能跑、哪些 API 需要人工确认、哪些数据不能读取】

成功标准：
【如何判断 Agent 做对了，例如测试通过、生成指定文档、通过人工审核、输出 JSON 等】

## 请你执行的分析任务

1. 阅读 `agent-loop-reference-kit/README.md`，判断本项目应该走哪条路径：
   - Path A: Basic Agent Loop
   - Path B: Enterprise Multi-Agent System
   - Path C: Memory, Orchestration, and Evals

2. 阅读以下核心文档并提炼适合本项目的设计：
   - `agent-loop-reference-kit/docs/01-core-handbook.md`
   - `agent-loop-reference-kit/docs/05-autonomous-loop-blueprint.md`
   - `agent-loop-reference-kit/docs/08-how-to-design-an-agent.md`
   - `agent-loop-reference-kit/docs/13-runtime-harness.md`
   - 如果需要企业级多 Agent，再读 `docs/10-enterprise-agent-system.md`
   - 如果需要记忆系统，再读 `docs/12-memory-rag-state.md`
   - 如果需要评测体系，再读 `docs/15-evals-system.md`

3. 分析当前项目结构，回答：
   - 哪些目录适合作为 Agent workspace？
   - 哪些工具可以开放给 Agent？
   - 哪些动作必须人工审批？
   - 是否需要 memory？
   - 是否需要多 Agent orchestration？
   - 是否需要 JSON Schema 输出契约？
   - 是否需要 evals？

4. 给出一个最小可落地方案，不要一开始做大平台。

## 输出格式

请用 Markdown 输出：

### 1. 项目适配结论

- 推荐路径：Path A / Path B / Path C / 混合
- 原因：
- 不建议做的事情：

### 2. 最小 Agent Loop 设计

- Agent 名称：
- 目标：
- 输入：
- 可用工具：
- 禁止行为：
- 输出：
- 成功标准：
- 终止条件：

### 3. Runtime Harness 设计

- Tool Registry：
- Context Builder：
- Guardrails：
- Verify Step：
- Trace：
- Human Review：

### 4. Memory 设计（如果需要）

- Working Memory：
- Short-term Memory：
- Long-term Memory：
- Memory Write Policy：
- Retrieval 策略：

### 5. 文件落地建议

请列出建议新增或复制的文件，例如：

```text
agent/
  prompts/
  schemas/
  memory_template/
  evals/
```

### 6. 实施步骤

按 3-7 个步骤列出，先做 MVP，再扩展。

### 7. 风险和人工审批点

列出高风险动作、审批规则和失败处理。

## 重要要求

- 不要整套照搬 `agent-loop-reference-kit/`，只选本项目需要的部分。
- 不要改动项目文件，除非我明确要求执行。
- 优先做单 Agent 自主 loop，再考虑多 Agent。
- 安全边界必须用代码和权限控制实现，不能只靠 prompt。
- 所有建议都要贴合当前项目实际结构。
```

