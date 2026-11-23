import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { WritingRuleViewer } from "./WritingRuleViewer";
import type { PresenterWritingRules } from "@/lib/productions-api";

const mocks = vi.hoisted(() => ({ get: vi.fn(), copy: vi.fn() }));
vi.mock("@/lib/productions-api", () => ({
  getPresenterWritingRules: mocks.get,
}));
const fullText =
  "---\nname: test-structure\n---\n# 结构规则\n" +
  "完整规则，不省略正文。\n".repeat(80) +
  "## 参考资料\n最后一行也要保留。";
function rules(): PresenterWritingRules {
  return {
    stage: "structure",
    skill_name: "test-structure",
    title: "结构规则",
    timing: "起稿时调用",
    documents: [
      {
        title: "结构规则",
        path: ".cursor/skills/test-structure/SKILL.md",
        kind: "skill",
        content: fullText,
        sha256: "a".repeat(64),
      },
    ],
    effective_prompt: "实际加载的固定规则正文。",
    style_source: "project_snapshot",
  };
}
beforeEach(() => {
  vi.clearAllMocks();
  mocks.get.mockResolvedValue(rules());
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: mocks.copy.mockResolvedValue(undefined) },
  });
});
afterEach(cleanup);

it("loads on demand and exposes the complete source and the actual fixed rules separately", async () => {
  render(
    <WritingRuleViewer
      stage="structure"
      presenterId="lina"
      projectId="project"
    />,
  );
  expect(mocks.get).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("查看完整 Skill 与规则 · 结构"));
  await waitFor(() =>
    expect(mocks.get).toHaveBeenCalledWith("structure", "lina", "project"),
  );
  await waitFor(() =>
    expect(document.querySelector("pre")?.textContent).toBe(fullText),
  );
  fireEvent.click(screen.getByRole("button", { name: "复制完整原文" }));
  await waitFor(() => expect(mocks.copy).toHaveBeenCalledWith(fullText));
  fireEvent.click(screen.getByText("核对本阶段实际加载的固定规则"));
  expect(screen.getByText("实际加载的固定规则正文。")).toBeVisible();
  expect(document.querySelectorAll("textarea, input")).toHaveLength(0);
});

it("keeps a retry action when complete rules cannot be read", async () => {
  mocks.get.mockRejectedValueOnce(new Error("暂时无法读取"));
  render(<WritingRuleViewer stage="polish" presenterId="lina" />);
  fireEvent.click(screen.getByText("查看完整 Skill 与规则 · 文字"));
  expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法读取");
  fireEvent.click(screen.getByRole("button", { name: "重新读取完整规则" }));
  await waitFor(() =>
    expect(document.querySelector("pre")?.textContent).toBe(fullText),
  );
  expect(mocks.get).toHaveBeenCalledTimes(2);
});
