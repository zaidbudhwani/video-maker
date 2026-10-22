"""Compare the clip-matching engines (Claude, Qwen stills, Qwen video) on one saved job.

Run LATER by the owner, only after approving that her footage may be sent to the model providers:

  venv/Scripts/python tests/compare_match_engines.py --job out/demo-tour-moved --engines claude,qwen,qwen_video \
      --runs 3 --truth ground_truth.json --out some/folder --send-footage-ok

Without --send-footage-ok nothing is sent and the tool stops. With --mock a deterministic fake replaces every
model (no key, no network, no cost; sockets are blocked to prove it): that is how the tool itself is tested.

Per engine it records cost, seconds, stability between runs, accuracy against a ground-truth file, agreement with the
other engines, and what the real decision step (match.plan) would do with each run's scores. Scores are injected into
match.plan, so the decision step never calls a model. Keys and response bodies are never printed or saved.
"""
import os
import argparse
import json
import random
import socket
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from video_tool import ai, assemble, match, qwen, settings  # noqa: E402

ENGINES = ("claude", "qwen", "qwen_video")
MOCK_KINDS = ("steady", "truth", "random", "noisy")
REFUSAL = ("Nothing was sent: this comparison would send small stills and, for qwen_video, low-resolution 1.2 second "
           "segments of her clips to OpenRouter (routed only to providers that do not keep or train on them), and the "
           "Claude stills to Anthropic as today, so it only runs after the owner approves and --send-footage-ok is added.")
STRONG, RECALL_AT = 8, 7


# ---------- loading ----------

def load_job(job, clips_dir=None):
    """Everything the engines need from a saved job. Raises ValueError with a plain sentence when it can't."""
    job = Path(job)
    if not job.is_dir():
        raise ValueError(f"The job folder {job} was not found.")
    wp = next((job / n for n in ("words_trimmed.json", "words.json") if (job / n).exists()), None)
    if wp is None:
        raise ValueError("The job has no saved words (words_trimmed.json), so there are no phrases to score.")
    words = json.loads(wp.read_text(encoding="utf-8"))
    report = json.loads((job / "report.json").read_text(encoding="utf-8")) if (job / "report.json").exists() else {}
    target = float(report.get("length") or (words[-1]["end"] if words else 0))
    # The clips as she filmed them (the normalized ones are already cut to their slots, too short to judge)
    src, notes = clips_dir, []
    if not src and (job.parent / (job.name + ".source")).exists():
        src = (job.parent / (job.name + ".source")).read_text(encoding="utf-8").strip()
    clips = sorted(Path(src).glob("*.mp4")) if src and Path(src).is_dir() else []
    if not clips:
        clips = sorted((job / "normalized").glob("*.mp4"))
        if clips:
            notes.append("Her original clips were not found, so the shorter normalized clips were used (pass --clips).")
    if not clips:
        raise ValueError("The job has no clips to match (no original folder and no normalized clips).")
    skips = {}
    try:
        for t in json.loads((job / "timeline.json").read_text(encoding="utf-8")):
            skips[t["clip"]] = float(t.get("skipped_start") or 0)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    checked = []
    for c in clips:
        dur, hdr = assemble.check_clip(c)
        skip = skips.get(c.name, 0.0)
        checked.append((str(c), dur - skip, hdr, skip))
    usable = [d for _, d, _, _ in checked]
    phr = match.phrases(words, target)
    per_clip = [match.frame_times(u) for u in usable]
    while sum(len(t) for t in per_clip) > match.MAX_FRAMES and max(len(t) for t in per_clip) > 2:
        per_clip = [t[:-1] if len(t) > 2 else t for t in per_clip]       # same trimming as match.plan
    saved = None
    try:
        saved = json.loads((job / "match" / "scores.json").read_text(encoding="utf-8"))
        if saved.get("frame_times") != per_clip:
            notes.append("The frame times worked out now differ from the saved ones, so the saved stills may not line up.")
    except (OSError, ValueError):
        pass
    even = assemble.plan_slots(usable, target, words)
    frames = [[job / "match" / "frames" / f"{i + 1:02d}_{j + 1}.jpg" for j in range(len(ts))] for i, ts in enumerate(per_clip)]
    return {"job": job, "words": words, "target": target, "clips": clips, "checked": checked, "phr": phr,
            "per_clip": per_clip, "frames": frames, "even": even, "notes": notes,
            "names": assemble.clip_names(clips)}


