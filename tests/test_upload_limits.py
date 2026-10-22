"""Both upload routes obey the same size and disk limits, and a refused or failed upload leaves nothing behind.

Run: venv\\Scripts\\python tests\\test_upload_limits.py
The old single-request upload (POST /api/jobs, still used by scripts and several tests) used to copy files with no total-size
and no free-disk check, and its body is parsed before the route runs. web/app.py now has a shared check (_check_capacity)
for both routes and a guard (OldUploadGuard) that refuses an oversize body before it is read. In-process copy of the site,
throwaway data folder, the video maker replaced by a recorder: no video is made, nothing is sent anywhere."""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = Path(tempfile.mkdtemp(prefix="alex-uplimits-"))
os.environ.update(ALEX_DATA_DIR=str(DATA), ALEX_HOSTED="1", ADMIN_USER="tester", ADMIN_PASSWORD="admin-pass-123",
                  ANTHROPIC_API_KEY="", APP_PASSWORD="", ALEX_AUTO_SLIM="0")

from fastapi.testclient import TestClient   # noqa: E402
from web import app as W, auth   # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {detail}"))
    if not cond:
        FAILS.append(name)


STARTS = []
W._start = lambda job_id, folder, user, extra=(): STARTS.append(job_id)
auth.set_user("alex", "alex-pass-1234")
alex = TestClient(W.app, base_url="http://testserver")
assert alex.post("/api/login", json={"username": "alex", "password": "alex-pass-1234"}).status_code == 200
W.UPLOADS.mkdir(parents=True, exist_ok=True)
REAL = dict(MAX_TOTAL=W.MAX_TOTAL, MIN_FREE=W.MIN_FREE, FORM_OVERHEAD=W.FORM_OVERHEAD)
MP4 = "video/mp4"


def folders():
    return sorted(p.name for p in W.UPLOADS.iterdir())


def reset():
    STARTS.clear()
    for k, v in REAL.items():
        setattr(W, k, v)


def upload(*sizes, voice=None, name="clip{}.mp4"):
    files = [("clips", (name.format(i), b"v" * n, MP4)) for i, n in enumerate(sizes, 1)]
    if voice:
        files.append(("voice", ("voice.m4a", b"a" * voice, "audio/mp4")))
    return alex.post("/api/jobs", files=files)


# ---------- ordinary uploads still work ----------
print("--- ordinary uploads still work ---")
reset()
r = upload(5000, 7000, voice=3000)
check("an ordinary upload with clips and a voice-over is accepted and started", r.status_code == 200 and len(STARTS) == 1, r.text)
job = r.json().get("job_id", "")
got = sorted(p.name for p in (W.UPLOADS / job).iterdir()) if (W.UPLOADS / job).is_dir() else []
check("...and every file is saved whole under its slot name", got == ["01.mp4", "02.mp4", "original_names.json", "voice.m4a"]
      and (W.UPLOADS / job / "01.mp4").stat().st_size == 5000 and (W.UPLOADS / job / "02.mp4").stat().st_size == 7000, got)
before = folders()

# ---------- refused up front, before the body is read ----------
print("\n--- an oversize body is refused before it is read ---")
reset()
r = alex.post("/api/jobs", content=b"tiny", headers={"content-type": "multipart/form-data; boundary=x",
                                                     "content-length": str(REAL["MAX_TOTAL"] + REAL["FORM_OVERHEAD"] + 1)})
check("a declared size over the limit is refused with 413 and a plain message", r.status_code == 413 and "more than one video can use" in r.text, (r.status_code, r.text))
check("...nothing was created or started", folders() == before and not STARTS, (folders(), STARTS))
reset()
W.MIN_FREE = 10 ** 18   # more free space than any disk has
r = alex.post("/api/jobs", content=b"tiny", headers={"content-type": "multipart/form-data; boundary=x", "content-length": "1000"})
check("a declared size that would leave too little disk space is refused with 507", r.status_code == 507 and "almost out of space" in r.text, (r.status_code, r.text))
check("...nothing was created or started", folders() == before and not STARTS)

# no declared size (chunked): the bytes are counted as they arrive
reset()
W.MAX_TOTAL, W.FORM_OVERHEAD = 100_000, 0
tmp_before = set(os.listdir(tempfile.gettempdir()))


def body():
    for _ in range(30):
        yield b"x" * 10_000


r = alex.post("/api/jobs", content=body(), headers={"content-type": "multipart/form-data; boundary=x"})
check("a body with no declared size is stopped once it passes the limit (413)", r.status_code == 413, (r.status_code, r.text[:120]))
check("...nothing was created or started, and no stray upload files were left in the temp folder",
      folders() == before and not STARTS and not [f for f in set(os.listdir(tempfile.gettempdir())) - tmp_before if f.startswith(("tmp", "spool"))],
      (folders(), sorted(set(os.listdir(tempfile.gettempdir())) - tmp_before)[:5]))

# ---------- refused by the route itself (sizes known after parsing) ----------
print("\n--- the route applies the same limits as the chunked upload ---")
reset()
W.MAX_TOTAL = 1000
r = upload(600, 600)
check("files that together pass the limit are refused (400) with the chunked route's message",
      r.status_code == 400 and "in total, more than one video can use" in r.text, (r.status_code, r.text))
check("...no folder was left behind and nothing started", folders() == before and not STARTS, (folders(), STARTS))
reset()
W.MIN_FREE = 10 ** 18
r = upload(600)
check("too little free disk space is refused (507)", r.status_code == 507 and "almost out of space" in r.text, (r.status_code, r.text))
check("...nothing was left behind", folders() == before and not STARTS)
reset()
r = upload(5000, 0)
check("an empty file is refused (400) as before", r.status_code == 400 and "empty" in r.text, (r.status_code, r.text))
check("...nothing was left behind", folders() == before and not STARTS)

# ---------- a failure while saving ----------
print("\n--- a failure while saving leaves no half-written video ---")
reset()
real_copy = W.shutil.copyfileobj


def failing_copy(src, dst, *a, **k):
    dst.write(b"partial")
    raise OSError(28, "No space left on device")


W.shutil.copyfileobj = failing_copy
try:
    r = upload(5000, 7000)
finally:
    W.shutil.copyfileobj = real_copy
check("a disk-full error while copying gives a plain 507", r.status_code == 507 and "out of space" in r.text, (r.status_code, r.text))
check("...the partly written folder is removed and nothing started", folders() == before and not STARTS, (folders(), STARTS))
reset()
r = upload(4000)
check("and the next ordinary upload still works", r.status_code == 200 and len(STARTS) == 1, r.text)

# ---------- the chunked route still has the same limits (it now shares the code) ----------
print("\n--- the chunked route shares the code and keeps its behaviour ---")
reset()
r = alex.post("/api/uploads", json={"clips": [{"name": "a.mp4", "size": REAL["MAX_TOTAL"] + 1}], "voice": None})
check("chunked: a total over the limit is refused (400)", r.status_code == 400 and "in total, more than one video can use" in r.text, (r.status_code, r.text))
W.MIN_FREE = 10 ** 18
r = alex.post("/api/uploads", json={"clips": [{"name": "a.mp4", "size": 1000}], "voice": None})
check("chunked: too little disk space is refused (507)", r.status_code == 507, (r.status_code, r.text))
reset()
r = alex.post("/api/uploads", json={"clips": [{"name": "a.mp4", "size": 1000}], "voice": None})
check("chunked: an ordinary start still works", r.status_code == 200 and r.json().get("slots"), (r.status_code, r.text))

print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED'}")
sys.exit(1 if FAILS else 0)
