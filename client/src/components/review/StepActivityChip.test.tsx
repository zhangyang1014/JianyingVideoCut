import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StepActivityChip } from "./StepActivityChip";

afterEach(() => {
  cleanup();
});

describe("StepActivityChip", () => {
  it("keeps model details out of the one-line public summary", () => {
    render(
      <StepActivityChip
        icon={<span aria-hidden>i</span>}
        name="ASR"
        model="FunASR"
        status="已完成"
        time="10:00"
      />,
    );

    expect(screen.getByText("ASR")).toBeTruthy();
    expect(screen.queryByText("FunASR")).toBeNull();
    expect(screen.getByText("已完成")).toBeTruthy();
    expect(screen.getByText("10:00")).toBeTruthy();
  });

  it("shows one-row history and rerun action on hover", async () => {
    const onAction = vi.fn();

    render(
      <StepActivityChip
        icon={<span aria-hidden>i</span>}
        name="ASR"
        status="已完成"
        time="10:00"
        hoverTitle="字幕识别历史"
        defaultOpen
        records={[
          {
            version: 2,
            label: "FunASR",
            meta: "120 个片段",
            time: "10:00",
            current: true,
          },
        ]}
        action={{ label: "重新识别", onClick: onAction }}
      />,
    );

    expect(await screen.findByText("字幕识别历史")).toBeTruthy();
    expect(screen.getByText("v2")).toBeTruthy();
    expect(screen.getByText("FunASR")).toBeTruthy();

    fireEvent.click(screen.getByText("重新识别"));
    expect(onAction).toHaveBeenCalledTimes(1);
  });

  it("supports selecting a historical version row", async () => {
    const onSelect = vi.fn();

    render(
      <StepActivityChip
        icon={<span aria-hidden>i</span>}
        name="ASR"
        status="已完成"
        hoverTitle="字幕识别历史"
        defaultOpen
        records={[
          {
            version: 2,
            label: "FunASR",
            actionLabel: "切换",
            onSelect,
          },
        ]}
      />,
    );

    fireEvent.click(await screen.findByText("切换"));

    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it("uses the public summary as the current record when history is empty", async () => {
    render(
      <StepActivityChip
        icon={<span aria-hidden>i</span>}
        name="文本整理"
        model="规则整理"
        status="分段 0.30s"
        time="7/24"
        hoverTitle="文本整理记录"
        defaultOpen
      />,
    );

    expect(await screen.findByText("文本整理记录")).toBeTruthy();
    expect(screen.getAllByText("当前").length).toBeGreaterThan(0);
    expect(screen.getAllByText("文本整理").length).toBeGreaterThan(0);
    expect(screen.getAllByText("规则整理").length).toBeGreaterThan(0);
    expect(screen.getAllByText("分段 0.30s").length).toBeGreaterThan(0);
    expect(screen.getAllByText("7/24").length).toBeGreaterThan(0);
  });

  it("shows no history when the current step has not run", async () => {
    render(
      <StepActivityChip
        icon={<span aria-hidden>i</span>}
        name="语义审计"
        status="未执行"
        hoverTitle="语义审计历史"
        defaultOpen
      />,
    );

    expect(await screen.findByText("语义审计历史")).toBeTruthy();
    expect(screen.getAllByText("无历史版本").length).toBeGreaterThan(0);
    expect(screen.queryByText("当前")).toBeNull();
  });
});
