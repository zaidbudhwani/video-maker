"""Finished-video checks: look at each finished video (read-only) and turn real problems into plain warnings.

    check(video, ass=None, words=None, spans=None, true_peak=None) -> [warning, ...]

What is checked, on the ENCODED file as a player shows it:
  - black picture: a stretch of near-black picture longer than BLACK_MIN_S
  - frozen picture: the picture stops moving while she is still talking (FROZEN_TALK_S), or holds still for a long time (HOLD_S)
  - picture/sound length drift: the picture and sound tracks differ in length by more than DRIFT_S
  - repeated footage: the same moving piece of footage shown twice (at least REPEAT_MIN_S long)
  - captions outside the safe frame: the burned-in caption text (rendered by the same caption engine, libass) reaches the
    edges of the picture, the top bar, or the bottom 20% where the platforms put their buttons and title
  - true peak: the level already measured after encoding (pipeline.enforce_peak) is still over the limit

Every warning says what is wrong, where (seconds, as m:ss) and the next step. A check that cannot run (an odd file, a
missing font) gives no warning: these checks must never stop a video or invent a problem.
Warning shape: {"clip": None|name, "kind": "final_check", "check": "<black|frozen|hold|drift|repeat|captions|peak>",
                "at": seconds, "message": "..."}
"""
import re
import subprocess
from pathlib import Path

import numpy as np

from . import ff
from .config import FFMPEG, HEIGHT, PEAK_LIMIT_DB, WIDTH

# Analysis frames: every frame (so a repeat lines up frame for frame), shrunk to 54x96 grey
TW, TH = 54, 96
REPEAT_MAX_FRAMES = 2400  # cap each square distance array at 23,040,000 bytes (float32)
BLACK_LUMA = 30          # 0-255; video black is 16, a dark room is usually well above 30 over most of the frame
BLACK_SHARE = 0.97       # this share of the (non-caption) picture under BLACK_LUMA = a black frame
BLACK_MIN_S = 0.5
FREEZE_DIFF = 0.04       # mean change between frames (0-255) below this = the very same picture (a held frame). Her
                         # steadiest real shots (a still recipe shot) still change 0.08-0.3 from frame to frame (sensor noise)
FROZEN_TALK_S = 1.0      # frozen this long while she talks (at least half of it with words)
HOLD_S = 4.0             # frozen this long at any time
DRIFT_S = 0.25
REPEAT_DIFF = 2.5        # two frames this close (RMS, 0-255) are the same footage
REPEAT_MOTION = 6.0      # ...and the run must move at least this much inside itself (a still wall matches anything)
REPEAT_MIN_S = 1.5
# Safe frame for caption text, as shares of the picture: inside the edges, under the top bar, above the bottom 20%
SAFE_X, SAFE_TOP, SAFE_BOTTOM = 0.03, 0.08, 0.81
CAP_SAMPLE_FPS = 4

NEXT_REMAKE = "tap Change something, then Remake video"


def clock(t):
    t = max(0.0, float(t))
    return f"{int(t // 60)}:{int(t % 60):02d}"


def span(t0, t1):
    """'from 0:10 to 0:12' or, when both round to the same second, 'at 0:10'."""
    a, b = clock(t0), clock(t1)
    return f"at {a}" if a == b else f"from {a} to {b}"


def _warn(check, at, message, clip=None):
    return {"clip": clip, "kind": "final_check", "check": check, "at": round(float(at), 2), "message": message}


def _run(cmd, cwd=None, timeout=600):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=timeout)


def frames(video):
    """(fps, array [n, TH, TW] float32) of the finished video's picture, every frame, grey."""
    info = ff.probe(video)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    num, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    p = _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(video), "-map", "0:v:0",
              "-vf", f"scale={TW}:{TH}:flags=area,format=gray", "-f", "rawvideo", "-"])
    data = np.frombuffer(p.stdout, dtype=np.uint8)
    n = len(data) // (TW * TH)
    return fps, data[: n * TW * TH].reshape(n, TH, TW).astype(np.float32)


