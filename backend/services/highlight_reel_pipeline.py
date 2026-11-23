"""直播卖货产品集锦计划生成、质检和写回编排。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException

from ..agent_runtime.contracts import (
    validate_highlight_reels_contract,
    validate_product_groups_contract,
)
from ..agent_runtime.harness import verify_contract_report
from ..agent_runtime.hooks import emit_agent_hook, emit_tool_hook
from ..models.task import (
    HighlightReel,
    HighlightReelBuildOptions,
    HighlightReelQualityReport,
    HighlightReelRepairAction,
    HighlightReelStrategy,
    HighlightReelVariant,
    HighlightStrategyQualityResult,
    ProductGroup,
    Segment,
)
from ..services.highlight_reel_plan import (
    apply_plan_issues_to_reels,
    highlight_reel_plan_fingerprint,
    product_group_as_occurrence,
    repair_highlight_reels,
)
from ..services.app_config_store import load_app_config
from ..services.highlight_strategy_generator import generate_product_strategy_reels
from ..services.highlight_model_executor import HighlightModelCallBudget
from ..services.highlight_reel_validator import run_highlight_reel_validation
from ..services.product_occurrence_detector import (
    group_product_occurrences,
    identify_product_occurrences,
)
from ..services.task_store import RevisionConflictError, get_store

LogCallback = Callable[..., Awaitable[None]]
_REFRESH_LOCKS: Dict[tuple[int, str], asyncio.Lock] = {}


class HighlightPipelineError(RuntimeError):
    """产品集锦计划阶段失败，供上层转译为用户可理解的错误类型。"""

    def __init__(
        self,
        message: str,
        error_type: str = "highlight_pipeline_failed",
        *,
        retryable: bool = False,
        on_fail: Optional[str] = None,
    ):
        super().__init__(message)
        self.error_type = error_type
        self.retryable = retryable
        self.on_fail = on_fail


def _refresh_lock(task_id: str) -> asyncio.Lock:
    key = (id(asyncio.get_running_loop()), task_id)
    lock = _REFRESH_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _REFRESH_LOCKS[key] = lock
    return lock


def _highlight_source_fingerprint(task) -> str:
    payload = {
        "audit_segments": [
            segment.model_dump(mode="json") for segment in task.audit_segments
        ],
        "existing_reels": [
            reel.model_dump(mode="json")
            for reel in (task.highlight_reels or task.highlight_reels_before_quality)
        ],
        "content_revision": task.content_revision,
        "subtitle_revision": task.subtitle_revision,
        "highlight_revision": task.highlight_revision,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


_WINNER_STRATEGY_RANK = {
    HighlightReelStrategy.BALANCED: 0,
    HighlightReelStrategy.CONCISE: 1,
    HighlightReelStrategy.FAITHFUL: 2,
    None: 3,
}


def _winner_rank(reel: HighlightReel) -> tuple:
    blockers = sum(issue.severity == "blocker" for issue in reel.issues)
    majors = sum(issue.severity == "major" for issue in reel.issues)
    unverified = any(issue.code == "HR-MODEL-UNVERIFIED" for issue in reel.issues)
    score = reel.score if reel.score is not None else 0.0
    duration_penalty = abs(reel.duration - 75.0)
    return (
        blockers,
        majors,
        1 if unverified else 0,
        -score,
        _WINNER_STRATEGY_RANK.get(reel.strategy_kind, 3),
        -len(reel.selected_selling_points),
        duration_penalty,
        reel.reel_id,
    )


def select_best_product_reels(candidates: List[HighlightReel]) -> List[HighlightReel]:
    """每个 product group 只保留一个稳定 winner；三策略仅作为内部候选。"""
    by_group: Dict[str, List[HighlightReel]] = {}
    for candidate in candidates:
        group_id = candidate.product_group_id or candidate.occurrence_id or candidate.reel_id
        by_group.setdefault(group_id, []).append(candidate)
    winners: List[HighlightReel] = []
    strategy_suffixes = (" · 忠实版", " · 均衡版", " · 精简版")
    for group_candidates in by_group.values():
        winner = min(group_candidates, key=_winner_rank).model_copy(deep=True)
        for suffix in strategy_suffixes:
            if winner.title.endswith(suffix):
                winner.title = winner.title[: -len(suffix)]
                break
        winner.hidden = False
        winners.append(winner)
    winners.sort(key=lambda reel: (reel.start, reel.end, reel.product_group_id or "", reel.reel_id))
    for index, winner in enumerate(winners, start=1):
        winner.clip_group = index
    return winners


def _product_group_rank(group: ProductGroup, segments_by_id: Dict[str, Segment]) -> tuple:
    selected = [
        segments_by_id[segment_id]
        for segment_id in group.segment_ids
        if segment_id in segments_by_id
    ]
    duration = sum(max(0.0, segment.end - segment.start) for segment in selected)
    selling_points = {
        point
        for segment in selected
        for point in (
            segment.product_semantic.selling_points
            if segment.product_semantic
            else []
        )
        if point
    }
    semantic_roles = {
        role
        for segment in selected
        for role in (
            segment.product_semantic.roles
            if segment.product_semantic
            else []
        )
    }
    return (
        -duration,
        -(group.confidence or 0.0),
        -len(selling_points),
        -len(semantic_roles),
        -len(selected),
        group.start,
        group.product_group_id,
    )


def _final_winner_report(
    candidate_report: HighlightReelQualityReport,
    winners: List[HighlightReel],
) -> HighlightReelQualityReport:
    winner_ids = {winner.reel_id for winner in winners}
    issues = [
        issue
        for issue in candidate_report.issues
        if issue.reel_id in winner_ids
    ]
    issues_by_reel = {
        winner.reel_id: [
            issue for issue in issues if issue.reel_id == winner.reel_id
        ]
        for winner in winners
    }
    scores = []
    for winner in winners:
        winner_issues = issues_by_reel[winner.reel_id]
        blockers = sum(issue.severity == "blocker" for issue in winner_issues)
        majors = sum(issue.severity == "major" for issue in winner_issues)
        minors = sum(issue.severity == "minor" for issue in winner_issues)
        if winner.score is not None:
            score = winner.score
            if blockers:
                score = min(score, 0.4)
            elif majors:
                score = min(score, 0.69)
            elif minors:
                score = min(score, 0.89)
            scores.append(score)
            continue
        scores.append(
            max(
                0.0,
                1.0
                - 0.2 * blockers
                - 0.1 * majors
                - 0.03 * len(winner_issues),
            )
        )
    degraded = bool(
        candidate_report.degraded
        or any(issue.code == "HR-MODEL-UNVERIFIED" for issue in issues)
    )
    return candidate_report.model_copy(
        deep=True,
        update={
            "passed": not any(issue.severity in {"blocker", "major"} for issue in issues),
            "score": sum(scores) / len(scores) if scores else candidate_report.score,
            "issues": issues,
            "repair_actions": [
                action
                for action in candidate_report.repair_actions
                if action.reel_id in winner_ids
            ],
            "proposed_repair_actions": [
                action
                for action in candidate_report.proposed_repair_actions
                if action.reel_id in winner_ids
            ],
            "degraded": degraded,
            "variant": HighlightReelVariant.AFTER_QUALITY,
        },
    )


async def _stage_log(
    log_callback: Optional[LogCallback],
    level: str,
    source: str,
    message: str,
    progress: Optional[float] = None,
    *,
    stage: str,
    **extra,
) -> None:
    if not log_callback:
        return
    try:
        await log_callback(level, source, message, progress, stage=stage, **extra)
    except TypeError:
        try:
            await log_callback(level, source, message, progress)
        except Exception:
            return
    except Exception:
        return


async def refresh_highlight_plan(
    task_id: str,
    *,
    options: Optional[HighlightReelBuildOptions] = None,
    run_model_validation: bool = True,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    ollama_model: Optional[str] = None,
    ollama_base_url: Optional[str] = None,
    log_callback: Optional[LogCallback] = None,
) -> Tuple[List[HighlightReel], HighlightReelQualityReport]:
    """同一任务的集锦刷新串行执行，不同任务可共享供应商并发配额。"""
    async with _refresh_lock(task_id):
        return await _refresh_highlight_plan_locked(
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


async def _refresh_highlight_plan_locked(
    task_id: str,
    *,
    options: Optional[HighlightReelBuildOptions] = None,
    run_model_validation: bool = True,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    ollama_model: Optional[str] = None,
    ollama_base_url: Optional[str] = None,
    log_callback: Optional[LogCallback] = None,
) -> Tuple[List[HighlightReel], HighlightReelQualityReport]:
    """从当前 audit_segments 生成 canonical 产品集锦计划，并写回任务。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    task = task.model_copy(deep=True)
    source_fingerprint = _highlight_source_fingerprint(task)
    highlight_runtime = load_app_config().highlight_runtime
    max_parallel_calls = highlight_runtime.cloud_max_parallel_calls
    call_budget = HighlightModelCallBudget(
        max_calls=highlight_runtime.max_model_calls_per_run
    )

    await _stage_log(
        log_callback,
        "info",
        "highlight",
        "正在生成产品集锦计划...",
        0.88,
        stage="reel_plan",
    )
    emit_agent_hook(
        task_id,
        "before_agent_run",
        "detect.product_occurrences",
        payload={"segments_count": len(task.audit_segments)},
    )
    occurrences = identify_product_occurrences(task.audit_segments)
    all_product_groups = group_product_occurrences(occurrences, task.audit_segments)
    await verify_contract_report(
        task_id,
        "product_group_detection",
        validate_product_groups_contract(
            occurrences,
            all_product_groups,
            task.audit_segments,
        ),
        capability="detect.product_occurrences",
        log_callback=log_callback,
    )
    product_groups = list(all_product_groups)
    target_count = (
        (options.target_product_count or options.target_reel_count)
        if options
        else None
    )
    if target_count:
        segments_by_id = {segment.id: segment for segment in task.audit_segments}
        product_groups = sorted(
            product_groups,
            key=lambda group: _product_group_rank(group, segments_by_id),
        )[:target_count]
        product_groups.sort(key=lambda group: (group.start, group.end, group.product_group_id))
    candidate_scopes = [product_group_as_occurrence(group) for group in product_groups]
    emit_agent_hook(
        task_id,
        "after_model_output",
        "detect.product_occurrences",
        payload={
            "occurrences_count": len(occurrences),
            "product_groups_count": len(product_groups),
        },
    )
    source_reels = task.highlight_reels or task.highlight_reels_before_quality
    emit_agent_hook(
        task_id,
        "before_agent_run",
        "generate.highlight_reels",
        payload={"product_groups_count": len(product_groups), "candidates_per_product": 3},
    )
    reels = await generate_product_strategy_reels(
        task.audit_segments,
        candidate_scopes,
        source_reels,
        options,
        provider=provider,
        api_key=api_key,
        model=model,
        ollama_model=ollama_model,
        ollama_base_url=ollama_base_url,
        log_callback=log_callback,
        use_model=run_model_validation and (options.run_model_generation if options else True),
        max_parallel_calls=max_parallel_calls,
        use_role_routing=True,
        product_groups=product_groups,
        call_budget=call_budget,
    )
    emit_agent_hook(
        task_id,
        "after_model_output",
        "generate.highlight_reels",
        payload={"reels_count": len(reels)},
    )

    emit_agent_hook(
        task_id,
        "before_agent_run",
        "validate.highlight_reels",
        payload={"reels_count": len(reels), "use_model": run_model_validation},
    )
    await _stage_log(
        log_callback,
        "info",
        "validator",
        "正在执行产品集锦验收与自动优化...",
        0.9,
        stage="reel_validate",
        reels_count=len(reels),
    )
    try:
        initial_report = await run_highlight_reel_validation(
            reels,
            task.audit_segments,
            task.video_duration,
            options,
            candidate_scopes,
            provider=provider,
            api_key=api_key,
            model=model,
            ollama_model=ollama_model,
            ollama_base_url=ollama_base_url,
            log_callback=log_callback,
            use_model=run_model_validation,
            max_parallel_calls=max_parallel_calls,
            use_role_routing=True,
            call_budget=call_budget,
        )
    except Exception as exc:
        raise HighlightPipelineError(
            str(exc),
            "highlight_validation_failed",
            retryable=bool(getattr(exc, "retryable", False)),
            on_fail=getattr(exc, "on_fail", None),
        ) from exc

    before_quality_report = initial_report.model_copy(deep=True)
    before_quality_report.variant = HighlightReelVariant.BEFORE_QUALITY
    before_quality_reels = apply_plan_issues_to_reels(
        reels,
        before_quality_report,
        hide_hard_issues=False,
    )
    final_reels = reels
    report = initial_report
    repair_actions = []
    if not initial_report.passed:
        hard_issues = [
            issue
            for issue in initial_report.issues
            if issue.severity in {"blocker", "major"}
        ]
        failed_reel_ids = {
            issue.reel_id for issue in hard_issues if issue.reel_id
        }
        if any(issue.reel_id is None for issue in hard_issues) or not failed_reel_ids:
            failed_reel_ids = {reel.reel_id for reel in reels}
        repaired_reels, repair_actions = repair_highlight_reels(
            reels,
            task.audit_segments,
            initial_report,
            options,
            candidate_scopes,
        )
        groups_by_id = {group.product_group_id: group for group in product_groups}
        for repaired in repaired_reels:
            group = groups_by_id.get(repaired.product_group_id or repaired.occurrence_id or "")
            if group:
                repaired.product_group_id = group.product_group_id
                repaired.source_occurrence_ids = list(group.occurrence_ids)
        if run_model_validation and (options.run_model_generation if options else True):
            await _stage_log(
                log_callback,
                "info",
                "highlight",
                "正在把首轮质检证据反馈给策略模型重新生成失败候选...",
                0.905,
                stage="reel_repair",
            )
            regenerated_reels = await generate_product_strategy_reels(
                task.audit_segments,
                candidate_scopes,
                repaired_reels,
                options,
                provider=provider,
                api_key=api_key,
                model=model,
                ollama_model=ollama_model,
                ollama_base_url=ollama_base_url,
                log_callback=log_callback,
                use_model=True,
                max_parallel_calls=max_parallel_calls,
                use_role_routing=True,
                product_groups=product_groups,
                quality_feedback=initial_report.issues,
                target_reel_ids=failed_reel_ids,
                call_budget=call_budget,
            )
            before_by_id = {reel.reel_id: reel for reel in repaired_reels}
            for regenerated in regenerated_reels:
                previous = before_by_id.get(regenerated.reel_id)
                if previous and (
                    previous.segment_ids != regenerated.segment_ids
                    or previous.title != regenerated.title
                ):
                    repair_actions.append(
                        HighlightReelRepairAction(
                            action="regenerate_with_quality_feedback",
                            reel_id=regenerated.reel_id,
                            segment_ids=list(regenerated.segment_ids),
                            reason="将首轮质检问题反馈给策略模型后重新生成候选",
                        )
                    )
            repaired_reels = regenerated_reels
        if highlight_reel_plan_fingerprint(repaired_reels) != highlight_reel_plan_fingerprint(reels):
            await _stage_log(
                log_callback,
                "info",
                "validator",
                f"集锦验收发现问题，已自动修订 {len(repair_actions)} 项并重新验收...",
                0.91,
                stage="reel_validate",
                repaired_count=len(repair_actions),
            )
            try:
                report = await run_highlight_reel_validation(
                    repaired_reels,
                    task.audit_segments,
                    task.video_duration,
                    options,
                    candidate_scopes,
                    provider=provider,
                    api_key=api_key,
                    model=model,
                    ollama_model=ollama_model,
                    ollama_base_url=ollama_base_url,
                    log_callback=log_callback,
                    use_model=run_model_validation,
                    max_parallel_calls=max_parallel_calls,
                    use_role_routing=True,
                    target_reel_ids=failed_reel_ids,
                    previous_report=initial_report,
                    call_budget=call_budget,
                )
                final_reels = repaired_reels
            except Exception as exc:
                raise HighlightPipelineError(
                    str(exc),
                    "highlight_validation_failed",
                    retryable=bool(getattr(exc, "retryable", False)),
                    on_fail=getattr(exc, "on_fail", None),
                ) from exc
        else:
            await _stage_log(
                log_callback,
                "warn",
                "validator",
                "集锦验收发现问题，但没有可安全自动修订的集锦，保留候选并进入人工复核。",
                0.91,
                stage="reel_validate",
            )
    report.proposed_repair_actions = list(
        {
            (
                action.action,
                action.reel_id,
                tuple(action.segment_ids),
                action.reason,
            ): action
            for action in (
                list(initial_report.proposed_repair_actions)
                + list(report.proposed_repair_actions)
            )
        }.values()
    )
    report.repair_actions = list(report.repair_actions) + repair_actions
    report.repair_count = len(repair_actions)
    report.variant = HighlightReelVariant.AFTER_QUALITY
    await _stage_log(
        log_callback,
        "info",
        "highlight",
        (
            f"本轮模型逻辑调用 {call_budget.used_calls}/"
            f"{call_budget.max_calls}，剩余 {call_budget.remaining_calls}"
        ),
        stage="reel_usage",
        model_calls=call_budget.used_calls,
        model_call_budget=call_budget.max_calls,
    )
    emit_agent_hook(
        task_id,
        "after_model_output",
        "validate.highlight_reels",
        payload={"passed": report.passed, "issues_count": len(report.issues)},
    )

    await _stage_log(
        log_callback,
        "info",
        "highlight",
        "正在保存产品集锦计划...",
        0.92,
        stage="reel_sync",
        reels_count=len(final_reels),
        quality_passed=report.passed,
    )
    candidate_report = report
    candidate_reels = apply_plan_issues_to_reels(
        final_reels,
        candidate_report,
        hide_hard_issues=False,
    )
    reels = select_best_product_reels(candidate_reels)
    report = _final_winner_report(candidate_report, reels)
    contract_report = validate_highlight_reels_contract(
        reels,
        task.audit_segments,
        task.video_duration,
        options,
        candidate_scopes,
        product_groups=product_groups,
    )
    try:
        await verify_contract_report(task_id, "highlight_reels", contract_report, log_callback=log_callback)
    except Exception as exc:
        raise HighlightPipelineError(
            str(exc),
            "highlight_contract_failed",
            retryable=bool(getattr(exc, "retryable", False)),
            on_fail=getattr(exc, "on_fail", None),
        ) from exc

    emit_tool_hook(
        task_id,
        "before_tool_call",
        "orchestrate.clip_pipeline",
        "write_audit_segments",
        payload={"stage": "sync_highlight_plan", "reels_count": len(reels)},
    )
    saved_task = None
    for attempt in range(2):
        latest = store.get(task_id)
        if not latest:
            raise HTTPException(404, "Task not found")
        if _highlight_source_fingerprint(latest) != source_fingerprint:
            raise HighlightPipelineError(
                "集锦生成期间字幕、审计结果或人工集锦已变化，已放弃写回旧结果",
                "highlight_source_changed",
            )
        write_task = latest.model_copy(deep=True)
        write_task.highlight_revision = (write_task.highlight_revision or 0) + 1
        saved_before_quality_reels = [
            reel.model_copy(deep=True) for reel in before_quality_reels
        ]
        saved_reels = [reel.model_copy(deep=True) for reel in reels]
        for reel in saved_before_quality_reels + saved_reels:
            reel.source_content_revision = write_task.content_revision or 0
        write_task.product_occurrences = [
            occurrence.model_copy(deep=True) for occurrence in occurrences
        ]
        write_task.product_groups = [
            group.model_copy(deep=True) for group in all_product_groups
        ]
        write_task.highlight_reels_before_quality = saved_before_quality_reels
        write_task.highlight_quality_report_before_quality = before_quality_report.model_copy(deep=True)
        write_task.highlight_reels = saved_reels
        write_task.highlight_quality_report = report.model_copy(deep=True)
        write_task.highlight_strategy_quality_results = []
        for reel in candidate_reels:
            if not reel.occurrence_id or not reel.strategy_kind:
                continue
            reel_issues = [
                issue
                for issue in candidate_report.issues
                if issue.reel_id in {None, reel.reel_id}
            ]
            write_task.highlight_strategy_quality_results.append(
                HighlightStrategyQualityResult(
                    occurrence_id=reel.occurrence_id,
                    product_group_id=reel.product_group_id,
                    strategy_kind=reel.strategy_kind,
                    reel_id=reel.reel_id,
                    passed=not any(issue.severity in {"blocker", "major"} for issue in reel_issues),
                    score=reel.score if reel.score is not None else candidate_report.score,
                    issues=reel_issues,
                    validated_at=candidate_report.validated_at,
                )
            )
        write_task.update_stats()
        try:
            try:
                saved_task = store.update(write_task, expected_revision=latest.revision)
            except TypeError as exc:
                # 测试/嵌入式 Store 可能只实现旧版 update(task) 协议。
                if "expected_revision" not in str(exc):
                    raise
                latest.highlight_revision = write_task.highlight_revision
                latest.product_occurrences = write_task.product_occurrences
                latest.product_groups = write_task.product_groups
                latest.highlight_reels_before_quality = write_task.highlight_reels_before_quality
                latest.highlight_quality_report_before_quality = write_task.highlight_quality_report_before_quality
                latest.highlight_reels = write_task.highlight_reels
                latest.highlight_quality_report = write_task.highlight_quality_report
                latest.highlight_strategy_quality_results = write_task.highlight_strategy_quality_results
                latest.update_stats()
                saved_task = store.update(latest)
            break
        except RevisionConflictError:
            if attempt == 1:
                raise HighlightPipelineError(
                    "保存集锦结果时任务已被并发修改，请重试",
                    "highlight_write_conflict",
                )
    if saved_task is None:
        raise HighlightPipelineError("集锦结果未能保存", "highlight_write_conflict")
    emit_tool_hook(
        task_id,
        "after_tool_call",
        "orchestrate.clip_pipeline",
        "write_audit_segments",
        payload={"stage": "sync_highlight_plan", "status": saved_task.status.value},
    )
    await _stage_log(
        log_callback,
        "success" if report.passed else "warn",
        "highlight",
        f"产品集锦计划已生成：{len([reel for reel in reels if not reel.hidden])} 个可见产品集锦",
        0.94,
        stage="reel_ready",
        reels_count=len([reel for reel in reels if not reel.hidden]),
        quality_passed=report.passed,
    )
    return reels, report
