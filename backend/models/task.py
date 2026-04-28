"""
GoldenClip Data Models
Design: 暗金剪辑台 · 编导美学
All task states, segment states, and export configs are defined here.
"""

from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
import uuid


class TaskStatus(str, Enum):
    PENDING = "pending"                       # 待处理
    ASR_RUNNING = "asr_running"               # ASR中
    ASR_DONE = "asr_done"                     # ASR完成
    SEG_OPTIM_RUNNING = "seg_optim_running"   # 段落优化中
    SEG_OPTIM_DONE = "seg_optim_done"         # 段落优化完成
    AUDIT_RUNNING = "audit_running"           # 审计中
    REVIEW = "review"                         # 待Review
    EXPORT_RUNNING = "export_running"         # 导出中
    DONE = "done"                             # 已完成
    ERROR = "error"                           # 错误


class TaskType(str, Enum):
    HIGHLIGHT_REEL = "highlight_reel"   # 精彩集锦 (1-3min)
    INTERVIEW_COMPRESS = "interview_compress"  # 直播访谈压缩
    MONOLOGUE_CLEAN = "monologue_clean"  # 口播精修


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


class WordTimestamp(BaseModel):
    word: str
    start: float
    end: float
    speaker: Optional[str] = None  # e.g. "spk0", "spk1"


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
    # Tagged script representation for Claude
    tagged_text: Optional[str] = None
    # Manual override by user
    user_override: bool = False
    # Claude 原始决策冻结：审计完成后快照，不随用户操作改变
    claude_action: Optional[SegmentAction] = None
    claude_reason: Optional[str] = None
    # Style tag from Claude (e.g. "Golden_Template_A")
    style: Optional[str] = None
    # Highlight reel: clip grouping for one-to-many exports (1, 2, 3...)
    clip_group: Optional[int] = None
    # Highlight reel: clip title, usually only set on the first segment of a group
    clip_title: Optional[str] = None
    # Highlight reel: AI score (1-10) and reason, only set on first segment of a group
    clip_score: Optional[float] = None
    clip_reason: Optional[str] = None
    # 字幕层修正文本：action=subtitle_fix / text_fix 时使用，音频不剪切，字幕显示此文本
    # subtitle_fix 例：text="就就是顺着往后面上就行了" → display_text="就是顺着往后面上就行了"
    # text_fix    例：text="老道了很久" → display_text="唠叨了很久"（ASR同音字纠错）
    display_text: Optional[str] = None
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


class PreprocStats(BaseModel):
    """ASR 预处理阶段的统计数据，随 ASRResult 一同保存。"""
    total_words: int = 0          # 识别的总字符数
    filler_count: int = 0         # <FIL> 语气词标记数
    stutter_count: int = 0        # <STU> 结巴标记数
    silence_count: int = 0        # <SIL> 停顿段数
    silence_total_s: float = 0.0  # 停顿总时长（秒）
    segment_count: int = 0        # 最终分段数
    speaker_count: int = 0        # 说话人数量


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
    # 预处理统计
    preprocess_stats: Optional[PreprocStats] = None


class ASRSnapshot(BaseModel):
    """每次 ASR 识别的历史快照"""
    version: int                    # 版本号，从 1 开始递增
    created_at: str                 # ISO 时间戳，识别完成时间
    engine: str                     # 识别引擎，如 "FunASR Paraformer-zh" / "Whisper base"
    segments_count: int             # 识别到的片段数量
    duration: float                 # 视频时长（秒）
    asr_result: ASRResult           # 完整识别结果快照


class SegOptimSnapshot(BaseModel):
    """每次段落优化的历史快照"""
    version: int                    # 版本号，从 1 开始递增
    created_at: str                 # ISO 时间戳，优化完成时间
    model: str                      # 使用的模型，如 "claude-3.5-sonnet" / "规则引擎"
    segments_before: int            # 优化前片段数量
    segments_after: int             # 优化后片段数量
    optim_deleted_count: int = 0    # 段落优化标记为 DELETE 的片段数（用于 token 节约估算）
    optim_deleted_chars: int = 0    # 被删除片段的总字数（用于精确估算 token）
    segments: List[Segment]         # 优化后的完整段落列表（用于版本切换恢复）


class AuditSnapshot(BaseModel):
    """每次语义审计的历史快照"""
    version: int                    # 版本号，从 1 开始递增
    created_at: str                 # ISO 时间戳，审计完成时间
    model: str                      # 审计模型，如 "claude-3-5-sonnet" / "规则引擎"
    segments_count: int             # 总片段数量
    segments_kept: int              # 保留片段数量
    segments_deleted: int           # 删除片段数量


class TaskParams(BaseModel):
    theme_id: Optional[str] = None    # 视频主题（仅 highlight_reel）：interview / product_demo / vlog / lecture
    silence_threshold: float = 0.5    # 静音阈值（秒）
    breath_lead_ms: int = 150          # 气口预留开头（毫秒）
    breath_tail_ms: int = 100          # 气口预留结尾（毫秒）
    min_segment_duration: float = 1.5  # 最短片段时长（秒）
    filler_words: List[str] = ["嗯", "啊", "那个", "然后", "就是说", "就是", "这个"]
    retake_char_threshold: int = 5     # 重说识别字符数阈值
    style_mode: str = "immersive"      # "quick_cut" | "immersive"
    enable_diarization: bool = True    # 是否开启说话人识别
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


