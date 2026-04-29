import unittest

from backend.models.task import Segment, SegmentAction
from backend.services.semantic_auditor import build_audit_prompt, parse_claude_response


class HighlightReelParserTests(unittest.TestCase):
    def test_segment_model_exposes_clip_metadata_fields(self):
        self.assertIn("clip_group", Segment.model_fields)
        self.assertIn("clip_title", Segment.model_fields)

    def test_parse_claude_response_maps_clip_group_and_title(self):
        original_segments = [
            Segment(id="seg_001", start=0.0, end=4.0, text="第一段"),
            Segment(id="seg_002", start=10.0, end=14.0, text="第二段"),
        ]

        response_text = """
        [
          {"i": 1, "a": "k", "clip_group": 1, "clip_title": "第一支短视频", "style": "cold_open", "r": "强钩子"},
          {"i": 2, "a": "d", "clip_group": null, "clip_title": null, "r": "过渡废话"}
        ]
        """

        updated_segments = parse_claude_response(
            response_text=response_text,
            original_segments=original_segments,
            idx_to_id={1: "seg_001", 2: "seg_002"},
        )

        self.assertEqual(updated_segments[0].action, SegmentAction.KEEP)
        self.assertEqual(updated_segments[0].clip_group, 1)
        self.assertEqual(updated_segments[0].clip_title, "第一支短视频")
        self.assertEqual(updated_segments[0].style, "cold_open")
        self.assertEqual(updated_segments[1].action, SegmentAction.DELETE)
        self.assertIsNone(updated_segments[1].clip_group)
        self.assertIsNone(updated_segments[1].clip_title)

    def test_parse_claude_response_clears_stale_audit_state_for_missing_segments(self):
        original_segments = [
            Segment(id="seg_001", start=0.0, end=4.0, text="第一段", action=SegmentAction.DELETE, clip_group=9, clip_title="旧标题"),
            Segment(id="seg_002", start=10.0, end=14.0, text="第二段", action=SegmentAction.DELETE, clip_group=10, clip_title="旧标题2"),
        ]

        updated_segments = parse_claude_response(
            response_text='[{"i": 1, "a": "k", "clip_group": 1, "clip_title": "新标题"}]',
            original_segments=original_segments,
            idx_to_id={1: "seg_001", 2: "seg_002"},
        )

        self.assertEqual(updated_segments[0].action, SegmentAction.KEEP)
        self.assertEqual(updated_segments[0].clip_group, 1)
        self.assertEqual(updated_segments[0].clip_title, "新标题")
        self.assertEqual(updated_segments[1].action, SegmentAction.KEEP)
        self.assertIsNone(updated_segments[1].clip_group)
        self.assertIsNone(updated_segments[1].clip_title)
        self.assertIsNone(updated_segments[1].reason)

    def test_build_audit_prompt_requests_clip_metadata_for_highlight_reel(self):
        prompt, _ = build_audit_prompt(
            segments=[Segment(id="seg_001", start=0.0, end=4.0, text="第一段")],
            task_type="highlight_reel",
        )

        self.assertIn("clip_group", prompt)
        self.assertIn("clip_title", prompt)
        self.assertIn("1剪多", prompt)


if __name__ == "__main__":
    unittest.main()
