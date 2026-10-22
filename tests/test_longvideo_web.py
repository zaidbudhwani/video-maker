"""Shorts from a long video, website side: the upload choices, honest time estimate, progress mapping, the YouTube post
routes and who may open them, the remake with 'How many Shorts?', My videos. No AI, no real video is made.
Run:  venv\\Scripts\\python tests\\test_longvideo_web.py
In-process (FastAPI TestClient) with a throwaway data folder. The engine is replaced by a recorder, so nothing of hers
is sent anywhere. The finished job is copied from an engine run in out/ (mode 'highlights')."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = Path(tempfile.mkdtemp(prefix="alex-longvideo-"))
os.environ.update(ALEX_DATA_DIR=str(DATA), ALEX_HOSTED="1", ADMIN_USER="tester", ADMIN_PASSWORD="admin-pass-123",
                  ANTHROPIC_API_KEY="", APP_PASSWORD="", ALEX_AUTO_SLIM="0")

from fastapi.testclient import TestClient   # noqa: E402

from video_tool.config import FFMPEG   # noqa: E402
from web import app as W, auth   # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        fails.append(name)


def client(user=None, pw=None):
    c = TestClient(W.app, base_url="http://testserver")
    if user:
        assert c.post("/api/login", json={"username": user, "password": pw}).status_code == 200
    return c


def plain(msg):
    """No paths, no code words in what she reads."""
    return not any(x in msg for x in ("C:\\", "\\", "/tmp", ".json", "Traceback", "ffmpeg", "ffprobe", "highlights", "HTTP"))


auth.set_user("alex", "alex-pass-1234")
auth.set_user("other", "other-pass-1234")
alex, other, admin, anon = client("alex", "alex-pass-1234"), client("other", "other-pass-1234"), \
    client("tester", "admin-pass-123"), client()
OUT = W.OUT

# ---------- media made with ffmpeg ----------
MEDIA = DATA / "media"
MEDIA.mkdir()


def make_video(path, seconds, fps=1):
    p = subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=s=160x120:d={seconds}:r={fps}",
                        "-f", "lavfi", "-i", f"sine=f=300:d={seconds}", "-shortest", "-c:v", "libx264", "-pix_fmt",
                        "yuv420p", "-c:a", "aac", "-b:a", "24k", str(path)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


tiny = MEDIA / "tiny.mp4"
make_video(tiny, 5)
hundred = MEDIA / "hundred.mp4"
make_video(hundred, 100)
twenty5 = MEDIA / "twenty-five-minutes.mp4"
make_video(twenty5, 25 * 60)
tone = MEDIA / "tone.m4a"
subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=3", "-c:a", "aac", str(tone)], check=True)
check("test videos made (5 s, 100 s, 25 min)", all(f.stat().st_size > 1000 for f in (tiny, hundred, twenty5)))

STARTS = []
_real_start = W._start
W._start = lambda job_id, folder, user, extra=(): STARTS.append({"job": job_id, "folder": Path(folder), "user": user,
                                                                "extra": list(extra)})


def upload(c, clips, voice=None, **extra):
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


def start_body(**extra):
    return {"clips": [{"name": "Talk.mov", "size": 10}], **extra}


# ---------- 1. the 'highlights' extra ----------
r = alex.post("/api/uploads", json=start_body(highlights=3))
check("highlights 3 is accepted", r.status_code == 200, r.text)
pend = json.loads((W.UPLOADS / r.json()["job_id"] / "pending.json").read_text())
check("highlights is stored as a whole number 3", pend["extras"].get("highlights") == 3 and
      isinstance(pend["extras"]["highlights"], int), pend["extras"])
for ok_val in (1, 5, 2.0):
    r = alex.post("/api/uploads", json=start_body(highlights=ok_val))
    check(f"highlights {ok_val} is accepted", r.status_code == 200, r.text)
for none_val in (None, 0):
    r = alex.post("/api/uploads", json=start_body(highlights=none_val))
    pend = json.loads((W.UPLOADS / r.json()["job_id"] / "pending.json").read_text()) if r.status_code == 200 else {}
    check(f"highlights {none_val!r} means an ordinary video (nothing added)", r.status_code == 200 and
          "highlights" not in pend.get("extras", {}), r.text)
for bad in (6, -1, 2.5, "3", True, [], {"n": 3}, "many"):
    r = alex.post("/api/uploads", json=start_body(highlights=bad))
    msg = r.json().get("detail", "") if r.status_code == 400 else ""
    check(f"highlights {bad!r} is a plain 400, never a 500", r.status_code == 400 and "1 to 5" in msg and plain(msg), r.text)

# clean_extras directly
check("clean_extras keeps other choices next to highlights",
      W._clean_extras({"hints": "Ellis Park", "highlights": 4}) == {"hints": "Ellis Park", "highlights": 4})

# ---------- 2. one long video only; no voice-over ----------
r = alex.post("/api/uploads", json={"clips": [{"name": "A.mov", "size": 10}, {"name": "B.mov", "size": 10}], "highlights": 3})
check("two videos with Shorts from a long video: 'Pick just one long video'", r.status_code == 400 and
      r.json()["detail"].startswith("Pick just one long video for this") and plain(r.json()["detail"]), r.text)
r = alex.post("/api/uploads", json={**start_body(highlights=3), "voice": {"name": "v.m4a", "size": 10}})
check("a voice-over with Shorts from a long video is refused in plain words", r.status_code == 400 and
      "voice-over" in r.json()["detail"] and plain(r.json()["detail"]), r.text)
r = alex.post("/api/uploads", json={"clips": [], "highlights": 3})
check("no video at all is still the normal 'Add at least one' message", r.status_code == 400 and "Add at least one" in r.json()["detail"])

# ---------- 3. finish: too short, and a long VIDEO is not held to the voice-over limit ----------
STARTS.clear()
r = upload(alex, [("Quick.mov", tiny)], highlights=3)
check("a 5 second video is 'short enough to be a Short already'", r.status_code == 400 and
      "short enough to be a Short already" in r.json()["detail"] and "A video from my clips" in r.json()["detail"]
      and plain(r.json()["detail"]), r.text)
check("...and nothing was started", not STARTS)
r = upload(alex, [("Talk.mov", hundred)], highlights=2)
check("a 100 second video is accepted", r.status_code == 200, r.text)
check("...and started with the Shorts count in its one-off settings", len(STARTS) == 1 and "--settings" in STARTS[0]["extra"])
if STARTS:
    over = json.loads(Path(STARTS[0]["extra"][STARTS[0]["extra"].index("--settings") + 1]).read_text())
    check("settings file says highlights 2", over.get("highlights") == 2, over)
    check("the 20 minute voice limit is not in play for a 100 s video", True)
STARTS.clear()
r = upload(alex, [("Whole talk.mov", twenty5)], highlights=5)
check("a 25 minute VIDEO is not refused by the 20 minute voice-over limit", r.status_code == 200, r.text)
check("...it is started (not an error)", len(STARTS) == 1)
first_long = STARTS[0] if STARTS else None
STARTS.clear()
r = upload(alex, [("Voice.m4a", tone)])   # a voice recording as the only 'clip'
check("an audio file as a clip is still refused as before", r.status_code == 400, r.text)
r = upload(alex, [("Talk.mov", hundred), ("Other.mov", hundred)], highlights=3)
check("two videos at finish time are refused too", r.status_code == 400 and "just one long video" in r.json()["detail"], r.text)
# the ordinary flow is unchanged: two short clips and no highlights
r = upload(alex, [("A.mov", tiny), ("B.mov", tiny)])
check("ordinary clips still work and carry no highlights", r.status_code == 200 and len(STARTS) == 1 and
      "highlights" not in (json.loads(Path(STARTS[0]["extra"][STARTS[0]["extra"].index("--settings") + 1]).read_text())
                           if "--settings" in STARTS[0]["extra"] else {}), r.text)

# ---------- 4. the legacy one-request route ----------
STARTS.clear()
with open(hundred, "rb") as fh:
    r = alex.post("/api/jobs", files=[("clips", ("Talk.mov", fh, "video/quicktime"))], data={"highlights": "3"})
check("legacy upload with highlights 3 starts", r.status_code == 200 and len(STARTS) == 1, r.text)
with open(hundred, "rb") as fh:
    r = alex.post("/api/jobs", files=[("clips", ("Talk.mov", fh, "video/quicktime"))], data={"highlights": "99"})
check("legacy upload with highlights 99 is a plain 400", r.status_code == 400 and "1 to 5" in r.json()["detail"], r.text)
with open(hundred, "rb") as fh:
    r = alex.post("/api/jobs", files=[("clips", ("Talk.mov", fh, "video/quicktime"))], data={"highlights": "abc"})
check("legacy upload with highlights 'abc' is a plain 400", r.status_code == 400, r.text)

# ---------- 5. the estimate ----------
est, listen = W._long_estimate(600, 3)
check("estimate formula: 10 minutes, 3 Shorts = 0.22*600 + 180 + 20 = 332 s", abs(est - 332) < 0.01 and abs(listen - 132) < 0.01, est)
check("30 minutes with 3 Shorts is about 10 to 13 minutes", 9 * 60 < W._long_estimate(1800, 3)[0] < 13 * 60)
folder = W.UPLOADS / "eta-test"
folder.mkdir(parents=True, exist_ok=True)
shutil.copy2(twenty5, folder / "01.mp4")
check("_eta_minutes reads the video's real length (25 min, 5 Shorts: 0.22*1500+300+20 = 650 s = 11 min)",
      W._eta_minutes(folder, 5) == 11, W._eta_minutes(folder, 5))
check("a normal job's estimate is the old footage-size guess", W._eta_minutes(folder) == max(3, round(2.0 + (twenty5.stat().st_size / 1048576) / 28)))
check("a long job gets the long timeout and a normal one does not", W.RENDER_TIMEOUT_LONG > W.RENDER_TIMEOUT >= 30 * 60)

# ---------- 6. progress lines -> where the page is ----------
job = {"status": "running", "highlights": 3, "video_seconds": 448}
log = "Job x: 1 clip(s)\n0/6 Getting your clips ready...\n"
s0 = W._long_status(job, 3, log)
check("0/6: stage 0, nothing done", s0["stage"] == 0 and s0["progress"] == 0 and s0["heard"] is None, s0)
log += "1/6 Listening to your whole video (this takes a few minutes for a long one)...\n  heard 0:45 of 7:28\n  heard 3:44 of 7:28\n"
s1 = W._long_status(job, 3, log)
check("1/6 with 'heard 3:44 of 7:28': stage 1, 224 of 448 s heard", s1["stage"] == 1 and s1["heard"] == 224 and s1["heard_of"] == 448, s1)
check("...the bar is part-way and moves with it", 0.1 < s1["progress"] < 0.4, s1["progress"])
check("...a honest minutes-left (a few, not zero)", 3 <= s1["minutes_left"] <= 6, s1["minutes_left"])
log += "  heard 7:28 of 7:28\n2/6 Choosing the best moments...\n3/6 Cutting your Shorts out of the video...\n"
s3 = W._long_status(job, 3, log)
check("3/6: stage 3 and further along than listening", s3["stage"] == 3 and s3["progress"] > s1["progress"], s3)
log += "4/6 Making Short 2 of 3 (framing, captions, titles)...\n"
s4 = W._long_status(job, 3, log)
check("4/6 Making Short 2 of 3: stage 4, short 2 of 3", s4["stage"] == 4 and s4["short"] == 2 and s4["of"] == 3, s4)
check("...progress is past the first Short's share", s4["progress"] > s3["progress"], s4)
log += "5/6 Gathering your Shorts...\n6/6 Writing the YouTube post for the whole video...\n"
s6 = W._long_status(job, 3, log)
check("6/6: nearly done, never 100 percent before the report exists", s6["stage"] == 6 and 0.9 <= s6["progress"] <= 0.97, s6)
check("a video longer than an hour in the log ('heard 1:05:00 of 1:10:00') is read", W._long_status(
    {"status": "running", "highlights": 1}, 1, "1/6 Listening...\n  heard 1:05:00 of 1:10:00\n")["heard"] == 3900)
check("a queued job reports no progress yet", W._long_status({"status": "queued", "highlights": 3, "video_seconds": 448}, 3, "")["progress"] == 0)
check("a log with nothing in it is still valid", W._long_status({"status": "running", "highlights": 2}, 2, "")["stage"] == 0)

# through the status route, with a real log file in out/
(OUT / "web-live-1.owner").write_text("alex")
(OUT / "web-live-1.log").write_text("Job: 1 clip(s)\n0/6 Getting your clips ready...\n1/6 Listening to your whole video (...)...\n  heard 1:00 of 7:28\n",
                                    encoding="utf-8")
W.jobs["web-live-1"] = {"status": "running", "queued_at": time.time(), "eta_minutes": 6, "highlights": 3, "video_seconds": 448}
r = alex.get("/api/jobs/web-live-1")
j = r.json()
check("job status carries a 'long' block with the Shorts count", r.status_code == 200 and j.get("long", {}).get("shorts") == 3 and
      j["long"]["stage"] == 1 and j["long"]["heard"] == 60, j)
check("...and still the old 'step' line", "Listening" in j.get("step", ""), j.get("step"))
check("a normal job has no 'long' block", "long" not in (W.jobs.setdefault("web-live-2", {"status": "running", "queued_at": time.time()}) and
      (OUT / "web-live-2.owner").write_text("alex") and alex.get("/api/jobs/web-live-2").json()), "")

# a long job's timeout is the long one
seen = {}
_orig_run = subprocess.run


class _Done:
    returncode = 1


def fake_run(cmd, **kw):
    seen["timeout"] = kw.get("timeout")
    return _Done()


def fake_tree(cmd, timeout, **kw):     # the website runs the pipeline through proc.run_tree (it stops the whole tree on a timeout)
    seen["timeout"] = timeout
    return _Done()


W.subprocess.run = fake_run
_orig_tree = W.proc.run_tree
W.proc.run_tree = fake_tree
try:
    (OUT / "web-to-1.owner").write_text("alex")
    W.jobs["web-to-1"] = {"status": "queued", "highlights": 2}
    W._run_inner("web-to-1", DATA, "alex", [])
    t_long = seen["timeout"]
    W.jobs["web-to-2"] = {"status": "queued"}
    W._run_inner("web-to-2", DATA, "alex", [])
    t_norm = seen["timeout"]
finally:
    W.subprocess.run = _orig_run
    W.proc.run_tree = _orig_tree
check("a Shorts job gets the long timeout, an ordinary one the old one", t_long == W.RENDER_TIMEOUT_LONG and t_norm == W.RENDER_TIMEOUT,
      (t_long, t_norm))

# ---------- 7. a finished Shorts job (copied from an engine run) ----------
src = None
for d in (sorted((ROOT / "out").iterdir()) if (ROOT / "out").is_dir() else []):
    try:
        if src is None and d.is_dir() and json.loads((d / "report.json").read_text(encoding="utf-8")).get("mode") == "highlights" \
                and (d / "longform.json").exists() and (d / "long.srt").exists() and (d / "final_part1.mp4").exists():
            src = d
    except (OSError, ValueError):
        pass
if src is None:
    print("SKIPPED the finished-Shorts-job checks: no saved Shorts run in out/ on this computer (videos are not in the repository)")
    print()
    if fails:
        print(f"{len(fails)} FAILED: " + "; ".join(fails))
        sys.exit(1)
    print("ALL PASSED (finished-job checks skipped)")
    sys.exit(0)
check("an engine run in Shorts mode exists to copy from", src is not None)


def build_fixture(job_id, owner, shorts=3):
    d = OUT / job_id
    d.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.is_file() and f.name in ("report.json", "copy.json", "longform.json", "long.srt", "transcript.txt", "words.json",
                                      "captions_part1.ass", "final_part1.mp4", "ai_prompt.txt"):
            if f.suffix in (".mp4", ".ass"):   # big files are linked; the small ones are copied: they get rewritten below
                try:
                    os.link(f, d / f.name)
                    continue
                except OSError:
                    pass
            shutil.copy2(f, d / f.name)   # never a link: writing a link would change the engine's own run
    rep = json.loads((d / "report.json").read_text(encoding="utf-8"))
    copy = json.loads((d / "copy.json").read_text(encoding="utf-8"))
    base, cbase = rep["parts"][0], copy["parts"][0]
    rep["parts"], copy["parts"] = [], []
    for k in range(1, shorts + 1):
        if k > 1:
            shutil.copy2(d / "final_part1.mp4", d / f"final_part{k}.mp4")
            shutil.copy2(d / "captions_part1.ass", d / f"captions_part{k}.ass")
        rep["parts"].append({**base, "file": f"final_part{k}.mp4", "start_at": 30 + 60 * k, "end_at": 62 + 60 * k,
                             "why": f"A clear answer, number {k}."})
        copy["parts"].append({**cbase, "label": f"Short {k}", "part": k, "of": shorts, "file": f"final_part{k}.mp4",
                              "titles": [f"Short {k} title one", f"Short {k} title two", f"Short {k} title three"]})
    rep["settings"] = {**rep.get("settings", {}), "highlights": shorts}
    rep["original"] = 448
    (d / "report.json").write_text(json.dumps(rep), encoding="utf-8")
    (d / "copy.json").write_text(json.dumps(copy), encoding="utf-8")
    (OUT / f"{job_id}.owner").write_text(owner)
    (OUT / f"{job_id}.settings.json").write_text(json.dumps({"highlights": shorts}))
    return d


if src:
    # the source folder of the upload, so remakes can find the files
    srcdir = W.UPLOADS / "web-hl-src"
    srcdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(hundred, srcdir / "01.mp4")
    (srcdir / "original_names.json").write_text(json.dumps(["Home Value.mp4"]))
    build_fixture("web-hl-1", "alex")
    (OUT / "web-hl-1.source").write_text(str(srcdir))
    fixture_ok = True
else:
    fixture_ok = False

if fixture_ok:
    r = alex.get("/api/jobs/web-hl-1")
    j = r.json()
    check("finished job answers with report mode 'highlights' and 3 Shorts", r.status_code == 200 and
          j["report"]["mode"] == "highlights" and len(j["report"]["parts"]) == 3, r.text[:200])
    check("...and no place on the server's disk", "final" not in j["report"], j["report"].keys())
    check("...a Short has where it came from and why", j["report"]["parts"][1]["start_at"] == 150 and j["report"]["parts"][1]["why"])
    check("...and a 'long' block only while it is being made", "long" not in j)

    # --- YouTube post routes
    r = alex.get("/api/jobs/web-hl-1/longform")
    check("owner reads the YouTube post kit", r.status_code == 200 and len(r.json()["titles"]) == 3 and
          "description" in r.json() and "chapters" in r.json() and "tags" in r.json(), r.text[:150])
    r = alex.get("/api/jobs/web-hl-1/long.srt")
    cd = r.headers.get("content-disposition", "")
    check("owner downloads long.srt as an attachment with a plain name", r.status_code == 200 and "attachment" in cd
          and cd.encode("ascii") and cd.rstrip('"').endswith("-captions.srt") and "web-hl" not in cd, cd)
    check("...it is subtitle text (numbered cues with times)", "-->" in r.text and r.text.lstrip().startswith("1"), r.text[:60])
    check("...served as text, not a page", "text/html" not in r.headers.get("content-type", ""), r.headers.get("content-type"))
    check("someone else gets a 404 for the kit", other.get("/api/jobs/web-hl-1/longform").status_code == 404)
    check("someone else gets a 404 for long.srt", other.get("/api/jobs/web-hl-1/long.srt").status_code == 404)
    check("admin can read the kit and the srt", admin.get("/api/jobs/web-hl-1/longform").status_code == 200 and
          admin.get("/api/jobs/web-hl-1/long.srt").status_code == 200)
    check("signed-out is a 401", anon.get("/api/jobs/web-hl-1/longform").status_code == 401 and
          anon.get("/api/jobs/web-hl-1/long.srt").status_code == 401)
    check("an id that does not exist is a 404", alex.get("/api/jobs/web-nope/longform").status_code == 404 and
          alex.get("/api/jobs/web-nope/long.srt").status_code == 404)
    check("a video with no long-video post has none (404 in plain words)", (lambda r: r.status_code == 404 and plain(r.json()["detail"]))(
        (build_fixture("web-hl-bare", "alex", 1), (OUT / "web-hl-bare" / "longform.json").unlink(), (OUT / "web-hl-bare" / "long.srt").unlink(),
         alex.get("/api/jobs/web-hl-bare/longform"))[-1]))
    check("a path trick is not served", alex.get("/api/jobs/web-hl-1/..%2Freport.json").status_code in (404, 400, 422))

    # --- post pack: the engine's files ride along
    r = alex.get("/api/jobs/web-hl-1/postpack")
    import io, zipfile
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist() if r.status_code == 200 else []
    check("post pack zips the Shorts, long.srt and the YouTube post text", r.status_code == 200 and "long.srt" in names and
          "youtube-long-video.txt" in names and sum(n.endswith(".mp4") for n in names) == 3, names)

    # --- a Short downloads with a Short name, not a Part name
    r = alex.get("/api/jobs/web-hl-1/final_part2.mp4")
    cd = r.headers.get("content-disposition", "")
    check("a Short's file is named '...-short2.mp4'", r.status_code == 200 and "-short2.mp4" in cd and "-part" not in cd, cd)

    # --- My videos: one video with 3 Shorts
    rows = alex.get("/api/videos").json()["videos"]
    row = next(x for x in rows if x["job_id"] == "web-hl-1")
    check("My videos shows one video with 3 Shorts (3 download files)", row["shorts"] == 3 and row["parts"] == 3 and
          row["files"] == ["final_part1.mp4", "final_part2.mp4", "final_part3.mp4"] and row["mode"] == "highlights", row)
    check("...and the thumbnail works", alex.get("/api/jobs/web-hl-1/thumb.jpg").status_code == 200)

    # --- class results: one video, not three
    from video_tool import results
    summ = results.summarise([row], [])
    allg = next(g for g in summ["groups"] if g["user"] == "All")
    check("class results count the long video job as ONE video", allg["videos"] == 1 and len(summ["videos"]) == 1, allg)
    r = admin.get("/api/admin/results")
    check("the admin results page still answers", r.status_code == 200, r.text[:100])

    # --- remake: How many Shorts?
    STARTS.clear()
    r = alex.post("/api/jobs/web-hl-1/feedback", json={"changes": {"highlights": 2}, "remake": True})
    j = r.json()
    check("remake with 2 Shorts starts a new job", r.status_code == 200 and j.get("job_id") and len(STARTS) == 1, r.text)
    if STARTS:
        ex = STARTS[0]["extra"]
        over = json.loads(Path(ex[ex.index("--settings") + 1]).read_text())
        check("...with highlights 2 and the earlier job's transcript reused (--reuse)", over.get("highlights") == 2 and
              "--reuse" in ex and ex[ex.index("--reuse") + 1].endswith("web-hl-1"), ex)
        check("...and the plain description says 2 Shorts", any("2" in a and "Short" in a for a in j.get("applied", [])), j)
    for bad in (0, 6, "3", True, -2, None):
        STARTS.clear()
        r = alex.post("/api/jobs/web-hl-1/feedback", json={"changes": {"highlights": bad, "caption_size": 88}, "remake": True})
        ex = STARTS[0]["extra"] if STARTS else []
        over = json.loads(Path(ex[ex.index("--settings") + 1]).read_text()) if "--settings" in ex else {}
        check(f"remake with How many Shorts = {bad!r} keeps the old count and still applies the caption size",
              r.status_code == 200 and over.get("highlights") == 3 and over.get("caption_size") == 88, (r.text[:150], over))
    STARTS.clear()
    r = alex.post("/api/jobs/web-hl-1/feedback", json={"changes": {"caption_size": 90}, "remake": True})
    ex = STARTS[0]["extra"] if STARTS else []
    over = json.loads(Path(ex[ex.index("--settings") + 1]).read_text()) if "--settings" in ex else {}
    check("an ordinary caption change on a Shorts job keeps highlights and reuses the transcript",
          r.status_code == 200 and over.get("highlights") == 3 and over.get("caption_size") == 90 and "--reuse" in ex, over)
    # a normal video must not become a Shorts job through the same field
    fx = OUT / "web-norm-1"
    fx.mkdir()
    (fx / "report.json").write_text(json.dumps({"job_id": "web-norm-1", "mode": "own_audio", "parts": [{"file": "final.mp4", "length": 20}],
                                                "length": 20, "settings": {}}))
    (OUT / "web-norm-1.owner").write_text("alex")
    (OUT / "web-norm-1.source").write_text(str(srcdir))
    STARTS.clear()
    r = alex.post("/api/jobs/web-norm-1/feedback", json={"changes": {"highlights": 3, "caption_size": 88}, "remake": True})
    ex = STARTS[0]["extra"] if STARTS else []
    over = json.loads(Path(ex[ex.index("--settings") + 1]).read_text()) if "--settings" in ex else {}
    check("How many Shorts is ignored for an ordinary video", r.status_code == 200 and "highlights" not in over, over)
    # remembering it for later videos must not happen
    from video_tool import settings as S
    check("'highlights' is never one of the settings she can save for every future video", "highlights" not in S.REMEMBER_KEYS)

    # --- retry keeps the Shorts choice
    STARTS.clear()
    W.jobs.pop("web-hl-1", None)
    r = alex.post("/api/jobs/web-hl-1/retry")
    ex = STARTS[0]["extra"] if STARTS else []
    over = json.loads(Path(ex[ex.index("--settings") + 1]).read_text()) if "--settings" in ex else {}
    check("retry of a Shorts job keeps highlights", r.status_code == 200 and over.get("highlights") == 3, (r.text, over))

    # --- reorder is refused politely (one video: nothing to reorder)
    r = alex.post("/api/jobs/web-hl-1/reorder", json={"order": [1]})
    check("reorder on a one-video job is a plain 400, not a crash", r.status_code == 400 and plain(r.json()["detail"]), r.text)

    # --- delete removes the Shorts job and its files
    build_fixture("web-hl-del", "alex", 2)
    r = alex.delete("/api/jobs/web-hl-del")
    check("delete works for a Shorts job", r.status_code == 200 and not (OUT / "web-hl-del").exists())

# ---------- 8. the real server code path for status of a job that was in progress when the site restarted ----------
(OUT / "web-lost-1.owner").write_text("alex")
(OUT / "web-lost-1.settings.json").write_text(json.dumps({"highlights": 2}))
r = alex.get("/api/jobs/web-lost-1")
check("a Shorts job lost in a restart says so in plain words, with a retry", r.status_code == 200 and r.json()["status"] == "failed"
      and plain(r.json()["error"]), r.text[:200])

W._start = _real_start
print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails))
    sys.exit(1)
print("ALL PASSED")
