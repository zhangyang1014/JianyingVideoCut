import json

import pytest

from backend.agent_runtime.contracts import validate_highlight_reels_contract
from backend.models.task import (
    HighlightReel,
    HighlightReelBuildOptions,
    HighlightReelQualityReport,
    HighlightReelStrategy,
    ProductEntity,
    ProductOccurrence,
    ProductSemanticMark,
    Segment,
    SegmentAction,
    Task,
    TaskStatus,
)
from backend.services.highlight_reel_plan import (
    build_highlight_reel_plan,
    repair_highlight_reels,
    validate_highlight_reels_code,
)
from backend.services.highlight_lineage import (
    HighlightLineageError,
    resolve_highlight_reel_segment_ids,
)
from backend.services.product_occurrence_detector import (
    group_product_occurrences,
    identify_product_occurrences,
)


def _mark(product: str, role: str, index: int) -> ProductSemanticMark:
    return ProductSemanticMark(
        roles=[role],
        product_entities=[ProductEntity(name=product, canonical_name=product)],
        selling_points=[f"{product}卖点{index}"] if role == "selling_point" else [],
        angle_label=f"{product}卖点{index}",
        confidence=0.9,
    )


def _occurrence_segments(product: str, prefix: str, start: float) -> list[Segment]:
    segments = []
    for index in range(4):
        role = "product_opening" if index == 0 else "selling_point"
        segments.append(
            Segment(
                id=f"{prefix}_{index}",
                start=start + index * 10,
                end=start + (index + 1) * 10,
                text=f"{product} {role} {index}",
                action=SegmentAction.KEEP,
                product_semantic=_mark(product, role, index),
            )
        )
    return segments


def _aba_segments() -> list[Segment]:
    return (
        _occurrence_segments("产品A", "a1", 0)
        + _occurrence_segments("产品B", "b", 50)
        + _occurrence_segments("产品A", "a2", 100)
    )


def _opening_rule_segments() -> list[Segment]:
    roles_and_text = [
        ("context", "先说一个常见使用场景"),
        ("selling_point", "它能解决这个场景里的核心痛点"),
        ("product_opening", "这款产品A就是今天要介绍的主角"),
        ("proof", "实际使用后的反馈也很稳定"),
    ]
    return [
        Segment(
            id=f"opening_{index}",
            start=index * 10,
            end=(index + 1) * 10,
            text=text,
            action=SegmentAction.KEEP,
            product_semantic=_mark("产品A", role, index),
        )
        for index, (role, text) in enumerate(roles_and_text)
    ]


def _product_group_reel(
    segment_ids: list[str],
) -> tuple[list[Segment], list, HighlightReel]:
    segments = _opening_rule_segments()
    groups = group_product_occurrences(identify_product_occurrences(segments), segments)
    assert len(groups) == 1
    group = groups[0]
    reel = HighlightReel(
        reel_id="opening-rule-reel",
        clip_group=1,
        title="产品A：核心痛点",
        segment_ids=segment_ids,
        product_group_id=group.product_group_id,
        product_name=group.canonical_name,
    )
    return segments, groups, reel


def test_a_b_a_is_three_stable_occurrences():
    segments = _aba_segments()
    first = identify_product_occurrences(segments)
    second = identify_product_occurrences(segments)

    assert [occurrence.name for occurrence in first] == ["产品A", "产品B", "产品A"]
    assert [occurrence.occurrence_id for occurrence in first] == [
        occurrence.occurrence_id for occurrence in second
    ]
    assert len(set(occurrence.occurrence_id for occurrence in first)) == 3


def test_a_b_a_groups_as_two_unique_products_without_cross_product_segments():
    segments = _aba_segments()
    occurrences = identify_product_occurrences(segments)
    groups = group_product_occurrences(occurrences, segments)

    assert len(groups) == 2
    product_a = next(group for group in groups if group.canonical_name == "产品A")
    assert len(product_a.occurrence_ids) == 2
    assert all(segment_id.startswith(("a1_", "a2_")) for segment_id in product_a.segment_ids)
    assert not any(segment_id.startswith("b_") for segment_id in product_a.segment_ids)


