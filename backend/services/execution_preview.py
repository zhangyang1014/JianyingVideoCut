"""Build and validate read-only previews for user-triggered model reruns."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from ..models.app_config import ROUTERHUB_BASE_URL
from ..models.task import Task
from .app_config_store import (
    ResolvedFeatureLlm,
    load_app_config,
    merge_feature_llm_request,
    resolve_feature_llm,
    resolve_role_candidates,
)
from .golden_quote_extractor import _build_golden_quote_user_prompt
from .highlight_strategy_generator import build_manual_highlight_prompt_pack
from .product_occurrence_detector import (
    group_product_occurrences,
    identify_product_occurrences,
)
from .prompt_registry import (
    audit_prompt_id,
    golden_quote_prompt_id,
    prompt_payload,
    resolve_prompt_content,
)
from .semantic_auditor import build_audit_prompt


_TOKEN_SECRET = os.urandom(32)
_TOKEN_TTL_SECONDS = 10 * 60
_SENSITIVE_KEYS = {
    "api_key",
    "openai_api_key",
    "claude_api_key",
    "subtitle_api_key",
    "tts_api_key",
    "preview_token",
}


class ExecutionPreviewError(ValueError):
    """Raised when a preview token is invalid or stale."""


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _sanitize_options(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitize_options(item)
            for key, item in value.items()
            if key not in _SENSITIVE_KEYS and key != "preview_mode"
        }
    if isinstance(value, list):
        return [_sanitize_options(item) for item in value]
    return value


def _prompt_ids(operation: str, task: Task) -> list[str]:
    task_type = task.task_type.value if task.task_type else "monologue_clean"
    if operation == "audit":
        return [audit_prompt_id(task_type), "audit.coherence"]
    if operation in {"highlight_rebuild", "highlight_auto_run"}:
        ids = [
            "highlight.faithful",
            "highlight.balanced",
            "highlight.concise",
            "highlight_reel.validation",
        ]
        if operation == "highlight_auto_run":
            ids = [audit_prompt_id(task_type), "audit.coherence", *ids]
        return ids
    if operation == "golden_quote":
        return [golden_quote_prompt_id(task_type)]
    if operation == "translate_retry":
        return ["translate.cues"]
    return []


def _config_prompt_digest(operation: str, task: Task) -> str:
    cfg = load_app_config().model_dump(mode="json")
    prompts = {
        prompt_id: resolve_prompt_content(prompt_id)
        for prompt_id in _prompt_ids(operation, task)
    }
    return _digest({"config": cfg, "prompts": prompts})


def issue_execution_preview_token(
    task: Task,
    operation: str,
    mode: str,
    options: dict[str, Any],
) -> str:
    payload = {
        "task_id": task.id,
        "task_revision": task.revision,
        "operation": operation,
        "mode": mode,
        "options_digest": _digest(_sanitize_options(options)),
        "config_prompt_digest": _config_prompt_digest(operation, task),
        "expires_at": int(time.time()) + _TOKEN_TTL_SECONDS,
    }
    encoded = base64.urlsafe_b64encode(_json_bytes(payload)).decode("ascii").rstrip("=")
    signature = hmac.new(_TOKEN_SECRET, encoded.encode("ascii"), hashlib.sha256).digest()
    signed = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{encoded}.{signed}"


def validate_execution_preview_token(
    token: Optional[str],
    *,
    task: Task,
    operation: str,
    mode: str,
    options: dict[str, Any],
) -> None:
    """Validate a token when supplied; first-run and legacy requests remain compatible."""
    if not token:
        return
    try:
        encoded, signed = token.split(".", 1)
        expected = base64.urlsafe_b64encode(
            hmac.new(_TOKEN_SECRET, encoded.encode("ascii"), hashlib.sha256).digest()
        ).decode("ascii").rstrip("=")
        if not hmac.compare_digest(signed, expected):
            raise ExecutionPreviewError("执行预览签名无效，请重新打开确认弹窗")
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except ExecutionPreviewError:
        raise
    except Exception as exc:
        raise ExecutionPreviewError("执行预览已损坏，请重新打开确认弹窗") from exc

    if payload.get("expires_at", 0) < int(time.time()):
        raise ExecutionPreviewError("执行预览已过期，请重新确认")
    if payload.get("task_id") != task.id or payload.get("operation") != operation:
        raise ExecutionPreviewError("执行预览与当前任务不匹配，请重新确认")
    if payload.get("mode") != mode:
        raise ExecutionPreviewError("执行方式已变化，请重新确认")
    if payload.get("task_revision") != task.revision:
        raise ExecutionPreviewError("任务内容已变化，请重新确认")
    if payload.get("options_digest") != _digest(_sanitize_options(options)):
        raise ExecutionPreviewError("执行参数已变化，请重新确认")
    if payload.get("config_prompt_digest") != _config_prompt_digest(operation, task):
        raise ExecutionPreviewError("模型配置或提示词已变化，请重新确认")


def _endpoint_details(base_url: Optional[str], suffix: str = "") -> dict[str, Any]:
    if not base_url:
        return {"base_url": None, "port": None, "request_url": None}
    normalized = base_url.rstrip("/")
    parsed = urlparse(normalized)
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80 if parsed.scheme == "http" else None
    return {
        "base_url": normalized,
        "port": port,
        "request_url": f"{normalized}{suffix}",
    }


def _route_payload(
    *,
    role: str,
    feature: str,
    override: Optional[ResolvedFeatureLlm] = None,
    include_escalation: bool = True,
) -> list[dict[str, Any]]:
    if override is not None:
        routes = [{
            "provider": override.provider,
            "provider_profile_id": None,
            "model": override.model,
            "tier": "override",
            "credentials_configured": bool(override.api_key),
            **_endpoint_details(
                override.base_url,
                "/api/chat" if override.provider == "ollama" else "/chat/completions",
            ),
        }]
        return routes

    candidates = resolve_role_candidates(role, include_escalation=include_escalation)
    if candidates:
        return [{
            "provider": candidate.provider,
            "provider_profile_id": candidate.provider_profile_id,
            "model": candidate.model,
            "tier": candidate.tier,
            "temperature": candidate.temperature,
            "max_tokens": candidate.max_tokens,
            "thinking": candidate.thinking,
            "credentials_configured": bool(candidate.api_key),
            **_endpoint_details(
                candidate.base_url,
                "/api/chat" if candidate.provider == "ollama" else "/chat/completions",
            ),
        } for candidate in candidates]

    fallback = resolve_feature_llm(feature)
    return _route_payload(role=role, feature=feature, override=fallback)


def _prompt_view(prompt_id: str, user_prompt: Optional[str] = None, note: Optional[str] = None) -> dict[str, Any]:
    metadata = prompt_payload(prompt_id, include_content=False)
    system_prompt = resolve_prompt_content(prompt_id)
    return {
        "id": prompt_id,
        "label": metadata["label"],
        "source": metadata["source"],
        "active_version": metadata.get("active_version"),
        "digest": _digest(system_prompt),
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "note": note,
    }


def _local_stage(
    stage_id: str,
    label: str,
    model: str,
    *,
    parameters: Optional[dict[str, Any]] = None,
    note: str = "本机进程直接运行，不调用 HTTP 模型服务，也没有 LLM Prompt。",
) -> dict[str, Any]:
    return {
        "id": stage_id,
        "label": label,
        "kind": "local_model",
        "routes": [{
            "provider": "local_process",
            "model": model,
            "tier": "primary",
            "base_url": None,
            "port": None,
            "request_url": None,
            "credentials_configured": True,
        }],
        "parameters": parameters or {},
        "prompts": [],
        "prompt_note": note,
    }


def _llm_stage(
    stage_id: str,
    label: str,
    *,
    role: str,
    feature: str,
    prompts: Iterable[dict[str, Any]],
    parameters: Optional[dict[str, Any]] = None,
    override: Optional[ResolvedFeatureLlm] = None,
) -> dict[str, Any]:
    return {
        "id": stage_id,
        "label": label,
        "kind": "llm",
        "routes": _route_payload(role=role, feature=feature, override=override),
        "parameters": parameters or {},
        "prompts": list(prompts),
    }


def _audit_override(options: dict[str, Any]) -> Optional[ResolvedFeatureLlm]:
    requested_model = (
        options.get("openai_model")
        or options.get("claude_model")
        or options.get("ollama_model")
        or ""
    )
    candidates = resolve_role_candidates("audit.semantic")
    if requested_model and any(candidate.model == requested_model for candidate in candidates):
        return None
    explicit = any(options.get(key) for key in (
        "provider",
        "openai_model",
        "claude_model",
        "ollama_model",
        "ollama_base_url",
        "openai_api_key",
        "claude_api_key",
    ))
    if not explicit:
        return None
    return merge_feature_llm_request(
        "audit",
        provider=options.get("provider"),
        openai_model=options.get("openai_model") or options.get("claude_model"),
        ollama_model=options.get("ollama_model"),
        ollama_base_url=options.get("ollama_base_url"),
        api_key=options.get("openai_api_key") or options.get("claude_api_key"),
    )


def _audit_stages(task: Task, options: dict[str, Any]) -> list[dict[str, Any]]:
    task_type = task.task_type.value if task.task_type else "monologue_clean"
    source_segments = task.audit_segments or (task.asr_result.segments if task.asr_result else [])
    style_mode = options.get("style_mode") or task.params.style_mode
    user_prompt, _ = build_audit_prompt(
        source_segments,
        style_mode=style_mode,
        task_type=task_type,
    )
    cfg = load_app_config()
    stages = [_llm_stage(
        "audit",
        "语义审计",
        role="audit.semantic",
        feature="audit",
        override=_audit_override(options),
        parameters={
            "style_mode": style_mode,
            "segments": len(source_segments),
            "force_rule_engine": bool(options.get("force_rule_engine")),
            "cloud_chunk_segments": cfg.audit_runtime.cloud_chunk_segments,
            "cloud_max_parallel_chunks": cfg.audit_runtime.cloud_max_parallel_chunks,
            "cloud_initial_parallel_chunks": cfg.audit_runtime.cloud_initial_parallel_chunks,
            "cloud_chunk_token_budget": cfg.audit_runtime.cloud_chunk_token_budget,
            "cache": cfg.audit_runtime.enable_incremental_cache,
        },
        prompts=[_prompt_view(
            audit_prompt_id(task_type),
            user_prompt,
            "执行时会按分块拆分用户 Prompt；这里展示完整任务输入。",
        )],
    )]
    if cfg.audit_runtime.coherence_strategy != "local_first":
        stages.append(_llm_stage(
            "audit_coherence",
            "句群连贯性复核",
            role="audit.coherence",
            feature="audit",
            parameters={"strategy": cfg.audit_runtime.coherence_strategy},
            prompts=[_prompt_view("audit.coherence")],
        ))
    return stages


def _highlight_prompt_pack(task: Task) -> Optional[dict[str, Any]]:
    if not task.audit_segments:
        return None
    groups = list(task.product_groups)
    if not groups:
        occurrences = task.product_occurrences or identify_product_occurrences(task.audit_segments)
        groups = group_product_occurrences(occurrences, task.audit_segments)
    if not groups:
        return None
    return build_manual_highlight_prompt_pack(task.audit_segments, groups)


def _highlight_stages(task: Task, options: dict[str, Any]) -> list[dict[str, Any]]:
    cfg = load_app_config()
    pack = _highlight_prompt_pack(task)
    prompts = [
        _prompt_view("highlight.faithful", pack["user_prompt"] if pack else None),
        _prompt_view("highlight.balanced", pack["user_prompt"] if pack else None),
        _prompt_view("highlight.concise", pack["user_prompt"] if pack else None),
    ]
    stages = [_llm_stage(
        "highlight_strategy",
        "产品集锦三策略生成",
        role="highlight.strategy",
        feature="highlight_reel",
        parameters={
            "target_product_count": options.get("target_product_count"),
            "min_reel_duration": options.get("min_reel_duration", 60),
            "max_reel_duration": options.get("max_reel_duration", 300),
            "target_total_duration": options.get("target_total_duration"),
            "cloud_max_parallel_calls": cfg.highlight_runtime.cloud_max_parallel_calls,
            "cache": True,
        },
        prompts=prompts,
    )]
    stages.append(_llm_stage(
        "highlight_validation",
        "产品集锦独立质检",
        role="highlight.validation",
        feature="highlight_reel",
        parameters={"cloud_max_parallel_calls": cfg.highlight_runtime.cloud_max_parallel_calls},
        prompts=[_prompt_view("highlight_reel.validation")],
    ))
    return stages


def _translation_user_prompt(task: Task, video_id: Optional[str], options: dict[str, Any]) -> Optional[str]:
    video = next((item for item in task.translation_videos if item.id == video_id), None)
    if not video or not video.cues:
        return None
    compact = [{
        "id": cue.id,
        "start": cue.start,
        "end": cue.end,
        "text": cue.calibrated_text or cue.source_text,
    } for cue in video.cues]
    return (
        f"原语言: {options.get('source_language', 'zh')}\n"
        f"目标语言: {options.get('target_language', 'en')}\n"
        f"字幕 JSON:\n{json.dumps(compact, ensure_ascii=False)}"
    )


def _translation_resume_stage(task: Task, video_id: Optional[str], force_restart: bool) -> str:
    if force_restart:
        return "asr"
    video = next((item for item in task.translation_videos if item.id == video_id), None)
    if not video:
        return "asr"
    from .one_click_translate import determine_resume_stage

    return determine_resume_stage(video)


def build_execution_preview(
    task: Task,
    *,
    operation: str,
    mode: str,
    options: Optional[dict[str, Any]] = None,
    video_id: Optional[str] = None,
) -> dict[str, Any]:
    options = dict(options or {})
    cfg = load_app_config()
    stages: list[dict[str, Any]] = []
    affected: list[str] = []
    warnings: list[str] = []
    api_path = ""

    if operation == "asr":
        backend = options.get("backend") or "whisper"
        model = "FunASR Paraformer-zh" if backend == "funasr" else f"Whisper {options.get('model') or 'base'}"
        stages.append(_local_stage(
            "asr",
            "ASR 语音识别",
            model,
            parameters={
                "backend": backend,
                "chunk_minutes": cfg.asr.funasr_chunk_minutes if backend == "funasr" else None,
                "parallel_chunks": cfg.asr.funasr_parallel_chunks if backend == "funasr" else None,
            },
        ))
        api_path = f"/api/tasks/{task.id}/asr"
        affected = ["ASR 当前版本", "语义审计结果", "产品集锦", "金句选择", "导出引用"]
    elif operation == "diarize":
        stages.append(_local_stage(
            "diarization",
            "说话人分离",
            "iic/speech_campplus_speaker-diarization_common",
            parameters={"oracle_num": 2 if task.task_type.value == "interview_compress" else None},
        ))
        api_path = f"/api/tasks/{task.id}/asr/diarize"
        affected = ["说话人标注", "文本整理结果", "语义审计结果", "产品集锦", "导出引用"]
    elif operation == "audit":
        stages.extend(_audit_stages(task, options))
        api_path = f"/api/tasks/{task.id}/audit"
        affected = (
            ["现有审计决定与手动编辑", "产品集锦"]
            if mode == "reset"
            else ["新增审计版本", "产品集锦"]
        )
    elif operation in {"highlight_rebuild", "highlight_auto_run"}:
        if operation == "highlight_auto_run" and not task.audit_segments:
            if not (task.asr_result and task.asr_result.words):
                stages.append(_local_stage(
                    "asr",
                    "ASR 语音识别",
                    "FunASR Paraformer-zh",
                    parameters={
                        "chunk_minutes": cfg.asr.funasr_chunk_minutes,
                        "parallel_chunks": cfg.asr.funasr_parallel_chunks,
                    },
                ))
            stages.extend(_audit_stages(task, options))
        elif operation == "highlight_auto_run":
            warnings.append("当前已有审计片段，后端会跳过 ASR 和语义审计，直接更新集锦。")
        stages.extend(_highlight_stages(task, options))
        api_path = (
            f"/api/tasks/{task.id}/highlight-reels/auto-run"
            if operation == "highlight_auto_run"
            else f"/api/tasks/{task.id}/highlight-reels/rebuild"
        )
        affected = ["产品分组", "质检前候选", "质检后产品集锦"]
    elif operation == "golden_quote":
        task_type = task.task_type.value if task.task_type else "monologue_clean"
        requested = options.get("openai_model") or options.get("claude_model")
        candidates = resolve_role_candidates("golden_quote.extract")
        override = None
        if requested and not any(candidate.model == requested for candidate in candidates):
            override = merge_feature_llm_request(
                "golden_quote",
                provider=options.get("provider"),
                openai_model=requested,
                ollama_model=options.get("ollama_model"),
                ollama_base_url=options.get("ollama_base_url"),
                api_key=options.get("openai_api_key") or options.get("claude_api_key"),
            )
        kept = [segment for segment in task.audit_segments if segment.action.value != "delete"]
        stages.append(_llm_stage(
            "golden_quote",
            "金句候选提取",
            role="golden_quote.extract",
            feature="golden_quote",
            override=override,
            parameters={"temperature": 0.2, "max_tokens": 1024, "cache": True},
            prompts=[_prompt_view(
                golden_quote_prompt_id(task_type),
                _build_golden_quote_user_prompt(kept, task_type=task_type),
            )],
        ))
        api_path = f"/api/tasks/{task.id}/golden-quotes/suggest"
        affected = ["当前金句候选"]
    elif operation == "translate_retry":
        force_restart = bool(options.get("force_restart"))
        resume_stage = _translation_resume_stage(task, video_id, force_restart)
        stage_order = ["asr", "translate", "tts", "dub"]
        start_index = stage_order.index(resume_stage)
        if start_index <= 0:
            stages.append(_local_stage(
                "asr",
                "ASR 语音识别",
                "FunASR Paraformer-zh" if options.get("asr_backend", "funasr") == "funasr"
                else f"Whisper {options.get('asr_model', 'base')}",
            ))
        if start_index <= 1:
            subtitle = options.get("subtitle_model") or {}
            subtitle_override = merge_feature_llm_request(
                "translate",
                provider=subtitle.get("provider"),
                openai_model=subtitle.get("model"),
                ollama_model=subtitle.get("model"),
                ollama_base_url=subtitle.get("base_url"),
                base_url=subtitle.get("base_url"),
                api_key=options.get("subtitle_api_key"),
            )
            stages.append(_llm_stage(
                "translate",
                "字幕校准与翻译",
                role="translate.subtitle",
                feature="translate",
                override=subtitle_override,
                parameters={
                    "source_language": options.get("source_language", "zh"),
                    "target_language": options.get("target_language", "en"),
                    "temperature": 0.2,
                    "batch_cues": 40,
                    "cache": True,
                },
                prompts=[_prompt_view(
                    "translate.cues",
                    _translation_user_prompt(task, video_id, options),
                    "执行时按每批最多 40 条 cue 拆分用户 Prompt。",
                )],
            ))
        if start_index <= 2:
            tts = options.get("tts_model") or {}
            tts_base = tts.get("base_url") or resolve_feature_llm("tts").base_url or ROUTERHUB_BASE_URL
            stages.append({
                "id": "tts",
                "label": "TTS 配音",
                "kind": "tts",
                "routes": [{
                    "provider": tts.get("provider") or "openai",
                    "provider_profile_id": None,
                    "model": tts.get("model") or resolve_feature_llm("tts").model,
                    "tier": "primary",
                    "credentials_configured": bool(options.get("tts_api_key") or resolve_feature_llm("tts").api_key),
                    **_endpoint_details(tts_base, "/audio/speech"),
                }],
                "parameters": {"voice": tts.get("voice"), "resume_stage": resume_stage},
                "prompts": [],
                "prompt_note": "TTS 直接合成字幕文本，不使用 LLM Prompt。",
            })
        api_path = f"/api/tasks/{task.id}/one-click-translate/videos/{video_id}/retry"
        affected = ["所选视频从断点后的阶段产物"]
        if force_restart:
            affected = ["所选视频的 ASR、翻译、TTS 与预览产物"]
    else:
        raise ExecutionPreviewError(f"不支持的执行预览类型: {operation}")

    token_options = {**options, "video_id": video_id} if video_id else options
    return {
        "operation": operation,
        "mode": mode,
        "title": {
            "asr": "确认重新识别",
            "diarize": "确认重新分离",
            "audit": "确认重新审计",
            "highlight_rebuild": "确认更新产品集锦",
            "highlight_auto_run": "确认完整重新分析",
            "golden_quote": "确认重新分析金句",
            "translate_retry": "确认重试翻译视频",
        }[operation],
        "api_path": api_path,
        "task_revision": task.revision,
        "stages": stages,
        "affected_artifacts": affected,
        "warnings": warnings,
        "preview_token": issue_execution_preview_token(task, operation, mode, token_options),
        "expires_in_seconds": _TOKEN_TTL_SECONDS,
    }
