r"""Evaluate the on-laptop clip matcher (match_engine = "clip") on her house tour, fully offline.

    set ANTHROPIC_API_KEY= & set OPENROUTER_API_KEY= & set HF_HUB_OFFLINE=1
    venv\Scripts\python tests\evaluate_local_matcher.py [--job out\engine-A-claude] [--key "<answer key json>"] [--json out.json]

Nothing is sent anywhere: every socket connection is refused for the whole run (the count of blocked attempts is
printed; it must be 0), HF_HUB_OFFLINE is set, and there is no key. Frames are the stills already saved in the job
(match/frames, the same ones Claude scored), copied to a temporary folder that is deleted at the end.

Metric (the same as the corrected engine comparison, Oct 9 2026, "finished_video_score.score_timeline"): over a 0.05 s
grid, the share of the time spent in SUPPORTED phrases (phrases at least one clip illustrates per the answer key) where
the clip on screen is one the key says illustrates the phrase being spoken. The plan is what the real planner
(match.plan, through tests/compare_match_engines.decide, default settings) makes from the scores.

Honest split: the 11 supported phrases are split by a fixed rule (alternate ones in time order). Temperature, templates
and crops are chosen ONLY on the tuning half; the chosen setting is then reported on the other half and on all phrases.
The planner sees every phrase either way (a phrase's scores can move clips for the others), so the held-out half is
not perfectly independent: that is said in the output.
"""
import argparse
import json
import os
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["OPENROUTER_API_KEY"] = ""

import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

BLOCKED = []


def _refuse(self, *a, **k):
    BLOCKED.append(1)
    raise OSError("network is blocked during this evaluation")


socket.socket.connect = _refuse
socket.socket.connect_ex = _refuse

from video_tool import localmatch, match  # noqa: E402
import compare_match_engines as cme  # noqa: E402

DEFAULT_KEY = Path("answer-key.json")
DT = match.DT
GRID = {"temperature": (0.005, 0.0075, 0.01, 0.015, 0.02),
        "templates": {"plain": ("{}.",), "three": ("a photo of {}.", "a real estate photo of {}.", "a photo of a room in a house: {}.")},
        "crops": {"middle": (1,), "three": (0, 1, 2)}, "centre": ("phrase", "both")}
EARLIER = {"Claude (finished video)": 0.817, "Qwen stills": 0.749, "Qwen video": 0.487, "her order, even split": 0.243}


def load_key(path):
    j = json.loads(Path(path).read_text(encoding="utf-8"))["jobs"]["demo-tour-moved"]
    n = len(j["clips"])
    ill, con = [set() for _ in range(n)], [set() for _ in range(n)]
    for c in j["clips"]:
        ill[c["clip"] - 1], con[c["clip"] - 1] = set(c["illustrates"]), set(c["contradicts"])
    return ill, con, j["phrases"]


def score_timeline(order, slots, length, pt, ill, con, only=None):
    """finished_video_score.score_timeline, plus `only`: phrases outside it count as neutral. -> dict incl per-phrase."""
    P = len(pt)
    supported = {p for p in range(P) if any(p in s for s in ill)}
    if only is not None:
        supported &= set(only)
    starts, a = [], 0.0
    for l in slots:
        starts.append(a)
        a += l
    grid = np.arange(0, length, DT)
    ph = np.searchsorted([e for _, e in pt], grid, side="right").clip(0, P - 1)
    ci = (np.searchsorted(np.array(starts), grid, side="right") - 1).clip(0, len(order) - 1)
    on = bad = tot = 0
    per = {p: [0, 0] for p in sorted(supported)}
    for p, k in zip(ph, ci):
        if p not in supported:
            continue
        c = order[k]
        tot += 1
        per[p][1] += 1
        if p in ill[c]:
            on += 1
            per[p][0] += 1
        elif p in con[c]:
            bad += 1
    return {"on_share": on / tot if tot else None, "bad_share": bad / tot if tot else None, "seconds": tot * DT,
            "per_phrase": {p: (v[0] * DT, v[1] * DT) for p, v in per.items()}}


def plan_of(scores, jd, scratch):
    d = cme.decide("clip", scores, jd, scratch)
    n = len(scores)
    order = d.get("order") if d.get("used") and d.get("order") else list(range(n))
    slots = d["slots"] if d.get("slots") else jd["even"]
    return order, slots, d


