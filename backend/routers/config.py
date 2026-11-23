"""健康检查、Ollama 模型查询、编辑美学/风格/词典配置、场景提示词 CRUD。"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..core.paths import BASE_DIR
from ..models.app_config import AppConfig, ROUTERHUB_BASE_URL
from ..services.app_config_store import (
    load_app_config,
    save_app_config,
    resolve_feature_api_key,
    resolve_provider_api_key,
)
from ..services.ffmpeg_executor import check_ffmpeg_available
from ..services.provider_pricing import (
    ROUTERHUB_PROFILE_ID,
    get_provider_pricing_catalog,
    list_provider_pricing_statuses,
    sync_routerhub_pricing,
)
from ..services.prompt_registry import (
    list_prompt_payloads,
    prompt_payload,
    resolve_prompt_content,
    save_prompt_content,
)

router = APIRouter()
_MODEL_CATALOG_CACHE: dict[tuple[str, bool], tuple[float, dict]] = {}
_MODEL_CATALOG_TTL_SECONDS = 600


class LlmTestRequest(BaseModel):
    feature: str = "audit"
    provider_profile_id: Optional[str] = None
    base_url: str = ROUTERHUB_BASE_URL
    api_key: Optional[str] = None
    model: str
    voice: Optional[str] = None
    kind: str = "chat"  # "chat" | "tts"


def _split_openrouter_model(model: str) -> tuple[str, str]:
    if "/" not in model:
        raise HTTPException(400, "OpenRouter 模型名需要使用完整 slug，例如 qwen/qwen3.7-plus")
    author, slug = model.split("/", 1)
    if not author or not slug:
        raise HTTPException(400, "OpenRouter 模型名格式无效")
    return author, slug


@router.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "ffmpeg": check_ffmpeg_available(),
        "version": "3.0.0"
    }


@router.get("/api/ollama/models")
async def get_ollama_models(base_url: str = "http://localhost:11434"):
    """
    查询本地 Ollama 已安装的模型列表。
    前端在配置页切换到 Ollama 时调用，用于动态展示可选模型。
    """
    import httpx
    url = base_url.rstrip("/") + "/api/tags"
    try:
        # 使用 AsyncHTTPTransport(proxy=None) 绕过系统代理（VPN/ClashX），确保直连本地 Ollama
        transport = httpx.AsyncHTTPTransport(proxy=None)
        async with httpx.AsyncClient(transport=transport, timeout=5.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            # Ollama /api/tags 返回 {"models": [{"name": "...", ...}, ...]}
            models = [m["name"] for m in data.get("models", [])]
            return {"models": models, "online": True}
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"models": [], "online": False, "error": str(e)}
        )


# ============================================================
# Config Management
# ============================================================

@router.get("/api/config/editing-aesthetic")
async def get_editing_aesthetic():
    config_path = BASE_DIR / "Editing_Aesthetic.md"
    if config_path.exists():
        return {"content": config_path.read_text(encoding="utf-8")}
    return {"content": ""}


@router.put("/api/config/editing-aesthetic")
async def update_editing_aesthetic(body: dict):
    config_path = BASE_DIR / "Editing_Aesthetic.md"
    content = body.get("content", "")
    config_path.write_text(content, encoding="utf-8")
    return {"success": True}


@router.get("/api/config/user-style")
async def get_user_style():
    """获取个人剪辑风格档案。"""
    path = BASE_DIR / "backend" / "data" / "user_style.md"
    if path.exists():
        return {"content": path.read_text(encoding="utf-8")}
    return {"content": ""}


@router.put("/api/config/user-style")
async def update_user_style_api(body: dict):
    """更新个人剪辑风格档案。"""
    path = BASE_DIR / "backend" / "data" / "user_style.md"
    content = body.get("content", "")
    path.write_text(content, encoding="utf-8")
    return {"success": True}


@router.get("/api/config/glossary")
async def get_glossary():
    """获取专业术语词典内容。"""
    glossary_path = BASE_DIR / "backend" / "data" / "glossary.txt"
    if glossary_path.exists():
        return {"content": glossary_path.read_text(encoding="utf-8")}
    return {"content": ""}


@router.put("/api/config/glossary")
async def update_glossary(body: dict):
    """保存专业术语词典内容。"""
    glossary_path = BASE_DIR / "backend" / "data" / "glossary.txt"
    content = body.get("content", "")
    glossary_path.write_text(content, encoding="utf-8")
    return {"success": True}


@router.get("/api/config/app")
async def get_app_config_api():
    """获取全局应用配置（API Key、模型、剪映目录等）。"""
    return load_app_config().model_dump()


@router.put("/api/config/app")
async def update_app_config_api(body: dict):
    """保存全局应用配置到 backend/data/app_config.json。"""
    current = load_app_config().model_dump()
    merged = {**current, **body}
    saved = save_app_config(AppConfig(**merged))
    return {"success": True, "config": saved.model_dump()}


@router.get("/api/config/provider-pricing")
async def get_provider_pricing_statuses():
    """返回已登记的供应商报价版本和同步时间。"""
    return {"providers": list_provider_pricing_statuses()}


@router.post("/api/config/provider-pricing/{provider_profile_id}/sync")
async def sync_provider_pricing(provider_profile_id: str):
    """从供应商公开模型页同步最新 USD 报价。"""
    if provider_profile_id != ROUTERHUB_PROFILE_ID:
        raise HTTPException(400, "当前只支持同步 RouterHub 公开报价")
    try:
        catalog = await sync_routerhub_pricing()
    except Exception as exc:
        raise HTTPException(502, f"同步 RouterHub 报价失败: {exc}") from exc
    _MODEL_CATALOG_CACHE.clear()
    return {
        "success": True,
        "provider": catalog.status_payload(),
    }


@router.get("/api/config/llm/models")
async def list_llm_models(
    feature: str = "audit",
    base_url: str = ROUTERHUB_BASE_URL,
    provider_profile_id: Optional[str] = None,
    refresh: bool = False,
):
    """从当前 OpenAI-compatible 服务读取用户实际可选的模型目录。"""
    import httpx
    import time

    profile = None
    if provider_profile_id:
        profile = next(
            (item for item in load_app_config().provider_profiles if item.id == provider_profile_id),
            None,
        )
        if not profile:
            raise HTTPException(404, "供应商档案不存在")
    endpoint = ((profile.base_url if profile else base_url) or ROUTERHUB_BASE_URL).rstrip("/")
    cache_key = (endpoint, feature == "tts")
    cached = _MODEL_CATALOG_CACHE.get(cache_key)
    if not refresh and cached and time.monotonic() - cached[0] < _MODEL_CATALOG_TTL_SECONDS:
        return cached[1]

    headers = {}
    api_key = resolve_provider_api_key(profile) if profile else resolve_feature_api_key(feature)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        transport = httpx.AsyncHTTPTransport(proxy=None)
        async with httpx.AsyncClient(transport=transport, timeout=15.0) as client:
            response = await client.get(f"{endpoint}/models", headers=headers)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取模型目录失败: {exc}")

    raw_models = payload.get("data", payload.get("models", [])) if isinstance(payload, dict) else []
    pricing_profile_id = (
        profile.id
        if profile
        else ROUTERHUB_PROFILE_ID
        if "routerhub.ai" in endpoint
        else None
    )
    registered_catalog = (
        get_provider_pricing_catalog(pricing_profile_id)
        if pricing_profile_id
        else None
    )
    models = []
    for raw in raw_models:
        if not isinstance(raw, dict):
            continue
        model_id = str(raw.get("id") or raw.get("name") or "").strip()
        if not model_id:
            continue
        architecture = raw.get("architecture") if isinstance(raw.get("architecture"), dict) else {}
        input_modalities = architecture.get("input_modalities") or []
        output_modalities = architecture.get("output_modalities") or []
        if feature == "tts":
            if output_modalities and "audio" not in output_modalities:
                continue
        elif output_modalities and "text" not in output_modalities:
            continue

        pricing = raw.get("pricing") if isinstance(raw.get("pricing"), dict) else {}
        registered_pricing = (
            registered_catalog.models.get(model_id)
            if registered_catalog
            else None
        )
        prompt_price = pricing.get("prompt")
        completion_price = pricing.get("completion")
        if registered_pricing:
            if (
                prompt_price is None
                and registered_pricing.input_per_million_tokens is not None
            ):
                prompt_price = str(
                    registered_pricing.input_per_million_tokens / 1_000_000
                )
            if (
                completion_price is None
                and registered_pricing.output_per_million_tokens is not None
            ):
                completion_price = str(
                    registered_pricing.output_per_million_tokens / 1_000_000
                )
        models.append({
            "id": model_id,
            "name": str(raw.get("name") or model_id),
            "description": str(raw.get("description") or ""),
            "context_length": raw.get("context_length"),
            "input_modalities": input_modalities,
            "output_modalities": output_modalities,
            "pricing": {
                "prompt": prompt_price,
                "completion": completion_price,
            },
        })

    models.sort(key=lambda item: (item["name"].lower(), item["id"].lower()))
    result = {"models": models, "count": len(models), "source": endpoint}
    _MODEL_CATALOG_CACHE[cache_key] = (time.monotonic(), result)
    return result


@router.post("/api/config/llm/test")
async def test_llm_model(req: LlmTestRequest):
    """
    检查 OpenRouter 模型状态并执行最小真实试跑。
    Chat 模型使用 max_tokens=1；TTS 模型请求极短 ping 音频但不保存。
    """
    import httpx

    profile = None
    if req.provider_profile_id:
        profile = next(
            (item for item in load_app_config().provider_profiles if item.id == req.provider_profile_id),
            None,
        )
        if not profile:
            raise HTTPException(404, "供应商档案不存在")
    base_url = ((profile.base_url if profile else req.base_url) or ROUTERHUB_BASE_URL).rstrip("/")
    api_key = req.api_key or (
        resolve_provider_api_key(profile)
        if profile
        else resolve_feature_api_key(req.feature)
    )
    if not api_key:
        raise HTTPException(400, "缺少 API Key，无法试跑模型")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-OpenRouter-Title": "GoldenClip Config Test",
    }
    transport = httpx.AsyncHTTPTransport(proxy=None)

    result: dict = {
        "success": False,
        "model": req.model,
        "kind": req.kind,
        "checks": {},
    }

    try:
        async with httpx.AsyncClient(transport=transport, timeout=20.0) as client:
            if "openrouter.ai" in base_url:
                author, slug = _split_openrouter_model(req.model)
                key_resp = await client.get(f"{base_url}/auth/key", headers=headers)
                result["checks"]["auth"] = {
                    "ok": key_resp.status_code == 200,
                    "status": key_resp.status_code,
                }
                model_resp = await client.get(f"{base_url}/model/{author}/{slug}", headers=headers)
                result["checks"]["model"] = {
                    "ok": model_resp.status_code == 200,
                    "status": model_resp.status_code,
                }
            else:
                result["checks"]["auth"] = {"ok": True, "status": "trial"}
                result["checks"]["model"] = {"ok": True, "status": "trial"}

            if req.kind == "tts":
                tts_resp = await client.post(
                    f"{base_url}/audio/speech",
                    headers=headers,
                    json={
                        "model": req.model,
                        "input": "ping",
                        "voice": req.voice or "alloy",
                        "response_format": "mp3",
                    },
                )
                result["checks"]["trial"] = {
                    "ok": tts_resp.status_code == 200,
                    "status": tts_resp.status_code,
                    "bytes": len(tts_resp.content) if tts_resp.status_code == 200 else 0,
                }
                if tts_resp.status_code != 200:
                    result["checks"]["trial"]["error"] = tts_resp.text[:500]
            else:
                chat_resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": req.model,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 1,
                        "temperature": 0,
                    },
                )
                result["checks"]["trial"] = {
                    "ok": chat_resp.status_code == 200,
                    "status": chat_resp.status_code,
                }
                if chat_resp.status_code == 200:
                    data = chat_resp.json()
                    result["checks"]["trial"]["usage"] = data.get("usage")
                    result["checks"]["trial"]["generation_id"] = data.get("id")
                else:
                    result["checks"]["trial"]["error"] = chat_resp.text[:500]

    except httpx.TimeoutException:
        raise HTTPException(504, "模型试跑超时")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"模型试跑失败: {str(e)}")

    result["success"] = bool(result["checks"].get("trial", {}).get("ok"))
    return result


# ============================================================
# Prompt Management API
# ============================================================

PROMPTS_DIR = BASE_DIR / "backend" / "prompts"

SCENARIO_PROMPT_IDS = {
    "monologue_clean": "audit.monologue_clean",
    "interview_compress": "audit.interview_compress",
    "highlight_reel": "audit.highlight_reel",
}


@router.get("/api/config/prompts")
async def list_config_prompts(feature: Optional[str] = None):
    """列出某功能实际运行时可用的 system prompt。"""
    return {"prompts": list_prompt_payloads(feature)}


@router.get("/api/config/prompts/{prompt_id}")
async def get_config_prompt(prompt_id: str):
    try:
        return prompt_payload(prompt_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown prompt: {prompt_id}")


@router.put("/api/config/prompts/{prompt_id}")
async def update_config_prompt(prompt_id: str, body: dict):
    content = body.get("content")
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(status_code=400, detail="Prompt content cannot be empty")
    try:
        return {"success": True, "prompt": save_prompt_content(prompt_id, content)}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown prompt: {prompt_id}")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@router.get("/api/prompts/{scenario}")
async def get_prompt(scenario: str):
    """旧版场景接口；也解析到当前实际激活的 Prompt。"""
    prompt_id = SCENARIO_PROMPT_IDS.get(scenario)
    if not prompt_id:
        raise HTTPException(status_code=404, detail=f"Unknown scenario: {scenario}")
    content = resolve_prompt_content(prompt_id)
    rules = _parse_rules_from_prompt(content)
    return {"content": content, "rules": rules}


@router.put("/api/prompts/{scenario}")
async def update_prompt(scenario: str, body: dict):
    """旧版场景保存接口；写入当前实际激活的 Prompt。"""
    prompt_id = SCENARIO_PROMPT_IDS.get(scenario)
    if not prompt_id:
        raise HTTPException(status_code=404, detail=f"Unknown scenario: {scenario}")
    content = body.get("content", "")
    save_prompt_content(prompt_id, content)
    rules = _parse_rules_from_prompt(content)
    return {"success": True, "rules": rules}


@router.get("/api/prompts/{scenario}/rules")
async def get_rules(scenario: str):
    """Get parsed rules from a scenario prompt."""
    prompt_id = SCENARIO_PROMPT_IDS.get(scenario)
    if not prompt_id:
        raise HTTPException(status_code=404, detail=f"Unknown scenario: {scenario}")
    content = resolve_prompt_content(prompt_id)
    return {"rules": _parse_rules_from_prompt(content)}


@router.put("/api/prompts/{scenario}/rules")
async def update_rules(scenario: str, body: dict):
    """
    Receive updated rules list and patch them back into the prompt file.
    Each rule has: {code, name, priority, desc, logic, example}
    """
    prompt_id = SCENARIO_PROMPT_IDS.get(scenario)
    if not prompt_id:
        raise HTTPException(status_code=404, detail=f"Unknown scenario: {scenario}")

    rules = body.get("rules", [])
    content = resolve_prompt_content(prompt_id)
    content = _patch_rules_into_prompt(content, rules)
    save_prompt_content(prompt_id, content)
    return {"success": True, "rules": rules}


def _parse_rules_from_prompt(content: str) -> list:
    """
    Parse structured rules from prompt markdown.
    Looks for patterns like: ### 【P1】重说识别 ★★★ ...
    Returns list of {code, name, priority, desc, logic, full_text}
    """
    import re
    rules = []
    # Match rule headers: ### 【CODE】Name ★...
    pattern = re.compile(
        r'###\s+【([A-Z0-9-]+)】([^\n★]+)([★☆]*)([^\n]*)\n(.*?)(?=###\s+【|## |\Z)',
        re.DOTALL
    )
    for m in pattern.finditer(content):
        code = m.group(1).strip()
        name = m.group(2).strip()
        stars = m.group(3).strip()
        priority_note = m.group(4).strip()
        body = m.group(5).strip()

        # Extract first paragraph as short desc
        lines = [l.strip() for l in body.split('\n') if l.strip()]
        desc = lines[0] if lines else ""
        # Remove bold markers
        desc = re.sub(r'\*\*([^*]+)\*\*', r'\1', desc)
        if desc.startswith('**') or desc.startswith('触发') or desc.startswith('执行'):
            desc = lines[1] if len(lines) > 1 else desc

        priority = len([c for c in stars if c == '★'])

        rules.append({
            "code": code,
            "name": name,
            "priority": priority,
            "priority_note": priority_note.strip('() '),
            "stars": stars,
            "desc": desc[:80] if desc else "",
            "full_text": m.group(0).strip(),
        })
    return rules


def _patch_rules_into_prompt(content: str, rules: list) -> str:
    """
    Replace individual rule sections in the prompt with updated versions.
    Only updates rules that have a 'full_text' field provided.
    """
    import re
    for rule in rules:
        code = rule.get("code")
        new_text = rule.get("full_text", "").strip()
        if not code or not new_text:
            continue
        # Replace the old rule block with the new one
        pattern = re.compile(
            r'###\s+【' + re.escape(code) + r'】.*?(?=###\s+【|## |\Z)',
            re.DOTALL
        )
        replacement = new_text + "\n\n"
        new_content = pattern.sub(replacement, content)
        if new_content != content:
            content = new_content
    return content
