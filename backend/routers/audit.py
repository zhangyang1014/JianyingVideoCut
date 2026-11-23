"""语义审计触发、片段增删改/合并/批量操作、裁剪区间、金句、提示词反馈、手动审计。"""

import asyncio
import time
from datetime import datetime
from typing import List, Optional, Dict, Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..models.task import (
    TaskStatus, AuditRequest, SegmentAction, ManualSegmentAction, AuditSnapshot,
    UpdateSegmentsRequest, ClipRangeRequest,
)
from ..services.task_store import get_store
from ..services.semantic_auditor import (
    run_semantic_audit,
    load_system_prompt as load_audit_system_prompt,
    build_audit_prompt, parse_claude_response, apply_segment_corrections,
    apply_sentence_group_guard,
)
from ..services.prompt_optimizer import (
    collect_corrections,
    generate_prompt_improvement,
    merge_user_style,
    apply_feedback_prompt_changes,
)
from ..services.app_config_store import (
    merge_feature_llm_request,
    resolve_role_candidates,
    role_primary_or_feature,
)
from ..services.audit_pipeline import AuditPipelineError, format_elapsed, run_audit_pipeline
from ..models.app_config import DEFAULT_GOLDEN_QUOTE_MODEL, DEFAULT_LEARNING_MODEL, ROUTERHUB_BASE_URL
from ..services.golden_quote_extractor import suggest_golden_quotes
from ..services.subtitle_text import set_manual_subtitle_text
from ..services.execution_preview import (
    ExecutionPreviewError,
    validate_execution_preview_token,
)
from ..services.model_usage import model_usage_task_context
from ..agent_runtime.contracts import validate_audit_segments_contract
from ..agent_runtime.harness import verify_contract_report
from ..agent_runtime.hooks import emit_agent_hook
from ..core.ws import manager, log_to_ws
from ..core.task_runner import (
    _start_task_runner, _task_runner_superseded, _rollback_cancelled_task,
    mark_current_execution_failed,
)

router = APIRouter()


def _clear_segment_audit_state(segment):
    segment.action = SegmentAction.KEEP
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


def _reset_prior_audit_results(task) -> None:
    """清空语义审计产物，但保留当前 ASR/文本整理版本。"""
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
    task.audit_segments = [segment.model_copy(deep=True) for segment in source_segments]
    for segment in task.audit_segments:
        _clear_segment_audit_state(segment)
        override = subtitle_overrides.get(segment.id)
        if override:
            segment.subtitle_text = override["subtitle_text"]
            segment.subtitle_text_source = override["subtitle_text_source"]
            segment.subtitle_updated_at = override["subtitle_updated_at"]
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
    task.update_stats()

def _mark_content_changed(task) -> None:
    """字幕和剪辑结构变更递增内容版本；已启动导出继续使用旧快照。"""
    task.content_revision = (task.content_revision or 0) + 1


# ============================================================
# Claude Semantic Audit
# ============================================================

@router.post("/api/tasks/{task_id}/audit")
async def trigger_audit(task_id: str, req: AuditRequest):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.audit_segments:
        raise HTTPException(400, "No segments to audit. Run ASR first.")
    if req.preview_token:
        try:
            validate_execution_preview_token(
                req.preview_token,
                task=task,
                operation="audit",
                mode=req.preview_mode,
                options=req.model_dump(
                    exclude={"preview_token", "preview_mode"},
                    exclude_unset=True,
                ),
            )
        except ExecutionPreviewError as exc:
            raise HTTPException(409, str(exc)) from exc

    _start_task_runner(
        task_id,
        _run_audit_task(task_id, req),
        TaskStatus.AUDIT_RUNNING,
        operation="audit",
    )
    return {"success": True, "message": "语义审计已启动"}