def caption_boxes(ass, length):
    """Where the burned-in caption text actually lands: render the .ass alone (same libass engine as the final render)
    on black and find the white text. Returns (list of (time, x0, y0, x1, y1) as shares of the picture, row mask)."""
    ass = Path(ass)
    w, h = WIDTH // 4, HEIGHT // 4
    p = _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
              "-i", f"color=black:s={WIDTH}x{HEIGHT}:r={CAP_SAMPLE_FPS}:d={max(length, 0.5):.3f}",
              "-vf", f"subtitles={ass.name},scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"],
             cwd=str(ass.parent))
    data = np.frombuffer(p.stdout, dtype=np.uint8)
    n = len(data) // (w * h)
    if p.returncode != 0 or not n:
        return None, None
    img = data[: n * w * h].reshape(n, h, w)
    boxes, rows = [], np.zeros(h, bool)
    for i in range(n):
        on = img[i] > 100
        if not on.any():
            continue
        ys, xs = np.where(on)
        boxes.append((i / CAP_SAMPLE_FPS, xs.min() / w, ys.min() / h, (xs.max() + 1) / w, (ys.max() + 1) / h))
        rows[ys.min(): ys.max() + 1] = True
    return boxes, rows


def _runs(flags, min_len):
    """[(first, last_exclusive)] of True runs at least min_len long."""
    out, start = [], None
    for i, f in enumerate(list(flags) + [False]):
        if f and start is None:
            start = i
        elif not f and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    return out


def _clip_at(spans, t):
    for s, e, name in spans or ():
        if s <= t < e:
            return name
    return None


def _said(words, t0, t1):
    ws = [w for w in words or () if w["end"] > t0 and w["start"] < t1]
    talk = sum(min(w["end"], t1) - max(w["start"], t0) for w in ws)
    return ws, talk


def check_black(fps, fr, keep):
    dark = (fr[:, keep, :] < BLACK_LUMA).mean(axis=(1, 2)) >= BLACK_SHARE
    out = []
    for a, b in _runs(dark, max(2, int(round(BLACK_MIN_S * fps)) + 1)):
        out.append((a / fps, b / fps))
    return out


def check_frozen(fps, fr, keep):
    if len(fr) < 3:
        return []
    diff = np.abs(np.diff(fr[:, keep, :], axis=0)).mean(axis=(1, 2))
    still = diff < FREEZE_DIFF
    # a run of k still steps = k+1 identical frames
    return [(a / fps, (b + 1) / fps) for a, b in _runs(still, max(2, int(FROZEN_TALK_S * fps)))]


