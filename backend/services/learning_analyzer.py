"""
GoldenClip 学习分析器
对比原始视频与人工剪辑视频的 ASR 字幕，通过 Claude 分析人工剪辑思路，
生成更新后的版本化提示词文件。
"""

import difflib
import json
import os
import re
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable

from ..models.task import ASRResult, LearningTask, TaskType
from .semantic_auditor import load_system_prompt, SCENARIO_PROMPT_MAP, PROMPTS_DIR

LEARNING_PROMPT_PATH = PROMPTS_DIR / "learning_analyze.md"
PROMPT_REWRITE_PATH = PROMPTS_DIR / "prompt_rewrite.md"

SCENARIO_LABELS = {
    "monologue_clean": "口播精修",
    "interview_compress": "访谈压缩",
    "highlight_reel": "精彩集锦",
}


def _extract_plain_text(asr: ASRResult) -> str:
    """从 ASR 结果中提取纯文本（按分段拼接，保留段落编号）。"""
    lines = []
    for i, seg in enumerate(asr.segments, 1):
        text = seg.text.strip()
        if text:
            start = f"{seg.start:.1f}"
            end = f"{seg.end:.1f}"
            lines.append(f"[{i}] ({start}s-{end}s) {text}")
    return "\n".join(lines)


def _extract_segment_texts(asr: ASRResult) -> List[str]:
    """提取每个分段的纯文本列表。"""
    return [seg.text.strip() for seg in asr.segments if seg.text.strip()]


def diff_transcripts(
    original_asr: ASRResult, edited_asr: ASRResult
) -> Dict[str, Any]:
    """
    对比两个 ASR 结果，找出被删减和保留的内容。
    使用 difflib.SequenceMatcher 做序列级比对。
    """
    original_texts = _extract_segment_texts(original_asr)
    edited_texts = _extract_segment_texts(edited_asr)

    original_full = "".join(original_texts)
    edited_full = "".join(edited_texts)

    matcher = difflib.SequenceMatcher(None, original_texts, edited_texts)

    kept_segments = []
    removed_segments = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for idx in range(i1, i2):
                seg = original_asr.segments[idx]
                kept_segments.append({
                    "index": idx + 1,
                    "text": original_texts[idx],
                    "start": seg.start,
                    "end": seg.end,
                })
        elif tag == "delete":
            for idx in range(i1, i2):
                seg = original_asr.segments[idx]
                removed_segments.append({
                    "index": idx + 1,
                    "text": original_texts[idx],
                    "start": seg.start,
                    "end": seg.end,
                })
        elif tag == "replace":
            for idx in range(i1, i2):
                seg = original_asr.segments[idx]
                removed_segments.append({
                    "index": idx + 1,
                    "text": original_texts[idx],
                    "start": seg.start,
                    "end": seg.end,
                })
            for idx in range(j1, j2):
                kept_segments.append({
                    "index": idx + 1,
                    "text": edited_texts[idx],
                    "start": edited_asr.segments[idx].start,
                    "end": edited_asr.segments[idx].end,
                })

    original_duration = original_asr.duration or sum(
        s.end - s.start for s in original_asr.segments
    )
    edited_duration = edited_asr.duration or sum(
        s.end - s.start for s in edited_asr.segments
    )
    retention_rate = (edited_duration / original_duration * 100) if original_duration > 0 else 0

    return {
        "kept_segments": kept_segments,
        "removed_segments": removed_segments,
        "kept_count": len(kept_segments),
        "removed_count": len(removed_segments),
        "original_duration": round(original_duration, 1),
        "edited_duration": round(edited_duration, 1),
        "retention_rate": round(retention_rate, 1),
        "original_char_count": len(original_full),
        "edited_char_count": len(edited_full),
    }


def load_learning_system_prompt() -> str:
    """加载学习分析（第一步：仅分析）的元提示词。"""
    if LEARNING_PROMPT_PATH.exists():
        return LEARNING_PROMPT_PATH.read_text(encoding="utf-8")
    raise FileNotFoundError(f"学习分析提示词文件不存在: {LEARNING_PROMPT_PATH}")