async def _run_audit_task(task_id: str, req: AuditRequest):
    store = get_store()
    task = store.get(task_id)
    if not task:
        return

    audit_started_at = time.perf_counter()
    async def log(level, source, msg, progress=None, **extra):
        await log_to_ws(task_id, level, source, msg, progress, **extra)

    try:
        result = await run_audit_pipeline(
            task_id,
            req,
            log_callback=log,
            include_highlight_plan=False,
            store=store,
            semantic_audit_runner=run_semantic_audit,
        )
        task = store.get(task_id)
        if not task:
            return
        await manager.broadcast(task_id, {
            "type": "status_change",
            "task_id": task_id,
            "status": TaskStatus.REVIEW,
            "segments_kept": task.segments_kept,
            "segments_deleted": task.segments_deleted,
            "edited_duration": task.edited_duration,
        })
        if result.reels_count:
            await manager.broadcast(task_id, {
                "type": "highlight_reels_ready",
                "task_id": task_id,
                "reels_count": result.reels_count,
                "canonical_reels_count": len(task.highlight_reels),
                "occurrences_count": len(task.product_occurrences),
                "strategies": ["faithful", "balanced", "concise"],
                "quality_passed": result.quality_passed,
            })

    except asyncio.CancelledError:
        if not _task_runner_superseded(task_id):
            await log(
                "warn",
                "audit",
                f"语义审计已取消 | 已运行 {format_elapsed(time.perf_counter() - audit_started_at)}",
                stage="cancelled",
            )
            await _rollback_cancelled_task(task_id, TaskStatus.ASR_DONE, "system", "语义审计")

    except AuditPipelineError as e:
        task = store.get(task_id)
        if not task:
            return
        if e.allow_rule_engine_fallback:
            has_reviewable_audit = bool(
                task.audit_current_version
                or task.audit_history
                or any(seg.action != SegmentAction.KEEP or seg.claude_action for seg in task.audit_segments)
            )
            task.status = TaskStatus.REVIEW if has_reviewable_audit else TaskStatus.ASR_DONE
            store.update(task)
            await log(
                "error",
                "audit",
                f"AI 审计失败 | 类型: {e.error_type} | 模型: {e.runtime_model or '未知'} | "
                f"运行时间: {format_elapsed(time.perf_counter() - audit_started_at)} | 错误: {e}",
                error_type=e.error_type,
                stage="failed",
            )
            await manager.broadcast(task_id, {
                "type": "status_change",
                "task_id": task_id,
                "status": task.status,
            })
            await manager.broadcast(task_id, {
                "type": "claude_failed",
                "task_id": task_id,
                "error": str(e),
                "error_type": e.error_type,
            })
        else:
            task.status = TaskStatus.ERROR
            task.error_message = str(e)
            store.update(task)
            await log(
                "error",
                "audit",
                f"审计失败 | 类型: {e.error_type} | 运行时间: {format_elapsed(time.perf_counter() - audit_started_at)} | 错误: {e}",
                error_type=e.error_type,
                stage="failed",
            )
            await manager.broadcast(task_id, {
                "type": "status_change",
                "task_id": task_id,
                "status": task.status,
            })
        mark_current_execution_failed(task_id, e)

    except Exception as e:
        error_msg = str(e)
        task = store.get(task_id)
        if task:
            task.status = TaskStatus.ERROR
            task.error_message = error_msg
            store.update(task)
            await manager.broadcast(task_id, {
                "type": "status_change",
                "task_id": task_id,
                "status": task.status,
            })
        await log(
            "error",
            "audit",
            f"审计失败 | 运行时间: {format_elapsed(time.perf_counter() - audit_started_at)} | 错误: {error_msg}",
            error_type="unexpected_failed",
            stage="failed",
        )
        mark_current_execution_failed(task_id, e)


# ============================================================
# Segment Review
# ============================================================

@router.put("/api/tasks/{task_id}/segments")
async def update_segments(task_id: str, req: UpdateSegmentsRequest):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    task.audit_segments = req.segments
    _mark_content_changed(task)
    task.update_stats()
    store.update(task)
    return {"success": True, "stats": {
        "kept": task.segments_kept,
        "deleted": task.segments_deleted,
        "edited_duration": task.edited_duration
    }}


@router.put("/api/tasks/{task_id}/segments/{segment_id}/toggle")
async def toggle_segment(task_id: str, segment_id: str):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    for seg in task.audit_segments:
        if seg.id == segment_id:
            seg.action = (
                SegmentAction.KEEP
                if seg.action == SegmentAction.DELETE
                else SegmentAction.DELETE
            )
            seg.user_override = True
            seg.manual_action = (
                ManualSegmentAction.RESTORE
                if seg.action == SegmentAction.KEEP
                else ManualSegmentAction.DELETE
            )
            if seg.action == SegmentAction.KEEP:
                seg.reason = "手动恢复"
            break

    _mark_content_changed(task)
    task.update_stats()
    store.update(task)
    return {"success": True}


