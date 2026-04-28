"""
GoldenClip SemanticAuditor
Core AI class that applies scenario-specific rules via Claude API.

Design: 暗金剪辑台 · 编导美学
Flow: Tagged Script → Claude (Scenario Prompt) → JSON Audit Instructions

Scenarios:
  - monologue_clean:     口播精修 (Prompt P1-P8)
  - interview_compress:  直播访谈压缩 (Prompt I1-I8)
  - highlight_reel:      精彩集锦 (Prompt H1-H5)
"""

import json
import os
import re
from typing import Dict, List, Optional, Callable, Tuple  # noqa: F401
from pathlib import Path

from ..models.task import Segment, SegmentAction, AuditRule, TaskParams, TaskType

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

# Scenario → Prompt file mapping
SCENARIO_PROMPT_MAP = {
    TaskType.MONOLOGUE_CLEAN: PROMPTS_DIR / "monologue_clean.md",
    TaskType.INTERVIEW_COMPRESS: PROMPTS_DIR / "interview_compress.md",
    TaskType.HIGHLIGHT_REEL: PROMPTS_DIR / "highlight_reel.md",
}

# Fallback generic prompt
GENERIC_PROMPT_PATH = PROMPTS_DIR / "semantic_audit.md"


def load_system_prompt(task_type: Optional[str] = None) -> str:
    """
    Load the scenario-specific SemanticAuditor system prompt.
    Falls back to generic prompt if scenario-specific one is not found.
    """
    # Try to match task_type to a scenario
    if task_type:
        for t, path in SCENARIO_PROMPT_MAP.items():
            if t.value == task_type or t == task_type:
                if path.exists():
                    return path.read_text(encoding="utf-8")

    # Fallback to generic
    if GENERIC_PROMPT_PATH.exists():
        return GENERIC_PROMPT_PATH.read_text(encoding="utf-8")

    return _get_default_system_prompt()


def _get_default_system_prompt() -> str:
    return """你是一名拥有十年经验的高级视频编导，专门负责长访谈与口播视频的精修。
    应用8条语义审计规则，将带标记的原始脚本转化为剪辑指令集。
    直接输出JSON数组，不包含任何其他内容。"""


def build_sentence_groups(
    segments: List[Segment],
    sentence_threshold: float = 1.5,
    id_to_idx: Optional[Dict[str, int]] = None,
) -> str:
    """
    将片段按句间停顿合并为"句子组"，注入到 Prompt 中辅助 Claude 做重复句检测。

    id_to_idx: 若提供，使用数字索引替代 UUID 以节省 token。
    """
    if not segments:
        return ""

    sentences = []
    current_labels: List[str] = []
    current_text_parts: List[str] = []

    for i, seg in enumerate(segments):
        label = str(id_to_idx[seg.id]) if id_to_idx else seg.id
        current_labels.append(label)
        current_text_parts.append(seg.text)

        is_last = (i == len(segments) - 1)
        if not is_last:
            gap = segments[i + 1].start - seg.end
            has_boundary = gap >= sentence_threshold
        else:
            has_boundary = True

        if has_boundary and current_labels:
            sentence_text = "".join(current_text_parts).strip()
            if sentence_text:
                id_range = current_labels[0] if len(current_labels) == 1 else f"{current_labels[0]}~{current_labels[-1]}"
                sentences.append(f"句{len(sentences)} | {id_range} | {sentence_text}")
            current_labels = []
            current_text_parts = []

    if not sentences:
        return ""

    lines = "\n".join(sentences)
    return f"""## 分句结构（重复句检测用）

> 片段间隔≥{sentence_threshold}s 为句子边界。检测重说规则时，比对相邻句开头是否≥5字相同。

```
{lines}
```

"""


def build_audit_prompt(
    segments: List[Segment],
    style_mode: str = "immersive",
    task_type: Optional[str] = None,
    target_duration_ratio: Optional[float] = None,
) -> Tuple[str, Dict[int, str]]:
    """
    构建发送给 Claude 的用户 Prompt（压缩版）。

    压缩策略：
    - 字段缩写：i=片段序号, t=台词, s=说话人, tt=标注台词(仅口播/集锦)
    - 删除 start/end/duration（Claude 做语义决策不需要时间戳）
    - 访谈模式删除 tagged_text（I7 铁律：不做词级切除，<STU>/<FIL> 标签无用）
    - 输出缩写：a=action, k=keep, d=delete, r=reason(仅 delete 时必填)
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

    # 构建压缩片段列表（单行 JSON，不格式化缩进）
    segments_lines = []
    for i, seg in enumerate(segments):
        entry: dict = {"i": i + 1, "t": seg.text, "s": seg.speaker or "spk0"}
        if include_tagged and seg.tagged_text and seg.tagged_text != seg.text:
            entry["tt"] = seg.tagged_text
        segments_lines.append(json.dumps(entry, ensure_ascii=False))
    segments_compact = "[\n" + ",\n".join(segments_lines) + "\n]"

    scenario_context = _build_scenario_context(task_type, style_mode, target_duration_ratio, segments)
    total_duration = sum(s.end - s.start for s in segments)
    speakers = list(set(seg.speaker or "spk0" for seg in segments))

    sentence_block = ""
    if task_type in ("monologue_clean", "interview_compress"):
        sentence_block = build_sentence_groups(segments, sentence_threshold=1.5, id_to_idx=id_to_idx)

    # 缩写说明（放在 Prompt 开头，声明一次即可）
    tagged_hint = " tt=标注台词(<STU>结巴/<FIL>语气词)" if include_tagged else ""
    abbrev_legend = (
        f"## 字段缩写\n"
        f"输入: i=片段序号 t=台词 s=说话人(spk0主播/spk1嘉宾){tagged_hint}\n"
        f"输出: a=动作 k=keep d=delete r=原因(仅d时必填)\n"
        f"特殊动作缩写: sf=subtitle_fix tf=text_fix mn=merge_next sp=split\n\n"
    )

    if task_type == "highlight_reel":
        keep_schema = '{"i":序号,"a":"k","clip_group":整数分组号,"clip_title":"每组首段填写标题，其他保留段为null","r":"可选保留原因"}'
        delete_schema = '{"i":序号,"a":"d","clip_group":null,"clip_title":null,"r":"规则编号+简短原因"}'
    else:
        keep_schema = '{"i":序号,"a":"k"}'
        delete_schema = '{"i":序号,"a":"d","r":"规则编号+简短原因"}'

    prompt = f"""{abbrev_legend}## 任务配置
- 场景: {_get_scenario_name(task_type)} | 风格: {style_mode}
- 片段: {len(segments)}个 | 时长: {total_duration:.0f}秒({total_duration/60:.1f}分钟)
- 说话人: {', '.join(speakers)}

{scenario_context}

{sentence_block}## 待审计片段

```json
{segments_compact}
```

请严格按场景规则审计所有片段，输出JSON数组（每片段必须有结果）。
keep片段: {keep_schema}
delete片段: {delete_schema}
**仅输出JSON数组，不要任何解释文字或markdown标记。**"""

    return prompt, idx_to_id


def _get_scenario_name(task_type: Optional[str]) -> str:
    names = {
        "monologue_clean": "口播精修 (Monologue Clean)",
        "interview_compress": "直播访谈压缩 (Live Interview Compress)",
        "highlight_reel": "精彩集锦 (Highlight Reel)",
    }
    return names.get(task_type, "通用审计")


def _build_scenario_context(
    task_type: Optional[str],
    style_mode: str,
    target_duration_ratio: Optional[float],
    segments: List[Segment],
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

        # Count speakers
        speakers = {}
        for s in segments:
            spk = s.speaker or "spk0"
            speakers[spk] = speakers.get(spk, 0) + (s.end - s.start)

        speaker_info = "\n".join([
            f"  - {spk}: {dur:.0f}秒 ({'主播' if i == 0 else '嘉宾'})"
            for i, (spk, dur) in enumerate(speakers.items())
        ])

        return f"""## 场景参数
- 目标时长: {target_dur:.0f}秒 ({target_dur/60:.1f}分钟，原始的{target_ratio*100:.0f}%)
- 嘉宾保留率: 60-70%（spk1片段）
- 主播保留率: 20-30%（spk0片段）
- 说话人时长分布:
{speaker_info}
- 重点规则: I1(Q&A闭环) > I3(精华保护) > I2(跨段去重) > I4(主播精简)
- 气口预留: 开头200ms / 结尾150ms"""

    elif task_type == "highlight_reel":
        target_dur = min(180, total_duration * 0.15)

        return f"""## 场景参数
