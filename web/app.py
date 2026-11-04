"""Upload page backend. Run: venv\\Scripts\\python -m uvicorn web.app:app --port 8000

Each upload runs the normal command-line pipeline in a subprocess, so the web
page and the CLI always behave the same. One video renders at a time.
"""
import importlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import types
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

from fastapi.exceptions import RequestValidationError
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from video_tool.config import DATA_DIR
from . import auth
from . import review
from .interpret import clean_values, describe, interpret_full

ROOT = Path(__file__).resolve().parent.parent
OUT = DATA_DIR / "out"
OUT.mkdir(parents=True, exist_ok=True)
UPLOADS = OUT / "_uploads"
from video_tool.config import AUDIO_EXT, VIDEO_EXT
from video_tool import proc
JOB_RE = re.compile(r"^[a-z0-9-]+\Z")   # \Z, not $: a trailing newline must not slip through
SERVE = {"copy.json": "application/json", "ai_prompt.txt": "text/plain"}
VIDEO_OUT = re.compile(r"^final(_part\d{1,2})?\.mp4$")
PUBLIC = {"/login", "/api/login", "/api/local-reset", "/healthz", "/api/help",
          "/manifest.webmanifest", "/sw.js", "/assets/icon-192.png", "/assets/icon-512.png", "/assets/og-image.png", "/static/scene3d.js"}

app = FastAPI(title="Alex Video Tool")
auth.bootstrap()


KEEP_AWAKE = {"on": False}   # shown on the admin health card


def _keep_awake():
    """Windows: ask the computer not to go to sleep while the site is running, so Alex's link doesn't die when the
    laptop is left alone. Closing the lid or pressing the power button still works; stopping the site lets it sleep again."""
    if os.name != "nt":
        return
    KEEP_AWAKE["on"] = True
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001

    def hold():
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        threading.Event().wait()   # the request lasts exactly as long as this thread lives

    threading.Thread(target=hold, daemon=True, name="keep-awake").start()


_keep_awake()


def _auto_slim():
    """Once at start: drop the working files of videos older than 14 days. Finished videos, titles, captions and
    everything a remake needs stay. Set ALEX_AUTO_SLIM=0 to turn it off."""
    if os.getenv("ALEX_AUTO_SLIM", "1") == "0":
        return
    try:
        import maintain
        freed = sum(maintain.slim_job(d, True) for d, _ in maintain.old_jobs(14) if (d / "report.json").exists())
        if freed:
            print(f"Tidied up {maintain.human(freed)} of old working files (finished videos untouched).", flush=True)
    except Exception as e:   # housekeeping must never stop the site
        print(f"Tidy-up skipped: {e}", flush=True)


def _tidy_uploads(max_age_days=2, now=None):
    """Remove uploads that were never made into a video (a dropped connection, a file that wouldn't open) after a couple
    of days, and keep the click and failure logs from growing for ever. Returns how many upload folders were removed."""
    now = now or time.time()
    gone = 0
    try:
        for d in UPLOADS.iterdir():
            if d.is_dir() and not (OUT / f"{d.name}.source").exists() and now - d.stat().st_mtime > max_age_days * 86400:
                shutil.rmtree(d, ignore_errors=True)
                (OUT / f"{d.name}.owner").unlink(missing_ok=True)
                gone += 1
    except OSError:
        pass
    for log in (OUT / "events.jsonl", OUT / "failures.jsonl"):
        try:
            lines = log.read_text(encoding="utf-8").splitlines()
            if len(lines) > 20000:
                log.write_text("\n".join(lines[-10000:]) + "\n", encoding="utf-8")
        except OSError:
            pass
    return gone


def _housekeeping():
    _auto_slim()
    _tidy_uploads()


threading.Thread(target=_housekeeping, daemon=True, name="auto-slim").start()


def _hosted(request):
    """Reached over the internet (tunnel/host), or running as a hosted service."""
    return bool(os.getenv("ALEX_HOSTED")) or "cf-connecting-ip" in request.headers         or "x-forwarded-for" in request.headers


def _wants_login(request):
    from dotenv import dotenv_values
    if _hosted(request):
        return True    # reached over the internet: always sign in, whatever .env says
    if (dotenv_values(ROOT / ".env").get("PASSWORD_OFF") or "").strip().lower() == "true":
        return False   # testing only, on this computer
    try:
        return bool(auth.load_users())
    except auth.UsersFileError:
        return True    # accounts exist but can't be read: never fall open into "no login"


@app.exception_handler(RequestValidationError)
async def _plain_validation(request: Request, exc: RequestValidationError):
    """A form or address with a wrong value gets a plain sentence, not the framework's technical list."""
    return JSONResponse({"detail": "That request wasn't understood. Please try again."}, status_code=400)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")          # nobody can put the site inside another page
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    kind = resp.headers.get("content-type", "")
    if request.url.path.startswith("/api/") or kind.startswith("text/html"):
        # Her data, her videos and the pages are never kept by a proxy or a CDN (a CDN caches by ".mp4" or ".jpg",
        # whoever is asking), so the video and picture files get the same header as the answers
        resp.headers["Cache-Control"] = "private, no-store"
    resp.headers.setdefault("Content-Security-Policy",
                            "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
                            "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                            "font-src 'self' https://fonts.gstatic.com data:; connect-src 'self'; frame-ancestors 'none'; "
                            "base-uri 'self'; form-action 'self'")
    return resp


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC:
        return await call_next(request)
    if not _wants_login(request):
        # The owner on their own computer: no login. So the request must really come from a page on this computer:
        # a local name in the address (not a web page tricking the browser by DNS rebinding) and, for anything that
        # changes something, no other web site as the sender (a cross-site form post).
        if not _local_host_name(request):
            return JSONResponse({"detail": "This address can't be used. Open the Video Maker from http://localhost:8000."},
                                status_code=403)
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin.split("://", 1)[-1] != request.headers.get("host"):
            return JSONResponse({"detail": "That request came from another web page, so it was refused."}, status_code=403)
        request.state.user = auth.LOCAL_USER
        return await call_next(request)
    user = auth.read_cookie(request.cookies.get(auth.COOKIE))
    if not user:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Please sign in again."}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    request.state.user = user
    resp = await call_next(request)
    if (auth.cookie_needs_refresh(request.cookies.get(auth.COOKIE))
            and path not in ("/api/logout", "/api/change-password") and "set-cookie" not in resp.headers):
        _set_session(resp, request, user)   # a fresh 90 days, handed out at most once a day
    return resp


def _client_ip(request):
    """Who is asking, for the sign-in limiter. The forwarded-for headers are only believed when the request really
    came from this computer (the tunnel or a local proxy); anyone else could write anything in them. Behind a
    hosting proxy everyone shares the proxy's address, so the limit is effectively per name, which is what we want
    (the Dockerfile runs uvicorn with --no-proxy-headers so a typed X-Forwarded-For can't pose as another address)."""
    host = request.client.host if request.client else ""
    if host in ("127.0.0.1", "::1", "localhost"):
        fwd = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
        return fwd or host
    return host


def _set_session(resp, request, name):
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp.set_cookie(auth.COOKIE, auth.make_cookie(name), max_age=auth.SESSION_DAYS * 86400,
                    httponly=True, samesite="lax", secure=secure)


def _help_contact():
    """The line telling her whom to ask (HELP_CONTACT in .env or the host's settings); empty if not set."""
    val = (os.getenv("HELP_CONTACT") or "").strip()
    if not val:
        from dotenv import dotenv_values
        val = (dotenv_values(ROOT / ".env").get("HELP_CONTACT") or "").strip()
    return val


async def _json_body(request):
    """The request body as a dict, or a plain 400."""
    # Only a JSON body. A web page on another site can send "text/plain" without asking first, but not this.
    if not (request.headers.get("content-type") or "").lower().startswith("application/json"):
        raise HTTPException(400, "That request wasn't understood. Please try again.")
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(400, "That request wasn't understood. Please try again.")
    return body


def _user_dir(user):
    return ROOT if user == auth.LOCAL_USER else DATA_DIR / "users" / user


def _prefs_file(user):
    return ROOT / "prefs.json" if user == auth.LOCAL_USER else _user_dir(user) / "prefs.json"


def _owner_of(job_id):
    try:
        return (OUT / f"{job_id}.owner").read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _mine(request, job_id):
    """Her own videos only. Old videos from before accounts belong to nobody: admin only."""
    _check_id(job_id)
    me = request.state.user
    owner = _owner_of(job_id)
    if owner != me and not auth.is_admin(me):
        raise HTTPException(404, "Job not found.")
    if job_id == _demo_id():
        raise HTTPException(404, "Job not found.")   # the example video is read-only: only /api/demo shows it
    return owner


def _with_origin(page, request):
    """The link-preview image must be a full address; the public link changes at each restart, so it is filled in per request."""
    return page.replace("__ORIGIN__", str(request.base_url).rstrip("/"))


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return _with_origin((Path(__file__).parent / "login.html").read_text(encoding="utf-8"), request)


@app.post("/api/login")
async def login(request: Request):
    body = await _json_body(request)
    name = await run_in_threadpool(auth.check_login, _client_ip(request), str(body.get("username", "")),
                                   str(body.get("password", "")))
    if name is None:
        raise HTTPException(429, "Too many tries. Wait a minute and try again.")
    if not name:
        raise HTTPException(401, "That username or password isn't right.")
    resp = JSONResponse({"ok": True, "user": name})
    _set_session(resp, request, name)
    return resp


@app.post("/api/change-password")
async def change_password(request: Request):
    """Body: {"current", "new"}. Signed-in people change their own password; the session carries on."""
    user = request.state.user
    if user == auth.LOCAL_USER:
        raise HTTPException(400, "There is no password on this computer to change.")
    body = await _json_body(request)
    cur, new = body.get("current"), body.get("new")
    if not isinstance(cur, str) or not isinstance(new, str) or not cur or not new:
        raise HTTPException(400, "Please type your current password and a new one.")
    ok = await run_in_threadpool(auth.check_login, _client_ip(request), user, cur)   # same limiter as signing in
    if ok is None:
        raise HTTPException(429, "Too many tries. Wait a minute and try again.")
    if not ok:
        raise HTTPException(400, "That isn't your current password.")
    if len(new) < 8:
        raise HTTPException(400, "Your new password needs at least 8 characters.")
    if new == cur:
        raise HTTPException(400, "Your new password has to be different from the old one.")
    try:
        await run_in_threadpool(auth.set_user, user, new, None, 8)
    except ValueError as e:
        raise HTTPException(400, str(e))
    resp = JSONResponse({"ok": True})
    _set_session(resp, request, user)   # the old cookie stops working when the password changes: hand over a new one
    return resp


@app.get("/api/help")
def help_info():
    """Public: whom to ask for help, so even the sign-in page can say it."""
    return {"contact": _help_contact()}


def _local_host_name(request):
    """The page was reached by a local name (localhost, 127.0.0.1, [::1]). A web page that tricks the browser into
    talking to this computer under its own name (DNS rebinding) is refused."""
    hdr = (request.headers.get("host") or "").strip().lower()
    name = hdr[:hdr.index("]") + 1] if hdr.startswith("[") else hdr.rsplit(":", 1)[0] if ":" in hdr else hdr
    return name in ("localhost", "127.0.0.1", "[::1]")


def _on_this_computer(request):
    """Only someone sitting at the computer running the tool, never a visitor through the public link."""
    host = request.client.host if request.client else ""
    if _hosted(request) or host not in ("127.0.0.1", "::1", "localhost"):
        return False
    return _local_host_name(request)


@app.get("/api/local-reset")
def local_reset_info(request: Request):
    """Lets the login page offer "Forgot password?" on the owner's own computer only."""
    if not _on_this_computer(request):
        return {"available": False}
    try:
        return {"available": True, "users": sorted(auth.load_users())}
    except auth.UsersFileError:
        return {"available": True, "users": []}


@app.post("/api/local-reset")
async def local_reset(request: Request):
    """Pick a new password for an account, then sign straight in. This computer only."""
    if not _on_this_computer(request):
        raise HTTPException(403, "Passwords can only be reset on the computer running the Video Maker.")
    origin = request.headers.get("origin")
    if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
        raise HTTPException(403, "That request came from another web page, so it was refused.")
    body = await _json_body(request)
    try:
        name = await run_in_threadpool(auth.set_user, str(body.get("username", "")), str(body.get("password", "")))
    except ValueError as e:
        raise HTTPException(400, str(e))
    resp = JSONResponse({"ok": True, "user": name})
    _set_session(resp, request, name)
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    return resp


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    u = request.state.user
    return {"user": u, "admin": auth.is_admin(u), "local": u == auth.LOCAL_USER, "help": _help_contact(),
            "show_tips": _show_tips(u)}


def _show_tips(user):
    """The three first-run tips: only for an account that has never made a video and never closed them."""
    if review.tips_seen(OUT, user):
        return False
    return not any(_read(f) == user for f in OUT.glob("*.owner"))


@app.post("/api/tips-seen")
def tips_done(request: Request):
    """She closed the first-run tips: never show them to this account again (on any device)."""
    try:
        review.mark_tips_seen(OUT, request.state.user)
    except OSError:
        raise HTTPException(503, "I couldn't save that just now. Please try again.")
    return {"ok": True}


jobs = {}                      # job_id -> {"status": ..., "error": ..., "source": upload folder}
render_lock = threading.Lock()  # one render at a time: it uses the whole CPU
FEEDBACK_LOG = OUT / "feedback.jsonl"
feedback_lock = threading.Lock()


# A Windows path (C:\Users\...) or a long Unix one: shown to her as just the file name
WIN_PATH = re.compile(r"[A-Za-z]:[\\/][^\n\"']+|(?:/[\w.-]+){3,}")


