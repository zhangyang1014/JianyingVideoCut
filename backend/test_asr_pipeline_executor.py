import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from backend.models.task import TaskParams, WordTimestamp
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


if __name__ == "__main__":
    unittest.main()
