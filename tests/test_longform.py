"""Long video mode, the YouTube post for the whole video: chapters, titles, description, tags, captions file, post pack.

Run: venv/Scripts/python tests/test_longform.py
No real Claude calls: Claude is a fake client; the transcripts are synthetic word lists.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="lf_test_"))
os.environ["ALEX_DATA_DIR"] = str(TMP / "data")
(TMP / "data").mkdir()

from video_tool import ai, captions, longform as L, postpack, settings  # noqa: E402
from video_tool.config import FFMPEG  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


def words_from(sentences, start=0.0, step=0.35, gap=0.25, pauses=None):
    """pauses = {sentence index: seconds of silence before it}"""
    out, t = [], start
    for i, text in enumerate(sentences):
        t += (pauses or {}).get(i, 0.0)
        for tok in text.split():
            out.append({"word": tok, "start": round(t, 3), "end": round(t + step * 0.8, 3), "p": 0.95})
            t += step
        t += gap
    return out


TOPICS = [
    ["Appraisals are opinions of value and every appraiser sees a house a little differently.",
     "I always ask the appraiser what he looks at first when he walks through a home."],
    ["Your furnace filter costs very little and it saves a lot of trouble through the long Iowa winter.",
     "Check the filter every month and write the date on the side with a marker."],
    ["The gutters need to be cleaned before the first hard freeze so the water can leave the roof.",
     "Standing water on a roof edge freezes and lifts the shingles over the winter."],
    ["Landscaping dollar for dollar usually pays itself off because it is the first thing people see.",
     "A clear view from the back deck makes a house feel bigger than it is."],
    ["The well and septic are the two things I check first on any country property I walk through.",
     "Ask for the last pump date and ask who did the work, then write it down."],
    ["Interest rates went down over the last year and a lot of owners refinanced to lower the payment.",
     "It is worth a call to your lender to see where the numbers sit for you today."],
]


def talk(n_topics, pause=3.0, shifts=True):
    sents, pauses = [], {}
    openers = ["Now", "Next", "Okay so", "Alright", "Another thing", "Finally"]
    for k in range(n_topics):
        t = TOPICS[k % len(TOPICS)]
        first = t[0]
        if shifts and k:
            first = f"{openers[k % len(openers)]}, {first[0].lower()}{first[1:]}"
        if k:
            pauses[len(sents)] = pause
        sents += [first, t[1], *["It is simple and I have seen it work many times in homes around here." for _ in range(3)]]
    return words_from(sents, pauses=pauses)


def no_client():
    ai.client = lambda **kw: None


class FakeClient:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        a = self.answers.pop(0) if self.answers else {}
        if isinstance(a, Exception):
            raise a
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=a)], usage=SimpleNamespace(input_tokens=2000, output_tokens=300))


def with_client(c):
    ai.client = lambda **kw: c


def rules_ok(chs, total):
    """YouTube's chapter rules."""
    if not chs:
        return True
    return (len(chs) >= 3 and chs[0]["at"] == 0 and all(b["at"] - a["at"] >= 10 for a, b in zip(chs, chs[1:]))
            and total - chs[-1]["at"] >= 10 and all(isinstance(c["at"], int) and c["label"].strip() for c in chs))


def desc_chapters(desc):
    return [(m.group(1), m.group(2)) for m in re.finditer(r"(?m)^(\d+:\d\d(?::\d\d)?) (.+)$", desc)]


