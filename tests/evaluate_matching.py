r"""Measure clip matching honestly over MANY finished jobs and person-made answer keys.

    venv\Scripts\python tests\evaluate_matching.py out\house-1 out\house-2 out\house-3 [--keys DIR] [--csv results.csv]
        [--holdout "house-3"] [--boot 2000] [--seed 0]

Offline and read-only: it reads each job's saved timeline (timeline.json), its saved words and the answer keys
(answer_key.<rater>.json next to the job, made with tests\answer_key_tool.py). No model is called and nothing is sent.

For every job, every rater's key and every timeline it reports the four figures kept apart (see
tests\matching_accuracy_report.py for why there are four):
  1. footage coverage             narration time some clip can illustrate / all narration time
  2. accuracy when supported      time the shown clip illustrates the phrase / supported narration time
  3. accuracy across ALL narration time the shown clip illustrates the phrase / all narration time
  4. contradictions               time the shown clip is one the key says CONTRADICTS the phrase (supported / all)
Timelines compared: "finished" (the saved timeline.json: Claude's plan when the job ran with a key, her own order when it
did not), "her_order" (her clips in the order she filmed them, the even split the tool uses without Claude), and any saved
alternative (timeline.<name>.json, or alternatives\<name>.json, same shape as timeline.json).

Pooled figures add up seconds over the jobs (a long tour counts more than a short one). The 95% interval is a bootstrap over
HOUSES (houses drawn with replacement, fixed seed), because clips and phrases inside one house are not independent. With two
raters on the same job it also prints their agreement: percent of (clip, phrase) pairs labelled the same and Cohen's kappa.
Held-out houses (--holdout, or a fixed one-in-three pick by name) are reported apart so a change tuned on some houses can be
checked on houses it never saw.

    venv\Scripts\python tests\evaluate_matching.py --import-legacy "<old model-made key json>" --job out\engine-A-claude
        --clips samples\tour --out-dir <folder>
converts the earlier one-tour MODEL-made key into this format (rater "model"), so that tour can go through the same runner.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["OPENROUTER_API_KEY"] = ""
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import matching_accuracy_report as R   # noqa: E402

KIND = "alex-answer-key"
FIGS = ("coverage", "acc_supported", "acc_all", "contradict_supported", "contradict_all")
FIG_NAMES = {"coverage": "footage coverage", "acc_supported": "accuracy when supported",
             "acc_all": "accuracy across all narration", "contradict_supported": "contradictions (supported)",
             "contradict_all": "contradictions (all)"}
# numerator seconds, denominator seconds for each figure (so pooling adds seconds, not percentages)
PARTS = {"coverage": ("supported_s", "narration_s"), "acc_supported": ("ok_sup_s", "supported_s"),
         "acc_all": ("ok_all_s", "narration_s"), "contradict_supported": ("bad_sup_s", "supported_s"),
         "contradict_all": ("bad_all_s", "narration_s")}
SAFE_RATER = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


# ---------------------------------------------------------------- what a job offers to be marked
def job_words(job):
    job = Path(job)
    wp = next((job / n for n in ("words_trimmed.json", "words.json") if (job / n).exists()), None)
    return json.loads(wp.read_text(encoding="utf-8")) if wp else None


def job_clips_dir(job, clips_dir=None):
    job = Path(job)
    src = clips_dir
    if not src and (job.parent / (job.name + ".source")).exists():
        src = (job.parent / (job.name + ".source")).read_text(encoding="utf-8").strip()
    if src and Path(src).is_dir() and list(Path(src).glob("*.mp4")):
        return Path(src)
    if (job / "normalized").is_dir():
        return job / "normalized"
    return None


def snapshot(job, clips_dir=None, probe=True):
    """Phrases and clips of one finished job, as a person marks them. Raises ValueError with a plain sentence."""
    from video_tool import match
    job = Path(job)
    words = job_words(job)
    if not words:
        raise ValueError("This video has no saved words, so there are no phrases to mark.")
    report = json.loads((job / "report.json").read_text(encoding="utf-8")) if (job / "report.json").exists() else {}
    length = float(report.get("length") or words[-1]["end"])
    phr = [{"start": round(p["start"], 3), "end": round(p["end"], 3), "text": p["text"]} for p in match.phrases(words, length)]
    src = job_clips_dir(job, clips_dir)
    if src is None:
        raise ValueError("The clips of this video were not found.")
    skips = {}
    try:
        for t in json.loads((job / "timeline.json").read_text(encoding="utf-8")):
            skips[t["clip"]] = float(t.get("skipped_start") or 0)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    clips = []
    for c in sorted(src.glob("*.mp4")):
        usable = None
        if probe:
            try:
                from video_tool import assemble
                usable = round(assemble.check_clip(c)[0] - skips.get(c.name, 0.0), 3)
            except Exception:   # a clip that cannot be read still gets marked; her-order figures then need its length
                usable = None
        clips.append({"name": c.name, "usable": usable})
    return {"job": job.name, "length": length, "phrases": phr, "clips": clips, "clips_dir": str(src)}


def new_key(snap, rater, house=None, made_by="person"):
    return {"kind": KIND, "version": 1, "job": snap["job"], "house": house or snap["job"], "rater": rater,
            "made_by": made_by, "saved": datetime.now().isoformat(timespec="seconds"), "length": snap["length"],
            "phrases": snap["phrases"], "clips": [{"name": c["name"], "usable": c["usable"]} for c in snap["clips"]],
            "marks": {c["name"]: {"illustrates": [], "contradicts": []} for c in snap["clips"]}}


def key_path(folder, rater):
    if not SAFE_RATER.match(rater or ""):
        raise ValueError("A rater name is letters, numbers, - or _ only (up to 32).")
    return Path(folder) / f"answer_key.{rater}.json"


def check_key(k):
    """Raise ValueError if the key is not usable; return it with marks cleaned (sorted unique ints, no overlap)."""
    if k.get("kind") != KIND:
        raise ValueError("Not an answer key made by this tool.")
    P = len(k["phrases"])
    names = {c["name"] for c in k["clips"]}
    for name, m in k["marks"].items():
        if name not in names:
            raise ValueError(f"The key marks a clip that is not in the video: {name}")
        ill = sorted({int(p) for p in m.get("illustrates", [])})
        con = sorted({int(p) for p in m.get("contradicts", [])})
        if any(p < 0 or p >= P for p in ill + con):
            raise ValueError(f"The key marks a phrase number that does not exist for {name}.")
        if set(ill) & set(con):
            raise ValueError(f"{name} is marked both 'shows it' and 'contradicts' for the same phrase.")
        m["illustrates"], m["contradicts"] = ill, con
    return k


def load_key(path):
    return check_key(json.loads(Path(path).read_text(encoding="utf-8")))


# ---------------------------------------------------------------- timelines to score
def her_order_slots(usable, length, words):
    """The tool's no-Claude plan: her clips in filmed order, cut at sentence breaks (assemble.plan_slots); when that
    cannot fit, play in order (each clip until it runs out)."""
    from video_tool import assemble
    slots = assemble.plan_slots(usable, length, words or [{"word": "x.", "start": 0, "end": length}]) if len(usable) else None
    if slots is None:
        slots, left = [], length
        for u in usable:
            s = min(u, left)
            slots.append(s)
            left -= s
        if left > 0 and slots:
            slots[-1] += left
    return slots


def read_timeline(path, names):
    tl = json.loads(Path(path).read_text(encoding="utf-8"))
    idx = {n: i for i, n in enumerate(names)}
    missing = [t["clip"] for t in tl if t["clip"] not in idx]
    if missing:
        raise ValueError(f"{Path(path).name} uses clips the key does not have: {missing}")
    return [idx[t["clip"]] for t in tl], [t["end"] - t["start"] for t in tl]


def timelines(job, key):
    job = Path(job)
    names = [c["name"] for c in key["clips"]]
    out, notes = {}, []
    if (job / "timeline.json").exists():
        out["finished"] = read_timeline(job / "timeline.json", names)
    usable = [c.get("usable") for c in key["clips"]]
    if all(u is not None for u in usable):
        out["her_order"] = (list(range(len(names))), her_order_slots(usable, key["length"], job_words(job)))
    else:
        notes.append("her own order was not scored: the key does not have every clip's length")
    alts = sorted(job.glob("timeline.*.json")) + sorted((job / "alternatives").glob("*.json"))
    for p in alts:
        name = p.stem.split(".", 1)[1] if p.name.startswith("timeline.") else p.stem
        try:
            out["alt:" + name] = read_timeline(p, names)
        except (ValueError, KeyError, TypeError) as e:
            notes.append(f"alternative {p.name} skipped: {e}")
    return out, notes


# ---------------------------------------------------------------- the four figures, in seconds
def seconds(key, order, slots):
    names = [c["name"] for c in key["clips"]]
    ill = [set(key["marks"].get(n, {}).get("illustrates", [])) for n in names]
    con = [set(key["marks"].get(n, {}).get("contradicts", [])) for n in names]
    ends = [p["end"] for p in key["phrases"]]
    m = R.measure(order, slots, key["length"], ends, ill, con)
    n, s = m["narration_s"], m["supported_s"]
    return {"narration_s": n, "supported_s": s, "ok_all_s": m["acc_all"] * n, "bad_all_s": m["contradict_all"] * n,
            "ok_sup_s": (m["acc_supported"] or 0) * s, "bad_sup_s": (m["contradict_supported"] or 0) * s}


def ratios(sec):
    return {f: (sec[a] / sec[b] if sec[b] > 0 else None) for f, (a, b) in PARTS.items()}


def add(secs):
    tot = {k: 0.0 for k in ("narration_s", "supported_s", "ok_all_s", "bad_all_s", "ok_sup_s", "bad_sup_s")}
    for s in secs:
        for k in tot:
            tot[k] += s[k]
    return tot


def bootstrap(by_house, n=2000, seed=0, level=0.95):
    """by_house: {house: seconds dict}. Draw houses with replacement; return {figure: (low, high)} (None if undefined)."""
    houses = sorted(by_house)
    rng = np.random.default_rng(seed)
    draws = {f: [] for f in FIGS}
    for _ in range(n):
        pick = rng.integers(0, len(houses), len(houses))
        r = ratios(add(by_house[houses[i]] for i in pick))
        for f in FIGS:
            if r[f] is not None:
                draws[f].append(r[f])
    lo, hi = (1 - level) / 2 * 100, (1 + level) / 2 * 100
    return {f: ((float(np.percentile(v, lo)), float(np.percentile(v, hi))) if v else None) for f, v in draws.items()}


def bootstrap_diff(by_house_a, by_house_b, fig="acc_all", n=2000, seed=0, level=0.95):
    """Interval for (timeline A minus timeline B) on one figure, drawing the SAME houses for both."""
    houses = sorted(set(by_house_a) & set(by_house_b))
    if not houses:
        return None
    rng = np.random.default_rng(seed)
    a, b = PARTS[fig]
    vals = []
    for _ in range(n):
        pick = [houses[i] for i in rng.integers(0, len(houses), len(houses))]
        x, y = add(by_house_a[h] for h in pick), add(by_house_b[h] for h in pick)
        if x[b] > 0 and y[b] > 0:
            vals.append(x[a] / x[b] - y[a] / y[b])
    if not vals:
        return None
    lo, hi = (1 - level) / 2 * 100, (1 + level) / 2 * 100
    return float(np.percentile(vals, lo)), float(np.percentile(vals, hi))


# ---------------------------------------------------------------- agreement between two raters
def labels(key):
    """One label per (clip, phrase): 1 shows it, 2 contradicts, 0 neither. Ordered by clip name, then phrase."""
    out = {}
    for c in key["clips"]:
        m = key["marks"].get(c["name"], {})
        ill, con = set(m.get("illustrates", [])), set(m.get("contradicts", []))
        for p in range(len(key["phrases"])):
            out[(c["name"], p)] = 1 if p in ill else 2 if p in con else 0
    return out


def agreement(key_a, key_b):
    """Percent agreement and Cohen's kappa over every (clip, phrase) pair. kappa is None when chance agreement is 100%."""
    la, lb = labels(key_a), labels(key_b)
    if set(la) != set(lb):
        raise ValueError("The two keys are for different clips or phrases, so they cannot be compared.")
    pairs = sorted(la)
    n = len(pairs)
    po = sum(la[p] == lb[p] for p in pairs) / n
    pe = sum((sum(la[p] == c for p in pairs) / n) * (sum(lb[p] == c for p in pairs) / n) for c in (0, 1, 2))
    kappa = (po - pe) / (1 - pe) if pe < 1 else None
    # the yes/no question that drives figure 2 and 3: "does this clip show this phrase?"
    ya = {p: la[p] == 1 for p in pairs}
    yb = {p: lb[p] == 1 for p in pairs}
    po2 = sum(ya[p] == yb[p] for p in pairs) / n
    pa, pb = sum(ya.values()) / n, sum(yb.values()) / n
    pe2 = pa * pb + (1 - pa) * (1 - pb)
    return {"pairs": n, "percent": po, "kappa": kappa, "percent_shows": po2,
            "kappa_shows": (po2 - pe2) / (1 - pe2) if pe2 < 1 else None}


