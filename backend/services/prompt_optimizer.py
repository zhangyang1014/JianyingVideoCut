"""
GoldenClip PromptOptimizer
根据用户对 AI 审计结果的二次修改，生成提示词改进建议。

流程：
  1. 收集 user_override 且 claude_action != action 的片段（用户主动修改）
  2. 按修改方向分组（误删 / 漏删 / 其他）
  3. 构造反馈 Prompt，包含当前场景提示词全文 + 修改清单
  4. 调 OpenAI 生成规则改进建议
"""

import asyncio
import json
import re
from typing import List, Dict, Any, Optional
from pathlib import Path

from ..core.task_runner import RetryableExecutionError, execute_with_retry
from ..models.task import Segment, SegmentAction, Task, TaskType
from ..models.app_config import DEFAULT_LEARNING_MODEL, ROUTERHUB_BASE_URL, ResolvedFeatureLlm
from .semantic_auditor import load_system_prompt, SCENARIO_PROMPT_MAP
from .app_config_store import resolve_openai_api_key
from .llm_router import route_chat_completion
from .learning_analyzer import apply_prompt_changes, save_versioned_prompt, activate_prompt_version
from .semantic_auditor import (
    MODEL_CACHE_DIR,
    content_cache_fingerprint,
    load_content_cache,
    save_content_cache,
)

USER_STYLE_PATH = Path(__file__).parent.parent / "data" / "user_style.md"
PROMPT_FEEDBACK_CACHE_DIR = MODEL_CACHE_DIR / "prompt_feedback"

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
    收集用户主动修改统计：必须是 user_override，且当前 action 与 AI 原始 action 不同。
    返回结构：
    {
        "total_modified": int,
        "delete_to_keep": [...],    # Claude 误删 → 用户恢复
        "keep_to_delete": [...],    # Claude 漏删 → 用户删除
        "other_changes": [...],     # 其他类型变更
        "unchanged": int,           # 仅供兼容旧前端/调试，不参与学习 prompt
    }
    """
    delete_to_keep: List[Dict[str, Any]] = []
    keep_to_delete: List[Dict[str, Any]] = []
    other_changes: List[Dict[str, Any]] = []
    unchanged = 0

    for seg in segments:
        if seg.claude_action is None:
            continue

        if not seg.user_override:
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
    """构造提交给 OpenAI 的反馈分析 Prompt。"""
    scenario_labels = {
        "monologue_clean": "口播精修",
        "interview_compress": "直播访谈压缩",
        "highlight_reel": "直播卖货集锦",
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

## 用户主动修正记录

以下只包含编导主动调整过的片段，共 {corrections["total_modified"]} 处。未改动片段已排除，不作为本次学习依据。

{corrections_text}
{user_notes_section}
## 输出要求

请按以下格式输出，包含三个部分：

### 第零部分：本次更新点

用 2-5 条短句先列出本次真正会改动的规则更新点。每条只讲变化，不复述完整规则。

### 第一部分：提示词规则改进建议

1. **问题分析**：简要概括用户修正反映出的规则缺陷模式（2-3 句话）
2. **具体建议**：针对每条需要修改的规则，给出：
   - 规则编号（如 P1、I3 等）
   - 当前问题
   - 建议修改内容（给出修改后的规则文本片段）
3. **新增规则**（如需要）：如果现有规则无法覆盖用户的修正模式，建议新增规则

### 第二部分：个人剪辑风格档案更新

请描述该用户的个人剪辑偏好：
```json
{{
  "语气词偏好": "根据修正总结用户对语气词的态度，例如：保留'嗯'开头的思考停顿，删除'然后'...",
  "停顿与节奏偏好": "根据修正总结用户对停顿的态度，例如：容忍 1s 以内的自然停顿...",
  "内容保留倾向": "根据修正总结用户的内容取舍偏好，例如：倾向保留个人故事和情感表达..."
}}
```

### 第三部分：结构化写入数据

请在全文最后额外输出一个 JSON 代码块（用 ```json 包裹），供系统自动写入提示词和风格档案。格式必须严格如下：
```json
{{
  "updates": [
    {{
      "target_rule": "H2",
      "change_type": "modify",
      "summary": "将短片段删除规则改成带悬念和情绪价值豁免"
    }}
  ],
  "prompt_changes": {{
    "changes": [
      {{
        "type": "modify",
        "rule_id": "H2",
        "summary": "将短片段删除规则改成带悬念和情绪价值豁免",
        "full_rule_text": "### 【H2】规则名 ★★★\\n完整的新规则块，必须从三级标题开始，包含该规则全部内容"
      }},
      {{
        "type": "add",
        "rule_id": "H5",
        "insert_after": "H2",
        "summary": "新增真实感与口语瑕疵豁免",
        "full_rule_text": "### 【H5】规则名 ★★★\\n完整的新规则块，必须从三级标题开始，包含该规则全部内容"
      }}
    ]
  }},
  "style_update": {{
    "语气词偏好": "根据修正总结用户对语气词的态度",
    "停顿与节奏偏好": "根据修正总结用户对停顿的态度",
    "内容保留倾向": "根据修正总结用户的内容取舍偏好"
  }}
}}
```

注意：
- 只针对用户修正中明确暴露的问题提建议，不要臆测
- 建议应具体可执行，不要泛泛而谈
- `prompt_changes.changes[*].full_rule_text` 必须是可直接替换或新增到审计提示词中的完整规则块，不要只写差异片段
- `type=modify` 时 `rule_id` 必须对应当前提示词中已有的规则编号；`type=add` 时必须提供 `insert_after`
- 保持规则的格式一致性（### 【编号】规则名 ★★★）
- `style_update` 仅输出能从本次修正中确定的偏好，不确定的项写"暂无足够数据"
- 用中文输出
"""


