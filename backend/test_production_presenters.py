"""Digital-person profiles and project overrides stay isolated through the real API."""
import asyncio
import json
import httpx
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.models.production import BrandProfile, ProductionProject
from backend.routers import productions as api
from backend.services import production_heygen as heygen
from backend.services import production_planner as planner
from backend.services import production_presenters as presenters
from backend.services.production_store import ProductionStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = ProductionStore(tmp_path / "projects")
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(presenters, "DATA_PATH", tmp_path / "presenters.json")
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        yield client, store


def config(**changes):
    return {"name": "小禾", "primary_audience": "刚开始工作的年轻人", "address": ["各位朋友"],
            "text_style": "简洁直接", "structure_style": "判断 → 原因 → 一个动作", **changes}


def fields(profile):
    return {k: v for k, v in profile.items() if k not in {"id", "revision", "archived"}}


def create_video(client, profile, **extra):
    return client.post("/api/productions", json={"name": "日常收纳", "product": "收纳盒",
        "project_type": "digital_presenter", "presenter_id": profile["id"],
        "presenter_revision": profile["revision"], **extra})


def test_catalog_failure_is_recoverable_and_does_not_overwrite_user_data(env, monkeypatch):
    client, _ = env
    broken = '{"broken":'
    presenters.DATA_PATH.write_text(broken)
    response = client.get("/api/productions/presenters?include_archived=true")
    assert response.status_code == 503
    assert "档案暂时无法读取" in response.json()["detail"]
    response = client.post("/api/productions/presenters", json=config())
    assert response.status_code == 503
    assert presenters.DATA_PATH.read_text() == broken


@pytest.mark.parametrize("exception,status", [(httpx.ReadTimeout("timeout"), 504), (httpx.ConnectError("network"), 502)])
def test_binding_network_failures_return_actionable_errors_without_saving(env, monkeypatch, exception, status):
    client, _ = env
    profile = client.post("/api/productions/presenters", json=config()).json()
    async def failed(_key):
        raise exception
    monkeypatch.setattr(heygen, "catalog", failed)
    response = client.put(f"/api/productions/presenters/{profile['id']}", json={**fields(profile),
        "expected_revision": profile["revision"], "text_style": "不应保存",
        "heygen": {"avatar_id": "avatar", "voice_id": "voice"}})
    assert response.status_code == status
    assert "本次档案未保存" in response.json()["detail"]
    saved = presenters.presenter_catalog()[profile["id"]]
    assert saved.revision == profile["revision"]
    assert saved.text_style == profile["text_style"]


def test_catalog_has_a_total_deadline_and_cancels_the_lookup(env, monkeypatch):
    client, _ = env
    stopped = []
    async def stalled(_key):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.append(True)
    monkeypatch.setattr(heygen, "catalog", stalled)
    monkeypatch.setattr(api, "HEYGEN_CATALOG_TIMEOUT_SECONDS", 0.01)
    response = client.post("/api/productions/heygen/catalog", json={"api_key": ""})
    assert response.status_code == 504
    assert stopped == [True]


def test_creation_requires_an_existing_selected_person_and_rejects_invalid_overrides(env):
    client, _ = env
    base = {"name": "选数字人", "product": "书", "project_type": "digital_presenter"}
    for extra in ({}, {"presenter_id": "unknown"}, {"presenter_id": "../lina"},
                  {"presenter_id": "lina", "presenter_overrides": config(primary_audience="  ")}):
        assert client.post("/api/productions", json={**base, **extra}).status_code == 422
    assert client.post("/api/productions", json={"name": "普通视频", "product": "书", "presenter_overrides": config()}).status_code == 422
    for change in ({"name": " "}, {"address": []}, {"address": [" "]}, {"heygen": {"avatar_id": "only-avatar"}}):
        assert client.post("/api/productions/presenters", json=config(**change)).status_code == 422


