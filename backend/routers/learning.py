"""学习模块：学习任务 CRUD、双轨 ASR、OpenAI 学习分析、提示词版本、手动模式。"""

import os
import asyncio
import json
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel

from ..models.task import (
    TaskParams,
    LearningTask, LearningTaskStatus, LearningEditedVariant, LearningProposal,
    CreateLearningTaskRequest, LearningAnalyzeRequest,
)
from ..models.app_config import ROUTERHUB_BASE_URL
from ..services.learning_store import get_learning_store
from ..services.task_store import get_store as get_task_store
from ..services.asr_pipeline import run_asr_pipeline, get_video_duration
from ..services.learning_analyzer import (
    run_learning_analysis, save_versioned_prompt, get_prompt_versions,
    diff_transcripts, build_analysis_prompt, build_rewrite_prompt,
    load_learning_system_prompt, load_rewrite_system_prompt, apply_prompt_changes,
    build_decision_diffs, parse_learning_proposals, run_source_learning_analysis,
    activate_prompt_version,
)
from ..services.prompt_optimizer import merge_user_style
from ..services.app_config_store import (
    merge_feature_llm_request,
    resolve_role_candidates,
    role_primary_or_feature,
)
from ..services.semantic_auditor import load_system_prompt as load_audit_system_prompt
from ..core.paths import LEARNING_UPLOADS_DIR
from ..core.ws import manager, log_to_ws
from ..core.task_runner import _learning_active_tasks, _cancel_learning_task
from ..core.serialization import serialize_learning_task_list_item
from ..core.uploads import _save_upload_file
from .asr import AsrRequest

router = APIRouter()


def _track_learning_task(task_id: str, task: asyncio.Task) -> None:
    """登记任务，并防止旧任务的 done callback 误删同 key 的新任务。"""
    _learning_active_tasks[task_id] = task

    def _cleanup(done_task: asyncio.Task) -> None:
        if _learning_active_tasks.get(task_id) is done_task:
            _learning_active_tasks.pop(task_id, None)

    task.add_done_callback(_cleanup)


# ============================================================
# 学习模块 (Learning Module)
# ============================================================

@router.post("/api/learning/tasks")
async def create_learning_task(req: CreateLearningTaskRequest):
    try:
        store = get_learning_store()
        task = LearningTask(name=req.name, task_type=req.task_type)
        store.create(task)
        return task.model_dump()
    except Exception as e:
        err_msg = str(e) if str(e) else "创建学习任务失败"
        print(f"[create_learning_task] 失败: {e}", flush=True)
        raise HTTPException(status_code=500, detail=err_msg)


@router.get("/api/learning/tasks")
async def list_learning_tasks():
    store = get_learning_store()
    return [serialize_learning_task_list_item(t) for t in store.get_all()]


@router.get("/api/learning/tasks/{task_id}")
async def get_learning_task(task_id: str):
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")
    return task.model_dump()


class CreateLearningFromSourceRequest(BaseModel):
    name: str
    source_task_id: str


class UpdateDiffSelectionRequest(BaseModel):
    selected: bool
    note: str = ""


class ApplyProposalRequest(BaseModel):
    action: str  # apply | reject


@router.get("/api/learning/sources")
async def list_learning_sources():
    """只返回可作为学习基准的已完成 ASR 与审核任务摘要。"""
    return [
        {
            "id": task.id,
            "name": task.name,
            "task_type": task.task_type.value,
            "status": task.status.value,
            "asr_version": task.asr_current_version,
            "audit_version": task.audit_current_version,
            "segments_count": len(task.audit_segments),
            "human_overrides": sum(1 for segment in task.audit_segments if segment.user_override),
            "highlight_reels": [
                {
                    "reel_id": reel.reel_id,
                    "title": reel.title,
                    "export_path": reel.export_path,
                }
                for reel in task.highlight_reels
                if reel.export_path and os.path.exists(reel.export_path)
            ],
        }
        for task in get_task_store().get_all()
        if task.asr_result and task.audit_segments
        and task.task_type.value in {"monologue_clean", "interview_compress", "highlight_reel"}
    ]


