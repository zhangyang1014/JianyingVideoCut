import { describe, expect, it } from "vitest";
import type { AppConfigPayload } from "@/lib/api";
import {
  appConfigFromPayload,
  appConfigToPayload,
  DEFAULT_AUDIT_MODEL,
  DEFAULT_HIGHLIGHT_REEL_MODEL,
  DEFAULT_LLM_ROLE_ROUTES,
  DEFAULT_TRANSLATE_MODEL,
} from "./useAppConfig";

const basePayload = (): AppConfigPayload => ({
  schema_version: 2,
  openai_api_key: "",
  jianying_dir: "",
  provider: "openai",
  ollama_model: "qwen2.5:14b",
  ollama_base_url: "http://localhost:11434",
});

describe("useAppConfig role routing serialization", () => {
  it("uses RouterHub quality presets while keeping OpenRouter for TTS", () => {
    const config = appConfigFromPayload(basePayload());

    expect(config.featureLlm.audit.cloud.model).toBe(DEFAULT_AUDIT_MODEL);
    expect(config.featureLlm.highlight_reel.cloud.model).toBe(DEFAULT_HIGHLIGHT_REEL_MODEL);
    expect(config.featureLlm.translate.cloud.model).toBe(DEFAULT_TRANSLATE_MODEL);
    expect(config.featureLlm.tts.cloud).toMatchObject({
      providerLabel: "OpenRouter TTS",
      baseUrl: "https://openrouter.ai/api/v1",
    });
    expect(config.llmRoleRoutes["audit.coherence"].candidates[0].model).toBe(
      "openai/gpt-5.6-sol",
    );
    expect(config.llmRoleRoutes["highlight.strategy"].candidates[0].model).toBe(
      "anthropic/claude-opus-4.8",
    );
    expect(
      Object.values(config.llmRoleRoutes)
        .flatMap((route) => route.candidates)
        .every((candidate) => candidate.providerProfileId === "routerhub"),
    ).toBe(true);
    expect(
      config.llmRoleRoutes["audit.semantic"].candidates[0].extraBody,
    ).toEqual({ reasoning: { effort: "high" } });
  });

  it("preserves custom feature model and endpoint without silent upgrade", () => {
    const payload = basePayload();
    payload.feature_llm = {
      audit: {
        mode: "cloud",
        cloud: {
          provider_label: "Custom",
          base_url: "https://api.openai.com/v1",
          model: "openai/gpt-5.1",
        },
      },
    };
    const config = appConfigFromPayload(payload);
    expect(config.featureLlm.audit.cloud.baseUrl).toBe("https://api.openai.com/v1");
    expect(config.featureLlm.audit.cloud.model).toBe("openai/gpt-5.1");
    expect(config.featureLlm.tts.cloud.baseUrl).toBe("https://openrouter.ai/api/v1");
    expect(config.featureLlm.tts.cloud.model).toBe("mistralai/voxtral-mini-tts-2603");
  });

  it("round trips provider profiles and role candidates", () => {
    const payload = basePayload();
    payload.provider_profiles = [{
      id: "custom",
      label: "Custom",
      provider: "openai_compatible",
      base_url: "https://example.test/v1",
      api_key: "sk-test",
      enabled: true,
    }];
    payload.llm_role_routes = Object.fromEntries(
      Object.entries(DEFAULT_LLM_ROLE_ROUTES).map(([role, route]) => [
        role,
        {
          label: route.label,
          purpose: route.purpose,
          quality_gate_escalation: route.qualityGateEscalation,
          candidates: route.candidates.map((candidate, index) => ({
            provider_profile_id: role === "audit.semantic" && index === 0
              ? "custom"
              : candidate.providerProfileId,
            model: role === "audit.semantic" && index === 0
              ? "custom-semantic"
              : candidate.model,
            tier: candidate.tier,
            enabled: candidate.enabled,
            thinking: candidate.thinking,
          })),
        },
      ]),
    );

    const roundTrip = appConfigFromPayload(appConfigToPayload(appConfigFromPayload(payload)));
    expect(roundTrip.providerProfiles[0].apiKey).toBe("sk-test");
    expect(roundTrip.llmRoleRoutes["audit.semantic"].candidates[0]).toMatchObject({
      providerProfileId: "custom",
      model: "custom-semantic",
      tier: "primary",
    });
  });
});
