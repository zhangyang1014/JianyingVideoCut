import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ApiConfigPage from "./ApiConfigPage";
import {
  DEFAULT_AUDIT_MODEL,
  DEFAULT_GOLDEN_QUOTE_MODEL,
  DEFAULT_HIGHLIGHT_REEL_MODEL,
  DEFAULT_LEARNING_MODEL,
  DEFAULT_LLM_ROLE_ROUTES,
  DEFAULT_PROVIDER_PROFILES,
  DEFAULT_TRANSLATE_MODEL,
  type AppConfig,
  type FeatureModelConfig,
} from "@/hooks/useAppConfig";

const {
  mockUseAppConfig,
  mockUpdateConfig,
  mockListConfigPrompts,
  mockGetConfigPrompt,
  mockUpdateConfigPrompt,
  mockListAvailableModels,
  mockFetchProviderPricingStatuses,
  mockSyncProviderPricing,
} = vi.hoisted(() => ({
  mockUseAppConfig: vi.fn(),
  mockUpdateConfig: vi.fn(),
  mockListConfigPrompts: vi.fn(),
  mockGetConfigPrompt: vi.fn(),
  mockUpdateConfigPrompt: vi.fn(),
  mockListAvailableModels: vi.fn(),
  mockFetchProviderPricingStatuses: vi.fn(),
  mockSyncProviderPricing: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  },
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    schemaVersion: 5,
    ...actual,
    testLlmModel: vi.fn(),
    listAvailableLlmModels: mockListAvailableModels,
    fetchProviderPricingStatuses: mockFetchProviderPricingStatuses,
    syncProviderPricing: mockSyncProviderPricing,
    listConfigPrompts: mockListConfigPrompts,
    getConfigPrompt: mockGetConfigPrompt,
    updateConfigPrompt: mockUpdateConfigPrompt,
    getGlossary: vi.fn().mockResolvedValue("GoldenClip"),
    updateGlossary: vi.fn().mockResolvedValue(undefined),
  };
});

vi.mock("@/hooks/useAppConfig", async () => {
  const actual = await vi.importActual<typeof import("@/hooks/useAppConfig")>("@/hooks/useAppConfig");
  return {
    ...actual,
    useAppConfig: () => mockUseAppConfig(),
  };
});

