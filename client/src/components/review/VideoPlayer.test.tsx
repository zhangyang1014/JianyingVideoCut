import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Segment } from "@/lib/api";
import { VideoPlayer } from "./VideoPlayer";

const deleteSegment: Segment = {
  id: "seg-delete",
  start: 1,
  end: 2,
  text: "隐藏片段",
  action: "delete",
};

function renderPlayer(overrides: Partial<React.ComponentProps<typeof VideoPlayer>> = {}) {
  return render(
    <VideoPlayer
      taskId="task-1"
      currentTime={0}
      onTimeUpdate={vi.fn()}
      segments={[]}
      playbackMode="all"
      onPlaybackModeChange={vi.fn()}
      videoUrl="/video.mp4"
      {...overrides}
    />,
  );
}

beforeEach(() => {
  Object.defineProperty(HTMLMediaElement.prototype, "play", {
    configurable: true,
    value: vi.fn().mockResolvedValue(undefined),
  });
  Object.defineProperty(HTMLMediaElement.prototype, "pause", {
    configurable: true,
    value: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("VideoPlayer", () => {
  it("synchronizes subtitle time after a paused seek and the final pause frame", () => {
    const onTimeUpdate = vi.fn();
    const { container } = renderPlayer({ onTimeUpdate });
    const video = container.querySelector("video")!;
    video.currentTime = 7.25;
    fireEvent.seeked(video);
    expect(onTimeUpdate).toHaveBeenLastCalledWith(7.25);
    expect(screen.getByRole("slider").getAttribute("aria-valuenow")).toBe("7.25");
    video.currentTime = 8.75;
    fireEvent.pause(video);
    expect(onTimeUpdate).toHaveBeenLastCalledWith(8.75);
  });

  it("executes small and repeated explicit word seeks and exits an earlier audition", () => {
    const props = { taskId: "task", currentTime: 1, seekRequestId: 1, onTimeUpdate: vi.fn(), segments: [], playbackMode: "all" as const, onPlaybackModeChange: vi.fn() };
    const view = render(<VideoPlayer {...props} />);
    const video = view.container.querySelector("video")!;
    video.currentTime = 1.3;
    view.rerender(<VideoPlayer {...props} seekRequestId={2} />);
    expect(video.currentTime).toBe(1);
    view.rerender(<VideoPlayer {...props} seekRequestId={2} auditionRequest={{ id: 1, label: "接缝试听", ranges: [{ start: 3, end: 5 }] }} />);
    expect(screen.getByText("接缝试听")).toBeTruthy();
    view.rerender(<VideoPlayer {...props} currentTime={3.2} seekRequestId={3} auditionRequest={{ id: 1, label: "接缝试听", ranges: [{ start: 3, end: 5 }] }} />);
    expect(video.currentTime).toBe(3.2);
    expect(screen.queryByText("接缝试听")).toBeNull();
  });

  it("does not report a cancelled play Promise as a playback failure", async () => {
    let rejectPlay!: (error: Error) => void;
    vi.mocked(HTMLMediaElement.prototype.play).mockImplementationOnce(() => new Promise<void>((_, reject) => { rejectPlay = reject; }));
    renderPlayer({ auditionRequest: { id: 1, label: "快速试看", ranges: [{ start: 0, end: 3 }] } });
    fireEvent.click(screen.getByRole("button", { name: "结束试听" }));
    await act(async () => { rejectPlay(new DOMException("interrupted", "AbortError")); });
    expect(screen.queryByText(/无法开始试听/)).toBeNull();
  });

  it("a rejected old play Promise cannot cancel a newer audition", async () => {
    let rejectPlay!: (error: Error) => void;
    vi.mocked(HTMLMediaElement.prototype.play).mockImplementationOnce(() => new Promise<void>((_, reject) => { rejectPlay = reject; }));
    const props = { taskId: "task", currentTime: 0, onTimeUpdate: vi.fn(), segments: [], playbackMode: "all" as const, onPlaybackModeChange: vi.fn() };
    const view = render(<VideoPlayer {...props} auditionRequest={{ id: 1, label: "旧集锦", ranges: [{ start: 0, end: 3 }] }} />);
    view.rerender(<VideoPlayer {...props} auditionRequest={{ id: 2, label: "新集锦", ranges: [{ start: 4, end: 6 }] }} />);
    await act(async () => { rejectPlay(new DOMException("interrupted", "AbortError")); });
    expect(screen.getByText("新集锦")).toBeTruthy();
    expect(screen.queryByText(/无法开始试听/)).toBeNull();
  });

  it("still reports an actual failure of the current audition", async () => {
    vi.mocked(HTMLMediaElement.prototype.play).mockRejectedValueOnce(new Error("unsupported"));
    await act(async () => { renderPlayer({ auditionRequest: { id: 1, label: "试看", ranges: [{ start: 0, end: 3 }] } }); });
    expect(screen.getByText(/无法开始试听/)).toBeTruthy();
  });

  it("reports the natural video size from loaded metadata", () => {
    const onVideoNaturalSize = vi.fn();
    const { container } = renderPlayer({ onVideoNaturalSize });
    const video = container.querySelector("video");
    expect(video).toBeTruthy();
    Object.defineProperty(video!.parentElement, "clientWidth", { configurable: true, value: 300 });

    Object.defineProperty(video, "videoWidth", { configurable: true, value: 1080 });
    Object.defineProperty(video, "videoHeight", { configurable: true, value: 1920 });
    fireEvent.loadedMetadata(video!);

    expect(onVideoNaturalSize).toHaveBeenCalledWith({
      width: 1080,
      height: 1920,
      suggestedViewportHeight: 533,
    });
  });

  it("spreads primary controls horizontally and keeps the timestamp on one line", () => {
    renderPlayer({ segments: [deleteSegment] });

    const backButton = screen.getByRole("button", { name: "向前 10 秒" });
    expect(backButton).toBeTruthy();
    expect(screen.getByRole("button", { name: "上一句" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "播放" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "下一句" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "往后 10 秒" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "静音" })).toBeTruthy();
    expect(screen.getByRole("slider", { name: "视频进度" })).toBeTruthy();
    expect(backButton.parentElement?.className).toContain("grid-cols-[");

    const timestamp = screen.getByText(/00:00\.000 \/ 00:00\.000/);
    expect(timestamp.parentElement?.className).toContain("whitespace-nowrap");
    expect(timestamp.className).toContain("tabular-nums");
  });

  it("seeks to the previous and next sentence", () => {
    const segments: Segment[] = [
      { ...deleteSegment, id: "seg-1", start: 1, end: 3, action: "keep" },
      { ...deleteSegment, id: "seg-2", start: 4, end: 6, action: "keep" },
      { ...deleteSegment, id: "seg-3", start: 7, end: 9, action: "keep" },
    ];
    const { container } = renderPlayer({ segments });
    const video = container.querySelector("video")!;
    video.currentTime = 5;

    fireEvent.click(screen.getByRole("button", { name: "上一句" }));
    expect(video.currentTime).toBe(1);

    video.currentTime = 5;
    fireEvent.click(screen.getByRole("button", { name: "下一句" }));
    expect(video.currentTime).toBe(7);
  });

  it("renders all three playback modes as a second-row segmented control", () => {
    const onPlaybackModeChange = vi.fn();
    renderPlayer({
      segments: [deleteSegment],
      highlightSegmentIds: new Set([deleteSegment.id]),
      onPlaybackModeChange,
    });

    const modeGroup = screen.getByRole("radiogroup", { name: "播放模式" });
    expect(modeGroup.className).toContain("grid");
    expect(modeGroup.className).toContain("w-full");
    expect(modeGroup.className).toContain("grid-cols-3");
    expect(screen.getByText("全部")).toBeTruthy();
    expect(screen.getByText("只播放不隐藏")).toBeTruthy();
    expect(screen.getByText("直播卖货集锦")).toBeTruthy();

    fireEvent.click(screen.getByText("只播放不隐藏"));
    expect(onPlaybackModeChange).toHaveBeenCalledWith("skip-hidden");

    fireEvent.click(screen.getByText("直播卖货集锦"));
    expect(onPlaybackModeChange).toHaveBeenCalledWith("highlight-reel");
  });

  it("disables highlight playback until a highlight reel exists", () => {
    renderPlayer({ segments: [deleteSegment] });

    expect(screen.getByRole("radio", { name: "直播卖货集锦" })).toBeDisabled();
  });

  it("uses the same second-row lane for preview exit", () => {
    const onExitPreview = vi.fn();
    renderPlayer({ previewMode: true, onExitPreview });

    expect(screen.queryByRole("radiogroup", { name: "播放模式" })).toBeNull();
    const exitButton = screen.getByRole("button", { name: "返回原视频" });
    expect(exitButton.className).toContain("w-full");

    fireEvent.click(exitButton);
    expect(onExitPreview).toHaveBeenCalledTimes(1);
  });
});
