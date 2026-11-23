import React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ProductionWorkbench from "./ProductionWorkbench";
import type {
  ProductionAsset,
  ProductionProject,
  ProductionShot,
} from "@/lib/productions-api";
import { presenterConfig } from "@/lib/presenters";

const mock = vi.hoisted(() => ({
  get: vi.fn(),
  list: vi.fn(),
  presenters: vi.fn(),
  action: vi.fn(),
  create: vi.fn(),
  params: { id: "project" } as { id?: string },
}));
vi.mock("wouter", () => ({
  useParams: () => mock.params,
  useLocation: () => ["/productions/project", vi.fn()],
  Link: ({ href, children, ...props }: any) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));
vi.mock("@/lib/productions-api", () => ({
  getProduction: mock.get,
  listProductions: mock.list,
  listProductionPresenters: mock.presenters,
  productionAction: mock.action,
  createProduction: mock.create,
  uploadProductionFile: vi.fn(),
  cancelProductionRender: vi.fn(),
  getHeyGenSettings: vi.fn().mockResolvedValue({ has_key: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function project(blocked = true): ProductionProject {
  const shot: ProductionShot = {
    shot_id: "S01",
    revision: 1,
    purpose: "产品展示",
    visual: "展示完整产品",
    spoken_text: "产品介绍",
    subtitle: "产品介绍",
    target_duration: 5,
    source_type: "shoot",
    voice_mode: "original",
    framing: "contain",
    trim_pauses: true,
    tool: "通用",
    reference_ids: [],
    fact_ids: [],
    filming_guide: "正面拍摄",
    prompt: "当前制作提示词",
    selection: null,
    voiceover: null,
    verified_signature: null,
    review_note: "",
    required: true,
  };
  return {
    id: "project",
    name: "制作项目",
    project_type: "selling_video",
    presenter_id: null,
    workflow_notes: "",
    platform: "抖音",
    target_duration: 45,
    selling_points: "已确认事实",
    offer: "",
    revision: 7,
    content_revision: 3,
    stage: "collecting",
    selected_direction_id: "demo",
    brand: {
      id: "brand",
      name: "品牌",
      product: "水杯",
      style: "自然",
      sources: [],
      facts: [],
    },
    directions: [],
    shots: [shot],
    assets: [],
    music_asset_id: null,
    renders: [],
    heygen_jobs: [],
    readiness: { S01: blocked ? ["S01 待补充视频素材"] : [] },
    planning_status: "idle",
    planning_note: "",
    warnings: [],
  };
}
function videoAsset(): ProductionAsset {
  return {
    id: "video",
    filename: "口播.mp4",
    url: "/video.mp4",
    fingerprint: "demo",
    kind: "video",
    role: "footage",
    shot_id: "S01",
    version: 1,
    duration: 7.25,
    width: 1080,
    height: 1920,
    has_audio: true,
    frames: [],
    cues: [],
    usable_start: 0,
    usable_end: 7.25,
    analysis_status: "pending",
    analysis_note: "",
    visual_summary: "",
  };
}

function studioProject(): ProductionProject {
  const p = project(false);
  p.project_type = "digital_presenter";
  p.presenter_id = "lina";
  p.narration_draft = null;
  const master: ProductionAsset = {
    ...videoAsset(),
    filename: "完整母片.mp4",
    url: "/master.mp4",
    fingerprint: "master-v1",
    shot_id: null,
    duration: 30,
    usable_end: 30,
    analysis_status: "done",
    frames: ["/whole-source-frame.jpg"],
    origin: { kind: "original", provider: "" },
  };
  const voice: ProductionAsset = {
    ...master,
    id: "voice",
    filename: "画外音.wav",
    url: "/voice.wav",
    kind: "audio",
    role: "voiceover",
    frames: [],
    width: 0,
    height: 0,
  };
  const opening: ProductionShot = {
    ...p.shots[0],
    purpose: "开场提问",
    spoken_text: "孩子放学回家想喝什么？",
    selection: { asset_id: master.id, start: 2, end: 6 },
  };
  const demonstration: ProductionShot = {
    ...opening,
    shot_id: "S02",
    purpose: "家庭演示",
    visual: "妈妈给孩子接一杯温水",
    spoken_text: "用常用杯子接一杯温水。",
    selection: { asset_id: master.id, start: 12, end: 18 },
    voice_mode: "voiceover",
    voiceover: { asset_id: voice.id, start: 0, end: 6 },
  };
  p.shots = [opening, demonstration];
  p.assets = [master, voice];
  p.readiness = { S01: [], S02: ["S02 素材内容待检查，或文案/选段已变更"] };
  return p;
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  mock.get.mockReset();
  mock.action.mockReset();
  mock.params = { id: "project" };
  mock.list.mockResolvedValue([]);
  mock.presenters.mockResolvedValue([
    {
      id: "lina",
      name: "Lina",
      revision: 1,
      archived: false,
      primary_audience: "中国小学至初中孩子的妈妈",
      secondary_audience: "幼儿园和高中生的妈妈",
      address: ["各位妈妈", "你家孩子", "你家宝宝"],
      guidance: ["用家庭生活和陪孩子学习的真实场景切入。"],
      text_style: "自然的日常短句",
      structure_style: "具体场景 → 第一步 → 分层解释 → 行动",
    },
  ]);
  mock.get.mockResolvedValue(project());
});

describe("数字人分幕工作台", () => {
  it("mounts only the selected scene's video and uses a thumbnail from each selected range", async () => {
    const p = studioProject();
    mock.get.mockResolvedValue(p);
    const { container } = render(<ProductionWorkbench />);

    const first = await screen.findByRole("button", {
      name: /第 1 幕 S01 开场提问/,
    });
    const second = screen.getByRole("button", { name: /第 2 幕 S02 家庭演示/ });
    const firstThumbnail = new URL(
      first.querySelector("img")!.getAttribute("src")!,
      "http://localhost",
    );
    const secondThumbnail = new URL(
      second.querySelector("img")!.getAttribute("src")!,
      "http://localhost",
    );
    expect(firstThumbnail.pathname).toBe(
      "/api/productions/project/assets/video/thumbnail",
    );
    expect(secondThumbnail.pathname).toBe(firstThumbnail.pathname);
    expect(firstThumbnail.searchParams.get("v")).toBe("master-v1");
    const firstTime = Number(firstThumbnail.searchParams.get("t"));
    const secondTime = Number(secondThumbnail.searchParams.get("t"));
    expect(firstTime).toBeGreaterThanOrEqual(2);
    expect(firstTime).toBeLessThan(6);
    expect(secondTime).toBeGreaterThanOrEqual(12);
    expect(secondTime).toBeLessThan(18);
    expect(secondTime).not.toBe(firstTime);
    expect(first.querySelector("img")).not.toHaveAttribute(
      "src",
      "/whole-source-frame.jpg",
    );

    const firstEditor = screen.getByRole("article", { name: "S01 当前幕编辑" });
    const firstVideo = firstEditor.querySelector("video");
    expect(container.querySelectorAll("video")).toHaveLength(1);
    expect(firstVideo).toHaveAttribute(
      "src",
      expect.stringContaining("/master.mp4"),
    );
    expect(
      screen.queryByRole("article", { name: "S02 当前幕编辑" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "上一幕", exact: true }),
    ).toBeDisabled();
    fireEvent.click(second);
    const secondEditor = screen.getByRole("article", {
      name: "S02 当前幕编辑",
    });
    expect(container.querySelectorAll("video")).toHaveLength(1);
    expect(secondEditor.querySelector("video")).not.toBe(firstVideo);
    expect(firstVideo).not.toBeInTheDocument();
    expect(second).toHaveAttribute("aria-current", "step");
    expect(first).not.toHaveAttribute("aria-current");
    expect(
      screen.getByRole("button", { name: "下一幕", exact: true }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole("button", { name: "上一幕", exact: true }),
    );
    expect(
      screen.getByRole("article", { name: "S01 当前幕编辑" }),
    ).toBeInTheDocument();
  });

  it("shows script, footage selection and independent voiceover in their own inspector tabs", async () => {
    mock.get.mockResolvedValue(studioProject());
    render(<ProductionWorkbench />);
    fireEvent.click(
      await screen.findByRole("button", { name: /第 2 幕 S02 家庭演示/ }),
    );

    const scriptTab = screen.getByRole("tab", { name: "文案", exact: true });
    expect(scriptTab).toHaveAttribute("aria-selected", "true");
    expect(
      within(screen.getByRole("tabpanel", { name: "文案" })).getByText(
        "用常用杯子接一杯温水。",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("combobox", { name: "画面素材版本" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "画面", exact: true }));
    const visual = within(screen.getByRole("tabpanel", { name: "画面" }));
    expect(visual.getByRole("combobox", { name: "画面素材版本" })).toHaveValue(
      "video",
    );
    expect(visual.getByRole("spinbutton", { name: "video入点" })).toHaveValue(
      12,
    );
    expect(visual.getByRole("spinbutton", { name: "video出点" })).toHaveValue(
      18,
    );
    fireEvent.change(visual.getByRole("spinbutton", { name: "video入点" }), {
      target: { value: "13" },
    });
    expect(
      screen.queryByRole("combobox", { name: "旁白素材版本" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "声音", exact: true }));
    const audio = within(screen.getByRole("tabpanel", { name: "声音" }));
    expect(audio.getByRole("combobox", { name: "旁白素材版本" })).toHaveValue(
      "voice",
    );
    expect(
      audio.getByRole("spinbutton", { name: "voiceover出点" }),
    ).toHaveValue(6);
    fireEvent.change(audio.getByRole("spinbutton", { name: "voiceover出点" }), {
      target: { value: "5" },
    });
    fireEvent.click(screen.getByRole("tab", { name: "画面", exact: true }));
    expect(screen.getByRole("spinbutton", { name: "video入点" })).toHaveValue(
      13,
    );
    fireEvent.click(screen.getByRole("tab", { name: "声音", exact: true }));
    expect(
      screen.getByRole("spinbutton", { name: "voiceover出点" }),
    ).toHaveValue(5);
    expect(mock.action).not.toHaveBeenCalled();
    expect(scriptTab).toHaveAttribute("aria-selected", "false");
    expect(
      screen
        .getByRole("article", { name: "S02 当前幕编辑" })
        .querySelectorAll("video"),
    ).toHaveLength(1);
  });

  it("filters pending scenes without changing the current scene and restores its tools through navigation", async () => {
    mock.get.mockResolvedValue(studioProject());
    render(<ProductionWorkbench />);
    await screen.findByRole("article", { name: "S01 当前幕编辑" });
    fireEvent.click(screen.getByRole("button", { name: "待处理 1" }));
    expect(
      screen.queryByRole("button", { name: /第 1 幕 S01/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("article", { name: "S01 当前幕编辑" }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: /第 2 幕 S02 家庭演示/ }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "完整口播稿", exact: true }),
    );
    expect(
      screen.getByRole("heading", { name: "数字人口播稿" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("article", { name: "S02 当前幕编辑" }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "返回当前幕", exact: true }),
    );
    expect(
      screen.getByRole("article", { name: "S02 当前幕编辑" }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "创意方向", exact: true }),
    );
    expect(
      screen.getByRole("heading", { name: "选择创意方向" }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: /第 2 幕 S02 家庭演示/ }),
    );
    expect(
      screen.queryByRole("heading", { name: "选择创意方向" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("article", { name: "S02 当前幕编辑" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "全部 2" }));
    expect(
      screen.getByRole("button", { name: /第 1 幕 S01 开场提问/ }),
    ).toBeInTheDocument();
  });

  it("keeps the selected scene after an API update and refreshes its readiness", async () => {
    const p = studioProject();
    mock.get.mockResolvedValue(p);
    mock.action.mockResolvedValue({
      ...p,
      revision: 8,
      readiness: { S01: [], S02: [] },
    });
    render(<ProductionWorkbench />);
    fireEvent.click(
      await screen.findByRole("button", { name: /第 2 幕 S02 家庭演示/ }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "已检查画面与声音，标记可用" }),
    );

    await waitFor(() =>
      expect(mock.action).toHaveBeenCalledWith(
        expect.objectContaining({ id: p.id, revision: 7 }),
        "shots/S02/verify",
        {},
        undefined,
      ),
    );
    const current = await screen.findByRole("button", {
      name: /第 2 幕 S02 家庭演示，可用/,
    });
    expect(current).toHaveAttribute("aria-current", "step");
    expect(
      screen.getByRole("article", { name: "S02 当前幕编辑" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "待处理 0" }),
    ).toBeInTheDocument();
  });

  it("protects changed scene copy across scene, tool and stage navigation, but allows unchanged edits", async () => {
    mock.get.mockResolvedValue(studioProject());
    render(<ProductionWorkbench />);
    fireEvent.click(
      await screen.findByRole("button", { name: "修改文案与镜头" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "下一幕", exact: true }),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "上一幕", exact: true }),
    );
    fireEvent.click(screen.getByRole("button", { name: "修改文案与镜头" }));
    fireEvent.change(screen.getByRole("textbox", { name: "口播 / 旁白" }), {
      target: { value: "还没保存的开场" },
    });
    for (const label of [
      "下一幕",
      "完整口播稿",
      "数字人设定",
      "01 品牌资料",
      "03 成片与交付",
      "成片与交付",
    ]) {
      fireEvent.click(screen.getByRole("button", { name: label, exact: true }));
      const dialog = within(screen.getByRole("dialog"));
      expect(
        dialog.getByRole("heading", { name: "未保存当前修改" }),
      ).toBeInTheDocument();
      fireEvent.click(dialog.getByRole("button", { name: "继续编辑" }));
      expect(screen.getByRole("textbox", { name: "口播 / 旁白" })).toHaveValue(
        "还没保存的开场",
      );
    }
    fireEvent.click(
      screen.getByRole("button", { name: /第 2 幕 S02 家庭演示/ }),
    );
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "放弃修改并切换",
      }),
    );
    expect(
      screen.getByRole("article", { name: "S02 当前幕编辑" }),
    ).toBeInTheDocument();
    expect(mock.action).not.toHaveBeenCalled();
  });

  it("preserves the other unsaved selection when one channel is saved and clears protection only after both save", async () => {
    const p = studioProject();
    const voiceSaved: ProductionProject = {
      ...p,
      revision: 8,
      shots: [
        p.shots[0],
        {
          ...p.shots[1],
          revision: 2,
          voiceover: { asset_id: "voice", start: 0, end: 5 },
        },
      ],
    };
    const bothSaved: ProductionProject = {
      ...voiceSaved,
      revision: 9,
      shots: [
        p.shots[0],
        {
          ...voiceSaved.shots[1],
          revision: 3,
          selection: { asset_id: "video", start: 13, end: 18 },
        },
      ],
    };
    mock.get.mockResolvedValue(p);
    mock.action
      .mockResolvedValueOnce(voiceSaved)
      .mockResolvedValueOnce(bothSaved);
    render(<ProductionWorkbench />);
    fireEvent.click(
      await screen.findByRole("button", { name: /第 2 幕 S02 家庭演示/ }),
    );
    fireEvent.click(screen.getByRole("tab", { name: "画面", exact: true }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "video入点" }), {
      target: { value: "13" },
    });
    fireEvent.click(screen.getByRole("tab", { name: "声音", exact: true }));
    fireEvent.change(
      screen.getByRole("spinbutton", { name: "voiceover出点" }),
      { target: { value: "5" } },
    );
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "使用此选段" }));
    });
    await waitFor(() => expect(mock.action).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "使用此选段" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("tab", { name: "画面", exact: true }));
    expect(screen.getByRole("spinbutton", { name: "video入点" })).toHaveValue(
      13,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "上一幕", exact: true }),
    );
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "继续编辑",
      }),
    );
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "使用此选段" }));
    });
    await waitFor(() => expect(mock.action).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "使用此选段" })).toBeEnabled(),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "上一幕", exact: true }),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(
      screen.getByRole("article", { name: "S01 当前幕编辑" }),
    ).toBeInTheDocument();
  });

  it("keeps full-script changes after a failed save and clears the leave warning after a successful retry", async () => {
    const p = studioProject();
    mock.get.mockResolvedValue(p);
    mock.action
      .mockRejectedValueOnce(new Error("保存失败"))
      .mockResolvedValueOnce({
        ...p,
        revision: 8,
        narration_draft: { text: "新的全文口播", shots: [] },
      });
    render(<ProductionWorkbench />);
    fireEvent.click(
      await screen.findByRole("button", { name: "完整口播稿", exact: true }),
    );
    fireEvent.click(screen.getByRole("button", { name: "编辑口播稿" }));
    fireEvent.change(screen.getByRole("textbox", { name: "口播稿" }), {
      target: { value: "新的全文口播" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存口播稿" }));
    await waitFor(() => expect(mock.get).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "返回当前幕" }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "继续编辑",
      }),
    );
    expect(screen.getByRole("textbox", { name: "口播稿" })).toHaveValue(
      "新的全文口播",
    );
    fireEvent.click(screen.getByRole("button", { name: "保存口播稿" }));
    await screen.findByRole("button", { name: "编辑口播稿" });
    fireEvent.click(screen.getByRole("button", { name: "返回当前幕" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(
      screen.getByRole("article", { name: "S01 当前幕编辑" }),
    ).toBeInTheDocument();
  });

  it("protects unsaved presenter settings while leaving an unchanged editor freely", async () => {
    const p = studioProject();
    p.presenter_snapshot = {
      ...presenterConfig(),
      id: "lina",
      revision: 1,
      archived: false,
      name: "Lina",
      primary_audience: "妈妈",
      address: ["妈妈们"],
      text_style: "原始风格",
    };
    mock.get.mockResolvedValue(p);
    mock.action.mockResolvedValue({
      ...p,
      revision: 8,
      presenter_snapshot: { ...p.presenter_snapshot, text_style: "本次新风格" },
    });
    render(<ProductionWorkbench />);
    fireEvent.click(
      await screen.findByRole("button", { name: "数字人设定", exact: true }),
    );
    fireEvent.click(screen.getByText("本次数字人设定 · Lina"));
    fireEvent.click(screen.getByRole("button", { name: "临时编辑本次设定" }));
    fireEvent.click(screen.getByRole("button", { name: "返回当前幕" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "数字人设定", exact: true }),
    );
    fireEvent.click(screen.getByText("本次数字人设定 · Lina"));
    fireEvent.click(screen.getByRole("button", { name: "临时编辑本次设定" }));
    fireEvent.change(screen.getByRole("textbox", { name: "文字风格" }), {
      target: { value: "本次新风格" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: /第 2 幕 S02 家庭演示/ }),
    );
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "继续编辑",
      }),
    );
    expect(screen.getByRole("textbox", { name: "文字风格" })).toHaveValue(
      "本次新风格",
    );
    fireEvent.click(screen.getByRole("button", { name: "保存本次设定" }));
    await screen.findByRole("button", { name: "临时编辑本次设定" });
    fireEvent.click(screen.getByRole("button", { name: "返回当前幕" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("falls back to the first current scene after reloading the same project with a removed selection", async () => {
    const p = studioProject();
    const replacement: ProductionShot = {
      ...p.shots[0],
      shot_id: "S03",
      purpose: "新的开场",
    };
    const refreshed = {
      ...p,
      revision: 8,
      shots: [replacement],
      readiness: { S03: [] },
    };
    mock.get.mockResolvedValueOnce(p).mockResolvedValue(refreshed);
    mock.action.mockRejectedValue(new Error("保存结果未确认"));
    render(<ProductionWorkbench />);
    fireEvent.click(
      await screen.findByRole("button", { name: /第 2 幕 S02 家庭演示/ }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "已检查画面与声音，标记可用" }),
    );

    expect(
      await screen.findByRole("article", { name: "S03 当前幕编辑" }),
    ).toBeInTheDocument();
    expect(mock.get).toHaveBeenCalledTimes(2);
    expect(mock.get).toHaveBeenLastCalledWith(p.id);
    expect(
      screen.queryByRole("button", { name: /S02 家庭演示/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /第 1 幕 S03 新的开场/ }),
    ).toHaveAttribute("aria-current", "step");
    expect(
      screen.getByRole("button", { name: "上一幕", exact: true }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "下一幕", exact: true }),
    ).toBeDisabled();
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("卖货视频工作台", () => {
  it("requires a selected person and resets overrides when switching people or restoring defaults", async () => {
    mock.params = {};
    const lina = {
      ...presenterConfig(),
      id: "lina",
      revision: 1,
      archived: false,
      name: "Lina",
      primary_audience: "妈妈",
      address: ["各位妈妈"],
      text_style: "自然",
    };
    const another = {
      ...lina,
      id: "another",
      name: "小禾",
      primary_audience: "年轻人",
      address: ["朋友们"],
      text_style: "简洁",
    };
    mock.presenters.mockResolvedValue([lina, another]);
    render(<ProductionWorkbench />);
    fireEvent.change(screen.getByLabelText("项目类型"), {
      target: { value: "digital_presenter" },
    });
    fireEvent.change(screen.getByLabelText("项目名称"), {
      target: { value: "测试项目" },
    });
    fireEvent.change(screen.getByLabelText("主推产品"), {
      target: { value: "书" },
    });
    await screen.findByRole("option", { name: "Lina" });
    expect(screen.getByRole("button", { name: "建立制作项目" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("数字人"), {
      target: { value: "lina" },
    });
    fireEvent.change(screen.getByLabelText("文字风格"), {
      target: { value: "临时改写" },
    });
    fireEvent.click(screen.getByRole("button", { name: "恢复数字人默认设定" }));
    expect(screen.getByLabelText("文字风格")).toHaveValue("自然");
    fireEvent.change(screen.getByLabelText("数字人"), {
      target: { value: "another" },
    });
    expect(screen.getByLabelText("主要受众")).toHaveValue("年轻人");
    expect(screen.getByLabelText("文字风格")).toHaveValue("简洁");
    expect(screen.getByLabelText("观众称呼")).toHaveValue("朋友们");
    fireEvent.change(screen.getByLabelText("项目类型"), {
      target: { value: "selling_video" },
    });
    expect(screen.queryByLabelText("文字风格")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("口播目标")).not.toBeInTheDocument();
  });

  it("prefers the project snapshot over a later global profile and saves local edits separately", async () => {
    const p = project();
    p.project_type = "digital_presenter";
    p.presenter_id = "lina";
    p.presenter_snapshot = {
      ...presenterConfig(),
      id: "lina",
      revision: 1,
      archived: false,
      name: "Lina 旧档案",
      primary_audience: "本片受众",
      address: ["妈妈们"],
      text_style: "本片原始风格",
    };
    mock.get.mockResolvedValue(p);
    mock.action.mockResolvedValue(p);
    render(<ProductionWorkbench />);
    fireEvent.click(
      await screen.findByRole("button", { name: "数字人设定", exact: true }),
    );
    fireEvent.click(await screen.findByText("本次数字人设定 · Lina 旧档案"));
    expect(screen.getByText("本片受众")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "临时编辑本次设定" }));
    expect(screen.getByLabelText("文字风格")).toHaveValue("本片原始风格");
    fireEvent.change(screen.getByLabelText("文字风格"), {
      target: { value: "只改本片" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存本次设定" }));
    await waitFor(() =>
      expect(mock.action).toHaveBeenCalledWith(
        expect.objectContaining({ id: p.id }),
        "presenter",
        expect.objectContaining({
          presenter_id: "lina",
          presenter_overrides: expect.objectContaining({
            text_style: "只改本片",
          }),
        }),
        "PUT",
      ),
    );
  });

  it("creates a separate digital-presenter project from the type selector", async () => {
    mock.params = {};
    mock.create.mockResolvedValue({
      ...project(),
      project_type: "digital_presenter",
      presenter_id: "lina",
    });
    render(<ProductionWorkbench />);
    fireEvent.change(screen.getByLabelText("项目类型"), {
      target: { value: "digital_presenter" },
    });
    expect(screen.getByLabelText("参考片长（秒，选填）")).toHaveValue(null);
    const intent = screen.getByRole("combobox", { name: "口播目标" });
    expect(intent).toHaveValue("");
    expect(
      within(intent)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["沿用默认写法", "卖货转化", "选购对比", "经验分享"]);
    fireEvent.change(intent, { target: { value: "product_sales" } });
    await screen.findByRole("option", { name: "Lina" });
    expect(screen.getByLabelText("数字人")).toHaveValue("");
    fireEvent.change(screen.getByLabelText("数字人"), {
      target: { value: "lina" },
    });
    expect(screen.getByLabelText("主要受众")).toHaveValue(
      "中国小学至初中孩子的妈妈",
    );
    expect(screen.getByLabelText("文字风格")).toHaveValue("自然的日常短句");
    fireEvent.change(screen.getByLabelText("文字风格"), {
      target: { value: "本片用轻松的问答语气" },
    });
    fireEvent.change(screen.getByLabelText("项目名称"), {
      target: { value: "饮水机" },
    });
    fireEvent.change(screen.getByLabelText("主推产品"), {
      target: { value: "雪山母婴矿泉机" },
    });
    fireEvent.click(screen.getByRole("button", { name: "建立制作项目" }));
    await waitFor(() =>
      expect(mock.create).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "饮水机",
          project_type: "digital_presenter",
          presenter_id: "lina",
          target_duration: null,
          script_intent: "product_sales",
          presenter_overrides: expect.objectContaining({
            text_style: "本片用轻松的问答语气",
          }),
          presenter_revision: 1,
        }),
      ),
    );
  });

  it("shows the digital presenter script and saves workflow notes in the same project", async () => {
    const p = project();
    p.project_type = "digital_presenter";
    p.presenter_id = "lina";
    p.shots[0].voice_mode = "generated";
    mock.get.mockResolvedValue(p);
    mock.action.mockResolvedValue(p);
    render(<ProductionWorkbench />);
    fireEvent.click(
      await screen.findByRole("button", { name: "完整口播稿", exact: true }),
    );
    expect(
      await screen.findByRole("heading", { name: "数字人口播稿" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "复制出镜口播" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "编辑口播稿" }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "数字人设定", exact: true }),
    );
    const profile = await screen.findByText("本次数字人设定 · Lina");
    fireEvent.click(profile);
    expect(screen.getByText("中国小学至初中孩子的妈妈")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "01 品牌资料" }));
    expect(screen.getByRole("combobox", { name: "口播目标" })).toHaveValue("");
    fireEvent.change(screen.getByLabelText("参考片长（秒，选填）"), {
      target: { value: "105" },
    });
    fireEvent.change(screen.getByLabelText("工作流与复盘"), {
      target: { value: "先核对 Lina 的中文读音和口型" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存资料修改" }));
    await waitFor(() =>
      expect(mock.action).toHaveBeenCalledWith(
        expect.objectContaining({ revision: 7 }),
        "brief",
        expect.objectContaining({
          workflow_notes: "先核对 Lina 的中文读音和口型",
          target_duration: 105,
        }),
        "PATCH",
      ),
    );
    expect(mock.action.mock.calls[0]?.[2]).not.toHaveProperty("script_intent");
  });

  it("loads a saved script intent and can reset it through the brief", async () => {
    const p: ProductionProject = {
      ...project(),
      project_type: "digital_presenter",
      presenter_id: "lina",
      script_intent: "buying_comparison",
    };
    mock.get.mockResolvedValue(p);
    mock.action.mockResolvedValue({ ...p, revision: 8, script_intent: null });
    render(<ProductionWorkbench />);

    await screen.findByRole("heading", { name: "分镜工作台" });
    fireEvent.click(screen.getByRole("button", { name: "01 品牌资料" }));
    const intent = screen.getByRole("combobox", { name: "口播目标" });
    expect(intent).toHaveValue("buying_comparison");
    fireEvent.change(intent, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "保存资料修改" }));
    await waitFor(() =>
      expect(mock.action).toHaveBeenCalledWith(
        expect.objectContaining({ revision: 7 }),
        "brief",
        expect.objectContaining({ script_intent: null }),
        "PATCH",
      ),
    );
  });

  it("lets the user inspect the generated outline and writing review on directions with review data", async () => {
    const p = project();
    p.project_type = "digital_presenter";
    p.presenter_id = "lina";
    p.script_intent = "buying_comparison";
    const direction = {
      id: "legacy",
      title: "旧创意方向",
      hook: "想喝温水要等多久？",
      structure: "放学回家 → 温水场景",
      selling_points: [],
      new_shots: "拍接水操作",
      shots: p.shots,
    };
    p.directions = [
      direction,
      {
        ...direction,
        id: "demo",
        title: "放学后顺手喝水",
        writing_review: {
          script_intent: "product_sales",
          beats: [
            {
              role: "first_answer",
              purpose: "先答温水怎么接",
              audience_question: "孩子想喝温水时，怎么接得快？",
              answer: "演示用常用杯子接一杯温水。",
              shot_ids: ["S01"],
              fact_ids: ["F01"],
            },
          ],
          issues: [
            {
              code: "missing_fact",
              message: "接水时间需要商品资料支持。",
              shot_ids: ["S01"],
            },
          ],
          structure_prompt_sha256: "a".repeat(64),
          polish_prompt_sha256: "b".repeat(64),
        },
      },
    ];
    mock.get.mockResolvedValue(p);
    render(<ProductionWorkbench />);

    fireEvent.click(
      await screen.findByRole("button", { name: "创意方向", exact: true }),
    );
    expect(
      await screen.findByRole("heading", { name: "旧创意方向" }),
    ).toBeInTheDocument();
    const summary = screen.getByText(/口播提纲与审查/);
    const details = summary.closest("details")!;
    expect(details).not.toHaveAttribute("open");
    fireEvent.click(summary);
    const review = within(details);
    expect(review.getByText("写稿目标 · 卖货转化")).toBeVisible();
    expect(review.getByText(/孩子想喝温水时，怎么接得快/)).toBeVisible();
    expect(review.getByText(/演示用常用杯子接一杯温水/)).toBeVisible();
    expect(review.getByText(/接水时间需要商品资料支持/)).toBeVisible();
    expect(review.getByText(/a{8}/)).toBeVisible();
    expect(review.getByText(/b{8}/)).toBeVisible();
  });

  it("guides regeneration only after saving changes and applies the storyboard after confirmation", async () => {
    const p: ProductionProject = {
      ...project(false),
      project_type: "digital_presenter",
      presenter_id: "lina",
      narration_draft: null,
    };
    const text = "孩子运动后想喝温水，按家里常用的温度接一杯，三秒就接好。";
    const factWarning = "接水时间“三秒”暂未找到商品资料支持。";
    const newShot: ProductionShot = {
      ...p.shots[0],
      shot_id: "S02",
      purpose: "演示接温水",
      visual: "用水壶演示接水",
      spoken_text: text,
      subtitle: text,
    };
    const saved = {
      ...p,
      revision: 8,
      narration_draft: { text, shots: [] },
      narration_fact_warnings: [factWarning],
    };
    const generating: ProductionProject = {
      ...saved,
      revision: 9,
      planning_status: "running",
    };
    const generated = {
      ...saved,
      revision: 10,
      planning_status: "done",
      narration_draft: { text, shots: [newShot] },
    };
    const applied = {
      ...generated,
      revision: 11,
      content_revision: 4,
      narration_draft: null,
      narration_fact_warnings: [],
      shots: [newShot],
      readiness: { S02: ["S02 待补充视频素材"] },
    };
    mock.get.mockResolvedValueOnce(p).mockResolvedValue(generated);
    let resolveGeneration!: (value: ProductionProject) => void;
    const generation = new Promise<ProductionProject>((resolve) => {
      resolveGeneration = resolve;
    });
    mock.action
      .mockResolvedValueOnce(saved)
      .mockReturnValueOnce(generation)
      .mockResolvedValueOnce(applied);

    render(<ProductionWorkbench />);
    fireEvent.click(
      await screen.findByRole("button", { name: "完整口播稿", exact: true }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "编辑口播稿" }));
    const editor = screen.getByRole("textbox", { name: "口播稿" });
    const save = screen.getByRole("button", { name: "保存口播稿" });
    expect(editor).toHaveValue("产品介绍");
    expect(save).toBeDisabled();
    fireEvent.change(editor, { target: { value: " 产品介绍\n " } });
    expect(save).toBeDisabled();
    expect(mock.action).not.toHaveBeenCalled();

    fireEvent.change(editor, { target: { value: text } });
    expect(
      screen.queryByRole("button", { name: "按新稿重新生成分镜" }),
    ).not.toBeInTheDocument();
    fireEvent.click(save);

    await screen.findByRole("button", { name: "按新稿重新生成分镜" });
    fireEvent.click(screen.getByRole("button", { name: "03 成片与交付" }));
    expect(
      screen.getByRole("button", { name: "一键剪辑", exact: true }),
    ).toBeDisabled();
    expect(mock.action).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "02 创意与镜头" }));
    fireEvent.click(
      screen.getByRole("button", { name: "完整口播稿", exact: true }),
    );
    const script = within(
      screen.getByRole("heading", { name: "数字人口播稿" }).closest("section")!,
    );
    expect(script.getByText("商品表述待核对")).toBeVisible();
    expect(script.getByText(factWarning)).toBeVisible();
    fireEvent.click(script.getByRole("button", { name: "编辑口播稿" }));
    expect(script.queryByText("商品表述待核对")).not.toBeInTheDocument();
    expect(script.queryByText(factWarning)).not.toBeInTheDocument();
    fireEvent.click(script.getByRole("button", { name: "取消" }));
    expect(script.getByText(factWarning)).toBeVisible();
    const regenerate = script.getByRole("button", {
      name: "按新稿重新生成分镜",
    });
    expect(regenerate).toBeEnabled();
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      fireEvent.click(regenerate);
      expect(
        script.getByRole("button", { name: "正在生成分镜" }),
      ).toBeDisabled();
      expect(script.getByRole("status")).toHaveTextContent(
        "正在按新口播稿生成分镜…",
      );
      await act(async () => resolveGeneration(generating));
      await act(async () => vi.advanceTimersByTimeAsync(2000));
    } finally {
      vi.useRealTimers();
    }
    const preview = script.getByRole("button", { name: "预览并应用新分镜" });
    expect(mock.action).toHaveBeenCalledTimes(2);
    expect(mock.action).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ revision: 7 }),
      "narration",
      { text },
      "PUT",
    );
    expect(mock.action).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ revision: 8 }),
      "narration/storyboard",
      {},
      undefined,
    );
    expect(
      screen.getByRole("button", { name: /第 1 幕 S01 产品展示/ }),
    ).toHaveAttribute("aria-current", "step");
    expect(
      screen.queryByRole("heading", { name: "演示接温水" }),
    ).not.toBeInTheDocument();

    fireEvent.click(preview);
    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByText(new RegExp(newShot.visual))).toBeInTheDocument();
    expect(dialog.getByText(new RegExp(text))).toBeInTheDocument();
    fireEvent.click(
      dialog.getByRole("button", { name: "应用新分镜", exact: true }),
    );

    expect(
      await screen.findByRole("heading", { name: "演示接温水" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /第 1 幕 S02 演示接温水/ }),
    ).toHaveAttribute("aria-current", "step");
    expect(mock.action).toHaveBeenNthCalledWith(
      3,
      expect.objectContaining({ revision: 10 }),
      "narration/storyboard/apply",
      {},
      undefined,
    );
    expect(
      screen.queryByRole("heading", { name: "产品展示" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "预览并应用新分镜" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(factWarning)).not.toBeInTheDocument();
  });

  it("shows the generation failure beside retry and identifies a basic storyboard when ready", async () => {
    const text = "孩子运动后想喝温水。";
    const p: ProductionProject = {
      ...project(),
      project_type: "digital_presenter",
      presenter_id: "lina",
      narration_draft: { text, shots: [] },
      planning_status: "failed",
      planning_note: "分镜生成失败：模型服务连接超时。",
    };
    const generated: ProductionProject = {
      ...p,
      revision: 8,
      narration_draft: { text, shots: [{ ...p.shots[0], spoken_text: text }] },
      planning_status: "done",
      planning_note: "已生成基础分镜，请预览后应用。",
    };
    mock.get.mockResolvedValue(p);
    mock.action.mockResolvedValueOnce(generated);

    render(<ProductionWorkbench />);
    fireEvent.click(
      await screen.findByRole("button", { name: "完整口播稿", exact: true }),
    );
    const heading = await screen.findByRole("heading", {
      name: "数字人口播稿",
    });
    const script = within(heading.closest("section")!);
    expect(script.getByRole("alert")).toHaveTextContent(p.planning_note);
    expect(
      script.queryByRole("button", { name: "预览并应用新分镜" }),
    ).not.toBeInTheDocument();

    fireEvent.click(script.getByRole("button", { name: "重新尝试生成分镜" }));
    expect(script.getByRole("button", { name: "正在生成分镜" })).toBeDisabled();
    expect(
      await script.findByRole("button", { name: "预览并应用新分镜" }),
    ).toBeInTheDocument();
    expect(script.getByRole("status")).toHaveTextContent(
      generated.planning_note,
    );
    expect(script.queryByRole("alert")).not.toBeInTheDocument();
    expect(mock.action).toHaveBeenCalledWith(
      expect.objectContaining({ revision: p.revision }),
      "narration/storyboard",
      {},
      undefined,
    );
  });

  it("locates missing required footage and prevents rendering", async () => {
    render(<ProductionWorkbench />);
    await screen.findByRole("heading", { name: "产品展示" });
    fireEvent.click(screen.getByRole("button", { name: "03 成片与交付" }));
    expect(screen.getByText("S01 待补充视频素材")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "一键剪辑", exact: true }),
    ).toBeDisabled();
    expect(mock.action).not.toHaveBeenCalled();
  });

  it("shows the script before video exists and exposes actual trim points only after choosing footage", async () => {
    const p = project();
    p.project_type = "digital_presenter";
    p.shots[0].voice_mode = "generated";
    const a = videoAsset();
    p.assets = [a];
    mock.get.mockResolvedValue(p);
    mock.action.mockResolvedValue(p);
    render(<ProductionWorkbench />);
    expect(await screen.findByText("口播文案（生成用）")).toBeInTheDocument();
    expect(
      screen.queryByRole("combobox", { name: "画面素材版本" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "画面", exact: true }));
    expect(screen.getByText(/预计 5.0 秒/)).toBeInTheDocument();
    expect(screen.getByText(/回传或选择视频后/)).toBeInTheDocument();
    expect(
      screen.queryByRole("spinbutton", { name: "video出点" }),
    ).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox", { name: "画面素材版本" }), {
      target: { value: a.id },
    });
    expect(screen.getByRole("spinbutton", { name: "video入点" })).toHaveValue(
      0,
    );
    expect(screen.getByRole("spinbutton", { name: "video出点" })).toHaveValue(
      7.25,
    );
    fireEvent.click(screen.getByRole("button", { name: "使用此选段" }));
    await waitFor(() =>
      expect(mock.action).toHaveBeenCalledWith(
        expect.objectContaining({ revision: 7 }),
        "shots/S01/select",
        { selection: { asset_id: "video", start: 0, end: 7.25 } },
        undefined,
      ),
    );
  });

  it("labels actual media and video stills independently of the planned platform", async () => {
    const p = project();
    const video = { ...videoAsset(), frames: ["/video-frame.jpg"] };
    const photo: ProductionAsset = {
      ...video,
      id: "photo",
      url: "/product.jpg",
      filename: "产品照片.jpg",
      kind: "image",
      role: "product",
      frames: [],
      origin: { kind: "original", provider: "" },
    };
    p.assets = [video, photo];
    p.brand.sources = [
      {
        id: "slide",
        filename: "产品资料.pptx",
        page: 1,
        text: "",
        preview: "/slide.jpg",
        images: [],
        visual_summary: "",
      },
    ];
    Object.assign(p.shots[0], {
      source_type: "aigc",
      tool: "HeyGen",
      reference_ids: ["photo", "slide"],
      selection: { asset_id: video.id, start: 0, end: 5 },
    });
    mock.get.mockResolvedValue(p);
    mock.action.mockResolvedValue({
      ...p,
      assets: [
        { ...video, origin: { kind: "aigc", provider: "TapNow" } },
        photo,
      ],
    });

    render(<ProductionWorkbench />);
    const shot = (await screen.findByLabelText("S01 画面预览")).closest(
      "article",
    )!;
    const player = within(shot.querySelector("video")!.closest("figure")!);
    expect(within(shot).getByText(/计划用 HeyGen/)).toBeInTheDocument();
    expect(player.getByText("来源待确认")).toBeInTheDocument();
    expect(within(shot).getByText("原素材 · PPT 页面")).toBeInTheDocument();
    expect(screen.getByText("来源待确认 · 视频抽帧")).toBeInTheDocument();

    fireEvent.click(
      player.getByRole("button", { name: "标记来源 · 口播.mp4" }),
    );
    const dialog = within(await screen.findByRole("dialog"));
    fireEvent.change(dialog.getByLabelText("素材来源"), {
      target: { value: "aigc" },
    });
    expect(dialog.getByRole("button", { name: "保存来源" })).toBeDisabled();
    fireEvent.change(dialog.getByLabelText("实际生成平台"), {
      target: { value: " TapNow " },
    });
    fireEvent.click(dialog.getByRole("button", { name: "保存来源" }));

    await waitFor(() =>
      expect(mock.action).toHaveBeenCalledWith(
        expect.objectContaining({ revision: p.revision }),
        "assets/video/origin",
        { origin: { kind: "aigc", provider: "TapNow" } },
        "PATCH",
      ),
    );
    expect(await player.findByText("TapNow AIGC 生成")).toBeInTheDocument();
    expect(screen.getByText("TapNow AIGC 生成 · 视频抽帧")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "01 品牌资料" }));
    const reference = screen
      .getByRole("img", { name: "产品照片.jpg" })
      .closest("figure")!;
    expect(within(reference).getByText("原素材")).toBeInTheDocument();
  });

  it("sends one versioned render request without repeated confirmations", async () => {
    const p = project(false);
    mock.get.mockResolvedValue(p);
    mock.action.mockResolvedValue(p);
    render(<ProductionWorkbench />);
    await screen.findByRole("heading", { name: "产品展示" });
    fireEvent.click(screen.getByRole("button", { name: "03 成片与交付" }));
    fireEvent.click(
      screen.getByRole("button", { name: "一键剪辑", exact: true }),
    );
    await waitFor(() => expect(mock.action).toHaveBeenCalledTimes(1));
    expect(mock.action).toHaveBeenCalledWith(
      expect.objectContaining({ revision: 7 }),
      "renders",
      {},
      undefined,
    );
  });
  it("keeps historical output visible and identifies the source of each rendered clip", async () => {
    const p = project();
    const asset = videoAsset();
    p.assets = [{ ...asset, origin: { kind: "original_edit", provider: "" } }];
    p.renders = [
      {
        id: "old",
        content_revision: 1,
        status: "completed",
        progress: 1,
        step: "完成",
        attempt: 1,
        outputs: { mp4: "/old.mp4" },
        error: "",
        checks: {},
        is_current: false,
        created_at: "",
        timeline: {
          duration: 30,
          clips: [
            {
              shot_id: "S01",
              asset_id: asset.id,
              source_start: 0,
              source_end: 5,
              start: 0,
              duration: 5,
              spoken_text: "",
              prompt: "",
            },
          ],
        },
      },
    ];
    mock.get.mockResolvedValue(p);
    render(<ProductionWorkbench />);
    await screen.findByRole("heading", { name: "产品展示" });
    fireEvent.click(screen.getByRole("button", { name: "03 成片与交付" }));
    expect(screen.getByText(/历史方案，修改后请重新成片/)).toBeInTheDocument();
    expect(
      screen.getByText("剪辑成片 · 画面来源 原素材剪辑"),
    ).toBeInTheDocument();
    expect(screen.getByText("原素材剪辑")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "下载 MP4" })).toHaveAttribute(
      "href",
      "/old.mp4",
    );
    expect(
      screen.getByRole("button", { name: "一键剪辑", exact: true }),
    ).toBeDisabled();
  });
});
