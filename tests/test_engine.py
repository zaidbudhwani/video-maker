"""Engine lane unit tests: retakes, opening check, uncertain words, warnings, hotwords, AI errors, settings.

Run: venv/Scripts/python tests/test_engine.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from video_tool import ai, match, own_audio, quality, settings, silence, transcribe  # noqa: E402
from video_tool.errors import AlexError  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


def words_from(text, start=0.0, step=0.4, gap=0.3):
    """Synthetic word list: words end 'sentences' with their own punctuation, a short gap after each word."""
    out, t = [], start
    for tok in text.split():
        out.append({"word": tok, "start": round(t, 3), "end": round(t + step * 0.8, 3), "p": 0.95})
        t += step
        if tok.endswith((".", "?", "!")):
            t += gap
    return out


def join_words(*chunks, between=0.5):
    out, t = [], 0.0
    for c in chunks:
        ws = words_from(c, t)
        out += ws
        t = ws[-1]["end"] + between
    return out


# ---------- retakes ----------
settings.configure({})
A = "Welcome to this beautiful three bedroom home in Marion."
A2 = "Welcome to this beautiful three bedroom home in Marion."
B = "The kitchen has granite counters and new appliances."
C = "Call me today to set up a showing of this home."

w = join_words(B, A, A2, C)
r = own_audio.find_retakes(w)
check("retake: exact repeat is found", len(r) == 1, r)
check("retake: first take is cut, last kept", r and r[0]["i0"] == len(B.split()) and r[0]["i1"] == len(B.split()) + len(A.split()), r)
check("retake: said is the removed take", r and r[0]["said"] == A, r)
kept = own_audio.drop_ranges(w, [(r[0]["i0"], r[0]["i1"])])
check("retake: kept words read cleanly", " ".join(x["word"] for x in kept) == " ".join([B, A2, C]))

# a stumbled first take (partial) then the full line
partial = "Welcome to this beautiful three bedroom home in"
w = join_words(B, partial, A2, C, between=1.0)
r = own_audio.find_retakes(w)
check("retake: partial first take is cut", len(r) == 1 and r[0]["i1"] == len(B.split()) + len(partial.split()), r)

# a short false start between the takes
w = join_words(B, A, "Sorry, let me.", A2, C)
r = own_audio.find_retakes(w)
check("retake: false start between takes", len(r) == 1 and r[0]["i1"] > r[0]["i0"] + len(A.split()), r)

# slightly different wording on the retake (still the same line)
w = join_words(B, "This home has three bedrooms and two full baths.", "This home has three bedrooms and two full baths.", C)
check("retake: identical longer line", len(own_audio.find_retakes(w)) == 1)

# false positives
w = join_words("Location, location, location.", B, C)
check("no retake: location location location", own_audio.find_retakes(w) == [])
w = words_from("Location, location, location. Location, location, location.")
check("no retake: repeated short phrase", own_audio.find_retakes(w) == [])
w = join_words("Call me today.", "Call me today.", B)
check("no retake: short sentence said twice", own_audio.find_retakes(w) == [])
w = join_words(A, B, C)
check("no retake: ordinary talk", own_audio.find_retakes(w) == [])
w = join_words("The kitchen is on the left side of the house.", "The bathroom is on the right side of the house.")
check("no retake: same pattern, different content", own_audio.find_retakes(w) == [])
w = join_words(A, A2, A)
check("no retake: said three times on purpose", own_audio.find_retakes(w) == [])
w = join_words(A, B, "And then we walk to the back of the house to see the yard.", A2)
check("no retake: same line far apart with content between", own_audio.find_retakes(w) == [])
w = join_words(A, "x " * 3 + "y.", A2)  # 4 tokens of junk between: still a restart
check("retake: tiny false start between", len(own_audio.find_retakes(w)) == 1)
w = join_words(A, A2) ; w2 = [{**x, "start": x["start"] + 0} for x in w]
w2 += words_from(A, 70.0)   # far later: beyond 45 s, unrelated
check("no retake: repeat 60 s later", len(own_audio.find_retakes(w2)) == 1 and own_audio.find_retakes(w2)[0]["i1"] == len(A.split()))
# retake at the very start and the very end
w = join_words(A, A2, B)
r = own_audio.find_retakes(w)
check("retake at the very start", len(r) == 1 and r[0]["i0"] == 0, r)
w = join_words(B, A, A2)
r = own_audio.find_retakes(w)
check("retake at the very end", len(r) == 1 and r[0]["i1"] + len(A.split()) == len(w), r)
check("retake: empty and tiny lists", own_audio.find_retakes([]) == [] and own_audio.find_retakes(words_from("Hi.")) == [])
# two separate retakes
w = join_words(A, A2, B, C, C)
check("two separate retakes", len(own_audio.find_retakes(w)) == 2)

# the cut really goes through the pause machinery
w = join_words(B, A, A2, C)
r = own_audio.find_retakes(w)
cut = own_audio.drop_ranges(w, [(r[0]["i0"], r[0]["i1"])])
segs = silence.keep_segments(cut, cut[-1]["end"] + 1)
check("retake: pause machinery splits at the cut", len(segs) >= 2, segs)
tot_removed = w[r[0]["i1"]]["start"] - w[r[0]["i0"]]["start"]
check("retake: removed span is gone from the timeline",
      abs(sum(b - a for a, b in segs) - (w[-1]["end"] + 0.4 - w[0]["start"] + 0.25 - tot_removed)) < 1.5)

# ---------- opening ----------
body = " ".join(["The kitchen has granite counters and a large island with seating."] * 2)
w = words_from("Hi everybody, it's Alex, welcome back. " + body)
o = own_audio.find_opening(w)
check("opening: hi everybody / it's Alex / welcome back", o is not None and o["hook_index"] == 6, o)
check("opening: said text", o and o["said"].startswith("Hi everybody"), o)
for greeting in ("Hey guys.", "Hello everyone!", "Hi friends, welcome to my channel.", "Hey y'all, it's Alex.",
                 "Good morning everybody.", "This is Alex with Example Realty.", "Welcome back."):
    o = own_audio.find_opening(words_from(greeting + " " + body))
    check(f"opening: {greeting}", o is not None and o["hook_index"] == len(greeting.split()), o)
o = own_audio.find_opening(words_from("Hi everybody. Today I want to show you something. " + body))
check("opening: 'today I want to show you.' after a greeting is lead-in", o is not None and o["hook_index"] == 9, o)
o = own_audio.find_opening(words_from("Today I want to show you a four bedroom home on five acres in Marion. " + body))
check("no opening: 'today I' alone is the hook", o is None, o)
check("no opening: starts with content", own_audio.find_opening(words_from(body)) is None)
check("no opening: greeting only", own_audio.find_opening(words_from("Hi everybody it's Alex welcome back to the channel.")) is None)
check("no opening: 'high' is not 'hi'", own_audio.find_opening(words_from("Highway access is great, and the lot is large. " + body)) is None)
check("no opening: short list", own_audio.find_opening(words_from("Hi everybody.")) is None)
check("no opening: 'Hillcrest' is not a greeting", own_audio.find_opening(words_from("Hillcrest Drive is quiet. " + body)) is None)

# ---------- forgot the voice-over ----------
try:
    own_audio.check_speech(words_from("hmm yeah okay"), 30.0)
    check("junk: too few words refused", False)
except AlexError as e:
    check("junk: too few words refused", "forget to add your voice-over" in str(e) and "File:" not in str(e), str(e))
try:
    own_audio.check_speech(words_from("a b c d e f g h"), 90.0)
    check("junk: very sparse speech refused", False)
except AlexError:
    check("junk: very sparse speech refused", True)
try:
    own_audio.check_speech(words_from("Welcome to the kitchen, it has granite counters."), 8.0)
    check("junk: short real talk accepted", True)
except AlexError as e:
    check("junk: short real talk accepted", False, str(e))

ghost = [{"word": x, "start": i, "end": i + 0.5, "p": 0.003} for i, x in enumerate("Hiawatha, Linn County, Fort Worth, Fort Worth, Fort Worth, Fort Worth".split())]
try:
    own_audio.check_speech(ghost, 20.0)
    check("junk: words Whisper invented over noise (p near 0) refused", False)
except AlexError:
    check("junk: words Whisper invented over noise (p near 0) refused", True)
own_audio.check_speech(words_from("Welcome to the kitchen, it has granite counters."), 8.0)   # no p at all: counts
try:
    own_audio.check_speech(words_from("Baa " * 60), 30.0)
    check("junk: a looping word (Baa Baa Baa) is refused", False)
except AlexError:
    check("junk: a looping word (Baa Baa Baa) is refused", True)
import json as _j
_tips = _j.loads((ROOT / "out" / "engine-tips" / "words.json").read_text(encoding="utf-8")) if (ROOT / "out" / "engine-tips" / "words.json").exists() else None
if _tips:
    own_audio.check_speech(_tips, 149.0)
    check("junk: real 150 s talking video is accepted", True)

# ---------- uncertain words ----------
ws = [{"word": "This", "start": 0, "end": 0.3, "p": 0.99}, {"word": "is", "start": 0.3, "end": 0.5, "p": 0.99},
      {"word": "412", "start": 0.5, "end": 1.0, "p": 0.5}, {"word": "Ellis", "start": 1.0, "end": 1.3, "p": 0.4},
      {"word": "Park", "start": 1.3, "end": 1.5, "p": 0.55}, {"word": "Drive.", "start": 1.5, "end": 1.9, "p": 0.97},
      {"word": "The", "start": 2.0, "end": 2.2, "p": 0.3}, {"word": "septic", "start": 2.2, "end": 2.6, "p": 0.3},
      {"word": "the", "start": 2.6, "end": 2.7, "p": 0.1}, {"word": "tiffin", "start": 2.7, "end": 2.9, "p": 0.7},
      {"word": "Shellsburg,", "start": 3.0, "end": 3.5, "p": 0.62}, {"word": "nothing", "start": 3.5, "end": 3.9}]
u = transcribe.uncertain_words(ws)
check("uncertain: lowest first", [d["p"] for d in u] == sorted(d["p"] for d in u), u)
words_flagged = [d["word"] for d in u]
check("uncertain: digits and names under 0.6", {"412", "Ellis", "Park"} <= set(words_flagged), u)
check("uncertain: sentence-start capital 'The' is not a name", "The" not in words_flagged, u)
check("uncertain: a short plain word is not listed", "the" not in words_flagged, u)
check("uncertain: ordinary word under 0.45", "septic" in words_flagged, u)
check("uncertain: confident and missing p are not listed", "tiffin" not in words_flagged and "Shellsburg" not in words_flagged
      and "nothing" not in words_flagged, u)
many = [{"word": f"Name{i}", "start": i, "end": i + 0.5, "p": 0.1 + i / 100} for i in range(1, 40)]
many[0]["word"] = "Start"
check("uncertain: capped at 12", len(transcribe.uncertain_words(many)) == 12)
check("uncertain: old words without p", transcribe.uncertain_words([{"word": "Hello", "start": 0, "end": 1}]) == [])
check("uncertain: remap keeps p", silence.remap([{"word": "a", "start": 1.0, "end": 1.2, "p": 0.33}], [(0.5, 2.0)])[0]["p"] == 0.33)
check("uncertain: remap without p still fine", "p" not in silence.remap([{"word": "a", "start": 1.0, "end": 1.2}], [(0.5, 2.0)])[0])

# ---------- quality warnings ----------
iss = [("shake", 1.6, "Clip 1 (a.mov)"), ("shake", 1.3, "Clip 2 (b.mov)"), ("dark", 1.2, "Clip 3 (c.mov)"),
       ("blur", 1.1, "Clip 4 (d.mov)"), ("shake", 1.01, "Clip 5 (e.mov)")]
g = quality.group_warnings(iss)
check("quality: worst 3 only, grouped", sum(len(x["clips"]) for x in g) == 3 and len(g) == 2, g)
check("quality: neutral wording", all(not any(b in x["message"].lower() for b in ("try", "tripod", "tap the", "brace", "more light"))
                                      for x in g), g)
check("quality: no server file names", all("normalized" not in x["message"] and "\\" not in x["message"] for x in g))
check("quality: one clip singular", quality.group_warnings([("dark", 1.2, "Clip 3 (c.mov)")])[0]["message"] == "Clip 3 (c.mov) is on the dark side.")
check("quality: none", quality.group_warnings([]) == [])

# ---------- not shown ----------
texts = ["Welcome to my channel.", "We also have an attached garage with plenty of storage.", "The kitchen has granite counters.",
         "Call me today.", "There is a large deck overlooking the backyard."]
times = [[0, 2.5], [2.5, 6.0], [6.0, 9.0], [9.0, 10.0], [10.0, 13.0]]
scores = [[[5, 1, 9, 5, 2], [5, 0, 8, 5, 8]], [[5, 2, 3, 5, 1]]]
ns = match.not_shown(scores, times, texts)
check("not_shown: only the garage line", len(ns) == 1 and ns[0]["at"] == 2.5 and "garage" in ns[0]["idea"], ns)
check("not_shown: unrelated phrases ignored", all(n["said"] != texts[2] for n in ns))
check("not_shown: deck shown, not listed", all("deck" not in n["said"] for n in ns))
check("not_shown: idea is plain", ns[0]["idea"] == "A short clip of an attached garage", ns)
check("not_shown: filler (greeting, call to action) skipped", all(n["said"] not in (texts[0], texts[3]) for n in ns))
for line in ("Call Alex to schedule your tour, 555-010-0123.", "There's plenty of potential here to make this house your own.",
             "Come inside and see it for yourself today."):
    check(f"not_shown: skips '{line[:30]}'", match.not_shown([[[0]]], [[0, 3.0]], [line]) == [])
check("not_shown: 'This home also includes an attached garage' reads as a garage",
      match.idea_for("This home also includes an attached garage.") == "A short clip of an attached garage")
check("idea: lead-in 'sometimes I add turmeric'", match.idea_for("Sometimes I add turmeric and then we add in some flax meal.") == "A short clip of turmeric")
many_t = [f"Item number {i} is something unique." for i in range(8)]
many_times = [[i * 3, i * 3 + 3] for i in range(8)]
check("not_shown: max 3", len(match.not_shown([[[0] * 8]], many_times, many_t)) == 3)
check("not_shown: mismatched or empty input", match.not_shown([], times, texts) == [] and match.not_shown(scores, times[:2], texts) == [])
check("not_shown: short dwell skipped", match.not_shown([[[0]]], [[0, 1.0]], ["An attached garage is here."]) == [])

# ---------- hotwords: hints, no echo, no truncation of words ----------
settings.configure({"hints": "Ellis Park Dr,  Shellsburg"})
h = transcribe.hotwords()
check("hotwords: hints come first", h.startswith("Ellis Park Dr, Shellsburg, "), h[:60])
check("hotwords: vocabulary still there", "Example Realty" in h and "escrow" in h)
check("hotwords: capped", len(h) <= transcribe.HOTWORD_CHARS + 5, len(h))
check("hotwords: never ends mid-word", h.split(", ")[-1] in transcribe.vocabulary().split(", "))
settings.configure({"hints": "x" * 900})
check("hotwords: long hints trimmed to 400 by settings", len(settings.get("hints")) == 400)
check("hotwords: long hints still bounded", len(transcribe.hotwords()) <= 705)
settings.configure({})
check("hotwords: no hints = vocabulary only", transcribe.hotwords() == transcribe.vocabulary()[:700].rsplit(",", 1)[0]
      if len(transcribe.vocabulary()) > 700 else transcribe.hotwords() == transcribe.vocabulary())
for term in ("appraiser", "appraisal", "escrow", "Example Homestead", "closing costs"):
    check(f"vocabulary has {term}", term in transcribe.vocabulary())


def seg(text, nsp=0.0, lp=-0.2):
    return SimpleNamespace(text=text, no_speech_prob=nsp, avg_logprob=lp, words=[])


hot = transcribe.hotwords("Ellis Park Dr, Shellsburg")
segs = [seg("Welcome to Ellis Park Dr."), seg("Ellis Park Dr, Shellsburg", nsp=0.6, lp=-1.2), seg("Ellis Park Dr, Shellsburg"),
        seg("Marion", nsp=0.9, lp=-1.5), seg("Um, so, uh, I was like, hmm, let me think.", 0.1, -0.3)]
kept = [s.text for s in transcribe.speech_segments(segs, hot)]
check("echo: hint list repeated by Whisper over noise is dropped, the confident one is kept",
      kept.count("Ellis Park Dr, Shellsburg") == 1, kept)
check("echo: real sentence kept", "Welcome to Ellis Park Dr." in kept)
check("echo: the prompt itself is dropped", "Um, so, uh, I was like, hmm, let me think." not in kept)

# a remake with new hints must not reuse an old transcript
with tempfile.TemporaryDirectory() as d:
    old = Path(d)
    (old / "words_hints.txt").write_text("", encoding="utf-8")
    check("reuse: no hints either time reuses", transcribe._same_hints(old, ""))
    check("reuse: new hints re-listen", not transcribe._same_hints(old, "Ellis Park Dr"))
    (old / "words_hints.txt").write_text("Ellis Park Dr", encoding="utf-8")
    check("reuse: same hints reuse", transcribe._same_hints(old, "Ellis Park Dr"))
    check("reuse: dropped hints re-listen", not transcribe._same_hints(old, ""))
    check("reuse: old job with no marker and no hints reuses", transcribe._same_hints(Path(d) / "nothing", ""))

# ---------- AI failures in plain English ----------
class FakeAuth(Exception):
    status_code = 401


FakeAuth.__name__ = "AuthenticationError"
FakeAuth.__module__ = "anthropic"
e1 = ai.explain(FakeAuth("invalid x-api-key"))
check("ai: bad key", e1["kind"] == "key" and "sign in" in e1["message"], e1)


class Fake402(Exception):
    status_code = 402


e2 = ai.explain(Fake402("Your credit balance is too low"), "match")
check("ai: no credit", e2["kind"] == "credit" and "clips play in your order" in e2["message"], e2)
e3 = ai.explain(type("RateLimitError", (Exception,), {"status_code": 429})("slow down"))
check("ai: busy", e3["kind"] == "rate", e3)
e4 = ai.explain(RuntimeError("weird"))
check("ai: anything else", e4["kind"] == "other", e4)
check("ai: messages have no jargon", all(not any(b in m["message"].lower() for b in ("api", "token", "http", "traceback", "claude", "401"))
                                           for m in (e1, e2, e3, e4)))
check("ai: both lost", "titles" in ai.message("key", "both") and "order" in ai.message("key", "both"))
check("ai: is_ai_error: runtime from Claude", ai.is_ai_error(RuntimeError("Claude stopped early (refusal)")))
check("ai: is_ai_error: ffmpeg problem is not", not ai.is_ai_error(AlexError("Grabbing a frame failed")))
check("ai: is_ai_error: json decode", ai.is_ai_error(json.JSONDecodeError("x", "", 0)))

# ---------- settings ----------
s = settings.configure({})
check("settings: default caption margin 0.36", s["caption_margin"] == 0.36, s["caption_margin"])
check("settings: floor 0.33", settings.configure({"caption_margin": 0.1})["caption_margin"] == 0.33)
check("settings: old saved margin 0.30 is raised to the floor", settings.configure({"caption_margin": 0.30})["caption_margin"] == 0.33)
check("settings: ceiling holds", settings.configure({"caption_margin": 5})["caption_margin"] == 0.6)
check("settings: junk margin text is safe",
      settings.LIMITS["caption_margin"][0] <= settings.configure({"caption_margin": "0.5"})["caption_margin"] <= 0.6)
check("settings: new keys present", {"hints", "format", "cut_retakes", "start_at_hook", "match_twice"} <= set(settings.DEFAULTS))
check("settings: cut_retakes default on, start_at_hook default off",
      settings.configure({})["cut_retakes"] is True and settings.configure({})["start_at_hook"] is False)
settings.configure({})

# ---------- wide clips (real ffmpeg clips) ----------
from video_tool import pipeline  # noqa: E402
from video_tool.config import FFMPEG  # noqa: E402

with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    sizes = {"wide.mp4": "1920x1080", "tall.mp4": "1080x1920", "square.mp4": "1080x1080", "short.mp4": "1920x1080"}
    for name, sz in sizes.items():
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:s={sz}:d=3:r=10", "-pix_fmt", "yuv420p",
                        str(d / name)], check=True)
    clips = [d / n for n in sizes]
    timeline = [{"clip": "wide.mp4", "start": 0, "end": 4}, {"clip": "tall.mp4", "start": 4, "end": 8},
                {"clip": "square.mp4", "start": 8, "end": 11}, {"clip": "short.mp4", "start": 11, "end": 12.5}]
    hers = {"wide.mp4": "Clip 1 (Backyard.mov)", "square.mp4": "Clip 3 (Porch.mov)", "short.mp4": "Clip 4 (Shed.mov)"}
    wv = pipeline.wide_warnings(clips, timeline, hers)
    check("wide: landscape and square found, tall and barely-used not", [x["clip"] for x in wv] == ["Clip 1 (Backyard.mov)", "Clip 3 (Porch.mov)"], wv)
    check("wide: kind and plain message", wv[0]["kind"] == "wide" and wv[0]["message"] ==
          "Clip 1 (Backyard.mov) was filmed sideways, so only the middle of it shows.", wv[0])
    check("wide: no advice, no paths", all("tripod" not in x["message"] and "\\" not in x["message"] for x in wv))
    # more than 3
    many_clips = [d / "wide.mp4"] * 5
    tl = [{"clip": "wide.mp4", "start": 0, "end": 5}]
    wv = pipeline.wide_warnings(many_clips, tl, hers)
    check("wide: capped at 3 plus a combined line", len(wv) == 4 and wv[-1]["clip"] is None and "2 more" in wv[-1]["message"], wv)

# ---------- clips not used / too much footage (real ffmpeg) ----------
from video_tool import assemble  # noqa: E402

with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    for n, secs in (("01.mp4", 4), ("02.mp4", 4), ("03.mp4", 20)):
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=360x640:d={secs}:r=10", "-pix_fmt", "yuv420p",
                        str(d / n)], check=True)
    job = d / "job"
    job.mkdir()
    settings.configure({"skip_dead_start": False, "stabilize": False})
    _, fit = assemble.assemble([d / "01.mp4", d / "02.mp4"], 3.0, job, words=None)
    check("unused: a second clip the voice never reaches is listed", fit["unused"] == ["02.mp4"], fit["unused"])
    check("unused: the first clip is in the timeline", [t["clip"] for t in fit["timeline"]] == ["01.mp4"])
    job2 = d / "job2"
    job2.mkdir()
    try:
        assemble.assemble([d / "03.mp4"] * 1 + [d / "03.mp4"] * 2, 1.5, job2, words=None)
        check("overrun: far too much footage still refused, plainly", False)
    except AlexError as e:
        check("overrun: far too much footage still refused, plainly", "File:" not in str(e) and "folder" not in str(e)
              and "voice-over belongs with these clips" in str(e), str(e))
    job3 = d / "job3"
    job3.mkdir()
    try:
        assemble.assemble([d / "01.mp4"], 40.0, job3, words=None)
        check("shortfall: too little footage refused, plainly", False)
    except AlexError as e:
        check("shortfall: too little footage refused, plainly", "File:" not in str(e) and "more seconds of clips" in str(e), str(e))
    settings.configure({})
check("config: 12x overrun allowance (long raw footage is fine)", __import__("video_tool.config", fromlist=["x"]).MAX_OVERRUN_RATIO == 12.0)

# ---------- face crop edge cases (face finder replaced by fixed answers) ----------
from video_tool import facecrop  # noqa: E402

fake_info = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}]}
real_fc = facecrop.face_centres
scaled_w = 1920 * 1920 / 1080


def px(cx):
    return int(min(max(cx * scaled_w - 540, 0), scaled_w - 1080))


def plan(xs, crowded=0):
    real_model = facecrop.MODEL
    facecrop.MODEL = Path(__file__)   # the finder is replaced below; the real model file is not needed (the installer downloads it)
    facecrop.face_centres = lambda path, w, h: (xs, len(xs), crowded)
    try:
        return facecrop.plan_crop("x.mp4", fake_info)
    finally:
        facecrop.face_centres = real_fc
        facecrop.MODEL = real_model


short_gap = [0.3] * 20 + [None] * 7 + [0.3] * 20     # camera looks away for 3.5 s and comes back
f, why = plan(short_gap)
check("facecrop: a short look away keeps her crop (no jump to the middle)", f is not None and f"{px(0.5)}" not in f.split("crop=")[1], f)
long_gap = [0.3] * 20 + [None] * 24 + [0.3] * 20     # 12 s of the room
f, why = plan(long_gap)
check("facecrop: a long stretch of the room is centred", f is not None and str(px(0.5)) in f, f)
f, why = plan([0.3] * 40, crowded=30)
check("facecrop: two people in the picture -> whole frame", f is None and "more than one person" in why, (f, why))
f, why = plan([0.3] * 40, crowded=3)
check("facecrop: one stray second face is ignored", f is not None, (f, why))
f, why = plan([None] * 38 + [0.3, 0.3])
check("facecrop: almost no face -> whole frame", f is None and "no face" in why, why)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("ALL PASS")
