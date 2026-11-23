import type { PresenterConfig, ProductionPresenter } from "./productions-api";

export function presenterConfig(
  profile?: ProductionPresenter,
): PresenterConfig {
  return {
    name: profile?.name ?? "",
    primary_audience: profile?.primary_audience ?? "",
    secondary_audience: profile?.secondary_audience ?? "",
    address: [...(profile?.address ?? [])],
    guidance: [...(profile?.guidance ?? [])],
    positioning: profile?.positioning ?? "",
    text_style: profile?.text_style ?? "",
    structure_style: profile?.structure_style ?? "",
    speaking_style: profile?.speaking_style ?? "",
    visual_style: profile?.visual_style ?? "",
    boundaries: profile?.boundaries ?? "",
    heygen: {
      avatar_id: "",
      avatar_name: "",
      voice_id: "",
      voice_name: "",
      engine: "",
      ...profile?.heygen,
    },
  };
}

export const presenterFieldLabels: Record<keyof PresenterConfig, string> = {
  name: "数字人名称",
  primary_audience: "主要受众",
  secondary_audience: "兼顾受众",
  address: "观众称呼",
  guidance: "补充要求",
  positioning: "内容定位",
  text_style: "文字风格",
  structure_style: "结构风格",
  speaking_style: "口播习惯",
  visual_style: "画面风格",
  boundaries: "表达禁忌",
  heygen: "形象与声音",
};

export function presenterValidationMessage(config: PresenterConfig): string {
  if (
    !config.name.trim() ||
    !config.primary_audience.trim() ||
    !config.address.length
  )
    return "请填写数字人名称、主要受众和观众称呼。";
  if (
    config.address.length > 30 ||
    config.address.some((x) => !x.trim() || x.length > 200)
  )
    return "观众称呼最多 30 条，每条须为 1–200 字。";
  if (
    config.guidance.length > 50 ||
    config.guidance.some((x) => !x.trim() || x.length > 4000)
  )
    return "补充要求最多 50 条，每条须为 1–4000 字。";
  for (const key of Object.keys(presenterFieldLabels) as Array<
    keyof PresenterConfig
  >) {
    const value = config[key];
    const limit =
      key === "name"
        ? 120
        : key === "text_style" || key === "structure_style"
          ? 12000
          : 4000;
    if (typeof value === "string" && value.length > limit)
      return `${presenterFieldLabels[key]}最多 ${limit} 字。`;
  }
  if (Boolean(config.heygen.avatar_id) !== Boolean(config.heygen.voice_id))
    return "请同时选择形象和声音，或同时留空。";
  return "";
}

export function validPresenter(config: PresenterConfig) {
  return !presenterValidationMessage(config);
}

// Rebase independent edits; fields changed differently in both versions need a choice.
export function mergePresenterDraft(
  base: PresenterConfig,
  draft: PresenterConfig,
  latest: PresenterConfig,
) {
  const merged = { ...latest };
  const conflicts: Array<keyof PresenterConfig> = [];
  for (const key of Object.keys(presenterFieldLabels) as Array<
    keyof PresenterConfig
  >) {
    if (JSON.stringify(draft[key]) === JSON.stringify(base[key])) continue;
    if (
      JSON.stringify(latest[key]) !== JSON.stringify(base[key]) &&
      JSON.stringify(latest[key]) !== JSON.stringify(draft[key])
    )
      conflicts.push(key);
    Object.assign(merged, { [key]: draft[key] });
  }
  return { merged, conflicts };
}
