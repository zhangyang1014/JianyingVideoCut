import tempfile
import unittest
from pathlib import Path

from backend.services import prompt_registry
from backend.services.semantic_auditor import load_system_prompt


class PromptRegistryTests(unittest.TestCase):
    def test_highlight_audit_resolves_active_runtime_version(self):
        payload = prompt_registry.prompt_payload("audit.highlight_reel")

        self.assertEqual(payload["active_version"], "highlight_reel_v3.md")
        self.assertTrue(payload["source"].endswith("backend/prompts/highlight_reel_v3.md"))
        self.assertEqual(
            load_system_prompt("highlight_reel"),
            prompt_registry.resolve_prompt_content("audit.highlight_reel"),
        )

    def test_feature_listing_separates_audit_and_highlight_validation(self):
        audit_ids = {item["id"] for item in prompt_registry.list_prompt_payloads("audit")}
        validation_ids = {
            item["id"] for item in prompt_registry.list_prompt_payloads("highlight_reel")
        }

        self.assertIn("audit.highlight_reel", audit_ids)
        self.assertNotIn("highlight_reel.validation", audit_ids)
        self.assertIn("highlight_reel.validation", validation_ids)

    def test_registered_prompt_save_updates_resolved_source(self):
        with tempfile.TemporaryDirectory(dir=prompt_registry.PROMPTS_DIR) as temp_dir:
            path = Path(temp_dir) / "editable.md"
            path.write_text("before", encoding="utf-8")
            prompt_id = "test.editable"
            prompt_registry._SPECS[prompt_id] = prompt_registry.PromptSpec(
                prompt_id=prompt_id,
                feature="test",
                label="测试",
                description="测试保存",
                path=path,
            )
            try:
                prompt_registry.save_prompt_content(prompt_id, "after")
                self.assertEqual(path.read_text(encoding="utf-8"), "after")
            finally:
                prompt_registry._SPECS.pop(prompt_id, None)


if __name__ == "__main__":
    unittest.main()