def test_potato_animation_name_variants_are_one_product_group():
    segments = (
        _occurrence_segments("土豆科普动画第四季", "potato1", 0)
        + _occurrence_segments("豆食APP", "app", 100)
        + _occurrence_segments("土豆动画片", "potato2", 220)
        + _occurrence_segments("瑶光彩妆", "makeup", 400)
        + _occurrence_segments("土豆科普动画片", "potato3", 600)
    )
    occurrences = identify_product_occurrences(segments)
    groups = group_product_occurrences(occurrences, segments)

    potato = next(group for group in groups if "土豆" in group.canonical_name)
    assert len(potato.occurrence_ids) == 3
    assert all(segment_id.startswith("potato") for segment_id in potato.segment_ids)
    assert not any(segment_id.startswith(("app_", "makeup_")) for segment_id in potato.segment_ids)


def test_nested_app_scope_is_absorbed_into_its_parent_product_group():
    segments = _occurrence_segments("土豆科普动画", "potato", 0)
    potato = ProductOccurrence(
        occurrence_id="occ-potato",
        name="土豆科普动画",
        aliases=["土豆", "儿童科普动画"],
        start=0,
        end=40,
        segment_ids=["potato_0", "potato_1", "potato_2", "potato_3"],
    )
    app = ProductOccurrence(
        occurrence_id="occ-app",
        name="豆食APP",
        aliases=["豆食APP", "动画观看平台"],
        start=10,
        end=30,
        segment_ids=["potato_1", "potato_2"],
    )

    groups = group_product_occurrences([potato, app], segments)

    assert len(groups) == 1
    assert groups[0].canonical_name == "土豆科普动画"
    assert groups[0].occurrence_ids == ["occ-potato", "occ-app"]
    assert "豆食APP" in groups[0].aliases


def test_models_and_same_brand_different_categories_do_not_merge():
    segments = (
        _occurrence_segments("手机X1", "x1", 0)
        + _occurrence_segments("手机X2", "x2", 100)
        + _occurrence_segments("瑶光粉底液", "foundation", 200)
        + _occurrence_segments("瑶光口红", "lipstick", 300)
    )
    groups = group_product_occurrences(
        identify_product_occurrences(segments),
        segments,
    )

    assert len(groups) == 4
    assert {group.canonical_name for group in groups} == {
        "手机X1",
        "手机X2",
        "瑶光粉底液",
        "瑶光口红",
    }


def test_generic_category_aliases_do_not_bridge_unrelated_products():
    segments = (
        _occurrence_segments("少年经济学院", "economy", 0)
        + _occurrence_segments("像经济学家一样思考", "thinking", 100)
        + _occurrence_segments("漫画世界史", "world", 200)
        + _occurrence_segments("历史是一群喵", "cats", 300)
    )
    occurrences = identify_product_occurrences(segments)
    occurrences[0].aliases.append("儿童经济学读物")
    occurrences[1].aliases.append("儿童经济学读物")
    occurrences[2].aliases.extend(["儿童历史读物", "图书"])
    occurrences[3].aliases.extend(["儿童历史读物", "图书"])

    groups = group_product_occurrences(occurrences, segments)

    assert len(groups) == 4
    assert {group.canonical_name for group in groups} == {
        "少年经济学院",
        "像经济学家一样思考",
        "漫画世界史",
        "历史是一群喵",
    }


def test_confirmed_asr_name_variants_still_group_without_category_bridge():
    segments = (
        _occurrence_segments("无有用", "wuyouyong1", 0)
        + _occurrence_segments("吴有用上学记", "wuyouyong2", 100)
        + _occurrence_segments("米无", "miwu1", 200)
        + _occurrence_segments("米吴科普童书", "miwu2", 300)
    )

    groups = group_product_occurrences(
        identify_product_occurrences(segments),
        segments,
    )

    assert len(groups) == 2
    assert sorted(len(group.occurrence_ids) for group in groups) == [2, 2]


