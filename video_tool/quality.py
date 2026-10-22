"""Quality warnings: shake, blur, darkness, audio clipping.

Warnings never stop a video. They tell Alex which clip to reshoot or check.
Video is analysed on the normalized clips (vertical, SDR) at low resolution.
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from . import ff
from .config import FFMPEG

W, H, RATE = 180, 320, 8          # analysis frame size and frames per second

SHAKE_PX = 4.0       # RMS jitter (pixels at 180 wide) above this = shaky; walking handheld is ~2-3.5
BLUR_MAX = 0.47      # blur score above this = blurry; her sharp clips are 0.20-0.39
DARK_LUMA = 55.0     # average brightness (0-255) below this = dark
CLIP_SAMPLES = 20    # this many samples at 0 dBFS = clipped audio


def frames(path, vf=f"fps={RATE},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}", w=W, h=H,
           start=None, length=None):
    """Small grey frames for analysis. start/length look at one stretch of a longer clip."""
    window = (["-ss", f"{start:.2f}"] if start else []) + (["-t", f"{length:.2f}"] if length else [])
    cmd = [FFMPEG, "-v", "error", *window, "-i", str(path), "-vf", vf + ",format=gray", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    n = len(raw) // (w * h)
    return np.frombuffer(raw[: n * w * h], np.uint8).reshape(n, h, w).astype(np.float32)


def detail_frames(path):
    """Blur needs real resolution: centre 400x400 of a 540-wide frame, 2 per second."""
    return frames(path, "fps=2,scale=540:960,crop=400:400", 400, 400)


def shifts(fr):
    """Frame-to-frame camera movement by phase correlation."""
    win = np.outer(np.hanning(H), np.hanning(W))
    F = np.fft.fft2(fr * win)
    out = []
    for a, b in zip(F[:-1], F[1:]):
        r = a * np.conj(b)
        r /= np.abs(r) + 1e-9
        c = np.abs(np.fft.ifft2(r))
        y, x = np.unravel_index(np.argmax(c), c.shape)
        out.append((x - W if x > W // 2 else x, y - H if y > H // 2 else y))
    return np.array(out, np.float32)


def shake_score(fr):
    """Jitter = movement minus its smoothed trend, so a steady pan or walk isn't shake."""
    if len(fr) < 8:
        return 0.0
    s = shifts(fr)
    k = np.ones(5) / 5
    trend = np.stack([np.convolve(s[:, i], k, mode="same") for i in (0, 1)], 1)
    return float(np.sqrt(np.mean(np.sum((s - trend) ** 2, 1))))


def blur_score(fr):
    """Crete et al. no-reference blur (0 = sharp, 1 = very blurry), median over frames.

    Re-blur each frame and see how much edge detail is lost: a sharp frame loses a lot,
    an already-blurry one loses little. Works across scenes, unlike raw sharpness.
    """
    k = np.ones(9, np.float32) / 9
    scores = []
    for f in fr[:: max(1, len(fr) // 12)]:
        out = []
        for axis in (0, 1):
            rb = np.apply_along_axis(lambda v: np.convolve(v, k, mode="same"), axis, f)
            d = np.abs(np.diff(f, axis=axis))
            dr = np.abs(np.diff(rb, axis=axis))
            v = np.maximum(0, d - dr)
            s = d.sum()
            out.append((s - v.sum()) / s if s else 1.0)
        scores.append(max(out))
    return float(np.median(scores))


def clipped_samples(path):
    p = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    peak, zero = None, 0
    for line in p.stderr.splitlines():
        if "max_volume" in line:
            peak = float(line.split(":")[1].replace("dB", ""))
        if "histogram_0db" in line:
            zero = int(line.split(":")[1])
    return peak, (zero if peak is not None and peak >= -0.1 else 0)


def analyze_clip(path, name):
    """Returns (metrics, issues). issues = [(kind, how far past the limit as a ratio, name)]."""
    fr = frames(path)
    if not len(fr):
        return {"clip": name}, []
    m = {"clip": name, "shake": round(shake_score(fr), 2), "blur": round(blur_score(detail_frames(path)), 3),
         "brightness": round(float(fr.mean()), 1)}
    issues = []
    if m["shake"] > SHAKE_PX:
        issues.append(("shake", m["shake"] / SHAKE_PX, name))
    if m["blur"] > BLUR_MAX:
        issues.append(("blur", m["blur"] / BLUR_MAX, name))
    if m["brightness"] < DARK_LUMA:
        issues.append(("dark", DARK_LUMA / max(m["brightness"], 1.0), name))
    return m, issues


MIN_USED = 2.0     # a clip on screen for no longer than this isn't worth a note
MAX_NOTES = 3      # the worst few only: a long list stops being read


def _list(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def group_warnings(issues):
    """The worst MAX_NOTES (clip, problem) pairs, merged into one note per kind of problem. Plain facts, no advice."""
    worst = sorted(issues, key=lambda i: -i[1])[:MAX_NOTES]
    out = []
    for kind in ("shake", "blur", "dark"):
        names = [n for k, _, n in worst if k == kind]
        if not names:
            continue
        one = len(names) == 1
        text = {"shake": f"{'looks' if one else 'look'} shaky in places",
                "blur": f"{'looks' if one else 'look'} a little soft or out of focus",
                "dark": f"{'is' if one else 'are'} on the dark side"}[kind]
        out.append({"clip": names[0], "clips": names, "kind": kind, "message": f"{_list(names)} {text}."})
    return out


def analyze(job, clip_files, names, audio=None, audio_name=None, used=None):
    """clip_files: normalized clips; audio: the sound that ends up in the video.

    used[i] = seconds clip i is on screen. Clips that barely show are measured but never mentioned.
    """
    metrics, issues, warnings = [], [], []
    for i, (f, n) in enumerate(zip(clip_files, names)):
        m, found = analyze_clip(f, n)
        metrics.append(m)
        if used is None or i >= len(used) or used[i] > MIN_USED:
            issues += found
    warnings += group_warnings(issues)
    if audio:
        peak, n = clipped_samples(audio)
        metrics.append({"audio": audio_name, "peak_db": peak, "clipped_samples": n})
        if n >= CLIP_SAMPLES:
            warnings.append({"clip": audio_name, "kind": "clipping",
                             "message": f"The sound in {audio_name} distorts in a few loud spots."})
    (Path(job) / "quality.json").write_text(json.dumps({"metrics": metrics, "warnings": warnings}, indent=1),
                                           encoding="utf-8")
    return warnings


if __name__ == "__main__":  # python -m video_tool.quality clip.mp4 [...]
    for p in sys.argv[1:]:
        print(analyze_clip(p, Path(p).name))
