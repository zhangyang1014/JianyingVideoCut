"""Read-only execution previews used by model rerun confirmation dialogs."""

from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services.execution_preview import (
    ExecutionPreviewError,
    build_execution_preview,
)
from ..services.task_store import get_store


router = APIRouter()


class ExecutionPreviewRequest(BaseModel):
    operation: Literal[
        "asr",
        "diarize",
        "audit",
        "highlight_rebuild",
        "highlight_auto_run",
        "golden_quote",
        "translate_retry",
    ]
    mode: Literal["rerun", "resume", "reset"] = "rerun"
    video_id: Optional[str] = None
    options: dict[str, Any] = Field(default_factory=dict)


@router.post("/api/tasks/{task_id}/execution-preview")
async def preview_execution(task_id: str, req: ExecutionPreviewRequest):
    task = get_store().get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    try:
        return build_execution_preview(
            task,
            operation=req.operation,
            mode=req.mode,
            options=req.options,
            video_id=req.video_id,
        )
    except ExecutionPreviewError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, f"无法生成执行预览: {exc}") from exc