def test_compound_categories_and_generic_names_do_not_merge_distinct_products():
    segments = (
        _occurrence_segments("青春期故事书（这套书）", "teen", 0)
        + _occurrence_segments("其他青春期教育读物（对比用）", "comparison", 200)
        + _occurrence_segments("菲欧防晒霜", "branded", 400)
        + _occurrence_segments("防晒霜", "generic", 600)
        + _occurrence_segments("DK神话系列（埃及/北欧）", "myth", 800)
        + _occurrence_segments("DK科普精装书", "science", 1000)
    )
    occurrences = identify_product_occurrences(segments)
    occurrences[0].aliases.append("童书/青春期教育读物")
    occurrences[1].aliases.append("童书/青春期教育读物")
    occurrences[2].aliases.append("防晒霜")
    occurrences[3].aliases.append("防晒")
    occurrences[4].aliases.append("童书/科普书")
    occurrences[5].aliases.append("童书/科普书")

    groups = group_product_occurrences(occurrences, segments)

    assert len(groups) == 6


def test_confirmed_cross_return_titles_use_explicit_identity_mapping():
    segments = (
        _occurrence_segments("中国五大科学博物馆绘本", "museum1", 0)
        + _occurrence_segments("博物馆系列书", "museum2", 100)
        + _occurrence_segments("中外文明对照机关书", "history1", 200)
        + _occurrence_segments("中外历史对比书", "history2", 300)
    )

    groups = group_product_occurrences(
        identify_product_occurrences(segments),
        segments,
    )

    assert len(groups) == 2
    assert sorted(len(group.occurrence_ids) for group in groups) == [2, 2]


def test_six_potato_returns_produce_three_candidates_and_one_final_winner():
    product_names = [
        "土豆科普动画第四季",
        "土豆动画片",
        "土豆科普动画",
        "土豆科普动画片",
        "土豆动画片",
        "土豆科普动画第四季",
    ]
    segments = []
    for index, product_name in enumerate(product_names):
        segments.extend(_occurrence_segments(product_name, f"potato{index}", index * 200))
        if index == 1:
            segments.extend(_occurrence_segments("豆食APP", "app", index * 200 + 80))
        if index == 3:
            segments.extend(_occurrence_segments("瑶光彩妆", "makeup", index * 200 + 80))

    occurrences = identify_product_occurrences(segments)
    groups = group_product_occurrences(occurrences, segments)
    potato = next(group for group in groups if "土豆" in group.canonical_name)
    candidates = build_highlight_reel_plan(segments, product_groups=[potato])

    from backend.services.highlight_reel_pipeline import select_best_product_reels

    winners = select_best_product_reels(candidates)
    assert len(potato.occurrence_ids) == 6
    assert len(candidates) == 3
    assert len(winners) == 1
    assert all(segment_id.startswith("potato") for segment_id in potato.segment_ids)
    assert not any(segment_id.startswith(("app_", "makeup_")) for segment_id in potato.segment_ids)


def test_each_occurrence_has_exactly_three_bounded_strategies():
    segments = _aba_segments()
    occurrences = identify_product_occurrences(segments)
    reels = build_highlight_reel_plan(segments, product_occurrences=occurrences)

    assert len(reels) == 9
    for occurrence in occurrences:
        occurrence_reels = [reel for reel in reels if reel.occurrence_id == occurrence.occurrence_id]
        assert {reel.strategy_kind for reel in occurrence_reels} == set(HighlightReelStrategy)
        assert len(occurrence_reels) == 3
        for reel in occurrence_reels:
            assert set(reel.segment_ids) <= set(occurrence.segment_ids)
            assert reel.hidden is False


