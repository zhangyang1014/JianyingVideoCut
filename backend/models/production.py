"""Project-level contracts for selling-video production, independent of Task."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

ProductionType = Literal["selling_video", "digital_presenter"]
ScriptIntent = Literal["product_sales", "buying_comparison", "experience_sharing"]
PresenterId = str
WORKFLOW_NOTES_MAX_LENGTH = 8000
NARRATION_MAX_LENGTH = 12000


def new_id() -> str:
    return uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_default=True)


class PresenterHeyGen(Contract):
    avatar_id: str = Field(default="", max_length=256)
    avatar_name: str = Field(default="", max_length=256)
    voice_id: str = Field(default="", max_length=256)
    voice_name: str = Field(default="", max_length=256)
    engine: str = Field(default="", max_length=80)

    @model_validator(mode="after")
    def paired(self):
        if bool(self.avatar_id) != bool(self.voice_id):
            raise ValueError("请同时选择数字人形象和声音，或同时留空")
        return self


class PresenterConfig(Contract):
    name: str = Field(min_length=1, max_length=120)
    primary_audience: str = Field(min_length=1, max_length=4000)
    secondary_audience: str = Field(default="", max_length=4000)
    address: list[str] = Field(min_length=1, max_length=30)
    guidance: list[str] = Field(default_factory=list, max_length=50)
    positioning: str = Field(default="", max_length=4000)
    text_style: str = Field(default="", max_length=12000)
    structure_style: str = Field(default="", max_length=12000)
    speaking_style: str = Field(default="", max_length=4000)
    visual_style: str = Field(default="", max_length=4000)
    boundaries: str = Field(default="", max_length=4000)
    heygen: PresenterHeyGen = Field(default_factory=PresenterHeyGen)

    @model_validator(mode="after")
    def meaningful(self):
        self.name = self.name.strip()
        self.primary_audience = self.primary_audience.strip()
        if not self.name or not self.primary_audience or any(not x.strip() or len(x) > 200 for x in self.address):
            raise ValueError("请填写数字人名称、主要受众和非空的观众称呼")
        if any(not x.strip() or len(x) > 4000 for x in self.guidance):
            raise ValueError("补充要求每条须为 1–4000 字")
        return self


class PresenterProfile(PresenterConfig):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    revision: int = Field(default=1, ge=1)
    archived: bool = False


class ProductionSettings(Contract):
    project_type: ProductionType = "selling_video"
    presenter_id: PresenterId | None = None
    workflow_notes: str = Field(default="", max_length=WORKFLOW_NOTES_MAX_LENGTH)
    target_duration: int | None = Field(default=None, gt=0)
    script_intent: ScriptIntent | None = None

    @model_validator(mode="after")
    def validate_project_settings(self):
        if self.project_type == "selling_video":
            if self.presenter_id is not None:
                raise ValueError("请选择数字人口播项目后指定主播")
            if "target_duration" not in self.model_fields_set:
                self.target_duration = 45
            if self.target_duration is None or not 30 <= self.target_duration <= 60:
                raise ValueError("卖货视频的预计时长须为 30–60 秒")
        elif self.presenter_id is None:
            self.presenter_id = "lina"
        return self


class SourcePage(Contract):
    id: str = Field(default_factory=new_id)
    filename: str
    page: int = 1
    text: str = ""
    images: list[str] = Field(default_factory=list)
    preview: str | None = None
    visual_summary: str = ""


class BrandFact(Contract):
    id: str = Field(default_factory=new_id)
    text: str
    source_id: str


class BrandProfile(Contract):
    id: str = Field(default_factory=new_id)
    name: str = ""
    product: str = ""
    style: str = "自然可信，清晰展示产品，不夸大功效"
    sources: list[SourcePage] = Field(default_factory=list)
    facts: list[BrandFact] = Field(default_factory=list)


class Cue(Contract):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("字幕结束时间须大于开始时间")
        return self


MediaOriginKind = Literal["unknown", "original", "original_edit", "aigc"]


class MediaOrigin(Contract):
    kind: MediaOriginKind = "unknown"
    provider: str = Field(default="", max_length=100)

    @model_validator(mode="after")
    def validate_provider(self):
        self.provider = self.provider.strip()
        if self.kind == "aigc" and not self.provider:
            raise ValueError("请填写实际使用的 AIGC 平台")
        if self.kind != "aigc" and self.provider:
            raise ValueError("只有 AIGC 素材可以填写生成平台")
        return self


class ProductionAsset(Contract):
    id: str = Field(default_factory=new_id)
    filename: str
    path: str
    fingerprint: str
    kind: Literal["video", "audio", "image"]
    role: Literal["footage", "product", "presenter", "voiceover", "music"] = "footage"
    shot_id: str | None = None
    origin: MediaOrigin = Field(default_factory=MediaOrigin)
    version: int = 1
    duration: float = 0
    width: int = 0
    height: int = 0
    has_audio: bool = False
    frames: list[str] = Field(default_factory=list)
    cues: list[Cue] = Field(default_factory=list)
    usable_start: float = 0
    usable_end: float = 0
    analysis_status: Literal["pending", "running", "done", "failed"] = "pending"
    analysis_note: str = ""
    visual_summary: str = ""
    created_at: str = Field(default_factory=now)


class AssetSelection(Contract):
    asset_id: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("出点须大于入点")
        return self


class StoryboardShot(Contract):
    shot_id: str
    revision: int = 1
    purpose: str
    visual: str
    spoken_text: str = ""
    subtitle: str = ""
    target_duration: float = Field(default=6, gt=0, le=60)
    source_type: Literal["existing", "shoot", "aigc"] = "shoot"
    voice_mode: Literal["original", "generated", "voiceover", "silent"] = "original"
    framing: Literal["contain", "cover"] = "contain"
    trim_pauses: bool = True
    tool: str = "通用视频生成工具"
    reference_ids: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    filming_guide: str = ""
    prompt: str = ""
    selection: AssetSelection | None = None
    voiceover: AssetSelection | None = None
    # Verification is tied to the precise shot revision and selected source ranges.
    verified_signature: str | None = None
    review_note: str = ""
    required: bool = True


class CreatorStyleSnapshot(Contract):
    creator_id: str
    version: str
    method_version: str
    evidence_status: Literal["candidate", "reported", "verified"]
    card_text: str = Field(min_length=1)
    evidence_json: str = Field(min_length=2)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class WritingBeat(Contract):
    role: Literal["opening", "first_answer", "demonstration", "explanation", "objection", "action"]
    purpose: str
    audience_question: str
    answer: str
    shot_ids: list[str]
    fact_ids: list[str]


class WritingIssue(Contract):
    code: str
    message: str
    shot_ids: list[str]


class WritingReview(Contract):
    beats: list[WritingBeat]
    issues: list[WritingIssue]
    structure_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    polish_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    structure_model: str | None = None
    polish_model: str | None = None
    script_intent: ScriptIntent | None = None


class CreativeDirection(Contract):
    id: str
    title: str
    hook: str
    structure: str
    selling_points: list[str] = Field(default_factory=list)
    new_shots: str = ""
    shots: list[StoryboardShot] = Field(default_factory=list)
    style_snapshot: CreatorStyleSnapshot | None = None
    presenter_snapshot: PresenterProfile | None = None
    writing_review: WritingReview | None = None


class NarrationDraft(Contract):
    text: str = Field(min_length=1, max_length=NARRATION_MAX_LENGTH)
    shots: list[StoryboardShot] = Field(default_factory=list)


class TimelineClip(Contract):
    shot_id: str
    shot_revision: int
    asset_id: str
    source_start: float
    source_end: float
    start: float
    duration: float
    framing: Literal["contain", "cover"] = "contain"
    audio_asset_id: str | None = None
    audio_source_start: float = 0
    spoken_text: str = ""
    prompt: str = ""


class EditTimeline(Contract):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    duration: float
    clips: list[TimelineClip]
    sources: dict[str, dict] = Field(default_factory=dict)
    subtitles: list[Cue] = Field(default_factory=list)
    brand_label: str = ""
    music_asset_id: str | None = None
    music_volume: float = 0.10


class ProductionRenderJob(Contract):
    id: str = Field(default_factory=new_id)
    fingerprint: str
    content_revision: int
    status: Literal["queued", "running", "completed", "failed", "cancelled"] = "queued"
    progress: float = 0
    step: str = "等待剪辑"
    attempt: int = 1
    snapshot: dict
    timeline: EditTimeline
    outputs: dict[str, str] = Field(default_factory=dict)
    checks: dict = Field(default_factory=dict)
    error: str = ""
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)


class HeyGenJob(Contract):
    id: str = Field(default_factory=new_id)
    shot_id: str
    shot_revision: int
    request: dict[str, object]
    key_fingerprint: str
    status: Literal["submitting", "processing", "importing", "completed", "paused", "failed"] = "submitting"
    video_id: str | None = None
    asset_id: str | None = None
    error: str = ""
    created_at: str = Field(default_factory=now)


class ProductionProject(ProductionSettings):
    id: str = Field(default_factory=new_id)
    name: str = Field(min_length=1, max_length=120)
    presenter_snapshot: PresenterProfile | None = None
    presenter_style_snapshot: CreatorStyleSnapshot | None = None
    brand: BrandProfile
    platform: str = "抖音"
    selling_points: str = ""
    offer: str = ""
    revision: int = 1
    content_revision: int = 1
    stage: Literal["brief", "storyboard", "collecting", "ready", "rendering", "completed"] = "brief"
    directions: list[CreativeDirection] = Field(default_factory=list)
    selected_direction_id: str | None = None
    shots: list[StoryboardShot] = Field(default_factory=list)
    narration_draft: NarrationDraft | None = None
    assets: list[ProductionAsset] = Field(default_factory=list)
    music_asset_id: str | None = None
    renders: list[ProductionRenderJob] = Field(default_factory=list)
    heygen_jobs: list[HeyGenJob] = Field(default_factory=list)
    planning_status: Literal["idle", "running", "done", "failed"] = "idle"
    planning_note: str = ""
    warnings: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)

    @model_validator(mode="after")
    def consistent_presenter(self):
        if self.presenter_snapshot and (
            self.project_type != "digital_presenter" or self.presenter_snapshot.id != self.presenter_id
        ):
            raise ValueError("数字人档案与项目关联不一致")
        if self.presenter_style_snapshot and (
            self.project_type != "digital_presenter" or self.presenter_style_snapshot.creator_id != self.presenter_id
        ):
            raise ValueError("创作者风格与数字人关联不一致")
        return self
