r"""The music bed sits UNDER her voice: the voice is the main thing, the music is background.

Run: venv\Scripts\python tests\test_music_mix.py
Uses the real tracks in assets/music/ and her voice-over sample (samples/recipe/voice.m4a), all local. SKIPs when either is
missing. Measures loudness (LUFS), the way a player does, not peaks. Nothing is sent anywhere."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.update(ANTHROPIC_API_KEY="", OPENROUTER_API_KEY="")

from video_tool import music, settings   # noqa: E402
from video_tool.config import AUDIO_EXT, FFMPEG, MUSIC_DIR, MUSIC_LUFS_OFFSET, VOICE_LUFS   # noqa: E402

VOICE = ROOT / "samples" / "recipe" / "voice.m4a"
tracks = sorted(p for p in MUSIC_DIR.glob("*") if p.suffix.lower() in AUDIO_EXT)
if not tracks or not VOICE.exists():
    print("SKIP: needs at least one track in assets/music/ and samples/recipe/voice.m4a")
    sys.exit(0)

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {detail}"))
    if not cond:
        FAILS.append(name)


def ff(*args):
    subprocess.run([FFMPEG, "-v", "error", "-y", *map(str, args)], check=True)


def lufs(path):
    p = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return float(re.search(r"I:\s+(-?[\d.]+)\s+LUFS", p.stderr[p.stderr.rfind("Summary:"):]).group(1))


tmp = Path(tempfile.mkdtemp(prefix="alex-musicmix-"))
try:
    SECS = 24
    voice = tmp / "voice.wav"
    ff("-i", VOICE, "-t", SECS, "-af", f"loudnorm=I={VOICE_LUFS}:TP=-2:LRA=11,aresample=48000", "-ac", "2", voice)
    v = lufs(voice)
    check("her voice is at about -14 LUFS to start with (one quick pass on a short excerpt, so allow 2.5)", abs(v - VOICE_LUFS) <= 2.5, v)

    for i, tr in enumerate(tracks, 1):
        out = music.music_bed(tr, SECS, tmp, name=f"bed{i}.wav")
        b = lufs(out)
        want = settings.get("music_db") + MUSIC_LUFS_OFFSET
        check(f"{tr.name}: the bed lands at {want:g} LUFS (within 1.5)", abs(b - want) <= 1.5, b)
        check(f"{tr.name}: the bed is at least 18 LU under her voice", v - b >= 18.0, (v, b))
        mix = tmp / f"mix{i}.wav"
        ff("-i", voice, "-i", out, "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:normalize=0[a]", "-map", "[a]", mix)
        m = lufs(mix)
        check(f"{tr.name}: adding it changes the overall loudness by under 0.3 LU (the voice stays the main thing)",
              abs(m - v) <= 0.3, (v, m))

    # the loudest setting a person can pick is still clearly under the voice
    loud_db = settings.clamp({**settings.DEFAULTS, "music_db": 99})["music_db"]
    check("the loudest allowed music setting is still at least 10 LU under the voice", VOICE_LUFS - (loud_db + MUSIC_LUFS_OFFSET) >= 10.0,
          (loud_db, VOICE_LUFS - (loud_db + MUSIC_LUFS_OFFSET)))
    check("the default keeps the bed at 20 LU under the voice", abs((VOICE_LUFS - (settings.DEFAULTS['music_db'] + MUSIC_LUFS_OFFSET)) - 20.0) < 0.01)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED'}")
sys.exit(1 if FAILS else 0)
