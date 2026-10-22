"""Film-for-the-voice-over shot list (video_tool/shotlist.py and /api/shotlist). No network, no real AI: a fake Claude client.
Run:  venv\\Scripts\\python tests\\test_shotlist.py"""
import json
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = Path(tempfile.mkdtemp(prefix="alex-shotlist-"))
os.environ.update(ALEX_DATA_DIR=str(DATA), ALEX_HOSTED="1", ADMIN_USER="tester", ADMIN_PASSWORD="admin-pass-123",
                  ANTHROPIC_API_KEY="", OPENROUTER_API_KEY="", APP_PASSWORD="", ALEX_AUTO_SLIM="0")

from video_tool import shotlist   # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        fails.append(name)


ROOM = shotlist.HOW["room"][0]
OUT_ = shotlist.HOW["outside"][0]
DET = shotlist.HOW["detail"][0]

# ---------- the no-key list: fixed words -> exact list
TEXT = ("Come on in. This home has a large deck off the back, and the big open kitchen has a granite island. "
        "The living room has a fireplace. Back to the deck one more time.")
want = [
    {"phrase": "large deck", "film": OUT_.format(it="deck"), "seconds": 6},
    {"phrase": "big open kitchen", "film": ROOM.format(it="kitchen"), "seconds": 6},
    {"phrase": "granite island", "film": DET.format(it="island"), "seconds": 4},
    {"phrase": "living room", "film": ROOM.format(it="living room"), "seconds": 6},
    {"phrase": "fireplace", "film": DET.format(it="fireplace"), "seconds": 4},
]
got = shotlist.plain_shots(TEXT)
check("plain list: one shot per thing she names, in her order, her own words, repeats once", got == want,
      json.dumps(got, indent=1))
check("plain list: the deck example reads the way the brief says",
      got and got[0]["film"].startswith("Film the deck from far enough back"), got[:1])
r = shotlist.shot_list(TEXT, client=None)
check("no client: source is plain, the same list, filming tips included",
      r["source"] == "plain" and r["shots"] == want and len(r["tips"]) >= 3 and r["warnings"] == [], r)
check("nothing to film -> an empty list, no error", shotlist.shot_list("Hi everybody, thanks for watching.", None)["shots"] == [])
check("empty text -> an empty list", shotlist.shot_list("   ", None)["shots"] == [])
fh = shotlist.shot_list("It has a family-friendly yard and a kitchen.", None)
check("Fair Housing word in her words: a plain note, and the shot keeps only the thing",
      [s["phrase"] for s in fh["shots"]] == ["yard", "kitchen"] and len(fh["warnings"]) == 1
      and "family-friendly" in fh["warnings"][0] and "describe the place" in fh["warnings"][0], fh)
check("the plain list never adds a number she did not say",
      not any(ch.isdigit() for s in got for ch in s["film"]), got)
check("no-key default path (client='auto' with no key) still works", shotlist.shot_list(TEXT)["shots"] == want)


# Explicit negatives and malformed narration are not filmed.
for text, expected in [("There is no pool or fireplace, but the kitchen is open.", ["kitchen"]),
                       ("Without a deck. The kitchen has an island.", ["kitchen", "island"])]:
    check("negative features are omitted and affirmative clauses survive",
          [x["film"] for x in shotlist.plain_shots(text)] ==
          [shotlist.HOW[shotlist.KIND[n]][0].format(it=n) for n in expected], text)

# ---------- the Claude path, with a fake client
class Fake:
    def __init__(self, shots=None, boom=False):
        self.calls, self.shots, self.boom = [], shots, boom
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self.boom:
            raise RuntimeError("no credit")
        block = types.SimpleNamespace(type="tool_use", input={"shots": self.shots})
        return types.SimpleNamespace(content=[block])


good = [
    {"phrase": "large deck", "film": "Stand in the doorway to the deck and film all of it.", "seconds": 5},
    {"phrase": "big open kitchen", "film": "Film the kitchen from the far corner.", "seconds": 6},
    {"phrase": "hot tub", "film": "Film the hot tub.", "seconds": 4},                          # she never said it: invented
    {"phrase": "granite island", "film": "Film the island. It seats 6 people.", "seconds": 4},   # a number she never said
    {"phrase": "living room", "film": "Film this stunning room.", "seconds": 5},                 # hype word
    {"phrase": "fireplace", "film": "Film the fireplace, perfect for families.", "seconds": 5},  # Fair Housing
    {"phrase": "Large deck", "film": "Again.", "seconds": 5},                                     # repeat
    {"phrase": "fireplace", "film": "Film the fireplace up close.", "seconds": 99},               # seconds clamped
]
fake = Fake(good)
r = shotlist.shot_list(TEXT, client=fake)
check("Claude path: one call, her words in the prompt, tool forced", len(fake.calls) == 1 and TEXT in json.dumps(fake.calls[0])
      and fake.calls[0]["tool_choice"]["name"] == "shot_list")
check("Claude path: keeps only shots that use her words and pass every check",
      r["source"] == "ai" and [s["phrase"] for s in r["shots"]] == ["large deck", "big open kitchen", "fireplace"], r["shots"])
