"""Measure the optional music ducking on her real voice-over (samples/recipe/voice.m4a) with every installed track.

Run: venv\\Scripts\\python tests\\measure_ducking.py
Not a pass/fail suite (run_all.py skips it): it prints numbers. All local, nothing is sent anywhere.

For each track and each way (ducking off / on) it:
  1. renders a real video through pipeline.render (plain grey picture, her voice, the real music bed), and reports the
     finished file's loudness (LUFS) and true peak (after encoding);
  2. runs the SAME voice leveller + ducking filter with the music bed alone as the output, and reports the music level
     (median of 100 ms RMS, dBFS) while she speaks vs in her pauses (speech/pause defined relative to her median level, see below).
Whether ducking SOUNDS better is not measured here: a person must do a blind listening comparison."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.update(ANTHROPIC_API_KEY="", OPENROUTER_API_KEY="")

from video_tool import ff, music, pipeline, settings   # noqa: E402
from video_tool.config import AUDIO_EXT, AUDIO_RATE, FFMPEG, MUSIC_DIR   # noqa: E402

VOICES = [ROOT / "samples" / n / "voice.m4a" for n in ("recipe", "tour")]
VOICES = [v for v in VOICES if v.exists()]
tracks = sorted(p for p in MUSIC_DIR.glob("*") if p.suffix.lower() in AUDIO_EXT)
if not tracks or not VOICES:
    print("SKIP: needs a track in assets/music/ and samples/recipe/voice.m4a")
    sys.exit(0)

ASS = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, "
       "PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
       "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Arial,60,&H00FFFFFF,"
       "&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n\n[Events]\nFormat: Layer, Start, End, Style, "
       "Name, MarginL, MarginR, MarginV, Effect, Text\n")


def rms100(path):
    p = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
                       capture_output=True, check=True)
    x = np.frombuffer(p.stdout, np.float32).astype(np.float64)
    n = len(x) // 4800
    return 20 * np.log10(np.sqrt((x[: n * 4800].reshape(n, 4800) ** 2).mean(axis=1)) + 1e-9)


def main():
    for v in VOICES:
        measure(v)


def measure(VOICE):
    tmp = ROOT / "out" / "ducking-measurements" / VOICE.parent.name
    tmp.mkdir(parents=True, exist_ok=True)
    inputs = [VOICE, *tracks, *[ROOT / "video_tool" / n for n in ("pipeline.py", "music.py", "settings.py", "config.py")]]
    fingerprint = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    record = tmp / "measurements.json"
    if record.exists():
        saved = json.loads(record.read_text(encoding="utf-8"))
        if all(saved["inputs"].get(k) == v for k, v in fingerprint.items()):
            print("REUSE current measurements: " + str(record))
            print(json.dumps(saved["rows"], indent=2))
            return
    rows = []
    try:
        settings.configure({})
        length = float(ff.probe(VOICE)["format"]["duration"])
        shutil.copy(VOICE, tmp / "voice.m4a")
        (tmp / "c.ass").write_text(ASS, encoding="utf-8")
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:s=1080x1920:r=30:d={length:.3f}",
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "video.mp4"], cwd=tmp, check=True)
        level = pipeline.loudness_filter(tmp, "voice.m4a", [])
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", "voice.m4a", "-af", f"{level},aresample={AUDIO_RATE}", "-ac", "1",
                        "voice_lev.wav"], cwd=tmp, check=True)
        v = rms100(tmp / "voice_lev.wav")
        med = float(np.median(v))
        speech, pause = v > med - 10, v < med - 20   # relative to her own level; in-between windows are left out
        speech[-25:] = pause[-25:] = False           # the last 2.5 s: the bed fades out there
        print(f"\n{VOICE.parent.name} voice: {length:.1f} s; 100 ms windows: {speech.sum()} speech, {pause.sum()} pause "
              f"(speech = within 10 dB of her median level {med:.1f} dBFS, pause = 20 dB or more under it)\n")
        if not speech.any() or not pause.any():
            raise ValueError("Not enough speech and pause windows for a comparison")
        voice_level = pipeline.measure_loudness(tmp / "voice_lev.wav")
        if voice_level is None:
            raise ValueError("Could not measure the levelled voice")
        print(f"Levelled voice: {voice_level['lufs']:.1f} LUFS (target -14; report any miss, do not hide it)")
        print(f"{'track':48} {'duck':4} {'LUFS':>6} {'TP':>6} {'music speech':>13} {'music pause':>12} {'dip':>5}")
        for i, tr in enumerate(tracks):
            bed = music.music_bed(tr, length, tmp, name=f"bed{i}.wav").name
            for duck in (False, True):
                out = f"r{i}_{int(duck)}.mp4"
                pipeline.render(tmp, "video.mp4", "c.ass", out, voice="voice.m4a", music_wav=bed, duck=duck)
                m = pipeline.measure_loudness(tmp / out)
                assert m and m["true_peak"] <= -1.0, m
                # The measurement reports target misses; ducking must not hide a baseline levelling limit.
                graph = (f"[0:a]{level},aresample={AUDIO_RATE},aformat=channel_layouts=stereo[key];"
                         f"[1:a][key]{pipeline.DUCK}[a]" if duck else "[1:a]anull[a]")
                b_out = f"b{i}_{int(duck)}.wav"
                subprocess.run([FFMPEG, "-v", "error", "-y", "-i", "voice.m4a", "-i", bed, "-filter_complex", graph,
                                "-map", "[a]", b_out], cwd=tmp, check=True)
                b = rms100(tmp / b_out)
                k = min(len(b), len(speech))
                ms, mp = float(np.median(b[:k][speech[:k]])), float(np.median(b[:k][pause[:k]]))
                row = dict(track=tr.name, duck=duck, lufs=m["lufs"], true_peak=m["true_peak"],
                           music_speech_dbfs=ms, music_pause_dbfs=mp, dip_db=mp-ms)
                if duck:
                    base = rows[-1]
                    row["extra_speech_reduction_db"] = base["music_speech_dbfs"] - ms
                    row["extra_pause_reduction_db"] = base["music_pause_dbfs"] - mp
                rows.append(row)
                print(f"{tr.name[:48]:48} {'on' if duck else 'off':4} {m['lufs']:6.1f} {m['true_peak']:6.1f} "
                      f"{ms:13.1f} {mp:12.1f} {mp - ms:5.1f}")
        record.write_text(json.dumps({"inputs": fingerprint, "speech_windows": int(speech.sum()),
                                      "pause_windows": int(pause.sum()), "voice_lufs": voice_level["lufs"],
                                      "rows": rows}, indent=2), encoding="utf-8")
    finally:
        print("Local measurements/listening files retained at " + str(tmp))


if __name__ == "__main__":
    main()
