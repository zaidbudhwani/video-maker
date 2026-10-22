"""Engine lane browser check: make a real video through the site and look at what the engine reports.

Covers the report fields the other pages build on (size_mb, skipped_clips, uncertain_words, not_shown, warnings shape,
no file paths in anything she can read), the first progress line, and the new caption height.
Run only this part:  set E2E_ONLY=engine & venv\\Scripts\\python tests\\e2e_site.py"""
import re


def run(page, ctx):
    page.goto(ctx.BASE)
    files = sorted(ctx.SAMPLE.glob("*.mp4")) + [ctx.SAMPLE / "voice.m4a"]
    page.set_input_files("#files", [str(f) for f in files])
    ctx.expect(page.locator("#clips li.clip")).to_have_count(3)
    page.click("#go")
    made = ctx.wait_for_video(page)
    ctx.check("engine part: video was made", made, "" if made else page.locator("#failMsg").inner_text().replace("\n", " | "))
    if not made:
        return
    job_id = page.evaluate("jobId")
    j = page.evaluate("fetch(`/api/jobs/${jobId}`).then(r => r.json())")
    report = j["report"]

    parts = report["parts"]
    ctx.check("engine part: every output file has its size in MB", all(isinstance(p.get("size_mb"), (int, float)) and p["size_mb"] > 0 for p in parts),
              [p.get("size_mb") for p in parts])
    ctx.check("engine part: file is not huge (bitrate ceiling)", all(p["size_mb"] < 60 for p in parts), [p["size_mb"] for p in parts])
    ctx.check("engine part: skipped_clips is a list", isinstance(report.get("skipped_clips"), list), report.get("skipped_clips"))
    ctx.check("engine part: uncertain_words is a list (max 12)", isinstance(report.get("uncertain_words"), list) and len(report["uncertain_words"]) <= 12)
    for u in report["uncertain_words"]:
        ctx.check("engine part: an uncertain word has word, at, p", {"word", "at", "p"} <= set(u), u)
    ctx.check("engine part: not_shown is a list of at/said/idea (voice-over mode)", isinstance(report.get("not_shown"), list)
              and all({"at", "said", "idea"} <= set(n) for n in report["not_shown"]) and len(report["not_shown"]) <= 3)
    ctx.check("engine part: warnings keep their shape", all({"kind", "message"} <= set(w) for w in report["warnings"]), report["warnings"])
    bad = [w["message"] for w in report["warnings"] if re.search(r"[A-Za-z]:\\|/out/|normalized|\.json|tripod|tap the screen", w["message"])]
    ctx.check("engine part: warnings have no paths, file names of ours or filming advice", not bad, bad)
    ctx.check("engine part: no AI error without a key (the video simply uses plain copy)", "ai_error" not in report, report.get("ai_error"))
    ctx.check("engine part: captions sit higher by default (0.36)", report["settings"]["caption_margin"] == 0.36, report["settings"]["caption_margin"])

    log = (ctx.DATA / "out" / f"{job_id}.log")
    steps = [l for l in log.read_text(encoding="utf-8", errors="replace").splitlines() if re.match(r"^\s*\d/6", l)] if log.exists() else []
    ctx.check("engine part: first progress line is 0/6 Getting your clips ready", steps and steps[0].startswith("0/6 Getting your clips ready"), steps[:1])
    ctx.check("engine part: the 1/6..6/6 lines are still there", any(l.startswith("1/6") for l in steps) and any(l.startswith("4/6") for l in steps), steps)