class PatchSegmentRequest(BaseModel):
    text: Optional[str] = None
    start: Optional[float] = None
    end: Optional[float] = None
    reason: Optional[str] = None


@router.patch("/api/tasks/{task_id}/segments/{segment_id}")
async def patch_segment(task_id: str, segment_id: str, req: PatchSegmentRequest):
    """修改单个片段的文本、时间戳或人工原因。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    found = False
    subtitle_changed = False
    content_changed = False
    touched = False
    for seg in task.audit_segments:
        if seg.id == segment_id:
            if req.text is not None:
                subtitle_changed = set_manual_subtitle_text(
                    seg,
                    req.text,
                    updated_at=datetime.now().isoformat(),
                )
                touched = True
            if req.start is not None:
                if abs(req.start - seg.start) > 0.001:
                    seg.start = req.start
                    content_changed = True
                    touched = True
            if req.end is not None:
                if abs(req.end - seg.end) > 0.001:
                    seg.end = req.end
                    content_changed = True
                    touched = True
            if req.reason is not None:
                if req.reason != seg.reason:
                    seg.reason = req.reason
                    touched = True
            if touched:
                seg.user_override = True
            if content_changed or subtitle_changed:
                seg.manual_action = ManualSegmentAction.ADJUST
            found = True
            break

    if not found:
        raise HTTPException(404, "Segment not found")

    if content_changed:
        _mark_content_changed(task)
    if subtitle_changed:
        task.subtitle_revision = (task.subtitle_revision or 0) + 1
    task.update_stats()
    store.update(task)
    return {"success": True}


@router.delete("/api/tasks/{task_id}/segments/{segment_id}")
async def remove_segment(task_id: str, segment_id: str):
    """彻底删除单个片段（从列表中移除）。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    original_len = len(task.audit_segments)
    task.audit_segments = [s for s in task.audit_segments if s.id != segment_id]
    if len(task.audit_segments) == original_len:
        raise HTTPException(404, "Segment not found")

    _mark_content_changed(task)
    task.update_stats()
    store.update(task)
    return {"success": True}


@router.post("/api/tasks/{task_id}/segments/{segment_id}/merge-next")
async def merge_segment_next(task_id: str, segment_id: str):
    """将指定片段与其下一段合并：拼接文本/words、延伸时间边界、移除下一段。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    segs = task.audit_segments
    idx = next((i for i, s in enumerate(segs) if s.id == segment_id), None)
    if idx is None:
        raise HTTPException(404, "Segment not found")
    if idx + 1 >= len(segs):
        raise HTTPException(400, "已是最后一段，无法合并")

    seg = segs[idx]
    next_seg = segs[idx + 1]

    seg.end = round(next_seg.end, 3)
    seg.text = (seg.text or "") + (next_seg.text or "")
    seg.tagged_text = (seg.tagged_text or "") + (next_seg.tagged_text or "")
    seg.words = list(seg.words) + list(next_seg.words)
    seg.user_override = True
    seg.manual_action = ManualSegmentAction.MERGE
    seg.reason = "手动合并"
    seg.merged = True

    # 移除下一段
    task.audit_segments = segs[:idx + 1] + segs[idx + 2:]

    _mark_content_changed(task)
    task.update_stats()
    store.update(task)
    return {"success": True, "segment": seg.model_dump()}


class BatchSegmentRequest(BaseModel):
    segment_ids: List[str]
    action: str  # "keep" | "delete" | "remove"


@router.post("/api/tasks/{task_id}/segments/batch")
async def batch_segment_action(task_id: str, req: BatchSegmentRequest):
    """批量操作片段：标记保留/删除 或 彻底移除。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    target_ids = set(req.segment_ids)

    if req.action == "remove":
        task.audit_segments = [s for s in task.audit_segments if s.id not in target_ids]
    elif req.action in ("keep", "delete"):
        new_action = SegmentAction.KEEP if req.action == "keep" else SegmentAction.DELETE
        for seg in task.audit_segments:
            if seg.id in target_ids:
                seg.action = new_action
                seg.user_override = True
                seg.manual_action = (
                    ManualSegmentAction.RESTORE
                    if new_action == SegmentAction.KEEP
                    else ManualSegmentAction.DELETE
                )
                if new_action == SegmentAction.KEEP:
                    seg.reason = "手动恢复"
                else:
                    seg.reason = "手动删除"
                    seg.rule = "手动调整"
    else:
        raise HTTPException(400, f"Unknown action: {req.action}")

    _mark_content_changed(task)
    task.update_stats()
    store.update(task)
    return {"success": True, "stats": {
        "kept": task.segments_kept,
        "deleted": task.segments_deleted,
        "edited_duration": task.edited_duration
    }}


