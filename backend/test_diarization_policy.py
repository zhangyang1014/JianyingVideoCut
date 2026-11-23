"""说话人分离按任务类型启用的策略测试。"""

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.models.task import (
    TaskType,
    TaskParams,
    should_enable_diarization_for_task_type,
    default_task_params,
    resolve_enable_diarization,
)
from backend.services.task_store import get_store


@pytest.mark.parametrize(
    "task_type,expected",
    [
        (TaskType.INTERVIEW_COMPRESS, True),
        (TaskType.MONOLOGUE_CLEAN, False),
        (TaskType.HIGHLIGHT_REEL, False),
        (TaskType.ONE_CLICK_TRANSLATE, False),
    ],
)
def test_should_enable_diarization_for_task_type(task_type, expected):
    assert should_enable_diarization_for_task_type(task_type) is expected


def test_default_task_params_follows_task_type():
    assert default_task_params(TaskType.INTERVIEW_COMPRESS).enable_diarization is True
    assert default_task_params(TaskType.MONOLOGUE_CLEAN).enable_diarization is False


def test_resolve_enable_diarization_prefers_request_override():
    assert resolve_enable_diarization(
        TaskType.MONOLOGUE_CLEAN,
        request_override=True,
    ) is True
    assert resolve_enable_diarization(
        TaskType.INTERVIEW_COMPRESS,
        request_override=False,
    ) is False


def test_resolve_enable_diarization_falls_back_to_task_type():
    assert resolve_enable_diarization(TaskType.INTERVIEW_COMPRESS) is True
    assert resolve_enable_diarization(TaskType.HIGHLIGHT_REEL) is False


class TestCreateTaskDiarizationDefaults:
    def setup_method(self):
        self.client = TestClient(app)

    def test_interview_task_defaults_to_diarization_on(self, tmp_path, monkeypatch):
        from backend.services import task_store as task_store_module

        data_dir = tmp_path / "data"
        monkeypatch.setattr(task_store_module, "DATA_DIR", data_dir)
        monkeypatch.setattr(task_store_module, "TASKS_FILE", data_dir / "tasks.json")
        monkeypatch.setattr(task_store_module, "TASKS_DIR", data_dir / "tasks")
        task_store_module._store = None

        res = self.client.post(
            "/api/tasks",
            json={"name": "访谈任务", "task_type": "interview_compress"},
        )
        assert res.status_code == 200
        assert res.json()["params"]["enable_diarization"] is True

    def test_monologue_task_defaults_to_diarization_off(self, tmp_path, monkeypatch):
        from backend.services import task_store as task_store_module

        data_dir = tmp_path / "data"
        monkeypatch.setattr(task_store_module, "DATA_DIR", data_dir)
        monkeypatch.setattr(task_store_module, "TASKS_FILE", data_dir / "tasks.json")
        monkeypatch.setattr(task_store_module, "TASKS_DIR", data_dir / "tasks")
        task_store_module._store = None

        res = self.client.post(
            "/api/tasks",
            json={"name": "口播任务", "task_type": "monologue_clean"},
        )
        assert res.status_code == 200
        assert res.json()["params"]["enable_diarization"] is False

    def test_explicit_params_override_task_default(self, tmp_path, monkeypatch):
        from backend.services import task_store as task_store_module

        data_dir = tmp_path / "data"
        monkeypatch.setattr(task_store_module, "DATA_DIR", data_dir)
        monkeypatch.setattr(task_store_module, "TASKS_FILE", data_dir / "tasks.json")
        monkeypatch.setattr(task_store_module, "TASKS_DIR", data_dir / "tasks")
        task_store_module._store = None

        res = self.client.post(
            "/api/tasks",
            json={
                "name": "口播但开分离",
                "task_type": "monologue_clean",
                "params": {"enable_diarization": True},
            },
        )
        assert res.status_code == 200
        assert res.json()["params"]["enable_diarization"] is True


class TestTriggerAsrDiarizationDefaults:
    def setup_method(self):
        self.client = TestClient(app)

    def _patch_store(self, tmp_path, monkeypatch):
        from backend.services import task_store as task_store_module

        data_dir = tmp_path / "data"
        monkeypatch.setattr(task_store_module, "DATA_DIR", data_dir)
        monkeypatch.setattr(task_store_module, "TASKS_FILE", data_dir / "tasks.json")
        monkeypatch.setattr(task_store_module, "TASKS_DIR", data_dir / "tasks")
        task_store_module._store = None

    def test_trigger_asr_without_override_uses_task_type_default(self, tmp_path, monkeypatch):
        self._patch_store(tmp_path, monkeypatch)
        store = get_store()
        from backend.models.task import Task

        task = store.create(
            Task(
                name="旧口播任务",
                task_type=TaskType.MONOLOGUE_CLEAN,
                params=TaskParams(enable_diarization=True),
                video_path=str(tmp_path / "demo.mp4"),
            )
        )
        (tmp_path / "demo.mp4").write_bytes(b"fake")

        def _discard_runner(_task_id, coro, _expected_status=None, **_kwargs):
            coro.close()

        with monkeypatch.context() as m:
            m.setattr("backend.routers.asr._start_task_runner", _discard_runner)
            res = self.client.post(
                f"/api/tasks/{task.id}/asr",
                json={"backend": "funasr", "model": "paraformer-zh"},
            )

        assert res.status_code == 200
        updated = store.get(task.id)
        assert updated.params.enable_diarization is False

    def test_trigger_asr_with_explicit_override(self, tmp_path, monkeypatch):
        self._patch_store(tmp_path, monkeypatch)
        store = get_store()
        from backend.models.task import Task

        task = store.create(
            Task(
                name="口播任务",
                task_type=TaskType.MONOLOGUE_CLEAN,
                params=TaskParams(enable_diarization=False),
                video_path=str(tmp_path / "demo.mp4"),
            )
        )
        (tmp_path / "demo.mp4").write_bytes(b"fake")

        def _discard_runner(_task_id, coro, _expected_status=None, **_kwargs):
            coro.close()

        with monkeypatch.context() as m:
            m.setattr("backend.routers.asr._start_task_runner", _discard_runner)
            res = self.client.post(
                f"/api/tasks/{task.id}/asr",
                json={
                    "backend": "funasr",
                    "model": "paraformer-zh",
                    "enable_diarization": True,
                },
            )

        assert res.status_code == 200
        updated = store.get(task.id)
        assert updated.params.enable_diarization is True
