"""
GoldenClip PromptOptimizer
根据用户对 Claude 审计结果的二次修改，生成提示词改进建议。

流程：
  1. 收集 claude_action != action 的片段（用户修改）
  2. 按修改方向分组（误删 / 漏删 / 其他）
  3. 构造反馈 Prompt，包含当前场景提示词全文 + 修改清单
  4. 调 Claude 生成规则改进建议
"""

import json
import os
import re
from typing import List, Dict, Any, Optional
from pathlib import Path

from ..models.task import Segment, SegmentAction, Task, TaskType
from .semantic_auditor import load_system_prompt, SCENARIO_PROMPT_MAP

USER_STYLE_PATH = Path(__file__).parent.parent / "data" / "user_style.md"

# 动作的中文显示名
ACTION_LABELS: Dict[str, str] = {
    "keep": "保留",
    "delete": "删除",
    "subtitle_fix": "字幕去重",
    "text_fix": "ASR纠错",
    "split": "断句",
    "merge_next": "合并下段",
}


def collect_corrections(segments: List[Segment]) -> Dict[str, Any]:
    """
    收集用户修改统计：对比 claude_action 与当前 action。
    返回结构：
    {
        "total_modified": int,
        "delete_to_keep": [...],    # Claude 误删 → 用户恢复
        "keep_to_delete": [...],    # Claude 漏删 → 用户删除
        "other_changes": [...],     # 其他类型变更
        "unchanged": int,
    }
    """
    delete_to_keep: List[Dict[str, Any]] = []
    keep_to_delete: List[Dict[str, Any]] = []
    other_changes: List[Dict[str, Any]] = []
    unchanged = 0

    for seg in segments:
        if seg.claude_action is None:
            continue

        if seg.action == seg.claude_action:
            unchanged += 1
            continue

        entry = {
            "id": seg.id,
            "text": seg.text[:80],
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "claude_action": seg.claude_action.value if seg.claude_action else None,
            "claude_reason": seg.claude_reason or "",
            "user_action": seg.action.value if seg.action else None,
            "rule": seg.rule.value if seg.rule else None,
        }

        claude_val = seg.claude_action.value if seg.claude_action else ""
        user_val = seg.action.value if seg.action else ""

        if claude_val == "delete" and user_val in ("keep", "subtitle_fix", "text_fix", "merge_next"):
            delete_to_keep.append(entry)
        elif claude_val in ("keep", "subtitle_fix", "text_fix", "merge_next") and user_val == "delete":
            keep_to_delete.append(entry)
        else:
            other_changes.append(entry)

    return {
        "total_modified": len(delete_to_keep) + len(keep_to_delete) + len(other_changes),
        "delete_to_keep": delete_to_keep,
        "keep_to_delete": keep_to_delete,
        "other_changes": other_changes,
        "unchanged": unchanged,
    }


