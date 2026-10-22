"""Compare versions on the result page (web/index.html "Compare with version N-1"). Works on its own:

    E2E_PORT=8371 E2E_ONLY=compare venv/Scripts/python tests/e2e_site.py

Two small finished videos are written straight into the test server's throwaway data folder (a version 1 and its
remake, version 2), so the real routes answer: /api/jobs/<id> (version, previous) and the video files (byte ranges).
No pipeline run, nothing sent anywhere. Checks: the button only on a remake, one above the other on a phone and side
by side on a wide screen, nothing plays until she taps Play, she hears only the newer version, both start from the
same moment and stay together, one Play/Pause, the position slider, close, 44 px buttons."""
import json
import shutil
import subprocess
import sys

PHONE = {"width": 390, "height": 844}
WIDE = {"width": 1280, "height": 900}


def run(page, ctx):
    BASE, ROOT, check = ctx.BASE, ctx.ROOT, ctx.check
    sys.path.insert(0, str(ROOT))
    from video_tool.config import FFMPEG
    out = ctx.DATA / "out"
    errors = []

    def video(path, seconds, src, freq):
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"{src}=size=270x480:rate=15:duration={seconds}",
                        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(path)], capture_output=True)

    def finished(jid, seconds, src, freq, parent=None):
        d = out / jid
        d.mkdir(parents=True, exist_ok=True)
        video(d / "final.mp4", seconds, src, freq)
        rep = {"job_id": jid, "mode": "voice", "length": float(seconds), "parts": [{"file": "final.mp4", "length": float(seconds), "size_mb": 0.2}],
               "settings": {}, "warnings": [], "timeline": [], "not_shown": [], "uncertain_words": [], "seconds_to_build": 60,
               "clip_match": {"used": False, "reason": "no API key"}}
        (d / "report.json").write_text(json.dumps(rep), encoding="utf-8")
        (d / "copy.json").write_text(json.dumps({"titles": ["Compare test"], "hook": "", "description": "A test.", "cta": "",
                                                 "captions": {}, "checklist": [], "review_notes": []}), encoding="utf-8")
        (out / f"{jid}.owner").write_text("tester", encoding="utf-8")
        if parent:
            (out / f"{jid}.parent").write_text(parent, encoding="utf-8")

    finished("cmp-v1", 8, "testsrc2", 330)
    finished("cmp-v2", 6, "testsrc", 520, parent="cmp-v1")

    p = page.context.new_page()
    p.on("pageerror", lambda e: errors.append(str(e)))
    p.set_viewport_size(PHONE)

    def boxes():   # both in one go: the page may still be scrolling to the comparison
        return p.evaluate("""() => [cmpOld, cmpNew].map(v => { const r = v.getBoundingClientRect(); return {x: r.x, y: r.y, width: r.width, height: r.height}; })""")

    def open_result(jid):
        p.goto(f"{BASE}/?job={jid}")
        p.wait_for_function("() => !document.getElementById('done').classList.contains('hidden')", timeout=20000)

    print("Compare versions", flush=True)
    open_result("cmp-v1")
    check("compare: no Compare button on a first version", p.locator("#compareBtn").is_hidden())

    open_result("cmp-v2")
    check("compare: the remake says Version 2 (from the server, after a fresh load)", "Version 2" in p.inner_text("#versionTag"), p.inner_text("#versionTag"))
    btn = p.locator("#compareBtn")
    check("compare: a remake offers 'Compare with version 1'", btn.is_visible() and btn.inner_text() == "Compare with version 1", btn.inner_text())
    check("compare: the button is at least 44 px tall", btn.bounding_box()["height"] >= 44, btn.bounding_box())
    check("compare: the compare videos are not shown before she asks", p.locator("#compareCard").is_hidden())

    # she watched up to 2.5 s of the new version, then asks to compare: both start from that moment
    p.wait_for_function("() => document.querySelector('#players video').readyState >= 1", timeout=15000)
    p.evaluate("document.querySelector('#players video').currentTime = 2.5")
    p.wait_for_timeout(300)
    btn.click()
    p.wait_for_function("() => document.getElementById('cmpOld').readyState >= 1 && document.getElementById('cmpNew').readyState >= 1", timeout=15000)
    p.wait_for_timeout(500)
    st = p.evaluate("""() => { const a = cmpOld, b = cmpNew; return {aT: a.currentTime, bT: b.currentTime, aP: a.paused, bP: b.paused,
        aM: a.muted, bM: b.muted, auto: a.autoplay || b.autoplay, ctrl: a.controls || b.controls,
        aSrc: a.currentSrc, bSrc: b.currentSrc, main: document.querySelector('#players video').paused}; }""")
    check("compare: the version before and this version are loaded", st["aSrc"].endswith("/api/jobs/cmp-v1/final.mp4") and st["bSrc"].endswith("/api/jobs/cmp-v2/final.mp4"), st)
    check("compare: nothing plays by itself (no autoplay, both paused)", st["aP"] and st["bP"] and not st["auto"], st)
    check("compare: she hears only the newer version", st["aM"] and not st["bM"], st)
    check("compare: both start from the moment she was at", abs(st["aT"] - 2.5) < 0.2 and abs(st["bT"] - 2.5) < 0.2, st)
    check("compare: the main player is paused while comparing", st["main"], st)
    check("compare: the two are labelled in words", p.inner_text("#cmpOldCap") == "Version 1 (before)" and p.inner_text("#cmpNewCap") == "Version 2 (now)",
          (p.inner_text("#cmpOldCap"), p.inner_text("#cmpNewCap")))
    note = p.locator("#cmpNote")
    check("compare: a shorter version is said plainly", note.is_visible() and "Version 2 is shorter" in note.inner_text(), note.inner_text())

    a_box, b_box = boxes()
    check("compare: on a phone, one above the other", b_box["y"] >= a_box["y"] + a_box["height"] - 1 and abs(a_box["x"] - b_box["x"]) < 40, (a_box, b_box))
    check("compare: on a phone, no sideways scrolling", p.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"))
    for sel in ("#cmpPlay", "#cmpClose", "#cmpSeek"):
        h = p.locator(sel).bounding_box()["height"]
        check(f"compare: {sel} is at least 44 px tall", h >= 44, h)

    # one Play for both, kept together
    p.click("#cmpPlay")
    p.wait_for_timeout(1500)
    st = p.evaluate("() => ({a: cmpOld.currentTime, b: cmpNew.currentTime, aP: cmpOld.paused, bP: cmpNew.paused, label: document.getElementById('cmpPlay').textContent})")
    check("compare: one tap plays both", not st["aP"] and not st["bP"] and st["label"] == "Pause both", st)
    check("compare: both moved on from where they started", st["b"] > 3.0, st)
    check("compare: they stay at the same moment (within a third of a second)", abs(st["a"] - st["b"]) < 0.35, st)
    p.click("#cmpPlay")
    p.wait_for_timeout(200)
    st = p.evaluate("() => ({aP: cmpOld.paused, bP: cmpNew.paused, label: document.getElementById('cmpPlay').textContent})")
    check("compare: the same button pauses both", st["aP"] and st["bP"] and st["label"] == "Play both", st)

    # the slider moves both to the same moment
    p.evaluate("() => { const s = document.getElementById('cmpSeek'); s.value = 1; s.dispatchEvent(new Event('input')); }")
    p.wait_for_timeout(400)
    st = p.evaluate("() => ({a: cmpOld.currentTime, b: cmpNew.currentTime, t: cmpTime.textContent})")
    check("compare: the slider moves both to the same moment", abs(st["a"] - 1) < 0.2 and abs(st["b"] - 1) < 0.2 and st["t"] == "0:01", st)

    # Once the shorter new version ends, the longer old version can continue and pause normally.
    p.evaluate("() => { cmpSeek.value=5.6; cmpSeek.dispatchEvent(new Event('input')); }")
    p.click("#cmpPlay")
    p.wait_for_timeout(1100)
    check("compare: longer version continues after shorter ends", p.evaluate("cmpNew.ended && cmpOld.currentTime > 6 && !cmpOld.paused"))
    p.click("#cmpPlay")
    check("compare: shared Pause still pauses the longer version", p.evaluate("cmpOld.paused && cmpNew.paused"))
    p.evaluate("() => { cmpSeek.value=1; cmpSeek.dispatchEvent(new Event('input')); }")

    # keyboard: Tab reaches Play, Enter plays, focus is visible
    p.focus("#cmpSeek")
    p.keyboard.press("Shift+Tab")
    focused = p.evaluate("document.activeElement.id")
    outline = p.evaluate("getComputedStyle(document.activeElement).outlineStyle")
    check("compare: the keyboard reaches Play both, with a visible focus ring", focused == "cmpPlay" and outline != "none", (focused, outline))
    p.keyboard.press("Enter")
    p.wait_for_timeout(500)
    check("compare: Enter on Play both plays them", p.evaluate("!cmpNew.paused && !cmpOld.paused"))
    p.click("#cmpPlay")

    p.emulate_media(reduced_motion="reduce")
    p.locator("#compareCard").scroll_into_view_if_needed()
    p.screenshot(path=ctx.SHOTS / "compare-phone.png", full_page=True)
    p.screenshot(path=ctx.SHOTS / "compare-phone-viewport.png")
    p.click("#cmpClose")
    check("compare: Close hides the comparison and stops both", p.locator("#compareCard").is_hidden() and p.evaluate("cmpOld.paused && cmpNew.paused"))
    check("compare: focus goes back to the Compare button", p.evaluate("document.activeElement.id") == "compareBtn")

    # wide screen: side by side
    p.set_viewport_size(WIDE)
    p.click("#compareBtn")
    p.wait_for_function("() => cmpOld.readyState >= 1 && cmpNew.readyState >= 1", timeout=15000)
    a_box, b_box = boxes()
    check("compare: on a wide screen, side by side", abs(a_box["y"] - b_box["y"]) < 4 and b_box["x"] > a_box["x"] + a_box["width"] - 1, (a_box, b_box))
    p.screenshot(path=ctx.SHOTS / "compare-wide.png", full_page=False)
    p.click("#cmpClose")

    # the example video never offers it
    check("compare: no JavaScript errors", not errors, errors)
    p.close()
    for jid in ("cmp-v1", "cmp-v2"):   # synthetic jobs: later parts (content) scan every copy.json in this data folder
        shutil.rmtree(out / jid, ignore_errors=True)
        for ext in ("owner", "parent"):
            (out / f"{jid}.{ext}").unlink(missing_ok=True)
