"""Gentle music ducking (setting music_duck, OFF by default): the bed dips while she speaks, comes back in her pauses.

Run: venv\\Scripts\\python tests\\test_music_duck.py
All local, synthetic sound (no samples needed): a "voice" of 1 kHz bursts (2 s on, 2 s off) and a steady 100 Hz "music" bed,
rendered through the REAL pipeline.render. The music is measured in the finished file by keeping only the low band.
Nothing is sent anywhere."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.update(ANTHROPIC_API_KEY="", OPENROUTER_API_KEY="")

from video_tool import pipeline, settings   # noqa: E402
from video_tool.config import FFMPEG, PEAK_LIMIT_DB, VOICE_LUFS   # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {detail}"))
    if not cond:
        FAILS.append(name)


tmp = Path(tempfile.mkdtemp(prefix="alex-duck-"))


def ffr(*args):
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True, cwd=tmp)


def band_rms_db(path, filt):
    """Per-100 ms RMS (dBFS) of one frequency band of the file's sound."""
    p = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-vn", "-af", filt, "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
                       capture_output=True, check=True)
    x = np.frombuffer(p.stdout, np.float32).astype(np.float64)
    n = len(x) // 4800
    w = x[: n * 4800].reshape(n, 4800)
    return 20 * np.log10(np.sqrt((w ** 2).mean(axis=1)) + 1e-9)


try:
    # --- settings: off by default, only a real yes turns it on, it is remembered like the other music choices
    check("ducking is OFF by default", settings.DEFAULTS["music_duck"] is False)
    check("the text 'false' does not turn ducking on", settings.clamp({**settings.DEFAULTS, "music_duck": "false"})["music_duck"] is False)
    check("a real yes turns it on", settings.clamp({**settings.DEFAULTS, "music_duck": True})["music_duck"] is True)
    check("ducking is remembered with the other music choices", "music_duck" in settings.REMEMBER_KEYS)

    # --- the graph: ducking sits before the peak guard and never touches the voice path
    g_on = pipeline.music_graph("[1:a]LEVEL", "[2:a]", True)
    g_off = pipeline.music_graph("[1:a]LEVEL", "[2:a]", False)
    check("no ducking filter when the setting is off", "sidechaincompress" not in g_off, g_off)
    check("ducking: the compressor runs on the music, keyed from the voice", "[2:a][key]sidechaincompress" in g_on, g_on)
    check("ducking comes before the peak guard", "sidechaincompress" in g_on and pipeline.PEAK_GUARD in g_on
          and g_on.index("sidechaincompress") < g_on.index(pipeline.PEAK_GUARD), g_on)
    check("ducking never turns the music up (makeup=1) and moves slowly (attack >= 100 ms, release >= 500 ms)",
          "makeup=1" in pipeline.DUCK and int(pipeline.DUCK.split("attack=")[1].split(":")[0]) >= 100
          and int(pipeline.DUCK.split("release=")[1].split(":")[0]) >= 500, pipeline.DUCK)

    # --- a real render, both ways
    (tmp / "c.ass").write_text("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\n"
                               "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
                               "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
                               "MarginR, MarginV, Encoding\nStyle: Default,Arial,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,"
                               "100,100,0,0,1,2,0,2,10,10,10,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
                               "MarginV, Effect, Text\n", encoding="utf-8")
    SECS = 16
    ffr("-f", "lavfi", "-i", f"color=c=gray:s=1080x1920:r=30:d={SECS}", "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "video.mp4")
    # "voice": 1 kHz, on for 2 s then off for 2 s (a tiny floor so the leveller has something in the pauses)
    ffr("-f", "lavfi", "-i", f"aevalsrc='0.3*sin(2*PI*1000*t)*lt(mod(t\\,4)\\,2)+0.0005*sin(2*PI*1000*t)':s=48000:d={SECS}",
        "-ac", "1", "voice.wav")
    # "music": steady 100 Hz at about -34 dBFS RMS (roughly where the real bed lands)
    ffr("-f", "lavfi", "-i", f"aevalsrc='0.028*sin(2*PI*100*t)':s=48000:d={SECS}", "-ac", "2", "bed.wav")
    pipeline.render(tmp, "video.mp4", "c.ass", "off.mp4", voice="voice.wav", music_wav="bed.wav", duck=False)
    pipeline.render(tmp, "video.mp4", "c.ass", "on.mp4", voice="voice.wav", music_wav="bed.wav", duck=True)
    settings.configure({"music_duck": False})
    pipeline.render(tmp, "video.mp4", "c.ass", "default.mp4", voice="voice.wav", music_wav="bed.wav")

    LOW = "lowpass=f=250,lowpass=f=250,lowpass=f=250"
    t = np.arange(SECS * 10) / 10 + 0.05

    def speech_pause(path):
        m = band_rms_db(path, LOW)[: len(t)]
        tt = t[: len(m)]
        # skip the first 300 ms of each half so the slow attack/release has settled
        speech = m[(np.mod(tt, 4) > 0.8) & (np.mod(tt, 4) < 2.0)]
        pause = m[(np.mod(tt, 4) > 3.0) & (np.mod(tt, 4) < 4.0)]
        return float(np.median(speech)), float(np.median(pause))

    s_off, p_off = speech_pause(tmp / "off.mp4")
    s_on, p_on = speech_pause(tmp / "on.mp4")
    print(f"     music level: off speech {s_off:.1f} / pause {p_off:.1f} dBFS; on speech {s_on:.1f} / pause {p_on:.1f} dBFS")
    check("without ducking the music is the same in speech and pauses (within 1 dB)", abs(s_off - p_off) <= 1.0, (s_off, p_off))
    check("with ducking the music dips 3 to 9 dB while she speaks (gentle, not gone)", 3.0 <= p_on - s_on <= 9.0, (s_on, p_on))
    check("with ducking the music in her pauses comes back to within 1.5 dB of no ducking", abs(p_on - p_off) <= 1.5, (p_on, p_off))
    sd, pd = speech_pause(tmp / "default.mp4")
    check("the default render (setting off) does not duck", abs(sd - pd) <= 1.0, (sd, pd))

    # the voice band is unchanged by ducking
    HIGH = "highpass=f=600,highpass=f=600,highpass=f=600"
    v_off, v_on = band_rms_db(tmp / "off.mp4", HIGH), band_rms_db(tmp / "on.mp4", HIGH)
    k = min(len(v_off), len(v_on))
    sp = (np.mod(t[:k], 4) > 0.5) & (np.mod(t[:k], 4) < 1.9)
    check("ducking does not change her voice (within 0.5 dB)", abs(np.median(v_off[:k][sp]) - np.median(v_on[:k][sp])) <= 0.5,
          (np.median(v_off[:k][sp]), np.median(v_on[:k][sp])))
    m = pipeline.measure_loudness(tmp / "on.mp4")
    check("with ducking the encoded true peak is still at most -1.0 dBFS", m and m["true_peak"] <= PEAK_LIMIT_DB, m)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED'}")
sys.exit(1 if FAILS else 0)
