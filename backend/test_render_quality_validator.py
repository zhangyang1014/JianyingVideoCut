import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.services.render_quality_validator import validate_rendered_media


class RenderQualityValidatorTests(unittest.TestCase):
    def test_reports_missing_output_as_error(self):
        report = validate_rendered_media("/tmp/does-not-exist.mp4", expected_duration=3.0)

        self.assertFalse(report.passed)
        self.assertEqual(report.issues[0].code, "RQ-01")

    def test_accepts_readable_audio_video_within_duration_tolerance(self):
        payload = {
            "format": {"duration": "10.2"},
            "streams": [
                {"codec_type": "video"},
                {"codec_type": "audio"},
            ],
        }
        with tempfile.NamedTemporaryFile(suffix=".mp4") as output, patch(
            "backend.services.render_quality_validator.run_text_subprocess",
            return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload)),
        ):
            output.write(b"media")
            output.flush()
            report = validate_rendered_media(output.name, expected_duration=10.0)

        self.assertTrue(report.passed)
        self.assertEqual(report.actual_duration, 10.2)

    def test_rejects_duration_mismatch_or_missing_audio(self):
        payload = {
            "format": {"duration": "14.0"},
            "streams": [{"codec_type": "video"}],
        }
        with tempfile.NamedTemporaryFile(suffix=".mp4") as output, patch(
            "backend.services.render_quality_validator.run_text_subprocess",
            return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload)),
        ):
            output.write(b"media")
            output.flush()
            report = validate_rendered_media(output.name, expected_duration=10.0)

        self.assertFalse(report.passed)
        self.assertEqual({issue.code for issue in report.issues}, {"RQ-05", "RQ-07"})


if __name__ == "__main__":
    unittest.main()