# ---------------------------------------------------------------- held-out houses
def default_holdout(houses):
    """A fixed pick (not random per run): about one house in three, chosen by a hash of its name; at least one when there
    are three or more houses, none when fewer (too few to hold any back)."""
    houses = sorted(houses)
    if len(houses) < 3:
        return []
    ranked = sorted(houses, key=lambda h: hashlib.sha1(h.encode("utf-8")).hexdigest())
    return sorted(ranked[:max(1, len(houses) // 3)])


# ---------------------------------------------------------------- the run
def find_keys(job, keys_dirs=()):
    job = Path(job)
    found = list(job.glob("answer_key.*.json"))
    for d in keys_dirs:
        found += [p for p in Path(d).glob("*.json")]
    keys = []
    for p in found:
        try:
            k = load_key(p)
        except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError):
            continue
        if k["job"] == job.name:
            keys.append(k)
    return sorted(keys, key=lambda k: k["rater"])


def evaluate(jobs, keys_dirs=(), holdout=None, boot=2000, seed=0):
    rows, notes, agree = [], [], []
    for job in jobs:
        job = Path(job)
        keys = find_keys(job, keys_dirs)
        if not keys:
            notes.append(f"{job.name}: no answer key, left out")
            continue
        for k in keys:
            try:
                tls, tn = timelines(job, k)
            except (ValueError, OSError) as e:
                notes.append(f"{job.name} ({k['rater']}): {e}")
                continue
            notes += [f"{job.name} ({k['rater']}): {t}" for t in tn]
            for tname, (order, slots) in tls.items():
                rows.append({"job": job.name, "house": k["house"], "rater": k["rater"], "timeline": tname,
                             "sec": seconds(k, order, slots)})
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                try:
                    agree.append({"job": job.name, "raters": (keys[i]["rater"], keys[j]["rater"]), **agreement(keys[i], keys[j])})
                except ValueError as e:
                    notes.append(f"{job.name}: {e}")
    houses = sorted({r["house"] for r in rows})
    held = sorted(holdout) if holdout is not None else default_holdout(houses)
    pooled = []
    for rater in sorted({r["rater"] for r in rows}):
        for tname in sorted({r["timeline"] for r in rows if r["rater"] == rater}):
            sel = [r for r in rows if r["rater"] == rater and r["timeline"] == tname]
            for scope, keep in (("all houses", lambda h: True), ("tuning houses", lambda h: h not in held),
                                ("held-out houses", lambda h: h in held)):
                if scope != "all houses" and not held:
                    continue
                by_house = {}
                for r in sel:
                    if keep(r["house"]):
                        by_house[r["house"]] = add([by_house[r["house"]], r["sec"]]) if r["house"] in by_house else r["sec"]
                if not by_house:
                    continue
                pooled.append({"scope": scope, "rater": rater, "timeline": tname, "houses": sorted(by_house),
                               "sec": add(by_house.values()), "by_house": by_house,
                               "ci": bootstrap(by_house, boot, seed) if len(by_house) > 1 else None})
    diffs = []
    for p in pooled:
        if p["timeline"] == "her_order" or p["scope"] != "all houses":
            continue
        base = next((q for q in pooled if q["scope"] == p["scope"] and q["rater"] == p["rater"] and q["timeline"] == "her_order"), None)
        if base and len(p["by_house"]) > 1:
            diffs.append({"rater": p["rater"], "timeline": p["timeline"],
                          "diff": ratios(p["sec"])["acc_all"] - ratios(base["sec"])["acc_all"],
                          "ci": bootstrap_diff(p["by_house"], base["by_house"], "acc_all", boot, seed)})
    return {"rows": rows, "pooled": pooled, "agreement": agree, "holdout": held, "houses": houses, "notes": notes,
            "diffs": diffs, "boot": boot, "seed": seed}


def pct(v):
    return "   -  " if v is None else f"{v:6.1%}"


def report(res, out=print):
    out(f"Matching evaluation: {len(res['houses'])} house(s), {len({r['job'] for r in res['rows']})} job(s), "
        f"bootstrap {res['boot']} draws over houses, seed {res['seed']}.")
    if not res["rows"]:
        out("Nothing to score: no job had an answer key.")
    head = f"{'':38}{'coverage':>9}{'acc|sup':>9}{'acc|ALL':>9}{'contra|sup':>11}{'contra|ALL':>11}{'narration':>11}"
    if res["rows"]:
        out("\nPer job")
        out(head)
        for r in res["rows"]:
            f = ratios(r["sec"])
            out(f"{(r['job'] + ' / ' + r['rater'] + ' / ' + r['timeline'])[:37]:38}{pct(f['coverage']):>9}{pct(f['acc_supported']):>9}"
                f"{pct(f['acc_all']):>9}{pct(f['contradict_supported']):>11}{pct(f['contradict_all']):>11}{r['sec']['narration_s']:>10.1f}s")
    if res["pooled"]:
        out("\nPooled (seconds added over jobs; [95% interval over houses] when there are 2 or more)")
        for p in res["pooled"]:
            f = ratios(p["sec"])
            out(f"  {p['scope']} ({', '.join(p['houses'])}), rater {p['rater']}, {p['timeline']}:")
            for k in FIGS:
                ci = (p["ci"] or {}).get(k)
                ci_s = f"  [{ci[0]:.1%} to {ci[1]:.1%}]" if ci else ("  [one house: no interval]" if p["ci"] is None else "")
                out(f"      {FIG_NAMES[k]:32}{pct(f[k])}{ci_s}")
    for d in res["diffs"]:
        ci = d["ci"]
        out(f"\n{d['timeline']} minus her own order, accuracy across all narration (rater {d['rater']}): {d['diff']:+.1%}"
            + (f"  [95% interval {ci[0]:+.1%} to {ci[1]:+.1%}]" if ci else ""))
        if ci:
            out("  " + ("The interval is above zero: on these houses it shows the right room more than her own order does."
                        if ci[0] > 0 else "The interval includes zero or less: these houses do not show it is better."))
    if res["agreement"]:
        out("\nAgreement between raters (every clip x phrase pair: shows it / contradicts / neither)")
        for a in res["agreement"]:
            k = "-" if a["kappa"] is None else f"{a['kappa']:.2f}"
            k2 = "-" if a["kappa_shows"] is None else f"{a['kappa_shows']:.2f}"
            out(f"  {a['job']}: {a['raters'][0]} vs {a['raters'][1]}: {a['percent']:.1%} same label, kappa {k}; "
                f"'shows it' yes/no: {a['percent_shows']:.1%}, kappa {k2} ({a['pairs']} pairs)")
        out("  Kappa reads: below 0.4 weak, 0.4 to 0.6 moderate, 0.6 to 0.8 good, above 0.8 very good. Weak agreement means\n"
            "  the key itself is unsure, so the accuracy figures above are unsure too.")
    out("\nHeld-out houses: " + (", ".join(res["holdout"]) if res["holdout"] else "none (fewer than 3 houses, or none chosen)"))
    for n in res["notes"]:
        out("Note: " + n)
    out("\nWhat these numbers do NOT show: how the video looks or sounds, whether she likes the cut, or houses unlike these.\n"
        "Few houses give wide intervals; a key is one person's judgement of what each clip shows.")


def write_csv(res, path):
    cols = ["scope", "job", "house", "rater", "timeline", "narration_s", "supported_s", *FIGS,
            *[f"{f}_low" for f in FIGS], *[f"{f}_high" for f in FIGS]]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in res["rows"]:
            f = ratios(r["sec"])
            w.writerow({"scope": "job", "job": r["job"], "house": r["house"], "rater": r["rater"], "timeline": r["timeline"],
                        "narration_s": round(r["sec"]["narration_s"], 2), "supported_s": round(r["sec"]["supported_s"], 2),
                        **{k: "" if f[k] is None else round(f[k], 4) for k in FIGS}})
        for p in res["pooled"]:
            f = ratios(p["sec"])
            row = {"scope": p["scope"], "job": "", "house": " ".join(p["houses"]), "rater": p["rater"], "timeline": p["timeline"],
                   "narration_s": round(p["sec"]["narration_s"], 2), "supported_s": round(p["sec"]["supported_s"], 2),
                   **{k: "" if f[k] is None else round(f[k], 4) for k in FIGS}}
            for k in FIGS:
                ci = (p["ci"] or {}).get(k)
                row[f"{k}_low"], row[f"{k}_high"] = (round(ci[0], 4), round(ci[1], 4)) if ci else ("", "")
            w.writerow(row)


def import_legacy(legacy, job, clips_dir, out_dir, house=None):
    """The earlier model-made key ({"jobs": {...: {"clips": [{"clip": n, "illustrates", "contradicts"}], "phrases"}}}),
    whose clip numbers are 1-based in her filmed order and phrase numbers are 0-based."""
    snap = snapshot(job, clips_dir)
    j = next(iter(json.loads(Path(legacy).read_text(encoding="utf-8"))["jobs"].values()))
    if len(j["clips"]) != len(snap["clips"]):
        raise ValueError("The old key has a different number of clips from this job.")
    k = new_key(snap, "model", house=house, made_by="model (earlier one-tour key)")
    for c in j["clips"]:
        k["marks"][snap["clips"][c["clip"] - 1]["name"]] = {"illustrates": list(c["illustrates"]), "contradicts": list(c["contradicts"])}
    check_key(k)
    p = key_path(out_dir, "model")
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(k, indent=1), encoding="utf-8")
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description="Clip-matching figures over many jobs and answer keys (offline).")
    ap.add_argument("jobs", nargs="*", help="finished job folders (out\\<job>)")
    ap.add_argument("--keys", action="append", default=[], help="another folder holding answer_key.*.json files")
    ap.add_argument("--holdout", help="comma-separated house names to hold out (default: a fixed one-in-three pick)")
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--csv", help="save every figure here")
    ap.add_argument("--import-legacy", help="convert the earlier model-made one-tour key (needs --job and --out-dir)")
    ap.add_argument("--job")
    ap.add_argument("--clips")
    ap.add_argument("--out-dir")
    a = ap.parse_args(argv)
    if a.import_legacy:
        if not (a.job and a.out_dir):
            ap.error("--import-legacy needs --job and --out-dir")
        print(f"Saved {import_legacy(a.import_legacy, a.job, a.clips, a.out_dir)}")
        return 0
    if not a.jobs:
        ap.error("name at least one finished job folder")
    for job in a.jobs:
        if not Path(job).is_dir():
            ap.error(f"Job folder does not exist or is not a directory: {job}. "
                     "Check the spelling and pass the finished folder under out/<job>.")
    hold = [h.strip() for h in a.holdout.split(",") if h.strip()] if a.holdout is not None else None
    res = evaluate(a.jobs, a.keys, hold, a.boot, a.seed)
    report(res)
    if a.csv:
        write_csv(res, a.csv)
        print(f"\nSaved {a.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
