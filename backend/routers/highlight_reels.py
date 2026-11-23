"""直播卖货产品集锦计划、质检与一键自动流程。"""

import asyncio
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..models.task import (
    HighlightReel,
    HighlightReelAutoRunRequest,
    HighlightReelBuildOptions,
    HighlightReelQualityReport,
    HighlightReelVariant,
    TaskStatus,
    TaskType,
    UpdateHighlightReelsRequest,
    AuditRequest,
)
from ..services.task_store import get_store
from ..services.task_store import RevisionConflictError
from ..services.highlight_reel_plan import (
    apply_plan_issues_to_reels,
    product_group_as_occurrence,
    rebuild_reel_metrics,
    reels_to_clip_groups,
    validate_highlight_reels_code,
)
from ..services.audit_pipeline import AuditPipelineError, run_audit_pipeline
from ..services.highlight_reel_pipeline import refresh_highlight_plan
from ..services.highlight_strategy_generator import build_manual_highlight_prompt_pack
from ..services.manual_highlight_import import (
    ManualHighlightImportError,
    apply_compiled_manual_highlight_result,
    compile_manual_highlight_result,
)
from ..services.product_occurrence_detector import (
    group_product_occurrences,
    identify_product_occurrences,
)
from ..services.model_usage import model_usage_task_context
from ..services.execution_preview import (
    ExecutionPreviewError,
    validate_execution_preview_token,
)
from ..agent_runtime.contracts import (
    format_contract_issues,
    validate_highlight_reels_contract,
)
from ..agent_runtime.hooks import emit_agent_hook, emit_tool_hook
from ..core.ws import manager, log_to_ws
from ..core.task_runner import (
    _start_task_runner,
    _task_runner_superseded,
    _rollback_cancelled_task,
    mark_current_execution_failed,
)
from ..routers.asr import _run_asr_task, _run_asr_diarization_task, _run_asr_analyze_task

router = APIRouter()


def _variant_reels(task, variant: HighlightReelVariant) -> List[HighlightReel]:
    if variant == HighlightReelVariant.BEFORE_QUALITY:
        return task.highlight_reels_before_quality or []
    return task.highlight_reels or []


def _variant_report(task, variant: HighlightReelVariant) -> Optional[HighlightReelQualityReport]:
    if variant == HighlightReelVariant.BEFORE_QUALITY:
        return task.highlight_quality_report_before_quality
    return task.highlight_quality_report


def _highlight_reels_response(task):
    before_reels = task.highlight_reels_before_quality or []
    after_reels = task.highlight_reels or []
    return {
        "reels": [reel.model_dump() for reel in after_reels],
        "product_occurrences": [occurrence.model_dump() for occurrence in task.product_occurrences],
        "product_groups": [group.model_dump() for group in task.product_groups],
        "strategy_quality_results": [
            result.model_dump() for result in task.highlight_strategy_quality_results
        ],
        "occurrences_count": len(task.product_occurrences),
        "product_groups_count": len(task.product_groups),
        "canonical_reels_count": len(after_reels),
        "quality_report": task.highlight_quality_report.model_dump()
        if task.highlight_quality_report
        else None,
        "variants": {
            HighlightReelVariant.BEFORE_QUALITY.value: {
                "reels": [reel.model_dump() for reel in before_reels],
                "quality_report": task.highlight_quality_report_before_quality.model_dump()
                if task.highlight_quality_report_before_quality
                else None,
            },
            HighlightReelVariant.AFTER_QUALITY.value: {
                "reels": [reel.model_dump() for reel in after_reels],
                "quality_report": task.highlight_quality_report.model_dump()
                if task.highlight_quality_report
                else None,
            },
        },
    }


