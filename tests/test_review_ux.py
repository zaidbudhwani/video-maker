"""Review helpers on the result and upload pages: the trust line, versions for Compare, the review timer (and the class
report fields it feeds), remembered settings in plain words, the first-run tips. No AI, no video is made.

    venv\\Scripts\\python tests\\test_review_ux.py

In-process (FastAPI TestClient) with a throwaway data folder; finished jobs are small hand-made report.json files."""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = Path(tempfile.mkdtemp(prefix="alex-review-ux-"))
os.environ.update(ALEX_DATA_DIR=str(DATA), ALEX_HOSTED="1", ADMIN_USER="tester", ADMIN_PASSWORD="admin-pass-123",
                  ANTHROPIC_API_KEY="", OPENROUTER_API_KEY="", APP_PASSWORD="", ALEX_AUTO_SLIM="0")

from fastapi.testclient import TestClient   # noqa: E402

from video_tool import results  # noqa: E402
from web import app as W, auth, review   # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f"  -> {detail}"), flush=True)
    if not ok:
        fails.append(name)


def client(user=None, pw=None):
    c = TestClient(W.app, base_url="http://testserver")
    if user:
        assert c.post("/api/login", json={"username": user, "password": pw}).status_code == 200
    return c


auth.set_user("alex", "alex-pass-1234")
auth.set_user("other", "other-pass-1234")
auth.set_user("newbie", "newbie-pass-1234")
alex, other, newbie, admin = client("alex", "alex-pass-1234"), client("other", "other-pass-1234"), \
    client("newbie", "newbie-pass-1234"), client("tester", "admin-pass-123")
OUT = W.OUT

PHRASES = ["Here is the big kitchen island", "The large deck out back", "The finished basement with a bar",
           "Call me today for a showing", "The primary bedroom has a walk-in closet"]


def job(jid, owner, parent=None, mode="voice", clip_match=None, scores=None, settings=None, own=None, parts=None):
    d = OUT / jid
    d.mkdir(parents=True, exist_ok=True)
    rep = {"job_id": jid, "mode": mode, "length": 20.0, "parts": parts or [{"file": "final.mp4", "length": 20.0}],
           "settings": settings or {}, "warnings": [], "timeline": [], "seconds_to_build": 90}
    if clip_match is not None:
        rep["clip_match"] = clip_match
    (d / "report.json").write_text(json.dumps(rep), encoding="utf-8")
    (OUT / f"{jid}.owner").write_text(owner, encoding="utf-8")
    if parent:
        (OUT / f"{jid}.parent").write_text(parent, encoding="utf-8")
    if own:
        (OUT / f"{jid}.settings.json").write_text(json.dumps(own), encoding="utf-8")
    if scores is not None:
        (d / "match").mkdir(exist_ok=True)
        (d / "match" / "scores.json").write_text(json.dumps({"phrases": PHRASES, "phrase_times": [[i * 4, i * 4 + 3] for i in range(5)],
                                                             "scores": scores}), encoding="utf-8")


# two clips, two frames each; per phrase: kitchen 9, deck 8, basement 2 (none), call-me (filler, ignored), bedroom 3 (none)
SCORES = [[[9, 1, 1, 5, 1], [8, 2, 2, 5, 2]], [[1, 8, 2, 5, 3], [1, 7, 1, 5, 1]]]

