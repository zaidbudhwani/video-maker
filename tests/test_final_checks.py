r"""Finished-video checks (video_tool/final_checks.py): synthetic broken videos MUST be flagged, clean ones MUST NOT.

    venv\Scripts\python tests\test_final_checks.py

No AI, no network. Videos are made with ffmpeg in a temp folder. Also checks that real finished videos from her sample
clips (out\demo-tour and friends, if present on this computer) get no warning, and that the pipeline adds the warnings.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from video_tool import final_checks as fc, pipeline  # noqa: E402
from video_tool.config import FFMPEG  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not cond:
        fails.append(name)


tmp = Path(tempfile.mkdtemp(prefix="alex_fc_"))
S = "1080x1920"


def src(t0, dur):
    """Moving test picture starting at its own second t0 (different t0 = different footage)."""
    return f"testsrc2=s={S}:r=30:d={t0 + dur},trim=start={t0},setpts=PTS-STARTPTS,format=yuv420p"


def make(name, vgraph, adur, inputs=None):
    """Encode a video from a filter graph ([v] out) plus a sine voice of adur seconds, like a finished video."""
    out = tmp / name
    ff("-f", "lavfi", "-i", f"sine=frequency=220:duration={adur}", "-filter_complex", vgraph, "-map", "[v]", "-map", "0:a",
       "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", out)
    return out


ASS_HEAD = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n\n[V4+ Styles]\n"
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,"
            "StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            "Style: Default,Arial,{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,6,0,2,150,150,"
            "{margin},1\n\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")


def ass(name, size, margin, lines):
    body = "".join(f"Dialogue: 0,0:00:{a:05.2f},0:00:{b:05.2f},Default,,0,0,0,,{t}\n" for a, b, t in lines)
    p = tmp / name
    p.write_text(ASS_HEAD.format(size=size, margin=margin) + body, encoding="utf-8")
    return p


def burn(video, ass_path, name):
    out = tmp / name
    ff("-i", video, "-vf", f"subtitles={ass_path.name}", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "copy", out.name)
    return out


# ffmpeg runs in the temp folder: the subtitles filter then gets a bare file name (no drive-letter escaping)
def ff(*args):
    p = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], capture_output=True, text=True,
                       cwd=tmp)
    assert p.returncode == 0, p.stderr[-800:]


WORDS = [{"word": w, "start": 0.4 + i * 0.5, "end": 0.8 + i * 0.5} for i, w in enumerate(
    "come take a look at the large deck and the attached garage out back today".split())]
NORMAL = [(0.2, 2.0, "come take a look"), (2.0, 4.0, "at the large deck"), (4.0, 6.0, "and the attached garage")]


def kinds(ws):
    return sorted({w["check"] for w in ws})


try:
    clean = make("clean.mp4", f"{src(0, 6)}[v]", 6)
    cap_ass = ass("normal.ass", 78, 691, NORMAL)
    clean_cap = burn(clean, cap_ass, "clean_cap.mp4")
    ws = fc.check(clean_cap, cap_ass, WORDS)
    check("clean captioned video: no warning", ws == [], kinds(ws))
    # a tripod-like slow shot: barely moving, but moving (must not count as frozen)
    slow = make("slow.mp4", f"testsrc2=s={S}:r=1:d=6,fps=30,format=yuv420p,"
                            f"noise=alls=4:allf=t[v]", 6)
    ws = fc.check(slow, None, WORDS)
    check("steady shot with sensor noise: not called frozen", "frozen" not in kinds(ws) and "hold" not in kinds(ws), kinds(ws))

    # 1. black stretch of 1 s in the middle
    black = make("black.mp4", f"{src(0, 2)}[a];color=black:s={S}:r=30:d=1,format=yuv420p[b];{src(10, 2)}[c];"
                              "[a][b][c]concat=n=3:v=1:a=0[v]", 5)
    ws = fc.check(burn(black, cap_ass, "black_cap.mp4"), cap_ass, WORDS)
    b = [w for w in ws if w["check"] == "black"]
    check("black stretch flagged", len(b) == 1 and 1.8 <= b[0]["at"] <= 2.2, [(w["check"], w["at"]) for w in ws])
    check("black warning says where and what to do", b and "0:02" in b[0]["message"] and "Next step" in b[0]["message"])
    short_black = make("black_short.mp4", f"{src(0, 2)}[a];color=black:s={S}:r=30:d=0.3,format=yuv420p[b];{src(10, 2)}[c];"
                                          "[a][b][c]concat=n=3:v=1:a=0[v]", 4.3)
    check("a 0.3 s dip to black is not flagged", "black" not in kinds(fc.check(short_black)))

    # 2. frozen tail: the last 2 s hold the last frame while she still talks
    frozen = make("frozen.mp4", f"{src(0, 4)},tpad=stop_mode=clone:stop_duration=2[v]", 6)
    ws = fc.check(burn(frozen, cap_ass, "frozen_cap.mp4"), cap_ass, WORDS)
    f = [w for w in ws if w["check"] == "frozen"]
    check("frozen ending while talking flagged", len(f) == 1 and 3.8 <= f[0]["at"] <= 4.2, [(w["check"], w["at"]) for w in ws])
    check("frozen warning quotes her words and says 'at the end'",
          f and "at the end" in f[0]["message"] and "garage" in f[0]["message"], f and f[0]["message"])
    check("same frozen tail with no words: no 'talking' warning (2 s is not a long hold)",
          kinds(fc.check(frozen, None, [])) == [])
    hold = make("hold.mp4", f"{src(0, 2)},tpad=stop_mode=clone:stop_duration=5[v]", 7)
    check("5 s still hold with no words flagged as a long hold", "hold" in kinds(fc.check(hold, None, [])))

    # 3. drift: sound 1 s longer than picture
    drift = make("drift.mp4", f"{src(0, 4)}[v]", 5)
    ws = fc.check(drift)
    check("picture/sound drift flagged", kinds(ws) == ["drift"], [w["message"] for w in ws])

    # 4. repeated segment: footage A, other footage, A again
    rep = make("repeat.mp4", f"{src(0, 2)}[a];{src(10, 2)}[b];{src(0, 2)}[c];[a][b][c]concat=n=3:v=1:a=0[v]", 6)
    rep_cap = burn(rep, cap_ass, "repeat_cap.mp4")
    spans = [(0, 2, "Clip 1 (kitchen.mp4)"), (2, 4, "Clip 2 (deck.mp4)"), (4, 6, "Clip 3 (kitchen copy.mp4)")]
    ws = fc.check(rep_cap, cap_ass, WORDS, spans)
    r = [w for w in ws if w["check"] == "repeat"]
    check("repeated footage flagged once", len(r) == 1 and 3.8 <= r[0]["at"] <= 4.2, [(w["check"], w["at"]) for w in ws])
    check("repeat warning names both times and the clips", r and "0:00" in r[0]["message"] and "0:04" in r[0]["message"]
          and "kitchen copy.mp4" in r[0]["message"], r and r[0]["message"])

    check("repeat advice uses supported upload actions, not removal in Clip order", r
          and "Make another video" in r[0]["message"] and "when you upload" in r[0]["message"]
          and "Clip order" not in r[0]["message"], r and r[0]["message"])
    black_advice = [w for w in fc.check(black) if w["check"] == "black"]
    check("black advice uses supported replacement upload action", black_advice
          and "Make another video" in black_advice[0]["message"]
          and "Clip order" not in black_advice[0]["message"], black_advice)

    # 5. oversized captions: huge text and a long word that cannot wrap, and captions sunk into the bottom 20%
    big = ass("big.ass", 200, 691, [(0.2, 3.0, "Unbelievablyspaciousbackyard")])
    ws = fc.check(burn(clean, big, "big_cap.mp4"), big, WORDS)
    check("oversized caption running off the side flagged", "captions" in kinds(ws), kinds(ws))
    low = ass("low.ass", 78, 40, [(0.2, 3.0, "come take a look")])
    ws = fc.check(burn(clean, low, "low_cap.mp4"), low, WORDS)
    c = [w for w in ws if w["check"] == "captions"]
    check("caption in the bottom 20% flagged with a next step", c and "bottom" in c[0]["message"]
          and "Smaller" in c[0]["message"], c and c[0]["message"])
    maxed = ass("max.ass", 110, int(1920 * 0.33), [(0.2, 3.0, "come take a look"), (3.0, 5.0, "at the large deck")])
    check("largest allowed caption size at the lowest allowed height: not flagged",
          kinds(fc.check(burn(clean, maxed, "max_cap.mp4"), maxed, WORDS)) == [])

    # 6. true peak: reuses the measured value
    check("true peak over the limit flagged", kinds(fc.check(clean, None, WORDS, true_peak=-0.2)) == ["peak"])
    check("true peak at the limit not flagged", kinds(fc.check(clean, None, WORDS, true_peak=-1.0)) == [])

    # 7. never stops a video
    bad = tmp / "not_a_video.mp4"
    bad.write_bytes(b"nope")
    check("unreadable file: no warning, no crash", fc.check(bad) == [])

    # 8. the pipeline hook adds them to the warnings, with the part number for split videos
    got = pipeline.finished_warnings(rep_cap, cap_ass, WORDS, [{"clip": "03.mp4", "start": 4, "end": 6}],
                                     {"03.mp4": "Clip 3 (kitchen copy.mp4)"}, None, part=2)
    check("pipeline hook: warning tagged with the part", got and got[0]["part"] == 2
          and got[0]["message"].startswith("Part 2: ") and got[0]["kind"] == "final_check", got)
    check("pipeline hook: missing captions file is fine", pipeline.finished_warnings(clean, tmp / "none.ass", WORDS, None, {},
                                                                                     None) == [])

    # Offset is on the encoded part's clock, not the whole job's clock.
    from unittest.mock import patch
    with patch.object(fc, "check", return_value=[]) as inspect_check:
        pipeline.finished_warnings(clean, cap_ass, [],
                                   [{"clip": "first", "start": 0, "end": 50},
                                    {"clip": "second", "start": 50, "end": 100}],
                                   {"second": "Kitchen.mov"}, None, part=2, offset=50)
        spans = inspect_check.call_args.args[3]
        check("Part 2 warning names use the part's clock", fc._clip_at(spans, 4) == "Kitchen.mov", spans)

    # Force a small ceiling and observe every square allocation entering sqrt.
    import numpy as np
    rng = np.random.default_rng(4)
    fr = rng.uniform(50, 200, (301, 1, fc.TW)).astype(np.float32)
    sizes = []
    sqrt = np.sqrt
    def observe(a):
        sizes.append(a.shape)
        return sqrt(a)
    with patch.object(fc, "REPEAT_MAX_FRAMES", 60), patch.object(fc.np, "sqrt", side_effect=observe):
        fc.check_repeat(30, fr, np.ones(1, bool))
    check("repeat matrix stays within the sample ceiling on long input", sizes and max(sizes[0]) <= 60, sizes)

    # 9. her real sample-made videos (saved jobs on this computer, read-only): none flagged
    real = [ROOT / "out" / j for j in ("demo-tour", "demo-recipe", "demo-tips")]
    real = [j for j in real if (j / "final.mp4").exists()]
    if not real:
        print("SKIP real sample videos (no saved demo jobs on this computer)")
    for j in real:
        words = None
        try:
            import json
            words = json.loads((j / "words_trimmed.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        rep_j = json.loads((j / "report.json").read_text(encoding="utf-8"))
        ws = fc.check(j / "final.mp4", j / "captions.ass" if (j / "captions.ass").exists() else None, words,
                      fc.spans_from_timeline(rep_j.get("timeline")))
        check(f"real sample video {j.name}: no warning", ws == [], [w["message"] for w in ws])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILED: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
