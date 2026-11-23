import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import backend.routers.translate as main
from backend.core.task_runner import execute_with_retry as real_execute_with_retry
from backend.models.task import (
    ASRResult,
    LLMModelConfig,
    OneClickTranslateConfig,
    OneClickTranslateStartRequest,
    Segment,
    Task,
    TaskStatus,
    TaskType,
    TranslationCue,
    TranslationVideo,
    TTSModelConfig,
    UpdateTranslationCuesRequest,
)
from backend.services.one_click_translate import (
    build_translation_cues,
    calibrate_and_translate_cues,
    determine_resume_stage,
    generate_tts_audio_for_cues,
    validate_cue_timeline,
)


class RecordingStore:
    def __init__(self, task):
        self.task = task
        self.statuses = []

    def get(self, task_id):
        return self.task if task_id == self.task.id else None

    def update(self, task):
        self.task = task
        self.statuses.append(task.status)
        return task


class OneClickTranslateServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_cues_preserves_original_timeline_and_normalizes_text(self):
        asr_result = ASRResult(
            duration=4.0,
            segments=[
                Segment(id="seg_1", start=0.0, end=1.5, text="<FIL> 你好  "),
                Segment(id="seg_2", start=2.0, end=4.0, text="世界"),
            ],
        )

        cues = build_translation_cues(asr_result)

        self.assertEqual([cue.source_segment_id for cue in cues], ["seg_1", "seg_2"])
        self.assertEqual([(cue.start, cue.end) for cue in cues], [(0.0, 1.5), (2.0, 4.0)])
        self.assertEqual(cues[0].source_text, "你好")

    async def test_mock_calibration_translation_keeps_ids_and_sets_target_text(self):
        cues = [
            TranslationCue(id="cue_1", source_segment_id="seg_1", start=0.0, end=1.0, source_text="你好"),
        ]
        config = OneClickTranslateConfig(
            source_language="zh",
            target_language="en",
            subtitle_model=LLMModelConfig(provider="mock", model="local"),
            tts_model=TTSModelConfig(provider="silent", model="aligned-silent", voice="alloy"),
        )

        result = await calibrate_and_translate_cues(cues, config)

        self.assertEqual(result[0].id, "cue_1")
        self.assertEqual(result[0].calibrated_text, "你好")
        self.assertEqual(result[0].translated_text, "[EN] 你好")
        self.assertEqual(result[0].status, "translated")

    async def test_validate_cue_timeline_rejects_reordered_cues(self):
        cues = [
            TranslationCue(id="late", source_segment_id="seg_2", start=2.0, end=3.0, source_text="晚"),
            TranslationCue(id="early", source_segment_id="seg_1", start=1.0, end=1.5, source_text="早"),
        ]

        with self.assertRaises(ValueError):
            validate_cue_timeline(cues)


def _make_asr_result() -> ASRResult:
    return ASRResult(
        duration=2.0,
        segments=[Segment(id="seg_1", start=0.0, end=2.0, text="你好世界")],
    )


def _make_video(video_id="vid_1", **overrides) -> TranslationVideo:
    defaults = dict(
        id=video_id,
        filename=f"{video_id}.mp4",
        video_path=f"/tmp/{video_id}.mp4",
        duration=2.0,
    )
    defaults.update(overrides)
    return TranslationVideo(**defaults)


class DetermineResumeStageTests(unittest.TestCase):
    def test_no_asr_artifacts_starts_from_asr(self):
        video = _make_video()
        self.assertEqual(determine_resume_stage(video), "asr")

    def test_asr_done_without_translation_resumes_from_translate(self):
        asr_result = _make_asr_result()
        video = _make_video(asr_result=asr_result, cues=build_translation_cues(asr_result))
        self.assertEqual(determine_resume_stage(video), "translate")

    def test_translated_without_audio_resumes_from_tts(self):
        asr_result = _make_asr_result()
        cues = build_translation_cues(asr_result)
        for cue in cues:
            cue.translated_text = "Hello world"
        video = _make_video(asr_result=asr_result, cues=cues)
        self.assertEqual(determine_resume_stage(video), "tts")

    def test_audio_path_missing_on_disk_resumes_from_tts(self):
        asr_result = _make_asr_result()
        cues = build_translation_cues(asr_result)
        for cue in cues:
            cue.translated_text = "Hello world"
            cue.audio_path = "/tmp/does-not-exist-xyz.mp3"
        video = _make_video(asr_result=asr_result, cues=cues)
        self.assertEqual(determine_resume_stage(video), "tts")

    def test_corrupt_tts_cache_sidecar_resumes_from_tts(self):
        asr_result = _make_asr_result()
        cues = build_translation_cues(asr_result)
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "cue.mp3"
            audio_path.write_bytes(b"broken-audio")
            Path(str(audio_path) + ".json").write_text("{broken", encoding="utf-8")
            for cue in cues:
                cue.translated_text = "Hello world"
                cue.audio_path = str(audio_path)
            video = _make_video(asr_result=asr_result, cues=cues)
            self.assertEqual(determine_resume_stage(video), "tts")

    def test_all_artifacts_ready_resumes_from_dub(self):
        asr_result = _make_asr_result()
        cues = build_translation_cues(asr_result)
        with tempfile.TemporaryDirectory() as temp_dir:
            for cue in cues:
                cue.translated_text = "Hello world"
                audio_path = Path(temp_dir) / f"{cue.id}.mp3"
                audio_path.write_bytes(b"audio")
                cue.audio_path = str(audio_path)
            video = _make_video(asr_result=asr_result, cues=cues)
            self.assertEqual(determine_resume_stage(video), "dub")


class OneClickTranslateApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_translation_cues_clears_stale_audio_when_text_changes(self):
        old_cue = TranslationCue(
            id="cue_1",
            source_segment_id="seg_1",
            start=0.0,
            end=1.0,
            source_text="你好",
            calibrated_text="你好",
            translated_text="Hello",
            audio_path="/tmp/old.mp3",
            audio_duration=1.0,
            audio_text="Hello",
            status="audio_done",
        )
        video = TranslationVideo(
            id="vid_1",
            filename="demo.mp4",
            video_path="/tmp/demo.mp4",
            cues=[old_cue],
        )
        task = Task(
            id="task_translate",
            name="translate",
            task_type=TaskType.ONE_CLICK_TRANSLATE,
            status=TaskStatus.REVIEW,
            translation_videos=[video],
        )
        store = RecordingStore(task)
        new_cue = old_cue.model_copy(deep=True, update={"translated_text": "Hi"})

        with tempfile.TemporaryDirectory() as temp_dir, patch("backend.routers.translate.get_store", return_value=store), patch.object(
            main, "EXPORTS_DIR", Path(temp_dir)
        ):
            result = await main.update_translation_cues(
                task.id,
                video.id,
                UpdateTranslationCuesRequest(cues=[new_cue]),
            )

        updated_cue = result["video"]["cues"][0]
        self.assertEqual(updated_cue["translated_text"], "Hi")
        self.assertIsNone(updated_cue["audio_path"])
        self.assertIsNone(updated_cue["audio_text"])
        self.assertEqual(store.task.status, TaskStatus.REVIEW)

    async def test_update_translation_cues_rejects_changed_cue_set(self):
        video = TranslationVideo(
            id="vid_1",
            filename="demo.mp4",
            video_path="/tmp/demo.mp4",
            cues=[
                TranslationCue(id="cue_1", source_segment_id="seg_1", start=0.0, end=1.0, source_text="你好"),
            ],
        )
        task = Task(
            id="task_translate",
            name="translate",
            task_type=TaskType.ONE_CLICK_TRANSLATE,
            status=TaskStatus.REVIEW,
            translation_videos=[video],
        )
        store = RecordingStore(task)
        changed = TranslationCue(id="cue_2", source_segment_id="seg_1", start=0.0, end=1.0, source_text="你好")

        with patch("backend.routers.translate.get_store", return_value=store):
            with self.assertRaises(Exception):
                await main.update_translation_cues(
                    task.id,
                    video.id,
                    UpdateTranslationCuesRequest(cues=[changed]),
                )

    async def test_pipeline_runs_stages_in_order_and_finishes_at_review(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_dir = Path(temp_dir) / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            video = TranslationVideo(
                id="vid_1",
                filename="demo.mp4",
                video_path=str(Path(temp_dir) / "demo.mp4"),
                duration=2.0,
            )
            task = Task(
                id="task_translate",
                name="translate",
                task_type=TaskType.ONE_CLICK_TRANSLATE,
                status=TaskStatus.PENDING,
                translation_videos=[video],
            )
            store = RecordingStore(task)
            asr_result = ASRResult(
                duration=2.0,
                segments=[Segment(id="seg_1", start=0.0, end=2.0, text="你好世界")],
            )

            async def fake_tts(cues, output_dir, tts_config, api_key=None, log_callback=None):
                Path(output_dir).mkdir(parents=True, exist_ok=True)
                for cue in cues:
                    audio_path = Path(output_dir) / f"{cue.id}.mp3"
                    audio_path.write_bytes(b"audio")
                    cue.audio_path = str(audio_path)
                    cue.audio_text = cue.translated_text
                    cue.audio_duration = cue.end - cue.start
                    cue.status = "audio_done"
                return cues

            with patch("backend.routers.translate.get_store", return_value=store), patch.object(
                main, "EXPORTS_DIR", Path(temp_dir)
            ), patch(
                "backend.routers.translate.run_asr_pipeline", new=AsyncMock(return_value=asr_result)
            ), patch(
                "backend.routers.translate.generate_tts_audio_for_cues", new=AsyncMock(side_effect=fake_tts)
            ), patch(
                "backend.routers.translate.compose_aligned_audio_track", new=AsyncMock(return_value=str(Path(temp_dir) / "aligned.m4a"))
            ) as compose_mock, patch(
                "backend.routers.translate.mux_video_with_audio", new=AsyncMock(return_value=str(Path(temp_dir) / "preview.mp4"))
            ) as mux_mock, patch.object(
                main.manager, "broadcast", new=AsyncMock()
            ), patch(
                "backend.routers.translate.log_to_ws", new=AsyncMock()
            ):
                req = OneClickTranslateStartRequest(
                    source_language="zh",
                    target_language="en",
                    subtitle_model=LLMModelConfig(provider="mock", model="local"),
                    tts_model=TTSModelConfig(provider="silent", model="aligned-silent", voice="alloy"),
                )
                await main._run_one_click_translate_task(task.id, req)

            self.assertIn(TaskStatus.ASR_RUNNING, store.statuses)
            self.assertIn(TaskStatus.TRANSLATE_RUNNING, store.statuses)
            self.assertIn(TaskStatus.TTS_RUNNING, store.statuses)
            self.assertIn(TaskStatus.DUBBING_RUNNING, store.statuses)
            self.assertEqual(store.task.status, TaskStatus.REVIEW)
            self.assertEqual(store.task.translation_videos[0].status, "ready_preview")
            self.assertTrue(store.task.translation_videos[0].translated_srt_path)
            compose_mock.assert_awaited_once()
            mux_mock.assert_awaited_once()


async def _fake_tts(cues, output_dir, tts_config, api_key=None, log_callback=None):
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    for cue in cues:
        audio_path = Path(output_dir) / f"{cue.id}.mp3"
        audio_path.write_bytes(b"audio")
        cue.audio_path = str(audio_path)
        cue.audio_text = cue.translated_text
        cue.audio_duration = cue.end - cue.start
        cue.status = "audio_done"
    return cues


def _mock_request(**overrides) -> OneClickTranslateStartRequest:
    return OneClickTranslateStartRequest(
        source_language="zh",
        target_language="en",
        subtitle_model=LLMModelConfig(provider="mock", model="local"),
        tts_model=TTSModelConfig(provider="silent", model="aligned-silent", voice="alloy"),
        **overrides,
    )


class OneClickTranslateFailureIsolationTests(unittest.IsolatedAsyncioTestCase):
    def _make_task(self, videos):
        return Task(
            id="task_translate",
            name="translate",
            task_type=TaskType.ONE_CLICK_TRANSLATE,
            status=TaskStatus.PENDING,
            translation_videos=videos,
        )

    async def test_single_video_failure_marks_error_and_continues(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            videos = [_make_video("vid_1"), _make_video("vid_2")]
            task = self._make_task(videos)
            store = RecordingStore(task)

            asr_calls = []

            async def fake_asr(video_path, params, log, **kwargs):
                asr_calls.append(video_path)
                if len(asr_calls) == 1:
                    raise RuntimeError("ASR 模型崩溃")
                return _make_asr_result()

            log_mock = AsyncMock()
            with patch("backend.routers.translate.get_store", return_value=store), patch.object(
                main, "EXPORTS_DIR", Path(temp_dir)
            ), patch(
                "backend.routers.translate.run_asr_pipeline", new=AsyncMock(side_effect=fake_asr)
            ), patch(
                "backend.routers.translate.generate_tts_audio_for_cues", new=AsyncMock(side_effect=_fake_tts)
            ), patch(
                "backend.routers.translate.compose_aligned_audio_track", new=AsyncMock(return_value="aligned.m4a")
            ), patch(
                "backend.routers.translate.mux_video_with_audio", new=AsyncMock(return_value="preview.mp4")
            ), patch.object(
                main.manager, "broadcast", new=AsyncMock()
            ), patch(
                "backend.routers.translate.log_to_ws", new=log_mock
            ):
                await main._run_one_click_translate_task(task.id, _mock_request())

            self.assertEqual(len(asr_calls), 2, "第一个视频失败后应继续处理第二个视频")
            self.assertEqual(store.task.translation_videos[0].status, "error")
            self.assertIn("ASR 模型崩溃", store.task.translation_videos[0].error_message)
            self.assertEqual(store.task.translation_videos[1].status, "ready_preview")
            self.assertEqual(store.task.status, TaskStatus.REVIEW)
            self.assertIn("1 个视频处理失败", store.task.error_message)
            error_logs = [c for c in log_mock.await_args_list if c.args[1] == "error"]
            self.assertTrue(error_logs, "视频失败时应有 error 级别的 WebSocket 日志")

    async def test_all_videos_failed_sets_task_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            videos = [_make_video("vid_1"), _make_video("vid_2")]
            task = self._make_task(videos)
            store = RecordingStore(task)

            with patch("backend.routers.translate.get_store", return_value=store), patch.object(
                main, "EXPORTS_DIR", Path(temp_dir)
            ), patch(
                "backend.routers.translate.run_asr_pipeline", new=AsyncMock(side_effect=RuntimeError("boom"))
            ), patch.object(
                main.manager, "broadcast", new=AsyncMock()
            ), patch(
                "backend.routers.translate.log_to_ws", new=AsyncMock()
            ):
                await main._run_one_click_translate_task(task.id, _mock_request())

            self.assertEqual(store.task.status, TaskStatus.ERROR)
            self.assertIn("全部 2 个视频处理失败", store.task.error_message)
            for video in store.task.translation_videos:
                self.assertEqual(video.status, "error")
                self.assertIn("boom", video.error_message)


class RetryTranslationVideoTests(unittest.IsolatedAsyncioTestCase):
    def _make_task(self, videos):
        return Task(
            id="task_translate",
            name="translate",
            task_type=TaskType.ONE_CLICK_TRANSLATE,
            status=TaskStatus.ERROR,
            translation_videos=videos,
        )

    async def test_retry_resumes_from_tts_and_skips_asr_translate(self):
        """已有 ASR + 翻译产物、缺音频的视频，重试应直接从 TTS 续跑。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            asr_result = _make_asr_result()
            cues = build_translation_cues(asr_result)
            for cue in cues:
                cue.translated_text = "Hello world"
            video = _make_video("vid_1", asr_result=asr_result, cues=cues, status="error", error_message="TTS 失败")
            task = self._make_task([video])
            store = RecordingStore(task)

            captured = {}

            def fake_start_runner(task_id, coro, _expected_status=None, **_kwargs):
                captured["coro"] = coro

            asr_mock = AsyncMock()
            translate_mock = AsyncMock()
            with patch("backend.routers.translate.get_store", return_value=store), patch.object(
                main, "EXPORTS_DIR", Path(temp_dir)
            ), patch(
                "backend.routers.translate._start_task_runner", side_effect=fake_start_runner
            ), patch(
                "backend.routers.translate.run_asr_pipeline", new=asr_mock
            ), patch(
                "backend.routers.translate.calibrate_and_translate_cues", new=translate_mock
            ), patch(
                "backend.routers.translate.generate_tts_audio_for_cues", new=AsyncMock(side_effect=_fake_tts)
            ), patch(
                "backend.routers.translate.compose_aligned_audio_track", new=AsyncMock(return_value="aligned.m4a")
            ), patch(
                "backend.routers.translate.mux_video_with_audio", new=AsyncMock(return_value="preview.mp4")
            ), patch.object(
                main.manager, "broadcast", new=AsyncMock()
            ), patch(
                "backend.routers.translate.log_to_ws", new=AsyncMock()
            ):
                result = await main.retry_translation_video(task.id, video.id, _mock_request())
                self.assertEqual(result["resume_stage"], "tts")
                await captured["coro"]

            asr_mock.assert_not_awaited()
            translate_mock.assert_not_awaited()
            self.assertEqual(store.task.translation_videos[0].status, "ready_preview")
            self.assertIsNone(store.task.translation_videos[0].error_message)
            self.assertEqual(store.task.status, TaskStatus.REVIEW)

    async def test_retry_force_restart_runs_from_asr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asr_result = _make_asr_result()
            cues = build_translation_cues(asr_result)
            for cue in cues:
                cue.translated_text = "Hello world"
            video = _make_video("vid_1", asr_result=asr_result, cues=cues, status="error")
            task = self._make_task([video])
            store = RecordingStore(task)

            captured = {}

            def fake_start_runner(task_id, coro, _expected_status=None, **_kwargs):
                captured["coro"] = coro

            asr_mock = AsyncMock(return_value=_make_asr_result())
            with patch("backend.routers.translate.get_store", return_value=store), patch.object(
                main, "EXPORTS_DIR", Path(temp_dir)
            ), patch(
                "backend.routers.translate._start_task_runner", side_effect=fake_start_runner
            ), patch(
                "backend.routers.translate.run_asr_pipeline", new=asr_mock
            ), patch(
                "backend.routers.translate.generate_tts_audio_for_cues", new=AsyncMock(side_effect=_fake_tts)
            ), patch(
                "backend.routers.translate.compose_aligned_audio_track", new=AsyncMock(return_value="aligned.m4a")
            ), patch(
                "backend.routers.translate.mux_video_with_audio", new=AsyncMock(return_value="preview.mp4")
            ), patch.object(
                main.manager, "broadcast", new=AsyncMock()
            ), patch(
                "backend.routers.translate.log_to_ws", new=AsyncMock()
            ):
                result = await main.retry_translation_video(
                    task.id, video.id, _mock_request(force_restart=True)
                )
                self.assertEqual(result["resume_stage"], "asr")
                await captured["coro"]

            asr_mock.assert_awaited_once()
            self.assertEqual(store.task.translation_videos[0].status, "ready_preview")
            self.assertEqual(store.task.status, TaskStatus.REVIEW)

    async def test_retry_failure_marks_video_error_again(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asr_result = _make_asr_result()
            cues = build_translation_cues(asr_result)
            for cue in cues:
                cue.translated_text = "Hello world"
            video = _make_video("vid_1", asr_result=asr_result, cues=cues, status="error")
            task = self._make_task([video])
            store = RecordingStore(task)

            captured = {}

            def fake_start_runner(task_id, coro, _expected_status=None, **_kwargs):
                captured["coro"] = coro

            with patch("backend.routers.translate.get_store", return_value=store), patch.object(
                main, "EXPORTS_DIR", Path(temp_dir)
            ), patch(
                "backend.routers.translate._start_task_runner", side_effect=fake_start_runner
            ), patch(
                "backend.routers.translate.generate_tts_audio_for_cues", new=AsyncMock(side_effect=RuntimeError("TTS 再次失败"))
            ), patch.object(
                main.manager, "broadcast", new=AsyncMock()
            ), patch(
                "backend.routers.translate.log_to_ws", new=AsyncMock()
            ):
                await main.retry_translation_video(task.id, video.id, _mock_request())
                await captured["coro"]

            self.assertEqual(store.task.translation_videos[0].status, "error")
            self.assertIn("TTS 再次失败", store.task.translation_videos[0].error_message)
            self.assertEqual(store.task.status, TaskStatus.ERROR)

    async def test_retry_unknown_video_returns_404(self):
        task = self._make_task([_make_video("vid_1")])
        store = RecordingStore(task)
        with patch("backend.routers.translate.get_store", return_value=store):
            with self.assertRaises(Exception):
                await main.retry_translation_video(task.id, "missing", _mock_request())


async def _fast_retry(operation):
    return await real_execute_with_retry(
        operation,
        base_delay=0,
        max_delay=0,
        jitter_ratio=0,
    )


def _translation_test_cues(count=5):
    return [
        TranslationCue(
            id=f"cue_{index}",
            source_segment_id=f"seg_{index}",
            start=float(index),
            end=float(index + 1),
            source_text=f"原文{index}",
        )
        for index in range(count)
    ]


class TranslationCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_batch_only_is_retried_after_resume(self):
        cues = _translation_test_cues()
        config = OneClickTranslateConfig(
            source_language="zh",
            target_language="en",
            subtitle_model=LLMModelConfig(provider="openai", model="test-model"),
            tts_model=TTSModelConfig(provider="silent", model="silent", voice="alloy"),
        )
        calls = {0: 0, 2: 0, 4: 0}
        fail_second_batch = True

        async def fake_llm(batch, _config, _api_key):
            first = int(batch[0].id.split("_")[-1])
            calls[first] += 1
            if first == 2 and fail_second_batch:
                raise TimeoutError("batch two timeout")
            return [
                {
                    "id": cue.id,
                    "calibrated_text": cue.source_text,
                    "translated_text": f"EN {cue.source_text}",
                }
                for cue in batch
            ]

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "backend.services.one_click_translate._call_subtitle_llm",
            new=AsyncMock(side_effect=fake_llm),
        ), patch(
            "backend.services.one_click_translate.execute_with_retry",
            new=_fast_retry,
        ):
            with self.assertRaisesRegex(RuntimeError, "翻译批次重试耗尽"):
                await calibrate_and_translate_cues(
                    cues,
                    config,
                    api_key="test-key",
                    checkpoint_dir=temp_dir,
                    batch_size=2,
                )
            self.assertEqual(calls, {0: 1, 2: 4, 4: 1})

            fail_second_batch = False
            result = await calibrate_and_translate_cues(
                cues,
                config,
                api_key="test-key",
                checkpoint_dir=temp_dir,
                batch_size=2,
            )

        self.assertEqual(calls, {0: 1, 2: 5, 4: 1})
        self.assertTrue(all(cue.status == "translated" for cue in result))

    async def test_tts_resume_only_retries_failed_cue_and_voice_change_invalidates_cache(self):
        cues = _translation_test_cues(3)
        for cue in cues:
            cue.calibrated_text = cue.source_text
            cue.translated_text = f"EN {cue.source_text}"
            cue.status = "translated"
        calls = {cue.translated_text: 0 for cue in cues}
        fail_text = cues[1].translated_text
        should_fail = True

        async def fake_tts(text, output_path, _config, _api_key):
            calls[text] += 1
            if text == fail_text and should_fail:
                raise TimeoutError("cue timeout")
            Path(output_path).write_bytes(b"valid-audio")

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "backend.services.one_click_translate._generate_openai_tts",
            new=AsyncMock(side_effect=fake_tts),
        ), patch(
            "backend.services.one_click_translate._is_valid_audio_file",
            return_value=True,
        ), patch(
            "backend.services.one_click_translate.execute_with_retry",
            new=_fast_retry,
        ):
            config = TTSModelConfig(provider="openai", model="tts-test", voice="alloy")
            with self.assertRaisesRegex(RuntimeError, "TTS cue 重试耗尽"):
                await generate_tts_audio_for_cues(cues, temp_dir, config, api_key="test-key")
            self.assertEqual(list(calls.values()), [1, 4, 1])

            should_fail = False
            await generate_tts_audio_for_cues(cues, temp_dir, config, api_key="test-key")
            self.assertEqual(list(calls.values()), [1, 5, 1])

            changed_voice = TTSModelConfig(provider="openai", model="tts-test", voice="nova")
            await generate_tts_audio_for_cues(cues, temp_dir, changed_voice, api_key="test-key")

        self.assertEqual(list(calls.values()), [2, 6, 2])


class TranslationCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_rolls_back_video_running_state_and_task_status(self):
        asr_result = _make_asr_result()
        cues = build_translation_cues(asr_result)
        for cue in cues:
            cue.translated_text = "Hello"
            cue.status = "translated"
        video = _make_video(
            "vid_cancel",
            asr_result=asr_result,
            cues=cues,
            status="tts_running",
        )
        task = Task(
            id="task_cancel_translate",
            name="translate-cancel",
            task_type=TaskType.ONE_CLICK_TRANSLATE,
            status=TaskStatus.TTS_RUNNING,
            translation_videos=[video],
        )
        store = RecordingStore(task)
        entered = asyncio.Event()

        async def fake_process(*_args, **_kwargs):
            entered.set()
            await asyncio.Event().wait()

        async def fake_rollback(_task_id, fallback, _source, _label):
            task.status = fallback
            store.update(task)

        with patch("backend.routers.translate.get_store", return_value=store), patch(
            "backend.routers.translate._process_translation_video",
            new=AsyncMock(side_effect=fake_process),
        ), patch(
            "backend.routers.translate._rollback_cancelled_task",
            new=AsyncMock(side_effect=fake_rollback),
        ), patch(
            "backend.routers.translate._task_runner_superseded",
            return_value=False,
        ), patch(
            "backend.routers.translate.cancel_asr_processes",
        ):
            runner = asyncio.create_task(
                main._run_one_click_translate_task(task.id, _mock_request())
            )
            await entered.wait()
            runner.cancel()
            await runner

        self.assertEqual(video.status, "translated")
        self.assertIsNone(video.error_message)
        self.assertEqual(task.status, TaskStatus.PENDING)


if __name__ == "__main__":
    unittest.main()

