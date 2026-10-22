"""Fixer lane regression tests: one check for each review finding that was fixed.
Run:  venv/Scripts/python tests/test_fixer.py
Throwaway data folder, FastAPI's TestClient (in-process). No Claude call and no video is made (the render start is
replaced by a recorder where a render would begin)."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = Path(tempfile.mkdtemp(prefix="alex-fixer-"))
os.environ.update(ALEX_DATA_DIR=str(DATA), ALEX_HOSTED="1", ADMIN_USER="tester", ADMIN_PASSWORD="admin-pass-123",
                  ANTHROPIC_API_KEY="", APP_PASSWORD="", ALEX_AUTO_SLIM="0")

from fastapi.testclient import TestClient   # noqa: E402

from web import app as W, auth   # noqa: E402
from video_tool import captions, local_copy, own_audio, settings, writer   # noqa: E402
from video_tool.config import FFMPEG   # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {str(detail)[:300]}"))
    if not ok:
        fails.append(name)


def client(user=None, pw=None, base="http://testserver"):
    c = TestClient(W.app, base_url=base)
    if user:
        assert c.post("/api/login", json={"username": user, "password": pw}).status_code == 200
    return c


auth.set_user("alex", "alex-pass-1234")
alex, admin = client("alex", "alex-pass-1234"), client("tester", "admin-pass-123")
OUT = W.OUT

# ======================= retakes: parallel lines are not retakes =======================
def words_from(text, start=0.0, step=0.4, gap=0.3):
    out, t = [], start
    for tok in text.split():
        out.append({"word": tok, "start": round(t, 3), "end": round(t + step * 0.8, 3), "p": 0.95})
        t += step
        if tok.endswith((".", "?", "!")):
            t += gap
    return out


PARALLEL = [
    ("You need to check the roof every year.", "You need to check the gutters every year."),
    ("This bedroom has a ceiling fan and a big window.", "This bedroom has a ceiling fan and a big closet."),
    ("I have three bedrooms in this house today.", "I have four bedrooms in this house today."),
    ("Add one cup of flour to the big bowl now.", "Add two cups of flour to the big bowl now."),
    ("The first step is to turn off the water here.", "The second step is to turn off the water here."),
    ("The kitchen was updated in 2015 with new cabinets.", "The kitchen was updated in 2018 with new cabinets."),
]
for a, b in PARALLEL:
    check(f"parallel lines are not alike: {a[:34]} / {b[-22:]}", own_audio._alike(a, b) is False)
check("a real repeat is still alike", own_audio._alike("This kitchen has the original oak cabinets and a big island.",
                                                     "This kitchen has the original oak cabinets and a big island."))
check("a repeat with a stutter word is still alike", own_audio._alike("So this kitchen has the original oak cabinets and a big island.",
                                                                    "This kitchen has the original oak cabinets and a big island."))
check("a broken-off first take is a retake", own_audio._alike("This kitchen has the original oak cabinets and",
                                                            "This kitchen has the original oak cabinets and a big island."))
w = words_from("Tip one. You need to check the roof every year. You need to check the gutters every year. Tip two is the furnace.")
check("find_retakes keeps both parallel lines", own_audio.find_retakes(w) == [], own_audio.find_retakes(w))
w = words_from("The deck was rebuilt last summer with new boards. The deck was rebuilt last summer with new boards. Then the stairs.")
check("find_retakes still cuts a true repeat", len(own_audio.find_retakes(w)) == 1)

# ======================= Fair Housing: street names, recipe label =======================
check("fh: Church St is a street", writer.fh_hits("Home Tour: 220 Church St, Walford") == [])
check("fh: School St is a street", writer.fh_hits("Close to School St and Main") == [])
check("fh: Senior Dr is a street", writer.fh_hits("Home Tour: 14 Senior Dr") == [])
check("fh: a church is still flagged", "church" in writer.fh_hits("Next to a church"))
check("fh: schools still flagged", "school" in writer.fh_hits("Close to schools and parks"))
check("fh: the safe at the back is a box", writer.fh_hits("Check the safe at the back of the closet") == [])
check("fh: a safe place is a claim", "safe" in writer.fh_hits("A safe place to live"))
check("is_property: recipe about a smoothie is relaxed", writer.is_property("recipe", "general", "blend the banana with milk") is False)
check("is_property: a house video labelled recipe is still checked",
      writer.is_property("recipe", "general", "this home has a bedroom and a bathroom and a garage") is True
      and writer.is_property("recipe", "property_or_location", "x") is True)

# ======================= no-key copy =======================
tips = ("So I was at an appraisal last week. He goes, can I tell you exactly dollar amount you will get? "
        "The brazer is standing in my living room and he wants a number. Appraisals are opinions of value. "
        "Keep that in mind when you are planning.")
c = local_copy.make_copy(tips, unsure=["brazer"])
check("copy: no quoted speech in the hook", not local_copy.QUOTED.match(c["hook"]) and "goes" not in c["hook"].lower(), c["hook"])
check("copy: no sentence with an unsure word in the titles", all("brazer" not in t.lower() for t in c["titles"]), c["titles"])
check("copy: an unsure word is not in the hook", "brazer" not in c["hook"].lower())
c2 = local_copy.make_copy("The brazer is standing in my living room. Okay.", unsure=["brazer"])
check("copy: when nothing else is usable, a note asks her to check the word",
      any("brazer" in n for n in c2["review_notes"]), c2["review_notes"])
fb = local_copy.make_copy("Here is one thing to know about this house. It has three bedrooms and a big yard. The kitchen is new. "
                          "Call me to see it.")["captions"]["facebook_reels"]
check("copy: Facebook caption is two sentences plus the closing line, not the whole description",
      fb.count(".") <= 4 and "kitchen is new" not in fb, fb)
check("checklist: no brokerage line while no disclosure is set", all("brokerage" not in x for x in writer.checklist(False))
      if not writer.disclosure_text() else True)

# ======================= captions: 3-row captions sink one row =======================
settings.configure({})
ws = words_from("one two three four five six seven eight nine ten", step=0.1)
d = Path(tempfile.mkdtemp(dir=DATA))
_, lines = captions.write_ass([{**x, "end": x["end"]} for x in ws], d, "t.ass")
ass = (d / "t.ass").read_text(encoding="utf-8")
dial = [l for l in ass.splitlines() if l.startswith("Dialogue")]
margins = [int(l.split(",", 9)[7]) for l in dial]
rows3 = [l for l in dial if l.count(r"\N") >= 2]
if rows3:
    mv = int(rows3[0].split(",", 9)[7])
    check("captions: a 3-row caption has its own lower margin, above the bottom 20%", 384 <= mv < int(1920 * settings.get("caption_margin")), mv)
else:
    check("captions: (no 3-row caption in this sample)", True)
check("captions: 1 and 2 row captions use the style margin", all(m == 0 for l, m in zip(dial, margins) if l.count(r"\N") < 2), margins)

# ======================= settings.remember =======================
pf = DATA / "prefs-test.json"
saved = settings.remember({"caption_size": 90, "hints": "Ellis Park Dr", "format": "recipe", "start_at_hook": True,
                           "match_twice": True, "pause_gap": 0.5, "caption_fixes": {"Alexe": "Alex"}}, pf)
p = json.loads(pf.read_text(encoding="utf-8"))
check("remember: keeps only look-and-feel keys", sorted(saved) == ["caption_size", "pause_gap"], saved)
check("remember: no per-video key in the file", not any(k in p for k in ("hints", "format", "start_at_hook", "match_twice")), p)
check("remember: only what she changed (new defaults still reach her)", "music_db" not in p and "caption_margin" not in p, p)
check("remember: caption fixes are kept", p["caption_fixes"] == {"Alexe": "Alex"})
pf.write_text(json.dumps({"format": "recipe", "hints": "x", "caption_size": 70}), encoding="utf-8")   # an older file
settings.remember({"pause_gap": 0.6}, pf)
p = json.loads(pf.read_text(encoding="utf-8"))
check("remember: an old per-video value already in the file is dropped", "format" not in p and "hints" not in p and p["caption_size"] == 70, p)
os.environ["ALEX_PREFS_FILE"] = str(pf)
check("remember: a later video starts as 'auto' again", settings.configure({})["format"] == "auto")
os.environ.pop("ALEX_PREFS_FILE")

# ======================= uploads: chunks, finish, limits =======================
MEDIA = DATA / "media"
MEDIA.mkdir()
clip = MEDIA / "good.mp4"
subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x240:d=1:r=15", "-f", "lavfi", "-i", "sine=f=300:d=1",
                "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)], check=True)
data = clip.read_bytes()
REAL_START = W._start
STARTS = []
W._start = lambda job_id, folder, user, extra=(): STARTS.append(job_id)


def begin(c=alex, n=1):
    r = c.post("/api/uploads", json={"clips": [{"name": f"Clip{i}.mov", "size": len(data)} for i in range(n)]})
    assert r.status_code == 200, r.text
    return r.json()


j = begin()
slot = j["slots"][0]["slot"]


def put(offset=0, body=data, c=alex):
    return c.put(f"/api/uploads/{j['job_id']}/{slot}?offset={offset}", content=body)


with ThreadPoolExecutor(8) as ex:
    codes = sorted(f.result().status_code for f in [ex.submit(put, 0, data[:3000]) for _ in range(8)])
path = W.UPLOADS / j["job_id"] / slot
check("chunks: the same piece sent 8 times at once is written once", path.stat().st_size == 3000 and codes.count(200) == 1, (codes, path.stat().st_size))
check("chunks: the others are told where the file ends", set(codes) <= {200, 409})
r = put(3000, data[3000:])
check("chunks: carries on from there and finishes whole", r.status_code == 200 and path.stat().st_size == len(data), r.text)
# a file already too long (an old double write) is started again instead of being stuck for ever
with open(path, "ab") as fh:
    fh.write(b"x" * 500)
r = alex.get(f"/api/uploads/{j['job_id']}")
check("chunks: an overlong file is reset so it can be sent again", r.json()["slots"][0]["received"] == 0 and not path.exists(), r.text)
put(0)
W.CHUNK_MAX, keep = 1000, W.CHUNK_MAX
j2 = begin()
r = alex.put(f"/api/uploads/{j2['job_id']}/{j2['slots'][0]['slot']}?offset=0", content=b"y" * 2000)
check("chunks: a too-big piece is refused (413)", r.status_code == 413, r.status_code)
W.CHUNK_MAX = keep

# Finish: four taps at once start one video
before = len(STARTS)
with ThreadPoolExecutor(4) as ex:
    rs = [f.result() for f in [ex.submit(lambda: alex.post(f"/api/uploads/{j['job_id']}/finish")) for _ in range(4)]]
check("finish: four taps at once all get the job id", all(r.status_code == 200 and r.json()["job_id"] == j["job_id"] for r in rs), [r.text for r in rs])
check("finish: but the video is started once", len(STARTS) - before == 1, STARTS)

# the real _start never starts the same video twice
ran = []
W._run = lambda *a, **k: ran.append(a[0])
orig_threads = threading.active_count()
REAL_START("web-twice-1", DATA, "alex")
REAL_START("web-twice-1", DATA, "alex")
time.sleep(0.2)
check("_start: a job id that is already known is not started again", ran == ["web-twice-1"], ran)

# ======================= bad numbers and bodies =======================
r = alex.post("/api/uploads", content=b'{"clips":[{"name":"a.mp4","size":Infinity}]}', headers={"content-type": "application/json"})
check("uploads: Infinity as a size is a plain 400, not a 500", r.status_code == 400 and "wasn't understood" in r.text, (r.status_code, r.text))
r = alex.post("/api/uploads", json={"clips": [{"name": "a.mp4", "size": True}]})
check("uploads: true as a size is a 400", r.status_code == 400, r.text)
r = alex.post("/api/jobs/web-20260101-000000-000000/reorder", content=b'{"order":[Infinity]}', headers={"content-type": "application/json"})
check("reorder: Infinity is not a 500", r.status_code in (400, 404), r.status_code)
r = alex.post("/api/uploads", content=b'{"clips":[{"name":"a.mp4","size":10}]}', headers={"content-type": "text/plain"})
check("a JSON body must say it is JSON (a cross-site form post can't)", r.status_code == 400, r.status_code)
r = alex.post("/api/jobs", data={"denoise": "banana"}, files=[("clips", ("z.mp4", b"", "video/mp4"))])
check("old upload route: bad form value gets a plain sentence", r.status_code == 400 and "detail" in r.json() and "bool_parsing" not in r.text, r.text)
r = alex.post("/api/jobs", files=[("clips", ("z.mp4", b"", "video/mp4"))])
check("old upload route: an empty clip is refused plainly", r.status_code == 400 and "empty" in r.text, r.text)
r = alex.put("/api/uploads/web-20260101-000000-000000/01.mov?offset=abc", content=b"x")
check("bad offset: plain 400, no framework dump", r.status_code in (400, 404) and "loc" not in r.text, r.text)

# ======================= finished-only actions, remakes, report paths =======================
def seed(job_id, owner="alex", done=True):
    OUT.joinpath(f"{job_id}.owner").write_text(owner, encoding="utf-8")
    if done:
        (OUT / job_id).mkdir(exist_ok=True)
        (OUT / job_id / "report.json").write_text(json.dumps({"final": r"C:\Users\x\out\final.mp4", "length": 20, "settings": {}}), encoding="utf-8")
        (OUT / job_id / "final.mp4").write_bytes(b"\x00\x00\x00 ftypmp42" + b"0" * 200)


seed("web-run-1", done=False)
W.jobs["web-run-1"] = {"status": "running", "queued_at": time.time()}
check("posting: refused while the video is still being made", alex.post("/api/jobs/web-run-1/posting", json={"status": "posted"}).status_code == 409)
check("event: refused while the video is still being made", alex.post("/api/jobs/web-run-1/event", json={"kind": "copy"}).status_code == 409)
check("feedback: a remake of a video still being made is refused",
      alex.post("/api/jobs/web-run-1/feedback", json={"changes": {"denoise": True}, "remake": True}).status_code == 409)
check("feedback: a plain note is still accepted", alex.post("/api/jobs/web-run-1/feedback", json={"comment": "nice", "remake": False}).status_code == 200)
seed("web-done-1")
W.jobs["web-done-1"] = {"status": "done"}
check("posting: allowed once finished", alex.post("/api/jobs/web-done-1/posting", json={"status": "reviewed"}).status_code == 200)
check("event: allowed once finished", alex.post("/api/jobs/web-done-1/event", json={"kind": "copy"}).status_code == 200)
st = alex.get("/api/jobs/web-done-1").json()
check("status: the report has no place on the server's disk", "final" not in st["report"] and "C:" not in json.dumps(st), st.get("report"))
r = alex.get("/api/jobs/web-done-1/final.mp4")
check("video files are never cached by a proxy", r.status_code == 200 and "no-store" in r.headers.get("cache-control", "")
      and "private" in r.headers.get("cache-control", ""), r.headers)
check("api answers are not cached either", "no-store" in alex.get("/api/me").headers.get("cache-control", ""))
check("a content security policy is sent", "default-src 'self'" in alex.get("/api/me").headers.get("content-security-policy", ""))
r = alex.post("/api/jobs/web-done-1/feedback", json={"changes": {"caption_fixes": {"{\\fs400}x": "a\\Nb{c}"}}, "remake": False, "remember": False})
log = [json.loads(l) for l in (OUT / "feedback.jsonl").read_text(encoding="utf-8").splitlines()]
fx = log[-1]["button_changes"]["caption_fixes"]
check("caption fixes lose subtitle codes", all(not any(ch in k + v for ch in "{}\\") for k, v in fx.items()), fx)
r = alex.post("/api/jobs/web-done-1/feedback", json={"changes": {"caption_size": 90, "hints": "Ellis Park Dr", "format": "recipe",
                                                                    "start_at_hook": True}, "remember": True, "remake": False})
check("feedback: 'remember' answers with only the look-and-feel keys", r.status_code == 200 and r.json()["remembered"] == ["caption_size"], r.text)
prefs = json.loads(W._prefs_file("alex").read_text(encoding="utf-8"))
check("feedback: the saved file has no per-video keys", set(prefs) <= set(settings.REMEMBER_KEYS) | {"caption_fixes"}, prefs)
check("admin resolve: an id that isn't there is a 404", admin.post("/api/admin/feedback/zzz/resolve").status_code == 404)
fid = log[-1]["id"]
check("admin resolve: a real id still works", admin.post(f"/api/admin/feedback/{fid}/resolve").status_code == 200)

# ======================= accounts =======================
try:
    auth.set_user("shorty", "abc1234")
    ok = False
except ValueError:
    ok = True
check("password: 8 characters at least, even when an admin sets it", ok)
check("password: 8 characters is fine", auth.set_user("eightok", "abcd1234") == "eightok")


class Req:
    def __init__(self, host, headers):
        self.client = type("C", (), {"host": host})()
        self.headers = headers


check("client ip: forwarded-for from a stranger is ignored", W._client_ip(Req("8.8.8.8", {"x-forwarded-for": "1.2.3.4"})) == "8.8.8.8")
check("client ip: from the local proxy, the last (proxy-written) entry is used",
      W._client_ip(Req("127.0.0.1", {"x-forwarded-for": "6.6.6.6, 1.2.3.4"})) == "1.2.3.4")
check("Dockerfile does not trust typed forwarded-for headers", "--forwarded-allow-ips='*'" not in (ROOT / "Dockerfile").read_text(encoding="utf-8"))

# ======================= tidy-up of abandoned uploads and logs =======================
old = W.UPLOADS / "web-old-abandoned"
old.mkdir()
(old / "01.mov").write_bytes(b"x")
new = W.UPLOADS / "web-new-pending"
new.mkdir()
used = W.UPLOADS / "web-old-used"
used.mkdir()
(OUT / "web-old-used.source").write_text("x", encoding="utf-8")
long_ago = time.time() - 5 * 86400
for p in (old, used):
    os.utime(p, (long_ago, long_ago))
gone = W._tidy_uploads()
check("tidy: an abandoned upload older than 2 days is removed", not old.exists() and gone >= 1)
check("tidy: a recent upload and one that became a video stay", new.exists() and used.exists())
(OUT / "events.jsonl").write_text("\n".join('{"a":%d}' % i for i in range(25000)) + "\n", encoding="utf-8")
W._tidy_uploads()
check("tidy: the click log is capped", len((OUT / "events.jsonl").read_text(encoding="utf-8").splitlines()) <= 10001)

# ======================= local mode (no login): other web pages can't act as the owner =======================
W.jobs.clear()
real_load = auth.load_users
auth.load_users = lambda: {}
os.environ.pop("ALEX_HOSTED", None)
try:
    me = client(base="http://localhost:8000")
    r = me.get("/api/me")
    check("local: the owner on localhost is signed in without a password", r.status_code == 200 and r.json().get("user") == "local", r.text)
    evil = {"username": "evil", "password": "evilpass1", "admin": True}
    r = me.post("/api/admin/users", content=json.dumps(evil), headers={"content-type": "text/plain", "origin": "http://evil.example"})
    check("local: a cross-site form post is refused", r.status_code == 403, r.status_code)
    r = me.post("/api/admin/users", content=json.dumps(evil), headers={"content-type": "text/plain"})
    check("local: text/plain is not accepted as JSON even with no origin", r.status_code == 400, r.status_code)
    check("local: no account was created", real_load.__call__().get("evil") is None)
    rb = client(base="http://rebind.evil.example:8051")
    check("local: a rebinding name can't read anything", rb.get("/api/me").status_code == 403 and rb.get("/api/videos").status_code == 403)
    check("local: 127.0.0.1 works too", client(base="http://127.0.0.1:8000").get("/api/me").status_code == 200)
    r = me.get("/api/me", headers={"origin": "http://evil.example"})
    check("local: reading is not blocked by an origin header, only changing things", r.status_code == 200)
finally:
    auth.load_users = real_load
    os.environ["ALEX_HOSTED"] = "1"

print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: " + "; ".join(fails))
sys.exit(1 if fails else 0)
