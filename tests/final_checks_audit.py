r"""Run the finished-video checks over saved jobs (read-only) and count the warnings each video gets.

    venv\Scripts\python tests\final_checks_audit.py [out folder] [--stats]

Reads out\<job>\final*.mp4 with its captions*.ass and words; writes nothing. A good video should get no warnings.
--stats also prints the raw measurements (smallest frame change, darkest stretch) used to set the thresholds.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from video_tool import final_checks as fc  # noqa: E402


def words_for(job, video):
    if video.name != "final.mp4":
        return None   # parts: words are on the whole timeline; the frozen-while-talking check falls back to 'long hold'
    for n in ("words_trimmed.json",):
        try:
            return json.loads((job / n).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    try:
        rep = json.loads((job / "report.json").read_text(encoding="utf-8"))
        if rep.get("mode") == "own_audio" and len(rep.get("parts", [])) == 1:
            return json.loads((job / "words.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return None


def main(argv):
    out = Path(next((a for a in argv if not a.startswith("-")), ROOT / "out"))
    total = flagged = 0
    counts = {}
    t0 = time.time()
    jobs = sorted(p for p in out.iterdir() if p.is_dir())
    if "--sample" in argv:   # every Nth job, about 25 in all (the full folder takes hours)
        jobs = jobs[::max(1, len(jobs) // 25)]
    for job in jobs:
        try:
            rep = json.loads((job / "report.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rep = {}
        for video in sorted(job.glob("final*.mp4")):
            if video.stem.endswith(("_loud", "_peak")):
                continue
            tag = video.stem.replace("final", "")
            ass = job / f"captions{tag}.ass"
            spans = fc.spans_from_timeline(rep.get("timeline")) if video.name == "final.mp4" else None
            ws = fc.check(video, ass if ass.exists() else None, words_for(job, video), spans)
            total += 1
            flagged += bool(ws)
            for w in ws:
                counts[w["check"]] = counts.get(w["check"], 0) + 1
            print(f"{len(ws)}  {job.name}/{video.name}" + "".join(f"\n     - [{w['check']}] {w['message']}" for w in ws),
                  flush=True)
    print(f"\n{total} videos checked in {time.time() - t0:.0f} s; {flagged} with at least one warning; by check: {counts}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
