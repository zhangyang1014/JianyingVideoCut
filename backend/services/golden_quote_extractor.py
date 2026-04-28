"""
GoldenClip 金句提取服务
分析保留片段，推荐最适合做视频开场钩子的「金句」候选。

设计思路：
- 金句标准：观点鲜明、语言精炼、有反转/悬念/共鸣感，时长 5~30 秒，是完整句子
- 复用 semantic_auditor 中已有的 AI 客户端（Claude / OpenRouter / Ollama）
- 同步调用，约 5-15 秒返回结果
- 根据 task_type 选择不同的 system prompt 和说话人过滤策略
"""

import json
import re
from typing import List, Optional, Callable, Dict, Any, Set, Tuple
from pathlib import Path

from ..models.task import Segment, SegmentAction

# 金句开场候选结果数据类（不依赖 pydantic，直接用 TypedDict）
GoldenQuoteCandidate = Dict[str, Any]

# 说话人标签映射（与前端 SPEAKER_LABELS 保持一致）
_SPEAKER_LABELS: Dict[str, str] = {
    "spk0": "主播",
    "spk1": "嘉A",
    "spk2": "嘉B",
    "spk3": "嘉C",
}

# ============================================================
# 场景化 System Prompt
# ============================================================

_PROMPT_MONOLOGUE = """你是一名资深视频剪辑编导，专精直播切片和短视频开场设计。

你的任务：从主播说话的文字片段中，挑选出最适合作为视频开场"钩子"的金句。

金句评判标准（优先级从高到低）：
1. **强反转/颠覆认知**：打破常识，让观众想继续看（"你以为X，其实是Y"）
2. **强情绪共鸣**：说出目标观众的心声、痛点或渴望
3. **悬念钩子**：话说到一半，让人想知道后续（"我后来才发现..."）
4. **干货密度高**：一句话说了很多有用信息，让人觉得值得看
5. **语言精炼有力**：句子完整、朗朗上口、不拖沓

排除条件（不适合做金句）：
- 开场寒暄或结束语（"大家好"、"感谢收看"）
- 纯叙述性过渡句（"然后我们接着说"）
- 不完整的句子（上下文依赖性强，单独拿出来看不懂）
- 时长过短（<3秒）或过长（>45秒）的片段

输出格式要求：
严格输出 JSON 数组，必须包含 5~7 个候选（优先保证 5 个），每个元素格式如下：
[
  {
    "segment_id": "片段ID字符串",
    "reason": "推荐理由（20字以内，说明为何适合做开场）"
  }
]

重要：即使部分片段质量一般，也要凑够 5 个候选，让用户自行筛选。
只输出 JSON 数组，不要有任何其他内容。"""


_PROMPT_INTERVIEW_COMPRESS = """你是一名资深视频剪辑编导，专精直播访谈切片和短视频开场设计。

这是一段直播访谈视频，包含主播和嘉宾的对话。你的任务：**只从主播的发言中**挑选最适合做视频开场"钩子"的金句。

金句评判标准（优先级从高到低）：
1. **主播核心观点输出**：主播对某个话题给出鲜明、有力的个人见解
2. **强反转/颠覆认知**：主播说出打破常识的话，让观众想继续看
3. **强情绪共鸣**：主播说出目标观众的心声、痛点或渴望
4. **悬念钩子**：主播的话说到关键处戛然而止，引发好奇
5. **干货密度高**：一句话信息量大，让人觉得这个视频值得看

排除条件（不适合做金句）：
- 嘉宾的发言（所有标记为嘉宾的片段不选）
- 主播对嘉宾的过渡性回应（"对对对"、"嗯是的"、"你说得对"）
- 主播的提问句（提问本身不适合做开场钩子）
- 开场寒暄或结束语（"欢迎来到直播间"、"感谢收看"）
- 纯叙述性过渡句（"我们接着聊下一个话题"）
- 不完整的句子（单独拿出来看不懂）
- 时长过短（<3秒）或过长（>45秒）的片段

输出格式要求：
严格输出 JSON 数组，必须包含 5~7 个候选（优先保证 5 个），每个元素格式如下：
[
  {
    "segment_id": "片段ID字符串",
    "reason": "推荐理由（20字以内，说明为何适合做开场）"
  }
]

重要：即使部分片段质量一般，也要凑够 5 个候选，让用户自行筛选。
只输出 JSON 数组，不要有任何其他内容。"""


