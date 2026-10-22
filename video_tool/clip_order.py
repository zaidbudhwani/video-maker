"""Put her clips in the order she filmed them, so she never has to number or sort them.

A phone writes the moment it started filming inside every clip. Sorted by that moment, a walk through a
house plays in walking order. Names only decide when the moments can't be trusted. If they can't, the order
she was given is kept (never guessed).

The check that keeps this honest: filming one clip takes as long as the clip is, so two clips can't overlap in time. A batch of clips that were all exported or sent at the same moment fails
the check, because their "times" are the same few seconds. Then nothing is moved.
"""
import re
from datetime import datetime, timezone

from . import ff

MIN_EPOCH = 1104537600       # 2005-01-01: earlier dates mean the phone's clock was never set
SLACK = 1.5                  # seconds of tolerance when comparing a gap with the previous clip's length
_TAGS = ("com.apple.quicktime.creationdate", "creation_time")   # the first has the time zone and is exact


def natural_key(name):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", str(name))]


def parse_time(text):
    """'2026-09-22T11:52:29-0500', '2026-09-22T16:52:29.000000Z' -> seconds since 1970 (None if it isn't a date)."""
    if not text:
        return None
    s = str(text).strip().replace("Z", "+00:00")
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    t = d.timestamp()
    return t if t > MIN_EPOCH else None


def capture_time(path=None, info=None):
    """When this clip was filmed, from the file itself (None if the file doesn't say)."""
    try:
        info = info or ff.probe(path)
    except Exception:
        return None
    for source in [info.get("format", {}).get("tags") or {}] + [s.get("tags") or {} for s in info.get("streams", [])]:
        low = {k.lower(): v for k, v in source.items()}
        for tag in _TAGS:
            t = parse_time(low.get(tag))
            if t:
                return t
    return None


def plan_order(items):
    """items: [{'name', 'time' (seconds or None), 'seconds' (clip length or None)}] in the order she gave them.

    Returns (order, basis): order is the list of indexes in play order and basis is 'filmed' when the filming
    times were used, or None when her order is kept as it was (too few clips, a missing time, times that can't
    be filming times, or the clips were already in that order)."""
    n = len(items)
    same = list(range(n))
    if n < 2:
        return same, None
    times = [it.get("time") for it in items]
    if any(t is None for t in times):
        return same, None
    order = sorted(same, key=lambda i: (times[i], natural_key(items[i].get("name", ""))))
    if times[order[-1]] - times[order[0]] < 1:
        return same, None                      # every clip says the same moment: that is an export time, not a filming time
    # Phones differ on whether the time is when filming started or when it ended. Either way two clips can't
    # overlap: when the time is a start, the gap to the next clip is at least this clip's length; when it is an
    # end, the gap is at least the NEXT clip's length. Times that fit neither way are export times, not filming times.
    pairs = list(zip(order, order[1:]))
    as_start = all(not items[a].get("seconds") or times[b] - times[a] >= items[a]["seconds"] - SLACK for a, b in pairs)
    as_end = all(not items[b].get("seconds") or times[b] - times[a] >= items[b]["seconds"] - SLACK for a, b in pairs)
    if not (as_start or as_end):
        return same, None
    return (order, "filmed") if order != same else (same, None)


def sort_files(files):
    """Clip files in the order they were filmed (used when nothing numbered them). Falls back to natural file-name
    order when the files don't hold usable times. Returns (files, basis) with basis 'filmed' or 'name'."""
    files = sorted(files, key=lambda f: natural_key(f.name))
    items = []
    for f in files:
        try:
            info = ff.probe(f)
        except Exception:
            info = None
        items.append({"name": f.name, "time": capture_time(info=info) if info else None,
                      "seconds": info["duration"] if info else None})
    order, basis = plan_order(items)
    return ([files[i] for i in order], "filmed") if basis == "filmed" else (files, "name")
