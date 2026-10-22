"""The class report: did the tool save her time, and is she posting more?

Works from three things the website already keeps: every video's row (when it was made, how long the build took,
her estimate of how long it would have taken by hand), the events log (copy / download / posted, never her words),
and nothing else. No video, caption or title text goes in.

What is measured, and how honestly:
- build minutes: the tool's own clock for the first version of a video.
- review minutes: from the moment the first version was ready to her first Copy / Download / Posted tap for that
  video (or any of its remakes). Only counted when that is within 4 hours; a video she comes back to the next day
  says nothing about how long review took, so it is left out instead of guessed.
- timed review minutes: her own "Time my review" stopwatch on the result page, added up over the video and all its
  remakes. Only videos she timed have it; the other review number is never mixed in.
- by hand minutes: her own answer to "about how many minutes would this have taken you to edit yourself?".
- saved minutes: by hand minus (build + review), only for videos that have all three numbers. It is an estimate,
  and the report says how many videos it rests on.
"""
import statistics
from datetime import datetime

REVIEW_WINDOW_MIN = 240


def _t(text):
    try:
        return datetime.fromisoformat(text) if text else None
    except (TypeError, ValueError):
        return None


def _avg(values, digits=1):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), digits) if values else None


def _median(values, digits=1):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), digits) if values else None


def per_video(videos, events):
    """One row per video (a first version and all its remakes). videos: rows as served by /api/videos;
    events: rows of events.jsonl. Only finished videos count."""
    done = [v for v in videos if v.get("status") == "done" and v.get("first_version")]
    families = {}
    for v in done:
        families.setdefault(v["first_version"], []).append(v)
    first_event = {}
    posted = set()
    for e in events:
        t = _t(e.get("time"))
        if not t or not e.get("job"):
            continue
        for fam, members in families.items():
            if any(m["job_id"] == e["job"] for m in members):
                if fam not in first_event or t < first_event[fam]:
                    first_event[fam] = t
                if e.get("kind") == "posted":
                    posted.add(fam)
    rows = []
    for fam, members in families.items():
        members.sort(key=lambda m: m.get("made_at") or "")
        first = next((m for m in members if m["job_id"] == fam), members[0])
        ready = _t(first.get("made_at"))
        review = None
        if ready and fam in first_event:
            minutes = (first_event[fam] - ready).total_seconds() / 60
            if 0 <= minutes <= REVIEW_WINDOW_MIN:
                review = round(minutes, 1)
        build = round(first["build_seconds"] / 60, 1) if first.get("build_seconds") else None
        by_hand = next((m["by_hand_minutes"] for m in members if m.get("by_hand_minutes")), None)
        timed = [m["timed_review_minutes"] for m in members if m.get("timed_review_minutes")]
        timed = round(sum(timed), 3) if timed else None
        saved = round(by_hand - build - review, 1) if None not in (by_hand, build, review) else None
        iso = ready.isocalendar() if ready else None
        rows.append({
            "job": fam, "user": first.get("owner") or "(before accounts)", "made_at": first.get("made_at"),
            "week": f"{iso[0]}-W{iso[1]:02d}" if iso else None, "mode": first.get("mode"), "clips": first.get("clips"),
            "length_s": first.get("length"), "build_min": build, "review_min": review, "timed_review_min": timed, "by_hand_min": by_hand,
            "saved_min": saved, "remakes": len(members) - 1, "posted": fam in posted,
        })
    rows.sort(key=lambda r: r["made_at"] or "")
    return rows


def summarise(videos, events):
    rows = per_video(videos, events)
    groups = {"All": rows}
    for r in rows:
        groups.setdefault(r["user"], []).append(r)
    out = []
    for name, rs in groups.items():
        weeks = {}
        for r in rs:
            if r["week"]:
                w = weeks.setdefault(r["week"], {"made": 0, "posted": 0})
                w["made"] += 1
                w["posted"] += 1 if r["posted"] else 0
        counted = [r for r in rs if r["saved_min"] is not None]
        out.append({
            "user": name, "videos": len(rs), "remakes": sum(r["remakes"] for r in rs),
            "posted": sum(1 for r in rs if r["posted"]),
            "avg_build_min": _avg([r["build_min"] for r in rs]),
            "median_review_min": _median([r["review_min"] for r in rs]),
            "review_counted": sum(1 for r in rs if r["review_min"] is not None),
            "median_timed_review_min": _median([r["timed_review_min"] for r in rs], digits=3),
            "timed_counted": sum(1 for r in rs if r["timed_review_min"] is not None),
            "avg_by_hand_min": _avg([r["by_hand_min"] for r in rs]),
            "by_hand_counted": sum(1 for r in rs if r["by_hand_min"] is not None),
            "avg_saved_min": _avg([r["saved_min"] for r in rs]),
            "saved_counted": len(counted),
            "weeks": [{"week": k, **weeks[k]} for k in sorted(weeks)],
            "best_week_made": max((w["made"] for w in weeks.values()), default=0),
        })
    return {"groups": out, "videos": rows}


CSV_COLUMNS = ["job", "user", "made_at", "week", "mode", "clips", "length_s", "build_min", "review_min",
               "timed_review_min", "by_hand_min", "saved_min", "remakes", "posted"]


def to_csv(rows):
    """A spreadsheet of one row per video. Fields that could start a formula are never text here, but the job id and
    names come from the account list, so anything starting with = + - @ is defanged anyway."""
    import csv
    import io

    def cell(v):
        if v is None:
            return ""
        s = str(v)
        return "'" + s if s[:1] in ("=", "+", "-", "@") and not s.lstrip("-").replace(".", "", 1).isdigit() else s

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    for r in rows:
        w.writerow([cell(r.get(c)) for c in CSV_COLUMNS])
    return buf.getvalue()
