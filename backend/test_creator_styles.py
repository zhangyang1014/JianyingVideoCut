"""Creator bundles keep evidence status and provenance through version changes."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from backend.models.production import BrandProfile, ProductionProject
from backend.services.creator_styles import STYLE_ROOT, load_creator_style
from backend.services.production_planner import template_directions
from backend.services.production_store import ProductionStore


class CreatorStyleTests(unittest.TestCase):
    def test_reported_lina_summary_does_not_become_verified_evidence(self):
        snapshot = load_creator_style("lina")
        self.assertEqual(snapshot.evidence_status, "reported")
        evidence = json.loads(snapshot.evidence_json)
        self.assertEqual(evidence["works"], [])
        self.assertEqual(evidence["batch"]["paragraph_locators_verified_locally"], 0)
        self.assertEqual([m["numerator_reported"] for m in evidence["metrics"]], [8, 45, 81])
        self.assertEqual([r["status"] for r in evidence["rules"]], ["reported", "candidate", "candidate"])

    def test_another_creator_does_not_inherit_lina(self):
        self.assertIsNone(load_creator_style("another_creator"))

    def test_invalid_registered_bundles_fail(self):
        for field, value in (("creator_id", "someone_else"), ("version", "v2"),
                             ("method_version", "unknown"), ("evidence_status", "assumed_verified")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "styles"
                shutil.copytree(STYLE_ROOT, root)
                path = root / "lina/v1/evidence.json"
                evidence = json.loads(path.read_text(encoding="utf-8"))
                evidence[field] = value
                path.write_text(json.dumps(evidence), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_creator_style("lina", root=root)

    def test_invalid_version_and_path_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "active.json").write_text('{"lina": "../../outside"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_creator_style("lina", root=root)
            with self.assertRaises(ValueError):
                load_creator_style("../lina", root=root)

    def test_saved_direction_keeps_original_bundle_after_active_version_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "styles"
            shutil.copytree(STYLE_ROOT, root)
            original = load_creator_style("lina", root=root)
            self.assertEqual(original, load_creator_style("lina", root=root))
            project = ProductionProject(name="风格快照", brand=BrandProfile(product="书"))
            project.directions = template_directions(project)
            project.directions[0].style_snapshot = original
            store = ProductionStore(Path(temp) / "projects")
            store.create(project)
            shutil.copytree(root / "lina/v1", root / "lina/v2")
            path = root / "lina/v2/evidence.json"
            evidence = json.loads(path.read_text(encoding="utf-8"))
            evidence["version"] = "v2"
            path.write_text(json.dumps(evidence), encoding="utf-8")
            (root / "active.json").write_text('{"lina": "v2"}', encoding="utf-8")
            latest = load_creator_style("lina", root=root)
            self.assertNotEqual(original.content_sha256, latest.content_sha256)
            self.assertEqual(latest.version, "v2")
            saved = ProductionStore(store.root).get(project.id).directions[0].style_snapshot
            self.assertEqual(saved, original)


if __name__ == "__main__":
    unittest.main()
