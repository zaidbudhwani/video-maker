"""On-laptop clip matcher (match_engine = "clip", video_tool/localmatch.py). No network: every socket connection is
refused for the whole run, HF_HUB_OFFLINE is set and both AI keys are blank.

Run: venv/Scripts/python tests/test_localmatch.py
Prints SKIP when the model files (assets/models/clip) are missing; the tour check is skipped without samples/tour.
"""
import json
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["OPENROUTER_API_KEY"] = ""
ATTEMPTS = []


def _refuse(self, *a, **k):
    ATTEMPTS.append(a)
    raise OSError("network is blocked in this test")


socket.socket.connect = _refuse
socket.socket.connect_ex = _refuse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from video_tool import ai, assemble, ff, localmatch, match, qwen, settings  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


def never(*a, **k):
    raise AssertionError("an outside AI was called")


ai.client = never
qwen.chat = never
tmp = Path(tempfile.mkdtemp(prefix="localmatch-test-"))
try:
    # ---------- settings: accepted, never the default, not remembered ----------
    check("settings: default engine is still claude", settings.DEFAULTS["match_engine"] == "claude")
    check("settings: clip accepted", settings.configure({"match_engine": "clip"})["match_engine"] == "clip")
    check("settings: unknown engine still reset to claude", settings.configure({"match_engine": "clipx"})["match_engine"] == "claude")
    check("settings: other engines unchanged", settings.ENGINES == ("claude", "qwen", "qwen_video"))
    check("settings: not remembered as a pref", "match_engine" not in settings.REMEMBER_KEYS)
    web = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in (ROOT / "web").rglob("*.html"))
    check("settings: not offered on any page", 'value="clip"' not in web and "match_engine" not in web and "localmatch" not in web)

    # ---------- missing model files: plain fallback, never a crash ----------
    real_dir = localmatch.MODEL_DIR
    localmatch.MODEL_DIR = tmp / "no-models"
    settings.configure({"match_engine": "clip"})
    fake_checked = [(str(tmp / "nothing.mp4"), 6.0, False, 0.0)] * 2
    words0 = [{"word": "Hello.", "start": 0.0, "end": 1.0}]
    try:
        slots, info, warns = match.plan(fake_checked, words0, 12.0, [6.0, 6.0], tmp)
        check("missing models: even split with a plain reason", slots is None and info["used"] is False and info["engine"] == "clip"
              and "missing" in info["reason"] and warns == [] and "ai_error" not in info, info)
    except Exception as e:
        check("missing models: never raises", False, repr(e))
    try:
        localmatch.ask([[tmp / "x.jpg"]], [[0.5]], [{"text": "a"}], model_dir=tmp / "no-models")
        check("missing models: ask raises its own error", False)
    except localmatch.LocalMatchError:
        check("missing models: ask raises its own error", True)
    localmatch.MODEL_DIR = real_dir
    settings.configure({})

    if not localmatch.available():
        print(f"\nNETWORK: {len(ATTEMPTS)} blocked connection attempt(s)")
        print("ALL PASS so far; SKIP: the on-laptop matcher's model files are not in assets/models/clip" if not FAILS
              else "FAILED: " + ", ".join(FAILS))
        sys.exit(1 if FAILS else 0)

    # ---------- synthetic coloured images ----------
    colours = {"red": (220, 20, 20), "blue": (20, 40, 220), "green": (20, 180, 40)}
    img = {}
    for name, rgb in colours.items():
        img[name] = tmp / f"{name}.jpg"
        Image.new("RGB", (288, 512), rgb).save(img[name], quality=95)
    te = localmatch.embed_texts(["a red picture", "a blue picture"])
    fe = localmatch.embed_frames([img["red"], img["blue"]])
    sim = localmatch.similarities(fe, te)
    check("sanity: 'a red picture' scores the red image above the blue one", sim[0, 0] > sim[1, 0], sim.tolist())
    check("sanity: 'a blue picture' scores the blue image above the red one", sim[1, 1] > sim[0, 1], sim.tolist())
    check("embeddings are unit vectors", np.allclose(np.linalg.norm(fe, axis=-1), 1, atol=1e-3)
          and np.allclose(np.linalg.norm(te, axis=-1), 1, atol=1e-3))

    # ---------- ask(): same shape and range as the other engines, deterministic, cached ----------
    fdir = tmp / "job" / "match" / "frames"
    fdir.mkdir(parents=True)
    order = ["red", "blue", "green", "red", "blue"]
    frames = []
    for i, ts in enumerate([[0.5, 2.0], [0.5, 2.0, 4.0]]):
        row = []
        for j in range(len(ts)):
            p = fdir / f"{i + 1:02d}_{j + 1}.jpg"
            shutil.copy(img[order[len(sum(frames, [])) + j]], p)
            row.append(p)
        frames.append(row)
    per_clip = [[0.5, 2.0], [0.5, 2.0, 4.0]]
    phr = [{"text": "a red room."}, {"text": "a blue room,"}, {"text": "Call me to book a showing."}]
    s1, usage = localmatch.ask(frames, per_clip, phr)
    check("ask: shape is clips x frames x phrases", [len(c) for c in s1] == [2, 3] and all(len(r) == 3 for c in s1 for r in c), s1)
    check("ask: whole numbers 0 to 10", all(isinstance(x, int) and 0 <= x <= 10 for c in s1 for r in c for x in r), s1)
    check("ask: usage costs nothing", usage == {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}, usage)
    check("ask: embeddings cached in the job folder", (tmp / "job" / "match" / "clip_embeddings.npz").exists())
    s2, _ = localmatch.ask(frames, per_clip, phr)
    (tmp / "job" / "match" / "clip_embeddings.npz").unlink()
    localmatch._models.clear()
    s3, _ = localmatch.ask(frames, per_clip, phr)
    check("ask: deterministic (cached, and recomputed after reloading the models)", s1 == s2 == s3, (s1, s3))
    check("ask: the red frame scores 'a red room' above the blue frame", s1[0][0][0] > s1[0][1][0], s1)
    check("ask: scores_from accepts the same shape the models return",
          match.scores_from({"rows": [{"shot": k + 1, "frame": j + 1, "scores": r} for k, c in enumerate(s1)
                                      for j, r in enumerate(c)]}, per_clip, phr) == s1)
    check("engine switch: match.ask_model('clip') is the local matcher",
          match.ask_model("clip", frames, per_clip, phr)[0] == s1)
    k_clip = match.cache_key([(str(img["red"]), 3.0, False, 0.0)], [[0.5]], [{"text": "a"}], "clip")
    k_qwen = match.cache_key([(str(img["red"]), 3.0, False, 0.0)], [[0.5]], [{"text": "a"}], "qwen")
    k_claude = match.cache_key([(str(img["red"]), 3.0, False, 0.0)], [[0.5]], [{"text": "a"}])
    check("cache key: clip has its own key", len({k_clip, k_qwen, k_claude}) == 3)

    # ---------- match.plan end to end on synthetic coloured clips (made here, not hers) ----------
    clips = []
    for i, c in enumerate(["red", "blue", "green"], 1):
        out = tmp / "clips" / f"{i:02d}.mp4"
        out.parent.mkdir(exist_ok=True)
        ff.run(["-f", "lavfi", "-i", f"color=c={c}:s=288x512:d=6:r=30", "-c:v", "libx264", "-pix_fmt", "yuv420p", out],
               file=out, what="Making a test clip")
        clips.append(out)
    checked = []
    for c in clips:
        dur, hdr = assemble.check_clip(c)
        checked.append((str(c), dur, hdr, 0.0))
    texts = "This is the green room. Here is the red room. And now the blue room.".split()
    words = [{"word": w, "start": 0.4 * i, "end": 0.4 * i + 0.35} for i, w in enumerate(texts)]
    words = [{**w, "start": w["start"] * 3, "end": w["end"] * 3} for w in words]   # about 15 s of voice
    length = words[-1]["end"] + 0.3
    even = assemble.plan_slots([d for _, d, _, _ in checked], length, words)
    ai.has_key = lambda: False
    ai.has_openrouter_key = lambda: False
    settings.configure({"match_engine": "clip", "match_twice": True})
    job = tmp / "plan_job"
    job.mkdir()
    slots, info, warns = match.plan(checked, words, length, even, job)
    check("plan clip: runs with no key at all, records engine and model", info["engine"] == "clip"
          and info["model"] == localmatch.MODEL_ID and "no API key" not in info.get("reason", ""), info)
    check("plan clip: one pass even with match_twice on", info.get("passes") == 1, info)
    check("plan clip: no AI error, never raises", "ai_error" not in info and "Error" not in str(info.get("reason", "")), info)
    sj = json.loads((job / "match" / "scores.json").read_text(encoding="utf-8"))
    check("plan clip: scores.json records the engine and fits the frames", sj["engine"] == "clip"
          and [len(r) for r in sj["scores"]] == [len(t) for t in sj["frame_times"]], sj.get("engine"))
    check("plan clip: a plan or a clean even-split reason", (slots is not None and abs(sum(slots) - length) < 0.1)
          or (slots is None and info.get("reason")), (slots, info))
    if info.get("order"):
        check("plan clip: green clip moved first, where 'green room' is said", info["order"][0] == 2, info)
    job2 = tmp / "plan_job2"
    job2.mkdir()
    slots2, info2, _ = match.plan(checked, words, length, even, job2, reuse=job)
    check("plan clip: remake reuses the saved scores", info2.get("cached") is True and slots2 == slots, info2)
    settings.configure({})

    # ---------- her tour sample (optional): runs offline, right shape ----------
    tour = sorted((ROOT / "samples" / "tour").glob("0*.mp4"))[:3]
    if len(tour) == 3:
        tc = []
        for c in tour:
            dur, hdr = assemble.check_clip(c)
            tc.append((str(c), dur, hdr, 0.0))
        pc = [match.frame_times(d)[:2] for _, d, _, _ in tc]
        fr = match.extract_frames(tc, pc, tmp / "tour" / "match" / "frames")
        sc, _ = match.ask_model("clip", fr, pc, [{"text": "the front door of a house"}, {"text": "a basement rec room"}])
        check("tour sample: scored offline, right shape", [len(c) for c in sc] == [2, 2, 2] and all(len(r) == 2 for c in sc for r in c))
    else:
        print("SKIP tour sample check (samples/tour is not in this copy)")

    check("no network: not a single connection was attempted", not ATTEMPTS, ATTEMPTS)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("ALL PASS" if not FAILS else "FAILED: " + ", ".join(FAILS))
sys.exit(1 if FAILS else 0)
