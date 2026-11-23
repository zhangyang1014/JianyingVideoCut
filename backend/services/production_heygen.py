"""HeyGen direct avatar video API for the Lina production workbench."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from pydantic import Field, SecretStr

from ..models.production import AssetSelection, Contract, HeyGenJob, MediaOrigin, ProductionAsset, ProductionProject
from .production_media import inspect_asset, parse_srt
from .production_store import ProductionStore, atomic_json

API_URL = "https://api.heygen.com/v3/"
ACTIVE = {"submitting", "processing", "importing"}
POLL_SECONDS = 15


class Settings(Contract):
    api_key: SecretStr = SecretStr("")
    avatar_id: str = Field(default="", max_length=256)
    voice_id: str = Field(default="", max_length=256)
    avatar_name: str = ""
    voice_name: str = ""
    engine: str = ""

    @property
    def key(self) -> str:
        return os.environ.get("HEYGEN_API_KEY", "").strip() or self.api_key.get_secret_value()

    def public(self) -> dict:
        return {**self.model_dump(exclude={"api_key"}), "has_key": bool(self.key),
                "managed_key": bool(os.environ.get("HEYGEN_API_KEY", "").strip())}


def load_settings(store: ProductionStore) -> Settings:
    path = store.root / "heygen-settings.json"
    return Settings.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else Settings()


def save_settings(store: ProductionStore, settings: Settings):
    # atomic_json uses a private (0600) temporary file; rename preserves its permissions.
    atomic_json(store.root / "heygen-settings.json",
                {**settings.model_dump(exclude={"api_key"}), "api_key": settings.api_key.get_secret_value()})


def project_settings(project: ProductionProject, settings: Settings) -> Settings:
    settings = settings.model_copy(deep=True)
    if project.presenter_snapshot is not None:
        binding = project.presenter_snapshot.heygen
        for key in ("avatar_id", "avatar_name", "voice_id", "voice_name", "engine"):
            setattr(settings, key, getattr(binding, key))
    elif project.presenter_id != "lina":
        settings.avatar_id = settings.voice_id = settings.avatar_name = settings.voice_name = settings.engine = ""
    return settings


class HeyGenError(Exception):
    def __init__(self, message: str, *, unclear=False, transient=False, retry_after=POLL_SECONDS):
        super().__init__(message)
        self.unclear = unclear
        self.transient = transient
        self.retry_after = retry_after


async def api(client: httpx.AsyncClient, key: str, method: str, path: str, *, job_id=None, **kwargs) -> dict:
    headers = {"X-Api-Key": key}
    if job_id:
        headers["Idempotency-Key"] = job_id
    try:
        response = await client.request(method, API_URL + path, headers=headers, **kwargs)
    except httpx.RequestError as exc:
        raise HeyGenError("无法连接 HeyGen，请检查网络后重试", unclear=True, transient=True) from exc
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if response.is_success and isinstance(payload, dict) and "data" in payload:
        return payload
    error = payload.get("error") if isinstance(payload, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    safe_code = f" · {code}" if isinstance(code, str) and re.fullmatch(r"[a-zA-Z0-9_]{1,80}", code) else ""
    status = response.status_code
    message = {
        401: "HeyGen API Key 无效或过期",
        402: "HeyGen API 额度不足或当前方案不支持",
        403: "HeyGen API Key 缺少此操作的权限",
        404: "HeyGen 中找不到指定资源，请核对账户和素材",
        429: "HeyGen 请求频率达到上限，请稍后重试",
    }.get(status, f"HeyGen 返回异常（HTTP {status}）")
    unclear = response.is_success or status >= 500 or status == 429 or code == "request_in_progress"
    retry_after = response.headers.get("Retry-After", "")
    raise HeyGenError(message + safe_code, unclear=unclear, transient=unclear,
                      retry_after=max(POLL_SECONDS, int(retry_after[:8])) if retry_after.isascii() and retry_after.isdecimal()
                      else POLL_SECONDS)


async def catalog(key: str) -> dict[str, list[dict[str, str]]]:
    if not key:
        raise HeyGenError("请先填写 HeyGen API Key")

    async def options(client, path, params, id_field, ready_status):
        result = []
        tokens = set()
        while True:
            page = await api(client, key, "GET", path, params=params)
            if not isinstance(page["data"], list):
                raise HeyGenError("HeyGen 素材列表格式异常")
            for item in page["data"]:
                if not isinstance(item, dict):
                    continue
                item_id = item.get(id_field)
                if not isinstance(item_id, str) or not item_id or str(item.get("status")).lower() != ready_status:
                    continue
                option = {"id": item_id, "name": item.get("name") or item_id}
                if id_field == "id":
                    supported = item.get("supported_api_engines") or []
                    option["engine"] = next((engine for engine in ["avatar_iv", "heygen-avatar-6", "heygen-avatar-6-turbo", "avatar_v", "avatar_iii"]
                                             if engine in supported), "")
                elif item.get("mode") not in {"instant", "professional"}:
                    continue
                result.append(option)
            token = page.get("next_token")
            if not page.get("has_more"):
                return result
            if not token or token in tokens:
                raise HeyGenError("HeyGen 素材列表分页异常")
            tokens.add(token)
            params = {**params, "token": token}

    async with httpx.AsyncClient(timeout=25) as client:
        avatars, voices = await asyncio.gather(
            options(client, "avatars/looks", {"ownership": "private", "limit": 50}, "id", "completed"),
            options(client, "models/audio/voices", {"type": "private", "limit": 50}, "voice_id", "active"),
        )
    return {"avatars": avatars, "voices": voices}


def eligibility(project: ProductionProject, shot_id: str) -> bool:
    return bool(
        project.project_type == "digital_presenter" and project.presenter_id is not None
        and project.selected_direction_id and project.shots and project.shots[0].shot_id == shot_id
        and project.shots[0].source_type == "aigc" and project.shots[0].voice_mode == "generated"
        and project.shots[0].tool.strip().casefold() == "heygen"
    )


def build_job(project: ProductionProject, settings: Settings) -> HeyGenJob:
    shot = project.shots[0]
    request = {"type": "avatar", "avatar_id": settings.avatar_id, "voice_id": settings.voice_id,
               "script": shot.spoken_text.strip(), "title": f"{project.name} · {shot.shot_id}",
               "aspect_ratio": "9:16", "resolution": "1080p", "caption": {"file_format": "srt"}}
    if settings.engine and settings.engine != "avatar_iv":
        request["engine"] = {"type": settings.engine}
    return HeyGenJob(
        shot_id=shot.shot_id,
        shot_revision=shot.revision,
        key_fingerprint=sha256(settings.key.encode()).hexdigest(),
        request=request,
    )


async def download(client: httpx.AsyncClient, url: str, path: Path, limit: int):
    parts = urlsplit(url)
    host = parts.hostname or ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if (parts.scheme != "https" or "." not in host or parts.username or parts.password
            or parts.port not in {None, 443} or (address and not address.is_global)
            or host.endswith((".localhost", ".local", ".internal"))):
        raise HeyGenError("HeyGen 返回了无效的素材下载地址")
    try:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            size = 0
            with path.open("wb") as output:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise HeyGenError("HeyGen 素材超过下载大小限制")
                    output.write(chunk)
            if not size:
                raise HeyGenError("HeyGen 返回了空素材")
    except httpx.HTTPError as exc:
        raise HeyGenError("HeyGen 素材下载未完成，可继续取回原任务") from exc


async def import_asset(client: httpx.AsyncClient, directory: Path, job: HeyGenJob, data: dict) -> ProductionAsset:
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"heygen-{job.id}"
    path = directory / f"{stem}.mp4"
    part = directory / f"{stem}.part"
    subtitles = directory / f"{stem}.srt"
    if not data.get("video_url"):
        raise HeyGenError("HeyGen 尚未返回成片下载地址，可继续取回原任务")
    try:
        if not path.exists():
            await download(client, data["video_url"], part, 1024 ** 3)
            part.replace(path)
        asset = await asyncio.to_thread(inspect_asset, path, f"{job.shot_id}-HeyGen.mp4", "footage", None)
        if asset.kind != "video" or not asset.has_audio:
            raise HeyGenError("HeyGen 成片缺少视频或音轨")
    except (ValueError, HeyGenError, OSError):
        path.unlink(missing_ok=True)
        for frame in directory.glob(f"{stem}-frame-*.jpg"):
            frame.unlink(missing_ok=True)
        raise
    finally:
        part.unlink(missing_ok=True)
    asset.origin = MediaOrigin(kind="aigc", provider="HeyGen")
    asset.analysis_note = "请播放成片，核对克隆声音、发音和口型"
    if data.get("subtitle_url"):
        try:
            await download(client, data["subtitle_url"], subtitles, 200_000)
            cues = sorted(parse_srt(subtitles.read_text(encoding="utf-8-sig")), key=lambda cue: cue.start)
            asset.cues = [cue for cue in cues if cue.text and cue.start < asset.duration and cue.end <= asset.duration + .25]
            for cue in asset.cues:
                cue.end = min(cue.end, asset.duration)
        except (ValueError, OSError, HeyGenError):
            asset.cues = []
        finally:
            subtitles.unlink(missing_ok=True)
    if not asset.cues:
        asset.analysis_note += "。字幕待补充，可分析实际语音或导入 SRT"
    return asset


async def run_job(store: ProductionStore, project_id: str, job: HeyGenJob):
    def update_job(mutate: Callable, *, content=False):
        return store.update(
            project_id, lambda p: mutate(p, next(j for j in p.heygen_jobs if j.id == job.id)), content=content,
        )

    def set_status(status, error="", video_id=None):
        def mutate(_p, current):
            current.status, current.error = status, error
            if video_id:
                current.video_id = video_id
        update_job(mutate)

    try:
        settings = load_settings(store)
        if not settings.key:
            raise HeyGenError("请配置原 HeyGen 账户的 API Key 后继续", unclear=True)
        async with httpx.AsyncClient(timeout=30) as client:
            video_id = job.video_id
            if not video_id:
                if sha256(settings.key.encode()).hexdigest() != job.key_fingerprint:
                    raise HeyGenError("提交状态待确认，请用提交时的 HeyGen API Key 继续", unclear=True)
                if datetime.now(timezone.utc) - datetime.fromisoformat(job.created_at) >= timedelta(hours=23, minutes=55):
                    raise HeyGenError("已超出安全重试时间，请到 HeyGen 后台核对并回传成片", unclear=True)
                created = (await api(client, settings.key, "POST", "videos", job_id=job.id, json=job.request))["data"]
                video_id = created.get("video_id") if isinstance(created, dict) else None
                if not isinstance(video_id, str) or not video_id:
                    raise HeyGenError("HeyGen 未返回任务编号，可继续查询同一次提交", unclear=True)
                set_status("processing", video_id=video_id)
                job.video_id = video_id

            # A known video ID is always polled or reimported, never resubmitted.
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 30 * 60
            while True:
                interval = POLL_SECONDS
                try:
                    data = (await api(client, settings.key, "GET", f"videos/{quote(video_id, safe='')}"))["data"]
                except HeyGenError as exc:
                    if not exc.transient or loop.time() + exc.retry_after > deadline:
                        raise
                    interval = exc.retry_after
                else:
                    status = data.get("status") if isinstance(data, dict) else None
                    if status == "failed":
                        code = data.get("failure_code")
                        detail = f" · {code}" if isinstance(code, str) and re.fullmatch(r"[a-zA-Z0-9_]{1,80}", code) else ""
                        set_status("failed", "HeyGen 生成失败" + detail)
                        return
                    if status == "completed":
                        break
                    if status not in {"pending", "processing", "waiting"}:
                        raise HeyGenError("HeyGen 返回了未知进度，可继续查询原任务")
                if loop.time() + interval > deadline:
                    raise HeyGenError("HeyGen 仍在处理，可继续查询原任务")
                await asyncio.sleep(interval)

            if job.status != "importing":
                set_status("importing")
            asset = await import_asset(client, store.directory(project_id) / "assets", job, data)

        def complete(project, current):
            matching_shot = eligibility(project, job.shot_id) and project.shots[0].revision == job.shot_revision
            if matching_shot:
                shot = project.shots[0]
                asset.shot_id = shot.shot_id
                if not shot.selection:
                    shot.selection = AssetSelection(asset_id=asset.id, start=0, end=asset.duration)
                    shot.verified_signature = None
                    shot.review_note = asset.analysis_note
                    project.stage = "collecting"
            asset.version = 1 + max(
                (a.version for a in project.assets if a.shot_id == asset.shot_id and a.role == asset.role), default=0,
            )
            project.assets.append(asset)
            current.status, current.asset_id, current.error = "completed", asset.id, ""

        update_job(complete, content=True)
    except HeyGenError as exc:
        set_status("paused" if job.video_id or exc.unclear else "failed", str(exc))
    except Exception:
        set_status("paused", "HeyGen 任务暂时中断，可继续原任务")