def test_manage_profiles_persist_revision_and_archive_without_breaking_projects(env):
    client, store = env
    response = client.post("/api/productions/presenters", json=config())
    assert response.status_code == 200
    original = response.json()
    assert presenters.presenter_catalog()[original["id"]].name == "小禾"
    project = create_video(client, original).json()
    body = {**fields(original), "name": "小禾新版", "expected_revision": 1, "archived": True}
    result = client.put(f"/api/productions/presenters/{original['id']}", json=body)
    assert result.status_code == 200
    assert result.json()["revision"] == 2
    assert client.put(f"/api/productions/presenters/{original['id']}", json=body).status_code == 409
    assert all(p["id"] != original["id"] for p in client.get("/api/productions/presenters").json())
    assert any(p["id"] == original["id"] and p["archived"] for p in client.get("/api/productions/presenters?include_archived=true").json())
    assert create_video(client, original).status_code == 422
    saved = ProductionStore(store.root).get(project["id"])
    assert saved.presenter_snapshot.name == "小禾"
    assert saved.presenter_snapshot.revision == 1
    # A project can still edit its own settings after its source person is archived.
    response = client.put(f"/api/productions/{saved.id}/presenter", json={"expected_revision": saved.revision,
        "presenter_id": original["id"], "presenter_overrides": config(text_style="本片活泼一点")})
    assert response.status_code == 200
    assert response.json()["presenter_snapshot"]["text_style"] == "本片活泼一点"


def test_project_overrides_and_later_catalog_changes_are_isolated(env):
    client, store = env
    original = client.post("/api/productions/presenters", json=config()).json()
    overrides = {**fields(original), "primary_audience": "本片只给大学新生", "text_style": "本片轻松一点"}
    response = create_video(client, original, presenter_overrides=overrides)
    assert response.status_code == 200
    project = response.json()
    assert presenters.presenter_catalog()[original["id"]].text_style == "简洁直接"
    updated = client.put(f"/api/productions/presenters/{original['id']}", json={**fields(original),
        "expected_revision": 1, "text_style": "长期改为严谨语气"}).json()
    assert create_video(client, original).status_code == 409
    assert create_video(client, updated).json()["presenter_snapshot"]["text_style"] == "长期改为严谨语气"
    saved = store.get(project["id"])
    assert saved.presenter_snapshot.primary_audience == "本片只给大学新生"
    assert saved.presenter_snapshot.text_style == "本片轻松一点"
    planned = client.post(f"/api/productions/{saved.id}/plan", json={"expected_revision": saved.revision, "mode": "template"}).json()
    assert planned["directions"][0]["presenter_snapshot"]["text_style"] == "本片轻松一点"
    assert planned["directions"][0]["shots"][0]["spoken_text"].startswith("各位朋友")


def test_ai_uses_the_saved_override_without_rereading_global_style(env, monkeypatch):
    client, store = env
    profile = client.post("/api/productions/presenters", json=config()).json()
    project = create_video(client, profile, presenter_overrides={**fields(profile), "text_style": "只用本片的写法"}).json()
    saved = store.get(project["id"])
    inputs = []
    stages = []

    async def route(role, messages, **kwargs):
        content = messages[1]["content"]
        payload = json.loads(content[0]["text"] if isinstance(content, list) else content)
        inputs.append(payload)
        if isinstance(content, list):
            stages.append("structure")
            reference = client.get(f"/api/productions/presenter-workflow?stage=structure&project_id={saved.id}").json()
            assert messages[0]["content"] == reference["effective_prompt"]
            assert "# 数字人口播结构" in messages[0]["content"]
            assert "# Lina 短视频结构" not in messages[0]["content"]
            directions = [d.model_dump() for d in planner.template_directions(saved)]
        else:
            stages.append("polish")
            reference = client.get(f"/api/productions/presenter-workflow?stage=polish&project_id={saved.id}").json()
            assert messages[0]["content"] == reference["effective_prompt"]
            assert "# 数字人口播文字润色" in messages[0]["content"]
            assert "# Lina 口播语气润色" not in messages[0]["content"]
            directions = [{"id": d["id"], "shots": [{k: s[k] for k in ("shot_id", "spoken_text", "subtitle")}
                                                    for s in d["shots"]]} for d in payload["directions"]]
            directions[0]["shots"][0].update(spoken_text="朋友们，先把手边这一件收好。", subtitle="朋友们，先把手边这一件收好。")
        return SimpleNamespace(value=kwargs["validate"](json.dumps({"directions": directions})))

    def unexpected(*args, **kwargs):
        raise AssertionError("A saved project must not reread mutable defaults")

    monkeypatch.setattr(planner, "route_chat_completion", route)
    monkeypatch.setattr(planner, "load_creator_style", unexpected)
    monkeypatch.setattr(presenters, "presenter_catalog", unexpected)
    directions, _ = asyncio.run(planner.plan_with_ai(saved))
    assert stages == ["structure", "polish"]
    assert inputs[0]["presenter"]["text_style"] == "只用本片的写法"
    assert inputs[1]["presenter"]["text_style"] == "只用本片的写法"
    assert "creator_style" not in inputs[0]
    assert all(d.presenter_snapshot == saved.presenter_snapshot and d.style_snapshot is None for d in directions)
    assert directions[0].shots[0].spoken_text == "朋友们，先把手边这一件收好。"
    assert directions[0].structure == inputs[1]["directions"][0]["structure"]


