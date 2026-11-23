"""任务接口的序列化辅助。列表使用字段白名单，避免新增重字段意外泄漏。"""

from ..models.task import Task, LearningTask

# 保留常量兼容旧导入；真正的列表序列化使用下方白名单。
TASK_LIST_EXCLUDE_FIELDS = {
    "asr_result": True,
    "asr_history": True,
    "analysis_history": True,
    "audit_segments": True,
    "audit_history": True,
    "params": True,
    "golden_quote_order": True,
    "translation_videos": True,
    "execution_runs": True,
    "highlight_reels": True,
    "highlight_reels_before_quality": True,
    "highlight_quality_report": True,
    "highlight_quality_report_before_quality": True,
    "product_occurrences": True,
    "product_groups": True,
    "highlight_strategy_quality_results": True,
    "audit_coherence_report": True,
    "export_jobs": True,
}

TASK_DETAIL_EXCLUDE_FIELDS = {
    # 详情页只展示 ASR 历史元数据；切换版本由后端完成，无需重复传输完整识别结果。
    "asr_history": {"__all__": {"asr_result": True}},
    # 分析历史列表当前未展示完整片段和报告；当前生效内容仍保留在 task.asr_result。
    "analysis_history": {"__all__": {"segments": True, "analysis_report": True}},
}

LEARNING_TASK_LIST_EXCLUDE_FIELDS = {
    "original_asr_result": True,
    "edited_asr_result": True,
    "source_asr_result": True,
    "source_audit_segments": True,
    "edited_variants": {"__all__": {"asr_result": True, "alignment_result": True}},
    "diff_items": True,
    "proposals": True,
    "analysis_result": True,
    "new_prompt_content": True,
    "new_prompt_path": True,
}


def serialize_task_list_item(task: Task) -> dict:
    # 不使用 model_dump(exclude=...)：Task 后续新增大型业务字段时，排除列表很容易
    # 漏同步，曾导致一个 103 MiB 任务把列表响应放大到 10 MiB。
    payload = {
        "id": task.id,
        "name": task.name,
        "task_type": task.task_type,
        "status": task.status,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "revision": task.revision,
        "video_path": task.video_path,
        "video_filename": task.video_filename,
        "video_duration": task.video_duration,
        "thumbnail_path": task.thumbnail_path,
        "original_duration": task.original_duration,
        "edited_duration": task.edited_duration,
        "segments_kept": task.segments_kept,
        "segments_deleted": task.segments_deleted,
        "error_message": task.error_message,
    }
    latest_run = task.execution_runs[-1] if task.execution_runs else None
    payload["execution_run"] = (
        latest_run.model_dump(exclude={"stages"}) if latest_run is not None else None
    )
    return payload


def serialize_task_detail(task: Task) -> dict:
    return task.model_dump(exclude=TASK_DETAIL_EXCLUDE_FIELDS)


def serialize_learning_task_list_item(task: LearningTask) -> dict:
    return task.model_dump(exclude=LEARNING_TASK_LIST_EXCLUDE_FIELDS)
