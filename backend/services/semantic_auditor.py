"""
GoldenClip SemanticAuditor
Core AI class that applies scenario-specific rules via Claude API.

Design: 暗金剪辑台 · 编导美学
Flow: Tagged Script → Claude (Scenario Prompt) → JSON Audit Instructions

Scenarios:
  - monologue_clean:     口播精修 (Prompt P1-P8)
  - interview_compress:  长访谈压缩 (Prompt I1-I8)
  - highlight_reel:      精彩集锦 (Prompt H1-H5)
"""

import json
import os
import re
from typing import List, Optional, Callable
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


def build_audit_prompt(
    segments: List[Segment],
    style_mode: str = "immersive",
    task_type: Optional[str] = None,
    target_duration_ratio: Optional[float] = None,
) -> str:
    """
    Build the user prompt for Claude with segments, tagged script, and scenario context.
    """
    segments_json = []
    for seg in segments:
        segments_json.append({
            "id": seg.id,
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "duration": round(seg.end - seg.start, 3),
            "text": seg.text,
            "tagged_text": seg.tagged_text or seg.text,
            "speaker": seg.speaker or "spk0",
        })

    # Scenario-specific context
    scenario_context = _build_scenario_context(task_type, style_mode, target_duration_ratio, segments)

    total_duration = sum(s["duration"] for s in segments_json)
    speakers = list(set(s["speaker"] for s in segments_json))

    prompt = f"""## 任务配置
- 场景类型: {_get_scenario_name(task_type)}
- 风格模式: {style_mode}
- 片段总数: {len(segments)}
- 总时长: {total_duration:.1f}秒 ({total_duration/60:.1f}分钟)
- 说话人: {', '.join(speakers)}

{scenario_context}

## 待审计片段列表

```json
{json.dumps(segments_json, ensure_ascii=False, indent=2)}
```

请严格按照场景规则对以上所有片段进行审计，输出JSON数组。
**每个片段必须有对应的审计结果（action: keep 或 delete）。**
**严格输出JSON数组，不要有任何其他文字、解释或markdown标记。**"""
    return prompt


def _get_scenario_name(task_type: Optional[str]) -> str:
    names = {
        "monologue_clean": "口播精修 (Monologue Clean)",
        "interview_compress": "长访谈压缩 (Interview Compress)",
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
- 目标时长: {target_dur:.0f}秒 (1-3分钟集锦)
- 目标保留率: 10-25%（只保留高能时刻）
- 重点规则: H1(高能识别) > H2(前3秒钩子) > H5(废话零容忍) > H3(节奏加速)
- 气口预留: 开头80ms / 结尾50ms（快节奏）
- 特别要求: 在reason中注明建议的排列顺序（集锦可能需要重新排序）"""

    return ""


def parse_claude_response(response_text: str, original_segments: List[Segment]) -> List[Segment]:
    """
    Parse Claude's JSON response and update segment actions.
    Falls back gracefully if parsing fails.
    """
    json_text = response_text.strip()

    # Remove markdown code blocks if present
    json_text = re.sub(r'^```(?:json)?\s*', '', json_text, flags=re.MULTILINE)
    json_text = re.sub(r'\s*```$', '', json_text, flags=re.MULTILINE)
    json_text = json_text.strip()

    try:
        audit_results = json.loads(json_text)
    except json.JSONDecodeError:
        # Try to extract JSON array from text
        match = re.search(r'\[.*\]', json_text, re.DOTALL)
        if match:
            try:
                audit_results = json.loads(match.group())
            except Exception:
                return original_segments
        else:
            return original_segments

    # Build lookup by id
    result_map = {r.get("id"): r for r in audit_results if isinstance(r, dict)}

    # Update original segments
    updated_segments = []
    for seg in original_segments:
        result = result_map.get(seg.id)
        if result:
            action_str = result.get("action", "keep").lower()
            seg.action = SegmentAction.DELETE if action_str == "delete" else SegmentAction.KEEP
            seg.reason = result.get("reason", "")
            seg.style = result.get("style")

            # Map rule string to enum
            rule_str = result.get("rule", "")
            if rule_str:
                for rule in AuditRule:
                    if rule.value in rule_str or rule_str in rule.value:
                        seg.rule = rule
                        break

        updated_segments.append(seg)

    return updated_segments


async def run_semantic_audit(
    segments: List[Segment],
    params: TaskParams,
    task_type: Optional[str] = None,
    api_key: Optional[str] = None,
    model: str = "claude-3-5-sonnet-20241022",
    style_mode: str = "immersive",
    log_callback: Optional[Callable] = None
) -> List[Segment]:
    """
    Run semantic audit using Claude API with scenario-specific prompts.
    Falls back to rule-based audit if API key not available.

    Args:
        segments: List of ASR segments to audit
        params: Task parameters (thresholds, rules, etc.)
        task_type: Scenario type ("monologue_clean", "interview_compress", "highlight_reel")
        api_key: Claude API key (optional, falls back to env var)
        model: Claude model to use
        style_mode: "quick_cut" or "immersive"
        log_callback: Async callback for real-time logging
    """
    scenario_name = _get_scenario_name(task_type)

    if log_callback:
        await log_callback("info", "claude", f"开始语义审计 | 场景: {scenario_name} | 模型: {model}")

    # Get API key from params or environment
    key = api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")

    if not key:
        if log_callback:
            await log_callback("warn", "claude", f"未找到 Claude API Key，使用规则引擎模拟审计（场景: {scenario_name}）")
        return await _rule_based_audit(segments, params, task_type, log_callback)

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key)

        # Load scenario-specific system prompt
        system_prompt = load_system_prompt(task_type)
        user_prompt = build_audit_prompt(segments, style_mode, task_type)

        if log_callback:
            await log_callback(
                "info", "claude",
                f"发送 {len(segments)} 个片段给 Claude | 场景: {scenario_name} | 风格: {style_mode}"
            )

        message = client.messages.create(
            model=model,
            max_tokens=8192,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )

        response_text = message.content[0].text

        if log_callback:
            await log_callback("info", "claude", "Claude 响应已接收，正在解析...")

        updated_segments = parse_claude_response(response_text, segments)

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

    except ImportError:
        if log_callback:
            await log_callback("warn", "claude", "anthropic 库未安装，使用规则引擎模拟审计")
        return await _rule_based_audit(segments, params, task_type, log_callback)
    except Exception as e:
        if log_callback:
            await log_callback("error", "claude", f"Claude API 错误: {str(e)}")
        return await _rule_based_audit(segments, params, task_type, log_callback)


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

    result_segments = list(segments)

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

    # ── Post-processing: Highlight Reel special logic ─────────────────────────
    if task_type == "highlight_reel":
        # Mark the longest kept segment as a potential highlight
        kept_segs = [s for s in result_segments if s.action == SegmentAction.KEEP]
        if kept_segs:
            longest = max(kept_segs, key=lambda s: s.end - s.start)
            longest.style = "highlight"
            longest.reason = (longest.reason or "") + " [规则引擎标注：最长保留片段，建议作为核心高能时刻]"

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
