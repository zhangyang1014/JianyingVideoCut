import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.core.task_runner import execute_with_retry as real_execute_with_retry
from backend.models.app_config import AppConfig, AsrRuntimeConfig
from backend.models.task import Segment, TaskParams, WordTimestamp
from backend.services import asr_pipeline


class RecordingLoop:
    def __init__(self):
        self.calls = []

    async def run_in_executor(self, executor, func, *args):
        self.calls.append((executor, func, args))
        return func(*args)


class FakeWord:
    def __init__(self, word, start, end):
        self.word = word
        self.start = start
        self.end = end


class FakeSegment:
    def __init__(self, words):
        self.words = words


class FakeWhisperModel:
    def __init__(self, model_name, device, compute_type):
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type

    def transcribe(self, audio_path, language, word_timestamps, vad_filter, vad_parameters):
        return (
            [
                FakeSegment(
                    [
                        FakeWord("你好", 0.1, 0.5),
                        FakeWord("世界", 0.5, 0.9),
                    ]
                )
            ],
            {"language": language, "audio_path": audio_path},
        )


class SegmentBySilenceTests(unittest.TestCase):
    def _chars(self, text, start, speaker=None, step=0.12):
        words = []
        t = start
        for char in text:
            words.append(WordTimestamp(word=char, start=round(t, 3), end=round(t + 0.08, 3), speaker=speaker))
            t += step
        return words

    def test_segment_by_silence_splits_before_exceeding_max_duration(self):
        words = [
            WordTimestamp(word="我", start=0.0, end=4.0),
            WordTimestamp(word="爱", start=4.05, end=8.0),
            WordTimestamp(word="剪", start=8.05, end=12.2),
            WordTimestamp(word="辑", start=12.25, end=15.9),
        ]

        segments = asr_pipeline.segment_by_silence(
            words,
            silence_threshold=0.5,
            min_duration=0.3,
            max_duration=12.0,
        )

        self.assertEqual([seg.text for seg in segments], ["我爱", "剪辑"])
        self.assertTrue(all((seg.end - seg.start) < 12.0 for seg in segments))

    def test_segment_by_silence_preserves_short_segment_as_candidate(self):
        words = [WordTimestamp(word="好", start=0.0, end=0.08)]

        segments = asr_pipeline.segment_by_silence(
            words,
            silence_threshold=0.3,
            min_duration=0.3,
            max_duration=12.0,
        )

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].text, "好")
        self.assertEqual(segments[0].candidate_edits[0].source, "short_segment")

    def test_filler_candidate_edits_are_attached_to_segments(self):
        words = [
            WordTimestamp(word="我", start=0.0, end=0.1),
            WordTimestamp(word="嗯", start=0.12, end=0.2),
            WordTimestamp(word="懂", start=0.22, end=0.34),
        ]
        tagged_words = asr_pipeline.annotate_filler_boundaries(words, {"嗯"})
        edits = asr_pipeline._extract_filler_candidate_edits(tagged_words)

        segments = asr_pipeline.segment_by_silence(
            tagged_words,
            silence_threshold=0.3,
            min_duration=0.3,
            max_duration=12.0,
            candidate_edits=edits,
        )

        self.assertEqual(len(edits), 1)
        self.assertEqual(segments[0].candidate_edits[0].source, "filler")
        self.assertEqual(segments[0].candidate_edits[0].text, "嗯")

    def test_glossary_alias_mapping_supports_single_token_length_change(self):
        words = [WordTimestamp(word="克劳德", start=0.0, end=0.4)]
        glossary = [asr_pipeline.GlossaryEntry(source="克劳德", target="Claude")]

        corrected, edits = asr_pipeline.apply_glossary_with_report(words, glossary)

        self.assertEqual(corrected[0].word, "Claude")
        self.assertEqual(edits[0].source, "glossary")
        self.assertEqual(edits[0].edit_type, "normalize_text")

    def test_strip_analysis_tags_before_rerun(self):
        words = [
            WordTimestamp(word="<STU>就", start=0.0, end=0.1, speaker="spk0"),
            WordTimestamp(word="<FIL s=0.100 e=0.300>嗯", start=0.12, end=0.2, speaker="spk0"),
        ]

        cleaned = asr_pipeline.strip_analysis_tags_from_words(words)

        self.assertEqual([w.word for w in cleaned], ["就", "嗯"])
        self.assertEqual([w.speaker for w in cleaned], ["spk0", "spk0"])

    def test_postprocess_merges_short_semantic_fragment(self):
        words = (
            self._chars("所以它更偏", 0.0)
            + self._chars("理科啊是什么呀", 1.02)
        )

        raw_segments = asr_pipeline.segment_by_silence(
            words,
            silence_threshold=0.3,
            min_duration=0.3,
            max_duration=12.0,
        )
        merged_segments = asr_pipeline.postprocess_segments(raw_segments)

        self.assertEqual([seg.text for seg in raw_segments], ["所以它更偏", "理科啊是什么呀"])
        self.assertEqual([seg.text for seg in merged_segments], ["所以它更偏理科啊是什么呀"])

    def test_postprocess_keeps_sentence_end_boundary(self):
        words = (
            self._chars("谁是卧底。", 0.0)
            + self._chars("怎么玩儿", 1.24)
        )

        raw_segments = asr_pipeline.segment_by_silence(
            words,
            silence_threshold=0.3,
            min_duration=0.3,
            max_duration=12.0,
        )
        merged_segments = asr_pipeline.postprocess_segments(raw_segments)

        self.assertEqual([seg.text for seg in merged_segments], ["谁是卧底。", "怎么玩儿"])

    def test_postprocess_merges_standalone_sentence_punctuation_into_previous_card(self):
        words = (
            self._chars("露营安全吗", 0.0)
            + self._chars("。", 3.0)
            + self._chars("主角土豆", 4.0)
        )

        raw_segments = asr_pipeline.segment_by_silence(
            words,
            silence_threshold=0.3,
            min_duration=0.3,
            max_duration=12.0,
        )
        self.assertEqual([seg.text for seg in raw_segments], ["露营安全吗", "。", "主角土豆"])
        processed_segments = asr_pipeline.postprocess_segments(raw_segments)

        self.assertEqual([seg.text for seg in processed_segments], ["露营安全吗。", "主角土豆"])

    def test_postprocess_merges_standalone_ascii_question_mark_into_previous_card(self):
        words = (
            self._chars("你熟了吗", 0.0)
            + self._chars("?", 3.0)
            + self._chars("这一讲", 4.0)
        )

        raw_segments = asr_pipeline.segment_by_silence(
            words,
            silence_threshold=0.3,
            min_duration=0.3,
            max_duration=12.0,
        )
        processed_segments = asr_pipeline.postprocess_segments(raw_segments)

        self.assertEqual([seg.text for seg in processed_segments], ["你熟了吗?", "这一讲"])

    def test_postprocess_merges_isolated_particle_without_diarization(self):
        words = (
            self._chars("要去户外", 0.0)
            + self._chars("的", 1.0)
            + self._chars("。", 1.8)
        )

        raw_segments = asr_pipeline.segment_by_silence(
            words,
            silence_threshold=0.3,
            min_duration=0.3,
            max_duration=12.0,
        )
        self.assertEqual([seg.text for seg in raw_segments], ["要去户外", "的", "。"])
        processed_segments = asr_pipeline.postprocess_segments(raw_segments)
        self.assertEqual([seg.text for seg in processed_segments], ["要去户外的。"])

    def test_postprocess_merges_sentence_start_pronoun_into_next_segment(self):
        words = (
            self._chars("上一句。", 0.0)
            + self._chars("我", 1.0)
            + self._chars("们继续说", 1.86)
        )

        raw_segments = asr_pipeline.segment_by_silence(
            words,
            silence_threshold=0.3,
            min_duration=0.3,
            max_duration=12.0,
        )
        processed_segments = asr_pipeline.postprocess_segments(raw_segments)

        self.assertEqual([seg.text for seg in raw_segments], ["上一句。", "我", "们继续说"])
        self.assertEqual([seg.text for seg in processed_segments], ["上一句。", "我们继续说"])

    def test_postprocess_keeps_independent_short_reply_after_sentence_end(self):
        words = (
            self._chars("上一句。", 0.0)
            + self._chars("好", 1.0)
            + self._chars("我们继续说", 1.86)
        )

        raw_segments = asr_pipeline.segment_by_silence(
            words,
            silence_threshold=0.3,
            min_duration=0.3,
            max_duration=12.0,
        )
        processed_segments = asr_pipeline.postprocess_segments(raw_segments)

        self.assertEqual(
            [seg.text for seg in processed_segments],
            ["上一句。", "好", "我们继续说"],
        )

    def test_postprocess_splits_internal_sentence_punctuation_for_review_cards(self):
        words = self._chars("这个书就是干嘛？它不是为了培养你什么用词语优美啊", 0.0)

        raw_segments = asr_pipeline.segment_by_silence(
            words,
            silence_threshold=0.3,
            min_duration=0.3,
            max_duration=12.0,
        )
        processed_segments = asr_pipeline.postprocess_segments(raw_segments)

        self.assertEqual([seg.text for seg in raw_segments], ["这个书就是干嘛？它不是为了培养你什么用词语优美啊"])
        self.assertEqual(
            [seg.text for seg in processed_segments],
            ["这个书就是干嘛？", "它不是为了培养你什么用词语优美啊"],
        )

    def test_postprocess_keeps_long_pause_boundary(self):
        words = (
            self._chars("所以它更偏", 0.0)
            + self._chars("理科啊", 1.6)
        )

        raw_segments = asr_pipeline.segment_by_silence(
            words,
            silence_threshold=0.3,
            min_duration=0.3,
            max_duration=12.0,
        )
        merged_segments = asr_pipeline.postprocess_segments(raw_segments)

        self.assertEqual([seg.text for seg in merged_segments], ["所以它更偏", "理科啊"])

    def test_postprocess_keeps_real_speaker_turn_boundary(self):
        words = (
            self._chars("所以它更偏", 0.0, speaker="spk0")
            + self._chars("理科啊", 1.02, speaker="spk1")
        )

        raw_segments = asr_pipeline.segment_by_silence(
            words,
            silence_threshold=0.3,
            min_duration=0.3,
            max_duration=12.0,
        )
        merged_segments = asr_pipeline.postprocess_segments(raw_segments, enable_diarization=True)

        self.assertEqual([seg.text for seg in merged_segments], ["所以它更偏", "理科啊"])

    def test_semantic_fragment_merge_keeps_overlong_subtitle_boundary(self):
        segments = [
            Segment(start=0.0, end=0.8, text="所以它更偏"),
            Segment(start=1.0, end=3.0, text="理科啊这个后面还有一长串解释用于超过字幕长度限制"),
        ]

        merged_segments = asr_pipeline.merge_short_semantic_fragments(segments, max_chars=20)

        self.assertEqual([seg.text for seg in merged_segments], [seg.text for seg in segments])

    def test_postprocess_moves_trailing_fragment_to_next_segment(self):
        prev_words = self._chars("这个书就是干嘛它不是为了培养语感所以它更偏", 0.0)
        curr_start = prev_words[-1].end + 0.34
        words = prev_words + self._chars("理理科啊它是什么呀", curr_start)

        raw_segments = asr_pipeline.segment_by_silence(
            words,
            silence_threshold=0.3,
            min_duration=0.3,
            max_duration=12.0,
        )
        merged_segments = asr_pipeline.postprocess_segments(raw_segments)

        self.assertEqual([seg.text for seg in raw_segments], ["这个书就是干嘛它不是为了培养语感所以它更偏", "理理科啊它是什么呀"])
        self.assertEqual(
            [seg.text for seg in merged_segments],
            ["这个书就是干嘛它不是为了培养语感", "所以它更偏理理科啊它是什么呀"],
        )

    def test_postprocess_moves_discourse_tail_from_hard_cut_to_next_segment(self):
        prev_words = self._chars("你得逐字逐句的去分析才能找到漏洞找到破绽所以他对文字就是对孩子读文字对吧", 0.0, step=0.31)
        curr_words = self._chars("养成节奏就是细节读细节的能力", prev_words[-1].end, step=0.22)
        segments = [
            Segment(
                start=0.0,
                end=11.8,
                text="你得逐字逐句的去分析才能找到漏洞找到破绽所以他对文字就是对孩子读文字对吧",
                words=prev_words,
            ),
            Segment(
                start=11.8,
                end=18.0,
                text="养成节奏就是细节读细节的能力",
                words=curr_words,
            ),
        ]

        merged_segments = asr_pipeline.postprocess_segments(segments)

        self.assertEqual(
            [seg.text for seg in merged_segments],
            [
                "你得逐字逐句的去分析才能找到漏洞找到破绽所以他对文字就是对孩子读文字",
                "对吧养成节奏就是细节读细节的能力",
            ],
        )

    def test_postprocess_merges_protected_phrase_across_slightly_long_gap(self):
        prev_words = self._chars("分析问题的你看谁是", 0.0)
        curr_start = prev_words[-1].end + 0.56
        words = prev_words + self._chars("卧底这个是去鬼怪镇", curr_start)

        raw_segments = asr_pipeline.segment_by_silence(
            words,
            silence_threshold=0.3,
            min_duration=0.3,
            max_duration=12.0,
        )
        merged_segments = asr_pipeline.postprocess_segments(raw_segments)

        self.assertEqual([seg.text for seg in raw_segments], ["分析问题的你看谁是", "卧底这个是去鬼怪镇"])
        self.assertEqual([seg.text for seg in merged_segments], ["分析问题的你看谁是卧底这个是去鬼怪镇"])