def test_product_groups_build_three_candidates_but_select_one_balanced_winner():
    from backend.services.highlight_reel_pipeline import select_best_product_reels

    segments = _aba_segments()
    occurrences = identify_product_occurrences(segments)
    groups = group_product_occurrences(occurrences, segments)
    candidates = build_highlight_reel_plan(
        segments,
        product_groups=groups,
    )
    winners = select_best_product_reels(candidates)

    assert len(candidates) == len(groups) * 3
    assert len(winners) == len(groups)
    assert all(winner.strategy_kind == HighlightReelStrategy.BALANCED for winner in winners)
    assert len({winner.product_group_id for winner in winners}) == len(groups)
    contract = validate_highlight_reels_contract(
        winners,
        segments,
        product_groups=groups,
    )
    assert contract.contract_id == "goldenclip.highlight_reels@3"
    assert contract.passed is True


def test_balanced_ratio_and_concise_limit_and_anchors():
    segments = _aba_segments()
    occurrences = identify_product_occurrences(segments)
    reels = build_highlight_reel_plan(segments, product_occurrences=occurrences)
    report = validate_highlight_reels_code(
        reels,
        segments,
        product_occurrences=occurrences,
    )

    balanced = [reel for reel in reels if reel.strategy_kind == HighlightReelStrategy.BALANCED]
    concise = [reel for reel in reels if reel.strategy_kind == HighlightReelStrategy.CONCISE]
    assert all(0.4 <= (reel.retention_ratio or 0) <= 0.5 for reel in balanced)
    assert all(0.4 <= (reel.content_retention_ratio or 0) <= 0.5 for reel in balanced)
    assert all(reel.duration <= 120 for reel in concise)
    assert all(reel.selected_selling_points for reel in concise)
    assert not any(issue.code in {"HR-23", "HR-25", "HR-26", "HR-27"} for issue in report.issues)


def test_balanced_unreachable_atomic_ratio_is_minor_and_non_blocking():
    segments = [
        Segment(
            id="atomic_open",
            start=0,
            end=10,
            text="产品A是一套训练推理的逻辑书",
            action=SegmentAction.KEEP,
            product_semantic=_mark("产品A", "product_opening", 0),
        ),
        Segment(
            id="atomic_point",
            start=10,
            end=20,
            text="它用故事讲清楚推理方法",
            action=SegmentAction.KEEP,
            product_semantic=_mark("产品A", "selling_point", 1),
        ),
        Segment(
            id="atomic_proof",
            start=20,
            end=30,
            text="孩子实际读起来也能理解",
            action=SegmentAction.KEEP,
            product_semantic=_mark("产品A", "proof", 2),
        ),
    ]
    occurrences = identify_product_occurrences(segments)
    reels = build_highlight_reel_plan(segments, product_occurrences=occurrences)

    report = validate_highlight_reels_code(
        reels,
        segments,
        product_occurrences=occurrences,
    )

    balanced_issue = next(issue for issue in report.issues if issue.code == "HR-25")
    assert balanced_issue.severity == "minor"
    assert "原子片段粒度" in balanced_issue.message
    assert report.passed is True


def test_concise_limit_uses_rendered_duration_including_natural_gap():
    segments = [
        Segment(
            id="gap_open",
            start=0,
            end=10,
            text="产品A是一套逻辑书",
            action=SegmentAction.KEEP,
            product_semantic=_mark("产品A", "product_opening", 0),
        ),
        Segment(
            id="gap_point",
            start=120,
            end=130,
            text="它的核心卖点是训练推理",
            action=SegmentAction.KEEP,
            product_semantic=_mark("产品A", "selling_point", 1),
        ),
    ]
    occurrence = ProductOccurrence(
        occurrence_id="occ-gap",
        name="产品A",
        start=0,
        end=130,
        segment_ids=["gap_open", "gap_point"],
    )
    reels = build_highlight_reel_plan(
        segments,
        product_occurrences=[occurrence],
    )

    report = validate_highlight_reels_code(
        reels,
        segments,
        product_occurrences=[occurrence],
    )

    concise = next(
        reel for reel in reels if reel.strategy_kind == HighlightReelStrategy.CONCISE
    )
    assert concise.duration == 130
    assert any(
        issue.code == "HR-26" and issue.reel_id == concise.reel_id
        for issue in report.issues
    )