_PROMPT_HIGHLIGHT_REEL = """你是一名资深视频剪辑编导，专精精彩集锦和短视频开场设计。

这是一段需要制作精彩集锦的视频。你的任务：从所有发言中挑选最具视觉冲击力和情绪张力的片段，作为视频开场"钩子"。不限说话人身份，谁说的精彩就选谁。

金句评判标准（优先级从高到低）：
1. **高能量/爆点时刻**：情绪激烈、语气有感染力、让人瞬间被抓住
2. **观点交锋/冲突**：两种立场碰撞的瞬间，张力十足
3. **笑点/金句名场面**：让人想分享、想二刷的片段
4. **强反转/颠覆认知**：出人意料的发言，打破观众预期
5. **强情绪共鸣**：说出大部分观众心声的瞬间

排除条件（不适合做金句）：
- 开场寒暄或结束语
- 纯叙述性过渡句
- 不完整的句子（单独拿出来看不懂）
- 平淡陈述、缺乏情绪起伏的片段
- 时长过短（<3秒）或过长（>45秒）的片段

输出格式要求：
严格输出 JSON 数组，必须包含 5~7 个候选（优先保证 5 个），每个元素格式如下：
[
  {
    "segment_id": "片段ID字符串",
    "reason": "推荐理由（20字以内，说明为何适合做开场）"
  }
]

重要：即使部分片段质量一般，也要凑够 5 个候选，让用户自行筛选。
只输出 JSON 数组，不要有任何其他内容。"""


# ============================================================
# 场景配置：根据 task_type 选择 prompt 和说话人过滤规则
# ============================================================

def _get_scene_config(task_type: str) -> Tuple[str, Optional[Set[Optional[str]]]]:
    """
    返回 (system_prompt, 允许的 speaker 集合)。
    speaker 集合为 None 表示不过滤说话人。
    集合中包含 None 是为了兼容没有说话人标注的片段。
    """
    if task_type == "interview_compress":
        return _PROMPT_INTERVIEW_COMPRESS, {"spk0", None}
    elif task_type == "highlight_reel":
        return _PROMPT_HIGHLIGHT_REEL, None
    else:
        return _PROMPT_MONOLOGUE, None


# ============================================================
# 构建用户提示词
# ============================================================

def _build_golden_quote_user_prompt(kept_segments: List[Segment]) -> str:
    """构建发给 AI 的用户提示词，包含所有保留片段的文本、时长和说话人信息。"""
    lines = ["以下是视频中的发言片段（已保留部分），请从中挑选最适合做开场金句的片段：\n"]

    for seg in kept_segments:
        duration = seg.end - seg.start
        text = (seg.display_text or seg.text or "").strip()
        if not text:
            continue
        speaker_tag = _SPEAKER_LABELS.get(seg.speaker or "", seg.speaker or "")
        if speaker_tag:
            lines.append(f"[ID:{seg.id}] [时长:{duration:.1f}s] [{speaker_tag}] {text}")
        else:
            lines.append(f"[ID:{seg.id}] [时长:{duration:.1f}s] {text}")

    lines.append("\n请严格按格式输出 5 个候选金句的 JSON 数组。")
    return "\n".join(lines)


# ============================================================
# 解析 AI 响应
# ============================================================

