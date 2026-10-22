"""Qwen (OpenRouter) engine tests. NO real network call: a mock OpenRouter server on 127.0.0.1 and stubs only.

Run: venv/Scripts/python tests/test_qwen.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAKE_KEY = "sk-or-FAKE-TEST-KEY-0123456789"
os.environ["OPENROUTER_API_KEY"] = FAKE_KEY          # only inside this test process
os.environ["ALEX_ALLOW_OTHER_AI"] = "1"            # the explicit authorization a hand-run comparison uses (never set on the website)

from video_tool import ai, assemble, ff, match, qwen, settings  # noqa: E402
from video_tool.config import CLAUDE_MODEL  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


# ---------- mock OpenRouter ----------
SCRIPT = []      # queue of (status, body-or-callable, delay) consumed per request; empty -> smart success
SEEN = []        # {"headers", "payload"} of every request
LOG = []         # everything the server printed


def smart_reply(payload, cost=0.0123, wrap=False):
    """A valid scoring reply built from the request: one row per 'Shot k, frame j' (or per video segment)."""
    parts = payload["messages"][0]["content"]
    text = " ".join(p.get("text", "") for p in parts if p["type"] == "text")
    m = int(re.search(r"exactly (\d+) integer", text).group(1))
    rows = []
    for k, j in re.findall(r"Shot (\d+), frame (\d+)", text):
        rows.append((int(k), int(j)))
    for k, n in re.findall(r"Shot (\d+) \((\d+) segments", text):
        rows += [(int(k), j) for j in range(1, int(n) + 1)]
    data = {"rows": [{"shot": k, "frame": j, "scores": [(k * 3 + j * 2 + i) % 11 for i in range(m)]} for k, j in rows]}
    body = json.dumps(data)
    if wrap:
        body = "Sure! Here are the scores:\n```json\n" + body + "\n```\nHope that helps."
    return {"choices": [{"message": {"role": "assistant", "content": body}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200, **({"cost": cost} if cost is not None else {})}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        LOG.append(fmt % args)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append({"headers": dict(self.headers), "payload": payload, "path": self.path})
        status, body, delay = SCRIPT.pop(0) if SCRIPT else (200, smart_reply, 0)
        if delay:
            time.sleep(delay)
        if callable(body):
            body = body(payload)
        raw = body if isinstance(body, str) else json.dumps(body)
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw.encode())))
            self.end_headers()
            self.wfile.write(raw.encode())
        except OSError:
            pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["OPENROUTER_BASE_URL"] = f"http://127.0.0.1:{server.server_address[1]}/api/v1"
qwen.BACKOFF = 0.01


def reset():
    SCRIPT.clear()
    SEEN.clear()
    LOG.clear()


def raises(fn):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        return e
    return None


PARTS = [{"type": "text", "text": "Shot 1, frame 1 (0.5s in):"},
         {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
         {"type": "text", "text": "exactly 2 integer scores. Shot 1, frame 1 (0.5s in):"}]
GOOD = {"rows": [{"shot": 1, "frame": 1, "scores": [3, 4]}]}


def ok_reply(content=None, cost=0.0123):
    return {"choices": [{"message": {"content": content if content is not None else json.dumps(GOOD)}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200, **({"cost": cost} if cost is not None else {})}}


# ---------- chat(): success, payload, headers ----------
reset()
SCRIPT.append((200, ok_reply(), 0))
parsed, usage = qwen.chat(PARTS, match.SCHEMA, 5)
check("chat: parsed JSON returned", parsed == GOOD, parsed)
check("chat: usage from usage.cost", usage == {"input_tokens": 1000, "output_tokens": 200, "cost_usd": 0.0123}, usage)
pl, hd = SEEN[0]["payload"], SEEN[0]["headers"]
check("payload: model id", pl["model"] == "qwen/qwen3.8-omni-flash" == ai.QWEN_MODEL)
check("payload: temperature 0", pl["temperature"] == 0)
check("payload: data_collection deny", pl["provider"]["data_collection"] == "deny")
check("payload: require_parameters", pl["provider"]["require_parameters"] is True)
check("payload: usage include", pl["usage"] == {"include": True})
rf = pl["response_format"]
check("payload: strict json_schema using match.SCHEMA", rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
      and rf["json_schema"]["schema"] == match.SCHEMA)
check("payload: one user message", len(pl["messages"]) == 1 and pl["messages"][0]["role"] == "user")
check("headers: Authorization/X-Title/Referer", hd.get("Authorization") == f"Bearer {FAKE_KEY}"
      and hd.get("X-Title") == "Alex Video Maker" and hd.get("HTTP-Referer") == "http://localhost", hd)
check("path is /api/v1/chat/completions", SEEN[0]["path"] == "/api/v1/chat/completions", SEEN[0]["path"])

# cost fallback from tokens x prices
reset()
SCRIPT.append((200, ok_reply(cost=None), 0))
_, usage = qwen.chat(PARTS, match.SCHEMA, 5)
check("cost: fallback = tokens x price constants", abs(usage["cost_usd"] - (1000 * 0.15 + 200 * 0.47) / 1e6) < 1e-9, usage)

# ---------- ladder: 400 about response_format -> retry without it ----------
reset()
SCRIPT.append((400, {"error": {"message": "Provider does not support response_format json_schema"}}, 0))
SCRIPT.append((200, ok_reply("Here you go: " + json.dumps(GOOD) + " thanks!"), 0))
parsed, usage = qwen.chat(PARTS, match.SCHEMA, 5)
check("ladder: second request made", len(SEEN) == 2, len(SEEN))
check("ladder: retry has no response_format", "response_format" not in SEEN[1]["payload"])
check("ladder: data_collection deny is NEVER dropped", all(s["payload"]["provider"].get("data_collection") == "deny" for s in SEEN))
check("ladder: JSON extracted from prose", parsed == GOOD, parsed)
# Real Qwen reply seen on the first live run: a bare list of rows with no {"rows": ...} wrapper
ROWS3 = [{"shot": 1, "frame": 1, "scores": [3, 4]}, {"shot": 1, "frame": 2, "scores": [5, 6]}, {"shot": 2, "frame": 1, "scores": [7, 8]}]
for label, content in (("bare list", json.dumps(ROWS3)),
                       ("list in a code fence", "```json\n" + json.dumps(ROWS3) + "\n```"),
                       ("list after prose", "Here are the scores: " + json.dumps(ROWS3) + " Done."),
                       ("rows object in a code fence", "```json\n" + json.dumps({"rows": ROWS3}) + "\n```")):
    reset()
    SCRIPT.append((200, ok_reply(content), 0))
    parsed, _ = qwen.chat(PARTS, match.SCHEMA, 5)
    check(f"reply shape: {label} gives all {len(ROWS3)} rows", parsed == {"rows": ROWS3}, parsed)
reset()
SCRIPT.append((200, ok_reply(json.dumps(ROWS3[0])), 0))
parsed, _ = qwen.chat(PARTS, match.SCHEMA, 5)
check("reply shape: a lone row object is not mistaken for the answer", "rows" not in parsed, parsed)
reset()
SCRIPT.append((200, ok_reply(json.dumps([])), 0))
e = raises(lambda: qwen.chat(PARTS, match.SCHEMA, 5))
check("reply shape: an empty list is an error, not a plan", isinstance(e, qwen.QwenError), e)

reset()
SCRIPT.append((404, "No endpoints found that support the requested parameter", 0))
SCRIPT.append((200, ok_reply(), 0))
qwen.chat(PARTS, match.SCHEMA, 5)
check("ladder: 404 about a parameter also retries once", len(SEEN) == 2)
reset()
SCRIPT += [(400, "unsupported parameter response_format", 0)] * 2
e = raises(lambda: qwen.chat(PARTS, match.SCHEMA, 5))
check("ladder: only one drop, then the error surfaces", isinstance(e, qwen.QwenError) and e.status_code == 400 and len(SEEN) == 2, e)
reset()
SCRIPT.append((400, "bad request: messages malformed", 0))
e = raises(lambda: qwen.chat(PARTS, match.SCHEMA, 5))
check("ladder: unrelated 400 does not retry", isinstance(e, qwen.QwenError) and len(SEEN) == 1)

# ---------- bad answers ----------
reset()
SCRIPT.append((200, ok_reply("this is not json at all"), 0))
e = raises(lambda: qwen.chat(PARTS, match.SCHEMA, 5))
check("malformed JSON -> QwenError", isinstance(e, qwen.QwenError) and ai.is_ai_error(e), e)
reset()
SCRIPT.append((200, {"choices": []}, 0))
check("empty choices -> QwenError", isinstance(raises(lambda: qwen.chat(PARTS, match.SCHEMA, 5)), qwen.QwenError))

# ---------- error classification ----------
for status, kind in ((401, "key"), (403, "key"), (402, "credit"), (429, "rate"), (500, "other")):
    reset()
    SCRIPT += [(status, {"error": {"message": f"nope {FAKE_KEY}"}}, 0)] * 3
    e = raises(lambda: qwen.chat(PARTS, match.SCHEMA, 5))
    check(f"{status}: QwenError with status_code", isinstance(e, qwen.QwenError) and e.status_code == status, repr(e))
    check(f"{status}: is_ai_error and explain kind {kind}", ai.is_ai_error(e) and ai.explain(e, "match")["kind"] == kind,
          ai.explain(e, "match") if e else "")
    check(f"{status}: key never in exception text", FAKE_KEY not in str(e) and FAKE_KEY not in repr(e))
reset()
SCRIPT.append((401, "bad key", 0))
raises(lambda: qwen.chat(PARTS, match.SCHEMA, 5))
check("401 is not retried", len(SEEN) == 1)
reset()
SCRIPT.append((402, "no credit", 0))
raises(lambda: qwen.chat(PARTS, match.SCHEMA, 5))
check("402 is not retried", len(SEEN) == 1)
check("explain text is the plain AI helper line", "AI helper" in ai.explain(qwen.QwenError("x", 402), "match")["message"]
      and "credit" in ai.explain(qwen.QwenError("x", 402), "match")["message"])

# ---------- retries for transient errors ----------
reset()
SCRIPT += [(429, "slow down", 0), (200, ok_reply(), 0)]
parsed, _ = qwen.chat(PARTS, match.SCHEMA, 5)
check("429 then success: retried", parsed == GOOD and len(SEEN) == 2)
reset()
SCRIPT += [(503, "busy", 0), (502, "busy", 0), (200, ok_reply(), 0)]
parsed, _ = qwen.chat(PARTS, match.SCHEMA, 5)
check("5xx twice then success: 3 tries", parsed == GOOD and len(SEEN) == 3)
reset()
SCRIPT += [(429, "busy", 0)] * 5
e = raises(lambda: qwen.chat(PARTS, match.SCHEMA, 5))
check("429 forever: gives up after 1 + 2 retries", isinstance(e, qwen.QwenError) and e.status_code == 429 and len(SEEN) == 3, len(SEEN))

# ---------- hung reply ----------
reset()
SCRIPT += [(200, ok_reply(), 2.5)] * 3
t0 = time.time()
e = raises(lambda: qwen.chat(PARTS, match.SCHEMA, 0.4))
check("hung reply: timeout -> QwenError (retried, then stops)", isinstance(e, qwen.QwenError) and ai.is_ai_error(e) and 1.0 < time.time() - t0 < 6, (e, time.time() - t0))
check("hung reply: explain kind other", ai.explain(e, "match")["kind"] == "other")

# ---------- no key / key never leaks ----------
os.environ["OPENROUTER_API_KEY"] = ""
check("has_openrouter_key false when empty", ai.has_openrouter_key() is False)
os.environ["OPENROUTER_API_KEY"] = "your_key_here"
check("has_openrouter_key false for the placeholder", ai.has_openrouter_key() is False and ai.openrouter_key() == "")
reset()
e = raises(lambda: qwen.chat(PARTS, match.SCHEMA, 5))
check("no key: chat refuses and sends nothing", isinstance(e, qwen.QwenError) and not SEEN)
os.environ["OPENROUTER_API_KEY"] = FAKE_KEY
check("has_openrouter_key true with a key", ai.has_openrouter_key() is True and ai.openrouter_key() == FAKE_KEY)

# ---------- settings ----------
s = settings.configure({})
check("settings: default engine claude", s["match_engine"] == "claude" and settings.get("match_engine") == "claude")
for v in ("qwen", "qwen_video", "claude"):
    check(f"settings: {v} kept", settings.configure({"match_engine": v})["match_engine"] == v)
for v in ("gpt", "", None, 3, "QWEN"):
    check(f"settings: {v!r} reset to claude", settings.configure({"match_engine": v})["match_engine"] == "claude")
check("settings: ENGINES constant", settings.ENGINES == ("claude", "qwen", "qwen_video"))
check("settings: clamp on old prefs without the key", settings.clamp({**{k: v for k, v in settings.DEFAULTS.items() if k != "match_engine"}})["match_engine"] == "claude")
check("settings: not remembered as a pref", "match_engine" not in settings.REMEMBER_KEYS)
settings.configure({})

# ---------- real sample clips ----------
SAMPLE = ROOT / "samples" / "recipe"
clips = sorted(SAMPLE.glob("0*.mp4"))
HAVE_SAMPLES = len(clips) >= 3
if not HAVE_SAMPLES:
    print("SKIPPED the real-clip checks: samples/recipe is not in this copy (her footage is not in the repository)")
    server.shutdown()
    print()
    print('ALL PASS (real-clip checks skipped)' if not FAILS else 'FAILED: ' + ', '.join(FAILS))
    sys.exit(1 if FAILS else 0)
checked = []
for c in clips:
    dur, hdr = assemble.check_clip(c)
    checked.append((c, dur, hdr, 0.0))
per_clip = [match.frame_times(d) for _, d, _, _ in checked]
tmp = Path(tempfile.mkdtemp(prefix="qwen-test-"))


def probe(path):
    info = ff.probe(path)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    return info, v


# video builder
notes = {}
t0 = time.time()
vids = match.extract_videos(checked, per_clip, tmp / "videos", notes)
print(f"     (videos built in {time.time() - t0:.1f}s, notes {notes})")
check("videos: one NN.mp4 per shot", [p.name for p in vids] == ["01.mp4", "02.mp4", "03.mp4"] and all(p.exists() for p in vids))
for i, p in enumerate(vids):
    info, v = probe(p)
    want = sum(max(0.2, min(notes["video_segment_s"], checked[i][1] - t)) for t in per_clip[i])
    check(f"video {i + 1}: h264 yuv420p 288x512 no audio", v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p"
          and (v["width"], v["height"]) == (288, 512) and not ff.has_stream(info, "audio"), v)
    check(f"video {i + 1}: length matches the segments ({want:.2f}s)", abs(info["duration"] - want) < 0.5, info["duration"])
    nsegs = len(per_clip[i])
    check(f"video {i + 1}: never longer than segments allow (end of clip)",
          info["duration"] <= nsegs * (notes["video_segment_s"] + 1 / notes["video_fps"]) + 0.2, info["duration"])
    fps = eval(v["avg_frame_rate"])
    check(f"video {i + 1}: fps {notes['video_fps']}", abs(fps - notes["video_fps"]) < 0.5, fps)
total = sum(p.stat().st_size for p in vids)
check("videos: total bytes under 8 MB", total <= 8_000_000 and notes["video_bytes"] == total, total)
check("videos: faststart (moov before mdat)", all((lambda b: b.find(b"moov") < b.find(b"mdat"))(p.read_bytes()[:200000]) for p in vids))

# alignment: the first picture of segment j looks like the still frame j (and unlike the other segments' stills)
try:
    from PIL import Image
    import numpy as np

    frames = match.extract_frames(checked, per_clip, tmp / "frames")

    def grab(video, at):
        out = tmp / "probe.png"
        ff.run(["-ss", f"{at:.3f}", "-i", video, "-frames:v", "1", out])
        return np.asarray(Image.open(out).convert("L").resize((36, 64)), dtype=float)

    def still(path):
        return np.asarray(Image.open(path).convert("L").resize((36, 64)), dtype=float)

    import math

    good_align, seg, fps = True, notes["video_segment_s"], notes["video_fps"]
    misses = []
    for i in (0, 1):
        offs, acc = [], 0.0
        for t in per_clip[i]:
            offs.append(acc)
            acc += math.ceil(max(0.2, min(seg, checked[i][1] - t)) * fps) / fps     # whole frames per segment
        for j in range(len(per_clip[i])):
            g = grab(vids[i], offs[j])   # first picture of the segment
            d = [np.abs(g - still(frames[i][k])).mean() for k in range(len(per_clip[i]))]
            if int(np.argmin(d)) != j:
                misses.append((i, j, [round(x, 1) for x in d]))
    good_align = not misses
    if misses:
        print("     misses:", misses)
    check("videos: segment j picture matches still frame j", good_align)
except ImportError:
    print("SKIP alignment check (no PIL)")

# cache reuse
mt = [p.stat().st_mtime_ns for p in vids]
notes2 = {}
vids2 = match.extract_videos(checked, per_clip, tmp / "videos", notes2)
check("videos: cached on disk (not rebuilt)", [p.stat().st_mtime_ns for p in vids2] == mt and notes2 == notes)

# size cap ladder
old_cap = match.VIDEO_CAP
match.VIDEO_CAP = 250_000
n3 = {}
v3 = match.extract_videos(checked, per_clip, tmp / "videos_small", n3)
check("cap: small cap honoured by lowering quality", sum(p.stat().st_size for p in v3) <= 250_000 and n3["video_shrunk"] is True, n3)
match.VIDEO_CAP = 1000
e = raises(lambda: match.extract_videos(checked, per_clip, tmp / "videos_tiny", {}))
check("cap: impossible cap raises (plan then falls back)", isinstance(e, ValueError), e)
match.VIDEO_CAP = old_cap

# ---------- request building ----------
phr = [{"text": "This is my go-to smoothie recipe.", "start": 0.0, "end": 3.0},
       {"text": "I start with frozen bananas.", "start": 3.0, "end": 6.0}]
cl = match.build_parts(frames, per_clip, phr)
op = match.build_parts(frames, per_clip, phr, "openrouter")
check("parts: same labels and prompt for both engines (apples to apples)",
      [p["text"] for p in cl if p["type"] == "text"] == [p["text"] for p in op if p["type"] == "text"])
check("parts: first label format", cl[0]["text"] == f"Shot 1, frame 1 ({per_clip[0][0]:.1f}s in):")
check("parts: prompt is match.PROMPT", cl[-1]["text"] == match.PROMPT.format(n=3, phrases="1. This is my go-to smoothie recipe.\n2. I start with frozen bananas.", m=2))
check("parts: claude uses base64 image blocks", cl[1]["type"] == "image" and cl[1]["source"]["media_type"] == "image/jpeg")
check("parts: openrouter uses image_url data URLs", op[1]["type"] == "image_url" and op[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
check("parts: same picture bytes", cl[1]["source"]["data"] == op[1]["image_url"]["url"].split(",", 1)[1])
vp = match.build_video_parts(vids, per_clip, phr)
check("video parts: one video_url per shot", sum(p["type"] == "video_url" for p in vp) == 3
      and all(p["video_url"]["url"].startswith("data:video/mp4;base64,") for p in vp if p["type"] == "video_url"))
check("video parts: segment prompt", "consecutive segments" in vp[-1]["text"] and "exactly 2 integer" in vp[-1]["text"])

# ---------- ask_model through the mock ----------
reset()
scores, usage = match.ask_model("qwen", frames, per_clip, phr)
check("ask_model qwen: full score grid", [len(r) for r in scores] == [len(t) for t in per_clip]
      and all(len(x) == 2 for r in scores for x in r), scores)
check("ask_model qwen: cost from usage.cost", usage["cost_usd"] == 0.0123)
types = [p["type"] for p in SEEN[0]["payload"]["messages"][0]["content"]]
check("payload (stills): image_url parts, no video_url", types.count("image_url") == sum(len(t) for t in per_clip) and "video_url" not in types)
reset()
scores, usage = match.ask_model("qwen_video", None, per_clip, phr, vids)
types = [p["type"] for p in SEEN[0]["payload"]["messages"][0]["content"]]
check("payload (video): one video_url per shot, no image_url", types.count("video_url") == 3 and "image_url" not in types)
check("ask_model qwen_video: grid aligned with frame times", [len(r) for r in scores] == [len(t) for t in per_clip])
check("ask_model qwen_video: needs videos", isinstance(raises(lambda: match.ask_model("qwen_video", None, per_clip, phr)), qwen.QwenError))
reset()
SCRIPT.append((200, lambda p: {**smart_reply(p), "choices": [{"message": {"content": json.dumps(
    {"rows": [{"shot": 1, "frame": 1, "scores": [1, 2]}]})}}]}, 0))
e = raises(lambda: match.ask_model("qwen", frames, per_clip, phr))
check("missing rows -> QwenError (incomplete)", isinstance(e, qwen.QwenError) and "incomplete" in str(e), e)
reset()
SCRIPT.append((200, lambda p: {**smart_reply(p), "choices": [{"message": {"content": json.dumps({"nope": 1})}}]}, 0))
e = raises(lambda: match.ask_model("qwen", frames, per_clip, phr))
check("wrong shape -> QwenError", isinstance(e, qwen.QwenError), e)
reset()
SCRIPT.append((200, lambda p: smart_reply(p, wrap=True), 0))
scores, _ = match.ask_model("qwen", frames, per_clip, phr)
check("ladder-style prose reply still scores", len(scores) == 3)

# double-check passes average for the qwen engines
reset()
replies = iter([lambda p: ok_reply(json.dumps({"rows": [{"shot": k, "frame": j, "scores": [2, 8]} for k, j in
                                                      [(k + 1, j + 1) for k in range(3) for j in range(len(per_clip[k]))]]})),
                lambda p: ok_reply(json.dumps({"rows": [{"shot": k, "frame": j, "scores": [4, 10]} for k, j in
                                                      [(k + 1, j + 1) for k in range(3) for j in range(len(per_clip[k]))]]}))])
SCRIPT += [(200, next(replies), 0), (200, next(replies), 0)]
sc, us, done = match.ask_claude_times(2, frames, per_clip, phr, "qwen")
vals = {tuple(x) for r in sc for x in r}
check("passes=2 qwen: averaged (one request per pass)", done == 2 and len(SEEN) == 2, (vals, done))
check("passes=2 qwen: scores are the mean of the two replies", vals == {(3.0, 9.0)}, vals)
check("passes=2 qwen: usage summed", abs(us["cost_usd"] - 0.0246) < 1e-6, us)
reset()
sc, us, done = match.ask_claude_times(1, frames, per_clip, phr, "qwen")
check("passes=1 qwen works", done == 1 and len(SEEN) == 1)
reset()
SCRIPT += [(400, "bad", 0), (400, "bad", 0)]
e = raises(lambda: match.ask_claude_times(2, frames, per_clip, phr, "qwen"))
check("passes=2 qwen: all failing raises", isinstance(e, qwen.QwenError))


# ---------- ask_claude unchanged (stub client) ----------
class StubMessages:
    def __init__(self):
        self.kwargs = None

    def create(self, **kw):
        self.kwargs = kw
        m = int(re.search(r"exactly (\d+) integer", kw["messages"][0]["content"][-1]["text"]).group(1))
        rows = [{"shot": k + 1, "frame": j + 1, "scores": [7, 3] + [5] * (m - 2)} for k in range(3) for j in range(len(per_clip[k]))]
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps({"rows": rows}))],
                               usage=SimpleNamespace(input_tokens=2000, output_tokens=300))


stub = SimpleNamespace(messages=StubMessages())
real_client, real_has = ai.client, ai.has_key
ai.client = lambda **kw: stub
scores, usage = match.ask_claude(frames, per_clip, phr)
kw = stub.messages.kwargs
check("ask_claude: model, max_tokens, effort, schema unchanged", kw["model"] == CLAUDE_MODEL and kw["max_tokens"] == 16000
      and kw["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": match.SCHEMA}})
content = kw["messages"][0]["content"]
check("ask_claude: content is the old shape", content[0] == {"type": "text", "text": f"Shot 1, frame 1 ({per_clip[0][0]:.1f}s in):"}
      and content[1]["type"] == "image" and content[1]["source"]["type"] == "base64" and content[-1]["type"] == "text"
      and len(content) == 2 * sum(len(t) for t in per_clip) + 1)
check("ask_claude: scores and usage", scores[0][0] == [7, 3] and usage == {"input_tokens": 2000, "output_tokens": 300,
      "cost_usd": ai.cost(CLAUDE_MODEL, SimpleNamespace(input_tokens=2000, output_tokens=300))}, usage)
check("ask_model('claude') dispatches to ask_claude", match.ask_model("claude", frames, per_clip, phr)[1]["input_tokens"] == 2000)
sc, us, done = match.ask_claude_times(2, frames, per_clip, phr)
check("ask_claude_times claude default signature still works", done == 2 and sc[0][0] == [7.0, 3.0])
ai.client = real_client

# ---------- cache key ----------
phr_texts = [{"text": "a b c"}, {"text": "d e"}]
k_claude = match.cache_key(checked, per_clip, phr_texts)
check("cache key: claude explicit == default", k_claude == match.cache_key(checked, per_clip, phr_texts, "claude"))
check("cache key: qwen differs", match.cache_key(checked, per_clip, phr_texts, "qwen") != k_claude)
check("cache key: qwen_video differs from qwen", match.cache_key(checked, per_clip, phr_texts, "qwen_video")
      != match.cache_key(checked, per_clip, phr_texts, "qwen"))
import hashlib  # noqa: E402

h = hashlib.sha1(f"{match.VERSION}|{CLAUDE_MODEL}".encode())
for (clip, _, _, skip), ts in zip(checked, per_clip):
    h.update(f"{Path(clip).stat().st_size}|{skip:.2f}|{ts}".encode())
h.update("|".join(p["text"].lower() for p in phr_texts).encode())
check("cache key: claude key is byte-for-byte the old formula", h.hexdigest() == k_claude)
saved = ROOT / "out" / "demo-recipe" / "match" / "scores.json"
if saved.exists() and (ROOT / "out" / "demo-recipe" / "words.json").exists():
    words = json.loads((ROOT / "out" / "demo-recipe" / "words.json").read_text(encoding="utf-8"))
    rep = json.loads((ROOT / "out" / "demo-recipe" / "report.json").read_text(encoding="utf-8"))
    length = rep["voice"]
    pc = [match.frame_times(d) for _, d, _, _ in checked]
    while sum(len(t) for t in pc) > match.MAX_FRAMES and max(len(t) for t in pc) > 2:
        pc = [t[:-1] if len(t) > 2 else t for t in pc]
    sk = json.loads(saved.read_text(encoding="utf-8")).get("key")
    # the pipeline trims the voice; the saved key is only comparable when this job's phrases match, so report either way
    kk = match.cache_key(checked, pc, match.phrases(words, length))
    print(f"     (demo-recipe saved Claude key {'matches' if kk == sk else 'differs (words.json is untrimmed)'}: informational)")

# ---------- plan() ----------
words = json.loads((ROOT / "out" / "demo-recipe" / "words.json").read_text(encoding="utf-8")) \
    if (ROOT / "out" / "demo-recipe" / "words.json").exists() else None
if words:
    length = words[-1]["end"] + 0.3
    even = assemble.plan_slots([d for _, d, _, _ in checked], length, words)
    settings.configure({"match_engine": "qwen"})
    os.environ["OPENROUTER_API_KEY"] = ""
    reset()
    job = tmp / "job_nokey"
    job.mkdir()
    slots, info, warns = match.plan(checked, words, length, even, job)
    check("plan qwen, no key: even split + reason, engine recorded", slots is None and info["used"] is False
          and info["reason"] == "no OpenRouter key" and info["engine"] == "qwen" and info["model"] == ai.QWEN_MODEL and warns == [], info)
    check("plan qwen, no key: nothing sent, never fell back to Claude", not SEEN and not (job / "match" / "frames").exists())
    called = []
    ai.client = lambda **kw: called.append(1)
    slots, info, _ = match.plan(checked, words, length, even, job)
    check("plan qwen, no key: Claude client never touched", not called)
    ai.client = real_client
    os.environ["OPENROUTER_API_KEY"] = FAKE_KEY

    for engine in ("qwen", "qwen_video"):
        settings.configure({"match_engine": engine})
        reset()
        job = tmp / f"job_{engine}"
        job.mkdir()
        slots, info, warns = match.plan(checked, words, length, even, job)
        check(f"plan {engine}: one request, engine+model in info", len(SEEN) == 1 and info["engine"] == engine
              and info["model"] == ai.QWEN_MODEL, info)
        check(f"plan {engine}: scored (cost/usage/seconds/passes in info)", info.get("cost_usd") == 0.0123
              and info.get("input_tokens") == 1000 and info.get("passes") == 1 and "seconds" in info or "reason" in info, info)
        check(f"plan {engine}: no AI error recorded", "ai_error" not in info and "Error" not in info.get("reason", ""), info)
        sj = json.loads((job / "match" / "scores.json").read_text(encoding="utf-8"))
        check(f"plan {engine}: scores.json records engine and fits the frames", sj["engine"] == engine
              and [len(r) for r in sj["scores"]] == [len(t) for t in sj["frame_times"]], sj.get("engine"))
        if engine == "qwen_video":
            check("plan qwen_video: video size recorded in info", info.get("video_bytes", 0) > 0 and info["video_bytes"] <= 8_000_000, info)
            check("plan qwen_video: videos sent, not stills", any(p["type"] == "video_url" for p in SEEN[0]["payload"]["messages"][0]["content"]))
        else:
            check("plan qwen: stills sent", any(p["type"] == "image_url" for p in SEEN[0]["payload"]["messages"][0]["content"]))
        check(f"plan {engine}: plan or a clean even-split reason (never raises)", (slots is not None and abs(sum(slots) - length) < 0.1)
              or (slots is None and info.get("reason")), (slots, info))
        # cached rerun: same job reused, no second request
        reset()
        job2 = tmp / f"job_{engine}_2"
        job2.mkdir()
        slots2, info2, _ = match.plan(checked, words, length, even, job2, reuse=job)
        check(f"plan {engine}: reuse hits its own cache, no request", not SEEN and info2.get("cached") is True, info2)

    # failures become the usual fallback with the plain line
    settings.configure({"match_engine": "qwen"})
    for status, kind in ((401, "key"), (402, "credit"), (429, "rate")):
        reset()
        SCRIPT += [(status, "x", 0)] * 3
        job = tmp / f"job_err{status}"
        job.mkdir()
        slots, info, _ = match.plan(checked, words, length, even, job)
        check(f"plan qwen {status}: even split + ai_error kind {kind}", slots is None and info.get("ai_error", {}).get("kind") == kind
              and "AI helper" in info["ai_error"]["message"] and info["engine"] == "qwen", info)
    reset()
    SCRIPT.append((200, ok_reply("garbage"), 0))
    job = tmp / "job_garbage"
    job.mkdir()
    slots, info, _ = match.plan(checked, words, length, even, job)
    check("plan qwen malformed: even split, never raises", slots is None and "reason" in info)

    # claude path in plan: key check + info, no network (stubbed client)
    settings.configure({"match_engine": "claude"})
    ai.has_key = lambda: False
    slots, info, _ = match.plan(checked, words, length, even, tmp)
    check("plan claude, no key: unchanged reason", info["reason"] == "no API key" and info["engine"] == "claude"
          and info["model"] == CLAUDE_MODEL and slots is None)
    ai.has_key = lambda: True
    ai.client = lambda **kw: stub
    stub.messages.create = StubMessages().create
    job = tmp / "job_claude"
    job.mkdir()
    slots, info, _ = match.plan(checked, words, length, even, job)
    sj = json.loads((job / "match" / "scores.json").read_text(encoding="utf-8"))
    check("plan claude (stub): engine claude, key == old formula", sj["engine"] == "claude" and info["engine"] == "claude"
          and sj["key"] == match.cache_key(checked, sj["frame_times"], match.phrases(words, length)), info)
    ai.client, ai.has_key = real_client, real_has
else:
    print("SKIP plan tests (out/demo-recipe/words.json missing)")

# ---------- nothing leaked ----------
settings.configure({})
check("key never in the mock server's log", not any(FAKE_KEY in l for l in LOG))
leak = [p for p in tmp.rglob("*") if p.is_file() and p.suffix in (".json", ".txt") and FAKE_KEY in p.read_text(encoding="utf-8", errors="ignore")]
check("key never in any file the plans wrote", not leak, leak)
check("no real host contacted: base url is 127.0.0.1", qwen.base_url().startswith("http://127.0.0.1:"))

server.shutdown()
print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
