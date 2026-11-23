"""Post-audit coherence review and safe repair pass."""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

from ..models.app_config import DEFAULT_AUDIT_MODEL, ROUTERHUB_BASE_URL
from ..core.task_runner import RetryableExecutionError, execute_with_retry
from ..models.task import (
    AuditCoherenceIssue,
    AuditCoherenceReport,
    AuditCoherenceTermFix,
    AuditRule,
    Segment,
    SegmentAction,
)
from ..services.app_config_store import resolve_openai_api_key
from ..services.llm_router import route_chat_completion
from ..services.prompt_registry import resolve_prompt_content
from ..services.model_usage import (
    manual_pricing_for_feature,
    openrouter_catalog_pricing,
    record_model_call,
)
from ..services.provider_pricing import provider_profile_id_for_base_url
from ..services.semantic_auditor import (
    MODEL_CACHE_DIR,
    content_cache_fingerprint,
    load_content_cache,
    save_content_cache,
)

COHERENCE_CACHE_DIR = MODEL_CACHE_DIR / "audit_coherence"

KEPT_ACTIONS = {
    SegmentAction.KEEP,
    SegmentAction.SUBTITLE_FIX,
    SegmentAction.TEXT_FIX,
    SegmentAction.MERGE_NEXT,
}

DANGLING_PREFIXES = (
    "然后",
    "所以",
    "但是",
    "因为",
    "而且",
    "以及",
    "包括",
    "也",
    "还",
    "这个",
    "那个",
    "这就是",
    "也就是",
    "所以说",
    "他自己",
    "她自己",
    "它自己",
)

DANGLING_SUFFIXES = (
    "因为",
    "所以",
    "但是",
    "而且",
    "以及",
    "包括",
    "如果",
    "当",
    "这个是",
    "那就是",
    "也就是",
)

TAIL_MIGRATION_SPLIT_PHRASES = (
    "看第一个事件",
    "看第一个",
    "他自己就",
    "她自己就",
    "它自己就",
    "孩子就",
    "学生就",
    "看下一个",
    "我们看",
    "你看",
    "接下来",
    "最后",
    "然后",
    "比如",
    "例如",
    "这个是",
    "那就是",
    "也就是",
)
TAIL_MIGRATION_MAX_GAP = 1.0
TAIL_MIGRATION_MAX_CHARS = 14
TAIL_MIGRATION_MIN_PREFIX_CHARS = 8


