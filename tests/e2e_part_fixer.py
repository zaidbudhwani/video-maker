"""Fixer lane browser checks: tap targets, the sign-in eye, plain words, no page breaks under the new security headers.
Works on its own:  E2E_PORT=8081 E2E_ONLY=fixer venv/Scripts/python tests/e2e_site.py
Seeds two made-up finished videos in the throwaway data folder and removes them again."""
import json
import shutil
import subprocess
import sys


def run(page, ctx):
    out = ctx.DATA / "out"
    made = []
    # a context of its own WITHOUT bypass_csp, so the pages run under the real security policy (signed in the same way)
    mine = page.context.browser.new_context(storage_state=page.context.storage_state(), viewport={"width": 390, "height": 844},
                                          bypass_csp=False)
    page = mine.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)[:200]))
    page.on("console", lambda m: errors.append(m.text[:200]) if m.type == "error" and "Content Security" in m.text else None)
    try:
        _run(page, ctx, out, made, errors)
    finally:
        page.close()
        mine.close()
        for j in made:
            shutil.rmtree(out / j, ignore_errors=True)
            for ext in ("owner", "parent", "source", "settings.json"):
                (out / f"{j}.{ext}").unlink(missing_ok=True)


def _small(page, selector):
    """Visible tappable things matching selector that are under 44 px tall."""
    return page.evaluate("""(sel) => [...document.querySelectorAll(sel)].filter(e => e.offsetParent !== null)
        .map(e => { const r = e.getBoundingClientRect(); return [e.textContent.trim().slice(0, 30), Math.round(r.width), Math.round(r.height)]; })
        .filter(x => x[2] < 43)""", selector)


def _run(page, ctx, out, made, errors):
    BASE, check = ctx.BASE, ctx.check
    sys.path.insert(0, str(ctx.ROOT))
    from video_tool.config import FFMPEG
    tiny = ctx.DATA / "fixer-tiny.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=270x480:d=2:r=15", "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", str(tiny)], check=True)
    for job, title in (("web-fixer-1", "Fixer's tour"), ("web-fixer-2", "Second one")):
        d = out / job
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(tiny, d / "final.mp4")
        (d / "report.json").write_text(json.dumps({"job_id": job, "mode": "voice", "parts": [{"file": "final.mp4"}], "length": 2.0,
                                                   "seconds_to_build": 5, "warnings": [{"clip": None, "kind": "shake", "message": "x"}],
                                                   "timeline": [{}], "settings": {"privacy": "private"}}), encoding="utf-8")
        (d / "copy.json").write_text(json.dumps({"titles": [title]}), encoding="utf-8")
        (out / f"{job}.owner").write_text("tester", encoding="utf-8")
        (out / f"{job}.settings.json").write_text(json.dumps({"privacy": "private"}), encoding="utf-8")
        made.append(job)

    page.set_viewport_size({"width": 390, "height": 844})

    # ---- My videos: thumb-sized buttons, plain words, a sensible Your numbers
    page.goto(f"{BASE}/videos")
    page.wait_for_selector(".row")
    small = _small(page, ".row .btn, .row .toggle, .top .btn, .who a")
    check("fixer: My videos buttons and links are all at least 44 px tall", small == [], small)
    note = page.locator(".note").inner_text()
    check("fixer: the footer says where videos are in plain words", "saved here until you delete them" in note and "computer" not in note, note)
    chips = page.locator(".row .chip").all_inner_texts()
    check("fixer: the unexplained 'Notes' chip says what it is", "Things to check" in chips and "Notes" not in chips, chips)
    check("fixer: the picture-privacy chip is plain", "Your pictures were not shared" in chips and not any("computer" in c for c in chips), chips)
    page.locator("#numbersFold summary").click()
    total = int(page.locator("#sTotal").inner_text())
    check("fixer: Your numbers shows the chart only from 3 videos on", page.locator("#weeksBox").is_visible() == (total >= 3), total)
    label = page.locator("#sMinutesLabel").inner_text()
    value = float(page.locator("#sMinutes").inner_text())
    check("fixer: a few seconds of video is never shown as '0.0 minutes'", (label == "minutes of video" and value >= 0.1) or label == "seconds of video", (label, value))
    check("fixer: an apostrophe in a title shows as typed", "Fixer's tour" in page.locator(".row .title").all_inner_texts())
    check("fixer: no script error or blocked resource on My videos", errors == [], errors)

    # ---- Team page: links and boxes
    page.goto(f"{BASE}/admin")
    page.wait_for_selector("h1")
    small = _small(page, "button, .btn, p.muted a, label.check, select")
    check("fixer: Team page buttons, links and boxes are at least 44 px", small == [], small)
    check("fixer: Team page has no 'this computer' wording", "on this computer" not in page.locator("body").inner_text())
    check("fixer: no script error or blocked resource on the Team page", errors == [], errors)

    # ---- sign-in page: the eye is a real thumb target and still hold-to-show
    anon = page.context.browser.new_context(viewport={"width": 390, "height": 844})
    lp = anon.new_page()
    try:
        lp.goto(f"{BASE}/login")
        box = lp.locator(".peek").first.bounding_box()
        check("fixer: the eye button on sign-in is 44 x 44", box and round(box["width"]) >= 44 and round(box["height"]) >= 44, box)
        lp.fill("#p", "secret-pass-1")
        lp.locator(".peek").first.hover()
        lp.mouse.down()
        held = lp.get_attribute("#p", "type")
        lp.mouse.up()
        check("fixer: holding the eye shows the password, letting go hides it", held == "text" and lp.get_attribute("#p", "type") == "password", held)
    finally:
        lp.close()
        anon.close()

    # ---- upload page: privacy words, nothing blocked
    page.goto(BASE)
    page.wait_for_selector("#drop")
    page.set_input_files("#files", [str(ctx.SAMPLE / "01.mp4")])
    priv = page.locator("#private").locator("xpath=..").inner_text()
    check("fixer: the privacy box has no 'computer' or brand name", "computer" not in priv and "Claude" not in priv and "AI helper" in priv,
          priv.replace("\n", " "))
    check("fixer: the upload page has no blocked resource or script error", errors == [], errors)
    page.evaluate("clearAll()")

    # ---- a plain-text post is not taken for JSON
    r = page.request.post(f"{BASE}/api/jobs/web-fixer-1/feedback", data="{}", headers={"content-type": "text/plain"})
    check("fixer: a plain-text post is refused", r.status == 400, r.status)
