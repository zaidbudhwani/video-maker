"""Horizontal YouTube output, real local renders (no AI key, nothing sent anywhere). Slow: a few minutes.

    venv\\Scripts\\python tests\\test_horizontal_render.py          (add "tour", "wide" or "square" to run one)

1. tour:   her house tour (11 upright phone clips + voice-over) as a 1920x1080 video: every clip shown whole with bars.
2. wide:   a 16:9 clip cut from her tips video (as in test_landscape_framing.py), talking to camera: fills the frame.
3. square: her square tips video itself, talking to camera: shown whole with bars.
Each finished file is checked for its size and frame rate, that the picture is not stretched (the width of the shown
picture matches the clip's own shape), that the sound is there, and the captions are drawn inside the frame in the lower
third (the real caption file is drawn on a blank frame with the same caption renderer). SKIPs without the samples."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = Path(tempfile.mkdtemp(prefix="alex-hrender-"))
os.environ.update(ALEX_DATA_DIR=str(DATA), ALEX_PREFS_FILE=str(DATA / "prefs.json"), ANTHROPIC_API_KEY="",
                  OPENROUTER_API_KEY="")

TOUR = ROOT / "samples" / "tour"
TIPS = ROOT / "samples" / "tips" / "Home Value.mp4"
if not (TOUR / "voice.m4a").exists() or not TIPS.exists():
    print("SKIP: needs samples/tour and samples/tips/Home Value.mp4")
    sys.exit(0)

from video_tool import ff, pipeline, writer  # noqa: E402
from video_tool.config import FFMPEG, OUT_DIR  # noqa: E402

assert str(OUT_DIR).startswith(str(DATA)), OUT_DIR   # never into the real out/ folder
assert not writer.has_key(), "this test must run with no AI key"
FAILS = []
W, H = 1920, 1080


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {detail}"), flush=True)
    if not cond:
        FAILS.append(name)


def gray(path, t):
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "gray", "-"], capture_output=True).stdout
    return raw if len(raw) >= W * H else None


def picture_columns(path, times):
    """(first, last) column that has picture in it (not a plain black bar), over several moments."""
    cols = [0] * W
    for t in times:
        raw = gray(path, t)
        if raw is None:
            continue
        for x in range(W):
            # mean of a few rows in the top half (above the captions); bars are black (16 in TV range)
            cols[x] = max(cols[x], max(raw[y * W + x] for y in range(60, H // 2, 37)))
    lit = [x for x in range(W) if cols[x] > 40]
    return (lit[0], lit[-1]) if lit else (None, None)


def caption_box(job, ass):
    """Draw the finished caption file on a blank 1920x1080 frame at each line's middle; (left, top, right, bottom) of
    every white pixel over all lines, or None when nothing was drawn."""
    lines = [l for l in (job / ass).read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue:")]
    box = None
    for l in lines[:: max(1, len(lines) // 8)]:
        f = l.split(",", 9)
        a, b = (sum(float(x) * m for x, m in zip(t.split(":"), (3600, 60, 1))) for t in (f[1], f[2]))
        raw = subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"color=black:s={W}x{H}:d={b + 1}",
                              "-vf", f"subtitles={ass}", "-ss", f"{(a + b) / 2:.2f}", "-frames:v", "1", "-f", "rawvideo",
                              "-pix_fmt", "gray", "-"], cwd=job, capture_output=True).stdout
        if len(raw) < W * H:
            continue
        pts = [(i % W, i // W) for i in range(0, W * H, 2) if raw[i] > 200]
        if not pts:
            continue
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        nb = (min(xs), min(ys), max(xs), max(ys))
        box = nb if box is None else (min(box[0], nb[0]), min(box[1], nb[1]), max(box[2], nb[2]), max(box[3], nb[3]))
    return box


def common(job, name, expect_whole_width=None):
    final = job / name
    info = ff.probe(final)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    check(f"{job.name}: 1920x1080", (v["width"], v["height"]) == (W, H), (v["width"], v["height"]))
    num, den = (int(x) for x in v["r_frame_rate"].split("/"))
    check(f"{job.name}: 30 fps", abs(num / den - 30) < 0.01, v["r_frame_rate"])
    loud = pipeline.measure_loudness(final)
    check(f"{job.name}: sound present (about -14 LUFS)", loud and -17 < loud["lufs"] < -11, loud)
    dur = info["duration"]
    first, last = picture_columns(final, [dur * k / 6 for k in range(1, 6)])
    print(f"   measured duration={dur:.3f}s loudness={loud} codecs={v['codec_name']} SAR={v.get('sample_aspect_ratio')} fps={v['r_frame_rate']}")
    print(f"   picture spans columns {first}-{last} of 1920")
    if expect_whole_width:
        shown = (last - first + 1) if first is not None else 0
        check(f"{job.name}: shown whole, not stretched (picture {shown} px wide, its own shape gives "
              f"{expect_whole_width} px)", abs(shown - expect_whole_width) <= 12, (first, last))
        check(f"{job.name}: bars on both sides, picture centred", first is not None and abs(first - (W - 1 - last)) <= 6,
              (first, last))
    else:
        check(f"{job.name}: fills the frame (no side bars)", first is not None and first <= 4 and last >= W - 5, (first, last))
    ass = next(iter(sorted(job.glob("captions*.ass"))))
    box = caption_box(job, ass.name)
    print(f"   captions drawn inside {box}")
    check(f"{job.name}: captions inside the frame with room at the edges",
          box is not None and box[0] >= 40 and box[2] <= W - 40 and box[3] <= H - 20, box)
    check(f"{job.name}: captions in the lower third", box is not None and box[1] >= H * 2 / 3 - 40, box)
    cp = json.loads((job / "copy.json").read_text(encoding="utf-8"))
    check(f"{job.name}: YouTube copy has no Shorts hashtag", "#shorts" not in json.dumps(cp).lower() and cp.get("orientation") == "horizontal")
    subprocess.run([FFMPEG, "-v", "error", "-ss", "3", "-i", str(final), "-frames:v", "1", str(job / "review.png")], check=True)
    return info, json.loads((job / "report.json").read_text(encoding="utf-8"))


def run(folder, job_id, over):
    cfg = DATA / f"{job_id}.json"
    cfg.write_text(json.dumps(over), encoding="utf-8")
    code = pipeline.main([str(folder), "--job-id", job_id, "--settings", str(cfg)])
    check(f"{job_id}: made without an AI key", code == 0)
    return OUT_DIR / job_id


which = [a for a in sys.argv[1:] if not a.startswith("-")] or ["tour", "wide", "square"]
try:
    if "tour" in which:
        print("tour: 11 upright clips + voice-over, horizontal")
        src = DATA / "tour"
        shutil.copytree(TOUR, src)
        job = run(src, "tour-h", {"orientation": "horizontal"})
        info, rep = common(job, "final.mp4", expect_whole_width=round(1080 * 1080 / 1920))
        check("tour-h: one video, as long as the voice-over", len(rep["parts"]) == 1 and abs(info["duration"] - rep["voice"]) < 0.5,
              (rep["parts"], rep.get("voice")))
        check("tour-h: report keeps the choice", rep["settings"]["orientation"] == "horizontal")
        kit = json.loads((job / "longform.json").read_text(encoding="utf-8"))
        check("tour-h: YouTube post written (titles, description)", kit.get("titles") and kit.get("description")
              and rep.get("longform", {}).get("file") == "longform.json", kit.keys())
        check("tour-h: no warning that clips were cut (they are shown whole)",
              not [w for w in rep["warnings"] if w.get("kind") == "wide"], rep["warnings"])

    if "wide" in which:
        print("wide: 16:9 talking clip cut from her tips video, horizontal")
        src = DATA / "wide"
        src.mkdir()
        subprocess.run([FFMPEG, "-v", "error", "-y", "-t", "40", "-i", str(TIPS), "-vf", "crop=1440:810:0:100",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", str(src / "01.mp4")], check=True)
        job = run(src, "wide-h", {"orientation": "horizontal"})
        info, rep = common(job, "final.mp4")
        check("wide-h: one video (no 60 s split)", len(rep["parts"]) == 1, rep["parts"])
        check("wide-h: framing reported", rep["framing"] and rep["framing"][0]["mode"] in ("full screen", "face-centred"),
              rep.get("framing"))

    if "square" in which:
        print("square: her square tips video (2.5 minutes), talking to camera, horizontal")
        src = DATA / "square"
        src.mkdir()
        shutil.copy(TIPS, src / "01.mp4")
        job = run(src, "square-h", {"orientation": "horizontal"})
        info, rep = common(job, "final.mp4", expect_whole_width=1080)
        check("square-h: over a minute and still ONE video", len(rep["parts"]) == 1 and info["duration"] > 60,
              (rep["parts"], info["duration"]))
        check("square-h: framing says whole frame", rep["framing"][0]["mode"] == "whole frame", rep.get("framing"))
finally:
    keep = os.environ.get("KEEP_RENDERS")
    if keep:
        print(f"renders kept in {OUT_DIR}")
    else:
        shutil.rmtree(DATA, ignore_errors=True)

print()
print(f"{'FAILED: ' + ', '.join(FAILS) if FAILS else 'ALL PASS'}")
sys.exit(1 if FAILS else 0)
