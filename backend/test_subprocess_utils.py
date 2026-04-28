import subprocess
import unittest
from unittest.mock import patch

from backend.services import asr_pipeline, ffmpeg_executor


class SafeTextSubprocessTests(unittest.TestCase):
    def test_run_text_subprocess_forces_utf8_replacement(self):
        from backend.services.subprocess_utils import run_text_subprocess

        sentinel = object()
        with patch("backend.services.subprocess_utils.subprocess.run", return_value=sentinel) as run_mock:
            result = run_text_subprocess(["ffprobe", "-version"], capture_output=True)

        self.assertIs(result, sentinel)
        _, kwargs = run_mock.call_args
        self.assertTrue(kwargs["text"])
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")

    def test_asr_pipeline_uses_safe_text_subprocess_for_video_duration(self):
        completed = subprocess.CompletedProcess(
            args=["ffprobe"],
            returncode=0,
            stdout='{"format":{"duration":"12.3"}}',
            stderr="",
        )

        with patch("backend.services.asr_pipeline.run_text_subprocess", return_value=completed) as run_mock:
            duration = asr_pipeline.get_video_duration("demo.mp4")

        self.assertEqual(duration, 12.3)
        run_mock.assert_called_once()

    def test_ffmpeg_executor_uses_safe_text_subprocess_for_video_info(self):
        completed = subprocess.CompletedProcess(
            args=["ffprobe"],
            returncode=0,
            stdout='{"format":{"duration":"7.5","size":"42","bit_rate":"900"},"streams":[{"codec_type":"video","width":1280,"height":720,"r_frame_rate":"30/1","codec_name":"h264"}]}',
            stderr="",
        )

        with patch("backend.services.ffmpeg_executor.run_text_subprocess", return_value=completed) as run_mock:
            info = ffmpeg_executor.get_video_info("demo.mp4")

        self.assertEqual(info["duration"], 7.5)
        self.assertEqual(info["codec"], "h264")
        run_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
