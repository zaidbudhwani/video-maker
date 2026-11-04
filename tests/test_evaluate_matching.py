r"""The evaluation runner (tests/evaluate_matching.py) and the answer-key page (tests/answer_key_tool.py).
Run: venv\Scripts\python tests\test_evaluate_matching.py
Synthetic jobs and keys whose numbers are worked out by hand below; no footage, no AI, nothing leaves this computer."""
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

os.environ["ANTHROPIC_API_KEY"] = ""
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import evaluate_matching as E   # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {detail}"))
    if not cond:
        FAILS.append(name)


def near(a, b, tol=0.005):
    return a is not None and abs(a - b) < tol


TMP = Path(tempfile.mkdtemp(prefix="evalmatch-"))


def make_job(name, phrases, clips, timeline, words, alts=None):
    j = TMP / name
    j.mkdir()
    (j / "words_trimmed.json").write_text(json.dumps(words), encoding="utf-8")
    (j / "report.json").write_text(json.dumps({"length": phrases[-1]["end"]}), encoding="utf-8")
    (j / "timeline.json").write_text(json.dumps(timeline), encoding="utf-8")
    for n, tl in (alts or {}).items():
        (j / f"timeline.{n}.json").write_text(json.dumps(tl), encoding="utf-8")
    snap = {"job": name, "length": phrases[-1]["end"], "phrases": phrases, "clips": clips}
    return j, snap


def save_key(job, snap, rater, marks, house):
    k = E.new_key(snap, rater, house)
    for c, (ill, con) in marks.items():
        k["marks"][c] = {"illustrates": ill, "contradicts": con}
    E.check_key(k)
    E.key_path(job, rater).write_text(json.dumps(k), encoding="utf-8")
    return k


def tl(*parts):
    return [{"clip": c, "start": s, "end": e} for c, s, e in parts]


