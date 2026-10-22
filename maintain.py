"""Housekeeping for the Video Maker: disk space and backups.  Nothing is deleted unless you add --yes.

  python maintain.py usage                  how much space videos use, newest first
  python maintain.py slim [--days 7]        drop the big working files of videos older than 7 days (finished video,
                                            titles, captions and remake data are kept)         add --yes to do it
  python maintain.py clean [--days 90]      delete whole videos and their uploads older than 90 days   add --yes
  python maintain.py backup [--to DIR] [--with-videos] [--keep 8]
                                            zip the accounts, settings, titles and history (not .env, not the session key)
"""
import argparse
import shutil
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

from video_tool.config import DATA_DIR, OUT_DIR

UPLOADS = OUT_DIR / "_uploads"
# Working files a finished video doesn't need. A remake re-renders from the saved upload and reuses words.json
# and match/scores.json, so those (and the results) stay.
SLIM_FILES = ("joined.mp4", "video.mp4", "trimmed.mp4", "concat.txt", "cut_graph.txt", "voice_trimmed.wav", "clip_audio.wav",
              "clip_sound.wav", "clip_sound_clean.wav", "voice_clean.wav")
SLIM_GLOBS = ("music_bed*.wav",)
SLIM_DIRS = ("normalized", "denoise", "match/frames")


def size(path):
    path = Path(path)
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def jobs():
    """(folder, last-changed time) for every video folder, oldest last."""
    rows = []
    for d in OUT_DIR.iterdir():
        if d.is_dir() and not d.name.startswith("_"):
            marker = d / "report.json"
            rows.append((d, (marker if marker.exists() else d).stat().st_mtime))
    return sorted(rows, key=lambda r: -r[1])


def sidecars(job_id):
    return [p for p in OUT_DIR.glob(f"{job_id}.*")]


def old_jobs(days):
    cutoff = time.time() - days * 86400
    return [(d, t) for d, t in jobs() if t < cutoff]


def cmd_usage(a):
    total = 0
    rows = jobs()
    for d, t in rows[:15]:
        s = size(d)
        print(f"{datetime.fromtimestamp(t):%Y-%m-%d %H:%M}  {human(s):>9}  {d.name}")
    for d, t in rows:
        total += size(d)
    print(f"\n{len(rows)} videos, {human(total)} in {OUT_DIR}; uploads {human(size(UPLOADS))}; disk free {human(shutil.disk_usage(OUT_DIR).free)}")
    slim = sum(size(d / n) for d, _ in rows for n in SLIM_FILES) + sum(size(d / n) for d, _ in rows for n in SLIM_DIRS)
    print(f"`slim` could free about {human(slim)} without losing any finished video")


def slim_job(d, really):
    freed = 0
    victims = [d / n for n in SLIM_FILES] + [d / n for n in SLIM_DIRS] + [f for g in SLIM_GLOBS for f in d.glob(g)]
    for v in victims:
        if not v.exists():
            continue
        freed += size(v)
        if really:
            shutil.rmtree(v) if v.is_dir() else v.unlink()
    return freed


def cmd_slim(a):
    freed = n = 0
    for d, _ in old_jobs(a.days):
        if not (d / "report.json").exists():
            continue   # still being made, or never finished: leave it
        f = slim_job(d, a.yes)
        freed += f
        n += bool(f)
    print(f"{'Freed' if a.yes else 'Would free'} {human(freed)} from {n} videos older than {a.days} days. Finished videos untouched.")
    if not a.yes:
        print("Nothing was deleted. Run again with --yes to do it.")


def cmd_clean(a):
    victims = old_jobs(a.days)
    freed = 0
    for d, t in victims:
        up = UPLOADS / d.name
        freed += size(d) + size(up) + sum(size(p) for p in sidecars(d.name))
        if a.yes:
            shutil.rmtree(d, ignore_errors=True)
            shutil.rmtree(up, ignore_errors=True)
            for p in sidecars(d.name):
                p.unlink(missing_ok=True)
    print(f"{'Deleted' if a.yes else 'Would delete'} {len(victims)} videos older than {a.days} days ({human(freed)}), including their uploads.")
    if not a.yes:
        print("Nothing was deleted. Run again with --yes to do it. Download anything you want to keep first.")


def cmd_backup(a):
    dest = Path(a.to).expanduser() if a.to else Path.home() / "Documents" / "video-backups"
    dest.mkdir(parents=True, exist_ok=True)
    zpath = dest / f"video-backup-{datetime.now():%Y%m%d-%H%M%S}.zip"
    small = [DATA_DIR / "users.json", DATA_DIR / "prefs.json", OUT_DIR / "feedback.jsonl", OUT_DIR / "failures.jsonl"]
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in small:
            if f.exists():
                z.write(f, f.name)
        for d in (DATA_DIR / "users",):
            for f in d.rglob("*") if d.exists() else []:
                if f.is_file():
                    z.write(f, f.relative_to(DATA_DIR))
        for d, _ in jobs():
            for name in ("copy.json", "report.json", "transcript.txt", "words.json"):
                if (d / name).exists():
                    z.write(d / name, f"videos/{d.name}/{name}")
            for p in sidecars(d.name):
                z.write(p, f"videos/{p.name}")
            if a.with_videos:
                for f in d.glob("final*.mp4"):
                    z.write(f, f"videos/{d.name}/{f.name}")
    old = sorted(dest.glob("video-backup-*.zip"))[:-a.keep] if a.keep else []
    for o in old:
        o.unlink()
    print(f"Backup saved: {zpath} ({human(zpath.stat().st_size)}). Kept the newest {a.keep}.")
    print("It holds password hashes: keep it somewhere private. The .env file (API key) and session key are NOT in it.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("usage").set_defaults(fn=cmd_usage)
    for name, fn, days in (("slim", cmd_slim, 7), ("clean", cmd_clean, 90)):
        p = sub.add_parser(name)
        p.add_argument("--days", type=int, default=days)
        p.add_argument("--yes", action="store_true", help="actually do it (otherwise only shows what would happen)")
        p.set_defaults(fn=fn)
    b = sub.add_parser("backup")
    b.add_argument("--to")
    b.add_argument("--with-videos", action="store_true")
    b.add_argument("--keep", type=int, default=8)
    b.set_defaults(fn=cmd_backup)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
