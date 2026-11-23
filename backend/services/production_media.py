"""Local media inspection and traceable PPTX import. No mock analysis."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from ..models.production import BrandFact, Cue, ProductionAsset, SourcePage


def parse_srt(srt: str) -> list[Cue]:
    def stamp(value):
        hours, minutes, seconds = value.strip().replace(",", ".").split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    cues = []
    for block in re.split(r"\n\s*\n", srt.strip().replace("\r", "")):
        lines = block.splitlines()
        for index, line in enumerate(lines):
            if "-->" in line:
                start, end = line.split("-->")
                cues.append(Cue(start=stamp(start), end=stamp(end), text="\n".join(lines[index + 1:]).strip()))
                break
    return cues


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(cmd: list[str], timeout=90):
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode:
        raise ValueError(f"媒体处理失败：{result.stderr[-1200:]}")
    return result.stdout


def probe(path: Path) -> dict:
    return json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]))


def inspect_asset(path: Path, filename: str, role: str, shot_id: str | None) -> ProductionAsset:
    payload = probe(path)
    videos = [s for s in payload["streams"] if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")]
    audios = [s for s in payload["streams"] if s["codec_type"] == "audio"]
    is_image = path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    if not videos and not audios:
        raise ValueError("文件中没有可读取的画面或音频")
    duration = float(payload.get("format", {}).get("duration") or 0)
    if not is_image and (duration <= 0 or duration > 3600):
        raise ValueError("素材时长须大于 0 且不超过 60 分钟")
    v = videos[0] if videos else {}
    asset = ProductionAsset(filename=filename, path=str(path), fingerprint=fingerprint(path),
                            kind="image" if is_image else "video" if videos else "audio", role=role,
                            shot_id=shot_id, duration=duration, width=v.get("width", 0), height=v.get("height", 0),
                            has_audio=bool(audios), usable_end=duration,
                            analysis_note="已读取时长、画幅与音轨；画面内容尚待检查")
    if videos:
        for i, time in enumerate([0] if is_image else [duration * .15, duration * .5, duration * .85]):
            frame = path.with_name(f"{path.stem}-frame-{i}.jpg")
            run(["ffmpeg", "-v", "error", "-y", "-ss", str(time), "-i", str(path), "-frames:v", "1",
                 "-vf", "scale=640:-2", str(frame)])
            asset.frames.append(str(frame))
    return asset


def import_brief(path: Path, filename: str) -> tuple[list[SourcePage], list[BrandFact], list[str]]:
    """Read page text + embedded visuals; use LibreOffice for faithful slide previews."""
    pages, warnings = [], []
    if path.suffix.lower() in {".txt", ".md"}:
        pages = [SourcePage(filename=filename, text=path.read_text(encoding="utf-8")[:100000])]
    elif path.suffix.lower() == ".pptx":
        ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
        with zipfile.ZipFile(path) as archive:
            if sum(i.file_size for i in archive.infolist()) > 200 * 1024 * 1024:
                raise ValueError("PPTX 解压后超过 200MB，请精简后上传")
            slides = sorted((n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+.xml", n)),
                            key=lambda n: int(re.search(r"slide(\d+)", n)[1]))
            if len(slides) > 80:
                raise ValueError("首版支持最多 80 页品牌 PPT")
            for number, name in enumerate(slides, 1):
                root = ET.fromstring(archive.read(name))
                text = "\n".join("".join(p.itertext()) for p in root.findall(".//a:t", ns))
                page = SourcePage(filename=filename, page=number, text=text)
                rel = f"ppt/slides/_rels/{Path(name).name}.rels"
                if rel in archive.namelist():
                    for link in ET.fromstring(archive.read(rel)):
                        target = link.attrib.get("Target", "")
                        if link.attrib.get("Type", "").endswith("/image") and target.startswith("../media/"):
                            media = "ppt/media/" + target.split("/")[-1]
                            if media in archive.namelist() and Path(media).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                                output = path.parent / f"{path.stem}-p{number}-{Path(media).name}"
                                output.write_bytes(archive.read(media))
                                page.images.append(str(output))
                pages.append(page)
        if not pages:
            raise ValueError("PPTX 中没有可读取的页面")
        if shutil.which("soffice") and shutil.which("pdftoppm"):
            try:
                with tempfile.TemporaryDirectory(prefix="goldenclip-office-") as profile:
                    run(["soffice", f"-env:UserInstallation={Path(profile).as_uri()}", "--headless", "--convert-to", "pdf",
                         "--outdir", str(path.parent), str(path)], timeout=120)
                pdf = path.with_suffix(".pdf")
                if not pdf.exists():
                    raise ValueError("未生成页面 PDF")
                prefix = str(path.with_suffix("")) + "-slide"
                run(["pdftoppm", "-jpeg", "-scale-to", "1200", str(pdf), prefix], timeout=120)
                images = sorted(path.parent.glob(f"{path.stem}-slide-*.jpg"),
                                key=lambda p: int(p.stem.rsplit("-", 1)[1]))
                for page, image in zip(pages, images):
                    page.preview = str(image)
            except (ValueError, subprocess.TimeoutExpired) as exc:
                warnings.append(f"页面预览生成失败，文字和原始图片已保存：{str(exc)[:160]}")
        else:
            warnings.append("未安装 LibreOffice / pdftoppm：已提取文字及页面原始图片，完整页面预览待补充")
    else:
        raise ValueError("品牌资料支持 .pptx、.txt、.md；旧版 .ppt 请先另存为 .pptx")
    facts = [BrandFact(text=line.strip(), source_id=p.id) for p in pages for line in p.text.splitlines() if line.strip()]
    return pages, facts, warnings