def _her_name_for(path, folder):
    """'Clip 3 (IMG_4521.MOV)' or 'your voice-over (Tour audio.m4a)' instead of a path she never saw."""
    name = Path(str(path).strip()).name
    try:
        orig = json.loads((Path(folder) / "original_names.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        orig = []
    stem = Path(name).stem
    if stem.isdigit():
        i = int(stem)
        return f"Clip {i} ({orig[i - 1]})" if 0 < i <= len(orig) else f"Clip {i}"
    if stem == "voice":
        return f"your voice-over ({orig[-1]})" if orig else "your voice-over"
    return None if Path(name).suffix == "" else name   # a folder isn't worth naming


def _friendly_error(text, folder):
    """What went wrong, in her words: no folder paths, files called what she called them."""
    stopped = text.split("STOPPED:", 1)
    if len(stopped) < 2:   # a crash, not a problem with her files: don't show a traceback
        return ("Something went wrong on our side while making this video. Your files are fine. "
                "Please try again, and if it happens twice let us know.")
    lines, which = [], None
    for line in stopped[1].strip().splitlines():
        if line.strip().startswith("File:"):
            which = _her_name_for(line.split("File:", 1)[1], folder)
        elif line.strip().startswith("[") or re.search(r"\.\w{2,4}: \w", line):
            continue   # raw ffmpeg output ("moov atom not found"): detail for the team, not for her
        else:
            lines.append(WIN_PATH.sub(lambda m: Path(m.group(0)).name, line))
    msg = "\n".join(lines).strip()
    if re.search(r"Can't read this file|no readable length", msg) and "re-download" not in msg.lower():
        msg += "\nIt may not have finished downloading. Re-download it and add it again."
    return msg +(f"\nWhich file: {which}" if which else "")


def _log_failure(job_id, user, shown, text):
    """Keep the technical detail for the team (out/failures.jsonl); she only sees the plain message."""
    with open(OUT / "failures.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), "job": job_id, "user": user,
                             "shown": shown, "detail": text[-3000:]}, ensure_ascii=False) + "\n")


RENDER_TIMEOUT = 30 * 60   # a render that takes longer than this is stuck
RENDER_TIMEOUT_LONG = 120 * 60   # Shorts from a long video: listening alone is about a fifth of the video's length

# Measured on this computer (see the engine notes): seconds = 0.22 x the video's length + 60 per Short + 20
LONG_LISTEN = 0.22
LONG_PER_SHORT = 60
LONG_FIXED = 20
LONG_MIN_SECONDS = 75.0   # a video this short or shorter is already a Short (video_tool.highlights.MIN_LONG)
LONG_MAX_SECONDS = 4 * 3600   # longer than this cannot be finished inside RENDER_TIMEOUT_LONG with the render lock held


def _long_estimate(video_seconds, shorts):
    """(total seconds, seconds of listening) for Shorts from a long video."""
    listen = LONG_LISTEN * max(0.0, float(video_seconds))
    return listen + LONG_PER_SHORT * max(1, int(shorts)) + LONG_FIXED, listen


def _long_video_seconds(folder):
    """How long the one long video in an upload folder is (None when it can't be read)."""
    clips, _ = _source_files(folder)
    if not clips:
        return None
    info = _loose_probe(clips[0])
    try:
        return float(info["format"]["duration"])
    except (TypeError, KeyError, ValueError):
        return None


def _eta_minutes(folder, highlights=0):
    """A rough 'usually about N minutes', from how much footage there is. Talking videos take longer."""
    if highlights:
        length = _long_video_seconds(folder)
        if length:
            return max(2, math.ceil(_long_estimate(length, highlights)[0] / 60))
        mb = sum(f.stat().st_size for f in Path(folder).glob("*") if f.is_file()) / 1048576
        return max(3, round(2.0 + mb / 28))   # unreadable: the footage-size guess
    mb = sum(f.stat().st_size for f in Path(folder).glob("*") if f.is_file()) / 1048576
    has_voice = any(f.stem == "voice" for f in Path(folder).glob("voice.*"))
    return max(2, round(1.0 + mb / 70)) if has_voice else max(3, round(2.0 + mb / 28))


def _run(job_id, folder, user, extra=()):
    try:
        _run_inner(job_id, folder, user, extra)
    except Exception as e:   # anything unexpected here must not leave her page spinning forever
        jobs[job_id].update(status="failed", can_retry=True,
                            error="Something went wrong on our side while making this video. Your files are fine. "
                                  "Please try again, and if it happens twice let us know.")
        try:
            _log_failure(job_id, user, jobs[job_id]["error"], f"{type(e).__name__}: {e}")
        except OSError:
            pass


def _pipeline_command(folder, job_id, extra=()):
    return [sys.executable, "-u", "-W", "ignore", "-m", "video_tool.pipeline", str(folder), "--job-id", job_id, *extra]


