"""The clip-matching accuracy figures keep four things apart: coverage, accuracy when supported, accuracy across all narration,
and contradictions. Run: venv\Scripts\python tests\test_matching_accuracy.py
Synthetic phrases, clips and key (no footage, no AI): this checks the DEFINITIONS in tests/matching_accuracy_report.py, and
that on the real saved tour (when present) the four figures are consistent with each other."""
import os
import sys
from pathlib import Path

os.environ["ANTHROPIC_API_KEY"] = ""
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import matching_accuracy_report as R   # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {detail}"))
    if not cond:
        FAILS.append(name)


def near(a, b):
    return abs(a - b) < 0.02


# 4 phrases of 5 s each (20 s). Clip 0 illustrates phrase 0 (and contradicts 3); clip 1 illustrates phrase 1; nothing illustrates
# phrases 2 and 3, so only half the narration is "supported".
ENDS = [5.0, 10.0, 15.0, 20.0]
ILL = [{0}, {1}]
CON = [{3}, {2}]
m = R.measure([0, 1], [10.0, 10.0], 20.0, ENDS, ILL, CON)
check("coverage is the share of narration some clip can illustrate (10 of 20 s = 50%)", near(m["coverage"], 0.5), m["coverage"])
check("accuracy when supported: clip 0 is right for phrase 0 and wrong for phrase 1 (5 of 10 s = 50%)", near(m["acc_supported"], 0.5), m["acc_supported"])
check("accuracy across ALL narration is coverage x accuracy when supported (5 of 20 s = 25%)", near(m["acc_all"], 0.25) and near(m["acc_all"], m["coverage"] * m["acc_supported"]), m)
check("a contradiction is counted where the shown clip is one the key says contradicts (clip 1 during phrase 2: 5 s of 20)",
      near(m["contradict_all"], 0.25), m["contradict_all"])
check("...and that one is in an unsupported phrase, so it is not in the supported figure", m["contradict_supported"] == 0, m["contradict_supported"])
check("unsupported phrases are listed (2 and 3, 0-based)", m["unsupported_phrases"] == [2, 3], m["unsupported_phrases"])

perfect = R.measure([0, 1], [5.0, 5.0], 10.0, ENDS[:2], ILL, CON)
check("a perfect plan on fully supported narration scores 100% on every accuracy figure", perfect["coverage"] > 0.99 and perfect["acc_supported"] > 0.99 and perfect["acc_all"] > 0.99, perfect)
none_ill = R.measure([0], [10.0], 10.0, ENDS[:2], [set()], [set()])
check("narration nothing can show has 0% coverage and no accuracy figure to quote", none_ill["coverage"] == 0 and none_ill["acc_supported"] is None, none_ill)

# the saved tour, when it is here: the figures must agree with each other and with the earlier headline
job = ROOT / "out" / "engine-A-claude"
key = R.DEFAULT_KEY
if job.joinpath("timeline.json").exists() and key.exists():
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        R.main([])
    out = buf.getvalue()
    check("saved tour: accuracy across all narration = coverage x accuracy when supported", "= accuracy across all narration" in out
          and out.count("47.7%") >= 2, out[-400:])
    check("saved tour: the earlier 82% headline is reproduced as accuracy during SUPPORTED narration only", "81.7%" in out and "58.4%" in out, out[:600])
else:
    print("SKIP the saved-tour check: out/engine-A-claude or the answer key is not here")

print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED'}")
sys.exit(1 if FAILS else 0)
