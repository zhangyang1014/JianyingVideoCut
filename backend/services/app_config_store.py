"""
GoldenClip 应用配置持久化。
配置写入 backend/data/app_config.json（gitignored），供 API 配置页与后端 Key 兜底读取。
"""

import json
import os
import shutil
from pathlib import Path
from typing import Optional

from ..models.app_config import (
    APP_CONFIG_SCHEMA_VERSION,
    AppConfig,
    CloudModelConfig,
    DEFAULT_AUDIT_MODEL,
    DEFAULT_GOLDEN_QUOTE_MODEL,
    DEFAULT_HIGHLIGHT_REEL_MODEL,
    DEFAULT_LEARNING_MODEL,
    DEFAULT_TRANSLATE_MODEL,
    DEFAULT_TTS_MODEL,
    DEFAULT_TTS_VOICE,
    FeatureLlmConfigs,
    FeatureModelConfig,
    LlmRoleRoute,
    LlmRouteCandidate,
    LocalModelConfig,
    OPENROUTER_BASE_URL,
    ROUTERHUB_BASE_URL,
    ProviderProfile,
    ResolvedFeatureLlm,
    ResolvedRoleCandidate,
    TtsFeatureConfig,
    default_llm_role_routes,
    default_provider_profiles,
)

DATA_DIR = Path(__file__).parent.parent / "data"
APP_CONFIG_PATH = DATA_DIR / "app_config.json"

_DEFAULT = AppConfig()
FEATURE_DEFAULT_MODELS = {
    "audit": DEFAULT_AUDIT_MODEL,
    "translate": DEFAULT_TRANSLATE_MODEL,
    "highlight_reel": DEFAULT_HIGHLIGHT_REEL_MODEL,
    "golden_quote": DEFAULT_GOLDEN_QUOTE_MODEL,
    "learning": DEFAULT_LEARNING_MODEL,
    "tts": DEFAULT_TTS_MODEL,
}
_V4_FEATURE_DEFAULTS = {
    "audit": {"qwen/qwen3.7-plus", "openai/gpt-5.1"},
    "translate": {"qwen/qwen3.7-plus"},
    "highlight_reel": {"qwen/qwen3.7-plus"},
    "golden_quote": {
        "deepseek/deepseek-v3.2",
        "deepseek/deepseek-v4-flash",
    },
    "learning": {"qwen/qwen3.7-plus"},
}
_V4_SYSTEM_ROUTE_CANDIDATES = {
    ("routerhub", "qwen/qwen3.7-plus"),
    ("routerhub", "deepseek/deepseek-v4-flash"),
    ("openrouter", "qwen/qwen3.7-plus"),
    ("openrouter", "openai/gpt-5.1"),
    ("openrouter", "deepseek/deepseek-v3.2"),
    ("openrouter", "deepseek/deepseek-v4-flash"),
    ("volcengine-ark", "doubao-seed-2-0-pro-260215"),
    ("zhipu", "glm-4.7"),
    ("zhipu", "glm-4.7-flash"),
    ("zhipu", "glm-5.2"),
}


def _default_cloud_base_url(base_url: Optional[str]) -> str:
    """只为空值补默认地址；用户显式端点必须原样保留。"""
    return base_url or ROUTERHUB_BASE_URL


def _default_feature_model(feature: str, model: Optional[str]) -> str:
    """只为空值补默认模型，不再根据模型名称猜测并静默升级。"""
    return model or FEATURE_DEFAULT_MODELS.get(feature, "")


