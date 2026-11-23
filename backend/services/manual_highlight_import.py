"""把 Cursor 返回的集锦 JSON 校验并升级为当前 product-group 文档结构。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Sequence

from ..agent_runtime.contracts import validate_highlight_reels_contract
from ..models.task import (
    HighlightReel,
    HighlightReelQualityReport,
    HighlightReelStrategy,
    HighlightReelVariant,
    HighlightStrategyQualityResult,
    ProductGroup,
    ProductOccurrence,
    Task,
)
from .highlight_reel_pipeline import select_best_product_reels
from .highlight_reel_plan import (
    apply_plan_issues_to_reels,
    build_highlight_reel_plan,
    product_group_as_occurrence,
    rebuild_reel_metrics,
    repair_highlight_reels,
    validate_highlight_reels_code,
)
from .product_occurrence_detector import (
    group_product_occurrences,
    identify_product_occurrences,
)


class ManualHighlightImportError(ValueError):
    """Cursor 结果无法安全映射到当前任务。"""


@dataclass
class CompiledManualHighlightImport:
    product_occurrences: List[ProductOccurrence]
    product_groups: List[ProductGroup]
    before_quality_reels: List[HighlightReel]
    before_quality_report: HighlightReelQualityReport
    winners: List[HighlightReel]
    winner_report: HighlightReelQualityReport
    strategy_quality_results: List[HighlightStrategyQualityResult]
    input_results_count: int
    repair_count: int

    def summary(self, *, expected_revision: int) -> Dict[str, Any]:
        issues = self.before_quality_report.issues
        return {
            "can_apply": True,
            "expected_revision": expected_revision,
            "input_results_count": self.input_results_count,
            "product_occurrences_count": len(self.product_occurrences),
            "product_groups_count": len(self.product_groups),
            "candidates_count": len(self.before_quality_reels),
            "winners_count": len(self.winners),
            "repair_count": self.repair_count,
            "issues_count": len(issues),
            "blocker_count": sum(issue.severity == "blocker" for issue in issues),
            "major_count": sum(issue.severity == "major" for issue in issues),
            "quality_passed": self.winner_report.passed,
        }


def _parse_result_object(result_json: str) -> Dict[str, Any]:
    text = result_json.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManualHighlightImportError(f"Cursor 结果不是有效 JSON：{exc.msg}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ManualHighlightImportError('Cursor 结果必须是 {"results":[...]} 对象')
    return payload


def _final_winner_report(
    candidate_report: HighlightReelQualityReport,
    winners: Sequence[HighlightReel],
) -> HighlightReelQualityReport:
    winner_ids = {winner.reel_id for winner in winners}
    winner_issues = [
        issue
        for issue in candidate_report.issues
        if issue.reel_id in winner_ids
    ]
    scores = [winner.score for winner in winners if winner.score is not None]
    return candidate_report.model_copy(
        deep=True,
        update={
            "passed": not any(
                issue.severity in {"blocker", "major"}
                for issue in winner_issues
            ),
            "score": (
                sum(scores) / len(scores)
                if scores
                else candidate_report.score
            ),
            "issues": winner_issues,
            "variant": HighlightReelVariant.AFTER_QUALITY,
        },
    )


def _selected_selling_points(
    segment_ids: Sequence[str],
    segment_map: Dict[str, Any],
) -> List[str]:
    return list(
        dict.fromkeys(
            point
            for segment_id in segment_ids
            for point in (
                segment_map[segment_id].product_semantic.selling_points
                if segment_map[segment_id].product_semantic
                else []
            )
            if point
        )
    )


def compile_manual_highlight_result(
    task: Task,
    result_json: str,
) -> CompiledManualHighlightImport:
    """纯函数式预览：解析旧 occurrence 或新 product-group 输出，不写任务。"""
    payload = _parse_result_object(result_json)
    raw_results = payload["results"]
    if not raw_results:
        raise ManualHighlightImportError("Cursor 结果 results 不能为空")

    occurrences = (
        [occurrence.model_copy(deep=True) for occurrence in task.product_occurrences]
        or identify_product_occurrences(task.audit_segments)
    )
    if not occurrences:
        raise ManualHighlightImportError("当前任务没有可映射的产品场次")
    groups = group_product_occurrences(occurrences, task.audit_segments)
    if not groups:
        raise ManualHighlightImportError("当前任务无法生成产品组")

    occurrence_by_id = {
        occurrence.occurrence_id: occurrence
        for occurrence in occurrences
    }
    group_by_id = {group.product_group_id: group for group in groups}
    group_by_occurrence_id = {
        occurrence_id: group
        for group in groups
        for occurrence_id in group.occurrence_ids
    }
    segment_map = {segment.id: segment for segment in task.audit_segments}
    source_rows: Dict[
        tuple[str, HighlightReelStrategy],
        List[tuple[float, Dict[str, Any]]],
    ] = {}
    seen_source_jobs: set[tuple[str, HighlightReelStrategy]] = set()
    legacy_scopes: set[str] = set()
    group_scopes: set[str] = set()

    for index, raw in enumerate(raw_results, start=1):
        if not isinstance(raw, dict):
            raise ManualHighlightImportError(f"results[{index}] 必须是对象")
        try:
            strategy = HighlightReelStrategy(str(raw.get("strategy") or ""))
        except ValueError as exc:
            raise ManualHighlightImportError(
                f"results[{index}] strategy 非法"
            ) from exc

        product_group_id = str(raw.get("product_group_id") or "").strip()
        occurrence_id = str(raw.get("occurrence_id") or "").strip()
        if product_group_id:
            group = group_by_id.get(product_group_id)
            source_scope_id = product_group_id
            group_scopes.add(product_group_id)
            allowed_ids = set(group.segment_ids) if group else set()
            source_start = group.start if group else 0.0
        else:
            group = group_by_occurrence_id.get(occurrence_id)
            occurrence = occurrence_by_id.get(occurrence_id)
            source_scope_id = occurrence_id
            legacy_scopes.add(occurrence_id)
            allowed_ids = set(occurrence.segment_ids) if occurrence else set()
            source_start = occurrence.start if occurrence else 0.0
        if not source_scope_id or not group:
            raise ManualHighlightImportError(
                f"results[{index}] 引用了未知产品范围"
            )

        job_id = str(raw.get("job_id") or "").strip()
        expected_job_id = f"{source_scope_id}:{strategy.value}"
        if job_id and job_id != expected_job_id:
            raise ManualHighlightImportError(
                f"results[{index}] job_id 应为 {expected_job_id}"
            )
        source_job = (source_scope_id, strategy)
        if source_job in seen_source_jobs:
            raise ManualHighlightImportError(f"重复 JOB：{expected_job_id}")
        seen_source_jobs.add(source_job)

        raw_segment_ids = raw.get("segment_ids")
        if not isinstance(raw_segment_ids, list) or not raw_segment_ids:
            raise ManualHighlightImportError(
                f"results[{index}] segment_ids 必须是非空数组"
            )
        selected_ids: List[str] = []
        seen_ids: set[str] = set()
        for value in raw_segment_ids:
            segment_id = str(value)
            if segment_id in seen_ids:
                continue
            if segment_id not in segment_map:
                raise ManualHighlightImportError(
                    f"results[{index}] 引用了未知片段 {segment_id}"
                )
            if segment_id not in allowed_ids:
                raise ManualHighlightImportError(
                    f"results[{index}] 片段 {segment_id} 超出产品范围"
                )
            seen_ids.add(segment_id)
            selected_ids.append(segment_id)
        normalized = dict(raw)
        normalized["segment_ids"] = selected_ids
        source_rows.setdefault(
            (group.product_group_id, strategy),
            [],
        ).append((source_start, normalized))

    strategies = set(HighlightReelStrategy)
    if group_scopes and legacy_scopes:
        raise ManualHighlightImportError("不能混用 product_group_id 与 occurrence_id 结果")
    if group_scopes:
        missing_scopes = set(group_by_id) - group_scopes
        expected_jobs = {
            (group_id, strategy)
            for group_id in group_by_id
            for strategy in strategies
        }
    else:
        missing_scopes = set(occurrence_by_id) - legacy_scopes
        expected_jobs = {
            (occurrence_id, strategy)
            for occurrence_id in occurrence_by_id
            for strategy in strategies
        }
    if missing_scopes:
        raise ManualHighlightImportError(
            f"Cursor 结果覆盖不完整，缺少 {len(missing_scopes)} 个产品范围"
        )
    missing_jobs = expected_jobs - seen_source_jobs
    if missing_jobs:
        example_scope, example_strategy = sorted(
            missing_jobs,
            key=lambda item: (item[0], item[1].value),
        )[0]
        raise ManualHighlightImportError(
            f"Cursor 结果缺少 {len(missing_jobs)} 个 JOB，例如 "
            f"{example_scope}:{example_strategy.value}"
        )

    base_candidates = build_highlight_reel_plan(
        task.audit_segments,
        options=None,
        product_groups=groups,
    )
    base_by_key = {
        (reel.product_group_id or reel.occurrence_id or "", reel.strategy_kind): reel
        for reel in base_candidates
    }
    candidates: List[HighlightReel] = []
    for group in groups:
        for strategy in HighlightReelStrategy:
            base = base_by_key.get((group.product_group_id, strategy))
            rows = sorted(
                source_rows.get((group.product_group_id, strategy), []),
                key=lambda item: item[0],
            )
            if not base or not rows:
                raise ManualHighlightImportError(
                    f"无法构建 {group.product_group_id}:{strategy.value}"
                )
            selected_set = {
                segment_id
                for _, row in rows
                for segment_id in row["segment_ids"]
            }
            selected_ids = sorted(
                selected_set,
                key=lambda segment_id: (
                    segment_map[segment_id].start,
                    segment_map[segment_id].end,
                    segment_id,
                ),
            )
            opening_anchor = rows[0][1]["segment_ids"][0]
            if opening_anchor in selected_ids and selected_ids.index(opening_anchor) > 1:
                selected_ids.remove(opening_anchor)
                selected_ids.insert(0, opening_anchor)
            title = next(
                (
                    str(row.get("title") or "").strip()
                    for _, row in rows
                    if str(row.get("title") or "").strip()
                ),
                base.title,
            )
            rationale = next(
                (
                    str(row.get("rationale") or "").strip()
                    for _, row in rows
                    if str(row.get("rationale") or "").strip()
                ),
                base.rationale,
            )
            candidate = base.model_copy(
                deep=True,
                update={
                    "title": title,
                    "rationale": rationale,
                    "segment_ids": selected_ids,
                    "hook_segment_id": opening_anchor,
                    "source_occurrence_ids": list(group.occurrence_ids),
                    "selected_selling_points": _selected_selling_points(
                        selected_ids,
                        segment_map,
                    ),
                },
            )
            candidates.append(
                rebuild_reel_metrics(candidate, task.audit_segments)
            )

    scopes = [product_group_as_occurrence(group) for group in groups]
    before_report = validate_highlight_reels_code(
        candidates,
        task.audit_segments,
        task.video_duration,
        product_occurrences=scopes,
    )
    before_report.variant = HighlightReelVariant.BEFORE_QUALITY
    before_quality = apply_plan_issues_to_reels(
        candidates,
        before_report,
        hide_hard_issues=False,
    )

    final_candidates = [reel.model_copy(deep=True) for reel in candidates]
    repair_count = 0
    candidate_report = before_report
    if not before_report.passed:
        repaired, actions = repair_highlight_reels(
            final_candidates,
            task.audit_segments,
            before_report,
            product_occurrences=scopes,
        )
        repair_count = len(actions)
        if actions:
            final_candidates = repaired
            groups_by_id = {
                group.product_group_id: group
                for group in groups
            }
            for reel in final_candidates:
                group = groups_by_id.get(
                    reel.product_group_id or reel.occurrence_id or ""
                )
                if group:
                    reel.product_group_id = group.product_group_id
                    reel.source_occurrence_ids = list(group.occurrence_ids)
            candidate_report = validate_highlight_reels_code(
                final_candidates,
                task.audit_segments,
                task.video_duration,
                product_occurrences=scopes,
            )
            candidate_report.repair_actions = actions
            candidate_report.repair_count = repair_count
    scored_candidates = apply_plan_issues_to_reels(
        final_candidates,
        candidate_report,
        hide_hard_issues=False,
    )
    winners = select_best_product_reels(scored_candidates)
    winner_report = _final_winner_report(candidate_report, winners)
    contract_report = validate_highlight_reels_contract(
        winners,
        task.audit_segments,
        task.video_duration,
        product_groups=groups,
    )
    if contract_report.rejected:
        detail = "；".join(
            issue.message for issue in contract_report.issues[:3]
        )
        raise ManualHighlightImportError(f"升级后集锦契约不通过：{detail}")

    quality_results = []
    for reel in scored_candidates:
        reel_issues = [
            issue
            for issue in candidate_report.issues
            if issue.reel_id in {None, reel.reel_id}
        ]
        quality_results.append(
            HighlightStrategyQualityResult(
                occurrence_id=reel.occurrence_id or reel.product_group_id or "",
                product_group_id=reel.product_group_id,
                strategy_kind=reel.strategy_kind,
                reel_id=reel.reel_id,
                passed=not any(
                    issue.severity in {"blocker", "major"}
                    for issue in reel_issues
                ),
                score=reel.score if reel.score is not None else candidate_report.score,
                issues=reel_issues,
                validated_at=candidate_report.validated_at or datetime.now().isoformat(),
            )
        )
    return CompiledManualHighlightImport(
        product_occurrences=occurrences,
        product_groups=groups,
        before_quality_reels=before_quality,
        before_quality_report=before_report,
        winners=winners,
        winner_report=winner_report,
        strategy_quality_results=quality_results,
        input_results_count=len(raw_results),
        repair_count=repair_count,
    )


def apply_compiled_manual_highlight_result(
    task: Task,
    compiled: CompiledManualHighlightImport,
) -> Task:
    """把已预览结果写到 Task 副本；持久化和 CAS 由路由负责。"""
    updated = task.model_copy(deep=True)
    updated.highlight_revision = (updated.highlight_revision or 0) + 1
    updated.product_occurrences = [
        occurrence.model_copy(deep=True)
        for occurrence in compiled.product_occurrences
    ]
    updated.product_groups = [
        group.model_copy(deep=True)
        for group in compiled.product_groups
    ]
    updated.highlight_reels_before_quality = [
        reel.model_copy(
            deep=True,
            update={"source_content_revision": updated.content_revision or 0},
        )
        for reel in compiled.before_quality_reels
    ]
    updated.highlight_quality_report_before_quality = (
        compiled.before_quality_report.model_copy(deep=True)
    )
    updated.highlight_reels = [
        reel.model_copy(
            deep=True,
            update={"source_content_revision": updated.content_revision or 0},
        )
        for reel in compiled.winners
    ]
    updated.highlight_quality_report = compiled.winner_report.model_copy(deep=True)
    updated.highlight_strategy_quality_results = [
        result.model_copy(deep=True)
        for result in compiled.strategy_quality_results
    ]
    updated.update_stats()
    return updated
