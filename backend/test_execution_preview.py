import unittest

from backend.models.task import Segment, SegmentAction, Task, TaskStatus, TaskType
from backend.services.execution_preview import (
    ExecutionPreviewError,
    build_execution_preview,
    validate_execution_preview_token,
)


def _review_task(task_type: TaskType = TaskType.MONOLOGUE_CLEAN) -> Task:
    return Task(
        id="execution-preview-task",
        name="执行预览",
        task_type=task_type,
        status=TaskStatus.REVIEW,
        audit_segments=[
            Segment(
                id="seg-1",
                start=0,
                end=3,
                text="这个产品的核心卖点是操作简单",
                action=SegmentAction.KEEP,
            )
        ],
    )


class ExecutionPreviewTests(unittest.TestCase):
    def test_audit_preview_exposes_routes_and_prompts_without_credentials(self):
        task = _review_task()
        options = {
            "style_mode": "immersive",
            "provider": "openai",
            "openai_model": "z-ai/glm-5.2",
            "force_rule_engine": False,
            "ollama_model": "qwen2.5:32b",
            "ollama_base_url": "http://localhost:11434",
            "reset_prior_audit": False,
            "openai_api_key": "must-not-leak",
        }

        preview = build_execution_preview(
            task,
            operation="audit",
            mode="rerun",
            options=options,
        )

        self.assertEqual(preview["api_path"], f"/api/tasks/{task.id}/audit")
        self.assertEqual(preview["stages"][0]["id"], "audit")
        self.assertTrue(preview["stages"][0]["prompts"][0]["system_prompt"])
        self.assertIn("待审计片段", preview["stages"][0]["prompts"][0]["user_prompt"])
        for stage in preview["stages"]:
            for route in stage["routes"]:
                self.assertNotIn("api_key", route)
        self.assertNotIn("must-not-leak", str(preview))

    def test_preview_token_rejects_changed_options_and_task_revision(self):
        task = _review_task()
        options = {"backend": "funasr", "model": "paraformer-zh"}
        preview = build_execution_preview(
            task,
            operation="asr",
            mode="rerun",
            options=options,
        )

        validate_execution_preview_token(
            preview["preview_token"],
            task=task,
            operation="asr",
            mode="rerun",
            options=options,
        )
        with self.assertRaises(ExecutionPreviewError):
            validate_execution_preview_token(
                preview["preview_token"],
                task=task,
                operation="asr",
                mode="rerun",
                options={"backend": "whisper", "model": "base"},
            )

        task.revision += 1
        with self.assertRaises(ExecutionPreviewError):
            validate_execution_preview_token(
                preview["preview_token"],
                task=task,
                operation="asr",
                mode="rerun",
                options=options,
            )

    def test_local_model_preview_has_no_prompt_or_http_port(self):
        task = _review_task(TaskType.INTERVIEW_COMPRESS)
        preview = build_execution_preview(
            task,
            operation="diarize",
            mode="rerun",
            options={"enabled": True},
        )

        stage = preview["stages"][0]
        self.assertEqual(stage["routes"][0]["port"], None)
        self.assertEqual(stage["prompts"], [])
        self.assertEqual(stage["parameters"]["oracle_num"], 2)

    def test_highlight_auto_run_reports_skipped_audit_when_results_exist(self):
        task = _review_task(TaskType.HIGHLIGHT_REEL)
        preview = build_execution_preview(
            task,
            operation="highlight_auto_run",
            mode="rerun",
            options={
                "target_product_count": 1,
                "min_reel_duration": 20,
                "max_reel_duration": 90,
                "auto_export": False,
            },
        )

        stage_ids = [stage["id"] for stage in preview["stages"]]
        self.assertNotIn("asr", stage_ids)
        self.assertNotIn("audit", stage_ids)
        self.assertIn("highlight_strategy", stage_ids)
        self.assertTrue(any("跳过 ASR" in warning for warning in preview["warnings"]))


if __name__ == "__main__":
    unittest.main()
