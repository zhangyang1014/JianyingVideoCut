# GoldenClip Agent Index

这个文件是给 Codex/代码代理使用的项目检索入口。进入项目后先读本文件，再按任务读取相关源码。

## DeepSeek Harness 开发规范

修改本仓库的代码、架构、测试、文档、注释、提示词或产品文案前，先读并应用 [DeepSeek Harness](.cursor/skills/deepseek-harness/SKILL.md)。其中的 GoldenClip 目录映射适用于本仓库。

## 项目概览

GoldenClip 是一个本地智能视频剪辑工作站：

- 前端：React 19 + Vite + TypeScript，入口在 `client/src/`。
- 后端：FastAPI + Python，入口在 `backend/main.py`。
- 核心能力：视频上传、ASR、语义审计、片段 review、FFmpeg 导出、剪映草稿生成、prompt 学习优化。
- 运行数据：上传、导出、缩略图、任务 JSON 都是本地文件数据，不应当当作源码优先检索。

## 快速检索地图

### 根目录

- `README.md`：项目说明、架构、启动方式、API 概览。
- `Editing_Aesthetic.md`：剪辑美学和字幕风格配置。
- `package.json`：前端/Node 脚本与依赖。
- `requirements.txt`：Python 后端依赖。
- `start_backend.sh`：后端启动脚本。
- `vite.config.ts`：Vite 配置、别名、开发服务配置。
- `server/index.ts`：生产模式 Node/Express 静态服务入口。

### 后端

- `backend/main.py`：FastAPI 应用入口，API 路由、WebSocket、后台任务调度集中在这里。
- `backend/models/task.py`：核心 Pydantic 数据模型，包含任务、片段、导出、ASR、学习任务等类型。
- `backend/services/task_store.py`：任务 JSON 本地存储。
- `backend/services/learning_store.py`：学习任务本地存储。
- `backend/services/asr_pipeline.py`：ASR 管道、VAD、分段、说话人平滑等。
- `backend/services/semantic_auditor.py`：语义审计 prompt 构建、模型调用、结果解析、规则 fallback。
- `backend/services/ffmpeg_executor.py`：FFmpeg 视频信息、字幕、无损导出、多 clip 导出、取消导出。
- `backend/services/jianying_builder.py`：剪映草稿生成。
- `backend/services/prompt_optimizer.py`：基于人工修正收集 prompt 改进。
- `backend/services/learning_analyzer.py`：学习分析、prompt 改写、版本保存。
- `backend/services/golden_quote_extractor.py`：金句候选建议。
- `backend/services/one_click_translate.py`：一键转英文/目标语言配音流水线，负责字幕 cue 交接、翻译、TTS、音频对齐和合成。
- `backend/prompts/*.md`：不同任务类型和学习流程的模型 prompt。
- `backend/data/user_style.md`：用户风格记忆。
- `backend/data/glossary.txt`：领域词表。

### 前端

- `client/src/App.tsx`：路由入口。
- `client/src/main.tsx`：React 挂载入口。
- `client/src/pages/Dashboard.tsx`：任务看板。
- `client/src/pages/ReviewWorkbench.tsx`：主 review 工作台。
- `client/src/pages/LearningPage.tsx`：学习分析页面。
- `client/src/pages/ConfigPage.tsx`：prompt 管理。
- `client/src/pages/ApiConfigPage.tsx`：API/模型配置。
- `client/src/components/AppLayout.tsx`：主布局和导航。
- `client/src/components/HighlightClipsPanel.tsx`：精彩集锦控制面板。
- `client/src/components/ModelPickerPopover.tsx`：模型选择控件。
- `client/src/lib/api.ts`：前端 API 客户端和共享类型。
- `client/src/lib/highlight-clips.ts`：精彩集锦片段逻辑。
- `client/src/hooks/`：组合状态、配置、撤销历史等 hooks。
- `client/src/components/ui/`：通用 UI 组件。

### 文档和技能

- `docs/`：功能设计、路线图、优化记录。
- `docs/one-click-translate.md`：一键转英文需求、阶段交接契约和 API。
- `docs/agent-architecture/`：通用多 Agent 系统设计规范（架构、schema、16 个 Agent 规格、注册表与能力路由、domain profile 实例化模板）。**这是只读模板，不要为具体业务修改。**
- `domains/goldenclip/`：模板在本项目上的具体实例——profile、5 个 Agent manifest、业务层 prompt、验收契约、工具注册表，全部映射到现有 backend/services 实现。改 Agent 行为先看这里。
- `.cursor/skills/*/SKILL.md`：Cursor 技能文档，可作为特定问题的历史实现线索。

### Lina 数字人口播写稿

数字人口播的工具分工、同声音视频交接、片长与素材入出点约定见 [制作工作流](docs/digital-presenter-workflow.md)。该文另含带日期的饮水机样片待办与 i9 RAGFlow 运维记录，继续制作或检索前核对实际状态。

