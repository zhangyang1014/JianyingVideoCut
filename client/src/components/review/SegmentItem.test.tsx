import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SegmentItem } from "./SegmentItem";
import type { Segment } from "@/lib/api";
import { createPlaybackClock } from "@/lib/playback-clock";

afterEach(() => {
  cleanup();
});

function makeSegment(overrides: Partial<Segment> = {}): Segment {
  return {
    id: "seg-1",
    start: 10,
    end: 13.05,
    text: "在这儿升级到了APP里。",
    action: "keep",
    reason: "内容完整，建议保留",
    rule: "Rule 7: 问答闭环",
    speaker: "spk0",
    ...overrides,
  };
}

function renderSegment(segment: Segment, overrides: Partial<React.ComponentProps<typeof SegmentItem>> = {}) {
  const props: React.ComponentProps<typeof SegmentItem> = {
    segment,
    isActive: false,
    isSelected: false,
    onClick: vi.fn(),
    onToggle: vi.fn(),
    onCharClick: vi.fn(),
    onSeek: vi.fn(),
    activeCharTime: -1,
    onEditSave: vi.fn(),
    ...overrides,
  };

  return render(<SegmentItem {...props} />);
}

describe("SegmentItem", () => {
  it("advances word highlighting from the playback clock without new parent props", () => {
    const playbackClock = createPlaybackClock();
    playbackClock.setTime(10.1);
    renderSegment(makeSegment({ text: "核对表达", words: [
      { word: "核对", start: 10, end: 11 }, { word: "表达", start: 11, end: 12 },
    ] }), { isActive: true, playbackClock });
    expect(screen.getByText("核对").className.split(" ").some((value) => value.startsWith("bg-primary"))).toBe(true);
    act(() => playbackClock.setTime(11.2));
    expect(screen.getByText("表达").className.split(" ").some((value) => value.startsWith("bg-primary"))).toBe(true);
    expect(screen.getByText("核对").className.split(" ").some((value) => value.startsWith("bg-primary"))).toBe(false);
  });

  it("reports the editing lifecycle when saving", () => {
    const onEditStart = vi.fn();
    const onEditEnd = vi.fn();
    const onEditSave = vi.fn();
    const segment = makeSegment();

    renderSegment(segment, { onEditStart, onEditEnd, onEditSave });

    fireEvent.doubleClick(screen.getByText(segment.text));
    expect(onEditStart).toHaveBeenCalledWith("seg-1");

    fireEvent.click(screen.getByTitle("确认保存 (Enter)"));
    expect(onEditSave).toHaveBeenCalledWith("seg-1", segment.text, segment.start, segment.end);
    expect(onEditEnd).toHaveBeenCalledWith("seg-1", "save");
  });

  it("reports the editing lifecycle when cancelling", () => {
    const onEditStart = vi.fn();
    const onEditEnd = vi.fn();
    const segment = makeSegment();

    renderSegment(segment, { onEditStart, onEditEnd });

    fireEvent.doubleClick(screen.getByText(segment.text));
    fireEvent.click(screen.getByTitle("取消 (Esc)"));

    expect(onEditStart).toHaveBeenCalledWith("seg-1");
    expect(onEditEnd).toHaveBeenCalledWith("seg-1", "cancel");
  });

  it("shows the effective subtitle by default and keeps original text in details", () => {
    const segment = makeSegment({
      action: "text_fix",
      display_text: "在这儿升级到了 App 里。",
      reason: "ASR 纠错后字幕更顺畅",
      rule: "Rule 4: 词内去重",
    });

    renderSegment(segment);

    expect(screen.getByText("在这儿升级到了 App 里。")).toBeTruthy();
    expect(screen.queryByText("在这儿升级到了APP里。")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /详情/ }));

    expect(screen.getByText("原文")).toBeTruthy();
    expect(screen.getByText("在这儿升级到了APP里。")).toBeTruthy();
    expect(screen.getByText("字幕")).toBeTruthy();
  });

  it("keeps the reason as a single editable row", () => {
    const onEditReason = vi.fn();
    const reason = "与上一句语义重复，删除后节奏更紧凑";
    const segment = makeSegment({
      action: "delete",
      reason,
      rule: "Rule 5: 语义去重",
    });

    renderSegment(segment, { onEditReason });

    expect(screen.getByText("语义重复")).toBeTruthy();
    fireEvent.click(screen.getByTitle(`点击修改原因：${reason}`));

    expect(onEditReason).toHaveBeenCalledWith("seg-1", "delete");
  });

  it("expands long subtitle text", () => {
    const segment = makeSegment({
      text: "这是一个很长很长的单句字幕，用来确认默认态会压缩到两行以内，同时用户仍然可以展开查看完整内容。",
    });

    renderSegment(segment);

    fireEvent.click(screen.getByRole("button", { name: "展开全文" }));

    expect(screen.getByRole("button", { name: "收起全文" })).toBeTruthy();
  });

  it("moves semantic, product, candidates and line-break hints into details", () => {
    const segment = makeSegment({
      source_segment_ids: ["a", "b"],
      semantic_unit_id: "semantic-1",
      product_semantic: {
        roles: ["product_opening", "selling_point"],
        product_entities: [{ name: "土豆传输助手", canonical_name: "土豆传输助手" }],
        selling_points: ["跨平台同步"],
        eligible_for_highlight: true,
      },
      candidate_edits: [{
        id: "edit-1",
        source: "filler",
        edit_type: "delete_range",
        start: 10.2,
        end: 10.5,
        text: "呃",
        reason: "语气词",
        status: "candidate",
      }],
      subtitle_line_break: "APP里",
    } as Partial<Segment> & { subtitle_line_break: string });

    renderSegment(segment);

    expect(screen.queryByText("语义稿")).toBeNull();
    expect(screen.queryByText("候选 1")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /详情/ }));

    expect(screen.getByText("语义稿")).toBeTruthy();
    expect(screen.getByText("来源 2 个原始片段")).toBeTruthy();
    expect(screen.getByText("产品 土豆传输助手")).toBeTruthy();
    expect(screen.getByText("候选 1")).toBeTruthy();
    expect(screen.getByText(/起换第二张字幕卡/)).toBeTruthy();
  });

  it("renders add and remove controls for the focused highlight", () => {
    const onHighlightMembershipToggle = vi.fn();
    const segment = makeSegment();

    const { rerender } = renderSegment(segment, {
      highlightMembershipAction: "add",
      onHighlightMembershipToggle,
    });
    fireEvent.click(screen.getByRole("button", { name: "加入当前集锦" }));
    expect(onHighlightMembershipToggle).toHaveBeenCalledWith("seg-1", "add");

    rerender(
      <SegmentItem
        segment={segment}
        isActive={false}
        isSelected={false}
        isInFocusedHighlight
        onClick={vi.fn()}
        onToggle={vi.fn()}
        onCharClick={vi.fn()}
        onSeek={vi.fn()}
        activeCharTime={-1}
        onEditSave={vi.fn()}
        highlightMembershipAction="remove"
        onHighlightMembershipToggle={onHighlightMembershipToggle}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "移出当前集锦" }));
    expect(onHighlightMembershipToggle).toHaveBeenLastCalledWith("seg-1", "remove");
  });

  it("does not render a highlight control for an ineligible sentence", () => {
    renderSegment(makeSegment(), {
      highlightMembershipAction: null,
      onHighlightMembershipToggle: vi.fn(),
    });

    expect(screen.queryByRole("button", { name: /当前集锦/ })).toBeNull();
  });
});