print("Trust line")
job("t-match", "alex", clip_match={"used": True}, scores=SCORES)
t = alex.get("/api/jobs/t-match").json()["trust"]
check("saved scores describe estimated availability, not shown matches", t["kind"] == "estimate" and t["estimated_available"] == 2 and t["estimated_missing"] == 2, t)
check("trust text avoids verified matches and exact counts", "estimates available footage" in t["text"] and "not whether the finished video shows the right shots" in t["text"] and "I matched" not in t["text"] and not any(c.isdigit() for c in t["text"]), t)
job("t-even", "alex", clip_match={"used": False}, scores=SCORES)
t = alex.get("/api/jobs/t-even").json()["trust"]
check("unused scores remain estimates, no equal-time claim", t["kind"] == "estimate" and "order and available length" in t["text"] and "equal time" not in t["text"], t)
job("t-none", "alex", clip_match={"used": False, "reason": "no API key"})
t = alex.get("/api/jobs/t-none").json()["trust"]
check("no key: honest order/length fallback without invented counts", t["kind"] == "none" and t["estimated_available"] is None and "order and available length" in t["text"] and "equal time" not in t["text"], t)
job("t-private", "alex", clip_match={"used": True}, scores=SCORES, own={"privacy": "private"})
t = alex.get("/api/jobs/t-private").json()["trust"]
check("private choice takes precedence over saved scores", t["kind"] == "none" and "keep your pictures private" in t["text"], t)
job("t-talk", "alex", mode="own_audio")
check("talking to camera: no trust line", alex.get("/api/jobs/t-talk").json()["trust"] is None)
job("t-bad", "alex", clip_match={"used": True})
d = OUT / "t-bad" / "match"; d.mkdir(exist_ok=True)
for bad in ("{not json", "[]", json.dumps({"phrases": PHRASES, "scores": [[["oops"] * 5]]}), json.dumps({"phrases": PHRASES, "scores": [[[float("nan")] * 5]]}), json.dumps({"phrases": PHRASES, "scores": [[[True] * 5]]}), json.dumps({"phrases": PHRASES, "scores": []})):
    (d / "scores.json").write_text(bad, encoding="utf-8")
    r = alex.get("/api/jobs/t-bad")
    check("invalid or incomplete saved scores never fabricate estimates", r.status_code == 200 and r.json()["trust"]["kind"] == "none" and "unavailable" in r.json()["trust"]["text"], r.text[:200])

# Versions and existing security checks
print("Versions for Compare")
job("v1", "alex")
job("v2", "alex", parent="v1", parts=[{"file": "final_part1.mp4", "length": 20.0}])
job("v3", "alex", parent="v2")
r1, r2, r3 = (alex.get(f"/api/jobs/{j}").json() for j in ("v1", "v2", "v3"))
check("first version: version 1, nothing to compare", r1["version"] == 1 and r1["previous"] is None, (r1["version"], r1["previous"]))
check("third version: version 3, compares with version 2", r3["version"] == 3 and r3["previous"] == "v2", (r3["version"], r3["previous"]))
check("the earlier version's own video file is named", r3["previous_file"] == "final_part1.mp4" and r2["previous_file"] == "final.mp4",
      (r3.get("previous_file"), r2.get("previous_file")))
(OUT / "v1" / "report.json").unlink()
check("an earlier version that was deleted is not offered", alex.get("/api/jobs/v2").json()["previous"] is None)
job("v1", "alex")
job("o1", "other")
job("o2", "alex", parent="o1")
check("someone else's video is never offered to compare", alex.get("/api/jobs/o2").json()["previous"] is None)
(OUT / "loop-a.parent").write_text("loop-b", encoding="utf-8")
job("loop-b", "alex", parent="loop-a")
job("loop-a", "alex", parent="loop-b")
check("a parent loop cannot hang the page", alex.get("/api/jobs/loop-a").json()["version"] >= 2)

print("Review timer")
check("not timed yet: no minutes", alex.get("/api/jobs/v3").json()["review_minutes"] is None)
res = alex.post("/api/jobs/v3/review-time", json={"seconds": 600})
check("a timed session is saved", res.status_code == 200 and res.json()["review_minutes"] == 10.0, res.text)
res = alex.post("/api/jobs/v3/review-time", json={"seconds": 330})
check("a second session adds up", res.json()["review_minutes"] == 15.5, res.text)
check("the result page gets the saved minutes", alex.get("/api/jobs/v3").json()["review_minutes"] == 15.5)
for bad_body in ({"seconds": 0}, {"seconds": -5}, {"seconds": 5 * 3600}, {"seconds": "600"}, {"seconds": True}, {}):
    res = alex.post("/api/jobs/v3/review-time", json=bad_body)
    check(f"a bad time is refused in plain words: {bad_body}", res.status_code == 400 and "four hours" in res.json()["detail"], res.text)