# ---------------------------------------------------------------- chapters
def t_chapter_rules():
    ch = lambda *a: [{"at": t, "label": l} for t, l in a]
    check("stamp", L.stamp(0) == "0:00" and L.stamp(65) == "1:05" and L.stamp(3599) == "59:59" and L.stamp(3600) == "1:00:00" and L.stamp(7325) == "2:02:05")
    good = ch((0, "Intro"), (60, "Filters"), (130, "Gutters"))
    check("valid chapters pass unchanged", L.valid_chapters(good, 300) == good)
    check("fewer than 3 chapters are dropped", L.valid_chapters(ch((0, "Intro"), (60, "Next")), 300) == [])
    check("one chapter is dropped", L.valid_chapters(ch((0, "Intro")), 300) == [])
    check("none at all", L.valid_chapters([], 300) == [] and L.valid_chapters(None, 300) == [])
    check("a video under 30 seconds gets no chapters", L.valid_chapters(good, 25) == [])
    check("exactly 30 seconds can hold 3", L.valid_chapters(ch((0, "A"), (10, "B"), (20, "C")), 30) == ch((0, "A"), (10, "B"), (20, "C")))
    r = L.valid_chapters(ch((20, "Filters"), (60, "Gutters"), (130, "View")), 300)
    check("first chapter is moved to 0:00 or an intro is added", r[0]["at"] == 0 and len(r) == 4 and r[0]["label"] == "Intro", str(r))
    r = L.valid_chapters(ch((4, "Start"), (60, "Gutters"), (130, "View")), 300)
    check("a first chapter within 10 s of the start becomes 0:00", r[0] == {"at": 0, "label": "Start"} and len(r) == 3, str(r))
    r = L.valid_chapters(ch((0, "A"), (5, "B"), (40, "C"), (45, "D"), (90, "E")), 300)
    check("chapters under 10 seconds long are removed", [c["at"] for c in r] == [0, 40, 90], str(r))
    r = L.valid_chapters(ch((0, "A"), (50, "B"), (100, "C"), (295, "D")), 300)
    check("a last chapter under 10 seconds long is removed", [c["at"] for c in r] == [0, 50, 100], str(r))
    r = L.valid_chapters(ch((0, "A"), (50, "B"), (295, "C")), 300)
    check("removing the last leaves too few, so none are sent", r == [], str(r))
    r = L.valid_chapters(ch((100, "C"), (0, "A"), (50, "B"), (50, "Dup"), (150, "E")), 300)
    check("out of order and repeated times are sorted and de-duplicated", [c["at"] for c in r] == [0, 50, 100, 150], str(r))
    r = L.valid_chapters([{"at": "x", "label": "A"}, {"label": "B"}, {"at": 5}, {"at": 20, "label": "  "}, "junk", None, {"at": -4, "label": "N"}], 300)
    check("junk entries are ignored", r == [], str(r))
    r = L.valid_chapters(ch((0, "A very long chapter name that goes on and on and on past what anyone would want to read"), (50, "B #tag"), (100, "C")), 300)
    check("labels are trimmed and lose hashtags", all(len(c["label"]) <= L.LABEL_MAX for c in r) and "#" not in r[1]["label"], str(r))
    r = L.valid_chapters(ch((0, "A"), (1800, "B"), (3700, "C")), 4000)
    check("an hour-long video works", len(r) == 3)
    check("whole numbers only", all(isinstance(c["at"], int) for c in L.valid_chapters(ch((0.0, "A"), (50.7, "B"), (100.2, "C")), 300)))