def test_opening_anchor_warning_is_non_blocking_when_first_two_sentences_miss_product():
    segments, groups, reel = _product_group_reel(
        ["opening_0", "opening_1", "opening_2", "opening_3"],
    )

    report = validate_highlight_reels_code(
        [reel],
        segments,
        product_groups=groups,
    )

    opening_issues = [issue for issue in report.issues if issue.code == "HR-29"]
    assert len(opening_issues) == 1
    assert opening_issues[0].severity == "minor"
    assert report.passed is True


def test_moving_opening_anchor_to_first_sentence_is_rejected_as_export_order_mismatch():
    segments, groups, reel = _product_group_reel(
        ["opening_2", "opening_0", "opening_1", "opening_3"],
    )

    report = validate_highlight_reels_code(
        [reel],
        segments,
        product_groups=groups,
    )

    assert any(issue.code == "HR-06" for issue in report.issues)
    assert not any(issue.code == "HR-29" for issue in report.issues)


def test_opening_anchor_in_second_sentence_still_requires_source_order():
    segments, groups, reel = _product_group_reel(
        ["opening_0", "opening_2", "opening_1", "opening_3"],
    )

    report = validate_highlight_reels_code(
        [reel],
        segments,
        product_groups=groups,
    )

    assert any(issue.code == "HR-06" for issue in report.issues)
    assert not any(issue.code == "HR-29" for issue in report.issues)


def test_old_task_json_loads_without_new_fields():
    task = Task.model_validate({"id": "legacy", "name": "旧任务"})

    assert task.product_occurrences == []
    assert task.product_groups == []
    assert task.highlight_strategy_quality_results == []
    assert task.highlight_reels == []


def test_deterministic_repair_restores_triplet_and_occurrence_boundary():
    segments = _aba_segments()
    occurrences = identify_product_occurrences(segments)
    reels = build_highlight_reel_plan(segments, product_occurrences=occurrences)
    first_occurrence = occurrences[0]
    broken = [
        reel.model_copy(deep=True)
        for reel in reels
        if not (
            reel.occurrence_id == first_occurrence.occurrence_id
            and reel.strategy_kind == HighlightReelStrategy.CONCISE
        )
    ]
    first_balanced = next(
        reel
        for reel in broken
        if reel.occurrence_id == first_occurrence.occurrence_id
        and reel.strategy_kind == HighlightReelStrategy.BALANCED
    )
    first_balanced.segment_ids.append(occurrences[1].segment_ids[0])
    report = validate_highlight_reels_code(
        broken,
        segments,
        product_occurrences=occurrences,
    )

    repaired, actions = repair_highlight_reels(
        broken,
        segments,
        report,
        product_occurrences=occurrences,
    )
    repaired_report = validate_highlight_reels_code(
        repaired,
        segments,
        product_occurrences=occurrences,
    )

    assert actions
    assert len(repaired) == 9
    assert not any(issue.code in {"HR-21", "HR-23", "HR-25", "HR-26"} for issue in repaired_report.issues)