- 目标形态: 1剪多，从长视频中拆出多个独立可发布短视频
- clip_group 数量目标: 5-10 个
- 单个 clip 建议时长: 20-90秒
- 目标保留率: 10-25%（只保留高能时刻）
- 重点规则: H1(高能识别) > H2(残忍删除) > H3(独立成片) > H4(分组质量)
- 气口预留: 开头80ms / 结尾50ms（快节奏）
- 特别要求: keep 片段必须填写 clip_group；每组首段必须填写 clip_title；delete 片段两字段必须为 null"""

    return ""


def parse_claude_response(
    response_text: str,
    original_segments: List[Segment],
    idx_to_id: Optional[Dict[int, str]] = None,
) -> List[Segment]:
    """
    解析 Claude 的 JSON 输出，将审计结果映射回 Segment 列表。

    同时支持：
    - 新版压缩格式：{i(序号), a(action缩写), r(reason)}
    - 旧版完整格式：{id, action, reason, rule, style, ...}

    动作缩写映射：k=keep, d=delete, sf=subtitle_fix, tf=text_fix, mn=merge_next, sp=split

    idx_to_id: 1-based整数索引 → 原始UUID，由 build_audit_prompt 返回。
    解析失败时静默回退到原始 segments（全部 KEEP）。
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
                return original_segments
        else:
            return original_segments

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
        updated_seg.display_text = None
        updated_seg.fix_type = None
        updated_seg.split_before = None
        updated_seg.split_part1_action = SegmentAction.KEEP
        updated_seg.split_part2_action = SegmentAction.KEEP
        updated_seg.subtitle_line_break = None
        updated_seg.merged = False
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

            rule_str = result.get("rule", "")
            if rule_str:
                for rule in AuditRule:
                    if rule.value in rule_str or rule_str in rule.value:
                        updated_seg.rule = rule
                        break

        updated_segments.append(updated_seg)

    return updated_segments


