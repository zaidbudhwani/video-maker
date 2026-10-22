"""Picture and sound quality guards for the video engine (no AI, no network, synthetic media only).

    venv\\Scripts\\python tests\\test_render_quality.py

1. Peak limit: a finished video never peaks above -1.0 dBFS (true peak) AFTER AAC encoding, measured on the encoded file,
   and the loudness stays within 1 LU of the -14 LUFS target.
2. Cuts do not click: a 5 ms fade where two pieces of sound meet (voice-over pause trim and talking-mode jump cuts).
3. Resizing uses lanczos (sharper than ffmpeg's default bicubic when a clip is enlarged), measured as edge energy.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from video_tool import assemble, facecrop, ff, own_audio, pipeline, settings, silence  # noqa: E402
from video_tool.config import FFMPEG, PEAK_LIMIT_DB, SCALER, TRUE_PEAK_TARGET, VOICE_LUFS  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not cond:
        fails.append(name)


settings._current.clear()
settings._current.update(settings.clamp(dict(settings.DEFAULTS)))   # built-in defaults, never this computer's prefs

tmp = Path(tempfile.mkdtemp(prefix="alex_rq_"))
ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ffr(*args):
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True, cwd=tmp)


def pcm(path, start=0.0, dur=None):
    a = ["-ss", str(start)] if start else []
    t = ["-t", str(dur)] if dur else []
    p = subprocess.run([FFMPEG, "-v", "error", *a, "-i", str(path), *t, "-vn", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(p.stdout, np.float32).astype(np.float64)


try:
    # ---------------------------------------------------------------- 1. true peak after encoding
    (tmp / "c.ass").write_text(ASS, encoding="utf-8")
    ffr("-f", "lavfi", "-i", "color=c=gray:s=1080x1920:r=30:d=8", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt",
        "yuv420p", "video.mp4")
    # Hard-driven voice stand-in: a loud tone with sharp square-wave bursts, peaking at 0 dBFS (the case that clipped)
    ffr("-f", "lavfi", "-i", "aevalsrc='0.35*sin(2*PI*220*t)+0.6*sgn(sin(2*PI*3*t))*gt(sin(2*PI*0.5*t)\\,0.6)*sin(2*PI*1800*t)':s=48000:d=8",
        "-af", "volume=0dB,alimiter=limit=1:level=0", "-ac", "1", "loud.wav")
    # A music bed at full level mixed on top of the levelled voice: the old chain sent this over 0 dBFS
    ffr("-f", "lavfi", "-i", "aevalsrc='0.9*sgn(sin(2*PI*110*t))':s=48000:d=8", "-ac", "2", "bed.wav")
    out = pipeline.render(tmp, "video.mp4", "c.ass", "final.mp4", voice="loud.wav", music_wav="bed.wav")
    m = pipeline.measure_loudness(tmp / "final.mp4")
    check("render: encoded true peak is at most -1.0 dBFS", m and m["true_peak"] <= PEAK_LIMIT_DB, m)
    check("render: returns the measured peak of the encoded file", out is not None and abs(out - m["true_peak"]) < 0.05, out)
    seen = []                       # the filter graph the real render hands to ffmpeg
    real_run = pipeline.ff.run
    pipeline.ff.run = lambda args, *a, **k: (seen.append(list(args)), real_run(args, *a, **k))[1]
    try:
        pipeline.render(tmp, "video.mp4", "c.ass", "voice_only.mp4", voice="loud.wav")
    finally:
        pipeline.ff.run = real_run
    graphs = [g[g.index("-filter_complex") + 1] for g in seen if "-filter_complex" in g]
    check("render puts the peak guard in the filter graph (not only the after-the-fact check)",
          any(pipeline.PEAK_GUARD in g for g in graphs), graphs[:1])
    v = pipeline.measure_loudness(tmp / "voice_only.mp4")
    check("render (voice only): peak under the limit and loudness within 1 LU of the target",
          v and v["true_peak"] <= PEAK_LIMIT_DB and abs(v["lufs"] - VOICE_LUFS) <= 1.0, v)
    check("leveller aims under the limit, leaving room for AAC overshoot", TRUE_PEAK_TARGET <= PEAK_LIMIT_DB - 0.9)
    check("peak guard never turns the sound up (level=0), keeps sync (latency=1) and sees between samples (4x rate)",
          "level=0" in pipeline.PEAK_GUARD and "latency=1" in pipeline.PEAK_GUARD
          and pipeline.PEAK_GUARD.startswith("aresample=192000,") and pipeline.PEAK_GUARD.endswith("aresample=48000"))

    # enforce_peak alone: an already-encoded file that overshoots is brought under the limit, picture untouched
    ffr("-i", "video.mp4", "-f", "lavfi", "-i", "aevalsrc='0.999*sgn(sin(2*PI*150*t))':s=48000:d=8", "-map", "0:v",
        "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "hot.mp4")
    before = pipeline.measure_loudness(tmp / "hot.mp4")["true_peak"]
    vbefore = ff.probe(tmp / "hot.mp4")["streams"][0]
    after = pipeline.enforce_peak(tmp, "hot.mp4")
    vafter = ff.probe(tmp / "hot.mp4")["streams"][0]
    check("enforce_peak: hot file starts over the limit (test is real)", before > PEAK_LIMIT_DB, before)
    check("enforce_peak: brought to at most -1.0 dBFS", after is not None and after <= PEAK_LIMIT_DB, after)
    check("enforce_peak: picture copied, not re-encoded", vbefore.get("bit_rate") == vafter.get("bit_rate")
          and vbefore["nb_frames"] == vafter["nb_frames"])
    ok = pipeline.enforce_peak(tmp, "final.mp4")
    check("enforce_peak: a file already under the limit is left as it is", ok is not None and abs(ok - m["true_peak"]) < 0.05)

    # ---------------------------------------------------------------- 2. no click at cuts
    # A tone that never stops: every cut lands mid-wave, the worst case for a click
    ffr("-f", "lavfi", "-i", "sine=f=330:r=48000:d=6", "-af", "volume=-6dB", "tone.wav")
    words = [{"word": "one", "start": 0.3, "end": 0.9}, {"word": "two", "start": 2.37, "end": 2.9},
             {"word": "three", "start": 4.13, "end": 4.8}]   # gaps over 0.7 s: two cuts, at odd sample phases
    wav, _, length = silence.trim_silence(tmp / "tone.wav", words, tmp)
    segs = [tuple(s) for s in silence.keep_segments(words, 6.0)]
    x = pcm(wav)
    steady = np.abs(np.diff(x[2000:4000])).max()     # largest sample step in the uncut tone
    edges = np.cumsum([b - a for a, b in segs])[:-1]
    jumps = [np.abs(np.diff(x[int(e * 48000) - 48:int(e * 48000) + 48])).max() for e in edges]
    check("voice-over trim makes cuts (test is real)", len(edges) == 2, segs)
    check("voice-over trim: no step at a cut bigger than the tone's own", max(jumps) <= steady * 1.05,
          f"steady {steady:.4f}, at cuts {[round(j, 4) for j in jumps]}")
    check("join_fades: nothing at the very start and end", silence.join_fades(0, 0, 1, 1) == "")
    check("join_fades: 5 ms each side in the middle", "afade=t=in:st=0:d=0.005" in silence.join_fades(1, 2, 3, 3)
          and "afade=t=out:st=0.9950:d=0.005" in silence.join_fades(1, 2, 3, 3))

    ffr("-f", "lavfi", "-i", "color=c=gray:s=1080x1920:r=30:d=6", "-i", "tone.wav", "-c:v", "libx264", "-preset",
        "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "joined.mp4")
    own_audio.cut_pauses(tmp, segs)
    y = pcm(tmp / "trimmed.mp4")
    steady2 = np.abs(np.diff(y[24000:48000])).max()
    jumps2 = [np.abs(np.diff(y[int(e * 48000) - 96:int(e * 48000) + 96])).max() for e in edges]
    check("talking-mode jump cuts: no step at a cut bigger than the tone's own", max(jumps2) <= steady2 * 1.1,
          f"steady {steady2:.4f}, at cuts {[round(j, 4) for j in jumps2]}")
    check("talking-mode jump cuts keep picture and sound the same length",
          abs(ff.probe(tmp / "trimmed.mp4")["duration"] - sum(b - a for a, b in segs)) < 0.1)

    # ---------------------------------------------------------------- 3. sharper resizing
    check("every resize step uses lanczos", "lanczos" in SCALER and f"flags={SCALER}" in assemble.FRAME
          and f"flags={SCALER}" in own_audio.FIT)
    src = facecrop.__file__   # the face-crop filter text is built inline; check the source carries the flags
    check("face-centred crop resize uses lanczos", "scale=-2:{HEIGHT}:flags={SCALER}" in Path(src).read_text(encoding="utf-8"))
    # Round trip: a detailed 1080x1920 picture made smaller (as a 1080p or square phone clip is), then enlarged back by the
    # real normalize step. The closer it comes back to the original (PSNR, dB), the more detail the resize kept.
    ffr("-f", "lavfi", "-i", "mandelbrot=s=1080x1920", "-frames:v", "1", "-c:v", "ffv1", "-pix_fmt", "yuv420p", "ref.mkv")
    ffr("-i", "ref.mkv", "-vf", "scale=810:1440:flags=area", "-c:v", "ffv1", "small.mkv")
    lossless = ["-c:v", "ffv1"]
    ffr("-i", "small.mkv", "-vf", assemble.FRAME, "-an", *lossless, "big_new.mkv")
    ffr("-i", "small.mkv", "-vf", assemble.FRAME.replace(f":flags={SCALER}", ""), "-an", *lossless, "big_old.mkv")

    def psnr(path):
        p = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path), "-i", "ref.mkv", "-lavfi", "psnr", "-f", "null", "-"],
                           capture_output=True, text=True, cwd=tmp)
        return float(p.stderr.rsplit("average:", 1)[1].split()[0])

    p_new, p_old = psnr(tmp / "big_new.mkv"), psnr(tmp / "big_old.mkv")
    check("enlarged picture keeps more detail than the default resize", p_new > p_old + 0.1,
          f"PSNR {p_old:.2f} -> {p_new:.2f} dB")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{'ALL PASS' if not fails else str(len(fails)) + ' FAILED'}")
sys.exit(1 if fails else 0)
