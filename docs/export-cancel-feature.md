# 导出终止功能开发文档

| 版本 | 日期 | 变更内容 | 变更人 |
|------|------|----------|--------|
| v1.0 | 2026-03-14 | 初版：实现 FFmpeg 导出中止功能 | 大象 |

---

## 功能概述

在 FFmpeg 视频导出过程中，支持用户随时点击"终止导出"按钮中断任务。后端杀掉 FFmpeg 子进程、取消 asyncio 协程，将任务状态回滚为 `review`；前端通过 WebSocket 实时收到通知后刷新 UI，导出按钮恢复为可用状态。

---

## 改动文件

| 文件 | 改动说明 |
|------|----------|
| `backend/services/ffmpeg_executor.py` | 新增进程注册表，支持按 task_id 终止子进程 |
| `backend/main.py` | 保存 asyncio.Task 引用，新增取消端点，补充 WebSocket 状态广播 |
| `client/src/lib/api.ts` | 新增 `cancelExport` HTTP 调用函数 |
| `client/src/pages/ReviewWorkbench.tsx` | ExportPanel 新增终止按钮，补全 WebSocket 消息处理 |

---

## 架构设计

### 数据流（终止时）

```
用户点击"终止导出"
  → POST /api/tasks/{id}/export/cancel
      → ffmpeg_executor.kill_export(task_id)   → proc.terminate()
      → _export_tasks[task_id].cancel()        → 协程收到 CancelledError
          → task.status = REVIEW
          → store.update(task)
          → broadcast: export_cancelled
          → broadcast: status_change(review)
              → 前端 onMessage → loadTask()
                  → ExportPanel isExporting=false → 显示"FFmpeg 快速导出"
```

---

## 后端实现

### `ffmpeg_executor.py` — 进程注册表

```python
_active_processes: dict[str, subprocess.Popen] = {}

def register_process(task_id: str, proc: subprocess.Popen) -> None:
    _active_processes[task_id] = proc

def unregister_process(task_id: str) -> None:
    _active_processes.pop(task_id, None)

def kill_export(task_id: str) -> bool:
    proc = _active_processes.pop(task_id, None)
    if proc:
        try:
            proc.terminate()
        except Exception:
            pass
        return True
    return False
```

`_run_ffmpeg` 在 `Popen` 后立即注册，`communicate()` 的 `finally` 块中注销：

```python
def _run_ffmpeg(cmd, task_id=None):
    process = subprocess.Popen(cmd, stdout=PIPE, stderr=PIPE, text=True)
    if task_id:
        register_process(task_id, process)
    try:
        stdout, stderr = process.communicate()
    finally:
        if task_id:
            unregister_process(task_id)
    return process.returncode == 0, stderr or stdout
```

`export_ffmpeg_lossless` 新增 `task_id` 参数并透传给所有 `_run_ffmpeg` 调用。

---

### `main.py` — 任务引用表 + 取消端点

**Task 引用表：**

```python
_export_tasks: Dict[str, asyncio.Task] = {}
```

**导出端点保存引用：**

```python
@app.post("/api/tasks/{task_id}/export/ffmpeg")
async def export_ffmpeg(task_id: str, req: ExportRequest):
    t = asyncio.create_task(_run_ffmpeg_export(task_id, req))
    _export_tasks[task_id] = t   # 保存引用，否则无法取消
    return {"success": True}
```

**取消端点：**

```python
@app.post("/api/tasks/{task_id}/export/cancel")
async def cancel_export(task_id: str):
    task = store.get(task_id)
    if task.status != TaskStatus.EXPORT_RUNNING:
        raise HTTPException(400, "No export running for this task")

    kill_export(task_id)   # 先终止子进程（解除 communicate() 阻塞）

    t = _export_tasks.pop(task_id, None)
    if t and not t.done():
        t.cancel()         # 让协程抛出 CancelledError

    return {"success": True}
```

**协程内 CancelledError 处理：**

