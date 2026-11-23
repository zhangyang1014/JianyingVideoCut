"""Reusable presenter profiles shared by the creator and the storyboard planner."""
import json
import os
import threading
from pathlib import Path

from ..models.production import PresenterConfig, PresenterProfile, ProductionProject, new_id
from .production_store import ProductionConflict, atomic_json

CATALOG_PATH = Path(__file__).parents[2] / "domains/goldenclip/presenters.json"
DATA_PATH = Path(os.environ.get("GOLDENCLIP_PRESENTER_PATH", Path(__file__).parents[1] / "data/presenters.json"))
_lock = threading.RLock()


def presenter_catalog() -> dict[str, PresenterProfile]:
    """Seed profiles remain in source; user-managed revisions are stored separately."""
    profiles = [PresenterProfile.model_validate(row) for row in json.loads(CATALOG_PATH.read_text(encoding="utf-8"))]
    catalog = {profile.id: profile for profile in profiles}
    if len(catalog) != len(profiles):
        raise ValueError("数字人配置中存在重复主播编号")
    with _lock:
        if DATA_PATH.exists():
            saved = json.loads(DATA_PATH.read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                raise ValueError("数字人档案文件格式无效")
            for key, row in saved.items():
                profile = PresenterProfile.model_validate(row)
                if key != profile.id:
                    raise ValueError("数字人档案编号不一致")
                catalog[key] = profile
        return catalog


def save_presenter(config: PresenterConfig, *, presenter_id: str | None = None,
                   expected_revision: int | None = None, archived: bool = False) -> PresenterProfile:
    with _lock:
        catalog = presenter_catalog()
        if presenter_id is not None:
            current = catalog[presenter_id]
            if current.revision != expected_revision:
                raise ProductionConflict("数字人档案已更新，请重新打开后再保存；本次修改未覆盖新版本")
        else:
            presenter_id = f"presenter_{new_id()}"
            current = None
        profile = PresenterProfile(**config.model_dump(), id=presenter_id,
                                   revision=current.revision + 1 if current else 1, archived=archived)
        saved = json.loads(DATA_PATH.read_text(encoding="utf-8")) if DATA_PATH.exists() else {}
        saved[presenter_id] = profile.model_dump()
        atomic_json(DATA_PATH, saved)
        return profile


def project_presenter(project: ProductionProject) -> PresenterProfile | None:
    if project.project_type != "digital_presenter":
        return None
    return project.presenter_snapshot or presenter_catalog().get(project.presenter_id)
