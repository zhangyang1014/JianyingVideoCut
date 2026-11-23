"""AI 审计后台流水线编排。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, Optional

from ..agent_runtime.contracts import validate_audit_coherence_contract, validate_audit_segments_contract
from ..agent_runtime.harness import ContractVerificationError, verify_contract_report
from ..agent_runtime.hooks import emit_agent_hook, emit_tool_hook
from ..models.app_config import ROUTERHUB_BASE_URL, ResolvedFeatureLlm
from ..models.task import AuditRequest, AuditSnapshot, HighlightReelBuildOptions, SegmentAction, TaskStatus, TaskType
from ..services.app_config_store import (
    load_app_config,
    merge_feature_llm_request,
    resolve_role_candidates,
    role_primary_or_feature,
)
from ..services.audit_coherence import run_audit_coherence_review
from ..services.highlight_reel_pipeline import HighlightPipelineError, refresh_highlight_plan
from ..services.semantic_auditor import apply_segment_corrections, apply_sentence_group_guard, run_semantic_audit
from ..services.task_store import get_store

LogCallback = Callable[..., Awaitable[None]]


@dataclass
class AuditPipelineResult:
    runtime_kind: str
    runtime_model: str
    elapsed_seconds: float
    reels_count: int = 0
    quality_passed: Optional[bool] = None


class AuditPipelineError(RuntimeError):
    """审计流水线失败，携带可供前端展示和 fallback 的错误分类。"""

    def __init__(
        self,
        message: str,
        *,
        error_type: str,
        runtime_model: str = "",
        allow_rule_engine_fallback: bool = False,
    ):
        super().__init__(message)
        self.error_type = error_type
        self.runtime_model = runtime_model
        self.allow_rule_engine_fallback = allow_rule_engine_fallback


def format_elapsed(seconds: float) -> str:
    if seconds >= 60:
        minutes = int(seconds // 60)
        remainder = seconds - minutes * 60
        return f"{minutes}分{remainder:.1f}秒"
    return f"{seconds:.1f}秒"


def _build_reset_audit_source(task):
    """构造重置后的审计输入副本，不提前破坏当前可审核结果。"""
    subtitle_overrides = {
        segment.id: {
            "subtitle_text": segment.subtitle_text,
            "subtitle_text_source": segment.subtitle_text_source,
            "subtitle_updated_at": segment.subtitle_updated_at,
        }
        for segment in task.audit_segments
        if segment.subtitle_text is not None
    }
    current_snapshot = None
    if task.asr_current_version is not None:
        current_snapshot = next(
            (snapshot for snapshot in task.asr_history if snapshot.version == task.asr_current_version),
            None,
        )
    source_segments = (
        current_snapshot.asr_result.segments
        if current_snapshot
        else task.asr_result.segments
        if task.asr_result
        else task.audit_segments
    )
    prepared = [segment.model_copy(deep=True) for segment in source_segments]
    for segment in prepared:
        segment.action = SegmentAction.KEEP
        segment.word_deletions = []
        segment.reason = None
        segment.rule = None
        segment.user_override = False
        segment.manual_action = None
        segment.claude_action = None
        segment.claude_reason = None
        segment.style = None
        segment.clip_group = None
        segment.clip_title = None
        segment.display_text = None
        segment.fix_type = None
        segment.split_before = None
        segment.split_part1_action = SegmentAction.KEEP
        segment.split_part2_action = SegmentAction.KEEP
        segment.subtitle_line_break = None
        segment.merged = False
        segment.semantic_unit_id = None
        segment.product_semantic = None
        override = subtitle_overrides.get(segment.id)
        if override:
            segment.subtitle_text = override["subtitle_text"]
            segment.subtitle_text_source = override["subtitle_text_source"]
            segment.subtitle_updated_at = override["subtitle_updated_at"]
    return prepared


def _commit_reset_metadata(task) -> None:
    """新审计结果通过模型、契约和连贯性复核后才清除旧版本元数据。"""
    task.audit_json_path = None
    task.audit_history = []
    task.audit_current_version = None
    task.audit_coherence_report = None
    task.highlight_reels = []
    task.highlight_reels_before_quality = []
    task.highlight_quality_report = None
    task.highlight_quality_report_before_quality = None
    task.product_occurrences = []
    task.product_groups = []
    task.highlight_strategy_quality_results = []


def audit_runtime_desc(feat, force_rule_engine: bool) -> tuple[str, str, Optional[str]]:
    if force_rule_engine:
        return "本地规则引擎", "无", None
    if feat.provider == "ollama" or feat.mode == "local":
        return "本地 Ollama", feat.model, feat.base_url
    return "云端 OpenAI-compatible", feat.model, feat.base_url


async def stage_log(
    log_callback: Optional[LogCallback],
    level: str,
    source: str,
    message: str,
    progress: Optional[float] = None,
    *,
    stage: str,
    error_type: Optional[str] = None,
    **extra,
) -> None:
    if not log_callback:
        return
    try:
        await log_callback(level, source, message, progress, stage=stage, error_type=error_type, **extra)
    except TypeError:
        await log_callback(level, source, message, progress)


async def run_audit_pipeline(
    task_id: str,
    req: AuditRequest,
    *,
    log_callback: Optional[LogCallback] = None,
    include_highlight_plan: bool = True,
    highlight_build_options: Optional[HighlightReelBuildOptions] = None,
    store=None,
    semantic_audit_runner=None,
) -> AuditPipelineResult:
    """运行语义审计；需要时可在 highlight_reel 任务中继续生成产品集锦计划。"""
    store = store or get_store()
    semantic_audit_runner = semantic_audit_runner or run_semantic_audit
    task = store.get(task_id)
    if not task:
        raise AuditPipelineError("Task not found", error_type="task_missing")

    audit_source_segments = (
        _build_reset_audit_source(task)
        if req.reset_prior_audit
        else [segment.model_copy(deep=True) for segment in task.audit_segments]
    )
    task.status = TaskStatus.AUDIT_RUNNING
    store.update(task)

    audit_started_at = time.perf_counter()
    explicit_model_override = bool(
        req.provider
        or req.openai_model
        or req.claude_model
        or req.ollama_model
        or req.ollama_base_url
        or req.openai_api_key
        or req.claude_api_key
    )
    configured_audit_candidates = resolve_role_candidates("audit.semantic")
    requested_model = req.openai_model or req.claude_model or req.ollama_model
    if requested_model and any(
        candidate.model == requested_model for candidate in configured_audit_candidates
    ):
        explicit_model_override = False
    feat = (
        merge_feature_llm_request(
            "audit",
            provider=req.provider or None,
            openai_model=req.openai_model or req.claude_model or None,
            ollama_model=req.ollama_model or None,
            ollama_base_url=req.ollama_base_url or None,
            api_key=req.openai_api_key or req.claude_api_key,
        )
        if explicit_model_override
        else role_primary_or_feature("audit.semantic", "audit")
    )
    audit_provider = "ollama" if feat.provider == "ollama" else "openai"
    runtime_kind, runtime_model, runtime_endpoint = audit_runtime_desc(feat, req.force_rule_engine)
    task_type_label = task.task_type.value if task.task_type else "unknown"
    endpoint_part = f" | 端点: {runtime_endpoint}" if runtime_endpoint else ""

    await stage_log(
        log_callback,
        "info",
        "audit",
        f"语义审计启动 | 调用: {runtime_kind} | 模型: {runtime_model} | 场景: {task_type_label} | "
        f"风格: {req.style_mode} | 片段: {len(audit_source_segments)}{endpoint_part}",
        0.02,
        stage="audit_start",
        model=runtime_model,
        provider=audit_provider,
    )
    emit_agent_hook(
        task_id,
        "before_agent_run",
        "orchestrate.clip_pipeline",
        payload={"stage": "audit", "task_type": task.task_type.value if task.task_type else None},
    )
    emit_agent_hook(
        task_id,
        "before_model_call",
        "audit.segments",
        payload={"provider": audit_provider, "model": feat.model, "force_rule_engine": req.force_rule_engine},
    )

    await stage_log(
        log_callback,
        "info",
        "audit",
        f"正在调用审计模型 | 调用: {runtime_kind} | 模型: {runtime_model}...",
        0.08,
        stage="model_call",
        model=runtime_model,
        provider=audit_provider,
    )
    route_features = [feat]
    if not explicit_model_override and not req.force_rule_engine:
        route_features = [
            ResolvedFeatureLlm(
                feature="audit",
                mode="local" if candidate.provider == "ollama" else "cloud",
                provider=candidate.provider,
                model=candidate.model,
                base_url=candidate.base_url,
                api_key=candidate.api_key,
            )
            for candidate in configured_audit_candidates
        ] or [feat]
    route_errors = []
    updated_segments = None
    for route_index, route_feat in enumerate(route_features):
        route_provider = "ollama" if route_feat.provider == "ollama" else "openai"
        try:
            candidate_segments = await semantic_audit_runner(
                segments=audit_source_segments,
                params=task.params,
                task_type=task.task_type.value if task.task_type else None,
                api_key=route_feat.api_key if route_provider == "openai" else None,
                model=route_feat.model,
                style_mode=req.style_mode,
                log_callback=log_callback,
                force_rule_engine=req.force_rule_engine,
                provider=route_provider,
                ollama_model=route_feat.model if route_provider == "ollama" else req.ollama_model,
                ollama_base_url=route_feat.base_url if route_provider == "ollama" else req.ollama_base_url,
                openai_base_url=route_feat.base_url or ROUTERHUB_BASE_URL,
            )
            candidate_report = validate_audit_segments_contract(
                audit_source_segments,
                candidate_segments,
            )
            confidences = [
                float(segment.confidence)
                for segment in candidate_segments
                if segment.confidence is not None
            ]
            average_confidence = (
                sum(confidences) / len(confidences) if confidences else 1.0
            )
            if candidate_report.rejected:
                raise ValueError("审计候选未通过结果契约")
            if average_confidence < 0.65 and route_index + 1 < len(route_features):
                await stage_log(
                    log_callback,
                    "warn",
                    "audit",
                    f"{route_feat.model} 平均置信度 {average_confidence:.2f}，升级下一候选",
                    0.72,
                    stage="model_escalation",
                    model=route_feat.model,
                )
                continue
            updated_segments = candidate_segments
            feat = route_feat
            audit_provider = route_provider
            runtime_model = route_feat.model
            runtime_endpoint = route_feat.base_url
            break
        except Exception as exc:
            route_errors.append(f"{route_feat.model}: {exc}")
            if route_index + 1 < len(route_features):
                await stage_log(
                    log_callback,
                    "warn",
                    "audit",
                    f"{route_feat.model} 失败，切换下一候选 | {exc}",
                    0.70,
                    stage="model_fallback",
                    model=route_feat.model,
                )
                continue
    if updated_segments is None:
        exc = RuntimeError("; ".join(route_errors) or "没有可用的语义审计候选")
        raise AuditPipelineError(
            str(exc),
            error_type="model_call_failed",
            runtime_model=runtime_model,
            allow_rule_engine_fallback=bool(feat.api_key and feat.provider != "ollama" and not req.force_rule_engine),
        ) from exc
    edit_plan_segments = sum(1 for segment in updated_segments if segment.compiled_from_operation)
    if edit_plan_segments:
        await stage_log(
            log_callback,
            "info",
            "audit",
            f"自由断句语义编辑计划已编译 | 涉及 {edit_plan_segments} 个片段决策",
            0.76,
            stage="edit_plan_compile",
            edited_segments=edit_plan_segments,
        )
    updated_segments = apply_sentence_group_guard(
        updated_segments,
        task_type=task.task_type.value if task.task_type else None,
    )

    await stage_log(
        log_callback,
        "info",
        "audit",
        f"审计模型结果已返回 | 当前运行时间: {format_elapsed(time.perf_counter() - audit_started_at)}",
        0.78,
        stage="parse",
        model=runtime_model,
    )
    emit_agent_hook(
        task_id,
        "after_model_output",
        "audit.segments",
        payload={"segments_count": len(updated_segments)},
    )

    await stage_log(log_callback, "info", "audit", "正在校验审计结果契约...", 0.84, stage="contract")
    report = validate_audit_segments_contract(audit_source_segments, updated_segments)
    try:
        await verify_contract_report(task_id, "audit_segments", report, log_callback=log_callback)
    except ContractVerificationError as exc:
        raise AuditPipelineError(
            str(exc),
            error_type="contract_failed",
            runtime_model=runtime_model,
            allow_rule_engine_fallback=bool(feat.api_key and feat.provider != "ollama" and not req.force_rule_engine),
        ) from exc

    await stage_log(log_callback, "info", "audit", "正在处理断句/合并等后处理...", 0.86, stage="postprocess")
    updated_segments = apply_segment_corrections(updated_segments)

    audit_runtime = load_app_config().audit_runtime
    coherence_strategy = audit_runtime.coherence_strategy
    use_model_coherence = (
        coherence_strategy == "always"
        or (
            coherence_strategy == "auto"
            and (task.task_type == TaskType.HIGHLIGHT_REEL or len(updated_segments) < 2000)
        )
    )
    await stage_log(
        log_callback,
        "info",
        "coherence",
        "正在复核审计后全文连贯性..." if use_model_coherence else "正在执行本地连贯性复核...",
        0.88,
        stage="coherence",
        coherence_strategy=coherence_strategy,
        use_model=use_model_coherence,
    )
    coherence_input_segments = [seg.model_copy(deep=True) for seg in updated_segments]
    emit_agent_hook(
        task_id,
        "before_agent_run",
        "optimize.sentence_groups",
        payload={"segments_count": len(updated_segments), "task_type": task.task_type.value if task.task_type else None},
    )
    try:
        updated_segments, coherence_report = await run_audit_coherence_review(
            updated_segments,
            task_type=task.task_type.value if task.task_type else None,
            provider=audit_provider if explicit_model_override else "openai",
            api_key=(feat.api_key if audit_provider == "openai" else None) if explicit_model_override else None,
            model=feat.model if explicit_model_override else None,
            ollama_model=(feat.model if audit_provider == "ollama" else req.ollama_model) if explicit_model_override else "deepseek-r1:14b",
            ollama_base_url=(feat.base_url if audit_provider == "ollama" else req.ollama_base_url) if explicit_model_override else "http://localhost:11434",
            openai_base_url=(feat.base_url or ROUTERHUB_BASE_URL) if explicit_model_override else ROUTERHUB_BASE_URL,
            log_callback=log_callback,
            use_model=use_model_coherence,
        )
    except Exception as exc:
        raise AuditPipelineError(
            str(exc),
            error_type="coherence_review_failed",
            runtime_model=runtime_model,
            allow_rule_engine_fallback=False,
        ) from exc
    emit_agent_hook(
        task_id,
        "after_model_output",
        "optimize.sentence_groups",
        payload={
            "segments_count": len(updated_segments),
            "repaired_count": coherence_report.repaired_count,
            "passed": coherence_report.passed,
        },
    )

    coherence_contract = validate_audit_coherence_contract(coherence_input_segments, updated_segments)
    try:
        await verify_contract_report(task_id, "audit_coherence", coherence_contract, log_callback=log_callback)
    except ContractVerificationError as exc:
        raise AuditPipelineError(
            str(exc),
            error_type="coherence_contract_failed",
            runtime_model=runtime_model,
            allow_rule_engine_fallback=False,
        ) from exc
    updated_segments = apply_segment_corrections(updated_segments)

    task = store.get(task_id)
    if not task:
        raise AuditPipelineError("Task not found", error_type="task_missing", runtime_model=runtime_model)
    if req.reset_prior_audit:
        _commit_reset_metadata(task)
    task.audit_segments = updated_segments
    task.audit_coherence_report = coherence_report
    task.update_stats()
    task.status = TaskStatus.AUDIT_RUNNING if include_highlight_plan and task.task_type == TaskType.HIGHLIGHT_REEL else TaskStatus.REVIEW

    audit_snap = AuditSnapshot(
        version=len(task.audit_history) + 1,
        created_at=datetime.now().isoformat(),
        model=(feat.model if not req.force_rule_engine else "规则引擎"),
        segments_count=len(updated_segments),
        segments_kept=task.segments_kept,
        segments_deleted=task.segments_deleted,
    )
    task.audit_history.append(audit_snap)
    task.audit_current_version = audit_snap.version
    store.update(task)

    if not include_highlight_plan and task.task_type == TaskType.HIGHLIGHT_REEL:
        task.highlight_reels = []
        task.highlight_reels_before_quality = []
        task.highlight_quality_report = None
        task.highlight_quality_report_before_quality = None
        task.product_occurrences = []
        task.product_groups = []
        task.highlight_strategy_quality_results = []
        for segment in task.audit_segments:
            segment.clip_group = None
            segment.clip_title = None
        store.update(task)

    reels_count = 0
    quality_passed: Optional[bool] = None
    if include_highlight_plan and task.task_type == TaskType.HIGHLIGHT_REEL and coherence_report.passed:
        try:
            reels, quality_report = await refresh_highlight_plan(
                task_id,
                options=highlight_build_options,
                log_callback=log_callback,
            )
        except HighlightPipelineError as exc:
            raise AuditPipelineError(
                str(exc),
                error_type=exc.error_type,
                runtime_model=runtime_model,
                allow_rule_engine_fallback=False,
            ) from exc
        reels_count = len([reel for reel in reels if not reel.hidden])
        quality_passed = quality_report.passed
    elif include_highlight_plan and task.task_type == TaskType.HIGHLIGHT_REEL:
        quality_passed = False
        await stage_log(
            log_callback,
            "warn",
            "coherence",
            "审计后全文连贯性未通过，已暂停自动生成产品集锦，请先在 Review 中处理上下文/断句/术语问题。",
            0.94,
            stage="coherence_gate",
            quality_passed=False,
        )

    await stage_log(log_callback, "info", "audit", "正在写入审计结果与历史快照...", 0.96, stage="persist")
    emit_tool_hook(
        task_id,
        "before_tool_call",
        "orchestrate.clip_pipeline",
        "write_audit_segments",
        payload={"segments_count": len(updated_segments)},
    )
    task = store.get(task_id)
    if not task:
        raise AuditPipelineError("Task not found", error_type="task_missing", runtime_model=runtime_model)
    task.status = TaskStatus.REVIEW
    store.update(task)
    emit_tool_hook(
        task_id,
        "after_tool_call",
        "orchestrate.clip_pipeline",
        "write_audit_segments",
        payload={"status": task.status.value, "segments_count": len(updated_segments)},
    )

    elapsed = time.perf_counter() - audit_started_at
    await stage_log(
        log_callback,
        "success",
        "audit",
        f"语义审计任务完成 | 调用: {runtime_kind} | 模型: {runtime_model} | "
        f"总运行时间: {format_elapsed(elapsed)} | 保留 {task.segments_kept}，删除 {task.segments_deleted}",
        1.0,
        stage="audit_done",
        model=runtime_model,
        reels_count=reels_count or None,
        quality_passed=quality_passed,
    )
    return AuditPipelineResult(
        runtime_kind=runtime_kind,
        runtime_model=runtime_model,
        elapsed_seconds=elapsed,
        reels_count=reels_count,
        quality_passed=quality_passed,
    )