async def _refresh_highlight_plan(
    task_id: str,
    *,
    options: Optional[HighlightReelBuildOptions] = None,
    run_model_validation: bool = True,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    ollama_model: Optional[str] = None,
    ollama_base_url: Optional[str] = None,
    log_callback=None,
) -> tuple[List[HighlightReel], HighlightReelQualityReport]:
    with model_usage_task_context(task_id):
        return await refresh_highlight_plan(
            task_id,
            options=options,
            run_model_validation=run_model_validation,
            provider=provider,
            api_key=api_key,
            model=model,
            ollama_model=ollama_model,
            ollama_base_url=ollama_base_url,
            log_callback=log_callback,
        )


@router.get("/api/tasks/{task_id}/highlight-reels")
async def get_highlight_reels(task_id: str):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return _highlight_reels_response(task)


@router.get("/api/tasks/{task_id}/manual-highlight-prompt")
async def get_manual_highlight_prompt(task_id: str):
    """返回可直接粘贴到 Cursor 对话窗的产品集锦分析提示词。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.audit_segments:
        raise HTTPException(400, "No segments to analyze. Run semantic audit first.")

    # 旧任务可能只有 occurrence。复制指令前先按当前规则回填全局产品组，
    # 避免把同一产品的返场、版本或内容角度各自生成三套 JOB。
    if not task.product_groups:
        occurrences = task.product_occurrences or identify_product_occurrences(
            task.audit_segments
        )
        product_groups = group_product_occurrences(
            occurrences,
            task.audit_segments,
        )
        if not product_groups:
            raise HTTPException(
                400,
                "No product groups to analyze. Run product detection first.",
            )
        write_task = task.model_copy(deep=True)
        write_task.product_occurrences = [
            occurrence.model_copy(deep=True) for occurrence in occurrences
        ]
        write_task.product_groups = [
            group.model_copy(deep=True) for group in product_groups
        ]
        try:
            task = store.update(write_task, expected_revision=task.revision)
        except RevisionConflictError as exc:
            latest_task = store.get(task_id)
            if not latest_task or not latest_task.product_groups:
                raise HTTPException(
                    409,
                    "Task changed while rebuilding product groups. Please retry.",
                ) from exc
            task = latest_task

    try:
        return build_manual_highlight_prompt_pack(
            task.audit_segments,
            task.product_groups,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class ManualHighlightResultRequest(BaseModel):
    result_json: str
    expected_revision: int


@router.post("/api/tasks/{task_id}/manual-highlight-result/preview")
async def preview_manual_highlight_result(
    task_id: str,
    req: ManualHighlightResultRequest,
):
    """只读预览 Cursor 结果升级，不修改任务文档。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.revision != req.expected_revision:
        raise HTTPException(409, "任务已更新，请刷新后重新预览 Cursor 结果")
    try:
        compiled = compile_manual_highlight_result(task, req.result_json)
    except ManualHighlightImportError as exc:
        raise HTTPException(400, str(exc)) from exc
    return compiled.summary(expected_revision=task.revision)