@pytest.mark.asyncio
async def test_pipeline_repairs_missing_strategy_and_persists_quality(monkeypatch):
    from backend.services import highlight_reel_pipeline as pipeline

    task = Task(name="三版集锦", audit_segments=_aba_segments())

    class Store:
        def __init__(self):
            self.task = task

        def get(self, task_id):
            return self.task if task_id == task.id else None

        def update(self, updated, **kwargs):
            self.task = updated
            return updated

    real_build = build_highlight_reel_plan

    async def broken_generate(segments, occurrences, *args, **kwargs):
        built = real_build(segments, product_occurrences=occurrences)
        return built[:-1]

    async def accept_contract(*args, **kwargs):
        return None

    store = Store()
    monkeypatch.setattr(pipeline, "get_store", lambda: store)
    monkeypatch.setattr(pipeline, "generate_product_strategy_reels", broken_generate)
    monkeypatch.setattr(pipeline, "verify_contract_report", accept_contract)

    reels, _ = await pipeline.refresh_highlight_plan(
        task.id,
        run_model_validation=False,
    )

    saved_task = store.get(task.id)
    assert len(reels) == 2
    assert len(saved_task.product_occurrences) == 3
    assert len(saved_task.product_groups) == 2
    assert len(saved_task.highlight_strategy_quality_results) == 6
    assert len({reel.product_group_id or reel.occurrence_id for reel in reels}) == 2
    assert all(not reel.hidden for reel in reels)


@pytest.mark.asyncio
async def test_target_product_count_limits_final_winners_not_occurrences(monkeypatch):
    from backend.services import highlight_reel_pipeline as pipeline

    task = Task(name="唯一产品数量", audit_segments=_aba_segments())

    class Store:
        def __init__(self):
            self.task = task

        def get(self, task_id):
            return self.task if task_id == task.id else None

        def update(self, updated, **kwargs):
            self.task = updated
            return updated

    store = Store()
    monkeypatch.setattr(pipeline, "get_store", lambda: store)

    reels, _ = await pipeline.refresh_highlight_plan(
        task.id,
        options=HighlightReelBuildOptions(target_product_count=1, target_reel_count=1),
        run_model_validation=False,
    )

    assert len(reels) == 1
    assert len(store.task.highlight_reels) == 1
    assert len(store.task.product_occurrences) == 3
    assert len(store.task.product_groups) == 2
    assert len(store.task.highlight_reels_before_quality) == 3


@pytest.mark.asyncio
async def test_manual_highlight_prompt_route_builds_cursor_instruction(monkeypatch):
    from backend.routers import highlight_reels as router

    segments = _occurrence_segments("产品A", "manual", 0)
    task = Task(
        name="Cursor 集锦指令",
        audit_segments=segments,
        product_occurrences=identify_product_occurrences(segments),
    )

    class Store:
        def __init__(self):
            self.task = task

        def get(self, task_id):
            return self.task if task_id == task.id else None

        def update(self, updated, **kwargs):
            self.task = updated
            return updated

    store = Store()
    monkeypatch.setattr(router, "get_store", lambda: store)

    response = await router.get_manual_highlight_prompt(task.id)

    assert response["jobs_count"] == 3
    assert response["product_groups_count"] == 1
    assert len(store.task.product_groups) == 1
    assert store.task.product_groups[0].product_group_id in response["combined"]
    assert store.task.product_occurrences[0].occurrence_id in response["combined"]
    assert "GoldenClip 产品集锦分析器" in response["system_prompt"]
    assert "不得按返场、版本、主题、卖点或 APP 再拆分产品" in response["system_prompt"]
    assert "STRATEGY faithful" in response["user_prompt"]
    assert "STRATEGY balanced" in response["user_prompt"]
    assert "STRATEGY concise" in response["user_prompt"]
    assert segments[0].id in response["combined"]


