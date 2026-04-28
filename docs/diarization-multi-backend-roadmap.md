# 说话人分离多后端架构升级方案

| 版本 | 日期 | 变更内容 | 变更人 |
|------|------|----------|--------|
| v1.0 | 2026-03-14 | 初版：方案调研与架构设计 | 大象 |

---

## 背景与动机

### 当前问题

CAM++（阿里达摩院 `iic/speech_campplus_speaker-diarization_common`）是段级说话人分离模型，对齐到字级后存在以下已知问题：

1. **短词误标**：时长 < 0.5s 的字（如"爸爸"、"嗯"）音频特征不稳定，容易被误归为另一说话人
2. **音色抖动**：情绪加重、笑着说话时音色临时变化，短段被误标
3. **边界噪声**：段级边界与字级时间戳不完全对齐，边界附近字的 speaker 容易抖动

### 已有缓解措施（v1.0 已实现）

`smooth_word_speakers` 函数（`backend/services/asr_pipeline.py`）采用 run-length 平滑策略：被同一说话人包夹、时长 < 1.0s 的异常 run 会被修正为包夹方的说话人。多轮迭代直到收敛。

### 升级动机

如果平滑后仍然频繁出现误标，说明 CAM++ 的段级边界质量本身存在问题。此时引入精度更高的 **pyannote-audio 3.1** 作为可选后端，用户可按需切换。

---

## 方案目标

1. **CAM++ 和 pyannote-audio 并存**，用户可在前端选择后端
2. **下游流程完全不变**：两个后端输出统一格式，`align_speakers_to_words` / `smooth_word_speakers` / `segment_by_silence` 无需修改
3. **降级兜底**：任意后端失败，降级为无说话人模式，不影响 ASR 主流程

---

## 技术方案

### 1. 数据流架构

```
TaskParams.diarization_backend = "cam++" | "pyannote"
                    │
    ┌───────────────┴────────────────┐
    ▼                                ▼
run_diarization_campplus()    run_diarization_pyannote()
（当前已有逻辑）               （新增）
    │                                │
    └───────────────┬────────────────┘
                    ▼
    统一输出 List[{start, end, speaker}]
                    ▼
    align_speakers_to_words  （不改）
                    ▼
    smooth_word_speakers     （不改）
                    ▼
    segment_by_silence       （不改）
```

### 2. 后端模型对比

| 维度 | CAM++ | pyannote-audio 3.1 |
|------|-------|---------------------|
| 来源 | 阿里达摩院，ModelScope | CNRS/Herve Bredin，HuggingFace |
| 精度 (DER) | 无公开基准 | ~11-19%（公开基准） |
| 中文场景 | 专为中文训练 | 主要英文，中文需验证 |
| 安装 | `pip install modelscope` | `pip install pyannote.audio` |
| 首次配置 | 无需注册 | 需 HF 账号 + 接受模型协议 + access token |
| 模型权重 | 从 ModelScope 自动下载 | 首次从 HuggingFace 下载（约 150MB） |
| 本地缓存 | `~/.cache/modelscope` | `~/.cache/huggingface` |
| 离线运行 | 支持 | 支持（首次下载后） |
| 国内网络 | ModelScope CDN，顺畅 | HuggingFace，建议配置镜像 |
| oracle_num | 支持 | 支持（min/max_speakers） |

### 3. 需要修改的文件清单

#### 后端

**`backend/models/task.py`**

在 `TaskParams` 中新增字段：

```python
# 说话人分离后端选择："cam++" 使用 ModelScope CAM++，"pyannote" 使用 pyannote-audio 3.1
diarization_backend: str = "cam++"
# pyannote 后端专用：HuggingFace access token（首次下载权重时使用，下载后本地缓存无需 token）
pyannote_hf_token: Optional[str] = None
```

**`backend/services/asr_pipeline.py`**

