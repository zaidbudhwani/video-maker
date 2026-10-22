"""Optional: remove background noise (wind, fans, traffic) from the sound with DeepFilterNet.

Off unless she asks for it: her style is raw, and most of her recordings are already quiet.
It runs the official standalone deep-filter program, not the Python package (that one
pulls in torch and an older numpy, which breaks Whisper and MediaPipe).
Noise removal is a nicety, so nothing here stops a video: any problem returns a
plain-English reason and the original sound is used.
"""
import shutil
import subprocess
from pathlib import Path

from . import ff
from .config import AUDIO_RATE, DEEP_FILTER, DENOISE_LIMIT_DB
from .errors import AlexError

PAD = 4800         # samples of silence added to the end first: deep-filter drops the last ~30 ms
TIMEOUT = 600      # seconds; normal is about 4s per minute of sound
MISSING = ("Background noise was not removed: the deep-filter program isn't in assets/tools/ "
           "(see README). The video has its original sound.")


def _skipped(why):
    return None, f"Background noise was not removed ({why}). The video has its original sound."


def _samples(wav):
    return int(ff.probe(wav)["streams"][0]["duration_ts"])


def _run_filter(src, outdir):
    """deep-filter's output file, or None when it failed.

    -D makes up for the model's delay so word timings (captions, cuts) still line up.
    """
    try:
        p = subprocess.run([DEEP_FILTER, "-D", "-a", str(DENOISE_LIMIT_DB), "-o", str(outdir), str(src)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = outdir / src.name   # deep-filter names its output after the input
    return out if p.returncode == 0 and out.exists() else None


def _clean(wav, work, out):
    """Pad, filter, trim back: the result is exactly as long as the original. Returns why not, or None."""
    padded = work / wav.name
    ff.run(["-i", wav, "-vn", "-af", f"aresample={AUDIO_RATE},apad=pad_len={PAD}", "-c:a", "pcm_s16le", padded],
           what="Preparing sound for noise removal")
    want = _samples(padded) - PAD
    filtered = _run_filter(padded, work / "out")
    if not filtered:
        return "deep-filter failed"
    if not want <= _samples(filtered) <= want + PAD:
        return "the cleaned sound came out a different length"
    ff.run(["-i", filtered, "-af", f"atrim=end_sample={want}", "-c:a", "pcm_s16le", out], what="Finishing cleaned sound")
    return None


def clean(wav, job, name):
    """Returns (cleaned wav, None), or (None, why not) and the caller keeps the original sound."""
    wav, job = Path(wav), Path(job)
    if not DEEP_FILTER:
        return None, MISSING
    work = job / "denoise"   # scratch folder, removed again whatever happens
    shutil.rmtree(work, ignore_errors=True)
    try:
        work.mkdir(parents=True)
        why = _clean(wav, work, job / name)
    except (AlexError, OSError, LookupError, ValueError):
        why = "the sound could not be read"
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return _skipped(why) if why else (job / name, None)


def clean_clips(video, job):
    """Talking to camera: clean the joined clips' own sound. Same return as clean()."""
    if not DEEP_FILTER:
        return None, MISSING
    sound = Path(job) / "clip_sound.wav"
    try:
        ff.run(["-i", video, "-vn", "-ar", AUDIO_RATE, "-c:a", "pcm_s16le", sound], what="Extracting clip sound")
    except (AlexError, OSError):
        return _skipped("the clips' sound could not be read")
    return clean(sound, job, "clip_sound_clean.wav")
