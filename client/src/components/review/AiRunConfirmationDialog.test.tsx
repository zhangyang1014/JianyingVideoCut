import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ModelRunPreview } from "@/lib/api";
import { AiRunConfirmationDialog } from "./AiRunConfirmationDialog";


const { previewModelRun } = vi.hoisted(() => ({
  previewModelRun: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, previewModelRun };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const preview: ModelRunPreview = {
  operation: "audit",
  mode: "rerun",
  title: "确认重新审计",
  api_path: "/api/tasks/task-1/audit",
  task_revision: 3,
  affected_artifacts: ["新增审计版本", "产品集锦"],
  warnings: [],
  preview_token: "signed-preview",
  expires_in_seconds: 600,
  stages: [{
    id: "audit",
    label: "语义审计",
    kind: "llm",
    routes: [{
      provider: "openai_compatible",
      model: "z-ai/glm-5.2",
      tier: "primary",
      base_url: "https://api.routerhub.ai/v1",
      request_url: "https://api.routerhub.ai/v1/chat/completions",
      port: 443,
      credentials_configured: true,
    }],
    parameters: { style_mode: "immersive" },
    prompts: [{
      id: "audit.monologue_clean",
      label: "口播精修",
      source: "backend/prompts/monologue_clean.md",
      digest: "abc",
      system_prompt: "完整 system prompt",
      user_prompt: "完整 user prompt",
    }],
  }],
};

describe("AiRunConfirmationDialog", () => {
  it("loads a read-only preview and confirms with its token", async () => {
    previewModelRun.mockResolvedValue(preview);
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    const onOpenChange = vi.fn();

    render(
      <AiRunConfirmationDialog
        open
        taskId="task-1"
        request={{ operation: "audit", mode: "rerun", options: { style_mode: "immersive" } }}
        onOpenChange={onOpenChange}
        onConfirm={onConfirm}
      />,
    );

    expect(await screen.findByText("z-ai/glm-5.2")).toBeTruthy();
    expect(screen.getByText(/端口 443/)).toBeTruthy();
    expect(screen.queryByText("完整 system prompt")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /口播精修/ }));
    expect(await screen.findByText("完整 system prompt")).toBeTruthy();
    expect(screen.getByText("完整 user prompt")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "确认执行" }));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith(preview));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("does not execute when cancelled", async () => {
    previewModelRun.mockResolvedValue(preview);
    const onConfirm = vi.fn();
    const onOpenChange = vi.fn();
    render(
      <AiRunConfirmationDialog
        open
        taskId="task-1"
        request={{ operation: "audit" }}
        onOpenChange={onOpenChange}
        onConfirm={onConfirm}
      />,
    );

    await screen.findByText("z-ai/glm-5.2");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