def apply_segment_corrections(segments: List[Segment]) -> List[Segment]:
    """
    后处理器：处理 split / merge_next 动作，重整段落边界。

    split 流程：
    1. 在 seg.words 中定位 split_before 文本首字符的 start 时间
    2. 将原段切为 part1（头部）和 part2（尾部）
    3. part1 为空（split_before 在段首）则只输出 part2
    4. 用修正后的段列表替换原段位置（碎段合并由 Step 3 负责）

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

    result: List[Segment] = []
    skip_ids: set = set()  # 已被合并的下游段 id，跳过不再重复添加

    for i, seg in enumerate(segments):
        if seg.id in skip_ids:
            continue

        # ── merge_next 处理 ────────────────────────────────────────────
        if seg.action == SegmentAction.MERGE_NEXT:
            # 跳过已删除的段，找到真正的下一个非删除段
            j = i + 1
            while j < len(segments) and segments[j].action == SegmentAction.DELETE:
                j += 1
            if j < len(segments):
                next_seg = segments[j]
                # 合并：延伸时间边界、拼接文本和 words
                merged_text = (seg.text or "") + (next_seg.text or "")
                merged_tagged = (seg.tagged_text or "") + (next_seg.tagged_text or "")
                merged_words = list(seg.words) + list(next_seg.words)
                seg.end = round(next_seg.end, 3)
                seg.text = merged_text.strip()
                seg.tagged_text = merged_tagged.strip()
                seg.words = merged_words
                seg.action = SegmentAction.KEEP
                seg.merged = True
                seg.reason = (seg.reason or "") + f" [已与 {next_seg.id} 合并为一张字幕卡]"
                skip_ids.add(next_seg.id)
            else:
                # 无非删除的后续段，降级为 keep
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
        def _make_seg(sid, start, end, words_list, action):
            text = "".join(
                _re.sub(r'<[^>]*>', '', w.word)
                for w in words_list
            ).strip() if words_list else ""
            tagged = "".join(w.word for w in words_list).strip() if words_list else ""
            return Segment(
                id=sid,
                start=round(start, 3),
                end=round(end, 3),
                text=text,
                tagged_text=tagged,
                speaker=seg.speaker,
                action=action,
                reason=seg.reason,
                rule=seg.rule,
                style=seg.style,
                words=words_list,
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
        )
        part2_end = seg.end
        part2_words = words_part2

        part2 = _make_seg(
            seg.id + "_p2",
            split_time,
            part2_end,
            part2_words,
            seg.split_part2_action,
        )

        # ── 4. 写入结果 ───────────────────────────────────────────────────
        if part1.text:  # part1 为空（split_before 在段首）则跳过
            result.append(part1)
        result.append(part2)

    return result


def _words_substance_chars(seg: "Segment") -> int:
    """
    通过 seg.words 统计段落中非 STU/FIL 标签的实质字符数。
    tagged_text 是词级 token 拼接（无空格分隔），字符串解析不可靠；
    words 列表每个 token 独立，可精确判断。
    """
    count = 0
    for w in (seg.words or []):
        token = w.word
        if token.startswith("<STU>") or token.startswith("<FIL"):
            continue  # 噪声 token，跳过
        clean = re.sub(r'[，。！？、\s]', '', token)
        count += len(clean)
    return count


def _is_pure_noise(seg: "Segment") -> bool:
    """
    判断一段是否是纯结巴/语气词噪声（P3：整段无实质内容）。

    优先用 words 列表逐 token 判断（精确）；
    无 words 数据时 fallback 到 text 字段启发式（保守）。
    """
    tagged = seg.tagged_text or ""

    # 无任何标签 → 不是噪声
    if "<STU>" not in tagged and "<FIL>" not in tagged:
        return False

    # 方法一：words 精确判断
    if seg.words:
        substance = _words_substance_chars(seg)
        return substance <= 1  # ≤1 个实质字符 → 纯噪声

    # 方法二：fallback（无 words 数据）—— 以噪声标签开头 + 极短 text
    if tagged.startswith("<STU>") or tagged.startswith("<FIL"):
        plain = re.sub(r'[，。！？、\s]', '', seg.text)
        if not plain or len(plain) <= 3 or len(set(plain)) == 1:
            return True

    return False


# 合法的 AA 型叠词：不应被当作结巴去掉（与 asr_pipeline._REDUPLICATION_AA_WORDS 保持同步）
# 此处作为防御层，兜底处理已存储的旧数据（ASR 时尚未加入该集合的历史数据）
# 无效沟通词库（I0）：整段文字去标点后完全匹配 → 无条件删除，适用所有场景
_INVALID_COMM_PHRASES: frozenset = frozenset({
    # 信号/设备确认（连线特有，但其他场景录到也该删）
    "喂", "听得到吗", "能听到吗", "信号好吗", "有没有声音", "有声音吗",
    "能听见吗", "麦克风好吗", "声音正常吗",
    # 打招呼
    "你好", "您好", "hi", "hello", "哈喽", "嗨", "哈喽哈喽", "你好你好",
    # 连线/节目寒暄
    "好久不见", "感谢来连线", "欢迎欢迎", "很高兴认识你", "谢谢邀请",
    "感谢主播", "今天很高兴", "谢谢今天的分享", "感谢今天",
    # 收尾告别
    "拜拜", "再见", "好的谢谢", "谢谢", "好的好的谢谢",
    "好今天就到这里", "今天就到这里", "好的今天就到这里",
    "我们下次再见", "下次再见", "下期见",
    # P8 录播结束语（常见于独白/课程/连线结尾）
    "好了今天就录到这里", "今天就录到这里", "好了今天录到这里",
    "今天就到这里了", "好今天就到这里了", "就到这里了",
    "差不多了", "好了差不多了", "今天差不多就到这里",
    "我们今天就说到这里", "今天就说到这里", "就说到这里",
    "好今天的内容就是这些", "今天的内容就是这些", "内容就是这些",
    "好了今天的分享就到这里", "今天的分享就到这里",
    "好了今天的课就到这里", "今天的课就到这里",
    "好今天就讲到这里", "今天就讲到这里",
    "感谢大家收看", "感谢收看", "感谢观看", "谢谢大家收看",
    "感谢大家的支持", "谢谢大家的支持",
    "我们下次见", "咱们下次见",
    "好了拜拜", "好的拜拜", "那拜拜", "那再见",
})


def _is_invalid_comm(seg: "Segment") -> bool:
    """判断是否是无效沟通段（打招呼/再见/信号确认等）。"""
    plain = re.sub(r'[，。！？、…\s]', '', seg.text)
    return plain in _INVALID_COMM_PHRASES


_VALID_AA_WORDS: frozenset = frozenset({
    # 亲属称谓
    "妈妈", "爸爸", "哥哥", "姐姐", "弟弟", "妹妹",
    "奶奶", "爷爷", "宝宝", "叔叔", "婆婆", "娃娃", "姑姑", "舅舅", "嫂嫂",
    # 日常叠词
    "谢谢", "拜拜",
    "看看", "想想", "试试", "说说", "听听", "走走", "玩玩",
    "找找", "等等", "问问", "聊聊", "坐坐", "笑笑",
    "好好", "慢慢", "快快", "轻轻", "深深", "静静", "默默", "悄悄",
    "明明", "暖暖", "凉凉", "多多", "少少", "早早", "晚晚",
    # AABB 成语的 AA / BB 部分（防止拆掉成语）
    # 支支吾吾、磕磕绊绊、断断续续、扎扎实实、清清楚楚、认认真真
    "支支", "吾吾", "磕磕", "绊绊", "断断", "续续",
    "扎扎", "清清", "楚楚", "认认", "踏踏",
})


def _rule_subtitle_fix_stutter(
    segments: List[Segment],
    task_type: str = "monologue_clean",
) -> Tuple[List[Segment], int]:
    """
    Step 1b：规则化轻微结巴字幕去重（对应主 LLM P2 轻微档）。

    触发条件（同时满足）：
    - 段未被删除
    - words 列表中存在 <STU> token
    - 去掉 STU token 后仍有 > 1 个实质字符（有内容，否则由 Step 1a 删掉）
    - 仅 monologue_clean 场景（访谈 I7 不做词级切除）

    动作：去掉 STU 词后生成 display_text，action 改为 SUBTITLE_FIX，音频不变。
    保护：AA 型合法叠词（妈妈/哥哥/看看等）不去重。
    访谈模式同样处理（主 LLM 访谈只做 keep/delete，不会再做词级，这里必须处理）。
    """
    result = [s.model_copy(deep=True) for s in segments]
    fixed = 0

    for seg in result:
        if seg.action == SegmentAction.DELETE:
            continue
        if not seg.words:
            continue
        tagged = seg.tagged_text or ""
        if "<STU>" not in tagged:
            continue

        # 按 words 重建 display_text：去掉 STU token，保留其余（含 FIL 词内容）
        words = seg.words
        display_parts = []
        has_stu = False

        for idx, w in enumerate(words):
            token = w.word
            if token.startswith("<STU>"):
                stu_char = token[5:]  # 去掉 <STU> 前缀，得到实际字符

                # 保护：检查 STU字符 + 下一个非STU字符 是否构成合法叠词
                next_char = ""
                for nxt in words[idx + 1:]:
                    nc = nxt.word
                    if nc.startswith("<STU>"):
                        nc = nc[5:]
                    elif nc.startswith("<FIL"):
                        nc = re.sub(r'<FIL[^>]*>', '', nc)
                    next_char = nc[:1]  # 只取第一个字符比较
                    if next_char:
                        break

                if (stu_char + next_char) in _VALID_AA_WORDS:
                    # 合法叠词，保留此 STU 字符不去重
                    display_parts.append(stu_char)
                    continue

                has_stu = True
                # 跳过（去掉结巴词）
            elif token.startswith("<FIL"):
                clean = re.sub(r'<FIL[^>]*>', '', token)
                display_parts.append(clean)
            else:
                display_parts.append(token)

        if not has_stu:
            continue

        display = "".join(display_parts).strip()
        if not display or len(re.sub(r'[，。！？、\s]', '', display)) <= 1:
            continue
        if display == seg.text:
            continue
        # 如果去重后内容比原文少了超过 20%，很可能是 ASR 误标 STU → 跳过
        orig_plain = re.sub(r'[，。！？、\s]', '', seg.text)
        disp_plain = re.sub(r'[，。！？、\s]', '', display)
        if len(orig_plain) > 0 and len(disp_plain) / len(orig_plain) < 0.80:
            continue
        # 字符级检查：如果 seg.text 中某个字在 display 里完全消失 → ASR 误标 → 跳过
        # 正常结巴去重只是减少重复次数，不会让一个字从字幕里彻底消失
        char_missing = any(ch not in disp_plain for ch in orig_plain)
        if char_missing:
            continue

        seg.action = SegmentAction.SUBTITLE_FIX
        seg.display_text = display
        seg.fix_type = "stutter"
        seg.reason = "预处理 P2: 去除结巴词，字幕去重"
        fixed += 1

    return result, fixed


def _rule_delete_pure_noise(
    segments: List[Segment],
    task_type: str = "monologue_clean",
) -> Tuple[List[Segment], int]:
    """
    Step 1a：规则删除无效段，包含两类：
      A. 纯噪声段：整段只有 STU/FIL 标签，无实质内容（P3）
         - interview_compress：嘉宾（非 spk0）的纯语气词保留（体现真实思考过程）
         - 其他模式：一律删除
      B. 无效沟通段：整段文字完全匹配打招呼/再见/信号确认词库（I0）
         - 所有场景均删除
    """
    result = [s.model_copy(deep=True) for s in segments]
    deleted = 0
    for seg in result:
        if seg.action == SegmentAction.DELETE:
            continue

        # A. 无效沟通（所有场景）
        if _is_invalid_comm(seg):
            seg.action = SegmentAction.DELETE
            seg.reason = "预处理 I0: 无效沟通（打招呼/告别/信号确认）"
            deleted += 1
            continue

        # B. 纯噪声
        if not _is_pure_noise(seg):
            continue
        if task_type == "interview_compress" and seg.speaker != "spk0":
            continue
        seg.action = SegmentAction.DELETE
        seg.reason = "预处理 P3: 纯结巴/语气词段"
        deleted += 1

    return result, deleted


def _rule_subtitle_line_break(
    segments: List[Segment],
) -> Tuple[List[Segment], int]:
    """
    Step 4（可选）：超长字幕自动分行（对应主 LLM P12）。

    触发条件：
    - 段未被删除
    - 时长 > 6s 且文字数 > 40 字
    - 尚未设置 subtitle_line_break

    换行点选择：
    1. 找到文本正中间附近（±10字范围内）的第一个逗号/句号/连接词
    2. 从该点之后的词作为第二行起始文字
    """
    result = [s.model_copy(deep=True) for s in segments]
    fixed = 0
    # 常见连接词（1-2字），优先在这里断行；二字连接词检查 text[i:i+2]
    _BREAK_CONNECTIVES_1 = {'但'}
    _BREAK_CONNECTIVES_2 = {'所以', '然后', '因为', '而且', '不过', '其实', '就是', '那么', '虽然'}

    for seg in result:
        if seg.action == SegmentAction.DELETE:
            continue
        if seg.subtitle_line_break:
            continue
        dur = seg.end - seg.start
        text = seg.text or ""
        if dur <= 6.0 or len(text) <= 40:
            continue

        # 找换行点：在 [1/3, 2/3] 字符范围内找连接词或逗号
        n = len(text)
        lo, hi = n // 3, (n * 2) // 3
        best_pos = None

        # 优先找连接词（二字优先，再检查单字）
        for i in range(lo, hi):
            if text[i:i+2] in _BREAK_CONNECTIVES_2:
                best_pos = i
                break
            if text[i] in _BREAK_CONNECTIVES_1:
                best_pos = i
                break
        # 没有连接词则找逗号
        if best_pos is None:
            for i in range(lo, hi):
                if text[i] in '，,':
                    best_pos = i + 1  # 逗号之后开始新行
                    break
        # 最后 fallback：取中间点
        if best_pos is None:
            best_pos = n // 2

        break_word = text[best_pos:best_pos + 1]
        if not break_word:
            continue

        seg.subtitle_line_break = break_word
        fixed += 1

    return result, fixed


def _rule_merge_next_v2(segments: List[Segment]) -> Tuple[List[Segment], int]:
    """
    Step 3：规则化碎段合并，跳过已删除的段。

    触发条件（同时满足）：
    1. 相邻两个非删除段的物理间隔 < 0.5s
    2. 合并后总时长 ≤ 5s
    3. 同一说话人（或说话人未知）
    4. 两段 tagged_text 均不含 <STU> 或 <FIL>（避免把噪声锁进干净内容）
    """
    result = [s.model_copy(deep=True) for s in segments]
    merges = 0

    # 只在非删除段之间计算间隔
    active = [i for i, s in enumerate(result) if s.action != SegmentAction.DELETE]

    for idx in range(len(active) - 1):
        i = active[idx]
        j = active[idx + 1]
        seg_a = result[i]
        seg_b = result[j]

        gap = seg_b.start - seg_a.end
        combined_dur = seg_b.end - seg_a.start
        if gap >= 0.5 or combined_dur > 5.0:
            continue

        # 说话人一致检查
        if seg_a.speaker and seg_b.speaker and seg_a.speaker != seg_b.speaker:
            continue

        # 两段都是纯噪声（只有 STU/FIL，没有实质内容）才跳过合并
        ta = seg_a.tagged_text or seg_a.text or ""
        tb = seg_b.tagged_text or seg_b.text or ""
        def _is_pure_noise(t: str) -> bool:
            clean = re.sub(r'<[^>]+>', '', t).strip()
            return len(clean) < 3
        if _is_pure_noise(ta) and _is_pure_noise(tb):
            continue

        # 前段末尾是完整句（。！？）→ 不合并，说明是两个独立句子
        last_char = seg_a.text.strip()[-1] if seg_a.text.strip() else ""
        if last_char in "。！？":
            continue

        seg_a.action = SegmentAction.MERGE_NEXT
        merges += 1

    return result, merges


_SPLIT_BATCH_SIZE = 50  # 每批发给 LLM 的段数


async def _llm_split_batch(
    batch: List[tuple],  # [(orig_idx, seg), ...]
    system_prompt: str,
    ollama_model: str,
    ollama_base_url: str,
    batch_offset: int,
) -> Dict[int, dict]:
    """向 LLM 发送一批段落，返回 {seq_1based: result_dict} 仅含 sp 动作。"""
    import httpx

    lines = []
    for seq, (_, seg) in enumerate(batch):
        next_gap = ""
        if seq + 1 < len(batch):
            next_seg = batch[seq + 1][1]
            gap = next_seg.start - seg.end
            next_gap = f" [距下段{gap:.1f}s]"
        lines.append(f"{seq + 1}. [{seg.start:.2f}-{seg.end:.2f}]{next_gap} {seg.text}")

    n = len(batch)
    user_msg = (
        f"输入共 {n} 个片段。\n"
        f"规则：只识别断句错误（末尾引导词属于下一句）→ 输出 sp；其余一律输出 k。\n"
        f"必须输出恰好 {n} 个条目的 JSON 数组，不输出任何其他文字。\n\n"
        + "\n".join(lines)
    )

    try:
        async with httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(proxy=None),
            timeout=120.0,
        ) as client:
            resp = await client.post(
                f"{ollama_base_url}/v1/chat/completions",
                json={
                    "model": ollama_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    "temperature": 0.0,
                    "stream": False,
                },
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return {}

    # 提取 JSON 数组
    json_text = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
    json_text = re.sub(r'\s*```$', '', json_text, flags=re.MULTILINE).strip()
    try:
        items = json.loads(json_text)
        if not isinstance(items, list):
            return {}
    except Exception:
        m = re.search(r'\[.*\]', json_text, re.DOTALL)
        if not m:
            return {}
        try:
            items = json.loads(m.group())
        except Exception:
            return {}

    ACTION_EXPAND = {"k": "keep", "sp": "split", "mn": "merge_next", "d": "delete"}
    result = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        i = item.get("i")
        a_raw = str(item.get("a", "k"))
        action = ACTION_EXPAND.get(a_raw, a_raw)
        if action == "split":
            # 全局序号 = batch_offset + batch内1-based序号
            global_seq = batch_offset + (i or 0)
            result[global_seq] = {
                "split_before": item.get("split_before") or item.get("split_at", ""),
                "p1": item.get("p1", "k"),
                "p2": item.get("p2", "k"),
                "r": item.get("r", "断句修正"),
            }
    return result


async def _llm_split_active(
    segments: List[Segment],
    ollama_model: str,
    ollama_base_url: str,
    log_callback: Optional[Callable] = None,
) -> List[Segment]:
    """
    Step 2：用本地 LLM 识别断句错误，只返回 split 决策。
    分批（每批 _SPLIT_BATCH_SIZE 段）调用 LLM，合并结果。
    已删除段跳过，LLM 只处理活跃段。
    """
    active = [(i, s) for i, s in enumerate(segments) if s.action != SegmentAction.DELETE]
    if not active:
        return segments

    prompt_path = PROMPTS_DIR / "segment_optimizer.md"
    system_prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""

    # 分批处理
    all_splits: Dict[int, dict] = {}  # global_seq_1based → split info
    n_batches = (len(active) + _SPLIT_BATCH_SIZE - 1) // _SPLIT_BATCH_SIZE

    for b in range(n_batches):
        batch = active[b * _SPLIT_BATCH_SIZE : (b + 1) * _SPLIT_BATCH_SIZE]
        offset = b * _SPLIT_BATCH_SIZE  # global 1-based offset for this batch
        splits = await _llm_split_batch(batch, system_prompt, ollama_model, ollama_base_url, offset)
        all_splits.update(splits)

    if log_callback:
        await log_callback("info", "seg_optim", f"LLM 识别断句错误 {len(all_splits)} 处（{n_batches} 批）")

    if not all_splits:
        return segments

    # 将 split 决策写回 segments
    result = [s.model_copy(deep=True) for s in segments]
    seg_by_id = {s.id: s for s in result}

    for global_seq_1based, info in all_splits.items():
        # global_seq_1based 对应 active 列表中的第几个（1-based）
        idx_in_active = global_seq_1based - 1
        if 0 <= idx_in_active < len(active):
            _, orig_seg = active[idx_in_active]
            target = seg_by_id.get(orig_seg.id)
            if target and info.get("split_before"):
                target.action = SegmentAction.SPLIT
                target.split_before = info["split_before"]
                target.split_part1_action = (
                    SegmentAction.DELETE if info.get("p1") == "d" else SegmentAction.KEEP
                )
                target.split_part2_action = (
                    SegmentAction.DELETE if info.get("p2") == "d" else SegmentAction.KEEP
                )
                target.reason = info.get("r", "断句修正")

    return result


async def run_segment_optimizer(
    segments: List[Segment],
    task_type: str = "monologue_clean",
    provider: str = "ollama",
    ollama_model: str = "qwen2.5:14b",
    ollama_base_url: str = "http://localhost:11434",
    api_key: Optional[str] = None,
    claude_model: str = "anthropic/claude-3.5-sonnet",
    log_callback: Optional[Callable] = None,
) -> List[Segment]:
    """
    段落结构优化器：在 AI 审计前修正分段边界。

    五步流水线：
      Step 1a 规则  — 删除纯噪声/无效沟通段（P3/I0）
      Step 1b 规则  — 轻微结巴字幕去重（P2：STU token → subtitle_fix）
      Step 2  LLM   — 断句修正 split（本地模型，P9/I-S）
      Step 3  规则  — 碎段合并（P11/I-M）
      Step 4  规则  — 超长字幕分行（P12：> 6s && > 40字）

    主模型保留 P9/P11/P2/P3 作为兜底，本地只处理规则可判断的明显 case。
    """
    if not segments:
        return segments

    log = log_callback or (lambda *_: None)

    async def _log(level, msg):
        await log(level, "seg_optim", msg)

    await _log("info", f"开始段落结构优化 | {len(segments)} 个片段 | 模式: {task_type}")

    try:
        # ── Step 1a：规则删除纯噪声段（P3）────────────────────────────
        working, noise_del = _rule_delete_pure_noise(segments, task_type)
        await _log("info", f"Step 1a 纯噪声删除: {noise_del} 段")

        # ── Step 1b：规则结巴字幕去重（P2 轻微档）───────────────────
        working, stutter_fix = _rule_subtitle_fix_stutter(working, task_type)
        await _log("info", f"Step 1b 结巴字幕去重: {stutter_fix} 段")

        # ── Step 2：LLM 断句修正（split only，仅支持 Ollama）──────────
        if provider != "ollama":
            raise ValueError(
                f"段落优化器 Step 2 需要本地 Ollama，当前 provider='{provider}'。"
                "请在设置中切换为 Ollama 模式，或确认 Ollama 服务已启动。"
            )
        working = await _llm_split_active(working, ollama_model, ollama_base_url, log_callback)

        # 应用 split（此时只有 SPLIT 和 DELETE action，不含 MERGE_NEXT）
        after_split = apply_segment_corrections(working)
        split_delta = len(after_split) - len(working)
        await _log("info", f"Step 2 断句修正: split 后段数 {len(working)} → {len(after_split)}")

        # ── Step 3：规则合并干净碎段 ────────────────────────────────
        after_merge_marked, merges = _rule_merge_next_v2(after_split)
        after_merge = apply_segment_corrections(after_merge_marked)
        await _log("info", f"Step 3 碎段合并: {merges} 处，段数 {len(after_split)} → {len(after_merge)}")

        # ── Step 4：超长字幕分行（P12）───────────────────────────────
        after_merge, line_breaks = _rule_subtitle_line_break(after_merge)
        await _log("info", f"Step 4 超长字幕分行: {line_breaks} 段")

        # ── 重置非删除段 action 供主模型审计 ────────────────────────
        # SUBTITLE_FIX 保留（已有 display_text），DELETE 保留，其余重置为 KEEP
        _preserve = {SegmentAction.DELETE, SegmentAction.SUBTITLE_FIX}
        for seg in after_merge:
            if seg.action not in _preserve:
                seg.action = SegmentAction.KEEP
                seg.reason = None

        total_change = len(segments) - len(after_merge)
        await _log("success", f"段落优化完成: {len(segments)} → {len(after_merge)} 段（净减 {total_change}）")
        return after_merge

    except Exception as e:
        await _log("error", f"段落优化出错: {e}，返回原始分段")
        return segments


async def run_semantic_audit(
    segments: List[Segment],
    params: TaskParams,
    task_type: Optional[str] = None,
    api_key: Optional[str] = None,
    model: str = "anthropic/claude-3.5-sonnet",
    style_mode: str = "immersive",
    log_callback: Optional[Callable] = None,
    force_rule_engine: bool = False,
    provider: str = "claude",
    ollama_model: str = "deepseek-r1:32b",
    ollama_base_url: str = "http://localhost:11434",
    highlight_target_dur: Optional[float] = None,
    highlight_max_clips: Optional[int] = None,
    highlight_clip_min_dur: Optional[float] = None,
    highlight_clip_max_dur: Optional[float] = None,
    highlight_total_clips: Optional[int] = None,
    theme_id: Optional[str] = None,
) -> List[Segment]:
    """
    Run semantic audit using Claude (OpenRouter/Anthropic) or local Ollama.

    Args:
        segments: List of ASR segments to audit
        params: Task parameters (thresholds, rules, etc.)
        task_type: Scenario type ("monologue_clean", "interview_compress", "highlight_reel")
        api_key: OpenRouter/Anthropic API key (仅 provider="claude" 时使用)
        model: Claude 模型名（provider="claude" 时使用）
        style_mode: "quick_cut" or "immersive"
        log_callback: Async callback for real-time logging
        force_rule_engine: 用户在前端明确确认后传 True，才使用规则引擎；
                           False 时若无 API Key 则直接抛出异常，不静默降级。
        provider: "claude" | "ollama"
        ollama_model: 本地 Ollama 模型名，如 "deepseek-r1:32b"
        ollama_base_url: Ollama 服务地址，默认 "http://localhost:11434"
    """
    scenario_name = _get_scenario_name(task_type)

    # 规则引擎模式：仅当用户在前端明确确认时才进入
    if force_rule_engine:
        if log_callback:
            await log_callback("info", "system", f"使用本地规则引擎审计（用户已确认）| 场景: {scenario_name}")
        return await _rule_based_audit(segments, params, task_type, log_callback)

    # ── highlight_reel：两个 provider 都走 chunk 模式 ─────────────────────
    if task_type == "highlight_reel":
        import asyncio as _asyncio

        if provider == "ollama":
            try:
                import openai as _openai
            except ImportError:
                import subprocess as _sp, sys as _sys
                _sp.check_call([_sys.executable, "-m", "pip", "install", "openai", "-q"])
                import openai as _openai
            import httpx as _httpx
            _transport = _httpx.HTTPTransport(proxy=None)
            _http_client = _httpx.Client(transport=_transport)
            _client = _openai.OpenAI(
                api_key="ollama",
                base_url=f"{ollama_base_url.rstrip('/')}/v1",
                http_client=_http_client,
            )

            async def call_llm(sys_p: str, usr_p: str) -> str:
                def _call():
                    return _client.chat.completions.create(
                        model=ollama_model,
                        max_tokens=2048,
                        messages=[
                            {"role": "system", "content": sys_p},
                            {"role": "user",   "content": usr_p},
                        ],
                        extra_body={"options": {"num_ctx": 8192}},
                    )
                comp = await _asyncio.to_thread(_call)
                return comp.choices[0].message.content or ""

        else:
            # Claude（OpenRouter 或 Anthropic 原生）
            key = (
                api_key
                or os.environ.get("OPENROUTER_API_KEY")
                or os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("CLAUDE_API_KEY")
            )
            if not key:
                raise ValueError("未配置 API Key，请在设置中填写 OpenRouter 或 Anthropic API Key，或选择使用本地规则引擎")

            use_openrouter = key.startswith("sk-or-") or bool(os.environ.get("OPENROUTER_API_KEY"))

            if use_openrouter:
                try:
                    import openai as _openai
                except ImportError:
                    import subprocess as _sp, sys as _sys
                    _sp.check_call([_sys.executable, "-m", "pip", "install", "openai", "-q"])
                    import openai as _openai
                _model_map = {
                    "claude-3-7-sonnet-20250219": "anthropic/claude-3.7-sonnet",
                    "claude-3-5-sonnet-20241022": "anthropic/claude-3.5-sonnet",
                    "claude-3-5-haiku-20241022":  "anthropic/claude-3.5-haiku",
                }
                or_model = _model_map.get(model, model if "/" in model else f"anthropic/{model}")
                _client = _openai.OpenAI(
                    api_key=key,
                    base_url="https://openrouter.ai/api/v1",
                    default_headers={
                        "HTTP-Referer": "https://github.com/zhangyang1014/JianyingVideoCut",
                        "X-Title": "GoldenClip Video Workstation",
                    },
                )

                async def call_llm(sys_p: str, usr_p: str) -> str:
                    def _call():
                        return _client.chat.completions.create(
                            model=or_model,
                            max_tokens=1024,
                            messages=[
                                {"role": "system", "content": sys_p},
                                {"role": "user",   "content": usr_p},
                            ],
                        )
                    comp = await _asyncio.to_thread(_call)
                    return comp.choices[0].message.content or ""

            else:
                try:
                    import anthropic as _anthropic
                except ImportError:
                    import subprocess as _sp, sys as _sys
                    _sp.check_call([_sys.executable, "-m", "pip", "install", "anthropic", "-q"])
                    import anthropic as _anthropic
                _client = _anthropic.Anthropic(api_key=key)

                async def call_llm(sys_p: str, usr_p: str) -> str:
                    def _call():
                        return _client.messages.create(
                            model=model,
                            max_tokens=1024,
                            system=sys_p,
                            messages=[{"role": "user", "content": usr_p}],
                        )
                    msg = await _asyncio.to_thread(_call)
                    return msg.content[0].text

        return await _run_highlight_chunks(
            segments, call_llm, provider, log_callback,
            highlight_target_dur=highlight_target_dur,
            highlight_max_clips=highlight_max_clips,
            highlight_clip_min_dur=highlight_clip_min_dur,
            highlight_clip_max_dur=highlight_clip_max_dur,
            highlight_total_clips=highlight_total_clips,
            theme_id=theme_id,
        )

    # ── 非 highlight_reel：Ollama 本地推理路径 ────────────────────────────
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

    if log_callback:
        await log_callback("info", "claude", f"开始语义审计 | 场景: {scenario_name} | 模型: {model}")

    # Priority: passed key → OPENROUTER_API_KEY → ANTHROPIC_API_KEY (legacy)
    key = (
        api_key
        or os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("CLAUDE_API_KEY")
    )

    if not key:
        raise ValueError("未配置 API Key，请在设置中填写 OpenRouter 或 Anthropic API Key，或选择使用本地规则引擎")

    # Detect whether this is an OpenRouter key (sk-or-*) or Anthropic key (sk-ant-*)
    use_openrouter = key.startswith("sk-or-") or bool(os.environ.get("OPENROUTER_API_KEY"))

    # Load scenario-specific system prompt
    system_prompt = load_system_prompt(task_type)

    # 注入个人剪辑风格档案（如果已学习过）
    user_style_path = Path(__file__).parent.parent / "data" / "user_style.md"
    if user_style_path.exists():
        style_content = user_style_path.read_text("utf-8").strip()
        if style_content and "（待学习）" not in style_content:
            system_prompt += f"\n\n---\n## 该用户的个人剪辑风格偏好\n\n> 以下是该用户历次反馈积累的个人剪辑偏好，请在审计时参考这些偏好做出更贴合用户习惯的决策。\n\n{style_content}"

    user_prompt, idx_to_id = build_audit_prompt(segments, style_mode, task_type)

    if log_callback:
        route = "OpenRouter" if use_openrouter else "Anthropic"
        await log_callback(
            "info", "claude",
            f"发送 {len(segments)} 个片段 → {route} | 场景: {scenario_name} | 风格: {style_mode}"
        )

    try:
        response_text = None

        if use_openrouter:
            # ── OpenRouter path (OpenAI-compatible) ──────────────────────────
            try:
                import openai
            except ImportError:
                import subprocess, sys
                subprocess.check_call([sys.executable, "-m", "pip", "install", "openai", "-q"])
                import openai

            # Normalize model name: if user passes Anthropic native name, map it
            _model_map = {
                "claude-3-7-sonnet-20250219": "anthropic/claude-3.7-sonnet",
                "claude-3-5-sonnet-20241022": "anthropic/claude-3.5-sonnet",
                "claude-3-5-haiku-20241022":  "anthropic/claude-3.5-haiku",
                "claude-3-opus-20240229":      "anthropic/claude-3-opus",
                "claude-3-sonnet-20240229":    "anthropic/claude-3-sonnet",
            }
            or_model = _model_map.get(model, model if "/" in model else f"anthropic/{model}")

            client = openai.OpenAI(
                api_key=key,
                base_url="https://openrouter.ai/api/v1",
                default_headers={
                    "HTTP-Referer": "https://github.com/zhangyang1014/JianyingVideoCut",
                    "X-Title": "GoldenClip Video Workstation",
                }
            )

            # 每片段约 250 tokens 输出，1.5x 安全系数，下限 8192，上限 65536
            dynamic_max_tokens = min(65536, max(8192, len(segments) * 250 * 3 // 2))

            completion = client.chat.completions.create(
                model=or_model,
                max_tokens=dynamic_max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ]
            )
            response_text = completion.choices[0].message.content

        else:
            # ── Anthropic native path ─────────────────────────────────────────
            try:
                import anthropic
            except ImportError:
                import subprocess, sys
                subprocess.check_call([sys.executable, "-m", "pip", "install", "anthropic", "-q"])
                import anthropic

            # 每片段约 250 tokens 输出，1.5x 安全系数，下限 8192，上限 65536
            dynamic_max_tokens = min(65536, max(8192, len(segments) * 250 * 3 // 2))

            client = anthropic.Anthropic(api_key=key)
            message = client.messages.create(
                model=model,
                max_tokens=dynamic_max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
            response_text = message.content[0].text

        if log_callback:
            await log_callback("info", "claude", "Claude 响应已接收，正在解析...")

        updated_segments = parse_claude_response(response_text, segments, idx_to_id=idx_to_id)

        # 解析后检测：若所有片段均为 KEEP 且输入片段数 > 10，极可能是响应被截断导致 JSON 解析失败
        deleted_after_parse = sum(1 for s in updated_segments if s.action == SegmentAction.DELETE)
        if deleted_after_parse == 0 and len(segments) > 10 and log_callback:
            await log_callback(
                "warn", "claude",
                f"⚠️ 解析结果全部为 KEEP（0 删除），响应可能被截断（响应长度: {len(response_text)} 字符）。"
                f"如确认模型本应删除内容，请重新触发审计，或检查模型输出 token 上限。"
            )

        # 冻结 Claude 原始决策，后续用户修改不会覆盖这两个字段
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
                "success", "claude",
                f"审计完成 | 保留 {kept} 片段，删除 {deleted} 片段 | "
                f"保留时长 {kept_duration:.1f}s ({retention_rate:.0f}%)"
            )

        return updated_segments

    except Exception as e:
        if log_callback:
            await log_callback("error", "claude", f"Claude API 错误: {str(e)}")
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

    if log_callback:
        await log_callback(
            "info", "ollama",
            f"开始本地审计 | 场景: {scenario_name} | 模型: {ollama_model} | "
            f"共 {total} 片段，分 {n_chunks} 批处理（每批≤{chunk_size}）"
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

        # 加载对应场景 system prompt
        system_prompt = load_system_prompt(task_type)
        user_style_path = Path(__file__).parent.parent / "data" / "user_style.md"
        if user_style_path.exists():
            style_content = user_style_path.read_text("utf-8").strip()
            if style_content and "（待学习）" not in style_content:
                system_prompt += (
                    f"\n\n---\n## 该用户的个人剪辑风格偏好\n\n"
                    f"> 以下是该用户历次反馈积累的个人剪辑偏好，请在审计时参考。\n\n{style_content}"
                )

        all_results: List[Segment] = []

        for chunk_idx in range(n_chunks):
            chunk_start = chunk_idx * chunk_size
            chunk_end = min(chunk_start + chunk_size, total)
            chunk_segs = segments[chunk_start:chunk_end]

            if log_callback:
                await log_callback(
                    "info", "ollama",
                    f"第 {chunk_idx + 1}/{n_chunks} 批 | 片段 {chunk_start + 1}-{chunk_end} → Ollama"
                )

            # 为本批构建 Prompt，idx 从 1 开始（独立于全局索引）
            user_prompt, idx_to_id = build_audit_prompt(chunk_segs, style_mode, task_type)

            # 每片段约 150 token 输出，1.5x 安全系数
            dynamic_max_tokens = min(16384, max(2048, len(chunk_segs) * 150 * 3 // 2))

            # 异步环境下用 asyncio.to_thread 避免阻塞事件循环
            import asyncio

            def _call_ollama():
                return client.chat.completions.create(
                    model=ollama_model,
                    max_tokens=dynamic_max_tokens,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_prompt},
                    ]
                )

            completion = await asyncio.to_thread(_call_ollama)
            response_text = completion.choices[0].message.content or ""

            if log_callback:
                await log_callback(
                    "info", "ollama",
                    f"第 {chunk_idx + 1}/{n_chunks} 批推理完成，开始解析"
                )

            # parse_claude_response 使用本批的 idx_to_id 映射，返回该批 Segment
            chunk_results = parse_claude_response(response_text, chunk_segs, idx_to_id)
            all_results.extend(chunk_results)

        if log_callback:
            await log_callback(
                "success", "ollama",
                f"全部 {n_chunks} 批处理完成，共 {len(all_results)} 个片段结果已合并"
            )

        return all_results

    except Exception as e:
        if log_callback:
            await log_callback("error", "ollama", f"Ollama 推理错误: {str(e)}")
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

    # Scenario-specific overrides（highlight_reel 不走此路径，已在 run_semantic_audit 提前分支）
    if task_type == "interview_compress":
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
            if task_type == "interview_compress":
                # Interview: more generous breath
                seg.start = max(0, seg.start - 0.20)
                seg.end = seg.end + 0.15
            else:
                # Monologue: standard breath
                seg.start = max(0, seg.start - 0.15)
                seg.end = seg.end + 0.10

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


# ─── Highlight Reel Chunk-Based Pipeline ──────────────────────────────────────

def build_highlight_chunks(
    segments: List[Segment],
    is_dual_speaker: bool = False,
    target_dur: float = 1200.0,
    hard_dur: float = 1800.0,
    overlap: float = 90.0,
) -> List[List[Segment]]:
    """
    将 audit_segments 按大时间窗口切分为高光审计 chunk。

    策略：
    - 目标窗口：云端默认 ~20 分钟（1200 秒），本地模型建议 ~5 分钟（360 秒）
    - 硬上限：云端 30 分钟，本地 8 分钟
    - 停止时机：到达目标时长后，在下一个自然停顿（句末 or 停顿 > 1.5s）处截断
    - overlap：云端 90 秒，本地 30 秒
    - 最短 chunk：至少 60 秒，避免尾部碎片独立成块
    """
    if not segments:
        return []

    TARGET_DUR = target_dur
    HARD_DUR   = hard_dur
    OVERLAP    = overlap
    MIN_DUR    = 60.0     # 尾部 chunk 最小时长

    chunks: List[List[Segment]] = []
    chunk_start = 0

    while chunk_start < len(segments):
        start_time = segments[chunk_start].start
        chunk_end = chunk_start
        reached_end = False

        while chunk_end < len(segments):
            seg = segments[chunk_end]
            duration = seg.end - start_time
            is_last = chunk_end == len(segments) - 1

            if is_last:
                chunk_end += 1
                reached_end = True
                break

            # 硬上限：强制截断
            if duration >= HARD_DUR:
                chunk_end += 1
                break

            # 达到目标时长后，等自然停顿截断
            if duration >= TARGET_DUR:
                last_text = seg.text.rstrip()
                next_gap = segments[chunk_end + 1].start - seg.end
                is_natural = (last_text and last_text[-1] in "。！？…") or next_gap > 1.5
                if is_natural:
                    chunk_end += 1
                    break

            chunk_end += 1

        chunk = segments[chunk_start:chunk_end]
        if not chunk:
            chunk_start += 1
            continue

        chunks.append(chunk)

        # 已到达视频末尾，不再继续
        if reached_end:
            break

        # overlap：从当前 chunk 末尾往前找第一个 start_time >= (chunk_end_time - OVERLAP) 的段
        chunk_end_time = segments[chunk_end - 1].end
        overlap_start_time = chunk_end_time - OVERLAP
        next_start = chunk_end - 1
        while next_start > chunk_start and segments[next_start].start > overlap_start_time:
            next_start -= 1
        # 保证至少前进 1 段，避免死循环
        next_start = max(chunk_start + 1, next_start)

        chunk_start = next_start

    return chunks


def build_chunk_prompt(
    chunk_segments: List[Segment],
    chunk_idx: int,
    total_chunks: int,
    is_dual_speaker: bool = False,
    max_clips: int = 6,
    clip_min_dur: float = 45.0,
    clip_max_dur: float = 190.0,
) -> str:
    """构建单个大窗口 chunk 的用户 Prompt。"""
    start_time = chunk_segments[0].start
    end_time = chunk_segments[-1].end
    duration_min = round((end_time - start_time) / 60, 1)
    lines = []
    for i, seg in enumerate(chunk_segments):
        entry: dict = {
            "i": i + 1,
            "s": round(seg.start, 1),
            "e": round(seg.end, 1),
            "t": seg.text,
        }
        if is_dual_speaker and seg.speaker:
            entry["sp"] = seg.speaker
        lines.append(json.dumps(entry, ensure_ascii=False))

    first_i = 1
    last_i = len(chunk_segments)
    segs_json = "[\n" + ",\n".join(lines) + "\n]"
    clip_min_s = round(clip_min_dur)
    clip_max_s = round(clip_max_dur)
    # 用实际平均段长估算示例 span，让 AI 对时长约束有直观感受
    chunk_duration = end_time - start_time
    avg_seg_s = chunk_duration / max(len(chunk_segments), 1)
    target_clip_s = (clip_min_dur + clip_max_dur) / 2   # 中间值
    example_span = max(2, round(target_clip_s / avg_seg_s))
    example_from = min(5, last_i)
    example_to = min(example_from + example_span, last_i)
    return (
        f"第 {chunk_idx}/{total_chunks} 段 | {len(chunk_segments)} 条片段 | "
        f"{duration_min} 分钟（{round(start_time)}s – {round(end_time)}s）\n\n"
        f"```json\n{segs_json}\n```\n\n"
        f"从上面的片段中找出最多 {max_clips} 个高光片段，每个片段时长需在 {clip_min_s}s 到 {clip_max_s}s 之间（可见每条片段的 s/e 字段，直接用 e[to_i]-s[from_i] 检查时长），"
        f"直接输出 JSON，格式如下：\n"
        f'{{"clips": [{{"from_i": {example_from}, "to_i": {example_to}, "title": "标题", "score": 8.0, "reason": "原因"}}]}}\n'
        f"其中 from_i 和 to_i 是上面列表里的 i 值（{first_i}–{last_i}之间的整数）。没有高光就输出 {{\"clips\": []}}。"
    )


def _repair_unescaped_quotes(text: str) -> str:
    """
    修复 JSON 字符串值中未转义的双引号。
    场景：AI 在 reason/title 里用 "词" 作强调，导致 JSON 解析失败。
    策略：逐字符扫描，在字符串上下文内遇到 " 时，若后续非 JSON 结构符则转义。
    """
    result: list = []
    i = 0
    n = len(text)
    in_string = False
    prev_escape = False

    while i < n:
        c = text[i]
        if prev_escape:
            result.append(c)
            prev_escape = False
            i += 1
            continue
        if c == '\\':
            result.append(c)
            prev_escape = True
            i += 1
            continue
        if c == '"':
            if not in_string:
                in_string = True
                result.append(c)
            else:
                # 判断是否是字符串结尾：后面紧跟 JSON 结构符
                j = i + 1
                while j < n and text[j] in ' \t\n\r':
                    j += 1
                if j >= n or text[j] in ',}]:':
                    in_string = False
                    result.append(c)
                else:
                    result.append('\\"')
        else:
            result.append(c)
        i += 1

    return ''.join(result)


def parse_chunk_response(
    response_text: str,
    chunk_segments: List[Segment],
    clip_min_dur: float = 45.0,
    clip_max_dur: float = 190.0,
) -> List[dict]:
    """
    解析单个大窗口 chunk 的 Claude 响应。
    返回 clip 列表，每项格式：
    {"from_id": str, "to_id": str, "title": str, "score": float, "reason": str}
    解析失败或无高光时返回 []。
    """
    text = response_text.strip()

    # 剥离 <think>...</think> 推理块（DeepSeek/Qwen 模型会输出）
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

    # 剥离 markdown 代码块
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*```$', '', text, flags=re.MULTILINE).strip()

    # 尝试解析 JSON
    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 先找数组 [...], 再找对象 {...}
        m = re.search(r'\[.*\]', text, re.DOTALL) or re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group())
            except Exception:
                # 修复：字符串值内含未转义双引号（如 AI 输出 "打破"成功妈妈"神话"）
                repaired = _repair_unescaped_quotes(m.group())
                try:
                    data = json.loads(repaired)
                except Exception:
                    pass

    if data is None:
        return []

    # 兼容两种格式：{"clips": [...]}  或直接  [...]
    if isinstance(data, list):
        raw_clips = data
    else:
        raw_clips = data.get("clips", [])
    if not isinstance(raw_clips, list):
        return []

    n = len(chunk_segments)
    result = []

    for clip in raw_clips:
        if not isinstance(clip, dict):
            continue
        from_i = clip.get("from_i")
        to_i = clip.get("to_i")

        # 兼容：模型可能返回 start/end 时间戳而非 from_i/to_i
        if from_i is None and "start" in clip:
            start_t = float(clip["start"])
            from_i = min(range(n), key=lambda k: abs(chunk_segments[k].start - start_t)) + 1
        if to_i is None and "end" in clip:
            end_t = float(clip["end"])
            to_i = min(range(n), key=lambda k: abs(chunk_segments[k].end - end_t)) + 1

        if from_i is None or to_i is None:
            continue

        from_i = max(1, min(int(from_i), n))
        to_i = max(from_i, min(int(to_i), n))

        from_seg = chunk_segments[from_i - 1]
        to_seg = chunk_segments[to_i - 1]

        # 时长校验：留容差避免边界误杀
        # 下限 -30%（AI 有时低估段落密度）；上限 +50%（system prompt 允许"略微超过"）
        clip_dur = to_seg.end - from_seg.start
        if clip_dur < clip_min_dur * 0.7 or clip_dur > clip_max_dur * 1.5:
            print(f"[parse_chunk] REJECT from_i={from_i} to_i={to_i} dur={clip_dur:.1f}s "
                  f"(limit: {clip_min_dur*0.7:.0f}–{clip_max_dur*1.5:.0f}s) n={n}", flush=True)
            continue

        try:
            score = float(clip.get("score", 5))
            score = max(1.0, min(10.0, score))
        except (TypeError, ValueError):
            score = 5.0

        result.append({
            "from_id": from_seg.id,
            "to_id": to_seg.id,
            "title": str(clip.get("title", "")).strip() or "精彩片段",
            "score": score,
            "reason": str(clip.get("reason", "")),
        })

    return result


def assemble_chunk_results(
    clip_results: List[List[dict]],
    all_segments: List[Segment],
    max_total_clips: int = 10,
) -> List[Segment]:
    """
    将所有 chunk 的 clips 汇总，去重、排序、裁剪，写回 all_segments。

    去重：两个 clip 的 segment 范围有交叠 → 保留 score 更高的；
         score 相同取时长更长的；再相同取时间更靠前的。
    裁剪：去重后按 score 降序保留前 max_total_clips 个。
    写回：按时间排序，分配 clip_group 1,2,3...；范围内 KEEP，范围外 DELETE。
    """
    id_to_idx: Dict[str, int] = {seg.id: i for i, seg in enumerate(all_segments)}

    raw_clips = []
    for chunk_clips in clip_results:
        for r in chunk_clips:
            fi = id_to_idx.get(r.get("from_id", ""))
            ti = id_to_idx.get(r.get("to_id", ""))
            if fi is None or ti is None:
                continue
            if fi > ti:
                fi, ti = ti, fi
            raw_clips.append({
                "fi": fi, "ti": ti,
                "title": r.get("title", "精彩片段"),
                "score": r.get("score", 5.0),
                "reason": r.get("reason", ""),
                "dur": all_segments[ti].end - all_segments[fi].start,
            })

    result = [s.model_copy(deep=True) for s in all_segments]

    if not raw_clips:
        for seg in result:
            seg.action = SegmentAction.DELETE
            seg.reason = "H2: 无高光时刻"
            seg.clip_group = None
            seg.clip_title = None
            seg.claude_action = SegmentAction.DELETE
            seg.claude_reason = "H2: 无高光时刻"
        return result

    # 去重：score 高优先占地盘，相同 score 取更长的，再相同取更靠前的
    raw_clips.sort(key=lambda x: (-x["score"], -x["dur"], x["fi"]))
    occupied: set = set()
    deduped = []
    for clip in raw_clips:
        seg_range = set(range(clip["fi"], clip["ti"] + 1))
        if seg_range & occupied:
            continue
        occupied |= seg_range
        deduped.append(clip)

    # 全局裁剪：按 score 降序保留前 max_total_clips 个
    deduped.sort(key=lambda x: (-x["score"], -x["dur"]))
    final_clips = deduped[:max_total_clips]

    # 按时间排序，分配 clip_group
    final_clips.sort(key=lambda x: x["fi"])

    # 先全部标为 DELETE
    for seg in result:
        seg.action = SegmentAction.DELETE
        seg.reason = "H2: 未入选高光"
        seg.clip_group = None
        seg.clip_title = None
        seg.clip_score = None
        seg.clip_reason = None
        seg.claude_action = SegmentAction.DELETE
        seg.claude_reason = "H2: 未入选高光"

    # clip 范围内标为 KEEP
    for group_num, clip in enumerate(final_clips, start=1):
        for idx in range(clip["fi"], clip["ti"] + 1):
            seg = result[idx]
            seg.action = SegmentAction.KEEP
            seg.reason = clip["reason"]
            seg.clip_group = group_num
            seg.clip_title = clip["title"] if idx == clip["fi"] else None
            seg.clip_score = clip["score"] if idx == clip["fi"] else None
            seg.clip_reason = clip["reason"] if idx == clip["fi"] else None
            seg.claude_action = SegmentAction.KEEP
            seg.claude_reason = clip["reason"]

    return result


async def _run_highlight_chunks(
    segments: List[Segment],
    call_llm,  # async (system_prompt: str, user_prompt: str) -> str
    source_tag: str,
    log_callback: Optional[Callable],
    highlight_target_dur: Optional[float] = None,
    highlight_max_clips: Optional[int] = None,
    highlight_clip_min_dur: Optional[float] = None,
    highlight_clip_max_dur: Optional[float] = None,
    highlight_total_clips: Optional[int] = None,
    theme_id: Optional[str] = None,
) -> List[Segment]:
    """
    Provider 无关的 chunk-based 高光审计核心逻辑。
    call_llm: 接收 (system_prompt, user_prompt)，返回模型原始文本。
    source_tag: 日志来源标签，如 "claude" 或 "ollama"。
    """
    import asyncio as _asyncio

    speakers = {seg.speaker for seg in segments if seg.speaker}
    is_dual = len(speakers) > 1

    # 本地模型用小窗口（6 分钟/chunk，最多 2 个 clip），云端用大窗口（20 分钟/chunk，最多 6 个 clip）
    is_local = source_tag == "ollama"
    if is_local:
        default_target_dur, default_hard_dur, default_overlap = 360.0, 480.0, 30.0
        default_max_clips = 2
    else:
        default_target_dur, default_hard_dur, default_overlap = 1200.0, 1800.0, 90.0
        default_max_clips = 6

    target_dur = highlight_target_dur if highlight_target_dur is not None else default_target_dur
    max_clips_per_chunk = highlight_max_clips if highlight_max_clips is not None else default_max_clips
    clip_min_dur = highlight_clip_min_dur if highlight_clip_min_dur is not None else 45.0
    clip_max_dur = highlight_clip_max_dur if highlight_clip_max_dur is not None else 190.0

    # 将实际参数注入 system prompt（占位符替换）
    window_dur_label = f"约{round(target_dur / 60):.0f}分钟"
    system_prompt = (
        load_system_prompt("highlight_reel")
        .replace("{window_dur}", window_dur_label)
        .replace("{max_clips}", str(max_clips_per_chunk))
        .replace("{clip_min_dur}", str(round(clip_min_dur)))
        .replace("{clip_max_dur}", str(round(clip_max_dur)))
    )

    # 注入用户剪辑偏好
    user_style_path = Path(__file__).parent.parent / "data" / "user_style.md"
    if user_style_path.exists():
        style_content = user_style_path.read_text("utf-8").strip()
        if style_content and "（待学习）" not in style_content:
            system_prompt += f"\n\n---\n## 用户剪辑偏好\n\n{style_content}"

    # 注入主题补充规则（如果用户选择了主题）
    if theme_id:
        theme_path = Path(__file__).parent.parent / "prompts" / "themes" / f"{theme_id}.md"
        if theme_path.exists():
            theme_content = theme_path.read_text("utf-8").strip()
            if theme_content:
                system_prompt += (
                    f"\n\n---\n{theme_content}\n\n"
                    f"> **优先级说明：** 以上主题补充规则优先级高于通用规则，如有冲突以本节为准。"
                )
    # hard_dur 跟随 target_dur 等比缩放（保持 1.5x 关系）
    hard_dur = default_hard_dur if highlight_target_dur is None else target_dur * 1.5

    chunks = build_highlight_chunks(
        segments, is_dual_speaker=is_dual,
        target_dur=target_dur, hard_dur=hard_dur, overlap=default_overlap,
    )
    total = len(chunks)

    if log_callback:
        window_label = f"{round(target_dur/60, 0):.0f}min/chunk"
        await log_callback(
            "info", source_tag,
            f"一剪多 chunk 审计 | {'双人' if is_dual else '单人'} | "
            f"{len(segments)} 段 → {total} 个 chunk（{window_label}，每批最多{max_clips_per_chunk}个，片段{clip_min_dur:.0f}s–{clip_max_dur:.0f}s）"
        )

    clip_results: List[List[dict]] = []
    total_candidates = 0

    for idx, chunk in enumerate(chunks):
        chunk_dur = round((chunk[-1].end - chunk[0].start) / 60, 1)
        user_prompt = build_chunk_prompt(chunk, idx + 1, total, is_dual, max_clips=max_clips_per_chunk,
                                         clip_min_dur=clip_min_dur, clip_max_dur=clip_max_dur)
        try:
            response_text = await call_llm(system_prompt, user_prompt)
            if log_callback:
                preview = response_text[:300].replace('\n', ' ')
                await log_callback("info", source_tag, f"Chunk {idx+1} 原始响应: {preview}")
            clips = parse_chunk_response(response_text, chunk, clip_min_dur=clip_min_dur, clip_max_dur=clip_max_dur)
            clip_results.append(clips)
            total_candidates += len(clips)

            if clips:
                titles = "  ".join(
                    f"score={c['score']} [{c['title'][:14]}{'...' if len(c['title']) > 14 else ''}]"
                    for c in clips
                )
                status = f"+{len(clips)} 个候选: {titles}"
            else:
                status = "- 无高光"

            if log_callback:
                await log_callback(
                    "info", source_tag,
                    f"Chunk {idx + 1}/{total}（{chunk_dur}min）{status}"
                )

        except Exception as e:
            clip_results.append([])
            if log_callback:
                await log_callback("warn", source_tag, f"Chunk {idx + 1}/{total} 调用失败: {e}")

    total_clips_limit = highlight_total_clips if highlight_total_clips is not None else 10
    result = assemble_chunk_results(clip_results, segments, max_total_clips=total_clips_limit)
    kept_dur = sum(s.end - s.start for s in result if s.action == SegmentAction.KEEP)
    clip_groups = len({s.clip_group for s in result if s.clip_group is not None})

    if log_callback:
        await log_callback(
            "success", source_tag,
            f"chunk 审计完成 | {total} 个 chunk → {total_candidates} 个候选 → "
            f"去重/裁剪后 {clip_groups} 个 clip | 保留时长 {kept_dur:.0f}s"
        )

    return result


