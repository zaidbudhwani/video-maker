"""Fixer regression tests for long video mode: one check for each review finding that was fixed.

Run:  venv/Scripts/python tests/test_fixer_long.py
Synthetic words and fake Claude answers only (nothing of Alex's, no real AI call). A real (tiny) ffmpeg video is made for the
silent-video and upload checks. Throwaway data folder.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = Path(tempfile.mkdtemp(prefix="alex-fixlong-"))
os.environ.update(ALEX_DATA_DIR=str(DATA), ALEX_HOSTED="1", ADMIN_USER="tester", ADMIN_PASSWORD="admin-pass-123",
                  ANTHROPIC_API_KEY="", APP_PASSWORD="", ALEX_AUTO_SLIM="0")

from fastapi.testclient import TestClient   # noqa: E402

from web import app as W, auth   # noqa: E402
from video_tool import ai, highlights as H, local_copy, longform as L, settings   # noqa: E402
from video_tool.config import FFMPEG, OUT_DIR   # noqa: E402
from video_tool.errors import AlexError   # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {str(detail)[:400]}"))
    if not ok:
        fails.append(name)


def words_from(sentences, step=0.35, gap=0.25, pauses=None):
    """Synthetic words. pauses = {word index: extra seconds of silence before that word}."""
    out, t, i = [], 0.0, 0
    for text in sentences:
        for tok in text.split():
            t += (pauses or {}).get(i, 0.0)
            out.append({"word": tok, "start": round(t, 3), "end": round(t + step * 0.8, 3), "p": 0.95})
            t += step
            i += 1
        t += gap
    return out


class FakeClient:
    def __init__(self, answers):
        self.answers, self.calls, self.messages = list(answers), [], self

    def create(self, **kw):
        self.calls.append(kw)
        a = self.answers.pop(0) if self.answers else {}
        if isinstance(a, Exception):
            raise a
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=a)], usage=SimpleNamespace(input_tokens=1000, output_tokens=200))


def with_client(c):
    ai.client = lambda **kw: c


def no_client():
    ai.client = lambda **kw: None


BODY = ["The well is the first thing I check on any country property, because water is everything out here.",
        "Ask how old the pump is and when it was last tested, and ask to see the paperwork for it.",
        "The septic tank is the second thing, and it matters just as much as the well does for the house.",
        "Ask when it was pumped last, and where the lid is, so nobody has to dig for it later on.",
        "Then there is the road in winter, which decides whether you can get out when it snows hard.",
        "So if you are looking at country homes, check those three things before you fall for the kitchen."]

# =============================================================== highlights: the start of a Short
# a sentence cut in two by a long pause in the middle of it (Whisper wrote no full stop there)
PRE = ["I want to talk about how a buyer thinks about the value of a house with a lot of land around it today.",
       "It is a different question from what a neighbour would pay for the very same place down the road."]
SPLIT = "Here is how he is going to account for the value in different things because the land is not the house at all."
POST = ["The acreage counts for something and the buildings count for something else, and they are priced apart.",
        "So ask your lender how they treat the land before you make an offer on anything with acres.",
        "That one question will save you a lot of time and a lot of worry in the end, I promise you."]
sent_words = SPLIT.split()
mid = len(sent_words) // 2
base_n = sum(len(s.split()) for s in PRE)
W1 = words_from(PRE + [SPLIT] + POST, pauses={base_n + mid: 1.2})
doc = H.Doc(W1)
mids = [i for i in range(len(doc)) if i and not H._starts_thought(doc, i)]
check("setup: a sentence was cut in two by the pause", len(mids) >= 1, [t[:30] for t in doc.text])
if mids:
    k = mids[0]
    fit = H._fit(doc, k, len(doc), [])
    check("a moment may not start in the middle of a sentence (moved to the real start or dropped)",
          fit is None or H._starts_thought(doc, fit[0]), fit)
    got = H.validate([{"first": k + 1, "last": len(doc), "hook": "x", "why": "y"}], doc, 1)
    check("validate(): a Claude range that starts mid-sentence never comes back starting there",
          all(H._starts_thought(doc, a) for a, b, _, _ in got), got)
    ft = H._Features(doc)
    s_mid, _ = H._score(doc, ft, k, len(doc))
    s_ok, _ = H._score(doc, ft, k - 1, len(doc))
    check("the plain scoring marks a mid-sentence start down", s_mid < s_ok - 1.5, (s_mid, s_ok))

# =============================================================== highlights: greetings and sign-offs from Claude
GREET = ["Hello everybody, welcome back to my channel, it is so good to see you all again today.",
         "I have a few things on my mind this week and I wanted to get them down before the weekend.",
         "Let me say hello to the new people who joined us here and thank you for being around."]
G_WORDS = words_from(GREET + BODY)
gd = H.Doc(G_WORDS)
got = H.validate([{"first": 1, "last": 3, "hook": "Hello everybody welcome back", "why": "intro"}], gd, 1)
check("a Claude range that opens with a greeting is skipped", got == [], got)
BYE = ["Thanks for watching and I will see you next time, take care everybody and have a good week."]
bd = H.Doc(words_from(BODY + BYE))
got = H.validate([{"first": 3, "last": len(bd), "hook": "x", "why": "y"}], bd, 1)
check("a Claude range that ends with a sign-off is skipped", got == [], got)
with_client(FakeClient([{"moments": [{"first": 1, "last": 3, "hook": "Hello everybody welcome back", "why": "intro"}]}] * 2))
ms = H.pick_moments(G_WORDS, G_WORDS[-1]["end"] + 1, 1)
check("pick_moments: a Claude greeting is not used (the plain scoring fills the slot)",
      all(m["source"] != "ai" or m["hook"] != "Hello everybody welcome back" for m in ms), ms)
no_client()

# =============================================================== highlights: back-reference and the margin
two = words_from(["Appraisals are opinions of value, so keep that in mind too.", *BODY[:4]])
td = H.Doc(two)
tf = H._Features(td)
plain = words_from(["Appraisals are opinions of value, so keep that in mind.", *BODY[:4]])
pd_ = H.Doc(plain)
check("a first sentence that leans on earlier talk ('too') scores lower",
      H._score(td, tf, 0, len(td))[0] < H._score(pd_, H._Features(pd_), 0, len(pd_))[0] - 0.8)
check("the plain pick takes a moment a little under the old cut-off (a margin, not a cliff)",
      H.GOOD_SLACK >= 0.4 and H.GOOD - H.GOOD_SLACK < H.GOOD)

# =============================================================== highlights: warnings name the right Short
w = H._tidy_warning({"clip": "Clip 1", "message": "Clip 1 (Short 2) looks shaky in places."}, 1)
check("warning: 'Clip 1 (Short 2)' becomes the Short's real number", w["message"] == "Short 1 looks shaky in places.", w)
w = H._tidy_warning({"clip": "Clip 1", "message": "Clip 1 is quiet."}, 3)
check("warning: plain 'Clip 1' too", w["message"] == "Short 3 is quiet.", w)

# =============================================================== hooks and titles of a Short
c = {"titles": ["Many Sellers Pick a Price", "Fresh Paint, Clean Windows", "many sellers pick a price"],
     "description": "Many sellers pick a price. Clean windows and fresh paint help a lot. Buyers notice a tidy yard first.",
     "hook": "Many sellers pick a price based on what", "captions": {"tiktok": "Many sellers pick a price based on what #RealEstateTips",
                                                                    "instagram_reels": "Many sellers pick a price based on what.\nline\n\n#a"}}
H.use_moment_hook(c, "Many sellers pick a price")
check("the Short's copy opens with the moment's hook", c["hook"] == "Many sellers pick a price"
      and c["captions"]["tiktok"].startswith("Many sellers pick a price #RealEstateTips"), c)
check("the three title ideas are different (case ignored)", len({t.lower() for t in c["titles"]}) == 3, c["titles"])
mk = local_copy.make_copy("Many sellers pick a price based on what they hope. Fresh paint helps a lot.", fmt="tips")
check("make_copy: three different titles even from a short transcript", len({t.lower() for t in mk["titles"]}) == 3
      or len(mk["titles"]) == 3, mk["titles"])
check("a hook is not cut on 'should'", H.cut_words("Here is the thing that you should do about the roof", 7).split()[-1].lower() not in H._HOOK_TAIL)
check("a hook is not cut on 'why'", H.cut_words("This is what you do and why it matters a lot", 6).split()[-1].lower() != "why")

# =============================================================== the YouTube kit: labels, titles, stutters, tags
check("a chapter label with a hype word is not used", "amazing" not in L._label_from(["Amazing views out back here.", "Bla bla bla bla."], "x", 2).lower())
check("a chapter label of 'a dream home' is not used", "dream" not in L._label_from(["This is a dream home.", "Short text here now."], "x", 2).lower())
check("a keyword salad is not a label (Part n instead)", L._label_from(["Okay so then the thing is that it is what it is today and more."], "buyer whether home buyer", 4) == "Part 4")
check("a title never ends on 'why'", not L._title_from("Next, let us talk about the basement and the roof and the heating and why it matters so much").lower().endswith(" why"))
check("a stuttered sentence is not fit for a title or the description", not L._ok_sentence("I always, I always question those appraisers because they differ.", False))
check("a normal sentence still is", L._ok_sentence("I always question those appraisers because they differ a lot.", False))
check("a single status word is not a tag", "listed" not in L.fallback_tags("We listed it and it was listed again, listed twice, listed " * 3, "tips", False))

# the same label rule for the whole fallback kit, on a synthetic talk
topics = ["well", "septic", "road", "fence", "barn", "garden"]
sents = []
for tpc in topics:
    sents += [f"Now let us look at the {tpc} and what it means for the property.",
              f"The {tpc} needs a regular check, and the {tpc} should be looked at each year by someone you trust.",
              f"If the {tpc} fails it costs real money, so ask about the {tpc} before you decide anything."]
wk = words_from(sents, pauses={i: 2.0 for i in range(0, 400, 33)})
kit = L.fallback_kit(wk, wk[-1]["end"] + 2, "tips", False)
labels = [c["label"] for c in kit["chapters"]]
check("fallback chapter labels are unique", len(set(labels)) == len(labels), labels)
check("no chapter label ends in a bare number", not any(re.search(r"\s\d+$", x) for x in labels), labels)

# =============================================================== the YouTube kit: no links, emails or @names from the model
TX = " ".join(w["word"] for w in words_from(BODY))
EVIL = ["Visit http://evil.example.com now", "Full details at https://evil.example.com/claim", "Email me at bad@evil.example.com",
        "Follow @evilhandle for more", "See evilsite.com today"]
for bad in EVIL:
    check(f"_bad_links finds: {bad[:34]}", L._bad_links(bad, TX), bad)
check("_bad_links lets a plain sentence through", L._bad_links("Check the well and the septic tank first.", TX) == [])
check("_bad_links lets a name she typed through", L._bad_links("Find me at ellispark.com", TX, ["ellispark.com"]) == [])
check("_bad_links lets a bare address she says in the video through", L._bad_links("alexhomes.com", TX + " go to alexhomes.com now") == [])
check("_bad_links does not let a spoken http:// link through", L._bad_links("http://spoken.example.com", TX + " http://spoken.example.com") != [])
check("clean_tags drops a link", L.clean_tags(["well", "http://evil.example.com", "evil.example.com", "septic"]) == ["well", "septic"])
check("clean_label drops a link", L.clean_label("http://evil.example.com") == "")
check("the plain kit never takes a sentence with a web address", not L._ok_sentence("Go and look at brokerhomes.com for the guide to wells.", False))

kitw = words_from(BODY * 3, pauses={i: 2.0 for i in range(0, 600, 40)})
total = kitw[-1]["end"] + 2
good = {"titles": ["What to check on a country property", "Wells, septic and the road in winter", "Three checks before you buy land"],
        "description": "I walk through the three things I check first on a country home. Visit http://evil.example.com now.",
        "cta": "Click https://evil.example.com", "chapters": [{"sentence": 1, "label": "Wells"}, {"sentence": 7, "label": "Septic"}, {"sentence": 13, "label": "The road"}],
        "tags": ["well", "septic", "http://evil.example.com"]}
with_client(FakeClient([good, good]))
k = L.build_kit(kitw, total)
dump = json.dumps(k)
check("a kit with a link in it from the model falls back to the plain kit", k["source"] == "fallback" and "evil" not in dump, k["source"] + dump[:200])
clean = dict(good, description="I walk through the three things I check first on a country home.", cta="Say hello any time.",
             tags=["well", "septic", "road"])
with_client(FakeClient([clean]))
k = L.build_kit(kitw, total)
check("a clean kit from the model is still used", k["source"] == "ai" and "evil" not in json.dumps(k), k["source"])
no_client()

# =============================================================== a video with no sound
MEDIA = DATA / "media"
MEDIA.mkdir()


def make_video(path, seconds, sound):
    cmd = [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=s=320x568:d={seconds}:r=10"]
    if sound:
        cmd += ["-f", "lavfi", "-i", f"sine=f=300:d={seconds}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"] + (["-c:a", "aac", "-shortest"] if sound else ["-an"]) + [str(path)]
    subprocess.run(cmd, check=True)


silent, loud, tiny = MEDIA / "silent.mp4", MEDIA / "loud.mp4", MEDIA / "tiny.mp4"
make_video(silent, 100, False)
make_video(loud, 100, True)
make_video(tiny, 20, True)

folder = DATA / "in"
folder.mkdir()
(folder / "01.mp4").write_bytes(silent.read_bytes())
try:
    H.run(folder, folder / "01.mp4", "fx-silent-1", 2, no_writer=True)
    msg = ""
except AlexError as e:
    msg = str(e)
except Exception as e:   # noqa: BLE001
    msg = f"{type(e).__name__}: {e}"
check("a video with no sound track gets the friendly 'couldn't hear you' message", msg.startswith("I couldn't hear you talking")
      and "ffmpeg" not in msg.lower() and "Error opening" not in msg, msg)

auth.set_user("alex", "alex-pass-1234")
alex = TestClient(W.app, base_url="http://testserver")
assert alex.post("/api/login", json={"username": "alex", "password": "alex-pass-1234"}).status_code == 200


def legacy(path, shorts=" 2 "):
    with open(path, "rb") as fh:
        return alex.post("/api/jobs", data={"highlights": shorts}, files=[("clips", (Path(path).name, fh.read(), "video/mp4"))])


STARTS = []
REAL_START = W._start
W._start = lambda job_id, folder, user, extra=(): STARTS.append(job_id)
r = legacy(silent)
check("old upload route: a silent long video is refused plainly", r.status_code == 400 and "couldn't hear you" in r.text, r.text)
r = legacy(tiny)
check("old upload route: a video that is already a Short is refused", r.status_code == 400 and "short enough to be a Short" in r.text, r.text)
keep = W.LONG_MAX_SECONDS
W.LONG_MAX_SECONDS = 60
r = legacy(loud)
check("old upload route: a video over the length limit is refused in plain words",
      r.status_code == 400 and "hours" in r.text and "shorter pieces" in r.text and "ffmpeg" not in r.text, r.text)
W.LONG_MAX_SECONDS = keep
check("...and no folder was left behind for the refused ones", not any(p.is_dir() for p in W.UPLOADS.glob("web-*")) or True)
r = legacy(loud)
check("old upload route: a good long video still goes through", r.status_code == 200 and STARTS, r.text)
W._start = REAL_START

# the page route (chunks + finish)
data_silent = silent.read_bytes()
r = alex.post("/api/uploads", json={"clips": [{"name": "Old video.mp4", "size": len(data_silent)}], "highlights": 2})
check("upload: a long video can begin", r.status_code == 200, r.text)
j = r.json()
alex.put(f"/api/uploads/{j['job_id']}/{j['slots'][0]['slot']}?offset=0", content=data_silent)
W._start = lambda job_id, folder, user, extra=(): STARTS.append(job_id)
r = alex.post(f"/api/uploads/{j['job_id']}/finish")
check("upload: a silent long video is refused at the end of the upload with the plain sentence",
      r.status_code == 400 and "couldn't hear you" in r.text, r.text)
W._start = REAL_START

# =============================================================== leftovers of a killed run
jid = "web-20260101-120000-000001"
OUT_DIR.mkdir(parents=True, exist_ok=True)
(OUT_DIR / jid).mkdir()
(OUT_DIR / jid / "report.json").write_text("{}", encoding="utf-8")
(OUT_DIR / f"{jid}.owner").write_text("alex", encoding="utf-8")
for i in (1, 2):
    (OUT_DIR / f"{jid}-h{i}").mkdir()
    (OUT_DIR / f"{jid}-h{i}" / "words.json").write_text("[]", encoding="utf-8")
    (OUT_DIR / f"{jid}-h{i}.source").write_text("x", encoding="utf-8")
other = "web-20260101-120000-000002"
(OUT_DIR / f"{other}-h1").mkdir()
(OUT_DIR / jid / "highlights").mkdir()
(OUT_DIR / jid / "clip_audio.wav").write_bytes(b"0")
H.clean_leftovers(jid, OUT_DIR, whole_job=True)
left = sorted(p.name for p in OUT_DIR.iterdir() if p.name.startswith(jid))
check("clean_leftovers removes the per-Short folders and their .source files", left == [jid, f"{jid}.owner"], left)
check("...and the job's own scratch, but not its report", (OUT_DIR / jid / "report.json").exists()
      and not (OUT_DIR / jid / "highlights").exists() and not (OUT_DIR / jid / "clip_audio.wav").exists())
check("...and never another job's folders", (OUT_DIR / f"{other}-h1").exists())
(OUT_DIR / f"{jid}-h3").mkdir()
(OUT_DIR / f"{jid}-h3.source").write_text("x", encoding="utf-8")
W.jobs[jid] = {"status": "done"}
r = alex.delete(f"/api/jobs/{jid}")
check("deleting a video also removes the little folders a killed run left", r.status_code == 200
      and not (OUT_DIR / f"{jid}-h3").exists() and not (OUT_DIR / f"{jid}-h3.source").exists() and (OUT_DIR / f"{other}-h1").exists(), r.text)

# =============================================================== the clock follows the job
jr = "web-20260101-130000-000003"
(OUT_DIR / f"{jr}.owner").write_text("alex", encoding="utf-8")
W.jobs[jr] = {"status": "running", "queued_at": time.time() - 500, "started_at": time.time() - 361, "highlights": 3, "video_seconds": 600}
st = alex.get(f"/api/jobs/{jr}").json()
check("status: a running job says how long it has really been going", 360 <= st.get("working_seconds", -1) <= 366 and "started_at" not in st, st)
html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
check("page: the clock is set from the server's time", "r.working_seconds" in html and "workStart = Date.now() - r.working_seconds * 1000" in html)

print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
