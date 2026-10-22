"""Claude is the only outside AI provider approved for Alex's content. This proves the website cannot select another one.

Run: venv\\Scripts\\python tests\\test_provider_policy.py
Everything here is a fake: a made-up OpenRouter key sits in the environment ON PURPOSE (so the path that would use it is live),
and every way out of the computer is replaced by a spy that fails the test if it is touched: sockets, httpx, the Qwen call and
the Claude client. Nothing is sent anywhere and no footage is used. The experimental providers stay available only to a
separate, explicit run (ALEX_ALLOW_OTHER_AI=1, never on the website)."""
import json
import os
import socket
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = Path(tempfile.mkdtemp(prefix="alex-provider-"))
os.environ.update(ALEX_DATA_DIR=str(DATA), ALEX_HOSTED="1", ADMIN_USER="tester", ADMIN_PASSWORD="admin-pass-123",
                  ANTHROPIC_API_KEY="", APP_PASSWORD="", ALEX_AUTO_SLIM="0",
                  OPENROUTER_API_KEY="sk-or-FAKE-TEST-KEY-0123456789", OPENROUTER_BASE_URL="http://127.0.0.1:9/api/v1")
os.environ.pop("ALEX_ALLOW_OTHER_AI", None)

FAILS, ATTEMPTS = [], []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {detail}"))
    if not cond:
        FAILS.append(name)


_real_connect, _real_connect_ex = socket.socket.connect, socket.socket.connect_ex
LOCAL = ("127.0.0.1", "::1", "localhost")


def _guard(real):
    def guarded(self, addr, *a):
        # the test client's own loopback plumbing is fine; anything else, and port 9 (where the fake OpenRouter would be), is an attempt
        if not (isinstance(addr, tuple) and addr[0] in LOCAL and addr[1] != 9):
            ATTEMPTS.append(addr)
            raise OSError("outbound connection blocked by the test")
        return real(self, addr, *a)
    return guarded


socket.socket.connect = _guard(_real_connect)
socket.socket.connect_ex = _guard(_real_connect_ex)


def refuse(*a, **k):
    ATTEMPTS.append(("httpx.post", a[:1]))
    raise OSError("outbound call blocked by the test")


import httpx   # noqa: E402
httpx.post = refuse

from video_tool import ai, match, qwen, settings   # noqa: E402
from web import app as W, auth, interpret as I   # noqa: E402

CALLS = []
qwen_chat_real = qwen.chat
ai.client = lambda *a, **k: CALLS.append("claude client") or None
check("setup: a (fake) OpenRouter key is present, so the experimental path would be live if it were reachable",
      ai.has_openrouter_key() is True)

# ---------- 1. the website's input boundary ----------
print("\n--- the website can never choose the provider ---")
for v in ("qwen", "qwen_video", "clip", "claude", "anything"):
    check(f"clean_values drops match_engine={v!r}", "match_engine" not in I.clean_values({"match_engine": v, "caption_size": 90}))
check("...and keeps the other settings in the same request", I.clean_values({"match_engine": "qwen", "caption_size": 90}) == {"caption_size": 90})

# a typed note read by a (fake) Claude that was talked into naming a provider
fake_msg = SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={"match_engine": "qwen_video", "caption_size": 90})],
                           usage=SimpleNamespace(input_tokens=1, output_tokens=1))
I._client = lambda: SimpleNamespace(messages=SimpleNamespace(create=lambda **k: fake_msg))
changes, unsupported, how, cost = I.interpret_full("use the other AI for the pictures and make the captions bigger", dict(settings.DEFAULTS))
check("a typed note can not select a provider even if the reader returns one", "match_engine" not in changes and changes.get("caption_size") == 90, changes)
I._client = lambda: None

