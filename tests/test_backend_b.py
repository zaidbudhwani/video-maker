"""Backend lane B: upload extras and file checks, formats, ideas, post pack, posting log and week, reminder file,
example video, phone-app files. No Claude call, no video made (the pipeline start is replaced by a recorder).
Run:  venv/Scripts/python tests/test_backend_b.py
Uses a throwaway data folder and FastAPI's TestClient (in-process). Finished videos from out/demo-* are linked in
as fixtures (read only; nothing is written to the real out/)."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = Path(tempfile.mkdtemp(prefix="alex-backend-b-"))
os.environ.update(ALEX_DATA_DIR=str(DATA), ALEX_HOSTED="1", ADMIN_USER="tester", ADMIN_PASSWORD="admin-pass-123",
                  ANTHROPIC_API_KEY="", APP_PASSWORD="", ALEX_AUTO_SLIM="0")

from fastapi.testclient import TestClient   # noqa: E402

from web import app as W, auth   # noqa: E402
from video_tool.config import FFMPEG   # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {str(detail)[:300]}"))
    if not ok:
        fails.append(name)


def client(user=None, pw=None):
    c = TestClient(W.app, base_url="http://testserver")
    if user:
        assert c.post("/api/login", json={"username": user, "password": pw}).status_code == 200
    return c


auth.set_user("alex", "alex-pass-1234")
auth.set_user("other", "other-pass-1234")
alex, other, admin, anon = client("alex", "alex-pass-1234"), client("other", "other-pass-1234"), \
    client("tester", "admin-pass-123"), client()
OUT = W.OUT

# ---------- real tiny media made with ffmpeg ----------
MEDIA = DATA / "media"
MEDIA.mkdir()


def ff(*args, out=None):
    p = subprocess.run([FFMPEG, "-y", "-v", "error", *args], capture_output=True, text=True)
    return p.returncode == 0


good_clip = MEDIA / "good.mp4"
ff("-f", "lavfi", "-i", "testsrc=s=320x240:d=1:r=15", "-f", "lavfi", "-i", "sine=f=300:d=1", "-shortest",
   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(good_clip))
audio_only_mp4 = MEDIA / "audioonly.mp4"
ff("-f", "lavfi", "-i", "sine=f=300:d=2", "-c:a", "aac", str(audio_only_mp4))
audio_only_3gp = MEDIA / "audioonly.3gp"
has_3gp = ff("-f", "lavfi", "-i", "sine=f=300:d=2", "-ar", "8000", "-ac", "1", "-c:a", "amr_nb", str(audio_only_3gp)) \
    or ff("-f", "lavfi", "-i", "sine=f=300:d=2", "-ar", "8000", "-ac", "1", "-c:a", "aac", "-f", "3gp", str(audio_only_3gp))
truncated = MEDIA / "truncated.mp4"
truncated.write_bytes(good_clip.read_bytes()[:good_clip.stat().st_size // 2])
tone_m4a = MEDIA / "tone.m4a"
ff("-f", "lavfi", "-i", "sine=f=440:d=3", "-c:a", "aac", str(tone_m4a))
silent_m4a = MEDIA / "silent.m4a"
ff("-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "3", "-c:a", "aac", str(silent_m4a))
tone_webm = MEDIA / "tone.webm"
ff("-f", "lavfi", "-i", "sine=f=440:d=3", "-c:a", "libopus", str(tone_webm))
# what a browser's MediaRecorder writes: streamed, so there is no length in the header
nodur_webm = MEDIA / "nodur.webm"
p = subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=3", "-c:a", "libopus", "-f", "webm", "-"],
                   capture_output=True)
nodur_webm.write_bytes(p.stdout)
tone_ogg = MEDIA / "tone.ogg"
ff("-f", "lavfi", "-i", "sine=f=440:d=3", "-c:a", "libopus", str(tone_ogg))
tone_mp4 = MEDIA / "tone.mp4"      # audio/mp4 from Safari, saved with an .mp4 ending
ff("-f", "lavfi", "-i", "sine=f=440:d=3", "-c:a", "aac", str(tone_mp4))
silent_webm = MEDIA / "silent.webm"
ff("-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "3", "-c:a", "libopus", str(silent_webm))
video_only = MEDIA / "videoonly.mp4"
ff("-f", "lavfi", "-i", "testsrc=s=320x240:d=1:r=15", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video_only))
junk_voice = MEDIA / "junk.m4a"
junk_voice.write_bytes(b"this is not audio at all" * 50)
for f in (good_clip, audio_only_mp4, truncated, tone_m4a, silent_m4a, tone_webm, nodur_webm, tone_ogg, tone_mp4, silent_webm, video_only):
    assert f.exists() and f.stat().st_size > 100, f"couldn't make {f.name}"
check("test media made with ffmpeg", True)
check("the browser-style recording really has no length in its header (so the conversion path is exercised)",
      not W._has_length(W._loose_probe(nodur_webm)) and W._loose_probe(tone_webm) is not None)

# the real pipeline is not started here: record what would have been run
STARTS = []
W._start = lambda job_id, folder, user, extra=(): STARTS.append({"job": job_id, "folder": Path(folder), "user": user,
                                                                "extra": list(extra)})


def upload(c, clips, voice=None, **extra):
    """Start, send and finish an upload like the page does. clips/voice are (her name, path). Returns finish response."""
    body = {"clips": [{"name": n, "size": p.stat().st_size} for n, p in clips],
            "voice": {"name": voice[0], "size": voice[1].stat().st_size} if voice else None, **extra}
    r = c.post("/api/uploads", json=body)
    if r.status_code != 200:
        return r
    j = r.json()
    files = [p for _, p in clips] + ([voice[1]] if voice else [])
    for slot, path in zip(j["slots"], files):
        r2 = c.put(f"/api/uploads/{j['job_id']}/{slot['slot']}?offset=0", content=path.read_bytes())
        assert r2.status_code == 200, r2.text
    r = c.post(f"/api/uploads/{j['job_id']}/finish")
    r.job_id = j["job_id"]
    return r


def plain(msg):
    """No paths, no code words in what she reads."""
    return not any(x in msg for x in ("C:\\", "\\", "/tmp", ".json", "Traceback", "ffmpeg", "ffprobe", "moov", "stream", "codec"))


# ---------- upload start: extras ----------
r = alex.post("/api/uploads", json={"clips": [{"name": "A.mov", "size": 10}], "hints": "  Ellis   Park Dr,\nShellsburg " + "x" * 600,
                                    "format": "recipe", "private": True})
check("start with hints, format and private is accepted", r.status_code == 200, r.text)
pend = json.loads((W.UPLOADS / r.json()["job_id"] / "pending.json").read_text())
ex = pend["extras"]
check("hints are tidied and cut at 400", ex["hints"].startswith("Ellis Park Dr, Shellsburg x") and len(ex["hints"]) == 400, ex)
check("format is stored", ex["format"] == "recipe")
check("private turns clip matching off and records privacy", ex["match_clips"] is False and ex["privacy"] == "private", ex)
r = alex.post("/api/uploads", json={"clips": [{"name": "A.mov", "size": 10}], "hints": "", "format": "auto", "private": False})
check("empty hints, auto and private=false add nothing", r.status_code == 200 and
      json.loads((W.UPLOADS / r.json()["job_id"] / "pending.json").read_text())["extras"] == {}, r.text)
for bad, why in (({"hints": 5}, "hints not text"), ({"hints": ["a"]}, "hints a list"), ({"format": "poem"}, "unknown format"),
                 ({"format": 3}, "format not text"), ({"private": "yes"}, "private not a bool"), ({"private": 1}, "private a number")):
    r = alex.post("/api/uploads", json={"clips": [{"name": "A.mov", "size": 10}], **bad})
    check(f"bad {why} is a plain 400", r.status_code == 400 and plain(r.json()["detail"]), r.text)
for body, why in ((b"[1]", "list body"), (b"{", "broken JSON"), (b'{"clips": "x"}', "clips not a list"),
                  (b'{"clips": [1, 2]}', "clips not objects"), (b'{"clips": [{"name": "a.mov", "size": "big"}]}', "size not a number"),
                  (b'{"clips": [{"name": "a.mov", "size": 5}], "voice": "v.m4a"}', "voice not an object")):
    r = alex.post("/api/uploads", content=body, headers={"content-type": "application/json"})
    check(f"upload start: {why} is a 400, not a 500", r.status_code == 400, f"{r.status_code} {r.text}")
r = alex.post("/api/uploads", json={"clips": [{"name": "A.mov", "size": 10}], "voice": {"name": "notes.txt", "size": 5}})
check("a text file can't be the voice-over", r.status_code == 400 and "notes.txt" in r.json()["detail"], r.text)
for nm in ("Voice over.webm", "rec.ogg", "rec.mp4", "memo.m4a", "memo.3gp", "x.weba"):
    r = alex.post("/api/uploads", json={"clips": [{"name": "A.mov", "size": 10}], "voice": {"name": nm, "size": 5}})
    check(f"voice slot accepts {nm}", r.status_code == 200, r.text)
check("signed-out upload start is refused", anon.post("/api/uploads", json={"clips": []}).status_code == 401)

# ---------- upload finish: what is checked, and what is said ----------
STARTS.clear()
r = upload(alex, [("Kitchen.mov", good_clip)], ("Tour talk.m4a", tone_m4a), hints="Hilltop", format="house_tour", private=True)
check("good clip + good voice starts the video", r.status_code == 200 and len(STARTS) == 1, r.text)
over = json.loads((OUT / f"{STARTS[0]['job']}.settings.json").read_text())
check("the job's settings overrides carry hints, format, privacy and match_clips off",
      over == {"hints": "Hilltop", "format": "house_tour", "match_clips": False, "privacy": "private"}, over)
check("a plain upload still starts with no settings file", (STARTS.clear() or upload(alex, [("a.mov", good_clip)]).status_code == 200)
      and STARTS[0]["extra"] == [], STARTS)
STARTS.clear()
r = upload(alex, [("a.mov", good_clip)], denoise=True, hints="x")
over = json.loads((OUT / f"{STARTS[0]['job']}.settings.json").read_text())
check("denoise and hints combine", over == {"denoise": True, "hints": "x"}, over)

STARTS.clear()
r = upload(alex, [("Kitchen.mov", good_clip), ("My note.mp4", audio_only_mp4)])
d = r.json().get("detail", "")
check("audio-only clip is refused naming Clip 2 and her file", r.status_code == 400 and "Clip 2 (My note.mp4)" in d
      and "no picture" in d and "voice-over" in d and plain(d), r.text)
if has_3gp:
    r = upload(alex, [("Hall.mov", good_clip), ("Voice note.3gp", audio_only_3gp)])
    check("audio-only .3gp clip is refused the same way", r.status_code == 400 and "Clip 2 (Voice note.3gp)" in r.json()["detail"], r.text)
r = upload(alex, [("Broken.mp4", truncated)])
d = r.json().get("detail", "")
check("truncated clip: 'can't be read, may not have finished downloading'", r.status_code == 400 and "Clip 1 (Broken.mp4)" in d
      and "can't be read" in d and "finished downloading" in d and plain(d), r.text)
r = upload(alex, [("a.mov", good_clip)], ("Muted.m4a", silent_m4a))
d = r.json().get("detail", "")
check("silent voice-over: 'I couldn't hear anything'", r.status_code == 400 and d.startswith("I couldn't hear anything in your voice-over (Muted.m4a)") and plain(d), r.text)
r = upload(alex, [("a.mov", good_clip)], ("Muted rec.webm", silent_webm))
check("silent browser recording is refused too", r.status_code == 400 and "couldn't hear anything" in r.json()["detail"], r.text)
r = upload(alex, [("a.mov", good_clip)], ("Pictures only.mp4", video_only))
d = r.json().get("detail", "")
check("voice file with no sound is refused", r.status_code == 400 and "Pictures only.mp4" in d and "no sound" in d and plain(d), r.text)
r = upload(alex, [("a.mov", good_clip)], ("Half.m4a", junk_voice))
d = r.json().get("detail", "")
check("junk voice file: can't be read", r.status_code == 400 and "Half.m4a" in d and "can't be read" in d and plain(d), r.text)

for nm, src in (("Recording.webm", tone_webm), ("Recording nodur.webm", nodur_webm), ("rec.ogg", tone_ogg), ("Safari rec.mp4", tone_mp4)):
    STARTS.clear()
    r = upload(alex, [("a.mov", good_clip)], (nm, src))
    folder = STARTS[0]["folder"] if STARTS else None
    names = sorted(f.name for f in folder.iterdir()) if folder else []
    voice_files = [n for n in names if n.startswith("voice")]
    from video_tool.inputs import find_inputs
    ok = r.status_code == 200 and voice_files == ["voice.m4a"] or (nm.endswith(".ogg") and voice_files in (["voice.ogg"], ["voice.m4a"]))
    check(f"{nm} as voice-over: accepted, and the pipeline sees one clip and an audio voice", ok, f"{r.status_code} {r.text} {names}")
    if folder and ok:
        try:
            clips, voice = find_inputs(folder)
            check(f"  {nm}: find_inputs gives 1 clip + the voice", len(clips) == 1 and voice is not None and voice.stem == "voice", (clips, voice))
        except Exception as e:
            check(f"  {nm}: find_inputs works", False, e)
        orig = json.loads((folder / "original_names.json").read_text())
        check(f"  {nm}: her name is kept in original_names", orig[-1] == nm, orig)
from video_tool import ff as FF   # noqa: E402
check("converted browser recording has a real length", abs(FF.probe(STARTS[-1]["folder"] / next(f for f in os.listdir(STARTS[-1]["folder"]) if f.startswith("voice")))["duration"] - 3) < 0.6)
# a retry after a refusal still works (the slot was renamed during the first try)
STARTS.clear()
body = {"clips": [{"name": "a.mov", "size": good_clip.stat().st_size}], "voice": {"name": "r.webm", "size": nodur_webm.stat().st_size}}
j = alex.post("/api/uploads", json=body).json()
alex.put(f"/api/uploads/{j['job_id']}/{j['slots'][0]['slot']}?offset=0", content=good_clip.read_bytes())
alex.put(f"/api/uploads/{j['job_id']}/{j['slots'][1]['slot']}?offset=0", content=nodur_webm.read_bytes())
r1 = alex.post(f"/api/uploads/{j['job_id']}/finish")
r2 = alex.post(f"/api/uploads/{j['job_id']}/finish")
check("finish twice does not start twice", r1.status_code == 200 and r2.status_code == 200 and len(STARTS) == 1, (r1.text, r2.text, len(STARTS)))
check("someone else can't finish her upload", other.post(f"/api/uploads/{j['job_id']}/finish").status_code == 404)

# legacy one-request upload takes the same extras
STARTS.clear()
r = alex.post("/api/jobs", files=[("clips", ("a.mov", good_clip.read_bytes(), "video/quicktime"))],
              data={"hints": "Solon", "format": "tips", "private": "true"})
over = json.loads((OUT / f"{STARTS[0]['job']}.settings.json").read_text()) if STARTS else None
check("legacy POST /api/jobs takes hints, format and private", r.status_code == 200 and over == {
    "hints": "Solon", "format": "tips", "match_clips": False, "privacy": "private"}, (r.text, over))
r = alex.post("/api/jobs", files=[("clips", ("a.mov", good_clip.read_bytes(), "video/quicktime"))], data={"format": "poem"})
check("legacy upload: bad format is a 400", r.status_code == 400, r.text)

# the friendly-error hint
msg = W._friendly_error("STOPPED: Can't read this file. It may be corrupt or not a real video/audio file.\n  File: C:\\x\\01.mp4", str(DATA))
check("pipeline 'can't read' error gets the re-download hint", "finished downloading" in msg and "C:" not in msg, msg)

# a private video stays private through a remake (feedback)
STARTS.clear()
(OUT / "web-priv-1").mkdir()
(OUT / "web-priv-1" / "report.json").write_text(json.dumps({"settings": {}}))
(OUT / "web-priv-1.owner").write_text("alex")
(OUT / "web-priv-1.source").write_text(str(DATA))
(OUT / "web-priv-1.settings.json").write_text(json.dumps({"match_clips": False, "privacy": "private"}))
r = alex.post("/api/jobs/web-priv-1/feedback", json={"changes": {"match_clips": True, "caption_size": 90}, "remake": True})
over = json.loads((OUT / f"{STARTS[0]['job']}.settings.json").read_text()) if STARTS else {}
check("remake of a private video keeps matching off", r.status_code == 200 and over.get("match_clips") is False
      and over.get("privacy") == "private" and over.get("caption_size") == 90, (r.text, over))
rows = alex.get("/api/videos").json()["videos"]
row = next(v for v in rows if v["job_id"] == "web-priv-1")
check("My videos rows say whether pictures stayed private", row["private"] is True, row)

# ---------- formats ----------
r = alex.get("/api/formats")
fm = r.json()
from video_tool.settings import FORMAT_KEYS   # noqa: E402
check("GET /api/formats lists every kind except auto", r.status_code == 200 and [f["id"] for f in fm] == [k for k in FORMAT_KEYS if k != "auto"], fm)
check("every format has a label and a filming list", all(f["label"] and len(f["film_list"]) >= 4 and set(f) == {"id", "label", "film_list"} for f in fm))
check("formats need sign-in", anon.get("/api/formats").status_code == 401)

# ---------- ideas ----------
r = alex.get("/api/ideas?n=3")
ideas = r.json().get("ideas", [])
check("GET /api/ideas?n=3 gives 3 ideas with title, format and shots", r.status_code == 200 and len(ideas) == 3
      and all(set(i) == {"title", "format", "shots"} and i["title"] and i["format"] in FORMAT_KEYS and len(i["shots"]) == 4 for i in ideas), r.text)
check("ideas: junk n falls back to 5", len(alex.get("/api/ideas?n=abc").json()["ideas"]) == 5)
check("ideas: n capped at 12", len(alex.get("/api/ideas?n=99").json()["ideas"]) <= 12)
check("ideas need sign-in", anon.get("/api/ideas").status_code == 401)
(OUT / "web-idea-1").mkdir()
(OUT / "web-idea-1" / "copy.json").write_text(json.dumps({"titles": [ideas[0]["title"]]}))
(OUT / "web-idea-1.owner").write_text("other")
W.IDEAS_CACHE.clear()
(OUT / "web-idea-1.owner").write_text("alex")
again = alex.get("/api/ideas?n=3").json()["ideas"]
check("ideas matching her recent titles are left out", ideas[0]["title"] not in [i["title"] for i in again] or len(again) < 3, again)
(OUT / "web-idea-1.owner").write_text("other")
W.IDEAS_CACHE.clear()

# ---------- fixtures: finished videos ----------
def fixture(src, dst, owner=None, keep=("final", "copy", "report", "captions", "transcript")):
    s, d = ROOT / "out" / src, OUT / dst
    if not s.is_dir():
        return False
    d.mkdir(parents=True, exist_ok=True)
    for f in s.iterdir():
        if f.is_file() and f.name.startswith(keep):
            try:
                os.link(f, d / f.name)
            except OSError:
                shutil.copy2(f, d / f.name)
    if owner:
        (OUT / f"{dst}.owner").write_text(owner)
    return True


have_recipe = fixture("demo-recipe", "web-rec-1", "alex")
have_tips = fixture("demo-tips", "web-tips-1", "alex")
have_tour = fixture("demo-tour-moved", "demo-tour-moved")
(OUT / "_demo.txt").write_text("demo-tour-moved\n")

HAVE_FIXTURES = have_recipe and have_tips and have_tour
if not HAVE_FIXTURES:
    print("SKIPPED the checks on finished videos (post pack, posting log, reminder, example): out/demo-* is not in this copy")
else:
    # ---------- post pack ----------
    r = alex.get("/api/jobs/web-rec-1/postpack")
    check("post pack downloads as a zip", r.status_code == 200 and r.headers["content-type"] == "application/zip", r.status_code)
    cd = r.headers.get("content-disposition", "")
    title = json.loads((OUT / "web-rec-1" / "copy.json").read_text(encoding="utf-8"))["titles"][0]
    check("post pack file name is built from the title and is plain ASCII", cd.startswith("attachment") and "post-pack.zip" in cd
          and cd.encode("ascii") and "web-rec" not in cd, cd)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = sorted(z.namelist())
    check("post pack holds exactly the video, captions, cover, alt text and post text",
          names == ["alt-text.txt", "captions.srt", "cover.jpg", "final.mp4", "post-text.txt"], names)
    check("no source clips, transcripts, settings or paths in the pack", not any(n.startswith(("0", "joined", "video", "normalized", "words", "report", "copy", "transcript")) for n in names))
    srt = z.read("captions.srt").decode("utf-8")
    check("captions.srt is a real SRT", srt.startswith("1\n") or srt.startswith("1\r\n"), srt[:60])
    check("cover.jpg is a jpeg", z.read("cover.jpg")[:3] == b"\xff\xd8\xff")
    check("zip is sound", z.testzip() is None)
    check("post-text has the four platforms' captions", all(w in z.read("post-text.txt").decode("utf-8").lower() for w in ("tiktok", "reels", "shorts")), z.read("post-text.txt")[:200])
    check("alt-text is not empty and has no path", len(z.read("alt-text.txt").strip()) > 20 and b":\\" not in z.read("alt-text.txt"))
    check("temp pack is removed after sending", not list((OUT / "_packs").glob("*.zip")), list((OUT / "_packs").glob("*")))
    r = alex.get("/api/jobs/web-tips-1/postpack")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = sorted(z.namelist())
    check("3-part video: pack has every part with its own captions and cover", r.status_code == 200 and names == sorted([
        "alt-text.txt", "post-text.txt", "final_part1.mp4", "final_part2.mp4", "final_part3.mp4", "captions_part1.srt", "captions_part2.srt",
        "captions_part3.srt", "cover.jpg", "cover_part2.jpg", "cover_part3.jpg"]), names)
    del r, z
    check("other person can't get her pack", other.get("/api/jobs/web-rec-1/postpack").status_code == 404)
    check("admin can", admin.get("/api/jobs/web-rec-1/postpack").status_code == 200)
    check("signed out can't", anon.get("/api/jobs/web-rec-1/postpack").status_code == 401)
    check("unknown job: 404", alex.get("/api/jobs/web-nope/postpack").status_code == 404)
    (OUT / "web-run-1").mkdir()
    (OUT / "web-run-1.owner").write_text("alex")
    r = alex.get("/api/jobs/web-run-1/postpack")
    check("video not finished: plain 409", r.status_code == 409 and plain(r.json()["detail"]), r.text)
    check("bad id is a 404", alex.get("/api/jobs/WEB..%2f..%2fx/postpack").status_code == 404)

    # ---------- posting log ----------
    today = datetime.now().date()
    r = alex.get("/api/jobs/web-rec-1/posting")
    check("a new video is a draft", r.status_code == 200 and r.json() == {"status": "draft", "posted": [], "date": None}, r.text)
    r = alex.post("/api/jobs/web-rec-1/posting", json={"status": "reviewed"})
    check("mark reviewed", r.json()["status"] == "reviewed", r.text)
    r = alex.post("/api/jobs/web-rec-1/posting", json={"status": "posted", "platform": "TikTok"})
    check("mark posted: platform is normalised, date defaults to today", r.json() == {"status": "posted", "posted": [{"platform": "tiktok", "date": today.isoformat()}],
                                                                                       "date": today.isoformat()}, r.text)
    yesterday = (today - timedelta(days=1)).isoformat()
    alex.post("/api/jobs/web-rec-1/posting", json={"status": "posted", "platform": "youtube_shorts", "date": yesterday})
    r = alex.post("/api/jobs/web-rec-1/posting", json={"status": "posted", "platform": "tiktok", "date": yesterday})
    v = r.json()
    check("same platform again changes its date; two platforms listed", len(v["posted"]) == 2 and {p["platform"] for p in v["posted"]} == {"tiktok", "youtube_shorts"}
          and all(p["date"] == yesterday for p in v["posted"]), r.text)
    check("GET returns what was stored", alex.get("/api/jobs/web-rec-1/posting").json() == v)
    check("posted shows on My videos", next(x for x in alex.get("/api/videos").json()["videos"] if x["job_id"] == "web-rec-1")["posted"] is True)
    for bad, why in (({"status": "done"}, "unknown status"), ({}, "no status"), ({"status": "posted", "date": "2026-13-45"}, "impossible date"),
                     ({"status": "posted", "date": "10/09/2026"}, "wrong date style"), ({"status": "posted", "date": "2026-10-09T10:00"}, "date with time"),
                     ({"status": "posted", "date": "2026-10-09\n"}, "date with newline"), ({"status": "posted", "date": 20261009}, "date a number"),
                     ({"status": "posted", "date": (today + timedelta(days=30)).isoformat()}, "far-future date"),
                     ({"status": "posted", "date": "1999-01-01"}, "ancient date"), ({"status": "posted", "platform": 5}, "platform a number")):
        r = alex.post("/api/jobs/web-rec-1/posting", json=bad)
        check(f"posting: {why} is a plain 400", r.status_code == 400 and plain(r.json()["detail"]), r.text)
    check("posting: broken body is a 400", alex.post("/api/jobs/web-rec-1/posting", content=b"[", headers={"content-type": "application/json"}).status_code == 400)
    check("posting: strange platform text is reduced to a name", alex.post("/api/jobs/web-rec-1/posting", json={"status": "posted", "platform": "Face book <b>!", "date": yesterday})
          .json()["posted"][-1]["platform"] == "face_book_b")
    check("second user can't read the posting log of her video", other.get("/api/jobs/web-rec-1/posting").status_code == 404)
    check("second user can't write it", other.post("/api/jobs/web-rec-1/posting", json={"status": "posted"}).status_code == 404)
    check("signed out is refused", anon.get("/api/jobs/web-rec-1/posting").status_code == 401)
    check("log file is stored per user, not shared", (DATA / "users" / "alex" / "posting.json").exists() and not (DATA / "users" / "other" / "posting.json").exists())
    check("second user's week is empty", other.get("/api/posting/week").json()["count_this_week"] == 0)
    alex.post("/api/jobs/web-rec-1/posting", json={"status": "draft"})
    check("back to draft forgets the posted entries", alex.get("/api/jobs/web-rec-1/posting").json() == {"status": "draft", "posted": [], "date": None})

    # week: post on known days so the numbers are exact
    monday = today - timedelta(days=today.weekday())
    for jid, day in (("web-rec-1", monday), ("web-tips-1", monday + timedelta(days=1)), ("web-rec-1", monday - timedelta(days=1))):
        alex.post(f"/api/jobs/{jid}/posting", json={"status": "posted", "platform": "instagram_reels", "date": day.isoformat()}) \
            if day <= today + timedelta(days=1) else None
    alex.post("/api/jobs/web-tips-1/posting", json={"status": "posted", "platform": "tiktok", "date": monday.isoformat()})
    w = alex.get("/api/posting/week").json()
    this_week = [p for p in w["posted"]]
    check("week: shape and goal", w["goal_min"] == 3 and w["goal_max"] == 5 and set(w) == {"goal_min", "goal_max", "count_this_week", "posted", "free_days"}, w)
    check("week: only Monday-to-Sunday of this week is counted", all(monday.isoformat() <= p["date"] <= (monday + timedelta(days=6)).isoformat() for p in this_week), this_week)
    check("week: entries have job, title, platform, date", all(set(p) == {"job", "title", "platform", "date"} and p["title"] for p in this_week), this_week)
    check("week: count is distinct videos", w["count_this_week"] == len({p["job"] for p in this_week}), w)
    names7 = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    posted_days = {p["date"] for p in this_week}
    expect_free = [names7[i] for i in range(today.weekday(), 7) if (monday + timedelta(days=i)).isoformat() not in posted_days]
    check("week: free_days are today onward with nothing posted", w["free_days"] == expect_free, (w["free_days"], expect_free))
    check("week needs sign-in", anon.get("/api/posting/week").status_code == 401)

    # ---------- reminder (.ics) ----------
    day = (today + timedelta(days=2)).isoformat()
    r = alex.get("/api/posting/reminder.ics", params={"day": day, "title": "Fall tips; 5 things, \\ done"})
    body = r.text
    check("ics: text/calendar download", r.status_code == 200 and r.headers["content-type"].startswith("text/calendar") and
          "attachment" in r.headers["content-disposition"], r.headers)
    check("ics: CRLF lines, required fields, fixed PRODID, no attendees", body.startswith("BEGIN:VCALENDAR\r\n") and body.endswith("END:VCALENDAR\r\n")
          and "PRODID:-//Alex Video Maker//Post reminder//EN" in body and "ATTENDEE" not in body and "ORGANIZER" not in body and "VERSION:2.0" in body
          and "BEGIN:VEVENT" in body and "UID:" in body and "DTSTAMP:" in body, body)
    d0 = day.replace("-", "")
    check("ics: one hour long on that day", f"DTSTART:{d0}T120000" in body and f"DTEND:{d0}T130000" in body, body)
    check("ics: title is escaped (; , \\)", "SUMMARY:Post your video: Fall tips\\; 5 things\\, \\\\ done" in body, body)
    r = alex.get("/api/posting/reminder.ics", params={"day": day, "title": "Hi\r\nATTENDEE:mailto:evil@example.com\r\nX-INJECT:1\x00\x1b\u2028BEGIN:VALARM"})
    body = r.text
    check("ics: CR/LF/control characters can't inject lines", r.status_code == 200 and "\nATTENDEE" not in body and "\nX-INJECT" not in body and "\nBEGIN:VALARM" not in body
          and "\x00" not in body and "\x1b" not in body and "\u2028" not in body, body)
    lines = body.split("\r\n")
    check("ics: every line is a known field or a continuation", all(l == "" or l.startswith(" ") or l.split(":")[0].split(";")[0] in
          {"BEGIN", "END", "VERSION", "PRODID", "CALSCALE", "METHOD", "UID", "DTSTAMP", "DTSTART", "DTEND", "SUMMARY", "DESCRIPTION"} for l in lines), lines)
    check("ics: no line longer than 75 bytes", all(len(l.encode("utf-8")) <= 75 for l in lines), [l for l in lines if len(l.encode()) > 75])
    r = alex.get("/api/posting/reminder.ics", params={"day": day, "title": "é" * 300})
    lines = r.text.split("\r\n")
    check("ics: long non-ASCII title is folded without breaking characters", r.status_code == 200 and all(len(l.encode()) <= 75 for l in lines)
          and "".join(l[1:] if l.startswith(" ") else l for l in lines).count("é") == 100, r.text[:300])
    r = alex.get("/api/posting/reminder.ics", params={"day": day, "time": "23:30"})
    check("ics: late reminder ends after midnight on the next day", f"DTEND:{(date.fromisoformat(day) + timedelta(days=1)):%Y%m%d}T003000" in r.text, r.text)
    check("ics: no title still works", "SUMMARY:Post your video\r\n" in alex.get("/api/posting/reminder.ics", params={"day": day}).text)
    for bad in ("", "tomorrow", "2026-02-30", "2026-10-09\r\nX:1", "2026-10-9", "9999-01-01", "../etc/passwd"):
        r = alex.get("/api/posting/reminder.ics", params={"day": bad})
        check(f"ics: bad day {bad!r:.30} is a plain 400", r.status_code == 400 and plain(r.json()["detail"]), r.text)
    for bad in ("25:00", "9:00", "12:60", "noon"):
        check(f"ics: bad time {bad} is a 400", alex.get("/api/posting/reminder.ics", params={"day": day, "time": bad}).status_code == 400)
    check("ics needs sign-in", anon.get("/api/posting/reminder.ics", params={"day": day}).status_code == 401)

    # ---------- example video ----------
    r = alex.get("/api/demo")
    dm = r.json()
    check("demo: available, finished-job shape plus example links", r.status_code == 200 and dm["available"] is True and dm["job"]["status"] == "done"
          and dm["job"]["example"] is True and "report" in dm["job"] and "copy" in dm["job"] and "transcript" in dm["job"]
          and dm["job"]["video_urls"] == ["/api/demo/video/1"] and dm["job"]["thumb_url"] == "/api/demo/thumb", list(dm["job"]))
    real = alex.get("/api/jobs/web-rec-1").json()
    check("demo job has the same keys as a normal finished job (minus the optional ones)", set(real) - {"ai_prompt", "step"} <= set(dm["job"]), (set(real), set(dm["job"])))
    r = alex.get("/api/demo/video/1")
    check("demo video plays (mp4 bytes)", r.status_code == 200 and r.headers["content-type"] == "video/mp4" and r.content[4:8] == b"ftyp", r.status_code)
    r = alex.get("/api/demo/video/1", headers={"range": "bytes=0-99"})
    check("demo video supports ranges", r.status_code == 206 and len(r.content) == 100, r.status_code)
    r = alex.get("/api/demo/thumb")
    check("demo thumb is a jpeg", r.status_code == 200 and r.content[:3] == b"\xff\xd8\xff", r.status_code)
    for bad in ("2", "0", "01", "%2e%2e", "..%2f..%2fusers.json", "1.mp4", "x", "-1", "100", "1%00"):
        r = alex.get(f"/api/demo/video/{bad}")
        check(f"demo video/{bad}: not served", r.status_code in (404, 422) and r.headers.get("content-type", "") != "video/mp4", r.status_code)
    for path in ("/api/demo/%2e%2e/jobs/web-rec-1/final.mp4", "/api/demo/thumb/%2e%2e/%2e%2e/users.json", "/api/demo/video/1/%2e%2e/%2e%2e/%2e%2e/_demo.txt", "/api/demo/video/..%5c..%5cusers.json"):
        r = alex.get(path)
        check(f"demo traversal {path[:40]} gets nothing", r.status_code in (404, 422) and b"demo-tour" not in r.content, r.status_code)
    check("demo needs sign-in", anon.get("/api/demo").status_code == 401 and anon.get("/api/demo/video/1").status_code == 401)
    check("demo job is not listed under any user", all(v["job_id"] != "demo-tour-moved" for c in (alex, admin) for v in c.get("/api/videos").json()["videos"]))
    check("demo job can't be fetched, deleted or remade through the normal routes",
          all(c.get("/api/jobs/demo-tour-moved").status_code == 404 and c.delete("/api/jobs/demo-tour-moved").status_code == 404
              and c.post("/api/jobs/demo-tour-moved/feedback", json={"changes": {"caption_size": 80}, "remake": True}).status_code == 404
              and c.get("/api/jobs/demo-tour-moved/final.mp4").status_code == 404 and c.get("/api/jobs/demo-tour-moved/postpack").status_code == 404
              for c in (alex, admin)))
    check("the example video is still on disk after those delete attempts", (OUT / "demo-tour-moved" / "report.json").exists())
    (OUT / "_demo.txt").write_text("web-missing-job")
    check("demo: points at a missing job -> not available", alex.get("/api/demo").json() == {"available": False, "job": None})
    check("demo video/thumb 404 when not available", alex.get("/api/demo/video/1").status_code == 404 and alex.get("/api/demo/thumb").status_code == 404)
    (OUT / "_demo.txt").write_text("../users")
    check("demo: a path in _demo.txt is ignored", alex.get("/api/demo").json()["available"] is False)
    (OUT / "_demo.txt").unlink()
    check("demo: no _demo.txt -> not available", alex.get("/api/demo").json()["available"] is False)
    (OUT / "_demo.txt").write_text("demo-tour-moved")


# ---------- phone app files ----------
r = anon.get("/manifest.webmanifest")
mf = r.json()
check("manifest is public, JSON, right names", r.status_code == 200 and "manifest+json" in r.headers["content-type"] and mf["name"] == "Alex's Video Maker"
      and mf["short_name"] == "Video Maker" and mf["display"] == "standalone" and mf["start_url"] == "/" and mf["scope"] == "/", r.text[:200])
check("manifest colours come from the page's tokens", mf["theme_color"] == "#2c76ae" and mf["background_color"] == "#f3f7fb")
html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
check("  ...and those tokens are really in index.html", "--accent: #2c76ae" in html and "--bg: #f3f7fb" in html)
sizes = {i["sizes"] for i in mf["icons"]}
check("manifest has 192 and 512 icons that load while signed out", {"192x192", "512x512"} <= sizes
      and all(anon.get(i["src"]).status_code == 200 and anon.get(i["src"]).headers["content-type"] == "image/png" for i in mf["icons"]))
from PIL import Image   # noqa: E402
im = Image.open(io.BytesIO(anon.get("/assets/icon-512.png").content))
check("icon is a real 512 PNG on the app's blue", im.size == (512, 512) and im.getpixel((5, 5)) == (44, 118, 174), (im.size, im.getpixel((5, 5))))
r = anon.get("/sw.js")
check("sw.js is public JavaScript with scope '/'", r.status_code == 200 and "javascript" in r.headers["content-type"] and r.headers.get("service-worker-allowed") == "/", r.headers)
sw = r.text
check("service worker only handles the icons and never touches /api/ or videos", "/api/" not in sw.replace("never touches", "") .split("self.addEventListener(\"fetch\"")[1]
      and "SHELL.includes(url.pathname)) return" in sw and 'req.method !== "GET") return' in sw, sw[-400:])
check("only the two icons are served from /assets (even signed in)", alex.get("/assets/make_icons.py").status_code == 404
      and alex.get("/assets/..%2fapp.py").status_code in (404, 400) and alex.get("/assets/icon-1.png").status_code == 404
      and anon.get("/assets/make_icons.py", follow_redirects=False).status_code in (303, 401))
check("other pages still need sign-in", anon.get("/api/videos").status_code == 401 and anon.get("/").status_code in (200, 303) and "/login" in str(anon.get("/", follow_redirects=False).headers.get("location", "/login")))
check("login page is still public", anon.get("/login").status_code == 200)
check("public routes added since the first login are only these six (3D scene for the sign-in page, the link-preview picture)",
      W.PUBLIC - {"/login", "/api/login", "/api/local-reset", "/healthz", "/api/help"} ==
      {"/manifest.webmanifest", "/sw.js", "/assets/icon-192.png", "/assets/icon-512.png", "/assets/og-image.png", "/static/scene3d.js"})
check("the link-preview picture is served signed out, and the sign-in page names it with a full address",
      anon.get("/assets/og-image.png").status_code == 200 and 'content="http://testserver/assets/og-image.png"' in anon.get("/login").text
      and "__ORIGIN__" not in anon.get("/login").text)
check("the scene file is served signed out, the other motion files are not",
      anon.get("/static/scene3d.js", follow_redirects=False).status_code == 200
      and anon.get("/static/gsap.min.js", follow_redirects=False).status_code in (303, 401)
      and anon.get("/static/motion.js", follow_redirects=False).status_code in (303, 401))

print(f"\n{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED: ' + '; '.join(fails)}")
shutil.rmtree(DATA, ignore_errors=True)
sys.exit(1 if fails else 0)