def _parse_golden_quote_response(
    response_text: str,
    kept_segments: List[Segment],
) -> List[GoldenQuoteCandidate]:
    """
    解析 AI 返回的 JSON，转换为带完整信息的候选列表。
    """
    match = re.search(r'\[[\s\S]*\]', response_text)
    if not match:
        return []

    try:
        raw_list = json.loads(match.group())
    except json.JSONDecodeError:
        return []

    seg_map = {s.id: s for s in kept_segments}

    results: List[GoldenQuoteCandidate] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        seg_id = item.get("segment_id", "")
        reason = item.get("reason", "")
        seg = seg_map.get(seg_id)
        if not seg:
            continue
        text = (seg.display_text or seg.text or "").strip()
        results.append({
            "segment_id": seg_id,
            "text": text,
            "start": seg.start,
            "end": seg.end,
            "reason": reason,
        })

    return results[:7]


# ============================================================
# 各 AI 提供商的调用函数
# ============================================================

async def suggest_golden_quotes_claude(
    kept_segments: List[Segment],
    api_key: str,
    model: str = "claude-3-5-haiku-20241022",
    use_openrouter: bool = False,
    log_callback: Optional[Callable] = None,
    system_prompt: str = _PROMPT_MONOLOGUE,
) -> List[GoldenQuoteCandidate]:
    """使用 Claude（Anthropic 原生或 OpenRouter）分析金句候选。"""
    user_prompt = _build_golden_quote_user_prompt(kept_segments)

    if log_callback:
        await log_callback("info", "golden_quote", f"正在用 AI 分析 {len(kept_segments)} 个片段，识别金句候选...")

    try:
        response_text = None

        if use_openrouter:
            try:
                import openai
            except ImportError:
                import subprocess, sys
                subprocess.check_call([sys.executable, "-m", "pip", "install", "openai", "-q"])
                import openai

            _model_map = {
                "claude-3-5-haiku-20241022": "anthropic/claude-3.5-haiku",
                "claude-3-5-sonnet-20241022": "anthropic/claude-3.5-sonnet",
                "claude-3-7-sonnet-20250219": "anthropic/claude-3.7-sonnet",
            }
            or_model = _model_map.get(model, model if "/" in model else f"anthropic/{model}")

            client = openai.OpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
                default_headers={
                    "HTTP-Referer": "https://github.com/zhangyang1014/JianyingVideoCut",
                    "X-Title": "GoldenClip Video Workstation",
                }
            )
            completion = client.chat.completions.create(
                model=or_model,
                max_tokens=1024,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ]
            )
            response_text = completion.choices[0].message.content

        else:
            try:
                import anthropic
            except ImportError:
                import subprocess, sys
                subprocess.check_call([sys.executable, "-m", "pip", "install", "anthropic", "-q"])
                import anthropic

            client = anthropic.Anthropic(api_key=api_key)
            message = client.messages.create(
                model=model,
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
            response_text = message.content[0].text

        if log_callback:
            await log_callback("info", "golden_quote", "AI 响应已接收，正在解析候选金句...")

        candidates = _parse_golden_quote_response(response_text, kept_segments)

        if log_callback:
            await log_callback(
                "success", "golden_quote",
                f"金句识别完成，找到 {len(candidates)} 个候选"
            )

        return candidates

    except Exception as e:
        if log_callback:
            await log_callback("error", "golden_quote", f"金句分析失败: {str(e)}")
        raise


async def suggest_golden_quotes_ollama(
    kept_segments: List[Segment],
    ollama_model: str = "deepseek-r1:14b",
    ollama_base_url: str = "http://localhost:11434",
    log_callback: Optional[Callable] = None,
    system_prompt: str = _PROMPT_MONOLOGUE,
) -> List[GoldenQuoteCandidate]:
    """使用本地 Ollama 分析金句候选。"""
    user_prompt = _build_golden_quote_user_prompt(kept_segments)

    if log_callback:
        await log_callback(
            "info", "golden_quote",
            f"正在用本地 Ollama ({ollama_model}) 分析 {len(kept_segments)} 个片段..."
        )

    try:
        try:
            import openai
        except ImportError:
            import subprocess, sys
            subprocess.check_call([sys.executable, "-m", "pip", "install", "openai", "-q"])
            import openai

        import httpx
        _transport = httpx.HTTPTransport(proxy=None)
        http_client = httpx.Client(transport=_transport)
        base_url = ollama_base_url.rstrip("/")
        client = openai.OpenAI(
            api_key="ollama",
            base_url=f"{base_url}/v1",
            http_client=http_client,
        )

        completion = client.chat.completions.create(
            model=ollama_model,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        response_text = completion.choices[0].message.content

        if log_callback:
            await log_callback("info", "golden_quote", "Ollama 响应已接收，正在解析...")

        candidates = _parse_golden_quote_response(response_text, kept_segments)

        if log_callback:
            await log_callback(
                "success", "golden_quote",
                f"金句识别完成，找到 {len(candidates)} 个候选"
            )

        return candidates

    except Exception as e:
        if log_callback:
            await log_callback("error", "golden_quote", f"Ollama 金句分析失败: {str(e)}")
        raise


# ============================================================
# 主入口
# ============================================================

async def suggest_golden_quotes(
    segments: List[Segment],
    provider: str = "claude",
    api_key: Optional[str] = None,
    model: str = "claude-3-5-haiku-20241022",
    use_openrouter: bool = False,
    ollama_model: str = "deepseek-r1:14b",
    ollama_base_url: str = "http://localhost:11434",
    log_callback: Optional[Callable] = None,
    task_type: str = "monologue_clean",
) -> List[GoldenQuoteCandidate]:
    """
    金句候选分析主入口，根据 provider 路由到对应 AI 服务。

    参数：
        segments: 任务的全部 audit_segments（函数内部会自动过滤只保留 kept 片段）
        provider: "claude" | "ollama"
        api_key: Claude / OpenRouter API Key（provider="claude" 时必填）
        model: Claude 模型名称
        use_openrouter: 是否走 OpenRouter 接口
        ollama_model: 本地 Ollama 模型名称
        ollama_base_url: 本地 Ollama 服务地址
        task_type: 任务类型，影响 prompt 和说话人过滤
    """
    system_prompt, allowed_speakers = _get_scene_config(task_type)

    # 只取保留片段（含 subtitle_fix / text_fix / merge_next）
    _kept_actions = (
        SegmentAction.KEEP,
        SegmentAction.SUBTITLE_FIX,
        SegmentAction.TEXT_FIX,
        SegmentAction.MERGE_NEXT,
    )
    kept_segments = [s for s in segments if s.action in _kept_actions]

    if not kept_segments:
        if log_callback:
            await log_callback("warn", "golden_quote", "没有保留片段，无法分析金句")
        return []

    # 过滤掉时长过短（<3s）和文本为空的片段
    valid_segments = [
        s for s in kept_segments
        if (s.end - s.start) >= 3.0 and (s.display_text or s.text or "").strip()
    ]

    # 说话人过滤（如 interview_compress 只保留主播片段）
    if allowed_speakers is not None:
        before_count = len(valid_segments)
        valid_segments = [s for s in valid_segments if s.speaker in allowed_speakers]
        if log_callback and before_count != len(valid_segments):
            await log_callback(
                "info", "golden_quote",
                f"场景过滤：{before_count} → {len(valid_segments)} 个片段（仅保留主播发言）"
            )

    if not valid_segments:
        if log_callback:
            await log_callback("warn", "golden_quote", "过滤后没有符合条件的片段，无法分析金句")
        return []

    if provider == "ollama":
        return await suggest_golden_quotes_ollama(
            kept_segments=valid_segments,
            ollama_model=ollama_model,
            ollama_base_url=ollama_base_url,
            log_callback=log_callback,
            system_prompt=system_prompt,
        )
    else:
        if not api_key:
            raise ValueError("Claude 分析需要提供 API Key")
        return await suggest_golden_quotes_claude(
            kept_segments=valid_segments,
            api_key=api_key,
            model=model,
            use_openrouter=use_openrouter,
            log_callback=log_callback,
            system_prompt=system_prompt,
        )