class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    task_type: TaskType = TaskType.MONOLOGUE_CLEAN
    status: TaskStatus = TaskStatus.PENDING
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    # File paths
    video_path: Optional[str] = None
    video_filename: Optional[str] = None
    video_duration: Optional[float] = None
    thumbnail_path: Optional[str] = None

    # 用户手动设置的裁剪区间（秒），None 表示不限制
    clip_start: Optional[float] = None
    clip_end: Optional[float] = None
    # 从哪个一剪多任务派生而来（溯源用），None 表示直接创建
    source_task_id: Optional[str] = None
    # 从一剪多派生精修任务时，保存原始 clip 标题（封面与简介弹窗预填用）
    clip_title: Optional[str] = None

    # Processing results
    asr_result: Optional[ASRResult] = None
    asr_history: List[ASRSnapshot] = []   # 历史识别快照，最新在最后
    asr_current_version: Optional[int] = None  # 当前激活的 ASR 版本号
    audit_segments: List[Segment] = []
    audit_json_path: Optional[str] = None
    seg_optim_history: List[SegOptimSnapshot] = []  # 历史段落优化快照，最新在最后
    seg_optim_current_version: Optional[int] = None  # 当前激活的段落优化版本号
    audit_history: List[AuditSnapshot] = []   # 历史审计快照，最新在最后
    audit_current_version: Optional[int] = None  # 当前激活的审计版本号

    # Export results
    export_path: Optional[str] = None
    jianying_draft_path: Optional[str] = None

    # Config
    params: TaskParams = Field(default_factory=TaskParams)

    # Error info
    error_message: Optional[str] = None

    # 金句开场：有序 segment_id 列表，导出时这些片段会被放到视频最开头
    golden_quote_order: List[str] = []

    # 一剪多 per-clip hook：{clip首段segment_id → 有序hook_segment_id列表}
    clip_hook_orders: Dict[str, List[str]] = {}

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
    ANALYZING = "analyzing"                   # Claude 分析中
    DONE = "done"                             # 分析完成
    ERROR = "error"                           # 错误


class LearningTask(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    task_type: TaskType = TaskType.MONOLOGUE_CLEAN
    status: LearningTaskStatus = LearningTaskStatus.PENDING
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())

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
    claude_model: str = "claude-3-5-sonnet-20241022"


# ============================================================
# 剪辑任务请求/响应模型
# ============================================================

# Request/Response models
class CreateTaskRequest(BaseModel):
    name: str
    task_type: TaskType = TaskType.MONOLOGUE_CLEAN
    params: Optional[TaskParams] = None
    theme_id: Optional[str] = None    # 仅 highlight_reel 使用


class UpdateSegmentsRequest(BaseModel):
    segments: List[Segment]


class HookConfig(BaseModel):
    """Hook 标题字幕：在 clip 开头叠加一行文字"""
    enabled: bool = False
    text: str = ""
    duration: float = 3.0
    font_size: int = 48
    color: str = "#FFFFFF"          # hex 文字颜色
    outline_color: str = "#000000"  # hex 描边颜色
    outline: float = 2.0
    bold: bool = False
    h_align: str = "center"         # left | center | right
    v_align: str = "top"            # top | middle | bottom
    x_pct: Optional[float] = None   # 0-100，文字中心 X 百分比（优先级高于 h_align）
    y_pct: Optional[float] = None   # 0-100，文字中心 Y 百分比（优先级高于 v_align）


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
    x_pct: Optional[float] = None   # 0-100，文字中心 X 百分比（拖拽/九宫格定位）
    y_pct: Optional[float] = None   # 0-100，文字中心 Y 百分比（拖拽/九宫格定位）


class ExportRequest(BaseModel):
    mode: ExportMode = ExportMode.FFMPEG
    output_name: Optional[str] = None
    jianying_draft_folder: Optional[str] = None
    burn_subtitles: bool = False
    subtitle_style: Optional[SubtitleStyle] = None
    cover_image_base64: Optional[str] = None  # data:image/png;base64,... 或纯 base64
    cover_duration: float = 0.04              # 封面静止时长（秒），默认 1 帧（25fps）


class ClipRangeRequest(BaseModel):
    clip_start: Optional[float] = None  # None = 从头开始
    clip_end: Optional[float] = None    # None = 到结尾


class AuditRequest(BaseModel):
    claude_api_key: Optional[str] = None
    claude_model: str = "claude-3-5-sonnet-20241022"
    style_mode: str = "immersive"
    # 用户在前端明确确认后才会传 True，不再静默降级
    force_rule_engine: bool = False
    # 本地 Ollama 支持
    provider: str = "claude"          # "claude" | "ollama"
    ollama_model: str = "deepseek-r1:14b"
    ollama_base_url: str = "http://localhost:11434"
    # 一剪多专用参数（None = 使用 provider 默认值）
    highlight_target_dur: Optional[float] = None    # 每次分析窗口时长（秒），云端默认1200，本地默认360
    highlight_max_clips: Optional[int] = None       # 每窗口最多提取片段数，云端默认6，本地默认2
    highlight_clip_min_dur: Optional[float] = None  # 片段最短时长（秒），默认45
    highlight_clip_max_dur: Optional[float] = None  # 片段最长时长（秒），默认190
    highlight_total_clips: Optional[int] = None     # 最终保留片段总数，默认10


class LogEntry(BaseModel):
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    level: str = "info"  # "info" | "warn" | "error" | "success"
    source: str = "system"  # "asr" | "claude" | "ffmpeg" | "jianying" | "system"
    message: str
    progress: Optional[float] = None  # 0.0 - 1.0