def _format_elapsed(seconds: float) -> str:
    if seconds >= 60:
        minutes = int(seconds // 60)
        remainder = seconds - minutes * 60
        return f"{minutes}分{remainder:.1f}秒"
    return f"{seconds:.1f}秒"


def _load_prompt() -> str:
    return resolve_prompt_content("audit.coherence")


def _effective_text(segment: Segment) -> str:
    return (segment.display_text or segment.text or "").strip()


def _is_kept(segment: Segment) -> bool:
    return segment.action in KEPT_ACTIONS


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _chunk_marker(segment: Segment) -> Optional[str]:
    for value in (segment.semantic_unit_id, segment.compiled_from_operation):
        if value and isinstance(value, str) and value.startswith("chunk") and ":" in value:
            return value.split(":", 1)[0]
    return None


def _split_part_action(value, default: SegmentAction = SegmentAction.KEEP) -> SegmentAction:
    if isinstance(value, SegmentAction):
        return value if value in {SegmentAction.KEEP, SegmentAction.DELETE} else default
    action = str(value or "").strip().lower()
    if action == SegmentAction.DELETE.value:
        return SegmentAction.DELETE
    if action == SegmentAction.KEEP.value:
        return SegmentAction.KEEP
    return default


def _find_tail_migration_split(segment: Segment, next_segment: Optional[Segment]) -> Optional[str]:
    if not next_segment or next_segment.action != SegmentAction.DELETE:
        return None
    if next_segment.start - segment.end > TAIL_MIGRATION_MAX_GAP:
        return None

    text = segment.text or ""
    clean = _clean_text(text)
    if not text or len(clean) <= TAIL_MIGRATION_MIN_PREFIX_CHARS:
        return None

    for phrase in sorted(TAIL_MIGRATION_SPLIT_PHRASES, key=len, reverse=True):
        idx = text.rfind(phrase)
        if idx <= 0:
            continue
        prefix = _clean_text(text[:idx])
        tail = _clean_text(text[idx:])
        if (
            len(prefix) >= TAIL_MIGRATION_MIN_PREFIX_CHARS
            and 0 < len(tail) <= TAIL_MIGRATION_MAX_CHARS
        ):
            return phrase
    return None


def _repair_key(repair: dict) -> tuple[str, str, str]:
    return (
        str(repair.get("segment_id") or repair.get("id") or ""),
        str(repair.get("action") or ""),
        str(repair.get("split_before") or repair.get("subtitle_line_break") or ""),
    )


def _merge_payloads(local_payload: dict, model_payload: dict) -> dict:
    """
    Keep deterministic local sentence-boundary repairs even when the model returns
    a sparse pass. The model can add repairs, but should not erase local guardrails.
    """
    merged = dict(model_payload)
    local_issues = [issue for issue in local_payload.get("issues", []) if isinstance(issue, dict)]
    model_issues = [issue for issue in model_payload.get("issues", []) if isinstance(issue, dict)]
    merged["issues"] = local_issues + model_issues

    repairs: List[dict] = []
    seen: set[tuple[str, str, str]] = set()
    local_segment_ids = set()
    for repair in local_payload.get("repairs", []):
        if not isinstance(repair, dict):
            continue
        key = _repair_key(repair)
        repairs.append(repair)
        seen.add(key)
        local_segment_ids.add(key[0])

    for repair in model_payload.get("repairs", []):
        if not isinstance(repair, dict):
            continue
        key = _repair_key(repair)
        if key in seen or key[0] in local_segment_ids:
            continue
        repairs.append(repair)
        seen.add(key)
    merged["repairs"] = repairs

    local_terms = [item for item in local_payload.get("term_fixes", []) if isinstance(item, dict)]
    model_terms = [item for item in model_payload.get("term_fixes", []) if isinstance(item, dict)]
    merged["term_fixes"] = local_terms + model_terms
    if local_issues and model_payload.get("passed") is True:
        merged["passed"] = local_payload.get("passed", True)
        merged["score"] = min(float(model_payload.get("score") or 1.0), float(local_payload.get("score") or 1.0))
    return merged


def _build_review_payload(segments: List[Segment], task_type: Optional[str]) -> str:
    entries = []
    kept_script = []
    chunk_boundaries = []
    previous_marker: Optional[str] = None
    for idx, segment in enumerate(segments, start=1):
        effective = _effective_text(segment)
        action = segment.action.value if isinstance(segment.action, SegmentAction) else str(segment.action)
        marker = _chunk_marker(segment)
        if marker and marker != previous_marker:
            chunk_boundaries.append({"chunk": marker, "starts_at_index": idx, "segment_id": segment.id})
        if marker:
            previous_marker = marker
        entry = {
            "i": idx,
            "id": segment.id,
            "start": segment.start,
            "end": segment.end,
            "action": action,
            "speaker": segment.speaker or "spk0",
            "text": segment.text,
            "display_text": segment.display_text,
            "effective_text": effective,
            "clip_group": segment.clip_group,
            "clip_title": segment.clip_title,
            "style": segment.style,
            "compiled_from_operation": segment.compiled_from_operation,
            "semantic_unit_id": segment.semantic_unit_id,
            "chunk": marker,
            "product_semantic": segment.product_semantic.model_dump() if segment.product_semantic else None,
        }
        entries.append(entry)
        if _is_kept(segment):
            kept_script.append(f"{idx}. [{segment.id}] {effective}")

    payload = {
        "task_type": task_type,
        "kept_script": kept_script,
        "segments": entries,
        "chunk_boundaries": chunk_boundaries,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _parse_json_object(response_text: str) -> Optional[dict]:
    json_text = response_text.strip()
    json_text = re.sub(r"^```(?:json)?\s*", "", json_text, flags=re.MULTILINE)
    json_text = re.sub(r"\s*```$", "", json_text, flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", json_text, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group())
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _issue_from_payload(payload: dict) -> AuditCoherenceIssue:
    return AuditCoherenceIssue(
        code=str(payload.get("code") or "AC-MODEL"),
        severity=str(payload.get("severity") or "minor"),
        message=str(payload.get("message") or "审计连贯性复核发现问题"),
        segment_id=payload.get("segment_id"),
        suggestion=payload.get("suggestion"),
    )


def _term_fix_from_payload(payload: dict) -> Optional[AuditCoherenceTermFix]:
    source = str(payload.get("source") or "").strip()
    target = str(payload.get("target") or "").strip()
    if not source or not target:
        return None
    raw_ids = payload.get("segment_ids") or []
    segment_ids = [str(segment_id) for segment_id in raw_ids if segment_id]
    return AuditCoherenceTermFix(source=source, target=target, segment_ids=segment_ids)


def _report_from_payload(payload: dict, *, repaired_count: int = 0) -> AuditCoherenceReport:
    issues = [
        _issue_from_payload(issue)
        for issue in payload.get("issues", [])
        if isinstance(issue, dict)
    ]
    term_fixes = [
        term_fix
        for term_fix in (
            _term_fix_from_payload(item)
            for item in payload.get("term_fixes", [])
            if isinstance(item, dict)
        )
        if term_fix is not None
    ]
    passed_value = payload.get("passed")
    if isinstance(passed_value, bool):
        passed = passed_value
    else:
        passed = not any(issue.severity in {"blocker", "major"} for issue in issues)
    score = payload.get("score")
    return AuditCoherenceReport(
        passed=passed,
        score=score if isinstance(score, (int, float)) else (1.0 if passed else 0.6),
        issues=issues,
        term_fixes=term_fixes,
        repaired_count=repaired_count,
        validated_at=datetime.now().isoformat(),
        validator_model=str(payload.get("validator_model") or "audit_coherence"),
    )


def _inherit_highlight_metadata(segments: List[Segment], index: int) -> None:
    segment = segments[index]
    if segment.clip_group is not None:
        return
    candidates = []
    if index + 1 < len(segments):
        candidates.append(segments[index + 1])
    if index > 0:
        candidates.append(segments[index - 1])
    for neighbor in candidates:
        if neighbor.clip_group is None:
            continue
        gap = min(abs(neighbor.start - segment.end), abs(segment.start - neighbor.end))
        if gap <= 10:
            segment.clip_group = neighbor.clip_group
            segment.clip_title = None
            segment.style = segment.style or "setup"
            return


def apply_coherence_repairs(segments: List[Segment], repairs: List[dict]) -> Tuple[List[Segment], int]:
    """Apply model/local repair instructions without mutating ASR text or timestamps."""
    repaired = [segment.model_copy(deep=True) for segment in segments]
    index_by_id = {segment.id: idx for idx, segment in enumerate(repaired)}
    applied = 0

    for repair in repairs:
        if not isinstance(repair, dict):
            continue
        segment_id = str(repair.get("segment_id") or repair.get("id") or "").strip()
        if segment_id not in index_by_id:
            continue
        idx = index_by_id[segment_id]
        segment = repaired[idx]
        action = str(repair.get("action") or "").lower()
        reason = str(repair.get("reason") or "连贯性复核自动修复").strip()

        changed = False
        if action == "keep":
            if segment.action != SegmentAction.KEEP:
                segment.action = SegmentAction.KEEP
                segment.reason = reason
                segment.rule = None
                segment.fix_type = None
                segment.display_text = segment.display_text
                changed = True
            if repair.get("clip_group") is not None:
                segment.clip_group = repair.get("clip_group")
            if repair.get("clip_title") is not None:
                segment.clip_title = repair.get("clip_title")
            if repair.get("style"):
                segment.style = str(repair.get("style"))
            _inherit_highlight_metadata(repaired, idx)
        elif action == "delete":
            if segment.action != SegmentAction.DELETE:
                segment.action = SegmentAction.DELETE
                segment.reason = reason
                segment.rule = segment.rule or AuditRule.DEDUP
                segment.clip_group = None
                segment.clip_title = None
                changed = True
        elif action == "text_fix":
            display_text = str(repair.get("display_text") or repair.get("corrected_text") or "").strip()
            segment.action = SegmentAction.TEXT_FIX
            segment.display_text = display_text
            segment.fix_type = "asr_error"
            segment.reason = reason
            changed = True
        elif action == "split":
            split_before = str(repair.get("split_before") or "").strip()
            if split_before:
                segment.action = SegmentAction.SPLIT
                segment.split_before = split_before
                segment.split_part1_action = _split_part_action(repair.get("split_part1_action"), SegmentAction.KEEP)
                segment.split_part2_action = _split_part_action(repair.get("split_part2_action"), SegmentAction.KEEP)
                if segment.split_part1_action == SegmentAction.DELETE or segment.split_part2_action == SegmentAction.DELETE:
                    segment.rule = segment.rule or AuditRule.FRAGMENT
                segment.reason = reason
                changed = True
        elif action == "merge_next":
            if idx + 1 < len(repaired):
                segment.action = SegmentAction.MERGE_NEXT
                segment.reason = reason
                changed = True

        line_break = str(repair.get("subtitle_line_break") or "").strip()
        if line_break and line_break in (segment.text or ""):
            segment.subtitle_line_break = line_break
            changed = True

        if changed:
            applied += 1

    return repaired, applied


def _local_coherence_payload(segments: List[Segment]) -> dict:
    from .word_edits import same_word_edit
    issues: List[dict] = []
    repairs: List[dict] = []
    for idx, segment in enumerate(segments):
        if not _is_kept(segment):
            continue
        text = _clean_text(_effective_text(segment))
        if not text:
            continue
        previous = segments[idx - 1] if idx > 0 else None
        next_segment = segments[idx + 1] if idx + 1 < len(segments) else None

        if previous and previous.action == SegmentAction.DELETE and segment.start - previous.end <= 3 and not same_word_edit(previous, segment):
            if any(text.startswith(prefix) for prefix in DANGLING_PREFIXES):
                issues.append(
                    {
                        "code": "AC-L01",
                        "severity": "minor",
                        "message": f"片段 {segment.id} 以承接词开头，已补回前置上下文 {previous.id}",
                        "segment_id": segment.id,
                        "suggestion": f"保留 {previous.id} 作为必要上下文",
                    }
                )
                repairs.append(
                    {
                        "segment_id": previous.id,
                        "action": "keep",
                        "reason": f"AC-L01: 为片段 {segment.id} 补回前置上下文，避免半句话开场",
                    }
                )

        if next_segment and next_segment.action == SegmentAction.DELETE and next_segment.start - segment.end <= 3 and not same_word_edit(segment, next_segment):
            if any(text.endswith(suffix) for suffix in DANGLING_SUFFIXES):
                issues.append(
                    {
                        "code": "AC-L02",
                        "severity": "minor",
                        "message": f"片段 {segment.id} 结尾像未完成句，已补回后续上下文 {next_segment.id}",
                        "segment_id": segment.id,
                        "suggestion": f"保留 {next_segment.id} 作为句尾延续",
                    }
                )
                repairs.append(
                    {
                        "segment_id": next_segment.id,
                        "action": "keep",
                        "reason": f"AC-L02: 为片段 {segment.id} 补回后续上下文，避免结尾截断",
                    }
                )

        split_before = None if same_word_edit(segment, next_segment) else _find_tail_migration_split(segment, next_segment)
        if split_before:
            issues.append(
                {
                    "code": "AC-L03",
                    "severity": "minor",
                    "message": f"片段 {segment.id} 末尾短语应随下一段隐藏，已从“{split_before}”处拆分",
                    "segment_id": segment.id,
                    "suggestion": f"从“{split_before}”切开，尾部与 {next_segment.id} 一起隐藏",
                }
            )
            repairs.append(
                {
                    "segment_id": segment.id,
                    "action": "split",
                    "split_before": split_before,
                    "split_part1_action": "keep",
                    "split_part2_action": "delete",
                    "reason": f"AC-L03: 末尾短语属于下一段上下文，应随 {next_segment.id} 一起隐藏",
                }
            )

    return {
        "passed": True,
        "score": 1.0 if not issues else 0.92,
        "validator_model": "local_coherence_heuristic",
        "issues": issues,
        "repairs": repairs,
        "term_fixes": [],
    }


async def run_audit_coherence_review(
    segments: List[Segment],
    *,
    task_type: Optional[str] = None,
    provider: str = "openai",
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    ollama_model: str = "deepseek-r1:14b",
    ollama_base_url: str = "http://localhost:11434",
    openai_base_url: str = ROUTERHUB_BASE_URL,
    log_callback: Optional[Callable] = None,
    use_model: bool = True,
) -> Tuple[List[Segment], AuditCoherenceReport]:
    """Review the post-audit kept script and apply safe coherence repairs."""
    started_at = time.perf_counter()
    payload = _local_coherence_payload(segments)
    runtime_model = "local_coherence_heuristic"

    if use_model:
        system_prompt = _load_prompt()
        review_payload = _build_review_payload(segments, task_type)
        try:
            explicit_override = bool(api_key or model or provider == "ollama")
            parsed = None
            if not explicit_override:
                def _validate_route_response(response_text: str) -> dict:
                    parsed_response = _parse_json_object(response_text) if response_text else None
                    if not parsed_response:
                        raise ValueError("审计连贯性模型返回了无效 JSON")
                    return parsed_response

                routed = await route_chat_completion(
                    "audit.coherence",
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": review_payload},
                    ],
                    max_tokens=8192,
                    temperature=0.0,
                    validate=_validate_route_response,
                )
                parsed = routed.value
                runtime_model = routed.candidate.model
                runtime_endpoint = routed.candidate.base_url
                call = None
            elif provider == "ollama":
                runtime_model = ollama_model
                runtime_endpoint = f"{ollama_base_url.rstrip('/')}/v1"
                call = lambda: _call_ollama(
                    system_prompt,
                    review_payload,
                    ollama_model,
                    ollama_base_url,
                )
            else:
                key = resolve_openai_api_key(api_key)
                if key:
                    runtime_model = model or DEFAULT_AUDIT_MODEL
                    runtime_endpoint = openai_base_url.rstrip("/")
                    call = lambda: _call_openai(
                        system_prompt,
                        review_payload,
                        runtime_model,
                        key,
                        openai_base_url,
                    )
                else:
                    call = None
                    runtime_endpoint = openai_base_url.rstrip("/")
            cache_key = content_cache_fingerprint(
                "audit_coherence:v1",
                {
                    "provider": provider,
                    "model": runtime_model,
                    "endpoint": runtime_endpoint,
                    "system_prompt": system_prompt,
                    "input": review_payload,
                },
            )
            if explicit_override:
                parsed = load_content_cache(COHERENCE_CACHE_DIR, cache_key)
                if not isinstance(parsed, dict):
                    parsed = None
                else:
                    record_model_call(
                        feature="audit_coherence",
                        role="audit.coherence",
                        provider="ollama" if provider == "ollama" else "openai_compatible",
                        requested_model=runtime_model,
                        status="cache_hit",
                        started_at=datetime.now(timezone.utc).isoformat(),
                        elapsed_ms=0,
                        from_cache=True,
                        cache_key=cache_key,
                    )
            if parsed is not None and log_callback:
                await log_callback("info", "coherence", "审计连贯性模型复核命中内容缓存")
            if parsed is None and call is not None:
                async def _call_and_parse():
                    response_text = await call()
                    parsed_response = _parse_json_object(response_text) if response_text else None
                    if not parsed_response:
                        raise RetryableExecutionError("审计连贯性模型返回了无效 JSON")
                    return parsed_response

                parsed = await execute_with_retry(_call_and_parse, max_retries=3)
                try:
                    save_content_cache(COHERENCE_CACHE_DIR, cache_key, parsed)
                except OSError:
                    pass
            elif parsed is None and call is None and log_callback:
                await log_callback(
                    "warn",
                    "coherence",
                    "未配置连贯性复核模型，明确使用本地确定性 fallback",
                )
            if parsed:
                payload = _merge_payloads(payload, parsed)
        except Exception as exc:
            runtime_model = "local_coherence_heuristic"
            if log_callback:
                await log_callback(
                    "warn",
                    "coherence",
                    f"审计连贯性模型复核重试耗尽，明确使用本地确定性 fallback | 错误: {exc}",
                )
    elif log_callback:
        await log_callback("info", "coherence", "按配置使用本地确定性连贯性复核")

    repairs = [repair for repair in payload.get("repairs", []) if isinstance(repair, dict)]
    repaired_segments, repaired_count = apply_coherence_repairs(segments, repairs)
    report = _report_from_payload(payload, repaired_count=repaired_count)
    report.validator_model = runtime_model if runtime_model != "local_coherence_heuristic" else report.validator_model
    if log_callback:
        await log_callback(
            "success" if report.passed else "warn",
            "coherence",
            f"审计连贯性复核完成：{'通过' if report.passed else '需人工复核'} | "
            f"自动修复 {repaired_count} 处 | 问题 {len(report.issues)} 项 | "
            f"耗时: {_format_elapsed(time.perf_counter() - started_at)}",
        )
    return repaired_segments, report


async def _call_openai(
    system_prompt: str,
    user_prompt: str,
    model: str,
    api_key: str,
    base_url: str,
) -> str:
    import httpx
    import openai

    transport = httpx.HTTPTransport(proxy=None)
    http_client = httpx.Client(transport=transport)
    client = openai.OpenAI(
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        http_client=http_client,
        timeout=30,
    )
    pricing = manual_pricing_for_feature("audit")
    pricing_source = "manual"
    if pricing is None or not any(value is not None for value in pricing.model_dump().values()):
        pricing = await openrouter_catalog_pricing(base_url, model, api_key)
        pricing_source = "catalog"

    def _call():
        started = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            completion = client.chat.completions.create(
                model=model,
                max_tokens=8192,
                temperature=0.0,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            payload = completion.model_dump()
            record_model_call(
                feature="audit_coherence",
                role="audit.coherence",
                provider_profile_id=provider_profile_id_for_base_url(base_url),
                provider="openai_compatible",
                requested_model=model,
                status="success",
                started_at=started_at,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                response_payload=payload,
                pricing=pricing,
                pricing_source=pricing_source,
            )
            return completion.choices[0].message.content or ""
        except Exception as exc:
            record_model_call(
                feature="audit_coherence",
                role="audit.coherence",
                provider_profile_id=provider_profile_id_for_base_url(base_url),
                provider="openai_compatible",
                requested_model=model,
                status="failed",
                error_type=type(exc).__name__,
                started_at=started_at,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )
            raise

    return await asyncio.wait_for(asyncio.to_thread(_call), timeout=45)


async def _call_ollama(
    system_prompt: str,
    user_prompt: str,
    model: str,
    base_url: str,
) -> str:
    import httpx
    import openai

    transport = httpx.HTTPTransport(proxy=None)
    http_client = httpx.Client(transport=transport)
    client = openai.OpenAI(
        api_key="ollama",
        base_url=f"{base_url.rstrip('/')}/v1",
        http_client=http_client,
        timeout=30,
    )

    def _call():
        completion = client.chat.completions.create(
            model=model,
            max_tokens=8192,
            temperature=0.0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return completion.choices[0].message.content or ""

    return await asyncio.wait_for(asyncio.to_thread(_call), timeout=45)