def _build_feedback_prompt(
    corrections: Dict[str, Any],
    current_prompt: str,
    task_type: str,
    user_notes: str = "",
) -> str:
    """构造提交给 Claude 的反馈分析 Prompt。"""
    scenario_labels = {
        "monologue_clean": "口播精修",
        "interview_compress": "访谈压缩",
        "highlight_reel": "精彩集锦",
    }
    scenario_name = scenario_labels.get(task_type, task_type)

    sections = []

    # 误删（Claude 标记删除，用户恢复为保留）
    if corrections["delete_to_keep"]:
        items = []
        for c in corrections["delete_to_keep"]:
            items.append(
                f'  - 片段 [{c["id"]}] ({c["start"]}s-{c["end"]}s): '
                f'"{c["text"]}"\n'
                f'    Claude 决策: {ACTION_LABELS.get(c["claude_action"], c["claude_action"])} '
                f'(理由: {c["claude_reason"]})\n'
                f'    用户修正: {ACTION_LABELS.get(c["user_action"], c["user_action"])}'
            )
        sections.append(
            f"### 误删（Claude 删除 → 用户恢复保留）共 {len(corrections['delete_to_keep'])} 处\n\n"
            + "\n\n".join(items)
        )

    # 漏删（Claude 保留，用户标记删除）
    if corrections["keep_to_delete"]:
        items = []
        for c in corrections["keep_to_delete"]:
            items.append(
                f'  - 片段 [{c["id"]}] ({c["start"]}s-{c["end"]}s): '
                f'"{c["text"]}"\n'
                f'    Claude 决策: {ACTION_LABELS.get(c["claude_action"], c["claude_action"])} '
                f'(理由: {c["claude_reason"]})\n'
                f'    用户修正: {ACTION_LABELS.get(c["user_action"], c["user_action"])}'
            )
        sections.append(
            f"### 漏删（Claude 保留 → 用户删除）共 {len(corrections['keep_to_delete'])} 处\n\n"
            + "\n\n".join(items)
        )

    # 其他变更
    if corrections["other_changes"]:
        items = []
        for c in corrections["other_changes"]:
            items.append(
                f'  - 片段 [{c["id"]}]: '
                f'{ACTION_LABELS.get(c["claude_action"], c["claude_action"])} → '
                f'{ACTION_LABELS.get(c["user_action"], c["user_action"])} '
                f'("{c["text"][:40]}")'
            )
        sections.append(
            f"### 其他变更 共 {len(corrections['other_changes'])} 处\n\n"
            + "\n".join(items)
        )

    corrections_text = "\n\n".join(sections)

    user_notes_section = ""
    if user_notes.strip():
        user_notes_section = f"""
## 用户备注

{user_notes.strip()}
"""

    return f"""你是一名资深的视频编导 AI 提示词工程师。以下是一个用于 {scenario_name} 场景的审计提示词，
以及编导（用户）在审核 AI 审计结果后做出的修正。请分析这些修正，找出提示词中导致误判的规则缺陷，
并给出具体的改进建议。

## 当前提示词全文

```markdown
{current_prompt}
```

## 用户修正记录

共 {corrections["total_modified"]} 处修改（{corrections["unchanged"]} 处未改动）

{corrections_text}
{user_notes_section}
## 输出要求

请按以下格式输出，包含两个部分：

### 第一部分：提示词规则改进建议

1. **问题分析**：简要概括用户修正反映出的规则缺陷模式（2-3 句话）
2. **具体建议**：针对每条需要修改的规则，给出：
   - 规则编号（如 P1、I3 等）
   - 当前问题
   - 建议修改内容（给出修改后的规则文本片段）
3. **新增规则**（如需要）：如果现有规则无法覆盖用户的修正模式，建议新增规则

### 第二部分：个人剪辑风格档案更新

请在最末尾输出一个 JSON 代码块（用 ```json 包裹），描述该用户的个人剪辑偏好：
```json
{{
  "语气词偏好": "根据修正总结用户对语气词的态度，例如：保留'嗯'开头的思考停顿，删除'然后'...",
  "停顿与节奏偏好": "根据修正总结用户对停顿的态度，例如：容忍 1s 以内的自然停顿...",
  "内容保留倾向": "根据修正总结用户的内容取舍偏好，例如：倾向保留个人故事和情感表达..."
}}
```

注意：
- 只针对用户修正中明确暴露的问题提建议，不要臆测
- 建议应具体可执行，不要泛泛而谈
- 保持规则的格式一致性（### 【编号】规则名 ★★★）
- 风格档案 JSON 仅输出能从本次修正中确定的偏好，不确定的项写"暂无足够数据"
- 用中文输出
"""


