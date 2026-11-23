import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ProductionAsset, ProductionShot } from "@/lib/productions-api";
import { ProductionShotPreview } from "./ProductionShotPreview";

const asset: ProductionAsset = {
  id: "picture", filename: "母片.mp4", url: "/picture.mp4", fingerprint: "picture",
  kind: "video", role: "footage", shot_id: null, version: 1,
  duration: 30, width: 1080, height: 1920, has_audio: true, frames: [], cues: [],
  usable_start: 0, usable_end: 30, analysis_status: "done", analysis_note: "", visual_summary: "",
};
const voice: ProductionAsset = { ...asset, id: "voice", url: "/voice.wav", kind: "audio", role: "voiceover", duration: 90 };
const shot: ProductionShot = {
  shot_id: "S02", revision: 1, purpose: "接水演示", visual: "接一杯水", spoken_text: "一杯温水",
  subtitle: "一杯温水", target_duration: 5, source_type: "existing", voice_mode: "voiceover",
  framing: "contain", trim_pauses: true, tool: "通用", reference_ids: [], fact_ids: [],
  filming_guide: "", prompt: "", selection: { asset_id: asset.id, start: 10, end: 15 },
  voiceover: { asset_id: voice.id, start: 40, end: 45 }, verified_signature: null, review_note: "", required: true,
};

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
  vi.stubGlobal("requestAnimationFrame", vi.fn().mockReturnValue(1));
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("ProductionShotPreview", () => {
  it("starts at the selected in point, clamps scrubbing, and stops at the out point", () => {
    render(<ProductionShotPreview shot={{ ...shot, voice_mode: "original" }} asset={asset} />);
    const video = screen.getByLabelText("S02 画面预览") as HTMLVideoElement;
    fireEvent.loadedMetadata(video);
    expect(video.currentTime).toBe(10);
    video.currentTime = 2;
    fireEvent.seeking(video);
    expect(video.currentTime).toBe(10);
    video.currentTime = 15.2;
    fireEvent.timeUpdate(video);
    expect(video.pause).toHaveBeenCalled();
    expect(video.currentTime).toBe(15);
    fireEvent.play(video);
    expect(video.currentTime).toBe(10);
    expect(screen.getByText("本幕 5.0 秒")).toBeInTheDocument();
  });

  it("synchronizes the separate voiceover to play, seek, pause and both clip limits", () => {
    const { container } = render(<ProductionShotPreview shot={shot} asset={asset} voiceoverAsset={voice} />);
    const video = container.querySelector("video")!;
    const audio = container.querySelector("audio")!;
    fireEvent.loadedMetadata(video);
    fireEvent.loadedMetadata(audio);
    expect(audio.currentTime).toBe(40);
    expect(video.muted).toBe(true);
    fireEvent.play(video);
    expect(audio.play).not.toHaveBeenCalled();
    fireEvent.playing(video);
    expect(vi.mocked(audio.play).mock.instances).toContain(audio);
    video.currentTime = 12.5;
    fireEvent.seeking(video);
    expect(audio.currentTime).toBe(42.5);
    fireEvent.pause(video);
    expect(vi.mocked(audio.pause).mock.instances).toContain(audio);
    audio.currentTime = 45.1;
    fireEvent.timeUpdate(audio);
    expect(audio.pause).toHaveBeenCalled();
    video.currentTime = 15.1;
    fireEvent.timeUpdate(video);
    expect(video.currentTime).toBe(15);
    expect(screen.getByText("已同步独立旁白 · 原画面静音")).toBeInTheDocument();
  });

  it("keeps generated dialogue audible and mutes silent or missing-voiceover previews", () => {
    const view = render(<ProductionShotPreview shot={{ ...shot, voice_mode: "generated" }} asset={asset} />);
    expect(view.container.querySelector("video")!.muted).toBe(false);
    view.rerender(<ProductionShotPreview shot={{ ...shot, voice_mode: "silent" }} asset={asset} />);
    expect(view.container.querySelector("video")!.muted).toBe(true);
    expect(view.container.querySelector("audio")).toBeNull();
    view.rerender(<ProductionShotPreview shot={shot} asset={asset} />);
    expect(view.container.querySelector("video")!.muted).toBe(true);
    expect(screen.getByText("尚未选择独立旁白，当前仅预览画面")).toBeInTheDocument();
  });

  it("stops both old media elements when switching scenes on the same mother clip", () => {
    const view = render(<ProductionShotPreview shot={shot} asset={asset} voiceoverAsset={voice} />);
    const oldVideo = view.container.querySelector("video")!;
    const oldAudio = view.container.querySelector("audio")!;
    fireEvent.loadedMetadata(oldVideo);
    fireEvent.loadedMetadata(oldAudio);
    fireEvent.play(oldVideo);
    fireEvent.playing(oldVideo);
    vi.mocked(HTMLMediaElement.prototype.pause).mockClear();
    view.rerender(<ProductionShotPreview shot={{ ...shot, shot_id: "S03", selection: { asset_id: asset.id, start: 20, end: 25 } }} asset={asset} voiceoverAsset={voice} />);
    expect(vi.mocked(HTMLMediaElement.prototype.pause).mock.instances).toContain(oldVideo);
    expect(vi.mocked(HTMLMediaElement.prototype.pause).mock.instances).toContain(oldAudio);
    expect(view.container.querySelectorAll("video")).toHaveLength(1);
    const nextVideo = screen.getByLabelText("S03 画面预览") as HTMLVideoElement;
    fireEvent.loadedMetadata(nextVideo);
    expect(nextVideo.currentTime).toBe(20);
  });

  it("does not report an aborted audio start as a playback failure after switching scenes", async () => {
    let reject!: (error: Error) => void;
    vi.mocked(HTMLMediaElement.prototype.play).mockImplementationOnce(() => new Promise<void>((_, rejectPromise) => { reject = rejectPromise; }));
    const view = render(<ProductionShotPreview shot={shot} asset={asset} voiceoverAsset={voice} />);
    fireEvent.loadedMetadata(view.container.querySelector("video")!);
    fireEvent.loadedMetadata(view.container.querySelector("audio")!);
    fireEvent.play(view.container.querySelector("video")!);
    fireEvent.playing(view.container.querySelector("video")!);
    view.rerender(<ProductionShotPreview shot={{ ...shot, shot_id: "S03" }} asset={asset} voiceoverAsset={voice} />);
    await act(async () => reject(new DOMException("interrupted", "AbortError")));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a recoverable failure when the current picture or voiceover cannot load", async () => {
    const view = render(<ProductionShotPreview shot={shot} asset={asset} voiceoverAsset={voice} />);
    fireEvent.error(view.container.querySelector("audio")!);
    expect(screen.getByRole("alert")).toHaveTextContent("旁白未能播放");
    fireEvent.error(view.container.querySelector("video")!);
    expect(screen.getByText("视频未能加载，请检查素材文件后重试。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新加载画面" }));
    expect(view.container.querySelector("video")).not.toBeNull();
  });

  it("reloads a failed voiceover before resuming metadata synchronization and playback", () => {
    const { container } = render(<ProductionShotPreview shot={shot} asset={asset} voiceoverAsset={voice} />);
    const video = container.querySelector("video")!;
    const audio = container.querySelector("audio")!;
    fireEvent.loadedMetadata(video);
    fireEvent.error(audio);
    expect(screen.getByRole("alert")).toHaveTextContent("旁白未能播放");
    fireEvent.click(screen.getByRole("button", { name: "重新加载旁白" }));
    expect(vi.mocked(audio.load).mock.instances).toContain(audio);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    video.currentTime = 12.2;
    fireEvent.loadedMetadata(audio);
    expect(audio.currentTime).toBeCloseTo(42.2);
    fireEvent.play(video);
    fireEvent.playing(video);
    expect(vi.mocked(audio.play).mock.instances).toContain(audio);
  });

  it("does not preview an unselected or invalid range", () => {
    const view = render(<ProductionShotPreview shot={{ ...shot, selection: null }} asset={asset} />);
    expect(screen.getByText("这一幕还没有可预览的画面")).toBeInTheDocument();
    expect(view.container.querySelector("video")).toBeNull();
    view.rerender(<ProductionShotPreview shot={{ ...shot, selection: { asset_id: asset.id, start: 30, end: 10 } }} asset={asset} />);
    expect(view.container.querySelector("video")).toBeNull();
  });
});
