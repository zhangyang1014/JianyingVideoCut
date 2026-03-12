"""
GoldenClip SemanticAuditor
Core AI class that applies 8 golden rules via Claude API.

Design: 暗金剪辑台 · 编导美学
Flow: Tagged Script → Claude → JSON Audit Instructions
"""

import json
import os
import re
from typing import List, Optional, Callable
from pathlib import Path

from ..models.task import Segment, SegmentAction, AuditRule, TaskParams

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
SYSTEM_PROMPT_PATH = PROMPTS_DIR / "semantic_audit.md"


def load_system_prompt() -> str:
    """Load the SemanticAuditor system prompt from file."""
    if SYSTEM_PROMPT_PATH.exists():
        return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    return _get_default_system_prompt()


def _get_default_system_prompt() -> str:
    return """你是一名拥有十年经验的高级视频编导，专门负责长访谈与口播视频的精修。
    应用8条语义审计规则，将带标记的原始脚本转化为剪辑指令集。
    直接输出JSON数组，不包含任何其他内容。"""


def build_audit_prompt(segments: List[Segment], style_mode: str = "immersive") -> str:
    """Build the user prompt for Claude with segments and tagged script."""
    segments_json = []
    for seg in segments:
        segments_json.append({
            "id": seg.id,
            "start": seg.start,
            "end": seg.end,
            "text": seg.text,
            "tagged_text": seg.tagged_text or seg.text,
            "speaker": seg.speaker,
            "duration": round(seg.end - seg.start, 3)
        })

    prompt = f"""## 当前任务配置
- 风格模式: {style_mode}
- 片段总数: {len(segments)}

## 待审计片段列表

```json
{json.dumps(segments_json, ensure_ascii=False, indent=2)}
```

请对以上所有片段应用8条黄金规则进行审计，输出JSON数组。
每个片段必须有对应的审计结果（action: keep 或 delete）。
严格输出JSON，不要有任何其他文字。"""
    return prompt


def parse_claude_response(response_text: str, original_segments: List[Segment]) -> List[Segment]:
    """
    Parse Claude's JSON response and update segment actions.
    Falls back gracefully if parsing fails.
    """
    # Extract JSON from response (handle markdown code blocks)
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
            except:
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

            # Apply breath padding (Rule 8)
            if seg.action == SegmentAction.KEEP:
                seg.start = max(0, seg.start - 0.15)  # 150ms lead
                seg.end = seg.end + 0.10  # 100ms tail
        updated_segments.append(seg)

    return updated_segments


async def run_semantic_audit(
    segments: List[Segment],
    params: TaskParams,
    api_key: Optional[str] = None,
    model: str = "claude-3-5-sonnet-20241022",
    style_mode: str = "immersive",
    log_callback: Optional[Callable] = None
) -> List[Segment]:
    """
    Run semantic audit using Claude API.
    Falls back to rule-based mock audit if API key not available.
    """
    if log_callback:
        await log_callback("info", "claude", f"开始语义审计，使用模型: {model}")

    # Get API key from params or environment
    key = api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")

    if not key:
        if log_callback:
            await log_callback("warn", "claude", "未找到 Claude API Key，使用规则引擎模拟审计")
        return await _rule_based_audit(segments, params, log_callback)

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key)
        system_prompt = load_system_prompt()
        user_prompt = build_audit_prompt(segments, style_mode)

        if log_callback:
            await log_callback("info", "claude", f"发送 {len(segments)} 个片段给 Claude 审计...")

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

        if log_callback:
            await log_callback(
                "success", "claude",
                f"审计完成：保留 {kept} 片段，删除 {deleted} 片段"
            )

        return updated_segments

    except ImportError:
        if log_callback:
            await log_callback("warn", "claude", "anthropic 库未安装，使用规则引擎模拟审计")
        return await _rule_based_audit(segments, params, log_callback)
    except Exception as e:
        if log_callback:
            await log_callback("error", "claude", f"Claude API 错误: {str(e)}")
        return await _rule_based_audit(segments, params, log_callback)


async def _rule_based_audit(
    segments: List[Segment],
    params: TaskParams,
    log_callback: Optional[Callable] = None
) -> List[Segment]:
    """
    Rule-based fallback audit engine.
    Implements simplified versions of the 8 golden rules.
    """
    if log_callback:
        await log_callback("info", "system", "规则引擎审计开始...")

    filler_words = set(params.filler_words) if params.filler_words else {
        "嗯", "啊", "那个", "然后", "就是说", "就是", "这个"
    }
    retake_threshold = params.retake_char_threshold
    min_duration = params.min_segment_duration

    result_segments = list(segments)

    for i, seg in enumerate(result_segments):
        duration = seg.end - seg.start

        # Rule 2: Fragment Rule - delete short segments
        if params.rules_enabled.get("fragment", True):
            if duration < min_duration:
                # Check if it can stand alone
                word_count = len(seg.text.replace(" ", ""))
                if word_count < 4:
                    seg.action = SegmentAction.DELETE
                    seg.reason = f"片段过短 ({duration:.1f}s)，无法独立成义"
                    seg.rule = AuditRule.FRAGMENT
                    continue

        # Rule 3: Filler Removal - delete pure filler segments
        if params.rules_enabled.get("filler", True):
            clean_text = seg.text.strip()
            if clean_text in filler_words or all(w in filler_words for w in clean_text.split()):
                seg.action = SegmentAction.DELETE
                seg.reason = f"纯语气词片段: {clean_text}"
                seg.rule = AuditRule.FILLER
                continue

        # Rule 1: Retake Rule - detect repeated sentence beginnings
        if params.rules_enabled.get("retake", True) and i > 0:
            prev_seg = result_segments[i - 1]
            if prev_seg.action == SegmentAction.KEEP:
                # Check if beginning chars match
                curr_start = seg.text[:retake_threshold]
                prev_start = prev_seg.text[:retake_threshold]
                if (len(curr_start) >= retake_threshold and
                        curr_start == prev_start and
                        len(seg.text) >= len(prev_seg.text)):
                    # Current is more complete, delete previous
                    prev_seg.action = SegmentAction.DELETE
                    prev_seg.reason = f"重说：被后句替代（开头相同 {retake_threshold} 字）"
                    prev_seg.rule = AuditRule.RETAKE

        # Rule 8: Breath padding for kept segments
        if params.rules_enabled.get("pacing", True) and seg.action == SegmentAction.KEEP:
            seg.start = max(0, seg.start - 0.15)
            seg.end = seg.end + 0.10

    kept = sum(1 for s in result_segments if s.action == SegmentAction.KEEP)
    deleted = sum(1 for s in result_segments if s.action == SegmentAction.DELETE)

    if log_callback:
        await log_callback(
            "success", "system",
            f"规则引擎审计完成：保留 {kept} 片段，删除 {deleted} 片段"
        )

    return result_segments
