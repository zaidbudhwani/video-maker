"""The "Shorter pauses" button (pause_gap 0.7 -> 0.45) must make pauses shorter, never repeat a bit of sound.
Before the fix, a pause between the setting and 0.5 s was kept as two overlapping pieces (0.25 s each side)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ALEX_PREFS_FILE"] = str(ROOT / "tests" / "_no_such_prefs.json")   # built-in defaults, never her prefs
from video_tool import settings, silence  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def words(gaps):
    out, t = [], 0.0
    for i, g in enumerate([0.0, *gaps]):
        t += g
        out.append({"word": f"w{i}", "start": round(t, 3), "end": round(t + 0.3, 3)})
        t += 0.3
    return out


def kept_length(segs):
    return sum(b - a for a, b in segs)


ws = words([0.47, 0.6, 0.9, 0.2])
total = ws[-1]["end"] + 1
lengths = {}
for gap in (0.7, 0.45, 0.4):
    settings.configure({"pause_gap": gap})
    segs = silence.keep_segments(ws, total)
    lengths[gap] = kept_length(segs)
    check(f"pause_gap {gap}: no piece overlaps the next", all(b[0] >= a[1] for a, b in zip(segs, segs[1:])), segs)
    for a, b in zip(segs, segs[1:]):
        check(f"pause_gap {gap}: each cut removes some silence", b[0] - a[1] > 0, f"{a[1]}..{b[0]}")
check("shorter setting -> shorter video", lengths[0.7] > lengths[0.45] >= lengths[0.4], lengths)
settings.configure({"pause_gap": 0.7})
segs = silence.keep_segments(ws, total)
# the 0.9 s pause keeps 0.25 s on each side, so 0.4 s of it is removed (same as before the fix)
check("default 0.7 still leaves 0.25 s each side of a cut (unchanged)",
      len(segs) == 2 and abs((segs[1][0] - segs[0][1]) - 0.4) < 1e-6, segs)
print(f"{len(fails)} failed" if fails else "all pause-gap checks passed")
sys.exit(1 if fails else 0)