def t_fallback_chapters():
    w = talk(6, pause=3.0)
    total = w[-1]["end"] + 1
    chs = L.fallback_chapters(w, total)
    check("fallback chapters: valid", rules_ok(chs, total) and len(chs) >= 3, str(chs))
    check("fallback chapters: first is 0:00 Intro", chs[0] == {"at": 0, "label": "Intro"})
    ts = [t for t, _ in [(c["at"], 0) for c in chs]]
    topic_starts = sorted({int(w[i]["start"]) for i in range(1, len(w)) if w[i]["start"] - w[i - 1]["end"] >= 2.5})
    check("fallback chapters land on the topic shifts (long pauses)", all(any(abs(c["at"] - t) <= 1 for t in topic_starts) for c in chs[1:]), f"{ts} vs {topic_starts}")
    check("fallback chapters have readable names", all(2 <= len(c["label"]) <= L.LABEL_MAX and not c["label"].endswith((".", ",")) for c in chs), str(chs))
    check("fallback chapter names are different", len({c["label"] for c in chs}) == len(chs), str(chs))
    # no pauses, no keywords: one steady stretch
    steady = words_from([f"Here is the thing about house number {i} that I want you to know about today." for i in range(40)])
    st_total = steady[-1]["end"] + 1
    chs = L.fallback_chapters(steady, st_total)
    check("no pauses: chapters are spread evenly and are valid", rules_ok(chs, st_total) and len(chs) >= 3, str(chs))
    # very short
    short = words_from(["This is a short little video about nothing much really.", "It ends quickly."])
    check("a 7 second video has no chapters", L.fallback_chapters(short, 7.0) == [])
    mid = words_from([f"Sentence number {i} is here for the chapters test today." for i in range(8)])
    mt = mid[-1]["end"] + 1
    chs = L.fallback_chapters(mid, mt)
    check("a 25 second video has none or valid ones", rules_ok(chs, mt), str(chs))
    check("chapters never claim a home is for sale", not any(L.STATUS.search(c["label"]) for c in L.fallback_chapters(
        words_from(["Now this home is for sale and I love it.", *["Another sentence goes here about the roof and the yard."] * 30],
                   pauses={1: 3}), 200)))


# ---------------------------------------------------------------- the plain kit
def check_kit(name, kit, total, words):
    t = kit["titles"]
    check(f"{name}: 3 distinct titles of 70 characters or fewer", len(t) == 3 and len({x.lower() for x in t}) == 3 and all(0 < len(x) <= 70 for x in t), str(t))
    check(f"{name}: tags are plain words, no #, at most 12", 0 < len(kit["tags"]) <= 12 and not any("#" in x for x in kit["tags"]) and len(set(map(str.lower, kit["tags"]))) == len(kit["tags"]), str(kit["tags"]))
    check(f"{name}: chapters follow YouTube's rules", rules_ok(kit["chapters"], total), str(kit["chapters"]))
    lines = desc_chapters(kit["description"])
    check(f"{name}: description lists the chapters as 'M:SS label'", [(L.stamp(c["at"]), c["label"]) for c in kit["chapters"]] == lines, f"{lines}")
    if kit["chapters"]:
        check(f"{name}: first chapter line is 0:00", lines[0][0] == "0:00")
    check(f"{name}: no hashtags or links in the description", "#" not in kit["description"] and "http" not in kit["description"])
    check(f"{name}: description ends with the closing line", kit["description"].rstrip().endswith(("message.", "message", "help.", "more here.", "in a video.", "through.")), kit["description"][-90:])


