""""Change this shot": pins that put her chosen clip on screen at one moment of the finished video.

    venv\\Scripts\\python tests\\test_pins.py

1. settings.clean_pins / merge_pins: junk is dropped, times clamped, a later choice for the same moment wins,
   pins are never remembered for future videos.
2. match.apply_pins on synthetic plans: only the pinned slot (and, when her clip played elsewhere, the slot it came
   from) changes; a too-short clip moves one cut only; impossible pins are skipped with a reason.
3. match.match_word: "possible match" / "not sure" / "no footage for this" / nothing without scores.
4. Effect proof, rendered on this laptop with four solid-colour clips and a tone as the voice (no Whisper, no AI, no
   key): the real pipeline makes a video, then a remake with one pin. The timelines are compared slot by slot, and the
   finished video's colour at the pinned moment is measured. Run once with matching off (no key) and once with
   matching on through a FAKE scorer (no outside call), in-process through assemble.assemble.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ANTHROPIC_API_KEY"] = ""          # nothing may reach Claude from this file
os.environ["OPENROUTER_API_KEY"] = ""
from video_tool import match, settings  # noqa: E402
from video_tool.config import FFMPEG  # noqa: E402

failed = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  - ' + str(detail) if detail else ''}", flush=True)
    if not ok:
        failed.append(name)


print("clean_pins")
P = settings.clean_pins
check("a good pin is kept", P([{"start": 3, "end": 6.5, "clip": 2}]) == [{"start": 3.0, "end": 6.5, "clip": 2}])
check("not a list -> none", P("clip 2") == [] and P(None) == [] and P({"start": 1}) == [])
check("junk entries dropped", P([{"start": "1", "end": 2, "clip": 1}, {"start": 1, "end": 2, "clip": True},
                                 {"start": 1, "end": 2, "clip": 0}, {"start": 1, "end": 2, "clip": 1.5},
                                 {"start": 5, "end": 4, "clip": 1}, 7, {"start": 1, "end": 2}]) == [])
check("times clamped to 0..600", P([{"start": -4, "end": 9999, "clip": 3}]) == [{"start": 0.0, "end": 600.0, "clip": 3}])
check("NaN / huge numbers are not a crash", P([{"start": float("nan"), "end": 2, "clip": 1}, {"start": 0, "end": 1, "clip": 1e400}]) in ([],))
check("later choice for the same moment wins", P([{"start": 2, "end": 5, "clip": 1}, {"start": 3, "end": 4, "clip": 2}])
      == [{"start": 3.0, "end": 4.0, "clip": 2}])
check("separate moments both kept, in time order", [p["clip"] for p in P([{"start": 9, "end": 11, "clip": 1},
                                                                           {"start": 1, "end": 3, "clip": 2}])] == [2, 1])
check("at most MAX_PINS", len(P([{"start": i, "end": i + 0.5, "clip": 1} for i in range(80)])) == settings.MAX_PINS)
check("merge: a new pin replaces the old one at the same moment, others stay",
      settings.merge_pins([{"start": 0, "end": 2, "clip": 1}, {"start": 4, "end": 6, "clip": 2}],
                          [{"start": 4.5, "end": 5.5, "clip": 3}]) == [{"start": 0.0, "end": 2.0, "clip": 1},
                                                                       {"start": 4.5, "end": 5.5, "clip": 3}])
check("clamp() tidies pins in a run", settings.clamp({**settings.DEFAULTS, "pins": [{"start": 1, "end": 2, "clip": "x"}]})["pins"] == [])
check("pins are never remembered for future videos", "pins" not in settings.REMEMBER_KEYS)
with tempfile.TemporaryDirectory() as td:
    pf = Path(td) / "prefs.json"
    settings.remember({"pins": [{"start": 1, "end": 2, "clip": 1}], "caption_size": 90}, pf)
    check("remember() drops pins", "pins" not in json.loads(pf.read_text(encoding="utf-8")))

print("apply_pins")
A = match.apply_pins
order, slots, usable = [0, 1, 2, 3], [5.0, 5.0, 5.0, 5.0], [9.0, 9.0, 9.0, 9.0]
o, s, ap, sk = A(order, slots, usable, [{"start": 5.2, "end": 9.5, "clip": 4}])
check("pin swaps her clip into the slot, times unchanged", o == [0, 3, 2, 1] and s == slots and not sk, (o, s, sk))
check("applied names the slot and its times", ap == [{"pos": 1, "clip": 4, "start": 5.0, "end": 10.0}], ap)
o, s, ap, sk = A(order, slots, usable, [{"start": 1, "end": 2, "clip": 1}])
check("pinning the clip already there changes nothing", o == order and s == slots and len(ap) == 1)
o, s, ap, sk = A(order, slots, [9.0, 9.0, 9.0, 3.5], [{"start": 5, "end": 10, "clip": 4}])
check("a short clip moves only the next cut", o == [0, 3, 2, 1] and s[0] == 5.0 and s[1] == 3.5 and abs(sum(s) - 20) < 1e-6
      and all(s[i] <= [9, 3.5, 9, 9][i] + 1e-6 for i in range(4)), (o, s))
o, s, ap, sk = A(order, slots, [5.0, 5.0, 5.0, 1.0], [{"start": 5, "end": 10, "clip": 4}])
check("impossible (clip far too short, no spare footage): skipped, plan untouched", o == order and s == slots and sk
      and "too short" in sk[0]["why"], (o, s, sk))
o, s, ap, sk = A(order, slots, usable, [{"start": 1, "end": 2, "clip": 9}])
check("a clip that isn't in the video: skipped", o == order and sk and "isn't in this video" in sk[0]["why"])
o, s, ap, sk = A(order, slots, usable, [{"start": 30, "end": 31, "clip": 2}])
check("a moment past the end: skipped", o == order and sk)
o, s, ap, sk = A(order, slots, usable, [{"start": 0, "end": 4, "clip": 3}, {"start": 16, "end": 19, "clip": 3}])
check("the same clip can't be her choice twice", o == [2, 1, 0, 3] and len(ap) == 1 and len(sk) == 1, (o, sk))
o, s, ap, sk = A(order, slots, usable, [{"start": 0, "end": 4, "clip": 2}, {"start": 16, "end": 19, "clip": 1}])
check("two pins: the first one stays where she put it", o[0] == 1 and o[3] == 0 and len(ap) == 2, o)
o, s, ap, sk = A([2, 0, 1], [6.0, 6.0, 6.0], [9.0, 9.0, 9.0], [{"start": 13, "end": 17, "clip": 3}])
check("works on a matched (reordered) plan", o == [1, 0, 2] and s == [6.0, 6.0, 6.0], o)
o, s, ap, sk = A([0, 1], [8.0, 8.0], [float("inf")] * 2, [{"start": 9, "end": 10, "clip": 1}])
check("in-order mode (no length limits): order swaps", o == [1, 0])

print("match_word")
W = match.match_word
check("no scores -> no word", W([], None) is None)
check("nothing scores well anywhere -> no footage", W([2, 3], 3) == "no footage for this")
check("shown clip scores 7+ -> possible match", W([4, 8], 9) == "possible match")
check("in between -> not sure", W([5, 6], 8) == "not sure")


# ---------- 4. effect proof: render, pin, compare ----------
def ff(*args):
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", *map(str, args)], check=True)


COLOURS = {1: (230, 40, 40), 2: (40, 200, 60), 3: (40, 70, 230), 4: (235, 220, 40)}


def make_inputs(folder, voice_len=20.0):
    folder.mkdir(parents=True)
    for n, (r, g, b) in COLOURS.items():
        ff("-f", "lavfi", "-i", f"color=c=0x{r:02x}{g:02x}{b:02x}:s=540x960:r=30:d=9", "-f", "lavfi",
           "-i", "anullsrc=r=48000:cl=mono", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
           folder / f"{n:02d}.mp4")
    ff("-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:duration={voice_len}", "-c:a", "aac", folder / "voice.m4a")
    # a transcript with no pauses, so trimming keeps the whole voice; sentences every 5 s
    text = ("This is the front of the house. Here is the big kitchen with an island. The living room gets the light. "
            "Out back is the deck and the yard.").split()
    step = voice_len / len(text)
    return [{"word": w, "start": round(i * step, 3), "end": round((i + 1) * step - 0.01, 3), "p": 0.99}
            for i, w in enumerate(text)]


def colour_at(video, t):
    raw = subprocess.run([FFMPEG, "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                          "-vf", "crop=200:200:440:200,scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return tuple(raw[:3])


def nearest(rgb):
    return min(COLOURS, key=lambda n: sum((a - b) ** 2 for a, b in zip(COLOURS[n], rgb)))


def run_pipeline(env, folder, job, over, reuse):
    f = Path(env["ALEX_DATA_DIR"]) / f"{job}.settings.json"
    f.write_text(json.dumps(over), encoding="utf-8")
    p = subprocess.run([sys.executable, "-u", "-W", "ignore", "-m", "video_tool.pipeline", str(folder), "--job-id", job,
                        "--no-writer", "--no-music", "--settings", str(f), "--reuse", str(reuse)],
                       cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    out = Path(env["ALEX_DATA_DIR"]) / "out" / job
    if p.returncode or not (out / "report.json").exists():
        print((p.stdout + p.stderr)[-1500:])
        return None
    return json.loads((out / "report.json").read_text(encoding="utf-8"))


def compare(before, after, pinned_pos):
    """Slots other than the pinned one and the one her clip came from must be identical (clip and times)."""
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if (a["clip"], a["start"], a["end"]) != (b["clip"], b["start"], b["end"])]
    return changed


print("effect proof (renders on this laptop)")
if not shutil.which(FFMPEG) and not Path(FFMPEG).exists():
    print("  SKIP effect proof: ffmpeg is not installed")
else:
    tmp = Path(tempfile.mkdtemp(prefix="alex-pins-"))
    try:
        data = tmp / "data"
        (data / "out").mkdir(parents=True)
        prefs = tmp / "prefs.json"
        prefs.write_text("{}", encoding="utf-8")
        env = {**os.environ, "ALEX_DATA_DIR": str(data), "ANTHROPIC_API_KEY": "", "OPENROUTER_API_KEY": "",
               "ALEX_PREFS_FILE": str(prefs), "PYTHONIOENCODING": "utf-8"}
        words = make_inputs(tmp / "up")
        seed = tmp / "seed"   # an "earlier version" holding the transcript, so Whisper never runs
        seed.mkdir()
        (seed / "words.json").write_text(json.dumps(words), encoding="utf-8")
        base = run_pipeline(env, tmp / "up", "base", {"match_clips": False}, seed)
        check("base video made (matching off, no key)", base is not None)
        if base:
            tl0 = base["timeline"]
            check("base: every clip in her order, even split", [t["clip"] for t in tl0] == ["01.mp4", "02.mp4", "03.mp4", "04.mp4"], tl0)
            s1 = tl0[1]
            pin = {"start": s1["start"] + 0.5, "end": s1["end"] - 0.5, "clip": 4}
            rem = run_pipeline(env, tmp / "up", "pinned", {"match_clips": False, "pins": [pin]}, data / "out" / "base")
            check("remake with one pin made", rem is not None)
            if rem:
                tl1 = rem["timeline"]
                ch = compare(tl0, tl1, 1)
                check("only the pinned slot and the slot her clip came from changed", ch == [1, 3], (ch, tl1))
                check("pinned slot now shows Clip 4 at the same times", (tl1[1]["clip"], tl1[1]["start"], tl1[1]["end"])
                      == ("04.mp4", s1["start"], s1["end"]) and tl1[1].get("pinned") is True, tl1[1])
                check("voice-over length unchanged", abs(rem["length"] - base["length"]) < 0.05, (base["length"], rem["length"]))
                mid = (s1["start"] + s1["end"]) / 2
                got_b = nearest(colour_at(data / "out" / "base" / "final.mp4", mid))
                got_a = nearest(colour_at(data / "out" / "pinned" / "final.mp4", mid))
                check("finished video: the pinned moment shows Clip 4's picture (was Clip 2's)", (got_b, got_a) == (2, 4), (got_b, got_a))
                for i in (0, 2):
                    t = (tl0[i]["start"] + tl0[i]["end"]) / 2
                    check(f"finished video: slot {i + 1} picture unchanged",
                          nearest(colour_at(data / "out" / "base" / "final.mp4", t)) == nearest(colour_at(data / "out" / "pinned" / "final.mp4", t)))
                check("report lists the pin as applied", rem.get("pins", {}).get("applied", [{}])[0].get("clip") == 4, rem.get("pins"))
                check("pins kept in the version's settings (a further remake starts from them)", rem["settings"]["pins"] == [
                    {k: round(v, 2) if k != "clip" else v for k, v in pin.items()}], rem["settings"]["pins"])
                # a further remake of the same project with another change keeps the pin
                again = run_pipeline(env, tmp / "up", "again", {"match_clips": False, "pins": rem["settings"]["pins"], "caption_size": 90},
                                     data / "out" / "pinned")
                check("further remake keeps the pinned clip", again is not None and again["timeline"] == tl1,
                      again and again["timeline"])

        # matching ON through a fake scorer, in-process (no outside call): the plan is matched, the pin still holds
        print("  matching on, fake scorer")
        from video_tool import assemble
        os.environ["ALEX_PREFS_FILE"] = str(prefs)
        calls = []

        def fake_scores(passes, frames, per_clip, phr, *a, **k):
            calls.append(1)
            # clip k shows phrase (3 - k): her order is reversed against her words, so the matcher reorders
            sc = [[[9 if j == len(phr) - 1 - min(k, len(phr) - 1) else 2 for j in range(len(phr))] for _ in t]
                  for k, t in enumerate(per_clip)]
            return sc, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}, 1

        match.ai.has_key = lambda: True
        match.ask_claude_times = fake_scores
        match.extract_frames = lambda checked, per_clip, out_dir: None
        clips = sorted((tmp / "up").glob("0*.mp4"))
        plans = {}
        for name, over in (("m0", {}), ("m1", None)):
            if over is None:
                mid = (plans["m0"][1]["start"] + plans["m0"][1]["end"]) / 2
                want = int(plans["m0"][3]["clip"][:2])   # the clip at the end goes to the second slot
                over = {"pins": [{"start": mid - 0.3, "end": mid + 0.3, "clip": want}]}
            settings.configure({"match_clips": True, "skip_dead_start": False, "stabilize": False, **over})
            jd = tmp / name
            jd.mkdir()
            _, fit = assemble.assemble(clips, 20.0, jd, words)
            plans[name] = fit["timeline"]
            if name == "m0":
                check("fake matcher was used and moved clips", fit["clip_match"].get("used") and calls, fit["clip_match"])
        a, b = plans["m0"], plans["m1"]
        ch = compare(a, b, 1)
        check("matched plan + pin: only the pinned slot and its swap partner changed", len(ch) == 2 and 1 in ch, (a, b))
        check("matched plan + pin: her clip is on screen there", b[1]["clip"] == f"{want:02d}.mp4" and b[1].get("pinned"), b[1])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

print()
print("FAILED: " + ", ".join(failed) if failed else "All pin tests passed.")
sys.exit(1 if failed else 0)
