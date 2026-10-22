""""Change this shot" on the website side: the /shots rows, the pin in a remake, pins kept and merged across remakes,
pins following a clip when she changes the clip order. No network, no AI, no video made (the job start is recorded).

    venv\\Scripts\\python tests\\test_shot_web.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = Path(tempfile.mkdtemp(prefix="alex-shot-web-"))
os.environ.update(ALEX_DATA_DIR=str(DATA), ALEX_HOSTED="1", ADMIN_USER="tester", ADMIN_PASSWORD="admin-pass-123",
                  ANTHROPIC_API_KEY="", OPENROUTER_API_KEY="", APP_PASSWORD="", ALEX_AUTO_SLIM="0", HELP_CONTACT="x")

from fastapi.testclient import TestClient   # noqa: E402

import web.app as A   # noqa: E402
from web import auth   # noqa: E402
from web.interpret import clean_values, describe   # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        fails.append(name)


auth.set_user("alex", "alex-pass-1234")
auth.set_user("other", "other-pass-1234")
c, o = TestClient(A.app, base_url="http://testserver"), TestClient(A.app, base_url="http://testserver")
check("signs in", c.post("/api/login", json={"username": "alex", "password": "alex-pass-1234"}).status_code == 200)
o.post("/api/login", json={"username": "other", "password": "other-pass-1234"})

OUT, UP = A.OUT, A.UPLOADS
src = UP / "web-src"
src.mkdir(parents=True)
for n in (1, 2, 3):
    (src / f"{n:02d}.mp4").write_bytes(b"x")
(src / "voice.m4a").write_bytes(b"x")
(src / "original_names.json").write_text(json.dumps(["Front.mov", "Kitchen.mov", "Deck.mov", "memo.m4a"]), encoding="utf-8")

JOB = "web-shot-1"
d = OUT / JOB
(d / "match").mkdir(parents=True)
tl = [{"clip": "01.mp4", "start": 0.0, "end": 4.0}, {"clip": "02.mp4", "start": 4.0, "end": 8.0},
      {"clip": "03.mp4", "start": 8.0, "end": 12.0, "pinned": True}]
report = {"job_id": JOB, "mode": "voice", "length": 12.0, "parts": [{"file": "final.mp4", "length": 12.0}], "timeline": tl,
          "settings": {"caption_fixes": {"Alexe": "Alex"}, "pins": [{"start": 8.5, "end": 11.0, "clip": 3}]}}
(d / "report.json").write_text(json.dumps(report), encoding="utf-8")
# 3 phrases; scores[clip][frame][phrase]
phrases = ["Hi, I'm Alexe, this is the front.", "Here's the kitchen.", "And the deck out back."]
times = [[0.0, 4.0], [4.0, 8.0], [8.0, 12.0]]
scores = [[[9, 2, 1], [8, 2, 1]],      # clip 1 shows phrase 1 well
          [[2, 5, 2], [2, 6, 2]],      # clip 2: so-so for the kitchen
          [[1, 2, 2], [1, 3, 3]]]      # nobody shows the deck
(d / "match" / "scores.json").write_text(json.dumps({"phrases": phrases, "phrase_times": times, "scores": scores,
                                                     "frame_times": [[0.5, 2.0], [0.5, 2.0], [0.5, 2.0]]}), encoding="utf-8")
(OUT / f"{JOB}.owner").write_text("alex", encoding="utf-8")
(OUT / f"{JOB}.source").write_text(str(src), encoding="utf-8")
(OUT / f"{JOB}.settings.json").write_text(json.dumps({"pins": [{"start": 8.5, "end": 11.0, "clip": 3}], "music_on": False}), encoding="utf-8")

# ---------- /shots ----------
r = c.get(f"/api/jobs/{JOB}/shots")
rows = r.json().get("rows", []) if r.status_code == 200 else []
check("/shots: one row per phrase", r.status_code == 200 and len(rows) == 3, r.text)
check("/shots: the clip on screen for each phrase", [x["clip"] for x in rows] == [1, 2, 3], rows)
check("/shots: match words from the saved scores", [x["match"] for x in rows] == ["possible match", "not sure", "no footage for this"], rows)
check("/shots: raw AI scores remain transparent", rows[0]["shown_score"] == 9 and rows[0]["best_score"] == 9, rows[0])
check("/shots: her word fixes are applied to the words", rows and "Alex," in rows[0]["text"] and "Alexe" not in rows[0]["text"], rows[:1])
check("/shots: her earlier choice is marked", [x["pinned"] for x in rows] == [False, False, True], rows)
check("/shots: someone else's video is not found", o.get(f"/api/jobs/{JOB}/shots").status_code == 404)
(d / "match" / "scores.json").rename(d / "match" / "scores.off")
words = [{"word": w, "start": i * 1.0, "end": i * 1.0 + 0.9} for i, w in enumerate("Hello there. This is the kitchen. Bye now.".split())]
(d / "words_trimmed.json").write_text(json.dumps(words), encoding="utf-8")
rows2 = c.get(f"/api/jobs/{JOB}/shots").json()["rows"]
check("/shots without scores: rows from her words, no match word", len(rows2) == 3 and all(x["match"] is None and x["shown_score"] is None and x["best_score"] is None for x in rows2), rows2)
(d / "match" / "scores.off").rename(d / "match" / "scores.json")
rep2 = {**report, "mode": "own_audio"}
(d / "report.json").write_text(json.dumps(rep2), encoding="utf-8")
check("/shots: none for a talking-to-camera video", c.get(f"/api/jobs/{JOB}/shots").json()["rows"] == [])
(d / "report.json").write_text(json.dumps(report), encoding="utf-8")

# ---------- the remake with a pin ----------
started = []
A._start = lambda *a, **k: started.append(a)
r = c.post(f"/api/jobs/{JOB}/feedback", json={"changes": {"pins": [{"start": 4.0, "end": 8.0, "clip": 1}]}, "remake": True})
check("feedback with a pin starts a remake", r.status_code == 200 and r.json().get("job_id") and started, r.text)
check("...and says what it does in plain words", "Clip 1 at 0:04" in r.json().get("applied", []), r.json())
over = json.loads((OUT / f"{started[-1][0]}.settings.json").read_text(encoding="utf-8")) if started else {}
check("remake keeps her earlier pin and adds the new one",
      over.get("pins") == [{"start": 4.0, "end": 8.0, "clip": 1}, {"start": 8.5, "end": 11.0, "clip": 3}], over)
check("remake keeps her other one-off settings", over.get("music_on") is False, over)
check("remake reuses the old version (transcript, scores)", "--reuse" in started[-1][3] if started else False)
r = c.post(f"/api/jobs/{JOB}/feedback", json={"changes": {"pins": [{"start": 9.0, "end": 10.0, "clip": 2}]}, "remake": True})
over = json.loads((OUT / f"{started[-1][0]}.settings.json").read_text(encoding="utf-8"))
check("a new choice for the same moment replaces the old one", over.get("pins") == [{"start": 9.0, "end": 10.0, "clip": 2}], over)
check("pins are not remembered for future videos", "pins" not in json.loads(A._prefs_file("alex").read_text(encoding="utf-8"))
      if A._prefs_file("alex").exists() else True)
r = c.post(f"/api/jobs/{JOB}/feedback", json={"changes": {"pins": [{"start": "x", "clip": 1}]}, "remake": True})
check("junk pins: nothing to remake", r.status_code == 200 and not r.json().get("job_id"), r.text)
check("clean_values: junk pins dropped, good kept", clean_values({"pins": [{"start": 1, "end": 2, "clip": 2}, "bad"]})
      == {"pins": [{"start": 1.0, "end": 2.0, "clip": 2}]} and clean_values({"pins": "clip 2"}) == {})
check("describe() names the pin", describe({"pins": [{"start": 65, "end": 70, "clip": 2}]}) == ["Clip 2 at 1:05"])

# ---------- clip order: the pin follows the clip ----------
r = c.post(f"/api/jobs/{JOB}/reorder", json={"order": [3, 1, 2]})
over = json.loads((OUT / f"{started[-1][0]}.settings.json").read_text(encoding="utf-8"))
check("reorder: her pinned clip 3 is now clip 1", r.status_code == 200 and over.get("pins") == [{"start": 8.5, "end": 11.0, "clip": 1}], over)

print()
print(f"FAILED: {', '.join(fails)}" if fails else "All shot web tests passed.")
sys.exit(1 if fails else 0)
