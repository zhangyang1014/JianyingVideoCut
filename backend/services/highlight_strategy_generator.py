"""按产品场次调用三套独立 Prompt 生成 faithful/balanced/concise 集锦。"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional

from ..core.task_runner import RetryableExecutionError, execute_with_retry
from ..models.task import (
    HighlightReel,
    HighlightReelBuildOptions,
    HighlightReelIssue,
    HighlightReelStrategy,
    ProductGroup,
    ProductOccurrence,
    Segment,
)
from .app_config_store import merge_feature_llm_request, role_primary_or_feature
from .llm_router import route_chat_completion
from .highlight_model_executor import (
    HighlightModelBatchAborted,
    HighlightModelCallBudget,
    HighlightModelExecutor,
    HighlightModelFatalError,
)
from .highlight_reel_plan import (
    build_highlight_reel_plan,
    product_group_as_occurrence,
    rebuild_reel_metrics,
)
from .prompt_registry import resolve_prompt_content
from .semantic_auditor import (
    MODEL_CACHE_DIR,
    content_cache_fingerprint,
    load_content_cache,
    save_content_cache,
)
from .subtitle_text import get_effective_subtitle_text

HIGHLIGHT_STRATEGY_CACHE_DIR = MODEL_CACHE_DIR / "highlight_strategy"


LogCallback = Callable[..., Awaitable[None]]
_PROMPT_IDS = {
    HighlightReelStrategy.FAITHFUL: "highlight.faithful",
    HighlightReelStrategy.BALANCED: "highlight.balanced",
    HighlightReelStrategy.CONCISE: "highlight.concise",
}


@dataclass(frozen=True)
class _StrategyJob:
    index: int
    occurrence: ProductOccurrence
    strategy: HighlightReelStrategy
    prompt_id: str
    system_prompt: str
    user_prompt: str
    allowed_ids: frozenset[str]
    base: HighlightReel


def _selected_ids_from(
    payload: dict,
    job: _StrategyJob,
    segment_map: Dict[str, Segment],
    *,
    strict: bool = False,
) -> List[str]:
    raw_ids = payload.get("segment_ids")
    error: Optional[str] = None
    if not isinstance(raw_ids, list) or not raw_ids:
        error = "segment_ids 必须是非空数组"
    elif any(not isinstance(item, str) or not item.strip() for item in raw_ids):
        error = "segment_ids 只能包含非空字符串"
    else:
        selected = [item.strip() for item in raw_ids]
        if len(selected) != len(set(selected)):
            error = "segment_ids 包含重复项"
        else:
            invalid = [
                segment_id
                for segment_id in selected
                if segment_id not in job.allowed_ids
                or segment_id not in segment_map
            ]
            if invalid:
                error = f"segment_ids 包含越界或不存在片段：{invalid[:5]}"
            elif selected != sorted(
                selected,
                key=lambda segment_id: (
                    segment_map[segment_id].start,
                    segment_map[segment_id].end,
                    segment_id,
                ),
            ):
                error = "segment_ids 未保持源时间顺序"
            else:
                return selected
    if strict:
        raise ValueError(error or "segment_ids 无效")
    return []


def _candidate_from_payload(
    job: _StrategyJob,
    payload: dict,
    segments: List[Segment],
    segment_map: Dict[str, Segment],
) -> HighlightReel:
    selected_ids = _selected_ids_from(payload, job, segment_map, strict=True)
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("模型未返回非空标题")
    candidate = job.base.model_copy(
        deep=True,
        update={
            "segment_ids": selected_ids,
            "title": title.strip(),
            "rationale": str(
                payload.get("rationale") or job.base.rationale or ""
            ).strip()
            or None,
        },
    )
    candidate.source_duration = job.base.source_duration
    candidate.source_segment_count = job.base.source_segment_count
    candidate = rebuild_reel_metrics(candidate, segments)
    candidate.content_retention_ratio = (
        round(len(candidate.segment_ids) / candidate.source_segment_count, 4)
        if candidate.source_segment_count
        else job.base.content_retention_ratio
    )
    candidate.selected_selling_points = list(
        dict.fromkeys(
            point
            for segment_id in candidate.segment_ids
            for point in (
                segment_map[segment_id].product_semantic.selling_points
                if segment_map[segment_id].product_semantic
                else []
            )
            if point
        )
    )
    return candidate


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
        # 日志是旁路，不能把成功的模型结果变成业务失败。
        return


def _parse_json_object(response_text: str) -> Optional[dict]:
    text = re.sub(r"^```(?:json)?\s*", "", response_text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group())
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _occurrence_payload(occurrence: ProductOccurrence, segments: List[Segment]) -> str:
    segment_map = {segment.id: segment for segment in segments}
    payload_segments = []
    for segment_id in occurrence.segment_ids:
        segment = segment_map.get(segment_id)
        if not segment:
            continue
        payload_segments.append(
            {
                "segment_id": segment.id,
                "start": round(segment.start, 3),
                "end": round(segment.end, 3),
                "text": get_effective_subtitle_text(segment, include_speaker_label=False),
                "roles": list(segment.product_semantic.roles or []) if segment.product_semantic else [],
                "selling_points": (
                    list(segment.product_semantic.selling_points or [])
                    if segment.product_semantic
                    else []
                ),
                "eligible": (
                    segment.product_semantic.eligible_for_highlight
                    if segment.product_semantic
                    else True
                ),
            }
        )
    return json.dumps(
        {
            "occurrence_id": occurrence.occurrence_id,
            "product_name": occurrence.name,
            "start": occurrence.start,
            "end": occurrence.end,
            "segments": payload_segments,
        },
        ensure_ascii=False,
    )


def _strategy_user_prompt(
    occurrence: ProductOccurrence,
    segments: List[Segment],
    quality_feedback: Optional[List[HighlightReelIssue]] = None,
) -> str:
    feedback = ""
    if quality_feedback:
        feedback = (
            "\n\n上一轮质检未通过。必须逐项修复以下问题，且不能引入新片段：\n"
            + "\n".join(
                f"- {issue.code}/{issue.severity}: {issue.message}"
                for issue in quality_feedback
            )
        )
    return (
        "请从该产品全部返场内容的给定 segment_id 中选择互补、不重复的最佳叙事片段。"
        "输出的第 1 或第 2 个 segment_id 必须明确说出产品名、别名或清晰品类，最好同时带出卖点；"
        "所有 segment_id 必须严格保持源时间顺序，不得把中部片段提到开头。"
        "第一句和最后一句都必须语义完整，不能依赖被剪掉的上下文。"
        "只输出 JSON 对象："
        '{"segment_ids":["..."],"title":"可选标题","rationale":"选择理由"}。'
        "不得创造、修改或跨范围引用 segment_id。\n\n"
        f"{_occurrence_payload(occurrence, segments)}"
        f"{feedback}"
    )


def _product_group_payload(group: ProductGroup, segments: List[Segment]) -> str:
    """人工分析指令使用产品组契约，避免把返场或内容角度再次拆成产品。"""
    occurrence = product_group_as_occurrence(group)
    payload = json.loads(_occurrence_payload(occurrence, segments))
    payload.pop("occurrence_id", None)
    payload["product_group_id"] = group.product_group_id
    payload["canonical_name"] = group.canonical_name
    payload["aliases"] = list(group.aliases)
    payload["source_occurrence_ids"] = list(group.occurrence_ids)
    return json.dumps(payload, ensure_ascii=False)


def _manual_group_prompt(group: ProductGroup, segments: List[Segment]) -> str:
    return (
        "该输入已经按全局产品完成归并：不同返场、版本/套装叫法、内容主题、卖点角度和承载 APP "
        "不能再次拆成独立产品。请从给定 segment_id 中选择互补、不重复的最佳叙事片段。"
        "输出的第 1 或第 2 个 segment_id 必须明确说出产品名、别名或清晰品类，最好同时带出卖点；"
        "所有 segment_id 必须严格保持源时间顺序，第一句和最后一句必须语义完整。"
        "不得创造、修改或跨产品组引用 segment_id。\n\n"
        f"{_product_group_payload(group, segments)}"
    )


def build_manual_highlight_prompt_pack(
    segments: List[Segment],
    product_groups: List[ProductGroup],
) -> dict:
    """构建可直接粘贴到 Cursor 对话窗的按产品组、多策略集锦提示词包。"""
    if not product_groups:
        raise ValueError("没有可分析的产品组，请先完成产品归组")

    system_prompt = (
        "你是 GoldenClip 产品集锦分析器。请独立完成下方每个 JOB，严格遵守该 JOB 的策略提示词，"
        "PRODUCT INPUT 是已确认的全局产品边界，不得按返场、版本、主题、卖点或 APP 再拆分产品。"
        "只能使用输入中存在的 segment_id，不得改写原字幕或虚构片段。"
        "最终只输出一个 JSON 对象，不要使用 Markdown 代码块或补充解释。输出格式："
        '{"results":[{"job_id":"<product_group_id>:<strategy>","product_group_id":"...",'
        '"strategy":"faithful|balanced|concise",'
        '"segment_ids":["..."],"title":"...","rationale":"..."}]}。'
    )
    strategy_sections = [
        "\n".join(
            [
                f"=== STRATEGY {strategy.value} ===",
                resolve_prompt_content(prompt_id),
            ]
        )
        for strategy, prompt_id in _PROMPT_IDS.items()
    ]
    input_sections = [
        "\n".join(
            [
                f"=== PRODUCT INPUT {group.product_group_id} ===",
                "JOB IDs: "
                + ", ".join(
                    f"{group.product_group_id}:{strategy.value}"
                    for strategy in _PROMPT_IDS
                ),
                _manual_group_prompt(group, segments),
            ]
        )
        for group in product_groups
    ]
    jobs_count = len(product_groups) * len(_PROMPT_IDS)
    user_prompt = "\n\n".join(
        [
            f"请对以下 {len(product_groups)} 个全局产品组分别执行 faithful、balanced、concise 三种策略，"
            f"共 {jobs_count} 个 JOB，并按 system 指定格式一次性返回结果。",
            "--- STRATEGY DEFINITIONS（每个产品均复用）---",
            *strategy_sections,
            "--- PRODUCT INPUTS ---",
            *input_sections,
        ]
    )
    combined = f"=== SYSTEM PROMPT ===\n{system_prompt}\n\n=== USER PROMPT ===\n{user_prompt}"
    return {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "combined": combined,
        "jobs_count": jobs_count,
        "product_groups_count": len(product_groups),
    }


async def generate_product_strategy_reels(
    segments: List[Segment],
    occurrences: List[ProductOccurrence],
    existing_reels: Optional[List[HighlightReel]] = None,
    options: Optional[HighlightReelBuildOptions] = None,
    *,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    ollama_model: Optional[str] = None,
    ollama_base_url: Optional[str] = None,
    log_callback: Optional[LogCallback] = None,
    use_model: bool = True,
    max_parallel_calls: int = 3,
    use_role_routing: bool = False,
    product_groups: Optional[List[ProductGroup]] = None,
    quality_feedback: Optional[List[HighlightReelIssue]] = None,
    target_reel_ids: Optional[set[str]] = None,
    call_budget: Optional[HighlightModelCallBudget] = None,
) -> List[HighlightReel]:
    """每个产品组三 Prompt 生成内部候选；失败时保留确定性候选。"""
    scope_occurrences = (
        [product_group_as_occurrence(group) for group in product_groups]
        if product_groups is not None
        else occurrences
    )
    deterministic = build_highlight_reel_plan(
        segments,
        existing_reels,
        options,
        product_occurrences=None if product_groups is not None else scope_occurrences,
        product_groups=product_groups,
    )
    if not deterministic or not use_model:
        return deterministic

    explicit_override = not use_role_routing
    feat = (
        merge_feature_llm_request(
            "highlight_reel",
            provider=provider,
            openai_model=model,
            ollama_model=ollama_model,
            ollama_base_url=ollama_base_url,
            api_key=api_key,
        )
        if explicit_override
        else role_primary_or_feature("highlight.strategy", "highlight_reel")
    )
    if feat.provider != "ollama" and not feat.api_key:
        await _safe_log(log_callback, "warn", "highlight", "未配置集锦模型 API Key，使用确定性三版生成")
        return deterministic

    by_key: Dict[tuple[str, HighlightReelStrategy], HighlightReel] = {
        (reel.occurrence_id, reel.strategy_kind): reel
        for reel in deterministic
        if reel.occurrence_id and reel.strategy_kind
    }
    segment_map = {segment.id: segment for segment in segments}
    output = list(deterministic)
    output_index = {reel.reel_id: index for index, reel in enumerate(output)}
    prompt_snapshots = {
        strategy: resolve_prompt_content(prompt_id)
        for strategy, prompt_id in _PROMPT_IDS.items()
    }
    jobs: List[_StrategyJob] = []
    for occurrence in scope_occurrences:
        allowed_ids = frozenset(
            segment_id
            for segment_id in occurrence.segment_ids
            if segment_id in segment_map
            and (
                not segment_map[segment_id].product_semantic
                or segment_map[segment_id].product_semantic.eligible_for_highlight
            )
        )
        for strategy, prompt_id in _PROMPT_IDS.items():
            base = by_key.get((occurrence.occurrence_id, strategy))
            if not base:
                continue
            if target_reel_ids is not None and base.reel_id not in target_reel_ids:
                continue
            reel_feedback = [
                issue
                for issue in (quality_feedback or [])
                if issue.reel_id in {None, base.reel_id}
            ]
            user_prompt = _strategy_user_prompt(
                occurrence,
                segments,
                reel_feedback,
            )
            jobs.append(
                _StrategyJob(
                    index=output_index[base.reel_id],
                    occurrence=occurrence,
                    strategy=strategy,
                    prompt_id=prompt_id,
                    system_prompt=prompt_snapshots[strategy],
                    user_prompt=user_prompt,
                    allowed_ids=allowed_ids,
                    base=base.model_copy(deep=True),
                )
            )

    if not jobs:
        return output

    effective_parallel = 1 if feat.provider == "ollama" else max(1, max_parallel_calls)
    fatal_error = asyncio.Event()
    completed = 0
    completed_lock = asyncio.Lock()

    if use_role_routing:
        jobs_by_occurrence: Dict[str, List[_StrategyJob]] = {}
        for job in jobs:
            jobs_by_occurrence.setdefault(job.occurrence.occurrence_id, []).append(job)

        async def _run_strategy_batch(
            occurrence_jobs: List[_StrategyJob],
        ) -> List[tuple[int, HighlightReel]]:
            occurrence = occurrence_jobs[0].occurrence
            expected_strategies = {job.strategy.value for job in occurrence_jobs}
            system_prompt = (
                "你是 GoldenClip 产品集锦策略生成器。一次完成同一产品的多种策略，"
                "各策略必须独立遵守对应定义，只输出 JSON 对象："
                '{"results":[{"strategy":"faithful|balanced|concise",'
                '"segment_ids":["..."],"title":"...","rationale":"..."}]}。\n\n'
                + "\n\n".join(
                    f"=== {job.strategy.value} ===\n{job.system_prompt}"
                    for job in occurrence_jobs
                )
            )
            feedback_by_strategy = {
                job.strategy.value: [
                    {
                        "code": issue.code,
                        "severity": issue.severity,
                        "message": issue.message,
                    }
                    for issue in (quality_feedback or [])
                    if issue.reel_id in {None, job.base.reel_id}
                ]
                for job in occurrence_jobs
            }
            user_prompt = (
                "PRODUCT INPUT:\n"
                f"{_occurrence_payload(occurrence, segments)}\n\n"
                f"QUALITY FEEDBACK BY STRATEGY:\n"
                f"{json.dumps(feedback_by_strategy, ensure_ascii=False)}"
            )
            cache_key = content_cache_fingerprint(
                "highlight_strategy_batch:v1",
                {
                    "occurrence_id": occurrence.occurrence_id,
                    "strategies": sorted(expected_strategies),
                    "system_prompt": system_prompt,
                    "provider": feat.provider,
                    "model": feat.model,
                    "endpoint": (feat.base_url or "").rstrip("/"),
                    "input": user_prompt,
                },
            )
            cached = load_content_cache(HIGHLIGHT_STRATEGY_CACHE_DIR, cache_key)
            by_strategy = {job.strategy.value: job for job in occurrence_jobs}

            def _validate_batch_content(content: str) -> dict:
                parsed = _parse_json_object(content)
                if not isinstance(parsed, dict) or not isinstance(
                    parsed.get("results"), list
                ):
                    raise RetryableExecutionError("批量策略响应缺少 results 数组")
                results_by_strategy: Dict[str, dict] = {}
                for item in parsed["results"]:
                    if not isinstance(item, dict):
                        raise RetryableExecutionError("批量策略 results 包含非对象")
                    strategy = str(item.get("strategy") or "")
                    job = by_strategy.get(strategy)
                    if not job or strategy in results_by_strategy:
                        raise RetryableExecutionError(
                            f"批量策略返回未知或重复 strategy：{strategy}"
                        )
                    try:
                        _selected_ids_from(item, job, segment_map, strict=True)
                    except ValueError as exc:
                        raise RetryableExecutionError(str(exc)) from exc
                    if not isinstance(item.get("title"), str) or not item[
                        "title"
                    ].strip():
                        raise RetryableExecutionError(
                            f"{strategy} 未返回非空标题"
                        )
                    results_by_strategy[strategy] = item
                if set(results_by_strategy) != expected_strategies:
                    raise RetryableExecutionError(
                        "批量策略未完整返回请求的 strategy"
                    )
                return {"results": list(results_by_strategy.values())}

            parsed: Optional[dict] = None
            if isinstance(cached, dict):
                try:
                    parsed = _validate_batch_content(
                        json.dumps(cached, ensure_ascii=False)
                    )
                except Exception:
                    parsed = None
            if parsed is None:
                if call_budget is not None:
                    await call_budget.consume()
                routed = await route_chat_completion(
                    "highlight.strategy",
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=8192,
                    temperature=0.0,
                    validate=_validate_batch_content,
                    timeout_seconds=120,
                )
                parsed = routed.value
                try:
                    save_content_cache(
                        HIGHLIGHT_STRATEGY_CACHE_DIR,
                        cache_key,
                        parsed,
                    )
                except OSError:
                    pass
            results_by_strategy = {
                str(item["strategy"]): item for item in parsed["results"]
            }
            return [
                (
                    job.index,
                    _candidate_from_payload(
                        job,
                        results_by_strategy[job.strategy.value],
                        segments,
                        segment_map,
                    ),
                )
                for job in occurrence_jobs
            ]

        async def _safe_run_strategy_batch(
            occurrence_jobs: List[_StrategyJob],
        ) -> List[tuple[int, HighlightReel]]:
            try:
                return await _run_strategy_batch(occurrence_jobs)
            except Exception as exc:
                await _safe_log(
                    log_callback,
                    "warn",
                    "highlight",
                    f"{occurrence_jobs[0].occurrence.name} 批量策略生成失败，使用确定性兜底：{exc}",
                )
                return []

        batch_results = await asyncio.gather(
            *(
                _safe_run_strategy_batch(occurrence_jobs)
                for occurrence_jobs in jobs_by_occurrence.values()
            )
        )
        for occurrence_results in batch_results:
            for index, candidate in occurrence_results:
                output[index] = candidate
        await _safe_log(
            log_callback,
            "info",
            "highlight",
            (
                f"集锦策略批量生成完成：{len(jobs_by_occurrence)} 次模型调用覆盖 "
                f"{len(jobs)} 个产品/策略 JOB"
            ),
            event="highlight_strategy_batch_completed",
            batch_calls=len(jobs_by_occurrence),
            total_jobs=len(jobs),
        )
        return output

    async with HighlightModelExecutor(
        provider=feat.provider,
        model=feat.model,
        api_key=feat.api_key,
        base_url=(
            feat.base_url
            or ollama_base_url
            or (
                "http://localhost:11434"
                if feat.provider == "ollama"
                else "https://openrouter.ai/api/v1"
            )
        ),
        max_parallel_calls=effective_parallel,
        max_retries=0,
    ) as executor:
        async def _run_job(job: _StrategyJob) -> tuple[int, Optional[HighlightReel]]:
            nonlocal completed
            try:
                await _safe_log(
                    log_callback,
                    "info",
                    "highlight",
                    f"{job.occurrence.name}：调用 {job.prompt_id} 生成策略",
                )
                cache_key = content_cache_fingerprint(
                    "highlight_strategy:v1",
                    {
                        "occurrence_id": job.occurrence.occurrence_id,
                        "strategy": job.strategy.value,
                        "prompt_id": job.prompt_id,
                        "system_prompt": job.system_prompt,
                        "provider": feat.provider,
                        "model": feat.model,
                        "endpoint": (
                            feat.base_url or ollama_base_url or "http://localhost:11434"
                        ).rstrip("/"),
                        "input": job.user_prompt,
                    },
                )

                parsed = load_content_cache(HIGHLIGHT_STRATEGY_CACHE_DIR, cache_key)
                if (
                    not isinstance(parsed, dict)
                    or not _selected_ids_from(parsed, job, segment_map)
                    or not isinstance(parsed.get("title"), str)
                    or not parsed.get("title", "").strip()
                ):
                    parsed = None
                else:
                    await _safe_log(
                        log_callback,
                        "info",
                        "highlight",
                        f"{job.occurrence.name} / {job.strategy.value} 命中策略缓存",
                    )

                async def _complete_and_parse() -> dict:
                    def _validate(response: str) -> dict:
                        parsed_response = _parse_json_object(response)
                        if parsed_response is None:
                            raise RetryableExecutionError("模型未返回有效 JSON 对象")
                        try:
                            _selected_ids_from(
                                parsed_response,
                                job,
                                segment_map,
                                strict=True,
                            )
                        except ValueError as exc:
                            raise RetryableExecutionError(str(exc)) from exc
                        if not isinstance(parsed_response.get("title"), str) or not str(
                            parsed_response.get("title")
                        ).strip():
                            raise RetryableExecutionError("模型未返回非空标题")
                        return parsed_response

                    if call_budget is not None:
                        await call_budget.consume()
                    if not explicit_override:
                        routed = await route_chat_completion(
                            "highlight.strategy",
                            [
                                {"role": "system", "content": job.system_prompt},
                                {"role": "user", "content": job.user_prompt},
                            ],
                            max_tokens=4096,
                            temperature=0.0,
                            validate=_validate,
                            timeout_seconds=90,
                        )
                        return routed.value
                    response = await executor.complete(
                        job.system_prompt,
                        job.user_prompt,
                        abort_event=fatal_error,
                    )
                    return _validate(response)

                if parsed is None:
                    parsed = await execute_with_retry(
                        _complete_and_parse,
                        max_retries=3 if explicit_override else 1,
                    )
                    try:
                        save_content_cache(HIGHLIGHT_STRATEGY_CACHE_DIR, cache_key, parsed)
                    except OSError:
                        pass
                candidate = _candidate_from_payload(
                    job,
                    parsed,
                    segments,
                    segment_map,
                )
                return job.index, candidate
            except asyncio.CancelledError:
                raise
            except HighlightModelBatchAborted as exc:
                await _safe_log(
                    log_callback,
                    "warn",
                    "highlight",
                    f"{job.occurrence.name} / {job.strategy.value} 未调用模型：{exc}",
                )
            except HighlightModelFatalError as exc:
                fatal_error.set()
                await _safe_log(
                    log_callback,
                    "warn",
                    "highlight",
                    f"集锦模型配置错误，停止派发剩余策略并使用确定性兜底：{exc}",
                )
            except Exception as exc:
                await _safe_log(
                    log_callback,
                    "warn",
                    "highlight",
                    f"{job.occurrence.name} 的 {job.strategy.value} 模型生成失败，使用确定性兜底：{exc}",
                )
            finally:
                async with completed_lock:
                    completed += 1
                    current_completed = completed
                await _safe_log(
                    log_callback,
                    "info",
                    "highlight",
                    f"集锦策略生成进度 {current_completed}/{len(jobs)}，并发上限 {effective_parallel}",
                    event="highlight_strategy_completed",
                    completed_jobs=current_completed,
                    total_jobs=len(jobs),
                    max_parallel=effective_parallel,
                )
            return job.index, None

        results = await asyncio.gather(*(_run_job(job) for job in jobs))

    for index, candidate in results:
        if candidate is not None:
            output[index] = candidate
    return output