class FunASRChunkPlannerTests(unittest.TestCase):
    def test_resolve_chunk_config_uses_app_config(self):
        app_config = AppConfig(
            asr=AsrRuntimeConfig(
                funasr_chunk_minutes=2,
                funasr_parallel_chunks=6,
            )
        )

        with patch("backend.services.app_config_store.load_app_config", return_value=app_config):
            config = asr_pipeline.resolve_funasr_chunk_config()

        self.assertEqual(config.target_chunk_sec, 120.0)
        self.assertEqual(config.min_chunk_sec, 96.0)
        self.assertEqual(config.max_chunk_sec, 144.0)
        self.assertEqual(config.max_parallel_chunks, 6)

    def test_default_macro_chunks_are_about_five_minutes(self):
        chunks = asr_pipeline.plan_funasr_macro_chunks(3600.0, silences=[])

        self.assertEqual(len(chunks), 12)
        self.assertEqual([c.logical_start for c in chunks[:3]], [0.0, 300.0, 600.0])
        self.assertEqual(chunks[0].logical_end, 300.0)
        self.assertEqual(chunks[-1].logical_end, 3600.0)

    def test_plan_macro_chunks_prefers_nearby_silence_point(self):
        config = asr_pipeline.FunASRChunkConfig(
            target_chunk_sec=480.0,
            min_chunk_sec=300.0,
            max_chunk_sec=600.0,
            overlap_sec=2.0,
        )
        chunks = asr_pipeline.plan_funasr_macro_chunks(
            1000.0,
            silences=[(477.5, 478.5)],
            config=config,
        )

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].logical_end, 478.0)
        self.assertEqual(chunks[1].extract_start, 476.0)
        self.assertEqual(chunks[1].extract_end, 1000.0)

    def test_plan_macro_chunks_hard_cuts_without_silence(self):
        config = asr_pipeline.FunASRChunkConfig(
            target_chunk_sec=480.0,
            min_chunk_sec=300.0,
            max_chunk_sec=600.0,
            overlap_sec=2.0,
        )
        chunks = asr_pipeline.plan_funasr_macro_chunks(1100.0, silences=[], config=config)

        self.assertEqual([c.logical_start for c in chunks], [0.0, 480.0, 960.0])
        self.assertEqual(chunks[-1].logical_end, 1100.0)