@router.post("/api/learning/tasks/from-source")
async def create_learning_task_from_source(req: CreateLearningFromSourceRequest):
    """绑定主任务并冻结 ASR、AI 审计与人工最终审核结果。"""
    source = get_task_store().get(req.source_task_id)
    if not source:
        raise HTTPException(404, "原任务不存在")
    if source.task_type.value not in {"monologue_clean", "interview_compress", "highlight_reel"}:
        raise HTTPException(400, "该原任务类型暂不支持提示词学习")
    if not source.asr_result or not source.audit_segments:
        raise HTTPException(400, "原任务须先完成 ASR 和语义审核")
    task = LearningTask(
        name=req.name,
        task_type=source.task_type,
        source_task_id=source.id,
        source_task_name=source.name,
        source_asr_version=source.asr_current_version,
        source_audit_version=source.audit_current_version,
        source_asr_result=source.asr_result.model_copy(deep=True),
        source_audit_segments=[segment.model_copy(deep=True) for segment in source.audit_segments],
    )
    get_learning_store().create(task)
    return task.model_dump()


@router.post("/api/learning/tasks/{task_id}/edited-variants")
async def upload_learning_edited_variants(task_id: str, files: List[UploadFile] = File(...)):
    """上传多个外部精修集锦；每个文件独立保留，绝不覆盖已有样本。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")
    if not task.source_task_id:
        raise HTTPException(400, "该旧学习任务未关联原任务，无法添加多集锦样本")
    created = []
    for file in files:
        ext = Path(file.filename or "").suffix.lower()
        if ext not in [".mp4", ".mov", ".avi", ".mkv", ".webm"]:
            raise HTTPException(400, f"不支持的视频格式: {ext}")
        variant = LearningEditedVariant(
            filename=file.filename or "精修集锦",
            video_path=str(LEARNING_UPLOADS_DIR / f"{task_id}_{os.urandom(4).hex()}{ext}"),
        )
        await _save_upload_file(file, Path(variant.video_path))
        variant.duration = await asyncio.get_event_loop().run_in_executor(
            None, get_video_duration, variant.video_path
        )
        task.edited_variants.append(variant)
        created.append(variant.model_dump())
    store.update(task)
    return {"variants": created}


@router.post("/api/learning/tasks/{task_id}/align")
async def trigger_source_learning_alignment(task_id: str, body: AsrRequest = None):
    """为所有精修样本执行 ASR 并映射至冻结的原视频时间轴。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")
    if not task.source_asr_result or not task.source_audit_segments:
        raise HTTPException(400, "学习任务缺少冻结的原任务快照")
    if not task.edited_variants:
        raise HTTPException(400, "请先上传至少一个精修集锦视频")
    _cancel_learning_task(task_id)
    backend = (body.backend if body else None) or "funasr"
    whisper_model = (body.model if body else None) or "base"
    bg_task = asyncio.create_task(_run_source_learning_alignment(task_id, whisper_model, backend))
    _track_learning_task(task_id, bg_task)
    return {"success": True, "message": "精修集锦 ASR 与对齐任务已启动"}


async def _run_source_learning_alignment(task_id: str, whisper_model: str, backend: str):
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        return
    async def log(level, source, msg, progress=None):
        await log_to_ws(f"learn_{task_id}", level, source, msg, progress)
    try:
        task.status = LearningTaskStatus.ASR_EDITED
        store.update(task)
        params = TaskParams(enable_diarization=False)
        for index, variant in enumerate(task.edited_variants, 1):
            if not variant.asr_result:
                await log("info", "asr", f"识别精修样本 {index}/{len(task.edited_variants)}：{variant.filename}")
                variant.asr_result = await run_asr_pipeline(
                    variant.video_path, params, log, whisper_model=whisper_model, backend=backend
                )
                variant.duration = variant.asr_result.duration
                store.update(task)
        task.status = LearningTaskStatus.ALIGNING
        store.update(task)
        task.diff_items = build_decision_diffs(task.source_audit_segments, task.edited_variants)
        task.status = LearningTaskStatus.DIFF_READY
        store.update(task)
        await log("success", "system", f"差异已生成，共 {len(task.diff_items)} 条，默认已选择高置信证据")
    except asyncio.CancelledError:
        return
    except Exception as exc:
        task.status = LearningTaskStatus.ERROR
        task.error_message = f"精修集锦对齐失败: {exc}"
        store.update(task)
        await log("error", "asr", task.error_message)


