import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from backend.models.task import (
    ASRResult,
    AuditSnapshot,
    ExportJob,
    HighlightReel,
    HighlightReelQualityReport,
    Segment,
    SegmentAction,
    Task,
    TaskStatus,
    WordTimestamp,
)
from backend.routers import audit as audit_router
from backend.routers import subtitles as subtitles_router
from backend.services.asr_pipeline import GlossaryEntry
from backend.services.one_click_translate import build_translation_cues
from backend.services.subtitle_optimizer import optimize_subtitle_text
from backend.services.subtitle_text import get_effective_subtitle_text


def _task() -> Task:
    return Task(
        id="task-subtitle-opt",
        name="subtitle",
        status=TaskStatus.REVIEW,
        subtitle_revision=3,
        audit_segments=[
            Segment(
                id="seg_1",
                start=0,
                end=2,
                text="我正在用克劳德",
                action=SegmentAction.KEEP,
                reason="AI 保留",
                claude_action=SegmentAction.KEEP,
                words=[
                    WordTimestamp(word="我", start=0.0, end=0.1),
                    WordTimestamp(word="正", start=0.1, end=0.2),
                ],
            ),
            Segment(
                id="seg_2",
                start=2,
                end=4,
                text="不用改",
                action=SegmentAction.DELETE,
                reason="AI 删除",
                claude_action=SegmentAction.DELETE,
            ),
        ],
        audit_history=[
            AuditSnapshot(
                version=1,
                created_at="2026-01-01T00:00:00",
                model="mock",
                segments_count=2,
                segments_kept=1,
                segments_deleted=1,
            )
        ],
        audit_current_version=1,
        highlight_reels=[
            HighlightReel(
                reel_id="reel_1",
                clip_group=1,
                title="Claude 片段",
                segment_ids=["seg_1"],
            )
        ],
        highlight_quality_report=HighlightReelQualityReport(passed=True),
        golden_quote_order=["seg_1"],
        export_jobs=[ExportJob(task_id="task-subtitle-opt", status="succeeded")],
    )


def _fake_store(task: Task):
    updates = []
    return SimpleNamespace(
        updates=updates,
        get=lambda task_id: task if task_id == task.id else None,
        update=lambda updated: updates.append(updated) or updated,
    )


class SubtitleOptimizerTests(unittest.IsolatedAsyncioTestCase):
    def test_optimizer_repairs_collapsed_duplicate_ascii_glossary_term(self):
        text, reasons = optimize_subtitle_text(
            "这个ap很好用，cap不要误改",
            [GlossaryEntry(source="APP", target="APP")],
        )

        self.assertEqual(text, "这个APP很好用，cap不要误改")
        self.assertIn("词典近似修复 1 处", reasons)

    async def test_preview_returns_changes_without_writing_task(self):
        task = _task()
        store = _fake_store(task)

        with patch("backend.routers.subtitles.get_store", return_value=store), patch(
            "backend.routers.subtitles.load_glossary",
            return_value=[GlossaryEntry(source="克劳德", target="Claude")],
        ):
            result = await subtitles_router.preview_subtitle_optimization(
                task.id,
                subtitles_router.SubtitleOptimizePreviewRequest(),
            )

        self.assertEqual(result["changed_count"], 1)
        self.assertEqual(result["changes"][0]["segment_id"], "seg_1")
        self.assertEqual(result["changes"][0]["after"], "我正在用Claude")
        self.assertEqual(task.subtitle_revision, 3)
        self.assertEqual(store.updates, [])

    async def test_apply_only_updates_subtitle_layer_and_preserves_downstream(self):
        task = _task()
        store = _fake_store(task)
        original_words = list(task.audit_segments[0].words)
        original_reels = task.highlight_reels
        original_jobs = task.export_jobs

        with patch("backend.routers.subtitles.get_store", return_value=store), patch(
            "backend.routers.subtitles.load_glossary",
            return_value=[GlossaryEntry(source="克劳德", target="Claude")],
        ), patch("backend.routers.subtitles.manager.broadcast", new=AsyncMock()):
            preview = await subtitles_router.preview_subtitle_optimization(
                task.id,
                subtitles_router.SubtitleOptimizePreviewRequest(),
            )
            result = await subtitles_router.apply_subtitle_optimization(
                task.id,
                subtitles_router.SubtitleOptimizeApplyRequest(
                    selected_segment_ids=["seg_1"],
                    expected_subtitle_revision=preview["subtitle_revision"],
                    glossary_fingerprint=preview["glossary_fingerprint"],
                ),
            )

        seg = task.audit_segments[0]
        self.assertTrue(result["success"])
        self.assertEqual(result["changed_count"], 1)
        self.assertEqual(task.subtitle_revision, 4)
        self.assertEqual(seg.subtitle_text, "我正在用Claude")
        self.assertEqual(seg.subtitle_text_source, "optimizer")
        self.assertEqual(seg.text, "我正在用克劳德")
        self.assertEqual(seg.words, original_words)
        self.assertEqual(seg.action, SegmentAction.KEEP)
        self.assertEqual(seg.reason, "AI 保留")
        self.assertEqual(task.audit_current_version, 1)
        self.assertEqual(task.audit_history[0].model, "mock")
        self.assertIs(task.highlight_reels, original_reels)
        self.assertIs(task.export_jobs, original_jobs)
        self.assertEqual(task.golden_quote_order, ["seg_1"])

    async def test_apply_rejects_stale_subtitle_revision(self):
        task = _task()
        store = _fake_store(task)

        with patch("backend.routers.subtitles.get_store", return_value=store), patch(
            "backend.routers.subtitles.load_glossary",
            return_value=[GlossaryEntry(source="克劳德", target="Claude")],
        ):
            with self.assertRaises(HTTPException) as ctx:
                await subtitles_router.apply_subtitle_optimization(
                    task.id,
                    subtitles_router.SubtitleOptimizeApplyRequest(
                        selected_segment_ids=["seg_1"],
                        expected_subtitle_revision=2,
                        glossary_fingerprint="wrong",
                    ),
                )

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIsNone(task.audit_segments[0].subtitle_text)
        self.assertEqual(store.updates, [])

    async def test_manual_text_patch_keeps_words_and_does_not_touch_content_revision(self):
        task = _task()
        store = _fake_store(task)
        original_words = list(task.audit_segments[0].words)
        original_content_revision = task.content_revision

        with patch("backend.routers.audit.get_store", return_value=store):
            await audit_router.patch_segment(
                task.id,
                "seg_1",
                audit_router.PatchSegmentRequest(text="我手动改了Claude"),
            )

        seg = task.audit_segments[0]
        self.assertEqual(get_effective_subtitle_text(seg, include_speaker_label=False), "我手动改了Claude")
        self.assertEqual(seg.text, "我正在用克劳德")
        self.assertEqual(seg.words, original_words)
        self.assertEqual(task.subtitle_revision, 4)
        self.assertEqual(task.content_revision, original_content_revision)

    def test_translation_cues_use_effective_subtitle_text(self):
        task = _task()
        task.audit_segments[0].subtitle_text = "我正在用Claude"
        task.asr_result = None

        cues = build_translation_cues(
            ASRResult(
                words=[],
                segments=task.audit_segments,
                duration=4,
            )
        )

        self.assertEqual(cues[0].source_text, "我正在用Claude")
