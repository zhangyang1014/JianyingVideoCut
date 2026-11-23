"""
GoldenClip FastAPI Backend
Local video editing workstation API server.

Design: 暗金剪辑台 · 编导美学
Run: uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

本文件只负责创建 app、注册中间件和挂载各域 router。
路由实现按域拆分在 backend/routers/，共享单例状态在 backend/core/。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import asr as asr_router
from .routers import audit as audit_router
from .routers import config as config_router
from .routers import execution_preview as execution_preview_router
from .routers import export as export_router
from .routers import highlight_reels as highlight_reels_router
from .routers import learning as learning_router
from .routers import subtitles as subtitles_router
from .routers import tasks as tasks_router
from .routers import translate as translate_router
from .routers import productions as productions_router

# ---- 共享状态 re-export（保持 `from backend.main import ...` 兼容） ----
from .core.paths import (  # noqa: F401
    BASE_DIR, UPLOADS_DIR, EXPORTS_DIR, THUMBNAILS_DIR,
    TRANSLATION_UPLOADS_DIR, LEARNING_UPLOADS_DIR,
)
from .core.ws import ConnectionManager, manager, log_to_ws  # noqa: F401
from .core.task_runner import (  # noqa: F401
    _learning_active_tasks, _export_tasks, _task_runners, _task_runner_statuses, _CANCEL_ROLLBACK_MAP,
    _start_task_runner, _task_runner_superseded, _rollback_cancelled_task,
    _cancel_learning_task, get_task_runtime_health, shutdown_background_tasks,
)
from .core.serialization import (  # noqa: F401
    TASK_LIST_EXCLUDE_FIELDS, LEARNING_TASK_LIST_EXCLUDE_FIELDS,
    serialize_task_list_item, serialize_learning_task_list_item,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    productions_router.store.recover()
    productions_router.resume_heygen_jobs()
    try:
        yield
    finally:
        await productions_router.shutdown()
        await shutdown_background_tasks()
        await manager.close_all()


app = FastAPI(
    title="GoldenClip API",
    description="智能视频工作站 - 编导思维驱动的本地剪辑工具",
    version="3.0.0",
    lifespan=lifespan,
)

# CORS for React frontend（仅允许本地开发来源，端口可变）
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(config_router.router)
app.include_router(execution_preview_router.router)
app.include_router(tasks_router.router)
app.include_router(translate_router.router)
app.include_router(asr_router.router)
app.include_router(audit_router.router)
app.include_router(subtitles_router.router)
app.include_router(export_router.router)
app.include_router(highlight_reels_router.router)
app.include_router(learning_router.router)
app.include_router(productions_router.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
