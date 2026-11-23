"""
GoldenClip SemanticAuditor
Core AI class that applies scenario-specific rules via Claude API.

Design: 暗金剪辑台 · 编导美学
Flow: Tagged Script → Claude (Scenario Prompt) → JSON Audit Instructions

Scenarios:
  - monologue_clean:     口播精修 (Prompt P1-P8)
  - interview_compress:  直播访谈压缩 (Prompt I1-I8)
  - highlight_reel:      直播卖货集锦 (Prompt L1-L6)
"""

import asyncio
import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable, Tuple, TypedDict
from ..services.app_config_store import load_app_config, resolve_openai_api_key

from .reference_script import ReferenceScriptReport, match_reference_script, build_reference_script_context
from ..models.task import ProductSemanticMark, Segment, SegmentAction, AuditRule, TaskParams, TaskType
from ..models.app_config import DEFAULT_AUDIT_MODEL, ROUTERHUB_BASE_URL
from ..core.task_runner import (
    PermanentExecutionError,
    RetryableExecutionError,
    execute_with_retry,
)
from ..agent_runtime.contracts import validate_audit_edit_plan_contract
from ..services.audit_edit_compiler import (
    _normalize_product_semantic_payload,
    compile_audit_edit_plan,
    parse_audit_edit_plan,
)
from ..services.prompt_registry import audit_prompt_id, resolve_prompt_content
from ..services.model_usage import (
    manual_pricing_for_feature,
    openrouter_catalog_pricing,
    provider_cost,
    record_model_call,
)
from ..services.provider_pricing import provider_profile_id_for_base_url

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

# Scenario → Prompt file mapping
SCENARIO_PROMPT_MAP = {
    TaskType.MONOLOGUE_CLEAN: PROMPTS_DIR / "monologue_clean.md",
    TaskType.INTERVIEW_COMPRESS: PROMPTS_DIR / "interview_compress.md",
    TaskType.HIGHLIGHT_REEL: PROMPTS_DIR / "highlight_reel.md",
}

CLOUD_AUDIT_SINGLE_CALL_LIMIT = 150
CLOUD_AUDIT_CONTEXT_GROUPS = 1
CLOUD_AUDIT_CONTEXT_SEGMENT_LIMIT = 30
CLOUD_AUDIT_MAX_RETRIES = 3
AUDIT_CACHE_DIR = Path(__file__).parent.parent / "data" / "audit_cache"
MODEL_CACHE_DIR = Path(__file__).parent.parent / "data" / "model_cache"
_PROVIDER_PARALLEL_HINTS: Dict[str, int] = {}


@dataclass(frozen=True)
class AuditChunk:
    index: int
    core_start: int
    core_end: int
    context_start: int
    context_end: int


@dataclass
class CloudAuditBatchResult:
    chunk: AuditChunk
    segments: List[Segment]
    model_elapsed: float
    parse_elapsed: float
    retries: int
    throttled: bool = False
    from_cache: bool = False
    chunk_hash: Optional[str] = None


class CloudAuditRetryableError(RetryableExecutionError):
    def __init__(self, message: str, *, retry_after: Optional[float] = None, throttled: bool = False):
        super().__init__(message, retry_after=retry_after)
        self.throttled = throttled


