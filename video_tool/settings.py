"""Adjustable look/feel, driven by Alex's feedback.

prefs.json (project root) holds what she chose to remember for all videos.
A remake can add one-off overrides on top. Everything else in config.py stays fixed.
"""
import json
import os
import re
from pathlib import Path

from .config import ROOT

PREFS_FILE = ROOT / "prefs.json"   # the single-user (local) copy


def prefs_path(path=None):
    """Whose prefs: an explicit path, else ALEX_PREFS_FILE (set per user by the website), else the local file."""
    return Path(path or os.getenv("ALEX_PREFS_FILE") or PREFS_FILE)

DEFAULTS = {
    "captions_on": True,
    "caption_size": 78,          # font size on a 1080x1920 frame
    "caption_margin": 0.36,      # distance of captions from the bottom, as a share of height (clear of the Shorts/Reels buttons and title)
    "caption_fixes": {},         # {"Alexe": "Alex"}: whole-word replacements
    "pause_gap": 0.7,            # pauses longer than this are cut
    "remove_fillers": True,      # cut "um", "uh"... using Whisper word timings
    "skip_dead_start": True,     # voice-over mode: skip a shaky/black start before each clip settles
    "match_clips": True,         # voice-over mode: Claude looks at frames and times each clip to her words
    "auto_order": True,          # move a clip to where she talks about it when Claude is confident
    "hints": "",                 # per-video: names, streets, numbers to listen for ("Ellis Park Dr, Shellsburg"); max 400 chars
    "format": "auto",            # auto | house_tour | recipe | tips | market_update | qa: shapes the titles and captions (video_tool/formats.py)
    "cut_retakes": True,         # talking mode: when she says a sentence twice in a row, keep the last take (the report lists each cut)
    "start_at_hook": False,      # talking mode: drop a greeting before the real first line ("Hi everybody, it's Alex") when asked to
    "highlights": 0,             # long video mode: 0 = off, 1-5 = how many Shorts to cut from ONE long talking video (over 75 s)
    "match_twice": False,        # opt-in: Claude scores the clips twice and the two are averaged (steadier, twice the cost)
    "match_engine": "claude",    # experiment, not in any web page: claude | qwen | qwen_video | clip (who scores the clip matching; clip = on this laptop)
    "stabilize": True,           # shaky clip: use a steadier part of the same clip
    "framing": "auto",           # auto (face-centred crop, else bars) | fill (centre crop) | fit (bars)
    "denoise": False,            # remove background noise (wind, fans, traffic); opt-in, her style is raw
    "music_on": True,
    "music_db": -22.0,
    "pins": [],                # per-video "Change this shot": [{"start": s, "end": e, "clip": n}] shows her clip n
                                 # (1 = her first clip) at that moment of the finished video. Never remembered.
    "music_track": "",           # "" = the tool picks (rotating per video); else the file name of an installed track (assets/music)
    "music_duck": False,         # opt-in: the music dips a few dB more while she speaks (not yet judged by ear: keep off)
    "orientation": "vertical",   # per-video: vertical (1080x1920 Short, under 60 s) | horizontal (1920x1080 YouTube video, up to 15 min)
    "tall_clips": "bars",        # per-video, horizontal only: bars (upright clips shown whole, plain bars at the sides) | fill (cuts top and bottom)
}

MAX_PINS = 30

ENGINES = ("claude", "qwen", "qwen_video")
EXPERIMENTAL = ("qwen", "qwen_video")   # other outside providers: NOT approved for her content (only Claude is)
LOCAL_ENGINES = ("clip",)      # on-laptop matcher (video_tool/localmatch.py): accepted, never the default
ORIENTATIONS = ("vertical", "horizontal")
TALL_CLIPS = ("bars", "fill")
FORMAT_KEYS = ("auto", "house_tour", "recipe", "tips", "market_update", "qa")

# Guard rails so a button can't break the brief's rules
LIMITS = {
    "caption_size": (54, 110),
    "caption_margin": (0.33, 0.60),   # never inside the bottom 20%, and clear of the platform title/button area
    "pause_gap": (0.4, 1.5),
    "music_db": (-32.0, -14.0),
}

_current = dict(DEFAULTS)


def clamp_highlights(value):
    """0 (off) to 5. Anything that is not a plain whole number (text, None, true/false) means off."""
    if isinstance(value, bool) or value is None:
        return 0
    try:
        n = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, min(5, n))


def other_ai_authorized():
    """May an outside AI provider other than Claude be used? Only for a separate, explicit run: the process must have
    ALEX_ALLOW_OTHER_AI=1 in its environment (a person running an approved comparison by hand does that). The website
    (ALEX_HOSTED=1) can never be authorized this way, whatever the environment or a request says."""
    return os.environ.get("ALEX_ALLOW_OTHER_AI") == "1" and os.environ.get("ALEX_HOSTED") != "1"


