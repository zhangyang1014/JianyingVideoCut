import json
import unittest

from backend.models.task import (
    HighlightReel,
    HighlightReelBuildOptions,
    HighlightReelQualityReport,
    ProductEntity,
    ProductOccurrence,
    ProductSemanticMark,
    Segment,
    SegmentAction,
)
from backend.services.highlight_reel_plan import (
    apply_plan_issues_to_reels,
    build_highlight_reel_plan,
    repair_highlight_reels,
    rebuild_reel_metrics,
    reels_to_clip_groups,
    sync_plan_to_segments,
    validate_highlight_reels_code,
)
from backend.services.highlight_reel_validator import _build_validation_payload, _parse_validation_response


class HighlightReelPlanTests(unittest.TestCase):
    def test_default_duration_bounds_are_60_to_300_seconds(self):
        options = HighlightReelBuildOptions()

        self.assertEqual(options.min_reel_duration, 60)
        self.assertEqual(options.max_reel_duration, 300)

    def _mark(self, product: str, roles=None, angle="核心卖点", eligible=True):
        return ProductSemanticMark(
            product_entities=[ProductEntity(name=product, canonical_name=product)],
            roles=roles or ["selling_point"],
            selling_points=[angle] if angle else [],
            angle_label=angle,
            eligible_for_highlight=eligible,
            exclude_reason=None if eligible else "链接话术",
        )

    def _segments(self):
        return [
            Segment(id="seg_1", start=0.0, end=10.0, text="高能开场", action=SegmentAction.KEEP, clip_group=1, clip_title="第一支短视频"),
            Segment(id="seg_2", start=10.0, end=25.0, text="故事细节", action=SegmentAction.KEEP, clip_group=1),
            Segment(id="seg_gap", start=25.0, end=30.0, text="过渡", action=SegmentAction.DELETE),
            Segment(id="seg_3", start=40.0, end=70.0, text="反常识金句", action=SegmentAction.KEEP, clip_group=2, clip_title="第二支短视频"),
        ]

    def test_build_highlight_reel_plan_groups_by_clip_group(self):
        reels = build_highlight_reel_plan(
            self._segments(),
            options=HighlightReelBuildOptions(min_reel_duration=1),
        )
        self.assertEqual(len(reels), 2)
        self.assertEqual(reels[0].clip_group, 1)
        self.assertEqual(reels[0].segment_ids, ["seg_1", "seg_2"])
        self.assertEqual(reels[1].title, "第二支短视频")

    def test_build_highlight_reel_plan_prefers_product_semantic_groups(self):
        segments = [
            Segment(id="book_open", start=0.0, end=8.0, text="这套逻辑书适合8岁孩子", action=SegmentAction.KEEP, product_semantic=self._mark("逻辑书", ["product_opening"], "适用年龄")),
            Segment(id="book_point", start=8.0, end=30.0, text="它用故事训练推理", action=SegmentAction.KEEP, product_semantic=self._mark("逻辑书", ["selling_point"], "适用年龄")),
            Segment(id="cream_open", start=40.0, end=50.0, text="这瓶面霜是干皮用的", action=SegmentAction.KEEP, product_semantic=self._mark("面霜", ["product_opening"], "干皮适用")),
            Segment(id="cream_point", start=50.0, end=75.0, text="它的肤感不闷", action=SegmentAction.KEEP, product_semantic=self._mark("面霜", ["selling_point"], "干皮适用")),
        ]

        reels = build_highlight_reel_plan(segments, options=HighlightReelBuildOptions(min_reel_duration=1))

        self.assertEqual(len(reels), 2)
        self.assertEqual(reels[0].product_name, "逻辑书")
        self.assertEqual(reels[0].segment_ids, ["book_open", "book_point"])
        self.assertEqual(reels[1].product_name, "面霜")
        self.assertEqual(reels[1].segment_ids, ["cream_open", "cream_point"])

    def test_semantic_short_reel_merge_does_not_mix_products(self):
        segments = [
            Segment(id="book", start=0.0, end=15.0, text="逻辑书卖点", action=SegmentAction.KEEP, product_semantic=self._mark("逻辑书", ["selling_point"], "训练推理")),
            Segment(id="cream", start=16.0, end=31.0, text="面霜卖点", action=SegmentAction.KEEP, product_semantic=self._mark("面霜", ["selling_point"], "干皮适用")),
        ]

        reels = build_highlight_reel_plan(
            segments,
            options=HighlightReelBuildOptions(min_reel_duration=1, max_reel_duration=90),
        )

        self.assertEqual(len(reels), 2)
        self.assertEqual([reel.product_name for reel in reels], ["逻辑书", "面霜"])

    def test_auto_target_count_follows_product_groups_without_five_item_warning(self):
        options = HighlightReelBuildOptions(min_reel_duration=1)
        reels = build_highlight_reel_plan(self._segments(), options=options)
        report = validate_highlight_reels_code(reels, self._segments(), video_duration=100.0, options=options)

        self.assertFalse(any(issue.code == "HR-12" for issue in report.issues))
        self.assertFalse(any(issue.code == "HR-13" for issue in report.issues))

    def test_fixed_target_count_still_validates_expected_range(self):
        reels = build_highlight_reel_plan(
            self._segments(),
            options=HighlightReelBuildOptions(target_reel_count=5),
        )
        report = validate_highlight_reels_code(
            reels,
            self._segments(),
            video_duration=100.0,
            options=HighlightReelBuildOptions(target_reel_count=5),
        )

        self.assertTrue(any(issue.code == "HR-12" for issue in report.issues))

    def test_build_highlight_reel_plan_excludes_deleted_sentence_tail(self):
        segments = [
            Segment(id="seg_1_p1", start=0.0, end=25.0, text="可以找到谁是卧底", action=SegmentAction.KEEP, clip_group=1, clip_title="谁是卧底"),
            Segment(id="seg_1_p2", start=25.0, end=26.0, text="看第一个事件", action=SegmentAction.DELETE, clip_group=1),
            Segment(id="seg_2", start=26.1, end=30.0, text="被隐藏的例子", action=SegmentAction.DELETE, clip_group=1),
            Segment(id="seg_3", start=30.2, end=55.0, text="后续观点", action=SegmentAction.KEEP, clip_group=1),
        ]

        reels = build_highlight_reel_plan(
            segments,
            options=HighlightReelBuildOptions(min_reel_duration=1),
        )

        self.assertEqual(len(reels), 1)
        self.assertEqual(reels[0].segment_ids, ["seg_1_p1", "seg_3"])

    def test_build_highlight_reel_plan_drops_existing_clip_groups_below_min_duration(self):
        segments = [
            Segment(id="seg_short", start=0.0, end=15.0, text="太短", action=SegmentAction.KEEP, clip_group=1, clip_title="第一支"),
            Segment(id="seg_long", start=25.0, end=55.0, text="足够长", action=SegmentAction.KEEP, clip_group=2, clip_title="第二支"),
        ]
        reels = build_highlight_reel_plan(
            segments,
            options=HighlightReelBuildOptions(min_reel_duration=20),
        )

        self.assertEqual(len(reels), 1)
        self.assertEqual(reels[0].clip_group, 1)
        self.assertEqual(reels[0].segment_ids, ["seg_long"])

    def test_build_highlight_reel_plan_merges_short_opening_into_next_reel(self):
        segments = [
            Segment(id="intro", start=0.0, end=12.0, text="这套书适合8岁孩子", action=SegmentAction.KEEP, clip_group=1, clip_title="这套书适合8岁孩子", style="cold_open", compiled_from_operation="op_intro"),
            Segment(id="point", start=12.0, end=35.0, text="它用故事训练理性思考", action=SegmentAction.KEEP, clip_group=1, style="highlight", compiled_from_operation="op_point"),
        ]

        reels = build_highlight_reel_plan(
            segments,
            options=HighlightReelBuildOptions(
                target_reel_count=2,
                min_reel_duration=20,
                max_reel_duration=90,
            ),
        )

        self.assertEqual(len(reels), 1)
        self.assertEqual(reels[0].segment_ids, ["intro", "point"])
        self.assertEqual(reels[0].duration, 35.0)

    def test_build_highlight_reel_plan_uses_target_options_for_fallback_groups(self):
        segments = [
            Segment(id="seg_1", start=0.0, end=9.0, text="1", action=SegmentAction.KEEP),
            Segment(id="seg_2", start=10.0, end=19.0, text="2", action=SegmentAction.KEEP),
            Segment(id="seg_3", start=20.0, end=29.0, text="3", action=SegmentAction.KEEP),
            Segment(id="seg_4", start=30.0, end=39.0, text="4", action=SegmentAction.KEEP),
        ]

        reels = build_highlight_reel_plan(
            segments,
            options=HighlightReelBuildOptions(
                target_reel_count=2,
                min_reel_duration=5,
                max_reel_duration=20,
            ),
        )

        self.assertEqual(len(reels), 2)
        self.assertEqual(reels[0].segment_ids, ["seg_1", "seg_2"])
        self.assertEqual(reels[1].segment_ids, ["seg_3", "seg_4"])

    def test_build_highlight_reel_plan_splits_ai_group_by_semantic_units_and_max_duration(self):
        segments = [
            Segment(
                id=f"seg_{idx}",
                start=float(idx * 30),
                end=float(idx * 30 + 30),
                text=f"卖点 {idx}",
                action=SegmentAction.KEEP,
                clip_group=1,
                clip_title="产品卖点" if idx == 0 else None,
                style="highlight",
                compiled_from_operation=f"op_{idx // 2}",
            )
            for idx in range(5)
        ]

        reels = build_highlight_reel_plan(
            segments,
            options=HighlightReelBuildOptions(
                target_reel_count=5,
                min_reel_duration=1,
                max_reel_duration=90,
            ),
        )

        self.assertEqual(len(reels), 2)
        self.assertTrue(all(reel.duration <= 90 for reel in reels))
        self.assertEqual(reels[0].segment_ids, ["seg_0", "seg_1"])
        self.assertEqual(reels[1].segment_ids, ["seg_2", "seg_3", "seg_4"])

    def test_fallback_group_merge_never_exceeds_max_duration(self):
        segments = [
            Segment(id=f"seg_{idx}", start=float(idx * 31), end=float(idx * 31 + 30), text=str(idx), action=SegmentAction.KEEP)
            for idx in range(4)
        ]

        reels = build_highlight_reel_plan(
            segments,
            options=HighlightReelBuildOptions(
                target_reel_count=1,
                min_reel_duration=1,
                max_reel_duration=60,
            ),
        )

        self.assertGreater(len(reels), 1)
        self.assertTrue(all(reel.duration <= 60 for reel in reels))

    def test_short_semantic_reels_merge_toward_max_duration(self):
        segments = [
            Segment(
                id=f"seg_{idx}",
                start=float(idx * 24),
                end=float(idx * 24 + 24),
                text=f"卖点 {idx}",
                action=SegmentAction.KEEP,
                clip_group=1,
                clip_title="产品卖点" if idx == 0 else None,
                style="highlight",
                compiled_from_operation=f"op_{idx}",
            )
            for idx in range(4)
        ]

        reels = build_highlight_reel_plan(
            segments,
            options=HighlightReelBuildOptions(
                target_reel_count=2,
                min_reel_duration=20,
                max_reel_duration=90,
            ),
        )

        self.assertEqual(len(reels), 2)
        self.assertEqual([round(reel.duration, 1) for reel in reels], [72.0, 48.0])
        self.assertIn("seg_0", reels[1].segment_ids)
        self.assertTrue(all(reel.duration <= 90 for reel in reels))

    def test_build_highlight_reel_plan_drops_groups_below_min_duration(self):
        segments = [
            Segment(id="short", start=0.0, end=14.0, text="太短", action=SegmentAction.KEEP),
            Segment(id="long_1", start=25.0, end=45.0, text="足够长 1", action=SegmentAction.KEEP),
            Segment(id="long_2", start=45.0, end=66.0, text="足够长 2", action=SegmentAction.KEEP),
        ]

        reels = build_highlight_reel_plan(
            segments,
            options=HighlightReelBuildOptions(
                target_reel_count=2,
                min_reel_duration=40,
                max_reel_duration=200,
            ),
        )

        self.assertEqual(len(reels), 1)
        self.assertEqual(reels[0].clip_group, 1)
        self.assertEqual(reels[0].segment_ids, ["long_1", "long_2"])
        self.assertGreaterEqual(reels[0].duration, 40)

    def test_validate_highlight_reels_code_flags_missing_title(self):
        reels = [
            HighlightReel(
                reel_id="reel_1",
                clip_group=1,
                title="",
                segment_ids=["seg_1"],
                start=0.0,
                end=10.0,
                duration=10.0,
            )
        ]
        report = validate_highlight_reels_code(reels, self._segments(), video_duration=100.0)
        self.assertFalse(report.passed)
        self.assertTrue(any(issue.code == "HR-01" for issue in report.issues))

    def test_validate_highlight_reels_code_uses_custom_duration_bounds(self):
        reels = [
            HighlightReel(
                reel_id="reel_1",
                clip_group=1,
                title="A",
                segment_ids=["seg_1"],
                start=0.0,
                end=10.0,
                duration=10.0,
            )
        ]

        report = validate_highlight_reels_code(
            reels,
            self._segments(),
            video_duration=100.0,
            options=HighlightReelBuildOptions(
                target_reel_count=1,
                min_reel_duration=5,
                max_reel_duration=12,
            ),
        )

        self.assertFalse(any(issue.code == "HR-07" for issue in report.issues))
        self.assertFalse(any(issue.code == "HR-08" for issue in report.issues))

    def test_validate_highlight_reels_code_flags_duplicate_segment_refs(self):
        reels = [
            HighlightReel(
                reel_id="reel_1",
                clip_group=1,
                title="A",
                segment_ids=["seg_1", "seg_1"],
                start=0.0,
                end=10.0,
                duration=10.0,
            )
        ]
        report = validate_highlight_reels_code(reels, self._segments(), video_duration=100.0)
        self.assertTrue(any(issue.code == "HR-03" for issue in report.issues))

    def test_validate_highlight_reels_code_allows_overlap_between_reels(self):
        reels = [
            HighlightReel(
                reel_id="reel_1",
                clip_group=1,
                title="A",
                segment_ids=["seg_1", "seg_2"],
                start=0.0,
                end=25.0,
                duration=25.0,
            ),
            HighlightReel(
                reel_id="reel_2",
                clip_group=2,
                title="B",
                segment_ids=["seg_1", "seg_3"],
                start=0.0,
                end=70.0,
                duration=40.0,
            ),
        ]

        report = validate_highlight_reels_code(reels, self._segments(), video_duration=100.0)

        self.assertFalse(any(issue.code == "HR-11" for issue in report.issues))

    def test_split_ai_group_reuses_product_opening_when_it_fits(self):
        segments = [
            Segment(id="intro", start=0.0, end=8.0, text="这套批判性思维书适合8岁孩子", action=SegmentAction.KEEP, clip_group=1, clip_title="批判性思维书", style="cold_open", compiled_from_operation="op_intro"),
            Segment(id="point_1", start=8.0, end=40.0, text="卖点一", action=SegmentAction.KEEP, clip_group=1, style="highlight", compiled_from_operation="op_1"),
            Segment(id="point_2", start=40.0, end=72.0, text="卖点二", action=SegmentAction.KEEP, clip_group=1, style="highlight", compiled_from_operation="op_2"),
            Segment(id="point_3", start=72.0, end=104.0, text="卖点三", action=SegmentAction.KEEP, clip_group=1, style="highlight", compiled_from_operation="op_3"),
        ]

        reels = build_highlight_reel_plan(
            segments,
            options=HighlightReelBuildOptions(
                target_reel_count=2,
                min_reel_duration=20,
                max_reel_duration=80,
            ),
        )

        self.assertGreaterEqual(len(reels), 2)
        self.assertIn("intro", reels[0].segment_ids)
        self.assertIn("intro", reels[1].segment_ids)
        self.assertTrue(all(reel.duration <= 80 for reel in reels))

    def test_sync_plan_to_segments_does_not_overwrite_audit_metadata(self):
        reels = build_highlight_reel_plan(
            self._segments(),
            options=HighlightReelBuildOptions(min_reel_duration=1),
        )
        source = [
            Segment(id="seg_1", start=0.0, end=10.0, text="高能开场", action=SegmentAction.KEEP),
            Segment(id="seg_2", start=10.0, end=25.0, text="故事细节", action=SegmentAction.KEEP),
        ]
        synced = sync_plan_to_segments(source, reels)
        self.assertIsNone(synced[0].clip_group)
        self.assertIsNone(synced[0].clip_title)

    def test_apply_plan_issues_to_reels_updates_status(self):
        reels = build_highlight_reel_plan(
            self._segments(),
            options=HighlightReelBuildOptions(min_reel_duration=1),
        )
        report = validate_highlight_reels_code(reels, self._segments(), video_duration=100.0)
        updated = apply_plan_issues_to_reels(reels, report)
        self.assertTrue(all(reel.status.value in {"passed", "warning", "failed"} for reel in updated))

    def test_apply_plan_issues_hides_hard_semantic_failures(self):
        reels = build_highlight_reel_plan(
            self._segments(),
            options=HighlightReelBuildOptions(min_reel_duration=1),
        )
        report = HighlightReelQualityReport(
            passed=False,
            score=0.5,
            issues=[
                {
                    "code": "HR-M02",
                    "severity": "major",
                    "message": "结尾截断",
                    "reel_id": reels[0].reel_id,
                }
            ],
        )

        updated = apply_plan_issues_to_reels(reels, report)

        self.assertEqual(updated[0].status.value, "failed")
        self.assertTrue(updated[0].hidden)

    def test_apply_plan_issues_keeps_before_quality_failures_visible(self):
        reels = build_highlight_reel_plan(
            self._segments(),
            options=HighlightReelBuildOptions(min_reel_duration=1),
        )
        report = HighlightReelQualityReport(
            passed=False,
            score=0.5,
            issues=[{
                "code": "HR-M02",
                "severity": "major",
                "message": "结尾截断",
                "reel_id": reels[0].reel_id,
            }],
        )

        updated = apply_plan_issues_to_reels(reels, report, hide_hard_issues=False)

        self.assertEqual(updated[0].status.value, "failed")
        self.assertFalse(updated[0].hidden)

    def test_apply_plan_issues_does_not_warn_unaffected_reels_on_global_failure(self):
        reels = build_highlight_reel_plan(
            self._segments(),
            options=HighlightReelBuildOptions(min_reel_duration=1),
        )
        report = HighlightReelQualityReport(
            passed=False,
            score=0.4,
            issues=[
                {
                    "code": "HR-M02",
                    "severity": "major",
                    "message": "第一条结尾截断",
                    "reel_id": reels[0].reel_id,
                }
            ],
        )

        updated = apply_plan_issues_to_reels(
            reels,
            report,
            hide_hard_issues=False,
        )

        self.assertEqual(updated[0].status.value, "failed")
        self.assertEqual(updated[1].status.value, "passed")
        self.assertEqual(updated[1].score, 1.0)

    def test_repair_highlight_reels_splits_mixed_products(self):
        segments = [
            Segment(id="book_open", start=0, end=8, text="逻辑书开场", action=SegmentAction.KEEP, product_semantic=self._mark("逻辑书", ["product_opening"], "适用年龄")),
            Segment(id="book_point", start=8, end=24, text="逻辑书卖点", action=SegmentAction.KEEP, product_semantic=self._mark("逻辑书", ["selling_point"], "适用年龄")),
            Segment(id="cream_open", start=24, end=34, text="面霜开场", action=SegmentAction.KEEP, product_semantic=self._mark("面霜", ["product_opening"], "干皮适用")),
            Segment(id="cream_point", start=34, end=55, text="面霜卖点", action=SegmentAction.KEEP, product_semantic=self._mark("面霜", ["selling_point"], "干皮适用")),
        ]
        reels = [
            HighlightReel(
                reel_id="mixed",
                clip_group=1,
                title="混合产品",
                segment_ids=[segment.id for segment in segments],
                start=0,
                end=55,
                duration=55,
            )
        ]
        report = HighlightReelQualityReport(
            passed=False,
            issues=[{
                "code": "HR-19",
                "severity": "blocker",
                "message": "混入多个产品",
                "reel_id": "mixed",
            }],
        )

        repaired, actions = repair_highlight_reels(
            reels,
            segments,
            report,
            HighlightReelBuildOptions(min_reel_duration=1),
        )

        self.assertEqual([reel.product_name for reel in repaired], ["逻辑书", "面霜"])
        self.assertTrue(any(action.action == "split_product" for action in actions))

    def test_repair_highlight_reels_removes_cta_segments(self):
        segments = [
            Segment(id="open", start=0, end=8, text="产品开场", action=SegmentAction.KEEP, product_semantic=self._mark("逻辑书", ["product_opening"], "适用年龄")),
            Segment(id="point", start=8, end=30, text="核心卖点", action=SegmentAction.KEEP, product_semantic=self._mark("逻辑书", ["selling_point"], "适用年龄")),
            Segment(id="cta", start=30, end=36, text="点一号链接", action=SegmentAction.KEEP, product_semantic=self._mark("逻辑书", ["cta"], "链接", eligible=False)),
        ]
        reels = [
            HighlightReel(
                reel_id="reel_cta",
                clip_group=1,
                title="逻辑书",
                segment_ids=[segment.id for segment in segments],
                start=0,
                end=36,
                duration=36,
            )
        ]
        report = HighlightReelQualityReport(
            passed=False,
            issues=[{
                "code": "HR-10",
                "severity": "blocker",
                "message": "包含 CTA",
                "reel_id": "reel_cta",
            }],
        )

        repaired, actions = repair_highlight_reels(
            reels,
            segments,
            report,
            HighlightReelBuildOptions(min_reel_duration=1),
        )

        self.assertEqual(repaired[0].segment_ids, ["open", "point"])
        self.assertTrue(any(action.action == "remove_cta" for action in actions))

    def test_apply_plan_issues_keeps_repairable_title_or_opening_issues_visible(self):
        reels = build_highlight_reel_plan(
            self._segments(),
            options=HighlightReelBuildOptions(min_reel_duration=1),
        )
        reels[0].hidden = True
        report = HighlightReelQualityReport(
            passed=False,
            score=0.65,
            issues=[
                {
                    "code": "HR-M04",
                    "severity": "major",
                    "message": "开头产品名不清，可通过标题或人工调整修复",
                    "reel_id": reels[0].reel_id,
                }
            ],
        )

        updated = apply_plan_issues_to_reels(reels, report)

        self.assertEqual(updated[0].status.value, "warning")
        self.assertFalse(updated[0].hidden)

    def test_rebuild_reel_metrics_recalculates_duration(self):
        reel = HighlightReel(
            reel_id="reel_1",
            clip_group=1,
            title="测试",
            segment_ids=["seg_1", "seg_2"],
            start=0.0,
            end=0.0,
            duration=0.0,
        )
        rebuilt = rebuild_reel_metrics(reel, self._segments())
        self.assertEqual(rebuilt.duration, 25.0)
        self.assertEqual(rebuilt.end, 25.0)

    def test_rebuild_reel_metrics_includes_natural_gap_in_rendered_duration(self):
        segments = [
            Segment(id="seg_1", start=0.0, end=10.0, text="第一句", action=SegmentAction.KEEP),
            Segment(id="seg_2", start=12.0, end=20.0, text="第二句", action=SegmentAction.KEEP),
        ]
        reel = HighlightReel(
            reel_id="reel_1",
            clip_group=1,
            title="测试",
            segment_ids=["seg_1", "seg_2"],
            start=0.0,
            end=0.0,
            duration=0.0,
            source_duration=18.0,
        )

        rebuilt = rebuild_reel_metrics(reel, segments)

        self.assertEqual(rebuilt.duration, 20.0)
        self.assertEqual(rebuilt.retention_ratio, 1.0)

    def test_reels_to_clip_groups_preserves_order(self):
        reels = build_highlight_reel_plan(
            self._segments(),
            options=HighlightReelBuildOptions(min_reel_duration=1),
        )
        groups = reels_to_clip_groups(reels)
        self.assertEqual(groups, [["seg_1", "seg_2"], ["seg_3"]])

    def test_validate_highlight_reels_code_flags_missing_hook_style(self):
        segments = [
            Segment(id="seg_1", start=0.0, end=10.0, text="铺垫", action=SegmentAction.KEEP, clip_group=1, clip_title="测试", style="setup"),
            Segment(id="seg_2", start=10.0, end=25.0, text="收束", action=SegmentAction.KEEP, clip_group=1, style="resolution"),
        ]
        reels = build_highlight_reel_plan(segments, options=HighlightReelBuildOptions(min_reel_duration=1))
        report = validate_highlight_reels_code(reels, segments, video_duration=100.0)
        self.assertTrue(any(issue.code == "HR-16" for issue in report.issues))
        self.assertTrue(any(issue.code == "HR-17" for issue in report.issues))
        hook_issue = next(issue for issue in report.issues if issue.code == "HR-16")
        self.assertEqual(hook_issue.suggestion, "换首段：选择 product_opening / selling_point 标记的片段作为开场")

    def test_parse_validation_response_includes_suggestion(self):
        code_report = HighlightReelQualityReport(passed=True, score=1.0, issues=[])
        response = """
        {
          "passed": false,
          "score": 0.69,
          "validator_model": "test-model",
          "repair_actions": [
            {
              "action": "choose_opening",
              "reel_id": "reel_1",
              "segment_ids": ["seg_2"],
              "reason": "换首段"
            }
          ],
          "issues": [
            {
              "code": "HR-M01",
              "severity": "major",
              "message": "开头仍是过渡句",
              "reel_id": "reel_1",
              "suggestion": "换首段：将 punchline 段提前"
            }
          ]
        }
        """
        report = _parse_validation_response(response, code_report)
        self.assertFalse(report.passed)
        self.assertEqual(report.issues[-1].suggestion, "换首段：将 punchline 段提前")
        self.assertEqual(report.proposed_repair_actions[0].action, "choose_opening")
        self.assertEqual(report.repair_actions, [])

    def test_parse_validation_response_strictly_rejects_invalid_contract(self):
        code_report = HighlightReelQualityReport(passed=True, score=1.0, issues=[])
        response = """
        {
          "passed": true,
          "score": 0.95,
          "validator_model": "test-model",
          "issues": [],
          "repair_actions": [
            {
              "action": "hide",
              "reel_id": "reel_1",
              "segment_ids": [],
              "reason": "隐藏"
            }
          ]
        }
        """

        with self.assertRaises(ValueError):
            _parse_validation_response(
                response,
                code_report,
                strict=True,
                expected_reel_id="reel_1",
                allowed_segment_ids={"seg_1"},
            )

    def test_validation_payload_uses_display_text_for_corrected_segments(self):
        segments = [
            Segment(
                id="seg_1",
                start=0.0,
                end=10.0,
                text="剪影很好用",
                action=SegmentAction.TEXT_FIX,
                display_text="剪映很好用",
                clip_group=1,
                clip_title="工具纠错",
                product_semantic=self._mark("剪映", ["selling_point"], "工具效率"),
            )
        ]
        reels = build_highlight_reel_plan(segments, options=HighlightReelBuildOptions(min_reel_duration=1))

        payload = json.loads(_build_validation_payload(reels, segments))

        self.assertEqual(payload[0]["segments"][0]["text"], "剪映很好用")
        self.assertEqual(payload[0]["segments"][0]["raw_text"], "剪影很好用")
        self.assertEqual(payload[0]["segments"][0]["product_semantic"]["product_entities"][0]["name"], "剪映")

    def test_validation_payload_includes_product_group_scope_and_code_issues(self):
        segments = [
            Segment(
                id="seg_1",
                start=0.0,
                end=10.0,
                text="这套逻辑书训练推理",
                action=SegmentAction.KEEP,
                product_semantic=self._mark(
                    "逻辑书",
                    ["product_opening", "selling_point"],
                    "训练推理",
                ),
            )
        ]
        occurrence = ProductOccurrence(
            occurrence_id="product_logic",
            name="逻辑书",
            aliases=["这套书"],
            start=0.0,
            end=10.0,
            segment_ids=["seg_1"],
        )
        reel = HighlightReel(
            reel_id="reel_logic",
            clip_group=1,
            title="逻辑书：训练推理",
            segment_ids=["seg_1"],
            occurrence_id="product_logic",
            product_group_id="product_logic",
            source_occurrence_ids=["occ_1"],
        )
        code_report = HighlightReelQualityReport(
            passed=True,
            score=0.97,
            issues=[
                {
                    "code": "HR-29",
                    "severity": "minor",
                    "message": "产品位置检查",
                    "reel_id": "reel_logic",
                }
            ],
        )

        payload = json.loads(
            _build_validation_payload(
                [reel],
                segments,
                [occurrence],
                code_report,
            )
        )[0]

        self.assertEqual(payload["product_group_id"], "product_logic")
        self.assertEqual(payload["aliases"], ["这套书"])
        self.assertEqual(payload["source_occurrence_ids"], ["occ_1"])
        self.assertEqual(payload["allowed_segment_ids"], ["seg_1"])
        self.assertEqual(payload["code_issues"][0]["code"], "HR-29")


if __name__ == "__main__":
    unittest.main()