def _run_inner(job_id, folder, user, extra=()):
    log = OUT / f"{job_id}.log"
    wide = _own_settings(job_id).get("orientation") == "horizontal"   # a YouTube video can be 15 minutes long
    limit = RENDER_TIMEOUT_LONG if jobs[job_id].get("highlights") or wide else RENDER_TIMEOUT
    # The pipeline reads this person's saved settings, nobody else's
    env = {**os.environ, "ALEX_PREFS_FILE": str(_prefs_file(user))}
    with render_lock, open(log, "w", encoding="utf-8") as fh:
        jobs[job_id]["status"] = "running"
        jobs[job_id]["started_at"] = time.time()
        try:
            # run_tree, not subprocess.run: on a timeout it stops the pipeline AND the ffmpeg processes it started, and
            # waits for them to be gone before the lock is released and the next video can start
            p = proc.run_tree(_pipeline_command(folder, job_id, extra), timeout=limit, cwd=ROOT, env=env, stdout=fh,
                              stderr=subprocess.STDOUT)
        except subprocess.TimeoutExpired:
            fh.write("\nTimed out after %d minutes\n" % (limit // 60))
            p = types.SimpleNamespace(returncode=1)
    text = log.read_text(encoding="utf-8", errors="replace")
    if p.returncode == 0 and (OUT / job_id / "report.json").exists():
        jobs[job_id]["status"] = "done"
    else:
        if jobs[job_id].get("highlights"):   # a killed or timed-out long video leaves little per-Short folders behind
            from video_tool import highlights as _hl
            _hl.clean_leftovers(job_id, OUT, whole_job=True)
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = _friendly_error(text, folder)
        jobs[job_id]["can_retry"] = "STOPPED:" not in text   # our crash, not her files: running it again can work
        _log_failure(job_id, user, jobs[job_id]["error"], text)


def _check_id(job_id):
    if not JOB_RE.match(job_id):
        raise HTTPException(404)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return _with_origin((Path(__file__).parent / "index.html").read_text(encoding="utf-8"), request)


@app.get("/api/prefs")
def prefs(request: Request):
    """What the upload page needs to show her saved choices (noise checkbox, music, ducking, the list of settings)."""
    from video_tool import music, settings as S
    raw = S.load_prefs(_prefs_file(request.state.user))
    saved = S.clamp({**S.DEFAULTS, **raw})
    track = saved["music_track"] if music.find(saved["music_track"]) else ""   # a remembered track that was since removed: automatic
    return {"denoise": saved.get("denoise") is True,
            "music_duck": saved.get("music_duck") is True,
            "music": music.NO_MUSIC if saved.get("music_on") is False else track,
            "saved": review.saved_words(raw)}


# ---------- music: the installed tracks, a 10-second preview of each ----------
MUSIC_CACHE = DATA_DIR / "cache" / "music"
_music_lock = threading.Lock()


@app.get("/api/music")
def music_list():
    """The installed tracks with the titles from LICENSES.txt, for the music choice (signed-in people only)."""
    from video_tool import music
    return {"tracks": music.tracks(), "preview_seconds": music.PREVIEW_SECONDS}


@app.get("/api/music/{name}")
def music_preview(name: str):
    """The first 10 seconds of one installed track, by its exact file name. Nothing else in the music folder (the
    licence list, a full track, any other file) can be read through this route."""
    from video_tool import music
    track = music.find(name)
    if not track:
        raise HTTPException(404, "That track isn't installed.")
    st = track.stat()
    out = MUSIC_CACHE / f"{re.sub(r'[^A-Za-z0-9_-]+', '_', track.stem)[:80]}-{st.st_size}-{int(st.st_mtime)}.mp3"
    with _music_lock:
        if not out.is_file():
            MUSIC_CACHE.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".part.mp3")
            try:
                music.preview(track, tmp)
                tmp.replace(out)
            except Exception:
                tmp.unlink(missing_ok=True)
                raise HTTPException(500, "I couldn't play that track just now.")
    return FileResponse(out, media_type="audio/mpeg")


def _music_choice(value):
    """The music choice of an upload or a remake -> settings overrides. "" / "auto" = let the tool pick, "none" = no
    music, otherwise the exact file name of an installed track (anything else is a plain 400)."""
    from video_tool import music
    if value is None or value in ("", "auto"):
        return {"music_on": True, "music_track": ""} if value is not None else {}
    if value == music.NO_MUSIC:
        return {"music_on": False}
    if not isinstance(value, str) or not music.find(value):
        raise HTTPException(400, "That music track isn't installed any more. Pick another one, or No music.")
    return {"music_on": True, "music_track": value}


@app.delete("/api/prefs")
def reset_prefs(request: Request):
    """'Back to normal': forget this person's saved choices. Nobody else's are touched."""
    f = _prefs_file(request.state.user)
    try:
        f.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        raise HTTPException(500, "Couldn't reset your settings just now. Please try again.")
    return {"ok": True}


@app.post("/api/jobs")
def create_job(request: Request, clips: List[UploadFile] = File(...), voice: Optional[UploadFile] = File(None),
               denoise: Optional[bool] = Form(None), hints: Optional[str] = Form(None),
               format: Optional[str] = Form(None), private: Optional[bool] = Form(None),
               highlights: Optional[str] = Form(None), music_duck: Optional[bool] = Form(None),
               orientation: Optional[str] = Form(None), tall_clips: Optional[str] = Form(None)):
    """Clips arrive in the order she set on the page and are saved as 01, 02, 03...

    denoise is the "Remove background noise" checkbox: a choice for this video only.
    """
    shorts = None
    if highlights not in (None, ""):
        try:
            shorts = int(str(highlights).strip())
        except ValueError:
            shorts = -1
    extras = _clean_extras({"hints": hints, "format": format, "private": private, "highlights": shorts, "music_duck": music_duck,
                            "orientation": orientation, "tall_clips": tall_clips})
    if extras.get("highlights"):
        _check_long_upload(len(clips), bool(voice))
    if voice and Path(voice.filename).suffix.lower() not in AUDIO_EXT:
        raise HTTPException(400, f"Voice-over must be an audio file (.m4a, .mp3, .wav). Got: {voice.filename}")
    bad = [c.filename for c in clips if Path(c.filename).suffix.lower() not in VIDEO_EXT]
    if bad:
        raise HTTPException(400, "These aren't video files: " + ", ".join(bad))

    sizes = [_upload_size(c) for c in clips] + ([_upload_size(voice)] if voice else [])
    if any(n <= 0 for n in sizes):
        raise HTTPException(400, "One of the files is empty (0 bytes). Check it opens on your phone, then pick it again.")
    _check_capacity(sizes)   # the same limits as the chunked route, before a single file is written
    user = request.state.user
    job_id = f"web-{datetime.now():%Y%m%d-%H%M%S-%f}"[:25]
    folder = UPLOADS / job_id
    folder.mkdir(parents=True)
    try:
        for i, c in enumerate(clips, 1):
            with open(folder / f"{i:02d}{Path(c.filename).suffix.lower()}", "wb") as fh:
                shutil.copyfileobj(c.file, fh)
        if voice:  # no voice-over = use the clips' own sound
            with open(folder / f"voice{Path(voice.filename).suffix.lower()}", "wb") as fh:
                shutil.copyfileobj(voice.file, fh)
    except OSError:
        shutil.rmtree(folder, ignore_errors=True)   # never leave half a video behind
        raise HTTPException(507, "I couldn't save your files: the computer running the Video Maker may be out of space. "
                                 "Please let us know.")
    empty = [f.name for f in folder.iterdir() if f.is_file() and f.stat().st_size == 0]
    if empty:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(400, "One of the files is empty (0 bytes). Check it opens on your phone, then pick it again.")
    if extras.get("highlights"):   # the same length and sound checks the page route makes, so this route can't skip them
        try:
            _check_long_file(folder / "01{}".format(Path(clips[0].filename).suffix.lower()))
        except HTTPException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
    (folder / "original_names.json").write_text(
        json.dumps([c.filename for c in clips] + ([voice.filename] if voice else []), indent=1), encoding="utf-8")

    over = {**({} if denoise is None else {"denoise": denoise}), **extras}
    _start(job_id, folder, user, _settings_arg(job_id, over) if over else [])
    return {"job_id": job_id}


# ---------- chunked, resumable upload ----------
# The old single-request upload (above) still works for scripts. The web page uses these: every piece is a
# separate small request (well under Cloudflare's 100 MB limit), a dropped connection only retries the
# current piece, and the file types are checked before a single byte is sent.
CHUNK_MAX = 32 * 1024 * 1024          # largest piece the server accepts in one request
MAX_TOTAL = 6 * 1024 ** 3             # one video's files together
MIN_FREE = 2 * 1024 ** 3              # keep this much of the disk free for the render itself
VOICE_MAX_RAW = 20 * 60               # a voice-over longer than this can't be a Short in any trimming
FORM_OVERHEAD = 16 * 1024 * 1024      # what a multipart body adds on top of the files themselves


def _check_capacity(sizes):
    """The limits BOTH upload routes share: all of one video's files together, and the disk space left for them."""
    total = sum(sizes)
    if total > MAX_TOTAL:
        raise HTTPException(400, f"That's {total / 1024 ** 3:.1f} GB in total, more than one video can use. "
                                 "Try fewer or shorter clips.")
    if shutil.disk_usage(UPLOADS.parent).free - total < MIN_FREE:
        raise HTTPException(507, "The computer running the Video Maker is almost out of space, so I can't take these files "
                                 "right now. Please let us know.")


def _upload_size(f):
    """Bytes in an uploaded file (already received by the time the route runs)."""
    size = getattr(f, "size", None)
    if isinstance(size, int):
        return size
    pos = f.file.tell()
    f.file.seek(0, 2)
    n = f.file.tell()
    f.file.seek(pos)
    return n


class OldUploadGuard:
    """The single-request upload (POST /api/jobs) is parsed BEFORE its route runs, so the route alone can't stop a huge body.
    This guard looks at the declared size first, counts the bytes as they arrive when no size is declared, and refuses
    with the same plain messages the chunked route gives. Everything else passes straight through."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/api/jobs":
            return await self.app(scope, receive, send)
        limit = MAX_TOTAL + FORM_OVERHEAD
        try:
            declared = int(dict(scope["headers"]).get(b"content-length", b"0") or 0)
        except ValueError:
            declared = 0
        refusal = None
        if declared > limit:
            refusal = (413, "That's more than one video can use. Try fewer or shorter clips.")
        elif declared and shutil.disk_usage(UPLOADS.parent).free - declared < MIN_FREE:
            refusal = (507, "The computer running the Video Maker is almost out of space, so I can't take these files right now. "
                            "Please let us know.")
        started, seen, tripped = [False], [0], [False]

        class TooBig(Exception):
            pass

        async def counted():
            msg = await receive()
            if msg["type"] == "http.request":
                seen[0] += len(msg.get("body") or b"")
                if seen[0] > limit:
                    tripped[0] = True
                    raise TooBig()
            return msg

        async def watched(msg):
            if tripped[0]:
                # the form parser turned our stop into a generic "error parsing the body": say what really happened, once
                if not started[0] and msg["type"] == "http.response.start":
                    started[0] = True
                    await reply(413, "That's more than one video can use. Try fewer or shorter clips.")
                return
            if msg["type"] == "http.response.start":
                started[0] = True
            await send(msg)

        async def reply(status, text):
            body = json.dumps({"detail": text}).encode()
            await send({"type": "http.response.start", "status": status,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})

        if refusal:
            return await reply(*refusal)
        try:
            await self.app(scope, counted, watched)
        except TooBig:
            if not started[0]:
                await reply(413, "That's more than one video can use. Try fewer or shorter clips.")


app.add_middleware(OldUploadGuard)


def _pending(job_id):
    p = UPLOADS / job_id / "pending.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(404, "This upload isn't on the server any more. Please pick your files again.")


def _save_pending(job_id, pend):
    (UPLOADS / job_id / "pending.json").write_text(json.dumps(pend), encoding="utf-8")


def _my_upload(request, job_id):
    _check_id(job_id)
    if _read(OUT / f"{job_id}.owner") != request.state.user:
        raise HTTPException(404, "This upload isn't on the server any more. Please pick your files again.")
    return _pending(job_id)


def _got(slot_path):
    try:
        return slot_path.stat().st_size
    except OSError:
        return 0


VOICE_EXT = AUDIO_EXT | VIDEO_EXT | {".weba", ".mka", ".mp2", ".mpga"}   # a recording made in a browser may be .webm or .mp4
HINTS_MAX = 400


def _voice_slot_ext(name):
    """The ending to save a voice file under, or '' when it can't be a voice recording at all."""
    ext = Path(str(name or "")).suffix.lower()
    return ext if ext in VOICE_EXT else ""


def _clean_extras(body):
    """The optional per-video choices of an upload, as settings overrides for that one video. Wrong kinds of value
    are a plain 400, never a crash.  hints: names and places (whitespace tidied, cut at 400 characters);
    format: an id from /api/formats ('auto' means no choice); private: pictures stay on this computer, which turns
    clip matching off for this video and is recorded as privacy='private'."""
    from video_tool.settings import FORMAT_KEYS
    out = {}
    hints = body.get("hints")
    if hints is not None:
        if not isinstance(hints, str):
            raise HTTPException(400, "The names and places box needs plain text.")
        hints = " ".join(hints.split())[:HINTS_MAX]
        if hints:
            out["hints"] = hints
    fmt = body.get("format")
    if fmt is not None:
        if not isinstance(fmt, str) or fmt not in FORMAT_KEYS:
            raise HTTPException(400, "I don't know that kind of video. Pick one from the list, or leave it on automatic.")
        if fmt != "auto":
            out["format"] = fmt
    duck = body.get("music_duck")
    if duck is not None:
        if not isinstance(duck, bool):
            raise HTTPException(400, "The music dipping choice has to be on or off.")
        out["music_duck"] = duck
    private = body.get("private")
    if private is not None:
        if not isinstance(private, bool):
            raise HTTPException(400, "The private-pictures choice has to be on or off.")
        if private:
            out.update(match_clips=False, privacy="private")
    shorts = body.get("highlights")
    if shorts is not None and shorts != 0:   # 0 / missing = the ordinary video
        ok = isinstance(shorts, int) and not isinstance(shorts, bool) or (isinstance(shorts, float) and shorts.is_integer())
        if not ok or not 1 <= int(shorts) <= 5:
            raise HTTPException(400, "How many Shorts has to be a whole number from 1 to 5.")
        out["highlights"] = int(shorts)
    if "music" in body:   # her music choice for this video: automatic, a track, or none
        out.update(_music_choice(body.get("music")))
    shape = body.get("orientation")
    if shape is not None and shape != "vertical":   # missing / vertical = the ordinary vertical video
        from video_tool.settings import ORIENTATIONS
        if shape not in ORIENTATIONS:
            raise HTTPException(400, "Pick a social video (upright) or a YouTube video (sideways).")
        if out.get("highlights"):
            raise HTTPException(400, "Shorts from a long video are always upright. Pick A video from my clips for a YouTube video.")
        out["orientation"] = shape
        tall = body.get("tall_clips")
        if tall is not None:
            from video_tool.settings import TALL_CLIPS
            if tall not in TALL_CLIPS:
                raise HTTPException(400, "The fill-the-screen choice has to be on or off.")
            if tall == "fill":
                out["tall_clips"] = "fill"
    labels = body.get("clip_labels")
    if labels is not None:
        from video_tool.settings import clean_clip_labels
        if isinstance(labels, list):
            if not all(v is None or isinstance(v, str) for v in labels):
                raise HTTPException(400, "Clip labels need plain text.")
            cleaned = clean_clip_labels(["" if v is None else v for v in labels])
        elif isinstance(labels, dict):
            if not all(isinstance(k, (str, int)) and not isinstance(k, bool) and (v is None or isinstance(v, str))
                       for k, v in labels.items()):
                raise HTTPException(400, "Clip labels need plain text.")
            cleaned = clean_clip_labels({k: ("" if v is None else v) for k, v in labels.items()})
        else:
            raise HTTPException(400, "Clip labels need plain text.")
        if cleaned:
            out["clip_labels"] = cleaned
    return out


def _check_long_upload(n_clips, has_voice):
    """Shorts from a long video: exactly one long video, no voice-over. Plain 400s in her words."""
    if n_clips != 1:
        raise HTTPException(400, "Pick just one long video for this. For several clips, use A video from my clips.")
    if has_voice:
        raise HTTPException(400, "A voice-over isn't used for Shorts from a long video. Take it off, "
                                 "or use A video from my clips.")


def _loose_probe(path):
    """ffprobe's view of a file, or None when it can't be read at all. Unlike ff.probe this does not insist on a length:
    a recording made in a browser often has none until it is converted."""
    from video_tool.config import FFPROBE
    try:
        p = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        info = json.loads(p.stdout) if p.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return info if info and info.get("streams") is not None else None


def _kinds(info):
    """(has a real picture, has sound). A cover image inside an audio file does not count as a picture."""
    pic = any(s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
              for s in info["streams"])
    return pic, any(s.get("codec_type") == "audio" for s in info["streams"])


def _has_length(info):
    try:
        return float(info["format"]["duration"]) > 0
    except (KeyError, TypeError, ValueError):
        return False


def _unreadable(label):
    return HTTPException(400, f"{label} can't be read. It may not have finished downloading. "
                              "Re-download it and add it again.")


def _check_slots(folder, pend):
    """Look inside every uploaded file before any work is done, and say what is wrong in her words.
    A clip with no picture (a voice recording sent as a clip) and a clip that can't be read are refused; a voice
    recording with no sound, or only silence, is refused; a voice recording that isn't in an ordinary audio file
    (the browser's .webm, an audio .mp4 or .3gp) is converted to one, so nothing mistakes it for a video."""
    from video_tool import ff
    from video_tool.config import FFMPEG
    for n, spec in enumerate([s for s in pend["slots"] if not s["slot"].startswith("voice")], 1):
        label = f"Clip {n} ({spec['name']})"
        info = _loose_probe(folder / spec["slot"])
        if info is None or not _has_length(info):
            raise _unreadable(label)
        pic, sound = _kinds(info)
        if not pic:
            raise HTTPException(400, f"{label} has no picture, it's audio only. Add it as your voice-over instead.")
    spec = next((s for s in pend["slots"] if s["slot"].startswith("voice")), None)
    if not spec:
        return
    label = f"Your voice-over ({spec['name']})"
    path = folder / spec["slot"]
    info = _loose_probe(path)
    if info is not None and not _kinds(info)[1]:
        raise HTTPException(400, f"{label} has no sound in it. Check it plays on your phone, then pick it again.")
    if info is None or Path(spec["slot"]).suffix.lower() not in AUDIO_EXT or not _has_length(info):
        new = folder / "voice.m4a"
        tmp = folder / "voice.converting.m4a"
        try:
            p = subprocess.run([FFMPEG, "-hide_banner", "-v", "error", "-y", "-i", str(path), "-vn", "-c:a", "aac",
                                "-b:a", "160k", str(tmp)], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=600)
        except (OSError, subprocess.TimeoutExpired):
            p = None
        if p is None or p.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 200:
            tmp.unlink(missing_ok=True)
            raise _unreadable(label)
        if path != new:
            path.unlink(missing_ok=True)
        tmp.replace(new)
        spec["slot"], path = new.name, new
        spec["size"] = new.stat().st_size
        _save_pending(folder.name, pend)   # the slot has a new name now: keep the record true if she has to try again
        info = _loose_probe(path)
        if info is None or not _has_length(info) or not _kinds(info)[1]:
            raise _unreadable(label)
    try:
        loud = ff.max_volume_db(path).get("max_volume")
    except (OSError, ValueError):
        loud = None
    if loud is not None and loud <= SILENT_DB:
        raise HTTPException(400, f"I couldn't hear anything in your voice-over ({spec['name']}). It sounds completely silent. "
                                 "Check the microphone was on and it plays on your phone, then record or pick it again.")


SILENT_DB = -70.0   # a recording whose loudest moment is below this has nothing in it


@app.post("/api/uploads")
async def upload_start(request: Request):
    """Body: {"clips": [{"name", "size"}...], "voice": {"name", "size"} | null, "denoise": bool | null,
    "hints": str | null (names and places to listen for, up to 400 characters), "format": one of /api/formats ids | null,
    "private": bool | null (pictures stay on this computer)}. The voice may be any audio recording, including one made
    in the browser (.webm, .ogg, .m4a, .mp4): the finish step checks what is inside, not the ending of the name."""
    body = await _json_body(request)
    clips, voice = body.get("clips") or [], body.get("voice")
    if not isinstance(clips, list) or not all(isinstance(c, dict) for c in clips) \
            or not (voice is None or isinstance(voice, dict)):
        raise HTTPException(400, "That request wasn't understood. Please pick your files again.")
    if not clips:
        raise HTTPException(400, "Add at least one video clip.")
    bad = [str(c.get("name", "?")) for c in clips if Path(str(c.get("name", ""))).suffix.lower() not in VIDEO_EXT]
    if bad:
        raise HTTPException(400, "These aren't video files I can use: " + ", ".join(bad))
    if voice and not _voice_slot_ext(voice.get("name")):
        raise HTTPException(400, f"I can't use {voice.get('name')} as a voice-over. Use a voice recording (.m4a, .mp3, .wav).")
    extras = _clean_extras(body)
    if extras.get("highlights"):
        _check_long_upload(len(clips), bool(voice))
    try:
        raw_sizes = [c.get("size") or 0 for c in clips] + ([voice.get("size") or 0] if voice else [])
        if any(isinstance(x, bool) for x in raw_sizes):
            raise ValueError
        sizes = [int(x) for x in raw_sizes]
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(400, "That request wasn't understood. Please pick your files again.")
    if any(s <= 0 for s in sizes):
        raise HTTPException(400, "One of the files is empty (0 bytes). Check it opens on your phone, then pick it again.")
    _check_capacity(sizes)
    user = request.state.user
    job_id = f"web-{datetime.now():%Y%m%d-%H%M%S-%f}"[:25]
    folder = UPLOADS / job_id
    folder.mkdir(parents=True)
    slots = [{"slot": f"{i:02d}{Path(c['name']).suffix.lower()}", "name": c["name"], "size": int(c["size"])}
             for i, c in enumerate(clips, 1)]
    if voice:
        slots.append({"slot": f"voice{_voice_slot_ext(voice['name'])}", "name": voice["name"], "size": int(voice["size"])})
    (OUT / f"{job_id}.owner").write_text(user, encoding="utf-8")
    (folder / "original_names.json").write_text(json.dumps([s["name"] for s in slots], indent=1), encoding="utf-8")
    _save_pending(job_id, {"slots": slots, "denoise": body.get("denoise"), "extras": extras, "started": False,
                           "manual_order": body.get("manual_order") is True})
    return {"job_id": job_id, "slots": [{"slot": s["slot"], "received": 0, "size": s["size"]} for s in slots],
            "chunk": 8 * 1024 * 1024}


@app.get("/api/uploads/{job_id}")
def upload_progress(job_id: str, request: Request):
    """How much of each file has arrived, so a dropped upload can carry on from there."""
    pend = _my_upload(request, job_id)
    for s in pend["slots"]:
        _drop_if_overlong(job_id, s)
    return {"job_id": job_id, "started": pend.get("started", False),
            "slots": [{"slot": s["slot"], "received": _got(UPLOADS / job_id / s["slot"]), "size": s["size"]} for s in pend["slots"]]}


_slot_locks = {}
_slot_locks_guard = threading.Lock()


def _lock_for(*key):
    """One lock per upload slot (or per upload), so two requests for the same piece can't both write it."""
    with _slot_locks_guard:
        return _slot_locks.setdefault(key, threading.Lock())


def _drop_if_overlong(job_id, spec):
    """A file with more bytes than it should have (two copies of a piece arrived at once) can never be finished:
    start that file again from zero so the page sends it afresh."""
    path = UPLOADS / job_id / spec["slot"]
    with _lock_for(job_id, spec["slot"]):
        if _got(path) > spec["size"]:
            try:
                path.unlink()
            except OSError:
                pass


@app.put("/api/uploads/{job_id}/{slot}")
async def upload_chunk(job_id: str, slot: str, offset: int, request: Request):
    """Append one piece. If the offset isn't where the file ends, say where it does: the page re-syncs and carries on."""
    pend = _my_upload(request, job_id)
    spec = next((s for s in pend["slots"] if s["slot"] == slot), None)
    if not spec:
        raise HTTPException(404, "Unknown file in this upload.")
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > CHUNK_MAX:
        raise HTTPException(413, "That piece is too big.")
    data = bytearray()
    async for part in request.stream():          # never hold more than one piece in memory
        data += part
        if len(data) > CHUNK_MAX:
            raise HTTPException(413, "That piece is too big.")
    data = bytes(data)
    path = UPLOADS / job_id / slot
    with _lock_for(job_id, slot):                # check and write as one step: a repeated piece can't be added twice
        have = _got(path)
        if offset != have:
            return JSONResponse({"detail": "out of step", "received": have}, status_code=409)
        if have + len(data) > spec["size"]:
            raise HTTPException(400, "More data arrived than the file's size. Please pick your files again.")
        with open(path, "ab") as fh:
            fh.write(data)
    return {"received": have + len(data), "size": spec["size"]}


def _estimate_trimmed(voice_path):
    """Roughly how long the voice-over is once long pauses are cut, from a fast silence scan (a few seconds)."""
    from video_tool import ff, settings
    from video_tool.config import FFMPEG, SILENCE_KEEP
    info = ff.probe(voice_path)
    raw = info["duration"]
    if not ff.has_stream(info, "audio"):
        raise HTTPException(400, "Your voice-over file has no sound in it. Check it plays on your phone, then pick it again.")
    gap = settings.DEFAULTS["pause_gap"]
    p = subprocess.run([FFMPEG, "-hide_banner", "-i", str(voice_path), "-af", f"silencedetect=noise=-35dB:d={gap}", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    cut = sum(max(0.0, float(m) - 2 * SILENCE_KEEP) for m in re.findall(r"silence_duration: ([\d.]+)", p.stderr))
    return raw, max(1.0, raw - cut)


@app.post("/api/uploads/{job_id}/finish")
def upload_finish(job_id: str, request: Request):
    """All pieces are in: check they are whole, check the voice-over length early, then start making the video."""
    _my_upload(request, job_id)
    with _lock_for("finish", job_id):   # a second tap waits here, then sees the video is already being made
        return _finish_locked(job_id, request)


def _finish_locked(job_id, request):
    from video_tool.config import MAX_SECONDS
    pend = _pending(job_id)
    folder = UPLOADS / job_id
    if pend.get("started"):
        return {"job_id": job_id}   # the reply was lost on the way back and she tapped again: don't make it twice
    for s in pend["slots"]:
        _drop_if_overlong(job_id, s)
    short = [s["name"] for s in pend["slots"] if _got(folder / s["slot"]) != s["size"]]
    if short:
        raise HTTPException(409, "Some of your files didn't finish uploading: " + ", ".join(short))
    _check_slots(folder, pend)   # unreadable, picture-less or silent files are named before any work starts
    if (pend.get("extras") or {}).get("highlights"):
        _check_long_video(folder, pend)
    _put_in_order(folder, pend)  # unless she set the order herself, the clips play in the order she filmed them
    voice = next((s for s in pend["slots"] if s["slot"].startswith("voice")), None)
    wide = (pend.get("extras") or {}).get("orientation") == "horizontal"
    if voice and wide:   # a YouTube video (horizontal) can be up to 15 minutes
        from video_tool.layout import H_MAX_SECONDS
        raw, est = _estimate_trimmed(folder / voice["slot"])
        if est > H_MAX_SECONDS + 3:
            raise HTTPException(400, f"Your voice-over is {int(raw // 60)}:{int(raw % 60):02d} long, about {int(est // 60)} minutes once "
                                     "the long pauses are cut. A YouTube video made here can be at most 15 minutes, so it needs to be "
                                     f"about {int((est - H_MAX_SECONDS) // 60) + 1} minutes shorter (or split it into two recordings).")
    elif voice:
        raw, est = _estimate_trimmed(folder / voice["slot"])
        if raw > VOICE_MAX_RAW or est > MAX_SECONDS + 3:
            raise HTTPException(400, f"Your voice-over is {int(raw // 60)}:{int(raw % 60):02d} long, about {int(est)} seconds once the "
                                     f"long pauses are cut. Videos can be at most about {int(MAX_SECONDS)} seconds, so it needs to be "
                                     f"around {int(est - MAX_SECONDS) + 1} seconds shorter (or split it into two recordings).")
    pend["started"] = True
    _save_pending(job_id, pend)
    denoise = pend.get("denoise")
    over = {**({} if denoise is None else {"denoise": bool(denoise)}), **(pend.get("extras") or {})}
    _start(job_id, folder, request.state.user, _settings_arg(job_id, over) if over else [])
    return {"job_id": job_id}


def _check_long_file(path):
    """The long video itself: long enough to be worth cutting, not so long it can't be finished, and with sound to listen to.
    Plain 400s in her words. A file that can't be read is left for the pipeline to explain."""
    info = _loose_probe(path)
    if info is None:
        return
    if not _kinds(info)[1]:
        raise HTTPException(400, "I couldn't hear you talking in this video, so there is nothing to make Shorts from. "
                                 "Please check that the sound was on when you recorded it.")
    try:
        length = float(info["format"]["duration"])
    except (TypeError, KeyError, ValueError):
        return
    if length <= LONG_MIN_SECONDS:
        raise HTTPException(400, f"This video is short enough to be a Short already ({int(length)} seconds). "
                                 "Use A video from my clips.")
    if length > LONG_MAX_SECONDS:
        h, m = divmod(int(length) // 60, 60)
        raise HTTPException(400, f"This video is {h} hour{'s' if h != 1 else ''} {m} minute{'s' if m != 1 else ''} long, which is more than "
                                 f"I can work through in one go (the most is {LONG_MAX_SECONDS // 3600} hours). "
                                 "Please cut it into shorter pieces first.")


def _check_long_video(folder, pend):
    """Shorts from a long video need ONE video that is longer than a Short already is."""
    clip_slots = [s for s in pend["slots"] if not s["slot"].startswith("voice")]
    _check_long_upload(len(clip_slots), any(s["slot"].startswith("voice") for s in pend["slots"]))
    _check_long_file(folder / clip_slots[0]["slot"])


def _put_in_order(folder, pend):
    """Renumber the clip files 01, 02, 03... in the order they were filmed (the time the phone wrote into each file).
    Skipped when she set the order herself with the arrows, when there is one clip, or when the files' times can't be
    trusted (see video_tool/clip_order.py): then her order stays exactly as she gave it. What was done is written to
    auto_sort.json so the result page can say so."""
    from video_tool import clip_order
    clip_slots = [s for s in pend["slots"] if not s["slot"].startswith("voice")]
    if pend.get("manual_order") or len(clip_slots) < 2:
        return
    items = []
    for s in clip_slots:
        info = _loose_probe(folder / s["slot"])
        try:
            seconds = float(info["format"]["duration"])
        except (TypeError, KeyError, ValueError):
            seconds = None
        items.append({"name": s["name"], "time": clip_order.capture_time(info=info) if info else None, "seconds": seconds})
    order, basis = clip_order.plan_order(items)
    if basis is None:
        return
    moved = [clip_slots[i]["name"] for pos, i in enumerate(order) if pos != i]
    tmp = {}
    for i, s in enumerate(clip_slots):                       # two steps so no file is overwritten on the way
        tmp[i] = folder / f"reorder-{i}{Path(s['slot']).suffix}"
        (folder / s["slot"]).replace(tmp[i])
    new_slots = []
    for pos, i in enumerate(order, 1):
        s = clip_slots[i]
        slot = f"{pos:02d}{Path(s['slot']).suffix.lower()}"
        tmp[i].replace(folder / slot)
        new_slots.append({**s, "slot": slot})
    pend["slots"] = new_slots + [s for s in pend["slots"] if s["slot"].startswith("voice")]
    old_labels = (pend.get("extras") or {}).get("clip_labels")
    if isinstance(old_labels, dict) and old_labels:
        remapped = {}
        for pos, i in enumerate(order, 1):
            lbl = old_labels.get(str(i + 1))
            if lbl:
                remapped[str(pos)] = lbl
        if remapped:
            pend["extras"]["clip_labels"] = remapped
        else:
            pend["extras"].pop("clip_labels", None)
    _save_pending(folder.name, pend)
    (folder / "original_names.json").write_text(json.dumps([s["name"] for s in pend["slots"]], indent=1), encoding="utf-8")
    (folder / "auto_sort.json").write_text(json.dumps({"basis": basis, "moved": moved}, indent=1), encoding="utf-8")


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str, request: Request):
    """Make a video that didn't finish again from the files already on the server (no new upload)."""
    owner = _mine(request, job_id)
    if jobs.get(job_id, {}).get("status") in ("queued", "running"):
        raise HTTPException(409, "This video is still being made.")
    src = _read(OUT / f"{job_id}.source")
    folder = Path(src) if src else None
    if not folder or not folder.is_dir():
        raise HTTPException(404, "The files for this video aren't on the server any more. Please add your clips again.")
    new_id = f"web-{datetime.now():%Y%m%d-%H%M%S-%f}"[:25]
    own = _own_settings(job_id)
    _start(new_id, folder, owner or request.state.user, _settings_arg(new_id, own) if own else [])
    return {"job_id": new_id}


def _source_files(folder):
    """The numbered clip files of an upload (in order) and its voice-over file, if any."""
    files = sorted(f for f in Path(folder).iterdir() if f.is_file())
    clips = sorted((f for f in files if f.stem.isdigit()), key=lambda f: int(f.stem))
    voice = next((f for f in files if f.stem == "voice"), None)
    return clips, voice


def _clip_names(folder, clips):
    try:
        names = json.loads((Path(folder) / "original_names.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        names = []
    return [names[i] if i < len(names) else f.name for i, f in enumerate(clips)]


@app.get("/api/jobs/{job_id}/clips")
def job_clips(job_id: str, request: Request):
    """Her clips by the names she gave them, for the 'change the order' list on the results page."""
    from video_tool.settings import clean_clip_labels
    _mine(request, job_id)
    src = _read(OUT / f"{job_id}.source")
    if not src or not Path(src).is_dir():
        return {"clips": []}
    clips, _ = _source_files(src)
    labels = clean_clip_labels(_own_settings(job_id).get("clip_labels") or _job_settings(job_id).get("clip_labels"),
                               max_clips=len(clips))
    return {"clips": [{"n": i + 1, "name": n, **({"label": labels[str(i + 1)]} if labels.get(str(i + 1)) else {})}
                      for i, n in enumerate(_clip_names(src, clips))]}


def _shot_rows(d, report):
    """One row per phrase of her voice-over: when it is said, the words, the clip on screen, and a plain match word
    from the saved clip scores (none when there are no scores). Only for a voice-over video in one part."""
    from video_tool import match
    tl = report.get("timeline") or []
    if report.get("mode") != "voice" or len(report.get("parts") or [1]) > 1 or not tl:
        return []
    try:
        sc = json.loads((d / "match" / "scores.json").read_text(encoding="utf-8"))
        texts, times, scores, ftimes = sc["phrases"], sc["phrase_times"], sc["scores"], sc["frame_times"]
        if len(texts) != len(times):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        sc = None
        try:
            words = json.loads((d / "words_trimmed.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        phr = match.phrases(words, float(report.get("length") or tl[-1]["end"]))
        texts, times = [p["text"] for p in phr], [[round(p["start"], 2), round(p["end"], 2)] for p in phr]
    from video_tool.settings import clean_clip_labels
    fixes = (report.get("settings") or {}).get("caption_fixes") or {}
    labels = clean_clip_labels((report.get("settings") or {}).get("clip_labels"))

    def fixed(t):
        for a, b in fixes.items():
            t = re.sub(rf"(?<!\w){re.escape(a)}(?!\w)", b, t)
        return t

    rows = []
    for j, (t0, t1) in enumerate(times):
        mid = (t0 + t1) / 2
        slot = next((t for t in tl if t["start"] <= mid < t["end"]), tl[-1])
        m = re.match(r"^0*(\d+)\.", str(slot.get("clip", "")))
        n = int(m.group(1)) if m else None
        word, shown_score, best_score = None, None, None
        if sc and n and 0 < n <= len(scores):
            try:
                best = max(float(r[j]) for clip in scores for r in clip if r is not None and len(r) > j)
                lo, hi = t0 - slot["start"], t1 - slot["start"]
                ft = ftimes[n - 1]
                near = [f for f, at in enumerate(ft) if lo - 0.5 <= at <= hi] or \
                       [min(range(len(ft)), key=lambda f: abs(ft[f] - max(0.0, mid - slot["start"])))]
                shown = [float(scores[n - 1][f][j]) for f in near]
                word = match.match_word(shown, best)
                shown_score, best_score = max(shown), best
            except (ValueError, TypeError, IndexError, KeyError):
                word = None
        lbl = slot.get("label") or (labels.get(str(n), "") if n else "")
        rows.append({"start": round(t0, 2), "end": round(t1, 2), "text": fixed(str(texts[j]).strip()), "clip": n,
                     "slot_start": slot["start"], "slot_end": slot["end"], "match": word,
                     "shown_score": shown_score, "best_score": best_score,
                     "pinned": bool(slot.get("pinned")),
                     **({"label": lbl} if lbl else {})})
    return rows


@app.get("/api/jobs/{job_id}/shots")
def job_shots(job_id: str, request: Request):
    """"Your words and your clips": one row per phrase of her voice-over (see _shot_rows)."""
    _mine(request, job_id)
    d = OUT / job_id
    try:
        report = json.loads((d / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(404, "That video isn't finished yet.")
    return {"rows": _shot_rows(d, report)}


@app.post("/api/jobs/{job_id}/reorder")
async def reorder_clips(job_id: str, request: Request):
    """Make the video again with her clips in a new order, reusing the files already uploaded. Body: {"order": [3, 1, 2]}."""
    owner = _mine(request, job_id)
    try:
        order = [x for x in (await _json_body(request)).get("order", [])]
        if any(isinstance(x, bool) for x in order):
            raise ValueError
        order = [int(x) for x in order]
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise HTTPException(400, "That order wasn't understood.")
    src = _read(OUT / f"{job_id}.source")
    if not src or not Path(src).is_dir():
        raise HTTPException(404, "The files for this video aren't on the server any more. Please add your clips again.")
    clips, voice = _source_files(src)
    if sorted(order) != list(range(1, len(clips) + 1)):
        raise HTTPException(400, "The order has to list every clip exactly once.")
    if order == list(range(1, len(clips) + 1)):
        raise HTTPException(400, "That's the same order as before.")
    if jobs.get(job_id, {}).get("status") in ("queued", "running"):
        raise HTTPException(409, "This video is still being made.")
    new_id = f"web-{datetime.now():%Y%m%d-%H%M%S-%f}"[:25]
    folder = UPLOADS / new_id
    folder.mkdir(parents=True)
    names = _clip_names(src, clips)

    def place(frm, to):
        try:
            os.link(frm, to)            # same file, no second copy of 200 MB
        except OSError:
            shutil.copy2(frm, to)

    for k, idx in enumerate(order, 1):
        place(clips[idx - 1], folder / f"{k:02d}{clips[idx - 1].suffix}")
    if voice:
        place(voice, folder / voice.name)
    shown = [names[i - 1] for i in order]
    try:
        orig = json.loads((Path(src) / "original_names.json").read_text(encoding="utf-8"))
        shown += orig[len(clips):]      # the voice-over's name stays last
    except (OSError, ValueError):
        pass
    (folder / "original_names.json").write_text(json.dumps(shown, indent=1), encoding="utf-8")
    (folder / "auto_sort.json").unlink(missing_ok=True)   # she set this order herself now: don't say the tool chose it
    own = _own_settings(job_id)
    if own.get("pins"):   # her "Change this shot" choices follow the clip to its new number
        own["pins"] = [{**p, "clip": order.index(p["clip"]) + 1} for p in own["pins"]
                       if isinstance(p, dict) and p.get("clip") in order]
    if isinstance(own.get("clip_labels"), dict) and own["clip_labels"]:
        old_lbl = own["clip_labels"]
        remapped = {str(pos): old_lbl[str(old_n)] for pos, old_n in enumerate(order, 1) if old_lbl.get(str(old_n))}
        if remapped:
            own["clip_labels"] = remapped
        else:
            own.pop("clip_labels", None)
    _start(new_id, folder, owner or request.state.user,
           [*(_settings_arg(new_id, own) if own else []), "--reuse", str(OUT / job_id)])
    (OUT / f"{new_id}.parent").write_text(job_id, encoding="utf-8")
    return {"job_id": new_id}


def _own_settings(job_id):
    """The one-off settings a job was made with (not her saved prefs)."""
    try:
        return json.loads((OUT / f"{job_id}.settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _settings_arg(job_id, overrides):
    """Save a job's one-off settings and return the pipeline arguments that apply them."""
    over = OUT / f"{job_id}.settings.json"
    over.write_text(json.dumps(overrides), encoding="utf-8")
    return ["--settings", str(over)]


def _highlights_of(extra):
    """How many Shorts the pipeline arguments ask for (0 for an ordinary video)."""
    extra = list(extra)
    try:
        over = json.loads(Path(extra[extra.index("--settings") + 1]).read_text(encoding="utf-8"))
        n = over.get("highlights")
        return int(n) if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 5 else 0
    except (ValueError, IndexError, OSError, AttributeError):
        return 0


def _start(job_id, folder, user, extra=()):
    if job_id in jobs or (OUT / job_id).exists():
        return   # this video is already being made (or was): never start it twice
    shorts = _highlights_of(extra)
    jobs[job_id] = {"status": "queued", "queued_at": time.time(), "eta_minutes": _eta_minutes(folder, shorts)}
    if shorts:
        jobs[job_id]["highlights"] = shorts
        jobs[job_id]["video_seconds"] = _long_video_seconds(folder)
    (OUT / f"{job_id}.owner").write_text(user, encoding="utf-8")
    (OUT / f"{job_id}.source").write_text(str(folder), encoding="utf-8")  # remakes survive a restart
    threading.Thread(target=_run, args=(job_id, folder, user, extra), daemon=True).start()


def _read_feedback():
    try:
        lines = FEEDBACK_LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for l in lines:
        try:
            out.append(json.loads(l))
        except ValueError:
            pass
    return out


def _log_feedback(entry):
    entry = {"id": uuid.uuid4().hex[:10], "time": datetime.now().isoformat(timespec="seconds"), **entry}
    with feedback_lock, open(FEEDBACK_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry["id"]


def _job_is_long(job_id):
    """Was this video made as Shorts from a long video?"""
    try:
        rep_ = json.loads((OUT / job_id / "report.json").read_text(encoding="utf-8"))
        if rep_.get("mode") == "highlights":
            return True
    except (OSError, ValueError, AttributeError):
        pass
    return bool(_own_settings(job_id).get("highlights"))


def _job_settings(job_id):
    """The settings the version she is looking at was built with."""
    try:
        return json.loads((OUT / job_id / "report.json").read_text(encoding="utf-8")).get("settings", {})
    except (OSError, ValueError):
        return {}


def _button_changes(body):
    """Her button choices from a feedback body: only settings with the right kind of value get through (a number for a
    number...), caption fixes tidied, and a music track only if it is installed."""
    changes = clean_values(body.get("changes"))
    if "caption_fixes" in changes:
        # no { } or backslashes (they are subtitle codes) and a sane length
        _tidy = lambda t: re.sub(r"[{}\\]", "", str(t)).strip()[:80]
        changes["caption_fixes"] = {_tidy(k): _tidy(v) for k, v in changes["caption_fixes"].items()
                                    if _tidy(k) and _tidy(v)}
    if changes.get("music_track"):
        from video_tool import music
        if not music.find(changes["music_track"]):
            raise HTTPException(400, "That music track isn't installed any more. Pick another one, or No music.")
    return changes


def _merge_changes(job_id, typed, changes, unsupported):
    """Buttons win over words when both touch the same setting; caption fixes combine. Raises a plain 400 for a word
    that breaks the posting rules; drops (and says so in `unsupported`) a Shorts count that can't apply."""
    merged = {**typed, **changes}
    if "caption_fixes" in typed or "caption_fixes" in changes:
        merged["caption_fixes"] = {**typed.get("caption_fixes", {}), **changes.get("caption_fixes", {})}
    # What goes onto the picture and into the copy gets the same word checks as the AI's own text
    from video_tool.writer import fh_hits, hype_hits
    said = " ".join(merged.get("caption_fixes", {}).values())
    hit = next(iter([*fh_hits(said), *hype_hits(said)]), None)
    if hit:
        raise HTTPException(400, f"I can't put \"{hit}\" into the captions: it breaks the Fair Housing / no-hype rules for your posts.")
    # "How many Shorts?" only means something for a video made from a long video, and only 1 to 5
    if "highlights" in merged:
        n = merged["highlights"]
        long_job = _job_is_long(job_id)
        if not long_job or isinstance(n, bool) or not isinstance(n, (int, float)) or not 1 <= int(n) <= 5:
            merged.pop("highlights")
            if "highlights" in typed:   # she typed it: say so instead of silently dropping it
                unsupported.append("Choosing how many Shorts only works for a video made from one long video.")
        else:
            merged["highlights"] = int(n)
    return merged


# Previews she has seen but not confirmed yet: token -> what the note was read as. Kept in memory for 30 minutes, so
# Confirm applies exactly what she was shown (and a typed note is read by the AI once, not twice).
PLANS = {}
PLAN_MINUTES = 30
_plans_lock = threading.Lock()
MUSIC_GUIDE = "To pick a different track, choose one under Music in More options, then tap Remake video."


def _take_plan(token, job_id, user, comment):
    """The preview this Confirm belongs to, used once. None when there is none (an old page, expired, another video)."""
    if not isinstance(token, str):
        return None
    with _plans_lock:
        now = time.time()
        for k in [k for k, v in PLANS.items() if now - v["t"] > PLAN_MINUTES * 60]:
            PLANS.pop(k, None)
        plan = PLANS.get(token)
        if not plan or plan["job"] != job_id or plan["user"] != user or plan["comment"] != comment:
            return None
        return PLANS.pop(token)


@app.post("/api/jobs/{job_id}/feedback/preview")
async def feedback_preview(job_id: str, request: Request):
    """The plain preview before a remake: a dry run. Nothing is made, saved, remembered or sent to the team.

    Body: like /feedback ({"changes", "comment"}) plus "answered": [question groups she already answered].
    Answer: {"plan": token for Confirm, "will": [...], "keep": [...], "cant": [...], "already": [...], "guide": [...],
    "question": {"group", "text", "options": [{"label", "changes"}]} | null, "changes": {setting: value}}.
    A vague note ("fix the captions") gets ONE question instead of a guess; the reader's guess for that part is dropped."""
    _mine(request, job_id)
    user = request.state.user
    from web import interpret as I
    from video_tool import music
    body = await _json_body(request)
    changes = _button_changes(body)
    comment = str(body.get("comment") or "")[:2000]
    answered = [str(a) for a in body.get("answered")] if isinstance(body.get("answered"), list) else []
    if jobs.get(job_id, {}).get("status") in ("queued", "running"):
        raise HTTPException(409, "This video is still being made. Try again when it's finished.")
    cur = _job_settings(job_id)
    typed, unsupported, how, cost = {}, [], "none", 0.0
    if comment.strip():
        typed, unsupported, how, cost = await run_in_threadpool(interpret_full, comment, cur)
    question = I.follow_up(comment, cur, answered=answered) if comment.strip() else None
    if question and set(question["keys"]) & set(changes):
        question = None   # a button already says which way
    if question:   # ask instead of guessing: what the reader guessed for that part is not applied
        typed = {k: v for k, v in typed.items() if k not in question["keys"]}
        question = {"group": question["group"], "text": question["text"], "options": question["options"]}
    merged = _merge_changes(job_id, typed, changes, unsupported)
    already = [u for u in unsupported if re.match(r"^Already (that way|as far as it goes):", u)]
    guide = [u for u in unsupported if u == I.CLIP_GUIDE]
    cant = [u for u in unsupported if u not in already and u not in guide]
    if music.installed():   # "use a different song": she can pick one herself now
        asks = [u for u in cant if re.search(r"\b(?:music|song|track)\b", u, re.I)]
        if asks:
            cant = [u for u in cant if u not in asks]
            guide.append(MUSIC_GUIDE)
    try:
        has_music = bool(json.loads((OUT / job_id / "report.json").read_text(encoding="utf-8")).get("music"))
    except (OSError, ValueError, AttributeError):
        has_music = False
    token = uuid.uuid4().hex
    with _plans_lock:
        PLANS[token] = {"job": job_id, "user": user, "comment": comment, "typed": typed,
                        "unsupported": unsupported, "how": how, "cost": cost, "t": time.time()}
    return {"plan": token, "will": I.plan_words(merged, cur), "keep": I.plan_keeps(merged, cur, has_music),
            "cant": cant, "already": already, "guide": guide, "question": question, "changes": merged}


@app.post("/api/jobs/{job_id}/feedback")
async def feedback(job_id: str, request: Request):
    """Apply her feedback: button choices plus anything she typed, then remake the video.

    Body: {"changes": {setting: value}, "remember": bool, "comment": str, "remake": bool}
    A typed comment is read by Claude and turned into settings. Requests no setting can do are
    saved as open items on the admin page so they get fixed in the code.
    """
    owner = _mine(request, job_id)
    user = request.state.user
    from video_tool import settings as S
    S = importlib.reload(S)  # re-read DEFAULTS so a newly added setting isn't dropped until a restart

    body = await _json_body(request)
    changes = _button_changes(body)
    by_hand = body.get("by_hand_minutes")
    by_hand = int(by_hand) if (isinstance(by_hand, (int, float)) and not isinstance(by_hand, bool)
                               and 1 <= by_hand <= 1440) else None
    remember = bool(body.get("remember"))
    comment = str(body.get("comment") or "")[:2000]
    remake = bool(body.get("remake"))
    if remake and jobs.get(job_id, {}).get("status") in ("queued", "running"):
        raise HTTPException(409, "This video is still being made. Try again when it's finished.")

    typed, unsupported, how, ai_cost = {}, [], "none", 0.0
    plan = _take_plan(body.get("plan"), job_id, user, comment) if remake else None
    if plan:   # she confirmed a preview: apply exactly what it showed her, without reading the note a second time
        typed, unsupported, how, ai_cost = dict(plan["typed"]), list(plan["unsupported"]), plan["how"], plan["cost"]
    elif comment.strip() and remake:
        typed, unsupported, how, ai_cost = await run_in_threadpool(interpret_full, comment, _job_settings(job_id))
    elif comment.strip():
        unsupported = [comment.strip()]   # "Just send feedback": a note for the team, nothing to apply
    merged = _merge_changes(job_id, typed, changes, unsupported)
    applied = describe(merged)
    if merged.get("highlights"):
        applied.append(f"{merged['highlights']} Short{'' if merged['highlights'] == 1 else 's'}")

    status = "open" if unsupported else ("applied" if merged else "noted")
    _log_feedback({"job": job_id, "user": user, "comment": comment, "button_changes": changes,
                   "typed_changes": typed, "applied": applied, "unsupported": unsupported, "how": how,
                   "remember": remember, "remake": remake, "status": status, "by_hand_minutes": by_hand,
                   "ai_cost_usd": ai_cost})
    # Only her button choices are remembered for future videos. A change that came from typed words is a guess by
    # the AI about what she meant, so it applies to this video only and never sticks to her account.
    # Only the look-and-feel choices are kept: what kind of video it is and the names to listen for belong to this video.
    remembered = []
    if remember and changes:
        remembered = S.remember(changes, _prefs_file(owner or user))
        if changes.get("caption_fixes") and "caption_fixes" not in remembered:
            remembered.append("caption_fixes")
    if not remake or not merged:
        return {"ok": True, "applied": applied, "unsupported": unsupported, "remembered": remembered}

    src = OUT / f"{job_id}.source"
    if not src.exists():
        raise HTTPException(404, "Can't find the original files for this video.")
    new_id = f"web-{datetime.now():%Y%m%d-%H%M%S-%f}"[:25]
    # A remake starts from the one-off choices of the version she is looking at (e.g. the noise
    # checkbox, or a change she didn't save for all videos), then adds what she just changed.
    before = _own_settings(job_id)
    if before.get("orientation") == "horizontal" and "framing" in merged:
        merged["tall_clips"] = "bars" if merged["framing"] == "fit" else "fill"
    overrides = {**before, **merged}
    if "caption_fixes" in before or "caption_fixes" in merged:
        overrides["caption_fixes"] = {**before.get("caption_fixes", {}), **merged.get("caption_fixes", {})}
    if "pins" in merged:   # "Change this shot": her earlier choices stay, a new one replaces one at the same moment
        overrides["pins"] = S.merge_pins(before.get("pins"), merged["pins"])
    if "clip_labels" in before or "clip_labels" in merged:
        merged_labels = S.merge_clip_labels(before.get("clip_labels"), merged.get("clip_labels"))
        if merged_labels:
            overrides["clip_labels"] = merged_labels
        else:
            overrides.pop("clip_labels", None)
    if overrides.get("privacy") == "private":
        overrides["match_clips"] = False   # a private video stays private through every remake
    _start(new_id, Path(src.read_text(encoding="utf-8")), owner or user,
           [*_settings_arg(new_id, overrides), "--reuse", str(OUT / job_id)])
    (OUT / f"{new_id}.parent").write_text(job_id, encoding="utf-8")  # the dashboard groups versions
    return {"job_id": new_id, "applied": applied, "unsupported": unsupported, "remembered": remembered}


STEP_LINE = re.compile(r"^\s*\d/6|^\s+Rendering|^\s+(Normalizing|Checking clip quality)")


def _plain_step(line):
    """The progress line as she should read it (the engine's lines are written for the log)."""
    line = line.strip()
    line = re.sub(r"^Normalizing and joining clips", "Preparing and joining your clips", line)
    line = re.sub(r"^(\d/6 )Normalizing and joining clips", r"\1Preparing and joining your clips", line)
    line = re.sub(r"^Checking clip quality[^.]*", "Checking your clips", line)
    return WIN_PATH.sub(lambda m: Path(m.group(0)).name, line)


def _transcript(d, report):
    """The words burned into the picture as plain text, one entry per part (a single entry when not split)."""
    from video_tool import captions
    files = sorted(d.glob("captions_part*.ass"), key=lambda f: int(re.search(r"part(\d+)", f.name).group(1))) \
        or ([d / "captions.ass"] if (d / "captions.ass").exists() else [])
    parts = []
    for f in files:
        try:
            parts.append(" ".join(l["text"] for l in captions.read_ass_lines(f) if l["text"]))
        except (OSError, ValueError, KeyError):
            parts.append("")
    return {"burned_in": bool(files), "parts": parts, "text": "\n\n".join(parts)}


def _public_report(report):
    """The report as the page may see it: no place on the server's disk (the page works from file names)."""
    if isinstance(report, dict):
        report.pop("final", None)
    return report


def _finished(job_id):
    """A video that has a result (or is marked done). Posting marks and counts only make sense for those."""
    return jobs.get(job_id, {}).get("status") == "done" or (OUT / job_id / "report.json").exists()


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, request: Request):
    """Where a video is. A video this account ever started always answers in the same shape (status, error,
    can_retry), even after a restart; an id that was never known is a clean 404."""
    _mine(request, job_id)
    job = jobs.get(job_id)
    if not job and (OUT / job_id / "report.json").exists():
        job = jobs[job_id] = {"status": "done"}  # finished before a server restart
    if not job and ((OUT / f"{job_id}.owner").exists() or (OUT / job_id).is_dir()):
        # Known job, no result, not running: the site was restarted (or the computer slept) while it was being made
        job = jobs[job_id] = {"status": "failed", "can_retry": (OUT / f"{job_id}.source").exists(),
                              "error": "The Video Maker was restarted while your video was being made, so it didn't finish. "
                                       "Your files are saved: tap Make it again and it will start over."}
    if not job:
        raise HTTPException(404, "I can't find that video any more. Please add your clips again.")
    out = {k: v for k, v in job.items() if k not in ("queued_at", "started_at")}
    if job["status"] == "running" and "started_at" in job:
        out["working_seconds"] = max(0, int(time.time() - job["started_at"]))   # so the page's clock survives coming back
    if job["status"] in ("queued", "running") and "queued_at" in job:
        out["ahead"] = sum(1 for j, o in jobs.items() if j != job_id and o.get("status") in ("queued", "running")
                           and o.get("queued_at", 0) < job["queued_at"])
    log = OUT / f"{job_id}.log"
    if log.exists():
        text = log.read_text(encoding="utf-8", errors="replace")
        steps = [l for l in text.splitlines() if STEP_LINE.match(l)]
        out["step"] = _plain_step(steps[-1]) if steps else "Starting..."
    else:
        text = ""
    if job["status"] in ("queued", "running"):
        n = job.get("highlights") or _own_settings(job_id).get("highlights")
        if isinstance(n, int) and 1 <= n <= 5:
            out["long"] = _long_status(job, n, text)
            out["highlights"] = n
    d = OUT / job_id
    if job["status"] == "done":
        out["report"] = _public_report(json.loads((d / "report.json").read_text(encoding="utf-8")))
        if (d / "copy.json").exists():
            out["copy"] = json.loads((d / "copy.json").read_text(encoding="utf-8"))
        if (d / "ai_prompt.txt").exists():
            out["ai_prompt"] = (d / "ai_prompt.txt").read_text(encoding="utf-8")
        out["transcript"] = _transcript(d, out["report"])
        out["trust"] = review.trust_line(d, out["report"], _own_settings(job_id))
        out["review_minutes"] = review.review_minutes(OUT, job_id)
        chain, cur_id = [], job_id
        while (par := _read(OUT / f"{cur_id}.parent")) and JOB_RE.match(par) and par not in chain and len(chain) < 200:
            chain.append(par)
            cur_id = par
        out["version"] = len(chain) + 1
        par = chain[0] if chain else None
        # the version before this one, only when it still has a finished video she may open
        out["previous"] = par if par and (OUT / par / "report.json").exists() and _owner_of(par) == _owner_of(job_id) else None
        if out["previous"]:
            try:
                prev_parts = json.loads((OUT / par / "report.json").read_text(encoding="utf-8")).get("parts") or []
                out["previous_file"] = prev_parts[0]["file"] if prev_parts and VIDEO_OUT.match(prev_parts[0]["file"]) else "final.mp4"
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                out["previous_file"] = "final.mp4"
    return out


@app.post("/api/jobs/{job_id}/review-time")
async def job_review_time(job_id: str, request: Request):
    """'Time my review': body {"seconds": N} for one timed session. Saved with this video for the class report.
    Only the length of the session is kept: nothing else she does is tracked."""
    _mine(request, job_id)
    if not _finished(job_id):
        raise HTTPException(409, "This video isn't finished yet.")
    body = await _json_body(request)
    try:
        total = review.add_review_time(OUT, job_id, body.get("seconds"), request.state.user, body.get("session_id"))
    except ValueError:
        raise HTTPException(400, "That time wasn't understood. It must be between one second and four hours.")
    except OSError:
        raise HTTPException(503, "I couldn't save your time just now. Please try again.")
    return {"ok": True, "review_minutes": total}


HEARD = re.compile(r"heard\s+(?:(\d+):)?(\d+):(\d\d)\s+of\s+(?:(\d+):)?(\d+):(\d\d)")
MAKING = re.compile(r"4/6 Making Short (\d+) of (\d+)")


def _clock_seconds(h, m, s):
    return int(h or 0) * 3600 + int(m) * 60 + int(s)


def _long_status(job, shorts, log_text):
    """Where Shorts-from-a-long-video work is, for a page that must never look stuck: which stage, how much of the
    video has been listened to, which Short is being made, how far along (0..0.97) and about how many minutes are left.
    The stage names come from the engine's progress lines."""
    video = job.get("video_seconds")
    heard = of = None
    for m in HEARD.finditer(log_text):
        heard, of = _clock_seconds(*m.group(1, 2, 3)), _clock_seconds(*m.group(4, 5, 6))
    if of and not video:
        video = of
    est, listen = _long_estimate(video or 0, shorts)
    stage = 0
    for key, n in (("0/6", 0), ("1/6", 1), ("2/6", 2), ("3/6", 3), ("4/6", 4), ("5/6", 5), ("6/6", 6)):
        if re.search(rf"^\s*{key} ", log_text, re.M):
            stage = n
    made = MAKING.findall(log_text)
    short_i = int(made[-1][0]) if made else None
    short_of = int(made[-1][1]) if made else shorts
    if job.get("status") == "queued" or stage == 0:
        done = 0.0
    elif stage == 1:
        done = listen * (min(1.0, heard / of) if heard and of else 0.0)
    elif stage == 2:
        done = listen + 3
    elif stage == 3:
        done = listen + 8
    elif stage == 4:
        done = listen + 10 + LONG_PER_SHORT * ((short_i or 1) - 1)
    else:
        done = est - (15 if stage == 5 else 8)
    done = max(0.0, min(done, est * 0.97))
    started = job.get("status") == "running" and stage > 0
    left = max(0.0, est - done)
    return {"shorts": shorts, "video_seconds": round(video) if video else None, "stage": stage,
            "heard": heard, "heard_of": of, "short": short_i, "of": short_of,
            "progress": round(done / est, 3) if est else 0.0,
            "minutes_left": max(1, math.ceil(left / 60)) if started or job.get("status") == "queued" else None,
            "estimate_minutes": max(2, math.ceil(est / 60))}


# ---------- delete ----------
def _all_job_ids():
    ids = {f.name[:-len(".owner")] for f in OUT.glob("*.owner")}
    ids |= {d.name for d in OUT.iterdir() if d.is_dir() and JOB_RE.match(d.name) and not d.name.startswith("_")}
    return {i for i in ids if JOB_RE.match(i)}


def _first_version(job_id):
    root, seen = job_id, set()
    while (p := _read(OUT / f"{root}.parent")) and p not in seen and JOB_RE.match(p):
        seen.add(root)
        root = p
    return root


def _remove_job_files(job_id):
    """Everything this one version left behind: its folder and its small side files (out/<id>.owner, .log ...)."""
    d = OUT / job_id
    if d.is_dir() and not d.is_symlink():
        shutil.rmtree(d, ignore_errors=True)
    from video_tool import highlights as _hl
    _hl.clean_leftovers(job_id, OUT)   # the little per-Short folders (<id>-h1 ...) a killed long-video run can leave
    for f in OUT.iterdir():
        if f.is_file() and f.name.startswith(job_id + "."):   # the dot: 'web-1' must never match 'web-10.owner'
            try:
                f.unlink()
            except OSError:
                pass
    jobs.pop(job_id, None)


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str, request: Request, all: int = 0):
    """Delete this video; with ?all=1 every version of it, and the clips she uploaded for it. Owner or admin only."""
    _mine(request, job_id)
    me = request.state.user
    admin = auth.is_admin(me)
    if all:
        root = _first_version(job_id)
        targets = {j for j in _all_job_ids() if _first_version(j) == root} | {job_id}
    else:
        targets = {job_id}
    targets = {j for j in targets if admin or _owner_of(j) == me}   # never someone else's version
    if job_id not in targets:
        raise HTTPException(404, "Job not found.")
    if any(jobs.get(j, {}).get("status") in ("queued", "running") for j in targets):
        raise HTTPException(409, "This video is still being made. You can delete it once it has finished.")
    sources = {_read(OUT / f"{j}.source") for j in targets}
    for j in _all_job_ids() - targets:   # a version that stays must not lose its family when a middle one goes
        par = _read(OUT / f"{j}.parent")
        if par in targets:
            while par in targets:
                par = _read(OUT / f"{par}.parent")
            if par and JOB_RE.match(par):
                (OUT / f"{j}.parent").write_text(par, encoding="utf-8")
            else:
                (OUT / f"{j}.parent").unlink(missing_ok=True)
    for j in sorted(targets):
        _remove_job_files(j)
    # An uploaded-clips folder goes only when it sits inside the uploads area and no video that is staying uses it
    keep = {_read(OUT / f"{j}.source") for j in _all_job_ids()}
    base = UPLOADS.resolve()
    for src in sources:
        if not src or src in keep:
            continue
        try:
            folder = Path(src).resolve()
        except OSError:
            continue
        if folder.parent == base and JOB_RE.match(folder.name) and folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
    return {"ok": True, "removed": len(targets)}


# ---------- what she does with a finished video (counts, never her words) ----------
EVENTS_LOG = OUT / "events.jsonl"
EVENT_KINDS = ("copy", "download", "posted")


@app.post("/api/jobs/{job_id}/event")
async def job_event(job_id: str, request: Request):
    """Body: {"kind": "copy"|"download"|"posted", "platform": optional short name}. One line in out/events.jsonl."""
    _mine(request, job_id)
    if not _finished(job_id):
        raise HTTPException(409, "This video isn't finished yet.")
    body = await _json_body(request)
    kind = body.get("kind")
    if kind not in EVENT_KINDS:
        raise HTTPException(400, "That kind of event isn't known.")
    platform = body.get("platform")
    if platform is not None:
        if not isinstance(platform, str):
            raise HTTPException(400, "That platform name wasn't understood.")
        platform = re.sub(r"[^a-z0-9_]+", "_", platform.strip().lower())[:24].strip("_") or None   # a name, never free text
    entry = {"user": request.state.user, "job": job_id, "kind": kind, "platform": platform,
             "time": datetime.now().isoformat(timespec="seconds")}
    with feedback_lock, open(EVENTS_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"ok": True}


def _read_jsonl(path):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for l in lines:
        try:
            rows.append(json.loads(l))
        except ValueError:
            pass
    return rows


def _posted_jobs():
    """{job id: [platforms she said she posted to]} from the events log."""
    out = {}
    for e in _read_jsonl(EVENTS_LOG):
        if e.get("kind") == "posted" and e.get("job"):
            out.setdefault(e["job"], [])
            if e.get("platform") and e["platform"] not in out[e["job"]]:
                out[e["job"]].append(e["platform"])
    return out


# ---------- dashboard: every video this account has made ----------
def _read(path, default=None):
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return default


def _by_hand():
    """{video: minutes she said it would have taken by hand} from the feedback log (latest answer wins)."""
    out = {}
    for e in _read_feedback():
        if e.get("by_hand_minutes") and e.get("job"):
            out[e["job"]] = e["by_hand_minutes"]
    return out


def _first_words(d):
    """A title for videos made without the writer: the start of what she says."""
    words = (_read(d / "transcript.txt") or "").split()
    if not words:
        return None
    text = " ".join(words[:9])
    return text.rstrip(",.") + ("…" if len(words) > 9 else "")


def _video_row(job_id):
    """One dashboard row, built from the files the pipeline leaves in out/<job>/."""
    d = OUT / job_id
    try:
        rep = json.loads((d / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rep = None
    try:
        copy = json.loads((d / "copy.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        copy = {}
    made = (d / "report.json") if rep else (OUT / f"{job_id}.owner")
    status = jobs.get(job_id, {}).get("status") or ("done" if rep else "failed")
    parts = (rep or {}).get("parts") or []
    root, seen = job_id, set()
    while (p := _read(OUT / f"{root}.parent")) and p not in seen:   # walk back to the first version
        seen.add(root)
        root = p
    return {
        "job_id": job_id, "status": status, "owner": _read(OUT / f"{job_id}.owner"),
        "made_at": datetime.fromtimestamp(made.stat().st_mtime).isoformat(timespec="seconds") if made.exists() else None,
        "title": (copy.get("titles") or [None])[0] or _first_words(d),
        "length": (rep or {}).get("length"), "parts": len(parts) or (1 if rep else 0),
        "files": [p["file"] for p in parts] or (["final.mp4"] if rep else []),
        "mode": (rep or {}).get("mode"), "clips": len((rep or {}).get("timeline") or []) or None,
        "shorts": len(parts) if (rep or {}).get("mode") == "highlights" else None,   # one long video, N Shorts: still one video
        "build_seconds": (rep or {}).get("seconds_to_build"),
        "music": (rep or {}).get("music"), "denoised": (rep or {}).get("denoised"),
        "warnings": len((rep or {}).get("warnings") or []),
        "parent": _read(OUT / f"{job_id}.parent"), "first_version": root,
        "by_hand_minutes": _by_hand().get(job_id) or _by_hand().get(root),
        "timed_review_minutes": review.review_minutes(OUT, job_id),
    }


@app.get("/api/videos")
def my_videos(request: Request):
    """Newest first. Admins see everyone's (including videos made before accounts existed)."""
    me = request.state.user
    admin = me == auth.LOCAL_USER or auth.is_admin(me)
    rows, demo = [], _demo_id()
    for d in OUT.iterdir():
        if not d.is_dir() or d.name.startswith("_") or not JOB_RE.match(d.name) or d.name == demo:
            continue
        owner = _read(OUT / f"{d.name}.owner")
        if owner != me and not admin:
            continue
        if not (d / "report.json").exists() and not owner:
            continue  # half-made test folders from before accounts
        rows.append(_video_row(d.name))
    rows.sort(key=lambda r: r["made_at"] or "", reverse=True)
    versions = {}
    for r in rows:
        versions[r["first_version"]] = versions.get(r["first_version"], 0) + 1
    posted = _posted_jobs()
    for r in rows:
        r["versions"] = versions[r["first_version"]]
        r["posted"] = r["job_id"] in posted
        r["posted_to"] = posted.get(r["job_id"], [])
        r["private"] = _own_settings(r["job_id"]).get("privacy") == "private"   # pictures stayed on this computer
    return {"user": me, "admin": admin, "videos": rows}


@app.get("/videos", response_class=HTMLResponse)
def videos_page():
    return (Path(__file__).parent / "videos.html").read_text(encoding="utf-8")


@app.get("/api/jobs/{job_id}/thumb.jpg")
def job_thumb(job_id: str, request: Request):
    """A still from 1s into the video, made the first time the dashboard asks for it."""
    _mine(request, job_id)
    d = OUT / job_id
    thumb = d / "thumb.jpg"
    if not thumb.exists():
        src = next((d / f for f in ("final.mp4", "final_part1.mp4") if (d / f).exists()), None)
        if not src:
            raise HTTPException(404)
        from video_tool.config import FFMPEG
        with THUMBS:
            subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", "1", "-i", str(src), "-frames:v", "1",
                            "-vf", "scale=216:384", "-q:v", "5", str(thumb)], capture_output=True)
        if not thumb.exists():
            raise HTTPException(404)
    return FileResponse(thumb, media_type="image/jpeg")


# ---------- video kinds, ideas, post pack, posting log, example video, phone app files ----------
# These routes sit above the catch-all /api/jobs/{job_id}/{name} below on purpose: it would swallow them.
WEB_DIR = Path(__file__).parent
ASSET_FILES = {"icon-192.png", "icon-512.png", "og-image.png"}   # og-image: the generic link-preview picture (no footage, no personal details)


def _platform_name(value):
    """A short machine-friendly platform name ('tiktok'), never free text. None for nothing."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(400, "That platform name wasn't understood.")
    return re.sub(r"[^a-z0-9_]+", "_", value.strip().lower())[:24].strip("_") or None


def _title_slug(job_id, fallback="video"):
    try:
        title = json.loads((OUT / job_id / "copy.json").read_text(encoding="utf-8"))["titles"][0]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        title = ""
    return re.sub(r"[^A-Za-z0-9]+", "-", str(title)).strip("-")[:70] or fallback


def _job_title(job_id):
    try:
        t = json.loads((OUT / job_id / "copy.json").read_text(encoding="utf-8"))["titles"][0]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        t = _first_words(OUT / job_id)
    return " ".join(str(t or "").split())[:120]


@app.get("/api/formats")
def formats_list():
    """The kinds of video she can pick on the upload page: [{"id", "label", "film_list": [...]}] (no 'auto' entry)."""
    from video_tool import formats
    return [{"id": k, "label": v["label"], "film_list": list(v["film_list"])}
            for k, v in formats.FORMATS.items() if k != "auto"]


IDEAS_CACHE = {}   # (user, n) -> (time, ideas): tapping the button over and over must not cost a Claude call each time


def _recent_titles(user, limit=30):
    mine = [d for d in OUT.iterdir() if d.is_dir() and JOB_RE.match(d.name) and not d.name.startswith("_")
            and _read(OUT / f"{d.name}.owner") == user]
    mine.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    return {_job_title(d.name).lower() for d in mine[:limit]} - {""}


@app.get("/api/ideas")
def ideas_list(request: Request, n: str = "5"):
    """{"ideas": [{"title", "format", "shots": [4 strings]}]}. Claude when there is a key (a cent or two), a built-in
    seasonal list otherwise. Suggestions only. Ideas that match one of her recent videos' titles are left out."""
    from video_tool import writer
    try:
        n = max(1, min(int(n), 12))
    except ValueError:
        n = 5
    user = request.state.user
    hit = IDEAS_CACHE.get((user, n))
    if hit and time.time() - hit[0] < 30:
        return {"ideas": hit[1]}
    got = writer.ideas(n=n)
    seen = _recent_titles(user)
    fresh = [i for i in got if i["title"].strip().lower() not in seen]
    result = (fresh or got)[:n]
    IDEAS_CACHE[(user, n)] = (time.time(), result)
    if len(IDEAS_CACHE) > 200:
        IDEAS_CACHE.clear()
    return {"ideas": result}


SHOTS_LOCK = threading.Lock()
SHOTS_CACHE = {}   # (user, text) -> (time, answer): asking again for the same words must not cost another Claude call


@app.get("/api/shotlist/sources")
def shotlist_sources(request: Request):
    """Her own earlier videos that have words to reuse: [{"job_id", "title"}], newest first, at most 10."""
    me = request.state.user
    rows = []
    for d in OUT.iterdir():
        if (d.is_dir() and JOB_RE.match(d.name) and not d.name.startswith("_") and d.name != _demo_id()
                and _read(OUT / f"{d.name}.owner") == me and (_read(d / "transcript.txt") or "").strip()):
            rows.append((d.stat().st_mtime, d.name))
    rows.sort(reverse=True)
    return {"videos": [{"job_id": j, "title": _job_title(j) or "An earlier video"} for _, j in rows[:10]]}


@app.post("/api/shotlist")
async def shotlist_make(request: Request):
    """{"text": what she plans to say} or {"job_id": one of her videos, to reuse its voice-over words}.
    Returns {"shots": [{"phrase", "film", "seconds"}], "tips", "warnings", "source": "ai"|"plain"}. Claude when there is a
    key (her words only), a plain list made on this computer otherwise. Suggestions only."""
    from video_tool import shotlist
    body = await _json_body(request)
    text = body.get("text")
    if body.get("job_id"):
        _mine(request, str(body["job_id"]))
        text = _read(OUT / str(body["job_id"]) / "transcript.txt") or ""
    if not isinstance(text, str):
        raise HTTPException(400, "The words box needs plain text.")
    text = " ".join(text.split())[:shotlist.MAX_TEXT]
    if not text:
        raise HTTPException(400, "Type or paste what you plan to say first.")
    key = (request.state.user, text)
    return await run_in_threadpool(_cached_shots, key)


def _cached_shots(key):
    from video_tool import shotlist
    # Serialize lookup + generation so simultaneous identical taps cannot bill twice.
    with SHOTS_LOCK:
        hit = SHOTS_CACHE.get(key)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        result = shotlist.shot_list(key[1])
        if len(SHOTS_CACHE) >= 200:
            SHOTS_CACHE.clear()
        SHOTS_CACHE[key] = (time.time(), result)
        return result


# ---- post pack ----
PACKS = OUT / "_packs"
PACK_SLOTS = threading.Semaphore(2)   # a pack is mostly a copy of the video: two at once is plenty


def _drop(path):
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


@app.get("/api/jobs/{job_id}/postpack")
def job_postpack(job_id: str, request: Request):
    """One zip with the video(s), captions.srt, cover.jpg, alt-text.txt and post-text.txt. Owner or admin only.
    Built into a temporary file that is removed once it has been sent."""
    import tempfile
    from starlette.background import BackgroundTask
    from video_tool import postpack
    from video_tool.errors import AlexError
    _mine(request, job_id)
    PACKS.mkdir(parents=True, exist_ok=True)
    for old in PACKS.glob("*.zip*"):   # leftovers of an interrupted download
        try:
            if time.time() - old.stat().st_mtime > 3600:
                old.unlink()
        except OSError:
            pass
    fd, tmp = tempfile.mkstemp(dir=PACKS, suffix=".zip")
    os.close(fd)
    try:
        with PACK_SLOTS:
            postpack.build(OUT / job_id, tmp)
    except AlexError:
        _drop(tmp)
        raise HTTPException(409, "This video isn't finished yet, so there is nothing to put in the pack.")
    except Exception:
        _drop(tmp)
        raise HTTPException(500, "I couldn't put the pack together just now. Please try again.")
    return FileResponse(tmp, media_type="application/zip", filename=f"{_title_slug(job_id, 'video')}-post-pack.zip",
                        background=BackgroundTask(_drop, tmp))


# ---- posting log: what she has posted, by hand, and when. Nothing here posts anything. ----
# Stored per person in a small JSON file: <data>/users/<name>/posting.json (this computer's own account:
# out/_local-posting.json). Shape: {"jobs": {job: {"status", "updated"}}, "posted": [{"job", "platform", "date", "title"}]}.
# A video's status is draft until she says otherwise. Marking it 'posted' adds one entry per platform (the same
# platform again just changes the date); marking it draft or reviewed moves it back and forgets its posted entries.
POSTING_STATUSES = ("draft", "reviewed", "posted")
posting_lock = threading.Lock()
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _posting_file(user):
    return OUT / "_local-posting.json" if user == auth.LOCAL_USER else _user_dir(user) / "posting.json"


def _load_posting(user):
    try:
        data = json.loads(_posting_file(user).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("jobs"), dict):
        data["jobs"] = {}
    if not isinstance(data.get("posted"), list):
        data["posted"] = []
    return data


def _save_posting(user, data):
    f = _posting_file(user)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)   # all or nothing, like users.json


def _posting_view(data, job_id):
    posted = sorted((p for p in data["posted"] if p.get("job") == job_id), key=lambda p: p.get("date") or "")
    rec = data["jobs"].get(job_id) or {}
    status = "posted" if posted else (rec.get("status") if rec.get("status") in ("draft", "reviewed") else "draft")
    return {"status": status, "posted": [{"platform": p.get("platform"), "date": p.get("date")} for p in posted],
            "date": posted[-1]["date"] if posted else None}


def _parse_day(value, what="date"):
    """A real calendar day written YYYY-MM-DD, or a plain 400."""
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            d = date.fromisoformat(value)
            if date(2020, 1, 1) <= d <= date.today() + timedelta(days=400):
                return d
        except ValueError:
            pass
    raise HTTPException(400, f"That {what} isn't right. Write it as year-month-day, for example 2026-10-09.")


@app.get("/api/jobs/{job_id}/posting")
def job_posting_get(job_id: str, request: Request):
    """{"status": "draft"|"reviewed"|"posted", "posted": [{"platform", "date"}], "date": last posted day | null}."""
    _mine(request, job_id)
    return _posting_view(_load_posting(request.state.user), job_id)


@app.post("/api/jobs/{job_id}/posting")
async def job_posting_set(job_id: str, request: Request):
    """Body: {"status": "draft"|"reviewed"|"posted", "platform": optional, "date": optional YYYY-MM-DD (default today)}.
    Returns the same shape as GET. The date can't be more than a day ahead."""
    _mine(request, job_id)
    if not _finished(job_id):
        raise HTTPException(409, "This video isn't finished yet.")
    user = request.state.user
    body = await _json_body(request)
    status = body.get("status")
    if status not in POSTING_STATUSES:
        raise HTTPException(400, "The status has to be draft, reviewed or posted.")
    platform = _platform_name(body.get("platform"))
    today = datetime.now().date()
    day = _parse_day(body["date"]) if body.get("date") is not None else today
    if day > today + timedelta(days=1):
        raise HTTPException(400, "That date is still ahead of us. Pick today or an earlier day.")
    title = _job_title(job_id)
    with posting_lock:
        data = _load_posting(user)
        data["jobs"][job_id] = {"status": status, "updated": datetime.now().isoformat(timespec="seconds")}
        keep = [p for p in data["posted"] if p.get("job") != job_id
                or (status == "posted" and p.get("platform") != platform)]
        if status == "posted":
            keep.append({"job": job_id, "platform": platform, "date": day.isoformat(), "title": title})
        data["posted"] = keep
        _save_posting(user, data)
        view = _posting_view(data, job_id)
    if status == "posted":   # the same line her 'I posted it' button writes, so My videos shows it as posted
        with feedback_lock, open(EVENTS_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"user": user, "job": job_id, "kind": "posted", "platform": platform,
                                 "time": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False) + "\n")
    return view


@app.get("/api/posting/week")
def posting_week(request: Request):
    """This week (Monday to Sunday, her local date): {"goal_min": 3, "goal_max": 5, "count_this_week": videos posted,
    "posted": [{"job", "title", "platform", "date"}], "free_days": ["Thu", ...]}. free_days are today and the days
    after it, up to Sunday, with nothing posted on them."""
    today = datetime.now().date()
    monday = today - timedelta(days=today.weekday())
    mine, rows = _load_posting(request.state.user), []
    for p in mine["posted"]:
        try:
            d = date.fromisoformat(str(p.get("date")))
        except ValueError:
            continue
        if monday <= d <= monday + timedelta(days=6):
            rows.append((d, p))
    rows.sort(key=lambda r: (r[0], str(r[1].get("job"))))
    busy = {d for d, _ in rows}
    free = [WEEKDAYS[i] for i in range(today.weekday(), 7) if monday + timedelta(days=i) not in busy]
    return {"goal_min": 3, "goal_max": 5, "count_this_week": len({p.get("job") for _, p in rows}),
            "posted": [{"job": p.get("job"), "title": p.get("title") or "", "platform": p.get("platform"),
                        "date": d.isoformat()} for d, p in rows],
            "free_days": free}


ICS_CONTROL = re.compile(r"[\x00-\x1f\x7f\u0085  ]+")


def _ics_text(value):
    """Text for an iCalendar value (RFC 5545 section 3.3.11): control characters and line breaks become spaces, and
    backslash, semicolon and comma are escaped so a title can never add a line or a field of its own."""
    value = ICS_CONTROL.sub(" ", str(value or "")).strip()
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")


def _ics_fold(line):
    """Lines longer than 75 bytes continue on the next line after one space (never inside a multi-byte character)."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > 75:
            out.append(cur)
            cur, size = " ", 1
        cur += ch
        size += n
    out.append(cur)
    return "\r\n".join(out)


@app.get("/api/posting/reminder.ics")
def posting_reminder(day: str = "", title: str = "", time_of_day: str = Query("12:00", alias="time")):
    """A calendar file for a one-hour 'Post your video' reminder on `day` (YYYY-MM-DD, required), at `time` (HH:MM,
    default 12:00, her own clock), with the video's name if given. No guests, nothing is sent anywhere."""
    d = _parse_day(day, "day")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_of_day or ""):
        raise HTTPException(400, "That time isn't right. Write it like 14:30.")
    start = datetime.combine(d, datetime.strptime(time_of_day, "%H:%M").time())
    name = ICS_CONTROL.sub(" ", title or "").strip()[:100].strip()
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Alex Video Maker//Post reminder//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "BEGIN:VEVENT", f"UID:{uuid.uuid4()}@video-maker.invalid", f"DTSTAMP:{stamp}",
             f"DTSTART:{start:%Y%m%dT%H%M%S}", f"DTEND:{start + timedelta(hours=1):%Y%m%dT%H%M%S}",
             "SUMMARY:" + _ics_text("Post your video" + (f": {name}" if name else "")),
             "DESCRIPTION:" + _ics_text("Open your Video Maker, download the post pack and post it yourself."),
             "END:VEVENT", "END:VCALENDAR"]
    body = "\r\n".join(_ics_fold(l) for l in lines) + "\r\n"
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="post-reminder-{d.isoformat()}.ics"'})


# ---- the example video ('See an example') ----
def _demo_id():
    """The finished video out/_demo.txt names, or None if there is no such file or no such finished video."""
    first = ((_read(OUT / "_demo.txt") or "").splitlines() or [""])[0].strip()
    if JOB_RE.match(first) and (OUT / first / "report.json").is_file():
        return first
    return None


def _ensure_thumb(d):
    thumb = d / "thumb.jpg"
    if not thumb.exists():
        src = next((d / f for f in ("final.mp4", "final_part1.mp4") if (d / f).exists()), None)
        if not src:
            return None
        from video_tool.config import FFMPEG
        with THUMBS:
            subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", "1", "-i", str(src), "-frames:v", "1",
                            "-vf", "scale=216:384", "-q:v", "5", str(thumb)], capture_output=True)
    return thumb if thumb.exists() else None


@app.get("/api/demo")
def demo_info():
    """{"available": false, "job": null} or {"available": true, "job": <as GET /api/jobs/{id} for a finished video,
    plus "example": true, "video_urls": ["/api/demo/video/1", ...], "thumb_url": "/api/demo/thumb">}. Read-only, same
    for every signed-in person; the video is nobody's and is not in My videos."""
    from video_tool import postpack
    did = _demo_id()
    if not did:
        return {"available": False, "job": None}
    d = OUT / did
    try:
        job = {"status": "done", "report": _public_report(json.loads((d / "report.json").read_text(encoding="utf-8")))}
        if (d / "copy.json").exists():
            job["copy"] = json.loads((d / "copy.json").read_text(encoding="utf-8"))
        job["transcript"] = _transcript(d, job["report"])
    except (OSError, ValueError):
        return {"available": False, "job": None}
    files = postpack.video_files(d)
    if not files:
        return {"available": False, "job": None}
    job.update(example=True, video_urls=[f"/api/demo/video/{n}" for n in range(1, len(files) + 1)],
               thumb_url="/api/demo/thumb")
    return {"available": True, "job": job}


@app.get("/api/demo/thumb")
def demo_thumb():
    did = _demo_id()
    thumb = _ensure_thumb(OUT / did) if did else None
    if not thumb:
        raise HTTPException(404)
    return FileResponse(thumb, media_type="image/jpeg")


@app.get("/api/demo/video/{part}")
def demo_video(part: str):
    """Part 1, 2, 3... of the example video. The file comes from the video's own list, never from the address."""
    from video_tool import postpack
    did = _demo_id()
    if not did or not re.fullmatch(r"[1-9]\d?", part):
        raise HTTPException(404)
    files = postpack.video_files(OUT / did)
    n = int(part)
    if n > len(files) or not VIDEO_OUT.match(files[n - 1]):
        raise HTTPException(404)
    return FileResponse(OUT / did / files[n - 1], media_type="video/mp4")


# ---- the YouTube post for the long video (Shorts from a long video) ----
def _longform_kit(job_id):
    try:
        kit = json.loads((OUT / job_id / "longform.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return kit if isinstance(kit, dict) else None


@app.get("/api/jobs/{job_id}/longform")
def job_longform(job_id: str, request: Request):
    """The YouTube post for the whole long video: {titles, description, chapters, tags, source}. Owner or admin only."""
    _mine(request, job_id)
    kit = _longform_kit(job_id)
    if kit is None:
        raise HTTPException(404, "This video doesn't have a YouTube post for a long video.")
    return kit


@app.get("/api/jobs/{job_id}/long.srt")
def job_long_srt(job_id: str, request: Request):
    """The captions file (.srt) for the whole long video. Owner or admin only."""
    _mine(request, job_id)
    f = OUT / job_id / "long.srt"
    if not f.is_file():
        raise HTTPException(404, "This video doesn't have a captions file for a long video.")
    kit = _longform_kit(job_id) or {}
    title = (kit.get("titles") or [""])[0]
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(title)).strip("-")[:70] or "long-video"
    return FileResponse(f, media_type="application/x-subrip", filename=f"{slug}-captions.srt")


# ---- install on the phone (home-screen app) ----
@app.get("/manifest.webmanifest")
def web_manifest():
    return FileResponse(WEB_DIR / "manifest.webmanifest", media_type="application/manifest+json",
                        headers={"Cache-Control": "no-cache"})


@app.get("/sw.js")
def service_worker():
    return FileResponse(WEB_DIR / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


# The motion files: GSAP and Lenis (web/vendor, refreshed with `npm run vendor`) and our own motion.js. Exact names only.
STATIC_FILES = {"gsap.min.js": ("vendor/gsap.min.js", "text/javascript"), "lenis.min.js": ("vendor/lenis.min.js", "text/javascript"),
                "lenis.css": ("vendor/lenis.css", "text/css"), "motion.js": ("motion.js", "text/javascript"),
                "scene3d.js": ("vendor/scene3d.js", "text/javascript")}


@app.get("/static/{name}")
def web_static(name: str):
    """Nothing else can be read through this route: the name has to be one of the four files above."""
    spec = STATIC_FILES.get(name)
    if not spec or not (WEB_DIR / spec[0]).is_file():
        raise HTTPException(404)
    return FileResponse(WEB_DIR / spec[0], media_type=spec[1], headers={"Cache-Control": "public, max-age=3600"})


@app.get("/assets/{name}")
def web_asset(name: str):
    """Only the app icons and the link-preview picture, by exact name: nothing else can be read through this route."""
    if name not in ASSET_FILES or not (WEB_DIR / "assets" / name).is_file():
        raise HTTPException(404)
    return FileResponse(WEB_DIR / "assets" / name, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


def _download_name(job_id, name):
    """'Affordable-Home-in-Solon-Iowa-Home-Tour-part2.mp4' instead of 'web-20261004-221402-03955-final_part2.mp4'."""
    try:
        title = json.loads((OUT / job_id / "copy.json").read_text(encoding="utf-8"))["titles"][0]
    except (OSError, ValueError, KeyError, IndexError):
        title = ""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")[:70] or job_id
    part = re.search(r"_part(\d+)", name)
    word = "-short" if part and _job_is_long(job_id) else "-part"
    return f"{slug}{word + part.group(1) if part else ''}.mp4"


THUMBS = threading.Semaphore(2)   # opening My videos for the first time must not start 100 ffmpegs at once


@app.get("/api/jobs/{job_id}/{name}")
def job_file(job_id: str, name: str, request: Request):
    _mine(request, job_id)
    is_video = bool(VIDEO_OUT.match(name))
    if not (is_video or name in SERVE) or not (OUT / job_id / name).exists():
        raise HTTPException(404)
    return FileResponse(OUT / job_id / name, media_type="video/mp4" if is_video else SERVE[name],
                        filename=_download_name(job_id, name) if is_video else None)


# ---------- admin: unresolved feedback + accounts ----------
def _admin(request):
    if not auth.is_admin(request.state.user):
        raise HTTPException(403, "Admins only.")


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    _admin(request)
    return (Path(__file__).parent / "admin.html").read_text(encoding="utf-8")


@app.get("/api/admin/feedback")
def admin_feedback(request: Request):
    _admin(request)
    try:
        users = {n: {"admin": u.get("admin", False)} for n, u in auth.load_users().items()}
    except auth.UsersFileError:
        users = {}
    return {"items": list(reversed(_read_feedback())), "users": users}


@app.get("/api/admin/health")
def admin_health(request: Request):
    _admin(request)
    from video_tool import ai
    ids = _all_job_ids()
    reports = [OUT / i / "report.json" for i in ids]
    times = [f.stat().st_mtime for f in reports if f.exists()]
    failed = {r.get("job") for r in _read_jsonl(OUT / "failures.jsonl") if r.get("job")}
    return {"disk_free_gb": round(shutil.disk_usage(OUT).free / 1024 ** 3, 1), "jobs_total": len(ids),
            "jobs_failed": len(failed), "key_present": ai.has_key(),
            "last_job_at": datetime.fromtimestamp(max(times)).isoformat(timespec="seconds") if times else None,
            "keep_awake": KEEP_AWAKE["on"], "help_contact_set": bool(_help_contact())}


def _all_video_rows():
    """Every finished or failed video's dashboard row, for the class report (never her words: see video_tool/results.py)."""
    rows, demo = [], _demo_id()
    for d in OUT.iterdir():
        if not d.is_dir() or d.name.startswith("_") or not JOB_RE.match(d.name) or d.name == demo:
            continue
        if not (d / "report.json").exists() and not _read(OUT / f"{d.name}.owner"):
            continue   # half-made test folders from before accounts
        rows.append(_video_row(d.name))
    return rows


@app.get("/api/admin/results")
def admin_results(request: Request):
    """Did the tool save her time and is she posting more? Videos, remakes, build / review / by-hand / saved minutes and
    videos per week, per person and overall. Admin only."""
    _admin(request)
    from video_tool import results
    return results.summarise(_all_video_rows(), _read_jsonl(EVENTS_LOG))


@app.get("/api/admin/results.csv")
def admin_results_csv(request: Request):
    """The same numbers, one row per video, for a spreadsheet (no titles, captions or file names). Admin only."""
    _admin(request)
    from video_tool import results
    body = results.to_csv(results.summarise(_all_video_rows(), _read_jsonl(EVENTS_LOG))["videos"])
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="video-maker-results.csv"'})


@app.get("/api/admin/failures")
def admin_failures(request: Request):
    """The last 20 failed videos with the technical detail (newest first). Admin only."""
    _admin(request)
    return {"items": list(reversed(_read_jsonl(OUT / "failures.jsonl")[-20:]))}


@app.post("/api/admin/feedback/{fid}/resolve")
def admin_resolve(fid: str, request: Request):
    _admin(request)
    with feedback_lock:
        items = _read_feedback()
        found = False
        for it in items:
            if it.get("id") == fid:
                it["status"] = "resolved"
                found = True
        if not found:
            raise HTTPException(404, "That item isn't there any more.")
        FEEDBACK_LOG.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")
    return {"ok": True}


@app.post("/api/admin/users")
async def admin_add_user(request: Request):
    _admin(request)
    body = await _json_body(request)
    try:
        name = await run_in_threadpool(auth.set_user, str(body.get("username", "")), str(body.get("password", "")),
                                       bool(body.get("admin")) if "admin" in body else None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "user": name}
