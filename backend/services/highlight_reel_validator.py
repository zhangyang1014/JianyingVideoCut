"""
产品集锦质检 Agent 服务：在代码校验之后做模型语义验收。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..core.task_runner import RetryableExecutionError, execute_with_retry
from ..models.app_config import DEFAULT_HIGHLIGHT_REEL_MODEL, ROUTERHUB_BASE_URL
from ..models.task import (
    HighlightReel,
    HighlightReelBuildOptions,
    HighlightReelIssue,
    HighlightReelQualityReport,
    HighlightReelRepairAction,
    HighlightReelVariant,
    ProductOccurrence,
    Segment,
)
from .app_config_store import merge_feature_llm_request, role_primary_or_feature, resolve_role_candidates
from .llm_router import route_chat_completion
from .highlight_model_executor import (
    HighlightModelBatchAborted,
    HighlightModelCallBudget,
    HighlightModelExecutor,
    HighlightModelFatalError,
)
from .highlight_reel_plan import validate_highlight_reels_code
from .prompt_registry import resolve_prompt_content
from .semantic_auditor import (
    MODEL_CACHE_DIR,
    content_cache_fingerprint,
    load_content_cache,
    save_content_cache,
)
from .subtitle_text import get_effective_subtitle_text

HIGHLIGHT_VALIDATION_CACHE_DIR = MODEL_CACHE_DIR / "highlight_validation"
LogCallback = Callable[..., Awaitable[None]]

_MODEL_ISSUE_SEVERITY = {
    "HR-M01": "major",
    "HR-M02": "major",
    "HR-M03": "major",
    "HR-M04": "major",
    "HR-M05": "major",
    "HR-M06": "minor",
    "HR-M07": "minor",
    "HR-M09": "blocker",
    "HR-M10": "major",
    "HR-M11": "major",
    "HR-M12": "minor",
}
_MODEL_REPAIR_ACTIONS = {"remove_cta", "split_product", "retitle", "choose_opening"}


class _ModelValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Literal["blocker", "major", "minor"]
    message: str = Field(min_length=1)
    reel_id: str = Field(min_length=1)
    suggestion: Optional[str] = None

    @model_validator(mode="after")
    def validate_code_and_severity(self):
        expected = _MODEL_ISSUE_SEVERITY.get(self.code)
        if expected is None:
            raise ValueError(f"不支持的模型质检问题码：{self.code}")
        if self.severity != expected:
            raise ValueError(f"{self.code} 的 severity 必须是 {expected}")
        return self


class _ModelRepairAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    reel_id: str = Field(min_length=1)
    segment_ids: List[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_action(self):
        if self.action not in _MODEL_REPAIR_ACTIONS:
            raise ValueError(f"不支持的修复动作：{self.action}")
        if self.action in {"remove_cta", "split_product", "choose_opening"} and not self.segment_ids:
            raise ValueError(f"{self.action} 必须引用 segment_ids")
        if len(self.segment_ids) != len(set(self.segment_ids)):
            raise ValueError("修复动作包含重复 segment_id")
        return self


class _ModelValidationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    validator_model: str = Field(min_length=1)
    issues: List[_ModelValidationIssue]
    repair_actions: List[_ModelRepairAction]

    @model_validator(mode="after")
    def validate_decision(self):
        hard_issues = [
            issue for issue in self.issues if issue.severity in {"blocker", "major"}
        ]
        if self.passed == bool(hard_issues):
            raise ValueError("passed 必须与 blocker/major 是否存在保持一致")
        if not self.issues and self.score < 0.9:
            raise ValueError("无问题时 score 不得低于 0.9")
        if self.issues and not hard_issues and not (0.75 <= self.score <= 0.89):
            raise ValueError("只有 minor 时 score 必须在 0.75-0.89")
        if any(issue.severity == "blocker" for issue in self.issues) and self.score > 0.4:
            raise ValueError("存在 blocker 时 score 不得高于 0.4")
        if hard_issues and not any(issue.severity == "blocker" for issue in self.issues) and self.score > 0.69:
            raise ValueError("存在 major 时 score 不得高于 0.69")
        return self


@dataclass(frozen=True)
class _ValidationResult:
    reel_id: str
    report: Optional[HighlightReelQualityReport]
    elapsed: float
    error: Optional[str] = None


async def _safe_log(
    log_callback: Optional[LogCallback],
    level: str,
    source: str,
    message: str,
    **extra,
) -> None:
    if not log_callback:
        return
    try:
        await log_callback(level, source, message, **extra)
    except Exception:
        return


def _load_validation_overlay() -> str:
    return resolve_prompt_content("highlight_reel.validation")


def _format_elapsed(seconds: float) -> str:
    if seconds >= 60:
        minutes = int(seconds // 60)
        remainder = seconds - minutes * 60
        return f"{minutes}分{remainder:.1f}秒"
    return f"{seconds:.1f}秒"


def _build_validation_payload(
    reels: List[HighlightReel],
    segments: List[Segment],
    product_occurrences: Optional[List[ProductOccurrence]] = None,
    code_report: Optional[HighlightReelQualityReport] = None,
) -> str:
    segment_map = {seg.id: seg for seg in segments}
    scope_map = {
        occurrence.occurrence_id: occurrence
        for occurrence in (product_occurrences or [])
    }
    payload = []
    for reel in reels:
        if reel.hidden:
            continue
        scope_id = reel.product_group_id or reel.occurrence_id or ""
        scope = scope_map.get(scope_id) or scope_map.get(reel.occurrence_id or "")
        texts = []
        for segment_id in reel.segment_ids:
            segment = segment_map.get(segment_id)
            if segment:
                display_text = get_effective_subtitle_text(segment, include_speaker_label=False)
                texts.append(
                    {
                        "segment_id": segment_id,
                        "start": segment.start,
                        "end": segment.end,
                        "text": display_text,
                        "raw_text": segment.text,
                        "style": segment.style,
                        "product_semantic": segment.product_semantic.model_dump()
                        if segment.product_semantic
                        else None,
                    }
                )
        payload.append(
            {
                "reel_id": reel.reel_id,
                "occurrence_id": reel.occurrence_id,
                "product_group_id": reel.product_group_id or reel.occurrence_id,
                "canonical_name": scope.name if scope else reel.product_name,
                "aliases": list(scope.aliases) if scope else [],
                "source_occurrence_ids": list(reel.source_occurrence_ids),
                "product_group_boundary": (
                    {"start": scope.start, "end": scope.end}
                    if scope
                    else {"start": reel.start, "end": reel.end}
                ),
                "allowed_segment_ids": list(scope.segment_ids) if scope else list(reel.segment_ids),
                "strategy_kind": reel.strategy_kind.value if reel.strategy_kind else None,
                "clip_group": reel.clip_group,
                "title": reel.title,
                "product_name": reel.product_name,
                "angle_label": reel.angle_label,
                "semantic_roles": reel.semantic_roles,
                "duration": reel.duration,
                "retention_ratio": reel.retention_ratio,
                "content_retention_ratio": reel.content_retention_ratio,
                "source_duration": reel.source_duration,
                "code_issues": [
                    issue.model_dump(mode="json")
                    for issue in (code_report.issues if code_report else [])
                    if issue.reel_id in {None, reel.reel_id}
                ],
                "segments": texts,
            }
        )
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _parse_validation_response(
    response_text: str,
    code_report: HighlightReelQualityReport,
    *,
    strict: bool = False,
    expected_reel_id: Optional[str] = None,
    allowed_segment_ids: Optional[set[str]] = None,
) -> HighlightReelQualityReport:
    json_text = response_text.strip()
    json_text = re.sub(r"^```(?:json)?\s*", "", json_text, flags=re.MULTILINE)
    json_text = re.sub(r"\s*```$", "", json_text, flags=re.MULTILINE).strip()

    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", json_text, re.DOTALL)
        if not match:
            if strict:
                raise ValueError("集锦验收响应未包含 JSON 对象")
            return code_report
        try:
            parsed = json.loads(match.group())
        except json.JSONDecodeError:
            if strict:
                raise ValueError("集锦验收响应 JSON 解析失败")
            return code_report
    if not isinstance(parsed, dict):
        if strict:
            raise ValueError("集锦验收响应必须是 JSON 对象")
        return code_report

    try:
        validated = _ModelValidationOutput.model_validate(parsed)
    except Exception as exc:
        if strict:
            raise ValueError(f"集锦验收响应不符合结构化契约：{exc}") from exc
        return code_report

    referenced_reel_ids = {
        item.reel_id for item in [*validated.issues, *validated.repair_actions]
    }
    if expected_reel_id and referenced_reel_ids - {expected_reel_id}:
        raise ValueError(
            f"模型返回了其他集锦 ID：{sorted(referenced_reel_ids - {expected_reel_id})}"
        )
    if allowed_segment_ids is not None:
        invalid_segment_ids = {
            segment_id
            for action in validated.repair_actions
            for segment_id in action.segment_ids
            if segment_id not in allowed_segment_ids
        }
        if invalid_segment_ids:
            raise ValueError(
                f"修复动作引用了产品组外片段：{sorted(invalid_segment_ids)}"
            )

    model_issues = [
        HighlightReelIssue(**issue.model_dump())
        for issue in validated.issues
    ]
    proposed_actions = [
        HighlightReelRepairAction(**action.model_dump())
        for action in validated.repair_actions
    ]

    merged_issues = list(code_report.issues) + model_issues
    if validated.passed is False and not any(
        issue.severity in {"blocker", "major"} for issue in merged_issues
    ):
        merged_issues.append(
            HighlightReelIssue(
                code="HR-MODEL-REJECTED",
                severity="major",
                message="模型验收未通过，但未返回具体问题",
            )
        )
    blockers = [issue for issue in merged_issues if issue.severity == "blocker"]
    majors = [issue for issue in merged_issues if issue.severity == "major"]
    passed = len(blockers) == 0 and len(majors) == 0
    score = validated.score

    return HighlightReelQualityReport(
        passed=passed,
        score=score,
        issues=merged_issues,
        validated_at=datetime.now().isoformat(),
        validator_model=validated.validator_model,
        proposed_repair_actions=(
            list(code_report.proposed_repair_actions) + proposed_actions
        ),
        repair_actions=list(code_report.repair_actions),
        repair_count=code_report.repair_count,
        degraded=code_report.degraded,
        variant=HighlightReelVariant.AFTER_QUALITY,
    )


async def run_highlight_reel_validation(
    reels: List[HighlightReel],
    segments: List[Segment],
    video_duration: Optional[float] = None,
    options: Optional[HighlightReelBuildOptions] = None,
    product_occurrences: Optional[List[ProductOccurrence]] = None,
    *,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    ollama_model: Optional[str] = None,
    ollama_base_url: Optional[str] = None,
    log_callback: Optional[Callable] = None,
    use_model: bool = True,
    max_parallel_calls: int = 3,
    use_role_routing: bool = False,
    target_reel_ids: Optional[set[str]] = None,
    previous_report: Optional[HighlightReelQualityReport] = None,
    call_budget: Optional[HighlightModelCallBudget] = None,
) -> HighlightReelQualityReport:
    """先执行代码校验，再可选执行模型语义质检。"""
    code_report = validate_highlight_reels_code(
        reels,
        segments,
        video_duration,
        options,
        product_occurrences,
    )
    visible_reels = [reel for reel in reels if not reel.hidden]
    if not visible_reels or not use_model:
        return code_report

    explicit_override = not use_role_routing
    feat = (
        merge_feature_llm_request(
            "highlight_reel",
            provider=provider or None,
            openai_model=model or None,
            ollama_model=ollama_model or None,
            ollama_base_url=ollama_base_url or None,
            api_key=api_key,
        )
        if explicit_override
        else role_primary_or_feature("highlight.validation", "highlight_reel")
    )
    resolved_provider = "ollama" if feat.provider == "ollama" else "openai"
    resolved_model = feat.model or model or DEFAULT_HIGHLIGHT_REEL_MODEL
    runtime_kind = "本地 Ollama" if resolved_provider == "ollama" else "云端 OpenAI-compatible"
    runtime_endpoint = feat.base_url or (ollama_base_url if resolved_provider == "ollama" else ROUTERHUB_BASE_URL)

    await _safe_log(
        log_callback,
        "info",
        "validator",
        f"产品集锦模型验收开始 | 调用: {runtime_kind} | 模型: {resolved_model} | 端点: {runtime_endpoint}",
    )

    system_prompt = _load_validation_overlay()
    total_started_at = time.perf_counter()
    model_elapsed = 0.0
    if resolved_provider != "ollama" and not feat.api_key:
        await _safe_log(log_callback, "warn", "validator", "未配置 API Key，跳过模型验收")
        code_report.degraded = True
        code_report.validator_model = "code_only"
        return code_report

    jobs = []
    scope_map = {
        occurrence.occurrence_id: occurrence
        for occurrence in (product_occurrences or [])
    }
    # 每个 product group + strategy 单独验收，避免一个长视频的所有产品挤在同一上下文中。
    model_reels = [
        reel
        for reel in visible_reels
        if target_reel_ids is None or reel.reel_id in target_reel_ids
    ]
    for reel in model_reels:
        user_prompt = (
            "请只质检以下一个 product group 的一种集锦策略，只输出 JSON：\n"
            "{\n"
            '  "passed": true/false,\n'
            '  "score": 0.0-1.0,\n'
            '  "validator_model": "模型名",\n'
            '  "issues": [{"code":"HR-M01","severity":"major","message":"...","reel_id":"...","suggestion":"可选修复建议"}],\n'
            '  "repair_actions": [{"action":"...","reel_id":"...","segment_ids":["..."],"reason":"..."}]\n'
            "}\n\n"
            f"{_build_validation_payload([reel], segments, product_occurrences, code_report)}"
        )
        jobs.append((reel, user_prompt))

    effective_parallel = 1 if resolved_provider == "ollama" else max(1, max_parallel_calls)
    fatal_error = asyncio.Event()
    completed = 0
    completed_lock = asyncio.Lock()

    if use_role_routing:
        jobs_by_scope: dict[str, list[tuple[HighlightReel, str]]] = {}
        for reel, user_prompt in jobs:
            scope_id = reel.product_group_id or reel.occurrence_id or reel.reel_id
            jobs_by_scope.setdefault(scope_id, []).append((reel, user_prompt))

        async def _validate_scope_batch(
            scope_jobs: list[tuple[HighlightReel, str]],
        ) -> list[_ValidationResult]:
            started_at = time.perf_counter()
            batch_reels = [reel for reel, _ in scope_jobs]
            expected_ids = {reel.reel_id for reel in batch_reels}
            batch_prompt = (
                "请一次质检同一 product group 的以下多种策略。只输出 JSON 对象：\n"
                '{"results":[{"reel_id":"...","passed":true,"score":0.95,'
                '"validator_model":"模型名","issues":[],"repair_actions":[]}]}。\n'
                "results 必须对每个输入 reel_id 恰好返回一次；问题和修复动作中的 reel_id "
                "必须与该 result 的 reel_id 相同。\n\n"
                f"{_build_validation_payload(batch_reels, segments, product_occurrences, code_report)}"
            )

            def _validate_batch_response(content: str) -> dict:
                text = re.sub(
                    r"^```(?:json)?\s*",
                    "",
                    content.strip(),
                    flags=re.MULTILINE,
                )
                text = re.sub(
                    r"\s*```$",
                    "",
                    text,
                    flags=re.MULTILINE,
                ).strip()
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise RetryableExecutionError("批量验收响应不是有效 JSON") from exc
                if not isinstance(parsed, dict) or not isinstance(
                    parsed.get("results"), list
                ):
                    raise RetryableExecutionError("批量验收响应缺少 results 数组")
                reports: dict[str, dict] = {}
                for item in parsed["results"]:
                    if not isinstance(item, dict):
                        raise RetryableExecutionError("批量验收 results 包含非对象")
                    reel_id = str(item.get("reel_id") or "")
                    if reel_id not in expected_ids or reel_id in reports:
                        raise RetryableExecutionError(
                            f"批量验收返回未知或重复 reel_id：{reel_id}"
                        )
                    payload = dict(item)
                    payload.pop("reel_id", None)
                    reel = next(
                        candidate
                        for candidate in batch_reels
                        if candidate.reel_id == reel_id
                    )
                    scope = scope_map.get(
                        reel.product_group_id or reel.occurrence_id or ""
                    )
                    report = _parse_validation_response(
                        json.dumps(payload, ensure_ascii=False),
                        HighlightReelQualityReport(
                            passed=True,
                            score=1.0,
                            issues=[],
                        ),
                        strict=True,
                        expected_reel_id=reel_id,
                        allowed_segment_ids=(
                            set(scope.segment_ids)
                            if scope
                            else set(reel.segment_ids)
                        ),
                    )
                    reports[reel_id] = report.model_dump(mode="json")
                if set(reports) != expected_ids:
                    raise RetryableExecutionError(
                        "批量验收未完整返回所有 reel_id"
                    )
                return {"reports": reports, "cache_payload": parsed}

            cache_key = content_cache_fingerprint("highlight_validation_batch:v1", {
                "system_prompt": system_prompt,
                "input": batch_prompt,
                "provider": resolved_provider,
                "model": resolved_model,
                "endpoint": runtime_endpoint,
                "routes": [candidate.model_dump(exclude={"api_key"}) for candidate in resolve_role_candidates("highlight.validation")],
            })
            cached = load_content_cache(HIGHLIGHT_VALIDATION_CACHE_DIR, cache_key)
            parsed = None
            if isinstance(cached, dict):
                try:
                    # 缓存同样走严格校验，防止旧报告引用失效片段或其他候选。
                    parsed = _validate_batch_response(json.dumps(cached, ensure_ascii=False))
                except (ValueError, TypeError, KeyError, RetryableExecutionError):
                    pass
            if parsed is None:
                if call_budget is not None:
                    await call_budget.consume()
                routed = await route_chat_completion(
                    "highlight.validation",
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": batch_prompt},
                    ],
                    max_tokens=8192,
                    temperature=0.0,
                    validate=_validate_batch_response,
                    timeout_seconds=120,
                )
                parsed = routed.value
                cache_payload = parsed["cache_payload"]
                try:
                    save_content_cache(HIGHLIGHT_VALIDATION_CACHE_DIR, cache_key, cache_payload)
                except OSError:
                    pass
            else:
                await _safe_log(log_callback, "info", "validator", f"{batch_reels[0].product_name or '产品组'} 批量验收命中内容缓存")
            elapsed = time.perf_counter() - started_at
            reports = parsed["reports"]
            return [
                _ValidationResult(
                    reel.reel_id,
                    HighlightReelQualityReport(**reports[reel.reel_id]),
                    elapsed / max(1, len(batch_reels)),
                )
                for reel in batch_reels
            ]

        async def _safe_validate_scope_batch(
            scope_jobs: list[tuple[HighlightReel, str]],
        ) -> list[_ValidationResult]:
            try:
                return await _validate_scope_batch(scope_jobs)
            except Exception as exc:
                await _safe_log(
                    log_callback,
                    "warn",
                    "validator",
                    f"{scope_jobs[0][0].product_name or '产品组'} 批量验收失败：{exc}",
                )
                return [
                    _ValidationResult(reel.reel_id, None, 0.0, str(exc))
                    for reel, _ in scope_jobs
                ]

        batch_validation_results = await asyncio.gather(
            *(
                _safe_validate_scope_batch(scope_jobs)
                for scope_jobs in jobs_by_scope.values()
            )
        )
        validation_results = [
            result
            for scope_results in batch_validation_results
            for result in scope_results
        ]
        await _safe_log(
            log_callback,
            "info",
            "validator",
            (
                f"集锦批量验收完成：{len(jobs_by_scope)} 次模型调用覆盖 "
                f"{len(jobs)} 个候选"
            ),
            event="highlight_validation_batch_completed",
            batch_calls=len(jobs_by_scope),
            total_jobs=len(jobs),
        )
    else:
        validation_results = []

    if not use_role_routing:
      async with HighlightModelExecutor(
          provider=resolved_provider,
          model=resolved_model,
          api_key=feat.api_key,
          base_url=feat.base_url or ollama_base_url or ROUTERHUB_BASE_URL,
          max_parallel_calls=effective_parallel,
          max_retries=0,
          feature="highlight_validation",
          role="highlight.validation",
      ) as executor:
        async def _validate_one(reel: HighlightReel, user_prompt: str) -> _ValidationResult:
            nonlocal completed
            started_at = time.perf_counter()
            try:
                cache_key = content_cache_fingerprint(
                    "highlight_validation:v2",
                    {
                        "provider": resolved_provider,
                        "model": resolved_model,
                        "endpoint": runtime_endpoint.rstrip("/"),
                        "system_prompt": system_prompt,
                        "reel_input": user_prompt,
                    },
                )
                cached = load_content_cache(HIGHLIGHT_VALIDATION_CACHE_DIR, cache_key)
                parsed_report = None
                if isinstance(cached, dict):
                    try:
                        parsed_report = HighlightReelQualityReport(**cached)
                    except Exception:
                        parsed_report = None
                if parsed_report is not None and any(
                    item.reel_id and item.reel_id != reel.reel_id
                    for item in (
                        parsed_report.issues
                        + parsed_report.proposed_repair_actions
                        + parsed_report.repair_actions
                    )
                ):
                    parsed_report = None
                if parsed_report is not None:
                    await _safe_log(
                        log_callback,
                        "info",
                        "validator",
                        f"{reel.reel_id} 验收命中内容缓存",
                    )
                else:
                    async def _complete_and_parse() -> HighlightReelQualityReport:
                        def _validate(response_text: str) -> HighlightReelQualityReport:
                            scope = scope_map.get(
                                reel.product_group_id or reel.occurrence_id or ""
                            )
                            try:
                                model_report = _parse_validation_response(
                                    response_text,
                                    HighlightReelQualityReport(
                                        passed=True,
                                        score=1.0,
                                        issues=[],
                                    ),
                                    strict=True,
                                    expected_reel_id=reel.reel_id,
                                    allowed_segment_ids=(
                                        set(scope.segment_ids)
                                        if scope
                                        else set(reel.segment_ids)
                                    ),
                                )
                            except ValueError as exc:
                                raise RetryableExecutionError(str(exc)) from exc
                            invalid_ids = {
                                item.reel_id
                                for item in model_report.issues + model_report.repair_actions
                                if item.reel_id and item.reel_id != reel.reel_id
                            }
                            if invalid_ids:
                                raise ValueError(
                                    f"模型返回了其他集锦 ID：{sorted(invalid_ids)}"
                                )
                            return model_report

                        if call_budget is not None:
                            await call_budget.consume()
                        if not explicit_override:
                            routed = await route_chat_completion(
                                "highlight.validation",
                                [
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user", "content": user_prompt},
                                ],
                                max_tokens=4096,
                                temperature=0.0,
                                validate=_validate,
                                timeout_seconds=90,
                            )
                            return routed.value
                        response_text = await executor.complete(
                            system_prompt,
                            user_prompt,
                            abort_event=fatal_error,
                        )
                        return _validate(response_text)

                    parsed_report = await execute_with_retry(
                        _complete_and_parse,
                        max_retries=3 if explicit_override else 1,
                    )
                    try:
                        save_content_cache(
                            HIGHLIGHT_VALIDATION_CACHE_DIR,
                            cache_key,
                            parsed_report.model_dump(mode="json"),
                        )
                    except OSError:
                        pass

                invalid_reel_ids = {
                    item.reel_id
                    for item in (
                        parsed_report.issues
                        + parsed_report.proposed_repair_actions
                        + parsed_report.repair_actions
                    )
                    if item.reel_id and item.reel_id != reel.reel_id
                }
                if invalid_reel_ids:
                    raise ValueError(f"模型返回了其他集锦 ID：{sorted(invalid_reel_ids)}")
                parsed_report.issues = [
                    issue if issue.reel_id else issue.model_copy(update={"reel_id": reel.reel_id})
                    for issue in parsed_report.issues
                ]
                parsed_report.repair_actions = [
                    action if action.reel_id else action.model_copy(update={"reel_id": reel.reel_id})
                    for action in parsed_report.repair_actions
                ]
                parsed_report.proposed_repair_actions = [
                    action
                    if action.reel_id
                    else action.model_copy(update={"reel_id": reel.reel_id})
                    for action in parsed_report.proposed_repair_actions
                ]
                elapsed = time.perf_counter() - started_at
                await _safe_log(
                    log_callback,
                    "info",
                    "validator",
                    f"{reel.product_name or reel.occurrence_id} / "
                    f"{reel.strategy_kind.value if reel.strategy_kind else 'legacy'} 验收完成"
                    f"（{_format_elapsed(elapsed)}）",
                )
                return _ValidationResult(reel.reel_id, parsed_report, elapsed)
            except asyncio.CancelledError:
                raise
            except HighlightModelBatchAborted:
                return _ValidationResult(reel.reel_id, None, 0.0, "同批次已停止派发")
            except HighlightModelFatalError as exc:
                fatal_error.set()
                await _safe_log(
                    log_callback,
                    "warn",
                    "validator",
                    f"集锦模型配置错误，停止派发剩余验收：{exc}",
                )
                return _ValidationResult(reel.reel_id, None, 0.0, str(exc))
            except Exception as exc:
                await _safe_log(
                    log_callback,
                    "warn",
                    "validator",
                    f"{reel.reel_id} 模型验收失败，保留该策略代码校验结果：{exc}",
                )
                return _ValidationResult(
                    reel.reel_id,
                    None,
                    time.perf_counter() - started_at,
                    str(exc),
                )
            finally:
                async with completed_lock:
                    completed += 1
                    current_completed = completed
                await _safe_log(
                    log_callback,
                    "info",
                    "validator",
                    f"集锦模型验收进度 {current_completed}/{len(jobs)}，并发上限 {effective_parallel}",
                    event="highlight_validation_completed",
                    completed_jobs=current_completed,
                    total_jobs=len(jobs),
                    max_parallel=effective_parallel,
                )

        validation_results = await asyncio.gather(
            *(_validate_one(reel, user_prompt) for reel, user_prompt in jobs)
        )

    preserved_model_issues = [
        issue.model_copy(deep=True)
        for issue in (previous_report.issues if previous_report else [])
        if issue.code.startswith("HR-M")
        and (
            target_reel_ids is None
            or issue.reel_id not in target_reel_ids
        )
    ]
    preserved_actions = [
        action.model_copy(deep=True)
        for action in (
            previous_report.proposed_repair_actions if previous_report else []
        )
        if target_reel_ids is None or action.reel_id not in target_reel_ids
    ]
    model_issues: List[HighlightReelIssue] = list(preserved_model_issues)
    proposed_actions: List[HighlightReelRepairAction] = list(preserved_actions)
    degraded = any(
        issue.code == "HR-MODEL-UNVERIFIED" for issue in preserved_model_issues
    )
    reels_by_id = {reel.reel_id: reel for reel in reels}
    for result in validation_results:
        model_elapsed += result.elapsed
        if result.report is None:
            degraded = True
            model_issues.append(
                HighlightReelIssue(
                    code="HR-MODEL-UNVERIFIED",
                    severity="minor",
                    message=f"模型验收未完成：{result.error or '未知错误'}",
                    reel_id=result.reel_id,
                    suggestion="请人工复核该集锦策略",
                )
            )
            continue
        model_issues.extend(result.report.issues)
        proposed_actions.extend(result.report.proposed_repair_actions)
        if result.report.score is not None:
            if result.reel_id in reels_by_id:
                reels_by_id[result.reel_id].score = result.report.score

    merged_issues = list(code_report.issues) + model_issues
    aggregate_scores = [
        reel.score for reel in visible_reels if reel.score is not None
    ]
    report = code_report.model_copy(
        deep=True,
        update={
            "passed": not any(issue.severity in {"blocker", "major"} for issue in merged_issues),
            "score": (
                sum(aggregate_scores) / len(aggregate_scores)
                if aggregate_scores
                else code_report.score
            ),
            "issues": merged_issues,
            "proposed_repair_actions": (
                list(code_report.proposed_repair_actions) + proposed_actions
            ),
            "repair_actions": list(code_report.repair_actions),
            "validator_model": resolved_model if not degraded else f"{resolved_model}:partial",
            "degraded": bool(code_report.degraded or degraded),
            "validated_at": datetime.now().isoformat(),
        },
    )
    await _safe_log(
        log_callback,
        "success" if report.passed and not report.degraded else "warn",
        "validator",
        f"产品集锦验收完成：{'通过' if report.passed and not report.degraded else '需人工复核'}"
        f"（{len(report.issues)} 项问题） | 模型累计耗时: {_format_elapsed(model_elapsed)} | "
        f"总运行时间: {_format_elapsed(time.perf_counter() - total_started_at)}",
    )
    return report