try:
    # House 1: 4 phrases of 5 s; c1 shows phrase 0 (contradicts 3), c2 shows phrase 1 (contradicts 2). Finished video: c1 for
    # 0-10 s, c2 for 10-20 s. By hand: supported 10 of 20 s; right 5 s (c1 in phrase 0); contradicting 5 s (c2 in phrase 2,
    # unsupported). Coverage 50%, accuracy when supported 50%, across all 25%, contradictions 0% supported / 25% all.
    P1 = [{"start": 5.0 * i, "end": 5.0 * (i + 1), "text": f"phrase {i}."} for i in range(4)]
    W1 = [{"word": f"w{i}.", "start": 5.0 * i, "end": 5.0 * i + 4.9} for i in range(4)]
    j1, s1 = make_job("tour-1", P1, [{"name": "c1.mp4", "usable": 10.0}, {"name": "c2.mp4", "usable": 10.0}],
                      tl(("c1.mp4", 0, 10), ("c2.mp4", 10, 20)), W1)
    kA = save_key(j1, s1, "A", {"c1.mp4": ([0], [3]), "c2.mp4": ([1], [2])}, "house-1")
    # rater B differs on one pair only: c1 x phrase 3 is "neither" instead of "contradicts"
    kB = save_key(j1, s1, "B", {"c1.mp4": ([0], []), "c2.mp4": ([1], [2])}, "house-1")
    # House 2: 2 phrases of 5 s, c1 shows 0, c2 shows 1, finished video is perfect (10 of 10 s). A saved alternative swaps them (0%).
    P2 = [{"start": 0.0, "end": 5.0, "text": "one."}, {"start": 5.0, "end": 10.0, "text": "two."}]
    W2 = [{"word": "one.", "start": 0.0, "end": 4.9}, {"word": "two.", "start": 5.0, "end": 9.9}]
    j2, s2 = make_job("tour-2", P2, [{"name": "c1.mp4", "usable": 5.0}, {"name": "c2.mp4", "usable": 5.0}],
                      tl(("c1.mp4", 0, 5), ("c2.mp4", 5, 10)), W2, alts={"local": tl(("c2.mp4", 0, 5), ("c1.mp4", 5, 10))})
    save_key(j2, s2, "A", {"c1.mp4": ([0], []), "c2.mp4": ([1], [])}, "house-2")

    res = E.evaluate([j1, j2], boot=4000, seed=7)
    rows = {(r["job"], r["rater"], r["timeline"]): E.ratios(r["sec"]) for r in res["rows"]}
    f = rows[("tour-1", "A", "finished")]
    check("job figures by hand: coverage 50%, accuracy when supported 50%, across all 25%",
          near(f["coverage"], .5) and near(f["acc_supported"], .5) and near(f["acc_all"], .25), f)
    check("job figures by hand: contradictions 0% during supported narration, 25% across all",
          near(f["contradict_supported"], 0) and near(f["contradict_all"], .25), f)
    check("a saved alternative timeline (timeline.local.json) is scored: swapped clips are 0% right",
          near(rows[("tour-2", "A", "alt:local")]["acc_all"], 0), rows.get(("tour-2", "A", "alt:local")))
    check("her own order is scored from the clip lengths in the key (house 2: cut at the sentence break, 100%)",
          near(rows[("tour-2", "A", "her_order")]["acc_all"], 1.0), rows.get(("tour-2", "A", "her_order")))

    pooled = {(p["scope"], p["rater"], p["timeline"]): p for p in res["pooled"]}
    pa = pooled[("all houses", "A", "finished")]
    r = E.ratios(pa["sec"])
    # pooled seconds: narration 20+10=30, supported 10+10=20, right 5+10=15
    check("pooled figures add seconds, not percentages: coverage 20/30, accuracy when supported 15/20, across all 15/30",
          near(r["coverage"], 2 / 3) and near(r["acc_supported"], .75) and near(r["acc_all"], .5), r)
    # bootstrap over 2 houses: draws are {1,1} 25%, {2,2} 100% or mixed 50%; each pure draw has chance 1/4, so the 2.5% and
    # 97.5% points are exactly 25% and 100%
    ci = pa["ci"]["acc_all"]
    check("bootstrap interval over houses is [25%, 100%] for accuracy across all narration (worked out by hand)",
          near(ci[0], .25) and near(ci[1], 1.0), ci)
    again = E.evaluate([j1, j2], boot=4000, seed=7)
    ci2 = next(p for p in again["pooled"] if p["scope"] == "all houses" and p["rater"] == "A" and p["timeline"] == "finished")["ci"]
    check("the bootstrap is reproducible with a fixed seed (same seed, same interval to the last digit)", ci2 == pa["ci"], (ci2, pa["ci"]))
    a1 = E.bootstrap({"x": {"narration_s": 10, "supported_s": 10, "ok_all_s": 1, "bad_all_s": 0, "ok_sup_s": 1, "bad_sup_s": 0},
                      "y": {"narration_s": 10, "supported_s": 10, "ok_all_s": 9, "bad_all_s": 0, "ok_sup_s": 9, "bad_sup_s": 0},
                      "z": {"narration_s": 10, "supported_s": 10, "ok_all_s": 5, "bad_all_s": 0, "ok_sup_s": 5, "bad_sup_s": 0}},
                     n=50, seed=1)
    a2 = E.bootstrap({"x": {"narration_s": 10, "supported_s": 10, "ok_all_s": 1, "bad_all_s": 0, "ok_sup_s": 1, "bad_sup_s": 0},
                      "y": {"narration_s": 10, "supported_s": 10, "ok_all_s": 9, "bad_all_s": 0, "ok_sup_s": 9, "bad_sup_s": 0},
                      "z": {"narration_s": 10, "supported_s": 10, "ok_all_s": 5, "bad_all_s": 0, "ok_sup_s": 5, "bad_sup_s": 0}},
                     n=50, seed=2)
    a1b = E.bootstrap({h: dict(v) for h, v in {"x": {"narration_s": 10, "supported_s": 10, "ok_all_s": 1, "bad_all_s": 0, "ok_sup_s": 1, "bad_sup_s": 0},
                      "y": {"narration_s": 10, "supported_s": 10, "ok_all_s": 9, "bad_all_s": 0, "ok_sup_s": 9, "bad_sup_s": 0},
                      "z": {"narration_s": 10, "supported_s": 10, "ok_all_s": 5, "bad_all_s": 0, "ok_sup_s": 5, "bad_sup_s": 0}}.items()},
                      n=50, seed=1)
    check("three houses, 50 draws, seed 1 twice: the same interval (a fixed seed reproduces the run)", a1 == a1b, (a1, a1b))
    check("...and a different seed gives a different set of draws (so the seed is really used)", a1["acc_all"] != a2["acc_all"], (a1, a2))

    # agreement by hand: 8 pairs, 7 the same = 87.5%. A's labels: neither 4, shows 2, contradicts 2; B's: 5, 2, 1.
    # chance = 4/8*5/8 + 2/8*2/8 + 2/8*1/8 = 26/64; kappa = (0.875 - 26/64) / (1 - 26/64) = 0.7895
    ag = E.agreement(kA, kB)
    check("percent agreement over (clip, phrase) pairs: 7 of 8 = 87.5%", near(ag["percent"], .875), ag)
    check("Cohen's kappa worked out by hand: 0.7895", near(ag["kappa"], (0.875 - 26 / 64) / (1 - 26 / 64), 1e-6), ag)
    check("on the yes/no 'shows it' question the raters agree fully (kappa 1)", near(ag["percent_shows"], 1) and near(ag["kappa_shows"], 1), ag)
    check("the runner reports agreement when two keys exist for a job", len(res["agreement"]) == 1 and res["agreement"][0]["job"] == "tour-1", res["agreement"])
    same = E.agreement(kA, kA)
    check("identical keys: 100% and kappa 1", near(same["percent"], 1) and near(same["kappa"], 1), same)
    blank = E.new_key(s1, "C")
    check("two blank keys agree 100% but kappa is undefined (all chance), shown as none rather than a made-up number",
          E.agreement(blank, blank)["kappa"] is None)

    # held-out houses
    check("two houses are too few to hold any back by default", res["holdout"] == [], res["holdout"])
    check("with three or more houses one in three is held out, the same pick every time",
          len(E.default_holdout(["a", "b", "c"])) == 1 and E.default_holdout(["c", "b", "a"]) == E.default_holdout(["a", "b", "c"]))
    rh = E.evaluate([j1, j2], holdout=["house-2"], boot=200, seed=0)
    ph = {(p["scope"], p["rater"], p["timeline"]): p for p in rh["pooled"]}
    check("a held-out house is reported apart: held-out = house 2 only (100%), tuning = house 1 only (25%)",
          near(E.ratios(ph[("held-out houses", "A", "finished")]["sec"])["acc_all"], 1.0)
          and near(E.ratios(ph[("tuning houses", "A", "finished")]["sec"])["acc_all"], .25), list(ph))
    check("a pool of one house gets no interval (cannot bootstrap over one house)", ph[("held-out houses", "A", "finished")]["ci"] is None)

    # Claude minus her order, same houses drawn for both
    d = next(x for x in res["diffs"] if x["timeline"] == "finished" and x["rater"] == "A")
    # her order: house 1 plan_slots gives c1 0-10, c2 10-20 (same as finished: 25%); house 2 100%: pooled 50%, diff 0
    check("difference against her own order uses the same houses: here both pool to 50%, difference 0", near(d["diff"], 0), d)

    # report and CSV
    lines = []
    E.report(res, lines.append)
    text = "\n".join(lines)
    check("the printed report names the four figures, the interval and what the numbers do NOT show",
          "accuracy across all narration" in text and "95% interval" in text and "do NOT show" in text and "kappa" in text, text[:300])
    csvp = TMP / "out.csv"
    E.write_csv(res, csvp)
    import csv
    crow = list(csv.DictReader(open(csvp, encoding="utf-8")))
    prow = next(c for c in crow if c["scope"] == "all houses" and c["rater"] == "A" and c["timeline"] == "finished")
    check("the CSV holds per-job and pooled rows with the interval", float(prow["acc_all"]) == 0.5 and float(prow["acc_all_low"]) == 0.25
          and any(c["scope"] == "job" for c in crow), prow)

    # a broken key is refused, not scored
    bad = E.new_key(s1, "D")
    bad["marks"]["c1.mp4"] = {"illustrates": [0], "contradicts": [0]}
    try:
        E.check_key(bad)
        ok = False
    except ValueError:
        ok = True
    check("a clip marked both 'shows it' and 'contradicts' for one phrase is refused", ok)
    try:
        E.key_path(TMP, "../x")
        ok = False
    except ValueError:
        ok = True
    check("a rater name cannot reach another folder", ok)
    check("a job without a key is left out with a note, not scored", any("no answer key" in n for n in
          E.evaluate([j1, j2, TMP / "missing"], boot=10)["notes"]))

    # The CLI must distinguish a bad path from an existing job without a key.
    import subprocess
    for badpath in (TMP / "missing", j1 / "report.json"):
        cp = subprocess.run([sys.executable, str(ROOT / "tests/evaluate_matching.py"), str(badpath)],
                            capture_output=True, text=True)
        check("CLI rejects a missing folder or file with an actionable error", cp.returncode == 2
              and str(badpath) in cp.stderr and "Check the spelling" in cp.stderr
              and "no answer key" not in cp.stdout, cp.stdout + cp.stderr)
    empty = TMP / "no-key"
    empty.mkdir()
    cp = subprocess.run([sys.executable, str(ROOT / "tests/evaluate_matching.py"), str(empty)],
                        capture_output=True, text=True)
    check("CLI still permits an existing folder without an answer key", cp.returncode == 0
          and "no answer key, left out" in cp.stdout, cp.stdout + cp.stderr)

    # ---------------------------------------------------------------- the answer-key page's helper (local only)
    import answer_key_tool as T
    port = int(os.environ.get("E2E_PORT") or 8321)
    clipdir = TMP / "clips"
    clipdir.mkdir()
    (clipdir / "c1.mp4").write_bytes(bytes(range(256)) * 4)
    (clipdir / "c2.mp4").write_bytes(b"x" * 100)
    snap = dict(s1, clips_dir=str(clipdir))
    keys_dir = TMP / "keys-page"
    srv = T.serve(j1, "R1", port=port, save_dir=keys_dir, house="house-1", snap=snap)
    base = f"http://127.0.0.1:{port}"
    try:
        check("the helper listens on this computer only", srv.server_address[0] == "127.0.0.1", srv.server_address)
        page = urllib.request.urlopen(base + "/").read().decode("utf-8")
        check("the page loads with 44 px buttons and a visible focus ring", "min-height:44px" in page and "focus-visible" in page
              and "prefers-reduced-motion" in page)
        check("the page calls no outside address", "http://" not in page.replace("http://127.0.0.1", "") and "https://" not in page)
        d0 = json.loads(urllib.request.urlopen(base + "/data").read())
        check("the page gets the job's phrases and clips, nothing marked yet", len(d0["phrases"]) == 4 and d0["clips"] == ["c1.mp4", "c2.mp4"]
              and not d0["saved"], d0)
        body = json.dumps({"marks": {"c1.mp4": {"illustrates": [0], "contradicts": [3]}, "c2.mp4": {"illustrates": [1], "contradicts": [2]}}}).encode()
        rq = urllib.request.Request(base + "/save", data=body, headers={"Content-Type": "application/json"}, method="POST")
        out = json.loads(urllib.request.urlopen(rq).read())
        saved = E.load_key(keys_dir / "answer_key.R1.json")
        check("saving writes answer_key.<rater>.json that the runner reads", out.get("ok") and saved["rater"] == "R1"
              and saved["house"] == "house-1" and saved["marks"]["c1.mp4"]["contradicts"] == [3], saved.get("marks"))
        check("the saved key from the page scores the same as the hand-made one", E.agreement(saved, kA)["percent"] == 1.0)
        d1 = json.loads(urllib.request.urlopen(base + "/data").read())
        check("reopening the page loads the earlier marks", d1["saved"] and d1["marks"]["c2.mp4"]["illustrates"] == [1], d1)
        badrq = urllib.request.Request(base + "/save", data=json.dumps({"marks": {"c1.mp4": {"illustrates": [0], "contradicts": [0]}}}).encode(),
                                       method="POST")
        try:
            urllib.request.urlopen(badrq)
            code = 200
        except urllib.error.HTTPError as e:
            code = e.code
        check("a contradictory mark is refused with a message and the saved key is untouched", code == 400
              and E.load_key(keys_dir / "answer_key.R1.json")["marks"]["c1.mp4"]["contradicts"] == [3], code)
        rr = urllib.request.urlopen(urllib.request.Request(base + "/clip/c1.mp4", headers={"Range": "bytes=10-19"}))
        check("clips are served with byte ranges so the video can be scrubbed", rr.status == 206 and rr.read() == bytes(range(10, 20)), rr.status)
        for path in ("/clip/..%2Fanswer_key.A.json", "/clip/" + str(TMP / "out.csv").replace("\\", "/")):
            try:
                urllib.request.urlopen(base + path)
                code = 200
            except urllib.error.HTTPError as e:
                code = e.code
            check(f"only the job's own clips are served ({path[:30]}...)", code == 404, code)
    finally:
        srv.shutdown()
        srv.server_close()

    # the earlier one-tour model-made key through the new runner must give the published figures
    job = ROOT / "out" / "engine-A-claude"
    legacy = Path("answer-key.json")
    if job.joinpath("timeline.json").exists() and legacy.exists() and (ROOT / "samples" / "tour").is_dir():
        kd = TMP / "legacy"
        E.import_legacy(legacy, job, str(ROOT / "samples" / "tour"), kd)
        lr = E.evaluate([job], keys_dirs=[kd], boot=10)
        lf = {x["timeline"]: E.ratios(x["sec"]) for x in lr["rows"]}
        check("saved tour through the new runner: Claude 81.7% when supported, 47.7% across all, coverage 58.4%",
              near(lf["finished"]["acc_supported"], .817, .001) and near(lf["finished"]["acc_all"], .477, .001)
              and near(lf["finished"]["coverage"], .584, .001), lf.get("finished"))
        # Phase 3 moved her-order cuts to phrase pauses: 24.3% / 14.2% before it (5608fc6), 25.9% / 15.1% after
        check("saved tour: her own order 25.9% / 15.1%, contradictions 34.5% / 20.2%", near(lf["her_order"]["acc_supported"], .259, .001)
              and near(lf["her_order"]["acc_all"], .151, .001) and near(lf["her_order"]["contradict_supported"], .345, .001)
              and near(lf["her_order"]["contradict_all"], .202, .001), lf.get("her_order"))
    else:
        print("SKIP the saved-tour check: out/engine-A-claude, samples/tour or the old answer key is not here")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED'}")
sys.exit(1 if FAILS else 0)