check("someone else cannot time her video", other.post("/api/jobs/v3/review-time", json={"seconds": 60}).status_code == 404)
(OUT / "nope.owner").write_text("alex", encoding="utf-8")
check("an unfinished video cannot be timed", alex.post("/api/jobs/nope/review-time", json={"seconds": 60}).status_code == 409)
saved = json.loads((OUT / "v3.review.json").read_text(encoding="utf-8"))
check("only the length of each session is kept", all(set(s) == {"seconds", "user", "ended"} for s in saved["sessions"]), saved)
alex.post("/api/jobs/v1/review-time", json={"seconds": 120})
rows = {r["job_id"]: r for r in alex.get("/api/videos").json()["videos"]}
check("My videos rows carry the timed minutes", rows["v3"]["timed_review_minutes"] == 15.5 and rows["v2"]["timed_review_minutes"] is None,
      (rows["v3"].get("timed_review_minutes"), rows["v2"].get("timed_review_minutes")))
rep = admin.get("/api/admin/results").json()
fam = next(v for v in rep["videos"] if v["job"] == "v1")
check("class report: a video's timed minutes add up over its versions", fam["timed_review_min"] == 17.5, fam)
g = {x["user"]: x for x in rep["groups"]}
check("class report: median timed review and how many videos it rests on",
      g["alex"]["median_timed_review_min"] == 17.5 and g["alex"]["timed_counted"] == 1, g["alex"])
csv_text = admin.get("/api/admin/results.csv").text
head = csv_text.splitlines()[0].split(",")
check("CSV has the timed review column", "timed_review_min" in head, head)
v1row = next(l for l in csv_text.splitlines() if l.startswith("v1,"))
check("CSV row has the number", v1row.split(",")[head.index("timed_review_min")] == "17.5", v1row)
# the maths alone, with no website
pv = results.per_video([{"job_id": "a", "first_version": "a", "status": "done", "made_at": "2026-10-12T10:00:00", "timed_review_minutes": 3.0},
                        {"job_id": "b", "first_version": "a", "status": "done", "made_at": "2026-10-12T11:00:00", "timed_review_minutes": 4.5}], [])
check("results maths: remakes' timed minutes are added", pv[0]["timed_review_min"] == 7.5, pv)
check("results maths: nothing timed is None, not 0",
      results.per_video([{"job_id": "c", "first_version": "c", "status": "done", "made_at": "2026-10-12T10:00:00"}], [])[0]["timed_review_min"] is None)
check("deleting a video deletes its timer file", alex.delete("/api/jobs/v3").status_code == 200 and not (OUT / "v3.review.json").exists())

# Small sessions count, and an acknowledged-but-lost response can be retried safely.
job("tiny", "alex")
r = alex.post("/api/jobs/tiny/review-time", json={"seconds": 2, "session_id": "tiny-one"})
check("a two-second session is nonzero", r.status_code == 200 and r.json()["review_minutes"] > 0, r.text)
r2 = alex.post("/api/jobs/tiny/review-time", json={"seconds": 2, "session_id": "tiny-one"})
check("retrying the same session does not count twice", r2.json()["review_minutes"] == r.json()["review_minutes"] and len(json.loads((OUT / "tiny.review.json").read_text())["sessions"]) == 1)
tr = admin.get("/api/admin/results").json()
tiny_group = results.summarise([{"job_id": "tiny-only", "first_version": "tiny-only", "status": "done", "owner": "alex", "made_at": "2026-10-12T10:00:00", "timed_review_minutes": 2 / 60}], [])["groups"][0]
check("short-session median and count stay nonzero", tiny_group["median_timed_review_min"] > 0 and tiny_group["timed_counted"] == 1, tiny_group)
check("short sessions reach report counts", next(x for x in tr["videos"] if x["job"] == "tiny")["timed_review_min"] > 0)
from unittest.mock import patch
original = (OUT / "tiny.review.json").read_bytes()
with patch.object(Path, "replace", side_effect=OSError("test write failure")):
    r = alex.post("/api/jobs/tiny/review-time", json={"seconds": 8, "session_id": "tiny-two"})
