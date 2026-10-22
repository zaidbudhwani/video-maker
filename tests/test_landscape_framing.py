"""Framing of a horizontal talking-to-camera clip: auto keeps her face in a full-screen crop, fill centre-crops, fit shows bars.

    venv\\Scripts\\python tests\\test_landscape_framing.py

Builds a real 16:9 clip from her tips sample (cropped out of the square video, no rotation flag) and a face-less test
pattern, runs each framing setting through the real normalize step, and looks at what comes out. SKIPs without the
sample or the face model."""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from video_tool import facecrop, ff, own_audio, settings  # noqa: E402
from video_tool.config import FFMPEG  # noqa: E402

SAMPLE = ROOT / "samples" / "tips" / "Home Value.mp4"
if not SAMPLE.exists() or not facecrop.MODEL.exists():
    print("SKIP: needs samples/tips/Home Value.mp4 and assets/models/blaze_face_short_range.tflite")
    sys.exit(0)

failed = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  - ' + str(detail) if detail else ''}", flush=True)
    if not ok:
        failed.append(name)


def size(path):
    v = next(s for s in ff.probe(path)["streams"] if s.get("codec_type") == "video")
    return v["width"], v["height"]


def rows(path, ys, t=2):
    """Average brightness (0-255) of the given rows of the 1080x1920 frame at t seconds."""
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "gray", "-"], capture_output=True).stdout
    w, h = 1080, 1920
    return [sum(raw[y * w:(y + 1) * w]) / w for y in ys] if len(raw) >= w * h else None


def run_with(framing, clip, out):
    """normalize_av with the framing setting swapped in (settings.get reads _current), restored afterwards."""
    real = settings.get
    with mock.patch.object(settings, "get", lambda k: framing if k == "framing" else real(k)):
        return own_audio.normalize_av(clip, out, False)


tmp = Path(tempfile.mkdtemp(prefix="alex_framing_"))
try:
    wide = tmp / "wide.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-t", "8", "-i", str(SAMPLE), "-vf", "crop=1440:810:0:100",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", str(wide)], check=True)
    check("test clip is 1440x810 (16:9, no rotation)", size(wide) == (1440, 810), size(wide))

    print("auto")
    out = tmp / "auto.mp4"
    fr = run_with("auto", wide, out)
    check("output is exactly 1080x1920", size(out) == (1080, 1920), size(out))
    check("reports face-centred", fr["mode"] == "face-centred", fr)
    xs, n, _ = facecrop.face_centres(out, 1080, 1920)
    hits = [x for x in xs if x is not None]
    check("her face is inside the output frame", n > 0 and len(hits) / n >= 0.5 and all(0 < x < 1 for x in hits), f"{len(hits)}/{n}")

    print("fill")
    out = tmp / "fill.mp4"
    fr = run_with("fill", wide, out)
    check("output is exactly 1080x1920", size(out) == (1080, 1920), size(out))
    check("reports full screen", fr["mode"] == "full screen", fr)

    print("fit")
    out = tmp / "fit.mp4"
    fr = run_with("fit", wide, out)
    check("output is exactly 1080x1920", size(out) == (1080, 1920), size(out))
    check("reports whole frame, asked for", fr["mode"] == "whole frame" and fr["why"] == "asked for", fr)
    r = rows(out, [10, 1909, 960])
    check("black bars top and bottom, picture in the middle", r is not None and r[0] < 20 and r[1] < 20 and r[2] > 20, r)

    print("no face")
    plain = tmp / "plain.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=6",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "6", "-shortest", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(plain)], check=True)
    out = tmp / "plain_auto.mp4"
    fr = run_with("auto", plain, out)
    check("output is exactly 1080x1920", size(out) == (1080, 1920), size(out))
    check("auto falls back to whole frame", fr["mode"] == "whole frame", fr)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("ALL PASSED" if not failed else f"{len(failed)} FAILED: " + "; ".join(failed))
sys.exit(1 if failed else 0)
