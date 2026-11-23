import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAppConfig } from "./useAppConfig";

const { mockFetchAppConfig, mockPutAppConfig } = vi.hoisted(() => ({
  mockFetchAppConfig: vi.fn(),
  mockPutAppConfig: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    fetchAppConfig: mockFetchAppConfig,
    putAppConfig: mockPutAppConfig,
  };
});

const legacyBackendPayload = {
  openai_api_key: "",
  openai_model: "qwen/qwen3.7-plus",
  jianying_dir: "",
  provider: "openai" as const,
  ollama_model: "qwen2.5:14b",
  ollama_base_url: "http://localhost:11434",
};

describe("useAppConfig highlight runtime", () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    mockFetchAppConfig.mockResolvedValue(legacyBackendPayload);
    mockPutAppConfig.mockResolvedValue(undefined);
  });

  it("defaults missing old-backend fields and writes the snake_case payload", async () => {
    const { result } = renderHook(() => useAppConfig());

    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.config.highlightRuntime.cloudMaxParallelCalls).toBe(3);

    await act(async () => {
      await result.current.updateConfig({
        highlightRuntime: { cloudMaxParallelCalls: 6 },
      });
    });

    expect(mockPutAppConfig).toHaveBeenCalledWith(
      expect.objectContaining({
        highlight_runtime: {
          cloud_max_parallel_calls: 6,
        },
      }),
    );
    expect(result.current.config.highlightRuntime.cloudMaxParallelCalls).toBe(6);
  });
});