def top1(scores, ill):
    best = cme.best_phrases(scores)
    return sum(1 for k in range(len(ill)) if ill[k] and best[k] in ill[k]), sum(1 for k in range(len(ill)) if ill[k])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", default=str(ROOT / "out" / "engine-A-claude"))
    ap.add_argument("--clips", default=str(ROOT / "samples" / "tour"))
    ap.add_argument("--key", default=str(DEFAULT_KEY))
    ap.add_argument("--json", help="also save the numbers here (no frames, no footage)")
    a = ap.parse_args(argv)
    if not localmatch.available():
        print("SKIP: the on-laptop matcher's model files are not in assets/models/clip.")
        return 0
    job = Path(a.job)
    saved = json.loads((job / "match" / "scores.json").read_text(encoding="utf-8"))
    jd = cme.load_job(job, a.clips)
    ill, con, key_phrases = load_key(a.key)
    # commas aside, the only difference allowed is the key's "[phone number]" in place of the number
    words = lambda s: " ".join("".join(c for c in s.lower().replace("phone number", "") if c.isalpha() or c == " ").split())
    assert [words(p["text"]) for p in jd["phr"]] == [words(t) for t in key_phrases], "the key's phrases differ from the job's"
    assert jd["per_clip"] == saved["frame_times"], "frame times differ from the saved stills"
    pt = [(p["start"], p["end"]) for p in jd["phr"]]
    length = jd["target"]
    supported = [p for p in range(len(pt)) if any(p in s for s in ill)]
    tune, held = supported[0::2], supported[1::2]
    print(f"Job {job.name}: {len(jd['checked'])} clips, {len(pt)} phrases, {len(supported)} supported by the key.")
    print(f"Tuning phrases (1-based): {[p + 1 for p in tune]}   held-out phrases: {[p + 1 for p in held]}")

    tmp = Path(tempfile.mkdtemp(prefix="localmatch-eval-"))
    try:
        fdir = tmp / "match" / "frames"
        fdir.mkdir(parents=True)
        frames = []
        for i, ts in enumerate(jd["per_clip"]):
            row = []
            for j in range(len(ts)):
                src = job / "match" / "frames" / f"{i + 1:02d}_{j + 1}.jpg"
                shutil.copy2(src, fdir / src.name)
                row.append(fdir / src.name)
            frames.append(row)
        flat = [p for r in frames for p in r]
        t0 = time.time()
        localmatch._load()
        t_load = time.time() - t0
        t0 = time.time()
        shipped_crops, localmatch.CROPS = localmatch.CROPS, ("top", "middle", "bottom")   # embed all three, pick later
        try:
            fe = localmatch.embed_frames(flat, cache=None)        # [frames, 3 crops, 512]
        finally:
            localmatch.CROPS = shipped_crops
        t_img = time.time() - t0
        t0 = time.time()
        texts = [p["text"] for p in jd["phr"]]
        te = {name: localmatch.embed_texts(texts, templates=tpl) for name, tpl in GRID["templates"].items()}
        t_txt = time.time() - t0

        def scores_for(T, tpl, crops, centre):
            S = localmatch.to_scale(localmatch.similarities(fe[:, list(GRID["crops"][crops])], te[tpl]), T, centre)
            out, k = [], 0
            for ts in jd["per_clip"]:
                out.append([S[k + j].tolist() for j in range(len(ts))])
                k += len(ts)
            return out

        # Baselines through the same scorer
        even = score_timeline(list(range(len(jd["checked"]))), jd["even"], length, pt, ill, con)
        held_even = score_timeline(list(range(len(jd["checked"]))), jd["even"], length, pt, ill, con, held)
        tune_even = score_timeline(list(range(len(jd["checked"]))), jd["even"], length, pt, ill, con, tune)
        t0 = time.time()
        co, cs, _ = plan_of(saved["scores"], jd, tmp / "dec_claude")
        t_plan = time.time() - t0
        claude_all = score_timeline(co, cs, length, pt, ill, con)
        claude_held = score_timeline(co, cs, length, pt, ill, con, held)
        claude_tune = score_timeline(co, cs, length, pt, ill, con, tune)

        print(f"\nModels loaded in {t_load:.1f}s; {len(flat)} frames x 3 crops embedded in {t_img:.1f}s; "
              f"{len(texts)} phrases x templates in {t_txt:.1f}s; one planner run {t_plan:.1f}s.")
        print("\nTUNING (tuning phrases only):")
        defaults = (localmatch.TEMPERATURE, next(k for k, v in GRID["templates"].items() if v == localmatch.TEMPLATES),
                    "middle" if localmatch.CROPS == ("middle",) else "three", localmatch.CENTRE)
        rows, other = [], {}
        for T in GRID["temperature"]:
            for tpl in GRID["templates"]:
                for crops in GRID["crops"]:
                    for centre in GRID["centre"]:
                        cfg = (T, tpl, crops, centre)
                        o, s, d = plan_of(scores_for(*cfg), jd, tmp / "dec")
                        r = score_timeline(o, s, length, pt, ill, con, tune)
                        # held-out and all are worked out from the same plan but only printed later, never used to choose
                        other[cfg] = (score_timeline(o, s, length, pt, ill, con, held)["on_share"],
                                      score_timeline(o, s, length, pt, ill, con)["on_share"])
                        rows.append((r["on_share"], cfg == defaults, -abs(T - localmatch.TEMPERATURE), cfg))
                        print(f"  T={T:<7} templates={tpl:6s} crops={crops:6s} centre={centre:6s} tuning on-words "
                              f"{100 * r['on_share']:5.1f}%  used={d.get('used')} order={[c + 1 for c in o]}")
        cfg = max(rows)[3]
        T, tpl, crops, centre = cfg
        print(f"Chosen on the tuning half: T={T}, templates={tpl}, crops={crops}, centre={centre}"
              + ("  (same as the module defaults)" if cfg == defaults else
                 "  (DIFFERENT from the module defaults: update localmatch.py if this is to be used)"))

        sc = scores_for(*cfg)
        t0 = time.time()
        o, s, d = plan_of(sc, jd, tmp / "dec_final")
        t_final_plan = time.time() - t0
        r_all = score_timeline(o, s, length, pt, ill, con)
        r_held = score_timeline(o, s, length, pt, ill, con, held)
        r_tune = score_timeline(o, s, length, pt, ill, con, tune)
        # Defaults (what the shipped module does) on all phrases, too
        dsc = scores_for(*defaults)
        do, ds, _ = plan_of(dsc, jd, tmp / "dec_default")
        r_def = score_timeline(do, ds, length, pt, ill, con)
        r_def_held = score_timeline(do, ds, length, pt, ill, con, held)
        print("\nEVERY SETTING on the held-out half and all phrases (shown for honesty; NOT used to choose):")
        spread = []
        for (T2, tpl2, crops2, centre2), (h2, a2) in sorted(other.items()):
            spread.append(a2)
            print(f"  T={T2:<7} templates={tpl2:6s} crops={crops2:6s} centre={centre2:6s} held-out {100 * h2:5.1f}%  all {100 * a2:5.1f}%")
        print(f"  all-phrase share over the {len(spread)} settings: median {100 * float(np.median(spread)):.1f}%, "
              f"range {100 * min(spread):.1f}% to {100 * max(spread):.1f}%")
        print("\nPER CLIP top phrase (1-based) - CLIP vs Claude vs what the key says the clip shows:")
        cb, kb = cme.best_phrases(sc), cme.best_phrases(saved["scores"])
        for k in range(len(ill)):
            print(f"  clip {k + 1:2d}: CLIP {cb[k] + 1:2d} {'ok ' if cb[k] in ill[k] else 'no '} Claude {kb[k] + 1:2d} "
                  f"{'ok ' if kb[k] in ill[k] else 'no '} key {sorted(p + 1 for p in ill[k])}  CLIP's pick: {texts[cb[k]][:45]}")
        hit, judged = top1(sc, ill)
        c_hit, _ = top1(saved["scores"], ill)
        pc = lambda x: "n/a" if x is None else f"{100 * x:.1f}%"
        print("\nRESULT (share of the supported voice-over where the clip on screen shows what she says)")
        print(f"{'':34s} {'held-out half':>14s} {'tuning half':>12s} {'all phrases':>12s}  wrong-room share (all)")
        print(f"{'on-laptop CLIP (chosen setting)':34s} {pc(r_held['on_share']):>14s} {pc(r_tune['on_share']):>12s} {pc(r_all['on_share']):>12s}  {pc(r_all['bad_share'])}")
        print(f"{'on-laptop CLIP (module defaults)':34s} {pc(r_def_held['on_share']):>14s} {'':>12s} {pc(r_def['on_share']):>12s}  {pc(r_def['bad_share'])}")
        print(f"{'Claude (its saved scores, replanned)':34s} {pc(claude_held['on_share']):>14s} {pc(claude_tune['on_share']):>12s} {pc(claude_all['on_share']):>12s}  {pc(claude_all['bad_share'])}")
        print(f"{'her order, even split':34s} {pc(held_even['on_share']):>14s} {pc(tune_even['on_share']):>12s} {pc(even['on_share']):>12s}  {pc(even['bad_share'])}")
        print("Earlier comparison (finished videos, all phrases): " + ", ".join(f"{k} {100 * v:.0f}%" for k, v in EARLIER.items()))
        print(f"Top choice really shows the phrase: CLIP {hit}/{judged} clips, Claude {c_hit}/{judged}.")
        print(f"CLIP plan: used={d.get('used')}, order={[c + 1 for c in o]}, reason={d.get('reason')}, moved={d.get('clips_moved')}")
        print(f"Claude plan order={[c + 1 for c in co]}")
        print("\nPER PHRASE (seconds on-words / supported seconds): CLIP vs Claude vs her order")
        wins = losses = ties = 0
        for p in supported:
            c1, n1 = r_all["per_phrase"][p]
            c2, _ = claude_all["per_phrase"][p]
            c3, _ = even["per_phrase"][p]
            tag = "held" if p in held else "tune"
            verdict = "CLIP better" if c1 > c2 + 0.1 else "Claude better" if c2 > c1 + 0.1 else "same"
            wins += verdict == "CLIP better"
            losses += verdict == "Claude better"
            ties += verdict == "same"
            print(f"  {p + 1:2d} [{tag}] {c1:4.1f}/{n1:4.1f}s vs {c2:4.1f}s vs {c3:4.1f}s  {verdict:13s} {texts[p][:60]}")
        print(f"  vs Claude: CLIP better on {wins}, worse on {losses}, same on {ties} of {len(supported)} supported phrases.")
        t_total = t_load + t_img + t_txt
        print(f"\nRUNTIME on this laptop: scoring {t_total:.1f}s (load {t_load:.1f}s + frames {t_img:.1f}s + text {t_txt:.1f}s), "
              f"planner {t_final_plan:.1f}s. Claude took about 43 s to score in the earlier comparison.")
        print("CAVEATS: one tour; the answer key was made by a model, not a person; tuning used about half the phrases but the "
              "planner sees all of them, so the held-out half is not fully independent; Claude's number here is its saved "
              "scores re-planned, which matched its finished video (81.7%) in the earlier comparison.")
        print(f"NETWORK: {len(BLOCKED)} connection attempt(s) blocked (must be 0).")
        if a.json:
            Path(a.json).write_text(json.dumps({
                "chosen": {"temperature": T, "templates": tpl, "crops": crops, "centre": centre}, "tune_phrases": tune, "held_phrases": held,
                "clip": {"held": r_held["on_share"], "tune": r_tune["on_share"], "all": r_all["on_share"], "bad": r_all["bad_share"]},
                "clip_defaults": {"held": r_def_held["on_share"], "all": r_def["on_share"]},
                "claude": {"held": claude_held["on_share"], "tune": claude_tune["on_share"], "all": claude_all["on_share"]},
                "even": {"held": held_even["on_share"], "tune": tune_even["on_share"], "all": even["on_share"]},
                "top1": [hit, c_hit, judged], "seconds": {"load": t_load, "frames": t_img, "text": t_txt, "plan": t_final_plan},
                "blocked": len(BLOCKED)}, indent=1), encoding="utf-8")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if not BLOCKED else 1


if __name__ == "__main__":
    sys.exit(main())