def test_missing_polish_skill_fails_before_starting_a_model_call(env, tmp_path, monkeypatch):
    client, store = env
    profile = client.post("/api/productions/presenters", json=config()).json()
    project = create_video(client, profile).json()
    workflow = json.loads(planner.PRESENTER_WORKFLOW.read_text(encoding="utf-8"))
    workflow["polish"]["prompt_file"] = ".cursor/skills/missing-polish/SKILL.md"
    registry = tmp_path / "workflow.json"
    registry.write_text(json.dumps(workflow), encoding="utf-8")
    monkeypatch.setattr(planner, "PRESENTER_WORKFLOW", registry)
    def unexpected(*args, **kwargs):
        raise AssertionError("Skills must be loaded before any model call")
    monkeypatch.setattr(planner, "route_chat_completion", unexpected)
    with pytest.raises(FileNotFoundError):
        asyncio.run(planner.plan_with_ai(store.get(project["id"])))


def test_rule_reference_returns_full_files_and_all_lina_rule_layers(env):
    client, _ = env
    structure = client.get("/api/productions/presenter-workflow?presenter_id=lina&stage=structure")
    assert structure.status_code == 200
    data = structure.json()
    skill = next(d for d in data["documents"] if d["kind"] == "skill")
    original = (planner.PROJECT_ROOT / skill["path"]).read_text(encoding="utf-8")
    assert skill["content"] == original
    assert "## 参考资料" in skill["content"]
    assert "## 参考资料" not in data["effective_prompt"]
    assert {d["kind"] for d in data["documents"]} == {"skill", "shared_rule", "style_card", "evidence"}
    evidence = json.loads(next(d["content"] for d in data["documents"] if d["kind"] == "evidence"))
    assert evidence["evidence_status"] == "reported"
    assert evidence["works"] == []
    polish = client.get("/api/productions/presenter-workflow?presenter_id=lina&stage=polish").json()
    assert [d["kind"] for d in polish["documents"]] == ["skill", "creator_rule"]
    for doc in polish["documents"]:
        assert doc["content"] == (planner.PROJECT_ROOT / doc["path"]).read_text(encoding="utf-8")
    _, spoken = planner.writing_skills("lina")
    assert polish["effective_prompt"] == planner.polish_system_prompt(spoken)
    generic = client.get("/api/productions/presenter-workflow?stage=polish").json()
    assert [d["kind"] for d in generic["documents"]] == ["skill"]
    assert "# Lina 口播语气润色" not in generic["effective_prompt"]
    assert client.get("/api/productions/presenter-workflow?presenter_id=../lina").status_code == 422
    assert client.get("/api/productions/presenter-workflow?stage=unknown").status_code == 422


