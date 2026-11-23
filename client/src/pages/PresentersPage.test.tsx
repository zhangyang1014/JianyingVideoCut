import React from "react";
import {
  cleanup,
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PresentersPage from "./PresentersPage";
import { presenterConfig } from "@/lib/presenters";
import type { ProductionPresenter } from "@/lib/productions-api";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  catalog: vi.fn(),
}));
vi.mock("@/lib/productions-api", () => ({
  listProductionPresenters: mocks.list,
  createProductionPresenter: mocks.create,
  updateProductionPresenter: mocks.update,
  getHeyGenCatalog: mocks.catalog,
}));
vi.mock("wouter", () => ({
  Link: ({ href, children, ...props }: any) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

function profile(): ProductionPresenter {
  return {
    ...presenterConfig(),
    id: "lina",
    revision: 2,
    archived: false,
    name: "Lina",
    primary_audience: "小学孩子的妈妈",
    address: ["各位妈妈"],
    text_style: "自然的短句",
    structure_style: "问题 → 动作 → 解释",
  };
}
beforeEach(() => {
  vi.clearAllMocks();
  mocks.list.mockResolvedValue([profile()]);
});
afterEach(cleanup);

describe("数字人管理", () => {
  it("shows a retry after load failure without presenting an empty catalog as a new person", async () => {
    mocks.list.mockRejectedValueOnce(new Error("读取超时，请重试"));
    render(<PresentersPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("读取超时");
    expect(screen.queryByLabelText("数字人名称")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新建数字人" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "重新读取数字人档案" }));
    expect(await screen.findByLabelText("数字人名称")).toHaveValue("Lina");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("retains separate drafts when switching between a profile and a new person", async () => {
    render(<PresentersPage />);
    await screen.findByLabelText("文字风格");
    fireEvent.change(screen.getByLabelText("文字风格"), {
      target: { value: "本次未保存" },
    });
    expect(
      screen.queryByRole("link", { name: "以此数字人创建视频" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "新建数字人" }));
    fireEvent.change(screen.getByLabelText("数字人名称"), {
      target: { value: "新档案草稿" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Lina/ }));
    expect(screen.getByLabelText("文字风格")).toHaveValue("本次未保存");
    fireEvent.click(screen.getByRole("button", { name: "新建数字人" }));
    expect(screen.getByLabelText("数字人名称")).toHaveValue("新档案草稿");
  });

  it("blocks duplicate saves and navigation to another editor while a save is pending", async () => {
    let finish!: (value: ProductionPresenter) => void;
    mocks.update.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<PresentersPage />);
    await screen.findByLabelText("文字风格");
    const form = screen
      .getByRole("button", { name: "保存数字人档案" })
      .closest("form")!;
    fireEvent.submit(form);
    fireEvent.submit(form);
    expect(mocks.update).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "新建数字人" })).toBeDisabled();
    await act(async () => finish({ ...profile(), revision: 3 }));
    expect(
      screen.getByRole("button", { name: "保存数字人档案" }),
    ).toBeEnabled();
  });

  it("rebases disjoint edits after a revision conflict without overwriting newer settings", async () => {
    mocks.update.mockRejectedValueOnce(
      Object.assign(new Error("档案已更新"), { status: 409 }),
    );
    const latest = { ...profile(), revision: 3, address: ["朋友们"] };
    render(<PresentersPage />);
    await screen.findByLabelText("文字风格");
    fireEvent.change(screen.getByLabelText("文字风格"), {
      target: { value: "本次编辑" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存数字人档案" }));
    await screen.findByText("档案已更新");
    mocks.list.mockResolvedValueOnce([latest]);
    fireEvent.click(
      screen.getByRole("button", { name: "读取最新档案并保留编辑" }),
    );
    await waitFor(() =>
      expect(screen.getByLabelText("观众称呼")).toHaveValue("朋友们"),
    );
    expect(screen.getByLabelText("文字风格")).toHaveValue("本次编辑");
    mocks.update.mockResolvedValueOnce({
      ...latest,
      text_style: "本次编辑",
      revision: 4,
    });
    fireEvent.click(screen.getByRole("button", { name: "保存数字人档案" }));
    await waitFor(() =>
      expect(mocks.update).toHaveBeenLastCalledWith(
        latest,
        expect.objectContaining({
          text_style: "本次编辑",
          address: ["朋友们"],
        }),
        false,
      ),
    );
  });

  it("requires an explicit field choice when the same setting changed in both versions", async () => {
    mocks.update.mockRejectedValueOnce(
      Object.assign(new Error("档案已更新"), { status: 409 }),
    );
    render(<PresentersPage />);
    await screen.findByLabelText("文字风格");
    fireEvent.change(screen.getByLabelText("文字风格"), {
      target: { value: "我的新风格" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存数字人档案" }));
    await screen.findByText("档案已更新");
    mocks.list.mockResolvedValueOnce([
      { ...profile(), revision: 3, text_style: "另一处的新风格" },
    ]);
    fireEvent.click(
      screen.getByRole("button", { name: "读取最新档案并保留编辑" }),
    );
    await screen.findByRole("region", { name: "处理档案冲突" });
    expect(
      screen.getByRole("button", { name: "确认选择并继续编辑" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "保存数字人档案" }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole("radio", { name: "最新档案：另一处的新风格" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "确认选择并继续编辑" }));
    expect(screen.getByLabelText("文字风格")).toHaveValue("另一处的新风格");
    expect(
      screen.getByRole("button", { name: "保存数字人档案" }),
    ).toBeEnabled();
  });

  it("explains invalid list limits before submitting a rejected save", async () => {
    render(<PresentersPage />);
    await screen.findByLabelText("观众称呼");
    fireEvent.change(screen.getByLabelText("观众称呼"), {
      target: { value: "称".repeat(201) },
    });
    expect(
      screen.getByRole("button", { name: "保存数字人档案" }),
    ).toBeDisabled();
    expect(
      screen.getByText("观众称呼最多 30 条，每条须为 1–200 字。"),
    ).toBeInTheDocument();
    expect(mocks.update).not.toHaveBeenCalled();
  });
  it("loads and updates long-term settings with the current revision", async () => {
    const saved = {
      ...profile(),
      text_style: "用一个具体例子解释",
      revision: 3,
    };
    mocks.update.mockResolvedValue(saved);
    render(<PresentersPage />);
    expect(await screen.findByLabelText("文字风格")).toHaveValue("自然的短句");
    expect(
      screen.getByRole("heading", { name: "结构风格 · Lina 短视频结构" }),
    ).toBeInTheDocument();
    expect(screen.getByText("lina-short-video-structure")).toBeInTheDocument();
    expect(
      screen.getByText("digital-presenter-spoken-polish"),
    ).toBeInTheDocument();
    expect(mocks.list).toHaveBeenCalledWith(true);
    expect(
      screen.getByRole("link", { name: "以此数字人创建视频" }),
    ).toHaveAttribute("href", "/productions?presenter=lina");
    fireEvent.change(screen.getByLabelText("文字风格"), {
      target: { value: saved.text_style },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存数字人档案" }));
    await waitFor(() =>
      expect(mocks.update).toHaveBeenCalledWith(
        profile(),
        expect.objectContaining({ text_style: saved.text_style }),
        false,
      ),
    );
    await waitFor(() =>
      expect(screen.getByLabelText("文字风格")).toHaveValue(saved.text_style),
    );
  });

  it("creates a different person with independent audience and style", async () => {
    mocks.create.mockResolvedValue({
      ...profile(),
      id: "presenter_new",
      name: "小禾",
      revision: 1,
    });
    render(<PresentersPage />);
    await screen.findByLabelText("数字人名称");
    fireEvent.click(screen.getByRole("button", { name: "新建数字人" }));
    expect(
      screen.getByText("digital-presenter-script-structure"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("lina-short-video-structure"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "保存数字人档案" }),
    ).toBeDisabled();
    fireEvent.change(screen.getByLabelText("数字人名称"), {
      target: { value: "小禾" },
    });
    fireEvent.change(screen.getByLabelText("主要受众"), {
      target: { value: "刚工作的年轻人" },
    });
    fireEvent.change(screen.getByLabelText("观众称呼"), {
      target: { value: "各位朋友\n大家" },
    });
    fireEvent.change(screen.getByLabelText("结构风格"), {
      target: { value: "结论 → 原因 → 行动" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存数字人档案" }));
    await waitFor(() =>
      expect(mocks.create).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "小禾",
          primary_audience: "刚工作的年轻人",
          address: ["各位朋友", "大家"],
          structure_style: "结论 → 原因 → 行动",
          guidance: [],
        }),
      ),
    );
  });

  it("keeps the draft on failure and can archive an existing person", async () => {
    mocks.update.mockRejectedValueOnce(new Error("档案已更新，请重新打开"));
    render(<PresentersPage />);
    await screen.findByLabelText("数字人名称");
    fireEvent.change(screen.getByLabelText("文字风格"), {
      target: { value: "保留本次编辑" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存数字人档案" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("档案已更新");
    expect(screen.getByLabelText("文字风格")).toHaveValue("保留本次编辑");
    mocks.update.mockResolvedValueOnce({
      ...profile(),
      archived: true,
      revision: 3,
    });
    fireEvent.click(screen.getByRole("button", { name: "停用数字人" }));
    await waitFor(() =>
      expect(mocks.update).toHaveBeenLastCalledWith(
        profile(),
        expect.objectContaining({ text_style: "保留本次编辑" }),
        true,
      ),
    );
    expect(
      await screen.findByRole("button", { name: "重新启用" }),
    ).toBeInTheDocument();
  });
});
