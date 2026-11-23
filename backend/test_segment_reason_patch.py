from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.models.task import ManualSegmentAction, Segment, SegmentAction, Task
from backend.routers import audit as audit_router


def _fake_store(task: Task) -> SimpleNamespace:
    return SimpleNamespace(
        get=lambda task_id: task if task_id == task.id else None,
        update=lambda t: t,
    )


@pytest.mark.asyncio
async def test_patch_segment_persists_manual_reason():
    task = Task(
        id="task-reason",
        name="reason test",
        audit_segments=[
            Segment(
                id="seg-1",
                start=0,
                end=1,
                text="所以这个书第一个可以培养孩子去读文字的能力。",
                action=SegmentAction.KEEP,
                reason="手动恢复",
            )
        ],
    )

    with patch("backend.routers.audit.get_store", return_value=_fake_store(task)):
        result = await audit_router.patch_segment(
            task.id,
            "seg-1",
            audit_router.PatchSegmentRequest(reason="重要知识点，值得保留"),
        )

    assert result["success"] is True
    assert task.audit_segments[0].reason == "重要知识点，值得保留"
    assert task.audit_segments[0].user_override is True
    assert task.audit_segments[0].manual_action is None


@pytest.mark.asyncio
async def test_patch_segment_records_manual_adjustment():
    task = Task(
        id="task-adjust",
        name="adjust test",
        audit_segments=[
            Segment(
                id="seg-1",
                start=0,
                end=1,
                text="原始字幕",
                action=SegmentAction.KEEP,
            )
        ],
    )

    with patch("backend.routers.audit.get_store", return_value=_fake_store(task)):
        result = await audit_router.patch_segment(
            task.id,
            "seg-1",
            audit_router.PatchSegmentRequest(text="修正后的字幕"),
        )

    assert result["success"] is True
    assert task.audit_segments[0].manual_action == ManualSegmentAction.ADJUST