async def generate_prompt_improvement(
    task: Task,
    api_key: Optional[str] = None,
    model: str = "anthropic/claude-3.5-sonnet",
    user_notes: str = "",
) -> Dict[str, Any]:
    """
    根据用户修正生成提示词改进建议。

    返回：
    {
        "corrections": {...},      # 修正统计
        "suggestion": str,         # Claude 返回的改进建议（Markdown）
        "scenario": str,           # 场景类型
    }
    """
    task_type = task.task_type.value if task.task_type else "monologue_clean"
    segments = task.audit_segments or []

    corrections = collect_corrections(segments)
    if corrections["total_modified"] == 0:
        return {
            "corrections": corrections,
            "suggestion": "没有发现用户修改，无需优化提示词。",
            "scenario": task_type,
        }

    current_prompt = load_system_prompt(task_type)
    feedback_prompt = _build_feedback_prompt(corrections, current_prompt, task_type, user_notes)

    key = (
        api_key
        or os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("CLAUDE_API_KEY")
    )

    if not key:
        raise ValueError("未配置 API Key，无法调用 Claude 生成改进建议")

    use_openrouter = key.startswith("sk-or-") or bool(os.environ.get("OPENROUTER_API_KEY"))

    try:
        response_text = None

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
                    "X-Title": "GoldenClip Prompt Optimizer",
                },
            )

            completion = client.chat.completions.create(
                model=or_model,
                max_tokens=4096,
                messages=[
                    {
                        "role": "system",
                        "content": "你是一名资深的视频编导 AI 提示词优化专家。根据用户反馈改进审计提示词。",
                    },
                    {"role": "user", "content": feedback_prompt},
                ],
            )
            response_text = completion.choices[0].message.content

        else:
            import anthropic

            client = anthropic.Anthropic(api_key=key)
            message = client.messages.create(
                model=model if "/" not in model else "claude-3-5-sonnet-20241022",
                max_tokens=4096,
                system="你是一名资深的视频编导 AI 提示词优化专家。根据用户反馈改进审计提示词。",
                messages=[{"role": "user", "content": feedback_prompt}],
            )
            response_text = message.content[0].text

        # 从响应中提取风格档案 JSON
        style_update = _extract_style_json(response_text or "")

        return {
            "corrections": corrections,
            "suggestion": response_text or "Claude 未返回有效内容",
            "scenario": task_type,
            "style_update": style_update,
        }

    except Exception as e:
        raise ValueError(f"Claude API 调用失败: {str(e)}")


def _extract_style_json(response_text: str) -> Optional[Dict[str, str]]:
    """从 Claude 响应中提取末尾的风格档案 JSON 代码块。"""
    match = re.search(r'```json\s*(\{[\s\S]*?\})\s*```', response_text)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def merge_user_style(style_update: Dict[str, str]) -> str:
    """
    将本次学到的风格偏好合并到 user_style.md 文件中。
    每个维度（语气词偏好、停顿倾向等）追加新的学习记录，保留历史。
    返回更新后的完整内容。
    """
    SECTION_MAP = {
        "语气词偏好": "## 语气词偏好",
        "停顿与节奏偏好": "## 停顿与节奏偏好",
        "停顿倾向": "## 停顿与节奏偏好",
        "内容保留倾向": "## 内容保留倾向",
    }

    if USER_STYLE_PATH.exists():
        content = USER_STYLE_PATH.read_text("utf-8")
    else:
        content = "# 个人剪辑风格档案\n\n> 本文件由 GoldenClip 自动学习更新，记录你的剪辑偏好。\n\n## 语气词偏好\n（待学习）\n\n## 停顿与节奏偏好\n（待学习）\n\n## 内容保留倾向\n（待学习）\n"

    from datetime import datetime
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    for key, value in style_update.items():
        if not value or "暂无" in value:
            continue
        section_header = SECTION_MAP.get(key)
        if not section_header:
            continue

        entry = f"- [{timestamp}] {value}"

        # 替换「（待学习）」占位符 或 在该 section 下追加
        placeholder = f"{section_header}\n（待学习）"
        if placeholder in content:
            content = content.replace(placeholder, f"{section_header}\n{entry}")
        elif section_header in content:
            content = content.replace(section_header, f"{section_header}\n{entry}", 1)

    USER_STYLE_PATH.write_text(content, "utf-8")
    return content
