# FFmpeg 视频导出优化开发文档

| 版本 | 日期 | 变更内容 | 变更人 |
|------|------|----------|--------|
| v1.0 | 2026-03-14 | 初版：修复黑屏、新增字幕、优化段合并 | 大象 |

---

## 功能概述

针对 FFmpeg 导出路径（`/export/ffmpeg`）的三个核心问题进行修复和优化：

1. **黑屏/无画面** — `-ss` 参数位置错误 + 源视频大关键帧间隔/B-frames
2. **无字幕** — FFmpeg 路径从未处理字幕数据
3. **句子间隙不自然** — 逐段切割引入编解码间隙，连续保留段应整合为大块

---

## 改动文件

| 文件 | 改动说明 |
|------|----------|
| `backend/services/ffmpeg_executor.py` | 修复 `-ss` 位置、改重编码模式、新增 `generate_srt()`、新增 `_merge_contiguous_chunks()` |
| `backend/models/task.py` | `ExportRequest` 新增 `burn_subtitles: bool = False` |
| `backend/main.py` | 导出前调用 `generate_srt()`，传入 `burn_subtitles` 参数 |
| `client/src/lib/api.ts` | `exportFFmpeg()` 新增 `burnSubtitles` 参数 |
| `client/src/pages/ReviewWorkbench.tsx` | `ExportPanel` 新增硬字幕烧录复选框 |

---

## 问题一：黑屏/无画面

### 根因分析

**阶段 1：`-ss` 参数位置错误**

原始代码将 `-ss` 放在 `-i` 之后：
```python
cmd = ["ffmpeg", "-y", "-i", video_path, "-ss", str(start), "-t", str(dur), "-c", "copy", output]
```

`-ss` 在 `-i` 后 = 慢速解码模式，FFmpeg 解码到目标时间点，但流复制（`-c copy`）必须从关键帧开始输出，导致 PTS/DTS 错位，部分播放器全程黑屏。

**修复**：将 `-ss` 移到 `-i` 之前。

**阶段 2：源视频大关键帧间隔 + B-frames**

通过 `ffprobe` 诊断源视频：
```
关键帧分布：-2.29s → 8.13s → 18.54s → 25.92s → 36.33s ...
has_b_frames: 2
```

- 关键帧间隔约 8-10 秒，ASR 切出的语音段通常 2-5 秒，大多数段内没有任何关键帧
- `has_b_frames=2`：B 帧依赖前后帧解码，流复制时被参考帧可能已被切掉
- 起始 PTS 为 -2.29s（负值 PTS 在 B-frame 编码中常见）

无论 `-ss` 放在哪，只要不重新编码，输出就是黑的。

**最终修复**：改用 libx264 重编码：
```python
cmd = [
    "ffmpeg", "-y",
    "-ss", str(chunk_start),
    "-i", video_path,
    "-t", str(duration),
    "-c:v", "libx264", "-preset", "fast", "-crf", "18",
    "-c:a", "aac", "-b:a", "192k",
    "-avoid_negative_ts", "make_zero",
    str(chunk_path)
]
```

---

## 问题二：无字幕

### 根因

`ffmpeg_executor.py` 全程无字幕逻辑，`seg.text` / `seg.display_text` 只在 `jianying_builder.py` 中被消费，FFmpeg 路径完全忽略。

### 解决方案

**软字幕（始终生成）：** 在每次导出前生成 `.srt` 文件，放在视频旁边。

**硬字幕（可选）：** 在 concat 阶段加 `-vf subtitles=` 滤镜，烧录进画面（需重编码）。

**SRT 时间轴公式**（与剪映草稿相同）：
```
SRT_start_n = Σ Duration_i (i=1..n-1)
SRT_end_n   = Σ Duration_i (i=1..n)
```

字幕文本优先级：`display_text`（修正文本）> `text`（ASR 原文）。

**前端设计：**
- 默认不勾选硬字幕（快速）
- 勾选后触发重编码（慢，但任何播放器均可见）
- 无论是否烧录，始终输出 `.srt` 文件

---

## 问题三：句子间隙不自然

### 根因

原实现对每个 kept segment 单独调一次 FFmpeg：

```
415 个保留段 → 415 次 FFmpeg 切割 → 415 个碎片 → concat
```

每次独立切割 + 拼接引入：
- **音频编解码延迟**：AAC 编码器有 ~20ms priming samples，每次切割/拼接引入微小静音
- **视频帧偏差**：24fps 每帧约 41.67ms，每次独立 seek 有 ±1 帧误差

### 错误的修复尝试

用间隙容差（0.05s）判断相邻段是否连续 → 实测 ASR 自然切句间隙分布：

```
gap_histogram:
  overlap (< 0):  101 个  ← ASR 精度导致的重叠
  <= 0.05s:        50 个  ← 只有这部分被合并
  <= 0.5s:        172 个  ← 自然停顿，不应切割
  > 0.5s:          91 个  ← 被删除的停顿
```

容差 0.05s 只合并了 50 个，415 段 → 319 块，几乎无效。

### 正确修复：以 delete 段为断点

```python
def _merge_contiguous_chunks(all_segments):
    chunks, chunk_start, chunk_end = [], None, None
    for seg in all_segments:
        if seg.action in _KEPT_ACTIONS:
            if chunk_start is None:
                chunk_start = seg.start
            chunk_end = seg.end
        else:  # delete → 当前块结束
            if chunk_start is not None:
                chunks.append((chunk_start, chunk_end))
                chunk_start = chunk_end = None
    if chunk_start is not None:
        chunks.append((chunk_start, chunk_end))
    return chunks
```

**结果：** 450 个总段（415 保留 + 35 删除）→ **20 个连续块**：

```
块1:  29.78s → 35.76s (6.0s)
块2:  36.31s → 49.74s (13.4s)
...
块9:  737.24s → 997.75s (260.5s)   ← 260 秒连续内容，一刀切
块10: 998.79s → 1447.99s (449.2s)  ← 449 秒连续内容，一刀切
...
```

FFmpeg 调用次数：415 → 20，导出速度提升约 20 倍。连续保留的句子完全保持原始音视频节奏。

---

## 架构图：优化后的导出流程

```
audit_segments (全部段，含 delete)
        ↓
_merge_contiguous_chunks()
        ↓
chunks: [(start1,end1), (start2,end2), ...]  ← 只在 delete 处断开
        ↓
for each chunk:
    ffmpeg -ss start -i video -t dur -c:v libx264 -crf 18 chunk_N.mp4
        ↓
generate_srt()  ← 按段粒度计算 SRT 时间轴（保留 display_text 覆盖）
        ↓
若只有 1 个 chunk → shutil.move (跳过 concat)
若多个 chunk → ffmpeg -f concat → output.mp4
若 burn_subtitles → ffmpeg concat + -vf subtitles= (重编码)
```

---

## 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 切割编码方式 | libx264 重编码 | 源视频关键帧间隔大（8-10s）+ B-frames，流复制无法正确切割 |
| 段合并判断依据 | delete 段位置 | ASR 自然间隙 0.07-0.5s，容差判断会漏合并大量连续段 |
| SRT 软字幕 | 始终生成 | 轻量，播放器支持，不影响导出速度 |
| 硬字幕 | 可选（前端复选框） | 需二次编码，速度慢，适合需要独立视频的场景 |
| 单块无 concat | `shutil.move` | 省略不必要的 concat 步骤 |
| `-preset fast` | 而非 `medium`/`slow` | 画质几乎无差异（CRF 18 接近无损），速度快 2-4 倍 |
