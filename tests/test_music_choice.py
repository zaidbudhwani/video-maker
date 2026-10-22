"""Music choice: the installed tracks with their titles, her pick per video (remembered only when she asks), rotation when
she has not picked, and the 10-second preview that serves nothing else from the music folder.

Run: venv\\Scripts\\python tests\\test_music_choice.py
No AI, no network. Uses a throwaway data folder, a throwaway music folder for the rotation checks, and the real tracks in
assets/music/ (read only) for the titles and the preview. The video maker itself is replaced by a recorder."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = Path(tempfile.mkdtemp(prefix="alex-music-"))
os.environ.update(ALEX_DATA_DIR=str(DATA), ALEX_HOSTED="1", ADMIN_USER="tester", ADMIN_PASSWORD="admin-pass-123",
                  ANTHROPIC_API_KEY="", OPENROUTER_API_KEY="", APP_PASSWORD="", ALEX_AUTO_SLIM="0")

from video_tool import music, pipeline, settings as S   # noqa: E402
from video_tool.config import FFPROBE, MUSIC_DIR   # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {detail}"))
    if not cond:
        FAILS.append(name)


# ---------- a throwaway music folder ----------
FAKE = DATA / "music"
FAKE.mkdir()
for n in ("a_one.mp3", "b_two.mp3", "c_three.mp3", "d_four.mp3", "notes.txt", "cover.jpg"):
    (FAKE / n).write_bytes(b"x")
(FAKE / "LICENSES.txt").write_text('a_one.mp3   "First Song" by Someone (published 2026-01-01, 2:00)\n'
                                   '    https://example.invalid/a\n', encoding="utf-8")
names = [p.name for p in music.installed(FAKE)]
check("only audio files count as tracks (not the licence list, notes or pictures)", names == ["a_one.mp3", "b_two.mp3", "c_three.mp3", "d_four.mp3"], names)
t = music.tracks(FAKE)
check("titles come from LICENSES.txt", t[0] == {"file": "a_one.mp3", "title": "First Song", "artist": "Someone"}, t[0])
check("a track missing from LICENSES.txt is shown by its name", t[1] == {"file": "b_two.mp3", "title": "b two", "artist": ""}, t[1])
for bad in ("../a_one.mp3", "LICENSES.txt", "notes.txt", "A_ONE.MP3", "", None, 5, "a_one"):
    check(f"find() refuses {bad!r}", music.find(bad, FAKE) is None)
check("find() gives an installed track by its exact name", music.find("c_three.mp3", FAKE) == FAKE / "c_three.mp3")

# ---------- which track goes under a video ----------
check("'none' means no music", music.pick_track("none", "web-1", FAKE) is None)
check("her pick wins over rotation", all(music.pick_track("c_three.mp3", f"web-{i}", FAKE).name == "c_three.mp3" for i in range(10)))
check("the same video always gets the same track (a remake keeps it)",
      len({music.pick_track("", "web-20261020-101010-1234", FAKE) for _ in range(5)}) == 1)
spread = {music.pick_track("", f"web-20261020-1010{i:02d}-5555", FAKE).name for i in range(24)}
check("different videos rotate over the tracks (not all the first one)", len(spread) >= 3, spread)
gone = music.pick_track("removed_track.mp3", "web-x", FAKE)
check("a pick that was since removed falls back to rotation, not to silence", gone == music.pick_track("", "web-x", FAKE), gone)
check("no seed: the first track (the old behaviour)", music.pick_track("", None, FAKE).name == "a_one.mp3")
check("an empty music folder: no music", music.pick_track("", "web-1", DATA / "nothing-here") is None)

# the pipeline asks with the upload folder's name, so a remake (same folder, new job id) keeps the track
real_pick = music.pick_track
asked = []
music.pick_track = lambda choice="", seed=None, music_dir=None: asked.append((choice, seed)) or "picked"
got = pipeline.choose_track(str(DATA / "_uploads" / "web-20261020-1"), {"music_on": True, "music_track": ""})
check("pipeline: the music is picked with the upload folder's name as the seed", got == "picked" and asked == [("", "web-20261020-1")], asked)
asked.clear()
check("pipeline: music off picks nothing", pipeline.choose_track("x", {"music_on": False, "music_track": "a.mp3"}) is None and not asked)
check("pipeline: --no-music picks nothing", pipeline.choose_track("x", {"music_on": True}, no_music=True) is None and not asked)
pipeline.choose_track("x", {"music_on": True, "music_track": "b_two.mp3"})
check("pipeline: her pick is passed on", asked == [("b_two.mp3", "x")], asked)
music.pick_track = real_pick

# ---------- the setting ----------
check("music_track is a setting, empty (automatic) by default", S.DEFAULTS.get("music_track") == "")
for bad in ("../../web/app.py", "a/b.mp3", "C:\\x.mp3", "x" * 200 + ".mp3", 7, None, ["a.mp3"]):
    check(f"setting: {str(bad)[:30]!r} becomes automatic", S.clamp({**S.DEFAULTS, "music_track": bad})["music_track"] == "")
check("setting: a plain file name is kept", S.clamp({**S.DEFAULTS, "music_track": "turning_pages-muted-rest.mp3"})["music_track"]
      == "turning_pages-muted-rest.mp3")
check("her music pick can be remembered for future videos", "music_track" in S.REMEMBER_KEYS)

# ---------- the real tracks ----------
real = music.tracks()
check("the installed tracks are listed with the titles from LICENSES.txt", len(real) >= 1 and all(t["title"] and t["artist"] for t in real), real)

# ---------- the website ----------
from fastapi.testclient import TestClient   # noqa: E402
from web import app as W, auth   # noqa: E402

STARTS = []
W._start = lambda job_id, folder, user, extra=(): STARTS.append(list(extra))
auth.set_user("alex", "alex-pass-1234")
alex = TestClient(W.app, base_url="http://testserver")
anon = TestClient(W.app, base_url="http://testserver")
assert alex.post("/api/login", json={"username": "alex", "password": "alex-pass-1234"}).status_code == 200

r = anon.get("/api/music")
check("signed out: no track list", r.status_code == 401, r.status_code)
r = anon.get(f"/api/music/{real[0]['file']}") if real else None
check("signed out: no preview", r is not None and r.status_code == 401, r and r.status_code)
r = alex.get("/api/music")
check("signed in: the tracks and titles", r.status_code == 200 and r.json()["tracks"] == real and r.json()["preview_seconds"] == 10, r.text[:200])

first = real[0]["file"]
r = alex.get(f"/api/music/{first}")
ok = r.status_code == 200 and r.headers.get("content-type") == "audio/mpeg"
length = None
if ok:
    f = DATA / "preview.mp3"
    f.write_bytes(r.content)
    p = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)], capture_output=True, text=True)
    length = float(p.stdout.strip() or 0)
check("the preview is an mp3 of about 10 seconds", ok and 9.0 <= length <= 10.5, (r.status_code, length))
check("...made once, then kept in the data folder (not the music folder)", any((DATA / "cache" / "music").glob("*.mp3"))
      and not any(p.name.endswith(".part.mp3") for p in MUSIC_DIR.iterdir()))
for bad in ("LICENSES.txt", "README.md", "..%2F..%2Fweb%2Fapp.py", "..%5C..%5Cweb%5Capp.py", first.upper(), first + ".bak", "nothing.mp3"):
    r = alex.get(f"/api/music/{bad}")
    check(f"nothing else in the folder is readable: {bad}", r.status_code == 404 and b"ffmpeg" not in r.content, r.status_code)


def upload_with(music_choice):
    r = alex.post("/api/uploads", json={"clips": [{"name": "kitchen.mp4", "size": 1000}], "voice": None, "music": music_choice})
    if r.status_code != 200:
        return r, None
    return r, json.loads((W.UPLOADS / r.json()["job_id"] / "pending.json").read_text())["extras"]


r, ex = upload_with("none")
check("upload: No music turns music off for this video", ex == {"music_on": False}, (r.text, ex))
r, ex = upload_with(first)
check("upload: a track is picked for this video", ex == {"music_on": True, "music_track": first}, (r.text, ex))
r, ex = upload_with("auto")
check("upload: Pick one for me = automatic", ex == {"music_on": True, "music_track": ""}, (r.text, ex))
r, ex = upload_with("../../web/app.py")
check("upload: a name that isn't an installed track is a plain 400", r.status_code == 400 and "isn't installed" in r.json()["detail"], r.text)
r = alex.post("/api/uploads", json={"clips": [{"name": "kitchen.mp4", "size": 1000}], "voice": None})
check("upload: no music choice sent = the saved settings decide",
      r.status_code == 200 and json.loads((W.UPLOADS / r.json()["job_id"] / "pending.json").read_text())["extras"] == {}, r.text)

# the Change panel: pick another track, remembered only when she ticks the box
src = DATA / "clips"
src.mkdir()
jid = "web-m-1"
(W.OUT / jid).mkdir(parents=True)
(W.OUT / jid / "report.json").write_text(json.dumps({"job_id": jid, "mode": "voice", "music": first, "settings": dict(S.DEFAULTS)}))
(W.OUT / f"{jid}.owner").write_text("alex")
(W.OUT / f"{jid}.source").write_text(str(src))


def remake(changes, remember=False):
    STARTS.clear()
    r = alex.post(f"/api/jobs/{jid}/feedback", json={"comment": "", "changes": changes, "remake": True, "remember": remember})
    over = json.loads(Path(STARTS[0][STARTS[0].index("--settings") + 1]).read_text()) if STARTS else {}
    return r, over


other = real[-1]["file"]
r, over = remake({"music_track": other})
check("remake: the new track reaches the video", r.status_code == 200 and over.get("music_track") == other, (r.text, over))
check("...and she is told which one", any(real[-1]["title"] in a for a in r.json().get("applied", [])), r.json().get("applied"))
check("...not remembered unless she asked", alex.get("/api/prefs").json().get("music") == "", alex.get("/api/prefs").json())
r, over = remake({"music_track": "not-installed.mp3"})
check("remake: a track that isn't installed is a plain 400 and nothing starts", r.status_code == 400 and not STARTS, r.text)
r, over = remake({"music_track": other}, remember=True)
check("remember: the upload page starts from her saved track", alex.get("/api/prefs").json().get("music") == other, alex.get("/api/prefs").json())
r, over = remake({"music_on": False}, remember=True)
check("remember: saved No music shows as No music", alex.get("/api/prefs").json().get("music") == "none", alex.get("/api/prefs").json())

print(f"{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED'}")
sys.exit(1 if FAILS else 0)