def load_rewrite_system_prompt() -> str:
    """加载提示词改写（第二步：完整改写）的元提示词。"""
    if PROMPT_REWRITE_PATH.exists():
        return PROMPT_REWRITE_PATH.read_text(encoding="utf-8")
    raise FileNotFoundError(f"提示词改写元提示词文件不存在: {PROMPT_REWRITE_PATH}")


def build_analysis_prompt(
    diff_result: Dict[str, Any],
    current_prompt: str,
    task_type: str,
    original_asr: ASRResult,
    edited_asr: ASRResult,
) -> str:
    """
    第一步：组装发给 Claude 的分析 Prompt（带完整 ASR 数据）。
    只要求输出分析报告 + 规则改进建议，不要求生成完整新提示词。
    """
    scenario_name = SCENARIO_LABELS.get(task_type, task_type)
    original_text = _extract_plain_text(original_asr)
    edited_text = _extract_plain_text(edited_asr)

    removed_lines = []
    for seg in diff_result["removed_segments"][:50]:
        removed_lines.append(
            f"  - [{seg['index']}] ({seg['start']:.1f}s-{seg['end']:.1f}s): "
            f"「{seg['text'][:60]}」"
        )
    removed_text = "\n".join(removed_lines) if removed_lines else "（无删减）"

    return f"""## 场景类型：{scenario_name}

## 当前场景提示词（仅供分析参考，判断哪些规则需要改进）

```markdown
{current_prompt}
```

## 原始视频字幕（完整，共 {len(original_asr.segments)} 段，{diff_result['original_duration']}秒）

{original_text}

## 人工剪辑视频字幕（编辑后，共 {len(edited_asr.segments)} 段，{diff_result['edited_duration']}秒）

{edited_text}

## 对比分析摘要

- 保留率: {diff_result['retention_rate']}%
- 原始段数: {len(original_asr.segments)} → 编辑后段数: {len(edited_asr.segments)}
- 被删减段数: {diff_result['removed_count']}
- 原始字数: {diff_result['original_char_count']} → 编辑后字数: {diff_result['edited_char_count']}

### 被删减的具体内容（按时间顺序）

{removed_text}

---

请按照你的分析维度系统性分析这位编导的剪辑思路，输出分析报告和规则改进建议。
"""


def build_rewrite_prompt(analysis_report: str, current_prompt: str, task_type: str) -> str:
    """
    第二步：组装发给 Claude 的改写 Prompt（不带 ASR 数据）。
    只要求 Claude 输出变更规则块的 JSON，由代码合并到原提示词中。
    """
    scenario_name = SCENARIO_LABELS.get(task_type, task_type)

    return f"""## 场景类型：{scenario_name}

## 当前场景提示词（供参考，了解现有规则结构）

{current_prompt}

## 剪辑思路分析报告（包含规则改进建议）

{analysis_report}

---

请根据分析报告，按照要求的 JSON 格式，只输出需要变更的规则块。
"""


