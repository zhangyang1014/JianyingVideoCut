import type { AppConfig } from "@/hooks/useAppConfig";
import { buildFeatureRequestOptions } from "@/hooks/useAppConfig";
import type { HighlightReelAutoRunRequest, HighlightReelBuildOptions } from "@/lib/api";
import { normalizeHighlightBuildOptions } from "@/lib/highlight-reel-action";

export function buildHighlightAutoRunModelOptions(
  config: AppConfig,
  styleMode: string,
  buildOptions?: HighlightReelBuildOptions,
): HighlightReelAutoRunRequest {
  const auditOpts = buildFeatureRequestOptions(config, "audit");
  return {
    provider: auditOpts.provider === "ollama" ? "ollama" : "openai",
    openai_api_key: auditOpts.openai_api_key,
    openai_model: auditOpts.openai_model,
    ollama_model: auditOpts.ollama_model,
    ollama_base_url: auditOpts.ollama_base_url,
    style_mode: styleMode,
    ...(buildOptions ? normalizeHighlightBuildOptions(buildOptions) : {}),
  };
}
