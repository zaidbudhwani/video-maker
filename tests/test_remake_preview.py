"""The plain preview before a remake: what she typed is shown back as "I will / I'll keep / I can't", a vague note gets ONE
question instead of a guess, and the remake starts only after Confirm, applying exactly what the preview showed.

Run: venv\\Scripts\\python tests\\test_remake_preview.py
No real Claude calls: the key is blanked and the AI reader is a fake client that returns a canned answer. The website
part uses an in-process copy of the site with a throwaway data folder; the video maker is replaced by a recorder."""
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = Path(tempfile.mkdtemp(prefix="alex-preview-"))
os.environ.update(ALEX_DATA_DIR=str(DATA), ALEX_HOSTED="1", ADMIN_USER="tester", ADMIN_PASSWORD="admin-pass-123",
                  ANTHROPIC_API_KEY="", OPENROUTER_API_KEY="", APP_PASSWORD="", ALEX_AUTO_SLIM="0")

from video_tool import music, settings as S   # noqa: E402
from web import interpret as I   # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {detail}"))
    if not cond:
        FAILS.append(name)


I._client = lambda: None   # never a real key
D = dict(S.DEFAULTS)

# ---------- the follow-up question ----------
VAGUE = [
    ("fix the captions", "captions", {"Bigger": {"caption_size": 88}, "Smaller": {"caption_size": 68}}),
    ("the captions look weird", "captions", None),
    ("the music isn't right", "music", {"Quieter": {"music_db": -26.0}, "Louder": {"music_db": -18.0}, "No music": {"music_on": False}}),
    ("can you change the pauses", "pauses", {"Cut more": {"pause_gap": 0.45}, "Keep more": {"pause_gap": 0.95}}),
    ("bigger captions, and fix the music", "music", None),
]
for note, group, want in VAGUE:
    q = I.follow_up(note, D)
    got = {o["label"]: o["changes"] for o in q["options"]} if q else None
    check(f"vague: {note!r} gets one question about the {group}", q and q["group"] == group and (want is None or got == want), q)
CLEAR = ["make the captions bigger", "the captions are too small", "turn the music down", "no music", "cut the pauses tighter",
         "change Alexe to Alex in the captions", "add a zoom transition", "use a different song", "looks great, thanks",
         "captions off", ""]
for note in CLEAR:
    check(f"clear or impossible: {note!r} gets no question", I.follow_up(note, D) is None, I.follow_up(note, D))
check("an answered question is not asked again", I.follow_up("fix the captions", D, answered=["captions"]) is None)
q = I.follow_up("fix the captions", {**D, "caption_size": 110})
check("an answer that can't change anything is not offered (captions already biggest)", q is None, q)

# ---------- the words ----------
check("plan words: bigger captions", I.plan_words({"caption_size": 88}, D) == ["make the captions bigger"])
check("plan words: several, in her words", I.plan_words({"caption_margin": 0.42, "pause_gap": 0.45, "auto_order": False,
      "caption_fixes": {"Alexe": "Alex"}, "music_on": False}, D) == ["move the captions higher", "cut more of the pauses (snappier)",
      "keeping your clip order", 'change "Alexe" to "Alex" in the captions', "take the music out"],
      I.plan_words({"caption_margin": 0.42, "pause_gap": 0.45, "auto_order": False, "caption_fixes": {"Alexe": "Alex"}, "music_on": False}, D))
check("plan keeps: clip order and music stay when untouched", I.plan_keeps({"caption_size": 88}, D, True)
      == ["your clips and your voice", "your clip order", "the same music"], I.plan_keeps({"caption_size": 88}, D, True))
check("plan keeps: clip order not promised when the order setting changes", "your clip order" not in I.plan_keeps({"auto_order": True}, D))

# ---------- the website ----------
from fastapi.testclient import TestClient   # noqa: E402
from web import app as W, auth   # noqa: E402

READS = []
real_read = I.interpret_full
W.interpret_full = lambda note, cur: READS.append(note) or real_read(note, cur)
STARTS = []
W._start = lambda job_id, folder, user, extra=(): STARTS.append(list(extra))
auth.set_user("alex", "alex-pass-1234")
auth.set_user("sam", "sam-pass-12345")
alex = TestClient(W.app, base_url="http://testserver")
sam = TestClient(W.app, base_url="http://testserver")
assert alex.post("/api/login", json={"username": "alex", "password": "alex-pass-1234"}).status_code == 200
assert sam.post("/api/login", json={"username": "sam", "password": "sam-pass-12345"}).status_code == 200
src = DATA / "clips"
src.mkdir()
JID = "web-p-1"
(W.OUT / JID).mkdir(parents=True)
(W.OUT / JID / "report.json").write_text(json.dumps({"job_id": JID, "mode": "voice", "music": "x.mp3", "settings": D}))
(W.OUT / f"{JID}.owner").write_text("alex")
(W.OUT / f"{JID}.source").write_text(str(src))


