"""
GoldenClip Data Models
Design: 暗金剪辑台 · 编导美学
All task states, segment states, and export configs are defined here.
"""

from enum import Enum
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field

from .app_config import DEFAULT_AUDIT_MODEL, DEFAULT_LEARNING_MODEL, DEFAULT_TTS_MODEL, DEFAULT_TTS_VOICE
from datetime import datetime
import uuid


DEFAULT_SEGMENT_SILENCE_THRESHOLD = 0.30
MIN_SEGMENT_SILENCE_THRESHOLD = 0.05
MAX_SEGMENT_SILENCE_THRESHOLD = 0.50


class TaskStatus(str, Enum):
    PENDING = "pending"           # 待处理
    ASR_RUNNING = "asr_running"   # ASR识别中
    ASR_RECOGNIZED = "asr_recognized"  # ASR识别完成，待文本整理
    DIARIZATION_RUNNING = "diarization_running"  # 说话人分离中
    ASR_DIARIZED = "asr_diarized"  # 说话人分离完成，待文本整理
    ANALYZE_RUNNING = "analyze_running"  # 文本整理中
    ASR_DONE = "asr_done"         # 文本整理完成
    AUDIT_RUNNING = "audit_running"  # 审计中
    TRANSLATE_RUNNING = "translate_running"  # 字幕校准/翻译中
    TTS_RUNNING = "tts_running"   # 目标语言音频生成中
    DUBBING_RUNNING = "dubbing_running"  # 配音合成中
    REVIEW = "review"             # 待Review
    EXPORT_RUNNING = "export_running"  # 导出中
    DONE = "done"                 # 已完成
    ERROR = "error"               # 错误


class TaskType(str, Enum):
    HIGHLIGHT_REEL = "highlight_reel"   # 直播卖货集锦
    INTERVIEW_COMPRESS = "interview_compress"  # 直播访谈压缩
    MONOLOGUE_CLEAN = "monologue_clean"  # 口播精修
    ONE_CLICK_TRANSLATE = "one_click_translate"  # 一键转英文/目标语言配音


def should_enable_diarization_for_task_type(task_type: TaskType) -> bool:
    """仅直播访谈/对话类任务默认开启 CAM++ 说话人分离。"""
    return task_type == TaskType.INTERVIEW_COMPRESS


def default_task_params(task_type: TaskType = TaskType.MONOLOGUE_CLEAN) -> "TaskParams":
    """按任务类型返回带合理默认值的 TaskParams。"""
    return TaskParams(
        enable_diarization=should_enable_diarization_for_task_type(task_type),
    )


def resolve_enable_diarization(
    task_type: TaskType,
    *,
    request_override: Optional[bool] = None,
) -> bool:
    """解析是否启用说话人分离：请求显式覆盖优先，否则按任务类型默认。"""
    if request_override is not None:
        return request_override
    return should_enable_diarization_for_task_type(task_type)


class ExportMode(str, Enum):
    FFMPEG = "ffmpeg"       # 路径A: FFmpeg 无损切割
    JIANYING = "jianying"   # 路径B: 剪映草稿


class SegmentAction(str, Enum):
    KEEP = "keep"
    DELETE = "delete"
    # 字幕层修正：音频/视频保持连贯不剪切，仅在字幕显示层去掉结巴重复词
    # 适用于小结巴（如"就就"），避免强制剪切引入音频拼接噪声
    SUBTITLE_FIX = "subtitle_fix"
    # ASR 识别错误纠正：视频/音频不变，字幕显示 display_text（Claude 推断的正确用字）
    # 与 subtitle_fix 链路相同，但语义上是修正机器识别错误，不是说话者问题
    TEXT_FIX = "text_fix"
    # 断句修正：ASR 在错误位置切段时，重新划定边界
    # 后处理器利用字符级 words 时间戳精确切分，并自动与相邻段合并
    SPLIT = "split"
    # 合并相邻碎段：ASR 过度切分时，将本段与下一段合并为一张字幕卡
    # 视频/音频不变（两段都保留），只是字幕显示层合并展示
    MERGE_NEXT = "merge_next"


class AuditRule(str, Enum):
    RETAKE = "Rule 1: 重说识别"
    FRAGMENT = "Rule 2: 残句清理"
    FILLER = "Rule 3: 语气词切除"
    STUTTER = "Rule 4: 词内去重"
    DEDUP = "Rule 5: 语义去重"
    INTRA_REPEAT = "Rule 6: 句内重复"
    QA_INTEGRITY = "Rule 7: 问答闭环"
    PACING = "Rule 8: 气口对齐"
    MANUAL = "手动调整"


class ManualSegmentAction(str, Enum):
    """最近一次人工编辑类型，仅由人工操作接口写入。"""
    DELETE = "delete"
    RESTORE = "restore"
    ADJUST = "adjust"
    MERGE = "merge"
    SPLIT = "split"
    WORD_DELETE = "word_delete"


class WordTimestamp(BaseModel):
    word: str
    start: float
    end: float
    speaker: Optional[str] = None  # e.g. "spk0", "spk1"


