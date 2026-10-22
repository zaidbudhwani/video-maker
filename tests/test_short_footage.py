"""Tests for match.short_footage: "you talk about it for longer than your footage of it lasts".

Run: venv/Scripts/python tests/test_short_footage.py
Pure data tests plus one check on the saved house tour (skipped when this computer has no samples/tour or out/ jobs).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from video_tool import match  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {str(detail)[:300]}"))
    if not cond:
        FAILS.append(name)


def scores(rows):
    """rows[clip] = list of per-phrase best scores; one frame per clip is enough for these tests."""
    return [[list(r)] for r in rows]


TEXTS = ["Come inside.", "Here is the large deck,", "perfect for grilling,", "and quiet evenings.", "Call me today."]
TIMES = [[0, 2], [2, 5], [5, 7], [7, 10], [10, 12]]

# the deck clip is 4 s but she talks about the deck for 8 s (phrases 1-3)
S = scores([[9, 1, 1, 1, 1],
            [1, 9, 9, 9, 1],
            [1, 1, 1, 1, 9]])
got = match.short_footage(S, TIMES, TEXTS, [3.0, 4.0, 3.0], ["Entry.mov", "Deck.mov", "Call.mov"])
check("one note for the deck", len(got) == 1, got)
n = got[0] if got else {}
check("it starts where she starts the deck", n.get("at") == 2.0, n)
check("needs 8 s, has 4 s, add 4 s", (n.get("need"), n.get("have"), n.get("add")) == (8.0, 4.0, 4), n)
check("names the clip in her words", n.get("clip") == "Deck.mov", n)
check("says what she says", "large deck" in n.get("said", "") and "quiet evenings" in n.get("said", ""), n)

# enough footage: no note
check("no note when the clip is long enough", match.short_footage(S, TIMES, TEXTS, [3.0, 8.5, 3.0]) == [])
# within the slack
check("a few tenths over is not worth a note", match.short_footage(S, TIMES, TEXTS, [3.0, 7.5, 3.0]) == [])
# two clips show the deck together: their lengths add up
check("two deck clips together cover it",
      match.short_footage(scores([[9, 1, 1, 1, 1], [1, 9, 9, 9, 1], [1, 9, 9, 9, 1]]), TIMES, TEXTS, [3.0, 4.0, 4.5]) == [])
# nobody shows anything: that is the "you mention things I couldn't show" panel, not this one
check("phrases no clip shows are left to the other panel",
      match.short_footage(scores([[1, 1, 1, 1, 1]]), TIMES, TEXTS, [5.0]) == [])
# a short bit of narration is ignored
check("narration under 2 s is ignored",
      match.short_footage(scores([[9, 1, 1, 1, 1]]), [[0, 1.5], [1.5, 3], [3, 4], [4, 5], [5, 6]], TEXTS, [0.2]) == [])
# bad input never raises
check("empty and mismatched input give nothing",
      match.short_footage([], TIMES, TEXTS, []) == [] and match.short_footage(S, TIMES[:2], TEXTS, [1, 2, 3]) == []
      and match.short_footage(S, TIMES, TEXTS, [1, 2]) == [])
check("None rows from a failed frame are skipped, not fatal",
      isinstance(match.short_footage([[None], [[9, 9, 9, 9, 9]]], TIMES, TEXTS, [1.0, 1.0]), list))
# at most three notes, biggest shortfall first picked, shown in time order
many_t = [[i * 4, i * 4 + 4] for i in range(5)]
many_s = scores([[9 if j == i else 1 for j in range(5)] for i in range(5)])
out = match.short_footage(many_s, many_t, ["a b c d e"] * 5, [1.0, 2.0, 3.0, 0.5, 1.5], limit=3)
check("limit of three, in time order", len(out) == 3 and [o["at"] for o in out] == sorted(o["at"] for o in out), out)
check("the three biggest shortfalls were kept", {o["at"] for o in out} == {0.0, 12.0, 16.0}, out)

# ---------- the real house tour: only the single deck clip is too short ----------
JOB = ROOT / "out" / "engine-A-claude"
TOUR = ROOT / "samples" / "tour"
if (JOB / "match" / "scores.json").exists() and TOUR.exists():
    from video_tool import ff
    data = json.loads((JOB / "match" / "scores.json").read_text(encoding="utf-8"))
    usable = [round(ff.probe(TOUR / f"{i + 1:02d}.mp4")["duration"], 2) for i in range(len(data["scores"]))]
    names = [f"{i + 1:02d}.mp4" for i in range(len(data["scores"]))]
    real = match.short_footage(data["scores"], data["phrase_times"], data["phrases"], usable, names)
    print("   real tour notes:", json.dumps(real))
    check("real tour: a note about the deck, starting at about 24 s", any(23 <= r["at"] <= 30 for r in real), real)
    deck = next((r for r in real if 23 <= r["at"] <= 30), {})
    check("real tour: she talks about it for 7-9 s, the clip is 5 s, so add about 3", 7 <= deck.get("need", 0) <= 9 and 4.5 <= deck.get("have", 0) <= 5.5 and deck.get("add") in (3, 4) and deck.get("clip") == "10.mp4", deck)
    check("real tour: at most three notes", len(real) <= 3, real)
else:
    print("SKIP real tour check: out/engine-A-claude or samples/tour not on this computer")


# ---------- pipeline.short_footage_report: reads the saved scores file, quiet when anything is missing ----------
import tempfile  # noqa: E402

from video_tool import pipeline  # noqa: E402

WORDS = []
t = 0.0
for text, (a, b) in zip(TEXTS, TIMES):
    parts = text.split()
    step = (b - a) / len(parts)
    for k, w in enumerate(parts):
        WORDS.append({"word": w, "start": a + k * step, "end": a + (k + 1) * step})
phr = match.phrases(WORDS, 12.0)
with tempfile.TemporaryDirectory() as td:
    job = Path(td)
    (job / "match").mkdir()
    n_ph = len(phr)
    sc = [[[9 if j == 0 else 1 for j in range(n_ph)]], [[9 if 1 <= j <= 3 else 1 for j in range(n_ph)]], [[9 if j == n_ph - 1 else 1 for j in range(n_ph)]]]
    data = {"phrases": [p["text"] for p in phr], "scores": sc, "usable": [3.0, 3.0, 3.0], "names": ["a.mov", "b.mov", "c.mov"]}
    (job / "match" / "scores.json").write_text(json.dumps(data), encoding="utf-8")
    rep = pipeline.short_footage_report(job, WORDS, 12.0)
    check("report: finds the short clip from the saved file", len(rep) == 1 and rep[0]["clip"] == "b.mov" and rep[0]["add"] >= 1, rep)
    data.pop("usable")
    (job / "match" / "scores.json").write_text(json.dumps(data), encoding="utf-8")
    check("report: an older scores file without clip lengths gives nothing", pipeline.short_footage_report(job, WORDS, 12.0) == [])
    (job / "match" / "scores.json").write_text("not json", encoding="utf-8")
    check("report: a broken file gives nothing, no crash", pipeline.short_footage_report(job, WORDS, 12.0) == [])
    check("report: no file at all gives nothing", pipeline.short_footage_report(job / "nowhere", WORDS, 12.0) == [])

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("ALL PASS")
