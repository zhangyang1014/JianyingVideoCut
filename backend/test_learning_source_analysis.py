from pathlib import Path

import pytest

from backend.core.task_runner import RetryableExecutionError
from backend.models.task import (
    ASRResult,
    LearningEditedVariant,
    LearningTask,
    Segment,
    SegmentAction,
)
from backend.services import learning_analyzer
from backend.services.learning_analyzer import (
    build_decision_diffs,
    parse_learning_proposals,
    run_learning_analysis,
)


def _variant(text: str) -> LearningEditedVariant:
    return LearningEditedVariant(
        filename="精修.mp4",
        video_path="/tmp/refined.mp4",
        asr_result=ASRResult(segments=[Segment(start=0, end=2, text=text)]),
    )


def test_build_decision_diffs_confirms_ai_over_deletion():
    source = [
        Segment(
            id="keep-me",
            start=10,
            end=12,
            text="这句内容需要保留",
            action=SegmentAction.KEEP,
            claude_action=SegmentAction.DELETE,
            user_override=True,
        )
    ]

    diffs = build_decision_diffs(source, [_variant("这句内容需要保留")])

    assert len(diffs) == 1
    assert diffs[0].category == "ai_over_deleted"
    assert diffs[0].refined_action == "kept"
    assert diffs[0].selected is True


def test_build_decision_diffs_marks_omitted_human_keep_content():
    source = [
        Segment(
            id="segment-1",
            start=0,
            end=2,
            text="完全不同的原始内容",
            action=SegmentAction.KEEP,
            claude_action=SegmentAction.DELETE,
        )
    ]

    diffs = build_decision_diffs(source, [_variant("无关内容")])

    assert len(diffs) == 1
    assert diffs[0].category == "refined_omitted_human_keep"
    assert diffs[0].selected is True


def test_parse_learning_proposals_rejects_unknown_targets():
    proposals = parse_learning_proposals(
        '{"proposals":[{"target":"audit_prompt","title":"收紧规则","rationale":"有证据","change":{}},'
        '{"target":"run_shell","title":"危险","rationale":"无","change":{}}]}'
    )

    assert len(proposals) == 1
    assert proposals[0].target == "audit_prompt"


@pytest.mark.asyncio
async def test_learning_step1_checkpoint_resumes_with_only_step2(tmp_path, monkeypatch):
    task = LearningTask(
        name="两步恢复",
        original_asr_result=ASRResult(
            segments=[Segment(id="original", start=0, end=2, text="原始内容")],
            duration=2,
        ),
        edited_asr_result=ASRResult(
            segments=[Segment(id="edited", start=0, end=1, text="原始")],
            duration=1,
        ),
    )
    labels = []
    step2_should_fail = True

    async def fake_call(**kwargs):
        nonlocal step2_should_fail
        labels.append(kwargs["label"])
        if kwargs["label"] == "Learning Analyzer":
            return "第一步分析报告"
        if step2_should_fail:
            raise RetryableExecutionError("step2 temporary")
        return '{"changes":[]}'

    checkpoints = []
    monkeypatch.setattr(learning_analyzer, "LEARNING_CACHE_DIR", Path(tmp_path))
    monkeypatch.setattr(learning_analyzer, "_call_openai", fake_call)

    with pytest.raises(RetryableExecutionError):
        await run_learning_analysis(
            task,
            api_key="sk-test",
            model="test-model",
            checkpoint_callback=checkpoints.append,
        )

    assert task.analysis_result == "第一步分析报告"
    assert checkpoints == ["第一步分析报告"]
    assert labels == ["Learning Analyzer", "Prompt Rewriter"]

    step2_should_fail = False
    result = await run_learning_analysis(
        task,
        api_key="another-secret",
        model="test-model",
        checkpoint_callback=checkpoints.append,
    )

    assert result["analysis_result"] == "第一步分析报告"
    assert labels == ["Learning Analyzer", "Prompt Rewriter", "Prompt Rewriter"]
    assert checkpoints == ["第一步分析报告", "第一步分析报告"]