# ---------- 2. the feedback / remake route ----------
print("\n--- the feedback and remake route ---")
from fastapi.testclient import TestClient   # noqa: E402
STARTS = []
W._start = lambda job_id, folder, user, extra=(): STARTS.append(list(extra))
auth.set_user("alex", "alex-pass-1234")
alex = TestClient(W.app, base_url="http://testserver")
assert alex.post("/api/login", json={"username": "alex", "password": "alex-pass-1234"}).status_code == 200
src = DATA / "clips"
src.mkdir()
(W.OUT / "web-p-1").mkdir(parents=True)
(W.OUT / "web-p-1" / "report.json").write_text(json.dumps({"job_id": "web-p-1", "mode": "voiceover", "settings": dict(settings.DEFAULTS)}))
(W.OUT / "web-p-1.owner").write_text("alex")
(W.OUT / "web-p-1.source").write_text(str(src))


def remake(changes, comment=""):
    STARTS.clear()
    r = alex.post("/api/jobs/web-p-1/feedback", json={"changes": changes, "comment": comment, "remake": True})
    over = {}
    if STARTS and "--settings" in STARTS[0]:
        over = json.loads(Path(STARTS[0][STARTS[0].index("--settings") + 1]).read_text())
    return r, over


for v in ("qwen", "qwen_video"):
    r, over = remake({"match_engine": v, "caption_size": 90})
    check(f"a hand-made remake asking for {v!r} is made without it (and with her real change)",
          r.status_code == 200 and over.get("match_engine") in (None, "claude") and over.get("caption_size") == 90, (r.text, over))
r, over = remake({"match_engine": "qwen_video"})
check("a request that ONLY names a provider changes nothing and starts no remake", r.status_code == 200 and not STARTS, (r.text, STARTS))

# ---------- 3. the settings boundary ----------
print("\n--- settings: even a saved or hand-edited value is turned back to Claude ---")
for v in ("qwen", "qwen_video"):
    check(f"settings.configure({v!r}) on the website process is Claude",
          settings.configure({"match_engine": v})["match_engine"] == "claude")
os.environ["ALEX_ALLOW_OTHER_AI"] = "1"
check("ALEX_ALLOW_OTHER_AI=1 does NOT unlock it while ALEX_HOSTED=1 (the website can never be authorized)",
      settings.configure({"match_engine": "qwen_video"})["match_engine"] == "claude" and settings.other_ai_authorized() is False)
del os.environ["ALEX_HOSTED"]
check("a separate hand-run process that sets ALEX_ALLOW_OTHER_AI=1 can use it (the explicit mechanism still works)",
      settings.other_ai_authorized() is True and settings.configure({"match_engine": "qwen_video"})["match_engine"] == "qwen_video")
check("the on-laptop matcher is not an outside provider and is unaffected", settings.configure({"match_engine": "clip"})["match_engine"] == "clip")
del os.environ["ALEX_ALLOW_OTHER_AI"]
check("without the variable the explicit mechanism is closed again", settings.configure({"match_engine": "qwen"})["match_engine"] == "claude")
os.environ["ALEX_HOSTED"] = "1"

# ---------- 4. the network boundary ----------
print("\n--- the last line of defence: the call itself refuses ---")
try:
    qwen_chat_real([{"type": "text", "text": "hello"}], {"type": "object"})
    check("qwen.chat refuses when not authorized", False, "it did not raise")
except qwen.QwenError as e:
    check("qwen.chat refuses when not authorized (before any network, with a key present)", e.status_code == 403, str(e))
settings.configure({"match_engine": "qwen_video"})   # what a hand-edited file would ask for
check("match.plan on the website process uses Claude and, with no Claude key, makes no call at all",
      match.plan([], [], 10.0, True, DATA)[1].get("engine") == "claude")

check("nothing was sent: no socket connections attempted", not ATTEMPTS, ATTEMPTS)
check("nothing was sent: the Claude client was never even created", not CALLS, CALLS)

print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED'}")
sys.exit(1 if FAILS else 0)
