"""Site polish: the shared top bar on My videos and the Team page, the result page's grouped "What I did", the sticky
Remake button, the voice-over Record button, and the two new 3D scenes (week on My videos, skyline on the Team page).
Works on its own:  E2E_PORT=8181 E2E_ONLY=site_polish venv/Scripts/python tests/e2e_site.py
Screenshots go to tests/shots/polish-*.png."""
import json

J = {"content-type": "application/json"}


def _scene(pg, ctx, url, kind):
    pg.goto(ctx.BASE + url)
    if not pg.evaluate("(() => { const c = document.createElement('canvas'); return !!(c.getContext('webgl2') || c.getContext('webgl')); })()"):
        return None
    try:
        pg.wait_for_selector("#sceneSlot #stage3d-hero canvas", timeout=45000)
    except Exception:
        return False
    return pg.get_attribute("#sceneSlot", "data-scene3d") == kind


def run(page, ctx):
    BASE, SH, check = ctx.BASE, ctx.SHOTS, ctx.check
    errors = []
    browser = page.context.browser
    state = page.context.storage_state()

    # ---- 1. shared top bar on My videos and the Team page ----
    for scheme in ("light", "dark"):
        c = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme=scheme, storage_state=state)
        p = c.new_page()
        p.on("pageerror", lambda e: errors.append(str(e)))
        for url, cur in (("/videos", "navVideos"), ("/admin", None)):
            p.goto(BASE + url)
            p.wait_for_selector("#acct:not(.hidden)", timeout=10000)
            if scheme == "light":
                check(f"polish {url}: top bar has Make a video and My videos", p.locator("#navNew").inner_text() == "Make a video"
                      and p.locator("#navVideos").inner_text() == "My videos")
                check(f"polish {url}: the right page is marked current",
                      (p.get_attribute("#navVideos", "aria-current") == "page") == (cur == "navVideos"))
                check(f"polish {url}: account button names who is signed in", "tester" in p.locator("#navUser").inner_text())
                p.click("#acctBtn")
                check(f"polish {url}: account menu shows Team page and Sign out", p.locator("#adminLink").is_visible()
                      and p.locator("#navSignout").is_visible())
                small = p.evaluate("""[...document.querySelectorAll('#nav a, #nav button')].filter(e => { const r = e.getBoundingClientRect(); return r.width && r.height < 43.5; }).map(e => e.id)""")
                check(f"polish {url}: top bar buttons are at least 44 px", small == [], small)
                p.mouse.click(5, 400)
                check(f"polish {url}: clicking elsewhere closes the menu", p.locator("#acctMenu").is_hidden())
            if url == "/videos" and p.locator(".empty a").count():
                col = p.evaluate("getComputedStyle(document.querySelector('.empty a')).color")
                check(f"polish /videos: the empty-state link uses the brand colour ({scheme})", col in ("rgb(44, 118, 174)", "rgb(108, 174, 223)"), col)
            p.screenshot(path=SH / f"polish-bar{url.replace('/', '-')}-{scheme}.png")
        c.close()

    # ---- 2. the two new 3D scenes, light and dark ----
    for scheme in ("light", "dark"):
        c = browser.new_context(viewport={"width": 1280, "height": 800}, color_scheme=scheme, storage_state=state)
        p = c.new_page()
        p.on("pageerror", lambda e: errors.append(str(e)))
        for url, kind in (("/videos", "week"), ("/admin", "skyline")):
            ok = _scene(p, ctx, url, kind)
            if ok is None:
                print("   (no WebGL here: skipping the scene check)", flush=True)
                continue
            check(f"polish {url}: the '{kind}' scene mounts ({scheme})", ok)
            if ok:
                check(f"polish {url}: scene is hidden from screen readers", p.get_attribute("#stage3d-hero", "aria-hidden") == "true")
                p.wait_for_timeout(3500)
                p.locator("#sceneSlot").screenshot(path=SH / f"polish-scene-{kind}-{scheme}.png")
        c.close()
    rc = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce", storage_state=state)
    rp = rc.new_page()
    for url in ("/videos", "/admin"):
        rp.goto(BASE + url)
        rp.wait_for_timeout(2500)
        check(f"polish {url}: reduced motion shows no scene", rp.locator("canvas").count() == 0)
    rc.close()
    sd = browser.new_context(viewport={"width": 390, "height": 844}, storage_state=state)
    sd.add_init_script("Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 2 })")
    sp = sd.new_page()
    sp.goto(BASE + "/videos")
    sp.wait_for_timeout(2500)
    check("polish: a 2-core computer gets no scene", sp.locator("canvas").count() == 0)
    sd.close()

    # ---- 3. result page: grouped "What I did", folded details, sticky Remake ----
    rep = {"job_id": "fxp", "mode": "voice", "length": 20.0, "music": "bed.mp3", "denoised": True, "parts": [{"file": "final.mp4"}],
           "settings": {"privacy": "private"}, "timeline": [{"clip": "01.mp4", "start": 0, "end": 10, "skipped_start": 0},
                                                         {"clip": "02.mp4", "start": 10, "end": 20, "skipped_start": 0}],
           "warnings": [{"clip": None, "kind": "wide", "message": "Clip 2 was filmed sideways, so it is shown with the sides filled in."}]}
    job = {"status": "done", "report": rep, "copy": {"titles": ["A test title"], "hook": "Hi.", "description": "D.", "cta": "C."},
           "transcript": {"burned_in": True, "parts": ["Hello there."], "text": "Hello there."}}

    def handle(route):
        path = route.request.url.split(BASE, 1)[1].split("?", 1)[0]
        if path == "/api/jobs/fxp":
            return route.fulfill(status=200, headers=J, body=json.dumps(job))
        if path.startswith("/api/jobs/fxp/"):
            if path.endswith(".mp4") or path.endswith(".jpg"):
                return route.fulfill(status=404, body="")
            return route.fulfill(status=200, headers=J, body='{"clips":[],"status":"draft","posted":[]}')
        return route.continue_()

    c = browser.new_context(viewport={"width": 390, "height": 844}, storage_state=state)
    p = c.new_page()
    p.on("pageerror", lambda e: errors.append(str(e)))
    p.route("**/api/**", handle)
    p.goto(BASE + "/?job=fxp")
    p.wait_for_function("() => !document.getElementById('done').classList.contains('hidden')", timeout=20000)
    check("polish result: 'Done for you' group", p.locator("#notesDone").inner_text() == "Done for you"
          and "Music added" in p.locator("#notes").inner_text())
    know = p.locator("#notesKnow")
    check("polish result: amber notes sit under 'Worth knowing'", know.count() == 1
          and p.locator("#notesKnow + .chips .chip.warn").count() == 1 and p.locator("#notesDone + .chips .chip.warn").count() == 0)
    check("polish result: 'Which clip is where' and 'What the captions say' start folded",
          p.evaluate("!document.getElementById('didBox').open && !document.getElementById('transBox').open"))
    p.click("#fbOpen")
    check("polish result: More options starts folded", p.evaluate("!document.getElementById('moreOpts').open"))
    check("polish result: Remake video and its summary stay in reach (sticky together)",
          p.evaluate("getComputedStyle(document.getElementById('remakeDock')).position") == "sticky"
          and p.evaluate("document.getElementById('summary').parentElement.id === 'remakeDock' && document.getElementById('remake').parentElement.id === 'remakeDock'"))
    check("polish result: the waiting Remake button is solid, so text never shows through it",
          p.evaluate("(() => { const b = document.getElementById('remake'); return !b.disabled || getComputedStyle(b).opacity === '1'; })()"))
    p.evaluate("document.getElementById('moreOpts').open = true")
    p.evaluate("document.getElementById('fbCard').scrollIntoView({block: 'start'})")
    p.wait_for_timeout(600)
    in_view = p.evaluate("(() => { const r = document.getElementById('remake').getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; })()")
    check("polish result: with the panel open at its top, Remake is still on screen", in_view)
    p.screenshot(path=SH / "polish-remake-sticky.png")
    c.close()

    # ---- 4. upload page: Record button and mic hint ----
    c = browser.new_context(viewport={"width": 390, "height": 844}, storage_state=state, permissions=["microphone"])
    p = c.new_page()
    p.goto(BASE)
    p.set_input_files("#files", [str(sorted(ctx.SAMPLE.glob("*.mp4"))[0])])
    try:
        p.wait_for_selector("#recGo", timeout=10000)
        h = p.locator("#recGo").bounding_box()["height"]
        check("polish upload: Record my voice-over is a big filled button", h >= 47.5
              and "Record my voice-over" in p.locator("#recGo").inner_text()
              and p.evaluate("getComputedStyle(document.getElementById('recGo')).backgroundColor") != "rgba(0, 0, 0, 0)", h)
        check("polish upload: a mic hint says what the phone will ask", "Tap Allow" in p.locator("#micHint").inner_text())
        p.screenshot(path=SH / "polish-record.png", full_page=True)
    except Exception as e:
        check("polish upload: Record button shown (this browser can record)", False, str(e)[:120])
    c.close()
    check("polish: no JavaScript errors", not errors, "; ".join(errors[:3]))
