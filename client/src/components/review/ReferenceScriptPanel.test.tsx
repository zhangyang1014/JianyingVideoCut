import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ReferenceScriptPanel } from "./ReferenceScriptPanel";
import {
  fetchReferenceScript,
  saveReferenceScript,
  type ReferenceScriptResult,
} from "@/lib/api";

vi.mock("@/lib/api", async () => ({
  ...(await vi.importActual("@/lib/api")),
  fetchReferenceScript: vi.fn(),
  saveReferenceScript: vi.fn(),
}));
const result: ReferenceScriptResult = {
  reference_script: "每天练习十分钟",
  report: {
    script_digest: "test",
    matched: 1,
    uncertain: 0,
    missing: 0,
    retained: 0,
    truncated: false,
    lines: [
      {
        index: 1,
        text: "每天练习十分钟",
        status: "matched",
        retained: false,
        candidates: [
          {
            segment_ids: ["a"],
            start: 3,
            end: 8,
            text: "每天练习十分钟",
            similarity: 1,
            coverage: 1,
            state: "deleted",
            warnings: [],
          },
        ],
      },
    ],
  },
};
const props = {
  taskId: "task",
  initialScript: result.reference_script,
  refreshKey: "1",
  onSaved: vi.fn(),
  onAudition: vi.fn(),
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchReferenceScript).mockResolvedValue(result);
});
afterEach(cleanup);

it("auditions the deleted source candidate with exact boundaries", async () => {
  render(<ReferenceScriptPanel {...props} />);
  fireEvent.click(
    await screen.findByRole("button", { name: "试听原稿第 1 句候选 1" }),
  );
  expect(props.onAudition).toHaveBeenCalledWith(
    [{ start: 3, end: 8 }],
    "原稿第 1 句 · 候选 1",
  );
  expect(screen.getByText("已匹配，但候选含删除内容，请核对")).toBeTruthy();
});

it("keeps unsaved edits when task refreshes and saves with the known previous script", async () => {
  const view = render(<ReferenceScriptPanel {...props} />);
  await screen.findByText("已匹配，但候选含删除内容，请核对");
  fireEvent.change(screen.getByRole("textbox"), {
    target: { value: "修改后的原稿" },
  });
  view.rerender(<ReferenceScriptPanel {...props} refreshKey="2" />);
  await waitFor(() => expect(fetchReferenceScript).toHaveBeenCalledTimes(2));
  await screen.findByText("已匹配，但候选含删除内容，请核对");
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(
    "修改后的原稿",
  );
  vi.mocked(saveReferenceScript).mockResolvedValue({
    ...result,
    reference_script: "修改后的原稿",
  });
  fireEvent.click(screen.getByRole("button", { name: "保存并匹配" }));
  await waitFor(() =>
    expect(saveReferenceScript).toHaveBeenCalledWith(
      "task",
      "修改后的原稿",
      result.reference_script,
    ),
  );
  await waitFor(() => expect(props.onSaved).toHaveBeenCalledOnce());
});

it("preserves draft and shows server conflict without claiming it was saved", async () => {
  vi.mocked(saveReferenceScript).mockRejectedValue(
    new Error("原稿已在其他窗口更新，请刷新后重试"),
  );
  render(<ReferenceScriptPanel {...props} />);
  await screen.findByText("已匹配，但候选含删除内容，请核对");
  fireEvent.change(screen.getByRole("textbox"), {
    target: { value: "新原稿" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存并匹配" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("其他窗口更新");
  expect(props.onSaved).not.toHaveBeenCalled();
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(
    "新原稿",
  );
});
