"""The post pack: one zip with everything she needs to post by hand.

final video(s), captions.srt (same lines as the burned-in captions), cover.jpg (a plain frame, no text added),
alt-text.txt and post-text.txt (every platform caption with its title ideas). Long video mode adds long.srt and
youtube-long-video.txt (titles, description with chapters, tags) for the whole long video. Nothing is posted anywhere.
Source clips, transcripts, settings and file paths are never put in the zip.
"""
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import numpy as np

from . import captions, ff, longform, quality
from .errors import AlexError
from .writer import PLATFORMS

COVER_WINDOW = 3.0      # the cover frame comes from the first 3 seconds
COVER_FPS = 6
CW, CH = 216, 384       # small frames are enough to judge sharpness and light


def _load(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def video_files(job_dir):
    """The finished video file names, in order: final.mp4 or final_part1.mp4, final_part2.mp4..."""
    job_dir = Path(job_dir)
    names = [o["file"] for o in (_load(job_dir / "report.json").get("parts") or []) if (job_dir / o.get("file", "")).is_file()]
    if not names:
        one = job_dir / "final.mp4"
        names = ["final.mp4"] if one.is_file() else sorted(p.name for p in job_dir.glob("final_part*.mp4"))
    return names


def frame_score(grey):
    """Sharpness (variance of the Laplacian) times a light factor: 1.0 for a normal exposure, falling off when dark or blown out."""
    f = grey.astype(np.float32)
    lap = 4 * f[1:-1, 1:-1] - f[:-2, 1:-1] - f[2:, 1:-1] - f[1:-1, :-2] - f[1:-1, 2:]
    sharp = float(lap.var())
    luma = float(f.mean())
    if luma < 70:
        light = max(0.05, luma / 70)
    elif luma > 190:
        light = max(0.05, (255 - luma) / 65)
    else:
        light = 1.0
    return sharp * light


def pick_cover_time(video):
    """Seconds into the video (within the first 3) of the sharpest, best-lit frame."""
    fr = quality.frames(video, vf=f"fps={COVER_FPS},scale={CW}:{CH}:force_original_aspect_ratio=increase,crop={CW}:{CH}",
                        w=CW, h=CH, length=COVER_WINDOW)
    if len(fr) == 0:
        return 0.0
    scores = [frame_score(f) for f in fr]
    return round(int(np.argmax(scores)) / COVER_FPS, 3)


def cover_source(job_dir, name):
    """Prefer the picture without captions burned in (video.mp4, same length) so the cover has no text on it; otherwise
    the finished video itself."""
    final = Path(job_dir) / name
    plain = Path(job_dir) / "video.mp4"
    if name == "final.mp4" and plain.is_file():
        try:
            if abs(ff.probe(plain)["duration"] - ff.probe(final)["duration"]) < 0.5:
                return plain
        except AlexError:
            pass
    return final


def make_cover(video, dest):
    """Save the chosen frame as a plain JPEG. No text, no graphics."""
    t = pick_cover_time(video)
    ff.run(["-ss", f"{t:.3f}", "-i", video, "-frames:v", "1", "-q:v", "2", dest], what="Saving the cover picture")
    return t


# ---------------------------------------------------------------- text files
def _clean(text):
    """A sentence of copy without hashtags and links."""
    text = re.sub(r"#\w+", "", text or "")
    return " ".join(text.split())


def alt_text(copy, orientation="vertical"):
    """One or two plain sentences describing the video for people who cannot see it."""
    copy = copy or {}
    desc = copy.get("description") or ""
    disc = copy.get("disclosure") or ""
    if disc:
        desc = desc.replace(disc, "")
    sents = [s for s in re.split(r"(?<=[.!?])\s+", _clean(desc)) if s]
    first = next((s for s in sents if not s.endswith("?")), sents[0] if sents else "")
    if len(first) > 220:
        cut = first[:219]
        first = (cut[:cut.rfind(" ")] if " " in cut else cut).rstrip(" ,;:") + "..."
    label = (copy.get("format_label") or ("YouTube" if orientation == "horizontal" else "Short")).lower()
    tail = f"A {orientation} {label} video from Alex Morgan of Example Realty, with captions on the screen."
    return (first + " " + tail).strip() if first else tail


def post_text(copy, files):
    """Every platform caption ready to paste, with the title ideas. One section per part for a split video."""
    copy = copy or {}
    out = ["YOUR POST TEXT", "Copy a caption, paste it into the app, and post it yourself.", ""]
    parts = copy.get("parts") or []
    sections = []
    if len(files) > 1 and len(parts) == len(files):
        sections = [(p.get("label") or f"Part {i} of {len(files)}", p) for i, p in enumerate(parts, 1)]
    else:
        sections = [("", copy)]
    for label, c in sections:
        if label:
            out += ["=" * 40, label.upper(), "=" * 40, ""]
        titles = c.get("titles") or []
        if titles:
            out += ["TITLE IDEAS (pick one)"] + [f"{i}. {t}" for i, t in enumerate(titles, 1)] + [""]
        if c.get("hook"):
            out += ["OPENING LINE", c["hook"], ""]
        caps = c.get("captions") or {}
        for key, name in PLATFORMS.items():
            if caps.get(key):
                name = "YouTube" if c.get("orientation") == "horizontal" and key == "youtube_shorts" else name
                out += [name.upper(), "-" * len(name), caps[key], ""]
    lines = copy.get("checklist") or []
    if lines:
        out += ["BEFORE YOU POST"] + [f"[ ] {l}" for l in lines] + [""]
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------- the zip
def build(job_dir, dest_path):
    """Make the post pack zip at dest_path and return dest_path."""
    job_dir, dest_path = Path(job_dir), Path(dest_path)
    files = video_files(job_dir)
    if not files:
        raise AlexError("There is no finished video to put in the pack yet.")
    copy = _load(job_dir / "copy.json")
    many = len(files) > 1
    work = Path(tempfile.mkdtemp(prefix="postpack_"))
    try:
        extras = []   # (name in zip, path on disk)
        for n, name in enumerate(files, 1):
            ass = job_dir / name.replace("final", "captions", 1).replace(".mp4", ".ass")   # final_part2.mp4 -> captions_part2.ass
            if ass.is_file():
                lines = captions.read_ass_lines(ass)
                if lines:
                    srt_name = "captions.srt" if not many else f"captions_part{n}.srt"
                    captions.write_srt(lines, work / srt_name)
                    extras.append((srt_name, work / srt_name))
            cover_name = "cover.jpg" if n == 1 else f"cover_part{n}.jpg"
            try:
                make_cover(cover_source(job_dir, name), work / cover_name)
                extras.append((cover_name, work / cover_name))
            except AlexError:
                pass   # a missing cover must not stop the pack
        (work / "alt-text.txt").write_text(alt_text(copy, (_load(job_dir / "report.json").get("settings") or {}).get("orientation", "vertical")) + "\n", encoding="utf-8")
        (work / "post-text.txt").write_text(post_text(copy, files), encoding="utf-8")
        extras += [("alt-text.txt", work / "alt-text.txt"), ("post-text.txt", work / "post-text.txt")]

        lf = _load(job_dir / "report.json").get("longform")
        if isinstance(lf, dict):   # long video mode: the captions and the YouTube post for the whole video go in too
            kit = _load(job_dir / str(lf.get("file") or ""))
            srt = job_dir / str(lf.get("srt") or "")
            if kit:
                (work / "youtube-long-video.txt").write_text(longform.kit_text(kit), encoding="utf-8")
                extras.append(("youtube-long-video.txt", work / "youtube-long-video.txt"))
            if srt.is_file():
                extras.append(("long.srt", srt))

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest_path.with_name(dest_path.name + ".part")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
            for name in files:
                z.write(job_dir / name, name)   # video is already compressed
            for name, path in extras:
                z.write(path, name)
        tmp.replace(dest_path)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return dest_path