# ============================================================
# Clip Range（裁剪区间）
# ============================================================

@router.patch("/api/tasks/{task_id}/clip")
async def update_clip_range(task_id: str, req: ClipRangeRequest):
    """
    设置视频裁剪区间（In/Out Point）。
    若已有 audit_segments，则对区间外的段自动标记为 delete（user_override=False）；
    放大区间时，原先因区间外被 auto-delete 的段自动恢复为 keep。
    """
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    old_clip_start = task.clip_start
    old_clip_end = task.clip_end

    task.clip_start = req.clip_start
    task.clip_end = req.clip_end
    if old_clip_start != task.clip_start or old_clip_end != task.clip_end:
        _mark_content_changed(task)

    clip_start = req.clip_start or 0.0
    clip_end = req.clip_end or (task.video_duration or float("inf"))

    if task.audit_segments:
        for seg in task.audit_segments:
            seg_out_of_range = seg.end <= clip_start or seg.start >= clip_end
            if seg_out_of_range:
                # 仅自动标记非用户手动操作的段
                if not seg.user_override:
                    seg.action = SegmentAction.DELETE
                    seg.reason = "区间外自动排除"
            else:
                # 区间内：若之前是因区间外被 auto-delete 的，恢复为 keep
                if (not seg.user_override) and seg.reason == "区间外自动排除":
                    seg.action = SegmentAction.KEEP
                    seg.reason = None

        task.update_stats()

    store.update(task)
    return {
        "success": True,
        "clip_start": task.clip_start,
        "clip_end": task.clip_end,
        "stats": {
            "kept": task.segments_kept,
            "deleted": task.segments_deleted,
            "edited_duration": task.edited_duration,
        }
    }


# ============================================================
# 金句开场
# ============================================================

class GoldenQuoteSuggestRequest(BaseModel):
    """金句分析请求参数，复用 AuditRequest 的 AI 配置结构。"""
    provider: str = ""              # 空值表示使用任务角色路由
    claude_api_key: Optional[str] = None
    claude_model: str = ""
    openai_api_key: Optional[str] = None
    openai_model: str = ""
    use_openrouter: bool = False
    ollama_model: str = "deepseek-r1:14b"
    ollama_base_url: str = "http://localhost:11434"
    preview_token: Optional[str] = None
    preview_mode: Literal["rerun", "resume", "reset"] = "rerun"


class GoldenQuoteOrderRequest(BaseModel):
    """保存用户选定并排序后的金句片段 ID 列表。"""
    segment_ids: List[str]


