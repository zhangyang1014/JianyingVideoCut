"""Selling-video project API; no changes to the single-video Task review contract."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

import httpx

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import Field, SecretStr, ValidationError, model_validator

from ..core.ws import manager
from ..models.production import (AssetSelection, BrandFact, BrandProfile, Contract, CreativeDirection, Cue,
                                 MediaOrigin, MediaOriginKind, NarrationDraft, PresenterConfig, PresenterHeyGen, PresenterProfile, ProductionProject,
                                 ProductionRenderJob, ProductionSettings, ScriptIntent, SourcePage, StoryboardShot,
                                 NARRATION_MAX_LENGTH, WORKFLOW_NOTES_MAX_LENGTH, new_id, now)
from ..services import production_heygen as heygen
from ..services.production_media import import_brief, inspect_asset, parse_srt
from ..services.production_planner import (basic_storyboard_from_narration, inspect_visual, normalize_narration,
                                          plan_with_ai, refresh_instructions, storyboard_from_narration, template_directions,
                                          writing_skill_reference)
from ..services.production_presenters import presenter_catalog, project_presenter, save_presenter
from ..services.creator_styles import load_creator_style
from ..services.production_renderer import render_production
from ..services.production_store import ProductionConflict, production_store as store
from ..services.production_timeline import compile_timeline, shot_issues, signature
from ..services.production_writing_review import draft_fact_warnings
from ..services.llm_router import LlmRoutingError

router = APIRouter(prefix="/api/productions", tags=["卖货视频创作"])
workers: dict[str, asyncio.Task] = {}
analysis_gate = asyncio.Semaphore(1)
HEYGEN_CATALOG_TIMEOUT_SECONDS = 35


def read_presenter_catalog():
    try:
        return presenter_catalog()
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "数字人档案暂时无法读取，请检查档案文件后重试；已有内容未被覆盖") from exc


def analysis_error(exc, *, template_fallback=True):
    if isinstance(exc, LlmRoutingError):
        kinds = {a.error_type for a in exc.attempts}
        if "authentication" in kinds:
            return "模型服务认证失败，请在 AI 配置中检查对应模型服务的凭据；项目资料已保留"
        if "request_error" in kinds:
            return "模型服务未接受请求，请检查所选模型是否支持图片和所需输出长度"
        if not exc.attempts:
            return "没有可用的模型服务，请在 AI 配置中配置卖货视频创意和素材检查角色"
        message = "模型分析未完成，请检查服务连接或稍后重试"
        return f"{message}；可先使用基础框架" if template_fallback else message
    return str(exc)[:500]


def get(project_id):
    try:
        return store.get(project_id)
    except KeyError:
        raise HTTPException(404, "制作项目不存在")


def update(project_id, mutate, revision=None, *, content=True):
    try:
        return store.update(project_id, mutate, revision, content=content)
    except ProductionConflict as exc:
        raise HTTPException(409, str(exc))
    except KeyError:
        raise HTTPException(404, "制作项目或素材不存在")
    except ValueError as exc:
        raise HTTPException(422, str(exc))


def url(project_id, path: str) -> str:
    relative = Path(path).resolve().relative_to(store.directory(project_id).resolve())
    return f"/api/productions/{project_id}/files/{relative.as_posix()}"


def public(project: ProductionProject):
    data = project.model_dump()
    for asset in data["assets"]:
        asset["url"] = url(project.id, asset.pop("path"))
        asset["frames"] = [url(project.id, p) for p in asset["frames"]]
    for source in data["brand"]["sources"]:
        source["preview"] = url(project.id, source["preview"]) if source["preview"] else None
        source["images"] = [url(project.id, p) for p in source["images"]]
    for job in data["renders"]:
        job.pop("snapshot")
        job["outputs"] = {k: url(project.id, v) for k, v in job["outputs"].items()} if job["status"] == "completed" else {}
        job["is_current"] = job["content_revision"] == project.content_revision
    data["heygen_jobs"] = [
        job.model_dump(exclude={"request", "key_fingerprint"}) for job in project.heygen_jobs
    ]
    if project.narration_draft:
        warnings = draft_fact_warnings(project.narration_draft.text, project.brand.facts)
        if warnings:
            data["narration_fact_warnings"] = warnings
    data["readiness"] = {s.shot_id: shot_issues(project, s) for s in project.shots}
    return data


def launch(key, coroutine):
    task = asyncio.create_task(coroutine)
    workers[key] = task
    task.add_done_callback(lambda finished: workers.pop(key, None) if workers.get(key) is finished else None)


async def shutdown():
    tasks = list(workers.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def resume_heygen_jobs():
    for project in store.list():
        for job in project.heygen_jobs:
            if job.status in heygen.ACTIVE:
                launch(f"heygen:{job.id}", heygen.run_job(store, project.id, job))


class CreateProject(ProductionSettings):
    name: str = Field(min_length=1, max_length=120)
    brand_name: str = ""
    product: str = Field(min_length=1, max_length=200)
    platform: str = "抖音"
    selling_points: str = ""
    offer: str = ""
    reuse_brand_project_id: str | None = None
    presenter_overrides: PresenterConfig | None = None
    presenter_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="before")
    @classmethod
    def require_presenter(cls, data):
        if isinstance(data, dict):
            if data.get("project_type") == "digital_presenter" and not data.get("presenter_id"):
                raise ValueError("请选择本次视频关联的数字人")
            if data.get("project_type", "selling_video") != "digital_presenter" and (
                data.get("presenter_overrides") is not None or data.get("presenter_revision") is not None
            ):
                raise ValueError("数字人设定仅用于数字人口播项目")
        return data


class Revision(Contract):
    expected_revision: int = Field(ge=1)


class PresenterUpdate(PresenterConfig):
    expected_revision: int = Field(ge=1)
    archived: bool = False


class ProjectPresenterUpdate(Revision):
    presenter_id: str
    presenter_revision: int | None = Field(default=None, ge=1)
    presenter_overrides: PresenterConfig | None = None


class BriefUpdate(Revision):
    selling_points: str
    offer: str
    style: str
    music_asset_id: str | None = None
    workflow_notes: str | None = Field(default=None, max_length=WORKFLOW_NOTES_MAX_LENGTH)
    target_duration: int | None = Field(default=None, gt=0)
    script_intent: ScriptIntent | None = None


class ChooseDirection(Revision):
    direction_id: str


class PlanRequest(Revision):
    mode: Literal["ai", "template"] = "ai"


class NarrationUpdate(Revision):
    text: str = Field(min_length=1, max_length=NARRATION_MAX_LENGTH)


class ShotUpdate(Revision):
    shot: StoryboardShot


class SelectAsset(Revision):
    selection: AssetSelection
    channel: Literal["video", "voiceover"] = "video"


class ReviewShot(Revision):
    note: str = "用户检查画面、台词与产品展示后标记可用"


class BindAsset(Revision):
    shot_id: str | None


class UpdateAssetOrigin(Revision):
    origin: MediaOrigin


class ImportCues(Revision):
    srt: str = Field(max_length=200000)


class HeyGenCredentials(Contract):
    api_key: SecretStr = SecretStr("")


class HeyGenSetup(HeyGenCredentials):
    avatar_id: str = Field(min_length=1, max_length=256)
    voice_id: str = Field(min_length=1, max_length=256)


class HeyGenBindingUpdate(Revision):
    binding: PresenterHeyGen


def heygen_credentials(body: HeyGenCredentials):
    settings = heygen.load_settings(store)
    supplied = body.api_key.get_secret_value().strip()
    if supplied:
        if settings.public()["managed_key"]:
            raise HTTPException(422, "API Key 已由后端环境变量配置")
        if not re.fullmatch(r"[!-~]{1,512}", supplied):
            raise HTTPException(422, "HeyGen API Key 格式无效")
        settings.api_key = SecretStr(supplied)
    return settings


async def heygen_catalog(settings):
    try:
        # Bound the entire paginated lookup, including all pages and both lists.
        return await asyncio.wait_for(heygen.catalog(settings.key), timeout=HEYGEN_CATALOG_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        raise HTTPException(504, "HeyGen 形象与声音校验超时，请稍后重试；本次档案未保存") from exc
    except httpx.RequestError as exc:
        raise HTTPException(502, "无法连接 HeyGen 校验形象与声音，请检查网络后重试；本次档案未保存") from exc
    except heygen.HeyGenError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/heygen/settings")
def read_heygen_settings(project_id: str | None = None):
    settings = heygen.load_settings(store)
    return (heygen.project_settings(get(project_id), settings) if project_id else settings).public()


@router.post("/heygen/catalog")
async def list_heygen_catalog(body: HeyGenCredentials):
    return await heygen_catalog(heygen_credentials(body))


@router.put("/heygen/settings")
async def configure_heygen(body: HeyGenSetup):
    settings = heygen_credentials(body)
    catalog = await heygen_catalog(settings)
    avatar = next((item for item in catalog["avatars"] if item["id"] == body.avatar_id), None)
    voice = next((item for item in catalog["voices"] if item["id"] == body.voice_id), None)
    if not avatar or not voice:
        raise HTTPException(422, "请选择当前 HeyGen 工作区可用的私人形象和声音")
    settings.avatar_id, settings.avatar_name = avatar["id"], avatar["name"]
    settings.voice_id, settings.voice_name = voice["id"], voice["name"]
    settings.engine = avatar.get("engine", "")
    heygen.save_settings(store, settings)
    return settings.public()


def find_shot(p, shot_id):
    return next((s for s in p.shots if s.shot_id == shot_id), None)


def find_asset(p, asset_id):
    return next((a for a in p.assets if a.id == asset_id), None)


def manual_facts(p):
    previous_page = next((s for s in p.brand.sources if s.filename == "本次填写的商品事实"), None)
    previous_facts = {f.text: f for f in p.brand.facts if previous_page and f.source_id == previous_page.id}
    p.brand.sources = [s for s in p.brand.sources if s.filename != "本次填写的商品事实"]
    valid_sources = {s.id for s in p.brand.sources}
    p.brand.facts = [f for f in p.brand.facts if f.source_id in valid_sources]
    if p.selling_points.strip():
        page = SourcePage(id=previous_page.id if previous_page else new_id(), filename="本次填写的商品事实", text=p.selling_points)
        p.brand.sources.append(page)
        p.brand.facts.extend(previous_facts.get(line) or BrandFact(text=line, source_id=page.id) for line in p.selling_points.splitlines() if line.strip())


@router.get("")
def list_projects():
    return [{"id": p.id, "name": p.name, "product": p.brand.product, "brand_name": p.brand.name,
             "project_type": p.project_type, "presenter_id": p.presenter_id, "workflow_notes": p.workflow_notes,
             "stage": p.stage, "updated_at": p.updated_at, "shot_count": len(p.shots),
             "ready_count": sum(not shot_issues(p, s) for s in p.shots)} for p in store.list()]


@router.post("")
async def create_project(body: CreateProject):
    presenter = None
    if body.project_type == "digital_presenter":
        presenter = await resolve_presenter(body.presenter_id, body.presenter_overrides, body.presenter_revision)
    p = ProductionProject(name=body.name.strip(), brand=BrandProfile(name=body.brand_name, product=body.product),
                          project_type=body.project_type, presenter_id=body.presenter_id, workflow_notes=body.workflow_notes,
                          presenter_snapshot=presenter,
                          presenter_style_snapshot=load_creator_style(presenter.id) if presenter else None,
                          platform=body.platform, target_duration=body.target_duration, script_intent=body.script_intent,
                          selling_points=body.selling_points, offer=body.offer)
    if body.reuse_brand_project_id:
        old = get(body.reuse_brand_project_id)
        p.brand = old.brand.model_copy(deep=True)
        p.brand.product = body.product
        dest = store.directory(p.id) / "brief"
        dest.mkdir(parents=True)
        def copy(path):
            output = dest / f"{new_id()}{Path(path).suffix}"
            shutil.copy2(path, output)
            return str(output)
        for source in p.brand.sources:
            if source.filename == "本次填写的商品事实":
                source.filename = f"品牌复用：{old.name} 填写的商品事实"
            source.images = [copy(path) for path in source.images]
            source.preview = copy(source.preview) if source.preview else None
        # Brand references are reusable; offers and prices remain in the new brief.
        for asset in old.assets:
            if asset.role == "presenter" and p.presenter_id is not None and old.presenter_id != p.presenter_id:
                continue
            if asset.role in {"product", "presenter"}:
                asset = asset.model_copy(deep=True)
                asset.path = copy(asset.path)
                asset.frames = [copy(path) for path in asset.frames]
                p.assets.append(asset)
    manual_facts(p)
    return public(store.create(p))


@router.get("/presenters")
def list_presenters(include_archived: bool = False) -> list[PresenterProfile]:
    return [p for p in read_presenter_catalog().values() if include_archived or not p.archived]


@router.get("/presenter-workflow")
def read_presenter_workflow(stage: Literal["structure", "polish"] = "structure",
                            presenter_id: str | None = Query(default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$"),
                            project_id: str | None = None):
    style, source = None, "current_catalog"
    try:
        if project_id:
            project = get(project_id)
            profile = project_presenter(project)
            if profile is None:
                raise HTTPException(422, "仅数字人口播项目可以查看数字人写稿规则")
            presenter_id = profile.id
            if project.presenter_snapshot is not None:
                style, source = project.presenter_style_snapshot, "project_snapshot"
            else:
                style = load_creator_style(presenter_id)
        elif presenter_id:
            style = load_creator_style(presenter_id)
        data = writing_skill_reference(presenter_id or "", stage, style)
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(503, "完整规则暂时无法读取，请稍后重试") from exc
    data["style_source"] = source
    return data


async def validate_presenter_binding(config: PresenterConfig, previous: PresenterProfile | None = None):
    binding = config.heygen
    if not binding.avatar_id:
        return
    if previous and binding == previous.heygen:
        return
    catalog = await heygen_catalog(heygen.load_settings(store))
    avatar = next((x for x in catalog["avatars"] if x["id"] == binding.avatar_id), None)
    voice = next((x for x in catalog["voices"] if x["id"] == binding.voice_id), None)
    if not avatar or not voice:
        raise HTTPException(422, "请选择当前 HeyGen 工作区可用的私人形象和声音")
    binding.avatar_name, binding.voice_name, binding.engine = avatar["name"], voice["name"], avatar.get("engine", "")


async def resolve_presenter(presenter_id: str, overrides: PresenterConfig | None, revision: int | None):
    profile = read_presenter_catalog().get(presenter_id)
    if profile is None or profile.archived:
        raise HTTPException(422, "所选数字人不存在或已停用，请重新选择")
    if revision is not None and revision != profile.revision:
        raise HTTPException(409, "数字人档案已更新，请重新选择后核对本次设定")
    if overrides is not None:
        await validate_presenter_binding(overrides, profile)
        profile = PresenterProfile(**overrides.model_dump(), id=profile.id, revision=profile.revision)
    return profile


@router.post("/presenters")
async def create_presenter(body: PresenterConfig) -> PresenterProfile:
    await validate_presenter_binding(body)
    try:
        return await asyncio.to_thread(save_presenter, body)
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "数字人档案未能保存，请检查本地档案文件和写入权限后重试") from exc


@router.put("/presenters/{presenter_id}")
async def edit_presenter(presenter_id: str, body: PresenterUpdate) -> PresenterProfile:
    previous = read_presenter_catalog().get(presenter_id)
    if previous is None:
        raise HTTPException(404, "数字人档案不存在")
    if previous.revision != body.expected_revision:
        raise HTTPException(409, "数字人档案已更新，请重新打开后再保存")
    config = PresenterConfig.model_validate(body.model_dump(exclude={"expected_revision", "archived"}))
    await validate_presenter_binding(config, previous)
    def persist():
        # Local project/history IO must not block other requests on the event loop.
        for project in store.list():
            if project.project_type == "digital_presenter" and project.presenter_id == presenter_id and project.presenter_snapshot is None:
                def pin(p):
                    p.presenter_snapshot = previous.model_copy(deep=True)
                    legacy_settings = heygen.project_settings(p.model_copy(update={"presenter_snapshot": None}), heygen.load_settings(store))
                    if not p.presenter_snapshot.heygen.avatar_id and legacy_settings.avatar_id and legacy_settings.voice_id:
                        p.presenter_snapshot.heygen = PresenterHeyGen.model_validate(legacy_settings.model_dump(exclude={"api_key"}))
                    p.presenter_style_snapshot = load_creator_style(presenter_id)
                update(project.id, pin, project.revision, content=False)
        return save_presenter(config, presenter_id=presenter_id, expected_revision=body.expected_revision, archived=body.archived)
    try:
        return await asyncio.to_thread(persist)
    except ProductionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "数字人档案未能保存，请检查本地档案文件和写入权限后重试") from exc


@router.put("/{project_id}/presenter")
async def edit_project_presenter(project_id: str, body: ProjectPresenterUpdate):
    current = get(project_id)
    if current.project_type != "digital_presenter":
        raise HTTPException(422, "仅数字人口播项目可以修改数字人设定")
    if current.planning_status == "running":
        raise HTTPException(409, "请等待本次生成完成后再修改数字人设定")
    if current.presenter_snapshot is not None and current.presenter_id == body.presenter_id:
        profile = current.presenter_snapshot.model_copy(deep=True)
        if body.presenter_overrides is not None:
            await validate_presenter_binding(body.presenter_overrides, profile)
            profile = PresenterProfile(**body.presenter_overrides.model_dump(), id=profile.id,
                                       revision=profile.revision, archived=profile.archived)
    else:
        profile = await resolve_presenter(body.presenter_id, body.presenter_overrides, body.presenter_revision)
    style = (current.presenter_style_snapshot if current.presenter_snapshot and current.presenter_id == profile.id
             else load_creator_style(profile.id))
    def mutate(p):
        p.presenter_id, p.presenter_snapshot, p.presenter_style_snapshot = profile.id, profile, style
        if not p.shots:
            p.directions = []
        p.planning_status, p.planning_note = "idle", "数字人设定已更新；现有口播保持原样，下次生成使用本次设定。"
        for shot in p.shots:
            shot.revision += 1
            shot.verified_signature = None
            shot.review_note = "数字人设定已修改，请核对当前文案、形象和声音"
            refresh_instructions(p, shot)
    return public(update(project_id, mutate, body.expected_revision))


@router.put("/{project_id}/heygen/binding")
async def edit_project_binding(project_id: str, body: HeyGenBindingUpdate):
    current = get(project_id)
    profile = project_presenter(current)
    if profile is None:
        raise HTTPException(422, "请先关联数字人")
    previous = profile.model_copy(deep=True)
    profile.heygen = body.binding
    await validate_presenter_binding(profile, previous)
    def mutate(p):
        p.presenter_snapshot = profile
        if current.presenter_snapshot is None:
            p.presenter_style_snapshot = load_creator_style(profile.id)
        if p.shots:
            p.shots[0].revision += 1
            p.shots[0].verified_signature = None
            p.shots[0].review_note = "本次数字人形象或声音已更换，请核对开场素材"
    return public(update(project_id, mutate, body.expected_revision))


@router.get("/{project_id}")
def read_project(project_id: str):
    return public(get(project_id))


@router.patch("/{project_id}/brief")
def edit_brief(project_id: str, body: BriefUpdate):
    current = get(project_id)
    duration_supplied = "target_duration" in body.model_fields_set
    intent_supplied = "script_intent" in body.model_fields_set
    metadata_only = (body.workflow_notes is not None or duration_supplied or intent_supplied) and (
        body.selling_points, body.offer, body.style, body.music_asset_id
    ) == (current.selling_points, current.offer, current.brand.style, current.music_asset_id)

    def mutate(p):
        if body.workflow_notes is not None:
            p.workflow_notes = body.workflow_notes
        if duration_supplied:
            p.target_duration = body.target_duration
        if intent_supplied:
            p.script_intent = body.script_intent
        if metadata_only:
            return
        p.selling_points, p.offer, p.brand.style = body.selling_points, body.offer, body.style
        if body.music_asset_id and not any(a.id == body.music_asset_id and a.has_audio for a in p.assets):
            raise ValueError("背景音乐不存在或没有音轨")
        p.music_asset_id = body.music_asset_id
        manual_facts(p)
        fact_ids = {fact.id for fact in p.brand.facts}
        for direction in p.directions:
            if direction.writing_review is not None and (
                any(not set(beat.fact_ids) <= fact_ids for beat in direction.writing_review.beats)
                or any(not set(shot.fact_ids) <= fact_ids for shot in direction.shots)
            ):
                direction.writing_review = None
        for shot in p.shots:
            shot.revision += 1
            shot.verified_signature = None
            shot.review_note = "商品事实或风格已修改，请检查受影响文案与素材"
            refresh_instructions(p, shot)
        p.stage = "collecting" if p.shots else "brief"
    return public(update(project_id, mutate, body.expected_revision, content=not metadata_only))


async def save_upload(project_id, file, folder, allowed, limit):
    name = Path((file.filename or "upload").replace("\\", "/")).name
    suffix = Path(name).suffix.lower()
    if suffix not in allowed:
        raise HTTPException(422, f"不支持 {suffix or '无扩展名'} 文件")
    directory = store.directory(project_id) / folder
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{new_id()}{suffix}"
    size = 0
    try:
        with path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, "文件超过上传大小限制")
                out.write(chunk)
        if not size:
            raise HTTPException(422, "文件为空")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    return path, name


@router.post("/{project_id}/brief/files")
async def upload_brief(project_id: str, expected_revision: int = Form(...), file: UploadFile = File(...)):
    get(project_id)
    path, name = await save_upload(project_id, file, "brief", {".pptx", ".txt", ".md"}, 50 * 1024 * 1024)
    try:
        pages, facts, warnings = await asyncio.to_thread(import_brief, path, name)
        def mutate(p):
            p.brand.sources.extend(pages)
            p.brand.facts.extend(facts)
            p.warnings.extend(warnings)
            for shot in p.shots:
                shot.verified_signature = None
                shot.review_note = "品牌资料已补充，请检查分镜与新增事实是否一致"
        return public(update(project_id, mutate))
    except (ValueError, OSError) as exc:
        raise HTTPException(422, f"资料导入失败：{exc}")


@router.post("/{project_id}/plan")
async def plan(project_id: str, body: PlanRequest):
    p = get(project_id)
    if p.narration_draft:
        raise HTTPException(422, "请先处理已保存的口播，再生成创意")
    if p.planning_status == "running":
        return public(p)
    if body.mode == "template":
        def mutate(p):
            p.directions = template_directions(p)
            p.planning_status = "done"
            p.planning_note = "基础创意框架，尚未经 AI 资料分析；可编辑后使用"
            if p.presenter_id == "lina":
                p.planning_note = "已生成 Lina 段落骨架。使用 AI 生成创意可根据资料写稿并润色。"
        return public(update(project_id, mutate, body.expected_revision))
    def running(p):
        p.planning_status = "running"
        p.planning_note = "正在结合资料文字、页面视觉和素材盘点生成创意"
        if p.presenter_id == "lina":
            p.planning_note = "正在根据资料按 Lina 的段落结构写稿，随后润色口播。"
    snapshot = update(project_id, running, body.expected_revision, content=False)
    launch(f"plan:{project_id}", _plan(project_id, snapshot))
    return public(snapshot)


async def _plan(project_id, snapshot):
    try:
        directions, summaries = await plan_with_ai(snapshot)
        def mutate(p):
            if p.script_intent != snapshot.script_intent:
                raise ValueError("生成期间写稿目的已变化，请重新生成创意")
            if (p.narration_draft or p.content_revision != snapshot.content_revision
                    or p.target_duration != snapshot.target_duration):
                raise ValueError("生成期间资料或预计时长已变化，请重新生成创意")
            p.directions = directions
            for source in p.brand.sources:
                source.visual_summary = summaries.get(source.id, source.visual_summary)
            p.planning_status = "done"
            p.planning_note = "AI 创意已生成；请选择一个方向。最多分析前 12 张页面视觉，其余文字均参与规划。"
            if p.presenter_id == "lina":
                p.planning_note = "已按 Lina 的段落结构写稿并完成语气润色。请选择一个方向。最多分析前 12 张页面视觉，其余文字均参与规划。"
        update(project_id, mutate)
    except BaseException as exc:
        def failed(p):
            p.planning_status = "failed"
            if isinstance(exc, asyncio.CancelledError):
                p.planning_note = "创意生成中断，可重试"
                return
            error_kinds = {attempt.error_type for attempt in exc.attempts} if isinstance(exc, LlmRoutingError) else set()
            if (snapshot.presenter_id == "lina" and "contract_error" in error_kinds
                    and not error_kinds.intersection({"authentication", "request_error"})):
                p.planning_note = "模型给出的创意未通过写稿检查。请核对写稿目的、商品事实和收尾动作后重试。"
                return
            p.planning_note = f"创意生成失败：{analysis_error(exc)}"
        update(project_id, failed, content=False)


def _shot_narration(shots: list[StoryboardShot]) -> str:
    return normalize_narration("\n".join(s.spoken_text for s in shots))


@router.put("/{project_id}/narration")
def save_narration(project_id: str, body: NarrationUpdate):
    project = get(project_id)
    if project.project_type != "digital_presenter":
        raise HTTPException(422, "口播稿编辑适用于数字人口播项目")
    if project.revision != body.expected_revision:
        raise HTTPException(409, "项目已更新，请刷新后重新保存口播稿")
    script = normalize_narration(body.text)
    if not script:
        raise HTTPException(422, "请输入口播稿")
    current = _shot_narration(project.shots)
    saved = normalize_narration(project.narration_draft.text) if project.narration_draft else current
    if script == saved:
        return public(project)

    def mutate(p):
        p.narration_draft = None if script == current else NarrationDraft(text=body.text)
        if p.planning_status != "running":
            p.planning_status = "idle"
            p.planning_note = ("口播稿已保存，请重新生成分镜。" if p.narration_draft
                               else "口播稿已恢复到当前分镜。")

    return public(update(project_id, mutate, body.expected_revision, content=False))


@router.post("/{project_id}/narration/storyboard")
async def generate_narration_storyboard(project_id: str, body: Revision):
    project = get(project_id)
    if project.project_type != "digital_presenter" or not project.narration_draft:
        raise HTTPException(422, "请先保存有改动的口播稿")
    if project.planning_status == "running":
        return public(project)

    def running(p):
        p.narration_draft.shots = []
        p.planning_status = "running"
        p.planning_note = "正在按已保存的口播稿生成分镜。"

    snapshot = update(project_id, running, body.expected_revision, content=False)
    launch(f"plan:{project_id}", _plan_narration(project_id, snapshot, project.narration_draft.text))
    return public(snapshot)


async def _plan_narration(project_id: str, snapshot: ProductionProject, text: str):
    shots, failure = [], ""
    note = "新分镜已生成，请预览后应用。"
    try:
        try:
            shots = await storyboard_from_narration(snapshot, text)
        except LlmRoutingError as exc:
            if not exc.attempts or any(attempt.error_type not in {"network", "timeout"} for attempt in exc.attempts):
                raise
            shots = basic_storyboard_from_narration(snapshot, text)
            kinds = {attempt.error_type for attempt in exc.attempts}
            reason = "模型请求超时" if kinds == {"timeout"} else "模型连接失败"
            note = f"{reason}，已按保存的口播稿生成基础分镜。请检查画面和时长后应用。"
        if not shots or _shot_narration(shots) != normalize_narration(text):
            raise ValueError("生成的分镜台词与保存的口播稿不一致")
    except asyncio.CancelledError:
        failure = "分镜生成已取消，可重试。"
    except Exception as exc:
        failure = f"分镜生成失败：{analysis_error(exc, template_fallback=False)}"

    def finish(p):
        if (not p.narration_draft or p.narration_draft.text != text
                or p.content_revision != snapshot.content_revision or p.target_duration != snapshot.target_duration):
            p.planning_status = "idle"
            p.planning_note = ("口播稿或项目内容有更新，请重新生成分镜。" if p.narration_draft
                               else "口播稿已恢复到当前分镜。")
        elif failure:
            p.planning_status = "failed"
            p.planning_note = failure
        else:
            p.narration_draft.shots = shots
            p.planning_status = "done"
            p.planning_note = note

    update(project_id, finish, content=False)


@router.post("/{project_id}/narration/storyboard/apply")
def apply_narration_storyboard(project_id: str, body: Revision):
    def mutate(p):
        draft = p.narration_draft
        if p.project_type != "digital_presenter" or not draft or not draft.shots or p.planning_status == "running":
            raise ValueError("请先生成新分镜")
        if _shot_narration(draft.shots) != normalize_narration(draft.text):
            raise ValueError("分镜已过期，请按保存的口播稿重新生成")
        old_ids = {shot.shot_id for shot in p.shots}
        used_ids = old_ids | {a.shot_id for a in p.assets if a.shot_id} | {job.shot_id for job in p.heygen_jobs}
        used_ids.update(clip.shot_id for job in p.renders for clip in job.timeline.clips)
        numbered_ids = (re.fullmatch(r"S(\d+)", shot_id) for shot_id in used_ids)
        next_id = 1 + max((int(match[1]) for match in numbered_ids if match), default=0)
        for asset in p.assets:
            if asset.shot_id in old_ids:
                asset.shot_id = None
        p.shots = draft.shots
        for index, shot in enumerate(p.shots, next_id):
            shot.shot_id = f"S{index:02}"
            shot.revision = 1
            shot.selection = shot.voiceover = shot.verified_signature = None
            shot.review_note = "分镜已更新，请检查素材、旁白和字幕"
            refresh_instructions(p, shot)
        structure = " → ".join(shot.purpose for shot in p.shots)
        direction = next((d for d in p.directions if d.id == p.selected_direction_id), None)
        if direction is None:
            direction = CreativeDirection(id=new_id(), title="口播分镜", hook="", structure=structure)
            p.directions.append(direction)
        direction.hook = p.shots[0].spoken_text
        direction.structure = structure
        direction.new_shots = f"根据定稿生成 {len(p.shots)} 个镜头"
        direction.shots = [s.model_copy(deep=True) for s in p.shots]
        direction.writing_review = None
        p.selected_direction_id = direction.id
        p.narration_draft = None
        p.stage = "collecting"
        p.planning_status = "done"
        p.planning_note = "已应用新分镜。原素材保留在素材库，可为新镜头选择素材。"

    return public(update(project_id, mutate, body.expected_revision))


@router.post("/{project_id}/direction")
def choose_direction(project_id: str, body: ChooseDirection):
    def mutate(p):
        if p.narration_draft:
            raise ValueError("请先处理已保存的口播，再选择创意方向")
        direction = next((d for d in p.directions if d.id == body.direction_id), None)
        if not direction:
            raise ValueError("创意方向不存在")
        if p.shots and p.selected_direction_id != direction.id:
            raise ValueError("已有分镜的项目请逐镜头修改，避免替换全部分镜导致素材错误绑定")
        if not p.shots:
            p.shots = [s.model_copy(deep=True) for s in direction.shots]
        p.selected_direction_id = direction.id
        p.stage = "collecting"
    return public(update(project_id, mutate, body.expected_revision))


@router.put("/{project_id}/shots/{shot_id}")
def edit_shot(project_id: str, shot_id: str, body: ShotUpdate):
    def mutate(p):
        original = find_shot(p, shot_id)
        if not original or body.shot.shot_id != shot_id:
            raise ValueError("镜头编号不可修改")
        shot = body.shot.model_copy(deep=True)
        allowed_refs = {a.id for a in p.assets} | {source.id for source in p.brand.sources if source.preview or source.images}
        if not set(shot.reference_ids) <= allowed_refs:
            raise ValueError("参考素材不存在")
        shot.selection, shot.voiceover = original.selection, original.voiceover
        shot.revision = original.revision + 1
        shot.verified_signature = None
        shot.review_note = "分镜已修改，提示词已更新，关联素材需重新检查"
        refresh_instructions(p, shot)
        p.shots[p.shots.index(original)] = shot
        if (normalize_narration(shot.spoken_text) != normalize_narration(original.spoken_text)
                or normalize_narration(shot.subtitle) != normalize_narration(original.subtitle)
                or set(shot.fact_ids) != set(original.fact_ids)):
            direction = next((d for d in p.directions if d.id == p.selected_direction_id), None)
            if direction is not None:
                direction.writing_review = None
        p.stage = "collecting"
    return public(update(project_id, mutate, body.expected_revision))


@router.post("/{project_id}/shots/{shot_id}/heygen")
async def generate_heygen(project_id: str, shot_id: str, body: Revision):
    project = get(project_id)
    if project.narration_draft:
        raise HTTPException(422, "请先用已保存的口播稿生成并应用新分镜")
    if not heygen.eligibility(project, shot_id):
        raise HTTPException(422, "请先选择数字人创意，确认首镜头为 HeyGen 数字人口播")
    if not project.shots[0].spoken_text.strip():
        raise HTTPException(422, "请先保存开场台词")
    if any(j.shot_id == shot_id and j.status in heygen.ACTIVE | {"paused"} for j in project.heygen_jobs):
        return public(project)
    settings = heygen.project_settings(project, heygen.load_settings(store))
    if not settings.key or not settings.avatar_id or not settings.voice_id:
        raise HTTPException(422, "请先连接 HeyGen，并为本次数字人选择形象和声音")
    job = heygen.build_job(project, settings)
    updated = update(project_id, lambda p: p.heygen_jobs.append(job), body.expected_revision, content=False)
    launch(f"heygen:{job.id}", heygen.run_job(store, project_id, job))
    return public(updated)


@router.post("/{project_id}/heygen/{job_id}/resume")
async def continue_heygen(project_id: str, job_id: str, body: Revision):
    project = get(project_id)
    job = next((j for j in project.heygen_jobs if j.id == job_id), None)
    if not job:
        raise HTTPException(404, "HeyGen 任务不存在")
    if job.status != "paused" or f"heygen:{job.id}" in workers:
        return public(project)

    def resume(p):
        current = next(j for j in p.heygen_jobs if j.id == job.id)
        current.status = "processing" if current.video_id else "submitting"
        current.error = ""

    updated = update(project_id, resume, body.expected_revision, content=False)
    launch(f"heygen:{job.id}", heygen.run_job(store, project_id, job))
    return public(updated)


@router.post("/{project_id}/assets")
async def upload_asset(project_id: str, file: UploadFile = File(...), expected_revision: int = Form(...),
                       shot_id: str | None = Form(None), role: Literal["footage", "product", "presenter", "voiceover", "music"] = Form("footage"),
                       analyze: bool = Form(True), origin_kind: MediaOriginKind = Form("unknown"),
                       origin_provider: str = Form("", max_length=100)):
    try:
        origin = MediaOrigin(kind=origin_kind, provider=origin_provider)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors()[0]["msg"]) from exc
    p = get(project_id)
    if not shot_id and role == "footage":
        match = re.search(r"(?:^|[^A-Za-z0-9])(S\d{2,})(?:[^0-9]|$)", file.filename or "", re.I)
        shot_id = match[1].upper() if match and find_shot(p, match[1].upper()) else None
    if shot_id and not find_shot(p, shot_id):
        raise HTTPException(422, "指定镜头不存在")
    path, name = await save_upload(project_id, file, "assets", {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".wav", ".mp3", ".m4a", ".aac", ".png", ".jpg", ".jpeg", ".webp"}, 2 * 1024 ** 3)
    try:
        asset = await asyncio.to_thread(inspect_asset, path, name, role, shot_id)
        asset.origin = origin
        auto_analyze = analyze and shot_id and asset.kind in {"video", "audio"} and role != "music"
        if auto_analyze:
            asset.analysis_status, asset.analysis_note = "running", "已保存，等待分析画面与实际语音"
        def mutate(p):
            asset.version = 1 + max((a.version for a in p.assets if a.shot_id == shot_id and a.role == role), default=0)
            p.assets.append(asset)
            if role == "music":
                p.music_asset_id = asset.id
            shot = find_shot(p, shot_id)
            if shot and asset.kind == "video" and not shot.selection:
                shot.selection = AssetSelection(asset_id=asset.id, start=0, end=asset.duration)
            if shot and role == "voiceover" and asset.has_audio and not shot.voiceover:
                shot.voiceover = AssetSelection(asset_id=asset.id, start=0, end=asset.duration)
            p.stage = "collecting" if p.shots else "brief"
        # Append-only registration can safely rebase over concurrent analysis progress.
        updated = update(project_id, mutate)
        if auto_analyze:
            launch(f"analysis:{project_id}:{asset.id}", _analyze(project_id, asset.id, updated))
        return public(updated)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, f"素材登记失败：{exc}")


@router.patch("/{project_id}/assets/{asset_id}/binding")
def bind_asset(project_id: str, asset_id: str, body: BindAsset):
    def mutate(p):
        asset = find_asset(p, asset_id)
        if not asset or (body.shot_id and not find_shot(p, body.shot_id)):
            raise ValueError("素材或镜头不存在")
        asset.shot_id = body.shot_id
    return public(update(project_id, mutate, body.expected_revision))


@router.patch("/{project_id}/assets/{asset_id}/origin")
def update_asset_origin(project_id: str, asset_id: str, body: UpdateAssetOrigin):
    def mutate(p):
        asset = find_asset(p, asset_id)
        if asset is None:
            raise KeyError(asset_id)
        asset.origin = body.origin
    return public(update(project_id, mutate, body.expected_revision, content=False))


@router.post("/{project_id}/shots/{shot_id}/select")
def select_asset(project_id: str, shot_id: str, body: SelectAsset):
    def mutate(p):
        shot, asset = find_shot(p, shot_id), find_asset(p, body.selection.asset_id)
        if not shot or not asset or body.selection.end > asset.duration + .001:
            raise ValueError("镜头或素材不存在，或选段超出素材时长")
        if body.channel == "video" and asset.kind != "video":
            raise ValueError("画面选段必须使用视频")
        if body.channel == "voiceover" and not asset.has_audio:
            raise ValueError("旁白素材没有音轨")
        if body.channel == "video":
            shot.selection = body.selection
        else:
            shot.voiceover = body.selection
        shot.verified_signature = None
        shot.review_note = "选段已变更，请检查此版本"
        p.stage = "collecting"
    return public(update(project_id, mutate, body.expected_revision))


@router.post("/{project_id}/shots/{shot_id}/verify")
def verify_shot(project_id: str, shot_id: str, body: ReviewShot):
    def mutate(p):
        shot = find_shot(p, shot_id)
        if not shot:
            raise ValueError("镜头不存在")
        issues = shot_issues(p, shot, verification=False)
        if issues:
            raise ValueError("；".join(issues))
        shot.verified_signature, shot.review_note = signature(shot), body.note
        p.stage = "ready" if all(not shot_issues(p, s) for s in p.shots if s.required) else "collecting"
    return public(update(project_id, mutate, body.expected_revision))


@router.post("/{project_id}/assets/{asset_id}/subtitles")
def import_subtitles(project_id: str, asset_id: str, body: ImportCues):
    try:
        cues = parse_srt(body.srt)
    except ValueError as exc:
        raise HTTPException(422, "SRT 时间格式错误") from exc
    def mutate(p):
        asset = find_asset(p, asset_id)
        if not asset or not cues or any(c.end > asset.duration + .001 or not c.text for c in cues):
            raise ValueError("SRT 为空或时间超出实际素材时长")
        ordered = sorted(cues, key=lambda c: c.start)
        if any(a.end > b.start for a, b in zip(ordered, ordered[1:])):
            raise ValueError("字幕区间不能重叠")
        asset.cues = ordered
        asset.analysis_note = "已导入与素材对应的实际音频字幕"
        for shot in p.shots:
            if any(s and s.asset_id == asset_id for s in [shot.selection, shot.voiceover]):
                shot.verified_signature = None
    return public(update(project_id, mutate, body.expected_revision))


@router.post("/{project_id}/assets/{asset_id}/analyze")
async def analyze_asset(project_id: str, asset_id: str, body: Revision):
    p = get(project_id)
    asset = find_asset(p, asset_id)
    if not asset:
        raise HTTPException(404, "素材不存在")
    if asset.analysis_status == "running":
        return public(p)
    def running(p):
        a = find_asset(p, asset_id)
        a.analysis_status, a.analysis_note = "running", "正在识别实际语音与画面"
    updated = update(project_id, running, body.expected_revision, content=False)
    launch(f"analysis:{project_id}:{asset_id}", _analyze(project_id, asset_id, updated))
    return public(updated)


async def _analyze(project_id, asset_id, snapshot):
    # Local ASR is expensive; do not load multiple recognition jobs concurrently.
    async with analysis_gate:
        await _analyze_one(project_id, asset_id, snapshot)


async def _analyze_one(project_id, asset_id, snapshot):
    asset = find_asset(snapshot, asset_id).model_copy(deep=True)
    shot = find_shot(snapshot, asset.shot_id)
    original_signature = signature(shot) if shot else None
    try:
        needs_audio = not shot or shot.voice_mode in {"original", "generated"} or asset.role == "voiceover"
        if asset.has_audio and asset.role != "music" and needs_audio:
            from ..models.task import TaskParams
            from ..services.asr_pipeline import run_asr_recognize
            result = await run_asr_recognize(asset.path, TaskParams(), backend="funasr")
            cues, group = [], []
            for word in result.words:
                text = re.sub(r"<[^>]+>", "", word.word)
                if not text.strip():
                    continue
                if group and (word.start - group[-1][1] > .6 or len("".join(w[2] for w in group)) >= 18):
                    cues.append(Cue(start=group[0][0], end=group[-1][1], text="".join(w[2] for w in group)))
                    group = []
                if word.end > word.start:
                    group.append((max(0, word.start), min(asset.duration, word.end), text))
            if group:
                cues.append(Cue(start=group[0][0], end=group[-1][1], text="".join(w[2] for w in group)))
            asset.cues = cues
            if cues:
                asset.usable_start, asset.usable_end = max(0, cues[0].start - .12), min(asset.duration, cues[-1].end + .12)
        visual_asset = asset
        if asset.role == "voiceover" and shot and shot.selection:
            visual_asset = find_asset(snapshot, shot.selection.asset_id).model_copy(deep=True)
            visual_asset.cues = asset.cues
        elif shot and shot.voice_mode == "voiceover" and shot.voiceover:
            voice_asset = find_asset(snapshot, shot.voiceover.asset_id)
            if voice_asset:
                visual_asset = asset.model_copy(deep=True)
                visual_asset.cues = voice_asset.cues
        visual = await inspect_visual(snapshot, visual_asset, shot) if visual_asset.frames else {"matches": False, "reason": "音频已识别，请在镜头中检查声音与画面", "summary": ""}
        def mutate(p):
            current = find_asset(p, asset_id)
            current.cues = asset.cues
            current.usable_start, current.usable_end = asset.usable_start, asset.usable_end
            current.visual_summary = str(visual.get("summary", ""))
            current.analysis_status, current.analysis_note = "done", visual["reason"]
            for linked in p.shots:
                if any(sel and sel.asset_id == asset_id for sel in [linked.selection, linked.voiceover]):
                    linked.verified_signature = None
                    linked.review_note = "素材分析已更新，请检查实际画面与语音"
            s = find_shot(p, asset.shot_id)
            related = s and any(selection and selection.asset_id == asset_id for selection in [s.selection, s.voiceover])
            if related and signature(s) == original_signature and s.selection:
                # Auto-trim only the untouched full-source default, never overwrite a user range.
                if s.voice_mode in {"original", "generated"} and s.selection.asset_id == asset_id and s.selection.start == 0 and s.selection.end == asset.duration and asset.cues:
                    s.selection.start, s.selection.end = asset.usable_start, asset.usable_end
                s.verified_signature = None
                s.review_note = visual["reason"]
                if visual["matches"] and not shot_issues(p, s, verification=False):
                    if p.project_type == "digital_presenter" and s.tool == "HeyGen" and s.voice_mode == "generated":
                        s.review_note = "抽帧与对白检查通过，请播放有声视频，核对发音、克隆声音和口型后标记可用"
                    else:
                        s.verified_signature = signature(s)
        update(project_id, mutate)
    except BaseException as exc:
        def failed(p):
            current = find_asset(p, asset_id)
            # Keep real ASR work even if the visual model fails.
            if asset.cues:
                current.cues = asset.cues
                for linked in p.shots:
                    if any(sel and sel.asset_id == asset_id for sel in [linked.selection, linked.voiceover]):
                        linked.verified_signature = None
            current.analysis_status = "failed"
            current.analysis_note = "分析已中断，可重试" if isinstance(exc, asyncio.CancelledError) else f"分析未完成：{analysis_error(exc)}"
        update(project_id, failed)


@router.post("/{project_id}/renders")
async def request_render(project_id: str, body: Revision):
    p = get(project_id)
    if p.narration_draft:
        raise HTTPException(422, "请先用已保存的口播稿生成并应用新分镜")
    try:
        timeline = compile_timeline(p)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    snapshot = p.model_dump(exclude={"renders", "heygen_jobs", "narration_draft"})
    fingerprint = hashlib.sha256(json.dumps({"revision": p.content_revision, "timeline": timeline.model_dump()}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    previous = next((j for j in p.renders if j.fingerprint == fingerprint), None)
    if previous and previous.status in {"queued", "running", "completed"}:
        return public(p)
    job = previous.model_copy(deep=True) if previous else ProductionRenderJob(fingerprint=fingerprint, content_revision=p.content_revision, snapshot=snapshot, timeline=timeline)
    if previous:
        job.attempt += 1
        job.status, job.error, job.progress, job.outputs = "queued", "", 0, {}
    def mutate(p):
        if previous:
            p.renders[p.renders.index(next(j for j in p.renders if j.id == previous.id))] = job
        else:
            p.renders.append(job)
        p.stage = "rendering"
    updated = update(project_id, mutate, body.expected_revision, content=False)
    launch(f"render:{job.id}", _render(project_id, job))
    return public(updated)


async def _render(project_id, job):
    async def progress(value, step):
        def mutate(p):
            j = next(j for j in p.renders if j.id == job.id)
            if j.status == "cancelled":
                raise asyncio.CancelledError()
            j.status, j.progress, j.step, j.updated_at = "running", value, step, now()
        update(project_id, mutate, content=False)
        await manager.broadcast(project_id, {"type": "production_progress", "job_id": job.id, "progress": value, "step": step})
    try:
        snapshot = ProductionProject.model_validate(job.snapshot)
        directory = store.directory(project_id) / "renders" / job.id / str(job.attempt)
        outputs, checks = await render_production(job.timeline, snapshot.assets, directory, progress)
        def complete(p):
            current = next(j for j in p.renders if j.id == job.id)
            if current.status == "cancelled":
                return
            current.status, current.progress, current.step = "completed", 1, "成片检查通过"
            current.outputs, current.checks, current.updated_at = outputs, checks, now()
            if p.content_revision == job.content_revision:
                p.stage = "completed"
        update(project_id, complete, content=False)
    except BaseException as exc:
        def failed(p):
            current = next(j for j in p.renders if j.id == job.id)
            current.status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
            current.error = "已取消，可重试" if current.status == "cancelled" else str(exc)[:1500]
            current.updated_at = now()
            if p.stage == "rendering":
                p.stage = "collecting"
        update(project_id, failed, content=False)


@router.post("/{project_id}/renders/{job_id}/cancel")
async def cancel_render(project_id: str, job_id: str):
    def mutate(p):
        job = next((j for j in p.renders if j.id == job_id), None)
        if not job:
            raise ValueError("成片请求不存在")
        if job.status in {"queued", "running"}:
            job.status, job.error = "cancelled", "已取消，可重试"
            p.stage = "collecting"
    update(project_id, mutate, content=False)
    worker = workers.get(f"render:{job_id}")
    if worker:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    return public(get(project_id))


def _cached_thumbnail_ready(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 4:
        return False
    with path.open("rb") as image:
        if image.read(2) != b"\xff\xd8":
            return False
        image.seek(-2, 2)
        return image.read(2) == b"\xff\xd9"


@router.get("/{project_id}/assets/{asset_id}/thumbnail")
def asset_thumbnail(project_id: str, asset_id: str, t: float = Query(default=0, ge=0)):
    """Extract an in-range source frame; sync execution keeps FFmpeg off the event loop."""
    project = get(project_id)
    asset = next((a for a in project.assets if a.id == asset_id), None)
    if asset is None:
        raise HTTPException(404, "素材不存在于当前项目")
    if asset.kind != "video":
        raise HTTPException(422, "只有视频素材可以生成选段缩略图")
    if not math.isfinite(t) or t < 0 or t >= asset.duration:
        raise HTTPException(422, "缩略图时间须在视频实际时长以内")
    root = store.directory(project_id).resolve()
    source = Path(asset.path).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise HTTPException(404, "视频文件不存在于当前项目")
    cache_dir = (root / ".thumbnails").resolve()
    if not cache_dir.is_relative_to(root):
        raise HTTPException(404, "缩略图目录不存在于当前项目")
    source_stat = source.stat()
    cache_key = hashlib.sha256(json.dumps([
        asset.id, asset.fingerprint, source_stat.st_size, source_stat.st_mtime_ns, float(t).hex(),
    ]).encode()).hexdigest()
    cached = cache_dir / f"{cache_key}.jpg"
    if not cached.resolve().is_relative_to(root):
        raise HTTPException(404, "缩略图不存在于当前项目")
    if not _cached_thumbnail_ready(cached):
        cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=cache_dir, suffix=".jpg", delete=False) as temporary:
            pending = Path(temporary.name)
        try:
            result = subprocess.run([
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", str(t), "-threads", "1", "-i", str(source), "-frames:v", "1", "-an",
                "-vf", "scale=320:320:force_original_aspect_ratio=decrease",
                "-q:v", "4", "-threads", "1", str(pending),
            ], capture_output=True, timeout=12)
            if result.returncode or not _cached_thumbnail_ready(pending):
                raise HTTPException(422, "无法读取该时间的画面，请检查视频或调整入点")
            pending.replace(cached)
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(504, "生成选段缩略图超时，请稍后重试") from exc
        except FileNotFoundError as exc:
            raise HTTPException(503, "缩略图工具暂不可用，请检查 FFmpeg") from exc
        finally:
            pending.unlink(missing_ok=True)
    return FileResponse(cached, media_type="image/jpeg", headers={"Cache-Control": "private, no-cache"})


@router.get("/{project_id}/files/{relative:path}")
def file_response(project_id: str, relative: str):
    p = get(project_id)
    root = store.directory(project_id).resolve()
    target = (root / relative).resolve()
    allowed = {Path(path).resolve() for a in p.assets for path in [a.path, *a.frames]}
    allowed.update(Path(path).resolve() for s in p.brand.sources for path in [*s.images, *([s.preview] if s.preview else [])])
    allowed.update(Path(path).resolve() for j in p.renders if j.status == "completed" for path in j.outputs.values())
    if not target.is_relative_to(root) or target not in allowed or not target.is_file():
        raise HTTPException(404, "文件不存在或尚未通过检查")
    return FileResponse(target)
