"""Thin subprocess wrappers around ffmpeg / ffprobe."""
import json
import subprocess

from .config import FFMPEG, FFPROBE
from .errors import AlexError


def run(args, file=None, cwd=None, what="ffmpeg step"):
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)]
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise AlexError(f"{what} failed:\n{p.stderr.strip()[-1500:]}", file)
    return p


def probe(path):
    p = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if p.returncode != 0:
        raise AlexError(f"Can't read this file. It may be corrupt or not a real video/audio file.\n  {p.stderr.strip()}", path)
    info = json.loads(p.stdout)
    try:
        info["duration"] = float(info["format"]["duration"])
    except (KeyError, ValueError):
        raise AlexError("File has no readable length. It may be corrupt or cut off mid-upload.", path)
    return info


def has_stream(info, kind):
    return any(s.get("codec_type") == kind for s in info["streams"])


def max_volume_db(path):
    """Peak level in dB via volumedetect. -91 means digital silence."""
    cmd = [FFMPEG, "-hide_banner", "-i", str(path), "-af", "volumedetect", "-vn", "-f", "null", "-"]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = {}
    for line in p.stderr.splitlines():
        for key in ("max_volume", "mean_volume"):
            if key in line:
                out[key] = float(line.split(":")[1].replace("dB", "").strip())
    return out