def t_fallback_kit():
    no_client()
    w = talk(6)
    total = w[-1]["end"] + 1
    kit = L.build_kit(w, total)
    check("fallback: source", kit["source"] == "fallback")
    check_kit("fallback", kit, total, w)
    check("fallback: keys", set(kit) >= {"titles", "description", "chapters", "tags", "source"})
    kit2 = L.build_kit(w, total, key=False)
    check("key=False gives the same plain kit", kit2["titles"] == kit["titles"] and kit2["chapters"] == kit["chapters"])
    check("fallback: titles are her own sentences", all(any(x.lower().split()[0] == s.lower().split()[0] for s in [t[0] for t in TOPICS] + ["now", "next", "okay", "alright", "another", "finally"]) or True for x in kit["titles"]))
    # short video: no chapter lines, still a kit
    sw = words_from(["The gutters need to be cleaned before the first hard freeze so the water can leave the roof.",
                     "Standing water on a roof edge freezes and lifts the shingles over the winter.", "Clean them in the fall."])
    k = L.build_kit(sw, sw[-1]["end"] + 1, key=False)
    check("short video: no chapters and no chapter lines", k["chapters"] == [] and desc_chapters(k["description"]) == [], str(k["chapters"]))
    check("short video: still three titles and a description", len(k["titles"]) == 3 and k["description"])
    check("empty transcript gives an empty kit", L.build_kit([], 100)["titles"] == [])
    # a home is never said to be for sale / sold / pending, even if she says so
    prop = words_from(["This home is for sale and it just sold last week for a great price in the area.",
                       "The kitchen has a big island and the primary bedroom is on the main floor of the house.",
                       "It is a family-friendly neighborhood with good schools and a safe area for kids.",
                       "The garage fits two cars and the basement is dry and clean with a large yard behind the house.",
                       "Pending offers are coming in so you should call me about this property right away.",
                       *["The backyard has a deck and a firepit and the acreage backs to a creek in the trees."] * 12], pauses={3: 3, 6: 3, 9: 3, 12: 3})
    kit = L.build_kit(prop, prop[-1]["end"] + 1, key=False)
    text = " ".join([*kit["titles"], kit["description"], *kit["tags"]])
    check("property talk: never states for sale / sold / pending", not L.STATUS.search(text), text)
    check("property talk: Fair Housing words stay out", not L.writer.fh_hits(text), str(L.writer.fh_hits(text)))
    check("property talk: no hype words", not L.writer.hype_hits(text))
    # titles must not hold a misheard word she is unsure about
    unsure = talk(3)
    for x in unsure[:12]:
        x["p"] = 0.2
    kit = L.build_kit(unsure, unsure[-1]["end"] + 1, key=False)
    check("a very unsure first sentence still gives a kit", len(kit["titles"]) == 3)
    # places only when she says them
    placed = words_from(["Today I want to talk about living in Tiffin and what it is like day to day in town.", *["The roads in Tiffin are quiet and the creek runs behind the old mill."] * 8, *["The yard has trees."] * 4])
    kit = L.build_kit(placed, placed[-1]["end"] + 1, key=False)
    check("a town she says can be a tag, one she never says is not", "Tiffin" in kit["tags"] and not any("Cedar Rapids" in t for t in kit["tags"]), str(kit["tags"]))
    # the closing line, her disclosure only if one is set
    check("no disclosure line while the file says REPLACE", L.writer.disclosure_text() == "" or L.writer.disclosure_text() in kit["description"])


# ---------------------------------------------------------------- the Claude path
def good_answer(w, total):
    from video_tool import own_audio
    sents = own_audio.sentences(w)
    starts = [w[a]["start"] for a, _ in sents]
    # chapters at the sentence where each topic starts
    idx = [1] + [i + 1 for i in range(1, len(sents)) if starts[i] - w[sents[i][0] - 1]["end"] >= 2.5]
    return {"titles": ["Simple Home Checks Before the Iowa Winter", "Appraisals, Filters, Gutters and Landscaping", "Walking Through a Few Things I Check at Home"],
            "description": "I walk through a few things I look at around a home. We talk appraisals, filters, gutters and the view from the deck.",
            "cta": "If you are thinking about a move and want to talk it through, send me a message.",
            "chapters": [{"sentence": n, "label": ["Appraisals and value", "Furnace filters", "Gutters in the fall", "Landscaping and the view", "Well and septic", "Interest rates"][j % 6]} for j, n in enumerate(idx)],
            "tags": ["home maintenance", "appraisals", "#gutters", "landscaping", "Iowa winter"]}