check("failed atomic save preserves prior sessions and reports failure", r.status_code == 503 and (OUT / "tiny.review.json").read_bytes() == original)

(OUT / "tiny.review.json").write_text("{corrupt", encoding="utf-8")
r = alex.post("/api/jobs/tiny/review-time", json={"seconds": 8, "session_id": "tiny-three"})
check("unreadable saved sessions are not overwritten", r.status_code == 503 and (OUT / "tiny.review.json").read_text() == "{corrupt")
(OUT / "tiny.review.json").write_bytes(original)

print("Remembered settings in plain words")
check("nothing saved: an empty list", alex.get("/api/prefs").json()["saved"] == [])
res = alex.post("/api/jobs/v2/feedback", json={"changes": {"caption_size": 92, "remove_fillers": False, "music_db": -18},
                                               "remember": True, "comment": "", "remake": False})
words = alex.get("/api/prefs").json()["saved"]
check("saved via the existing remember route are listed in words",
      "Bigger captions" in words and 'Keep the "um"s and "uh"s' in words, (res.status_code, res.text[:200], words))
check("the words are plain (no setting names)", not any("_" in w or "caption_size" in w for w in words), words)
check("someone else's saved settings are not shown", other.get("/api/prefs").json()["saved"] == [])
check("one-tap reset uses the existing route", alex.delete("/api/prefs").json() == {"ok": True} and alex.get("/api/prefs").json()["saved"] == [])
sw = review.saved_words({"caption_size": 60, "caption_margin": 0.5, "pause_gap": 1.0, "framing": "fit", "denoise": True,
                         "music_on": False, "captions_on": False, "skip_dead_start": False, "stabilize": False,
                         "caption_fixes": {"Alexe": "Alex", "a": "b", "c": "d", "e": "f"}})
for want in ("Smaller captions", "Captions higher up", "More pauses kept (more natural)", "Show the whole frame", "Remove background noise",
             "No music", "Captions off", "Keep the start of each clip", "Always start each clip at the beginning",
             'Spell "Alexe" as "Alex" in captions', "1 more word fix for captions"):
    check(f"plain words: {want}", want in sw, sw)
check("the normal settings say nothing", review.saved_words({"caption_size": 78, "music_db": -22.0, "denoise": False}) == [])

print("First-run tips")
check("a new account with no videos sees the tips", newbie.get("/api/me").json()["show_tips"] is True)
check("an account that already made videos does not", alex.get("/api/me").json()["show_tips"] is False)
check("closing them is saved", newbie.post("/api/tips-seen").json() == {"ok": True})
check("never again after, on any device", newbie.get("/api/me").json()["show_tips"] is False
      and client("newbie", "newbie-pass-1234").get("/api/me").json()["show_tips"] is False)
check("closing them for one account does not hide them for another",
      (auth.set_user("fresh", "fresh-pass-1234") or True) and client("fresh", "fresh-pass-1234").get("/api/me").json()["show_tips"] is True)

print()
tips_file = OUT / "tips_seen.json"
tips_before = tips_file.read_bytes()
tips_file.write_text("{corrupt", encoding="utf-8")
r = newbie.post("/api/tips-seen")
check("unreadable tips cannot erase other accounts' dismissals", r.status_code == 503 and tips_file.read_text() == "{corrupt")
tips_file.write_bytes(tips_before)
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: " + "; ".join(fails))
sys.exit(1 if fails else 0)
