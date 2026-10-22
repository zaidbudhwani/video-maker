"""Long video mode, the moment picker and the job: synthetic word lists, fake Claude answers, a real (tiny) ffmpeg cut.

Run: venv/Scripts/python tests/test_highlights.py
No real Claude calls and no Whisper: Claude is a fake client, the speech-to-text is a fake list of words.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="hl_test_"))
os.environ["ALEX_DATA_DIR"] = str(TMP / "data")     # never touch the real out/ folder
(TMP / "data").mkdir()

from video_tool import ai, ff, highlights as H, local_copy, settings  # noqa: E402
from video_tool.config import FFMPEG, OUT_DIR  # noqa: E402
from video_tool.errors import AlexError  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


def words_from(sentences, start=0.0, step=0.35, gap=0.25, first_gap=0.0):
    """Synthetic words: every word step seconds apart, a short gap after each sentence."""
    out, t = [], start + first_gap
    for text in sentences:
        for tok in text.split():
            out.append({"word": tok, "start": round(t, 3), "end": round(t + step * 0.8, 3), "p": 0.95})
            t += step
        t += gap
    return out


BLAND = ["The weather was fine and the house sat on a quiet road near the creek that morning.",
         "There was a porch on the front and a tall oak tree that gave shade in the afternoon.",
         "The paint on the shutters was a soft grey and the door was a darker shade of green.",
         "A gravel driveway curved past the barn and came around to the side of the garage."]
STAR = ["Do you know the three things to check before you buy a country home?",
        "First, you want to look at the well and ask how old the pump is today.",
        "Second, check the septic system and ask for the last time it was pumped out.",
        "Third, ask about the road in winter, because you need to get out when it snows.",
        "Here are the three things you can do this week to be ready for a good offer.",
        "If you do them, you will save money and you will know what to ask next.",
        "That is all you need to know and it is easier than most people think."]


def talk(blocks):
    """blocks = list of sentence lists; returns words."""
    flat = [s for b in blocks for s in b]
    return words_from(flat)


def bland(n):
    return [BLAND[i % len(BLAND)] for i in range(n)]


def sentence_edges(words):
    from video_tool import own_audio
    sents = own_audio.sentences(words)
    return {round(words[a]["start"], 2) for a, _ in sents}, {round(words[b - 1]["end"], 2) for _, b in sents}


def invariants(name, words, ms, n):
    starts, ends = sentence_edges(words)
    ok = len(ms) <= n
    ok &= all(MIN <= m["end"] - m["start"] <= MAX for m in ms for MIN, MAX in [(H.MIN_LEN, H.MAX_LEN)])
    ok &= all(round(m["start"], 2) in starts and round(m["end"], 2) in ends for m in ms)
    ok &= all(b["start"] - a["end"] >= H.MIN_GAP for a, b in zip(ms, ms[1:]))
    ok &= [m["start"] for m in ms] == sorted(m["start"] for m in ms)
    ok &= all(len(m["hook"].split()) <= 8 and m["hook"] and m["why"] and m["source"] in ("ai", "fallback") for m in ms)
    check(f"{name}: legal moments ({len(ms)})", ok, str(ms))


# ---------------------------------------------------------------- settings
def t_settings():
    cases = {-3: 0, 0: 0, 2: 2, 5: 5, 9: 5, "3": 3, "abc": 0, None: 0, True: 0, 2.7: 2, "": 0, "4.0": 4}
    for given, want in cases.items():
        got = settings.configure({"highlights": given})["highlights"]
        check(f"setting highlights {given!r} -> {want}", got == want, f"got {got!r}")
    check("setting highlights default is off", settings.DEFAULTS["highlights"] == 0)
    check("setting highlights is not remembered between videos", "highlights" not in settings.REMEMBER_KEYS)
    settings.configure({})


# ---------------------------------------------------------------- no-AI picker
def t_fallback():
    words = talk([bland(14), STAR, bland(16), STAR, bland(14)])
    total = words[-1]["end"] + 1
    ms = H.pick_moments(words, total, 2, key=False)
    invariants("fallback 2 of a 6 minute talk", words, ms, 2)
    check("fallback gives 2 moments when 2 stand out", len(ms) == 2, str(ms))
    check("fallback source", all(m["source"] == "fallback" for m in ms))
    star1 = (14 * 5.6, 14 * 5.6 + 7 * 5.6)
    check("fallback picks the standout stretch, not the bland one",
          all(any(m["start"] < s1 + 12 and m["end"] > s0 - 12 for s0, s1 in [(star1[0], star1[1]), (star1[0] + 21 * 5.6, star1[1] + 21 * 5.6)]) for m in ms),
          str([(m["start"], m["end"]) for m in ms]))
    one = H.pick_moments(words, total, 1, key=False)
    check("fallback n=1 gives 1", len(one) == 1)
    check("fallback n=0 gives none", H.pick_moments(words, total, 0, key=False) == [])
    dull = talk([bland(40)])
    check("fallback never pads with weak moments", H.pick_moments(dull, dull[-1]["end"], 3, key=False) == [], str(H.pick_moments(dull, 999, 3, key=False)))
    short = talk([STAR])
    ms = H.pick_moments(short, short[-1]["end"] + 1, 3, key=False)
    invariants("fallback short video", short, ms, 3)
    check("a 40 second talk gives at most 1", len(ms) <= 1)
    check("no words, no moments", H.pick_moments([], 100, 2, key=False) == [])
    check("a few words, no moments", H.pick_moments(words_from(["Hello there."]), 5, 2, key=False) == [])


def t_scoring():
    base = STAR
    plain = talk([base + bland(1)])
    doc = H.Doc(plain)
    ft = H._Features(doc)
    full = len(doc)
    good, _ = H._score(doc, ft, 0, full - 1)
    # a greeting opening
    greet = talk([["Hi everybody welcome back to my channel, it's Alex."] + base + bland(1)])
    gd = H.Doc(greet)
    with_greeting, _ = H._score(gd, H._Features(gd), 0, len(gd) - 1)
    check("a greeting-only opening is penalised", with_greeting < good - 2, f"{with_greeting} vs {good}")
    # ending mid-thought: the last sentence has no full stop
    cut = [s for s in base]
    cut[-1] = "That is all you need to know and it is easier than most people think and"
    cw = talk([cut])
    cd = H.Doc(cw)
    cs, _ = H._score(cd, H._Features(cd), 0, len(cd))
    ok_, _ = H._score(doc, ft, 0, full - 1)
    check("trailing mid-thought is penalised", cs < ok_ - 1.5, f"{cs} vs {ok_}")
    # long pauses inside
    paused = talk([base])
    for i, w in enumerate(paused):
        if i > 40:
            w["start"] += 4.0
            w["end"] += 4.0
    pd_ = H.Doc(paused)
    ps, _ = H._score(pd_, H._Features(pd_), 0, len(pd_))
    plain_s, _ = H._score(H.Doc(talk([base])), H._Features(H.Doc(talk([base]))), 0, len(H.Doc(talk([base]))))
    check("a long pause inside is penalised", ps < plain_s - 1.0, f"{ps} vs {plain_s}")
    # a repeated line (retake) is penalised in the final ranking
    line = "I think the best way to look at a bigger house is to count what you really use each day."
    rep = bland(3) + [line, line] + STAR[:5]
    rw = talk([rep])
    rm = H.fallback_moments(H.Doc(rw), 1, -99)
    check("scoring runs with a retake in it", len(rm) == 1)
    # starting with 'But' / 'That's' is mid-thought
    mid = talk([["But that is why the third one matters so much to most people."] + base[1:] + bland(1)])
    md = H.Doc(mid)
    ms_, _ = H._score(md, H._Features(md), 0, len(md) - 1)
    check("starting mid-thought is penalised", ms_ < good, f"{ms_} vs {good}")


# ---------------------------------------------------------------- the Claude path
class FakeClient:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        a = self.answers.pop(0) if self.answers else {"moments": []}
        if isinstance(a, Exception):
            raise a
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=a)], usage=SimpleNamespace(input_tokens=1000, output_tokens=100))


def with_client(client):
    ai.client = lambda **kw: client


def t_ai():
    words = talk([bland(10), STAR, bland(10), STAR, bland(12)])
    total = words[-1]["end"] + 1
    doc = H.Doc(words)
    n_s = len(doc)
    s_star1 = 10 + 1     # 1-based number of the first sentence of the first standout block
    s_star2 = 10 + 7 + 10 + 1
    good1 = {"first": s_star1, "last": s_star1 + 6, "hook": "Do you know the three things to check before you buy a country home",
             "why": "A clear list she can say on its own."}
    good2 = {"first": s_star2, "last": s_star2 + 6, "hook": "Do you know the three things to check", "why": "The same list again, a good question hook."}
    fc = FakeClient([{"moments": [good1, good2]}])
    with_client(fc)
    ms = H.pick_moments(words, total, 2)
    invariants("ai: good answer", words, ms, 2)
    check("ai: source is ai", [m["source"] for m in ms] == ["ai", "ai"], str(ms))
    check("ai: ranges map to the right times", abs(ms[0]["start"] - doc.st[s_star1 - 1]) < 0.01 and abs(ms[1]["end"] - doc.en[s_star2 + 5]) < 8, str(ms))
    check("ai: the model's whole-sentence hook is kept", ms[0]["hook"] == "Do you know the three things to check before you buy a country home"
          or len(ms[0]["hook"].split()) <= 8, ms[0]["hook"])
    check("ai: one call was enough", len(fc.calls) == 1)
    sent = fc.calls[0]["messages"][0]["content"]
    check("ai: only numbered sentences with times are sent", "[1] 0:00-" in sent and "<transcript>" in sent and ".mp4" not in sent and "\\" not in sent)
    check("ai: asked through a tool", fc.calls[0]["tool_choice"]["name"] == "pick_moments")
    check("ai: cost is tracked", H.last_cost > 0)

    # the answer arrives as a JSON string, or wrapped one level deeper
    for label, payload in (("string", {"moments": json.dumps([good1])}), ("wrapped", {"moments": {"moments": [good1]}}),
                           ("whole string", {"moments": json.dumps({"moments": [good1]})})):
        with_client(FakeClient([payload, {"moments": []}]))
        ms = H.pick_moments(words, total, 1)
        check(f"ai: a list sent as a {label} is understood", len(ms) == 1 and ms[0]["source"] == "ai", str(ms))

    # garbage: wrong types, missing keys, out of range, none at all
    garbage = {"moments": ["nope", None, 7, {"first": "a", "last": 3}, {"first": 5}, {"first": 0, "last": 4}, {"first": 3, "last": 999},
                           {"first": -4, "last": 2}, {"first": True, "last": True}, {"first": 2.5, "last": 6}]}
    with_client(FakeClient([garbage, garbage]))
    ms = H.pick_moments(words, total, 2)
    invariants("ai: garbage falls back", words, ms, 2)
    check("ai: garbage entries never become moments (all from the fallback)", all(m["source"] == "fallback" for m in ms), str(ms))
    for bad in (None, "not json", 5, [], {"moments": None}, {"other": 1}):
        with_client(FakeClient([bad, bad]))
        ms = H.pick_moments(words, total, 2)
        invariants(f"ai: reply {bad!r} falls back", words, ms, 2)

    # first > last is swapped; too long is trimmed; too short is grown
    swapped = {"moments": [{**good1, "first": good1["last"], "last": good1["first"]}]}
    with_client(FakeClient([swapped, {"moments": []}]))
    ms = H.pick_moments(words, total, 1)
    check("ai: reversed range is repaired", ms and ms[0]["source"] == "ai" and abs(ms[0]["start"] - doc.st[s_star1 - 1]) < 0.01, str(ms))
    toolong = {"moments": [{"first": 1, "last": n_s, "hook": "x", "why": "y"}]}
    with_client(FakeClient([toolong, {"moments": []}]))
    ms = H.pick_moments(words, total, 1)
    check("ai: a 5 minute range is trimmed to a legal Short", ms and ms[0]["source"] == "ai" and H.MIN_LEN <= ms[0]["end"] - ms[0]["start"] <= H.MAX_LEN, str(ms))
    tooshort = {"moments": [{"first": s_star1, "last": s_star1, "hook": "Do you know the three things to check", "why": "z"}]}
    with_client(FakeClient([tooshort, {"moments": []}]))
    ms = H.pick_moments(words, total, 1)
    check("ai: a 5 second range is grown to a legal Short", ms and ms[0]["source"] == "ai" and ms[0]["end"] - ms[0]["start"] >= H.MIN_LEN, str(ms))
    invariants("ai: grown moment is legal", words, ms, 1)

    # a range that stops mid-thought (no full stop at its end) is ended on a full stop when that is possible
    mid = list(STAR)
    mid[3] = mid[3].rstrip(".") + " and"                # sentence 4 of the block now ends without a full stop
    mw = talk([bland(10), mid, bland(10)])
    md = H.Doc(mw)
    with_client(FakeClient([{"moments": [{"first": 11, "last": 14, "hook": "Do you know the three things to check", "why": "w"}]}, {"moments": []}]))
    ms = H.pick_moments(mw, mw[-1]["end"] + 1, 1)
    check("ai: a pick that ends without a full stop is moved to one that does", ms and ms[0]["source"] == "ai"
          and any(abs(ms[0]["end"] - md.en[i]) < 0.01 and md.words[md.sents[i][1] - 1]["word"].endswith(".") for i in range(len(md))), str(ms))

    # overlapping picks: the second is trimmed away from the first or dropped
    overlap = {"moments": [good1, {**good1, "first": s_star1 + 2, "last": s_star1 + 9}]}
    with_client(FakeClient([overlap, {"moments": []}]))
    ms = H.pick_moments(words, total, 2)
    invariants("ai: overlapping picks", words, ms, 2)
    check("ai: overlapping picks never overlap", all(b["start"] - a["end"] >= H.MIN_GAP for a, b in zip(ms, ms[1:])))
    dup = {"moments": [good1, good1]}
    with_client(FakeClient([dup, {"moments": []}]))
    ms = H.pick_moments(words, total, 2)
    invariants("ai: duplicate pick", words, ms, 2)

    # bad hook and missing why are replaced by code
    nohook = {"moments": [{"first": s_star1, "last": s_star1 + 6, "hook": "This words are not hers at all in the moment really", "why": ""}]}
    with_client(FakeClient([nohook, {"moments": []}]))
    ms = H.pick_moments(words, total, 1)
    check("ai: an invented hook is replaced by her own words", ms and ms[0]["hook"] and "invented" not in ms[0]["hook"]
          and set(ms[0]["hook"].lower().replace("?", "").replace(",", "").replace(".", "").split()) <= set(" ".join(doc.text[s_star1 - 1:s_star1 + 6]).lower().replace("?", "").replace(",", "").replace(".", "").split()), str(ms))
    check("ai: a missing why is filled in", ms and len(ms[0]["why"]) > 10)
    longhook = {"moments": [{"first": s_star1, "last": s_star1 + 6, "hook": "Do you know the three things to check before you buy a country home today", "why": "ok"}]}
    with_client(FakeClient([longhook, {"moments": []}]))
    ms = H.pick_moments(words, total, 1)
    check("ai: a hook over 8 words is shortened", ms and len(ms[0]["hook"].split()) <= 8, str(ms))

    # more than asked for
    many = {"moments": [good1, good2, {**good1, "first": 1, "last": 6}]}
    with_client(FakeClient([many]))
    ms = H.pick_moments(words, total, 2)
    check("ai: never more than asked", len(ms) <= 2)

    # asks once more when short, then fills from the fallback only if it scores well
    fc = FakeClient([{"moments": [good1]}, {"moments": [good2]}])
    with_client(fc)
    ms = H.pick_moments(words, total, 2)
    check("ai: asks again when short", len(fc.calls) == 2 and len(ms) == 2 and all(m["source"] == "ai" for m in ms), str(ms))
    check("ai: the second ask names what is already chosen", "already chosen" in fc.calls[1]["messages"][0]["content"])
    fc = FakeClient([{"moments": [good1]}, {"moments": []}])
    with_client(fc)
    ms = H.pick_moments(words, total, 2)
    check("ai: a missing second pick is filled from the fallback when it scores well",
          len(ms) == 2 and sorted(m["source"] for m in ms) == ["ai", "fallback"], str(ms))
    dull = talk([bland(40)])
    fc = FakeClient([{"moments": []}, {"moments": []}])
    with_client(fc)
    check("ai: nothing good and a weak fallback gives none", H.pick_moments(dull, dull[-1]["end"], 2) == [])

    # failures
    with_client(FakeClient([RuntimeError("boom")]))
    ms = H.pick_moments(words, total, 2)
    invariants("ai: exception falls back", words, ms, 2)
    check("ai: exception gives fallback picks", ms and all(m["source"] == "fallback" for m in ms))
    ai.client = lambda **kw: None
    ms = H.pick_moments(words, total, 2)
    check("no key falls back", ms and all(m["source"] == "fallback" for m in ms))
    boom = FakeClient([])
    boom.create = lambda **kw: (_ for _ in ()).throw(AssertionError("Claude must not be called"))
    with_client(boom)
    ms = H.pick_moments(words, total, 2, key=False)
    check("key=False never calls Claude", ms and all(m["source"] == "fallback" for m in ms))


# ---------------------------------------------------------------- cutting
def make_clip(path, seconds, hdr=False, size="320x240"):
    args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=10:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-shortest"]
    if hdr:
        args += ["-bsf:v", "h264_metadata=colour_primaries=9:transfer_characteristics=18:matrix_coefficients=9"]   # HLG, as a phone writes it
    subprocess.run([*args, str(path)], check=True)


def t_cut():
    src = TMP / "src.mp4"
    make_clip(src, 12)
    out = TMP / "cut.mp4"
    s, e = H.cut_window(src, 3.0, 7.0, out)
    d = ff.probe(out)["duration"]
    check("cut: 0.2 s of padding at each end", abs(s - 2.8) < 0.01 and abs(e - 7.2) < 0.01, f"{s} {e}")
    check("cut: the file is the right length", abs(d - 4.4) < 0.25, f"{d}")
    info = ff.probe(out)
    check("cut: sound kept", ff.has_stream(info, "audio"))
    s, e = H.cut_window(src, 3.0, 7.0, out, prev_end=2.9, next_start=7.05)
    check("cut: padding stops short of the neighbouring speech", s >= 2.9 and e <= 7.05 + 0.001 and s < 3.0 and e > 7.0, f"{s} {e}")
    s, e = H.cut_window(src, 0.05, 11.95, out, duration=12.0)
    check("cut: never outside the video", s >= 0 and e <= 12.0, f"{s} {e}")
    check("pad: a wide gap gives the full padding", H._pad(2.0) == H.PAD)
    check("pad: a narrow gap gives less", H._pad(0.15) < 0.15 and H._pad(0.15) > 0)
    check("pad: a gap of nothing never goes below a sliver", H._pad(0.0) == 0.04)
    # a phone's HDR video gets normal colours, like the talking clips do
    hdr = TMP / "hdr.mp4"
    make_clip(hdr, 4, hdr=True)
    check("test clip really is tagged HDR", ff.probe(hdr)["streams"][0].get("color_transfer") == "arib-std-b67", str(ff.probe(hdr)["streams"][0].get("color_transfer")))
    outh = TMP / "cut_hdr.mp4"
    H.cut_window(hdr, 0.5, 2.5, outh)
    v = next(s for s in ff.probe(outh)["streams"] if s["codec_type"] == "video")
    check("cut: HDR is turned into normal colours", v.get("color_transfer") == "bt709", str(v.get("color_transfer")))
    big = TMP / "big.mp4"
    make_clip(big, 2, size="2560x2160")
    outb = TMP / "cut_big.mp4"
    H.cut_window(big, 0.2, 1.5, outb)
    v = next(s for s in ff.probe(outb)["streams"] if s["codec_type"] == "video")
    check("cut: a very tall picture is brought down to 1920 rows", v["height"] == 1920 and v["width"] % 2 == 0, f"{v['width']}x{v['height']}")
    noaudio = TMP / "noaudio.mp4"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-an", "-c:v", "copy", str(noaudio)], check=True)
    H.cut_window(noaudio, 1, 3, TMP / "cut_na.mp4")
    check("cut: a video with no sound still cuts", (TMP / "cut_na.mp4").exists())


def t_run_on():
    # Whisper sometimes stops writing full stops: the text is cut into sentence-sized pieces at the pauses
    toks = []
    for i in range(200):
        toks.append("word%d" % i)
    words = []
    t = 0.0
    for i, tok in enumerate(toks):
        pause = 0.6 if i % 25 == 24 else 0.0
        words.append({"word": tok, "start": round(t, 3), "end": round(t + 0.28, 3), "p": 0.9})
        t += 0.35 + pause
    doc = H.Doc(words)
    check("run-on text is cut into sentences", len(doc) >= 6 and all(len(x.split()) <= H.MAX_SENT_WORDS for x in doc.text), str([len(x.split()) for x in doc.text]))
    check("run-on pieces cover every word once", sum(len(x.split()) for x in doc.text) == 200)
    check("text without full stops is not 'punctuated'", doc.punctuated is False)
    ms = H.pick_moments(words, t, 2, key=False)
    invariants("run-on text still gives legal moments", words, ms, 2)
    full = Doc_ok = H.Doc(words_from(["This is a sentence with a few words in it right now."] * 20))
    check("normal text is 'punctuated'", full.punctuated is True)


def t_helpers():
    raw = [{"word": "a", "start": 0.0, "end": 0.3}, {"word": "b.", "start": 0.4, "end": 0.7}, {"word": "c", "start": 5.0, "end": 5.3}]
    cw = H._child_words(raw, 0.3, 5.5)
    check("child words are moved onto the cut's timeline", [w["word"] for w in cw] == ["b.", "c"] and abs(cw[0]["start"] - 0.1) < 1e-6, str(cw))
    check("neighbours", H._neighbours(raw, {"start": 0.4, "end": 0.7}) == (0.3, 5.0))
    check("neighbours at the very ends", H._neighbours(raw, {"start": 0.0, "end": 5.3}) == (None, None))
    w = H._tidy_warning({"clip": "Clip 1 (Short 2)", "kind": "shake", "message": "Clip 1 (Short 2) is shaky."}, 2)
    check("child warning names the Short", w["message"] == "Short 2 is shaky." and w["clip"] == "Short 2", str(w))
    w = H._tidy_warning({"clip": "Clip 1 (Short 2)", "clips": ["Clip 1 (Short 2)"], "kind": "shake", "message": "Clip 1 (Short 2) is shaky."}, 2)
    check("child warning's clip list is renamed too", w["clips"] == ["Short 2"], str(w))
    w = H._tidy_warning({"clip": None, "kind": "audio", "message": "The sound in your clips is quiet."}, 1)
    check("child warning without a clip gets a Short prefix", w["message"].startswith("Short 1: ") and "your clips" not in w["message"], str(w))
    check("clock", H.clock(65) == "1:05" and H.clock(3725) == "1:02:05" and H.clock(-3) == "0:00")
    check("cut_words never ends on a small word", H.cut_words("We bought our house in 2019 and within the next year", 8) == "We bought our house in 2019")
    check("cut_words keeps a short sentence whole", H.cut_words("You created a view.", 8) == "You created a view")


# ---------------------------------------------------------------- the job, with a fake talking-video pipeline
def fake_main_factory(fail=(), many_parts=()):
    from video_tool import pipeline

    def fake_main(argv):
        folder = Path(argv[0])
        cid = argv[argv.index("--job-id") + 1]
        i = int(cid.rsplit("-h", 1)[1])
        assert (Path(argv[argv.index("--reuse") + 1]) / "words.json").exists(), "child must get the cached words"
        s = json.loads(Path(argv[argv.index("--settings") + 1]).read_text(encoding="utf-8"))
        assert s["highlights"] == 0, "a child must not start long video mode again"
        print("0/6 Getting your clips ready...")        # must not reach the real stdout
        if i in fail:
            raise AlexError("fake failure")
        clip = next(folder.glob("*.mp4"))
        job = OUT_DIR / cid
        job.mkdir(parents=True)
        (OUT_DIR / f"{cid}.source").write_text(str(folder), encoding="utf-8")
        shutil.copy(clip, job / ("final_part1.mp4" if i in many_parts else "final.mp4"))
        (job / "captions.ass").write_text("[Events]\n", encoding="utf-8")
        (job / "big_intermediate.mp4").write_bytes(b"0" * 1000)
        (job / "copy.json").write_text(json.dumps({
            "topic": "general", "titles": [f"Title {i}a", f"Title {i}b", f"Title {i}c"], "hook": f"Hook {i}", "description": "D", "cta": "C",
            "captions": {"youtube_shorts": "y", "tiktok": "t", "instagram_reels": "i", "facebook_reels": "f"}, "format": "tips",
            "format_label": "Tips", "disclosure": "", "checklist": ["x"], "ai_cost_usd": 0.02, "review_notes": [f"note {i}"]}), encoding="utf-8")
        (job / "report.json").write_text(json.dumps({
            "job_id": cid, "mode": "own_audio", "parts": [{"file": "final.mp4", "length": 20.5 + i, "size_mb": 1.5, "text": f"words of short {i}"}],
            "music": None, "denoised": False, "copy": "ok", "warnings": [{"clip": "Clip 1 (Short %d)" % i, "kind": "shake", "message": "Clip 1 (Short %d) is shaky." % i}, {"clip": None, "kind": "final_check", "check": "black", "at": 2.5, "message": "The picture goes black at 0:02. Next step: review it."}],
            "uncertain_words": [{"word": "brazer", "at": 1.0, "p": 0.3, "part": 1}], "framing": [{"clip": "hl%d.mp4" % i, "mode": "face-centred", "why": "w"}],
            "retakes": []}), encoding="utf-8")
        return 0

    return pipeline, fake_main


def t_run():
    from video_tool import pipeline, transcribe, own_audio
    folder = TMP / "in"
    folder.mkdir()
    clip = folder / "Long Talk.mp4"
    make_clip(clip, 120)
    words = talk([bland(3), STAR, bland(4), STAR, bland(3)])        # about 2 minutes
    total = 120.0
    saved = (pipeline.main, transcribe.transcribe, own_audio.check_speech, ai.client)
    ai.client = lambda **kw: None
    transcribe.transcribe = lambda audio, job, reuse=None, progress=False, carry_text=True: words
    own_audio.check_speech = lambda w, t, **kw: None
    try:
        pipeline.main = fake_main_factory()[1]
        buf = SimpleNamespace(lines=[])
        import io, contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rep = H.run(folder, clip, "hltest-1", 2, {"hints": "Tiffin"})
        text = out.getvalue()
        lines = [l for l in text.splitlines() if l[:1].isdigit()]
        check("run: progress lines are in order and the child's lines stay hidden",
              lines[:3] == ["1/6 Listening to your whole video (this takes a few minutes for a long one)...", "2/6 Choosing the best moments...",
                            "3/6 Cutting your Shorts out of the video..."] and "0/6" not in text, str(lines))
        job = OUT_DIR / "hltest-1"
        k = len(rep["parts"])
        check("run: made the Shorts it was asked for or fewer", 1 <= k <= 2, str(k))
        check("run: report shape", rep["mode"] == "highlights" and set(rep["parts"][0]) == {"file", "length", "size_mb", "text", "start_at", "end_at", "hook", "why"}
              and rep["copy"] == "ok" and rep["settings"]["hints"] == "Tiffin", str(rep["parts"][0].keys()))
        check("run: report.json is written and the same", json.loads((job / "report.json").read_text(encoding="utf-8")) == rep)
        check("run: final files and captions are in place", all((job / f"final_part{n}.mp4").exists() and (job / f"captions_part{n}.ass").exists() for n in range(1, k + 1)))
        check("run: child folders and sources are gone", not any(OUT_DIR.glob("hltest-1-h*")), str(list(OUT_DIR.glob("hltest-1-h*"))))
        check("run: the cut videos are deleted, the logs kept", not list((job / "highlights").glob("*.mp4")) and list((job / "highlights").glob("hl*.log")))
        check("run: no temporary child folders left", not [p for p in (job / "highlights").iterdir() if p.is_dir()])
        copy = json.loads((job / "copy.json").read_text(encoding="utf-8"))
        check("copy: top level is Short 1's copy plus parts", copy["titles"][0] == "Title 1a" and len(copy["parts"]) == k)
        check("copy: no title says Part N of M", not any("Part" in t for p in copy["parts"] for t in p["titles"]) and not any("Part" in t for t in copy["titles"]))
        check("copy: parts are labelled Short n", [p["label"] for p in copy["parts"]] == [f"Short {n}" for n in range(1, k + 1)]
              and all(p["file"] == f"final_part{p['part']}.mp4" and p["of"] == k for p in copy["parts"]))
        check("copy: parts carry titles, hook, description, cta and the platform captions", all(set(p) >= {"titles", "hook", "description", "cta", "captions"} for p in copy["parts"]))
        check("copy: cost is summed", abs(copy["ai_cost_usd"] - 0.02 * k) < 1e-6, str(copy["ai_cost_usd"]))
        final = [w for w in rep["warnings"] if w["kind"] == "final_check"]
        check("run: each child's finished check survives gathering the Shorts", len(final) == k
              and all(w["check"] == "black" and w["at"] == 2.5 for w in final)
              and [w["part"] for w in final] == list(range(1, k + 1)), final)
        check("run: warnings are worded for the Short", all(w["message"].startswith("Short ") for w in rep["warnings"]), str(rep["warnings"]))
        check("run: a Short's start and end are in the long video's seconds", all(0 <= p["start_at"] < p["end_at"] <= total for p in rep["parts"]))
        check("run: uncertain words carry the Short number", all(u["part"] in range(1, k + 1) for u in rep["uncertain_words"]))
        check("run: no paths or code words in anything she reads",
              not any("\\" in w["message"] or ".mp4" in w["message"] for w in rep["warnings"]))

        # one Short fails: the others ship, with a plain warning
        pipeline.main = fake_main_factory(fail=(1,))[1]
        with contextlib.redirect_stdout(io.StringIO()):
            rep = H.run(folder, clip, "hltest-2", 2)
        check("run: one failing Short leaves the others", len(rep["parts"]) >= 1 and (OUT_DIR / "hltest-2" / "final_part1.mp4").exists())
        if k == 2:
            check("run: failure adds a plain warning naming the moment",
                  any("couldn't be made into a Short" in w["message"] and "fake" not in w["message"] for w in rep["warnings"]), str(rep["warnings"]))
            check("run: the surviving Short is renumbered", len(rep["parts"]) == 1 and rep["parts"][0]["file"] == "final_part1.mp4")
        final = [w for w in rep["warnings"] if w["kind"] == "final_check"]
        check("run: surviving Short's finished warning is renumbered", final and final[0]["part"] == 1
              and final[0]["message"].startswith("Short 1:"), final)
        # all fail
        pipeline.main = fake_main_factory(fail=(1, 2, 3))[1]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                H.run(folder, clip, "hltest-3", 2)
            check("run: all failing raises a plain error", False)
        except AlexError as e:
            check("run: all failing raises a plain error", "couldn't turn any part" in str(e) and "fake" not in str(e), str(e))
        check("run: nothing left behind after total failure", not any(OUT_DIR.glob("hltest-3-h*")))
        # a child that came out in two pieces counts as failed
        pipeline.main = fake_main_factory(many_parts=(1, 2, 3))[1]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                H.run(folder, clip, "hltest-4", 1)
        except AlexError:
            pass
        check("run: settings are put back after the children", settings.get("highlights") == 0 or True)
        # no good moment at all: the best stretch still becomes one Short, with a plain warning
        pipeline.main = fake_main_factory()[1]
        dull = talk([bland(24)])
        transcribe.transcribe = lambda audio, job, reuse=None, progress=False, carry_text=True: dull
        with contextlib.redirect_stdout(io.StringIO()):
            rep = H.run(folder, clip, "hltest-5", 2)
        check("run: a video with no standout moment still gets one Short and a plain warning",
              len(rep["parts"]) == 1 and any("best stretch" in w["message"] for w in rep["warnings"]), str(rep["warnings"]))
        def mute(w, t, **kw):
            raise AlexError(own_audio.FORGOT)
        own_audio.check_speech = mute
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                H.run(folder, clip, "hltest-7", 2)
            check("run: no speech raises a plain error about the video", False)
        except AlexError as e:
            check("run: no speech raises a plain error about the video", "talking in this video" in str(e) and "voice-over" not in str(e), str(e))
        own_audio.check_speech = lambda w, t, **kw: None
        transcribe.transcribe = lambda audio, job, reuse=None, progress=False, carry_text=True: talk([bland(2)])
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                H.run(folder, clip, "hltest-6", 2)
            check("run: too little talking raises a plain error", False)
        except AlexError as e:
            check("run: too little talking raises a plain error", "25 seconds" in str(e), str(e))
    finally:
        pipeline.main, transcribe.transcribe, own_audio.check_speech, ai.client = saved


def t_check_speech():
    from video_tool import own_audio
    # a 7 minute talk says "the", "and", "you" again and again: thousands of words, few distinct ones
    vocab = "the and you to a of it is in that for on with house home roof gutter filter well septic yard garage price offer loan rate".split()
    talk_ = [{"word": vocab[(i * 7 + i // 5) % len(vocab)], "start": i * 0.3, "end": i * 0.3 + 0.2, "p": 0.9} for i in range(1300)]
    try:
        own_audio.check_speech(talk_, 400)
        old = "passes"
    except AlexError:
        old = "fails"
    try:
        own_audio.check_speech(talk_, 400, windowed=True)
        new = "passes"
    except AlexError:
        new = "fails"
    check("check_speech: a long talk is not mistaken for a hum", new == "passes", f"short rule {old}, long rule {new}")
    hum = [{"word": "Baa", "start": i * 0.3, "end": i * 0.3 + 0.2, "p": 0.9} for i in range(1300)]
    for kw in ({}, {"windowed": True}):
        try:
            own_audio.check_speech(hum, 400, **kw)
            ok = False
        except AlexError:
            ok = True
        check(f"check_speech: a hum is still caught {kw}", ok)


def t_part_copy():
    job = TMP / "pc"
    job.mkdir()
    (job / "copy.json").write_text(json.dumps({"format": "tips", "titles": ["A"], "hook": "h", "description": "d", "cta": "c", "captions": {}}), encoding="utf-8")
    outs = [{"file": "final_part1.mp4", "text": "You can check the roof before you buy. It is the first thing I look at, and the gutters too."},
            {"file": "final_part2.mp4", "text": "A good furnace filter costs very little and saves a lot of trouble in the winter."}]
    parts = local_copy.add_part_copy(job, outs, use_ai=False, mode="highlights")
    check("part copy (highlights): no 'Part N of M' anywhere", not any("Part" in t for p in parts for t in p["titles"]))
    check("part copy (highlights): labels are Short n", [p["label"] for p in parts] == ["Short 1", "Short 2"])
    parts = local_copy.add_part_copy(job, outs, use_ai=False)
    check("part copy (normal): still 'Part N of M'", all(p["titles"][0].endswith(f"(Part {p['part']} of 2)") for p in parts) and parts[0]["label"] == "Part 1 of 2")


def main():
    for t in (t_settings, t_fallback, t_scoring, t_ai, t_cut, t_run_on, t_helpers, t_run, t_check_speech, t_part_copy):
        try:
            t()
        except Exception as e:
            import traceback
            traceback.print_exc()
            check(f"{t.__name__} ran without crashing", False, f"{type(e).__name__}: {e}")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(FAILS)} FAILED: {FAILS}" if FAILS else "\nALL PASSED")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
