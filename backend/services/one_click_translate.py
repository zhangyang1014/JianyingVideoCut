"""
One-click target-language dubbing pipeline.

The handoff unit is TranslationCue:
ASR segment -> calibrated subtitle -> translated subtitle -> aligned TTS audio.
All cue start/end values stay on the original video timeline so generated
audio can be placed with deterministic offsets.
"""

import asyncio
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from ..models.task import (
    ASRResult,
    LLMModelConfig,
    OneClickTranslateConfig,
    TranslationCue,
    TranslationVideo,
    TTSModelConfig,
)
from ..models.app_config import (
    DEFAULT_TRANSLATE_MODEL,
    OPENROUTER_BASE_URL,
    ROUTERHUB_BASE_URL,
    ResolvedFeatureLlm,
)
from .subtitle_text import get_effective_subtitle_text
from ..agent_runtime.contracts import (
    format_contract_issues,
    validate_translation_cues_contract,
    validate_translation_payload_contract,
)
from .app_config_store import resolve_openai_api_key
from .llm_router import route_chat_completion
from .ffmpeg_executor import _format_srt_time, get_video_info
from .prompt_registry import resolve_prompt_content
from .model_usage import (
    manual_pricing_for_feature,
    openrouter_catalog_pricing,
    record_model_call,
)
from ..core.task_runner import execute_with_retry, RetryableExecutionError


LANGUAGE_LABELS = {
    "auto": "自动检测",
    "zh": "中文",
    "en": "英文",
    "ja": "日文",
    "ko": "韩文",
    "es": "西班牙文",
    "fr": "法文",
    "de": "德文",
}

TRANSLATION_BATCH_SIZE = 40
_TRANSLATION_CACHE_SCHEMA = 1


