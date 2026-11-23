"""全局应用配置模型（供应商档案、任务角色路由、剪映目录等）。"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


FeatureKey = Literal["audit", "translate", "highlight_reel", "golden_quote", "learning", "tts"]
FeatureMode = Literal["cloud", "local"]
RoleKey = Literal[
    "audit.semantic",
    "audit.coherence",
    "translate.subtitle",
    "golden_quote.extract",
    "highlight.strategy",
    "highlight.validation",
    "learning.analyze",
    "learning.rewrite",
    "prompt_feedback.optimize",
]
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
ROUTERHUB_BASE_URL = "https://api.routerhub.ai/v1"
VOLCENGINE_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
APP_CONFIG_SCHEMA_VERSION = 5
ROUTERHUB_GLM_52_MODEL = "z-ai/glm-5.2"
ROUTERHUB_CLAUDE_OPUS_MODEL = "anthropic/claude-opus-4.8"
ROUTERHUB_CLAUDE_SONNET_MODEL = "anthropic/claude-sonnet-4.6"
ROUTERHUB_GPT_56_SOL_MODEL = "openai/gpt-5.6-sol"
ROUTERHUB_GEMINI_PRO_MODEL = "google/gemini-3.1-pro-preview"
DEFAULT_AUDIT_MODEL = ROUTERHUB_GLM_52_MODEL
DEFAULT_TRANSLATE_MODEL = ROUTERHUB_CLAUDE_SONNET_MODEL
DEFAULT_HIGHLIGHT_REEL_MODEL = ROUTERHUB_CLAUDE_OPUS_MODEL
DEFAULT_GOLDEN_QUOTE_MODEL = ROUTERHUB_GLM_52_MODEL
DEFAULT_LEARNING_MODEL = ROUTERHUB_CLAUDE_OPUS_MODEL
DEFAULT_TTS_MODEL = "mistralai/voxtral-mini-tts-2603"
DEFAULT_TTS_VOICE = "en_paul_neutral"


class ModelPricingConfig(BaseModel):
    """Optional manual USD pricing used when the provider omits cost."""

    input_per_million_tokens: Optional[float] = Field(default=None, ge=0)
    output_per_million_tokens: Optional[float] = Field(default=None, ge=0)
    reasoning_per_million_tokens: Optional[float] = Field(default=None, ge=0)
    per_request: Optional[float] = Field(default=None, ge=0)
    per_million_characters: Optional[float] = Field(default=None, ge=0)


class CloudModelConfig(BaseModel):
    provider_label: str = "RouterHub"
    base_url: str = ROUTERHUB_BASE_URL
    api_key: str = ""
    model: str = ""
    pricing: ModelPricingConfig = Field(default_factory=ModelPricingConfig)


class LocalModelConfig(BaseModel):
    base_url: str = "http://localhost:11434"
    model: str = "deepseek-r1:14b"


class FeatureModelConfig(BaseModel):
    mode: FeatureMode = "cloud"
    cloud: CloudModelConfig = Field(default_factory=CloudModelConfig)
    local: LocalModelConfig = Field(default_factory=LocalModelConfig)


class TtsFeatureConfig(BaseModel):
    """TTS 配音配置：云端 OpenAI TTS 或本地静音占位。"""
    mode: Literal["cloud", "silent"] = "cloud"
    cloud: CloudModelConfig = Field(default_factory=lambda: CloudModelConfig(
        provider_label="OpenRouter TTS",
        base_url=OPENROUTER_BASE_URL,
        model=DEFAULT_TTS_MODEL,
    ))
    voice: str = DEFAULT_TTS_VOICE


class FeatureLlmConfigs(BaseModel):
    audit: FeatureModelConfig = Field(default_factory=lambda: FeatureModelConfig(
        cloud=CloudModelConfig(provider_label="RouterHub", model=DEFAULT_AUDIT_MODEL),
        local=LocalModelConfig(model="qwen2.5:32b"),
    ))
    translate: FeatureModelConfig = Field(default_factory=lambda: FeatureModelConfig(
        cloud=CloudModelConfig(provider_label="RouterHub", model=DEFAULT_TRANSLATE_MODEL),
        local=LocalModelConfig(model="qwen2.5:14b"),
    ))
    highlight_reel: FeatureModelConfig = Field(default_factory=lambda: FeatureModelConfig(
        cloud=CloudModelConfig(provider_label="RouterHub", model=DEFAULT_HIGHLIGHT_REEL_MODEL),
        local=LocalModelConfig(model="qwen2.5:32b"),
    ))
    golden_quote: FeatureModelConfig = Field(default_factory=lambda: FeatureModelConfig(
        cloud=CloudModelConfig(provider_label="RouterHub", model=DEFAULT_GOLDEN_QUOTE_MODEL),
        local=LocalModelConfig(model="qwen2.5:14b"),
    ))
    learning: FeatureModelConfig = Field(default_factory=lambda: FeatureModelConfig(
        cloud=CloudModelConfig(provider_label="RouterHub", model=DEFAULT_LEARNING_MODEL),
        local=LocalModelConfig(model="qwen2.5:32b"),
    ))
    tts: TtsFeatureConfig = Field(default_factory=TtsFeatureConfig)


class ProviderProfile(BaseModel):
    """可复用的模型供应商凭据档案。"""

    id: str
    label: str
    provider: Literal["openai_compatible", "ollama"] = "openai_compatible"
    base_url: str
    api_key: str = ""
    api_key_env: str = ""
    enabled: bool = True


class LlmRouteCandidate(BaseModel):
    """任务角色的一个有序模型候选。"""

    provider_profile_id: str
    model: str
    tier: Literal["primary", "fallback", "escalation"] = "fallback"
    enabled: bool = True
    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    thinking: Literal["auto", "enabled", "disabled"] = "auto"
    extra_body: dict[str, Any] = Field(default_factory=dict)
    pricing: ModelPricingConfig = Field(default_factory=ModelPricingConfig)


class LlmRoleRoute(BaseModel):
    """某个任务角色的候选顺序和升级策略。"""

    label: str
    purpose: str
    candidates: list[LlmRouteCandidate] = Field(default_factory=list)
    quality_gate_escalation: bool = False


def default_provider_profiles() -> list[ProviderProfile]:
    return [
        ProviderProfile(
            id="routerhub",
            label="RouterHub",
            base_url=ROUTERHUB_BASE_URL,
            api_key_env="ROUTERHUB_API_KEY",
        ),
        ProviderProfile(
            id="volcengine-ark",
            label="火山方舟",
            base_url=VOLCENGINE_ARK_BASE_URL,
            api_key_env="VOLCENGINE_ARK_API_KEY",
        ),
        ProviderProfile(
            id="zhipu",
            label="智谱开放平台",
            base_url=ZHIPU_BASE_URL,
            api_key_env="ZHIPU_API_KEY",
        ),
        ProviderProfile(
            id="openrouter",
            label="OpenRouter",
            base_url=OPENROUTER_BASE_URL,
            api_key_env="OPENROUTER_API_KEY",
        ),
        ProviderProfile(
            id="ollama-local",
            label="本地 Ollama",
            provider="ollama",
            base_url="http://localhost:11434",
        ),
    ]


def _candidate(
    profile: str,
    model: str,
    tier: Literal["primary", "fallback", "escalation"],
    *,
    reasoning_effort: Optional[Literal["medium", "high"]] = None,
) -> LlmRouteCandidate:
    return LlmRouteCandidate(
        provider_profile_id=profile,
        model=model,
        tier=tier,
        extra_body=(
            {"reasoning": {"effort": reasoning_effort}}
            if reasoning_effort
            else {}
        ),
    )


def default_llm_role_routes() -> dict[str, LlmRoleRoute]:
    """GoldenClip 默认任务角色矩阵；无凭据的候选会在运行时跳过。"""

    def routerhub(
        model: str,
        tier: Literal["primary", "fallback", "escalation"],
        reasoning_effort: Optional[Literal["medium", "high"]] = None,
    ) -> LlmRouteCandidate:
        return _candidate(
            "routerhub",
            model,
            tier,
            reasoning_effort=reasoning_effort,
        )

    return {
        "audit.semantic": LlmRoleRoute(
            label="语义审计",
            purpose="复杂语义、多约束片段判定",
            candidates=[
                routerhub(ROUTERHUB_GLM_52_MODEL, "primary", "high"),
                routerhub(ROUTERHUB_CLAUDE_OPUS_MODEL, "fallback", "high"),
                routerhub(ROUTERHUB_GPT_56_SOL_MODEL, "escalation", "high"),
            ],
            quality_gate_escalation=True,
        ),
        "audit.coherence": LlmRoleRoute(
            label="连贯性复核",
            purpose="独立检查上下文与修复建议",
            candidates=[
                routerhub(ROUTERHUB_GPT_56_SOL_MODEL, "primary", "high"),
                routerhub(ROUTERHUB_CLAUDE_OPUS_MODEL, "fallback", "high"),
                routerhub(ROUTERHUB_GEMINI_PRO_MODEL, "escalation", "high"),
            ],
            quality_gate_escalation=True,
        ),
        "translate.subtitle": LlmRoleRoute(
            label="字幕翻译",
            purpose="高频字幕校准与翻译",
            candidates=[
                routerhub(ROUTERHUB_CLAUDE_SONNET_MODEL, "primary"),
                routerhub(ROUTERHUB_GEMINI_PRO_MODEL, "fallback"),
            ],
        ),
        "golden_quote.extract": LlmRoleRoute(
            label="金句提取",
            purpose="中文表达判断与钩子候选抽取",
            candidates=[
                routerhub(ROUTERHUB_GLM_52_MODEL, "primary", "medium"),
                routerhub(ROUTERHUB_CLAUDE_SONNET_MODEL, "fallback", "medium"),
                routerhub(ROUTERHUB_GPT_56_SOL_MODEL, "escalation", "medium"),
            ],
        ),
        "highlight.strategy": LlmRoleRoute(
            label="集锦策略生成",
            purpose="复杂片段选择与叙事编排",
            candidates=[
                routerhub(ROUTERHUB_CLAUDE_OPUS_MODEL, "primary", "high"),
                routerhub(ROUTERHUB_GPT_56_SOL_MODEL, "fallback", "high"),
                routerhub(ROUTERHUB_GEMINI_PRO_MODEL, "escalation", "high"),
            ],
            quality_gate_escalation=True,
        ),
        "highlight.validation": LlmRoleRoute(
            label="集锦独立质检",
            purpose="跨模型验收事实与结构完整性",
            candidates=[
                routerhub(ROUTERHUB_GPT_56_SOL_MODEL, "primary", "high"),
                routerhub(ROUTERHUB_GEMINI_PRO_MODEL, "fallback", "high"),
                routerhub(ROUTERHUB_CLAUDE_OPUS_MODEL, "escalation", "high"),
            ],
            quality_gate_escalation=True,
        ),
        "learning.analyze": LlmRoleRoute(
            label="学习分析",
            purpose="长上下文证据归因与规则提炼",
            candidates=[
                routerhub(ROUTERHUB_CLAUDE_OPUS_MODEL, "primary", "high"),
                routerhub(ROUTERHUB_GPT_56_SOL_MODEL, "fallback", "high"),
                routerhub(ROUTERHUB_GEMINI_PRO_MODEL, "escalation", "high"),
            ],
            quality_gate_escalation=True,
        ),
        "learning.rewrite": LlmRoleRoute(
            label="Prompt 改写",
            purpose="自然中文与规则块重写",
            candidates=[
                routerhub(ROUTERHUB_GLM_52_MODEL, "primary", "medium"),
                routerhub(ROUTERHUB_CLAUDE_SONNET_MODEL, "fallback", "medium"),
                routerhub(ROUTERHUB_GPT_56_SOL_MODEL, "escalation", "medium"),
            ],
        ),
        "production.plan": LlmRoleRoute(
            label="卖货视频创意",
            purpose="读取品牌资料与页面视觉，生成可追溯创意和分镜",
            candidates=[routerhub(ROUTERHUB_CLAUDE_OPUS_MODEL, "primary", "high")],
        ),
        "production.visual": LlmRoleRoute(
            label="卖货素材检查",
            purpose="对照产品、主播参考与镜头要求检查实际画面和语音",
            candidates=[routerhub(ROUTERHUB_CLAUDE_OPUS_MODEL, "primary", "high")],
        ),
        "prompt_feedback.optimize": LlmRoleRoute(
            label="反馈优化",
            purpose="根据人工修正优化中文 Prompt",
            candidates=[
                routerhub(ROUTERHUB_GLM_52_MODEL, "primary", "medium"),
                routerhub(ROUTERHUB_CLAUDE_SONNET_MODEL, "fallback", "medium"),
                routerhub(ROUTERHUB_GPT_56_SOL_MODEL, "escalation", "medium"),
            ],
        ),
    }


class AsrRuntimeConfig(BaseModel):
    """本地 ASR 运行配置。"""
    funasr_chunk_minutes: float = Field(default=5.0, ge=1.0, le=15.0)
    funasr_parallel_chunks: int = Field(default=4, ge=1, le=8)


class AuditRuntimeConfig(BaseModel):
    """云端语义审计运行配置。"""
    cloud_chunk_segments: int = Field(default=120, ge=40, le=150)
    cloud_max_parallel_chunks: int = Field(default=8, ge=1, le=16)
    cloud_initial_parallel_chunks: int = Field(default=8, ge=1, le=16)
    cloud_chunk_token_budget: int = Field(default=24000, ge=8000, le=120000)
    enable_batch_resume: bool = True
    enable_incremental_cache: bool = True
    coherence_strategy: Literal["always", "auto", "local_first"] = "auto"


class HighlightRuntimeConfig(BaseModel):
    """云端产品集锦生成与验收性能配置。"""
    cloud_max_parallel_calls: int = Field(default=3, ge=1, le=8)
    max_model_calls_per_run: int = Field(default=120, ge=6, le=1000)


class AppConfig(BaseModel):
    schema_version: int = APP_CONFIG_SCHEMA_VERSION
    # 旧版本字段：保留以兼容已存在的 app_config.json
    claude_api_key: str = ""
    openai_api_key: str = ""
    claude_model: str = ""
    openai_model: str = DEFAULT_TRANSLATE_MODEL
    jianying_dir: str = ""
    provider: Literal["openai", "ollama", "claude"] = "openai"
    ollama_model: str = "deepseek-r1:14b"
    ollama_base_url: str = Field(default="http://localhost:11434")

    # 按功能划分的 LLM 配置
    feature_llm: FeatureLlmConfigs = Field(default_factory=FeatureLlmConfigs)

    # 新版：供应商凭据复用 + 按任务角色路由
    provider_profiles: list[ProviderProfile] = Field(default_factory=default_provider_profiles)
    llm_role_routes: dict[str, LlmRoleRoute] = Field(default_factory=default_llm_role_routes)

    # 本地 ASR 性能配置
    asr: AsrRuntimeConfig = Field(default_factory=AsrRuntimeConfig)

    # 云端语义审计性能配置
    audit_runtime: AuditRuntimeConfig = Field(default_factory=AuditRuntimeConfig)

    # 云端产品集锦生成与验收性能配置
    highlight_runtime: HighlightRuntimeConfig = Field(default_factory=HighlightRuntimeConfig)


class ResolvedFeatureLlm(BaseModel):
    """运行时解析后的功能 LLM 参数。"""
    feature: str
    mode: str
    provider: str  # "openai_compatible" | "ollama" | "silent"
    model: str
    base_url: Optional[str] = None
    api_key: str = ""


class ResolvedRoleCandidate(BaseModel):
    """运行时解析后的单个角色候选，不向日志暴露密钥。"""

    role: str
    provider_profile_id: str
    provider: Literal["openai_compatible", "ollama"]
    model: str
    base_url: str
    api_key: str = ""
    tier: Literal["primary", "fallback", "escalation"]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    thinking: Literal["auto", "enabled", "disabled"] = "auto"
    extra_body: dict[str, Any] = Field(default_factory=dict)