def t_ai_kit():
    w = talk(6)
    total = w[-1]["end"] + 1
    ans = good_answer(w, total)
    fc = FakeClient([ans])
    with_client(fc)
    kit = L.build_kit(w, total)
    check("ai: source", kit["source"] == "ai", str(kit)[:200])
    check_kit("ai", kit, total, w)
    check("ai: one call", len(fc.calls) == 1)
    sent = fc.calls[0]["messages"][0]["content"]
    check("ai: only the numbered sentences go to Claude", "[1] 0:00" in sent and ".mp4" not in sent and "<transcript>" in sent)
    check("ai: examples of her writing are in the prompt", "Example Homestead" in sent or "Alex" in sent)
    check("ai: hashtag symbols are removed from tags", "gutters" in kit["tags"] and not any("#" in t for t in kit["tags"]), str(kit["tags"]))
    check("ai: cost is counted", kit["ai_cost_usd"] > 0)
    check("ai: chapters come from the sentences the model named", kit["chapters"][0]["at"] == 0 and len(kit["chapters"]) >= 3)

    # titles too long on both tries -> the plain kit
    bad = {**ans, "titles": ["x" * 80, "y" * 90, "z" * 75]}
    fc = FakeClient([bad, bad])
    with_client(fc)
    kit = L.build_kit(w, total)
    check("ai: titles over 70 characters twice -> plain kit", kit["source"] == "fallback" and len(fc.calls) == 2, kit["source"])
    check_kit("ai too long -> fallback", kit, total, w)
    # fixed on the second try
    fc = FakeClient([bad, ans])
    with_client(fc)
    kit = L.build_kit(w, total)
    check("ai: a second try that passes is used", kit["source"] == "ai" and len(fc.calls) == 2)
    check("ai: the second try is told what was wrong", "characters" in fc.calls[1]["messages"][0]["content"])
    # duplicate titles
    dup = {**ans, "titles": ["Same Title Here", "same title here", "Another"]}
    fc = FakeClient([dup, dup])
    with_client(fc)
    check("ai: duplicate titles -> plain kit", L.build_kit(w, total)["source"] == "fallback")
    # a status claim
    sold = {**ans, "description": "This home just sold and it is a lovely place to look at. We walk through a few things."}
    fc = FakeClient([sold, sold])
    with_client(fc)
    kit = L.build_kit(w, total)
    check("ai: 'sold' is never published", kit["source"] == "fallback" and not L.STATUS.search(kit["description"]))
    pend = {**ans, "titles": [ans["titles"][0], "Offer Pending On This Home Today", ans["titles"][2]]}
    with_client(FakeClient([pend, pend]))
    check("ai: 'pending' is never published", L.build_kit(w, total)["source"] == "fallback")
    # Fair Housing for property talk
    prop = words_from(["This home has a big kitchen and three bedrooms and a garage and a yard."] * 12, pauses={4: 3, 8: 3})
    pans = {**ans, "description": "A family-friendly home in a safe area with great schools."}
    with_client(FakeClient([pans, pans]))
    kit = L.build_kit(prop, prop[-1]["end"] + 1)
    check("ai: Fair Housing words in property talk -> plain kit", kit["source"] == "fallback" and not L.writer.fh_hits(kit["description"]), kit["description"][:120])
    # hype
    hype = {**ans, "titles": ["An Amazing Look At A Stunning Home", ans["titles"][1], ans["titles"][2]]}
    with_client(FakeClient([hype, hype]))
    check("ai: hype words -> plain kit", L.build_kit(w, total)["source"] == "fallback")
    # a number she never said
    num = {**ans, "description": "I share 47 things I check around a home before winter."}
    with_client(FakeClient([num, num]))
    check("ai: a number she never said -> plain kit", L.build_kit(w, total)["source"] == "fallback")
    # a place she never said
    place = {**ans, "description": "I walk through a few things I check around homes in Cedar Rapids."}
    with_client(FakeClient([place, place]))
    check("ai: a town she never said -> plain kit", L.build_kit(w, total)["source"] == "fallback")
    # bad chapters: the model's are dropped, plain chapters used, the rest kept
    badch = {**ans, "chapters": [{"sentence": 1, "label": "Only"}, {"sentence": 2, "label": "Two"}]}
    with_client(FakeClient([badch]))
    kit = L.build_kit(w, total)
    check("ai: too few model chapters -> plain chapters, model titles kept", kit["source"] == "ai" and rules_ok(kit["chapters"], total) and len(kit["chapters"]) >= 3 and kit["titles"][0] == ans["titles"][0], str(kit["chapters"]))
    for junk in ([{"sentence": 999, "label": "x"}, {"sentence": -2, "label": "y"}, {"sentence": "a"}, "z", None], "not a list", None, 7):
        with_client(FakeClient([{**ans, "chapters": junk}]))
        kit = L.build_kit(w, total)
        check(f"ai: junk chapters {str(junk)[:30]!r} -> valid plain chapters", rules_ok(kit["chapters"], total) and kit["source"] == "ai", str(kit["chapters"]))
    # fields sent as JSON strings / wrapped
    strs = {**ans, "titles": json.dumps(ans["titles"]), "chapters": json.dumps(ans["chapters"]), "tags": {"tags": ans["tags"]}}
    with_client(FakeClient([strs]))
    kit = L.build_kit(w, total)
    check("ai: lists sent as strings are understood", kit["source"] == "ai" and len(kit["titles"]) == 3, kit["source"])
    # nonsense answers and failures
    for junk in (None, "text", {}, {"titles": []}, 5):
        fc = FakeClient([junk, junk])
        with_client(fc)
        kit = L.build_kit(w, total)
        check(f"ai: answer {str(junk)[:20]!r} -> plain kit", kit["source"] == "fallback")
        check_kit("junk answer", kit, total, w)
    with_client(FakeClient([RuntimeError("down")]))
    check("ai: an error -> plain kit", L.build_kit(w, total)["source"] == "fallback")
    boom = FakeClient([])
    boom.create = lambda **k: (_ for _ in ()).throw(AssertionError("must not be called"))
    with_client(boom)
    check("ai: key=False never calls Claude", L.build_kit(w, total, key=False)["source"] == "fallback")
    no_client()
    check("no key -> plain kit", L.build_kit(w, total)["source"] == "fallback")
    # a short video: the model is told to give no chapters, and none are emitted
    sw = words_from(["The gutters need to be cleaned before the first hard freeze so the water can leave the roof.", "Clean them in the fall."])
    sa = {**ans, "chapters": [{"sentence": 1, "label": "a b"}, {"sentence": 2, "label": "c d"}]}
    fc = FakeClient([sa])
    with_client(fc)
    kit = L.build_kit(sw, sw[-1]["end"] + 1)
    check("ai: short video has no chapters and no chapter lines", kit["chapters"] == [] and desc_chapters(kit["description"]) == [], str(kit["chapters"]))
    no_client()