@router.patch("/api/learning/tasks/{task_id}/diffs/{diff_id}")
async def update_learning_diff_selection(task_id: str, diff_id: str, req: UpdateDiffSelectionRequest):
    task = get_learning_store().get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")
    for item in task.diff_items:
        if item.id == diff_id:
            item.selected = req.selected
            item.note = req.note
            get_learning_store().update(task)
            return item.model_dump()
    raise HTTPException(404, "差异项不存在")


@router.post("/api/learning/tasks/{task_id}/proposals/{proposal_id}")
async def resolve_learning_proposal(task_id: str, proposal_id: str, req: ApplyProposalRequest):
    """逐条采纳或忽略草案；未知目标一律不产生外部写入。"""
    if req.action not in {"apply", "reject"}:
        raise HTTPException(400, "action 仅支持 apply 或 reject")
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")
    proposal = next((item for item in task.proposals if item.id == proposal_id), None)
    if not proposal:
        raise HTTPException(404, "学习草案不存在")
    if proposal.status != "pending":
        raise HTTPException(400, "该学习草案已处理")
    if req.action == "reject":
        proposal.status = "rejected"
        store.update(task)
        return proposal.model_dump()

    try:
        if proposal.target in {"audit_prompt", "highlight_prompt"}:
            changes = proposal.change.get("changes") if isinstance(proposal.change, dict) else None
            if not isinstance(changes, list) or not changes:
                proposal.status = "unsupported"
            else:
                task_type = task.task_type.value
                current_prompt = load_audit_system_prompt(task_type)
                new_prompt = apply_prompt_changes(current_prompt, json.dumps({"changes": changes}, ensure_ascii=False))
                if new_prompt == current_prompt:
                    proposal.status = "unsupported"
                else:
                    filepath, version = save_versioned_prompt(task_type, new_prompt)
                    activate_prompt_version(task_type, filepath)
                    task.new_prompt_content = new_prompt
                    task.new_prompt_path = filepath
                    task.prompt_version = version
                    proposal.status = "applied"
        elif proposal.target == "user_style":
            style = proposal.change.get("style") if isinstance(proposal.change, dict) else None
            if isinstance(style, dict):
                merge_user_style({str(key): str(value) for key, value in style.items()})
                proposal.status = "applied"
            else:
                proposal.status = "unsupported"
        elif proposal.target == "glossary":
            terms = proposal.change.get("terms") if isinstance(proposal.change, dict) else None
            if isinstance(terms, list) and all(isinstance(term, str) and term.strip() for term in terms):
                glossary = Path(__file__).parent.parent / "data" / "glossary.txt"
                existing = glossary.read_text(encoding="utf-8") if glossary.exists() else ""
                additions = [term.strip() for term in terms if term.strip() not in existing]
                if additions:
                    glossary.write_text(existing.rstrip() + "\n" + "\n".join(additions) + "\n", encoding="utf-8")
                proposal.status = "applied"
            else:
                proposal.status = "unsupported"
        else:
            # 导出与 ASR 目前没有受控的运行时配置适配器，禁止隐式更改代码或模型。
            proposal.status = "unsupported"
    except Exception as exc:
        raise HTTPException(400, f"应用学习草案失败: {exc}")

    store.update(task)
    return proposal.model_dump()


@router.delete("/api/learning/tasks/{task_id}")
async def delete_learning_task(task_id: str):
    # 先取消正在运行的后台 ASR / 分析任务
    _cancel_learning_task(task_id)

    store = get_learning_store()
    if not store.delete(task_id):
        raise HTTPException(404, "学习任务不存在")
    manager.clear_buffer(f"learn_{task_id}")
    return {"success": True}


