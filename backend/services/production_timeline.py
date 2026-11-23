"""Deterministic, source-bounded timeline compiler shared by MP4 and drafts."""
import hashlib
import json
import math

from ..models.production import Cue, EditTimeline, ProductionProject, StoryboardShot, TimelineClip


def signature(shot: StoryboardShot) -> str:
    canonical = StoryboardShot.model_validate(shot.model_dump())
    fields = canonical.model_dump(exclude={"verified_signature", "review_note", "prompt", "filming_guide"})
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def shot_issues(project: ProductionProject, shot: StoryboardShot, *, verification=True) -> list[str]:
    issues = []
    assets = {a.id: a for a in project.assets}
    video = assets.get(shot.selection.asset_id) if shot.selection else None
    if video is None or video.kind != "video":
        return [f"{shot.shot_id} 待补充视频素材"]
    for label, selection, kind in [("画面", shot.selection, "video"), ("旁白", shot.voiceover, "audio")]:
        if selection:
            asset = assets.get(selection.asset_id)
            if asset is None or (kind == "audio" and not asset.has_audio):
                issues.append(f"{shot.shot_id} {label}素材不存在或无音轨")
            elif selection.end > asset.duration + .001:
                issues.append(f"{shot.shot_id} {label}出点超出素材实际时长 {asset.duration:.2f}s")
    if shot.voice_mode in {"original", "generated"} and not video.has_audio:
        issues.append(f"{shot.shot_id} 需要对白，但视频没有音轨")
    if shot.voice_mode == "voiceover":
        if not shot.voiceover:
            issues.append(f"{shot.shot_id} 待补充独立旁白")
        elif shot.voiceover.end - shot.voiceover.start > shot.selection.end - shot.selection.start + .001:
            issues.append(f"{shot.shot_id} 画面短于旁白，请补拍或扩大可用区间")
    if verification and shot.verified_signature != signature(shot):
        issues.append(f"{shot.shot_id} 素材内容待检查，或文案/选段已变更")
    if shot.voice_mode != "silent":
        audio = assets.get(shot.voiceover.asset_id) if shot.voice_mode == "voiceover" and shot.voiceover else video
        selection = shot.voiceover if shot.voice_mode == "voiceover" else shot.selection
        if audio and selection and not any(c.start < selection.end and c.end > selection.start for c in audio.cues):
            issues.append(f"{shot.shot_id} 缺少实际音频字幕，请分析语音或导入对应 SRT")
    return issues


def compile_timeline(project: ProductionProject) -> EditTimeline:
    if not project.shots:
        raise ValueError("请先选择创意并生成分镜")
    issues = [issue for shot in project.shots if shot.required or shot.selection for issue in shot_issues(project, shot)]
    if issues:
        raise ValueError("；".join(issues))
    assets = {a.id: a for a in project.assets}
    clips, subtitles, cursor = [], [], 0.0
    for shot in project.shots:
        if not shot.selection:
            continue
        selection = shot.selection
        voice = shot.voiceover if shot.voice_mode == "voiceover" else selection
        audio = assets[voice.asset_id] if shot.voice_mode != "silent" else None
        intervals = [(voice.start, voice.end)]
        if audio and shot.trim_pauses:
            cues = sorted((c for c in audio.cues if c.start < voice.end and c.end > voice.start), key=lambda c: c.start)
            cuts = [(max(voice.start, a.end + .15), min(voice.end, b.start - .15))
                    for a, b in zip(cues, cues[1:]) if b.start - a.end > .8]
            begin, intervals = voice.start, []
            for cut_start, cut_end in cuts:
                if cut_start > begin:
                    intervals.append((begin, cut_start))
                begin = max(begin, cut_end)
            if voice.end > begin:
                intervals.append((begin, voice.end))
        for audio_start, audio_end in intervals:
            duration = audio_end - audio_start
            source_start = selection.start + audio_start - voice.start
            cursor = _append_clip(shot, selection.asset_id, source_start, audio, audio_start, duration, cursor, clips, subtitles)
    if not clips:
        raise ValueError("没有可用镜头")
    if project.project_type == "digital_presenter":
        if cursor < 1:
            raise ValueError("实际成片须至少 1 秒，请检查选段")
    else:
        if cursor < 1 or cursor > 180:
            raise ValueError("实际成片须在 1–180 秒之间，请检查选段")
        if project.selected_direction_id and not 29.5 <= cursor <= 60.5:
            action = "补充镜头或扩大选段" if cursor < 30 else "缩短选段或收紧停顿"
            raise ValueError(f"实际素材编排后为 {cursor:.1f} 秒，未达到首版 30–60 秒成片范围，请{action}")
    if project.music_asset_id:
        music = assets.get(project.music_asset_id)
        if not music or not music.has_audio:
            raise ValueError("背景音乐不存在或没有音轨")
    used = {c.asset_id for c in clips} | {c.audio_asset_id for c in clips if c.audio_asset_id}
    if project.music_asset_id:
        used.add(project.music_asset_id)
    sources = {key: assets[key].model_dump(include={"id", "filename", "fingerprint", "version", "kind", "duration"}) for key in used}
    return EditTimeline(duration=round(cursor, 6), clips=clips, sources=sources, subtitles=subtitles,
                        brand_label=project.brand.name, music_asset_id=project.music_asset_id)


def _append_clip(shot, asset_id, source_start, audio, audio_start, duration, cursor, clips, subtitles):
    # CFR video, PCM audio and captions share frame-exact boundaries; never read past a selected out-point.
    duration = math.floor((duration + 1e-8) * 30) / 30
    if duration <= 0:
        raise ValueError(f"{shot.shot_id} 可用区间短于一帧")
    clips.append(TimelineClip(shot_id=shot.shot_id, shot_revision=shot.revision,
                              asset_id=asset_id, source_start=source_start,
                              source_end=source_start + duration, start=round(cursor, 6), duration=round(duration, 6),
                              framing=shot.framing, audio_asset_id=audio.id if audio else None,
                              audio_source_start=audio_start, spoken_text=shot.spoken_text, prompt=shot.prompt))
    if audio:
        # Source-audio cues are clipped and shifted, never estimated from planned duration.
        for cue in audio.cues:
            start, end = max(cue.start, audio_start), min(cue.end, audio_start + duration)
            if end > start:
                subtitles.append(Cue(start=round(cursor + start - audio_start, 6),
                                     end=round(cursor + end - audio_start, 6), text=cue.text))
    elif shot.subtitle.strip():
        subtitles.append(Cue(start=cursor, end=cursor + duration, text=shot.subtitle.strip()))
    return cursor + duration