function createFeature(model: string, overrides: Partial<FeatureModelConfig> = {}): FeatureModelConfig {
  return {
    mode: "cloud",
    cloud: {
      providerLabel: "RouterHub",
      baseUrl: "https://api.routerhub.ai/v1",
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
    schemaVersion: 5,
    openaiApiKey: "",
    openaiModel: DEFAULT_TRANSLATE_MODEL,
    jianyingDir: "",
    provider: "openai",
    ollamaModel: "qwen2.5:14b",
    ollamaBaseUrl: "http://localhost:11434",
    featureLlm: {
      audit: createFeature(DEFAULT_AUDIT_MODEL),
      translate: createFeature(DEFAULT_TRANSLATE_MODEL),
      highlight_reel: createFeature(DEFAULT_HIGHLIGHT_REEL_MODEL),
      golden_quote: createFeature(DEFAULT_GOLDEN_QUOTE_MODEL),
      learning: createFeature(DEFAULT_LEARNING_MODEL),
      tts: {
        mode: "cloud",
        cloud: {
          providerLabel: "OpenRouter TTS",
          baseUrl: "https://openrouter.ai/api/v1",
          apiKey: "",
          model: "mistralai/voxtral-mini-tts-2603",
        },
        voice: "en_paul_neutral",
      },
    },
    providerProfiles: DEFAULT_PROVIDER_PROFILES,
    llmRoleRoutes: DEFAULT_LLM_ROLE_ROUTES,
    asr: {
      funasrChunkMinutes: 5,
      funasrParallelChunks: 4,
    },
    auditRuntime: {
      cloudChunkSegments: 120,
      cloudMaxParallelChunks: 8,
      cloudInitialParallelChunks: 8,
      cloudChunkTokenBudget: 24000,
      enableBatchResume: true,
      enableIncrementalCache: true,
      coherenceStrategy: "auto",
    },
    highlightRuntime: {
      cloudMaxParallelCalls: 3,
    },
    ...overrides,
  };
}

describe("ApiConfigPage", () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
    mockUpdateConfig.mockResolvedValue(undefined);
    mockListAvailableModels.mockResolvedValue([]);
    mockFetchProviderPricingStatuses.mockResolvedValue([]);
    mockSyncProviderPricing.mockResolvedValue({
      provider_profile_id: "routerhub",
      source_url: "https://www.routerhub.ai/models",
      registered_at: "2026-08-01T01:00:00Z",
      synced_at: "2026-08-01T02:00:00Z",
      model_count: 42,
    });
    mockListConfigPrompts.mockImplementation(async (feature: string) =>
      feature === "audit"
        ? [{
            id: "audit.highlight_reel",
            feature: "audit",
            label: "直播卖货集锦审计",
            description: "实际运行 Prompt",
            editable: true,
            source: "backend/prompts/highlight_reel_v2.md",
            active_version: "highlight_reel_v2.md",
            kind: "system",
          }]
        : [],
    );
    mockGetConfigPrompt.mockResolvedValue({
      id: "audit.highlight_reel",
      feature: "audit",
      label: "直播卖货集锦审计",
      description: "实际运行 Prompt",
      editable: true,
      source: "backend/prompts/highlight_reel_v2.md",
      active_version: "highlight_reel_v2.md",
      kind: "system",
      content: "当前生效的集锦审计 Prompt",
    });
    mockUpdateConfigPrompt.mockImplementation(async (_id: string, content: string) => ({
      ...(await mockGetConfigPrompt()),
      content,
    }));
    mockUseAppConfig.mockReturnValue({
      config: createConfig(),
      updateConfig: mockUpdateConfig,
      loaded: true,
      loadError: null,
    });
  });

  it("shows ASR runtime settings only on the ASR tab", () => {
    render(<ApiConfigPage />);

    expect(screen.queryByRole("heading", { name: "本地 ASR 性能" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "ASR 性能" }));
    expect(screen.getByRole("heading", { name: "本地 ASR 性能" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "云端配置" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "语义审计" }));
    expect(screen.queryByRole("heading", { name: "本地 ASR 性能" })).toBeNull();
  });

  it("selects OpenRouter from provider dropdown and updates its address and route", async () => {
    render(<ApiConfigPage />);

    const providerSelect = screen.getByRole("combobox", { name: "API 服务商" });
    expect(providerSelect).toHaveValue("routerhub");
    expect(screen.getByRole("option", { name: /RouterHub/ })).toBeTruthy();
    expect(screen.getByRole("option", { name: /OpenRouter/ })).toBeTruthy();

    fireEvent.change(providerSelect, { target: { value: "openrouter" } });

    expect(screen.getByDisplayValue("https://openrouter.ai/api/v1")).toHaveAttribute("readonly");
    fireEvent.change(screen.getByPlaceholderText("OPENROUTER_API_KEY"), {
      target: { value: "openrouter-test-key" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));

    await waitFor(() => expect(mockUpdateConfig).toHaveBeenCalled());
    const saved = mockUpdateConfig.mock.calls[0][0] as AppConfig;
    expect(saved.featureLlm.audit.cloud.providerLabel).toBe("OpenRouter");
    expect(saved.featureLlm.audit.cloud.baseUrl).toBe("https://openrouter.ai/api/v1");
    expect(
      saved.providerProfiles.find((profile) => profile.id === "openrouter")?.apiKey,
    ).toBe("openrouter-test-key");
    expect(
      saved.llmRoleRoutes["audit.semantic"].candidates[0].providerProfileId,
    ).toBe("openrouter");
  });

  it("does not query Ollama when switching from a local model tab to the ASR tab", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ models: ["qwen2.5:14b"] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const config = createConfig({
      featureLlm: {
        ...createConfig().featureLlm,
        audit: createFeature("qwen/qwen3.7-plus", { mode: "local" }),
      },
    });
    mockUseAppConfig.mockReturnValue({
      config,
      updateConfig: mockUpdateConfig,
      loaded: true,
      loadError: null,
    });

    render(<ApiConfigPage />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    fetchMock.mockClear();

    fireEvent.click(screen.getByRole("button", { name: "ASR 性能" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "本地 ASR 性能" })).toBeTruthy());
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("saves ASR runtime changes through the shared config save action", async () => {
    render(<ApiConfigPage />);

    fireEvent.click(screen.getByRole("button", { name: "ASR 性能" }));
    fireEvent.change(screen.getByDisplayValue("5"), { target: { value: "8" } });
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));

    await waitFor(() =>
      expect(mockUpdateConfig).toHaveBeenCalledWith(
        expect.objectContaining({
          asr: expect.objectContaining({
            funasrChunkMinutes: 8,
            funasrParallelChunks: 4,
          }),
        }),
      ),
    );
  });

  it("saves cloud audit runtime changes through the shared config save action", async () => {
    render(<ApiConfigPage />);

    expect(screen.getByRole("heading", { name: "云端审计性能" })).toBeTruthy();
    fireEvent.change(screen.getByDisplayValue("120"), { target: { value: "80" } });
    fireEvent.change(screen.getAllByDisplayValue("8")[0], { target: { value: "12" } });
    fireEvent.change(screen.getByDisplayValue("24000"), { target: { value: "36000" } });
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));

    await waitFor(() =>
      expect(mockUpdateConfig).toHaveBeenCalledWith(
        expect.objectContaining({
          auditRuntime: expect.objectContaining({
            cloudChunkSegments: 80,
            cloudMaxParallelChunks: 12,
            cloudInitialParallelChunks: 8,
            cloudChunkTokenBudget: 36000,
            enableBatchResume: true,
            enableIncrementalCache: true,
            coherenceStrategy: "auto",
          }),
        }),
      ),
    );
  });

  it("shows and saves cloud highlight concurrency presets", async () => {
    render(<ApiConfigPage />);

    fireEvent.click(screen.getByRole("button", { name: "产品集锦质检" }));

    expect(screen.getByRole("heading", { name: "产品集锦云端并发" })).toBeTruthy();
    const input = screen.getByDisplayValue("3");
    expect(input.getAttribute("min")).toBe("1");
    expect(input.getAttribute("max")).toBe("8");
    [1, 3, 4, 6, 8].forEach((count) => {
      expect(screen.getByRole("button", { name: `${count} 路` })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "6 路" }));
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));

    await waitFor(() =>
      expect(mockUpdateConfig).toHaveBeenCalledWith(
        expect.objectContaining({
          highlightRuntime: {
            cloudMaxParallelCalls: 6,
          },
        }),
      ),
    );
  });

  it("shows fixed single-call guidance for local highlight mode", () => {
    const baseConfig = createConfig();
    const config = createConfig({
      featureLlm: {
        ...baseConfig.featureLlm,
        highlight_reel: createFeature("qwen/qwen3.7-plus", { mode: "local" }),
      },
    });
    mockUseAppConfig.mockReturnValue({
      config,
      updateConfig: mockUpdateConfig,
      loaded: true,
      loadError: null,
    });

    render(<ApiConfigPage />);
    fireEvent.click(screen.getByRole("button", { name: "产品集锦质检" }));

    expect(screen.getByText(/本地 Ollama 模式固定单路执行/)).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "产品集锦云端并发" })).toBeNull();
  });

  it("shows and saves the actual active prompt independently", async () => {
    render(<ApiConfigPage />);

    expect(await screen.findByText("生效版本：highlight_reel_v2.md")).toBeTruthy();
    const editor = screen.getByLabelText("直播卖货集锦审计 Prompt");
    fireEvent.change(editor, { target: { value: "更新后的运行时 Prompt" } });
    fireEvent.click(screen.getByRole("button", { name: "保存 Prompt" }));

    await waitFor(() =>
      expect(mockUpdateConfigPrompt).toHaveBeenCalledWith(
        "audit.highlight_reel",
        "更新后的运行时 Prompt",
      ),
    );
    expect(mockUpdateConfig).not.toHaveBeenCalled();
  });

  it("protects unsaved prompt edits when switching prompt sources", async () => {
    mockListConfigPrompts.mockResolvedValue([
      {
        id: "audit.monologue_clean",
        feature: "audit",
        label: "口播精修",
        description: "口播 Prompt",
        editable: true,
        source: "backend/prompts/monologue_clean.md",
        active_version: "monologue_clean.md",
        kind: "system",
      },
      {
        id: "audit.interview_compress",
        feature: "audit",
        label: "直播访谈压缩",
        description: "访谈 Prompt",
        editable: true,
        source: "backend/prompts/interview_compress.md",
        active_version: "interview_compress.md",
        kind: "system",
      },
    ]);
    mockGetConfigPrompt.mockImplementation(async (id: string) => ({
      id,
      feature: "audit",
      label: id === "audit.monologue_clean" ? "口播精修" : "直播访谈压缩",
      description: "Prompt",
      editable: true,
      source: `backend/prompts/${id}.md`,
      active_version: null,
      kind: "system",
      content: id,
    }));
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<ApiConfigPage />);
    const editor = await screen.findByLabelText("口播精修 Prompt");
    fireEvent.change(editor, { target: { value: "未保存内容" } });
    fireEvent.click(screen.getByRole("button", { name: "直播访谈压缩" }));

    expect(confirmSpy).toHaveBeenCalled();
    expect(mockGetConfigPrompt).not.toHaveBeenCalledWith("audit.interview_compress");
  });

  it("loads additional provider models and lets the user select one", async () => {
    mockListAvailableModels.mockResolvedValue([
      {
        id: "anthropic/claude-sonnet-4",
        name: "Claude Sonnet 4",
        description: "长上下文模型",
        context_length: 200000,
        input_modalities: ["text"],
        output_modalities: ["text"],
        pricing: {},
      },
    ]);

    render(<ApiConfigPage />);
    const catalogModel = await screen.findByRole("button", { name: /Claude Sonnet 4/ });
    fireEvent.click(catalogModel);

    expect(screen.getByDisplayValue("anthropic/claude-sonnet-4")).toBeTruthy();
    expect(screen.getByRole("button", { name: "保存配置" })).toBeTruthy();
  });

  it("edits provider profiles and shows role fallback order", async () => {
    render(<ApiConfigPage />);
    fireEvent.click(screen.getByRole("button", { name: "角色路由" }));

    expect(screen.getByText("供应商凭据档案")).toBeTruthy();
    expect(
      screen.getAllByDisplayValue("anthropic/claude-opus-4.8").length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByDisplayValue("openai/gpt-5.6-sol").length).toBeGreaterThan(0);
    expect(screen.getAllByText("旗舰升级").length).toBeGreaterThan(0);

    const volcengineNameInput = screen.getAllByDisplayValue("火山方舟")
      .find((element) => element.tagName === "INPUT");
    expect(volcengineNameInput).toBeTruthy();
    fireEvent.change(volcengineNameInput!, {
      target: { value: "火山方舟生产" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
    await waitFor(() => expect(mockUpdateConfig).toHaveBeenCalled());
    expect(
      mockUpdateConfig.mock.calls[0][0].providerProfiles
        .find((profile: { id: string }) => profile.id === "volcengine-ark")
        .label,
    ).toBe("火山方舟生产");
  });

  it("syncs RouterHub pricing and shows registration time", async () => {
    render(<ApiConfigPage />);
    fireEvent.click(screen.getByRole("button", { name: "角色路由" }));
    fireEvent.click(screen.getByRole("button", { name: "同步最新报价" }));

    await waitFor(() => expect(mockSyncProviderPricing).toHaveBeenCalledWith("routerhub"));
    expect(await screen.findByText(/42 个模型/)).toBeTruthy();
    expect(screen.getByText(/报价登记/)).toBeTruthy();
  });
});