@router.post("/api/learning/tasks/{task_id}/upload-original")
async def upload_original_video(task_id: str, file: UploadFile = File(...)):
    """上传原视频。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")

    ext = Path(file.filename).suffix.lower()
    if ext not in [".mp4", ".mov", ".avi", ".mkv", ".webm"]:
        raise HTTPException(400, f"不支持的视频格式: {ext}")

    video_path = LEARNING_UPLOADS_DIR / f"{task_id}_original{ext}"
    await _save_upload_file(file, video_path)

    duration = await asyncio.get_event_loop().run_in_executor(None, get_video_duration, str(video_path))
    task.original_video_path = str(video_path)
    task.original_video_filename = file.filename
    task.original_video_duration = duration
    store.update(task)

    return {
        "success": True,
        "video_path": str(video_path),
        "duration": duration,
        "filename": file.filename,
    }


@router.post("/api/learning/tasks/{task_id}/upload-edited")
async def upload_edited_video(task_id: str, file: UploadFile = File(...)):
    """上传人工剪辑视频。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")

    ext = Path(file.filename).suffix.lower()
    if ext not in [".mp4", ".mov", ".avi", ".mkv", ".webm"]:
        raise HTTPException(400, f"不支持的视频格式: {ext}")

    video_path = LEARNING_UPLOADS_DIR / f"{task_id}_edited{ext}"
    await _save_upload_file(file, video_path)

    duration = await asyncio.get_event_loop().run_in_executor(None, get_video_duration, str(video_path))
    task.edited_video_path = str(video_path)
    task.edited_video_filename = file.filename
    task.edited_video_duration = duration
    store.update(task)

    return {
        "success": True,
        "video_path": str(video_path),
        "duration": duration,
        "filename": file.filename,
    }


@router.post("/api/learning/tasks/{task_id}/asr")
async def trigger_learning_asr(task_id: str, body: AsrRequest = None):
    """对原视频和剪辑视频依次执行 ASR。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")
    if not task.original_video_path or not os.path.exists(task.original_video_path):
        raise HTTPException(400, "请先上传原视频")
    if not task.edited_video_path or not os.path.exists(task.edited_video_path):
        raise HTTPException(400, "请先上传人工剪辑视频")

    backend = (body.backend if body else None) or "funasr"
    whisper_model = (body.model if body else None) or "base"

    # 若该任务已有正在运行的后台任务，先取消
    _cancel_learning_task(task_id)

    bg_task = asyncio.create_task(_run_learning_asr(task_id, whisper_model, backend))
    _track_learning_task(task_id, bg_task)
    return {"success": True, "message": "双轨 ASR 任务已启动"}


async def _run_learning_asr(task_id: str, whisper_model: str, backend: str):
    """后台执行双轨 ASR：先原视频，再剪辑视频。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        return

    async def log(level, source, msg, progress=None):
        await log_to_ws(f"learn_{task_id}", level, source, msg, progress)

    # 学习双轨 ASR 只需文本对齐，不需要说话人分离
    params = TaskParams(enable_diarization=False)

    # 阶段一：原视频 ASR
    try:
        task.status = LearningTaskStatus.ASR_ORIGINAL
        store.update(task)
        await log("info", "asr", "开始原视频 ASR...")

        original_result = await run_asr_pipeline(
            task.original_video_path, params, log,
            whisper_model=whisper_model, backend=backend,
        )
        task.original_asr_result = original_result
        task.original_video_duration = original_result.duration
        store.update(task)
        await log("success", "asr", f"原视频 ASR 完成，共 {len(original_result.segments)} 段")
    except asyncio.CancelledError:
        # 任务被删除，静默退出，不写入错误状态
        return
    except Exception as e:
        task.status = LearningTaskStatus.ERROR
        task.error_message = f"原视频 ASR 失败: {str(e)}"
        store.update(task)
        await log("error", "asr", task.error_message)
        return

    # 阶段二：剪辑视频 ASR
    try:
        task.status = LearningTaskStatus.ASR_EDITED
        store.update(task)
        await log("info", "asr", "开始剪辑视频 ASR...")

        edited_result = await run_asr_pipeline(
            task.edited_video_path, params, log,
            whisper_model=whisper_model, backend=backend,
        )
        task.edited_asr_result = edited_result
        task.edited_video_duration = edited_result.duration
        task.status = LearningTaskStatus.ASR_DONE
        store.update(task)
        await log("success", "asr", f"剪辑视频 ASR 完成，共 {len(edited_result.segments)} 段")
    except asyncio.CancelledError:
        # 任务被删除，静默退出
        return
    except Exception as e:
        task.status = LearningTaskStatus.ERROR
        task.error_message = f"剪辑视频 ASR 失败: {str(e)}"
        store.update(task)
        await log("error", "asr", task.error_message)