def content_cache_fingerprint(namespace: str, payload: Any) -> str:
    """对不含密钥的模型输入生成稳定内容指纹。"""
    encoded = json.dumps(
        {"namespace": namespace, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_content_cache(cache_dir: Path, cache_key: str) -> Optional[Any]:
    """缓存损坏、结构不完整或并发残留均按 miss 处理。"""
    path = cache_dir / f"{cache_key}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("cache_key") != cache_key:
            return None
        return payload.get("value")
    except (OSError, ValueError, TypeError):
        return None


def save_content_cache(cache_dir: Path, cache_key: str, value: Any) -> None:
    """先完整写入同目录临时文件，再原子替换目标缓存。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{cache_key}.json"
    tmp_path = cache_dir / f".{cache_key}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        tmp_path.write_text(
            json.dumps(
                {"cache_key": cache_key, "created_at": time.time(), "value": value},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _extract_chat_response_content(payload: Dict[str, Any]) -> str:
    """Extract assistant content from OpenAI-compatible chat completion JSON."""
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise ValueError("OpenAI-compatible response missing choices[0]")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    if content is None and isinstance(choices[0].get("text"), str):
        return choices[0]["text"]
    raise ValueError("OpenAI-compatible response missing message.content")


def _parse_openai_compatible_response_text(response_text: str) -> Dict[str, Any]:
    """Parse provider JSON while producing a useful error for gateway HTML/empty bodies."""
    stripped = (response_text or "").strip()
    if not stripped:
        raise ValueError("OpenAI-compatible provider returned empty response body")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        prefix = stripped[:240].replace("\n", "\\n")
        raise ValueError(f"OpenAI-compatible provider returned non-JSON response: {prefix}") from exc
    if not isinstance(payload, dict):
        raise ValueError("OpenAI-compatible provider returned non-object JSON")
    return payload

# Fallback generic prompt
GENERIC_PROMPT_PATH = PROMPTS_DIR / "semantic_audit.md"
SEMANTIC_EDIT_PLAN_PATH = PROMPTS_DIR / "semantic_edit_plan.md"

KEPT_ACTIONS = {
    SegmentAction.KEEP,
    SegmentAction.SUBTITLE_FIX,
    SegmentAction.TEXT_FIX,
    SegmentAction.MERGE_NEXT,
}

SENTENCE_TERMINATORS = "。！？!?；;"
CONTEXT_PREFIXES = (
    "然后",
    "所以",
    "但是",
    "因为",
    "而且",
    "以及",
    "包括",
    "就是",
    "也就是",
    "也",
    "还",
    "都",
    "能",
    "会",
    "可以",
    "才能",
    "想要",
    "需要",
    "喜欢",
    "这个",
    "那个",
    "这种",
    "那种",
    "它",
    "他",
    "她",
)
CONTEXT_SUFFIXES = (
    "因为",
    "所以",
    "但是",
    "而且",
    "以及",
    "包括",
    "如果",
    "当",
    "这个",
    "那个",
    "这种",
    "那种",
    "就是",
    "也就是",
)
SHORT_STANDALONE_UTTERANCES = {"好的", "对", "没错", "是的", "确实", "可以", "行"}


class SentenceGroup(TypedDict):
    segment_ids: List[str]
    indices: List[int]
    text: str
    start: float
    end: float


def load_system_prompt(task_type: Optional[str] = None) -> str:
    """
    Load the scenario-specific SemanticAuditor system prompt.
    Falls back to generic prompt if scenario-specific one is not found.
    """
    if task_type:
        try:
            return resolve_prompt_content(audit_prompt_id(task_type))
        except (KeyError, FileNotFoundError, ValueError):
            pass

    # Fallback to generic
    if GENERIC_PROMPT_PATH.exists():
        return GENERIC_PROMPT_PATH.read_text(encoding="utf-8")

    return _get_default_system_prompt()


def _get_default_system_prompt() -> str:
    return """你是一名拥有十年经验的高级视频编导，专门负责长访谈与口播视频的精修。
    应用8条语义审计规则，将带标记的原始脚本转化为剪辑指令集。
    直接输出JSON数组，不包含任何其他内容。"""


def _format_elapsed(seconds: float) -> str:
    if seconds >= 60:
        minutes = int(seconds // 60)
        remainder = seconds - minutes * 60
        return f"{minutes}分{remainder:.1f}秒"
    return f"{seconds:.1f}秒"


def _strict_parse_failure(message: str, strict: bool, original_segments: List[Segment]) -> List[Segment]:
    if strict:
        raise ValueError(message)
    return original_segments


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        seconds = float(value)
        return max(0.0, min(seconds, 60.0))
    except ValueError:
        return None


def _dynamic_max_tokens(segment_count: int, *, chunked: bool = False) -> int:
    if chunked:
        return min(16384, max(4096, segment_count * 220))
    return min(24576, max(8192, segment_count * 220))


def build_sentence_groups(
    segments: List[Segment],
    sentence_threshold: float = 1.5,
    id_to_idx: Optional[Dict[str, int]] = None,
) -> str:
    """
    将片段按句间停顿合并为"句子组"，注入到 Prompt 中辅助 Claude 做重复句检测。

    id_to_idx: 若提供，使用数字索引替代 UUID 以节省 token。
    """
    sentence_groups = build_sentence_group_metadata(segments, sentence_threshold)
    if not sentence_groups:
        return ""

    sentences = []
    for group_idx, group in enumerate(sentence_groups):
        labels = [str(id_to_idx[segment_id]) if id_to_idx else segment_id for segment_id in group["segment_ids"]]
        id_range = labels[0] if len(labels) == 1 else f"{labels[0]}~{labels[-1]}"
        sentences.append(f"句{group_idx} | {id_range} | {group['text']}")

    if not sentences:
        return ""

    lines = "\n".join(sentences)
    return f"""## 分句结构（重复句检测用）

> 片段间隔≥{sentence_threshold}s 为句子边界。检测重说规则时，比对相邻句开头是否≥5字相同。

```
{lines}
```

"""


def build_sentence_group_metadata(
    segments: List[Segment],
    sentence_threshold: float = 1.5,
) -> List[SentenceGroup]:
    """按相邻片段时间间隔生成结构化句群，供 Prompt 和本地守卫共用。"""
    if not segments:
        return []

    groups: List[SentenceGroup] = []
    current_indices: List[int] = []
    current_ids: List[str] = []
    current_text_parts: List[str] = []

    for i, seg in enumerate(segments):
        current_indices.append(i)
        current_ids.append(seg.id)
        current_text_parts.append(seg.text or "")

        is_last = i == len(segments) - 1
        has_boundary = True
        if not is_last:
            gap = segments[i + 1].start - seg.end
            has_boundary = gap >= sentence_threshold

        if has_boundary and current_ids:
            sentence_text = "".join(current_text_parts).strip()
            if sentence_text:
                start_idx = current_indices[0]
                end_idx = current_indices[-1]
                groups.append(
                    {
                        "segment_ids": list(current_ids),
                        "indices": list(current_indices),
                        "text": sentence_text,
                        "start": segments[start_idx].start,
                        "end": segments[end_idx].end,
                    }
                )
            current_indices = []
            current_ids = []
            current_text_parts = []

    return groups


def _estimate_segment_audit_tokens(segment: Segment) -> int:
    text = segment.text or ""
    tagged_text = segment.tagged_text or ""
    display_text = segment.display_text or ""
    semantic_payload = ""
    if segment.product_semantic:
        try:
            semantic_payload = json.dumps(segment.product_semantic.model_dump(), ensure_ascii=False)
        except Exception:
            semantic_payload = str(segment.product_semantic)
    candidate_payload = ""
    if segment.candidate_edits:
        try:
            candidate_payload = json.dumps([edit.model_dump() for edit in segment.candidate_edits], ensure_ascii=False)
        except Exception:
            candidate_payload = str(segment.candidate_edits)
    char_count = len(text) + len(tagged_text) + len(display_text) + len(semantic_payload) + len(candidate_payload)
    return max(12, int(char_count / 1.6) + 18)


def _estimate_group_audit_tokens(group: SentenceGroup, segments: List[Segment]) -> int:
    token_count = sum(_estimate_segment_audit_tokens(segments[index]) for index in group.get("indices", []))
    return token_count + 24


def plan_audit_chunks(
    segments: List[Segment],
    *,
    chunk_segments: int = 120,
    token_budget: Optional[int] = None,
    context_groups: int = CLOUD_AUDIT_CONTEXT_GROUPS,
    single_call_limit: int = CLOUD_AUDIT_SINGLE_CALL_LIMIT,
) -> List[AuditChunk]:
    """
    按句群边界规划云端审计批次。

    core_start/core_end 是最终需要模型输出并参与归并的范围；
    context_start/context_end 仅用于构建上下文提示，不参与结果覆盖。
    """
    total = len(segments)
    if total == 0:
        return []
    if total <= single_call_limit:
        return [AuditChunk(0, 0, total, 0, total)]

    target = max(40, min(chunk_segments, single_call_limit))
    token_target = max(8000, token_budget or 0)
    groups = build_sentence_group_metadata(segments)
    if not groups:
        chunks: List[AuditChunk] = []
        for start in range(0, total, target):
            end = min(start + target, total)
            chunks.append(AuditChunk(len(chunks), start, end, start, end))
        return chunks

    chunks = []
    group_start = 0
    while group_start < len(groups):
        group_end = group_start
        core_count = 0
        core_tokens = 0
        while group_end < len(groups):
            group_len = len(groups[group_end]["indices"])
            group_tokens = _estimate_group_audit_tokens(groups[group_end], segments)
            exceeds_segment_budget = core_count + group_len > target
            exceeds_token_budget = bool(token_budget) and core_tokens + group_tokens > token_target
            if group_end > group_start and (exceeds_segment_budget or exceeds_token_budget):
                break
            core_count += group_len
            core_tokens += group_tokens
            group_end += 1

        core_start = groups[group_start]["indices"][0]
        core_end = groups[group_end - 1]["indices"][-1] + 1
        context_group_start = max(0, group_start - context_groups)
        context_group_end = min(len(groups), group_end + context_groups)
        context_start = groups[context_group_start]["indices"][0]
        context_end = groups[context_group_end - 1]["indices"][-1] + 1

        before_extra = core_start - context_start
        after_extra = context_end - core_end
        if before_extra + after_extra > CLOUD_AUDIT_CONTEXT_SEGMENT_LIMIT:
            before_keep = min(before_extra, CLOUD_AUDIT_CONTEXT_SEGMENT_LIMIT // 2)
            after_keep = min(after_extra, CLOUD_AUDIT_CONTEXT_SEGMENT_LIMIT - before_keep)
            context_start = core_start - before_keep
            context_end = core_end + after_keep

        chunks.append(AuditChunk(len(chunks), core_start, core_end, context_start, context_end))
        group_start = group_end

    return chunks


def _format_context_segments(segments: List[Segment], start_index: int) -> List[str]:
    lines = []
    for offset, segment in enumerate(segments, start=start_index + 1):
        text = (segment.text or "").strip()
        if not text:
            continue
        speaker = segment.speaker or "spk0"
        lines.append(f"{offset}. [{speaker}] {text}")
    return lines


def _build_chunk_context_block(
    all_segments: List[Segment],
    chunk: AuditChunk,
) -> str:
    before = all_segments[chunk.context_start:chunk.core_start]
    after = all_segments[chunk.core_end:chunk.context_end]
    if not before and not after:
        return ""

    lines = ["## 相邻上下文（仅供理解，不要输出这些片段）", ""]
    if before:
        lines.append("上文：")
        lines.extend(_format_context_segments(before, chunk.context_start))
    if after:
        if before:
            lines.append("")
        lines.append("下文：")
        lines.extend(_format_context_segments(after, chunk.core_end))
    lines.append("")
    lines.append("请只审计下面“待审计片段”中的核心片段；不要为本上下文区块输出 JSON 项。")
    lines.append("")
    return "\n".join(lines)


def _build_global_product_profile_block(all_segments: List[Segment], task_type: Optional[str]) -> str:
    if task_type != "highlight_reel":
        return ""
    product_names: Dict[str, int] = {}
    selling_points: Dict[str, int] = {}
    role_counts: Dict[str, int] = {}
    keyword_samples: List[str] = []
    commerce_keywords = re.compile(r"(这款|这个|产品|面料|价格|优惠|券|链接|拍|买|下单|尺码|颜色|效果|卖点|赠品|福利)")

    for index, segment in enumerate(all_segments, start=1):
        mark = segment.product_semantic
        if mark:
            for role in mark.roles:
                role_counts[role] = role_counts.get(role, 0) + 1
            for entity in mark.product_entities:
                name = entity.canonical_name or entity.name
                if name:
                    product_names[name] = product_names.get(name, 0) + 1
            for point in mark.selling_points:
                if point:
                    selling_points[point] = selling_points.get(point, 0) + 1
        text = (segment.text or "").strip()
        if len(keyword_samples) < 12 and text and commerce_keywords.search(text):
            keyword_samples.append(f"{index}. {text[:80]}")

    if not product_names and not selling_points and not keyword_samples:
        return ""

    def top_items(items: Dict[str, int], limit: int = 8) -> List[str]:
        return [f"{name}({count})" for name, count in sorted(items.items(), key=lambda item: item[1], reverse=True)[:limit]]

    lines = ["## 全局产品画像（供跨分块一致性参考，不要求逐字复述）", ""]
    if product_names:
        lines.append(f"- 高频产品/品类：{'、'.join(top_items(product_names))}")
    if selling_points:
        lines.append(f"- 高频卖点：{'、'.join(top_items(selling_points))}")
    if role_counts:
        lines.append(f"- 已识别内容角色：{'、'.join(top_items(role_counts, 6))}")
    if keyword_samples:
        lines.append("- 代表性卖货片段：")
        lines.extend(keyword_samples)
    lines.append("")
    lines.append("审计时请保留承载产品名称、核心卖点、价格优惠、使用效果、购买转化的信息锚点；删除与产品价值无关的口癖、空转和重复。")
    lines.append("")
    return "\n".join(lines)


def _has_real_speaker_split(segments: List[Segment]) -> bool:
    """只有出现两个及以上非空 speaker 时，才把说话人字段发给 LLM。"""
    speakers = {seg.speaker for seg in segments if seg.speaker}
    return len(speakers) >= 2


def build_audit_prompt(
    segments: List[Segment],
    style_mode: str = "immersive",
    task_type: Optional[str] = None,
    target_duration_ratio: Optional[float] = None,
    reference_report: Optional[ReferenceScriptReport] = None,
) -> Tuple[str, Dict[int, str]]:
    """
    构建发送给 Claude 的用户 Prompt（压缩版）。

    压缩策略：
    - 字段缩写：i=片段序号, t=台词, s=说话人(仅多说话人时), tt=标注台词(仅口播/集锦)
    - 直播卖货集锦模式额外提供 d=片段时长(秒)，用于估算 clip 总时长与保留率
    - 其他场景删除 start/end/duration（Claude 做语义决策不需要时间戳）
    - 访谈模式删除 tagged_text（I7 铁律：不做词级切除，<STU>/<FIL> 标签无用）
    - 输出缩写：a=action, k=keep, d=delete, r=reason(仅 delete 时必填), rule=AuditRule
      特殊动作：sf=subtitle_fix, tf=text_fix, mn=merge_next, sp=split
    - 片段序号用 1-based 整数，代替 8 位 UUID

    返回: (prompt_str, idx_to_id)
      idx_to_id: {1-based序号 → 原始UUID}，供 parse_claude_response 反映射
    """
    # 建立双向映射：1-based 整数索引 ↔ 原始 UUID
    idx_to_id: Dict[int, str] = {}
    id_to_idx: Dict[str, int] = {}
    for i, seg in enumerate(segments):
        idx = i + 1
        idx_to_id[idx] = seg.id
        id_to_idx[seg.id] = idx

    # 访谈模式不发 tagged_text（I7 铁律：不做词级切除）
    include_tagged = task_type != "interview_compress"
    include_speaker = _has_real_speaker_split(segments)

    # 构建压缩片段列表（单行 JSON，不格式化缩进）
    include_duration = task_type == "highlight_reel"
    segments_lines = []
    for i, seg in enumerate(segments):
        entry: dict = {"i": i + 1, "id": seg.id, "t": seg.text}
        if include_speaker:
            entry["s"] = seg.speaker
        if include_duration:
            entry["d"] = round(seg.end - seg.start, 1)
        if include_tagged and seg.tagged_text and seg.tagged_text != seg.text:
            entry["tt"] = seg.tagged_text
        if seg.candidate_edits:
            entry["ce"] = [
                {
                    "src": edit.source,
                    "type": edit.edit_type,
                    "t": edit.text,
                    "s": round(edit.start, 3),
                    "e": round(edit.end, 3),
                    "r": edit.reason,
                }
                for edit in seg.candidate_edits[:5]
            ]
        if include_tagged and seg.words and (seg.candidate_edits or "<STU>" in (seg.tagged_text or "") or "<FIL>" in (seg.tagged_text or "")):
            entry["w"] = [[index, re.sub(r"<[^>]*>", "", word.word)] for index, word in enumerate(seg.words)]
        segments_lines.append(json.dumps(entry, ensure_ascii=False))
    segments_compact = "[\n" + ",\n".join(segments_lines) + "\n]"

    scenario_context = _build_scenario_context(task_type, style_mode, target_duration_ratio, segments, include_speaker)
    if task_type == "monologue_clean":
        scenario_context += build_reference_script_context(reference_report, segments)
    total_duration = sum(s.end - s.start for s in segments)
    speakers = sorted({seg.speaker for seg in segments if seg.speaker})

    sentence_block = ""
    sentence_guard_block = ""
    if task_type in ("monologue_clean", "interview_compress", "highlight_reel"):
        sentence_block = build_sentence_groups(segments, sentence_threshold=1.5, id_to_idx=id_to_idx)
        if sentence_block and task_type in ("monologue_clean", "interview_compress"):
            sentence_guard_block = """## 句群完整性约束
- 分句结构中的同一“句”可能由多个 ASR 片段组成；审计时先判断整句是否成立，再给每个片段动作。
- 同一句内禁止只删除主语、前提、谓语、宾语或承接对象，导致剩余片段变成半句话。
- 如果相邻片段明显属于同一句且都应保留，请对前一片段输出 `a:"mn"`（merge_next），下一片段仍需按要求输出一条结果。
- 如果整句是重说/冗余，可以删除整句内相关片段；不要只删掉前半句后保留依赖它的后半句。

"""
        elif sentence_block and task_type == "highlight_reel":
            sentence_guard_block = """## 句子级产品理解约束
- 审计阶段只负责理解每句话：它提到了什么产品/品类、承担产品开场/卖点/故事/收束/CTA 中哪类角色、是否适合进入集锦。
- 不要在审计阶段直接强行编排“每个集锦如何组合”；后续集锦规划会读取 `product_semantic` 再按产品和卖点角度整合。
- 如果一句同时包含产品卖点和链接下单话术，优先用 `split_at` 或标记 `exclude_reason`，避免把纯 CTA 当作卖点。

"""

    # 缩写说明（放在 Prompt 开头，声明一次即可）
    tagged_hint = " tt=标注台词(<STU>结巴/<FIL>语气词)" if include_tagged else ""
    speaker_hint = " s=说话人(spk0主播/spk1嘉宾)" if include_speaker else ""
    duration_hint = " d=片段时长(秒)" if include_duration else ""
    candidate_hint = " ce=候选编辑(src来源/type类型/s-e原时间轴/r原因)，仅供判断，未自动采纳；w=[0起始字索引,原字]，仅有w时可输出delete_words"
    edit_plan_hint = (
        " 本场景优先输出 AuditEditPlan（operations 对象），直播卖货集锦保留段必须携带 "
        "product_semantic（产品实体、句子角色、卖点角度、是否适合集锦）。"
        if task_type == "highlight_reel"
        else ""
    )
    abbrev_legend = (
        f"## 字段缩写\n"
        f"输入: i=片段序号 t=台词{speaker_hint}{duration_hint}{tagged_hint}{candidate_hint}\n"
        f"输出: a=动作 k=keep d=delete r=原因(仅d时必填)\n"
        f"特殊动作缩写: sf=subtitle_fix tf=text_fix mn=merge_next sp=split{edit_plan_hint}\n\n"
    )

    if task_type == "highlight_reel":
        keep_schema = (
            '优先(edit_plan): {"op":"keep_span","source_ids":[原始片段ID],'
            '"product_semantic":{"product_entities":[{"name":"原文产品名","canonical_name":"规范产品名","category":"品类"}],'
            '"roles":["product_mention|product_opening|selling_point|differentiation_story|use_case|closing|cta"],'
            '"selling_points":["本句提到的卖点"],"angle_label":"卖点角度",'
            '"eligible_for_highlight":true,"exclude_reason":null,"confidence":0.0-1.0},'
            '"text_role":"兼容字段，可与 roles 同义","reason":"为什么保留这句话"} '
            '兼容(数组): {"i":序号,"a":"k","clip_group":整数分组号,'
            '"clip_title":"组首段填写产品名/品类标题，其他保留段为null",'
            '"style":"cold_open|setup|highlight|punchline|resolution",'
            '"product_semantic":{...},"r":"可选保留原因"}'
        )
        delete_schema = (
            '优先(edit_plan): {"op":"delete_span","source_ids":[原始片段ID],'
            '"product_semantic":{"roles":["cta"],"eligible_for_highlight":false,"exclude_reason":"链接/下单/跨产品串场"},'
            '"reason":"链接话术/跨产品串场/无卖点等删除原因",'
            '"rule":"Rule 1|Rule 2|Rule 3|Rule 4|Rule 5|Rule 6|Rule 7|Rule 8"} '
            '兼容(数组): {"i":序号,"a":"d","clip_group":null,"clip_title":null,'
            '"r":"规则编号+简短原因","rule":"Rule 1|Rule 2|Rule 3|Rule 4|Rule 5|Rule 6|Rule 7|Rule 8"}'
        )
    else:
        keep_schema = '{"i":序号,"a":"k"}'
        delete_schema = '{"i":序号,"a":"d","r":"规则编号+简短原因","rule":"Rule 1|Rule 2|Rule 3|Rule 4|Rule 5|Rule 6|Rule 7|Rule 8"}'

    edit_plan_block = ""
    if SEMANTIC_EDIT_PLAN_PATH.exists():
        edit_plan_block = f"""## 自由断句语义编辑计划（优先输出）

{SEMANTIC_EDIT_PLAN_PATH.read_text(encoding="utf-8")}

"""

    prompt = f"""{abbrev_legend}## 任务配置
- 场景: {_get_scenario_name(task_type)} | 风格: {style_mode}
- 片段: {len(segments)}个 | 时长: {total_duration:.0f}秒({total_duration/60:.1f}分钟)
{f"- 说话人: {', '.join(speakers)}" if include_speaker else ""}

{scenario_context}

{sentence_block}{sentence_guard_block}{edit_plan_block}## 待审计片段

```json
{segments_compact}
```

请严格按场景规则审计所有片段。优先输出 `AuditEditPlan` JSON 对象；如果输出旧版 JSON 数组，则每片段必须有结果。
keep片段: {keep_schema}
delete片段: {delete_schema}
delete 片段必须同时给出 r 和 rule；rule 必须使用 Rule 1-Rule 8 之一。
**仅输出 JSON，不要任何解释文字或markdown标记。**"""

    return prompt, idx_to_id


def _infer_audit_rule(reason: str, rule_str: str = "") -> Optional[AuditRule]:
    """把模型自由文本规则归一到本地枚举，避免少数字段缺失导致整次审计失败。"""
    text = f"{rule_str} {reason}".lower()
    if not text.strip():
        return None

    direct_map = {
        "rule 1": AuditRule.RETAKE,
        "p1": AuditRule.RETAKE,
        "i1": AuditRule.RETAKE,
        "rule 2": AuditRule.FRAGMENT,
        "p4": AuditRule.FRAGMENT,
        "rule 3": AuditRule.FILLER,
        "p3": AuditRule.FILLER,
        "rule 4": AuditRule.STUTTER,
        "p2": AuditRule.STUTTER,
        "rule 5": AuditRule.DEDUP,
        "rule 6": AuditRule.INTRA_REPEAT,
        "rule 7": AuditRule.QA_INTEGRITY,
        "rule 8": AuditRule.PACING,
    }
    for marker, rule in direct_map.items():
        if marker in text:
            return rule

    keyword_map = [
        (("重说", "重录", "替代"), AuditRule.RETAKE),
        (("残句", "过短", "碎片", "无法独立"), AuditRule.FRAGMENT),
        (("语气词", "口癖", "废话", "填充"), AuditRule.FILLER),
        (("结巴", "卡顿", "重复字"), AuditRule.STUTTER),
        (("重复", "冗余", "啰嗦"), AuditRule.DEDUP),
        (("句内", "绕口"), AuditRule.INTRA_REPEAT),
        (("问答", "问题", "回答"), AuditRule.QA_INTEGRITY),
        (("气口", "节奏", "停顿"), AuditRule.PACING),
    ]
    for keywords, rule in keyword_map:
        if any(keyword in text for keyword in keywords):
            return rule

    return AuditRule.DEDUP


def _get_scenario_name(task_type: Optional[str]) -> str:
    names = {
        "monologue_clean": "口播精修 (Monologue Clean)",
        "interview_compress": "直播访谈压缩 (Live Interview Compress)",
        "highlight_reel": "直播卖货集锦 (Live Commerce Reel)",
    }
    return names.get(task_type, "通用审计")


def _build_scenario_context(
    task_type: Optional[str],
    style_mode: str,
    target_duration_ratio: Optional[float],
    segments: List[Segment],
    include_speaker: bool = True,
) -> str:
    """Build scenario-specific context hints for the user prompt."""

    total_duration = sum(s.end - s.start for s in segments)

    if task_type == "monologue_clean":
        style_hint = {
            "quick_cut": "快剪模式：气口80ms/50ms，语气词全删，冗余激进压缩（删70%重复），最短片段1.0秒",
            "immersive": "沉浸模式：气口150ms/100ms，情绪词保留，冗余保守压缩（删40%重复），最短片段1.5秒",
        }.get(style_mode, "")

        return f"""## 场景参数
- 风格提示: {style_hint}
- 目标保留率: 60-80%（口播精修保留大部分内容）
- 重点规则: P1(重说) > P2(结巴) > P3(语气词) > P7(开头钩子)
- 预计输出时长: {total_duration * 0.7:.0f}-{total_duration * 0.85:.0f}秒"""

    elif task_type == "interview_compress":
        target_ratio = target_duration_ratio or 0.18
        target_dur = total_duration * target_ratio

        speaker_guidance = ""
        if include_speaker:
            # Count speakers only when diarization produced meaningful turns.
            speakers = {}
            for s in segments:
                if not s.speaker:
                    continue
                speakers[s.speaker] = speakers.get(s.speaker, 0) + (s.end - s.start)

            speaker_info = "\n".join([
                f"  - {spk}: {dur:.0f}秒 ({'主播' if i == 0 else '嘉宾'})"
                for i, (spk, dur) in enumerate(speakers.items())
            ])
            speaker_guidance = f"""
- 嘉宾保留率: 60-70%（spk1片段）
- 主播保留率: 20-30%（spk0片段）
- 说话人时长分布:
{speaker_info}"""

        return f"""## 场景参数
- 目标时长: {target_dur:.0f}秒 ({target_dur/60:.1f}分钟，原始的{target_ratio*100:.0f}%)
{speaker_guidance}
- 重点规则: I1(Q&A闭环) > I3(精华保护) > I2(跨段去重) > I4(主播精简)
- 气口预留: 开头200ms / 结尾150ms"""

    elif task_type == "highlight_reel":
        return f"""## 场景参数
- 审计目标: 理解每句话是否在讲某个产品，以及它承担产品提及、产品开场、卖点、故事/场景、收束、CTA 中哪类角色
- 语义输出: keep/delete 决策之外，必须尽量填写 product_semantic，包含产品实体、句子角色、卖点角度、是否适合集锦及排除原因
- 删除重点: 小黄车/链接/拍几号/下单/库存催单/货架切换/跨产品串场等无卖点话术
- 目标保留率: 10-25%（只保留能说明产品名、卖点、差异化故事的内容）
- 气口预留: 开头80ms / 结尾50ms（快节奏）
- 审计边界: 不要在这里承担最终集锦编排；每个集锦只讲一个产品、保留产品开场/卖点/故事等规则由后续集锦规划层执行
- 兼容要求: 如仍输出旧字段，可继续带 clip_group/clip_title/style；后端会转换为 product_semantic，但优先使用 product_semantic"""

    return ""


def _clean_semantic_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _ends_with_sentence_terminator(text: str) -> bool:
    clean = _clean_semantic_text(text)
    return bool(clean) and clean[-1] in SENTENCE_TERMINATORS


def _starts_with_context_prefix(text: str) -> bool:
    clean = _clean_semantic_text(text)
    return any(clean.startswith(prefix) for prefix in CONTEXT_PREFIXES)


def _ends_with_context_suffix(text: str) -> bool:
    clean = _clean_semantic_text(text)
    return any(clean.endswith(suffix) for suffix in CONTEXT_SUFFIXES)


def _same_speaker_or_unknown(left: Segment, right: Segment) -> bool:
    return not left.speaker or not right.speaker or left.speaker == right.speaker


def _is_kept_action(segment: Segment) -> bool:
    return segment.action in KEPT_ACTIONS


def _is_short_incomplete_fragment(segment: Segment) -> bool:
    text = _clean_semantic_text(segment.text)
    if not text or text in SHORT_STANDALONE_UTTERANCES:
        return False
    if _ends_with_sentence_terminator(text):
        return False
    return len(text) <= 8


def _is_adjacent_within_sentence(left: Segment, right: Segment, sentence_threshold: float) -> bool:
    if not _same_speaker_or_unknown(left, right):
        return False
    gap = right.start - left.end
    return -0.05 <= gap < sentence_threshold


def _should_restore_deleted_context(
    deleted: Segment,
    kept_neighbor: Segment,
    *,
    deleted_is_before: bool,
    sentence_threshold: float,
) -> bool:
    if not _is_adjacent_within_sentence(deleted, kept_neighbor, sentence_threshold):
        return False
    if deleted_is_before:
        return (
            _starts_with_context_prefix(kept_neighbor.text)
            or _is_short_incomplete_fragment(deleted)
            or _ends_with_context_suffix(deleted.text)
        )
    return (
        _ends_with_context_suffix(kept_neighbor.text)
        or _starts_with_context_prefix(deleted.text)
        or _is_short_incomplete_fragment(kept_neighbor)
    )


def _should_merge_adjacent_sentence_parts(
    left: Segment,
    right: Segment,
    sentence_threshold: float,
) -> bool:
    if left.word_deletions or right.word_deletions:
        return False
    if left.action != SegmentAction.KEEP or right.action != SegmentAction.KEEP:
        return False
    if not _is_adjacent_within_sentence(left, right, sentence_threshold):
        return False
    left_text = _clean_semantic_text(left.text)
    right_text = _clean_semantic_text(right.text)
    if not left_text or not right_text:
        return False
    if left_text in SHORT_STANDALONE_UTTERANCES or _ends_with_sentence_terminator(left_text):
        return False
    return (
        _is_short_incomplete_fragment(left)
        or _starts_with_context_prefix(right_text)
        or _ends_with_context_suffix(left_text)
    )


def _append_guard_reason(reason: Optional[str], addition: str) -> str:
    base = (reason or "").strip()
    if not base:
        return addition
    if addition in base:
        return base
    return f"{base} [{addition}]"


def apply_sentence_group_guard(
    segments: List[Segment],
    *,
    task_type: Optional[str] = None,
    sentence_threshold: float = 1.5,
) -> List[Segment]:
    """
    修复同一句被 ASR 切开后出现的危险审计结果：
    - 前后片段属于同一句时，不允许只删必要上下文；
    - 明显同一句的相邻保留片段自动转为 merge_next。
    """
    if task_type not in ("monologue_clean", "interview_compress"):
        return segments

    guarded = [segment.model_copy(deep=True) for segment in segments]
    groups = build_sentence_group_metadata(guarded, sentence_threshold)
    if not groups:
        return guarded

    for group in groups:
        indices = group["indices"]
        if len(indices) < 2:
            continue
        index_set = set(indices)

        for idx in indices:
            segment = guarded[idx]
            if segment.action != SegmentAction.DELETE:
                continue

            previous_segment = guarded[idx - 1] if idx - 1 in index_set else None
            next_segment = guarded[idx + 1] if idx + 1 in index_set else None
            restore = False
            linked_id: Optional[str] = None

            if next_segment and _is_kept_action(next_segment):
                restore = _should_restore_deleted_context(
                    segment,
                    next_segment,
                    deleted_is_before=True,
                    sentence_threshold=sentence_threshold,
                )
                linked_id = next_segment.id if restore else linked_id
            if not restore and previous_segment and _is_kept_action(previous_segment):
                restore = _should_restore_deleted_context(
                    segment,
                    previous_segment,
                    deleted_is_before=False,
                    sentence_threshold=sentence_threshold,
                )
                linked_id = previous_segment.id if restore else linked_id

            if restore:
                segment.action = SegmentAction.KEEP
                segment.rule = None
                segment.reason = (
                    f"SG-L01: 句群完整性保护，保留与片段 {linked_id} 构成完整句"
                    if linked_id else "SG-L01: 句群完整性保护，避免只保留半句话"
                )
                segment.claude_action = segment.action
                segment.claude_reason = segment.reason

        for left_idx, right_idx in zip(indices, indices[1:]):
            left = guarded[left_idx]
            right = guarded[right_idx]
            if _should_merge_adjacent_sentence_parts(left, right, sentence_threshold):
                left.action = SegmentAction.MERGE_NEXT
                left.rule = None
                left.reason = _append_guard_reason(left.reason, "SG-L02: 与下一段属于同一句，自动合并")
                left.claude_action = left.action
                left.claude_reason = left.reason

    return guarded


def parse_claude_response(
    response_text: str,
    original_segments: List[Segment],
    idx_to_id: Optional[Dict[int, str]] = None,
    task_type: Optional[str] = None,
    strict: bool = False,
) -> List[Segment]:
    """
    解析 Claude 的 JSON 输出，将审计结果映射回 Segment 列表。

    同时支持：
    - 新版压缩格式：{i(序号), a(action缩写), r(reason)}
    - 旧版完整格式：{id, action, reason, rule, style, ...}

    动作缩写映射：k=keep, d=delete, sf=subtitle_fix, tf=text_fix, mn=merge_next, sp=split

    idx_to_id: 1-based整数索引 → 原始UUID，由 build_audit_prompt 返回。
    strict=False 时解析失败静默回退到原始 segments（全部 KEEP）；
    strict=True 时抛出 ValueError，供并行批次触发重试。
    """
    # 动作缩写 → 完整字符串
    ACTION_EXPAND = {
        "k": "keep",
        "d": "delete",
        "sf": "subtitle_fix",
        "tf": "text_fix",
        "mn": "merge_next",
        "sp": "split",
    }

    json_text = response_text.strip()

    # 去除 markdown 代码块包裹
    json_text = re.sub(r'^```(?:json)?\s*', '', json_text, flags=re.MULTILINE)
    json_text = re.sub(r'\s*```$', '', json_text, flags=re.MULTILINE)
    json_text = json_text.strip()

    try:
        audit_results = json.loads(json_text)
    except json.JSONDecodeError:
        match = re.search(r'\[.*\]', json_text, re.DOTALL)
        if match:
            try:
                audit_results = json.loads(match.group())
            except Exception:
                return _strict_parse_failure("语义审计响应中的 JSON 数组解析失败", strict, original_segments)
        else:
            return _strict_parse_failure("语义审计响应未包含 JSON 数组或对象", strict, original_segments)

    if isinstance(audit_results, dict) and "operations" in audit_results:
        edit_plan = parse_audit_edit_plan(json_text, idx_to_id=idx_to_id)
        if edit_plan is None:
            return _strict_parse_failure("语义审计 edit_plan 解析失败", strict, original_segments)
        compiled = compile_audit_edit_plan(
            original_segments,
            edit_plan,
            # 模型遗漏必须由契约触发重试，不能把未覆盖片段静默解释为删除。
            default_action=SegmentAction.KEEP,
        )
        edit_report = validate_audit_edit_plan_contract(original_segments, edit_plan, compiled.segments)
        if edit_report.rejected:
            return _strict_parse_failure("语义审计 edit_plan 未通过契约校验", strict, original_segments)
        return compiled.segments

    if not isinstance(audit_results, list):
        return _strict_parse_failure("语义审计响应 JSON 不是数组或 edit_plan 对象", strict, original_segments)

    # 归一化：将新版缩写格式展开为完整字段名
    def _normalize(r: dict) -> dict:
        # i → id（通过 idx_to_id 反映射回原始 UUID）
        if "id" not in r and "i" in r:
            raw_idx = r["i"]
            if idx_to_id and isinstance(raw_idx, int):
                r["id"] = idx_to_id.get(raw_idx, str(raw_idx))
            else:
                r["id"] = str(raw_idx)
        # a → action（展开缩写）
        if "action" not in r and "a" in r:
            r["action"] = ACTION_EXPAND.get(str(r["a"]), str(r["a"]))
        # r → reason
        if "reason" not in r and "r" in r:
            r["reason"] = r["r"]
        return r

    normalized = [_normalize(r) for r in audit_results if isinstance(r, dict)]
    if strict and original_segments and not normalized:
        raise ValueError("语义审计响应数组为空或不含对象项")

    # 按 id 建索引
    result_map = {r.get("id"): r for r in normalized}

    # 使用 model_copy(deep=True) 创建副本，避免原地修改导致历史快照被污染
    updated_segments = []
    for seg in original_segments:
        updated_seg = seg.model_copy(deep=True)
        updated_seg.action = SegmentAction.KEEP
        updated_seg.reason = None
        updated_seg.rule = None
        updated_seg.style = None
        updated_seg.clip_group = None
        updated_seg.clip_title = None
        updated_seg.product_semantic = None
        updated_seg.display_text = None
        updated_seg.fix_type = None
        updated_seg.split_before = None
        updated_seg.split_part1_action = SegmentAction.KEEP
        updated_seg.split_part2_action = SegmentAction.KEEP
        updated_seg.subtitle_line_break = None
        updated_seg.merged = False
        updated_seg.user_override = False
        updated_seg.manual_action = None
        updated_seg.claude_action = None
        updated_seg.claude_reason = None
        result = result_map.get(updated_seg.id)
        if result:
            action_str = result.get("action", "keep").lower()
            if action_str == "delete":
                updated_seg.action = SegmentAction.DELETE
            elif action_str == "subtitle_fix":
                updated_seg.action = SegmentAction.SUBTITLE_FIX
                updated_seg.display_text = result.get("display_text") or result.get("fixed_text")
                updated_seg.fix_type = "stutter"
            elif action_str == "text_fix":
                updated_seg.action = SegmentAction.TEXT_FIX
                updated_seg.display_text = result.get("corrected_text") or result.get("display_text")
                updated_seg.fix_type = "asr_error"
            elif action_str == "split":
                updated_seg.action = SegmentAction.SPLIT
                updated_seg.split_before = result.get("split_before") or result.get("split_at")
                p1 = (result.get("split_part1_action") or "keep").lower()
                p2 = (result.get("split_part2_action") or "keep").lower()
                updated_seg.split_part1_action = SegmentAction.DELETE if p1 == "delete" else SegmentAction.KEEP
                updated_seg.split_part2_action = SegmentAction.DELETE if p2 == "delete" else SegmentAction.KEEP
            elif action_str == "merge_next":
                updated_seg.action = SegmentAction.MERGE_NEXT
            else:
                updated_seg.action = SegmentAction.KEEP
                updated_seg.clip_group = result.get("clip_group")
                updated_seg.clip_title = result.get("clip_title")
                line_break = result.get("subtitle_line_break")
                if line_break:
                    updated_seg.subtitle_line_break = line_break
            updated_seg.reason = result.get("reason", "")
            updated_seg.style = result.get("style")
            product_semantic_payload = _normalize_product_semantic_payload(result)
            if product_semantic_payload:
                try:
                    updated_seg.product_semantic = ProductSemanticMark(**product_semantic_payload)
                except Exception:
                    updated_seg.product_semantic = None

            rule_str = result.get("rule", "")
            if rule_str:
                for rule in AuditRule:
                    if rule.value in rule_str or rule_str in rule.value:
                        updated_seg.rule = rule
                        break
            if not updated_seg.rule:
                updated_seg.rule = _infer_audit_rule(updated_seg.reason or "", rule_str)
            if updated_seg.action == SegmentAction.DELETE:
                if not (updated_seg.reason or "").strip():
                    updated_seg.reason = "模型建议删除：未返回具体原因，已按语义冗余规则标记，建议人工复核"
                if not updated_seg.rule:
                    updated_seg.rule = AuditRule.DEDUP

        updated_segments.append(updated_seg)

    return updated_segments


def apply_segment_corrections(segments: List[Segment]) -> List[Segment]:
    """
    后处理器：处理 split / merge_next 动作，重整段落边界。

    split 流程：
    1. 在 seg.words 中定位 split_before 文本首字符的 start 时间
    2. 将原段切为 part1（头部）和 part2（尾部）
    3. 若 part2 与下一段间隔 < 1.0s，自动合并 part2 与下一段
    4. 用修正后的段列表替换原来两段的位置

    merge_next 流程：
    1. 找到 action=merge_next 的段（seg_a）
    2. 将 seg_a 与紧随其后的 seg_b 合并：
       - end 延伸至 seg_b.end
       - text/tagged_text 拼接
       - words 列表合并
    3. seg_a 动作改为 KEEP，seg_b 从列表移除

    对于 words 列表为空的段（如早期版本数据），降级处理：
    - 按文本字符位置估算时间比例切分
    """
    import re as _re
    import uuid as _uuid
    from .word_edits import apply_word_deletions

    segments = apply_word_deletions(segments)

    result: List[Segment] = []
    skip_ids: set = set()  # 已被合并的下游段 id，跳过不再重复添加

    for i, seg in enumerate(segments):
        if seg.id in skip_ids:
            continue

        # ── merge_next 处理 ────────────────────────────────────────────
        if seg.action == SegmentAction.MERGE_NEXT:
            if i + 1 < len(segments):
                merged_text = seg.text or ""
                merged_tagged = seg.tagged_text or ""
                merged_words = list(seg.words)
                merged_ids: List[str] = []
                merged_source_ids: List[str] = list(seg.source_segment_ids or [seg.id])
                merged_source_ranges = list(seg.source_word_ranges or [])
                merge_idx = i + 1

                while merge_idx < len(segments):
                    next_seg = segments[merge_idx]
                    merged_ids.append(next_seg.id)
                    merged_source_ids.extend(next_seg.source_segment_ids or [next_seg.id])
                    merged_source_ranges.extend(next_seg.source_word_ranges or [])
                    merged_text += next_seg.text or ""
                    merged_tagged += next_seg.tagged_text or ""
                    merged_words += list(next_seg.words)
                    seg.end = round(next_seg.end, 3)
                    skip_ids.add(next_seg.id)
                    if next_seg.action != SegmentAction.MERGE_NEXT:
                        break
                    merge_idx += 1

                # 合并：延伸时间边界、拼接文本和 words
                seg.text = merged_text.strip()
                seg.tagged_text = merged_tagged.strip()
                seg.words = merged_words
                seg.source_segment_ids = list(dict.fromkeys(merged_source_ids))
                seg.source_word_ranges = merged_source_ranges
                seg.action = SegmentAction.KEEP
                seg.merged = True
                merged_id_text = "、".join(merged_ids)
                seg.reason = (seg.reason or "") + f" [已与 {merged_id_text} 合并为一张字幕卡]"
            else:
                # 已是最后一段，无法 merge，降级为 keep
                seg.action = SegmentAction.KEEP
            result.append(seg)
            continue

        if seg.action != SegmentAction.SPLIT or not seg.split_before:
            result.append(seg)
            continue

        # ── 1. 在 words 中定位 split_before 的首字 ──────────────────────
        split_chars = [c for c in seg.split_before if c.strip()]
        split_time: Optional[float] = None

        if seg.words and split_chars:
            target_char = split_chars[0]
            # 逐字匹配：找到第一个字符与 target_char 匹配的 word
            for w_idx, w in enumerate(seg.words):
                raw = _re.sub(r'<[^>]*>', '', w.word).strip()
                if raw == target_char:
                    # 确认后续字符也吻合（最多匹配 split_chars 长度，提升精度）
                    match = True
                    for k, sc in enumerate(split_chars[1:], start=1):
                        if w_idx + k < len(seg.words):
                            nxt = _re.sub(r'<[^>]*>', '', seg.words[w_idx + k].word).strip()
                            if nxt != sc:
                                match = False
                                break
                    if match:
                        split_time = w.start
                        split_word_idx = w_idx
                        break

        if split_time is None:
            # 按文字比例估算（降级方案）
            total_chars = len(seg.text)
            before_chars = seg.text.find(seg.split_before)
            if before_chars > 0:
                ratio = before_chars / total_chars
                split_time = seg.start + (seg.end - seg.start) * ratio
                split_word_idx = None
            else:
                # 无法定位，退化为普通 keep
                seg.action = SegmentAction.KEEP
                result.append(seg)
                continue

        # ── 2. 构造 part1（头部）和 part2（尾部） ────────────────────────
        def _make_seg(
            sid,
            start,
            end,
            words_list,
            action,
            word_start_idx=None,
            word_end_idx=None,
            source_segment_ids_override=None,
            source_word_ranges_override=None,
        ):
            text = "".join(
                _re.sub(r'<[^>]*>', '', w.word)
                for w in words_list
            ).strip() if words_list else ""
            tagged = "".join(w.word for w in words_list).strip() if words_list else ""
            rule = seg.rule if action == SegmentAction.DELETE else None
            return Segment(
                id=sid,
                start=round(start, 3),
                end=round(end, 3),
                text=text,
                tagged_text=tagged,
                speaker=seg.speaker,
                action=action,
                reason=seg.reason,
                rule=rule,
                style=seg.style,
                words=words_list,
                source_segment_ids=source_segment_ids_override or list(seg.source_segment_ids or [seg.id]),
                source_word_ranges=source_word_ranges_override or [
                    {
                        "segment_id": (seg.source_segment_ids or [seg.id])[0],
                        "start_word_index": word_start_idx,
                        "end_word_index": word_end_idx,
                    }
                ],
                compiled_from_operation=seg.compiled_from_operation,
                semantic_unit_id=seg.semantic_unit_id,
                product_semantic=seg.product_semantic.model_copy(deep=True) if seg.product_semantic else None,
            )

        if seg.words and split_word_idx is not None:
            words_part1 = seg.words[:split_word_idx]
            words_part2 = seg.words[split_word_idx:]
        else:
            words_part1 = []
            words_part2 = []

        part1 = _make_seg(
            seg.id + "_p1",
            seg.start,
            split_time,
            words_part1,
            seg.split_part1_action,
            0 if words_part1 else None,
            split_word_idx if words_part1 else None,
        )
        part2_end = seg.end
        part2_words = words_part2
        part2_source_ids = list(seg.source_segment_ids or [seg.id])
        part2_source_ranges = [
            {
                "segment_id": (seg.source_segment_ids or [seg.id])[0],
                "start_word_index": split_word_idx if words_part2 else None,
                "end_word_index": len(seg.words) if words_part2 else None,
            }
        ]

        # ── 3. 尝试与下一段合并（gap < 1.0s）────────────────────────────
        if i + 1 < len(segments):
            next_seg = segments[i + 1]
            gap = next_seg.start - seg.end
            if gap < 1.0 and next_seg.action != SegmentAction.DELETE:
                # 合并 part2 尾部与 next_seg
                part2_end = next_seg.end
                part2_words = part2_words + list(next_seg.words)
                part2_source_ids.extend(next_seg.source_segment_ids or [next_seg.id])
                part2_source_ranges.extend(next_seg.source_word_ranges or [])
                skip_ids.add(next_seg.id)

        part2 = _make_seg(
            seg.id + "_p2",
            split_time,
            part2_end,
            part2_words,
            seg.split_part2_action,
            split_word_idx if words_part2 else None,
            len(seg.words) if words_part2 else None,
            list(dict.fromkeys(part2_source_ids)),
            part2_source_ranges,
        )

        # ── 4. 写入结果 ───────────────────────────────────────────────────
        if part1.text:  # part1 为空（split_before 在段首）则跳过
            result.append(part1)
        result.append(part2)

    return result


def _load_cloud_audit_system_prompt(task_type: Optional[str]) -> str:
    system_prompt = load_system_prompt(task_type)
    user_style_path = Path(__file__).parent.parent / "data" / "user_style.md"
    if user_style_path.exists():
        style_content = user_style_path.read_text("utf-8").strip()
        if style_content and "（待学习）" not in style_content:
            system_prompt += f"\n\n---\n## 该用户的个人剪辑风格偏好\n\n> 以下是该用户历次反馈积累的个人剪辑偏好，请在审计时参考这些偏好做出更贴合用户习惯的决策。\n\n{style_content}"
    return system_prompt


def _build_cloud_batch_prompt(
    all_segments: List[Segment],
    chunk: AuditChunk,
    *,
    style_mode: str,
    task_type: Optional[str],
    reference_report: Optional[ReferenceScriptReport] = None,
) -> Tuple[str, Dict[int, str]]:
    core_segments = all_segments[chunk.core_start:chunk.core_end]
    user_prompt, idx_to_id = build_audit_prompt(core_segments, style_mode, task_type, reference_report=reference_report)
    product_profile = _build_global_product_profile_block(all_segments, task_type)
    context_block = _build_chunk_context_block(all_segments, chunk)
    if product_profile:
        user_prompt = user_prompt.replace("## 待审计片段", f"{product_profile}## 待审计片段", 1)
    if context_block:
        user_prompt = user_prompt.replace("## 待审计片段", f"{context_block}## 待审计片段", 1)
    user_prompt += (
        "\n\n补充要求：优先输出结构化 edit_plan（按连续区间表达 delete_span/keep_span/text_fix 等操作）"
        "；如果无法稳定输出 edit_plan，再输出逐段 JSON。对不确定的保留/删除边界，请在 reason 中写明 uncertainty。"
    )
    return user_prompt, idx_to_id


def _namespace_chunk_metadata(segments: List[Segment], chunk: AuditChunk, task_type: Optional[str]) -> List[Segment]:
    namespaced = [segment.model_copy(deep=True) for segment in segments]
    prefix = f"chunk{chunk.index + 1}"
    clip_offset = (chunk.index + 1) * 1000
    for segment in namespaced:
        if segment.compiled_from_operation:
            segment.compiled_from_operation = f"{prefix}:{segment.compiled_from_operation}"
        if segment.semantic_unit_id:
            segment.semantic_unit_id = f"{prefix}:{segment.semantic_unit_id}"
        if task_type == "highlight_reel" and segment.clip_group is not None:
            segment.clip_group = clip_offset + int(segment.clip_group)
    return namespaced


def _audit_provider_key(base_url: str, model: str) -> str:
    return hashlib.sha256(f"{base_url.rstrip('/')}|{model or DEFAULT_AUDIT_MODEL}".encode("utf-8")).hexdigest()[:16]


def _segment_audit_hash(segment: Segment) -> str:
    payload = {
        "id": segment.id,
        "start": segment.start,
        "end": segment.end,
        "text": segment.text,
        "tagged_text": segment.tagged_text,
        "speaker": segment.speaker,
        "candidate_edits": [edit.model_dump(mode="json") for edit in segment.candidate_edits],
        "words": [word.model_dump(mode="json") for word in segment.words],
        "subtitle_text": segment.subtitle_text,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _chunk_cache_hash(
    all_segments: List[Segment],
    chunk: AuditChunk,
    *,
    system_prompt: str,
    task_type: Optional[str],
    style_mode: str,
    model: str,
    base_url: str,
    reference_report: Optional[ReferenceScriptReport] = None,
) -> str:
    user_prompt, _ = _build_cloud_batch_prompt(
        all_segments,
        chunk,
        style_mode=style_mode,
        task_type=task_type,
        reference_report=reference_report,
    )
    payload = {
        "schema": 3,
        "task_type": task_type,
        "style_mode": style_mode,
        "model": model or DEFAULT_AUDIT_MODEL,
        "base_url": base_url.rstrip("/"),
        "system_prompt_hash": hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
        "user_prompt_hash": hashlib.sha256(user_prompt.encode("utf-8")).hexdigest(),
        "segment_hashes": [_segment_audit_hash(segment) for segment in all_segments[chunk.core_start:chunk.core_end]],
        "core": [
            {
                "id": segment.id,
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
                "tagged_text": segment.tagged_text,
                "speaker": segment.speaker,
            }
            for segment in all_segments[chunk.core_start:chunk.core_end]
        ],
        "context": [
            {
                "id": segment.id,
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
            }
            for segment in all_segments[chunk.context_start:chunk.context_end]
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cache_path_for_chunk(chunk_hash: str) -> Path:
    return AUDIT_CACHE_DIR / f"{chunk_hash}.json"


def _load_cached_batch_result(chunk: AuditChunk, chunk_hash: str) -> Optional[CloudAuditBatchResult]:
    cache_path = _cache_path_for_chunk(chunk_hash)
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if payload.get("chunk_hash") != chunk_hash:
            return None
        segments = [Segment(**item) for item in payload.get("segments", [])]
        if not segments:
            return None
        return CloudAuditBatchResult(
            chunk=chunk,
            segments=segments,
            model_elapsed=0.0,
            parse_elapsed=0.0,
            retries=0,
            throttled=False,
            from_cache=True,
            chunk_hash=chunk_hash,
        )
    except Exception:
        return None


def _save_cached_batch_result(result: CloudAuditBatchResult) -> None:
    if not result.chunk_hash or result.from_cache:
        return
    try:
        cache_path = _cache_path_for_chunk(result.chunk_hash)
        AUDIT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = AUDIT_CACHE_DIR / (
            f".{result.chunk_hash}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            tmp_path.write_text(
                json.dumps({
                    "chunk_hash": result.chunk_hash,
                    "created_at": time.time(),
                    "segment_hashes": [_segment_audit_hash(segment) for segment in result.segments],
                    "segments": [segment.model_dump(mode="json") for segment in result.segments],
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(tmp_path, cache_path)
        finally:
            tmp_path.unlink(missing_ok=True)
    except Exception:
        pass


def _merge_cloud_batch_results(segments: List[Segment], results: List[CloudAuditBatchResult]) -> List[Segment]:
    merged_by_id: Dict[str, Segment] = {}
    expected_ids = {segment.id for segment in segments}
    for result in sorted(results, key=lambda item: item.chunk.index):
        core_ids = {segment.id for segment in segments[result.chunk.core_start:result.chunk.core_end]}
        for segment in result.segments:
            if segment.id not in core_ids:
                raise ValueError(f"批次 {result.chunk.index + 1} 返回了非核心片段 {segment.id}")
            if segment.id in merged_by_id:
                raise ValueError(f"批次归并发现重复片段 {segment.id}")
            merged_by_id[segment.id] = segment

    missing_ids = expected_ids - set(merged_by_id.keys())
    if missing_ids:
        sample = ", ".join(sorted(missing_ids)[:5])
        raise ValueError(f"批次归并缺少 {len(missing_ids)} 个片段结果: {sample}")
    return [merged_by_id[segment.id] for segment in segments]


def _response_retry_error(status_code: int, body: str, retry_after: Optional[str]) -> Optional[CloudAuditRetryableError]:
    if status_code in {408, 429} or status_code >= 500:
        prefix = body[:300].replace("\n", "\\n")
        return CloudAuditRetryableError(
            f"OpenAI-compatible HTTP {status_code}: {prefix}",
            retry_after=_parse_retry_after(retry_after),
            throttled=status_code == 429,
        )
    return None


async def _run_openai_audit_batch(
    client,
    all_segments: List[Segment],
    chunk: AuditChunk,
    *,
    system_prompt: str,
    task_type: Optional[str],
    style_mode: str,
    model: str,
    api_key: str,
    base_url: str,
    log_callback: Optional[Callable],
    total_chunks: int,
    chunk_hash: Optional[str] = None,
    reference_report: Optional[ReferenceScriptReport] = None,
) -> CloudAuditBatchResult:
    import httpx

    core_segments = all_segments[chunk.core_start:chunk.core_end]
    user_prompt, idx_to_id = _build_cloud_batch_prompt(
        all_segments,
        chunk,
        style_mode=style_mode,
        task_type=task_type,
        reference_report=reference_report,
    )
    dynamic_max_tokens = _dynamic_max_tokens(len(core_segments), chunked=total_chunks > 1)
    api_url = f"{base_url.rstrip('/')}/chat/completions"
    throttled = False
    attempts = 0

    async def _record_usage(
        status: str,
        started_at: str,
        elapsed_ms: int,
        payload: Optional[dict] = None,
        error_type: Optional[str] = None,
    ) -> None:
        response_payload = payload or {}
        pricing = manual_pricing_for_feature("audit")
        pricing_source = "manual"
        if provider_cost(response_payload) is None and (
            pricing is None
            or not any(value is not None for value in pricing.model_dump().values())
        ):
            pricing = await openrouter_catalog_pricing(base_url, model, api_key)
            pricing_source = "catalog"
        record_model_call(
            feature="audit",
            role="audit.semantic",
            stage=f"chunk_{chunk.index + 1}",
            provider_profile_id=provider_profile_id_for_base_url(base_url),
            provider="openai_compatible",
            requested_model=model or DEFAULT_AUDIT_MODEL,
            attempt=attempts,
            status=status,
            error_type=error_type,
            started_at=started_at,
            elapsed_ms=elapsed_ms,
            response_payload=response_payload,
            pricing=pricing,
            pricing_source=pricing_source,
        )

    async def _operation() -> CloudAuditBatchResult:
        nonlocal attempts, throttled
        attempts += 1
        if log_callback:
            retry_part = f" | 第 {attempts} 次尝试" if attempts > 1 else ""
            await log_callback(
                "info",
                "openai",
                f"第 {chunk.index + 1}/{total_chunks} 批模型调用中 | "
                f"片段 {chunk.core_start + 1}-{chunk.core_end} | max_tokens: {dynamic_max_tokens}{retry_part}",
            )

        model_started_at = time.perf_counter()
        model_started_iso = datetime.now(timezone.utc).isoformat()
        try:
            response = await client.post(
                api_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model or DEFAULT_AUDIT_MODEL,
                    "max_tokens": dynamic_max_tokens,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                },
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            await _record_usage(
                "failed",
                model_started_iso,
                int((time.perf_counter() - model_started_at) * 1000),
                error_type=type(exc).__name__,
            )
            raise RetryableExecutionError(str(exc)) from exc

        model_elapsed = time.perf_counter() - model_started_at
        raw_response_text = response.text or ""
        retry_error = _response_retry_error(
            response.status_code,
            raw_response_text,
            response.headers.get("retry-after"),
        )
        if retry_error:
            throttled = throttled or retry_error.throttled
            await _record_usage(
                "failed",
                model_started_iso,
                int(model_elapsed * 1000),
                error_type=type(retry_error).__name__,
            )
            raise retry_error
        if response.status_code >= 400:
            prefix = raw_response_text[:500].replace("\n", "\\n")
            error = PermanentExecutionError(
                f"OpenAI-compatible HTTP {response.status_code}: {prefix}"
            )
            error.status_code = response.status_code
            await _record_usage(
                "failed",
                model_started_iso,
                int(model_elapsed * 1000),
                error_type=type(error).__name__,
            )
            raise error

        response_payload: dict = {}
        usage_recorded = False
        try:
            response_payload = _parse_openai_compatible_response_text(raw_response_text)
            if response_payload.get("error"):
                raise ValueError(
                    f"OpenAI-compatible provider error: {response_payload.get('error')}"
                )
            await _record_usage(
                "success",
                model_started_iso,
                int(model_elapsed * 1000),
                response_payload,
            )
            usage_recorded = True
            response_text = _extract_chat_response_content(response_payload)
            parse_started_at = time.perf_counter()
            parsed_segments = parse_claude_response(
                response_text,
                core_segments,
                idx_to_id=idx_to_id,
                task_type=task_type,
                strict=True,
            )
        except ValueError as exc:
            if not usage_recorded:
                await _record_usage(
                    "failed",
                    model_started_iso,
                    int(model_elapsed * 1000),
                    response_payload,
                    error_type=type(exc).__name__,
                )
            raise RetryableExecutionError(
                f"第 {chunk.index + 1}/{total_chunks} 批响应解析失败: {exc}"
            ) from exc
        parse_elapsed = time.perf_counter() - parse_started_at
        parsed_segments = _namespace_chunk_metadata(parsed_segments, chunk, task_type)
        if log_callback:
            deleted = sum(1 for segment in parsed_segments if segment.action == SegmentAction.DELETE)
            await log_callback(
                "info",
                "openai",
                f"第 {chunk.index + 1}/{total_chunks} 批完成 | "
                f"模型耗时: {_format_elapsed(model_elapsed)} | 删除 {deleted}/{len(parsed_segments)}",
            )
        return CloudAuditBatchResult(
            chunk=chunk,
            segments=parsed_segments,
            model_elapsed=model_elapsed,
            parse_elapsed=parse_elapsed,
            retries=attempts - 1,
            throttled=throttled,
            chunk_hash=chunk_hash,
        )

    async def _on_retry(_context, exc: BaseException, _delay: float) -> None:
        if not log_callback:
            return
        retry_throttled = bool(getattr(exc, "throttled", False))
        level = "warn" if retry_throttled else "info"
        reason = "供应商限流，等待后重试" if retry_throttled else "批次调用失败，准备重试"
        await log_callback(
            level,
            "openai",
            f"第 {chunk.index + 1}/{total_chunks} 批{reason}: {exc}",
        )

    try:
        return await execute_with_retry(
            _operation,
            max_retries=CLOUD_AUDIT_MAX_RETRIES,
            on_retry=_on_retry,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"第 {chunk.index + 1}/{total_chunks} 批审计失败（共尝试 {attempts} 次）: {exc}"
        ) from exc


async def _run_openai_parallel_audit(
    segments: List[Segment],
    *,
    task_type: Optional[str],
    style_mode: str,
    api_key: str,
    model: str,
    openai_base_url: str,
    log_callback: Optional[Callable],
    chunk_segments: int,
    max_parallel_chunks: int,
    initial_parallel_chunks: int = 4,
    chunk_token_budget: int = 24000,
    enable_batch_resume: bool = False,
    enable_incremental_cache: bool = False,
    reference_script: str = "",
) -> List[Segment]:
    import httpx

    reference_report = await asyncio.to_thread(match_reference_script, reference_script, segments) if reference_script and task_type == "monologue_clean" else None
    chunks = plan_audit_chunks(segments, chunk_segments=chunk_segments, token_budget=chunk_token_budget)
    total_chunks = len(chunks)
    system_prompt = _load_cloud_audit_system_prompt(task_type)
    max_parallel = max(1, min(max_parallel_chunks, total_chunks))
    provider_key = _audit_provider_key(openai_base_url, model)
    provider_hint = _PROVIDER_PARALLEL_HINTS.get(provider_key, initial_parallel_chunks)
    target_parallel = max(1, min(provider_hint, max_parallel))
    # 断点恢复是流水线正确性的一部分；两个开关保留为兼容字段，不再关闭内容寻址缓存。
    use_cache = True
    completed: List[CloudAuditBatchResult] = []
    pending_chunks = list(chunks)
    active: Dict[asyncio.Task, AuditChunk] = {}
    consecutive_successes = 0
    started_at = time.perf_counter()

    if log_callback:
        mode_text = "单批" if total_chunks == 1 else f"自适应并行 | 初始 {target_parallel} 路 | 上限 {max_parallel} 路"
        await log_callback(
            "info",
            "openai",
            f"云端审计准备就绪 | 片段 {len(segments)} | 批次 {total_chunks} | {mode_text} | token预算 {chunk_token_budget}",
            None,
            stage="model_call",
            event="audit_chunk_planned",
            total_chunks=total_chunks,
            current_parallel=target_parallel,
            max_parallel=max_parallel,
        )

    timeout = httpx.Timeout(connect=30.0, read=180.0, write=30.0, pool=30.0)
    transport = httpx.AsyncHTTPTransport(proxy=None)
    async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
        try:
            while pending_chunks or active:
                while pending_chunks and len(active) < target_parallel:
                    chunk = pending_chunks.pop(0)
                    chunk_hash = _chunk_cache_hash(
                        segments,
                        chunk,
                        system_prompt=system_prompt,
                        task_type=task_type,
                        style_mode=style_mode,
                        model=model or DEFAULT_AUDIT_MODEL,
                        base_url=openai_base_url,
                        reference_report=reference_report,
                    )
                    if use_cache:
                        cached = _load_cached_batch_result(chunk, chunk_hash)
                        if cached:
                            completed.append(cached)
                            record_model_call(
                                feature="audit",
                                role="audit.semantic",
                                stage=f"chunk_{chunk.index + 1}",
                                provider="openai_compatible",
                                requested_model=model or DEFAULT_AUDIT_MODEL,
                                status="cache_hit",
                                started_at=datetime.now(timezone.utc).isoformat(),
                                elapsed_ms=0,
                                from_cache=True,
                                cache_key=chunk_hash,
                            )
                            if log_callback:
                                progress = min(0.82, 0.08 + 0.74 * (len(completed) / total_chunks))
                                await log_callback(
                                    "info",
                                    "openai",
                                    f"第 {chunk.index + 1}/{total_chunks} 批命中缓存 | 片段 {chunk.core_start + 1}-{chunk.core_end}",
                                    progress,
                                    stage="model_call",
                                    event="audit_cache_hit",
                                    chunk_index=chunk.index + 1,
                                    completed_chunks=len(completed),
                                    total_chunks=total_chunks,
                                )
                            continue
                    task = asyncio.create_task(
                        _run_openai_audit_batch(
                            client,
                            segments,
                            chunk,
                            system_prompt=system_prompt,
                            task_type=task_type,
                            style_mode=style_mode,
                            model=model or DEFAULT_AUDIT_MODEL,
                            api_key=api_key,
                            base_url=openai_base_url,
                            log_callback=log_callback,
                            total_chunks=total_chunks,
                            chunk_hash=chunk_hash,
                            reference_report=reference_report,
                        )
                    )
                    active[task] = chunk

                if not active:
                    continue
                done, _ = await asyncio.wait(active.keys(), return_when=asyncio.FIRST_COMPLETED)
                successful_done: List[CloudAuditBatchResult] = []
                first_error: Optional[BaseException] = None
                for task in done:
                    active.pop(task, None)
                    try:
                        result = task.result()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        first_error = first_error or exc
                        continue
                    successful_done.append(result)

                # 同一个 done 集合可能同时包含成功和失败任务。必须先把所有成功批次
                # 原子落盘，随后才能传播失败，确保重启只补失败/缺失批次。
                for result in successful_done:
                    completed.append(result)
                    if use_cache:
                        _save_cached_batch_result(result)

                if first_error is not None:
                    if active:
                        for running in active:
                            running.cancel()
                        await asyncio.gather(*active.keys(), return_exceptions=True)
                    raise first_error

                for result in successful_done:
                    if result.throttled and target_parallel > 1:
                        target_parallel = max(1, target_parallel // 2)
                        _PROVIDER_PARALLEL_HINTS[provider_key] = target_parallel
                        consecutive_successes = 0
                        if log_callback:
                            await log_callback(
                                "warn",
                                "openai",
                                f"检测到供应商限流，云端审计并发降至 {target_parallel} 路",
                                None,
                                stage="model_call",
                                event="audit_chunk_retry",
                                current_parallel=target_parallel,
                                throttled=True,
                            )
                    else:
                        consecutive_successes += 1
                        if consecutive_successes >= target_parallel and target_parallel < max_parallel:
                            target_parallel += 1
                            _PROVIDER_PARALLEL_HINTS[provider_key] = target_parallel
                            consecutive_successes = 0
                            if log_callback:
                                await log_callback(
                                    "info",
                                    "openai",
                                    f"批次稳定完成，云端审计并发提升至 {target_parallel} 路",
                                    None,
                                    stage="model_call",
                                    event="audit_parallel_increased",
                                    current_parallel=target_parallel,
                                )

                    if log_callback and total_chunks > 1:
                        progress = min(0.82, 0.08 + 0.74 * (len(completed) / total_chunks))
                        await log_callback(
                            "info",
                            "openai",
                            f"云端审计进度 {len(completed)}/{total_chunks} 批 | 当前并发 {target_parallel} 路",
                            progress,
                            stage="model_call",
                            event="audit_chunk_completed",
                            completed_chunks=len(completed),
                            total_chunks=total_chunks,
                            current_parallel=target_parallel,
                            cache_hits=sum(1 for result in completed if result.from_cache),
                        )
        except asyncio.CancelledError:
            for running in active:
                running.cancel()
            await asyncio.gather(*active.keys(), return_exceptions=True)
            raise

    merged = _merge_cloud_batch_results(segments, completed)
    for segment in merged:
        segment.claude_action = segment.action
        segment.claude_reason = segment.reason

    if log_callback:
        model_elapsed_total = sum(result.model_elapsed for result in completed)
        parse_elapsed_total = sum(result.parse_elapsed for result in completed)
        await log_callback(
            "success",
            "openai",
            f"云端审计批次全部完成 | 批次 {total_chunks} | "
            f"模型累计耗时: {_format_elapsed(model_elapsed_total)} | "
            f"解析累计耗时: {_format_elapsed(parse_elapsed_total)} | "
            f"墙钟耗时: {_format_elapsed(time.perf_counter() - started_at)}",
        )
    return merged


async def run_semantic_audit(
    segments: List[Segment],
    params: TaskParams,
    task_type: Optional[str] = None,
    api_key: Optional[str] = None,
    model: str = DEFAULT_AUDIT_MODEL,
    style_mode: str = "immersive",
    log_callback: Optional[Callable] = None,
    force_rule_engine: bool = False,
    provider: str = "openai",
    ollama_model: str = "deepseek-r1:32b",
    ollama_base_url: str = "http://localhost:11434",
    openai_base_url: str = ROUTERHUB_BASE_URL,
) -> List[Segment]:
    """
    Run semantic audit using OpenAI cloud models or local Ollama.

    Args:
        segments: List of ASR segments to audit
        params: Task parameters (thresholds, rules, etc.)
        task_type: Scenario type ("monologue_clean", "interview_compress", "highlight_reel")
        api_key: OpenAI API key（provider="openai" 时使用）
        model: OpenAI 模型名（provider="openai" 时使用）
        style_mode: "quick_cut" or "immersive"
        log_callback: Async callback for real-time logging
        force_rule_engine: 用户在前端明确确认后传 True，才使用规则引擎；
                           False 时若无 API Key 则直接抛出异常，不静默降级。
        provider: "openai" | "ollama"
        ollama_model: 本地 Ollama 模型名，如 "deepseek-r1:32b"
        ollama_base_url: Ollama 服务地址，默认 "http://localhost:11434"
    """
    scenario_name = _get_scenario_name(task_type)
    total_started_at = time.perf_counter()

    # 规则引擎模式：仅当用户在前端明确确认时才进入
    if force_rule_engine:
        if log_callback:
            await log_callback(
                "info",
                "rule_engine",
                f"开始审计 | 调用: 本地规则引擎 | 模型: 无 | 场景: {scenario_name} | 片段: {len(segments)}",
            )
        result = await _rule_based_audit(segments, params, task_type, log_callback)
        if log_callback:
            await log_callback(
                "success",
                "rule_engine",
                f"本地规则引擎审计完成 | 运行时间: {_format_elapsed(time.perf_counter() - total_started_at)}",
            )
        return result

    # ── Ollama 本地推理路径 ────────────────────────────────────────────────
    if provider == "ollama":
        return await _ollama_audit(
            segments=segments,
            params=params,
            task_type=task_type,
            style_mode=style_mode,
            ollama_model=ollama_model,
            ollama_base_url=ollama_base_url,
            log_callback=log_callback,
        )

    if provider == "claude":
        provider = "openai"

    if log_callback:
        await log_callback(
            "info",
            "openai",
            f"开始审计 | 调用: 云端 OpenAI-compatible | 模型: {model or DEFAULT_AUDIT_MODEL} | 场景: {scenario_name}",
        )

    # Priority: passed key → app_config.json → environment variables
    key = resolve_openai_api_key(api_key)

    if not key:
        # 不再静默降级，直接报错，由前端引导用户配置 Key 或主动选择规则引擎
        raise ValueError("未配置 OpenAI API Key，请在设置中填写 OpenAI API Key，或选择使用本地规则引擎")

    try:
        audit_runtime = load_app_config().audit_runtime
        if log_callback:
            await log_callback(
                "info",
                "openai",
                f"准备发送 {len(segments)} 个片段 | 风格: {style_mode} | 端点: {openai_base_url.rstrip('/')} | "
                f"分块: {audit_runtime.cloud_chunk_segments} | 初始并发: {audit_runtime.cloud_initial_parallel_chunks} | "
                f"最大并发: {audit_runtime.cloud_max_parallel_chunks} | token预算: {audit_runtime.cloud_chunk_token_budget}",
            )
        updated_segments = await _run_openai_parallel_audit(
            segments,
            task_type=task_type,
            style_mode=style_mode,
            api_key=key,
            model=model or DEFAULT_AUDIT_MODEL,
            openai_base_url=openai_base_url,
            log_callback=log_callback,
            chunk_segments=audit_runtime.cloud_chunk_segments,
            max_parallel_chunks=audit_runtime.cloud_max_parallel_chunks,
            initial_parallel_chunks=audit_runtime.cloud_initial_parallel_chunks,
            chunk_token_budget=audit_runtime.cloud_chunk_token_budget,
            enable_batch_resume=audit_runtime.enable_batch_resume,
            enable_incremental_cache=audit_runtime.enable_incremental_cache,
            reference_script=params.reference_script,
        )
        missing_delete_metadata = sum(
            1
            for s in updated_segments
            if s.action == SegmentAction.DELETE and (not (s.reason or "").strip() or not s.rule)
        )
        if log_callback:
            await log_callback(
                "info",
                "openai",
                f"删除片段元数据归一化完成 | 缺失 reason/rule: {missing_delete_metadata}",
            )

        # 解析后检测：若所有片段均为 KEEP 且输入片段数 > 10，极可能是响应被截断导致 JSON 解析失败
        deleted_after_parse = sum(1 for s in updated_segments if s.action == SegmentAction.DELETE)
        if deleted_after_parse == 0 and len(segments) > 10 and log_callback:
            await log_callback(
                "warn", "openai",
                "⚠️ 解析结果全部为 KEEP（0 删除），请确认是否符合预期。"
                "如模型本应删除内容，请重新触发审计，或检查提示词和输出 token 上限。"
            )

        # 冻结 AI 原始决策，后续用户修改不会覆盖这两个兼容字段
        for seg in updated_segments:
            seg.claude_action = seg.action
            seg.claude_reason = seg.reason

        kept = sum(1 for s in updated_segments if s.action == SegmentAction.KEEP)
        deleted = sum(1 for s in updated_segments if s.action == SegmentAction.DELETE)
        kept_duration = sum(s.end - s.start for s in updated_segments if s.action == SegmentAction.KEEP)
        total_duration = sum(s.end - s.start for s in segments)
        retention_rate = kept_duration / total_duration * 100 if total_duration > 0 else 0

        if log_callback:
            await log_callback(
                "success", "openai",
                f"审计完成 | 保留 {kept} 片段，删除 {deleted} 片段 | "
                f"保留时长 {kept_duration:.1f}s ({retention_rate:.0f}%) | "
                f"总运行时间: {_format_elapsed(time.perf_counter() - total_started_at)}"
            )

        return updated_segments

    except asyncio.CancelledError:
        raise
    except Exception as e:
        if log_callback:
            await log_callback(
                "error",
                "openai",
                f"OpenAI-compatible 调用失败 | 模型: {model or DEFAULT_AUDIT_MODEL} | "
                f"运行时间: {_format_elapsed(time.perf_counter() - total_started_at)} | 错误: {str(e)}",
            )
        raise


async def _ollama_audit(
    segments: List[Segment],
    params: TaskParams,
    task_type: Optional[str],
    style_mode: str,
    ollama_model: str,
    ollama_base_url: str,
    log_callback: Optional[Callable],
    chunk_size: int = 60,
) -> List[Segment]:
    """
    使用本地 Ollama（OpenAI 兼容接口）进行语义审计。
    接口地址: {ollama_base_url}/v1/chat/completions

    分片策略：将片段按 chunk_size 切分，逐片推理后合并结果。
    每片独立索引（从 1 开始），解析后映射回全局 UUID，最终拼装成完整列表。
    """
    scenario_name = _get_scenario_name(task_type)
    base_url = ollama_base_url.rstrip("/")
    total = len(segments)
    n_chunks = max(1, (total + chunk_size - 1) // chunk_size)
    total_started_at = time.perf_counter()
    model_elapsed_total = 0.0

    if log_callback:
        await log_callback(
            "info", "ollama",
            f"开始审计 | 调用: 本地 Ollama | 模型: {ollama_model} | 地址: {base_url} | 场景: {scenario_name} | "
            f"共 {total} 片段，分 {n_chunks} 批处理（每批≤{chunk_size}）"
        )

    # 加载场景 Prompt（所有批次复用同一 system prompt）
    system_prompt = load_system_prompt(task_type)

    # 注入个人风格档案
    user_style_path = Path(__file__).parent.parent / "data" / "user_style.md"
    if user_style_path.exists():
        style_content = user_style_path.read_text("utf-8").strip()
        if style_content and "（待学习）" not in style_content:
            system_prompt += (
                f"\n\n---\n## 该用户的个人剪辑风格偏好\n\n"
                f"> 以下是该用户历次反馈积累的个人剪辑偏好，请在审计时参考。\n\n{style_content}"
            )

    try:
        try:
            import openai
        except ImportError:
            import subprocess, sys
            subprocess.check_call([sys.executable, "-m", "pip", "install", "openai", "-q"])
            import openai

        import httpx
        # 使用 HTTPTransport(proxy=None) 显式绕过系统代理（VPN/ClashX 等），确保本地 Ollama 直连
        _transport = httpx.HTTPTransport(proxy=None)
        http_client = httpx.Client(transport=_transport)
        client = openai.OpenAI(
            api_key="ollama",  # Ollama 不需要真实 key，但 openai SDK 要求非空
            base_url=f"{base_url}/v1",
            http_client=http_client,
        )

        all_results: List[Segment] = []
        reference_report = await asyncio.to_thread(match_reference_script, params.reference_script, segments) if params.reference_script and task_type == "monologue_clean" else None

        for chunk_idx in range(n_chunks):
            chunk_start = chunk_idx * chunk_size
            chunk_end = min(chunk_start + chunk_size, total)
            chunk_segs = segments[chunk_start:chunk_end]

            if log_callback:
                await log_callback(
                    "info", "ollama",
                    f"第 {chunk_idx + 1}/{n_chunks} 批模型调用中... | 片段 {chunk_start + 1}-{chunk_end} | 模型: {ollama_model}"
                )

            # 为本批构建 Prompt，idx 从 1 开始（独立于全局索引）
            user_prompt, idx_to_id = build_audit_prompt(chunk_segs, style_mode, task_type, reference_report=reference_report)

            # 每片段约 150 token 输出，1.5x 安全系数
            dynamic_max_tokens = min(16384, max(2048, len(chunk_segs) * 150 * 3 // 2))

            # 异步环境下用 asyncio.to_thread 避免阻塞事件循环

            def _call_ollama():
                return client.chat.completions.create(
                    model=ollama_model,
                    max_tokens=dynamic_max_tokens,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_prompt},
                    ]
                )

            async def _call_and_parse_ollama():
                chunk_model_started_at = time.perf_counter()
                completion = await asyncio.wait_for(
                    asyncio.to_thread(_call_ollama),
                    timeout=180,
                )
                chunk_model_elapsed = time.perf_counter() - chunk_model_started_at
                response_text = completion.choices[0].message.content or ""
                try:
                    chunk_results = parse_claude_response(
                        response_text,
                        chunk_segs,
                        idx_to_id,
                        task_type=task_type,
                        strict=True,
                    )
                except ValueError as exc:
                    raise RetryableExecutionError(f"Ollama 审计响应解析失败: {exc}") from exc
                return response_text, chunk_results, chunk_model_elapsed

            response_text, chunk_results, chunk_model_elapsed = await execute_with_retry(
                _call_and_parse_ollama,
                max_retries=3,
            )
            model_elapsed_total += chunk_model_elapsed

            if log_callback:
                await log_callback(
                    "info", "ollama",
                    f"第 {chunk_idx + 1}/{n_chunks} 批响应已接收 | 模型耗时: {_format_elapsed(chunk_model_elapsed)} | "
                    f"输出: {len(response_text)} 字符，开始解析"
                )

            missing_delete_metadata = sum(
                1
                for s in chunk_results
                if s.action == SegmentAction.DELETE and (not (s.reason or "").strip() or not s.rule)
            )
            if log_callback:
                await log_callback(
                    "info",
                    "ollama",
                    f"第 {chunk_idx + 1}/{n_chunks} 批删除片段元数据归一化完成 | 缺失 reason/rule: {missing_delete_metadata}",
                )
            all_results.extend(chunk_results)

        if log_callback:
            await log_callback(
                "success", "ollama",
                f"本地 Ollama 审计完成 | 模型: {ollama_model} | 批次: {n_chunks} | "
                f"模型总耗时: {_format_elapsed(model_elapsed_total)} | 总运行时间: {_format_elapsed(time.perf_counter() - total_started_at)} | "
                f"共 {len(all_results)} 个片段结果已合并"
            )

        return all_results

    except Exception as e:
        if log_callback:
            await log_callback(
                "error",
                "ollama",
                f"Ollama 推理失败 | 模型: {ollama_model} | 运行时间: {_format_elapsed(time.perf_counter() - total_started_at)} | 错误: {str(e)}",
            )
        raise


async def _rule_based_audit(
    segments: List[Segment],
    params: TaskParams,
    task_type: Optional[str] = None,
    log_callback: Optional[Callable] = None
) -> List[Segment]:
    """
    Rule-based fallback audit engine with scenario-specific logic.
    Implements simplified versions of the scenario rules.
    """
    scenario_name = _get_scenario_name(task_type)

    if log_callback:
        await log_callback("info", "system", f"规则引擎审计开始 | 场景: {scenario_name}")

    filler_words = set(params.filler_words) if params.filler_words else {
        "嗯", "啊", "那个", "然后", "就是说", "就是", "这个"
    }
    retake_threshold = params.retake_char_threshold
    min_duration = params.min_segment_duration

    # Scenario-specific overrides
    if task_type == "highlight_reel":
        min_duration = 3.0       # Highlights need at least 3s
        retake_threshold = 3     # More aggressive retake detection
    elif task_type == "interview_compress":
        min_duration = 1.0       # Interviews can have shorter segments
    elif task_type == "monologue_clean":
        min_duration = params.min_segment_duration  # Use configured value

    # 使用 model_copy(deep=True) 创建副本，避免原地修改导致历史快照被污染
    result_segments = [seg.model_copy(deep=True) for seg in segments]

    for i, seg in enumerate(result_segments):
        duration = seg.end - seg.start

        # ── Rule 2 / P4 / I-: Fragment Rule ──────────────────────────────────
        if params.rules_enabled.get("fragment", True):
            if duration < min_duration:
                word_count = len(seg.text.replace(" ", ""))
                # Protect short but meaningful segments
                is_meaningful_short = any(
                    keyword in seg.text for keyword in ["好的", "对", "没错", "是的", "确实"]
                )
                if word_count < 4 and not is_meaningful_short:
                    seg.action = SegmentAction.DELETE
                    seg.reason = f"片段过短 ({duration:.1f}s < {min_duration}s)，无法独立成义"
                    seg.rule = AuditRule.FRAGMENT
                    continue

        # ── Rule 3 / P3: Filler Removal ──────────────────────────────────────
        if params.rules_enabled.get("filler", True):
            clean_text = seg.text.strip()
            filler_candidates = [
                edit for edit in (seg.candidate_edits or [])
                if edit.source == "filler" and edit.edit_type == "delete_range"
            ]
            if filler_candidates and clean_text in filler_words:
                best = max(filler_candidates, key=lambda edit: edit.end - edit.start)
                seg.action = SegmentAction.DELETE
                seg.reason = f"纯语气词候选，按精确边界删除: {clean_text}"
                seg.rule = AuditRule.FILLER
                seg.start = round(best.start, 3)
                seg.end = round(best.end, 3)
                for edit in seg.candidate_edits:
                    if edit.id == best.id:
                        edit.status = "accepted"
                continue
            # For highlight reel, be more aggressive with fillers
            if task_type == "highlight_reel":
                if any(fw in clean_text for fw in filler_words) and len(clean_text) < 8:
                    seg.action = SegmentAction.DELETE
                    seg.reason = f"集锦模式：含语气词且内容短，删除: {clean_text[:20]}"
                    seg.rule = AuditRule.FILLER
                    continue
            else:
                if clean_text in filler_words or all(w in filler_words for w in clean_text.split()):
                    seg.action = SegmentAction.DELETE
                    seg.reason = f"纯语气词片段: {clean_text}"
                    seg.rule = AuditRule.FILLER
                    continue

        # ── Rule 1 / P1: Retake Rule ──────────────────────────────────────────
        if params.rules_enabled.get("retake", True) and i > 0:
            prev_seg = result_segments[i - 1]
            if prev_seg.action == SegmentAction.KEEP:
                curr_start = seg.text[:retake_threshold]
                prev_start = prev_seg.text[:retake_threshold]
                if (len(curr_start) >= retake_threshold and
                        curr_start == prev_start and
                        len(seg.text) >= len(prev_seg.text)):
                    prev_seg.action = SegmentAction.DELETE
                    prev_seg.reason = f"重说：被后句替代（开头相同 {retake_threshold} 字）"
                    prev_seg.rule = AuditRule.RETAKE

        # ── Rule 7 / I1: Q&A Integrity (Interview only) ───────────────────────
        # In rule-based mode, we protect all segments in interview mode
        # (Claude handles the actual Q&A pairing logic)
        if task_type == "interview_compress":
            # Protect segments from spk1 (guest) more aggressively
            if seg.speaker == "spk1" and seg.action == SegmentAction.KEEP:
                # Guest segments: only delete if very short AND pure filler
                if duration < 0.8:
                    seg.action = SegmentAction.DELETE
                    seg.reason = f"嘉宾片段过短 ({duration:.1f}s)"
                    seg.rule = AuditRule.FRAGMENT

        # ── Rule 8 / P6 / I7: Breath Padding ─────────────────────────────────
        if params.rules_enabled.get("pacing", True) and seg.action == SegmentAction.KEEP:
            if task_type == "highlight_reel":
                # Quick cut: minimal breath
                seg.start = max(0, seg.start - 0.08)
                seg.end = seg.end + 0.05
            elif task_type == "interview_compress":
                # Interview: more generous breath
                seg.start = max(0, seg.start - 0.20)
                seg.end = seg.end + 0.15
            else:
                # Monologue: standard breath
                seg.start = max(0, seg.start - 0.15)
                seg.end = seg.end + 0.10

    # ── Post-processing: Live commerce reel special logic ─────────────────────
    if task_type == "highlight_reel":
        # Mark the longest kept segment as a potential highlight
        kept_segs = [s for s in result_segments if s.action == SegmentAction.KEEP]
        if kept_segs:
            longest = max(kept_segs, key=lambda s: s.end - s.start)
            longest.style = "highlight"
            longest.reason = (longest.reason or "") + " [规则引擎标注：最长保留片段，建议人工复核是否为核心产品卖点]"

    # 冻结规则引擎原始决策
    for seg in result_segments:
        seg.claude_action = seg.action
        seg.claude_reason = seg.reason

    kept = sum(1 for s in result_segments if s.action == SegmentAction.KEEP)
    deleted = sum(1 for s in result_segments if s.action == SegmentAction.DELETE)
    kept_duration = sum(s.end - s.start for s in result_segments if s.action == SegmentAction.KEEP)
    total_duration = sum(s.end - s.start for s in segments)
    retention_rate = kept_duration / total_duration * 100 if total_duration > 0 else 0

    if log_callback:
        await log_callback(
            "success", "system",
            f"规则引擎审计完成 | 保留 {kept} 片段，删除 {deleted} 片段 | "
            f"保留时长 {kept_duration:.1f}s ({retention_rate:.0f}%)"
        )

    return result_segments