制作、重做或导入数字人口播的 B-roll 前，先读 [B-roll 制作与验收工作流](docs/broll-production-workflow.md)。按旁白含义设计多个 1–2 秒逻辑镜头，优先按场景合并为每段 1–2 条多镜头提示词生成，回传后按实际切点取段。落实中国有孩子家庭的生活场景、9:16 画幅、环境音、素材绑定与当前预览选段，并在实际项目页面验收播放。用户在本轮提出的修正优先于历史样片安排。

创作者风格分析先读 `docs/creator-style-analysis.md`，复用选样、功能段落标注、题材统计和复核流程。按 `domains/goldenclip/creator_styles/active.json` 找到对应创作者的版本化风格卡与证据索引；换创作者从新语料归纳，不继承 Lina 的规则。区分用户报告、候选规则和本地已验证结论，缺失证据不补造。

写新的 Lina 短视频时，先读 `domains/goldenclip/presenters.json` 的受众设定和当前风格卡，再按 `.cursor/skills/lina-short-video-structure/SKILL.md` 确定开场、首答位置、分层和收尾，写出初稿。初稿完成后，按 `domains/goldenclip/overlays/lina_spoken_polish.md` 润色台词和字幕，保留段序、核心判断和已核实的事实。数字人口播工作台的 AI 生成入口在 `backend/services/production_planner.py`，生成方向保存所用风格快照。

数字人口播的技能路由与调用时机由 `domains/goldenclip/presenter_workflow.json` 维护，编辑页大标题与后端执行共用该配置。AI 起稿阶段使用结构技能，初稿完成后单独调用 `.cursor/skills/digital-presenter-spoken-polish/SKILL.md` 润色；通用结构技能为 `.cursor/skills/digital-presenter-script-structure/SKILL.md`，Lina 保留自己的结构技能与语气规则。人设、受众和表达边界是这些阶段共用的档案。已保存原稿转分镜保持原文，不重新执行写稿技能。

## 优先避开的目录

除非任务明确要求，不要优先扫描这些目录：

- `node_modules/`：前端依赖。
- `dist/`：构建产物。
- `models/`：ModelScope/FunASR 模型缓存，体积大。
- `uploads/`：用户上传视频。
- `exports/`：导出结果。
- `thumbnails/`：缩略图。
- `.pytest_cache/`、`.manus-logs/`：缓存和日志。
- `backend/data/tasks.json`：本地任务数据，可能较大且属于运行态数据。

## 常用命令

```bash
# 前端开发服务
pnpm dev

# 前端构建
pnpm build

# TypeScript 检查
pnpm check

# 前端测试
pnpm test:run

# 后端开发启动
PYTHONPATH=. python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

# 后端脚本启动
bash start_backend.sh

# Python 测试
python3 -m pytest backend
```

## 本地服务

- 前端开发服务默认由 Vite 提供，配置端口在 `vite.config.ts`，默认 `5177`，端口占用时会自动顺延。
- README 里提到的前端地址可能是旧的 `3000`；以实际 Vite 输出为准。
- 后端默认 `http://localhost:8000`。
- API 文档：`http://localhost:8000/docs`。
- WebSocket：`ws://localhost:8000/ws/{task_id}`。

## 关键数据流

1. 前端在 `Dashboard` 创建任务并上传视频。
2. `backend/main.py` 接收上传，写入 `uploads/`，生成缩略图。
3. ASR 流程调用 `backend/services/asr_pipeline.py`，结果写入任务对象。
4. 审计流程调用 `backend/services/semantic_auditor.py` 和 `backend/prompts/*.md`。
5. 前端 `ReviewWorkbench` 读取并人工调整 `audit_segments`。
6. 导出流程走 `backend/services/ffmpeg_executor.py` 或 `backend/services/jianying_builder.py`。
7. 任务状态和历史快照由 `backend/services/task_store.py` 写入本地 JSON。

## 修改注意事项

- 前后端共享字段要同时检查 `backend/models/task.py` 和 `client/src/lib/api.ts`。
- 修改 API 路由时，先查 `backend/main.py`，再更新 `client/src/lib/api.ts` 调用。
- 修改片段动作、状态、导出字段时，检查前端 review UI、后端模型、FFmpeg 导出链路三处。
- 不要把 API key、用户视频、导出文件、模型缓存写入新文档或提交。
- 如果要处理大模型缓存、上传文件或导出文件，先确认用户意图。
- 保持现有中文业务术语，例如 ASR、语义审计、金句、剪映草稿、精彩集锦、口播精修。

## 测试线索

- 后端测试：`backend/test_*.py`。
- 前端测试：`client/src/**/*.test.ts`、`client/src/**/*.test.tsx`。
- 精彩集锦逻辑：优先跑 `client/src/lib/highlight-clips.test.ts`。
- Dashboard 变更：优先跑 `client/src/pages/Dashboard.test.tsx`。
- 导出/取消/任务列表字段变更：优先跑对应的 `backend/test_*.py`。
