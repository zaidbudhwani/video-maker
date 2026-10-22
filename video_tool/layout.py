"""The shape of the finished video: vertical (Shorts, Reels, TikTok; the default) or horizontal (a YouTube video).

Every size, length limit and caption position that depends on the shape is read from here, so the vertical video stays
exactly what it was (the same numbers from config.py) and the horizontal one changes in one place.

Horizontal (setting "orientation": "horizontal"): 1920x1080, 30 fps, up to 15 minutes. Landscape clips fill the frame.
Upright and square phone clips are shown WHOLE with plain black bars at the sides (never stretched), unless the setting
"tall_clips" is "fill": then they fill the screen and the top and bottom are cut off. No zooms, no moving crops, no effects.
"""
from . import settings
from .config import FPS, HEIGHT, MAX_SECONDS, SCALER, WIDTH

H_WIDTH, H_HEIGHT = 1920, 1080
H_MAX_SECONDS = 15 * 60 - 0.5      # a sensible ceiling for one YouTube video made here (render time, file size)
LANDSCAPE = 1.05                    # a clip this much wider than tall counts as landscape (fills the frame)

# Captions on a 16:9 frame: the vertical caption_size / caption_margin settings are mapped so her buttons
# ("bigger captions", "move captions up") keep working. YouTube has no buttons over the picture, so the captions sit low,
# in the lower third.
H_SIZE_SCALE = 0.75                 # 78 px on the tall frame -> 58 px on a 1080-high frame (about 5% of the height)
H_MARGIN = (0.07, 0.20)             # bottom margin as a share of 1080 for the lowest .. highest caption_margin
H_FLOOR = 0.05                      # a caption never sits lower than 5% from the bottom


def horizontal():
    return settings._current.get("orientation") == "horizontal"


def size():
    """(width, height) of the finished video."""
    return (H_WIDTH, H_HEIGHT) if horizontal() else (WIDTH, HEIGHT)


def max_seconds():
    return H_MAX_SECONDS if horizontal() else MAX_SECONDS


def limit_words():
    """How the length limit is said to her."""
    return "15 minutes" if horizontal() else "60 seconds"


def fill_filter(w=None, h=None):
    """Scale to cover the frame and cut the overflow from the middle (aspect kept, nothing stretched)."""
    W, H = (w, h) if w else size()
    return (f"scale={W}:{H}:force_original_aspect_ratio=increase:flags={SCALER},"
            f"crop={W}:{H},setsar=1,fps={FPS},format=yuv420p")


def fit_filter(w=None, h=None):
    """Scale to fit inside the frame and add plain black bars (aspect kept, nothing stretched)."""
    W, H = (w, h) if w else size()
    return (f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags={SCALER},"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={FPS},format=yuv420p")


def is_landscape(w, h):
    return w > h * LANDSCAPE


def shows_whole(w, h):
    """Horizontal video: is this clip shown whole with bars at the sides? (upright and square clips, by default)"""
    return horizontal() and not is_landscape(w, h) and settings._current.get("tall_clips") != "fill"


def shown_share(w, h):
    """Share of the clip's picture that is still on screen once it fills the frame (1.0 = all of it)."""
    W, H = size()
    out, clip = W / H, w / h
    return min(out / clip, clip / out)


def caption_geometry():
    """(font size, bottom margin in px, lowest allowed margin in px) on the finished frame."""
    size_v, margin = settings.get("caption_size"), settings.get("caption_margin")
    if not horizontal():
        return size_v, int(HEIGHT * margin), int(HEIGHT * 0.2)
    lo, hi = settings.LIMITS["caption_margin"]
    share = H_MARGIN[0] + (min(hi, max(lo, margin)) - lo) / (hi - lo) * (H_MARGIN[1] - H_MARGIN[0])
    return round(size_v * H_SIZE_SCALE), int(H_HEIGHT * share), int(H_HEIGHT * H_FLOOR)
