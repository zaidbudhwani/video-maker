r"""A local page where a person builds the answer key for one finished video: for each phrase she says, which clips show it
and which contradict it (a clip of a different room). Two raters each save their own key.

    venv\Scripts\python tests\answer_key_tool.py out\<job> --rater A [--clips <her clips folder>] [--port 8321] [--save-dir DIR]

Then open the address it prints. Works offline, needs no key, listens on this computer only (127.0.0.1), and saves
answer_key.<rater>.json next to the job (or in --save-dir). Nothing is sent anywhere. Rater B should mark without looking at
A's file, so the agreement figure in tests\evaluate_matching.py means something.
"""
import argparse
import json
import mimetypes
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import evaluate_matching as E   # noqa: E402

PAGE = Path(__file__).resolve().parent / "answer_key_page.html"


def make_handler(job, snap, rater, save_dir, house=None):
    job, save_dir = Path(job), Path(save_dir)
    clip_dir = Path(snap["clips_dir"])
    clip_names = {c["name"] for c in snap["clips"]}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode("utf-8")
            elif isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path):
            size = path.stat().st_size
            start, end = 0, size - 1
            rng = self.headers.get("Range", "")
            if rng.startswith("bytes="):
                a, _, b = rng[6:].partition("-")
                try:
                    start = int(a) if a else max(0, size - int(b))
                    end = min(int(b), size - 1) if a and b else end
                except ValueError:
                    start, end = 0, size - 1
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(206 if rng else 200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if rng:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with open(path, "rb") as fh:
                fh.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = fh.read(min(1 << 16, left))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (ConnectionError, OSError):
                        return
                    left -= len(chunk)

        def do_GET(self):
            p = unquote(self.path.split("?", 1)[0])
            if p == "/":
                return self._send(200, PAGE.read_text(encoding="utf-8"), "text/html; charset=utf-8")
            if p == "/data":
                kp = E.key_path(save_dir, rater)
                key = None
                if kp.exists():
                    try:
                        key = E.load_key(kp)
                    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                        key = None
                if key is None:
                    key = E.new_key(snap, rater, house)
                return self._send(200, {"job": snap["job"], "rater": rater, "phrases": snap["phrases"],
                                        "clips": [c["name"] for c in snap["clips"]], "marks": key["marks"],
                                        "has_video": (job / "final.mp4").exists(), "saved": kp.exists()})
            if p.startswith("/clip/"):
                name = p[6:]
                if name not in clip_names:   # only the job's own clips, never any other path
                    return self._send(404, {"error": "not found"})
                return self._file(clip_dir / name)
            if p == "/video":
                if not (job / "final.mp4").exists():
                    return self._send(404, {"error": "not found"})
                return self._file(job / "final.mp4")
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/save":
                return self._send(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > 2_000_000:
                    raise ValueError("That is too much to save.")
                body = json.loads(self.rfile.read(n).decode("utf-8"))
                key = E.new_key(snap, rater, house)
                for name, m in (body.get("marks") or {}).items():
                    if name in key["marks"]:
                        key["marks"][name] = {"illustrates": m.get("illustrates", []), "contradicts": m.get("contradicts", [])}
                    else:
                        raise ValueError("A clip that is not in this video was marked.")
                E.check_key(key)
                save_dir.mkdir(parents=True, exist_ok=True)
                kp = E.key_path(save_dir, rater)
                tmp = kp.with_suffix(".tmp")
                tmp.write_text(json.dumps(key, indent=1), encoding="utf-8")
                tmp.replace(kp)
            except (ValueError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as e:
                return self._send(400, {"error": str(e)})
            return self._send(200, {"ok": True, "saved": kp.name})

    return H


def serve(job, rater, clips_dir=None, port=8321, save_dir=None, house=None, snap=None):
    """Start the page in a background thread; returns the server (call .shutdown() to stop)."""
    E.key_path(".", rater)   # checks the rater name
    snap = snap or E.snapshot(job, clips_dir)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(job, snap, rater, save_dir or job, house))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build an answer key for one finished video (local page, offline).")
    ap.add_argument("job")
    ap.add_argument("--rater", required=True, help="a short name for the person marking, e.g. A or B")
    ap.add_argument("--clips", help="the folder of her original clips (found from the job when left out)")
    ap.add_argument("--house", help="a name for the house (default: the job name); jobs of the same house share it")
    ap.add_argument("--port", type=int, default=8321)
    ap.add_argument("--save-dir")
    a = ap.parse_args(argv)
    try:
        srv = serve(a.job, a.rater, a.clips, a.port, a.save_dir, a.house)
    except ValueError as e:
        print(e)
        return 1
    print(f"Open http://127.0.0.1:{a.port}/ in a browser. Press Ctrl+C here when you are done (it saves as you go).")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