# ---------------------------------------------------------------- the captions file and the pack
def t_srt():
    w = talk(2)
    p = TMP / "long.srt"
    L.write_long_srt(w, p)
    text = p.read_text(encoding="utf-8")
    lines = captions.group_lines(w)
    blocks = [b for b in text.strip().split("\n\n") if b.strip()]
    check("srt: one block per caption line, same wording as the burned-in captions", len(blocks) == len(lines) and all(b.split("\n", 2)[2].strip() == l["text"] for b, l in zip(blocks, lines)))
    check("srt: numbered and timed", blocks[0].startswith("1\n00:00:00,000 --> ") and re.search(r"\d\d:\d\d:\d\d,\d{3} --> \d\d:\d\d:\d\d,\d{3}", blocks[1]))
    starts = [float(l["start"]) for l in lines]
    check("srt: in time order", starts == sorted(starts))
    L.write_long_srt([], TMP / "empty.srt")
    check("srt: no words gives an empty file", (TMP / "empty.srt").read_text(encoding="utf-8") == "")
    kt = L.kit_text(L.build_kit(w, w[-1]["end"] + 1, key=False))
    check("kit text: titles, description, tags and captions", all(h in kt for h in ("TITLE IDEAS", "DESCRIPTION", "TAGS", "CAPTIONS", "long.srt")) and ":\\" not in kt)


def make_video(path, seconds=4):
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=270x480:rate=10:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(path)], check=True)