class FunASRChunkMergeTests(unittest.TestCase):
    def test_merge_macro_chunks_removes_overlap_duplicate(self):
        left = asr_pipeline.FunASRMacroChunk(
            index=0,
            logical_start=0.0,
            logical_end=10.0,
            extract_start=0.0,
            extract_end=12.0,
            words=[
                WordTimestamp(word="我", start=8.0, end=8.1),
                WordTimestamp(word="爱", start=8.2, end=8.3),
                WordTimestamp(word="剪", start=8.4, end=8.5),
                WordTimestamp(word="辑", start=8.6, end=8.7),
            ],
        )
        right = asr_pipeline.FunASRMacroChunk(
            index=1,
            logical_start=10.0,
            logical_end=20.0,
            extract_start=8.0,
            extract_end=20.0,
            words=[
                WordTimestamp(word="我", start=8.02, end=8.12),
                WordTimestamp(word="爱", start=8.22, end=8.32),
                WordTimestamp(word="剪", start=8.42, end=8.52),
                WordTimestamp(word="辑", start=8.62, end=8.72),
                WordTimestamp(word="很", start=10.1, end=10.2),
                WordTimestamp(word="快", start=10.3, end=10.4),
            ],
        )

        merged = asr_pipeline.merge_funasr_macro_chunks(
            [left, right],
            config=asr_pipeline.FunASRChunkConfig(overlap_sec=2.0),
        )

        self.assertEqual("".join(w.word for w in merged), "我爱剪辑很快")
        self.assertEqual([w.start for w in merged], sorted(w.start for w in merged))


class AsrPipelineExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_whisper_asr_offloads_model_load_and_transcribe(self):
        loop = RecordingLoop()
        fake_module = types.SimpleNamespace(WhisperModel=FakeWhisperModel)

        with patch("backend.services.asr_pipeline.asyncio.get_running_loop", return_value=loop), patch.dict(
            sys.modules, {"faster_whisper": fake_module}
        ):
            words = await asr_pipeline.run_whisper_asr("demo.wav", whisper_model="small")

        self.assertEqual([w.word for w in words], ["你好", "世界"])
        self.assertEqual(len(loop.calls), 2)

    async def test_run_asr_pipeline_offloads_extract_audio(self):
        loop = RecordingLoop()
        fake_words = [WordTimestamp(word="你好", start=0.1, end=0.5)]

        with patch("backend.services.asr_pipeline.asyncio.get_running_loop", return_value=loop), patch(
            "backend.services.asr_pipeline.os.path.exists", return_value=True
        ), patch(
            "backend.services.asr_pipeline.extract_audio", return_value=True
        ), patch(
            "backend.services.asr_pipeline.run_whisper_asr",
            new=AsyncMock(return_value=fake_words),
        ), patch(
            "backend.services.asr_pipeline.load_glossary", return_value=[]
        ), patch(
            "backend.services.asr_pipeline.detect_stutter", side_effect=lambda words: words
        ), patch(
            "backend.services.asr_pipeline.annotate_filler_boundaries",
            side_effect=lambda words, _filler_set: words,
        ), patch(
            "backend.services.asr_pipeline.transform_to_tagged_script", return_value="你好"
        ), patch(
            "backend.services.asr_pipeline.segment_by_silence", return_value=[]
        ), patch(
            "backend.services.asr_pipeline.merge_leading_particles",
            side_effect=lambda segments: segments,
        ), patch(
            "backend.services.asr_pipeline.merge_leading_punctuation",
            side_effect=lambda segments: segments,
        ), patch(
            "backend.services.asr_pipeline.get_video_duration", return_value=1.0
        ):
            result = await asr_pipeline.run_asr_pipeline("demo.mp4", TaskParams(enable_diarization=False))

        self.assertEqual(result.duration, 1.0)
        self.assertEqual(len(loop.calls), 1)

    async def test_run_asr_pipeline_passes_max_segment_duration(self):
        fake_words = [WordTimestamp(word="你好", start=0.1, end=0.5)]

        with patch(
            "backend.services.asr_pipeline.os.path.exists", return_value=False
        ), patch(
            "backend.services.asr_pipeline.run_whisper_asr",
            new=AsyncMock(return_value=fake_words),
        ), patch(
            "backend.services.asr_pipeline.load_glossary", return_value=[]
        ), patch(
            "backend.services.asr_pipeline.detect_stutter", side_effect=lambda words: words
        ), patch(
            "backend.services.asr_pipeline.annotate_filler_boundaries",
            side_effect=lambda words, _filler_set: words,
        ), patch(
            "backend.services.asr_pipeline.transform_to_tagged_script", return_value="你好"
        ), patch(
            "backend.services.asr_pipeline.segment_by_silence", return_value=[]
        ) as segment_mock, patch(
            "backend.services.asr_pipeline.merge_leading_particles",
            side_effect=lambda segments: segments,
        ), patch(
            "backend.services.asr_pipeline.merge_leading_punctuation",
            side_effect=lambda segments: segments,
        ):
            await asr_pipeline.run_asr_pipeline("demo.mp4", TaskParams(enable_diarization=False))

        _, kwargs = segment_mock.call_args
        self.assertEqual(kwargs["max_duration"], 12.0)

    async def test_run_funasr_asr_routes_short_audio_to_single_pass(self):
        with patch(
            "backend.services.asr_pipeline.get_video_duration", return_value=120.0
        ), patch(
            "backend.services.asr_pipeline.os.path.exists", return_value=True
        ), patch(
            "backend.services.asr_pipeline._run_funasr_asr_single_pass",
            new=AsyncMock(return_value=[WordTimestamp(word="短", start=0.0, end=0.1)]),
        ) as single_mock, patch(
            "backend.services.asr_pipeline._run_funasr_asr_chunked",
            new=AsyncMock(return_value=[]),
        ) as chunked_mock:
            words = await asr_pipeline.run_funasr_asr("short.wav")

        self.assertEqual([w.word for w in words], ["短"])
        single_mock.assert_awaited_once()
        chunked_mock.assert_not_awaited()

    async def test_run_funasr_asr_routes_long_audio_to_chunked(self):
        with patch(
            "backend.services.asr_pipeline.get_video_duration", return_value=3600.0
        ), patch(
            "backend.services.asr_pipeline.os.path.exists", return_value=True
        ), patch(
            "backend.services.asr_pipeline._run_funasr_asr_single_pass",
            new=AsyncMock(return_value=[]),
        ) as single_mock, patch(
            "backend.services.asr_pipeline._run_funasr_asr_chunked",
            new=AsyncMock(return_value=[WordTimestamp(word="长", start=0.0, end=0.1)]),
        ) as chunked_mock:
            words = await asr_pipeline.run_funasr_asr("long.wav")

        self.assertEqual([w.word for w in words], ["长"])
        chunked_mock.assert_awaited_once()
        single_mock.assert_not_awaited()