def _stable_digest(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(part, path)


def _stable_cue_id(segment_id: str, start: float, end: float, text: str) -> str:
    digest = _stable_digest({
        "source_segment_id": segment_id,
        "start": round(start, 3),
        "end": round(end, 3),
        "source_text": text,
    })
    return f"cue_{digest[:16]}"


def normalize_subtitle_text(text: str) -> str:
    """Light cleanup before LLM calibration."""
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def build_translation_cues(asr_result: ASRResult) -> List[TranslationCue]:
    cues: List[TranslationCue] = []
    for seg in asr_result.segments:
        if seg.end <= seg.start:
            continue
        source_text = normalize_subtitle_text(get_effective_subtitle_text(seg, include_speaker_label=False))
        if not source_text:
            continue
        cues.append(
            TranslationCue(
                id=_stable_cue_id(seg.id, seg.start, seg.end, source_text),
                source_segment_id=seg.id,
                start=seg.start,
                end=seg.end,
                source_text=source_text,
                calibrated_text=source_text,
                status="asr_done",
            )
        )
    validate_cue_timeline(cues)
    return cues


def validate_cue_timeline(cues: Iterable[TranslationCue]) -> None:
    seen = set()
    previous_start = -1.0
    for cue in cues:
        if cue.id in seen:
            raise ValueError(f"重复字幕 cue id: {cue.id}")
        seen.add(cue.id)
        if cue.end <= cue.start:
            raise ValueError(f"字幕 cue 时间无效: {cue.id}")
        if cue.start < previous_start:
            raise ValueError("字幕 cue 必须按原视频时间轴升序排列")
        previous_start = cue.start


def validate_stage_ready(video: TranslationVideo, stage: str) -> None:
    if stage == "asr":
        if not video.asr_result or not video.cues:
            raise ValueError(f"{video.filename} 缺少 ASR 结果")
    elif stage == "calibrated":
        validate_stage_ready(video, "asr")
        missing = [cue.id for cue in video.cues if not cue.calibrated_text]
        if missing:
            raise ValueError(f"{video.filename} 存在未校准字幕: {missing[:5]}")
    elif stage == "translated":
        validate_stage_ready(video, "calibrated")
        missing = [cue.id for cue in video.cues if not cue.translated_text]
        if missing:
            raise ValueError(f"{video.filename} 存在未翻译字幕: {missing[:5]}")
    elif stage == "audio":
        validate_stage_ready(video, "translated")
        missing = [
            cue.id
            for cue in video.cues
            if not cue.audio_path or not _has_valid_cached_or_legacy_audio(cue.audio_path)
        ]
        if missing:
            raise ValueError(f"{video.filename} 存在未生成音频: {missing[:5]}")
    validate_cue_timeline(video.cues)


# 单视频流水线阶段顺序：asr → translate → tts → dub
TRANSLATION_STAGES = ["asr", "translate", "tts", "dub"]


def determine_resume_stage(video: TranslationVideo) -> str:
    """
    根据视频已有的阶段产物判断断点续跑的起始阶段。

    依次用 validate_stage_ready 检查 asr / translated / audio 契约，
    返回第一个未就绪的阶段（"asr" | "translate" | "tts"）；
    全部就绪则从 "dub"（音频对齐+混流出预览）重跑，代价最小。
    """

    def _ready(stage: str) -> bool:
        try:
            validate_stage_ready(video, stage)
            return True
        except ValueError:
            return False

    if not _ready("asr"):
        return "asr"
    if not _ready("translated"):
        return "translate"
    if not _ready("audio"):
        return "tts"
    return "dub"


def write_cues_srt(cues: List[TranslationCue], srt_path: str, text_field: str) -> str:
    lines: List[str] = []
    idx = 1
    for cue in cues:
        text = getattr(cue, text_field, None) or ""
        text = text.strip()
        if not text:
            continue
        lines.append(str(idx))
        lines.append(f"{_format_srt_time(cue.start)} --> {_format_srt_time(cue.end)}")
        lines.append(text)
        lines.append("")
        idx += 1

    Path(srt_path).parent.mkdir(parents=True, exist_ok=True)
    Path(srt_path).write_text("\n".join(lines), encoding="utf-8")
    return srt_path


def _translation_batch_identity(
    cues: List[TranslationCue],
    config: OneClickTranslateConfig,
) -> tuple[str, dict]:
    identity = {
        "schema": _TRANSLATION_CACHE_SCHEMA,
        "source_language": config.source_language,
        "target_language": config.target_language,
        "model": config.subtitle_model.model_dump(mode="json"),
        "prompt_digest": hashlib.sha256(
            resolve_prompt_content("translate.cues").encode("utf-8")
        ).hexdigest(),
        "cues": [
            {
                "id": cue.id,
                "source_segment_id": cue.source_segment_id,
                "start": round(cue.start, 3),
                "end": round(cue.end, 3),
                "text": cue.calibrated_text or cue.source_text,
            }
            for cue in cues
        ],
    }
    return _stable_digest(identity), identity


def _load_translation_batch_cache(
    path: Path,
    digest: str,
    originals: List[TranslationCue],
) -> Optional[List[TranslationCue]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != _TRANSLATION_CACHE_SCHEMA or payload.get("digest") != digest:
            return None
        raw_cues = payload.get("cues")
        if not isinstance(raw_cues, list):
            return None
        cached = [TranslationCue.model_validate(item) for item in raw_cues]
        report = validate_translation_cues_contract(originals, cached)
        if report.rejected:
            return None
        validate_stage_translated(cached)
        if [cue.id for cue in cached] != [cue.id for cue in originals]:
            return None
        return cached
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


async def calibrate_and_translate_cues(
    cues: List[TranslationCue],
    config: OneClickTranslateConfig,
    api_key: Optional[str] = None,
    log_callback: Optional[Callable] = None,
    checkpoint_dir: Optional[str] = None,
    batch_size: int = TRANSLATION_BATCH_SIZE,
) -> List[TranslationCue]:
    validate_cue_timeline(cues)
    if batch_size < 1:
        raise ValueError("翻译 batch_size 必须大于 0")
    provider = (config.subtitle_model.provider or "mock").lower()
    if provider not in {"", "mock", "rule_engine", "local", "ollama", "openai", "openrouter", "anthropic"}:
        raise ValueError(f"不支持的字幕模型 provider: {provider}")

    cache_root = Path(checkpoint_dir) if checkpoint_dir else None
    batches = [cues[index:index + batch_size] for index in range(0, len(cues), batch_size)]
    failures = []
    for batch_index, batch in enumerate(batches, start=1):
        originals = [cue.model_copy(deep=True) for cue in batch]
        digest, identity = _translation_batch_identity(originals, config)
        cache_path = cache_root / f"{digest}.json" if cache_root else None
        translated_batch = (
            _load_translation_batch_cache(cache_path, digest, originals)
            if cache_path is not None else None
        )
        if translated_batch is not None:
            if log_callback:
                await log_callback(
                    "info", "translate",
                    f"翻译批次 {batch_index}/{len(batches)} 命中断点缓存",
                )
        else:
            if log_callback:
                await log_callback(
                    "info", "translate",
                    f"调用 {provider} 校准并翻译批次 {batch_index}/{len(batches)}（{len(batch)} 条）",
                )

            async def _attempt_batch() -> List[TranslationCue]:
                try:
                    if provider in {"mock", "rule_engine", "local"}:
                        candidate = [cue.model_copy(deep=True) for cue in originals]
                        for cue in candidate:
                            calibrated = normalize_subtitle_text(cue.calibrated_text or cue.source_text)
                            cue.calibrated_text = calibrated
                            cue.translated_text = _mock_translate(calibrated, config.target_language)
                            cue.status = "translated"
                    else:
                        payload = await _call_subtitle_llm(originals, config, api_key)
                        payload_report = validate_translation_payload_contract(originals, payload)
                        if payload_report.rejected:
                            raise ValueError(
                                f"字幕模型输出契约失败: {format_contract_issues(payload_report.issues)}"
                            )
                        by_id = {item.get("id"): item for item in payload if isinstance(item, dict)}
                        candidate = [cue.model_copy(deep=True) for cue in originals]
                        missing = []
                        for cue in candidate:
                            item = by_id.get(cue.id)
                            if not item:
                                missing.append(cue.id)
                                continue
                            cue.calibrated_text = normalize_subtitle_text(
                                item.get("calibrated_text") or cue.source_text
                            )
                            cue.translated_text = normalize_subtitle_text(item.get("translated_text") or "")
                            cue.status = "translated" if cue.translated_text else "error"
                        if missing:
                            raise ValueError(f"模型输出缺少字幕 cue: {missing[:5]}")

                    validate_stage_translated(candidate)
                    report = validate_translation_cues_contract(originals, candidate)
                    if report.rejected:
                        raise ValueError(
                            f"字幕校准/翻译契约失败: {format_contract_issues(report.issues)}"
                        )
                    return candidate
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    raise RetryableExecutionError(
                        f"翻译批次 {batch_index} 失败: {exc}"
                    ) from exc

            try:
                translated_batch = await execute_with_retry(_attempt_batch)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                for cue in batch:
                    cue.status = "error"
                    cue.error_message = str(exc)
                failures.append((batch_index, exc))
                continue
            if cache_path is not None:
                _atomic_write_json(cache_path, {
                    "schema": _TRANSLATION_CACHE_SCHEMA,
                    "digest": digest,
                    "identity": identity,
                    "cues": [cue.model_dump(mode="json") for cue in translated_batch],
                })

        translated_by_id = {cue.id: cue for cue in translated_batch}
        for cue in batch:
            translated = translated_by_id[cue.id]
            cue.calibrated_text = translated.calibrated_text
            cue.translated_text = translated.translated_text
            cue.status = translated.status
            cue.error_message = None

    if failures:
        failed_batches = [index for index, _exc in failures]
        raise RuntimeError(
            f"{len(failures)} 个翻译批次重试耗尽: {failed_batches[:5]}；首个错误: {failures[0][1]}"
        )
    validate_stage_translated(cues)
    return cues


def validate_stage_translated(cues: Iterable[TranslationCue]) -> None:
    missing = [cue.id for cue in cues if not cue.calibrated_text or not cue.translated_text]
    if missing:
        raise ValueError(f"字幕校准/翻译结果不完整: {missing[:5]}")


def _mock_translate(text: str, target_language: str) -> str:
    if target_language == "en":
        return f"[EN] {text}"
    label = LANGUAGE_LABELS.get(target_language, target_language)
    return f"[{label}] {text}"


async def _call_subtitle_llm(
    cues: List[TranslationCue],
    config: OneClickTranslateConfig,
    api_key: Optional[str],
) -> list:
    model_cfg = config.subtitle_model
    provider = (model_cfg.provider or "").lower()
    source_label = LANGUAGE_LABELS.get(config.source_language, config.source_language)
    target_label = LANGUAGE_LABELS.get(config.target_language, config.target_language)
    compact_cues = [
        {
            "id": cue.id,
            "start": cue.start,
            "end": cue.end,
            "text": cue.calibrated_text or cue.source_text,
        }
        for cue in cues
    ]
    system_prompt = resolve_prompt_content("translate.cues")
    user_prompt = (
        f"原语言: {source_label}\n目标语言: {target_label}\n"
        f"字幕 JSON:\n{json.dumps(compact_cues, ensure_ascii=False)}"
    )

    if provider in {"", "ollama", "openai", "openrouter", "anthropic"}:
        override = None
        if provider:
            key = "ollama" if provider == "ollama" else resolve_openai_api_key(api_key)
            if provider != "ollama" and not key:
                raise ValueError("缺少 OpenAI API Key")
            override = ResolvedFeatureLlm(
                feature="translate",
                mode="local" if provider == "ollama" else "cloud",
                provider="ollama" if provider == "ollama" else "openai_compatible",
                model=model_cfg.model or DEFAULT_TRANSLATE_MODEL,
                base_url=(model_cfg.base_url or (
                    "http://localhost:11434" if provider == "ollama" else ROUTERHUB_BASE_URL
                )).rstrip("/"),
                api_key=key,
            )

        def _validate(raw: str) -> list:
            payload = _parse_json_array(raw)
            report = validate_translation_payload_contract(cues, payload)
            if report.rejected:
                raise ValueError(
                    f"字幕模型输出契约失败: {format_contract_issues(report.issues)}"
                )
            return payload

        routed = await route_chat_completion(
            "translate.subtitle",
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max(2048, len(cues) * 120),
            temperature=0.2,
            validate=_validate,
            override=override,
            timeout_seconds=120,
        )
        return routed.value
    raise ValueError(f"不支持的字幕模型 provider: {provider}")


async def _call_openai_compatible_json(
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> list:
    def _call() -> str:
        import openai

        client = openai.OpenAI(api_key=api_key, base_url=base_url)
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )
        return completion.choices[0].message.content or "[]"

    raw = await asyncio.to_thread(_call)
    return _parse_json_array(raw)


async def _call_anthropic_json(
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> list:
    def _call() -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=model,
            max_tokens=8192,
            temperature=0.2,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return "".join(block.text for block in message.content if getattr(block, "type", "") == "text")

    raw = await asyncio.to_thread(_call)
    return _parse_json_array(raw)


def _parse_json_array(raw: str) -> list:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    match = re.search(r"\[[\s\S]*\]", raw)
    if match:
        raw = match.group(0)
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("模型输出必须是 JSON 数组")
    return data


async def generate_tts_audio_for_cues(
    cues: List[TranslationCue],
    output_dir: str,
    tts_config: TTSModelConfig,
    api_key: Optional[str] = None,
    log_callback: Optional[Callable] = None,
) -> List[TranslationCue]:
    validate_stage_translated(cues)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    provider = (tts_config.provider or "openai").lower()
    if provider not in {"mock", "silent", "openai"}:
        raise ValueError(f"不支持的 TTS provider: {provider}")
    if provider == "openai" and not resolve_openai_api_key(api_key):
        raise ValueError("缺少 OpenAI API Key，无法生成目标语言语音")

    failures = []
    for index, cue in enumerate(cues, start=1):
        text = (cue.translated_text or "").strip()
        identity = {
            "schema": _TRANSLATION_CACHE_SCHEMA,
            "cue_id": cue.id,
            "text": text,
            "provider": provider,
            "model": tts_config.model,
            "voice": tts_config.voice,
            "base_url": tts_config.base_url,
            "format": "mp3",
        }
        digest = _stable_digest(identity)
        audio_path = str(Path(output_dir) / f"{cue.id}-{digest[:16]}.mp3")
        metadata_path = Path(audio_path + ".json")
        if _load_tts_cache(metadata_path, audio_path, digest):
            record_model_call(
                feature="tts",
                provider="openai_compatible",
                requested_model=tts_config.model,
                status="cache_hit",
                started_at=datetime.now(timezone.utc).isoformat(),
                elapsed_ms=0,
                character_count=len(text),
                from_cache=True,
                cache_key=digest,
            )
            cue.audio_path = audio_path
            cue.audio_duration = max(cue.end - cue.start, 0.2)
            cue.audio_text = text
            cue.status = "audio_done"
            cue.error_message = None
            continue

        if log_callback:
            await log_callback("info", "tts", f"生成配音 {index}/{len(cues)}: {cue.start:.2f}s")

        part_path = audio_path + ".part"

        async def _attempt_cue() -> None:
            try:
                try:
                    os.unlink(part_path)
                except FileNotFoundError:
                    pass
                if provider in {"mock", "silent"}:
                    await create_silent_audio(
                        part_path,
                        max(cue.end - cue.start, 0.2),
                        audio_format="mp3",
                    )
                else:
                    await _generate_openai_tts(text, part_path, tts_config, api_key)
                if not await asyncio.to_thread(_is_valid_audio_file, part_path):
                    raise RuntimeError("TTS 输出未通过 ffprobe 音频校验")
                os.replace(part_path, audio_path)
            except asyncio.CancelledError:
                try:
                    os.unlink(part_path)
                except FileNotFoundError:
                    pass
                raise
            except Exception as exc:
                try:
                    os.unlink(part_path)
                except FileNotFoundError:
                    pass
                raise RetryableExecutionError(f"TTS cue {cue.id} 失败: {exc}") from exc

        try:
            await execute_with_retry(_attempt_cue)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            cue.status = "error"
            cue.error_message = str(exc)
            failures.append((cue.id, exc))
            continue

        _atomic_write_json(metadata_path, {
            "schema": _TRANSLATION_CACHE_SCHEMA,
            "digest": digest,
            "identity": identity,
        })
        cue.audio_path = audio_path
        cue.audio_duration = max(cue.end - cue.start, 0.2)
        cue.audio_text = text
        cue.status = "audio_done"
        cue.error_message = None

    if failures:
        failed_ids = [cue_id for cue_id, _exc in failures]
        raise RuntimeError(f"{len(failures)} 个 TTS cue 重试耗尽: {failed_ids[:5]}；首个错误: {failures[0][1]}")
    return cues


def _is_valid_audio_file(path: str) -> bool:
    if not os.path.exists(path) or os.path.getsize(path) <= 0:
        return False
    process = subprocess.Popen(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_type",
            "-of", "json",
            path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, _stderr = process.communicate()
    if process.returncode != 0:
        return False
    try:
        streams = json.loads(stdout or "{}").get("streams", [])
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return any(stream.get("codec_type") == "audio" for stream in streams)


def _load_tts_cache(metadata_path: Path, audio_path: str, digest: str) -> bool:
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        return (
            payload.get("schema") == _TRANSLATION_CACHE_SCHEMA
            and payload.get("digest") == digest
            and _is_valid_audio_file(audio_path)
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _has_valid_cached_or_legacy_audio(audio_path: str) -> bool:
    """新缓存强校验；无 sidecar 的历史音频保持仅存在性兼容。"""
    if not os.path.exists(audio_path):
        return False
    metadata_path = Path(audio_path + ".json")
    if not metadata_path.exists():
        return True
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        digest = payload.get("digest")
        return bool(digest and _load_tts_cache(metadata_path, audio_path, digest))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


async def _generate_openai_tts(
    text: str,
    audio_path: str,
    tts_config: TTSModelConfig,
    api_key: Optional[str],
) -> None:
    key = resolve_openai_api_key(api_key)
    if not key:
        raise ValueError("缺少 OpenAI API Key，无法生成目标语言语音")

    pricing = manual_pricing_for_feature("tts")
    pricing_source = "manual"
    if pricing is None or not any(value is not None for value in pricing.model_dump().values()):
        pricing = await openrouter_catalog_pricing(
            tts_config.base_url or OPENROUTER_BASE_URL,
            tts_config.model,
            key,
        )
        pricing_source = "catalog"

    def _call() -> None:
        import openai

        kwargs = {"api_key": key}
        if tts_config.base_url:
            kwargs["base_url"] = tts_config.base_url
        client = openai.OpenAI(**kwargs)
        started = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            response = client.audio.speech.create(
                model=tts_config.model,
                voice=tts_config.voice,
                input=text,
                response_format="mp3",
            )
            response.write_to_file(audio_path)
            record_model_call(
                feature="tts",
                provider="openai_compatible",
                requested_model=tts_config.model,
                status="success",
                started_at=started_at,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                pricing=pricing,
                pricing_source=pricing_source,
                character_count=len(text),
            )
        except Exception as exc:
            record_model_call(
                feature="tts",
                provider="openai_compatible",
                requested_model=tts_config.model,
                status="failed",
                error_type=type(exc).__name__,
                started_at=started_at,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                pricing=pricing,
                pricing_source=pricing_source,
                character_count=len(text),
            )
            raise

    await asyncio.to_thread(_call)


async def create_silent_audio(
    output_path: str,
    duration: float,
    audio_format: Optional[str] = None,
) -> None:
    suffixes = {suffix.lower() for suffix in Path(output_path).suffixes}
    resolved_format = audio_format or (
        "m4a" if ".m4a" in suffixes else "aac" if ".aac" in suffixes else "mp3"
    )
    codec = "aac" if resolved_format in {"m4a", "aac"} else "libmp3lame"
    container = "ipod" if resolved_format == "m4a" else "adts" if resolved_format == "aac" else "mp3"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", "anullsrc=r=24000:cl=mono",
        "-t", str(max(duration, 0.2)),
        "-q:a", "9",
        "-acodec", codec,
        "-f", container,
        output_path,
    ]
    await run_subprocess(cmd)


async def compose_aligned_audio_track(
    cues: List[TranslationCue],
    video_duration: float,
    output_path: str,
) -> str:
    validate_stage_translated(cues)
    audio_cues = [cue for cue in cues if cue.audio_path and os.path.exists(cue.audio_path)]
    if not audio_cues:
        await create_silent_audio(output_path, video_duration)
        return output_path

    cmd = ["ffmpeg", "-y"]
    for cue in audio_cues:
        cmd.extend(["-i", cue.audio_path or ""])

    filter_parts = []
    labels = []
    for idx, cue in enumerate(audio_cues):
        delay_ms = max(0, int(round(cue.start * 1000)))
        label = f"a{idx}"
        filter_parts.append(
            f"[{idx}:a]adelay={delay_ms}|{delay_ms},apad,atrim=0:{video_duration}[{label}]"
        )
        labels.append(f"[{label}]")

    if len(labels) == 1:
        filter_parts.append(f"{labels[0]}atrim=0:{video_duration}[outa]")
    else:
        filter_parts.append(
            f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0,"
            f"atrim=0:{video_duration}[outa]"
        )

    cmd.extend([
        "-filter_complex", ";".join(filter_parts),
        "-map", "[outa]",
        "-c:a", "aac",
        output_path,
    ])
    await run_subprocess(cmd)
    return output_path


async def mux_video_with_audio(video_path: str, audio_path: str, output_path: str) -> str:
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        output_path,
    ]
    await run_subprocess(cmd)
    return output_path


async def run_subprocess(cmd: List[str]) -> str:
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    communicate_task = asyncio.create_task(asyncio.to_thread(process.communicate))
    try:
        stdout, stderr = await communicate_task
    except asyncio.CancelledError:
        if process.poll() is None:
            process.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=2.0)
            except (asyncio.TimeoutError, OSError):
                if process.poll() is None:
                    process.kill()
        raise
    if process.returncode != 0:
        raise RuntimeError(stderr or stdout or f"Command failed: {cmd[0]}")
    return stdout or stderr


def resolve_video_duration(video: TranslationVideo) -> float:
    if video.duration and video.duration > 0:
        return video.duration
    info = get_video_info(video.video_path)
    return float(info.get("duration") or 0)