def clean_pins(value):
    """A tidy list of pins: whole clip numbers from 1, times in seconds (0 to 600) with end after start, at most
    MAX_PINS. Junk entries are dropped, never guessed at. When two pins overlap, the later one in the list wins."""
    out = []
    for p in value if isinstance(value, list) else []:
        if not isinstance(p, dict):
            continue
        try:
            vals = [p.get("start"), p.get("end"), p.get("clip")]
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or abs(v) == float("inf") for v in vals):
                continue
            start, end = (round(min(600.0, max(0.0, float(v))), 2) for v in vals[:2])
            clip = vals[2]
            if clip != int(clip) or not 1 <= clip <= 99 or end - start < 0.1:
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        new = {"start": start, "end": end, "clip": int(clip)}
        out = [q for q in out if q["end"] <= start or q["start"] >= end]   # the later choice for the same moment wins
        out.append(new)
    out = out[-MAX_PINS:]
    return sorted(out, key=lambda q: q["start"])


def merge_pins(old, new):
    """The pins of the version she is looking at, plus the ones she just chose (a new one replaces an old one at the
    same moment)."""
    return clean_pins(list(old if isinstance(old, list) else []) + list(new if isinstance(new, list) else []))


def clamp(s):
    for k, (lo, hi) in LIMITS.items():
        s[k] = min(hi, max(lo, float(s[k])))
    s["caption_size"] = int(s["caption_size"])
    s["denoise"] = s["denoise"] is True   # only a real yes: the text "false" must not turn it on
    s["match_twice"] = s["match_twice"] is True   # same: it costs money, so only a real yes
    s["start_at_hook"] = s["start_at_hook"] is True
    s["music_duck"] = s.get("music_duck") is True   # only a real yes
    s["highlights"] = clamp_highlights(s.get("highlights"))
    s["cut_retakes"] = s["cut_retakes"] is not False   # on unless she explicitly keeps both takes
    s["pins"] = clean_pins(s.get("pins"))
    s["hints"] = " ".join(str(s["hints"] or "").split())[:400]
    if s.get("match_engine") not in ENGINES + LOCAL_ENGINES or (s["match_engine"] in EXPERIMENTAL and not other_ai_authorized()):
        s["match_engine"] = "claude"
    track = s.get("music_track")
    # a bare file name only (never a path); whether it is installed is checked when the music is picked
    s["music_track"] = track if isinstance(track, str) and re.fullmatch(r"[\w][\w .()'&,-]{0,118}\.\w{2,4}", track) else ""
    if s["format"] not in FORMAT_KEYS:
        s["format"] = "auto"
    if s.get("orientation") not in ORIENTATIONS:
        s["orientation"] = "vertical"
    if s.get("tall_clips") not in TALL_CLIPS:
        s["tall_clips"] = "bars"
    if s["framing"] not in ("auto", "fill", "fit"):
        s["framing"] = "auto"
    return s


def load_prefs(path=None):
    try:
        return json.loads(prefs_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def configure(overrides=None):
    """Called once per run: defaults <- remembered prefs <- this run's overrides."""
    s = {**DEFAULTS, **load_prefs(), **(overrides or {})}
    # A vertical framing preference must not override this video's explicit horizontal upload choice.
    if (overrides or {}).get("orientation") == "horizontal" and "framing" not in overrides:
        s["framing"] = "auto"
    s["caption_fixes"] = {**load_prefs().get("caption_fixes", {}), **(overrides or {}).get("caption_fixes", {})}
    _current.clear()
    _current.update(clamp(s))
    return dict(_current)


def get(key):
    return _current[key]


# Choices about the look and feel that are worth keeping for every future video. Per-video things (what kind of
# video it is, the names to listen for, one-off switches) are never saved: they would leak into the next video.
REMEMBER_KEYS = ("captions_on", "caption_size", "caption_margin", "pause_gap", "remove_fillers", "skip_dead_start",
                 "stabilize", "framing", "denoise", "music_on", "music_db", "music_track", "music_duck")


def remember(changes, path=None):
    """Save changes so future videos use them. Only the look-and-feel keys, and only what she changed, so a later
    change to the built-in defaults still reaches her. Returns the keys that were saved."""
    prefs = load_prefs(path)
    fixes = {**prefs.get("caption_fixes", {}), **(changes.get("caption_fixes") or {})}
    kept = {k: v for k, v in changes.items() if k in REMEMBER_KEYS}
    checked = clamp({**DEFAULTS, **kept})          # same limits as a run, applied to what she changed
    prefs = {k: v for k, v in prefs.items() if k in REMEMBER_KEYS}   # also drops per-video keys saved by older versions
    prefs.update({k: checked[k] for k in kept})
    prefs["caption_fixes"] = fixes
    target = prefs_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    return sorted(kept)