@pytest.mark.asyncio
async def test_manual_highlight_prompt_groups_name_variants_before_counting_jobs(
    monkeypatch,
):
    from backend.routers import highlight_reels as router

    segments = (
        _occurrence_segments("土豆科普动画第四季", "potato1", 0)
        + _occurrence_segments("豆食APP", "app", 100)
        + _occurrence_segments("土豆动画片", "potato2", 220)
    )
    task = Task(
        name="旧任务产品归组",
        audit_segments=segments,
        product_occurrences=identify_product_occurrences(segments),
    )

    class Store:
        def __init__(self):
            self.task = task

        def get(self, task_id):
            return self.task if task_id == task.id else None

        def update(self, updated, **kwargs):
            self.task = updated
            return updated

    store = Store()
    monkeypatch.setattr(router, "get_store", lambda: store)

    response = await router.get_manual_highlight_prompt(task.id)

    # 三个返场实体归并为两个全局产品组，因此是 2 * 3，而不是 3 * 3 个 JOB。
    assert response["product_groups_count"] == 2
    assert response["jobs_count"] == 6
    potato_group = next(
        group for group in store.task.product_groups if "土豆" in group.canonical_name
    )
    assert len(potato_group.occurrence_ids) == 2
    assert f"{potato_group.product_group_id}:faithful" in response["combined"]
    assert '"source_occurrence_ids"' in response["combined"]


def test_manual_cursor_results_upgrade_occurrences_to_product_group_document():
    from backend.services.manual_highlight_import import (
        apply_compiled_manual_highlight_result,
        compile_manual_highlight_result,
    )

    segments = _aba_segments()
    occurrences = identify_product_occurrences(segments)
    task = Task(
        name="Cursor 结果升级",
        audit_segments=segments,
        product_occurrences=occurrences,
        revision=7,
        content_revision=2,
        highlight_revision=3,
    )
    results = []
    for occurrence in occurrences:
        for strategy in HighlightReelStrategy:
            results.append(
                {
                    "job_id": f"{occurrence.occurrence_id}:{strategy.value}",
                    "occurrence_id": occurrence.occurrence_id,
                    "strategy": strategy.value,
                    "segment_ids": occurrence.segment_ids,
                    "title": occurrence.name,
                    "rationale": "Cursor 选择",
                }
            )

    compiled = compile_manual_highlight_result(
        task,
        json.dumps({"results": results}, ensure_ascii=False),
    )
    updated = apply_compiled_manual_highlight_result(task, compiled)

    assert len(compiled.product_groups) == 2
    assert len(compiled.before_quality_reels) == 6
    assert len(compiled.winners) == 2
    assert updated.highlight_revision == 4
    assert len(updated.highlight_reels_before_quality) == 6
    assert len(updated.highlight_reels) == 2
    assert all(reel.product_group_id for reel in updated.highlight_reels)
    assert all(
        reel.source_content_revision == 2
        for reel in updated.highlight_reels
    )


@pytest.mark.asyncio
async def test_manual_cursor_result_apply_rejects_stale_revision(monkeypatch):
    from backend.routers import highlight_reels as router

    task = Task(name="并发回填", revision=4)

    class Store:
        def get(self, task_id):
            return task if task_id == task.id else None

    monkeypatch.setattr(router, "get_store", lambda: Store())
    request = router.ManualHighlightResultRequest(
        result_json='{"results":[]}',
        expected_revision=3,
    )

    with pytest.raises(Exception) as exc_info:
        await router.apply_manual_highlight_result(task.id, request)

    assert getattr(exc_info.value, "status_code", None) == 409