```python
async def _run_ffmpeg_export(task_id: str, req):
    task.status = TaskStatus.EXPORT_RUNNING
    store.update(task)
    # 关键：状态变更后立即广播，前端依赖此消息切换 UI
    await manager.broadcast(task_id, {
        "type": "status_change", "task_id": task_id, "status": "export_running"
    })

    try:
        success = await export_ffmpeg_lossless(..., task_id=task_id)
        if success:
            task.status = TaskStatus.DONE
            store.update(task)
            await manager.broadcast(task_id, {"type": "export_done", ...})
        else:
            task.status = TaskStatus.ERROR
            store.update(task)

    except asyncio.CancelledError:
        task_fresh = store.get(task_id)
        if task_fresh:
            task_fresh.status = TaskStatus.REVIEW
            store.update(task_fresh)
        await manager.broadcast(task_id, {"type": "export_cancelled", "task_id": task_id})
        await manager.broadcast(task_id, {"type": "status_change", "task_id": task_id, "status": "review"})
        # 不重新抛出，让 asyncio 正常回收 Task

    except Exception as e:
        task.status = TaskStatus.ERROR
        task.error_message = str(e)
        store.update(task)

    finally:
        _export_tasks.pop(task_id, None)   # 任何路径都清理
```

---

## 前端实现

### `api.ts` — cancelExport

```typescript
export async function cancelExport(taskId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/export/cancel`, {
    method: "POST",
  });
  if (!res.ok) throw new Error("Failed to cancel export");
}
```

### `ReviewWorkbench.tsx` — WebSocket 消息处理

```typescript
// 补全：export_cancelled 也需要 loadTask()
if (
  msg.type === "status_change" ||
  msg.type === "export_done" ||
  msg.type === "export_cancelled"
) {
  loadTask();
}
```

### ExportPanel — 终止按钮

```tsx
{isExporting ? (
  <Button
    className="flex-1 bg-destructive text-destructive-foreground hover:bg-destructive/90"
    onClick={onCancelExport}
  >
    <Loader2 className="w-3 h-3 animate-spin" />
    终止导出
  </Button>
) : (
  <Button onClick={() => onExportFFmpeg(burnSubtitles)}>
    <Download className="w-3 h-3" />
    FFmpeg 快速导出
  </Button>
)}
```

ExportPanel 的显示条件也需要加入 `export_running`：

```tsx
{(task.status === "review" ||
  task.status === "done" ||
  task.status === "asr_done" ||
  task.status === "export_running") && (
  <ExportPanel ... onCancelExport={handleCancelExport} />
)}
```

---

## 排查过程中发现的隐蔽 Bug

### Bug：终止按钮不出现（export_running 状态下仍显示旧按钮）

**现象**：console 在切割 FFmpeg 块，但按钮依然是黄色"FFmpeg 快速导出"。

**根因**：后端 `_run_ffmpeg_export` 将 `task.status` 改为 `EXPORT_RUNNING` 后，只调用了 `store.update(task)`，**没有广播 WebSocket 消息**。前端 WebSocket 消息处理只在收到 `status_change` 或 `export_done` 时才 `loadTask()`，导致前端 `task.status` 永远停留在 `done`（上次导出完成后的状态），`isExporting` 始终为 false。

**修复**：在 `store.update(task)` 后立即添加：

```python
await manager.broadcast(task_id, {
    "type": "status_change",
    "task_id": task_id,
    "status": "export_running"
})
```

**规律**：只要后端更改了 task.status，就必须配套广播 `status_change`；`store.update()` 只持久化到内存/磁盘，对已连接的前端 WebSocket 没有任何通知效果。

---

## 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 终止后任务回滚状态 | `REVIEW`（而非 `ERROR`） | 终止是用户主动行为，不是错误，审计结果依然有效 |
| 子进程终止信号 | `SIGTERM`（`proc.terminate()`） | FFmpeg 收到 SIGTERM 后会做正常退出清理，比 `SIGKILL` 更安全 |
| 终止端点返回时机 | kill + cancel 后立即返回 | 不等协程真正结束，避免 HTTP 请求超时；清理由协程内 `CancelledError` 完成 |
| 临时文件清理 | 由原有 `finally: shutil.rmtree(temp_dir)` 负责 | CancelledError 也会走到 finally，无需额外处理 |
