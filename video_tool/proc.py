"""Run a command and, if it has to be stopped, stop EVERYTHING it started.

The website runs the whole pipeline as one subprocess, and the pipeline starts ffmpeg, ffprobe and others. Python's own
subprocess.run(timeout=...) only kills the first process, so on a timeout an ffmpeg could be left running (using the
processor and writing files) while the next video started. run_tree() kills the whole tree and waits until it is gone."""
import os
import signal
import subprocess


def kill_tree(p):
    """Stop process p and everything it started. Returns when p has exited."""
    if p.poll() is None:
        try:
            if os.name == "nt":
                # /T = the whole tree, /F = force. Must run while the parent is still alive (that is how the tree is found).
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, timeout=30)
            else:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            p.kill()          # in case the tree kill could not run: at least the first process goes
        except OSError:
            pass
    try:
        p.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def run_tree(args, timeout, **popen_kwargs):
    """Like subprocess.run(args, timeout=timeout) for a command that starts other processes. Returns the finished Popen
    (check .returncode). On a timeout the whole tree is killed first, then subprocess.TimeoutExpired is raised."""
    if os.name != "nt":
        popen_kwargs.setdefault("start_new_session", True)   # its own process group, so one signal reaches the whole tree
    p = subprocess.Popen(args, **popen_kwargs)
    try:
        p.wait(timeout=timeout)
    except BaseException:    # a timeout, or the server stopping: never leave the tree running
        kill_tree(p)
        raise
    return p
