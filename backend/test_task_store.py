"""TaskStore 分文件持久化测试：落盘、迁移、原子写、损坏文件跳过、重启状态回退。"""

import json

import pytest

from backend.models.task import Task, TaskStatus, ExportJob, ExportJobKind, ExportJobStatus
from backend.services import task_store as task_store_module
from backend.services.task_store import RevisionConflictError, TaskStore


@pytest.fixture
def store_dirs(tmp_path, monkeypatch):
    """把 TaskStore 的所有路径重定向到临时目录，避免触碰真实运行数据。"""
    data_dir = tmp_path / "data"
    tasks_dir = data_dir / "tasks"
    monkeypatch.setattr(task_store_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(task_store_module, "TASKS_FILE", data_dir / "tasks.json")
    monkeypatch.setattr(task_store_module, "TASKS_DIR", tasks_dir)
    monkeypatch.setattr(task_store_module, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(task_store_module, "EXPORTS_DIR", tmp_path / "exports")
    return {"data_dir": data_dir, "tasks_dir": tasks_dir}


def _read_task_file(tasks_dir, task_id):
    with open(tasks_dir / f"{task_id}.json", "r", encoding="utf-8") as f:
        return json.load(f)


class TestPerFilePersistence:
    def test_create_writes_single_task_file(self, store_dirs):
        store = TaskStore()
        task = store.create(Task(name="任务A"))

        path = store_dirs["tasks_dir"] / f"{task.id}.json"
        assert path.exists()
        assert _read_task_file(store_dirs["tasks_dir"], task.id)["name"] == "任务A"
        serialized = path.read_text(encoding="utf-8")
        assert "\n" not in serialized
        assert "任务A" in serialized

    def test_update_refreshes_updated_at_and_rewrites_file(self, store_dirs):
        store = TaskStore()
        task = store.create(Task(name="任务A"))
        original_updated_at = task.updated_at

        task.name = "任务A改"
        updated = store.update(task)

        assert updated.updated_at >= original_updated_at
        on_disk = _read_task_file(store_dirs["tasks_dir"], task.id)
        assert on_disk["name"] == "任务A改"
        assert on_disk["updated_at"] == updated.updated_at

    def test_update_supports_optional_revision_cas(self, store_dirs):
        store = TaskStore()
        task = store.create(Task(name="任务A"))

        store.update(task, expected_revision=0)
        assert task.revision == 1
        with pytest.raises(RevisionConflictError):
            store.update(task, expected_revision=0)

    def test_update_only_touches_target_task_file(self, store_dirs):
        store = TaskStore()
        task_a = store.create(Task(name="任务A"))
        task_b = store.create(Task(name="任务B"))

        # 在 B 的文件里埋一个哨兵字段；若 update(A) 全量重写，哨兵会丢失
        path_b = store_dirs["tasks_dir"] / f"{task_b.id}.json"
        data_b = json.loads(path_b.read_text(encoding="utf-8"))
        data_b["__sentinel__"] = True
        path_b.write_text(json.dumps(data_b, ensure_ascii=False), encoding="utf-8")

        task_a.name = "任务A改"
        store.update(task_a)

        assert json.loads(path_b.read_text(encoding="utf-8")).get("__sentinel__") is True

    def test_delete_removes_task_file(self, store_dirs):
        store = TaskStore()
        task = store.create(Task(name="任务A"))
        path = store_dirs["tasks_dir"] / f"{task.id}.json"
        assert path.exists()

        assert store.delete(task.id) is True
        assert not path.exists()
        assert store.get(task.id) is None
        assert store.delete(task.id) is False

    def test_get_all_sorted_by_created_at_desc(self, store_dirs):
        store = TaskStore()
        older = store.create(Task(name="旧", created_at="2026-01-01T00:00:00"))
        newer = store.create(Task(name="新", created_at="2026-06-01T00:00:00"))

        assert [t.id for t in store.get_all()] == [newer.id, older.id]

    def test_reload_from_task_files(self, store_dirs):
        store = TaskStore()
        task = store.create(Task(name="任务A"))

        reloaded = TaskStore()
        assert reloaded.get(task.id) is not None
        assert reloaded.get(task.id).name == "任务A"


class TestLegacyMigration:
    def test_migrates_legacy_tasks_json_and_keeps_backup(self, store_dirs):
        data_dir = store_dirs["data_dir"]
        data_dir.mkdir(parents=True, exist_ok=True)
        legacy_tasks = [
            Task(name="旧任务1").model_dump(),
            Task(name="旧任务2").model_dump(),
        ]
        legacy_file = data_dir / "tasks.json"
        legacy_file.write_text(
            json.dumps(legacy_tasks, ensure_ascii=False, default=str), encoding="utf-8"
        )

        store = TaskStore()

        assert len(store.get_all()) == 2
        for task_data in legacy_tasks:
            assert (store_dirs["tasks_dir"] / f"{task_data['id']}.json").exists()
        assert not legacy_file.exists()
        assert (data_dir / "tasks.json.bak").exists()

    def test_corrupt_legacy_file_is_kept_and_load_continues(self, store_dirs):
        data_dir = store_dirs["data_dir"]
        store_dirs["tasks_dir"].mkdir(parents=True, exist_ok=True)
        legacy_file = data_dir / "tasks.json"
        legacy_file.write_text("{ 这不是合法JSON", encoding="utf-8")

        good_task = Task(name="正常任务")
        (store_dirs["tasks_dir"] / f"{good_task.id}.json").write_text(
            json.dumps(good_task.model_dump(), ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        store = TaskStore()

        assert legacy_file.exists()  # 迁移失败时保留原文件
        assert not (data_dir / "tasks.json.bak").exists()
        assert store.get(good_task.id) is not None


class TestAtomicWrite:
    def test_no_tmp_files_left_after_operations(self, store_dirs):
        store = TaskStore()
        task = store.create(Task(name="任务A"))
        task.name = "任务A改"
        store.update(task)
        store.delete(task.id)

        leftovers = list(store_dirs["tasks_dir"].glob("*.tmp"))
        assert leftovers == []

    def test_no_tmp_files_left_after_migration(self, store_dirs):
        data_dir = store_dirs["data_dir"]
        data_dir.mkdir(parents=True, exist_ok=True)
        legacy_file = data_dir / "tasks.json"
        legacy_file.write_text(
            json.dumps([Task(name="旧任务").model_dump()], ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        TaskStore()

        assert list(store_dirs["tasks_dir"].glob("*.tmp")) == []

    def test_save_failure_propagates(self, store_dirs, monkeypatch):
        store = TaskStore()
        task = store.create(Task(name="任务A"))

        def fail_write(_path, _payload):
            raise OSError("disk full")

        monkeypatch.setattr(task_store_module, "_atomic_write_json", fail_write)
        task.name = "不会静默成功"
        with pytest.raises(OSError, match="disk full"):
            store.update(task)


class TestCorruptTaskFiles:
    def test_corrupt_task_file_is_skipped(self, store_dirs, capsys):
        tasks_dir = store_dirs["tasks_dir"]
        tasks_dir.mkdir(parents=True, exist_ok=True)

        good_task = Task(name="正常任务")
        (tasks_dir / f"{good_task.id}.json").write_text(
            json.dumps(good_task.model_dump(), ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        (tasks_dir / "broken.json").write_text("{ 坏掉的文件", encoding="utf-8")

        store = TaskStore()

        assert store.get(good_task.id) is not None
        assert len(store.get_all()) == 1
        assert "跳过损坏的任务文件" in capsys.readouterr().out


class TestInterruptedStatusFallback:
    def test_running_status_falls_back_and_is_repersisted(self, store_dirs):
        tasks_dir = store_dirs["tasks_dir"]
        tasks_dir.mkdir(parents=True, exist_ok=True)

        task = Task(name="导出中任务", status=TaskStatus.EXPORT_RUNNING)
        (tasks_dir / f"{task.id}.json").write_text(
            json.dumps(task.model_dump(), ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        store = TaskStore()

        assert store.get(task.id).status == TaskStatus.REVIEW
        on_disk = _read_task_file(tasks_dir, task.id)
        assert on_disk["status"] == TaskStatus.REVIEW.value

    def test_running_export_job_is_marked_failed_without_changing_review_status(self, store_dirs):
        tasks_dir = store_dirs["tasks_dir"]
        tasks_dir.mkdir(parents=True, exist_ok=True)

        task = Task(
            name="独立导出任务",
            status=TaskStatus.REVIEW,
            export_jobs=[
                ExportJob(
                    task_id="placeholder",
                    kind=ExportJobKind.HIGHLIGHT_REEL,
                    status=ExportJobStatus.RUNNING,
                )
            ],
        )
        task.export_jobs[0].task_id = task.id
        (tasks_dir / f"{task.id}.json").write_text(
            json.dumps(task.model_dump(), ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        store = TaskStore()

        loaded = store.get(task.id)
        assert loaded.status == TaskStatus.REVIEW
        assert loaded.export_jobs[0].status == ExportJobStatus.FAILED
        assert loaded.export_jobs[0].error_message == "服务重启后导出已中断"
        on_disk = _read_task_file(tasks_dir, task.id)
        assert on_disk["status"] == TaskStatus.REVIEW.value
        assert on_disk["export_jobs"][0]["status"] == ExportJobStatus.FAILED.value

    def test_restart_preserves_checkpoint_and_marks_execution_interrupted(self, store_dirs):
        store = TaskStore()
        task = store.create(Task(name="可恢复任务"))
        run, _ = store.start_execution(task.id, operation="audit")
        assert store.checkpoint_execution(
            task.id,
            run.run_id,
            run.generation,
            stage="llm",
            status="running",
            artifact_ref="audit/input.json",
            artifact_digest="sha256:abc",
        )

        reloaded = TaskStore()
        loaded = reloaded.get(task.id)
        saved_run = loaded.execution_runs[-1]

        assert saved_run.status == "interrupted"
        assert saved_run.recoverable is True
        assert saved_run.stages[0].status == "interrupted"
        assert saved_run.stages[0].artifact_ref == "audit/input.json"
        assert saved_run.stages[0].artifact_digest == "sha256:abc"
