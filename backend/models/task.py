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
    PENDING = "pending"           # 待处理
    ASR_RUNNING = "asr_running"   # ASR中
    ASR_DONE = "asr_done"         # ASR完成
    AUDIT_RUNNING = "audit_running"  # 审计中
    REVIEW = "review"             # 待Review
    EXPORT_RUNNING = "export_running"  # 导出中
    DONE = "done"                 # 已完成
    ERROR = "error"               # 错误


class TaskType(str, Enum):
    HIGHLIGHT_REEL = "highlight_reel"   # 精彩集锦 (1-3min)
    INTERVIEW_COMPRESS = "interview_compress"  # 访谈压缩 (10-20min)
    MONOLOGUE_CLEAN = "monologue_clean"  # 口播精修


class ExportMode(str, Enum):
    FFMPEG = "ffmpeg"       # 路径A: FFmpeg 无损切割
    JIANYING = "jianying"   # 路径B: 剪映草稿


class SegmentAction(str, Enum):
    KEEP = "keep"
    DELETE = "delete"


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
    # Style tag from Claude (e.g. "Golden_Template_A")
    style: Optional[str] = None


class ASRResult(BaseModel):
    words: List[WordTimestamp] = []
    segments: List[Segment] = []
    tagged_script: str = ""
    duration: float = 0.0
    language: str = "zh"
    speakers: List[str] = []


class TaskParams(BaseModel):
    silence_threshold: float = 0.5    # 静音阈值（秒）
    breath_lead_ms: int = 150          # 气口预留开头（毫秒）
    breath_tail_ms: int = 100          # 气口预留结尾（毫秒）
    min_segment_duration: float = 1.5  # 最短片段时长（秒）
    filler_words: List[str] = ["嗯", "啊", "那个", "然后", "就是说", "就是", "这个"]
    retake_char_threshold: int = 5     # 重说识别字符数阈值
    style_mode: str = "immersive"      # "quick_cut" | "immersive"
    enable_diarization: bool = True    # 是否开启说话人识别
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

    # Processing results
    asr_result: Optional[ASRResult] = None
    audit_segments: List[Segment] = []
    audit_json_path: Optional[str] = None

    # Export results
    export_path: Optional[str] = None
    jianying_draft_path: Optional[str] = None

    # Config
    params: TaskParams = Field(default_factory=TaskParams)

    # Error info
    error_message: Optional[str] = None

    # Stats
    original_duration: Optional[float] = None
    edited_duration: Optional[float] = None
    segments_kept: int = 0
    segments_deleted: int = 0

    def update_stats(self):
        kept = [s for s in self.audit_segments if s.action == SegmentAction.KEEP]
        deleted = [s for s in self.audit_segments if s.action == SegmentAction.DELETE]
        self.segments_kept = len(kept)
        self.segments_deleted = len(deleted)
        self.edited_duration = sum(s.end - s.start for s in kept)


# Request/Response models
class CreateTaskRequest(BaseModel):
    name: str
    task_type: TaskType = TaskType.MONOLOGUE_CLEAN
    params: Optional[TaskParams] = None


class UpdateSegmentsRequest(BaseModel):
    segments: List[Segment]


class ExportRequest(BaseModel):
    mode: ExportMode = ExportMode.FFMPEG
    output_name: Optional[str] = None
    jianying_draft_folder: Optional[str] = None


class AuditRequest(BaseModel):
    claude_api_key: Optional[str] = None
    claude_model: str = "claude-3-5-sonnet-20241022"
    style_mode: str = "immersive"


class LogEntry(BaseModel):
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    level: str = "info"  # "info" | "warn" | "error" | "success"
    source: str = "system"  # "asr" | "claude" | "ffmpeg" | "jianying" | "system"
    message: str
    progress: Optional[float] = None  # 0.0 - 1.0