def load_truth(path, job_name):
    """-> (illustrates, contradicts, phrase_texts) as 0-based sets per clip, or raises ValueError."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    j = data["jobs"][job_name]
    n = len(j["clips"])
    ill, con = [set() for _ in range(n)], [set() for _ in range(n)]
    for c in j["clips"]:
        k = int(c["clip"]) - 1
        if 0 <= k < n:
            ill[k], con[k] = set(c.get("illustrates") or []), set(c.get("contradicts") or [])
    return ill, con, j.get("phrases", [])


# ---------- fakes (--mock) ----------

def _seed(*parts):
    return zlib.crc32("|".join(str(p) for p in parts).encode())


def make_fake(kind, per_clip, phr_n, truth, fail_engine, seed):
    """A stand-in for match.ask_model: same signature, same return shape, no model, no network."""
    ill, con = truth if truth else (None, None)
    counter = {}

    def fake(engine, frames, pc, phr, videos=None):
        if engine == fail_engine:
            raise qwen.QwenError("OpenRouter answered 402: (mock)", 402)
        run = counter[engine] = counter.get(engine, 0) + 1
        scores = []
        for k, ts in enumerate(pc):
            rows = []
            for j in range(len(ts)):
                row = []
                for p in range(len(phr)):
                    if kind == "steady":
                        v = _seed("steady", seed, k, j, p) % 11
                    elif kind == "random":
                        v = random.Random(_seed("random", seed, engine, run, k, j, p)).randint(0, 10)
                    else:   # truth / noisy
                        v = 10 if ill and k < len(ill) and p in ill[k] else 0 if con and k < len(con) and p in con[k] else 5
                        if kind == "noisy":
                            v = min(10, max(0, v + random.Random(_seed("noisy", seed, engine, run, k, j, p)).randint(-1, 1)))
                    row.append(int(v))
                rows.append(row)
            scores.append(rows)
        n_in = 1000 * sum(len(t) for t in pc)
        cost = round(n_in * (0.00001 if engine == "claude" else 0.0000002), 6)
        return scores, {"input_tokens": n_in, "output_tokens": 500, "cost_usd": cost}
    return fake


def block_network():
    """Mock mode: any attempt to open a connection is an error (and is counted)."""
    attempts = []

    def refuse(self, *a, **k):
        attempts.append(1)
        raise OSError("network is blocked in mock mode")
    socket.socket.connect = refuse
    socket.socket.connect_ex = refuse
    return attempts


# ---------- metrics ----------

def best_phrases(scores):
    return [int(np.array(clip, dtype=float).max(axis=0).argmax()) for clip in scores]


def stability(runs):
    """Mean agreement of per-clip best phrase, and mean absolute score difference, over every pair of runs."""
    if len(runs) < 2:
        return {"best_agreement": None, "mean_abs_diff": None, "pairs": 0}
    agree, diff, pairs = [], [], 0
    best = [best_phrases(r) for r in runs]
    for a in range(len(runs)):
        for b in range(a + 1, len(runs)):
            agree.append(float(np.mean([x == y for x, y in zip(best[a], best[b])])))
            diff.append(float(np.mean([np.abs(np.array(p, dtype=float) - np.array(q, dtype=float)).mean()
                                       for p, q in zip(runs[a], runs[b])])))
            pairs += 1
    return {"best_agreement": round(float(np.mean(agree)), 4), "mean_abs_diff": round(float(np.mean(diff)), 4), "pairs": pairs}


def accuracy_one(scores, ill, con):
    """One run against the truth. Shares are None when there is nothing to measure."""
    best = best_phrases(scores)
    judged = [k for k in range(min(len(scores), len(ill))) if ill[k]]
    hit = [best[k] in ill[k] for k in judged]
    strong = strong_ok = pair_n = pair_ok = c_hits = c_cells = 0
    for k, clip in enumerate(scores[:len(ill)]):
        S = np.array(clip, dtype=float)
        strong += int((S >= STRONG).sum())
        strong_ok += int(sum((S[:, p] >= STRONG).sum() for p in ill[k] if p < S.shape[1]))
        mx = S.max(axis=0)
        for p in ill[k]:
            if p < len(mx):
                pair_n += 1
                pair_ok += int(mx[p] >= RECALL_AT)
        for p in con[k]:
            if p < S.shape[1]:
                c_cells += S.shape[0]
                c_hits += int((S[:, p] >= STRONG).sum())
    share = lambda a, b: round(a / b, 4) if b else None
    return {"argmax_in_truth": share(sum(hit), len(hit)), "clips_judged": len(judged),
            "clips_skipped_empty_truth": min(len(scores), len(ill)) - len(judged),
            "precision": share(strong_ok, strong), "strong_scores": strong,
            "recall": share(pair_ok, pair_n), "truth_pairs": pair_n,
            "contradiction_hits": c_hits, "contradiction_rate": share(c_hits, c_cells)}


def accuracy(runs, truth):
    if not truth:
        return None
    ill, con, _ = truth
    per = [accuracy_one(r, ill, con) for r in runs]
    out = {"per_run": per}
    for k in ("argmax_in_truth", "precision", "recall", "contradiction_rate"):
        vals = [p[k] for p in per if p[k] is not None]
        out[k] = round(float(np.mean(vals)), 4) if vals else None
    out["contradiction_hits"] = round(float(np.mean([p["contradiction_hits"] for p in per])), 2)
    out["clips_skipped_empty_truth"] = per[0]["clips_skipped_empty_truth"]
    out["clips_judged"] = per[0]["clips_judged"]
    return out


def consensus(runs):
    """Per-clip best phrase of the average of an engine's runs."""
    avg = [np.mean([np.array(r[k], dtype=float) for r in runs], axis=0) for k in range(len(runs[0]))]
    return [int(a.max(axis=0).argmax()) for a in avg]


