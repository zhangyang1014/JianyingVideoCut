# GoldenClip 智能视频工作站 v3.0

> **编导思维驱动** · ASR + 多模型任务角色路由 · 分钟级产出高质量精华片段

[![Python](https://img.shields.io/badge/Python-3.10+-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-19-blue)](https://react.dev)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-required-orange)](https://ffmpeg.org)

---

## 🎬 项目愿景

GoldenClip 是一个"**编导思维**"驱动的本地剪辑工具，通过 **ASR + 按任务角色路由的大模型** 自动完成访谈与口播的初剪，辅以轻量化网页交互，实现"分钟级"产出高质量精华片段。

### 核心用例

| 用例 | 场景 | 价值 |
|------|------|------|
| **口播废话自动精修** | 5分钟口播含10处结巴、3处重说 | 1小时精剪工作 → 3分钟完成 |
| **长访谈嘉宾精华重组** | 100分钟主播对谈，多主题重复 | 原始素材 → 15分钟可发布成品 |

---

## 🏗 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    React 前端 (Port 3000)                 │
│  任务看板  │  三栏 Review 工作台  │  配置管理              │
└─────────────────────┬───────────────────────────────────┘
                      │ HTTP / WebSocket
┌─────────────────────▼───────────────────────────────────┐
│                  FastAPI 后端 (Port 8000)                 │
│                                                          │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ 任务管理  │  │ ASR 管道      │  │ SemanticAuditor  │   │
│  │ JSON存储  │  │ Whisper/VAD  │  │ 任务角色 LLM 路由 │   │
│  └──────────┘  └──────────────┘  └──────────────────┘   │
│                                                          │
│  ┌──────────────────┐  ┌──────────────────────────────┐  │
│  │  FFmpeg 执行层    │  │  剪映草稿生成                  │  │
│  │  无损切割 + 拼接  │  │  pyJianYingDraft             │  │
│  └──────────────────┘  └──────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

---

## 🚀 快速开始

### 1. 环境要求

```bash
# macOS。其他平台请先安装 FFmpeg 和 Python 3.10+。
brew install ffmpeg python@3.12

# 在项目根目录使用 Python 3.10+ 创建虚拟环境（以 3.12 为例）
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

### 2. 启动后端

```bash
# 方式一：后台启动（推荐）
bash start_backend.sh

# 方式二：开发模式（热重载）
bash start_backend.sh --dev

# 方式三：前台启动，直接查看后端日志
bash scripts/run-backend.sh
```

### 3. 启动前端

```bash
pnpm install
pnpm dev
# 访问 http://localhost:5177
```

### 4. 配置模型供应商（可选）

在「AI 配置 → 角色路由」中配置供应商档案，也可使用环境变量：

```bash
export ROUTERHUB_API_KEY="..."
export VOLCENGINE_ARK_API_KEY="..."
export ZHIPU_API_KEY="..."
# 默认仅云端 TTS 使用
export OPENROUTER_API_KEY="..."
```

文本模型默认统一通过 RouterHub，并按任务精确分工：中文复杂语义、金句和 Prompt 改写优先 GLM-5.2，集锦策略与学习归因优先 Claude Opus 4.8，字幕翻译优先 Claude Sonnet 4.6，独立复核优先 GPT-5.6 Sol，并用 Gemini 3.1 Pro 做跨模型后备。复杂任务启用 RouterHub 的 medium/high reasoning；OpenRouter 仅保留云端 TTS。没有 RouterHub 凭据时不会静默借用 OpenRouter Key，审计和集锦仍保留确定性规则兜底。

---

## 🧠 语义手术刀 — 8 条黄金规则

| 规则 | 名称 | 逻辑 |
|------|------|------|
| Rule 1 | **重说识别** | 相邻两句开头 5+ 字相同 → 删除前句 |
| Rule 2 | **残句清理** | 时长 < 1.5s 且无法独立成义 → 物理删除 |
| Rule 3 | **语气词切除** | 识别 `<FIL>` 标签（嗯/啊/那个/然后）→ 移除 |
| Rule 4 | **词内去重** | 识别 `<STU>` 结巴标记 → 保留最终版本 |
| Rule 5 | **语义去重** | 跨时段相同观点 → 保留评分最高片段 |
| Rule 6 | **句内重复** | 修正前的半句 → 删除 |
| Rule 7 | **问答闭环** | 访谈场景 → 保证 Q&A 成对出现 |
| Rule 8 | **气口对齐** | 剪辑点前预留 150ms，后预留 100ms |

---

## 📊 核心工作流

```
原始视频
    │
    ▼ 第一步：感知 (Data Prep)
Silero VAD → 识别静音期 <SIL>
Faster-Whisper → 字符级时间戳
标记 <FIL> 语气词 / <STU> 结巴
    │
    ▼ 第二步：审计 (Semantic Audit)
带标签剧本 → `audit.semantic` 角色路由
应用 8 条黄金规则
输出 JSON 指令集 [{action, start, end, reason, rule}]
    │
    ▼ 第三步：重构 (Draft Construction)
路径 A：FFmpeg -c copy 无损拼接 → 直接出片
路径 B：pyJianYingDraft → 剪映草稿 → 二次精修
```

---

## 🖥 前端界面

### 任务看板
- 卡片式展示所有任务（缩略图、状态、统计）
- 拖拽上传 MP4/MOV/AVI/MKV/WebM
- 任务类型：口播精修 / 精彩集锦 / 直播访谈压缩

### Review 工作台（三栏布局）
```
┌─────────────────┬──────────────────────┬──────────┐
│   视频播放器     │    字幕审计流          │  说话人  │
│   时间轴同步     │  绿=保留 / 红=删除    │  面板    │
│   跳转到片段     │  显示删除原因+规则    │  统计    │
│                 │  一键切换保留/删除    │         │
│   导出面板       │                      │         │
│   FFmpeg / 剪映  │                      │         │
└─────────────────┴──────────────────────┴──────────┘
│  实时 Console 日志（ASR进度 / Claude输出 / FFmpeg进度）│
└──────────────────────────────────────────────────────┘
```

---

## 📁 项目结构

```
goldenclip/
├── backend/                    # Python FastAPI 后端
│   ├── main.py                 # FastAPI 应用入口 + WebSocket
│   ├── models/
│   │   └── task.py             # Pydantic 数据模型
│   ├── services/
│   │   ├── task_store.py       # JSON 任务持久化
│   │   ├── asr_pipeline.py     # ASR 管道（VAD + Whisper + 标签）
│   │   ├── semantic_auditor.py # Claude SemanticAuditor
│   │   ├── ffmpeg_executor.py  # FFmpeg 无损切割拼接
│   │   └── jianying_builder.py # 剪映草稿生成
│   ├── prompts/
│   │   └── semantic_audit.md   # Claude System Prompt（8条规则）
│   └── data/
│       └── tasks.json          # 任务持久化存储
├── client/                     # React 19 前端
│   └── src/
│       ├── pages/
│       │   ├── Dashboard.tsx   # 任务看板
│       │   ├── ReviewWorkbench.tsx  # Review 工作台
│       │   └── ConfigPage.tsx  # 配置管理
│       ├── components/
│       │   └── AppLayout.tsx   # 左侧导航布局
│       └── lib/
│           └── api.ts          # API 客户端
├── uploads/                    # 上传的视频文件
├── exports/                    # FFmpeg 导出结果
├── thumbnails/                 # 视频缩略图
├── Editing_Aesthetic.md        # 剪辑美学配置（可在 UI 中编辑）
├── start_backend.sh            # 后端启动脚本
├── requirements.txt            # Python 依赖
└── README.md                   # 本文档
```

---

## 🔌 API 参考

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/tasks` | 获取所有任务 |
| `POST` | `/api/tasks` | 创建新任务 |
| `GET` | `/api/tasks/{id}` | 获取任务详情 |
| `DELETE` | `/api/tasks/{id}` | 删除任务 |
| `POST` | `/api/tasks/{id}/upload` | 上传视频 |
| `POST` | `/api/tasks/{id}/asr` | 触发 ASR 识别 |
| `POST` | `/api/tasks/{id}/audit` | 触发任务角色语义审计 |
| `POST` | `/api/tasks/{id}/segments/{seg_id}/toggle` | 切换片段保留/删除 |
| `POST` | `/api/tasks/{id}/export/ffmpeg` | FFmpeg 无损导出 |
| `POST` | `/api/tasks/{id}/export/jianying` | 生成剪映草稿 |
| `GET` | `/api/tasks/{id}/video` | 视频流 |
| `GET` | `/api/tasks/{id}/thumbnail` | 缩略图 |
| `WS` | `/ws/{id}` | WebSocket 实时日志 |

完整文档：`http://localhost:8000/docs`

---

## 🛠 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.10+, FastAPI, Uvicorn, Pydantic v2 |
| 前端 | React 19, TypeScript, Tailwind CSS 4, shadcn/ui |
| AI/ASR | FunASR/faster-whisper、OpenAI-compatible 多模型角色路由 |
| 视频处理 | FFmpeg (无损切割拼接) |
| 剪映集成 | pyJianYingDraft |
| 说话人分离 | pyannote.audio (可选) |

---

## 📝 开发说明

### 无 ASR 模型时的 Mock 数据

系统在未安装 `faster-whisper` 时自动生成演示用 mock ASR 数据，包含：
- 模拟的中文口播内容
- `<SIL>` 停顿标记
- `<FIL>` 语气词标记
- `<STU>` 结巴标记
- 重说片段（用于测试 Rule 1）

### 无 Claude API Key 时的规则引擎

系统内置规则引擎，直接应用 8 条黄金规则进行审计，无需 API Key。

---

## 📄 License

MIT License — 自由使用、修改、分发

---

*GoldenClip v3.0 · 让每一帧都有意义*