async def generate_prompt_improvement(
    task: Task,
    api_key: Optional[str] = None,
    model: str = DEFAULT_LEARNING_MODEL,
    user_notes: str = "",
    base_url: str = ROUTERHUB_BASE_URL,
) -> Dict[str, Any]:
    """
    根据用户修正生成提示词改进建议。

    返回：
    {
        "corrections": {...},      # 修正统计
        "suggestion": str,         # OpenAI 返回的改进建议（Markdown）
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
            "update_points": [],
            "prompt_changes": {"changes": []},
        }

    current_prompt = load_system_prompt(task_type)
    feedback_prompt = _build_feedback_prompt(corrections, current_prompt, task_type, user_notes)

    key = resolve_openai_api_key(api_key)

    if not key:
        raise ValueError("未配置 OpenAI API Key，无法生成改进建议")

    try:
        system_prompt = "你是一名资深的视频编导 AI 提示词优化专家。根据用户反馈改进审计提示词。"
        resolved_model = model or DEFAULT_LEARNING_MODEL
        cache_key = content_cache_fingerprint(
            "prompt_feedback:v1",
            {
                "model": resolved_model,
                "endpoint": base_url.rstrip("/"),
                "system_prompt": system_prompt,
                "input": feedback_prompt,
            },
        )
        response_text = load_content_cache(PROMPT_FEEDBACK_CACHE_DIR, cache_key)
        if not isinstance(response_text, str):
            def _validate_feedback(content: str) -> str:
                if not content.strip():
                    raise ValueError("提示词反馈模型返回空内容")
                parsed = extract_feedback_structure(content)
                changes = parsed.get("prompt_changes", {}).get("changes", [])
                if not parsed.get("update_points") and not changes:
                    raise ValueError("提示词反馈模型缺少结构化改进项")
                return content

            routed = await route_chat_completion(
                "prompt_feedback.optimize",
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": feedback_prompt},
                ],
                max_tokens=4096,
                temperature=0.2,
                validate=_validate_feedback,
                override=ResolvedFeatureLlm(
                    feature="learning",
                    mode="cloud",
                    provider="openai_compatible",
                    model=resolved_model,
                    base_url=base_url.rstrip("/"),
                    api_key=key,
                ),
                timeout_seconds=60,
            )
            response_text = routed.content
            try:
                save_content_cache(PROMPT_FEEDBACK_CACHE_DIR, cache_key, response_text)
            except OSError:
                pass

        structured = extract_feedback_structure(response_text or "")

        return {
            "corrections": corrections,
            "suggestion": response_text or "OpenAI 未返回有效内容",
            "scenario": task_type,
            "update_points": structured["update_points"],
            "prompt_changes": structured["prompt_changes"],
            "style_update": structured["style_update"],
        }

    except Exception as e:
        raise ValueError(f"OpenAI API 调用失败: {str(e)}")


def _json_blocks(response_text: str) -> List[Dict[str, Any]]:
    """提取响应中的 JSON 代码块；解析失败的代码块会被忽略。"""
    blocks: List[Dict[str, Any]] = []
    for match in re.finditer(r"```json\s*([\s\S]*?)\s*```", response_text, flags=re.IGNORECASE):
        try:
            data = json.loads(match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            blocks.append(data)
    return blocks


def _normalize_update_points(raw: Any) -> List[Dict[str, str]]:
    """把模型返回的更新点统一成前端可展示的短摘要结构。"""
    if not isinstance(raw, list):
        return []
    points: List[Dict[str, str]] = []
    for item in raw:
        if isinstance(item, str):
            summary = item.strip()
            if summary:
                points.append({"summary": summary})
            continue
        if not isinstance(item, dict):
            continue
        summary = str(item.get("summary") or item.get("title") or item.get("text") or "").strip()
        if not summary:
            continue
        point: Dict[str, str] = {"summary": summary}
        for key in ("target_rule", "change_type", "title"):
            value = item.get(key)
            if value is not None and str(value).strip():
                point[key] = str(value).strip()
        points.append(point)
    return points


def _normalize_prompt_changes(raw: Any) -> Dict[str, List[Dict[str, str]]]:
    """校验并规整可执行的规则变更，防止无效结构进入写入流程。"""
    raw_changes = raw.get("changes") if isinstance(raw, dict) else raw
    if not isinstance(raw_changes, list):
        return {"changes": []}

    changes: List[Dict[str, str]] = []
    for item in raw_changes:
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("rule_id") or "").strip()
        full_rule_text = str(item.get("full_rule_text") or "").strip()
        if not rule_id or not full_rule_text:
            continue
        change_type = str(item.get("type") or item.get("change_type") or "modify").strip()
        if change_type not in {"modify", "add"}:
            continue
        change: Dict[str, str] = {
            "type": change_type,
            "rule_id": rule_id,
            "full_rule_text": full_rule_text,
        }
        for key in ("insert_after", "summary"):
            value = item.get(key)
            if value is not None and str(value).strip():
                change[key] = str(value).strip()
        changes.append(change)
    return {"changes": changes}


def _looks_like_style_update(data: Dict[str, Any]) -> bool:
    style_keys = {"语气词偏好", "停顿与节奏偏好", "停顿倾向", "内容保留倾向"}
    return any(key in data for key in style_keys)


def extract_feedback_structure(response_text: str) -> Dict[str, Any]:
    """从 OpenAI 完整报告中提取更新点、规则变更和风格档案。"""
    update_points: List[Dict[str, str]] = []
    prompt_changes: Dict[str, List[Dict[str, str]]] = {"changes": []}
    style_update: Optional[Dict[str, str]] = None

    for block in _json_blocks(response_text):
        if "updates" in block or "update_points" in block or "prompt_changes" in block:
            update_points = _normalize_update_points(block.get("updates") or block.get("update_points"))
            prompt_changes = _normalize_prompt_changes(block.get("prompt_changes"))
            raw_style = block.get("style_update")
            if isinstance(raw_style, dict):
                style_update = {str(key): str(value) for key, value in raw_style.items()}
        elif _looks_like_style_update(block):
            style_update = {str(key): str(value) for key, value in block.items()}

    if not update_points and prompt_changes["changes"]:
        update_points = _normalize_update_points([
            {
                "target_rule": change.get("rule_id", ""),
                "change_type": change.get("type", ""),
                "summary": change.get("summary") or f"{change.get('type', 'modify')} {change.get('rule_id', '')}",
            }
            for change in prompt_changes["changes"]
        ])

    return {
        "update_points": update_points,
        "prompt_changes": prompt_changes,
        "style_update": style_update,
    }


def _extract_style_json(response_text: str) -> Optional[Dict[str, str]]:
    """兼容旧调用：从 OpenAI 响应中提取风格档案 JSON。"""
    return extract_feedback_structure(response_text)["style_update"]


def apply_feedback_prompt_changes(task_type: str, prompt_changes: Dict[str, Any]) -> Dict[str, Any]:
    """将结构化规则变更合并到当前生效提示词，并激活为新版本。"""
    normalized = _normalize_prompt_changes(prompt_changes)
    if not normalized["changes"]:
        raise ValueError("没有可写入的规则变更")

    current_prompt = load_system_prompt(task_type)
    new_prompt = apply_prompt_changes(
        current_prompt,
        json.dumps(normalized, ensure_ascii=False),
    )
    if new_prompt == current_prompt:
        raise ValueError("规则变更未匹配到当前提示词，请检查规则编号和完整规则块")

    filepath, version = save_versioned_prompt(task_type, new_prompt)
    activate_prompt_version(task_type, filepath)
    return {
        "success": True,
        "version": version,
        "path": filepath,
        "changes_applied": len(normalized["changes"]),
        "content": new_prompt,
    }


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