def cross_agreement(ok):
    names = [e for e, r in ok.items() if r]
    cons = {e: consensus(ok[e]) for e in names}
    out = {}
    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            x, y = cons[names[a]], cons[names[b]]
            n = min(len(x), len(y))
            out[f"{names[a]}|{names[b]}"] = round(float(np.mean([x[i] == y[i] for i in range(n)])), 4) if n else None
    return out


# ---------- decisions (match.plan with injected scores) ----------

def decide(engine, scores, job_data, scratch):
    """What the pipeline would do with these scores. No model is called: ask_model is replaced for the duration."""
    real = (match.ask_model, match.extract_frames, match.extract_videos, ai.has_key, ai.has_openrouter_key, qwen.chat, ai.client)

    def injected(eng, frames, per_clip, phr, videos=None):
        return [[list(r) for r in c] for c in scores], {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    def never(*a, **k):
        raise RuntimeError("the decision step must not call a model")
    scratch.mkdir(parents=True, exist_ok=True)
    keep = dict(settings._current)
    try:
        settings.configure({**job_data.get("overrides", {}), "match_engine": engine, "match_twice": False})
        match.ask_model, match.extract_frames, match.extract_videos = injected, (lambda *a, **k: []), (lambda *a, **k: [])
        ai.has_key = ai.has_openrouter_key = lambda: True
        qwen.chat = ai.client = never
        slots, info, warns = match.plan(job_data["checked"], job_data["words"], job_data["target"], job_data["even"],
                                        scratch, None, job_data["names"])
    finally:
        (match.ask_model, match.extract_frames, match.extract_videos, ai.has_key, ai.has_openrouter_key, qwen.chat, ai.client) = real
        settings._current.clear()
        settings._current.update(keep)
    order = info.get("order")
    texts = [p["text"] for p in job_data["phr"]]
    shown = match.not_shown(scores, [[p["start"], p["end"]] for p in job_data["phr"]], texts)
    keep_keys = ("used", "reason", "order", "moved", "score_matched", "score_reordered", "score_even", "matched_seconds",
                 "even_matched_seconds")
    names = job_data["names"]
    return {**{k: info[k] for k in keep_keys if k in info},
            "clips_moved": [names[c] for pos, c in enumerate(order) if c != pos] if order else [],
            "slots": slots, "warnings": len(warns), "not_shown": shown}


# ---------- running ----------

def get_videos(job_data, out_dir, notes):
    try:
        return [str(p) for p in match.extract_videos(job_data["checked"], job_data["per_clip"], out_dir / "videos", notes)], None
    except Exception as e:
        return None, f"The short shot videos could not be made ({type(e).__name__})."


def run_engine(engine, job_data, runs, send_ok, mock, out_dir):
    res = {"engine": engine, "runs": [], "failures": [], "skipped": None}
    videos, frames = None, None
    if not mock:
        key_ok = ai.has_openrouter_key() if engine != "claude" else ai.has_key()
        if not key_ok:
            res["skipped"] = "No key for this engine was found, so nothing was sent."
            return res, []
    if engine == "qwen_video":
        videos, why = get_videos(job_data, out_dir, res.setdefault("video_notes", {}))
        if why:
            res["skipped"] = why
            return res, []
    else:
        frames = job_data["frames"]
        if not mock and not all(p.exists() for row in frames for p in row):
            res["skipped"] = "The job has no saved stills (match/frames), so this engine cannot be tried on it."
            return res, []
    def one(r):
        t0 = time.time()
        try:
            scores, usage = match.ask_model(engine, frames, job_data["per_clip"], job_data["phr"], videos)
            return r, scores, {"run": r, "seconds": round(time.time() - t0, 2), "cost_usd": float(usage.get("cost_usd") or 0),
                               "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0)}, None
        except Exception as e:   # never print the body: only a plain sentence and the status
            msg = ai.explain(e, "match")["message"] if ai.is_ai_error(e) else f"The call failed ({type(e).__name__})."
            return r, None, None, {"run": r, "seconds": round(time.time() - t0, 2), "type": type(e).__name__,
                                   "status": getattr(e, "status_code", None), "message": msg}

    if mock or runs == 1:
        outs = [one(r) for r in range(1, runs + 1)]
    else:   # the runs are independent requests: send them at the same time (each can take minutes)
        with ThreadPoolExecutor(runs) as ex:
            outs = list(ex.map(one, range(1, runs + 1)))
    good = []
    for r, scores, entry, failure in sorted(outs, key=lambda o: o[0]):
        if failure:
            res["failures"].append(failure)
        else:
            res["runs"].append(entry)
            good.append(scores)
    return res, good


_MEMO = {}


def summarise(res, good, truth, job_data, scratch_root, max_decisions=None):
    secs = [r["seconds"] for r in res["runs"]]
    costs = [r["cost_usd"] for r in res["runs"]]
    res["cost_total_usd"] = round(sum(costs), 6)
    res["cost_per_run_usd"] = round(sum(costs) / len(costs), 6) if costs else None
    res["seconds_per_run"] = round(sum(secs) / len(secs), 2) if secs else None
    res["stability"] = stability(good) if good else None
    res["accuracy"] = accuracy(good, truth) if good else None
    res["decisions"] = []
    for i, scores in enumerate(good, 1):
        if max_decisions is not None and i > max_decisions:
            break
        try:   # the same scores give the same decision (engine only gates the key), so a repeat is not worked out twice
            memo = (res["engine"], json.dumps(scores))
            if memo not in _MEMO:
                _MEMO[memo] = decide(res["engine"], scores, job_data, scratch_root / f"{res['engine']}_run{i}")
            d = dict(_MEMO[memo])
        except Exception as e:
            d = {"error": f"The decision step could not run on this run ({type(e).__name__})."}
        d["run"] = i
        res["decisions"].append(d)
    return res


def fmt(v, pct=False, nd=2):
    if v is None:
        return "n/a"
    return f"{v * 100:.0f}%" if pct else f"{v:.{nd}f}"


def markdown(result):
    L = [f"# Clip matching comparison ({result['job']})", ""]
    if result["mock"]:
        L += [f"MOCK RUN ({result['mock']}): a fake stood in for every model. Nothing was sent anywhere.", ""]
    L += ["| Engine | Runs ok | Cost/run | Total cost | Seconds/run | Best-phrase agreement | Mean score diff | Best in truth | Precision | Recall | Strong on contradicted |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for e in result["engines"]:
        a, s = e.get("accuracy") or {}, e.get("stability") or {}
        L.append(f"| {e['engine']} | {len(e['runs'])}/{len(e['runs']) + len(e['failures'])} | "
                 f"{'n/a' if e.get('cost_per_run_usd') is None else '$' + format(e['cost_per_run_usd'], '.4f')} | ${e.get('cost_total_usd', 0):.4f} | "
                 f"{fmt(e.get('seconds_per_run'), nd=1)} | {fmt(s.get('best_agreement'), True)} | {fmt(s.get('mean_abs_diff'))} | "
                 f"{fmt(a.get('argmax_in_truth'), True)} | {fmt(a.get('precision'), True)} | {fmt(a.get('recall'), True)} | "
                 f"{a.get('contradiction_hits', 'n/a')} ({fmt(a.get('contradiction_rate'), True)}) |")
    L += ["", "Cross-engine agreement of best phrase per clip: " +
          (", ".join(f"{k} {fmt(v, True)}" for k, v in result["cross_engine_agreement"].items()) or "n/a"), ""]
    L += ["## What the pipeline would do", ""]
    for e in result["engines"]:
        for d in e.get("decisions", []):
            if "error" in d:
                L.append(f"- {e['engine']} run {d['run']}: {d['error']}")
                continue
            L.append(f"- {e['engine']} run {d['run']}: " + ("moves " + ", ".join(d["clips_moved"]) if d["clips_moved"]
                     else "keeps her order") + f"; used={d.get('used')}; plan score {d.get('score_matched')}"
                     + (f" (reordered {d['score_reordered']})" if "score_reordered" in d else "")
                     + f"; not shown: {len(d['not_shown'])}" + (f" ({d.get('reason')})" if d.get("reason") else ""))
    for e in result["engines"]:
        for f in e["failures"]:
            L.append(f"- FAILED {e['engine']} run {f['run']}: {f['message']}")
        if e.get("skipped"):
            L.append(f"- SKIPPED {e['engine']}: {e['skipped']}")
    L += ["", result["paragraph"], ""]
    return "\n".join(L)


def paragraph(result):
    parts = []
    for e in result["engines"]:
        if e.get("skipped") and not e["runs"]:
            parts.append(f"{e['engine']} was skipped: {e['skipped']}")
            continue
        if not e["runs"]:
            parts.append(f"{e['engine']} did not return any scores ({len(e['failures'])} failed call(s)).")
            continue
        a, s = e.get("accuracy") or {}, e.get("stability") or {}
        t = f"{e['engine']} cost about ${e['cost_per_run_usd']:.4f} a run and took about {e['seconds_per_run']:.0f} seconds"
        if s.get("best_agreement") is not None:
            t += f"; between runs it picked the same best phrase for {fmt(s['best_agreement'], True)} of the clips"
        if a.get("argmax_in_truth") is not None:
            t += f"; its top choice was a phrase the clip really shows for {fmt(a['argmax_in_truth'], True)} of the {a['clips_judged']} clips judged"
        if a.get("contradiction_hits"):
            t += f", and it gave a strong score to something the clip does not show {a['contradiction_hits']:g} time(s)"
        parts.append(t + ".")
    if result["cross_engine_agreement"]:
        parts.append("Engines agreed on the best phrase " + ", ".join(
            f"{fmt(v, True)} of the time ({k.replace('|', ' vs ')})" for k, v in result["cross_engine_agreement"].items()) + ".")
    return " ".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--job", required=True, help="a saved job folder, e.g. out/demo-tour-moved")
    ap.add_argument("--engines", default="claude,qwen,qwen_video")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--truth", help="ground_truth.json (optional; without it accuracy is left out)")
    ap.add_argument("--truth-job", help="the job name to read from the truth file when it differs from --job's folder name (e.g. demo-tour-moved)")
    ap.add_argument("--clips", help="folder of her original clips (default: the one the job was made from)")
    ap.add_argument("--out", required=True, help="folder for results.json / results.md")
    ap.add_argument("--send-footage-ok", action="store_true", help="the owner approved sending her footage to the model providers")
    ap.add_argument("--mock", nargs="?", const="steady", choices=MOCK_KINDS, help="use a deterministic fake instead of any model")
    ap.add_argument("--mock-fail", default="", help="mock only: this engine's calls fail (tests the failure path)")
    ap.add_argument("--max-decisions", type=int, default=None, help="work out the pipeline decision for only the first N runs of each engine (default: every run; each takes about a minute)")
    ap.add_argument("--settings", help="optional JSON of settings for the decision step (e.g. auto_order)")
    a = ap.parse_args(argv)

    if not a.mock and not a.send_footage_ok:
        print(REFUSAL)
        return 2
    os.environ["ALEX_ALLOW_OTHER_AI"] = "1"   # this hand-run tool is the separate, explicit authorization (it needs --send-footage-ok or --mock to get here)
    engines = [e.strip() for e in a.engines.split(",") if e.strip()]
    if not engines or any(e not in ENGINES for e in engines) or a.runs < 1:
        print(f"Engines must be from {', '.join(ENGINES)} and --runs at least 1.")
        return 2
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    attempts = block_network() if a.mock else []

    try:
        job_data = load_job(a.job, a.clips)
    except (ValueError, OSError, KeyError) as e:
        print(f"Could not load the job: {e}")
        return 1
    truth, truth_note = None, None
    if a.truth:
        try:
            truth = load_truth(a.truth, a.truth_job or Path(a.job).name)
            def words_of(s):   # a comma more or less in the transcript does not change which phrase is which
                return " ".join("".join(c for c in s.lower() if c.isalnum() or c == " ").split())
            if truth[2] and [words_of(t) for t in truth[2]] != [words_of(p["text"]) for p in job_data["phr"]]:
                truth_note = "The truth file's phrases are not the same as this job's phrases, so accuracy was left out."
                truth = None
        except (OSError, ValueError, KeyError) as e:
            truth_note = f"The truth file could not be used ({type(e).__name__}), so accuracy was left out."
    else:
        truth_note = "No truth file was given, so accuracy was left out."
    overrides = {}
    if a.settings:
        try:
            overrides = json.loads(Path(a.settings).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print("The settings file could not be read, so defaults were used.")

    job_data["overrides"] = overrides
    if a.mock:
        print(f"Mock run ({a.mock}): no key, no network, no cost.")
        match.ask_model = make_fake(a.mock, job_data["per_clip"], len(job_data["phr"]),
                                    (truth[0], truth[1]) if truth else None, a.mock_fail, 7)
    else:
        print("Sending small stills and, for qwen_video, low-resolution 1.2 second segments of her clips to OpenRouter "
              "(only providers that do not keep or train on them); Claude stills go to Anthropic as today.")

    results, ok = [], {}
    print(f"  {', '.join(engines)}: {a.runs} run(s) each" + ("" if a.mock else ", all sent at the same time (about 5 minutes)") + "...", flush=True)
    if a.mock or len(engines) == 1:
        ran = [run_engine(e, job_data, a.runs, a.send_footage_ok, bool(a.mock), out) for e in engines]
    else:   # engines are independent too: the slowest single request decides how long this takes, not the sum
        with ThreadPoolExecutor(len(engines)) as ex:
            ran = list(ex.map(lambda e: run_engine(e, job_data, a.runs, a.send_footage_ok, False, out), engines))
    for e, (res, good) in zip(engines, ran):
        print(f"  {e}: {len(good)} of {a.runs} run(s) scored; working out what the pipeline would do...", flush=True)
        results.append(summarise(res, good, truth, job_data, out / "decisions", a.max_decisions))
        ok[e] = good
    result = {"job": str(a.job), "mock": a.mock or False, "runs": a.runs, "engines": results,
              "cross_engine_agreement": cross_agreement(ok), "notes": job_data["notes"] + ([truth_note] if truth_note else []),
              "network_attempts_blocked": len(attempts), "phrases": [p["text"] for p in job_data["phr"]]}
    result["paragraph"] = paragraph(result)
    (out / "results.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    md = markdown(result)
    (out / "results.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"RESULTS: {out / 'results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
