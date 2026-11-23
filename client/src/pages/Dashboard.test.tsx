import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Dashboard from "./Dashboard";
import type { Task } from "@/lib/api";

const { mockFetchTasks, mockUseLocation } = vi.hoisted(() => ({
  mockFetchTasks: vi.fn<() => Promise<Task[]>>(),
  mockUseLocation: vi.fn(() => ["/", vi.fn()] as const),
}));

vi.mock("wouter", () => ({
  useLocation: () => mockUseLocation(),
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    fetchTasks: mockFetchTasks,
    createTask: vi.fn(),
    uploadVideo: vi.fn(),
    deleteTask: vi.fn(),
  };
});

function createTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "task-1",
    name: "运行中任务",
    task_type: "monologue_clean",
    status: "asr_running",
    created_at: "2026-04-09T12:00:00.000Z",
    updated_at: "2026-04-09T12:00:00.000Z",
    video_path: "/tmp/video.mp4",
    video_filename: "video.mp4",
    video_duration: 120,
    thumbnail_path: "/tmp/thumb.jpg",
    asr_history: [],
    audit_segments: [],
    audit_history: [],
    content_revision: 0,
    highlight_revision: 0,
    export_jobs: [],
    params: {
      silence_threshold: 0.3,
      breath_lead_ms: 80,
      breath_tail_ms: 80,
      min_segment_duration: 0.3,
      filler_words: [],
      retake_char_threshold: 5,
      style_mode: "immersive",
      enable_diarization: false,
      rules_enabled: {},
    },
    segments_kept: 0,
    segments_deleted: 0,
    golden_quote_order: [],
    ...overrides,
  };
}

describe("Dashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetchTasks.mockResolvedValue([createTask()]);
  });

  it("uses a bottom activity bar instead of a fullscreen running overlay", async () => {
    const { container } = render(<Dashboard />);

    await screen.findByText("运行中任务");

    await waitFor(() => {
      expect(container.innerHTML).toContain("absolute bottom-0 left-0 right-0 h-1");
    });

    expect(container.innerHTML).not.toContain("absolute inset-0 bg-black/60");
  });

  it("renders the execution_run list summary", async () => {
    mockFetchTasks.mockResolvedValue([
      createTask({
        status: "error",
        execution_run: {
          run_id: "run-list",
          generation: 2,
          operation: "audit",
          status: "retry_wait",
          current_stage: "semantic_audit",
          retry_state: {
            retries_used: 2,
            max_retries: 3,
            next_retry_at: new Date(Date.now() + 20_000).toISOString(),
          },
          error: { error_type: "rate_limit", message: "later", retryable: true },
          recoverable: true,
          created_at: "2026-07-30T08:00:00.000Z",
          updated_at: "2026-07-30T08:00:00.000Z",
        },
      }),
    ]);

    render(<Dashboard />);

    expect(await screen.findByText(/自动重试第 2 次（总4次）/)).toBeTruthy();
    expect(screen.getByText(/当前阶段：semantic_audit/)).toBeTruthy();
  });
});
