from backend.models.task import Segment, SegmentAction
from backend.services import prompt_optimizer
from backend.services.prompt_optimizer import collect_corrections, extract_feedback_structure


def test_collect_corrections_ignores_audit_generated_action_changes():
    corrections = collect_corrections([
        Segment(
            id="auto-text-fix",
            start=0,
            end=1,
            text="审计自动纠错",
            action=SegmentAction.TEXT_FIX,
            claude_action=SegmentAction.KEEP,
            reason="AC-M01 自动纠错",
            user_override=False,
        ),
        Segment(
            id="manual-restore",
            start=1,
            end=2,
            text="用户主动恢复",
            action=SegmentAction.KEEP,
            claude_action=SegmentAction.DELETE,
            reason="重要知识点，值得保留",
            user_override=True,
        ),
    ])

    assert corrections["total_modified"] == 1
    assert [c["id"] for c in corrections["delete_to_keep"]] == ["manual-restore"]
    assert corrections["keep_to_delete"] == []
    assert corrections["other_changes"] == []


def test_extract_feedback_structure_reads_update_points_and_prompt_changes():
    response_text = """
### 第零部分：本次更新点
- 修改 H2

```json
{
  "updates": [
    {
      "target_rule": "H2",
      "change_type": "modify",
      "summary": "短片段删除增加悬念豁免"
    }
  ],
  "prompt_changes": {
    "changes": [
      {
        "type": "modify",
        "rule_id": "H2",
        "summary": "短片段删除增加悬念豁免",
        "full_rule_text": "### 【H2】残忍删除原则 ★★★\\n保留有悬念或情绪价值的极短片段。"
      }
    ]
  },
  "style_update": {
    "内容保留倾向": "保留悬念短语和真实互动反馈。"
  }
}
```
"""

    structured = extract_feedback_structure(response_text)

    assert structured["update_points"] == [
        {
            "summary": "短片段删除增加悬念豁免",
            "target_rule": "H2",
            "change_type": "modify",
        }
    ]
    assert structured["prompt_changes"]["changes"][0]["rule_id"] == "H2"
    assert structured["prompt_changes"]["changes"][0]["type"] == "modify"
    assert structured["style_update"] == {"内容保留倾向": "保留悬念短语和真实互动反馈。"}


def test_apply_feedback_prompt_changes_replaces_rule_and_activates_version(monkeypatch):
    original_prompt = """# 精彩集锦

### 【H1】高能时刻识别 ★★★
旧 H1。

### 【H2】残忍删除原则 ★★★
旧 H2。

### 【H3】节奏规则 ★★★
旧 H3。
"""
    saved = {}

    monkeypatch.setattr(prompt_optimizer, "load_system_prompt", lambda task_type: original_prompt)

    def fake_save_versioned_prompt(task_type, content):
        saved["task_type"] = task_type
        saved["content"] = content
        return "/tmp/highlight_reel_v9.md", 9

    monkeypatch.setattr(prompt_optimizer, "save_versioned_prompt", fake_save_versioned_prompt)
    monkeypatch.setattr(prompt_optimizer, "activate_prompt_version", lambda task_type, path: saved.update({"active": path}))

    result = prompt_optimizer.apply_feedback_prompt_changes("highlight_reel", {
        "changes": [
            {
                "type": "modify",
                "rule_id": "H2",
                "full_rule_text": "### 【H2】残忍删除原则 ★★★\n新的 H2，保留悬念与情绪价值。",
            }
        ]
    })

    assert result["success"] is True
    assert result["version"] == 9
    assert saved["task_type"] == "highlight_reel"
    assert "新的 H2，保留悬念与情绪价值。" in saved["content"]
    assert "旧 H2。" not in saved["content"]
    assert saved["active"] == "/tmp/highlight_reel_v9.md"
