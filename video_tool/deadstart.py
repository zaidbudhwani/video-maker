"""Skip the dead footage at the start of b-roll clips (camera motion + PySceneDetect + brightness).

Right after she presses record the phone is often still pointing at the ground,
swinging up to the subject, or showing a black frame. In voice-over mode every
clip plays from its first frame, so that junk would open every shot.

Conservative on purpose: a clip is only trimmed when it opens on junk (straight
away, or after a brief still moment on the ground) and then clearly settles, and
never by more than 3s or a quarter of the clip. Anything unclear keeps the clip
whole. Only the first few seconds are read, at low resolution.

What does the work on her footage: the camera-swing check (phase correlation, the
same measure quality.py uses for shake) and the near-black check. PySceneDetect's
ContentDetector only fires on a hard jump (a hand over the lens, a flash, a cut);
during her real tilt-ups its score stays at 18-24, under its cut threshold. It is an
optional install (requirements-nodeps.txt); without it, clips keep their start.

Voice-over mode only. In talking-to-camera mode the picture must stay in sync with
her voice, and the pause trim already drops footage from before she starts talking.

Blur is not used: per-frame blur follows what's in the shot (a plain wall reads as
"blurry", 0.55 -> 0.79 while she walks toward one), so it would skip good footage.
The motion blur of a swing is caught by the motion checks anyway.
"""
import subprocess
import sys
from pathlib import Path

import numpy as np

from .config import FFMPEG, TONEMAP

try:  # installed on its own with --no-deps (its opencv-python would break mediapipe's cv2)
    import cv2
    from scenedetect import FrameTimecode
    from scenedetect.detectors import ContentDetector
    from scenedetect.stats_manager import StatsManager
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

W, H, RATE = 180, 320, 15.0   # analysis frame (vertical, as it will be shown) and frames per second
WINDOW = 4.0                  # seconds read from the start of each clip
MAX_SKIP, MAX_SHARE = 3.0, 0.25
MIN_SKIP = 0.2                # a frame or two of wobble isn't worth a cut
SETTLE = 0.5                  # the picture must stay steady this long to count as settled
LEAD_IN = 0.6                 # she may hold still on the ground this long before swinging up...
TRAVEL_PX = 60.0              # ...but then the swing must really move the view (a third of the frame)

CUT_SCORE = 27.0   # ContentDetector's own cut threshold: the frame changed as much as a hard cut
SWING_PX = 12.0    # camera moved this far (px at 180 wide) in 1/15s = fast swing; her walk-throughs peak at 11
CALM_PX = 8.0      # steady below this; her walking and slow pans are 1-7
DARK_LUMA = 40.0   # near-black frame (0-255)...
DARK_SHARE = 0.6   # ...that's also much darker than the rest of the opening


def opening_frames(clip, hdr):
    """First WINDOW seconds as small BGR frames, tone-mapped like the final video. None if unreadable."""
    vf = (f"fps={RATE:g},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          + (TONEMAP if hdr else "") + "format=bgr24")
    p = subprocess.run([FFMPEG, "-v", "error", "-t", f"{WINDOW}", "-i", str(clip), "-vf", vf,
                        "-f", "rawvideo", "-"], capture_output=True)
    if p.returncode != 0:
        return None  # normalizing the clip will report a real problem loudly
    n = len(p.stdout) // (W * H * 3)
    return np.frombuffer(p.stdout[: n * W * H * 3], np.uint8).reshape(n, H, W, 3)


def signals(frames):
    """Per frame: brightness, PySceneDetect content change, camera shift (px) from the previous frame."""
    det, stats = ContentDetector(threshold=CUT_SCORE), StatsManager()
    det.stats_manager = stats
    win = cv2.createHanningWindow((W, H), cv2.CV_32F)
    luma, change, shift, prev = [], [], [], None
    for i, f in enumerate(frames):
        tc = FrameTimecode(i, RATE)
        det.process_frame(tc, f)
        change.append(stats.get_metrics(tc, [ContentDetector.FRAME_SCORE_KEY])[0] or 0.0)
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
        (dx, dy), _ = cv2.phaseCorrelate(prev, g, win) if prev is not None else ((0.0, 0.0), 1.0)
        shift.append(float(np.hypot(dx, dy)))
        luma.append(float(g.mean()))
        prev = g
    change, shift = np.array(change), np.array(shift)
    if len(frames) > 1:  # frame 0 has nothing before it: it shares the first movement
        change[0], shift[0] = change[1], shift[1]
    return np.array(luma), change, shift


def classify(luma, change, shift):
    """junk = swinging or near-black; calm = not junk and the camera is steady."""
    moving = (change >= CUT_SCORE) | (shift >= SWING_PX)
    dark = (luma < DARK_LUMA) & (luma < DARK_SHARE * np.median(luma[len(luma) // 2:]))
    junk = moving | dark
    # motion measured on a near-black frame is just sensor noise, so it isn't called a swing
    return junk, moving & ~dark, dark, ~junk & (shift < CALM_PX)


def find_start(clip, duration, hdr):
    """Seconds of dead footage to skip at the start of the clip (0.0 when unsure), and why."""
    if not AVAILABLE:
        return 0.0, "PySceneDetect not installed"
    frames = opening_frames(clip, hdr)
    if frames is None:
        return 0.0, "couldn't read the start of the clip"
    need = int(SETTLE * RATE)
    if len(frames) < 2 * need:
        return 0.0, "too short to check"
    luma, change, shift = signals(frames)
    junk, moving, dark, calm = classify(luma, change, shift)
    first = int(np.argmax(junk)) if junk.any() else len(junk)
    if first > LEAD_IN * RATE:
        return 0.0, "starts on the subject"
    start = next((i for i in range(first + 1, len(frames) - need + 1) if calm[i:i + need].all()), None)
    if start is None:
        return 0.0, "never settles in the first seconds, kept whole"
    # motion is measured into each frame, so calm[start] means frame start-1 already
    # looks like the settled picture: keep that frame too, unless it's a black one
    if not dark[start - 1]:
        start -= 1
    t = start / RATE
    if junk[first:start].mean() < 0.5:
        return 0.0, "opening only partly shaky, kept whole"
    # a still moment before the swing is only dead footage if the swing went somewhere
    # else; a small jolt out of a steady shot of the subject is kept
    if first and shift[first:start][~dark[first:start]].sum() < TRAVEL_PX:
        return 0.0, "steady opening with a small jolt, kept whole"
    if t < MIN_SKIP:
        return 0.0, "only a frame or two of wobble"
    if t > min(MAX_SKIP, MAX_SHARE * duration):
        return 0.0, f"unsure: the shaky start runs {t:.1f}s, too long to cut"
    # name what most of the skipped part was (the one-frame jump out of a black start isn't a swing)
    why = [w for w, m in (("dark frames", dark), ("camera swinging", moving)) if m[first:start].mean() >= 0.25]
    return round(t, 2), " and ".join(why)


if __name__ == "__main__":  # python -m video_tool.deadstart clip.mp4 [...]
    from .assemble import check_clip
    for p in sys.argv[1:]:
        d, h = check_clip(Path(p))
        print(Path(p).name, *find_start(p, d, h))
