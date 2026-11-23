import pytest

from backend.agent_runtime.contracts import validate_audit_edit_plan_contract, validate_audit_segments_contract
from backend.models.task import AuditEditOperation, AuditEditPlan, Segment, SourceWordRange, WordTimestamp
from backend.services.audit_edit_compiler import compile_audit_edit_plan
from backend.services.semantic_auditor import apply_segment_corrections, parse_claude_response
from backend.services.ffmpeg_executor import get_expected_export_duration


def source():
    text = "我嗯我们开始"
    return Segment(id="source", text=text, start=0, end=len(text),
                   words=[WordTimestamp(word=char, start=i, end=i+1) for i, char in enumerate(text)])


def plan(start=1, end=3):
    return AuditEditPlan(operations=[AuditEditOperation(op="delete_words",
        source_word_ranges=[SourceWordRange(segment_id="source", start_word_index=start, end_word_index=end)],
        reason="去掉语气词和第一次重说", rule="Rule 4: 词内去重")])


def test_word_cut_keeps_content_and_matches_export_timeline():
    original = source()
    snapshot = original.model_dump()
    compiled = compile_audit_edit_plan([original], plan())
    assert validate_audit_edit_plan_contract([original], plan(), compiled.segments).passed
    assert validate_audit_segments_contract([original], compiled.segments).passed
    result = apply_segment_corrections(compiled.segments)
    assert [(s.text, s.action.value, s.start, s.end) for s in result] == [
        ("我", "keep", 0, 1), ("嗯我", "delete", 1, 3), ("们开始", "keep", 3, 6)]
    assert get_expected_export_duration(result) == 4
    assert "".join(s.text for s in result if s.action != "delete") == "我们开始"
    assert original.model_dump() == snapshot
    assert apply_segment_corrections(result) == result
    assert result[1].source_word_ranges[0].start_word_index == 1


@pytest.mark.parametrize("start,end", [(-1, 2), (2, 2), (3, 1), (0, 99), (None, 2)])
def test_invalid_ranges_are_rejected(start, end):
    original, edit_plan = source(), plan(start, end)
    compiled = compile_audit_edit_plan([original], edit_plan)
    assert validate_audit_edit_plan_contract([original], edit_plan, compiled.segments).rejected


@pytest.mark.parametrize("mutate", [
    lambda s: setattr(s, "words", []),
    lambda s: setattr(s.words[2], "start", 0),
    lambda s: setattr(s, "text", "改过的台词"),
    lambda s: setattr(s, "subtitle_text", "人工写的字幕"),
])
def test_unreliable_word_sources_are_not_cut(mutate):
    original = source()
    mutate(original)
    with pytest.raises(ValueError, match="契约"):
        parse_claude_response(plan().model_dump_json(), [original], strict=True)


def test_conflicting_whole_segment_edit_cannot_override_a_word_cut():
    original, edit_plan = source(), plan()
    edit_plan.operations.append(AuditEditOperation(op="delete_span", source_ids=[original.id], reason="删除"))
    assert validate_audit_edit_plan_contract([original], edit_plan,
        compile_audit_edit_plan([original], edit_plan).segments).rejected


def test_overlapping_ranges_are_unioned_and_neighbouring_speech_is_retained():
    original, edit_plan = source(), plan(1, 2)
    edit_plan.operations[0].source_word_ranges.append(SourceWordRange(segment_id="source", start_word_index=1, end_word_index=3))
    compiled = parse_claude_response(edit_plan.model_dump_json(), [original], strict=True)
    result = apply_segment_corrections(compiled)
    assert get_expected_export_duration(result) == 4
    assert len({s.id for s in result}) == 3


def test_word_edit_instruction_provides_indices_only_for_candidate_segments():
    from backend.models.task import CandidateEdit
    from backend.services.semantic_auditor import build_audit_prompt
    original = source()
    original.candidate_edits = [CandidateEdit(source="filler", start=1, end=2, text="嗯")]
    prompt, _ = build_audit_prompt([original], task_type="monologue_clean")
    assert '"w": [[0, "我"], [1, "嗯"]' in prompt
    assert "delete_words" in prompt
    interview, _ = build_audit_prompt([original], task_type="interview_compress")
    assert '"w": [[' not in interview


@pytest.mark.asyncio
async def test_sentence_guard_and_local_coherence_preserve_executable_word_cut():
    from backend.services.semantic_auditor import apply_sentence_group_guard
    from backend.services.audit_coherence import run_audit_coherence_review
    original = source()
    following = Segment(id="next", start=6.1, end=7, text="继续讲解")
    edit_plan = plan()
    edit_plan.operations.append(AuditEditOperation(op="keep_span", source_ids=[following.id]))
    compiled = parse_claude_response(edit_plan.model_dump_json(), [original, following], strict=True)
    guarded = apply_sentence_group_guard(compiled, task_type="monologue_clean")
    assert guarded[0].action == "keep"
    result = apply_segment_corrections(guarded)
    reviewed, _ = await run_audit_coherence_review(result, task_type="monologue_clean", use_model=False)
    assert "".join(s.text for s in reviewed if s.action != "delete") == "我们开始继续讲解"


def test_changed_word_timestamps_invalidate_audit_cache():
    from backend.services.semantic_auditor import _segment_audit_hash
    original = source()
    before = _segment_audit_hash(original)
    original.words[1].end = 1.8
    assert _segment_audit_hash(original) != before


def test_large_word_deletion_still_triggers_human_review_threshold():
    original, edit_plan = source(), plan(0, 5)
    compiled = compile_audit_edit_plan([original], edit_plan).segments
    report = validate_audit_segments_contract([original], compiled)
    assert report.flagged_for_human
    assert report.metrics["delete_duration_ratio"] == round(5 / 6, 4)
