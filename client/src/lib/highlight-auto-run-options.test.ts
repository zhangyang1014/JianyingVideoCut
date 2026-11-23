import { describe, expect, it } from "vitest";

import type { AppConfig, FeatureModelConfig } from "@/hooks/useAppConfig";
import { buildHighlightAutoRunModelOptions } from "./highlight-auto-run-options";

function createFeature(model: string, overrides: Partial<FeatureModelConfig> = {}): FeatureModelConfig {
  return {
    mode: "cloud",
    cloud: {
      providerLabel: "OpenRouter",
      baseUrl: "https://openrouter.ai/api/v1",
      apiKey: "",
      model,
    },
    local: {
      baseUrl: "http://localhost:11434",
      model: "qwen2.5:14b",
    },
    ...overrides,
  };
}

function createConfig(overrides: Partial<AppConfig> = {}): AppConfig {
  return {
    openaiApiKey: "",
    openaiModel: "fallback-audit-model",
    jianyingDir: "",
    provider: "openai",
    ollamaModel: "fallback-local-model",
    ollamaBaseUrl: "http://localhost:11434",
    featureLlm: {
      audit: createFeature("audit-cloud-model", {
        cloud: {
          providerLabel: "Audit Cloud",
          baseUrl: "https://audit.example/v1",
          apiKey: "audit-key",
          model: "audit-cloud-model",
        },
      }),
      translate: createFeature("translate-model"),
      highlight_reel: createFeature("highlight-quality-model", {
        cloud: {
          providerLabel: "Highlight Cloud",
          baseUrl: "https://highlight.example/v1",
          apiKey: "highlight-key",
          model: "highlight-quality-model",
        },
      }),
      golden_quote: createFeature("golden-quote-model"),
      learning: createFeature("learning-model"),
      tts: {
        mode: "cloud",
        cloud: {
          providerLabel: "OpenRouter TTS",
          baseUrl: "https://openrouter.ai/api/v1",
          apiKey: "",
          model: "tts-model",
        },
        voice: "en_paul_neutral",
      },
    },
    asr: {
      funasrChunkMinutes: 5,
      funasrParallelChunks: 4,
    },
    ...overrides,
  };
}

describe("buildHighlightAutoRunModelOptions", () => {
  it("uses audit cloud config for the auto-run audit request", () => {
    const options = buildHighlightAutoRunModelOptions(createConfig(), "immersive");

    expect(options).toMatchObject({
      provider: "openai",
      openai_api_key: "audit-key",
      openai_model: "audit-cloud-model",
      style_mode: "immersive",
    });
    expect(options.openai_model).not.toBe("highlight-quality-model");
  });

  it("uses audit local config instead of highlight reel quality config", () => {
    const config = createConfig({
      featureLlm: {
        ...createConfig().featureLlm,
        audit: createFeature("audit-cloud-model", {
          mode: "local",
          local: {
            baseUrl: "http://audit-ollama.local",
            model: "audit-local-model",
          },
        }),
        highlight_reel: createFeature("highlight-quality-model"),
      },
    });

    const options = buildHighlightAutoRunModelOptions(config, "compact");

    expect(options).toMatchObject({
      provider: "ollama",
      ollama_model: "audit-local-model",
      ollama_base_url: "http://audit-ollama.local",
      style_mode: "compact",
    });
    expect(options.ollama_model).not.toBe("highlight-quality-model");
  });

  it("maps the configured product count to new and legacy auto-run fields", () => {
    const options = buildHighlightAutoRunModelOptions(createConfig(), "immersive", {
      target_product_count: 6,
      min_reel_duration: 20,
      max_reel_duration: 90,
    });

    expect(options).toMatchObject({
      target_product_count: 6,
      target_reel_count: 6,
    });
  });
});
