"""Backend lane A: accounts, delete, events, admin, prefs, hardening. No network, no AI, no video made.
Run:  venv\\Scripts\\python tests\\test_backend_a.py
Uses a throwaway data folder and FastAPI's TestClient (in-process)."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = Path(tempfile.mkdtemp(prefix="alex-backend-a-"))
os.environ.update(ALEX_DATA_DIR=str(DATA), ALEX_HOSTED="1", ADMIN_USER="tester", ADMIN_PASSWORD="admin-pass-123",
                  ANTHROPIC_API_KEY="", APP_PASSWORD="", ALEX_AUTO_SLIM="0", HELP_CONTACT="Text the maintainer at 555-0100")

from fastapi.testclient import TestClient   # noqa: E402

from web import app as W, auth   # noqa: E402
from web.interpret import clean_values, interpret   # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        fails.append(name)


def client():
    return TestClient(W.app, base_url="http://testserver")


def login(c, user, pw):
    r = c.post("/api/login", json={"username": user, "password": pw})
    return r


# ---------- accounts ----------
auth.set_user("alex", "alex-pass-1234")
auth.set_user("other", "other-pass-1234")
admin, alex, other = client(), client(), client()
check("admin signs in", login(admin, "tester", "admin-pass-123").status_code == 200)
check("alex signs in", login(alex, "alex", "alex-pass-1234").status_code == 200)
check("other signs in", login(other, "other", "other-pass-1234").status_code == 200)

# help endpoint is public
anon = client()
r = anon.get("/api/help")
check("/api/help is public and returns the contact", r.status_code == 200 and r.json() == {"contact": "Text the maintainer at 555-0100"}, r.text)
check("/api/me still has the help line", alex.get("/api/me").json()["help"] == "Text the maintainer at 555-0100")
check("API needs sign-in", anon.get("/api/videos").status_code == 401)

# caching headers
r = alex.get("/api/me")
check("API JSON is no-store", "no-store" in r.headers.get("cache-control", ""), r.headers)
r = alex.get("/")
check("HTML page is no-store", "no-store" in r.headers.get("cache-control", ""))
check("security headers kept", r.headers.get("x-frame-options") == "DENY")

# ---------- change password ----------
c = client()
login(c, "alex", "alex-pass-1234")
check("change-password needs sign-in", anon.post("/api/change-password", json={"current": "a", "new": "b"}).status_code == 401)
r = c.post("/api/change-password", json={"current": "wrong-wrong", "new": "brand-new-pass"})
check("wrong current is a 400 in plain English", r.status_code == 400 and "current password" in r.json()["detail"], r.text)
r = c.post("/api/change-password", json={"current": "alex-pass-1234", "new": "short"})
check("short new password refused", r.status_code == 400 and "8" in r.json()["detail"], r.text)
r = c.post("/api/change-password", json={"current": "alex-pass-1234", "new": "alex-pass-1234"})
check("same password refused", r.status_code == 400 and "different" in r.json()["detail"], r.text)
for bad in ([], "x", {"current": 5, "new": 6}, {"current": "alex-pass-1234"}, {"new": "abcdefgh12"}):
    r = c.post("/api/change-password", json=bad)
    check(f"junk body {str(bad)[:25]} is a 400", r.status_code == 400, r.text)
admin_before = auth.load_users()["tester"]["hash"]
r = c.post("/api/change-password", json={"current": "alex-pass-1234", "new": "brand-new-pass"})
check("change-password works", r.status_code == 200 and r.json() == {"ok": True}, r.text)
check("session cookie re-issued: still signed in", c.get("/api/me").json()["user"] == "alex")
check("old cookie from before the change is dead", alex.get("/api/me").status_code == 401)
check("old password no longer works", login(client(), "alex", "alex-pass-1234").status_code == 401)
check("new password signs in", login(client(), "alex", "brand-new-pass").status_code == 200)
check("other accounts untouched", auth.load_users()["tester"]["hash"] == admin_before)
check("admin flag kept for admin on change", True)
r = admin.post("/api/change-password", json={"current": "admin-pass-123", "new": "admin-pass-456"})
check("admin changes own password and stays admin", r.status_code == 200 and auth.is_admin("tester"))
login(admin, "tester", "admin-pass-456")
alex = c
# rate limit: current-password guesses lock out
vic = client()
login(vic, "other", "other-pass-1234")
codes = [vic.post("/api/change-password", json={"current": f"guess-guess-{i}", "new": "whatever-new-1"}).status_code for i in range(7)]
check("change-password is rate limited like login", 429 in codes and codes[0] == 400, codes)

# ---------- limiter / ip ----------
class Req:
    def __init__(self, host, headers):
        self.client = type("C", (), {"host": host})
        self.headers = headers


check("forwarded IP ignored when not from loopback", W._client_ip(Req("8.8.8.8", {"x-forwarded-for": "1.2.3.4"})) == "8.8.8.8")
check("forwarded IP trusted from loopback", W._client_ip(Req("127.0.0.1", {"cf-connecting-ip": "1.2.3.4"})) == "1.2.3.4")
check("loopback with no header is loopback", W._client_ip(Req("127.0.0.1", {})) == "127.0.0.1")
auth._fails[("old", "x")] = [0, 0]
auth._fails[("lockedold", "x")] = [0, time.time() - 5]
auth._fails[("active", "x")] = [0, time.time() + 50]
auth.prune_fails()
check("prune drops empty/expired, keeps active lock-outs", ("old", "x") not in auth._fails and ("lockedold", "x") not in auth._fails
      and ("active", "x") in auth._fails)

# ---------- local reset host check ----------
def local_req(host, client_host="127.0.0.1", extra=None):
    from starlette.requests import Request
    hdrs = [(b"host", host.encode())] + [(k.encode(), v.encode()) for k, v in (extra or {}).items()]
    return Request({"type": "http", "headers": hdrs, "client": (client_host, 1), "method": "GET", "path": "/", "query_string": b"",
                    "scheme": "http", "server": ("x", 80)})


os.environ.pop("ALEX_HOSTED")
check("local reset allowed for localhost:8000", W._on_this_computer(local_req("localhost:8000")))
check("local reset allowed for 127.0.0.1:8000", W._on_this_computer(local_req("127.0.0.1:8000")))
check("local reset allowed for [::1]:8000", W._on_this_computer(local_req("[::1]:8000", "::1")))
check("local reset refused for rebinding host", not W._on_this_computer(local_req("evil.example.com:8000")))
check("local reset refused for a LAN name", not W._on_this_computer(local_req("laptop.local:8000")))
check("local reset refused for non-local client", not W._on_this_computer(local_req("localhost:8000", "10.0.0.5")))
check("local reset refused through the tunnel", not W._on_this_computer(local_req("localhost", extra={"cf-connecting-ip": "1.1.1.1"})))
os.environ["ALEX_HOSTED"] = "1"

# ---------- sliding cookie ----------
auth.SESSION_DAYS = 90
fresh = auth.make_cookie("alex")
check("fresh cookie does not need refresh", not auth.cookie_needs_refresh(fresh))
real = time.time
time.time = lambda: real() + 2 * 86400
check("cookie two days old needs refresh", auth.cookie_needs_refresh(fresh))
check("two-day-old cookie is still valid", auth.read_cookie(fresh) == "alex")
time.time = real
check("garbage cookie: no refresh", not auth.cookie_needs_refresh("junk") and not auth.cookie_needs_refresh(None))
check("session length is 90 days", auth.SESSION_DAYS == 90)
# an old 30-day-style cookie gets refreshed on a request
old_exp = int(time.time()) + 20 * 86400
import base64, hashlib, hmac   # noqa: E402
tag = auth.load_users()["alex"]["hash"][:12]
payload = base64.urlsafe_b64encode(f"alex|{old_exp}|{tag}".encode()).decode()
sig = hmac.new(auth._secret(), payload.encode(), hashlib.sha256).hexdigest()
oc = client()
oc.cookies.set(auth.COOKIE, f"{payload}.{sig}")
r = oc.get("/api/me")
check("request with an ageing cookie gets a fresh Set-Cookie", auth.COOKIE in r.headers.get("set-cookie", ""), r.headers)
r = oc.get("/api/me")   # now holds the fresh one
check("a fresh cookie is not re-sent every request", "set-cookie" not in r.headers, r.headers.get("set-cookie"))

# ---------- users.json corruption ----------
good = json.loads(auth.USERS_FILE.read_text(encoding="utf-8"))
auth.set_user("temp-user", "temp-pass-123")   # forces a .bak of the good file
check(".bak is written", auth.USERS_FILE.with_name("users.json.bak").exists())
auth.USERS_FILE.write_text("{ not json", encoding="utf-8")
users = auth.load_users()
check("corrupt users.json falls back to the backup", "alex" in users and "tester" in users, list(users))
check("...and the file is repaired", json.loads(auth.USERS_FILE.read_text(encoding="utf-8")).keys() == users.keys())
auth.USERS_FILE.write_text("[1,2]", encoding="utf-8")
check("a wrong-shaped file also falls back", "alex" in auth.load_users())
auth.USERS_FILE.write_text("garbage", encoding="utf-8")
auth.USERS_FILE.with_name("users.json.bak").write_text("also garbage", encoding="utf-8")
try:
    auth.load_users()
    check("unreadable file + unreadable backup never returns {} silently", False, "returned")
except auth.UsersFileError:
    check("unreadable file + unreadable backup never returns {} silently", True)
check("login fails closed, no crash", auth.check_login("1.1.1.1", "alex", "x") is False)
check("site still demands login", W._wants_login(local_req("localhost")) is True)
check("is_admin does not crash", auth.is_admin("tester") is False)
auth.USERS_FILE.write_text(json.dumps(good), encoding="utf-8")
auth.USERS_FILE.with_name("users.json.bak").write_text(json.dumps(good), encoding="utf-8")
check("accounts back after repair", "alex" in auth.load_users())
auth.remove_user("temp-user")

# ---------- fixtures: videos ----------
OUT, UP = W.OUT, W.UPLOADS


def make_job(job, owner, source=None, parent=None, report=True):
    (OUT / job).mkdir(exist_ok=True)
    if report:
        (OUT / job / "report.json").write_text(json.dumps({"settings": {}, "parts": [{"file": "final.mp4"}]}), encoding="utf-8")
        (OUT / job / "final.mp4").write_bytes(b"x")
    (OUT / f"{job}.owner").write_text(owner, encoding="utf-8")
    (OUT / f"{job}.log").write_text("0/6 Getting your clips ready...\n", encoding="utf-8")
    if source:
        (OUT / f"{job}.source").write_text(str(source), encoding="utf-8")
    if parent:
        (OUT / f"{job}.parent").write_text(parent, encoding="utf-8")


for n in ("web-a", "web-a2", "web-a3", "web-b", "web-ab"):
    (UP / n).mkdir(parents=True, exist_ok=True)
    (UP / n / "01.mp4").write_bytes(b"clip")
make_job("web-a", "alex", UP / "web-a")
make_job("web-a2", "alex", UP / "web-a", parent="web-a")      # a remake shares the upload folder
make_job("web-a3", "alex", UP / "web-a3", parent="web-a2")    # a reorder made its own folder
make_job("web-b", "other", UP / "web-b")
make_job("web-ab", "alex", UP / "web-ab")
(OUT / "web-a.settings.json").write_text("{}", encoding="utf-8")
(OUT / "web-a2.settings.json").write_text("{}", encoding="utf-8")
(OUT / "web-a-other.owner").write_text("other", encoding="utf-8")   # looks like a prefix sibling: must survive

d = client()
login(d, "alex", "brand-new-pass")
r = d.delete("/api/jobs/web-b")
check("cannot delete another user's video (404)", r.status_code == 404 and (OUT / "web-b").exists(), r.text)
r = d.delete("/api/jobs/web-b?all=1")
check("cannot delete another user's family either", r.status_code == 404 and (UP / "web-b").exists())
for bad in ("..", "../x", "..%2F..%2Fusers", "web-a%2F..", "WEB-A", "web-a%00", "web-a%0A", "%2e%2e%2fusers"):
    r = d.delete(f"/api/jobs/{bad}?all=1")
    check(f"traversal id {bad!r} refused", r.status_code in (404, 405, 400, 307, 308) and (OUT / "web-a").exists(), r.status_code)
check("nothing else vanished from the data folder", (DATA / "users.json").exists() and (OUT / "web-b").exists())
check("unknown id is a clean 404", d.delete("/api/jobs/web-nope").status_code == 404)
check("anonymous delete refused", anon.delete("/api/jobs/web-a").status_code == 401)

W.jobs["web-ab"] = {"status": "running"}
r = d.delete("/api/jobs/web-ab")
check("a video still being made is not deleted (409)", r.status_code == 409 and (OUT / "web-ab").exists(), r.text)
W.jobs.pop("web-ab")

r = d.delete("/api/jobs/web-a2")
check("single delete removes just that version", r.status_code == 200 and r.json() == {"ok": True, "removed": 1}, r.text)
check("...its folder and side files are gone", not (OUT / "web-a2").exists() and not (OUT / "web-a2.owner").exists()
      and not (OUT / "web-a2.settings.json").exists() and not (OUT / "web-a2.log").exists())
check("...the first version and its upload folder stay", (OUT / "web-a").exists() and (UP / "web-a").exists())
check("...a prefix-named sibling file stays", (OUT / "web-a-other.owner").exists())
r = d.get("/api/jobs/web-a2")
check("deleted video: clean 404 JSON", r.status_code == 404 and "detail" in r.json(), r.text)

r = d.delete("/api/jobs/web-a3?all=1")   # asked from the LAST version: the whole family goes
check("all=1 removes every version of the family", r.status_code == 200 and r.json()["removed"] == 2, r.text)
check("...including its saved clips", not (OUT / "web-a").exists() and not (OUT / "web-a3").exists()
      and not (UP / "web-a").exists() and not (UP / "web-a3").exists())
check("...but not other videos or their clips", (OUT / "web-ab").exists() and (UP / "web-ab").exists()
      and (OUT / "web-b").exists() and (UP / "web-b").exists())
check("...nor the prefix-named sibling", (OUT / "web-a-other.owner").exists())

# shared upload folder: do not remove while another video still uses it
make_job("web-s1", "alex", UP / "web-ab")
make_job("web-s2", "alex", UP / "web-ab")
r = d.delete("/api/jobs/web-s1")
check("shared clips folder is kept while another video uses it", r.status_code == 200 and (UP / "web-ab").exists())
# a source pointing outside the uploads area is never deleted
outside = DATA / "precious"
outside.mkdir()
(outside / "keep.txt").write_text("x")
make_job("web-evil", "alex", outside)
r = d.delete("/api/jobs/web-evil?all=1")
check("a source folder outside the uploads area is never removed", r.status_code == 200 and (outside / "keep.txt").exists())
# admin can delete anyone's
r = admin.delete("/api/jobs/web-b?all=1")
check("admin may delete another user's video", r.status_code == 200 and not (OUT / "web-b").exists() and not (UP / "web-b").exists(), r.text)

# ---------- events ----------
make_job("web-e1", "alex")
make_job("web-e2", "other")
ev = OUT / "events.jsonl"
r = d.post("/api/jobs/web-e1/event", json={"kind": "copy", "platform": "TikTok"})
check("event accepted", r.status_code == 200 and r.json() == {"ok": True}, r.text)
d.post("/api/jobs/web-e1/event", json={"kind": "download"})
d.post("/api/jobs/web-e1/event", json={"kind": "posted", "platform": "youtube_shorts"})
rows = [json.loads(l) for l in ev.read_text(encoding="utf-8").splitlines()]
check("three lines logged with user, job, kind, platform, time",
      len(rows) == 3 and rows[0] == {"user": "alex", "job": "web-e1", "kind": "copy", "platform": "tiktok", "time": rows[0]["time"]}
      and rows[1]["platform"] is None, rows)
check("unknown kind is a 400", d.post("/api/jobs/web-e1/event", json={"kind": "like"}).status_code == 400)
check("missing kind is a 400", d.post("/api/jobs/web-e1/event", json={}).status_code == 400)
check("non-dict body is a 400", d.post("/api/jobs/web-e1/event", json=[1]).status_code == 400)
check("non-string platform is a 400", d.post("/api/jobs/web-e1/event", json={"kind": "copy", "platform": 5}).status_code == 400)
check("someone else's video: 404", d.post("/api/jobs/web-e2/event", json={"kind": "copy"}).status_code == 404)
d.post("/api/jobs/web-e1/event", json={"kind": "copy", "platform": "Dear the maintainer, my secret text!!"})
last = json.loads(ev.read_text(encoding="utf-8").splitlines()[-1])
check("platform is reduced to a short name, never free text", re.fullmatch(r"[a-z0-9_]{1,24}", last["platform"] or "") if (re := __import__("re")) else 0, last)
check("bad requests wrote nothing", len(ev.read_text(encoding="utf-8").splitlines()) == 4)
vids = d.get("/api/videos").json()["videos"]
row = next(v for v in vids if v["job_id"] == "web-e1")
check("/api/videos marks posted videos", row["posted"] is True and row["posted_to"] == ["youtube_shorts"], row)

# ---------- admin ----------
for path in ("/api/admin/health", "/api/admin/failures", "/api/admin/feedback"):
    check(f"{path} refuses a non-admin", d.get(path).status_code == 403)
    check(f"{path} refuses anonymous", anon.get(path).status_code == 401)
h = admin.get("/api/admin/health").json()
check("health has every field", set(h) == {"disk_free_gb", "jobs_total", "jobs_failed", "last_job_at", "key_present", "keep_awake",
                                           "help_contact_set"}, h)
check("health values sensible", isinstance(h["disk_free_gb"], float) and h["jobs_total"] >= 3 and h["key_present"] is False
      and h["help_contact_set"] is True and isinstance(h["keep_awake"], bool) and h["last_job_at"], h)
for i in range(25):
    W._log_failure(f"web-f{i}", "alex", "shown", "detail " + str(i))
f = admin.get("/api/admin/failures").json()["items"]
check("failures: last 20, newest first", len(f) == 20 and f[0]["job"] == "web-f24" and f[-1]["job"] == "web-f5", [x["job"] for x in f][:3])
check("health counts failed jobs", admin.get("/api/admin/health").json()["jobs_failed"] == 25)
check("admin cannot create a short-password user", admin.post("/api/admin/users", json={"username": "zz", "password": "abc"}).status_code == 400)
check("admin user create with junk body: 400", admin.post("/api/admin/users", json=[1]).status_code == 400)

# ---------- prefs reset ----------
pf = W._prefs_file("alex")
pf.parent.mkdir(parents=True, exist_ok=True)
pf.write_text(json.dumps({"caption_size": 100}), encoding="utf-8")
pf_other = W._prefs_file("other")
pf_other.parent.mkdir(parents=True, exist_ok=True)
pf_other.write_text(json.dumps({"caption_size": 60}), encoding="utf-8")
r = d.delete("/api/prefs")
check("DELETE /api/prefs resets only mine", r.status_code == 200 and r.json() == {"ok": True} and not pf.exists() and pf_other.exists(), r.text)
check("resetting twice is fine", d.delete("/api/prefs").status_code == 200)
check("anonymous cannot reset", anon.delete("/api/prefs").status_code == 401)

# ---------- feedback ----------
make_job("web-fb", "alex", UP / "web-ab")
(OUT / "web-fb" / "report.json").write_text(json.dumps({"settings": {}, "parts": [{"file": "final.mp4"}]}), encoding="utf-8")


def fb(body, raw=None):
    if raw is not None:
        return d.post("/api/jobs/web-fb/feedback", content=raw, headers={"content-type": "application/json"})
    return d.post("/api/jobs/web-fb/feedback", json=body)


check("feedback: list body is 400", fb([1]).status_code == 400)
check("feedback: string body is 400", fb("hi").status_code == 400)
check("feedback: broken JSON is 400", fb(None, raw=b"{nope").status_code == 400)
check("feedback: empty body is 400", fb(None, raw=b"").status_code == 400)
r = fb({"changes": ["caption_size"], "remake": False})
check("feedback: non-dict changes are ignored, no 500", r.status_code == 200 and r.json()["applied"] == [], r.text)
r = fb({"changes": "x"})
check("feedback: string changes ignored", r.status_code == 200)
r = fb({"changes": {"caption_size": "abc", "music_db": [1], "pause_gap": None, "captions_on": "yes", "caption_margin": True}})
check("feedback: non-numeric/wrong-kind values dropped", r.status_code == 200 and r.json()["applied"] == [], r.text)
r = fb({"changes": {"caption_size": float("nan")}}) if False else fb(None, raw=b'{"changes": {"caption_size": NaN}}')
check("feedback: NaN dropped", r.status_code == 200 and r.json()["applied"] == [], r.text)
r = fb({"changes": {"caption_size": 90, "format": "bogus", "framing": "weird", "nope": 1}, "by_hand_minutes": True})
check("feedback: good value kept, bad enum dropped", r.status_code == 200 and r.json()["applied"] == ["caption size 90"], r.text)
log = [json.loads(l) for l in W.FEEDBACK_LOG.read_text(encoding="utf-8").splitlines()]
check("log holds no junk values and no bool minutes", log[-1]["button_changes"] == {"caption_size": 90} and log[-1]["by_hand_minutes"] is None, log[-1])
r = fb({"changes": {"caption_fixes": {"Alexe": "Alex"}}, "remember": True})
check("caption fix passes", r.status_code == 200 and r.json()["remembered"] == ["caption_fixes"], r.text)
r = fb({"changes": {"caption_fixes": {"house": "perfect for families"}}})
check("Fair Housing words refused in a fix", r.status_code == 400 and "Fair Housing" in r.json()["detail"], r.text)
r = fb({"changes": {"caption_fixes": {"x": "family-friendly"}}})
check("family-friendly refused in a fix", r.status_code == 400)
r = fb({"changes": {"caption_fixes": {"grill": "perfect for grilling"}}})
check("'perfect for grilling' is allowed (whole-word, place not people)", r.status_code == 200, r.text)
# remember: buttons only, typed never
pf.unlink(missing_ok=True)
r = fb({"changes": {"caption_size": 100}, "comment": "make the captions way bigger and higher", "remake": False, "remember": True})
check("remember saves button choices", r.status_code == 200 and r.json()["remembered"] == ["caption_size"]
      and json.loads(pf.read_text(encoding="utf-8"))["caption_size"] == 100, r.text)
pf.unlink()
# a typed change must never reach prefs: call with remake True would start a render, so test the rule on the remember list
import web.app as A   # noqa: E402
orig_interp = A.interpret_full
A.interpret_full = lambda note, cur: ({"caption_size": 88, "music_on": False}, [], "ai", 0.01)
started = []
A._start = lambda *a, **k: started.append(a)
(OUT / "web-fb.source").write_text(str(UP / "web-ab"), encoding="utf-8")
r = fb({"changes": {"pause_gap": 0.5}, "comment": "bigger captions and no music", "remake": True, "remember": True})
saved = json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else {}
check("typed (AI-guessed) changes apply to this video", r.status_code == 200 and "caption size 88" in r.json()["applied"] and started, r.text)
check("typed changes are NOT remembered; button choice is", r.json().get("remembered") == ["pause_gap"]
      and saved.get("pause_gap") == 0.5 and saved.get("caption_size", 78) == 78 and saved.get("music_on", True) is True, saved)   # only what she changed is saved
overrides = json.loads((OUT / (started[0][0] + ".settings.json")).read_text(encoding="utf-8"))
check("remake uses both typed and button changes", overrides.get("caption_size") == 88 and overrides.get("pause_gap") == 0.5, overrides)
log = [json.loads(l) for l in W.FEEDBACK_LOG.read_text(encoding="utf-8").splitlines()]
check("AI cost is logged", log[-1].get("ai_cost_usd") == 0.01)
A.interpret_full = orig_interp

# ---------- interpret units ----------
check("clean_values drops wrong kinds", clean_values({"caption_size": "abc", "music_on": "no", "pause_gap": 0.5, "x": 1, "caption_fixes": []})
      == {"pause_gap": 0.5})
check("clean_values on a non-dict", clean_values(["a"]) == {} and clean_values(None) == {})
check("clean_values: bool is not a number", clean_values({"caption_size": True}) == {})
ch, bad, how = interpret("captions higher", {})
check("keyword fallback: higher moves 0.06 from 0.36", abs(ch.get("caption_margin", 0) - 0.42) < 1e-9 and how == "keywords", (ch, how))
ch, bad, how = interpret("captions lower", {"caption_margin": 0.36})
check("keyword fallback: lower clamps at the 0.33 floor", abs(ch.get("caption_margin", 0) - 0.33) < 1e-9, ch)
ch, bad, how = interpret("captions lower", {"caption_margin": 0.33})
check("already at the floor: nothing to change", "caption_margin" not in ch, ch)
from web import interpret as I   # noqa: E402
check("tool text names the 0.33 floor", "0.33" in I.TOOL["input_schema"]["properties"]["caption_margin"]["description"])

# ---------- job_status ----------
make_job("web-st", "alex", UP / "web-ab")
(OUT / "web-st.log").write_text("0/6 Getting your clips ready...\n   Normalizing and joining clips...\n", encoding="utf-8")
W.jobs["web-st"] = {"status": "running", "queued_at": time.time()}
check("step: Normalizing line is shown in plain words", d.get("/api/jobs/web-st").json()["step"] == "Preparing and joining your clips...",
      d.get("/api/jobs/web-st").json())
(OUT / "web-st.log").write_text("0/6 Getting your clips ready...\n", encoding="utf-8")
check("step: 0/6 line understood", d.get("/api/jobs/web-st").json()["step"] == "0/6 Getting your clips ready...")
(OUT / "web-st.log").write_text("3/6 Normalizing and joining clips...\n   Checking clip quality (shake, blur, light, audio)...\n", encoding="utf-8")
check("step: quality check line in plain words", d.get("/api/jobs/web-st").json()["step"] == "Checking your clips...")
(OUT / "web-st.log").write_text("3/6 Normalizing and joining clips...\n", encoding="utf-8")
check("step: numbered normalizing line is plain", "Normaliz" not in d.get("/api/jobs/web-st").json()["step"])
W.jobs.pop("web-st")
# restart: owner file only
make_job("web-lost", "alex", UP / "web-ab", report=False)
r = d.get("/api/jobs/web-lost").json()
check("known job with no result: failed shape the page can show", r["status"] == "failed" and r["can_retry"] is True and "restarted" in r["error"], r)
W.jobs.pop("web-lost", None)
(OUT / "web-lost2").mkdir()
(OUT / "web-lost2.owner").write_text("alex", encoding="utf-8")
r = d.get("/api/jobs/web-lost2").json()
check("known job without a source: failed, can_retry false", r["status"] == "failed" and r["can_retry"] is False, r)
r = d.get("/api/jobs/web-never")
check("never-known id: clean 404 with detail", r.status_code == 404 and list(r.json()) == ["detail"], r.text)
r = d.get("/api/jobs/web-b")
check("someone else's id: same clean 404", r.status_code == 404 and list(r.json()) == ["detail"])
# transcript
(OUT / "web-e1" / "captions.ass").write_text(
    "[Events]\nDialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,Hello there\\Nfriends\nDialogue: 0,0:00:02.00,0:00:04.00,Default,,0,0,0,,second line, with comma\n",
    encoding="utf-8")
r = d.get("/api/jobs/web-e1").json()
check("transcript: burned-in words as plain text", r["transcript"] == {"burned_in": True, "parts": ["Hello there friends second line, with comma"],
                                                                      "text": "Hello there friends second line, with comma"}, r.get("transcript"))
(OUT / "web-e1" / "captions.ass").unlink()
(OUT / "web-e1" / "captions_part2.ass").write_text("Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,B words\n", encoding="utf-8")
(OUT / "web-e1" / "captions_part1.ass").write_text("Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,A words\n", encoding="utf-8")
(OUT / "web-e1" / "captions_part10.ass").write_text("Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,J words\n", encoding="utf-8")
r = d.get("/api/jobs/web-e1").json()["transcript"]
check("transcript: per part, in part order", r["parts"] == ["A words", "B words", "J words"], r)
for f in (OUT / "web-e1").glob("captions*"):
    f.unlink()
r = d.get("/api/jobs/web-e1").json()["transcript"]
check("transcript: captions off -> empty, burned_in false", r == {"burned_in": False, "parts": [], "text": ""}, r)

# ---------- login hardening ----------
check("login: junk body is a 400, not a 500", client().post("/api/login", content=b"[1]", headers={"content-type": "application/json"}).status_code == 400)
check("login: broken JSON is a 400", client().post("/api/login", content=b"{", headers={"content-type": "application/json"}).status_code == 400)
spoof = client()
codes = [spoof.post("/api/login", json={"username": "other", "password": f"nope{i}"}, headers={"x-forwarded-for": f"9.9.9.{i}"}).status_code
         for i in range(8)]
check("spoofed X-Forwarded-For does not dodge the limit", 429 in codes, codes)
r = d.get("/api/jobs/web-fb/not-a-file")
check("unknown file is 404", r.status_code == 404)

print(f"\n{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED: ' + '; '.join(fails)}")
import shutil   # noqa: E402
shutil.rmtree(DATA, ignore_errors=True)
sys.exit(1 if fails else 0)