- 将现有 `run_diarization` 重命名为 `run_diarization_campplus`（逻辑不变）
- 新增 `run_diarization_pyannote`（新实现）
- 新增 `run_diarization` 路由函数（根据 `diarization_backend` 参数分发）

`run_diarization_pyannote` 关键逻辑：

```python
async def run_diarization_pyannote(
    audio_path: str,
    oracle_num: Optional[int] = None,
    hf_token: Optional[str] = None,
    log_callback: Optional[Callable] = None,
) -> List[dict]:
    """
    使用 pyannote-audio 3.1 执行说话人分离。
    首次运行需要 HuggingFace access token + 接受模型使用协议：
      - https://huggingface.co/pyannote/speaker-diarization-3.1
      - https://huggingface.co/pyannote/segmentation-3.0
    权重下载到 ~/.cache/huggingface/ 后，后续完全离线运行。

    国内网络可设置镜像：
      export HF_ENDPOINT=https://hf-mirror.com
    """
    try:
        from pyannote.audio import Pipeline
    except ImportError:
        if log_callback:
            await log_callback("warn", "diarization",
                "pyannote.audio 未安装，请运行：pip install pyannote.audio")
        return []

    # 加载 pipeline（本地已缓存时无需 token）
    kwargs = {"use_auth_token": hf_token} if hf_token else {}
    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1", **kwargs
    )

    # oracle_num 控制说话人数量
    diarize_kwargs = {}
    if oracle_num is not None:
        diarize_kwargs["num_speakers"] = oracle_num

    diarization = pipeline(audio_path, **diarize_kwargs)

    # 转换为统一格式 [{start, end, speaker}]
    result = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        result.append({
            "start": turn.start,
            "end": turn.end,
            "speaker": f"spk{speaker[-1]}",  # "SPEAKER_00" → "spk0"
        })
    return result
```

#### 前端

**`client/src/pages/ReviewWorkbench.tsx`** 或 ASR 配置面板

在「说话人分离」选项区域新增后端切换下拉框：

```
说话人分离后端：  [CAM++ (默认) ▼]
                  ├── CAM++ (ModelScope，国内友好)
                  └── pyannote-audio 3.1 (精度更高，需 HF token)
```

可选：当用户选择 pyannote 时，显示 HF token 输入框（首次需要）。

**`client/src/lib/api.ts`**

在 `TaskParams` 接口中新增对应字段。

### 4. pyannote-audio 安装与配置指引

**安装**

```bash
pip install pyannote.audio
```

**首次配置（仅需一次）**

1. 注册 HuggingFace 账号：https://huggingface.co/join
2. 访问 https://huggingface.co/pyannote/speaker-diarization-3.1 → 点击 "Agree and access repository"
3. 访问 https://huggingface.co/pyannote/segmentation-3.0 → 同上
4. 在 https://huggingface.co/settings/tokens 生成 access token
5. 将 token 填入前端配置，或设置环境变量 `HUGGINGFACE_TOKEN`

**国内网络加速**

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

---

## 验证方案

实现完成后，建议用同一段访谈音频分别跑两个后端，对比：

1. UI 中被下划线标注的词数量（误标率）
2. 说话人切换点是否与实际对齐
3. `smooth_word_speakers` 修正的词数（越少说明原始精度越高）

---

## 实现优先级

当前 `smooth_word_speakers` 已能覆盖大多数 CAM++ 噪声场景。建议在以下情况下再启动本方案开发：

- 用户反馈在某类视频（如多人同框、背景噪声大）中说话人分离持续误标
- CAM++ 平滑后仍有 > 5% 的词被错误标注说话人

---

## 参考资料

- [pyannote-audio GitHub](https://github.com/pyannote/pyannote-audio)
- [pyannote/speaker-diarization-3.1 模型卡](https://huggingface.co/pyannote/speaker-diarization-3.1)
- [WhisperX（faster-whisper + pyannote 集成示例）](https://github.com/m-bain/whisperX)
- [pyannote-ai 公开基准](https://www.pyannote.ai/benchmark)