@router.post("/api/learning/tasks/{task_id}/analyze")
async def trigger_learning_analysis(task_id: str, req: LearningAnalyzeRequest):
    """触发 OpenAI 分析人工剪辑思路。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")
    if task.source_task_id and not any(item.selected for item in task.diff_items):
        raise HTTPException(400, "请至少选择一条差异作为学习证据")
    if not task.source_task_id and (not task.original_asr_result or not task.edited_asr_result):
        raise HTTPException(400, "请先完成双轨 ASR")

    # 若该任务已有正在运行的后台任务，先取消
    _cancel_learning_task(task_id)

    bg_task = asyncio.create_task(_run_learning_analysis(task_id, req))
    _track_learning_task(task_id, bg_task)
    return {"success": True, "message": "学习分析已启动"}


async def _run_learning_analysis(task_id: str, req: LearningAnalyzeRequest):
    """后台执行 OpenAI 学习分析。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        return

    task.status = LearningTaskStatus.ANALYZING
    store.update(task)

    async def log(level, source, msg, progress=None):
        await log_to_ws(f"learn_{task_id}", level, source, msg, progress)

    try:
        explicit_override = bool(
            req.openai_model
            or req.claude_model
            or req.openai_api_key
            or req.claude_api_key
        )
        requested_model = req.openai_model or req.claude_model
        if requested_model and any(
            candidate.model == requested_model
            for candidate in resolve_role_candidates("learning.analyze")
        ):
            explicit_override = False
        feat = (
            merge_feature_llm_request(
                "learning",
                openai_model=req.openai_model or req.claude_model or None,
                api_key=req.openai_api_key or req.claude_api_key,
            )
            if explicit_override
            else role_primary_or_feature("learning.analyze", "learning")
        )
        if task.source_task_id:
            await log("info", "openai", "根据用户选择的差异证据生成多目标修改草案...")
            task.proposals = await run_source_learning_analysis(
                task, api_key=feat.api_key, model=feat.model,
                base_url=feat.base_url or ROUTERHUB_BASE_URL,
                use_role_routing=not explicit_override,
            )
            task.analysis_result = f"已生成 {len(task.proposals)} 条待确认学习草案。"
            task.status = LearningTaskStatus.PROPOSALS_READY
        else:
            def persist_analysis_checkpoint(analysis_report: str) -> None:
                checkpoint_task = store.get(task_id)
                if not checkpoint_task:
                    return
                checkpoint_task.analysis_result = analysis_report
                checkpoint_task.status = LearningTaskStatus.ANALYZING
                checkpoint_task.error_message = None
                store.update(checkpoint_task)

            result = await run_learning_analysis(
                task=task,
                api_key=feat.api_key,
                model=feat.model,
                log_callback=log,
                base_url=feat.base_url or ROUTERHUB_BASE_URL,
                checkpoint_callback=persist_analysis_checkpoint,
                use_role_routing=not explicit_override,
            )
            task.analysis_result = result["analysis_result"]
            task.new_prompt_content = result["new_prompt_content"]
            task.status = LearningTaskStatus.DONE
        store.update(task)

        await manager.broadcast(f"learn_{task_id}", {
            "type": "status_change",
            "task_id": task_id,
            "status": LearningTaskStatus.DONE,
        })

    except asyncio.CancelledError:
        # 任务被删除，静默退出
        return
    except Exception as e:
        task.status = LearningTaskStatus.ERROR
        task.error_message = str(e)
        store.update(task)
        await log("error", "openai", f"学习分析失败: {str(e)}")


class SavePromptRequest(BaseModel):
    content: Optional[str] = None