@router.post("/api/tasks/{task_id}/golden-quotes/suggest")
async def suggest_golden_quotes_api(task_id: str, req: GoldenQuoteSuggestRequest):
    """
    调用 AI 分析当前任务的保留片段，推荐最多 5 个适合做开场钩子的金句候选。
    同步接口，约 5-15 秒返回。
    """
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.audit_segments:
        raise HTTPException(400, "暂无审计片段，请先完成 ASR 识别和语义审计")
    if req.preview_token:
        try:
            validate_execution_preview_token(
                req.preview_token,
                task=task,
                operation="golden_quote",
                mode=req.preview_mode,
                options=req.model_dump(
                    exclude={"preview_token", "preview_mode"},
                    exclude_unset=True,
                ),
            )
        except ExecutionPreviewError as exc:
            raise HTTPException(409, str(exc)) from exc

    async def log(level, source, msg, progress=None):
        await log_to_ws(task_id, level, source, msg, progress)

    try:
        explicit_override = bool(
            req.provider
            or req.openai_model
            or req.claude_model
            or req.openai_api_key
            or req.claude_api_key
        )
        requested_model = req.openai_model or req.claude_model
        if requested_model and any(
            candidate.model == requested_model
            for candidate in resolve_role_candidates("golden_quote.extract")
        ):
            explicit_override = False
        feat = (
            merge_feature_llm_request(
                "golden_quote",
                provider=req.provider or None,
                openai_model=req.openai_model or req.claude_model or None,
                ollama_model=req.ollama_model or None,
                ollama_base_url=req.ollama_base_url or None,
                api_key=req.openai_api_key or req.claude_api_key,
            )
            if explicit_override
            else role_primary_or_feature("golden_quote.extract", "golden_quote")
        )
        gq_provider = "ollama" if feat.provider == "ollama" else "openai"
        emit_agent_hook(
            task_id,
            "before_model_call",
            "extract.golden_quotes",
            payload={"provider": gq_provider, "model": feat.model},
        )
        with model_usage_task_context(task_id):
            candidates = await suggest_golden_quotes(
                segments=task.audit_segments,
                provider=gq_provider,
                api_key=feat.api_key if gq_provider == "openai" else None,
                model=feat.model,
                use_openrouter=req.use_openrouter,
                ollama_model=feat.model if gq_provider == "ollama" else req.ollama_model,
                ollama_base_url=feat.base_url if gq_provider == "ollama" else req.ollama_base_url,
                openai_base_url=feat.base_url or ROUTERHUB_BASE_URL,
                log_callback=log,
                task_type=task.task_type,
            )
        emit_agent_hook(
            task_id,
            "after_model_output",
            "extract.golden_quotes",
            payload={"candidates_count": len(candidates)},
        )
        return {"success": True, "candidates": candidates}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"金句分析失败: {str(e)}")


@router.put("/api/tasks/{task_id}/golden-quotes/order")
async def save_golden_quote_order(task_id: str, req: GoldenQuoteOrderRequest):
    """
    保存用户选定并排序后的金句片段 ID 列表。
    导出时这些片段将被放在视频最开头。
    传空列表表示清除金句开场设置。
    """
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    # 校验 segment_ids 是否在当前 audit_segments 中
    valid_ids = {s.id for s in task.audit_segments}
    invalid = [sid for sid in req.segment_ids if sid not in valid_ids]
    if invalid:
        raise HTTPException(400, f"以下 segment_id 不存在: {invalid}")

    task.golden_quote_order = req.segment_ids
    task.updated_at = datetime.now().isoformat()
    store.update(task)

    return {
        "success": True,
        "golden_quote_order": task.golden_quote_order,
        "count": len(task.golden_quote_order),
    }


# ============================================================
# Prompt Feedback (提示词反馈优化)
# ============================================================

@router.get("/api/tasks/{task_id}/corrections")
async def get_corrections(task_id: str):
    """获取用户修改汇总统计（对比 claude_action 与当前 action）。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    corrections = collect_corrections(task.audit_segments or [])
    return corrections


class PromptFeedbackRequest(BaseModel):
    claude_api_key: Optional[str] = None
    claude_model: str = ""
    openai_api_key: Optional[str] = None
    openai_model: str = ""
    user_notes: str = ""


@router.post("/api/tasks/{task_id}/prompt-feedback")
async def submit_prompt_feedback(task_id: str, req: PromptFeedbackRequest):
    """将用户修改提交给 Claude 分析，返回提示词改进建议。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    try:
        explicit_override = bool(
            req.openai_model
            or req.claude_model
            or req.openai_api_key
            or req.claude_api_key
        )
        requested_model = req.openai_model or req.claude_model
        if requested_model and any(
            candidate.model == requested_model
            for candidate in resolve_role_candidates("prompt_feedback.optimize")
        ):
            explicit_override = False
        feat = (
            merge_feature_llm_request(
                "learning",
                openai_model=req.openai_model or req.claude_model or None,
                api_key=req.openai_api_key or req.claude_api_key,
            )
            if explicit_override
            else role_primary_or_feature("prompt_feedback.optimize", "learning")
        )
        with model_usage_task_context(task_id):
            result = await generate_prompt_improvement(
                task=task,
                api_key=feat.api_key,
                model=feat.model,
                user_notes=req.user_notes,
                base_url=feat.base_url or ROUTERHUB_BASE_URL,
            )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"生成改进建议失败: {str(e)}")


