# 一键转英文需求与交接契约

## 目标

新增视频处理类型：`一键转英文`（内部类型 `one_click_translate`）。

它用于把 1 个或多个原始视频自动转换为目标语言配音视频。默认目标语言是英文，但模型和数据结构支持其他目标语言。

## 用户流程

1. 用户新建任务，选择 `一键转英文`。
2. 用户上传 1 个或多个视频，视频长度不限。
3. 用户选择：
   - 原语言：支持自动检测、中文、英文、日文、韩文等。
   - 目标语言：默认英文。
   - 字幕校准和翻译模型：本地 Ollama 或 API 大语言模型。
   - 语音生成模型：API TTS 模型，或测试用的对齐静音占位。
4. 用户确认后，系统自动启动流水线：
   - ASR 识别原语言并生成源字幕。
   - 调用字幕模型校准字幕。
   - 基于校准字幕翻译为目标语言。
   - 调用 TTS 生成目标语言音频片段。
   - 按原字幕 `start` 时间对齐音频片段，合成完整目标语言音轨。
   - 移除原视频音轨，替换为新音轨，生成预览视频。
5. 用户预览结果，可以修改中间字幕 cue。
6. 用户一键导出。系统会重新生成缺失或过期的 TTS 音频，并生成最终视频。

## 核心数据结构

任务类型：

- `Task.task_type = "one_click_translate"`

批量视频：

- `Task.translation_videos[]`
- 每个视频是一个 `TranslationVideo`

交接单元：

- `TranslationCue`
- 每条 cue 保持原视频时间轴：
  - `id`
  - `source_segment_id`
  - `start`
  - `end`
  - `source_text`
  - `calibrated_text`
  - `translated_text`
  - `audio_path`
  - `audio_text`

`start/end` 在整个流程中不允许被模型改写。TTS 音频通过 `start` 对齐到原视频时间线。

## 阶段交接

### 1. 上传视频

输入：

- 用户上传的视频文件。

输出：

- `TranslationVideo.video_path`
- `TranslationVideo.duration`
- `TranslationVideo.thumbnail_path`
- `TranslationVideo.status = "uploaded"`

校验：

- 文件扩展名必须是 MP4/MOV/AVI/MKV/WebM。
- 至少上传 1 个视频才能启动流水线。

### 2. ASR 识别

输入：

- `TranslationVideo.video_path`
- 用户选择的 ASR backend/model。

输出：

- `TranslationVideo.asr_result`
- `TranslationVideo.cues[]`
- `source.srt`
- `TranslationVideo.status = "asr_done"`

校验：

- 每个 cue 必须 `end > start`。
- cue 必须按 `start` 升序排列。
- cue 的 `source_segment_id` 来自 ASR segment id。

### 3. 字幕校准

输入：

- `TranslationCue.source_text`
- 原语言、目标语言和字幕模型配置。

输出：

- `TranslationCue.calibrated_text`
- `calibrated.srt`

校验：

- 模型输出必须保留所有 cue id。
- 不允许新增、删除、重排 cue。
- 不允许模型修改 `start/end`。

### 4. 翻译

输入：

- `TranslationCue.calibrated_text`
- 目标语言。

输出：

- `TranslationCue.translated_text`
- `translated.srt`
- `TranslationVideo.status = "translated"`

校验：

- 每条 cue 必须有非空 `translated_text`。
- cue id 集合必须和 ASR 阶段一致。

### 5. TTS 生成

输入：

- `TranslationCue.translated_text`
- TTS provider/model/voice。

输出：

- 每条 cue 的 `audio_path`
- `audio_text`
- `audio_duration`
- `TranslationCue.status = "audio_done"`

校验：

- `audio_path` 必须存在。
- `audio_text` 记录生成音频时使用的字幕文本。
- 用户修改 `translated_text` 后，旧的 `audio_path/audio_text/audio_duration` 会被清空，防止字幕和配音错配。

### 6. 音轨对齐

输入：

- 所有 cue 的音频文件。
- 原视频时长。

输出：

- `aligned_audio.m4a`

校验：

- 每个音频片段使用 cue 的 `start` 计算 delay。
- 合成音轨长度裁剪到原视频时长。

### 7. 视频合成

输入：

- 原视频画面。
- `aligned_audio.m4a`。

输出：

- `preview.mp4` 或 `final.mp4`

校验：

- FFmpeg 使用原视频视频流和新音轨。
- 原音轨不会进入输出视频。
- 预览成功后任务进入 `review`。
- 最终导出成功后任务进入 `done`。

## 失败处理

- 单个视频任意阶段失败时只标记该视频：`TranslationVideo.status = "error"`，错误写入 `TranslationVideo.error_message`，流水线继续处理下一个视频（失败隔离）。
- 全部视频处理完成后：至少 1 个视频成功出预览则任务进入 `review`（`Task.error_message` 记录失败摘要）；全部失败才进入 `error`。
- 失败的视频可通过单视频重跑接口断点续跑：按 `validate_stage_ready` 契约检查已有产物，从第一个未就绪的阶段继续（ASR → 翻译 → TTS → 混流），避免重复消耗 ASR/LLM/TTS 花费。
- 后端重启时，运行中状态会回退到 `pending`，避免 UI 永久卡住。
- 用户修改字幕时不允许改变 cue id 集合，防止时间轴断裂。

## API

- `POST /api/tasks/{task_id}/one-click-translate/videos`
  - 上传一个批处理视频，可重复调用。
- `POST /api/tasks/{task_id}/one-click-translate/start`
  - 启动 ASR → 校准 → 翻译 → TTS → 预览合成。
- `POST /api/tasks/{task_id}/one-click-translate/videos/{video_id}/retry`
  - 单视频断点续跑。请求体复用 start 的参数（API key 不落盘）；`force_restart: true` 时从 ASR 从头重跑，否则根据已有产物自动判断续跑阶段。
- `PUT /api/tasks/{task_id}/one-click-translate/videos/{video_id}/cues`
  - 更新中间字幕 cue。
- `POST /api/tasks/{task_id}/one-click-translate/export`
  - 基于当前字幕重新生成缺失/过期音频并导出最终视频。
- `GET /api/tasks/{task_id}/one-click-translate/videos/{video_id}/preview`
  - 读取预览视频。