check("Claude path: seconds kept to 3-10", [s["seconds"] for s in r["shots"]] == [5, 6, 10], r["shots"])
r = shotlist.shot_list(TEXT, client=Fake(boom=True))
check("Claude fails -> the plain list", r["source"] == "plain" and r["shots"] == want, r)
r = shotlist.shot_list(TEXT, client=Fake([{"phrase": "hot tub", "film": "Film the hot tub.", "seconds": 5}]))
check("Claude answers only invented things -> the plain list", r["source"] == "plain" and r["shots"] == want, r)
r = shotlist.shot_list("It has a family-friendly yard.", client=Fake([{"phrase": "yard", "film": "Film the yard.", "seconds": 5}]))
check("Claude path still gives the Fair Housing note", r["source"] == "ai" and len(r["warnings"]) == 1, r)

r = shotlist.shot_list("The kitchen.", client=Fake([
    {"phrase": "kitchen", "film": "Zoom into the marble counters beside the pool.", "seconds": 5}]))
check("AI advice cannot add features or effects", r["source"] == "ai" and
      r["shots"][0]["film"] == ROOM.format(it="kitchen") and "pool" not in r["shots"][0]["film"], r)
r = shotlist.shot_list("The kitchen and deck.", client=Fake([
    {"phrase": "deck", "film": "Film the deck.", "seconds": 5},
    {"phrase": "kitchen", "film": "Film the kitchen.", "seconds": 5}]))
check("AI shots follow narration order even if the response does not", [s["phrase"] for s in r["shots"]] == ["kitchen", "deck"], r)
from unittest.mock import patch
with patch("video_tool.ai.client", side_effect=RuntimeError("bad setup")):
    check("client initialization failure keeps the no-key fallback", shotlist.shot_list(TEXT)["shots"] == want)

# ---------- the website
from fastapi.testclient import TestClient   # noqa: E402
from web import app as W, auth   # noqa: E402

auth.set_user("alex", "alex-pass-1234")
auth.set_user("other", "other-pass-1234")
alex, other, anon = (TestClient(W.app, base_url="http://testserver") for _ in range(3))
alex.post("/api/login", json={"username": "alex", "password": "alex-pass-1234"})
other.post("/api/login", json={"username": "other", "password": "other-pass-1234"})
r = alex.post("/api/shotlist", json={"text": TEXT})
check("/api/shotlist: her typed words -> the list (no key: plain)", r.status_code == 200 and r.json()["shots"] == want
      and r.json()["source"] == "plain", r.text[:300])
check("/api/shotlist: empty words -> a plain-English 400", alex.post("/api/shotlist", json={"text": " "}).status_code == 400)
check("/api/shotlist: needs sign-in", anon.post("/api/shotlist", json={"text": TEXT}).status_code == 401)
job = "20261020-120000-abcd"
(W.OUT / job).mkdir(parents=True, exist_ok=True)
(W.OUT / job / "transcript.txt").write_text("Here is the screened porch and the garage.", encoding="utf-8")
(W.OUT / f"{job}.owner").write_text("alex", encoding="utf-8")
src = alex.get("/api/shotlist/sources").json()
check("/api/shotlist/sources: lists her earlier video with words", [v["job_id"] for v in src["videos"]] == [job], src)
check("/api/shotlist/sources: not someone else's", other.get("/api/shotlist/sources").json()["videos"] == [])
r = alex.post("/api/shotlist", json={"job_id": job})
check("/api/shotlist: reuse an earlier voice-over", r.status_code == 200
      and [s["phrase"] for s in r.json()["shots"]] == ["screened porch", "garage"], r.text[:300])
check("/api/shotlist: someone else's video is not found", other.post("/api/shotlist", json={"job_id": job}).status_code == 404)

for value in ([], {}, 42, False, None):
    check("invalid narration type returns 400", alex.post("/api/shotlist", json={"text": value}).status_code == 400, value)
check("path traversal is refused", alex.post("/api/shotlist", json={"job_id": "../other"}).status_code == 404)
check("valid ducking upload choice survives validation", W._clean_extras({"music_duck": True})["music_duck"] is True)
check("default upload does not turn ducking on", "music_duck" not in W._clean_extras({}))
try:
    W._clean_extras({"music_duck": "false"})
    check("string ducking value refused", False)
except W.HTTPException as e:
    check("string ducking value refused", e.status_code == 400)

init = alex.post("/api/uploads", json={"clips": [{"name": "clip.mp4", "size": 100}], "music_duck": True})
check("upload persists the ducking choice", init.status_code == 200 and
      W._pending(init.json()["job_id"])["extras"].get("music_duck") is True, init.text[:200])
from video_tool import settings as S
S.remember({"music_duck": True}, W._prefs_file("alex"))
check("upload preferences return her remembered ducking choice", alex.get("/api/prefs").json()["music_duck"] is True)
# Concurrent requests share one generation, including a fresh lookup inside the lock.
import threading, time
from concurrent.futures import ThreadPoolExecutor
W.SHOTS_CACHE.clear()
calls = []
def fake_shots(text):
    calls.append(text)
    time.sleep(0.05)
    return {"shots": [], "source": "plain"}
with patch.object(shotlist, "shot_list", side_effect=fake_shots):
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(W._cached_shots, [("alex", "kitchen")] * 2))
check("concurrent same-user same-text requests generate once", len(calls) == 1 and answers[0] == answers[1], calls)

print(f"\n{'ALL PASS' if not fails else str(len(fails)) + ' FAILED'}")
sys.exit(1 if fails else 0)