async def _fast_retry(operation):
    return await real_execute_with_retry(
        operation,
        base_delay=0,
        max_delay=0,
        jitter_ratio=0,
    )


class AsrCheckpointRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_macro_chunk_retries_four_times_and_resume_only_runs_missing_chunk(self):
        config = asr_pipeline.FunASRChunkConfig(
            min_duration_to_chunk=1,
            target_chunk_sec=10,
            min_chunk_sec=8,
            max_chunk_sec=12,
            overlap_sec=0,
            max_parallel_chunks=2,
            recalibrate_chunks=False,
        )
        calls = {0: 0, 1: 0}
        fail_second = True

        async def fake_single(audio_path, **_kwargs):
            index = int(Path(audio_path).stem.split("_")[-1])
            calls[index] += 1
            if index == 1 and fail_second:
                raise RuntimeError("chunk two transient failure")
            return [WordTimestamp(word=str(index), start=0.0, end=0.1)]

        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "source.wav"
            audio_path.write_bytes(b"source-audio")
            cache_dir = Path(temp_dir) / "cache"
            with patch.dict(os.environ, {"GOLDENCLIP_ASR_CHECKPOINT_DIR": str(cache_dir)}), patch(
                "backend.services.asr_pipeline._detect_silences", return_value=[]
            ), patch(
                "backend.services.asr_pipeline.get_funasr_model", new=AsyncMock()
            ), patch(
                "backend.services.asr_pipeline._extract_audio_range", return_value=True
            ), patch(
                "backend.services.asr_pipeline._run_funasr_asr_single_pass",
                new=AsyncMock(side_effect=fake_single),
            ), patch(
                "backend.services.asr_pipeline.execute_with_retry", new=_fast_retry
            ):
                with self.assertRaisesRegex(RuntimeError, "重试耗尽"):
                    await asr_pipeline._run_funasr_asr_chunked(
                        str(audio_path), 20.0, None, config
                    )
                self.assertEqual(calls, {0: 1, 1: 4})

                fail_second = False
                result = await asr_pipeline._run_funasr_asr_chunked(
                    str(audio_path), 20.0, None, config
                )

            self.assertEqual(calls, {0: 1, 1: 5})
            self.assertEqual("".join(word.word for word in result), "01")

    async def test_corrupt_macro_chunk_cache_is_a_miss(self):
        config = asr_pipeline.FunASRChunkConfig(
            min_duration_to_chunk=1,
            target_chunk_sec=10,
            min_chunk_sec=8,
            max_chunk_sec=12,
            overlap_sec=0,
            max_parallel_chunks=1,
            recalibrate_chunks=False,
        )
        calls = {0: 0, 1: 0}

        async def fake_single(audio_path, **_kwargs):
            index = int(Path(audio_path).stem.split("_")[-1])
            calls[index] += 1
            return [WordTimestamp(word=str(index), start=0.0, end=0.1)]

        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "source.wav"
            audio_path.write_bytes(b"source-audio")
            cache_dir = Path(temp_dir) / "cache"
            patches = (
                patch.dict(os.environ, {"GOLDENCLIP_ASR_CHECKPOINT_DIR": str(cache_dir)}),
                patch("backend.services.asr_pipeline._detect_silences", return_value=[]),
                patch("backend.services.asr_pipeline.get_funasr_model", new=AsyncMock()),
                patch("backend.services.asr_pipeline._extract_audio_range", return_value=True),
                patch(
                    "backend.services.asr_pipeline._run_funasr_asr_single_pass",
                    new=AsyncMock(side_effect=fake_single),
                ),
                patch("backend.services.asr_pipeline.execute_with_retry", new=_fast_retry),
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                await asr_pipeline._run_funasr_asr_chunked(str(audio_path), 20.0, None, config)
                cache_files = sorted((cache_dir / "funasr").glob("*.json"))
                self.assertEqual(len(cache_files), 2)
                cache_files[1].write_text("{broken", encoding="utf-8")
                await asr_pipeline._run_funasr_asr_chunked(str(audio_path), 20.0, None, config)

            self.assertEqual(sum(calls.values()), 3)
            self.assertEqual(sorted(calls.values()), [1, 2])

    async def test_whisper_failure_retries_four_times_without_mock_output(self):
        calls = 0

        class FailingWhisperModel:
            def __init__(self, *_args, **_kwargs):
                nonlocal calls
                calls += 1
                raise RuntimeError("temporary model load failure")

        fake_module = types.SimpleNamespace(WhisperModel=FailingWhisperModel)
        with patch.dict(sys.modules, {"faster_whisper": fake_module}), patch(
            "backend.services.asr_pipeline.execute_with_retry", new=_fast_retry
        ), patch(
            "backend.services.asr_pipeline._generate_mock_asr_data"
        ) as mock_data:
            with self.assertRaisesRegex(Exception, "Whisper ASR"):
                await asr_pipeline.run_whisper_asr("demo.wav")

        self.assertEqual(calls, 4)
        mock_data.assert_not_called()

    async def test_diarization_timeout_retries_four_times_without_empty_success_cache(self):
        calls = 0

        def fake_pipeline(**_kwargs):
            def run(_audio_path, **_run_kwargs):
                nonlocal calls
                calls += 1
                raise TimeoutError("CAM++ timeout")
            return run

        pipelines_module = types.ModuleType("modelscope.pipelines")
        pipelines_module.pipeline = fake_pipeline
        constant_module = types.ModuleType("modelscope.utils.constant")
        constant_module.Tasks = types.SimpleNamespace(speaker_diarization="speaker_diarization")
        failure = {}
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "source.wav"
            audio_path.write_bytes(b"source-audio")
            cache_dir = Path(temp_dir) / "cache"
            with patch.dict(
                sys.modules,
                {
                    "modelscope": types.ModuleType("modelscope"),
                    "modelscope.pipelines": pipelines_module,
                    "modelscope.utils": types.ModuleType("modelscope.utils"),
                    "modelscope.utils.constant": constant_module,
                },
            ), patch.dict(
                os.environ,
                {"GOLDENCLIP_ASR_CHECKPOINT_DIR": str(cache_dir)},
            ), patch(
                "backend.services.asr_pipeline.execute_with_retry",
                new=_fast_retry,
            ):
                result = await asr_pipeline.run_diarization(
                    str(audio_path),
                    timeout=0.01,
                    failure_out=failure,
                )

            self.assertEqual(result, [])
            self.assertEqual(calls, 4)
            self.assertTrue(failure["retry_exhausted"])
            self.assertEqual(list(cache_dir.rglob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
