"""
GoldenClip 金句提取服务
分析保留片段，推荐最适合做视频开场钩子的「金句」候选。

设计思路：
- 金句标准：观点鲜明、语言精炼、有反转/悬念/共鸣感，时长 5~30 秒，是完整句子
- 复用 semantic_auditor 中已有的 AI 客户端（OpenAI / Ollama）
- 同步调用，约 5-15 秒返回结果
- 根据 task_type 选择不同的 system prompt 和说话人过滤策略
"""

import asyncio
import json
import re
from typing import List, Optional, Callable, Dict, Any, Set, Tuple

from ..core.task_runner import execute_with_retry
from ..models.task import Segment, SegmentAction
from ..models.app_config import DEFAULT_GOLDEN_QUOTE_MODEL, ROUTERHUB_BASE_URL, ResolvedFeatureLlm
from .llm_router import route_chat_completion
from .subtitle_text import get_effective_subtitle_text
from .prompt_registry import golden_quote_prompt_id, resolve_prompt_content
from .semantic_auditor import (
    MODEL_CACHE_DIR,
    content_cache_fingerprint,
    load_content_cache,
    save_content_cache,
)

GOLDEN_QUOTE_CACHE_DIR = MODEL_CACHE_DIR / "golden_quote"

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
# 场景配置：根据 task_type 选择 prompt 和说话人过滤规则
# ============================================================

def _get_scene_config(task_type: str) -> Tuple[str, Optional[Set[Optional[str]]]]:
    """
    返回 (system_prompt, 允许的 speaker 集合)。
    speaker 集合为 None 表示不过滤说话人。
    集合中包含 None 是为了兼容没有说话人标注的片段。
    """
    system_prompt = resolve_prompt_content(golden_quote_prompt_id(task_type))
    if task_type == "interview_compress":
        return system_prompt, {"spk0", None}
    return system_prompt, None


# ============================================================
# 构建用户提示词
# ============================================================

def _build_golden_quote_user_prompt(
    kept_segments: List[Segment],
    task_type: str = "monologue_clean",
) -> str:
    """构建发给 AI 的用户提示词，包含所有保留片段的文本、时长和说话人信息。"""
    lines = ["以下是视频中的发言片段（已保留部分），请从中挑选最适合做开场金句的片段：\n"]

    for seg in kept_segments:
        duration = seg.end - seg.start
        text = get_effective_subtitle_text(seg, include_speaker_label=False)
        if not text:
            continue
        speaker_tag = _SPEAKER_LABELS.get(seg.speaker or "", seg.speaker or "")
        style_hint = f" [style:{seg.style}]" if seg.style else ""
        if speaker_tag:
            lines.append(f"[ID:{seg.id}] [时长:{duration:.1f}s] [{speaker_tag}]{style_hint} {text}")
        else:
            lines.append(f"[ID:{seg.id}] [时长:{duration:.1f}s]{style_hint} {text}")

    if task_type == "highlight_reel":
        lines.append("\n请严格按格式输出 JSON 数组（最多 7 个候选，质量不足可少于 5 个，禁止硬凑）。")
    else:
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
        text = get_effective_subtitle_text(seg, include_speaker_label=False)
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

