---
name: fastapi-async-task-cancel
description: FastAPI + asyncio 后台任务取消模式，结合 subprocess.Popen 子进程终止与 WebSocket 状态广播。解决 asyncio.create_task 无引用无法取消、Popen 子进程无法中止、前端状态不同步等问题。适用于需要支持"终止任务"功能的长耗时后台任务（如 FFmpeg 导出、大模型推理、批量处理）。触发场景：为后台任务加中止按钮、任务管理、asyncio 取消、subprocess 终止、WebSocket 状态推送。
---

# FastAPI 后台任务可取消模式

## 核心三层架构

```
前端"终止"按钮
    → POST /api/tasks/{id}/cancel
        → kill subprocess (Popen.terminate)
        → asyncio_task.cancel()          # 触发 CancelledError
            → except CancelledError 做清理
                → broadcast status_change via WebSocket
                    → 前端 loadTask() 刷新 UI
```

---

## 1. subprocess 进程注册表（executor 层）

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
            proc.terminate()   # SIGTERM，FFmpeg 会自动清理临时文件
        except Exception:
            pass
        return True
    return False
```

在调用 `Popen` 后立即注册，在 `communicate()` 的 `finally` 块中注销：

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

---

## 2. asyncio Task 引用表（API 层）

```python
_export_tasks: Dict[str, asyncio.Task] = {}

@app.post("/api/tasks/{task_id}/export/ffmpeg")
async def export_ffmpeg(task_id: str, req: ExportRequest):
    # ...校验...
    t = asyncio.create_task(_run_ffmpeg_export(task_id, req))
    _export_tasks[task_id] = t          # 保存引用，不要丢弃！
    return {"success": True}

@app.post("/api/tasks/{task_id}/export/cancel")
async def cancel_export(task_id: str):
    task = store.get(task_id)
    if task.status != TaskStatus.EXPORT_RUNNING:
        raise HTTPException(400, "No export running")

    kill_export(task_id)               # 先杀子进程

    t = _export_tasks.pop(task_id, None)
    if t and not t.done():
        t.cancel()                     # 触发协程内 CancelledError

    return {"success": True}
```

---

## 3. 协程内 CancelledError 处理

```python
async def _run_ffmpeg_export(task_id: str, req):
    task.status = TaskStatus.EXPORT_RUNNING
    store.update(task)
    # ✅ 关键：状态变更后立即广播，否则前端永远不知道
    await manager.broadcast(task_id, {
        "type": "status_change", "status": "export_running"
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
        # 回滚状态，不要重新 raise
        task.status = TaskStatus.REVIEW
        store.update(task)
        await manager.broadcast(task_id, {"type": "export_cancelled", ...})
        await manager.broadcast(task_id, {"type": "status_change", "status": "review"})

    except Exception as e:
        task.status = TaskStatus.ERROR
        store.update(task)

    finally:
        _export_tasks.pop(task_id, None)   # 任何路径都清理
```

---

## 4. 前端：WebSocket 消息与状态同步

**常见坑**：只监听 `export_done`，遗漏 `status_change` 和 `export_cancelled`。

```typescript
// 正确做法：所有会导致 task.status 变化的消息类型都 loadTask()
if (
  msg.type === "status_change" ||
  msg.type === "export_done" ||
  msg.type === "export_cancelled"
) {
  loadTask();
}
```

**终止按钮条件渲染**（与导出按钮同位置互换）：

```tsx
{isExporting ? (
  <Button
    className="bg-destructive text-destructive-foreground"
    onClick={onCancelExport}
  >
    <Loader2 className="animate-spin" />
    终止导出
  </Button>
) : (
  <Button onClick={() => onExportFFmpeg(burnSubtitles)}>
    FFmpeg 快速导出
  </Button>
)}
```

别忘了把 `export_running` 加入显示 Panel 的条件判断：
```tsx
{(status === "review" || status === "done" || status === "export_running") && (
  <ExportPanel ... />
)}
```

---

## 常见 Bug 清单

| 症状 | 根因 | 修复 |
|------|------|------|
| 终止按钮不出现 | 状态变更后未广播 `status_change` | 在 `store.update()` 后立即 `broadcast` |
| 终止后按钮不消失 | 前端未处理 `export_cancelled` 消息 | 加入 `loadTask()` 触发条件 |
| 进程仍在跑 | `asyncio.create_task()` 返回值未保存 | 存入 `_export_tasks` 字典 |
| `cancel()` 无效 | 子进程阻塞了事件循环 | 先 `kill_export()`（Popen.terminate），再 `task.cancel()` |
| 临时文件残留 | `finally` 清理在 `except` 后不执行 | 用 `try/except CancelledError` + `finally` 双重保障 |