@router.post("/api/tasks/{task_id}/manual-highlight-result/apply")
async def apply_manual_highlight_result(
    task_id: str,
    req: ManualHighlightResultRequest,
):
    """校验并以 CAS 原子写回最新版 product-group 集锦结构。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.revision != req.expected_revision:
        raise HTTPException(409, "任务已更新，请刷新后重新预览 Cursor 结果")
    try:
        compiled = compile_manual_highlight_result(task, req.result_json)
        updated = apply_compiled_manual_highlight_result(task, compiled)
        saved = store.update(updated, expected_revision=req.expected_revision)
    except ManualHighlightImportError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RevisionConflictError as exc:
        raise HTTPException(409, "任务已更新，Cursor 结果未写入，请重新预览") from exc
    return {
        "success": True,
        "message": (
            f"已升级为 {len(compiled.product_groups)} 个产品组、"
            f"{len(compiled.before_quality_reels)} 条候选和 "
            f"{len(compiled.winners)} 条 winner"
        ),
        "revision": saved.revision,
        **compiled.summary(expected_revision=saved.revision),
    }


@router.put("/api/tasks/{task_id}/highlight-reels")
async def update_highlight_reels(task_id: str, req: UpdateHighlightReelsRequest):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.revision != req.expected_revision:
        raise HTTPException(409, "任务已更新，请刷新后再保存产品集锦")

    variant = req.variant
    rebuilt_reels = [
        rebuild_reel_metrics(reel, task.audit_segments)
        for reel in req.reels
    ]
    candidate_scopes = [
        product_group_as_occurrence(group) for group in task.product_groups
    ]
    is_product_winner_variant = (
        variant == HighlightReelVariant.AFTER_QUALITY and bool(task.product_groups)
    )
    report = validate_highlight_reels_code(
        rebuilt_reels,
        task.audit_segments,
        task.video_duration,
        product_occurrences=None if is_product_winner_variant else candidate_scopes or None,
        product_groups=task.product_groups if is_product_winner_variant else None,
    )
    contract_report = validate_highlight_reels_contract(
        rebuilt_reels,
        task.audit_segments,
        task.video_duration,
        product_occurrences=None if is_product_winner_variant else candidate_scopes or None,
        product_groups=task.product_groups if is_product_winner_variant else None,
    )
    if variant == HighlightReelVariant.AFTER_QUALITY and contract_report.rejected:
        raise HTTPException(400, f"产品集锦契约失败: {format_contract_issues(contract_report.issues)}")

    report.variant = variant
    rebuilt_reels = apply_plan_issues_to_reels(
        rebuilt_reels,
        report,
        hide_hard_issues=variant == HighlightReelVariant.AFTER_QUALITY,
    )
    task.highlight_revision = (task.highlight_revision or 0) + 1
    for reel in rebuilt_reels:
        reel.source_content_revision = task.content_revision or 0
    if variant == HighlightReelVariant.BEFORE_QUALITY:
        task.highlight_reels_before_quality = rebuilt_reels
        task.highlight_quality_report_before_quality = report
    else:
        task.highlight_reels = rebuilt_reels
        task.highlight_quality_report = report
    task.update_stats()
    try:
        task = store.update(task, expected_revision=req.expected_revision)
    except RevisionConflictError as exc:
        raise HTTPException(409, "任务已更新，产品集锦修改未写入，请刷新后重试") from exc
    return {
        "reels": [reel.model_dump() for reel in rebuilt_reels],
        "quality_report": report.model_dump(),
        "variant": variant.value,
        "revision": task.revision,
    }


@router.post("/api/tasks/{task_id}/highlight-reels/rebuild")
async def rebuild_highlight_reels(task_id: str, req: HighlightReelBuildOptions = HighlightReelBuildOptions()):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if req.preview_token:
        try:
            validate_execution_preview_token(
                req.preview_token,
                task=task,
                operation="highlight_rebuild",
                mode=req.preview_mode,
                options=req.model_dump(
                    exclude={"preview_token", "preview_mode"},
                    exclude_unset=True,
                ),
            )
        except ExecutionPreviewError as exc:
            raise HTTPException(409, str(exc)) from exc

    task.status = TaskStatus.AUDIT_RUNNING
    task.error_message = None
    store.update(task)
    _start_task_runner(
        task_id,
        _run_highlight_rebuild(task_id, req),
        TaskStatus.AUDIT_RUNNING,
        operation="highlight_rebuild",
    )
    await manager.broadcast(task_id, {
        "type": "status_change",
        "task_id": task_id,
        "status": TaskStatus.AUDIT_RUNNING,
    })
    return {"success": True, "message": "产品集锦计划更新已启动"}


async def _run_highlight_rebuild(task_id: str, req: HighlightReelBuildOptions):
    async def log(level, source, msg, progress=None, **extra):
        await log_to_ws(task_id, level, source, msg, progress, **extra)

    try:
        reels, report = await _refresh_highlight_plan(
            task_id,
            options=req,
            log_callback=log,
        )
    except asyncio.CancelledError:
        if not _task_runner_superseded(task_id):
            await _rollback_cancelled_task(task_id, TaskStatus.REVIEW, "highlight", "产品集锦生成")
        return
    except Exception as exc:
        task = get_store().get(task_id)
        if task:
            task.status = TaskStatus.REVIEW
            task.error_message = str(exc)
            get_store().update(task)
            await manager.broadcast(task_id, {
                "type": "status_change",
                "task_id": task_id,
                "status": TaskStatus.REVIEW,
            })
        await log(
            "error",
            "highlight",
            f"产品集锦计划生成失败: {exc}",
            stage="reel_failed",
        )
        mark_current_execution_failed(task_id, exc)
        return

    visible_reels = [reel for reel in reels if not reel.hidden]
    await manager.broadcast(task_id, {
        "type": "highlight_reels_ready",
        "task_id": task_id,
        "reels_count": len(visible_reels),
        "canonical_reels_count": len(reels),
        "quality_passed": report.passed,
    })
    task = get_store().get(task_id)
    if task:
        task.status = TaskStatus.REVIEW
        task.error_message = None
        get_store().update(task)
    await manager.broadcast(task_id, {
        "type": "status_change",
        "task_id": task_id,
        "status": TaskStatus.REVIEW,
    })


@router.post("/api/tasks/{task_id}/highlight-reels/validate")
async def validate_highlight_reels_api(task_id: str):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    reels, report = await _refresh_highlight_plan(task_id)
    task = get_store().get(task_id)
    return _highlight_reels_response(task) if task else {
        "reels": [reel.model_dump() for reel in reels],
        "quality_report": report.model_dump(),
    }


@router.post("/api/tasks/{task_id}/highlight-reels/auto-run")
async def auto_run_highlight_reel(task_id: str, req: HighlightReelAutoRunRequest):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.task_type != TaskType.HIGHLIGHT_REEL:
        raise HTTPException(400, "仅直播卖货集锦任务支持自动流程")
    if req.preview_token:
        try:
            validate_execution_preview_token(
                req.preview_token,
                task=task,
                operation="highlight_auto_run",
                mode=req.preview_mode,
                options=req.model_dump(
                    exclude={"preview_token", "preview_mode"},
                    exclude_unset=True,
                ),
            )
        except ExecutionPreviewError as exc:
            raise HTTPException(409, str(exc)) from exc

    if task.audit_segments:
        running_status = TaskStatus.AUDIT_RUNNING
    elif task.asr_result and task.asr_result.words:
        running_status = TaskStatus.ANALYZE_RUNNING
    else:
        running_status = TaskStatus.ASR_RUNNING
    _start_task_runner(
        task_id,
        _run_highlight_auto_pipeline(task_id, req),
        running_status,
        operation="highlight_auto_run",
    )
    return {"success": True, "message": "直播卖货集锦自动流程已启动"}


async def _run_highlight_auto_pipeline(task_id: str, req: HighlightReelAutoRunRequest):
    store = get_store()
    task = store.get(task_id)
    if not task:
        return

    async def log(level, source, msg, progress=None, **extra):
        await log_to_ws(task_id, level, source, msg, progress, **extra)

    try:
        emit_agent_hook(
            task_id,
            "before_agent_run",
            "orchestrate.clip_pipeline",
            payload={"stage": "highlight_auto_pipeline", "auto_export": req.auto_export},
        )
        if not task.audit_segments:
            await log("info", "orchestrator", "步骤 1/3：启动 ASR...", stage="asr")
            task.status = TaskStatus.ANALYZE_RUNNING if (task.asr_result and task.asr_result.words) else TaskStatus.ASR_RUNNING
            store.update(task)
            emit_tool_hook(
                task_id,
                "before_tool_call",
                "orchestrate.clip_pipeline",
                "run_asr",
                payload={"backend": req.asr_backend, "model": req.asr_model},
            )
            if not task.asr_result or not task.asr_result.words:
                await _run_asr_task(task_id, req.asr_model, req.asr_backend)
            task = store.get(task_id)
            if task and task.params.enable_diarization and task.asr_result and not task.asr_result.diar_segments:
                await log("info", "orchestrator", "步骤 1/3：启动说话人分离...", stage="diarization")
                await _run_asr_diarization_task(task_id)
            await log("info", "orchestrator", "步骤 1/3：启动文本整理...", stage="asr_analyze")
            await _run_asr_analyze_task(task_id)
            emit_tool_hook(
                task_id,
                "after_tool_call",
                "orchestrate.clip_pipeline",
                "run_asr",
                payload={"status": "completed"},
            )
            task = store.get(task_id)
            if not task or not task.audit_segments:
                raise RuntimeError("ASR 未完成，无法继续自动流程")

        await log("info", "orchestrator", "步骤 2/3：启动产品识别、语义审计与产品集锦计划...", stage="model_call")
        audit_req = AuditRequest(
            provider=req.provider,
            openai_api_key=req.openai_api_key,
            openai_model=req.openai_model,
            ollama_model=req.ollama_model,
            ollama_base_url=req.ollama_base_url,
            style_mode=req.style_mode,
            force_rule_engine=req.force_rule_engine,
        )
        result = await run_audit_pipeline(
            task_id,
            audit_req,
            log_callback=log,
            include_highlight_plan=True,
            highlight_build_options=req,
        )

        task = store.get(task_id)
        if not task:
            return

        visible_reels = [reel for reel in task.highlight_reels if not reel.hidden]
        await manager.broadcast(task_id, {
            "type": "highlight_reels_ready",
            "task_id": task_id,
            "reels_count": len(visible_reels),
            "canonical_reels_count": len(task.highlight_reels),
            "occurrences_count": len(task.product_occurrences),
            "strategies": ["faithful", "balanced", "concise"],
            "quality_passed": result.quality_passed,
        })

        if req.auto_export:
            await log("warn", "orchestrator", "自动导出需要人工确认，已停在 ReviewWorkbench 等待导出按钮确认")

        await log(
            "success",
            "orchestrator",
            f"自动流程完成：{len(visible_reels)} 个产品集锦待 review",
            stage="auto_done",
            reels_count=len(visible_reels),
            quality_passed=result.quality_passed,
        )

    except asyncio.CancelledError:
        if not _task_runner_superseded(task_id):
            await _rollback_cancelled_task(task_id, TaskStatus.REVIEW, "orchestrator", "直播卖货集锦自动流程")

    except AuditPipelineError as exc:
        task = store.get(task_id)
        if task:
            task.status = TaskStatus.ASR_DONE if exc.allow_rule_engine_fallback else TaskStatus.ERROR
            task.error_message = str(exc)
            store.update(task)
            await manager.broadcast(task_id, {
                "type": "status_change",
                "task_id": task_id,
                "status": task.status,
            })
            if exc.allow_rule_engine_fallback:
                await manager.broadcast(task_id, {
                    "type": "claude_failed",
                    "task_id": task_id,
                    "error": str(exc),
                    "error_type": exc.error_type,
                })
        await log("error", "orchestrator", f"直播卖货集锦自动流程失败: {exc}", error_type=exc.error_type, stage="failed")
        mark_current_execution_failed(task_id, exc)

    except Exception as exc:
        task = store.get(task_id)
        if task:
            task.status = TaskStatus.ERROR
            task.error_message = str(exc)
            store.update(task)
            await manager.broadcast(task_id, {
                "type": "status_change",
                "task_id": task_id,
                "status": task.status,
            })
        await log("error", "orchestrator", f"直播卖货集锦自动流程失败: {exc}", error_type="unexpected_failed", stage="failed")
        mark_current_execution_failed(task_id, exc)