def check_repeat(fps, fr, keep):
    """Pairs (t_first, t_again, length) where the same moving footage is shown twice."""
    n = len(fr)
    L = int(round(REPEAT_MIN_S * fps))
    if n < 2 * L:
        return []
    step = max(1, (n + REPEAT_MAX_FRAMES - 1) // REPEAT_MAX_FRAMES)
    # shortcut: coarse sampling can miss brief repeats; use chunked comparisons if long inputs need full detail.
    f = fr[::step, keep, :].reshape(-1, int(keep.sum()) * TW)
    m = len(f)
    L = max(3, L // step)
    sq = (f * f).sum(1)
    d2 = (sq[:, None] + sq[None, :] - 2 * (f @ f.T)) / f.shape[1]
    d = np.sqrt(np.maximum(d2, 0))
    close = d < REPEAT_DIFF
    found = []
    for off in range(L, m):
        diag = np.diagonal(close, off)
        if diag.sum() < L:
            continue
        for a, b in _runs(diag, L):
            # the run must actually move: a still shot of the same wall is not repeated footage
            motion = max(d[a, a + k] for k in range(1, b - a))
            if motion < REPEAT_MOTION:
                continue
            found.append((a * step / fps, (a + off) * step / fps, (b - a) * step / fps))
    # one finding per repeated piece (neighbouring diagonals describe the same repeat)
    found.sort(key=lambda x: -x[2])
    kept = []
    for x in found:
        if not any(abs(x[0] - y[0]) < 1.0 and abs(x[1] - y[1]) < 1.0 for y in kept):
            kept.append(x)
    return sorted(kept)


def check_drift(video):
    info = ff.probe(video)
    lens = {}
    for s in info["streams"]:
        try:
            lens.setdefault(s["codec_type"], float(s["duration"]))
        except (KeyError, ValueError):
            pass
    if "video" in lens and "audio" in lens:
        return lens["video"] - lens["audio"]
    return 0.0


def check(video, ass=None, words=None, spans=None, true_peak=None):
    """All the finished-video checks for one finished video. words: her words on this video's timeline (to know when she
    is talking); spans: [(start, end, clip name)] which clip plays when (to say which clip); true_peak: dBTP if measured."""
    video = Path(video)
    out = []
    try:
        fps, fr = frames(video)
    except Exception:
        return out
    if not len(fr):
        return out
    length = len(fr) / fps
    keep = np.ones(TH, bool)
    boxes = None
    if ass and Path(ass).exists():
        try:
            boxes, rows = caption_boxes(ass, length)
        except Exception:
            boxes, rows = None, None
        if rows is not None and rows.any():
            # leave the caption rows (plus a margin) out of the picture checks: captions change while the picture is still
            idx = np.where(rows)[0]
            lo = max(0, int(idx.min() / len(rows) * TH) - 3)
            hi = min(TH, int((idx.max() + 1) / len(rows) * TH) + 3)
            keep[lo:hi] = False
            if keep.sum() < TH // 4:
                keep[:] = True

    for t0, t1 in check_black(fps, fr, keep):
        clip = _clip_at(spans, t0)
        where = f" during {clip}" if clip else ""
        out.append(_warn("black", t0,
                         f"The picture goes black {span(t0, t1)} ({t1 - t0:.1f} s){where}. Next step: film that part "
                         "again, then tap Make another video and choose the replacement clip when you upload.", clip))

    for t0, t1 in check_frozen(fps, fr, keep):
        ws, talk = _said(words, t0, t1)
        quote = " ".join(w["word"] for w in ws).strip()
        if len(quote) > 60:
            quote = quote[:57].rsplit(" ", 1)[0] + "..."
        at_end = t1 >= length - 0.2
        if t1 - t0 >= FROZEN_TALK_S and talk >= 0.5 * min(t1 - t0, FROZEN_TALK_S) and quote:
            where = "at the end" if at_end else span(t0, t1)
            out.append(_warn("frozen", t0,
                             f"The picture stops moving {where} ({t1 - t0:.1f} s) while you are still talking "
                             f"(\"{quote}\"). Next step: tap Make another video and upload a moving replacement clip, or a "
                             "shorter voice-over if you used one.", _clip_at(spans, t0)))
        elif t1 - t0 >= HOLD_S:
            out.append(_warn("hold", t0,
                             f"The picture holds completely still for {t1 - t0:.0f} s from {clock(t0)}. Next step: if that "
                             "wasn't on purpose, tap Make another video and choose a moving replacement clip when you upload.",
                             _clip_at(spans, t0)))

    for a, b, n in check_repeat(fps, fr, keep):
        ca, cb = _clip_at(spans, a), _clip_at(spans, b)
        which = f" ({ca} and {cb})" if ca and cb and ca != cb else ""
        out.append(_warn("repeat", b,
                         f"The same footage shows twice: at {clock(a)} and again at {clock(b)} ({n:.1f} s){which}. Next step: "
                         "check whether you uploaded the same footage twice. Tap Make another video and choose each clip only once "
                         "when you upload.", cb))

    try:
        drift = check_drift(video)
    except Exception:
        drift = 0.0
    if abs(drift) > DRIFT_S:
        longer = "picture" if drift > 0 else "sound"
        out.append(_warn("drift", length,
                         f"The {longer} runs {abs(drift):.1f} s longer than the {'sound' if longer == 'picture' else 'picture'}, "
                         f"so the end may look or sound cut off. Next step: {NEXT_REMAKE}; if it happens again, tell us in "
                         "'Anything else?'."))

    if boxes:
        bad = [b for b in boxes if b[1] < SAFE_X or b[3] > 1 - SAFE_X or b[2] < SAFE_TOP or b[4] > SAFE_BOTTOM]
        if bad:
            t = bad[0][0]
            edge = ("the bottom, where the app's buttons and title go" if bad[0][4] > SAFE_BOTTOM
                    else "the top of the screen" if bad[0][2] < SAFE_TOP else "the side of the picture")
            out.append(_warn("captions", t,
                             f"At {clock(t)} the captions reach {edge}, so some words may be hidden or cut off. Next step: "
                             "tap Change something > Captions > Smaller (or Move up / Move down), then Remake video."))

    if true_peak is not None and np.isfinite(true_peak) and true_peak > PEAK_LIMIT_DB + 0.05:
        out.append(_warn("peak", 0,
                         f"A few loud moments are still a little too loud ({true_peak:.1f} dB) and may crackle on some "
                         "phones. Next step: tap Change something > More options > Music > Quieter, or Remake video."))
    return out


def spans_from_timeline(timeline, names=None, offset=0.0):
    """[(start, end, her name for the clip)] from report.json's timeline, shifted onto one part's timeline."""
    out = []
    for t in timeline or ():
        try:
            out.append((t["start"] - offset, t["end"] - offset, (names or {}).get(t["clip"], t["clip"])))
        except (KeyError, TypeError):
            pass
    return out


def _main(argv):
    """Check finished videos by hand: python -m video_tool.final_checks <video.mp4> [captions.ass]"""
    import json
    if not argv:
        print(_main.__doc__)
        return 2
    print(json.dumps(check(argv[0], argv[1] if len(argv) > 1 else None), indent=1))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv[1:]))
