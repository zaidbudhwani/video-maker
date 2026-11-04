r"""When a render times out, EVERYTHING it started is stopped before the next video begins.

Run: venv\Scripts\python tests\test_process_tree.py
The website runs the pipeline as one subprocess and the pipeline starts ffmpeg. subprocess.run(timeout=...) only killed the
first process, so a stalled ffmpeg could be left running (processor, disk) while the next video started. video_tool/proc.py
kills the whole tree. A SYNTHETIC stalled pipeline is used (plain Python processes that sleep, never ffmpeg and never her
footage): a parent that starts a child and a grandchild, then stalls. Nothing is sent anywhere."""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = Path(tempfile.mkdtemp(prefix="alex-proctree-"))
os.environ.update(ALEX_DATA_DIR=str(DATA), ALEX_HOSTED="1", ADMIN_USER="tester", ADMIN_PASSWORD="admin-pass-123",
                  ANTHROPIC_API_KEY="", APP_PASSWORD="", ALEX_AUTO_SLIM="0")

from video_tool import proc   # noqa: E402

FAILS = []
SPAWNED = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {detail}"))
    if not cond:
        FAILS.append(name)


def alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
        return f'"{pid}"' in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def wait_gone(pids, seconds=8.0):
    end = time.time() + seconds
    while time.time() < end:
        if not any(alive(p) for p in pids):
            return True
        time.sleep(0.2)
    return False


CHILD = """import subprocess, sys, time
g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
open(sys.argv[1] + ".grand", "w").write(str(g.pid))
time.sleep(300)
"""
PARENT = """import os, subprocess, sys, time
c = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "child_stall.py"), sys.argv[1]])
open(sys.argv[1] + ".child", "w").write(str(c.pid))
time.sleep(300)
"""
(DATA / "child_stall.py").write_text(CHILD)
(DATA / "parent_stall.py").write_text(PARENT)


def stalled_pipeline(tag):
    """A parent that starts a child, which starts a grandchild, records their pids, then stalls (like an ffmpeg that hangs)."""
    pidfile = DATA / f"{tag}.pids"
    return [sys.executable, str(DATA / "parent_stall.py"), str(pidfile)], pidfile


def read_pids(pidfile):
    end = time.time() + 10
    out = {}
    while time.time() < end and len(out) < 2:
        for k in ("child", "grand"):
            f = Path(str(pidfile) + "." + k)
            if f.exists() and f.read_text().strip():
                out[k] = int(f.read_text())
        time.sleep(0.1)
    SPAWNED.extend(out.values())
    return out


try:
    # ---------- the helper itself ----------
    print("--- run_tree stops the whole tree ---")
    cmd, pf = stalled_pipeline("a")
    t0 = time.time()
    raised = False
    try:
        proc.run_tree(cmd, timeout=4)
    except subprocess.TimeoutExpired:
        raised = True
    pids = read_pids(pf)
    check("a stalled command raises TimeoutExpired at the limit (not before, not far after)", raised and 3.5 <= time.time() - t0 < 25, time.time() - t0)
    check("the synthetic child and grandchild had really started", len(pids) == 2, pids)
    check("after run_tree returns, the child and grandchild are gone (no orphan left running)", wait_gone(list(pids.values())), {k: alive(v) for k, v in pids.items()})

    ok = proc.run_tree([sys.executable, "-c", "import sys; sys.exit(3)"], timeout=30)
    check("a command that finishes normally returns its exit code and is left alone", ok.returncode == 3)
    ok = proc.run_tree([sys.executable, "-c", "print('hello')"], timeout=30, stdout=subprocess.DEVNULL)
    check("...exit code 0 is returned as 0", ok.returncode == 0)

    # ---------- the website's path ----------
    print("\n--- the website: a timed-out render is stopped completely, and the next video is made ---")
    from web import app as W   # noqa: E402
    W.RENDER_TIMEOUT = 5
    folder = DATA / "clips"
    folder.mkdir()
    cmds = {}
    cmd1, pf1 = stalled_pipeline("w1")
    W._pipeline_command = lambda folder, job_id, extra=(): cmds[job_id]
    cmds["web-stall"] = cmd1
    W.jobs["web-stall"] = {"status": "queued"}
    (W.OUT / "web-stall").mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    W._run("web-stall", folder, "alex")
    pids1 = read_pids(pf1)
    check("the website marks the stalled video failed and says it timed out",
          W.jobs["web-stall"]["status"] == "failed" and "Timed out" in (W.OUT / "web-stall.log").read_text(), W.jobs["web-stall"])
    check("...the pipeline's child and grandchild are not left running", len(pids1) == 2 and wait_gone(list(pids1.values())), {k: alive(v) for k, v in pids1.items()})
    check("...the render lock was released (the next video can start)", W.render_lock.acquire(blocking=False) and (W.render_lock.release() or True))

    ok_cmd = [sys.executable, "-c", "import json, pathlib, sys; d = pathlib.Path(sys.argv[1]); d.mkdir(parents=True, exist_ok=True); "
              "(d / 'report.json').write_text(json.dumps({'ok': True}))", str(W.OUT / "web-next")]
    cmds["web-next"] = ok_cmd
    W.jobs["web-next"] = {"status": "queued"}
    W._run("web-next", folder, "alex")
    check("the next video then completes normally", W.jobs["web-next"]["status"] == "done", W.jobs["web-next"])
finally:
    for pid in SPAWNED:      # belt and braces: nothing synthetic may outlive this test
        if alive(pid):
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
            else:
                os.kill(pid, 9)

print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED'}")
sys.exit(1 if FAILS else 0)
