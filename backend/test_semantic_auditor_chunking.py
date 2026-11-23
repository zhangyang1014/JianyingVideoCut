import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.models.task import Segment
from backend.services import semantic_auditor as auditor


def make_segments(count: int) -> list[Segment]:
    return [
        Segment(
            id=f"seg_{idx:03d}",
            start=idx * 3.0,
            end=idx * 3.0 + 1.0,
            text=f"第{idx}句内容。",
        )
        for idx in range(count)
    ]


class SemanticAuditorChunkingTests(unittest.IsolatedAsyncioTestCase):
    def test_plan_audit_chunks_covers_each_core_segment_once(self):
        segments = make_segments(310)

        chunks = auditor.plan_audit_chunks(segments, chunk_segments=120)
        covered: list[str] = []
        for chunk in chunks:
            self.assertLess(chunk.context_start, chunk.core_end)
            self.assertLessEqual(chunk.context_start, chunk.core_start)
            self.assertGreaterEqual(chunk.context_end, chunk.core_end)
            covered.extend(segment.id for segment in segments[chunk.core_start:chunk.core_end])

        self.assertGreater(len(chunks), 1)
        self.assertEqual(covered, [segment.id for segment in segments])
        self.assertEqual(len(covered), len(set(covered)))

    def test_plan_audit_chunks_respects_token_budget(self):
        segments = make_segments(180)
        for segment in segments:
            segment.text = "长文本" * 80

        chunks = auditor.plan_audit_chunks(segments, chunk_segments=150, token_budget=4000)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all((chunk.core_end - chunk.core_start) < 150 for chunk in chunks))

    def test_parse_strict_raises_on_invalid_json(self):
        with self.assertRaises(ValueError):
            auditor.parse_claude_response(
                "not json",
                [Segment(id="seg_1", start=0, end=1, text="测试")],
                strict=True,
            )

    async def test_parallel_runner_respects_limit_and_merges_in_original_order(self):
        segments = make_segments(125)
        active = 0
        max_active = 0

        async def fake_batch(client, all_segments, chunk, **kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01 * (len(all_segments) - chunk.core_start) / len(all_segments))
            active -= 1
            return auditor.CloudAuditBatchResult(
                chunk=chunk,
                segments=[segment.model_copy(deep=True) for segment in all_segments[chunk.core_start:chunk.core_end]],
                model_elapsed=0.01,
                parse_elapsed=0.0,
                retries=0,
            )

        with patch.object(auditor, "_run_openai_audit_batch", side_effect=fake_batch):
            result = await auditor._run_openai_parallel_audit(
                segments,
                task_type="monologue_clean",
                style_mode="immersive",
                api_key="sk-test",
                model="test-model",
                openai_base_url="https://example.test/v1",
                log_callback=None,
                chunk_segments=40,
                max_parallel_chunks=3,
            )

        self.assertLessEqual(max_active, 3)
        self.assertEqual([segment.id for segment in result], [segment.id for segment in segments])

    async def test_parallel_runner_uses_chunk_cache_when_enabled(self):
        segments = make_segments(185)
        calls = 0

        async def fake_batch(client, all_segments, chunk, **kwargs):
            nonlocal calls
            calls += 1
            return auditor.CloudAuditBatchResult(
                chunk=chunk,
                segments=[segment.model_copy(deep=True) for segment in all_segments[chunk.core_start:chunk.core_end]],
                model_elapsed=0.01,
                parse_elapsed=0.0,
                retries=0,
                chunk_hash=kwargs.get("chunk_hash"),
            )

        with tempfile.TemporaryDirectory() as cache_dir:
            with patch.object(auditor, "AUDIT_CACHE_DIR", Path(cache_dir)):
                with patch.object(auditor, "_run_openai_audit_batch", side_effect=fake_batch):
                    await auditor._run_openai_parallel_audit(
                        segments,
                        task_type="monologue_clean",
                        style_mode="immersive",
                        api_key="sk-test",
                        model="test-model",
                        openai_base_url="https://example.test/v1",
                        log_callback=None,
                        chunk_segments=40,
                        max_parallel_chunks=3,
                        enable_batch_resume=True,
                    )
                    first_calls = calls
                    await auditor._run_openai_parallel_audit(
                        segments,
                        task_type="monologue_clean",
                        style_mode="immersive",
                        api_key="sk-test",
                        model="test-model",
                        openai_base_url="https://example.test/v1",
                        log_callback=None,
                        chunk_segments=40,
                        max_parallel_chunks=3,
                        enable_batch_resume=True,
                    )

        self.assertGreater(first_calls, 0)
        self.assertEqual(calls, first_calls)

    async def test_parallel_runner_fails_atomically_when_one_batch_fails(self):
        segments = make_segments(185)

        async def fake_batch(client, all_segments, chunk, **kwargs):
            if chunk.index == 1:
                raise RuntimeError("boom")
            return auditor.CloudAuditBatchResult(
                chunk=chunk,
                segments=[segment.model_copy(deep=True) for segment in all_segments[chunk.core_start:chunk.core_end]],
                model_elapsed=0.01,
                parse_elapsed=0.0,
                retries=0,
            )

        with patch.object(auditor, "_run_openai_audit_batch", side_effect=fake_batch):
            with self.assertRaises(RuntimeError):
                await auditor._run_openai_parallel_audit(
                    segments,
                    task_type="monologue_clean",
                    style_mode="immersive",
                    api_key="sk-test",
                    model="test-model",
                    openai_base_url="https://example.test/v1",
                    log_callback=None,
                    chunk_segments=40,
                    max_parallel_chunks=3,
                )

    async def test_partial_failure_rerun_only_requests_failed_or_missing_chunks(self):
        segments = make_segments(185)
        calls: dict[int, int] = {}
        first_wave_ready = asyncio.Event()
        first_wave_count = 0
        fail_once = True

        async def fake_batch(client, all_segments, chunk, **kwargs):
            nonlocal first_wave_count, fail_once
            calls[chunk.index] = calls.get(chunk.index, 0) + 1
            if fail_once and chunk.index < 3:
                first_wave_count += 1
                if first_wave_count == 3:
                    first_wave_ready.set()
                await first_wave_ready.wait()
            if fail_once and chunk.index == 1:
                raise RuntimeError("boom")
            return auditor.CloudAuditBatchResult(
                chunk=chunk,
                segments=[segment.model_copy(deep=True) for segment in all_segments[chunk.core_start:chunk.core_end]],
                model_elapsed=0.01,
                parse_elapsed=0.0,
                retries=0,
                chunk_hash=kwargs.get("chunk_hash"),
            )

        with tempfile.TemporaryDirectory() as cache_dir:
            with patch.object(auditor, "AUDIT_CACHE_DIR", Path(cache_dir)):
                with patch.object(auditor, "_run_openai_audit_batch", side_effect=fake_batch):
                    with self.assertRaises(RuntimeError):
                        await auditor._run_openai_parallel_audit(
                            segments,
                            task_type="monologue_clean",
                            style_mode="immersive",
                            api_key="sk-test",
                            model="test-model",
                            openai_base_url="https://example.test/v1",
                            log_callback=None,
                            chunk_segments=40,
                            max_parallel_chunks=3,
                        )
                    first_counts = dict(calls)
                    fail_once = False
                    result = await auditor._run_openai_parallel_audit(
                        segments,
                        task_type="monologue_clean",
                        style_mode="immersive",
                        api_key="sk-test",
                        model="test-model",
                        openai_base_url="https://example.test/v1",
                        log_callback=None,
                        chunk_segments=40,
                        max_parallel_chunks=3,
                    )

        self.assertEqual([segment.id for segment in result], [segment.id for segment in segments])
        self.assertEqual(calls[0], first_counts[0])
        self.assertEqual(calls[2], first_counts[2])
        self.assertEqual(calls[1], first_counts[1] + 1)

    def test_cache_write_is_atomic_and_corruption_is_a_miss(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            cache_path = Path(cache_dir)
            key = auditor.content_cache_fingerprint("test", {"input": "A"})
            real_replace = auditor.os.replace
            with patch.object(auditor.os, "replace", wraps=real_replace) as replace:
                auditor.save_content_cache(cache_path, key, {"ok": True})

            replace.assert_called_once()
            self.assertEqual(auditor.load_content_cache(cache_path, key), {"ok": True})
            self.assertEqual(list(cache_path.glob("*.tmp")), [])

            (cache_path / f"{key}.json").write_text("{broken", encoding="utf-8")
            self.assertIsNone(auditor.load_content_cache(cache_path, key))

    def test_cache_fingerprint_changes_with_input_but_not_api_key(self):
        base = {
            "model": "model-a",
            "endpoint": "https://example.test/v1",
            "prompt": "prompt",
            "input": {"text": "A"},
        }
        first = auditor.content_cache_fingerprint("stage", base)
        changed = auditor.content_cache_fingerprint(
            "stage",
            {**base, "input": {"text": "B"}},
        )

        self.assertNotEqual(first, changed)
        self.assertNotIn("api_key", json.dumps(base))


if __name__ == "__main__":
    unittest.main()
