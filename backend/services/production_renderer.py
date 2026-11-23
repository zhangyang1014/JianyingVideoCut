"""Cancellable multi-source renderer and editable draft from one frozen timeline."""
from __future__ import annotations

import asyncio
import json
import shutil
import wave
import zipfile
from pathlib import Path

from ..models.production import EditTimeline, ProductionAsset
from .production_media import fingerprint, probe
from .production_store import atomic_json
from .render_quality_validator import validate_rendered_media


async def command(args: list[str], *, cwd: Path | None = None):
    process = await asyncio.create_subprocess_exec(*args, cwd=cwd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=1800)
    except BaseException:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        raise
    if process.returncode:
        raise RuntimeError(stderr.decode("utf-8", "replace")[-1600:])


def srt_time(value: float) -> str:
    ms = round(value * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def ass_time(value: float) -> str:
    cs = round(value * 100)
    return f"{cs // 360000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}"


def ass_text(text: str) -> str:
    # Treat user text as text, never ASS override commands.
    return text.replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\r", "").replace("\n", "\\N")


def write_subtitles(timeline: EditTimeline, directory: Path):
    (directory / "subtitles.srt").write_text("\n\n".join(
        f"{i}\n{srt_time(c.start)} --> {srt_time(c.end)}\n{c.text}" for i, c in enumerate(timeline.subtitles, 1)), encoding="utf-8")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {timeline.width}
PlayResY: {timeline.height}
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,PingFang SC,52,&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,0,0,0,0,100,100,0,0,1,3,1,2,80,80,220,1
Style: Brand,PingFang SC,38,&H0029B4F0,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,2,0,7,60,60,80,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = [f"Dialogue: 0,{ass_time(c.start)},{ass_time(c.end)},Default,,0,0,0,,{ass_text(c.text)}" for c in timeline.subtitles]
    if timeline.brand_label:
        events.append(f"Dialogue: 1,0:00:00.00,{ass_time(timeline.duration)},Brand,,0,0,0,,{ass_text(timeline.brand_label)}")
    (directory / "subtitles.ass").write_text(header + "\n".join(events), encoding="utf-8")


def build_draft(timeline: EditTimeline, directory: Path, video_files: list[Path], voice_files: list[Path], music: Path | None):
    import pyJianYingDraft as draft

    root = directory / "jianying"
    root.mkdir()
    script = draft.DraftFolder(str(root)).create_draft("GoldenClip", timeline.width, timeline.height, fps=timeline.fps)
    # pyJianYingDraft renamed these methods; support the installed release and 0.2.x.
    def add_track(kind, name):
        if hasattr(script, "add_track"):
            return script.add_track(kind, name)
        return script.append_track(draft.TrackSpec(kind, name))
    add_segment = script.add_segment
    def audio_material(path: Path, duration_us: int):
        material = draft.AudioMaterial(str(path))
        # The library rounds WAV metadata to milliseconds. Recover its true PCM
        # sample duration before validating a microsecond/frame-based source range.
        with wave.open(str(path), "rb") as wav:
            actual_us = round(wav.getnframes() / wav.getframerate() * 1e6)
        if actual_us + 1 < duration_us:
            raise ValueError("对白音频短于时间线，请重新生成")
        material.duration = actual_us
        return material
    project_dir = root / "GoldenClip"
    materials = project_dir / "materials"
    materials.mkdir()
    add_track(draft.TrackType.video, "画面")
    add_track(draft.TrackType.audio, "对白")
    add_track(draft.TrackType.text, "字幕")
    for i, clip in enumerate(timeline.clips):
        target = draft.Timerange(round(clip.start * 1e6), round(clip.duration * 1e6))
        src = draft.Timerange(0, round(clip.duration * 1e6))
        video = materials / f"{i:03}-video.mp4"
        voice = materials / f"{i:03}-voice.wav"
        shutil.copy2(video_files[i], video)
        shutil.copy2(voice_files[i], voice)
        add_segment(draft.VideoSegment(str(video), target, source_timerange=src, volume=0), "画面")
        add_segment(draft.AudioSegment(audio_material(voice, target.duration), target, source_timerange=src), "对白")
    for cue in timeline.subtitles:
        add_segment(draft.TextSegment(cue.text, draft.Timerange(round(cue.start * 1e6), round((cue.end - cue.start) * 1e6)),
                                             style=draft.TextStyle(size=8, align=1, auto_wrapping=True),
                                             clip_settings=draft.ClipSettings(transform_y=-.70), border=draft.TextBorder(width=20)), "字幕")
    if timeline.brand_label:
        add_track(draft.TrackType.text, "品牌")
        add_segment(draft.TextSegment(timeline.brand_label, draft.Timerange(0, round(timeline.duration * 1e6)),
                                             style=draft.TextStyle(size=6, color=(.94, .70, .16)),
                                             clip_settings=draft.ClipSettings(transform_x=-.25, transform_y=.88)), "品牌")
    if music:
        target = materials / "music.wav"
        shutil.copy2(music, target)
        add_track(draft.TrackType.audio, "音乐")
        add_segment(draft.AudioSegment(audio_material(target, round(timeline.duration * 1e6)), draft.Timerange(0, round(timeline.duration * 1e6)), volume=timeline.music_volume), "音乐")
    script.save()
    content = json.loads((project_dir / "draft_content.json").read_text())
    if len(content.get("tracks", [])) < 3:
        raise RuntimeError("剪映草稿缺少主要轨道")
    atomic_json(project_dir / "goldenclip_timeline.json", timeline.model_dump())
    (project_dir / "使用说明.txt").write_text(
        "草稿包含独立画面、对白、字幕、品牌和可选音乐轨道。画面已按最终画幅预处理，"
        "原始素材区间与提示词见 goldenclip_timeline.json。字幕位置和字体需在当前剪映版本中复核。\n"
        "ZIP 解压后请将 GoldenClip 文件夹放入剪映草稿目录；如素材离线，请重定位到 materials。\n"
        "结构校验通过不等于已在本机剪映打开验收。", encoding="utf-8")
    archive = directory / "jianying.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for path in project_dir.rglob("*"):
            if path.is_file():
                z.write(path, path.relative_to(root))
    return archive


async def render_production(timeline: EditTimeline, assets: list[ProductionAsset], directory: Path, progress):
    directory.mkdir(parents=True, exist_ok=True)
    asset_map = {a.id: a for a in assets}
    used = {c.asset_id for c in timeline.clips} | {c.audio_asset_id for c in timeline.clips if c.audio_asset_id}
    if timeline.music_asset_id:
        used.add(timeline.music_asset_id)
    for asset_id in used:
        asset = asset_map[asset_id]
        if not Path(asset.path).is_file() or await asyncio.to_thread(fingerprint, Path(asset.path)) != asset.fingerprint:
            raise ValueError(f"素材 {asset.filename} 已缺失或改变，请重新回传")
    video_files, voice_files, mux_files = [], [], []
    try:
        for i, clip in enumerate(timeline.clips):
            await progress(.05 + .65 * i / len(timeline.clips), f"处理 {clip.shot_id} 的画面与声音")
            video, voice, mux = [directory / f"{i:03}-{suffix}" for suffix in ["video.mp4", "voice.wav", "mux.mp4"]]
            fit = (f"scale={timeline.width}:{timeline.height}:force_original_aspect_ratio=increase,crop={timeline.width}:{timeline.height}"
                   if clip.framing == "cover" else
                   f"scale={timeline.width}:{timeline.height}:force_original_aspect_ratio=decrease,pad={timeline.width}:{timeline.height}:(ow-iw)/2:(oh-ih)/2")
            await command(["ffmpeg", "-v", "error", "-y", "-ss", str(clip.source_start), "-i", asset_map[clip.asset_id].path,
                           "-t", str(clip.duration), "-an", "-vf", fit + ",setsar=1", "-r", str(timeline.fps),
                           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(video)])
            if clip.audio_asset_id:
                args = ["-ss", str(clip.audio_source_start), "-i", asset_map[clip.audio_asset_id].path,
                        "-af", f"loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,apad=whole_len={round(clip.duration * 48000)},atrim=end_sample={round(clip.duration * 48000)},asetpts=N/SR/TB"]
            else:
                args = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            await command(["ffmpeg", "-v", "error", "-y", *args, "-t", str(clip.duration), "-vn", "-ar", "48000", "-ac", "2", str(voice)])
            await command(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(voice), "-c:v", "copy", "-c:a", "aac",
                           "-t", str(clip.duration), str(mux)])
            video_files.append(video)
            voice_files.append(voice)
            mux_files.append(mux)
        write_subtitles(timeline, directory)
        (directory / "concat.txt").write_text("\n".join(f"file '{p.name}'" for p in mux_files), encoding="utf-8")
        await progress(.73, "合成画面与音轨，另存 SRT 字幕")
        music = None
        if timeline.music_asset_id:
            music = directory / "music.wav"
            await command(["ffmpeg", "-v", "error", "-y", "-stream_loop", "-1", "-i", asset_map[timeline.music_asset_id].path,
                           "-t", str(timeline.duration), "-vn", "-ar", "48000", "-ac", "2", "-af", f"loudnorm=I=-18:TP=-2:LRA=11,aresample=48000,apad=whole_len={round(timeline.duration * 48000)},atrim=end_sample={round(timeline.duration * 48000)},asetpts=N/SR/TB", str(music)])
        args = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", "concat.txt"]
        if music:
            args += ["-i", "music.wav", "-filter_complex", f"[1:a]volume={timeline.music_volume}[music];[0:a][music]amix=inputs=2:normalize=0:duration=first[a]", "-map", "0:v", "-map", "[a]"]
        # Deliver clean picture so caption styling stays editable in SRT/the draft.
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac",
                 "-t", str(timeline.duration), "-movflags", "+faststart", "final.part.mp4"]
        await command(args, cwd=directory)
        await progress(.86, "检查成片时长、音视频流与完整解码")
        part = directory / "final.part.mp4"
        report = await asyncio.to_thread(validate_rendered_media, str(part), timeline.duration)
        if not report.passed:
            raise ValueError("；".join(issue.message for issue in report.issues))
        metadata = await asyncio.to_thread(probe, part)
        video_stream = next(s for s in metadata["streams"] if s["codec_type"] == "video")
        if (video_stream["width"], video_stream["height"]) != (timeline.width, timeline.height):
            raise ValueError("成片画幅与时间线不一致")
        await command(["ffmpeg", "-v", "error", "-xerror", "-i", str(part), "-f", "null", "-"])
        await progress(.94, "生成同一时间线的剪映多轨草稿")
        # The library call is short/local; completion precedes publishing any success state.
        archive = build_draft(timeline, directory, video_files, voice_files, music)
        atomic_json(directory / "timeline.json", timeline.model_dump())
        part.replace(directory / "final.mp4")
        checks = report.model_dump()
        checks.update(dimensions=True, full_decode=True, draft_structure=True, jianying_open_verified=False,
                      subtitles_burned=False, brand_overlay_burned=False)
        return {"mp4": str(directory / "final.mp4"), "srt": str(directory / "subtitles.srt"),
                "draft": str(archive), "timeline": str(directory / "timeline.json")}, checks
    finally:
        # Cancellation/failure never publishes partial output links.
        (directory / "final.part.mp4").unlink(missing_ok=True)
