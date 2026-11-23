from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.models.task import RenderProfile, Segment, SubtitleStyle
from backend.services import ffmpeg_executor as ffmpeg
from backend.services.subtitle_chunks import chunk_srt, parse_srt


def test_cues_are_clipped_and_shifted_at_chunk_boundary():
    cues = parse_srt("\ufeff1\r\n00:00:00,500 --> 00:00:03,000\r\n第一行\r\n第二行\r\n\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\n结尾\r\n")
    assert parse_srt(chunk_srt(cues, 0, 2)) == [(500, 2000, "第一行\n第二行")]
    assert parse_srt(chunk_srt(cues, 2, 4)) == [(0, 1000, "第一行\n第二行"), (1000, 2000, "结尾")]
    assert chunk_srt(cues, 5, 8) == ""


@pytest.mark.parametrize("content", ["bad srt", "1\n00:00:04,000 --> 00:00:03,000\nbad", "1\n00:99:00,000 --> 00:99:01,000\nbad"])
def test_invalid_srt_is_not_silently_dropped(content):
    with pytest.raises(ValueError):
        parse_srt(content)


@pytest.mark.asyncio
async def test_burn_once_incremental_subtitle_cache_and_atomic_failure(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"; source.write_bytes(b"source")
    srt = tmp_path / "captions.srt"
    output = tmp_path / "output.mp4"
    segments = [Segment(id="a", text="开头", start=0, end=2), Segment(id="d", text="删除", start=2, end=3, action="delete"), Segment(id="b", text="结尾", start=3, end=5)]
    monkeypatch.setattr(ffmpeg, "EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr(ffmpeg, "validate_rendered_media", lambda *args: SimpleNamespace(passed=True))
    monkeypatch.setattr(ffmpeg, "get_video_info", lambda *args: {"width": 640, "height": 360})
    calls = []
    local_subtitles = []
    fail = False
    def execute(cmd, **kwargs):
        calls.append(cmd)
        if fail:
            return False, "test failure"
        if "-vf" in cmd:
            vf = cmd[cmd.index("-vf") + 1]
            subtitle_path = vf.split("subtitles='")[1].split("'")[0]
            local_subtitles.append(parse_srt(Path(subtitle_path).read_text()))
        Path(cmd[-1]).write_bytes(b"rendered")
        return True, "ok"
    monkeypatch.setattr(ffmpeg, "execute_with_retry", execute)
    ffmpeg.generate_srt(segments, str(srt))
    assert await ffmpeg.export_ffmpeg_lossless(str(source), segments, str(output), burn_subtitles=True, srt_path=str(srt))
    assert len(calls) == 3
    assert calls[-1][calls[-1].index("-c") + 1] == "copy"
    assert local_subtitles == [[(0, 2000, "开头")], [(0, 2000, "结尾")]]
    # 相同字幕复用两块；只改第二块字幕仅重做该块。
    calls.clear(); local_subtitles.clear()
    assert await ffmpeg.export_ffmpeg_lossless(str(source), segments, str(output), burn_subtitles=True, srt_path=str(srt))
    assert len(calls) == 1 and not local_subtitles
    segments[-1].subtitle_text = "新的结尾"
    ffmpeg.generate_srt(segments, str(srt))
    calls.clear()
    assert await ffmpeg.export_ffmpeg_lossless(str(source), segments, str(output), burn_subtitles=True, srt_path=str(srt))
    assert len(calls) == 2 and local_subtitles == [[(0, 2000, "新的结尾")]]
    # 编码失败不能覆盖已存在的完整产物。
    output.write_bytes(b"original")
    fail = True
    assert not await ffmpeg.export_ffmpeg_lossless(str(source), segments, str(output), burn_subtitles=True, srt_path=str(srt), subtitle_style=SubtitleStyle(font_size=40))
    assert output.read_bytes() == b"original"
    assert not output.with_name("output.mp4.part").exists()


def test_styles_have_distinct_cache_keys(tmp_path):
    source = tmp_path / "video"; source.write_bytes(b"source")
    args = (str(source), 0, 2, RenderProfile.FINAL)
    plain = ffmpeg._chunk_cache_descriptor(*args)[0]
    first = ffmpeg._chunk_cache_descriptor(*args, subtitle_overlay={"srt": "text", "force_style": "white"})[0]
    second = ffmpeg._chunk_cache_descriptor(*args, subtitle_overlay={"srt": "text", "force_style": "yellow"})[0]
    assert len({plain, first, second}) == 3
