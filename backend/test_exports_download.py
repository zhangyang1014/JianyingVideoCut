import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.main as main
from backend.routers import export as export_router
from backend.routers import tasks as tasks_router


class ExportsDownloadTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.exports_dir = Path(self._temp_dir.name)
        self.secret_path = Path(self._temp_dir.name).parent / "secret_outside.txt"
        self.secret_path.write_text("secret")
        (self.exports_dir / "demo.mp4").write_bytes(b"fake video content")

        self._patcher = patch.object(tasks_router, "EXPORTS_DIR", self.exports_dir)
        self._patcher.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self._patcher.stop()
        self.secret_path.unlink(missing_ok=True)
        self._temp_dir.cleanup()

    def test_download_existing_export(self):
        resp = self.client.get("/api/exports/demo.mp4")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b"fake video content")

    def test_missing_file_returns_404(self):
        resp = self.client.get("/api/exports/not_exist.mp4")
        self.assertEqual(resp.status_code, 404)

    def test_path_traversal_is_rejected(self):
        # URL 编码的 ../ 穿越应被净化并返回 404
        resp = self.client.get(f"/api/exports/..%2F{self.secret_path.name}")
        self.assertEqual(resp.status_code, 404)

    def test_encoded_traversal_to_parent_rejected(self):
        resp = self.client.get("/api/exports/%2e%2e%2f%2e%2e%2fetc%2fpasswd")
        self.assertEqual(resp.status_code, 404)

    def test_dot_only_filename_rejected(self):
        resp = self.client.get("/api/exports/%2e%2e")
        self.assertEqual(resp.status_code, 404)

    def test_reveal_export_opens_file_in_finder(self):
        task = SimpleNamespace(export_path=str(self.exports_dir / "demo.mp4"))
        with (
            patch.object(export_router, "EXPORTS_DIR", self.exports_dir),
            patch.object(export_router, "get_store") as get_store,
            patch.object(export_router.subprocess, "Popen") as popen,
            patch.object(export_router.sys, "platform", "darwin"),
        ):
            get_store.return_value.get.return_value = task
            resp = self.client.post("/api/tasks/task-1/export/reveal")

        self.assertEqual(resp.status_code, 200)
        popen.assert_called_once_with(
            ["open", "-R", str((self.exports_dir / "demo.mp4").resolve())],
            stdout=export_router.subprocess.DEVNULL,
            stderr=export_router.subprocess.DEVNULL,
        )

    def test_reveal_export_rejects_path_outside_exports(self):
        task = SimpleNamespace(export_path=str(self.secret_path))
        with (
            patch.object(export_router, "EXPORTS_DIR", self.exports_dir),
            patch.object(export_router, "get_store") as get_store,
            patch.object(export_router.subprocess, "Popen") as popen,
        ):
            get_store.return_value.get.return_value = task
            resp = self.client.post("/api/tasks/task-1/export/reveal")

        self.assertEqual(resp.status_code, 403)
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
