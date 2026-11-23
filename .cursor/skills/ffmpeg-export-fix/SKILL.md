---
name: ffmpeg-export-fix
description: FFmpeg 视频导出三大问题修复模式：①-ss 位置错误导致黑屏、②大关键帧间隔/B-frames 必须重编码而非流复制、③逐段切割引入拼接间隙（应按 delete 段合并连续块）。适用于 ASR 切段后用 FFmpeg 导出剪辑视频的场景。触发场景：导出视频黑屏、画面异常、句子间隙不自然、视频乱序/重复、字幕缺失、FFmpeg -c copy 黑屏。
---

# FFmpeg 视频导出修复模式

## 核心问题总览

| 症状 | 根因 | 修复 |
|------|------|------|
| 导出视频全程黑屏 | `-ss` 在 `-i` 之后 + `-c copy` 导致 PTS/DTS 错位 | 将 `-ss` 移到 `-i` 之前 |
| 黑屏（关键帧间隔大的源） | 源视频关键帧间隔 8-10s + B-frames，流复制无法精确切割 | 改用 `-c:v libx264 -c:a aac` 重编码 |
| 连续句子之间有间隙 | 每个 ASR 段单独切割再拼接，编解码引入微小延迟 | 用 delete 段为断点合并连续保留段再切割 |
| 无字幕 | FFmpeg 路径从未消费 `seg.text` 字段 | 生成 SRT 文件 + 可选硬字幕烧录 |

---

## 1. `-ss` 参数位置（关键帧 seek）

**错误：**
```python
cmd = ["ffmpeg", "-y", "-i", video_path, "-ss", str(start), "-t", str(dur), "-c", "copy", output]
```
`-ss` 在 `-i` 后 + `-c copy`：FFmpeg 解码到目标时间，但流复制必须从关键帧输出，导致 PTS/DTS 错位 → 黑屏。

**正确：**
```python
cmd = ["ffmpeg", "-y", "-ss", str(start), "-i", video_path, "-t", str(dur), ...]
```
`-ss` 在 `-i` 前：关键帧快速 seek 模式，从最近关键帧开始，无黑屏问题。

---

## 2. 源视频编码诊断

在切割前用 `ffprobe` 检查源视频：

```bash
# 查看关键帧间隔和 B-frames
ffprobe -v quiet -show_entries stream=has_b_frames,codec_name -of compact video.mp4

# 查看实际关键帧分布（只取前 20 个）
ffprobe -v quiet -select_streams v -show_entries packet=pts_time,flags -of csv video.mp4 | grep ",K" | head -20
```

**判断标准：**
- `has_b_frames=2` 或关键帧间隔 > 5s → 必须重编码，不能用 `-c copy`
- 标准录制视频（GoPro、手机、剪映导出）通常关键帧间隔 8-10s

**重编码参数：**
```python
cmd = [
    "ffmpeg", "-y",
    "-ss", str(chunk_start),
    "-i", video_path,
    "-t", str(duration),
    "-c:v", "libx264",
    "-preset", "fast",     # 速度与压缩平衡
    "-crf", "18",          # 接近视觉无损
    "-c:a", "aac",
    "-b:a", "192k",
    "-avoid_negative_ts", "make_zero",
    output_path
]
```

---

## 3. 连续段合并（核心优化）

**错误做法：** 对每个 kept segment 单独调 FFmpeg → N 次切割 + 拼接，引入间隙。

**正确做法：** 以 `delete` 段为断点，将连续保留段合并为大块（chunk）。

```python
def _merge_contiguous_chunks(all_segments: List[Segment]) -> List[Tuple[float, float]]:
    """
    遍历全部段（含 delete），以 delete 段为断点合并连续保留段。
    不要用间隙容差判断——ASR 自然切句间隙 0.07-0.5s 均属正常。
    """
    chunks = []
    chunk_start = chunk_end = None

    for seg in all_segments:
        if seg.action in _KEPT_ACTIONS:
            if chunk_start is None:
                chunk_start = seg.start
            chunk_end = seg.end
        else:  # delete 段：断开当前块
            if chunk_start is not None:
                chunks.append((chunk_start, chunk_end))
                chunk_start = chunk_end = None

    if chunk_start is not None:
        chunks.append((chunk_start, chunk_end))
    return chunks
```

**效果：** 415 个保留段 → 20 个连续块，FFmpeg 调用次数从 415 降到 20，连续句子间无拼接间隙。

**关键陷阱：** 不要用间隙容差（`abs(gap) < 0.05`）判断连续性，ASR 相邻段间隙通常 0.07-0.5s，会导致几乎不合并。

---

## 4. SRT 字幕生成

```python
def generate_srt(segments, srt_path, params=None):
    """按累积偏移计算时间轴（与剪映草稿公式相同）"""
    kept = [s for s in segments if s.action in _KEPT_ACTIONS]
    lines, cursor, idx = [], 0.0, 1
    for seg in kept:
        dur = seg.end - seg.start
        text = (seg.display_text or seg.text or "").strip()
        if text:
            lines += [str(idx), f"{_fmt(cursor)} --> {_fmt(cursor + dur)}", text, ""]
            idx += 1
        cursor += dur
    Path(srt_path).parent.mkdir(parents=True, exist_ok=True)
    Path(srt_path).write_text("\n".join(lines), encoding="utf-8")

def _fmt(s):
    h, m = int(s//3600), int((s%3600)//60)
    sec, ms = int(s%60), int(round((s-int(s))*1000))
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"
```

**硬字幕烧录（concat 阶段完成，避免二次转码）：**
```python
# SRT 路径转义（macOS 路径含冒号）
escaped = srt_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
       "-vf", f"subtitles='{escaped}'", "-c:v", "libx264", "-crf", "18",
       "-c:a", "aac", output_path]
```

---

## 常见 Bug

| 症状 | 根因 | 修复 |
|------|------|------|
| 改了 `-ss` 位置还是黑屏 | 源视频关键帧间隔大（>5s）+ B-frames | 必须 `-c:v libx264` 重编码 |
| 合并块数与 kept 段数一样 | 用间隙容差（0.05s）判断连续性，ASR 间隙通常远大于此 | 改用 delete 段作为断点 |
| 视频乱序/重复 | ASR 段未按 start 排序，或 merge_next 段与原段同时保留 | 检查 `audit_segments` 是否有重叠或乱序 |
| 硬字幕路径报错 | macOS 路径含冒号，libass 语法需转义 | `srt_path.replace(":", "\\:")` |