async def suggest_golden_quotes_openai(
    kept_segments: List[Segment],
    api_key: str,
    model: str = DEFAULT_GOLDEN_QUOTE_MODEL,
    use_openrouter: bool = False,
    log_callback: Optional[Callable] = None,
    system_prompt: Optional[str] = None,
    base_url: str = ROUTERHUB_BASE_URL,
    task_type: str = "monologue_clean",
) -> List[GoldenQuoteCandidate]:
    """使用 OpenAI 分析金句候选。"""
    user_prompt = _build_golden_quote_user_prompt(kept_segments, task_type=task_type)
    system_prompt = system_prompt or resolve_prompt_content(golden_quote_prompt_id(task_type))

    if log_callback:
        await log_callback("info", "golden_quote", f"正在用 AI 分析 {len(kept_segments)} 个片段，识别金句候选...")

    try:
        override = ResolvedFeatureLlm(
            feature="golden_quote",
            mode="cloud",
            provider="openai_compatible",
            model=model or DEFAULT_GOLDEN_QUOTE_MODEL,
            base_url=base_url.rstrip("/"),
            api_key=api_key,
        )

        def _validate(response_text: str) -> List[GoldenQuoteCandidate]:
            candidates = _parse_golden_quote_response(response_text, kept_segments)
            if not candidates:
                raise ValueError("金句模型未返回有效候选")
            return candidates

        routed = await route_chat_completion(
            "golden_quote.extract",
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1024,
            temperature=0.2,
            validate=_validate,
            override=override,
            timeout_seconds=45,
        )
        response_text = routed.content

        if log_callback:
            await log_callback("info", "golden_quote", "AI 响应已接收，正在解析候选金句...")

        candidates = routed.value

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
    system_prompt: Optional[str] = None,
    task_type: str = "monologue_clean",
) -> List[GoldenQuoteCandidate]:
    """使用本地 Ollama 分析金句候选。"""
    user_prompt = _build_golden_quote_user_prompt(kept_segments, task_type=task_type)
    system_prompt = system_prompt or resolve_prompt_content(golden_quote_prompt_id(task_type))

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
            timeout=30,
        )

        def _call():
            return client.chat.completions.create(
                model=ollama_model,
                max_tokens=1024,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ]
            )

        async def _operation():
            return await asyncio.wait_for(asyncio.to_thread(_call), timeout=45)

        completion = await execute_with_retry(_operation, max_retries=3)
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
    provider: str = "openai",
    api_key: Optional[str] = None,
    model: str = DEFAULT_GOLDEN_QUOTE_MODEL,
    use_openrouter: bool = False,
    ollama_model: str = "deepseek-r1:14b",
    ollama_base_url: str = "http://localhost:11434",
    openai_base_url: str = ROUTERHUB_BASE_URL,
    log_callback: Optional[Callable] = None,
    task_type: str = "monologue_clean",
) -> List[GoldenQuoteCandidate]:
    """
    金句候选分析主入口，根据 provider 路由到对应 AI 服务。

    参数：
        segments: 任务的全部 audit_segments（函数内部会自动过滤只保留 kept 片段）
        provider: "openai" | "ollama"
        api_key: OpenAI API Key（provider="openai" 时必填）
        model: OpenAI 模型名称
        use_openrouter: 旧字段，保留兼容，不再使用
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

    resolved_model = ollama_model if provider == "ollama" else (model or DEFAULT_GOLDEN_QUOTE_MODEL)
    resolved_endpoint = (
        f"{ollama_base_url.rstrip('/')}/v1"
        if provider == "ollama"
        else openai_base_url.rstrip("/")
    )
    cache_key = content_cache_fingerprint(
        "golden_quote:v1",
        {
            "provider": provider,
            "model": resolved_model,
            "endpoint": resolved_endpoint,
            "system_prompt": system_prompt,
            "input": _build_golden_quote_user_prompt(valid_segments, task_type=task_type),
        },
    )
    cached = load_content_cache(GOLDEN_QUOTE_CACHE_DIR, cache_key)
    if isinstance(cached, list):
        if log_callback:
            await log_callback("info", "golden_quote", "金句分析命中内容缓存")
        return [item for item in cached if isinstance(item, dict)]

    if provider == "ollama":
        candidates = await suggest_golden_quotes_ollama(
            kept_segments=valid_segments,
            ollama_model=ollama_model,
            ollama_base_url=ollama_base_url,
            log_callback=log_callback,
            system_prompt=system_prompt,
            task_type=task_type,
        )
    else:
        if not api_key:
            raise ValueError("OpenAI 分析需要提供 API Key")
        candidates = await suggest_golden_quotes_openai(
            kept_segments=valid_segments,
            api_key=api_key,
            model=model,
            use_openrouter=use_openrouter,
            log_callback=log_callback,
            system_prompt=system_prompt,
            base_url=openai_base_url,
            task_type=task_type,
        )
    try:
        save_content_cache(GOLDEN_QUOTE_CACHE_DIR, cache_key, candidates)
    except OSError:
        pass
    return candidates