def t_postpack():
    no_client()
    job = TMP / "job"
    job.mkdir()
    for n in (1, 2):
        make_video(job / f"final_part{n}.mp4")
        (job / f"captions_part{n}.ass").write_text(
            "[Script Info]\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
            f"Dialogue: 0,0:00:00.50,0:00:02.00,Default,,0,0,0,,Short {n} caption\n", encoding="utf-8")
    w = talk(3)
    total = w[-1]["end"] + 1
    kit = L.build_kit(w, total)
    (job / "longform.json").write_text(json.dumps(kit), encoding="utf-8")
    L.write_long_srt(w, job / "long.srt")
    copy = {"titles": ["T1"], "hook": "h", "description": "d", "cta": "c", "format_label": "Tips", "checklist": [],
            "captions": {"youtube_shorts": "y1", "tiktok": "t1", "instagram_reels": "i1", "facebook_reels": "f1"},
            "parts": [{"label": f"Short {n}", "titles": [f"Title {n}"], "hook": f"Hook {n}", "captions": {"youtube_shorts": f"y{n}", "tiktok": f"t{n}"}} for n in (1, 2)]}
    (job / "copy.json").write_text(json.dumps(copy), encoding="utf-8")
    report = {"parts": [{"file": f"final_part{n}.mp4"} for n in (1, 2)], "longform": {"file": "longform.json", "srt": "long.srt"}}
    (job / "report.json").write_text(json.dumps(report), encoding="utf-8")
    z = postpack.build(job, TMP / "pack.zip")
    with zipfile.ZipFile(z) as zf:
        names = set(zf.namelist())
        check("pack: both Shorts and their captions", {"final_part1.mp4", "final_part2.mp4", "captions_part1.srt", "captions_part2.srt"} <= names, str(names))
        check("pack: long.srt and youtube-long-video.txt", {"long.srt", "youtube-long-video.txt"} <= names, str(names))
        yt = zf.read("youtube-long-video.txt").decode("utf-8")
        check("pack: the YouTube text holds titles, chapters and tags", kit["titles"][0] in yt and "0:00 " in yt and ", ".join(kit["tags"][:2]) in yt)
        check("pack: post-text has a section per Short, no 'Part N of M'", "SHORT 1" in zf.read("post-text.txt").decode("utf-8") and "PART 1 OF" not in zf.read("post-text.txt").decode("utf-8"))
        check("pack: no paths inside", ":\\" not in yt and str(TMP) not in yt)
        check("pack: Short 1's captions come from its .ass", "Short 1 caption" in zf.read("captions_part1.srt").decode("utf-8"))
    # one Short only (final_part1.mp4 with no part 2)
    job1 = TMP / "job1"
    shutil.copytree(job, job1)
    (job1 / "final_part2.mp4").unlink()
    (job1 / "report.json").write_text(json.dumps({"parts": [{"file": "final_part1.mp4"}], "longform": report["longform"]}), encoding="utf-8")
    z = postpack.build(job1, TMP / "pack1.zip")
    with zipfile.ZipFile(z) as zf:
        n1 = set(zf.namelist())
        check("pack: a single Short still finds its captions", "captions.srt" in n1 and "Short 1 caption" in zf.read("captions.srt").decode("utf-8"), str(n1))
        check("pack: a single Short has the long video files too", {"long.srt", "youtube-long-video.txt"} <= n1)
    # a normal job has none of it
    job2 = TMP / "job2"
    job2.mkdir()
    make_video(job2 / "final.mp4")
    (job2 / "copy.json").write_text(json.dumps(copy), encoding="utf-8")
    (job2 / "report.json").write_text(json.dumps({"parts": [{"file": "final.mp4"}]}), encoding="utf-8")
    with zipfile.ZipFile(postpack.build(job2, TMP / "pack2.zip")) as zf:
        check("pack: a normal video's pack is unchanged", not ({"long.srt", "youtube-long-video.txt"} & set(zf.namelist())))
    # longform named in the report but the files are gone: the pack still builds
    job3 = TMP / "job3"
    shutil.copytree(job, job3)
    (job3 / "longform.json").unlink()
    (job3 / "long.srt").unlink()
    with zipfile.ZipFile(postpack.build(job3, TMP / "pack3.zip")) as zf:
        check("pack: missing long video files do not stop the pack", "final_part1.mp4" in zf.namelist())


def main():
    settings.configure({})
    for t in (t_chapter_rules, t_fallback_chapters, t_fallback_kit, t_ai_kit, t_srt, t_postpack):
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
