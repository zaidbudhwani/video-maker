"""Step 5: optional flat music bed, levelled by loudness to sit about 20 LU under her voice. Optional gentle ducking under her voice happens in pipeline.render (setting music_duck, off by default)."""
import hashlib
import re
import subprocess
from pathlib import Path

from . import ff
from . import settings
from .config import AUDIO_EXT, AUDIO_RATE, FFMPEG, MUSIC_DIR, MUSIC_LUFS_OFFSET

NO_MUSIC = "none"   # the choice "No music" (the same as music_on off)
PREVIEW_SECONDS = 10
_LICENCE_ROW = re.compile(r'^(?P<file>\S+\.\w{2,4})\s+"(?P<title>[^"]+)"\s+by\s+(?P<artist>.+?)\s*(?:\(|$)')


def installed(music_dir=None):
    """The track files in the music folder, sorted by name (nothing else in the folder counts)."""
    d = Path(music_dir or MUSIC_DIR)
    if not d.is_dir():
        return []
    return sorted((p for p in d.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXT), key=lambda p: p.name.lower())


def tracks(music_dir=None):
    """[{"file", "title", "artist"}] for every installed track. Title and artist come from LICENSES.txt (where the team
    records each track's source); a track missing from it is shown by its file name."""
    d = Path(music_dir or MUSIC_DIR)
    names = {}
    try:
        for line in (d / "LICENSES.txt").read_text(encoding="utf-8", errors="replace").splitlines():
            m = _LICENCE_ROW.match(line.strip())
            if m:
                names[m.group("file").lower()] = (m.group("title").strip(), m.group("artist").strip())
    except OSError:
        pass
    out = []
    for p in installed(d):
        title, artist = names.get(p.name.lower(), (" ".join(p.stem.replace("_", " ").replace("-", " ").split()), ""))
        out.append({"file": p.name, "title": title, "artist": artist})
    return out


def find(name, music_dir=None):
    """The installed track with exactly this file name, or None. Only names from the folder listing match, so a path,
    a ".." or any other file in the folder can never be reached through it."""
    if not isinstance(name, str) or not name:
        return None
    return next((p for p in installed(music_dir) if p.name == name), None)


def pick_track(choice="", seed=None, music_dir=None):
    """Which track goes under this video.

    choice: an installed file name (her pick) or "none" (no music). Anything else ("" = let the tool pick, or a track
    that has since been removed) means rotation: the same video (seed = its upload folder name, which every remake
    keeps) always gets the same track, and different videos are spread over the tracks so they don't all share one.
    No seed: the first track by name (the old behaviour)."""
    if choice == NO_MUSIC:
        return None
    chosen = find(choice, music_dir)
    if chosen:
        return chosen
    found = installed(music_dir)
    if not found:
        return None
    if not seed:
        return found[0]
    n = int(hashlib.sha256(str(seed).encode("utf-8")).hexdigest(), 16)
    return found[n % len(found)]


def title_of(name, music_dir=None):
    """'"Muted Rest" by Turning_Pages' for an installed track's file name, or None."""
    t = next((t for t in tracks(music_dir) if t["file"] == name), None)
    if not t:
        return None
    return f'"{t["title"]}"' + (f' by {t["artist"]}' if t["artist"] else "")


def preview(track, out):
    """The first PREVIEW_SECONDS of a track (the part that goes under a video: the bed starts at the beginning) as a
    small mp3, faded out at the end. Same loudness as the file; the page plays it at a normal listening level."""
    ff.run(["-i", track, "-t", str(PREVIEW_SECONDS), "-vn", "-af", f"afade=t=out:st={PREVIEW_SECONDS - 1}:d=1",
            "-ac", "2", "-b:a", "128k", out], file=track, what="Preparing a music preview")
    return out


def track_lufs(track, length=None):
    """Integrated loudness in LUFS (how loud it sounds, not how high its peaks are) of the track, or of the first `length`
    seconds of it looped, which is the stretch that actually goes under a video. None if unreadable."""
    seg = ["-stream_loop", "-1", "-t", f"{length:.3f}"] if length else []
    p = subprocess.run([FFMPEG, "-hide_banner", "-nostats", *seg, "-i", str(track), "-vn", "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    m = re.search(r"I:\s+(-?[\d.]+)\s+LUFS", p.stderr[p.stderr.rfind("Summary:"):])
    return float(m.group(1)) if m else None


def music_bed(track, length, job_dir, name="music_bed.wav"):
    """Loop/trim the track to length and set its loudness so it sits well under her voice (see MUSIC_LUFS_OFFSET).

    Levelled by loudness (LUFS), not by average level: tracks with quiet passages or loud peaks then all sit at the same
    place under the voice. If the loudness cannot be measured, the old average-level method is used as a fallback."""
    track = Path(track)
    target = settings.get("music_db") + MUSIC_LUFS_OFFSET
    lufs = track_lufs(track, length)   # measured on the stretch used: an intro is often quieter than the whole track
    if lufs is None:
        mean = ff.max_volume_db(track).get("mean_volume")
        lufs = mean
    gain = target - lufs if lufs is not None else 0
    out = Path(job_dir) / name
    fade_at = max(0.0, length - 1.5)
    ff.run(["-stream_loop", "-1", "-i", track, "-t", f"{length:.3f}",
            "-af", f"volume={gain:.2f}dB,afade=t=out:st={fade_at:.2f}:d=1.5,aresample={AUDIO_RATE}",
            "-ac", "2", out], file=track, what="Preparing music")
    return out