def preview(note, changes=None, answered=None, who=None):
    return (who or alex).post(f"/api/jobs/{JID}/feedback/preview", json={"comment": note, "changes": changes or {}, "answered": answered or []})


def log_lines():
    try:
        return W.FEEDBACK_LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


READS.clear()
r = preview("make the captions bigger and add a zoom transition")
j = r.json()
check("preview: plain words for what will change", r.status_code == 200 and j["will"] == ["make the captions bigger"], r.text)
check("preview: what it can't do", j.get("cant") == ["Add a zoom transition"], j.get("cant"))
check("preview: what stays the same", "your clip order" in j.get("keep", []) and "the same music" in j.get("keep", []), j.get("keep"))
check("preview: a dry run (no remake started, nothing logged for the team, no new video)",
      not STARTS and not log_lines() and sorted(p.name for p in W.OUT.iterdir() if p.is_dir() and p.name.startswith("web-")) == [JID])

token = j["plan"]
STARTS.clear()
r = alex.post(f"/api/jobs/{JID}/feedback", json={"comment": "make the captions bigger and add a zoom transition", "changes": {},
                                                 "remake": True, "plan": token})
over = json.loads(Path(STARTS[0][STARTS[0].index("--settings") + 1]).read_text()) if STARTS else {}
check("Confirm: the remake starts with exactly what the preview showed", r.status_code == 200 and over.get("caption_size") == 88, (r.text, over))
check("Confirm: the note was read once, not again", READS == ["make the captions bigger and add a zoom transition"], READS)
check("Confirm: the part it can't do still goes to the team", json.loads(log_lines()[-1])["unsupported"] == ["Add a zoom transition"], log_lines()[-1:])
READS.clear()
STARTS.clear()
alex.post(f"/api/jobs/{JID}/feedback", json={"comment": "make the captions bigger and add a zoom transition", "remake": True, "plan": token})
check("a preview token works once (a second Confirm reads the note again)", READS == ["make the captions bigger and add a zoom transition"], READS)

t2 = preview("make the captions bigger").json()["plan"]
READS.clear()
STARTS.clear()
alex.post(f"/api/jobs/{JID}/feedback", json={"comment": "make the captions smaller", "remake": True, "plan": t2})
over = json.loads(Path(STARTS[0][STARTS[0].index("--settings") + 1]).read_text()) if STARTS else {}
check("a token for a different note is not used (the new note is read)", READS == ["make the captions smaller"] and over.get("caption_size") == 68, (READS, over))
t3 = preview("make the captions bigger").json()["plan"]
r = sam.post(f"/api/jobs/{JID}/feedback/preview", json={"comment": "x"})
check("someone else's video: no preview", r.status_code == 404, r.status_code)


class FakeClient:   # the AI reader guessing a size for a vague note
    def __init__(self, answer):
        self.answer, self.calls, self.messages = answer, [], self

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=10),
                               content=[SimpleNamespace(type="tool_use", input=dict(self.answer))])


fake = FakeClient({"caption_size": 88, "remove_fillers": False, "unsupported": []})
I._client = lambda: fake
j = preview("fix the captions and keep the ums").json()
check("vague note: one question instead of the AI's guess", j["question"] and j["question"]["text"] == "Should the captions be bigger or smaller?"
      and "caption_size" not in j["changes"], j)
check("vague note: the clear part is still planned", j["changes"].get("remove_fillers") is False and "keep the ums and uhs" in " ".join(j["will"]), j)
STARTS.clear()
alex.post(f"/api/jobs/{JID}/feedback", json={"comment": "fix the captions and keep the ums", "remake": True, "plan": j["plan"]})
over = json.loads(Path(STARTS[0][STARTS[0].index("--settings") + 1]).read_text()) if STARTS else {}
check("vague note: Confirm never applies the dropped guess", "caption_size" not in over and over.get("remove_fillers") is False, over)
opt = next(o for o in j["question"]["options"] if o["label"] == "Smaller")
j = preview("fix the captions and keep the ums", changes=opt["changes"], answered=[j["question"]["group"]]).json()
check("answering the question: no second question, and her answer is planned", j["question"] is None
      and j["changes"].get("caption_size") == 68 and "make the captions smaller" in j["will"], j)
I._client = lambda: None

j = preview("use a different song").json()
if music.tracks():
    check("'a different song': pointed to the music choice, not 'can't'", j["cant"] == [] and j["guide"] == [W.MUSIC_GUIDE] and j["will"] == [], j)
else:   # no tracks copied onto this computer: there is nothing to choose, so it says so plainly
    check("'a different song' with no tracks installed: said plainly it can't", j["cant"] == ["Use a different song"] and j["will"] == [], j)
W.jobs[JID] = {"status": "running"}
r = preview("make the captions bigger")
check("a video still being made: no preview", r.status_code == 409, r.status_code)
W.jobs.pop(JID)
r = preview("", changes={"music_track": "not-installed.mp3"})
check("a track that isn't installed: a plain 400", r.status_code == 400, r.text)

print(f"{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED'}")
sys.exit(1 if FAILS else 0)
