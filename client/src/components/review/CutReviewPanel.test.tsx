import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CutReviewPanel } from "./CutReviewPanel";
import type { Segment } from "@/lib/api";

const segments: Segment[] = [
  { id: "a", start: 0, end: 3, text: "观点", action: "keep" },
  { id: "b", start: 3, end: 5, text: "重说", action: "delete" },
  { id: "c", start: 5, end: 8, text: "解释", action: "keep" },
];
beforeEach(() => localStorage.clear());
afterEach(cleanup);

it("auditions both sides and restores only the deleted segments", async () => {
  const onAudition = vi.fn(),
    onRestore = vi.fn().mockResolvedValue(undefined);
  render(
    <CutReviewPanel
      taskId="one"
      segments={segments}
      onAudition={onAudition}
      onRestore={onRestore}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "听剪后" }));
  expect(onAudition).toHaveBeenCalledWith(
    [
      { start: 1, end: 3 },
      { start: 5, end: 7 },
    ],
    "剪后接缝",
  );
  fireEvent.click(screen.getByRole("button", { name: "恢复删除内容" }));
  await waitFor(() => expect(onRestore).toHaveBeenCalledWith(["b"]));
});

it("persists checked joins but invalidates a changed neighbouring sentence", () => {
  const props = {
    taskId: "one",
    segments,
    onAudition: vi.fn(),
    onRestore: vi.fn(),
  };
  const view = render(<CutReviewPanel {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "标记已检查" }));
  expect(screen.getByText("1 处剪切 · 0 处待检查")).toBeTruthy();
  view.unmount();
  const second = render(<CutReviewPanel {...props} />);
  expect(screen.getByText("1 处剪切 · 0 处待检查")).toBeTruthy();
  second.rerender(
    <CutReviewPanel
      {...props}
      segments={segments.map((s) =>
        s.id === "c" ? { ...s, text: "修改解释" } : s,
      )}
    />,
  );
  expect(screen.getByText("1 处剪切 · 1 处待检查")).toBeTruthy();
});