class CandidateEdit(BaseModel):
    """分析阶段发现的可选编辑，不自动改变导出时间线。"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    source: str  # filler | stutter | short_segment | glossary | postprocess
    edit_type: str = "delete_range"  # delete_range | subtitle_fix | normalize_text | info
    start: float
    end: float
    text: str = ""
    reason: str = ""
    confidence: Optional[float] = None
    segment_id: Optional[str] = None
    word_index: Optional[int] = None
    status: str = "candidate"  # candidate | accepted | rejected | superseded
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SourceWordRange(BaseModel):
    """输出语义片段在某个原始 ASR 片段中的字级来源范围。"""
    segment_id: str
    start_word_index: Optional[int] = None
    end_word_index: Optional[int] = None


class ProductEntity(BaseModel):
    """审计阶段识别到的句子级产品实体。"""
    name: str
    canonical_name: Optional[str] = None
    category: Optional[str] = None


class ProductSemanticMark(BaseModel):
    """一句话/片段对产品内容的语义理解，供后续集锦规划消费。"""
    roles: List[str] = Field(default_factory=list)
    product_entities: List[ProductEntity] = Field(default_factory=list)
    selling_points: List[str] = Field(default_factory=list)
    angle_id: Optional[str] = None
    angle_label: Optional[str] = None
    eligible_for_highlight: bool = True
    exclude_reason: Optional[str] = None
    confidence: Optional[float] = None
    source: str = "audit"


class HighlightReelStrategy(str, Enum):
    """同一产品组内部参与择优的三种剪辑策略。"""
    FAITHFUL = "faithful"
    BALANCED = "balanced"
    CONCISE = "concise"


class ProductOccurrence(BaseModel):
    """产品在源视频中的一次连续出现；同产品再次出现会产生新的 occurrence。"""
    occurrence_id: str = Field(default_factory=lambda: f"occ_{str(uuid.uuid4())[:8]}")
    name: str
    aliases: List[str] = Field(default_factory=list)
    start: float = 0.0
    end: float = 0.0
    segment_ids: List[str] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)
    confidence: Optional[float] = None
    source: str = "deterministic"


class ProductGroup(BaseModel):
    """同一产品跨多个连续场次的全局归组，作为最终集锦的唯一出片单位。"""
    product_group_id: str = Field(default_factory=lambda: f"product_{str(uuid.uuid4())[:8]}")
    canonical_name: str
    aliases: List[str] = Field(default_factory=list)
    occurrence_ids: List[str] = Field(default_factory=list)
    segment_ids: List[str] = Field(default_factory=list)
    start: float = 0.0
    end: float = 0.0
    confidence: Optional[float] = None
    evidence: List[str] = Field(default_factory=list)


class AuditEditOperation(BaseModel):
    """LLM 语义编辑计划中的单条操作，只表达意图，不直接改时间轴。"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    op: Literal[
        "keep_span",
        "delete_span",
        "delete_words",
        "split_at",
        "merge_span",
        "text_fix",
        "subtitle_line_break",
    ]
    source_id: Optional[str] = None
    source_ids: List[str] = Field(default_factory=list)
    source_word_ranges: List[SourceWordRange] = Field(default_factory=list)
    split_before: Optional[str] = None
    split_part1_action: SegmentAction = SegmentAction.KEEP
    split_part2_action: SegmentAction = SegmentAction.KEEP
    display_text: Optional[str] = None
    subtitle_line_break: Optional[str] = None
    reason: str = ""
    rule: Optional[AuditRule] = None
    text_role: Optional[str] = None
    semantic_unit_id: Optional[str] = None
    product_semantic: Optional[ProductSemanticMark] = None
    clip_group: Optional[int] = None
    clip_title: Optional[str] = None
    style: Optional[str] = None


class AuditEditPlan(BaseModel):
    """自由断句语义审计计划，由 LLM 输出、后端编译为 audit_segments。"""
    operations: List[AuditEditOperation] = Field(default_factory=list)
    term_fixes: List[Dict[str, Any]] = Field(default_factory=list)
    coherence_notes: List[str] = Field(default_factory=list)
    target_retention: Optional[float] = None
    validator_model: Optional[str] = None