def test_project_rule_reference_uses_the_pinned_style_instead_of_current_defaults(env):
    client, store = env
    response = client.post("/api/productions", json={"name": "固定规则版本", "product": "书",
        "project_type": "digital_presenter", "presenter_id": "lina"})
    saved = store.get(response.json()["id"])
    saved.presenter_style_snapshot.card_text = "# 项目固定的完整风格卡\n第一条规则。\n末尾规则也保留。"
    saved.presenter_style_snapshot.version = "v99"
    store.update(saved.id, lambda p: setattr(p, "presenter_style_snapshot", saved.presenter_style_snapshot))
    response = client.get(f"/api/productions/presenter-workflow?stage=structure&project_id={saved.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["style_source"] == "project_snapshot"
    card = next(d for d in data["documents"] if d["kind"] == "style_card")
    assert card["content"] == saved.presenter_style_snapshot.card_text
    assert "v99" in card["title"]
    legacy = store.create(ProductionProject(name="旧项目", project_type="digital_presenter", brand=BrandProfile(product="书")))
    assert client.get(f"/api/productions/presenter-workflow?project_id={legacy.id}").json()["style_source"] == "current_catalog"


def test_legacy_projects_pin_the_old_profile_and_voice_before_a_profile_edit(env):
    client, store = env
    legacy = store.create(ProductionProject(name="已有口播", project_type="digital_presenter", brand=BrandProfile(product="书")))
    heygen.save_settings(store, heygen.Settings(api_key=SecretStr("private-test-key"), avatar_id="legacy-avatar", voice_id="legacy-voice"))
    original = client.get("/api/productions/presenters").json()[0]
    response = client.put("/api/productions/presenters/lina", json={**fields(original), "expected_revision": 1, "primary_audience": "新的受众"})
    assert response.status_code == 200
    saved = store.get(legacy.id)
    assert saved.presenter_snapshot.primary_audience == original["primary_audience"]
    assert saved.presenter_snapshot.heygen.voice_id == "legacy-voice"
    assert saved.presenter_style_snapshot.creator_id == "lina"
    assert saved.content_revision == legacy.content_revision
    assert saved.presenter_snapshot.heygen.model_dump().get("api_key") is None


def test_heygen_uses_each_persons_binding_and_not_the_shared_lina_default(env, monkeypatch):
    client, store = env
    heygen.save_settings(store, heygen.Settings(api_key=SecretStr("private-test-key"), avatar_id="lina-avatar", voice_id="lina-voice"))

    async def catalog(settings):
        return {"avatars": [{"id": "own-avatar", "name": "小禾形象", "engine": "avatar_v"}],
                "voices": [{"id": "own-voice", "name": "小禾声音"}]}

    monkeypatch.setattr(api, "heygen_catalog", catalog)
    profile = client.post("/api/productions/presenters", json=config(heygen={"avatar_id": "own-avatar", "voice_id": "own-voice"})).json()
    project = create_video(client, profile).json()
    settings = client.get(f"/api/productions/heygen/settings?project_id={project['id']}").json()
    assert (settings["avatar_id"], settings["voice_id"]) == ("own-avatar", "own-voice")
    assert "api_key" not in settings
    planned = client.post(f"/api/productions/{project['id']}/plan", json={"expected_revision": project["revision"], "mode": "template"}).json()
    chosen = client.post(f"/api/productions/{project['id']}/direction", json={"expected_revision": planned["revision"], "direction_id": "demo"}).json()
    saved = store.get(project["id"])
    assert heygen.eligibility(saved, "S01")
    effective = heygen.project_settings(saved, heygen.load_settings(store))
    job = heygen.build_job(saved, effective)
    assert (job.request["avatar_id"], job.request["voice_id"]) == ("own-avatar", "own-voice")
    assert "private-test-key" not in json.dumps(chosen)
    unbound = client.post("/api/productions/presenters", json=config(name="未绑定数字人")).json()
    unbound_project = create_video(client, unbound).json()
    assert client.get(f"/api/productions/heygen/settings?project_id={unbound_project['id']}").json()["voice_id"] == ""