@router.post("/api/learning/tasks/{task_id}/save-prompt")
async def save_learning_prompt(task_id: str, body: SavePromptRequest = None):
    """将学习分析生成的新提示词保存为版本化文件。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")

    content = (body.content if body else None) or task.new_prompt_content
    if not content:
        raise HTTPException(400, "没有可保存的提示词内容")

    task_type = task.task_type.value if task.task_type else "monologue_clean"
    filepath, version = save_versioned_prompt(task_type, content)

    task.new_prompt_path = filepath
    task.prompt_version = version
    store.update(task)

    return {
        "success": True,
        "path": filepath,
        "version": version,
        "filename": Path(filepath).name,
    }


@router.get("/api/learning/prompts/{scenario}/versions")
async def list_prompt_versions(scenario: str):
    """获取某场景的提示词版本列表。"""
    versions = get_prompt_versions(scenario)
    return {"versions": versions, "scenario": scenario}


# ============================================================
# 学习模块 手动模式（离线 AI）
# ============================================================

@router.get("/api/learning/tasks/{task_id}/manual-prompt/step1")
async def get_learning_manual_prompt_step1(task_id: str):
    """下载学习分析第一步的提示词包（system + user prompt）。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")
    if not task.original_asr_result or not task.edited_asr_result:
        raise HTTPException(400, "请先完成双轨 ASR")

    task_type = task.task_type.value if task.task_type else "monologue_clean"
    diff_result = diff_transcripts(task.original_asr_result, task.edited_asr_result)
    current_prompt = load_audit_system_prompt(task_type)

    system_prompt = load_learning_system_prompt()
    user_prompt = build_analysis_prompt(
        diff_result, current_prompt, task_type,
        task.original_asr_result, task.edited_asr_result,
    )

    combined = f"=== SYSTEM PROMPT ===\n{system_prompt}\n\n=== USER PROMPT ===\n{user_prompt}"
    return {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "combined": combined,
    }


class ManualStep1ResultRequest(BaseModel):
    analysis_result: str


@router.post("/api/learning/tasks/{task_id}/manual-result/step1")
async def submit_learning_manual_result_step1(task_id: str, req: ManualStep1ResultRequest):
    """提交第一步（分析报告）的 AI 输出结果。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")
    if not req.analysis_result.strip():
        raise HTTPException(400, "分析报告内容不能为空")

    task.analysis_result = req.analysis_result.strip()
    store.update(task)
    return {"success": True, "message": "第一步分析报告已保存"}


@router.get("/api/learning/tasks/{task_id}/manual-prompt/step2")
async def get_learning_manual_prompt_step2(task_id: str):
    """下载学习分析第二步的提示词包（基于已有分析报告生成改写提示词）。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")
    if not task.analysis_result:
        raise HTTPException(400, "请先完成第一步分析")

    task_type = task.task_type.value if task.task_type else "monologue_clean"
    current_prompt = load_audit_system_prompt(task_type)

    system_prompt = load_rewrite_system_prompt()
    user_prompt = build_rewrite_prompt(task.analysis_result, current_prompt, task_type)

    combined = f"=== SYSTEM PROMPT ===\n{system_prompt}\n\n=== USER PROMPT ===\n{user_prompt}"
    return {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "combined": combined,
    }


class ManualStep2ResultRequest(BaseModel):
    changes_json: str


@router.post("/api/learning/tasks/{task_id}/manual-result/step2")
async def submit_learning_manual_result_step2(task_id: str, req: ManualStep2ResultRequest):
    """提交第二步（规则变更 JSON）的 AI 输出结果，合并生成新提示词。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")
    if not req.changes_json.strip():
        raise HTTPException(400, "变更内容不能为空")

    task_type = task.task_type.value if task.task_type else "monologue_clean"
    current_prompt = load_audit_system_prompt(task_type)
    new_prompt = apply_prompt_changes(current_prompt, req.changes_json.strip())

    task.new_prompt_content = new_prompt
    task.status = LearningTaskStatus.DONE
    store.update(task)

    return {
        "success": True,
        "message": "新提示词已生成",
        "new_prompt_length": len(new_prompt),
        "original_prompt_length": len(current_prompt),
    }