class Segment(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    start: float
    end: float
    text: str
    action: SegmentAction = SegmentAction.KEEP
    reason: Optional[str] = None
    rule: Optional[AuditRule] = None
    speaker: Optional[str] = None
    confidence: Optional[float] = None
    # 原片段上的半开字索引范围；契约校验后由后处理器编译成保留/删除子段。
    word_deletions: List[SourceWordRange] = Field(default_factory=list)
    # Tagged script representation for Claude
    tagged_text: Optional[str] = None
    # Manual override by user
    user_override: bool = False
    # 最近一次人工操作。与 user_override 分开保存，避免把自动规则误标为用户操作。
    manual_action: Optional[ManualSegmentAction] = None
    # Claude 原始决策冻结：审计完成后快照，不随用户操作改变
    claude_action: Optional[SegmentAction] = None
    claude_reason: Optional[str] = None
    # Style tag from Claude (e.g. "Golden_Template_A")
    style: Optional[str] = None
    # Highlight reel: clip grouping for one-to-many exports (1, 2, 3...)
    clip_group: Optional[int] = None
    # Highlight reel: clip title, usually only set on the first segment of a group
    clip_title: Optional[str] = None
    # 字幕层修正文本：action=subtitle_fix / text_fix 时使用，音频不剪切，字幕显示此文本
    # subtitle_fix 例：text="就就是顺着往后面上就行了" → display_text="就是顺着往后面上就行了"
    # text_fix    例：text="老道了很久" → display_text="唠叨了很久"（ASR同音字纠错）
    display_text: Optional[str] = None
    # 用户/字幕优化最终覆盖层：不改变原始 text、words、时间轴或审计决策。
    # 最终字幕显示优先级为 subtitle_text > display_text > text。
    subtitle_text: Optional[str] = None
    subtitle_text_source: Optional[Literal["manual", "optimizer"]] = None
    subtitle_updated_at: Optional[str] = None
    # fix_type：区分 subtitle_fix 和 text_fix 的修正类型
    # "stutter" → subtitle_fix（说话者结巴）
    # "asr_error" → text_fix（ASR 识别错误）
    fix_type: Optional[str] = None
    # 断句修正（action=split）专用字段
    # split_before: 从哪段文字开始切为新段（在 words 中按字符顺序匹配第一个出现位置）
    # 例：split_before="这个是" → 在 words 中找到"这"字，从其 start 时间处切断
    split_before: Optional[str] = None
    # 切分后各部分的动作，默认两部分都保留
    split_part1_action: SegmentAction = SegmentAction.KEEP
    split_part2_action: SegmentAction = SegmentAction.KEEP
    # 超长字幕分行显示点（action=keep 时使用），视频不切割，仅拆分字幕卡展示
    # 取值：分行位置的起始文字（匹配 words 查找时间点），用于生成两个 TextSegment
    # 例："让你的工作效率翻倍" → 在该句开头时间点切换字幕卡
    subtitle_line_break: Optional[str] = None
    # 合并标记（action=merge_next 专用）：后处理器将本段与下一段合并后置 True
    # 合并后本段 end 延伸至下一段 end，words 拼接，下一段将从列表中移除
    merged: bool = False
    # 字级时间戳列表，保留 ASR 输出的字符粒度数据供前端精准跳转
    words: List[WordTimestamp] = []
    # 分析阶段识别出的候选编辑，供审计/人工采纳后再影响导出
    candidate_edits: List[CandidateEdit] = Field(default_factory=list)
    # 自由断句编译后的来源追踪：当前片段来自哪些原始 ASR 片段/字范围
    source_segment_ids: List[str] = Field(default_factory=list)
    source_word_ranges: List[SourceWordRange] = Field(default_factory=list)
    compiled_from_operation: Optional[str] = None
    semantic_unit_id: Optional[str] = None
    # AI 审计产出的句子级产品理解。集锦规划读取该字段，而不是把集锦规则混入审计展示。
    product_semantic: Optional[ProductSemanticMark] = None


class CompiledAuditResult(BaseModel):
    """语义编辑计划编译结果。"""
    segments: List[Segment] = Field(default_factory=list)
    edit_plan: Optional[AuditEditPlan] = None
    warnings: List[str] = Field(default_factory=list)
    compiled_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class ASRResult(BaseModel):
    words: List[WordTimestamp] = []
    segments: List[Segment] = []
    tagged_script: str = ""
    duration: float = 0.0
    language: str = "zh"
    speakers: List[str] = []
    # CAM++ 原始段级说话人分离结果，用于前端调整平滑阈值后重新标注
    # 格式：[{'start': float, 'end': float, 'speaker': str}, ...]
    diar_segments: List[Dict[str, Any]] = []
    # 当前生效的平滑阈值（秒），与 smooth_word_speakers 的 min_switch_duration 对应
    diar_smooth_threshold: float = 1.0
    # 文本整理可解释报告：规则链、参数、统计与片段形成原因
    analysis_report: Optional[Dict[str, Any]] = None
    # 全局候选编辑索引，片段内也会保留与其相交的子集
    candidate_edits: List[CandidateEdit] = Field(default_factory=list)


class ASRSnapshot(BaseModel):
    """每次 ASR 识别的历史快照"""
    version: int                    # 版本号，从 1 开始递增
    created_at: str                 # ISO 时间戳，识别完成时间
    engine: str                     # 识别引擎，如 "FunASR Paraformer-zh" / "Whisper base"
    segments_count: int             # 识别到的片段数量
    duration: float                 # 视频时长（秒）
    asr_result: ASRResult           # 完整识别结果快照


class AnalysisSnapshot(BaseModel):
    """每次文本整理/字幕分段的历史快照。"""
    version: int
    created_at: str
    action: str = "text_organize"    # text_organize | resegment
    silence_threshold: float = DEFAULT_SEGMENT_SILENCE_THRESHOLD
    segments_count: int = 0
    candidate_edits_count: int = 0
    stats: Dict[str, Any] = Field(default_factory=dict)
    analysis_report: Optional[Dict[str, Any]] = None
    segments: List[Segment] = Field(default_factory=list)


class ResegmentInheritanceReport(BaseModel):
    """重分段时把旧审计/集锦投影到新片段的可追溯报告。"""
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    mode: Literal["none", "aggressive"] = "aggressive"
    old_segment_count: int = 0
    new_segment_count: int = 0
    mapped_segment_count: int = 0
    exact_segment_count: int = 0
    review_segment_count: int = 0
    unmapped_segment_count: int = 0
    inherited_audit_count: int = 0
    inherited_occurrence_count: int = 0
    inherited_reel_count: int = 0
    inherited_golden_quote_count: int = 0
    invalidated_export_count: int = 0
    review_segment_ids: List[str] = Field(default_factory=list)
    unmapped_segment_ids: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class LLMModelConfig(BaseModel):
    """字幕校准和翻译模型配置。API key 只用于请求体，不应写入任务持久化。"""
    provider: str = ""          # 空值表示使用 translate.subtitle 角色路由
    model: str = ""
    base_url: Optional[str] = None
    use_openrouter: bool = False


class TTSModelConfig(BaseModel):
    """目标语言语音生成配置。API key 只用于请求体，不应写入任务持久化。"""
    provider: str = ""          # 空值表示继承 tts 功能配置
    model: str = ""
    voice: str = DEFAULT_TTS_VOICE
    base_url: Optional[str] = None


class OneClickTranslateConfig(BaseModel):
    source_language: str = "zh"
    target_language: str = "en"
    subtitle_model: LLMModelConfig = Field(default_factory=LLMModelConfig)
    tts_model: TTSModelConfig = Field(default_factory=TTSModelConfig)


class TranslationCue(BaseModel):
    """
    一条跨步骤字幕/配音交接单元。
    start/end 始终沿用原视频时间轴，后续音频必须按 start 对齐。
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    source_segment_id: str
    start: float
    end: float
    source_text: str
    calibrated_text: Optional[str] = None
    translated_text: Optional[str] = None
    audio_path: Optional[str] = None
    audio_duration: Optional[float] = None
    audio_text: Optional[str] = None
    status: str = "asr_done"  # asr_done | calibrated | translated | audio_done | error
    error_message: Optional[str] = None


class TranslationVideo(BaseModel):
    """一键转英文任务中的单个视频条目。"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    filename: str
    video_path: str
    duration: Optional[float] = None
    thumbnail_path: Optional[str] = None
    status: str = "uploaded"
    asr_result: Optional[ASRResult] = None
    cues: List[TranslationCue] = Field(default_factory=list)
    source_srt_path: Optional[str] = None
    calibrated_srt_path: Optional[str] = None
    translated_srt_path: Optional[str] = None
    aligned_audio_path: Optional[str] = None
    preview_path: Optional[str] = None
    export_path: Optional[str] = None
    error_message: Optional[str] = None


class AuditSnapshot(BaseModel):
    """每次语义审计的历史快照"""
    version: int                    # 版本号，从 1 开始递增
    created_at: str                 # ISO 时间戳，审计完成时间
    model: str                      # 审计模型，如 "claude-3-5-sonnet" / "规则引擎"
    segments_count: int             # 总片段数量
    segments_kept: int              # 保留片段数量
    segments_deleted: int           # 删除片段数量


class HighlightReelStatus(str, Enum):
    PENDING = "pending"
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    EXPORTED = "exported"
    EXPORT_FAILED = "export_failed"


class HighlightQualityStatus(str, Enum):
    PENDING = "pending"
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class HighlightExportStatus(str, Enum):
    NOT_EXPORTED = "not_exported"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class HighlightReelVariant(str, Enum):
    BEFORE_QUALITY = "before_quality"
    AFTER_QUALITY = "after_quality"


class ExportJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExportJobKind(str, Enum):
    FFMPEG = "ffmpeg"
    JIANYING = "jianying"
    HIGHLIGHT_REEL = "highlight_reel"


class RenderProfile(str, Enum):
    """主任务 FFmpeg 导出的渲染档位。"""
    DRAFT = "draft"
    PREVIEW = "preview"
    FINAL = "final"


class RenderQualityIssue(BaseModel):
    """渲染后媒体校验的一条发现。"""
    code: str
    severity: str = "warning"  # error | warning | info
    message: str


class RenderQualityReport(BaseModel):
    """渲染产物的媒体层校验报告。"""
    passed: bool = False
    expected_duration: Optional[float] = None
    actual_duration: Optional[float] = None
    video_stream_present: bool = False
    audio_stream_present: bool = False
    issues: List[RenderQualityIssue] = Field(default_factory=list)
    checked_at: Optional[str] = None


class HighlightReelIssue(BaseModel):
    code: str
    severity: str = "minor"  # blocker | major | minor
    message: str
    reel_id: Optional[str] = None
    suggestion: Optional[str] = None


class HighlightReelRepairAction(BaseModel):
    """质检模型或确定性修订器给出的可审计修订动作。"""
    action: str
    reel_id: Optional[str] = None
    segment_ids: List[str] = Field(default_factory=list)
    reason: str = ""


class HighlightReel(BaseModel):
    """产品集锦 canonical 计划条目，由 audit_segments 派生并可独立编辑。"""
    reel_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    clip_group: int
    title: str
    segment_ids: List[str] = Field(default_factory=list)
    start: float = 0.0
    end: float = 0.0
    duration: float = 0.0
    hook_segment_id: Optional[str] = None
    rationale: Optional[str] = None
    product_name: Optional[str] = None
    angle_label: Optional[str] = None
    semantic_roles: List[str] = Field(default_factory=list)
    score: Optional[float] = None
    status: HighlightReelStatus = HighlightReelStatus.PENDING
    # 新字段拆分质检与导出生命周期；status 仅保留旧任务/旧客户端兼容。
    quality_status: Optional[HighlightQualityStatus] = None
    export_status: HighlightExportStatus = HighlightExportStatus.NOT_EXPORTED
    issues: List[HighlightReelIssue] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    export_path: Optional[str] = None
    source_content_revision: Optional[int] = None
    exported_subtitle_revision: Optional[int] = None
    exported_revision: Optional[int] = None
    exported_job_id: Optional[str] = None
    hidden: bool = False
    # 新 canonical 结构：旧任务缺少这些字段时仍可正常加载。
    occurrence_id: Optional[str] = None
    product_group_id: Optional[str] = None
    source_occurrence_ids: List[str] = Field(default_factory=list)
    strategy_kind: Optional[HighlightReelStrategy] = None
    retention_ratio: Optional[float] = None
    content_retention_ratio: Optional[float] = None
    source_duration: Optional[float] = None
    source_segment_count: Optional[int] = None
    selected_selling_points: List[str] = Field(default_factory=list)


class ExportJob(BaseModel):
    """独立导出作业。作业持有点击导出瞬间的快照，运行期间不读取实时字幕。"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    kind: ExportJobKind = ExportJobKind.FFMPEG
    render_profile: RenderProfile = RenderProfile.FINAL
    status: ExportJobStatus = ExportJobStatus.QUEUED
    reel_id: Optional[str] = None
    reel_variant: HighlightReelVariant = HighlightReelVariant.AFTER_QUALITY
    source_content_revision: int = 0
    source_highlight_revision: int = 0
    source_subtitle_revision: int = 0
    request: Dict[str, Any] = Field(default_factory=dict)
    snapshot: Dict[str, Any] = Field(default_factory=dict)
    output_path: Optional[str] = None
    output_url: Optional[str] = None
    output_files: List[str] = Field(default_factory=list)
    draft_path: Optional[str] = None
    srt_path: Optional[str] = None
    progress: Optional[float] = None
    error_message: Optional[str] = None
    render_fingerprint: Optional[str] = None
    quality_report: Optional[RenderQualityReport] = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class HighlightReelQualityReport(BaseModel):
    passed: bool = False
    score: Optional[float] = None
    issues: List[HighlightReelIssue] = Field(default_factory=list)
    validated_at: Optional[str] = None
    validator_model: Optional[str] = None
    proposed_repair_actions: List[HighlightReelRepairAction] = Field(default_factory=list)
    repair_actions: List[HighlightReelRepairAction] = Field(default_factory=list)
    repair_count: int = 0
    degraded: bool = False
    variant: HighlightReelVariant = HighlightReelVariant.AFTER_QUALITY


class HighlightStrategyQualityResult(BaseModel):
    """单个 product group + strategy 候选的质检结果；occurrence_id 保留兼容。"""
    occurrence_id: str
    product_group_id: Optional[str] = None
    strategy_kind: HighlightReelStrategy
    reel_id: Optional[str] = None
    passed: bool = False
    score: Optional[float] = None
    issues: List[HighlightReelIssue] = Field(default_factory=list)
    validated_at: Optional[str] = None


class AuditCoherenceIssue(BaseModel):
    code: str
    severity: str = "minor"  # blocker | major | minor
    message: str
    segment_id: Optional[str] = None
    suggestion: Optional[str] = None


class AuditCoherenceTermFix(BaseModel):
    source: str
    target: str
    segment_ids: List[str] = Field(default_factory=list)


class AuditCoherenceReport(BaseModel):
    """审计后全文连贯性复核结果，用于决定是否自动进入产品集锦。"""
    passed: bool = False
    score: Optional[float] = None
    issues: List[AuditCoherenceIssue] = Field(default_factory=list)
    term_fixes: List[AuditCoherenceTermFix] = Field(default_factory=list)
    repaired_count: int = 0
    validated_at: Optional[str] = None
    validator_model: Optional[str] = None


class TaskParams(BaseModel):
    reference_script: str = Field(default="", max_length=10000)
    silence_threshold: float = Field(
        DEFAULT_SEGMENT_SILENCE_THRESHOLD,
        ge=MIN_SEGMENT_SILENCE_THRESHOLD,
        le=MAX_SEGMENT_SILENCE_THRESHOLD,
    )                                   # 停顿切分阈值（秒）
    breath_lead_ms: int = 150          # 气口预留开头（毫秒）
    breath_tail_ms: int = 100          # 气口预留结尾（毫秒）
    min_segment_duration: float = 1.5  # 最短片段时长（秒）
    filler_words: List[str] = ["嗯", "啊", "那个", "然后", "就是说", "就是", "这个"]
    retake_char_threshold: int = 5     # 重说识别字符数阈值
    style_mode: str = "immersive"      # "quick_cut" | "immersive"
    enable_diarization: bool = False   # 是否开启说话人识别（访谈类任务创建时会设为 True）
    # 说话人标签注入（访谈模式专用）：导出剪映字幕时在每段前加 [主播]/[嘉宾] 前缀
    show_speaker_label: bool = False
    # 说话人显示名称映射，key 为 spk0/spk1 等，value 为显示名称
    speaker_names: Dict[str, str] = Field(default_factory=lambda: {"spk0": "主播", "spk1": "嘉宾"})
    # Rule toggles
    rules_enabled: Dict[str, bool] = Field(default_factory=lambda: {
        "retake": True,
        "fragment": True,
        "filler": True,
        "stutter": True,
        "dedup": True,
        "intra_repeat": True,
        "qa_integrity": True,
        "pacing": True,
    })


class ExecutionError(BaseModel):
    """可持久化的执行错误摘要，不保存请求体、凭据或大型响应。"""
    error_type: str
    message: str
    retryable: bool = False
    on_fail: Optional[str] = None
    status_code: Optional[int] = None
    retry_after_seconds: Optional[float] = None
    attempt: Optional[int] = None
    occurred_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class RetryState(BaseModel):
    """单个顶层执行拥有的重试预算；嵌套 helper 复用该预算。"""
    retries_used: int = Field(default=0, ge=0)
    max_retries: int = Field(default=3, ge=0)
    next_retry_at: Optional[str] = None
    last_delay_seconds: Optional[float] = None


class StageCheckpoint(BaseModel):
    """阶段级断点。产物只记录稳定引用和摘要，不内嵌大型数据。"""
    stage: str
    status: Literal[
        "pending",
        "running",
        "retry_wait",
        "succeeded",
        "failed",
        "cancelled",
        "interrupted",
    ] = "pending"
    attempts: int = Field(default=0, ge=0)
    artifact_ref: Optional[str] = None
    artifact_digest: Optional[str] = None
    error: Optional[ExecutionError] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class ExecutionRun(BaseModel):
    """一次可恢复、可去重且受 generation 写屏障保护的任务执行。"""
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    generation: int = Field(default=1, ge=1)
    operation: str = "task"
    idempotency_key: Optional[str] = None
    input_fingerprint: Optional[str] = None
    status: Literal[
        "pending",
        "running",
        "retry_wait",
        "succeeded",
        "failed",
        "cancelled",
        "interrupted",
    ] = "pending"
    current_stage: Optional[str] = None
    stages: List[StageCheckpoint] = Field(default_factory=list)
    retry_state: RetryState = Field(default_factory=RetryState)
    error: Optional[ExecutionError] = None
    recoverable: bool = True
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    @property
    def retries_used(self) -> int:
        return self.retry_state.retries_used

    @property
    def max_retries(self) -> int:
        return self.retry_state.max_retries

    @property
    def next_retry_at(self) -> Optional[str]:
        return self.retry_state.next_retry_at


class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    task_type: TaskType = TaskType.MONOLOGUE_CLEAN
    status: TaskStatus = TaskStatus.PENDING
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    # TaskStore 乐观并发版本；旧任务缺失时从 0 开始。
    revision: int = Field(default=0, ge=0)
    # 统一执行协议。旧路由无需传这些字段，默认仍可正常加载。
    execution_generation: int = Field(default=0, ge=0)
    active_execution_run_id: Optional[str] = None
    execution_runs: List[ExecutionRun] = Field(default_factory=list)

    # File paths
    video_path: Optional[str] = None
    video_filename: Optional[str] = None
    video_duration: Optional[float] = None
    thumbnail_path: Optional[str] = None

    # 用户手动设置的裁剪区间（秒），None 表示不限制
    clip_start: Optional[float] = None
    clip_end: Optional[float] = None

    # Processing results
    asr_result: Optional[ASRResult] = None
    asr_history: List[ASRSnapshot] = []   # 历史识别快照，最新在最后
    asr_current_version: Optional[int] = None  # 当前激活的 ASR 版本号
    analysis_history: List[AnalysisSnapshot] = []  # 文本整理/字幕分段历史，最新在最后
    analysis_current_version: Optional[int] = None
    resegment_inheritance_report: Optional[ResegmentInheritanceReport] = None
    audit_segments: List[Segment] = []
    audit_json_path: Optional[str] = None
    audit_history: List[AuditSnapshot] = []   # 历史审计快照，最新在最后
    audit_current_version: Optional[int] = None  # 当前激活的审计版本号
    content_revision: int = 0
    subtitle_revision: int = 0
    last_subtitle_optimization: Optional[Dict[str, Any]] = None
    highlight_revision: int = 0

    # Export results
    export_path: Optional[str] = None
    jianying_draft_path: Optional[str] = None
    export_jobs: List[ExportJob] = Field(default_factory=list)
    latest_export_job_id: Optional[str] = None

    # Config
    params: TaskParams = Field(default_factory=TaskParams)

    # Error info
    error_message: Optional[str] = None

    # 金句开场：有序 segment_id 列表，导出时这些片段会被放到视频最开头
    golden_quote_order: List[str] = []

    # 一键转英文/目标语言配音批处理数据
    one_click_translate_config: Optional[OneClickTranslateConfig] = None
    translation_videos: List[TranslationVideo] = Field(default_factory=list)

    # 直播卖货集锦：canonical 多产品集锦计划与质检报告
    highlight_reels: List[HighlightReel] = Field(default_factory=list)
    highlight_reels_before_quality: List[HighlightReel] = Field(default_factory=list)
    highlight_quality_report: Optional[HighlightReelQualityReport] = None
    highlight_quality_report_before_quality: Optional[HighlightReelQualityReport] = None
    product_occurrences: List[ProductOccurrence] = Field(default_factory=list)
    product_groups: List[ProductGroup] = Field(default_factory=list)
    highlight_strategy_quality_results: List[HighlightStrategyQualityResult] = Field(default_factory=list)
    audit_coherence_report: Optional[AuditCoherenceReport] = None

    # Stats
    original_duration: Optional[float] = None
    edited_duration: Optional[float] = None
    segments_kept: int = 0
    segments_deleted: int = 0

    def update_stats(self):
        # subtitle_fix / text_fix / merge_next 视频不剪切，统计上归入"保留"
        _kept_actions = (
            SegmentAction.KEEP,
            SegmentAction.SUBTITLE_FIX,
            SegmentAction.TEXT_FIX,
            SegmentAction.MERGE_NEXT,
        )
        kept = [s for s in self.audit_segments if s.action in _kept_actions]
        deleted = [s for s in self.audit_segments if s.action == SegmentAction.DELETE]
        self.segments_kept = len(kept)
        self.segments_deleted = len(deleted)
        self.edited_duration = sum(s.end - s.start for s in kept)


# ============================================================
# 学习模块模型
# ============================================================

class LearningTaskStatus(str, Enum):
    PENDING = "pending"                       # 待处理
    ASR_ORIGINAL = "asr_original"             # 原视频 ASR 中
    ASR_EDITED = "asr_edited"                 # 剪辑视频 ASR 中
    ASR_DONE = "asr_done"                     # 双轨 ASR 完成
    ALIGNING = "aligning"                     # 精修样本对齐中
    DIFF_READY = "diff_ready"                 # 差异待用户确认
    ANALYZING = "analyzing"                   # Claude 分析中
    PROPOSALS_READY = "proposals_ready"       # 修改草案待确认
    DONE = "done"                             # 分析完成
    ERROR = "error"                           # 错误


class LearningEditedVariant(BaseModel):
    """一个用于学习的精修集锦样本，可来自已有导出或外部上传。"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    filename: str
    video_path: str
    duration: Optional[float] = None
    source_kind: str = "uploaded_manual"  # uploaded_manual | exported_reel
    source_reel_id: Optional[str] = None
    asr_result: Optional[ASRResult] = None
    alignment_result: List[Dict[str, Any]] = Field(default_factory=list)
    error_message: Optional[str] = None


class LearningDiffItem(BaseModel):
    """源任务决策与精修样本证据的可选择学习单元。"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_segment_id: Optional[str] = None
    source_start: float
    source_end: float
    source_text: str
    ai_action: Optional[SegmentAction] = None
    human_action: Optional[SegmentAction] = None
    refined_action: str  # kept | omitted | unmatched
    category: str
    confidence: float = 0.0
    variant_ids: List[str] = Field(default_factory=list)
    selected: bool = True
    note: str = ""


class LearningProposal(BaseModel):
    """LLM 给出的候选变更；任何项都必须经用户单独确认。"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    target: str  # audit_prompt | highlight_prompt | glossary | user_style | export_rules | asr_config
    title: str
    rationale: str
    evidence_ids: List[str] = Field(default_factory=list)
    change: Dict[str, Any] = Field(default_factory=dict)
    risk: str = ""
    status: str = "pending"  # pending | applied | rejected | unsupported


class LearningTask(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    task_type: TaskType = TaskType.MONOLOGUE_CLEAN
    status: LearningTaskStatus = LearningTaskStatus.PENDING
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    # 关联主任务时冻结，避免主任务重跑 ASR/审计后让学习证据失真
    source_task_id: Optional[str] = None
    source_task_name: Optional[str] = None
    source_asr_version: Optional[int] = None
    source_audit_version: Optional[int] = None
    source_asr_result: Optional[ASRResult] = None
    source_audit_segments: List[Segment] = Field(default_factory=list)

    # 新流程：一个原任务可对应多个精修集锦样本
    edited_variants: List[LearningEditedVariant] = Field(default_factory=list)
    diff_items: List[LearningDiffItem] = Field(default_factory=list)
    proposals: List[LearningProposal] = Field(default_factory=list)

    # 原视频
    original_video_path: Optional[str] = None
    original_video_filename: Optional[str] = None
    original_video_duration: Optional[float] = None
    original_asr_result: Optional[ASRResult] = None

    # 人工剪辑视频
    edited_video_path: Optional[str] = None
    edited_video_filename: Optional[str] = None
    edited_video_duration: Optional[float] = None
    edited_asr_result: Optional[ASRResult] = None

    # Claude 分析结果
    analysis_result: Optional[str] = None        # 剪辑思路分析报告 (Markdown)
    new_prompt_content: Optional[str] = None      # 生成的新提示词全文
    new_prompt_path: Optional[str] = None         # 保存后的文件路径
    prompt_version: Optional[int] = None          # 版本号

    # 错误信息
    error_message: Optional[str] = None


class CreateLearningTaskRequest(BaseModel):
    name: str
    task_type: TaskType = TaskType.MONOLOGUE_CLEAN


class LearningAnalyzeRequest(BaseModel):
    claude_api_key: Optional[str] = None
    claude_model: str = ""
    openai_api_key: Optional[str] = None
    openai_model: str = ""


# ============================================================
# 剪辑任务请求/响应模型
# ============================================================

# Request/Response models
class CreateTaskRequest(BaseModel):
    name: str
    task_type: TaskType = TaskType.MONOLOGUE_CLEAN
    params: Optional[TaskParams] = None


class UpdateReferenceScriptRequest(BaseModel):
    reference_script: str = Field(max_length=10000)
    previous_script: str = Field(max_length=10000)


class OneClickTranslateStartRequest(BaseModel):
    source_language: str = "zh"
    target_language: str = "en"
    subtitle_model: LLMModelConfig = Field(default_factory=LLMModelConfig)
    tts_model: TTSModelConfig = Field(default_factory=TTSModelConfig)
    subtitle_api_key: Optional[str] = None
    tts_api_key: Optional[str] = None
    asr_backend: str = "funasr"
    asr_model: str = "paraformer-zh"
    # 单视频重跑接口专用：True 表示无视已有产物从 ASR 阶段从头重跑
    force_restart: bool = False
    # 最终导出专用：后端人工确认门禁，启动/重跑阶段不使用
    approval_token: Optional[str] = None
    # 用户重跑前的只读执行预览；首次运行可为空以保持兼容。
    preview_token: Optional[str] = None
    preview_mode: Literal["rerun", "resume", "reset"] = "rerun"


class UpdateTranslationCuesRequest(BaseModel):
    cues: List[TranslationCue]


class UpdateSegmentsRequest(BaseModel):
    segments: List[Segment]


class SubtitleStyle(BaseModel):
    """硬字幕烧录样式，对应 FFmpeg ASS force_style 参数"""
    font_name: str = "PingFang SC"   # 字体名称（与系统/FFmpeg 安装的字体一致）
    font_size: int = 28              # 字体大小（px）
    primary_color: str = "#FFFFFF"   # 文字颜色（前端传 hex，后端转 ASS 格式）
    outline_color: str = "#000000"   # 描边颜色
    outline: float = 2.0             # 描边厚度（0-4）
    shadow: float = 0.5              # 阴影距离（0-2）
    bold: bool = False               # 是否加粗
    alignment: int = 2               # 位置（1-9 numpad 布局，2=底部居中）
    margin_top: int = 40             # 上边距（px，顶部对齐时生效）
    # 竖屏(1080×1920)首次默认 100px≈5.2%，横屏(1920×1080)首次默认 80px≈7.4%，
    # 前端 openSubtitleConfig 会在首次使用时按视频方向动态覆盖；此处为后端兜底默认值
    margin_bottom: int = 80          # 下边距（px）
    margin_l: int = 60               # 左边距（px）
    margin_r: int = 60               # 右边距（px）
    # 每张字幕卡最多显示行数（0 = 不限制）。超出时自动将字幕拆成两张字幕卡依次显示，
    # 实际字符阈值由后端根据 font_size / margin_l / margin_r / 视频宽度动态计算。
    # 手动设置的 subtitle_line_break 优先级高于此自动拆分。
    max_lines: int = 2


class ExportRequest(BaseModel):
    mode: ExportMode = ExportMode.FFMPEG
    output_name: Optional[str] = None
    jianying_draft_folder: Optional[str] = None
    burn_subtitles: bool = False
    subtitle_style: Optional[SubtitleStyle] = None
    render_profile: RenderProfile = RenderProfile.FINAL
    approval_token: Optional[str] = None


class ClipRangeRequest(BaseModel):
    clip_start: Optional[float] = None  # None = 从头开始
    clip_end: Optional[float] = None    # None = 到结尾


class AuditRequest(BaseModel):
    claude_api_key: Optional[str] = None
    claude_model: str = ""
    openai_api_key: Optional[str] = None
    openai_model: str = ""
    style_mode: str = "immersive"
    # 用户在前端明确确认后才会传 True，不再静默降级
    force_rule_engine: bool = False
    # 本地 Ollama 支持
    provider: str = ""         # 空值表示使用 audit.semantic 角色路由
    ollama_model: str = ""
    ollama_base_url: str = ""
    # 重新审计时丢弃现有语义审计结果，但保留当前 ASR/文本整理版本。
    reset_prior_audit: bool = False
    preview_token: Optional[str] = None
    preview_mode: Literal["rerun", "resume", "reset"] = "rerun"


class UpdateHighlightReelsRequest(BaseModel):
    reels: List[HighlightReel]
    variant: HighlightReelVariant = HighlightReelVariant.AFTER_QUALITY
    expected_revision: int = Field(ge=0)


class HighlightReelBuildOptions(BaseModel):
    """产品集锦生成目标。空值表示使用系统默认值。"""
    # 唯一 product group 数量；每个产品最终只输出一条 winner。
    target_product_count: Optional[int] = Field(default=None, ge=1, le=200)
    run_model_generation: bool = True
    # 旧客户端兼容字段；产品集锦流程将其等价映射为最终产品/成片数量。
    target_reel_count: Optional[int] = Field(default=None, ge=1, le=20)
    min_reel_duration: Optional[float] = Field(default=60.0, ge=1.0, le=600.0)
    max_reel_duration: Optional[float] = Field(default=300.0, ge=1.0, le=1800.0)
    target_total_duration: Optional[float] = Field(default=None, ge=1.0, le=7200.0)
    preview_token: Optional[str] = None
    preview_mode: Literal["rerun", "resume", "reset"] = "rerun"


class HighlightReelAutoRunRequest(HighlightReelBuildOptions):
    """直播卖货集锦一键自动流程：ASR → 产品识别/审计 → 计划 → 质检 → 可选导出。"""
    auto_export: bool = False
    provider: str = ""
    openai_api_key: Optional[str] = None
    openai_model: str = ""
    ollama_model: str = ""
    ollama_base_url: str = ""
    style_mode: str = "immersive"
    force_rule_engine: bool = False
    asr_backend: str = "funasr"
    asr_model: str = "paraformer-zh"


class LogEntry(BaseModel):
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    level: str = "info"  # "info" | "warn" | "error" | "success"
    source: str = "system"  # "asr" | "claude" | "ffmpeg" | "jianying" | "system"
    message: str
    progress: Optional[float] = None  # 0.0 - 1.0
