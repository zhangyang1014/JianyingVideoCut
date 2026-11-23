---
name: srt-chunk-timestamp-fix
description: SRT 字幕时间轴漂移修复模式。当使用 chunk 合并策略导出视频时，SRT 生成算法若只累加段自身 duration 而忽略同 chunk 内段间停顿，会导致字幕越来越超前（视频越长漂移越大，如 270 段视频在末尾漂移可达 170s）。修复方案：改用 chunk 结构计算时间轴，每段输出时间 = chunk_cursor + (seg.start - chunk_start)。触发场景：字幕烧录后前段对齐后段漂移、ASR 切段导出字幕不同步、SRT 时间轴与视频不一致。
---

# SRT 字幕时间轴漂移修复模式

## 版本记录

| 版本 | 日期 | 变更内容 | 变更人 |
|------|------|----------|--------|
| v1.0 | 2026-03-15 | 初始创建，记录字幕漂移根因分析与修复方案 | 大象 |

---

## 核心问题总览

| 症状 | 根因 | 修复 |
|------|------|------|
| 字幕前段对齐、后段越来越超前 | SRT 时间轴只累加段自身 duration，未计入同 chunk 内段间停顿 | 改用 chunk 结构计算：`out_start = chunk_cursor + (seg.start - chunk_start)` |
| 视频越长漂移越严重 | 每个段间停顿误差累积（0.1s~25s 不等） | 同上 |
| 长视频末尾字幕超前数分钟 | 累积误差随段数线性增长 | 同上 |

---

## 1. 根因分析

### 背景：chunk 合并导出策略

GoldenClip（以及类似的 ASR 切段剪辑工具）在导出视频时，采用「以 delete 段为断点合并连续保留段」的策略：

```
原始视频:  [seg1 kept][seg2 kept][  del  ][seg3 kept][seg4 kept]
合并结果:  [   chunk A (seg1+gap+seg2)   ][   chunk B (seg3+gap+seg4)   ]
```

每个 chunk 通过一次 FFmpeg 调用切割，chunk 内部的段间停顿（呼吸、自然停顿）完整保留在输出视频中。

### 问题：旧 SRT 生成算法

```python
# ❌ 错误做法：只累加段自身 duration
cursor = 0.0
for seg in kept:
    duration = seg.end - seg.start
    # 写入 cursor → cursor + duration
    cursor += duration  # ← 漏掉了 seg 与前一个 seg 之间的停顿！
```

**漏计示意：**

```
实际视频时间轴:  0────2.0──2.15────4.0 (chunk A 共 4.0s，含 0.15s 停顿)
SRT 时间轴:      0────2.0──2.0─────3.85 (seg B 从 2.0s 开始，少了 0.15s)
                                    ↑ 字幕超前 0.15s
```

每个段间停顿（0.1~0.5s 正常停顿，偶有 5~25s 长停顿）都会引入误差，随段数累积。

**实测数据（270 段视频）：**
- 段间停顿总量（含跨 chunk）：169.9s
- 实际漏计的 within-chunk 停顿：111.6s
- SRT 旧算法时间轴总长：1596.5s
- 修复后时间轴总长：1708.2s（与实际视频时长吻合）

---

## 2. 正确修复方案

### 核心公式

```
每个段在输出视频中的时间 = chunk_cursor + (seg.start - chunk_start)
```

其中：
- `chunk_cursor`：前面所有已完成 chunk 的时长之和
- `seg.start - chunk_start`：该段在当前 chunk 内的偏移（自然包含前面段的停顿）

### 完整实现

```python
def generate_srt(
    segments: List[Segment],
    srt_path: str,
    params: Optional[TaskParams] = None,
) -> str:
    """
    时间轴按 chunk 结构计算，与实际导出视频完全对齐：
        输出时间 = chunk_cursor + (seg.start - chunk_start)
    其中 chunk_cursor 是前面所有 chunk 的时长累加，
    这样同一 chunk 内段与段之间的自然停顿也会被正确计入。
    """
    chunks = _merge_contiguous_chunks(segments)
    kept = [s for s in segments if s.action in _KEPT_ACTIONS]

    lines: List[str] = []
    idx = 1
    chunk_cursor = 0.0  # 前面所有 chunk 在输出视频中的累积时长

    for chunk_start, chunk_end in chunks:
        chunk_duration = chunk_end - chunk_start
        # 找出属于本 chunk 的保留段（按 start 位置判断）
        chunk_segs = [s for s in kept if chunk_start <= s.start and s.end <= chunk_end + 0.001]

        for seg in chunk_segs:
            # 在输出视频中的精确时间位置
            out_start = chunk_cursor + (seg.start - chunk_start)
            out_end = chunk_cursor + (seg.end - chunk_start)
            text = _get_subtitle_text(seg, params)
            if text:
                lines.append(str(idx))
                lines.append(f"{_format_srt_time(out_start)} --> {_format_srt_time(out_end)}")
                lines.append(text)
                lines.append("")
                idx += 1

        chunk_cursor += chunk_duration

    Path(srt_path).parent.mkdir(parents=True, exist_ok=True)
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return srt_path
```

---

## 3. 关键陷阱

### 陷阱 1：段间停顿不只是短暂停顿

实测中存在 5s、8s、甚至 25s 的段间停顿（两个 kept 段之间无 delete 段，但有长达数十秒的自然沉默）。这类大停顿若被漏计，会造成几十秒的突变漂移。

### 陷阱 2：chunk_segs 的筛选需要容差

由于浮点精度，`seg.end` 可能略大于 `chunk_end`，筛选时需加小容差：

```python
chunk_segs = [s for s in kept if chunk_start <= s.start and s.end <= chunk_end + 0.001]
```

### 陷阱 3：旧算法「前段正确」是假象

旧算法在视频开始时看起来正常（因为前几段停顿小），但误差是线性累积的，在 30~60s 后开始明显，视频越长越严重。用户描述「40s 后开始对不上」正是此特征。

### 陷阱 4：SRT 时间轴 ≠ 段列表累积

**SRT 的时间轴必须与实际输出视频的时间轴一致**，而实际视频由 chunk 结构决定。任何 SRT 生成算法必须从 chunk 边界出发计算，而非从段列表直接累加。

---

## 4. 诊断方法

若怀疑存在字幕漂移，可在 `generate_srt` 中加入以下临时诊断日志：

```python
_prev_end = None
_total_gap = 0.0
for seg in kept:
    if _prev_end is not None:
        gap = seg.start - _prev_end
        if gap > 0.01:
            _total_gap += gap
            print(f"[诊断] 段间间隙 {gap:.3f}s，累计漏计 {_total_gap:.3f}s")
    _prev_end = seg.end
print(f"[诊断] 共漏计停顿 {_total_gap:.3f}s（若 > 1s 则存在显著漂移风险）")
```

若 `_total_gap > 1s`，说明旧算法存在漂移，需切换为 chunk 结构算法。

---

## 5. 适用范围

本问题和修复方案适用于所有满足以下条件的场景：

1. **ASR/手动切段** → 生成若干 kept/delete 段列表
2. **chunk 合并导出** → 连续 kept 段合并为大块一次切割
3. **SRT/字幕生成** → 基于段列表生成时间轴

典型工具链：GoldenClip、剪映脚本导出、任何使用 FFmpeg concat demuxer 拼接的字幕烧录流程。
