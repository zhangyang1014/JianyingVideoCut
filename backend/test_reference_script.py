import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.models.task import Task, TaskParams, Segment, TaskStatus, TaskType, default_task_params
from backend.services.reference_script import match_reference_script, build_reference_script_context
from backend.services.semantic_auditor import build_audit_prompt, _chunk_cache_hash, plan_audit_chunks
from backend.routers import tasks
from backend.services import task_store


def seg(id, text, start=0, action="keep"):
    return Segment(id=id, text=text, start=start, end=start + 2, action=action)


def test_matches_across_segments_and_retakes_without_editing():
    source = [seg("a", "这个方法可以帮助大家", 0), seg("b", "每天轻松练习十分钟", 2),
              seg("c", "这个方法可以帮助大家每天轻松练习十分钟", 10, "delete")]
    report = match_reference_script("这个方法可以帮助大家每天轻松练习十分钟。", source)
    assert report.matched == report.retained == 1
    assert len(report.lines[0].candidates) == 2
    assert {tuple(c.segment_ids) for c in report.lines[0].candidates} == {("a", "b"), ("c",)}
    assert source[2].action.value == "delete"


@pytest.mark.parametrize(("script", "spoken", "warning"), [
    ("每天只需要花费100元就可以参加我们的全部课程", "每天只需要花费1000元就可以参加我们的全部课程", "数字"),
    ("每天练习这个动作不会导致肌肉酸痛请大家放心", "每天练习这个动作会导致肌肉酸痛请大家放心", "否定"),
    ("每天只需要花费一百元就可以参加我们的全部课程", "每天只需要花费一千元就可以参加我们的全部课程", "数字"),
])
def test_critical_meaning_changes_never_confident(script, spoken, warning):
    report = match_reference_script(script, [seg("a", spoken)])
    assert report.uncertain == 1
    assert warning in report.lines[0].candidates[0].warnings[0]


def test_deleted_and_missing_are_distinguished():
    report = match_reference_script("首先介绍今天的瑜伽动作。请在饭后散步半小时。", [seg("a", "首先介绍今天的瑜伽动作", action="delete")])
    assert (report.matched, report.missing, report.retained) == (1, 1, 0)


def test_does_not_join_across_long_silence():
    report = match_reference_script("甲乙丙丁戊己庚辛", [seg("a", "甲乙丙丁", 0), seg("b", "戊己庚辛", 30)])
    assert report.matched == 0
    assert all(len(c.segment_ids) == 1 for c in report.lines[0].candidates)


def test_empty_script_and_unicode_and_line_limit():
    assert not match_reference_script(" \n！？", []).lines
    assert match_reference_script("ＡＢＣ轻松训练", [seg("a", "abc，轻松训练")]).matched == 1
    assert match_reference_script("完整句子\n" * 201, []).truncated


def test_prompt_scope_and_script_changes_invalidate_cache():
    source = [seg("a", "这个方法可以帮助大家放松肩部", 0), seg("b", "这个方法可以帮助大家放松肩部", 8)]
    first = match_reference_script("这个方法可以帮助大家放松肩部", source)
    second = match_reference_script("这个方法可以帮助大家放松颈部", source)
    block = build_reference_script_context(first, source[:1])
    assert '"outside_batch":true' in block
    assert "禁止仅因 outside_batch" in block
    prompt, _ = build_audit_prompt(source, task_type="monologue_clean", reference_report=first)
    assert "口播原稿比对证据" in prompt
    interview, _ = build_audit_prompt(source, task_type="interview_compress", reference_report=first)
    assert "口播原稿比对证据" not in interview
    chunk = plan_audit_chunks(source, chunk_segments=120, token_budget=24000)[0]
    args = dict(system_prompt="test", task_type="monologue_clean", style_mode="immersive", model="mock", base_url="http://local")
    assert _chunk_cache_hash(source, chunk, reference_report=first, **args) != _chunk_cache_hash(source, chunk, reference_report=second, **args)


