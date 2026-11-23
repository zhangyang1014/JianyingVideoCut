"""Atomic project persistence with compare-and-swap and restart recovery."""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Callable

from ..models.production import ProductionProject, now

ROOT = Path(os.environ.get("GOLDENCLIP_PRODUCTION_ROOT", Path(__file__).parents[1] / "data" / "productions"))


class ProductionConflict(ValueError):
    pass


def atomic_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as f:
        tmp = Path(f.name)
        try:
            json.dump(payload, f, ensure_ascii=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _project_file_payload(project: ProductionProject) -> dict:
    """Keep newly added defaults optional on disk for older local readers."""
    payload = project.model_dump()
    if not payload["heygen_jobs"]:
        del payload["heygen_jobs"]
    if payload["narration_draft"] is None:
        del payload["narration_draft"]
    if payload["script_intent"] is None:
        del payload["script_intent"]
    for direction in payload["directions"]:
        if direction["style_snapshot"] is None:
            del direction["style_snapshot"]
        if direction["presenter_snapshot"] is None:
            del direction["presenter_snapshot"]
        if direction["writing_review"] is None:
            del direction["writing_review"]
    for asset in payload["assets"]:
        if asset["origin"]["kind"] == "unknown":
            del asset["origin"]
    return payload


class ProductionStore:
    def __init__(self, root: Path = ROOT):
        self.root = Path(root)
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)

    def directory(self, project_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", project_id):
            raise KeyError(project_id)
        return self.root / project_id

    def get(self, project_id: str) -> ProductionProject:
        with self._lock:
            path = self.directory(project_id) / "project.json"
            if not path.exists():
                raise KeyError(project_id)
            return ProductionProject.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> list[ProductionProject]:
        projects = []
        for path in self.root.glob("*/project.json"):
            try:
                projects.append(self.get(path.parent.name))
            except (ValueError, KeyError):
                logging.exception("无法读取制作项目 %s", path)
        return sorted(projects, key=lambda p: p.updated_at, reverse=True)

    def create(self, project: ProductionProject) -> ProductionProject:
        with self._lock:
            path = self.directory(project.id) / "project.json"
            if path.exists():
                raise ProductionConflict("项目已存在")
            atomic_json(path, _project_file_payload(project))
            return project.model_copy(deep=True)

    def update(self, project_id: str, mutate: Callable, expected_revision: int | None = None, *, content=True):
        with self._lock:
            project = self.get(project_id)
            if expected_revision is not None and project.revision != expected_revision:
                raise ProductionConflict("项目已更新，请刷新后重试；本次修改未覆盖新版本")
            previous = _project_file_payload(project)
            mutate(project)
            project.revision += 1
            if content:
                project.content_revision += 1
                if project.stage in {"completed", "rendering"}:
                    project.stage = "collecting" if project.shots else "brief"
            if (project.narration_draft and project.narration_draft.shots
                    and (content or project.target_duration != previous["target_duration"])):
                project.narration_draft.shots = []
                project.planning_status = "idle"
                project.planning_note = "项目内容或预计时长已更新，请按保存的口播重新生成分镜。"
            project.updated_at = now()
            # Validate again after mutation, before publishing.
            project = ProductionProject.model_validate(project.model_dump())
            if content:
                atomic_json(self.directory(project_id) / "history" / f"{previous['content_revision']}.json", previous)
            atomic_json(self.directory(project_id) / "project.json", _project_file_payload(project))
            return project

    def recover(self):
        for project in self.list():
            def interrupted(p):
                for job in p.renders:
                    if job.status in {"queued", "running"}:
                        job.status = "failed"
                        job.error = "服务重启导致剪辑中断，可重试当前成片请求"
                        job.updated_at = now()
                for asset in p.assets:
                    if asset.analysis_status == "running":
                        asset.analysis_status = "failed"
                        asset.analysis_note = "服务重启，素材已保留，可重新分析"
                if p.planning_status == "running":
                    p.planning_status = "failed"
                    p.planning_note = "服务重启，可重新生成创意"
                if p.stage == "rendering":
                    p.stage = "collecting"
            if (any(j.status in {"queued", "running"} for j in project.renders)
                    or any(a.analysis_status == "running" for a in project.assets)
                    or project.planning_status == "running"):
                self.update(project.id, interrupted, content=False)


production_store = ProductionStore()