def apply_prompt_changes(original_prompt: str, changes_json: str) -> str:
    """
    将 Claude 输出的变更 JSON 合并到原提示词中。
    对于 modify 类型：找到对应规则块并替换。
    对于 add 类型：在指定规则块之后插入新规则。
    若解析失败则返回原提示词。
    """
    # 清理 Claude 可能包裹的 markdown 代码块
    cleaned = changes_json.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned.strip())

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # 尝试提取 JSON 对象
        match = re.search(r'\{[\s\S]*\}', cleaned)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                return original_prompt
        else:
            return original_prompt

    changes = data.get("changes", [])
    if not changes:
        return original_prompt

    result = original_prompt

    for change in changes:
        rule_id = change.get("rule_id", "")
        change_type = change.get("type", "modify")
        full_rule_text = change.get("full_rule_text", "").strip()

        if not rule_id or not full_rule_text:
            continue

        # 转义规则 ID 用于正则（如 I-S、I-M 含有连字符）
        escaped_id = re.escape(rule_id)

        if change_type == "modify":
            # 匹配从 ### 【rule_id】 开始到下一个 ### 或文件末尾的整个规则块
            pattern = rf"(### 【{escaped_id}】.*?)(?=\n### 【|\Z)"
            replacement = full_rule_text + "\n"
            new_result = re.sub(pattern, replacement, result, flags=re.DOTALL)
            if new_result != result:
                result = new_result

        elif change_type == "add":
            insert_after = change.get("insert_after", "")
            if insert_after:
                escaped_after = re.escape(insert_after)
                # 找到 insert_after 规则块的末尾（下一个 ### 【 之前）
                pattern = rf"(### 【{escaped_after}】.*?)((?=\n### 【)|\Z)"
                def make_replacer(rule_text):
                    def replacer(m):
                        return m.group(1) + "\n\n" + rule_text + "\n" + m.group(2)
                    return replacer
                new_result = re.sub(
                    pattern, make_replacer(full_rule_text), result,
                    count=1, flags=re.DOTALL
                )
                if new_result != result:
                    result = new_result
                else:
                    # 找不到插入锚点时，直接追加到末尾
                    result = result.rstrip() + "\n\n" + full_rule_text + "\n"
            else:
                # 没有指定 insert_after，追加到末尾
                result = result.rstrip() + "\n\n" + full_rule_text + "\n"

    return result


def _get_next_version(task_type: str) -> int:
    """扫描现有版本文件，返回下一个版本号。"""
    existing_versions = [1]
    for f in PROMPTS_DIR.glob(f"{task_type}_v*.md"):
        match = re.search(r"_v(\d+)\.md$", f.name)
        if match:
            existing_versions.append(int(match.group(1)))
    return max(existing_versions) + 1


def save_versioned_prompt(task_type: str, content: str) -> tuple:
    """
    保存版本化提示词文件。
    返回 (文件路径, 版本号)。
    """
    version = _get_next_version(task_type)
    filename = f"{task_type}_v{version}.md"
    filepath = PROMPTS_DIR / filename
    filepath.write_text(content, encoding="utf-8")
    return str(filepath), version


def get_prompt_versions(task_type: str) -> List[Dict[str, Any]]:
    """获取某场景的所有提示词版本列表。"""
    versions = []

    base_path = PROMPTS_DIR / f"{task_type}.md"
    if base_path.exists():
        stat = base_path.stat()
        versions.append({
            "version": 1,
            "filename": base_path.name,
            "path": str(base_path),
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "is_base": True,
        })

    for f in sorted(PROMPTS_DIR.glob(f"{task_type}_v*.md")):
        match = re.search(r"_v(\d+)\.md$", f.name)
        if match:
            stat = f.stat()
            versions.append({
                "version": int(match.group(1)),
                "filename": f.name,
                "path": str(f),
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "is_base": False,
            })

    versions.sort(key=lambda v: v["version"])
    return versions


