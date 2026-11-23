"""产品场次识别：优先消费句子级 product_semantic，始终提供确定性兜底。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from collections import Counter
from typing import Iterable, List, Optional

from ..models.task import ProductGroup, ProductOccurrence, Segment, SegmentAction


DEFAULT_MAX_GAP_SECONDS = 60.0
_KEPT_ACTIONS = {
    SegmentAction.KEEP,
    SegmentAction.SUBTITLE_FIX,
    SegmentAction.TEXT_FIX,
    SegmentAction.MERGE_NEXT,
}
_SUPPORTING_SURFACE_RE = re.compile(
    r"(?:app|应用|小程序|公众号|观看平台|内容平台|会员|账号)$",
    re.IGNORECASE,
)
_GENERIC_PRODUCT_ALIAS_RE = re.compile(
    r"^(?:(?:儿童|青少年|少儿|亲子|学生|教育|科普|历史|经济学|传统文化|"
    r"青春期|校园|故事|拼音|桥梁|法律|军事|心理|性教育|物理|科学|"
    r"文学|一次性|速干|中外|中国|世界|dk)[/、·-]?)*"
    r"(?:产品|商品|书|书籍|图书|童书|读物|绘本|故事书|拼音书|桥梁书|"
    r"科普书|百科|科普百科|动画|动画片|科普动画|app|应用|平台|"
    r"服装|衣服|鞋|儿童鞋|防晒|防晒霜|精华|护肤品|玩具|科普玩具|"
    r"内衣|内衣袜|内裤|袜子|赠品|满赠)$",
    re.IGNORECASE,
)
_GENERIC_PRODUCT_ALIAS_EXACT = {
    "dk童书满赠",
    "dk童书赠品",
    "dk百科系列",
}


def _clean_name(value: Optional[str]) -> str:
    cleaned = re.sub(r"\s+", "", (value or "").strip()).casefold()
    return cleaned.strip("《》「」『』“”\"'")


def _identity_name(value: Optional[str]) -> str:
    """仅修正常见、可确认的 ASR 同音命名，不把品类词当产品身份。"""
    name = _family_name(value)
    aliases = {
        "博物馆系列书": "中国五大科学博物馆绘本",
        "中外历史对比书": "中外文明对照机关书",
    }
    normalized = name.replace("无有用", "吴有用").replace("米无", "米吴")
    return aliases.get(normalized, normalized)


def _is_generic_product_alias(value: Optional[str]) -> bool:
    name = _clean_name(value)
    if not name:
        return True
    if name in _GENERIC_PRODUCT_ALIAS_EXACT:
        return True
    parts = [part for part in re.split(r"[/、]+", name) if part]
    return bool(parts) and all(
        part in _GENERIC_PRODUCT_ALIAS_EXACT
        or bool(_GENERIC_PRODUCT_ALIAS_RE.fullmatch(part))
        for part in parts
    )


def _family_name(value: Optional[str]) -> str:
    """去除版本/套装修饰，避免同一产品被模型的细粒度命名切碎。"""
    name = _clean_name(value)
    name = re.sub(
        r"(第?[一二三四五六七八九十0-9]+(?:至|到|-)?[一二三四五六七八九十0-9]*季|"
        r"[一二三四五六七八九十0-9]+本套装|全集|套装|首发|新版|升级版|"
        r"[-—·（(]?(?:露营安全|数学主题|内容定位与系列背景)[）)]?)",
        "",
        name,
    )
    return name.strip("-—·（）()")


def _same_product_family(left: str, right: str) -> bool:
    left_family = _family_name(left)
    right_family = _family_name(right)
    if not left_family or not right_family:
        return False
    if left_family == right_family or left_family in right_family or right_family in left_family:
        return True
    left_discriminator = re.search(r"([a-z0-9]+)$", left_family)
    right_discriminator = re.search(r"([a-z0-9]+)$", right_family)
    if (
        left_discriminator
        and right_discriminator
        and left_discriminator.group(1) != right_discriminator.group(1)
    ):
        # “产品A/产品B”、型号 15/16 等尾部标识代表不同产品，不能被高字符相似度误合并。
        return False
    # 语义审计常在“动画片/科普动画”等近义命名间摆动；字符相似度用于合并这些别名，
    # 但阈值保持偏高，避免把同品牌下完全不同的产品误合并。
    return SequenceMatcher(None, left_family, right_family).ratio() >= 0.62


def _same_global_product(left: str, right: str) -> bool:
    """跨场次只比较强产品名，禁止宽泛品类通过别名链传递合并。"""
    left_family = _identity_name(left)
    right_family = _identity_name(right)
    if not left_family or not right_family:
        return False
    if left_family == right_family:
        return True
    left_discriminator = re.search(r"([a-z0-9]+)$", left_family)
    right_discriminator = re.search(r"([a-z0-9]+)$", right_family)
    if (
        left_discriminator
        and right_discriminator
        and left_discriminator.group(1) != right_discriminator.group(1)
    ):
        return False
    shorter, longer = sorted((left_family, right_family), key=len)
    similarity = SequenceMatcher(None, left_family, right_family).ratio()
    if (
        (
            len(shorter) >= 3
            or (
                len(shorter) >= 2
                and bool(re.fullmatch(r"[\u4e00-\u9fff]+", shorter))
                and longer.startswith(shorter)
            )
        )
        and shorter in longer
        and similarity >= 0.5
        and not _is_generic_product_alias(shorter)
    ):
        return True
    matcher = SequenceMatcher(None, left_family, right_family)
    longest_match = matcher.find_longest_match(
        0,
        len(left_family),
        0,
        len(right_family),
    ).size
    return (
        similarity >= 0.72
        and (
            longest_match >= max(3, round(min(len(left_family), len(right_family)) * 0.55))
            or (
                left_family[:2] == right_family[:2]
                and bool(re.fullmatch(r"[\u4e00-\u9fff]{2}", left_family[:2]))
            )
        )
    )


def _strong_occurrence_names(occurrence: ProductOccurrence) -> List[str]:
    names = [] if _is_generic_product_alias(occurrence.name) else [occurrence.name]
    names.extend(
        alias
        for alias in occurrence.aliases
        if not _is_generic_product_alias(alias)
    )
    return list(dict.fromkeys(name for name in names if _clean_name(name)))


def _is_nested_supporting_occurrence(
    left: ProductOccurrence,
    right: ProductOccurrence,
) -> bool:
    """把产品介绍中被误提为实体的承载 APP/平台收回主产品组。"""
    left_ids = set(left.segment_ids)
    right_ids = set(right.segment_ids)
    shared_count = len(left_ids & right_ids)
    smaller_scope = min(len(left_ids), len(right_ids))
    if shared_count < 2 or not smaller_scope or shared_count / smaller_scope < 0.5:
        return False
    names = [
        left.name,
        *left.aliases,
        right.name,
        *right.aliases,
    ]
    return any(_SUPPORTING_SURFACE_RE.search(_clean_name(name)) for name in names)


def _segment_product(segment: Segment) -> tuple[Optional[str], List[str], Optional[float]]:
    mark = segment.product_semantic
    if not mark or not mark.eligible_for_highlight or not mark.product_entities:
        return None, [], None
    entity = mark.product_entities[0]
    name = (entity.canonical_name or entity.name or entity.category or "").strip()
    aliases = [
        value.strip()
        for value in (entity.name, entity.canonical_name)
        if value and value.strip()
    ]
    if not aliases and entity.category and entity.category.strip():
        aliases.append(entity.category.strip())
    return name or None, list(dict.fromkeys(aliases)), mark.confidence


def _stable_occurrence_id(name: str, segment_ids: Iterable[str]) -> str:
    ids = list(segment_ids)
    raw = f"{_clean_name(name)}|{'|'.join(ids)}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", name.strip()).strip("-")[:18] or "product"
    return f"occ_{slug}_{digest}"


@dataclass
class _OccurrenceDraft:
    name: str
    aliases: List[str] = field(default_factory=list)
    segments: List[Segment] = field(default_factory=list)
    pending_gap: List[Segment] = field(default_factory=list)
    confidences: List[float] = field(default_factory=list)
    last_mark_end: float = 0.0

    def add_mark(self, segment: Segment, aliases: List[str], confidence: Optional[float]) -> None:
        self.segments.extend(self.pending_gap)
        self.pending_gap = []
        self.segments.append(segment)
        self.aliases = list(dict.fromkeys(self.aliases + aliases + [self.name]))
        if confidence is not None:
            self.confidences.append(confidence)
        self.last_mark_end = segment.end

    def to_model(self) -> ProductOccurrence:
        ordered = sorted({segment.id: segment for segment in self.segments}.values(), key=lambda item: item.start)
        ids = [segment.id for segment in ordered]
        evidence = [
            (segment.display_text or segment.text or "").strip()[:120]
            for segment in ordered
            if (segment.display_text or segment.text or "").strip()
        ][:5]
        confidence = sum(self.confidences) / len(self.confidences) if self.confidences else 0.75
        return ProductOccurrence(
            occurrence_id=_stable_occurrence_id(self.name, ids),
            name=self.name,
            aliases=list(dict.fromkeys(self.aliases + [self.name])),
            start=ordered[0].start if ordered else 0.0,
            end=ordered[-1].end if ordered else 0.0,
            segment_ids=ids,
            evidence=evidence,
            confidence=round(confidence, 3),
            source="product_semantic",
        )


def _semantic_occurrences(
    segments: List[Segment],
    *,
    max_gap_seconds: float,
) -> List[ProductOccurrence]:
    occurrences: List[ProductOccurrence] = []
    current: Optional[_OccurrenceDraft] = None

    for segment in segments:
        name, aliases, confidence = _segment_product(segment)
        if name:
            if (
                current
                and _same_product_family(name, current.name)
                and segment.start - current.last_mark_end <= max_gap_seconds
            ):
                current.add_mark(segment, aliases, confidence)
                continue
            if current:
                occurrences.append(current.to_model())
            current = _OccurrenceDraft(name=name)
            current.add_mark(segment, aliases, confidence)
            continue

        if current and segment.start - current.last_mark_end <= max_gap_seconds:
            current.pending_gap.append(segment)

    if current:
        # 尾部短上下文属于当前产品；遇到下一产品时 pending_gap 不会跨产品合并。
        current.segments.extend(current.pending_gap)
        occurrences.append(current.to_model())
    return [occurrence for occurrence in occurrences if occurrence.segment_ids]


def _materialize_occurrence_ranges(
    occurrences: List[ProductOccurrence],
    kept_segments: List[Segment],
) -> List[ProductOccurrence]:
    """把锚点场次扩展为连续 scope，纳入锚点之间未重复标产品名的正文。"""
    materialized: List[ProductOccurrence] = []
    for occurrence in occurrences:
        in_scope = [
            segment
            for segment in kept_segments
            if segment.end > occurrence.start and segment.start < occurrence.end
        ]
        if not in_scope:
            continue
        segment_ids = [segment.id for segment in in_scope]
        materialized.append(
            occurrence.model_copy(
                deep=True,
                update={
                    "occurrence_id": _stable_occurrence_id(occurrence.name, segment_ids),
                    "start": in_scope[0].start,
                    "end": in_scope[-1].end,
                    "segment_ids": segment_ids,
                },
            )
        )
    return materialized


def _legacy_occurrences(
    segments: List[Segment],
    *,
    max_gap_seconds: float,
) -> List[ProductOccurrence]:
    """旧任务无 product_semantic 时按连续 clip_group/时间块提供可用场次。"""
    groups: List[List[Segment]] = []
    current: List[Segment] = []
    current_group: Optional[int] = None
    for segment in segments:
        split = bool(
            current
            and (
                segment.start - current[-1].end > max_gap_seconds
                or (
                    segment.clip_group is not None
                    and current_group is not None
                    and segment.clip_group != current_group
                )
            )
        )
        if split:
            groups.append(current)
            current = []
        current.append(segment)
        if segment.clip_group is not None:
            current_group = segment.clip_group
    if current:
        groups.append(current)

    occurrences: List[ProductOccurrence] = []
    for index, group in enumerate(groups, start=1):
        title_segment = next((segment for segment in group if segment.clip_title), None)
        name = (title_segment.clip_title if title_segment else None) or f"产品场次 {index}"
        ids = [segment.id for segment in group]
        occurrences.append(
            ProductOccurrence(
                occurrence_id=_stable_occurrence_id(name, ids),
                name=name,
                aliases=[name],
                start=group[0].start,
                end=group[-1].end,
                segment_ids=ids,
                evidence=[(segment.display_text or segment.text or "").strip()[:120] for segment in group[:5]],
                confidence=0.5,
                source="legacy_fallback",
            )
        )
    return occurrences


def identify_product_occurrences(
    segments: List[Segment],
    *,
    max_gap_seconds: float = DEFAULT_MAX_GAP_SECONDS,
) -> List[ProductOccurrence]:
    """识别产品连续场次；A→B→A 必然得到三个 occurrence。"""
    kept = sorted(
        [segment for segment in segments if segment.action in _KEPT_ACTIONS],
        key=lambda item: (item.start, item.end, item.id),
    )
    if not kept:
        return []
    semantic = _semantic_occurrences(kept, max_gap_seconds=max_gap_seconds)
    if semantic:
        return _materialize_occurrence_ranges(semantic, kept)
    return _legacy_occurrences(kept, max_gap_seconds=max_gap_seconds)


def _stable_product_group_id(canonical_name: str, occurrence_ids: Iterable[str]) -> str:
    raw = f"{_family_name(canonical_name)}|{'|'.join(sorted(occurrence_ids))}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", canonical_name.strip()).strip("-")[:18] or "product"
    return f"product_{slug}_{digest}"


def group_product_occurrences(
    occurrences: List[ProductOccurrence],
    segments: List[Segment],
) -> List[ProductGroup]:
    """把跨时间返场的同一产品归为一个出片单位，不吸收中间其他产品片段。"""
    if not occurrences:
        return []
    segment_map = {segment.id: segment for segment in segments}
    drafts: List[List[ProductOccurrence]] = []
    aliases_by_draft: List[List[str]] = []

    for occurrence in sorted(
        occurrences,
        key=lambda item: (item.start, item.end, item.occurrence_id),
    ):
        names = _strong_occurrence_names(occurrence)
        matched_index = next(
            (
                index
                for index, (aliases, grouped_occurrences) in enumerate(
                    zip(aliases_by_draft, drafts)
                )
                if (
                    any(
                        _same_global_product(candidate, existing)
                        for candidate in names
                        for existing in aliases
                    )
                    or any(
                        _is_nested_supporting_occurrence(occurrence, existing)
                        for existing in grouped_occurrences
                    )
                )
            ),
            None,
        )
        if matched_index is None:
            drafts.append([occurrence])
            aliases_by_draft.append(names)
        else:
            drafts[matched_index].append(occurrence)
            aliases_by_draft[matched_index] = list(
                dict.fromkeys(aliases_by_draft[matched_index] + names)
            )

    groups: List[ProductGroup] = []
    for grouped_occurrences, aliases in zip(drafts, aliases_by_draft):
        name_counts = Counter(occurrence.name for occurrence in grouped_occurrences)
        canonical_name = sorted(
            name_counts,
            key=lambda name: (
                -name_counts[name],
                min(
                    occurrence.start
                    for occurrence in grouped_occurrences
                    if occurrence.name == name
                ),
                len(name),
                name,
            ),
        )[0]
        occurrence_ids = [occurrence.occurrence_id for occurrence in grouped_occurrences]
        segment_ids = sorted(
            {
                segment_id
                for occurrence in grouped_occurrences
                for segment_id in occurrence.segment_ids
                if segment_id in segment_map
            },
            key=lambda segment_id: (
                segment_map[segment_id].start,
                segment_map[segment_id].end,
                segment_id,
            ),
        )
        confidences = [
            occurrence.confidence
            for occurrence in grouped_occurrences
            if occurrence.confidence is not None
        ]
        evidence = list(
            dict.fromkeys(
                item
                for occurrence in grouped_occurrences
                for item in occurrence.evidence
                if item
            )
        )[:10]
        groups.append(
            ProductGroup(
                product_group_id=_stable_product_group_id(canonical_name, occurrence_ids),
                canonical_name=canonical_name,
                aliases=list(dict.fromkeys(aliases + [canonical_name])),
                occurrence_ids=occurrence_ids,
                segment_ids=segment_ids,
                start=min(occurrence.start for occurrence in grouped_occurrences),
                end=max(occurrence.end for occurrence in grouped_occurrences),
                confidence=(
                    round(sum(confidences) / len(confidences), 3)
                    if confidences
                    else None
                ),
                evidence=evidence,
            )
        )
    return sorted(groups, key=lambda item: (item.start, item.end, item.product_group_id))