@pytest.fixture
def client_store(tmp_path, monkeypatch):
    monkeypatch.setattr(task_store, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_store, "TASKS_FILE", tmp_path / "legacy.json")
    monkeypatch.setattr(task_store, "ensure_dirs", lambda: (tmp_path / "tasks").mkdir(exist_ok=True))
    store = task_store.TaskStore()
    monkeypatch.setattr(tasks, "get_store", lambda: store)
    app = FastAPI(); app.include_router(tasks.router)
    return TestClient(app), store


def test_create_with_script_preserves_scenario_defaults(client_store):
    client, store = client_store
    response = client.post("/api/tasks", json={"name": "有稿口播", "task_type": "monologue_clean", "params": {"reference_script": "欢迎学习今天的课程"}})
    assert response.status_code == 200
    task = store.get(response.json()["id"])
    assert task.params.breath_lead_ms == default_task_params(TaskType.MONOLOGUE_CLEAN).breath_lead_ms
    assert task.params.reference_script == "欢迎学习今天的课程"
    assert client.get(f"/api/tasks/{task.id}/reference-script").json()["report"]["missing"] == 1
    assert client.post("/api/tasks", json={"name": "bad", "task_type": "highlight_reel", "params": {"reference_script": "仅口播"}}).status_code == 400


def test_save_conflict_processing_and_length_limit(client_store):
    client, store = client_store
    task = store.create(Task(name="test", audit_segments=[seg("a", "欢迎学习今天的课程")]))
    url = f"/api/tasks/{task.id}/reference-script"
    req = {"reference_script": "欢迎学习今天的课程", "previous_script": ""}
    response = client.put(url, json=req)
    assert response.status_code == 200 and response.json()["report"]["retained"] == 1
    assert client.put(url, json=req).status_code == 409
    task = store.get(task.id); task.status = TaskStatus.AUDIT_RUNNING; store.update(task)
    assert client.put(url, json={"reference_script": "new", "previous_script": req["reference_script"]}).status_code == 409
    assert client.put(url, json={"reference_script": "a" * 10001, "previous_script": ""}).status_code == 422


def test_failed_save_does_not_mutate_live_task(client_store, monkeypatch):
    client, store = client_store
    task = store.create(Task(name="test"))
    def fail(*args, **kwargs):
        raise task_store.RevisionConflictError("conflict")
    monkeypatch.setattr(store, "update", fail)
    assert client.put(f"/api/tasks/{task.id}/reference-script", json={"reference_script": "新原稿", "previous_script": ""}).status_code == 409
    assert store.get(task.id).params.reference_script == ""


@pytest.mark.asyncio
async def test_cloud_and_ollama_receive_reference_evidence(monkeypatch):
    from types import SimpleNamespace
    import openai
    from backend.services import semantic_auditor as auditor
    source = [seg("a", "今天分享三个肩部放松的方法")]
    params = TaskParams(reference_script=source[0].text)
    received = []
    async def batch(client, segments, chunk, **kwargs):
        received.append(kwargs["reference_report"])
        return auditor.CloudAuditBatchResult(chunk=chunk, segments=segments, model_elapsed=0, parse_elapsed=0, retries=0)
    monkeypatch.setattr(auditor, "_run_openai_audit_batch", batch)
    monkeypatch.setattr(auditor, "_load_cached_batch_result", lambda *args: None)
    monkeypatch.setattr(auditor, "_save_cached_batch_result", lambda *args: None)
    await auditor.run_semantic_audit(source, params, task_type="monologue_clean", api_key="test-key", openai_base_url="http://example.test")
    assert received[0].matched == 1
    prompts = []
    def create(**kwargs):
        prompts.append(kwargs["messages"][1]["content"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='[{"i":1,"a":"k"}]'))])
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    await auditor.run_semantic_audit(source, params, task_type="monologue_clean", provider="ollama")
    assert "口播原稿比对证据" in prompts[0] and params.reference_script in prompts[0]
