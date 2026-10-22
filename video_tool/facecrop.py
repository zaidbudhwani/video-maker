"""Face-centred crop for talking-to-camera clips (MediaPipe face detection).

One fixed crop per clip, centred on where her face sits on average. No moving
or zooming crop: that would break her "raw, no effects" style. If no face is
found, or she moves around more than a vertical frame can hold, the caller
falls back to showing the whole frame with black bars (no face found, or more than one person in the picture).
"""
import subprocess

import numpy as np

from .config import FFMPEG, HEIGHT, ROOT, SCALER, WIDTH

MODEL = ROOT / "assets" / "models" / "blaze_face_short_range.tflite"
SAMPLE_FPS = 2
ANALYSIS_W = 480


def display_size(info):
    v = next(s for s in info["streams"] if s.get("codec_type") == "video")
    w, h = v["width"], v["height"]
    rot = next((abs(int(d.get("rotation", 0))) for d in v.get("side_data_list", []) if "rotation" in d), 0)
    return (h, w) if rot in (90, 270) else (w, h)


def face_centres(path, w, h):
    """(x-centres, frames, crowded frames): the most confident face per sampled frame, and how many frames show two."""
    pts, n, crowded = face_points(path, w, h)
    return [None if p is None else p[0] for p in pts], n, crowded


def face_points(path, w, h):
    """(centres, frames, crowded frames): (x, y) centre (0-1) of the most confident face per sampled frame, or None."""
    from mediapipe.tasks.python import BaseOptions, vision
    import mediapipe as mp

    aw = ANALYSIS_W
    ah = int(round(h * aw / w / 2) * 2)
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-vf", f"fps={SAMPLE_FPS},scale={aw}:{ah}",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    n = len(raw) // (aw * ah * 3)
    frames = np.frombuffer(raw[: n * aw * ah * 3], np.uint8).reshape(n, ah, aw, 3)
    opts = vision.FaceDetectorOptions(base_options=BaseOptions(model_asset_path=str(MODEL)),
                                      min_detection_confidence=0.5)
    xs = []  # one entry per sampled frame: face (x, y) centre (0-1), or None
    crowded = 0   # frames with a second clear face (a crop around one person would cut the other off)
    with vision.FaceDetector.create_from_options(opts) as det:
        for f in frames:
            res = det.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(f)))
            if res.detections:
                best = max(res.detections, key=lambda d: d.categories[0].score)
                b = best.bounding_box
                xs.append(((b.origin_x + b.width / 2) / aw, (b.origin_y + b.height / 2) / ah))
                if sum(1 for d in res.detections if d.categories[0].score >= 0.7 and d.bounding_box.width >= aw * 0.06) >= 2:
                    crowded += 1
            else:
                xs.append(None)
    return xs, n, crowded


SHORT_GAP = 4.0   # seconds: a scene stretch this short, between two on-camera ones, keeps her crop
MIN_RUN = 3.0   # seconds: shorter stretches are merged into their neighbours (no jitter)


def runs(xs, fps):
    """Group samples into steady stretches: ('face', median x) or ('scene', None)."""
    out = []  # [kind, start_s, end_s, [xs]]
    for i, x in enumerate(xs):
        kind = "scene" if x is None else "face"
        if out and out[-1][0] == kind and (kind == "scene" or abs(x - np.median(out[-1][3])) < 0.12):
            out[-1][2] = (i + 1) / fps
            if x is not None:
                out[-1][3].append(x)
        else:
            out.append([kind, i / fps, (i + 1) / fps, [] if x is None else [x]])
    # fold short stretches into the previous one (or the next, at the very start)
    merged = []
    for r in out:
        if merged and (r[2] - r[1] < MIN_RUN or merged[-1][2] - merged[-1][1] < MIN_RUN):
            merged[-1][2] = r[2]
            merged[-1][3] += r[3]
            if merged[-1][3]:
                merged[-1][0] = "face"
        else:
            merged.append(r)
    return [(r[0] if r[3] else "scene", r[1], r[2], float(np.median(r[3])) if r[3] else None) for r in merged]


def plan_crop(path, info, size=(WIDTH, HEIGHT)):
    """ffmpeg filter for a full-screen crop (1080x1920, or the size given) that keeps her face in frame.

    While she's on camera the crop is centred on her face; while the camera shows
    the room or yard it's centred on the frame. The crop only moves between those
    stretches, never within one. Returns (filter or None to fall back, reason).
    """
    if not MODEL.exists():
        return None, "face model missing"
    WIDTH, HEIGHT = size   # noqa: N806 (the frame being filled)
    w, h = display_size(info)
    scaled_w = w * HEIGHT / h
    if scaled_w < WIDTH * (1.0 if size == (1080, 1920) else 1.02):   # no wider than the frame: normal fill works
        return None, "not wide"
    xs, n, crowded = face_centres(path, w, h)
    hits = sum(x is not None for x in xs)
    if not n or hits / n < 0.15:
        return None, f"no face found ({hits}/{n} frames)"
    if crowded / max(hits, 1) > 0.25:
        return None, "more than one person is in the picture"
    stretches = runs(xs, SAMPLE_FPS)

    def px(cx):
        return int(min(max(cx * scaled_w - WIDTH / 2, 0), scaled_w - WIDTH))

    # A short look away from her (the camera swings to the room and back) keeps the crop where she was,
    # rather than jumping to the middle and back again
    xs_px, last = [], None
    for k, (kind, t0, t1, cx) in enumerate(stretches):
        later_face = any(s[0] == "face" for s in stretches[k + 1:])
        if kind == "face":
            xs_px.append(px(cx))
            last = cx
        elif last is not None and later_face and t1 - t0 < SHORT_GAP:
            xs_px.append(px(last))
        else:
            xs_px.append(px(0.5))
    expr = str(xs_px[-1])
    for (_, _, end, _), x in reversed(list(zip(stretches[:-1], xs_px[:-1]))):
        expr = f"if(lt(t\\,{end:.2f})\\,{x}\\,{expr})"
    faces = sum(1 for s in stretches if s[0] == "face")
    return (f"scale=-2:{HEIGHT}:flags={SCALER},crop={WIDTH}:{HEIGHT}:{expr}:0",
            f"{faces} on-camera stretch(es), {len(stretches) - faces} showing the scene")


FACE_FROM_TOP = 0.40   # where her face sits in a horizontal frame cut from an upright clip (a little above the middle)


def plan_rows_crop(path, info, size):
    """Horizontal video, upright clip, "fill the screen": ONE fixed crop of whole rows (the top and bottom are cut) that
    keeps her face in frame. Never moves, never zooms in further than filling the width. Returns (filter, reason); with
    no face found it is a plain centre crop."""
    W, H = size
    w, h = display_size(info)
    scaled_h = h * W / w
    if scaled_h <= H:
        return None, "not tall"
    centre = None
    if MODEL.exists():
        pts, n, crowded = face_points(path, w, h)
        ys = sorted(p[1] for p in pts if p is not None)
        if n and len(ys) / n >= 0.15 and crowded / max(len(ys), 1) <= 0.25:
            centre = ys[len(ys) // 2]
    if centre is None:
        y, why = int((scaled_h - H) / 2), "no face found, centre of the picture"
    else:
        y, why = int(min(max(centre * scaled_h - H * FACE_FROM_TOP, 0), scaled_h - H)), "kept her face in frame"
    return f"scale={W}:-2:flags={SCALER},crop={W}:{H}:0:{y}", why