async def _call_claude(
    system_prompt: str,
    user_prompt: str,
    key: str,
    model: str,
    max_tokens: int = 8192,
    label: str = "Claude",
) -> str:
    """统一的 Claude API 调用封装，支持 OpenRouter 和 Anthropic 直连。"""
    use_openrouter = key.startswith("sk-or-") or bool(os.environ.get("OPENROUTER_API_KEY"))

    if use_openrouter:
        import openai

        _model_map = {
            "claude-3-7-sonnet-20250219": "anthropic/claude-3.7-sonnet",
            "claude-3-5-sonnet-20241022": "anthropic/claude-3.5-sonnet",
            "claude-3-5-haiku-20241022": "anthropic/claude-3.5-haiku",
        }
        or_model = _model_map.get(model, model if "/" in model else f"anthropic/{model}")

        client = openai.OpenAI(
            api_key=key,
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": "https://github.com/zhangyang1014/JianyingVideoCut",
                "X-Title": f"GoldenClip {label}",
            },
        )
        completion = client.chat.completions.create(
            model=or_model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return completion.choices[0].message.content or ""

    else:
        import anthropic

        client = anthropic.Anthropic(api_key=key)
        message = client.messages.create(
            model=model if "/" not in model else "claude-3-5-sonnet-20241022",
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return message.content[0].text


async def run_learning_analysis(
    task: LearningTask,
    api_key: Optional[str] = None,
    model: str = "anthropic/claude-3.5-sonnet",
    log_callback: Optional[Callable] = None,
) -> Dict[str, Any]:
    """
    执行学习分析的完整流程（两步 Claude 调用）：
    步骤一：带完整 ASR 数据 → 仅输出分析报告 + 规则改进建议（短输出，不受 token 限制）
    步骤二：不带 ASR 数据 → 基于分析报告输出完整新提示词（输入短，输出空间充足）
    """
    if not task.original_asr_result or not task.edited_asr_result:
        raise ValueError("原始视频和剪辑视频的 ASR 结果都不能为空")

    task_type = task.task_type.value if task.task_type else "monologue_clean"
    scenario_name = SCENARIO_LABELS.get(task_type, task_type)

    key = (
        api_key
        or os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("CLAUDE_API_KEY")
    )
    if not key:
        raise ValueError("未配置 API Key，无法调用 Claude 进行学习分析")

    # ── 字幕对比 ─────────────────────────────────────────────
    if log_callback:
        await log_callback("info", "system", f"开始学习分析 | 场景: {scenario_name}")

    diff_result = diff_transcripts(task.original_asr_result, task.edited_asr_result)

    if log_callback:
        await log_callback(
            "info", "system",
            f"字幕对比完成 | 保留率: {diff_result['retention_rate']}% | "
            f"删减 {diff_result['removed_count']} 段"
        )

    current_prompt = load_system_prompt(task_type)

    # ── 第一步：分析阶段（带 ASR 数据，仅要求输出分析报告） ──
    if log_callback:
        await log_callback("info", "claude", f"【第1步/共2步】发送剪辑分析请求 → 模型: {model}")

    analysis_system = load_learning_system_prompt()
    analysis_user = build_analysis_prompt(
        diff_result, current_prompt, task_type,
        task.original_asr_result, task.edited_asr_result,
    )

    try:
        analysis_report = await _call_claude(
            system_prompt=analysis_system,
            user_prompt=analysis_user,
            key=key,
            model=model,
            max_tokens=4096,
            label="Learning Analyzer",
        )
    except Exception as e:
        if log_callback:
            await log_callback("error", "claude", f"第一步分析失败: {str(e)}")
        raise

    if log_callback:
        await log_callback(
            "info", "claude",
            f"分析报告完成 ({len(analysis_report)} 字)，开始生成新提示词..."
        )

    # ── 第二步：改写阶段（只让 Claude 输出变更块 JSON，代码合并到原提示词） ──
    if log_callback:
        await log_callback(
            "info", "claude",
            "【第2步/共2步】发送提示词变更请求 → 仅输出需要改动的规则块 JSON"
        )

    rewrite_system = load_rewrite_system_prompt()
    rewrite_user = build_rewrite_prompt(analysis_report, current_prompt, task_type)

    try:
        changes_json = await _call_claude(
            system_prompt=rewrite_system,
            user_prompt=rewrite_user,
            key=key,
            model=model,
            max_tokens=8192,
            label="Prompt Rewriter",
        )
    except Exception as e:
        if log_callback:
            await log_callback("error", "claude", f"第二步提示词改写失败: {str(e)}")
        raise

    # 代码层面将变更合并到原提示词，确保原内容完整保留
    new_prompt = apply_prompt_changes(current_prompt, changes_json)

    if log_callback:
        await log_callback(
            "success", "claude",
            f"学习分析完成 | 分析报告: {len(analysis_report)} 字 | "
            f"新提示词: {len(new_prompt)} 字（原: {len(current_prompt)} 字）"
        )

    return {
        "diff": diff_result,
        "analysis_result": analysis_report,
        "new_prompt_content": new_prompt,
    }
