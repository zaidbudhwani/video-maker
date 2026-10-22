r"""Recompute the clip-matching accuracy figures from the SAVED finished-video timeline, with the four numbers kept apart.

    venv\Scripts\python tests\matching_accuracy_report.py [--job out\engine-A-claude] [--clips samples\tour] [--key "<answer key json>"]

Offline and read-only: it reads the saved timeline, the saved words and the answer key. No model is called, nothing is sent,
no picture is opened. The answer key is a MODEL's labelling of one house tour (11 clips, 19 phrases), not a person's.

Why four numbers: the earlier "82% of the video shows what she says" divided by the time of the phrases that at least one
clip can illustrate (the "supported" phrases) and left the rest out. That is a fair number for "when a suitable clip exists,
does the planner put it on screen?", but it is not "how much of the narration is shown properly". So:

  1. Footage coverage       = narration time in supported phrases / all narration time        (a filming question)
  2. Accuracy when supported = time the shown clip illustrates the phrase / supported narration time  (the old headline)
  3. Accuracy across ALL narration = time the shown clip illustrates the phrase / all narration time
                              (= coverage x accuracy when supported: an unsupported phrase can never be shown properly)
  4. Contradictions         = time the shown clip is one the key says CONTRADICTS the phrase, over supported narration and
                              over all narration (a clip of the wrong room, not just an unrelated one)

Limits that stay attached to every number: one tour; a model-made key; no independent held-out houses (a phrase split inside
this tour does not show it works on a new house); a 0.05 s grid over the finished video's timeline.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["OPENROUTER_API_KEY"] = ""
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

DEFAULT_KEY = Path("answer-key.json")
DT = 0.05


def load_key(path):
    j = json.loads(Path(path).read_text(encoding="utf-8"))["jobs"]["demo-tour-moved"]
    n = len(j["clips"])
    ill, con = [set() for _ in range(n)], [set() for _ in range(n)]
    for c in j["clips"]:
        ill[c["clip"] - 1], con[c["clip"] - 1] = set(c["illustrates"]), set(c["contradicts"])
    return ill, con, j["phrases"]


def measure(order, slots, length, phrase_ends, ill, con):
    """The four numbers for one timeline (clip index per slot, slot lengths). Seconds on a DT grid."""
    P = len(phrase_ends)
    supported = {p for p in range(P) if any(p in s for s in ill)}
    starts, a = [], 0.0
    for l in slots:
        starts.append(a)
        a += l
    grid = np.arange(0, length, DT)
    ph = np.searchsorted(phrase_ends, grid, side="right").clip(0, P - 1)
    ci = (np.searchsorted(np.array(starts), grid, side="right") - 1).clip(0, len(order) - 1)
    total = len(grid)
    sup = ok_sup = bad_sup = ok_all = bad_all = 0
    for p, k in zip(ph, ci):
        c = order[k]
        is_ok, is_bad = p in ill[c], p in con[c]
        ok_all += is_ok
        bad_all += is_bad
        if p in supported:
            sup += 1
            ok_sup += is_ok
            bad_sup += is_bad
    return {"narration_s": total * DT, "supported_s": sup * DT, "coverage": sup / total,
            "acc_supported": ok_sup / sup if sup else None, "acc_all": ok_all / total,
            "contradict_supported": bad_sup / sup if sup else None, "contradict_all": bad_all / total,
            "supported_phrases": sorted(supported), "unsupported_phrases": [p for p in range(P) if p not in supported]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", default=str(ROOT / "out" / "engine-A-claude"))
    ap.add_argument("--clips", default=str(ROOT / "samples" / "tour"))
    ap.add_argument("--key", default=str(DEFAULT_KEY))
    ap.add_argument("--json", help="also save the numbers here")
    a = ap.parse_args(argv)
    import compare_match_engines as cme
    job = Path(a.job)
    if not (job / "timeline.json").exists() or not Path(a.key).exists():
        print("SKIP: needs the saved job (out/engine-A-claude with timeline.json) and the answer key.")
        return 0
    jd = cme.load_job(job, a.clips)
    ill, con, key_phrases = load_key(a.key)
    pt = [(p["start"], p["end"]) for p in jd["phr"]]
    ends = [e for _, e in pt]
    names = [Path(c[0]).name for c in jd["checked"]]
    tl = json.loads((job / "timeline.json").read_text(encoding="utf-8"))
    idx = {n: i for i, n in enumerate(names)}
    order = [idx[t["clip"]] for t in tl]
    slots = [t["end"] - t["start"] for t in tl]
    length = jd["target"]
    n_clips = len(jd["checked"])
    results = {"Claude, finished video (saved timeline)": measure(order, slots, length, ends, ill, con),
               "Her own order, even split": measure(list(range(n_clips)), jd["even"], length, ends, ill, con)}

    r0 = next(iter(results.values()))
    print(f"Job {job.name}: {n_clips} clips, {len(pt)} phrases, narration {r0['narration_s']:.1f} s on a {DT} s grid.")
    print(f"Phrases (1-based) some clip can illustrate: {[p + 1 for p in r0['supported_phrases']]}")
    print(f"Phrases NO clip illustrates (per the key):  {[p + 1 for p in r0['unsupported_phrases']]}\n")
    head = f"{'':42}{'coverage':>10}{'acc|supported':>15}{'acc|ALL':>10}{'contradict|sup':>16}{'contradict|ALL':>16}"
    print(head)
    for name, r in results.items():
        print(f"{name:42}{r['coverage']:>10.1%}{r['acc_supported']:>15.1%}{r['acc_all']:>10.1%}"
              f"{r['contradict_supported']:>16.1%}{r['contradict_all']:>16.1%}")
    print(f"\nSupported narration: {r0['supported_s']:.1f} s of {r0['narration_s']:.1f} s "
          f"({r0['coverage']:.1%} covered, {1 - r0['coverage']:.1%} unsupported = {r0['narration_s'] - r0['supported_s']:.1f} s).")
    c = results["Claude, finished video (saved timeline)"]
    print(f"Check: coverage x accuracy-when-supported = {c['coverage'] * c['acc_supported']:.1%} = accuracy across all narration {c['acc_all']:.1%}.")
    print("\nLimits: one tour, a model-made answer key, no independent held-out houses (splitting phrases inside this tour does not\n"
          "show it works on a new house). 'Supported' is the key's judgement of what the filmed clips can show.")
    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
