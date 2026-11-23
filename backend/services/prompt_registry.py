"""统一管理可配置的运行时 system prompt。

配置页和模型调用必须通过本模块解析同一个 Prompt，避免 UI 编辑的文件与
运行时实际读取的文件不一致。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = PROJECT_ROOT / "backend" / "prompts"
OVERLAYS_DIR = PROJECT_ROOT / "domains" / "goldenclip" / "overlays"
ACTIVE_PROMPTS_PATH = PROMPTS_DIR / "active_prompt_versions.json"


@dataclass(frozen=True)
class PromptSpec:
    prompt_id: str
    feature: str
    label: str
    description: str
    path: Path
    editable: bool = True
    active_scenario: Optional[str] = None


_SPECS: Dict[str, PromptSpec] = {
    "audit.monologue_clean": PromptSpec(
        "audit.monologue_clean",
        "audit",
        "口播精修",
        "语义审计 · 口播精修场景的 system prompt",
        PROMPTS_DIR / "monologue_clean.md",
        active_scenario="monologue_clean",
    ),
    "audit.interview_compress": PromptSpec(
        "audit.interview_compress",
        "audit",
        "直播访谈压缩",
        "语义审计 · 直播访谈压缩场景的 system prompt",
        PROMPTS_DIR / "interview_compress.md",
        active_scenario="interview_compress",
    ),
    "audit.highlight_reel": PromptSpec(
        "audit.highlight_reel",
        "audit",
        "直播卖货集锦审计",
        "产品识别与片段判定使用的 system prompt；模型继承语义审计配置",
        PROMPTS_DIR / "highlight_reel.md",
        active_scenario="highlight_reel",
    ),
    "audit.coherence": PromptSpec(
        "audit.coherence",
        "audit",
        "句群连贯性复核",
        "审计后检查断句和上下文完整性的 system prompt",
        OVERLAYS_DIR / "sentence_group_optimization.md",
    ),
    "audit.fallback": PromptSpec(
        "audit.fallback",
        "audit",
        "通用审计兜底",
        "任务场景无法识别时使用的通用 system prompt",
        PROMPTS_DIR / "semantic_audit.md",
    ),
    "highlight_reel.validation": PromptSpec(
        "highlight_reel.validation",
        "highlight_reel",
        "产品集锦质检",
        "集锦计划生成后进行独立语义验收的 system prompt",
        OVERLAYS_DIR / "highlight_reel_validation.md",
    ),
    "highlight.occurrence": PromptSpec(
        "highlight.occurrence",
        "highlight_reel",
        "产品场次识别",
        "按时间边界识别产品连续出现的 occurrence",
        PROMPTS_DIR / "product_occurrence.md",
    ),
    "highlight.faithful": PromptSpec(
        "highlight.faithful",
        "highlight_reel",
        "产品场次忠实版",
        "保留产品场次多数有效内容的生成 prompt",
        PROMPTS_DIR / "highlight_faithful.md",
    ),
    "highlight.balanced": PromptSpec(
        "highlight.balanced",
        "highlight_reel",
        "产品场次平衡版",
        "保留产品场次 40%-50% 有效内容的生成 prompt",
        PROMPTS_DIR / "highlight_balanced.md",
    ),
    "highlight.concise": PromptSpec(
        "highlight.concise",
        "highlight_reel",
        "产品场次精简版",
        "保留产品名和核心卖点、最长约 120 秒的生成 prompt",
        PROMPTS_DIR / "highlight_concise.md",
    ),
    "translate.cues": PromptSpec(
        "translate.cues",
        "translate",
        "字幕校准与翻译",
        "保持 cue 与时间轴不变的字幕校准、翻译 system prompt",
        PROMPTS_DIR / "subtitle_translate.md",
    ),
    "golden_quote.monologue_clean": PromptSpec(
        "golden_quote.monologue_clean",
        "golden_quote",
        "口播金句推荐",
        "口播精修场景的开场金句筛选 system prompt",
        PROMPTS_DIR / "golden_quote_monologue.md",
    ),
    "golden_quote.interview_compress": PromptSpec(
        "golden_quote.interview_compress",
        "golden_quote",
        "访谈金句推荐",
        "直播访谈场景的开场金句筛选 system prompt",
        PROMPTS_DIR / "golden_quote_interview.md",
    ),
    "golden_quote.highlight_reel": PromptSpec(
        "golden_quote.highlight_reel",
        "golden_quote",
        "卖货集锦金句推荐",
        "直播卖货集锦场景的产品开场筛选 system prompt",
        PROMPTS_DIR / "golden_quote_highlight.md",
    ),
    "learning.analyze": PromptSpec(
        "learning.analyze",
        "learning",
        "学习分析",
        "对比原片与人工成片并归纳剪辑规律的 system prompt",
        PROMPTS_DIR / "learning_analyze.md",
    ),
    "learning.rewrite": PromptSpec(
        "learning.rewrite",
        "learning",
        "Prompt 重写",
        "根据学习结论生成完整规则变更的 system prompt",
        PROMPTS_DIR / "prompt_rewrite.md",
    ),
}


def _active_versions() -> dict:
    if not ACTIVE_PROMPTS_PATH.exists():
        return {}
    try:
        value = json.loads(ACTIVE_PROMPTS_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def get_prompt_spec(prompt_id: str) -> PromptSpec:
    try:
        return _SPECS[prompt_id]
    except KeyError as exc:
        raise KeyError(f"Unknown prompt: {prompt_id}") from exc


def resolve_prompt_path(prompt_id: str) -> Path:
    """返回 Prompt 当前实际生效的路径，并限制在项目受信目录中。"""
    spec = get_prompt_spec(prompt_id)
    path = spec.path
    if spec.active_scenario:
        active_name = _active_versions().get(spec.active_scenario)
        if active_name:
            candidate = (PROMPTS_DIR / str(active_name)).resolve()
            if candidate.parent == PROMPTS_DIR.resolve() and candidate.exists():
                path = candidate

    resolved = path.resolve()
    trusted_roots = (PROMPTS_DIR.resolve(), OVERLAYS_DIR.resolve())
    if not any(resolved == root or root in resolved.parents for root in trusted_roots):
        raise ValueError(f"Prompt path is outside trusted directories: {resolved}")
    return resolved


def resolve_prompt_content(prompt_id: str) -> str:
    path = resolve_prompt_path(prompt_id)
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def save_prompt_content(prompt_id: str, content: str) -> dict:
    spec = get_prompt_spec(prompt_id)
    if not spec.editable:
        raise PermissionError(f"Prompt is read-only: {prompt_id}")
    path = resolve_prompt_path(prompt_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(path)
    return prompt_payload(prompt_id)


def prompt_payload(prompt_id: str, *, include_content: bool = True) -> dict:
    spec = get_prompt_spec(prompt_id)
    path = resolve_prompt_path(prompt_id)
    payload = {
        "id": spec.prompt_id,
        "feature": spec.feature,
        "label": spec.label,
        "description": spec.description,
        "editable": spec.editable,
        "source": str(path.relative_to(PROJECT_ROOT)),
        "active_version": path.name if spec.active_scenario else None,
        "kind": "system",
    }
    if include_content:
        payload["content"] = path.read_text(encoding="utf-8") if path.exists() else ""
    return payload


def list_prompt_payloads(feature: Optional[str] = None) -> List[dict]:
    return [
        prompt_payload(prompt_id, include_content=False)
        for prompt_id, spec in _SPECS.items()
        if feature is None or spec.feature == feature
    ]


def audit_prompt_id(task_type: Optional[str]) -> str:
    value = str(getattr(task_type, "value", task_type) or "")
    return {
        "monologue_clean": "audit.monologue_clean",
        "interview_compress": "audit.interview_compress",
        "highlight_reel": "audit.highlight_reel",
    }.get(value, "audit.fallback")


def golden_quote_prompt_id(task_type: str) -> str:
    return {
        "interview_compress": "golden_quote.interview_compress",
        "highlight_reel": "golden_quote.highlight_reel",
    }.get(task_type, "golden_quote.monologue_clean")