@pytest.mark.asyncio
async def test_rebuild_route_streams_pipeline_logs_and_ready_event(monkeypatch):
    from backend.routers import highlight_reels as router

    task = Task(name="实时日志任务")
    report = HighlightReelQualityReport(passed=True)
    streamed_logs = []
    broadcasts = []
    started = {}

    class Store:
        def get(self, task_id):
            return task if task_id == task.id else None

        def update(self, updated_task):
            return updated_task

    async def fake_log_to_ws(task_id, level, source, message, progress=None, **extra):
        streamed_logs.append((task_id, level, source, message, progress, extra))

    async def fake_refresh(task_id, *, options=None, log_callback=None, **kwargs):
        assert task_id == task.id
        assert options is not None
        assert log_callback is not None
        await log_callback(
            "info",
            "highlight",
            "正在生成产品集锦计划...",
            0.88,
            stage="reel_plan",
        )
        return [], report

    async def fake_broadcast(task_id, message):
        broadcasts.append((task_id, message))

    def fake_start_task_runner(task_id, coro, running_status, **kwargs):
        started.update({
            "task_id": task_id,
            "coro": coro,
            "running_status": running_status,
            "operation": kwargs.get("operation"),
        })

    monkeypatch.setattr(router, "get_store", lambda: Store())
    monkeypatch.setattr(router, "log_to_ws", fake_log_to_ws)
    monkeypatch.setattr(router, "_refresh_highlight_plan", fake_refresh)
    monkeypatch.setattr(router, "_start_task_runner", fake_start_task_runner)
    monkeypatch.setattr(router.manager, "broadcast", fake_broadcast)

    response = await router.rebuild_highlight_reels(
        task.id,
        HighlightReelBuildOptions(),
    )
    assert response["success"] is True
    assert task.status == TaskStatus.AUDIT_RUNNING
    assert started["task_id"] == task.id
    assert started["running_status"] == TaskStatus.AUDIT_RUNNING
    assert started["operation"] == "highlight_rebuild"

    await started["coro"]

    assert streamed_logs == [
        (
            task.id,
            "info",
            "highlight",
            "正在生成产品集锦计划...",
            0.88,
            {"stage": "reel_plan"},
        )
    ]
    assert [message["type"] for _, message in broadcasts] == [
        "status_change",
        "highlight_reels_ready",
        "status_change",
    ]
    assert broadcasts[1][1]["canonical_reels_count"] == 0
    assert task.status == TaskStatus.REVIEW


@pytest.mark.asyncio
async def test_highlight_preview_resolves_reel_and_quality_stage(monkeypatch):
    from backend.routers import export as export_router

    reel = HighlightReel(
        reel_id="canonical-reel",
        clip_group=1,
        title="产品A · 平衡版",
        segment_ids=["a1_0", "a1_1"],
        occurrence_id="occ-a",
        strategy_kind=HighlightReelStrategy.BALANCED,
    )
    task = Task(name="预览任务", highlight_reels=[reel], audit_segments=[
        Segment(id="a1_0", text="第一句", start=0, end=2),
        Segment(id="a1_1", text="第二句", start=2, end=4),
    ])

    class Store:
        def get(self, task_id):
            return task if task_id == task.id else None

    async def fake_preview(snapshot, ids):
        assert snapshot.id == task.id and ids == ["a1_0", "a1_1"]
        return {"url": "/api/exports/canonical.mp4"}

    monkeypatch.setattr(export_router, "get_store", lambda: Store())
    monkeypatch.setattr(export_router, "_render_clip_preview", fake_preview)

    payload = await export_router.export_highlight_preview(
        task.id,
        {"reel_id": reel.reel_id, "quality_stage": "after_quality"},
    )

    assert payload["reel_id"] == reel.reel_id
    assert payload["quality_stage"] == "after_quality"
    assert payload["strategy_kind"] == "balanced"


def test_highlight_lineage_rejects_cross_product_segment_without_time_fallback():
    segments = _aba_segments()
    occurrences = identify_product_occurrences(segments)
    groups = group_product_occurrences(occurrences, segments)
    group = groups[0]
    foreign_segment_id = next(
        segment_id
        for candidate in groups[1:]
        for segment_id in candidate.segment_ids
    )
    reel = HighlightReel(
        reel_id="cross-product",
        clip_group=1,
        title="错误混入",
        product_group_id=group.product_group_id,
        segment_ids=[group.segment_ids[0], foreign_segment_id],
    )

    with pytest.raises(HighlightLineageError, match="产品范围外"):
        resolve_highlight_reel_segment_ids(
            reel,
            segments,
            product_groups=groups,
            product_occurrences=occurrences,
        )