def _atomic_write_json(path: Path, payload: dict) -> None:
    tmp_path = path.parent / (path.name + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _migrate_legacy_to_feature_llm(raw: dict) -> FeatureLlmConfigs:
    """把旧版全局 provider/model/key 保守迁移为各功能配置。"""
    provider = raw.get("provider", "openai")
    if provider == "claude":
        provider = "openai"
    mode = "local" if provider == "ollama" else "cloud"

    openai_key = raw.get("openai_api_key") or raw.get("claude_api_key") or ""
    ollama_model = raw.get("ollama_model") or "deepseek-r1:14b"
    ollama_base_url = raw.get("ollama_base_url") or "http://localhost:11434"

    legacy_cloud_model = raw.get("openai_model") or raw.get("claude_model") or ""
    legacy_base_url = raw.get("openai_base_url") or ROUTERHUB_BASE_URL
    cloud = CloudModelConfig(
        provider_label=(
            "RouterHub"
            if "routerhub.ai" in legacy_base_url
            else "OpenRouter"
            if "openrouter.ai" in legacy_base_url
            else "兼容 API"
        ),
        base_url=legacy_base_url,
        api_key=openai_key,
        model=legacy_cloud_model or DEFAULT_TRANSLATE_MODEL,
    )
    local = LocalModelConfig(base_url=ollama_base_url, model=ollama_model)

    def _feature(cloud_model: str, local_model: str) -> FeatureModelConfig:
        return FeatureModelConfig(
            mode=mode,
            cloud=cloud.model_copy(update={"model": cloud_model}),
            local=local.model_copy(update={"model": local_model}),
        )

    def _cloud_model(default: str) -> str:
        return legacy_cloud_model or default

    return FeatureLlmConfigs(
        audit=_feature(_cloud_model(DEFAULT_AUDIT_MODEL), "qwen2.5:32b"),
        translate=_feature(_cloud_model(DEFAULT_TRANSLATE_MODEL), "qwen2.5:14b"),
        highlight_reel=_feature(_cloud_model(DEFAULT_HIGHLIGHT_REEL_MODEL), "qwen2.5:32b"),
        golden_quote=_feature(_cloud_model(DEFAULT_GOLDEN_QUOTE_MODEL), "qwen2.5:14b"),
        learning=_feature(_cloud_model(DEFAULT_LEARNING_MODEL), "qwen2.5:32b"),
        tts=TtsFeatureConfig(
            mode="cloud" if mode == "cloud" else "silent",
            cloud=CloudModelConfig(
                provider_label="OpenRouter TTS",
                base_url=OPENROUTER_BASE_URL,
                api_key=openai_key,
                model=DEFAULT_TTS_MODEL,
            ),
            voice=DEFAULT_TTS_VOICE,
        ),
    )


ROLE_TO_FEATURE = {
    "production.plan": "highlight_reel",
    "production.visual": "highlight_reel",
    "audit.semantic": "audit",
    "audit.coherence": "audit",
    "translate.subtitle": "translate",
    "golden_quote.extract": "golden_quote",
    "highlight.strategy": "highlight_reel",
    "highlight.validation": "highlight_reel",
    "learning.analyze": "learning",
    "learning.rewrite": "learning",
    "prompt_feedback.optimize": "learning",
}


def _normalise_feature_llm(raw: dict, merged: dict) -> FeatureLlmConfigs:
    """补齐旧功能配置，但保留所有非空用户值。"""
    if "feature_llm" not in raw or not raw.get("feature_llm"):
        return _migrate_legacy_to_feature_llm(raw)

    global_key = merged.get("openai_api_key") or merged.get("claude_api_key") or ""
    fl = dict(merged["feature_llm"])
    defaults = _DEFAULT.feature_llm.model_dump()
    for key in ("audit", "translate", "highlight_reel", "golden_quote", "learning"):
        feature_cfg = fl.setdefault(key, defaults[key])
        cloud = feature_cfg.setdefault("cloud", {})
        local = feature_cfg.setdefault("local", {})
        if not cloud.get("api_key"):
            cloud["api_key"] = global_key
        cloud["base_url"] = _default_cloud_base_url(cloud.get("base_url"))
        cloud["model"] = _default_feature_model(key, cloud.get("model"))
        local["base_url"] = local.get("base_url") or defaults[key]["local"]["base_url"]
        local["model"] = local.get("model") or defaults[key]["local"]["model"]
    tts = fl.setdefault("tts", defaults["tts"])
    tts_cloud = tts.setdefault("cloud", {})
    if not tts_cloud.get("api_key"):
        tts_cloud["api_key"] = global_key
    tts_cloud["base_url"] = tts_cloud.get("base_url") or OPENROUTER_BASE_URL
    tts_cloud["model"] = _default_feature_model("tts", tts_cloud.get("model"))
    tts["voice"] = tts.get("voice") or DEFAULT_TTS_VOICE
    return FeatureLlmConfigs(**fl)


def _profile_for_feature(
    profiles: list[ProviderProfile],
    feature_cfg: FeatureModelConfig,
) -> str:
    if feature_cfg.mode == "local":
        base_url = feature_cfg.local.base_url.rstrip("/")
        for profile in profiles:
            if profile.provider == "ollama" and profile.base_url.rstrip("/") == base_url:
                return profile.id
        profile_id = f"migrated-ollama-{len(profiles) + 1}"
        profiles.append(ProviderProfile(
            id=profile_id,
            label="迁移的本地 Ollama",
            provider="ollama",
            base_url=feature_cfg.local.base_url,
        ))
        return profile_id

    cloud = feature_cfg.cloud
    base_url = cloud.base_url.rstrip("/")
    for profile in profiles:
        if (
            profile.provider == "openai_compatible"
            and profile.base_url.rstrip("/") == base_url
            and (not profile.api_key or profile.api_key == cloud.api_key)
        ):
            if cloud.api_key and not profile.api_key:
                profile.api_key = cloud.api_key
            return profile.id
    profile_id = f"migrated-cloud-{len(profiles) + 1}"
    profiles.append(ProviderProfile(
        id=profile_id,
        label=cloud.provider_label or "迁移的兼容 API",
        base_url=cloud.base_url,
        api_key=cloud.api_key,
    ))
    return profile_id


def _migrate_role_routing(feature_llm: FeatureLlmConfigs) -> tuple[list[ProviderProfile], dict[str, LlmRoleRoute]]:
    """从功能配置生成角色路由，已有模型保持为首候选。"""
    profiles = [profile.model_copy(deep=True) for profile in default_provider_profiles()]
    recommendations = default_llm_role_routes()
    routes: dict[str, LlmRoleRoute] = {}
    for role, feature in ROLE_TO_FEATURE.items():
        feature_cfg: FeatureModelConfig = getattr(feature_llm, feature)
        profile_id = _profile_for_feature(profiles, feature_cfg)
        inherited_model = (
            feature_cfg.local.model if feature_cfg.mode == "local" else feature_cfg.cloud.model
        )
        candidates = [
            LlmRouteCandidate(
                provider_profile_id=profile_id,
                model=inherited_model,
                tier="primary",
            )
        ]
        seen = {(profile_id, inherited_model)}
        for recommended in recommendations[role].candidates:
            identity = (recommended.provider_profile_id, recommended.model)
            if identity in seen:
                continue
            candidate = recommended.model_copy(deep=True)
            if candidate.tier == "primary":
                candidate.tier = "fallback"
            candidates.append(candidate)
            seen.add(identity)
        route = recommendations[role].model_copy(deep=True)
        route.candidates = candidates
        routes[role] = route
    return profiles, routes


def _promote_routerhub_routing(merged: dict) -> None:
    """v4 一次性加入 RouterHub，并把它提升为每个角色的首选候选。"""
    profiles = [
        ProviderProfile.model_validate(item)
        for item in merged.get("provider_profiles", [])
        if isinstance(item, dict)
    ]
    routerhub = next((item for item in profiles if item.id == "routerhub"), None)
    if routerhub is None:
        routerhub = default_provider_profiles()[0]
    profiles = [routerhub, *[item for item in profiles if item.id != "routerhub"]]
    merged["provider_profiles"] = [profile.model_dump() for profile in profiles]

    defaults = default_llm_role_routes()
    raw_routes = merged.get("llm_role_routes", {})
    promoted: dict[str, dict] = {}
    for role, default_route in defaults.items():
        raw_route = raw_routes.get(role)
        route = (
            LlmRoleRoute.model_validate(raw_route)
            if isinstance(raw_route, dict)
            else default_route.model_copy(deep=True)
        )
        existing = next(
            (
                candidate
                for candidate in route.candidates
                if candidate.provider_profile_id == "routerhub"
            ),
            None,
        )
        primary = (
            existing.model_copy(deep=True)
            if existing
            else default_route.candidates[0].model_copy(deep=True)
        )
        primary.tier = "primary"
        remaining: list[LlmRouteCandidate] = []
        for candidate in route.candidates:
            if candidate is existing:
                continue
            copy = candidate.model_copy(deep=True)
            if copy.tier == "primary":
                copy.tier = "fallback"
            remaining.append(copy)
        route.candidates = [primary, *remaining]
        promoted[role] = route.model_dump()
    merged["llm_role_routes"] = promoted


def _apply_routerhub_quality_presets(merged: dict) -> None:
    """v5 用 RouterHub 质量矩阵替换系统旧预设，保留用户自定义项。"""
    feature_llm = merged.get("feature_llm", {})
    for feature, old_models in _V4_FEATURE_DEFAULTS.items():
        feature_config = feature_llm.get(feature)
        if not isinstance(feature_config, dict):
            continue
        cloud = feature_config.get("cloud")
        if not isinstance(cloud, dict) or cloud.get("model") not in old_models:
            continue
        base_url = str(cloud.get("base_url") or "")
        if "routerhub.ai" not in base_url and "openrouter.ai" not in base_url:
            continue
        was_openrouter = "openrouter.ai" in base_url
        cloud["provider_label"] = "RouterHub"
        cloud["base_url"] = ROUTERHUB_BASE_URL
        cloud["model"] = FEATURE_DEFAULT_MODELS[feature]
        if was_openrouter:
            # 两个平台的 Key 不兼容，不能把旧 OpenRouter Key 静默转交给 RouterHub。
            cloud["api_key"] = ""

    legacy_global_models = set().union(*_V4_FEATURE_DEFAULTS.values())
    if merged.get("openai_model") in legacy_global_models:
        merged["openai_model"] = DEFAULT_TRANSLATE_MODEL

    profiles = [
        ProviderProfile.model_validate(item)
        for item in merged.get("provider_profiles", [])
        if isinstance(item, dict)
    ]
    routerhub = next((profile for profile in profiles if profile.id == "routerhub"), None)
    if routerhub is None:
        routerhub = default_provider_profiles()[0]
    merged["provider_profiles"] = [
        routerhub.model_dump(),
        *[profile.model_dump() for profile in profiles if profile.id != "routerhub"],
    ]

    defaults = default_llm_role_routes()
    raw_routes = merged.get("llm_role_routes", {})
    migrated_routes: dict[str, dict] = {}
    for role, default_route in defaults.items():
        raw_route = raw_routes.get(role)
        if not isinstance(raw_route, dict):
            migrated_routes[role] = default_route.model_dump()
            continue
        route = LlmRoleRoute.model_validate(raw_route)
        has_system_preset = any(
            (candidate.provider_profile_id, candidate.model)
            in _V4_SYSTEM_ROUTE_CANDIDATES
            for candidate in route.candidates
        )
        if not has_system_preset:
            migrated_routes[role] = route.model_dump()
            continue

        candidates: list[LlmRouteCandidate] = []
        inserted_defaults = False
        seen: set[tuple[str, str]] = set()
        for candidate in route.candidates:
            identity = (candidate.provider_profile_id, candidate.model)
            if identity in _V4_SYSTEM_ROUTE_CANDIDATES:
                if inserted_defaults:
                    continue
                for recommended in default_route.candidates:
                    copy = recommended.model_copy(deep=True)
                    if copy.tier == "primary" and any(
                        existing.tier == "primary" for existing in candidates
                    ):
                        copy.tier = "fallback"
                    recommended_identity = (copy.provider_profile_id, copy.model)
                    if recommended_identity not in seen:
                        candidates.append(copy)
                        seen.add(recommended_identity)
                inserted_defaults = True
                continue
            if identity in seen:
                continue
            copy = candidate.model_copy(deep=True)
            if copy.tier == "primary" and candidates:
                copy.tier = "fallback"
            candidates.append(copy)
            seen.add(identity)
        route.candidates = candidates
        migrated_routes[role] = route.model_dump()
    merged["llm_role_routes"] = migrated_routes


def _has_legacy_text_presets(merged: dict) -> bool:
    """检测服务热重载期间已写成 v5、但仍残留的系统文本预设。"""
    feature_llm = merged.get("feature_llm", {})
    for feature, old_models in _V4_FEATURE_DEFAULTS.items():
        feature_config = feature_llm.get(feature)
        cloud = feature_config.get("cloud") if isinstance(feature_config, dict) else None
        if not isinstance(cloud, dict) or cloud.get("model") not in old_models:
            continue
        base_url = str(cloud.get("base_url") or "")
        if "routerhub.ai" in base_url or "openrouter.ai" in base_url:
            return True
    for raw_route in merged.get("llm_role_routes", {}).values():
        if not isinstance(raw_route, dict):
            continue
        for candidate in raw_route.get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            identity = (
                candidate.get("provider_profile_id"),
                candidate.get("model"),
            )
            if identity in _V4_SYSTEM_ROUTE_CANDIDATES:
                return True
    return False


def _migrate_config_payload(raw: dict) -> tuple[dict, bool]:
    """返回规范化配置及是否发生 schema 迁移。"""
    merged = {**_DEFAULT.model_dump(), **raw}
    feature_llm = _normalise_feature_llm(raw, merged)
    merged["feature_llm"] = feature_llm.model_dump()
    version = int(raw.get("schema_version") or 0)
    needs_role_migration = (
        version < 2
        or not raw.get("provider_profiles")
        or not raw.get("llm_role_routes")
    )
    if needs_role_migration:
        profiles, routes = _migrate_role_routing(feature_llm)
        merged["provider_profiles"] = [profile.model_dump() for profile in profiles]
        merged["llm_role_routes"] = {
            role: route.model_dump() for role, route in routes.items()
        }
    needs_routerhub_migration = version < 4
    if needs_routerhub_migration:
        _promote_routerhub_routing(merged)
    needs_quality_preset_migration = (
        version < 5 or _has_legacy_text_presets(merged)
    )
    if needs_quality_preset_migration:
        _apply_routerhub_quality_presets(merged)
    # Add new production roles without replacing any user-configured route.
    routes = merged.setdefault("llm_role_routes", {})
    defaults = default_llm_role_routes()
    needs_production_roles = False
    for role in ("production.plan", "production.visual"):
        if role not in routes:
            routes[role] = defaults[role].model_dump()
            needs_production_roles = True
    merged["schema_version"] = APP_CONFIG_SCHEMA_VERSION
    return merged, (
        needs_role_migration
        or needs_routerhub_migration
        or needs_quality_preset_migration
        or needs_production_roles
        or version < APP_CONFIG_SCHEMA_VERSION
    )


def load_app_config() -> AppConfig:
    """读取配置；旧配置由后端单点、保守且幂等地迁移。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not APP_CONFIG_PATH.exists():
        return AppConfig()
    try:
        with open(APP_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return AppConfig()

        merged, migrated = _migrate_config_payload(raw)
        config = AppConfig(**merged)
        if migrated:
            backup_path = APP_CONFIG_PATH.with_suffix(APP_CONFIG_PATH.suffix + ".bak")
            if not backup_path.exists():
                shutil.copy2(APP_CONFIG_PATH, backup_path)
            _atomic_write_json(APP_CONFIG_PATH, config.model_dump())
        return config
    except Exception as e:
        print(f"[AppConfigStore] 读取配置失败，使用默认值: {e}")
        return AppConfig()


def save_app_config(config: AppConfig) -> AppConfig:
    """校验并原子写入配置文件。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    validated = AppConfig(**config.model_dump())
    _atomic_write_json(APP_CONFIG_PATH, validated.model_dump())
    return validated


def resolve_openai_api_key(request_key: Optional[str] = None) -> str:
    """Key 解析优先级：请求体 > app_config.json > OPENAI_API_KEY 环境变量。"""
    if request_key:
        return request_key
    cfg = load_app_config()
    if cfg.openai_api_key:
        return cfg.openai_api_key
    return os.environ.get("OPENAI_API_KEY") or ""


def resolve_claude_api_key(request_key: Optional[str] = None) -> str:
    """旧接口兼容。"""
    return resolve_openai_api_key(request_key)


def resolve_feature_api_key(
    feature: str,
    request_key: Optional[str] = None,
) -> str:
    """解析某功能的 API Key：请求体 > 功能 cloud.api_key > 全局 openai_api_key > 环境变量。"""
    if request_key:
        return request_key
    cfg = load_app_config()
    feat_cfg = getattr(cfg.feature_llm, feature, None)
    if feat_cfg and hasattr(feat_cfg, "cloud") and feat_cfg.cloud.api_key:
        return feat_cfg.cloud.api_key
    if (
        feat_cfg
        and hasattr(feat_cfg, "cloud")
        and "routerhub.ai" in feat_cfg.cloud.base_url
    ):
        routerhub_key = os.environ.get("ROUTERHUB_API_KEY")
        if routerhub_key:
            return routerhub_key
    return resolve_openai_api_key(None)


def resolve_provider_api_key(profile: ProviderProfile) -> str:
    """供应商档案内 Key 优先，其次读取指定环境变量。"""
    if profile.provider == "ollama":
        return "ollama"
    if profile.api_key:
        return profile.api_key
    if profile.api_key_env:
        value = os.environ.get(profile.api_key_env)
        if value:
            return value
    if profile.id == "openrouter":
        return os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    if profile.id == "routerhub":
        return os.environ.get("ROUTERHUB_API_KEY") or ""
    if profile.id == "zhipu":
        return os.environ.get("ZHIPU_API_KEY") or os.environ.get("ZAI_API_KEY") or ""
    return ""


def resolve_role_candidates(
    role: str,
    *,
    include_escalation: bool = True,
) -> list[ResolvedRoleCandidate]:
    """按配置顺序解析角色候选；禁用、无凭据或无模型候选会被跳过。"""
    cfg = load_app_config()
    route = cfg.llm_role_routes.get(role)
    if not route:
        raise KeyError(f"Unknown LLM role: {role}")
    profiles = {profile.id: profile for profile in cfg.provider_profiles}
    resolved: list[ResolvedRoleCandidate] = []
    for candidate in route.candidates:
        if not candidate.enabled or (candidate.tier == "escalation" and not include_escalation):
            continue
        profile = profiles.get(candidate.provider_profile_id)
        if not profile or not profile.enabled or not candidate.model.strip():
            continue
        api_key = resolve_provider_api_key(profile)
        if profile.provider != "ollama" and not api_key:
            continue
        resolved.append(ResolvedRoleCandidate(
            role=role,
            provider_profile_id=profile.id,
            provider=profile.provider,
            model=candidate.model,
            base_url=profile.base_url.rstrip("/"),
            api_key=api_key,
            tier=candidate.tier,
            temperature=candidate.temperature,
            max_tokens=candidate.max_tokens,
            thinking=candidate.thinking,
            extra_body=candidate.extra_body,
        ))
    return resolved


def role_primary_or_feature(
    role: str,
    feature: str,
    *,
    include_escalation: bool = False,
) -> ResolvedFeatureLlm:
    """兼容旧调用层：优先角色首候选，无可用候选时回退功能配置。"""
    candidates = resolve_role_candidates(role, include_escalation=include_escalation)
    if not candidates:
        return resolve_feature_llm(feature)
    candidate = candidates[0]
    return ResolvedFeatureLlm(
        feature=feature,
        mode="local" if candidate.provider == "ollama" else "cloud",
        provider=candidate.provider,
        model=candidate.model,
        base_url=candidate.base_url,
        api_key=candidate.api_key,
    )


def resolve_feature_llm(
    feature: str,
    *,
    mode_override: Optional[str] = None,
    model_override: Optional[str] = None,
    base_url_override: Optional[str] = None,
    api_key_override: Optional[str] = None,
) -> ResolvedFeatureLlm:
    """
    解析某功能的运行时 LLM 参数。
    feature: audit | translate | highlight_reel | golden_quote | learning | tts
    """
    cfg = load_app_config()
    feat = getattr(cfg.feature_llm, feature)

    if feature == "tts":
        tts: TtsFeatureConfig = feat
        mode = mode_override or tts.mode
        if mode == "silent":
            return ResolvedFeatureLlm(
                feature=feature,
                mode="silent",
                provider="silent",
                model="aligned-silent",
                api_key="",
            )
        key = resolve_feature_api_key(feature, api_key_override)
        return ResolvedFeatureLlm(
            feature=feature,
            mode="cloud",
            provider="openai_compatible",
            model=model_override or tts.cloud.model or DEFAULT_TTS_MODEL,
            base_url=_default_cloud_base_url(base_url_override or tts.cloud.base_url),
            api_key=key,
        )

    fmc: FeatureModelConfig = feat
    mode = mode_override or fmc.mode

    if mode == "local":
        return ResolvedFeatureLlm(
            feature=feature,
            mode="local",
            provider="ollama",
            model=model_override or fmc.local.model or "deepseek-r1:14b",
            base_url=(base_url_override or fmc.local.base_url or "http://localhost:11434").rstrip("/"),
            api_key="ollama",
        )

    key = resolve_feature_api_key(feature, api_key_override)
    return ResolvedFeatureLlm(
        feature=feature,
        mode="cloud",
        provider="openai_compatible",
        model=model_override or fmc.cloud.model or FEATURE_DEFAULT_MODELS.get(feature, DEFAULT_TRANSLATE_MODEL),
        base_url=_default_cloud_base_url(base_url_override or fmc.cloud.base_url).rstrip("/"),
        api_key=key,
    )


def merge_feature_llm_request(
    feature: str,
    *,
    provider: Optional[str] = None,
    openai_model: Optional[str] = None,
    ollama_model: Optional[str] = None,
    ollama_base_url: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> ResolvedFeatureLlm:
    """合并请求参数与持久化功能配置；请求显式传参优先。"""
    stored = resolve_feature_llm(feature)

    explicit = (provider or "").lower()
    if explicit == "ollama":
        return ResolvedFeatureLlm(
            feature=feature,
            mode="local",
            provider="ollama",
            model=ollama_model or stored.model,
            base_url=(ollama_base_url or stored.base_url or "http://localhost:11434").rstrip("/"),
            api_key="ollama",
        )
    if explicit in {"openai", "openrouter", "anthropic", "claude", "openai_compatible"}:
        return ResolvedFeatureLlm(
            feature=feature,
            mode="cloud",
            provider="openai_compatible",
            model=openai_model or stored.model,
            base_url=_default_cloud_base_url(base_url or stored.base_url).rstrip("/"),
            api_key=resolve_feature_api_key(feature, api_key),
        )
    if explicit == "silent":
        return ResolvedFeatureLlm(
            feature=feature,
            mode="silent",
            provider="silent",
            model="aligned-silent",
            api_key="",
        )

    if stored.provider == "ollama" or stored.mode == "local":
        return ResolvedFeatureLlm(
            feature=feature,
            mode="local",
            provider="ollama",
            model=ollama_model or stored.model,
            base_url=(ollama_base_url or stored.base_url or "http://localhost:11434").rstrip("/"),
            api_key="ollama",
        )

    return ResolvedFeatureLlm(
        feature=feature,
        mode="cloud",
        provider="openai_compatible",
        model=openai_model or stored.model,
        base_url=_default_cloud_base_url(base_url or stored.base_url).rstrip("/"),
        api_key=resolve_feature_api_key(feature, api_key),
    )


def resolve_llm_settings(
    provider: Optional[str] = None,
    openai_model: Optional[str] = None,
    ollama_model: Optional[str] = None,
    ollama_base_url: Optional[str] = None,
) -> dict:
    """旧接口兼容：返回 audit 功能的全局等价参数。"""
    mode_override = None
    if provider == "ollama":
        mode_override = "local"
    elif provider in {"openai", "claude"}:
        mode_override = "cloud"

    resolved = resolve_feature_llm(
        "audit",
        mode_override=mode_override,
        model_override=ollama_model if provider == "ollama" else openai_model,
        base_url_override=ollama_base_url if provider == "ollama" else None,
    )
    cfg = load_app_config()
    return {
        "provider": "ollama" if resolved.provider == "ollama" else "openai",
        "openai_model": openai_model or cfg.openai_model or (resolved.model if resolved.mode == "cloud" else DEFAULT_TRANSLATE_MODEL),
        "ollama_model": ollama_model or (resolved.model if resolved.mode == "local" else cfg.ollama_model or "deepseek-r1:14b"),
        "ollama_base_url": ollama_base_url or (resolved.base_url if resolved.mode == "local" else cfg.ollama_base_url or "http://localhost:11434"),
    }
