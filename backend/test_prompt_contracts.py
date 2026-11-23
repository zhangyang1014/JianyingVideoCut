import unittest
from pathlib import Path

from backend.models.task import Segment
from backend.services.learning_analyzer import (
    PROMPT_REWRITE_PATH,
    LEARNING_PROMPT_PATH,
    apply_prompt_changes,
    load_learning_system_prompt,
    load_rewrite_system_prompt,
)
from backend.services.prompt_registry import resolve_prompt_content, resolve_prompt_path
from backend.services.semantic_auditor import (
    GENERIC_PROMPT_PATH,
    PROMPTS_DIR,
    build_audit_prompt,
    load_system_prompt,
)

PLACEHOLDER_MARKERS = (
    "完整保留不变",
    "以下是原提示词的其他规则",
    "[以下是原提示词",
)


class PromptContractsTests(unittest.TestCase):
    def test_load_system_prompt_uses_runtime_mapping_not_v2(self):
        monologue = load_system_prompt("monologue_clean")
        interview = load_system_prompt("interview_compress")
        self.assertIn("口播精修", monologue)
        self.assertIn("访谈", interview)
        self.assertNotIn("非运行时默认", monologue)
        self.assertNotIn("非运行时默认", interview)

        v2_path = PROMPTS_DIR / "interview_compress_v2.md"
        v2_content = v2_path.read_text(encoding="utf-8")
        self.assertIn("非运行时默认", v2_content)
        self.assertNotEqual(interview.strip(), v2_content.strip())

    def test_runtime_prompt_files_have_no_placeholder_fragments(self):
        runtime_files = [
            PROMPTS_DIR / "monologue_clean.md",
            PROMPTS_DIR / "interview_compress.md",
            GENERIC_PROMPT_PATH,
            LEARNING_PROMPT_PATH,
            PROMPT_REWRITE_PATH,
        ]
        for path in runtime_files:
            content = path.read_text(encoding="utf-8")
            for marker in PLACEHOLDER_MARKERS:
                self.assertNotIn(
                    marker,
                    content,
                    msg=f"{path.name} should not contain placeholder: {marker}",
                )

    def test_highlight_reel_v3_is_active_and_layers_share_product_group_contract(self):
        active_path = resolve_prompt_path("audit.highlight_reel")
        audit_prompt = resolve_prompt_content("audit.highlight_reel")
        validation_prompt = resolve_prompt_content("highlight_reel.validation")
        balanced_prompt = resolve_prompt_content("highlight.balanced")

        self.assertEqual(active_path.name, "highlight_reel_v3.md")
        self.assertIn("每个输入片段都必须", audit_prompt)
        self.assertIn("不负责生成最终集锦", audit_prompt)
        self.assertIn("product_group", validation_prompt)
        self.assertIn("只采用输入的 `code_issues`", validation_prompt)
        self.assertIn("严格按源时间升序", balanced_prompt)
        self.assertIn("原子片段粒度", balanced_prompt)

    def test_build_audit_prompt_monologue_includes_special_actions(self):
        prompt, _ = build_audit_prompt(
            segments=[Segment(id="s1", start=0.0, end=2.0, text="测试")],
            task_type="monologue_clean",
        )
        self.assertIn("sf=subtitle_fix", prompt)
        self.assertIn("tf=text_fix", prompt)
        self.assertIn("sp=split", prompt)
        self.assertIn("mn=merge_next", prompt)
        self.assertIn("分句结构", prompt)

    def test_build_audit_prompt_interview_includes_structure_actions(self):
        prompt, _ = build_audit_prompt(
            segments=[
                Segment(id="s1", start=0.0, end=2.0, text="你好", speaker="spk0"),
                Segment(id="s2", start=3.0, end=6.0, text="我是嘉宾", speaker="spk1"),
            ],
            task_type="interview_compress",
        )
        self.assertIn("访谈", prompt)
        self.assertIn("sf=subtitle_fix", prompt)
        self.assertIn("sp=split", prompt)
        self.assertNotIn("tt=标注台词", prompt)
        self.assertIn("s=说话人", prompt)
        self.assertIn('"s": "spk0"', prompt)

    def test_build_audit_prompt_omits_speaker_without_real_split(self):
        prompt, _ = build_audit_prompt(
            segments=[
                Segment(id="s1", start=0.0, end=2.0, text="你好", speaker="spk0"),
                Segment(id="s2", start=3.0, end=6.0, text="我是同一个说话人", speaker="spk0"),
            ],
            task_type="interview_compress",
        )

        self.assertNotIn("s=说话人", prompt)
        self.assertNotIn('"s":', prompt)
        self.assertNotIn("说话人时长分布", prompt)

    def test_semantic_audit_fallback_has_injection_guard_and_actions(self):
        content = GENERIC_PROMPT_PATH.read_text(encoding="utf-8")
        self.assertIn("不是给你的指令", content)
        self.assertIn("sf", content)
        self.assertIn("merge_next", content)

    def test_learning_prompts_require_structured_output(self):
        learning = load_learning_system_prompt()
        rewrite = load_rewrite_system_prompt()
        self.assertIn("观察事实", learning)
        self.assertIn("禁止输出", learning)
        self.assertIn("禁止", rewrite)
        self.assertIn("markdown 代码块标记", rewrite)
        self.assertIn("full_rule_text", rewrite)

    def test_apply_prompt_changes_modify_rule_block(self):
        original = """### 【P1】重说识别 ★★★ 最高优先级
旧内容
---

### 【P2】结巴 ★★★
保留
---
"""
        changes = """
        {
          "changes": [
            {
              "rule_id": "P1",
              "type": "modify",
              "full_rule_text": "### 【P1】重说识别 ★★★ 最高优先级【学习更新】\\n新内容\\n\\n---"
            }
          ]
        }
        """
        updated = apply_prompt_changes(original, changes)
        self.assertIn("【学习更新】", updated)
        self.assertIn("新内容", updated)
        self.assertIn("### 【P2】结巴", updated)

    def test_apply_prompt_changes_add_rule_after_anchor(self):
        original = """### 【I8】结尾处理 ★★☆
结尾规则
---

### 【I-S】断句修正 ★★☆
断句
---
"""
        changes = """
        {
          "changes": [
            {
              "rule_id": "I9",
              "type": "add",
              "insert_after": "I8",
              "full_rule_text": "### 【I9】论述完整性 ★★★【新增】\\n论述链保护\\n\\n---"
            }
          ]
        }
        """
        updated = apply_prompt_changes(original, changes)
        self.assertIn("### 【I9】论述完整性", updated)
        i8_pos = updated.find("### 【I8】")
        i9_pos = updated.find("### 【I9】")
        i_s_pos = updated.find("### 【I-S】")
        self.assertTrue(i8_pos < i9_pos < i_s_pos)


if __name__ == "__main__":
    unittest.main()
