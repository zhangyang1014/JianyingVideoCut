---
name: html5-video-seek-optimization
description: HTML5 视频播放跳过优化模式。解决「保留字幕」播放模式下跳过大量删除片段时的卡顿（seek 风暴、rAF 空转、短保留段碎片、React 全量重渲染）。通过 rAF seek 守卫、CHAIN_GAP 合并、React.memo 等技术消除卡顿。触发场景：视频审核播放卡顿、跳过删除段、requestAnimationFrame 优化、HTMLVideoElement seek 调优。
---

# HTML5 视频跳过播放优化

本文档总结 GoldenClip「保留字幕」播放模式下通过 7 轮运行时调试解决卡顿的完整经验。

## 问题模式识别

当出现以下症状时，使用本 Skill：

- `playKeptOnly` 模式下跳过删除段时有明显卡顿/黑屏
- 保留段之间的碎片内容意外播出（"漏听"删除内容）
- 多次快速 seek 在短时间内连续触发

## 根因分类与修复

### 1. Seek 风暴（最常见，最严重）

**现象**：rAF 每帧（~8ms）都重复检测同一 delete 段并反复 seek，seek 期间主线程完全阻塞。

**根因**：seek 是异步操作，`video.currentTime` 赋值后需 50-200ms 才生效。下一帧 `vt` 仍在原位，delete 检测再次命中，触发第二次 seek。

**修复**：在触发 seek 的分支加 `programmaticSeekRef.current` 守卫：

```typescript
if (playKeptOnlyRef.current && !programmaticSeekRef.current) {
  // delete 段检测...
  if (deleteSeg) {
    programmaticSeekRef.current = true;
    video.currentTime = target;
    return;
  }
}
```

`onSeeked` 回调中重置：

```typescript
onSeeked={() => {
  if (programmaticSeekRef.current) {
    programmaticSeekRef.current = false;
    startRaf();
  }
}}
```

### 2. rAF 在 Seek 期间空转（每次 seek 浪费 12-13 帧）

**现象（日志证据）**：seek 触发后，rAF 继续空跑 90ms（约 12 帧），`video.currentTime` 不变，完全是无效调度。

**根因**：seek 时只 `return` 退出了当前 tick，但立即又调用 `requestAnimationFrame(tick)` 进入下一帧。

**修复**：seek 时彻底停止 rAF，`onSeeked` 中恢复：

```typescript
// seek 触发时
if (rafRef.current !== null) {
  cancelAnimationFrame(rafRef.current);
  rafRef.current = null;
}
video.currentTime = target;
return; // 不再调用 requestAnimationFrame

// onSeeked 中
if (programmaticSeekRef.current) {
  programmaticSeekRef.current = false;
  startRaf(); // 重启 rAF
}
```

**效果**：每次 seek 从浪费 12-13 次 rAF tick 降为 0。

### 3. 短保留段碎片（CHAIN_GAP 太小）

**现象（日志证据）**：

```text
Seek 1: 928→941 (chainCount=3, 109ms)
[播放 941-944 之间 3.07s 碎片内容，脱离语境]
Seek 2: 944→973 (chainCount=8, 27ms)
```

两次 seek 之间夹着 3.07s 的短保留段，内容脱离上下文显得突兀。

**根因**：`CHAIN_GAP` 定义了「合并相邻 delete 段」的最大间距阈值。设为 0.5 时，delete 段之间 >0.5s 的保留内容不会被跳过，导致频繁 seek + 听到碎片内容。

**调优参数**（经实测确定）：

| 参数 | 初始值 | 最终值 | 说明 |
| --- | --- | --- | --- |
| `CHAIN_GAP` | 0.5s | **3.5s** | <3.5s 的短保留段被跳过合并 |
| `LOOKAHEAD` | 0.3s | **0.15s** | 检测到 delete 段前多少秒触发 seek |

`CHAIN_GAP = 3.5` 意味着：两个 delete 段之间的保留内容若 <3.5s，视为"碎片"一并跳过。

**警告**：`LOOKAHEAD` 过大（0.5s）会截断保留段末尾音频，用户明显感知到句子被打断。0.15s 是平衡点：足够早触发 seek，但不截断保留内容。

### 4. React 全量重渲染（477 个组件每帧重渲染）

