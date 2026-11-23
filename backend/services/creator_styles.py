"""Load a creator's versioned writing guidance without upgrading its evidence status."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ..models.production import CreatorStyleSnapshot

STYLE_ROOT = Path(__file__).parents[2] / "domains/goldenclip/creator_styles"


def load_creator_style(creator_id: str, *, root: Path | None = None) -> CreatorStyleSnapshot | None:
    """Missing creators have no inherited style; registered but invalid bundles fail before LLM calls."""
    root = (root or STYLE_ROOT).resolve()
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", creator_id):
        raise ValueError("创作者风格编号无效")
    active = json.loads((root / "active.json").read_text(encoding="utf-8"))
    if not isinstance(active, dict) or any(
        not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", key)
        or not isinstance(value, str) or not re.fullmatch(r"v[1-9][0-9]*", value)
        for key, value in active.items()
    ):
        raise ValueError("创作者风格版本目录无效")
    version = active.get(creator_id)
    if version is None:
        return None
    directory = root / creator_id / version
    paths = [(directory / name).resolve() for name in ("card.md", "evidence.json")]
    if any(not path.is_relative_to(root) for path in paths):
        raise ValueError("创作者风格文件必须位于风格目录内")
    card = paths[0].read_text(encoding="utf-8").strip()
    evidence = json.loads(paths[1].read_text(encoding="utf-8"))
    if not isinstance(evidence, dict) or (evidence.get("creator_id"), evidence.get("version")) != (creator_id, version):
        raise ValueError("风格卡与证据索引的创作者或版本不一致")
    if evidence.get("method_version") != "creator-style-analysis/v1":
        raise ValueError("不支持的创作者风格分析方法版本")
    payload = {
        "creator_id": creator_id,
        "version": version,
        "method_version": evidence["method_version"],
        "evidence_status": evidence.get("evidence_status"),
        "card_text": card,
        "evidence_json": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return CreatorStyleSnapshot(**payload, content_sha256=digest)