class AdoptStyleRequest(BaseModel):
    style_update: Dict[str, str]


class AdoptPromptFeedbackRequest(BaseModel):
    prompt_changes: Dict[str, Any]


@router.post("/api/tasks/{task_id}/adopt-prompt-feedback")
async def adopt_prompt_feedback(task_id: str, req: AdoptPromptFeedbackRequest):
    """将结构化提示词反馈变更写入当前场景提示词，并激活为新版本。"""
    task = get_store().get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    try:
        task_type = task.task_type.value if task.task_type else "monologue_clean"
        return apply_feedback_prompt_changes(task_type, req.prompt_changes)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"写入提示词规则失败: {str(e)}")


@router.post("/api/tasks/{task_id}/adopt-style")
async def adopt_style(task_id: str, req: AdoptStyleRequest):
    """将 Claude 学习到的个人风格偏好合并写入 user_style.md。"""
    try:
        content = merge_user_style(req.style_update)
        return {"success": True, "content": content}
    except Exception as e:
        raise HTTPException(500, f"写入风格档案失败: {str(e)}")


# ============================================================
# 剪辑模块 手动模式（离线 AI 审计）
# ============================================================

@router.get("/api/tasks/{task_id}/manual-audit-prompt")
async def get_manual_audit_prompt(task_id: str):
    """下载剪辑审计的提示词包（system + user prompt）。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.audit_segments:
        raise HTTPException(400, "No segments to audit. Run ASR first.")

    task_type = task.task_type.value if task.task_type else None
    system_prompt = load_audit_system_prompt(task_type)
    user_prompt, idx_to_id = build_audit_prompt(
        segments=task.audit_segments,
        style_mode=task.params.style_mode,
        task_type=task_type,
    )

    combined = f"=== SYSTEM PROMPT ===\n{system_prompt}\n\n=== USER PROMPT ===\n{user_prompt}"
    return {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "combined": combined,
        "idx_to_id": idx_to_id,
    }


class ManualAuditResultRequest(BaseModel):
    audit_json: str


@router.post("/api/tasks/{task_id}/manual-audit-result")
async def submit_manual_audit_result(task_id: str, req: ManualAuditResultRequest):
    """提交手动 AI 审计的 JSON 输出结果。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.audit_segments:
        raise HTTPException(400, "No segments to audit")
    if not req.audit_json.strip():
        raise HTTPException(400, "审计结果 JSON 不能为空")

    task_type = task.task_type.value if task.task_type else None

    # 重建 idx_to_id 映射（与 build_audit_prompt 使用相同逻辑）
    idx_to_id = {}
    for i, seg in enumerate(task.audit_segments):
        idx_to_id[i + 1] = seg.id

    updated_segments = parse_claude_response(
        response_text=req.audit_json.strip(),
        original_segments=task.audit_segments,
        idx_to_id=idx_to_id,
    )
    updated_segments = apply_sentence_group_guard(
        updated_segments,
        task_type=task_type,
    )

    report = validate_audit_segments_contract(task.audit_segments, updated_segments)
    await verify_contract_report(task_id, "manual_audit_segments", report)

    updated_segments = apply_segment_corrections(updated_segments)

    # 冻结 Claude 原始决策快照
    for seg in updated_segments:
        seg.claude_action = seg.action
        seg.claude_reason = seg.reason

    task.audit_segments = updated_segments
    _mark_content_changed(task)
    task.update_stats()
    task.status = TaskStatus.REVIEW

    audit_snap = AuditSnapshot(
        version=len(task.audit_history) + 1,
        created_at=datetime.now().isoformat(),
        model="手动模式（离线 AI）",
        segments_count=len(updated_segments),
        segments_kept=task.segments_kept,
        segments_deleted=task.segments_deleted,
    )
    task.audit_history.append(audit_snap)
    task.audit_current_version = audit_snap.version

    store.update(task)

    return {
        "success": True,
        "message": f"审计完成，保留 {task.segments_kept} 段，删除 {task.segments_deleted} 段",
        "segments_kept": task.segments_kept,
        "segments_deleted": task.segments_deleted,
        "edited_duration": task.edited_duration,
    }
