import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ShotNavigator, type ShotNavigationItem } from "./ShotNavigator";

const items: ShotNavigationItem[] = [
  {
    id: "S01",
    title: "开场提问",
    thumbnailUrl: "/opening.jpg",
    duration: 5.25,
    status: "ready",
  },
  { id: "S02", title: "生活场景", duration: 12, status: "pending" },
  {
    id: "S03",
    title: "产品演示",
    thumbnailUrl: "/demo.jpg",
    duration: 8,
    status: "warning",
    detail: "画面短于旁白",
  },
];

afterEach(cleanup);

it("selects a scene without changing which scene is active until its owner updates it", () => {
  const onSelect = vi.fn();
  const { rerender } = render(
    <ShotNavigator items={items} activeId="S01" onSelect={onSelect} />,
  );
  const opening = screen.getByRole("button", { name: /第 1 幕 S01/ });
  expect(opening).toHaveAttribute("aria-current", "step");
  const scene = screen.getByRole("button", { name: /第 2 幕 S02/ });
  fireEvent.click(scene);
  expect(onSelect).toHaveBeenCalledWith("S02");
  expect(opening).toHaveAttribute("aria-current", "step");
  rerender(<ShotNavigator items={items} activeId="S02" onSelect={onSelect} />);
  expect(scene).toHaveAttribute("aria-current", "step");
  expect(opening).not.toHaveAttribute("aria-current");
});

it("filters pending work while preserving original scene numbers and selection", () => {
  const onSelect = vi.fn();
  render(<ShotNavigator items={items} activeId="S03" onSelect={onSelect} />);
  fireEvent.click(screen.getByRole("button", { name: "待处理 2" }));
  expect(
    screen.queryByRole("button", { name: /第 1 幕 S01/ }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: /第 2 幕 S02/ }),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /第 3 幕 S03/ })).toHaveAttribute(
    "aria-current",
    "step",
  );
  expect(onSelect).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "全部 3" }));
  expect(
    screen.getByRole("button", { name: /第 1 幕 S01/ }),
  ).toBeInTheDocument();
});

it("shows an explicit empty state when no scenes need attention", () => {
  render(
    <ShotNavigator items={[items[0]]} activeId="S01" onSelect={vi.fn()} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "待处理 0" }));
  expect(screen.getByRole("status")).toHaveTextContent("所有分幕均已可用");
});

it("keeps broken thumbnails usable with a placeholder and accepts a new image URL", () => {
  const { rerender } = render(
    <ShotNavigator items={[items[0]]} activeId="S01" onSelect={vi.fn()} />,
  );
  const scene = screen.getByRole("button", { name: /第 1 幕 S01/ });
  const image = scene.querySelector("img")!;
  expect(image).toHaveAttribute("src", "/opening.jpg");
  fireEvent.error(image);
  expect(within(scene).getByText("暂无画面")).toBeInTheDocument();
  rerender(
    <ShotNavigator
      items={[{ ...items[0], thumbnailUrl: "/new-opening.jpg" }]}
      activeId="S01"
      onSelect={vi.fn()}
    />,
  );
  expect(scene.querySelector("img")).toHaveAttribute("src", "/new-opening.jpg");
});

it("keeps the selected scene in view when selection changes", () => {
  const scrollIntoView = vi.fn();
  const previous = HTMLElement.prototype.scrollIntoView;
  HTMLElement.prototype.scrollIntoView = scrollIntoView;
  try {
    const { rerender } = render(
      <ShotNavigator items={items} activeId="S01" onSelect={vi.fn()} />,
    );
    scrollIntoView.mockClear();
    rerender(<ShotNavigator items={items} activeId="S03" onSelect={vi.fn()} />);
    expect(scrollIntoView).toHaveBeenCalledOnce();
    expect(scrollIntoView.mock.instances[0]).toBe(
      screen.getByRole("button", { name: /第 3 幕 S03/ }),
    );
  } finally {
    HTMLElement.prototype.scrollIntoView = previous;
  }
});