**现象**：每帧 `currentTime` 变化（60fps），477 个 `SegmentItem` 全部因 `activeCharTime` prop 变化而 re-render。

**根因**：`<SegmentItem activeCharTime={currentTime} />` 每帧给所有组件传入新值，且没有 `React.memo`。

**修复**：

1. 只给活跃段传入 `currentTime`，其他传稳定值 `-1`：

```tsx
<SegmentItem
  activeCharTime={seg.id === activeSegmentId ? currentTime : -1}
/>
```

1. 用 `memo` + 自定义比较器包裹组件（忽略回调函数引用变化）：

```tsx
const SegmentItem = memo(function SegmentItem({ ... }) {
  // 组件实现
}, (prev, next) =>
  prev.segment === next.segment &&
  prev.isActive === next.isActive &&
  prev.isSelected === next.isSelected &&
  prev.isLast === next.isLast &&
  prev.activeCharTime === next.activeCharTime &&
  prev.readOnly === next.readOnly &&
  prev.wordEditMode === next.wordEditMode &&
  prev.selectedWordIndices === next.selectedWordIndices
);
```

**效果**：每帧从渲染 477 个组件降为 1 个（仅活跃段）。

**注意**：列表中的回调函数（`onClick`、`onToggle` 等匿名函数）每次渲染都是新引用，会破坏 memo。需在自定义比较器中**忽略回调参数**，只比较数据类 props。

### 5. 其他优化（有效但次要）

**添加 `preload="auto"`**：让浏览器预缓存完整视频，减少 seek 时的 HTTP Range Request I/O 延迟。

```tsx
<video src={videoUrl} preload="auto" />
```

**移除 opacity 黑闪**：seek 期间设置 `opacity=0` 会触发 CSS transition 动画，加重卡顿感知。seek 时直接显示冻帧比黑屏更自然。

## 已证伪的假设（勿重复尝试）

| 假设 | 验证结果 |
| --- | --- |
| seek 前 `muted=true` 消除咔嗒声 | **反效果**：seek 耗时从 100ms 增至 150ms，音频管线重配置增加延迟 |
| `fastSeek()` 替代 `currentTime=x` | Chrome **不支持**（`typeof video.fastSeek === 'undefined'`），回退无效 |
| 外部 `currentTime` prop 变化触发二次 seek | **未发生**：`programmaticSeekRef` 守卫有效拦截 |
| segment 线性搜索 `Array.find()` 性能瓶颈 | **未发生**：477 段搜索耗时 <1ms，可忽略 |

## 调试埋点方法论

### 核心指标收集

```typescript
// seek 触发时
{ from: vt, to: target, chainCount, seekStartMs: Date.now() }

// onSeeked 时
{ videoTime: video.currentTime, paused: video.paused, readyState: video.readyState }

// rAF 空转检测（seek 期间仍触发说明存在问题）
{ vt, programmatic: programmaticSeekRef.current }
```

### 关键判断依据

| 指标 | 修复前 | 修复后 | 含义 |
| --- | --- | --- | --- |
| seek 次数/60s | 8 次 | 3 次 | CHAIN_GAP 合并有效 |
| rAF 空转次数/seek | 12-13 次 | 0 次 | 停止 rAF 有效 |
| seek 耗时 | 100-250ms | 50-130ms | I/O 缓存改善 |
| chainCount | 1-3 | 8-11 | 合并更多 delete 段 |

### 定位碎片内容来源

当用户反映"听到了不该出现的内容"时：

1. 查看 `tick-seek` 日志的 `from` 值
2. 对比 `onSeeked` 的 `videoTime` 值
3. 两者之间播放的内容 = `from → videoTime` 区间
4. 若该区间内有保留段，其时长即为"碎片时长"
5. 将 `CHAIN_GAP` 调大到该时长以上，即可合并跳过

## 完整参数配置（已调优）

```typescript
const LOOKAHEAD = 0.15;  // 提前 0.15s 检测（不截断保留内容）
const CHAIN_GAP = 3.5;   // <3.5s 短保留段视为碎片跳过
```

**参数调整建议**：

- 访谈类视频（停顿较长）：`CHAIN_GAP` 可调到 4-5s
- 演讲类视频（节奏紧凑）：`CHAIN_GAP` 可调到 2-3s
- `LOOKAHEAD` 建议保持 0.1-0.2s 范围，过小可能来不及 seek，过大截断保留内容
