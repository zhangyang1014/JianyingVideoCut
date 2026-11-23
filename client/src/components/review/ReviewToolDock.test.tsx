import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ReviewToolDock, type ReviewToolItem } from "./ReviewToolDock";

afterEach(() => {
  cleanup();
});

function makeTools(overrides: Partial<Record<ReviewToolItem["id"], Partial<ReviewToolItem>>> = {}): ReviewToolItem[] {
  return [
    {
      id: "export",
      label: "导出",
      icon: <span aria-hidden="true">E</span>,
      content: <div>导出内容</div>,
      ...overrides.export,
    },
    {
      id: "funds",
      label: "资金消耗",
      icon: <span aria-hidden="true">$</span>,
      content: <div>资金消耗内容</div>,
      ...overrides.funds,
    },
    {
      id: "highlights",
      label: "产品集锦",
      icon: <span aria-hidden="true">H</span>,
      content: <div>产品集锦内容</div>,
      ...overrides.highlights,
    },
    {
      id: "scenario",
      label: "场景信息",
      icon: <span aria-hidden="true">S</span>,
      content: <div>场景信息内容</div>,
      ...overrides.scenario,
    },
    {
      id: "speakers",
      label: "说话人",
      icon: <span aria-hidden="true">P</span>,
      content: <div>说话人内容</div>,
      ...overrides.speakers,
    },
  ];
}

describe("ReviewToolDock", () => {
  it("mounts a lazy tool on first open and preserves its edits when switching tools", () => {
    render(<ReviewToolDock tools={makeTools({ funds: { lazy: true, content: <input aria-label="未保存原稿" defaultValue="原稿" /> } })} />);
    expect(screen.queryByLabelText("未保存原稿")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "打开资金消耗" }));
    fireEvent.change(screen.getByLabelText("未保存原稿"), { target: { value: "修改中的原稿" } });
    fireEvent.click(screen.getByRole("button", { name: "打开导出" }));
    fireEvent.click(screen.getByRole("button", { name: "打开资金消耗" }));
    expect(screen.getByLabelText("未保存原稿")).toHaveValue("修改中的原稿");
  });

  it("starts collapsed and keeps tool content mounted but hidden", () => {
    const { container } = render(<ReviewToolDock tools={makeTools()} />);

    expect(screen.getByLabelText("审阅工具坞")).toHaveAttribute("data-expanded", "false");
    expect(screen.getByRole("button", { name: "打开导出" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("导出内容")).not.toBeVisible();
    expect(container.querySelector("#review-tool-panel-export")).toHaveClass("h-full", "min-h-0", "overflow-y-auto");
  });

  it("opens the configured default tool on first render", () => {
    render(<ReviewToolDock tools={makeTools()} defaultActiveTool="highlights" />);

    expect(screen.getByLabelText("审阅工具坞")).toHaveAttribute("data-expanded", "true");
    expect(screen.getByText("产品集锦内容")).toBeVisible();
    expect(screen.getByRole("button", { name: "收起产品集锦" })).toHaveAttribute("aria-expanded", "true");
  });

  it("opens and closes the current tool from the rail", () => {
    render(<ReviewToolDock tools={makeTools()} />);

    fireEvent.click(screen.getByRole("button", { name: "打开导出" }));

    expect(screen.getByLabelText("审阅工具坞")).toHaveAttribute("data-expanded", "true");
    expect(screen.getByLabelText("审阅工具坞")).toHaveClass("w-[26rem]", "xl:w-[30rem]");
    expect(screen.getByText("导出内容")).toBeVisible();
    expect(screen.getByRole("button", { name: "收起导出" })).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(screen.getByRole("button", { name: "收起导出" }));

    expect(screen.getByLabelText("审阅工具坞")).toHaveAttribute("data-expanded", "false");
    expect(screen.getByText("导出内容")).not.toBeVisible();
  });

  it("switches between tools mutually", () => {
    render(<ReviewToolDock tools={makeTools()} />);

    fireEvent.click(screen.getByRole("button", { name: "打开导出" }));
    fireEvent.click(screen.getByRole("button", { name: "打开产品集锦" }));

    expect(screen.getByText("导出内容")).not.toBeVisible();
    expect(screen.getByText("产品集锦内容")).toBeVisible();
    expect(screen.getByRole("button", { name: "收起产品集锦" })).toHaveAttribute("aria-expanded", "true");
  });

  it("opens the funds usage tool", () => {
    render(<ReviewToolDock tools={makeTools()} />);

    fireEvent.click(screen.getByRole("button", { name: "打开资金消耗" }));

    expect(screen.getByText("资金消耗内容")).toBeVisible();
    expect(screen.getByRole("button", { name: "收起资金消耗" })).toHaveAttribute("aria-expanded", "true");
  });

  it("collapses when the active tool becomes unavailable", async () => {
    const { rerender } = render(<ReviewToolDock tools={makeTools()} />);

    fireEvent.click(screen.getByRole("button", { name: "打开导出" }));
    expect(screen.getByText("导出内容")).toBeVisible();

    rerender(<ReviewToolDock tools={makeTools({ export: { disabled: true } })} />);

    await waitFor(() => {
      expect(screen.getByLabelText("审阅工具坞")).toHaveAttribute("data-expanded", "false");
    });
    expect(screen.getByRole("button", { name: "打开导出" })).toBeDisabled();
  });

  it("does not render hidden tools in the rail", () => {
    render(<ReviewToolDock tools={makeTools({ highlights: { hidden: true } })} />);

    expect(screen.queryByRole("button", { name: "打开产品集锦" })).toBeNull();
    expect(screen.getByRole("button", { name: "打开场景信息" })).toBeTruthy();
  });
});
