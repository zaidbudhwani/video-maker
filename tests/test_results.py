"""The class report maths: videos, remakes, build/review/by-hand/saved minutes, videos per week.

    venv\\Scripts\\python tests\\test_results.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from video_tool import results  # noqa: E402

failed = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  - ' + str(detail) if detail else ''}", flush=True)
    if not ok:
        failed.append(name)


def v(job, owner, made, first=None, build=120, by_hand=None, status="done", **kw):
    return {"job_id": job, "owner": owner, "made_at": made, "first_version": first or job, "build_seconds": build,
            "by_hand_minutes": by_hand, "status": status, "mode": "voice", "clips": 3, "length": 26.0, **kw}


def ev(job, kind, when):
    return {"user": "alex", "job": job, "kind": kind, "time": when}


videos = [
    v("a", "alex", "2026-10-12T10:00:00", build=150, by_hand=120),             # Mon week 42, posted, all three numbers
    v("a2", "alex", "2026-10-12T10:20:00", first="a", by_hand=120),            # a remake of a
    v("b", "alex", "2026-10-14T09:00:00", build=60, by_hand=90),               # reviewed next day: no review minutes
    v("c", "alex", "2026-10-21T08:00:00", build=180),                          # a week later, no estimate
    v("x", "zaid", "2026-10-12T12:00:00", build=100),
    v("f", "alex", "2026-10-13T08:00:00", status="failed"),                    # failed: never counted
]
events = [
    ev("a2", "copy", "2026-10-12T10:35:00"),       # 35 min after a was ready (a remake's tap counts for the video)
    ev("a", "posted", "2026-10-12T11:00:00"),
    ev("b", "download", "2026-10-15T09:30:00"),    # more than 4 hours later
    ev("c", "copy", "not a date"),                 # bad row ignored
]

rows = results.per_video(videos, events)
by = {r["job"]: r for r in rows}
check("failed videos and remakes are not separate videos", sorted(by) == ["a", "b", "c", "x"], sorted(by))
check("a remake is counted on its video", by["a"]["remakes"] == 1 and by["b"]["remakes"] == 0)
check("build minutes come from the first version", by["a"]["build_min"] == 2.5, by["a"]["build_min"])
check("review minutes: ready to first tap, a remake's tap included", by["a"]["review_min"] == 35.0, by["a"]["review_min"])
check("a tap the next day says nothing about review time", by["b"]["review_min"] is None)
check("her by-hand answer on any version is used", by["a"]["by_hand_min"] == 120)
check("saved = by hand - build - review", by["a"]["saved_min"] == round(120 - 2.5 - 35, 1), by["a"]["saved_min"])
check("no saved number without all three", by["b"]["saved_min"] is None and by["c"]["saved_min"] is None)
check("posted is per video", by["a"]["posted"] and not by["b"]["posted"])
check("ISO week labels", by["a"]["week"] == "2026-W42" and by["c"]["week"] == "2026-W43", (by["a"]["week"], by["c"]["week"]))

s = results.summarise(videos, events)
g = {x["user"]: x for x in s["groups"]}
check("an 'All' group and one per person", set(g) == {"All", "alex", "zaid"}, set(g))
check("alex: 3 videos, 1 remake, 1 posted", (g["alex"]["videos"], g["alex"]["remakes"], g["alex"]["posted"]) == (3, 1, 1), g["alex"])
check("videos per week", g["alex"]["weeks"] == [{"week": "2026-W42", "made": 2, "posted": 1}, {"week": "2026-W43", "made": 1, "posted": 0}], g["alex"]["weeks"])
check("average build minutes", g["alex"]["avg_build_min"] == round((2.5 + 1.0 + 3.0) / 3, 1), g["alex"]["avg_build_min"])
check("counts say how many videos each average rests on", (g["alex"]["saved_counted"], g["alex"]["review_counted"], g["alex"]["by_hand_counted"]) == (1, 1, 2), g["alex"])
check("average saved", g["alex"]["avg_saved_min"] == 82.5)
check("best week", g["alex"]["best_week_made"] == 2)
check("nothing measured gives None, not 0", g["zaid"]["avg_saved_min"] is None and g["zaid"]["median_review_min"] is None)

empty = results.summarise([], [])
check("an empty log gives an empty report", empty["videos"] == [] and empty["groups"][0]["videos"] == 0)
check("a video without an owner is listed", results.per_video([v("o", None, "2026-10-12T10:00:00")], [])[0]["user"] == "(before accounts)")

csv_text = results.to_csv(rows + [{"job": "=1+1", "user": "@evil", "made_at": None}])
lines = csv_text.split("\r\n")
check("csv has a header and a row per video", lines[0].startswith("job,user,made_at") and len([l for l in lines if l]) == 1 + len(rows) + 1)
check("csv defangs formula-looking text", "'=1+1" in csv_text and "'@evil" in csv_text)
check("csv keeps negative numbers as numbers", "'-" not in results.to_csv([{"job": "j", "saved_min": -5.0}]))

print()
print("ALL PASSED" if not failed else f"{len(failed)} FAILED: " + "; ".join(failed))
sys.exit(1 if failed else 0)
