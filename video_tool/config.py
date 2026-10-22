"""Fixed settings. Alex never touches these."""
import glob
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# ALEX_DATA_DIR lets a hosted copy keep videos and accounts on a persistent disk
DATA_DIR = Path(os.getenv("ALEX_DATA_DIR") or ROOT)
OUT_DIR = DATA_DIR / "out"
MUSIC_DIR = ROOT / "assets" / "music"
PROMPTS_DIR = ROOT / "prompts"

WIDTH, HEIGHT, FPS = 1080, 1920, 30
AUDIO_RATE = 48000
MAX_SECONDS = 59.5            # YouTube Shorts limit is 60s; keep a margin

SILENCE_GAP = 0.7             # gaps longer than this get trimmed
SILENCE_KEEP = 0.25           # breathing room left on each side of a cut

CAPTION_MIN_WORDS, CAPTION_MAX_WORDS = 2, 4
CAPTION_MIN_SECONDS = 1.0

MUSIC_DB = -22.0
# The music_db setting is a level in dB (-22 = the default, higher = louder). It is applied as the bed's integrated loudness
# (LUFS) = music_db + MUSIC_LUFS_OFFSET, so the default bed sits 20 LU under her voice (-34 vs -14 LUFS) and the loudest
# allowed setting (-14) is still 12 LU under it: the voice is always the main thing, whatever track is used.
MUSIC_LUFS_OFFSET = -12.0
VOICE_LUFS = -14.0            # YouTube's playback loudness
PEAK_LIMIT_DB = -1.0          # no finished video may peak above this (true peak, measured on the ENCODED file)
TRUE_PEAK_TARGET = -2.0       # what the leveller aims for: AAC encoding adds up to about 1 dB of overshoot on top

# Resizing filter for every scale step. ffmpeg's default (bicubic, fast rounding, chroma not interpolated) is softer;
# lanczos keeps more detail when a square, sideways or 1080p clip is enlarged for the 1080x1920 frame. No effect added.
SCALER = "lanczos+accurate_rnd+full_chroma_int"

# Clips vs voice length sanity checks ("far apart" = fail loudly)
MAX_SHORTFALL_RATIO = 0.5     # clips cover less than half the voice -> fail
MAX_OVERRUN_RATIO = 12.0      # clips more than 12x the voice -> fail (long raw footage is normal: only the used part is rendered)

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")

# Phone HDR (HLG/PQ) -> normal SDR so colors don't look washed out on YouTube
HDR_TRANSFERS = {"arib-std-b67", "smpte2084"}
TONEMAP = ("zscale=t=linear:npl=203,format=gbrpf32le,zscale=p=bt709,"
           "tonemap=mobius:desat=0,zscale=t=bt709:m=bt709:r=tv,")

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".3gp", ".webm", ".mkv", ".avi"}
# Voice Memos on iPhone (.m4a, newer ones .qta), WhatsApp/Android voice notes (.opus, .ogg, .amr), .caf from Apple apps
AUDIO_EXT = {".m4a", ".mp3", ".wav", ".aac", ".caf", ".qta", ".opus", ".ogg", ".oga", ".amr", ".3ga", ".flac", ".aiff", ".aif", ".wma"}


def _find(tool: str) -> str:
    found = shutil.which(tool)
    if found:
        return found
    # winget installs ffmpeg here but the PATH change needs a new shell
    local = os.environ.get("LOCALAPPDATA", "")
    hits = glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "*", "bin", tool + ".exe"))
    if hits:
        return hits[0]
    raise SystemExit(f"ERROR: {tool} not found. Install ffmpeg and add it to PATH.")


FFMPEG = _find("ffmpeg")
FFPROBE = _find("ffprobe")


def _find_optional(tool: str):
    """Optional helper programs live in assets/tools/ (or on PATH). None when not installed.

    Only a runnable file counts (.exe on Windows, executable elsewhere), so a half-finished
    download or a notes file next to it is never picked. The plain name wins over longer ones.
    """
    def runnable(p):
        return p.is_file() and (p.suffix.lower() == ".exe" if os.name == "nt"
                                else p.suffix != ".exe" and os.access(p, os.X_OK))
    hits = sorted((p for p in (ROOT / "assets" / "tools").glob(tool + "*") if runnable(p)),
                  key=lambda p: (p.stem != tool, p.name))
    return str(hits[0]) if hits else shutil.which(tool)


# DeepFilterNet's standalone noise remover (see README). Videos are still made without it.
DEEP_FILTER = _find_optional("deep-filter")
DENOISE_LIMIT_DB = 24         # most the background is turned down; more starts to sound processed
