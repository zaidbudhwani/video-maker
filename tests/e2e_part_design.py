"""Design update (Oct 19): the promise-style opening with the 3D scene beside it, the upload area on the first screen, the four
steps, the floating pill top bar on every signed-in page, visible keyboard focus, and the link-preview tags.
Works on its own:  E2E_PORT=8185 E2E_ONLY=design venv/Scripts/python tests/e2e_site.py
This is a desktop browser at phone and desktop sizes. It is NOT a test on a real iPhone."""


def _webgl(p):
    return p.evaluate("(() => { const c = document.createElement('canvas'); return !!(c.getContext('webgl2') || c.getContext('webgl')); })()")


def run(page, ctx):
    BASE, check = ctx.BASE, ctx.check
    browser = page.context.browser
    state = page.context.storage_state()
    errors = []

    for label, vw, vh in (("phone", 390, 844), ("desktop", 1280, 800)):
        c = browser.new_context(viewport={"width": vw, "height": vh}, storage_state=state)
        p = c.new_page()
        p.on("pageerror", lambda e: errors.append(str(e)))
        p.goto(BASE + "/")
        p.wait_for_selector("#acct:not(.hidden)", timeout=10000)

        # ---- the opening and the upload area ----
        h1 = " ".join(p.locator("h1").inner_text().split())
        check(f"design {label}: the opening says 'Your clips. Your voice. Ready to share.'", h1 == "Your clips. Your voice. Ready to share.", h1)
        check(f"design {label}: one plain sentence says what she gets back",
              "finished vertical video" in p.locator("#subLine").inner_text() and "caption" in p.locator("#subLine").inner_text())
        drop = p.locator("#drop").bounding_box()
        check(f"design {label}: the upload area starts on the first screen, no scrolling (top at {round(drop['y'])} of {vh})",
              drop["y"] < vh - 120, drop)
        steps = [" ".join(p.locator(f"#how{i}").inner_text().split()) for i in range(1, 5)]
        check(f"design {label}: four steps: add, make, review, save",
              [s.split()[0] for s in steps] == ["Add", "Make", "Review", "Save"] and all(p.locator(f"#how{i}").is_visible() for i in range(1, 5)), steps)
        if _webgl(p):
            try:
                p.wait_for_selector("#heroStage #stage3d-hero canvas", timeout=45000)
                ok = True
            except Exception:
                ok = False
            check(f"design {label}: the 3D scene sits in the opening, beside or under the promise", ok)
            if ok and label == "desktop":
                hs, tx = p.locator("#heroStage").bounding_box(), p.locator(".hero-text").bounding_box()
                check("design desktop: scene to the right of the words, same band", hs["x"] > tx["x"] + tx["width"] - 4 and abs(hs["y"] - tx["y"]) < 160, (hs, tx))
            if ok:
                drop2 = p.locator("#drop").bounding_box()
                check(f"design {label}: with the scene in, the upload area is still on the first screen", drop2["y"] < vh - 120, drop2)
        check(f"design {label}: no sideways scrolling on the start page", not p.evaluate("document.documentElement.scrollWidth > innerWidth"))

        # ---- the top bar ----
        nav = p.locator("#nav")
        check(f"design {label}: the top bar stays at the top while scrolling (sticky)", p.evaluate("getComputedStyle(document.getElementById('nav')).position") == "sticky")
        check(f"design {label}: logo, then Make a video and My videos grouped together",
              p.locator("#nav > a.logo").count() == 1 and p.locator("#nav .seg #navNew").count() == 1 and p.locator("#nav .seg #navVideos").count() == 1)
        small = p.evaluate("""[...document.querySelectorAll('#nav a, #nav button')].filter(e => e.offsetParent)
            .map(e => [e.id || e.className, Math.round(e.getBoundingClientRect().height), Math.round(e.getBoundingClientRect().width)])
            .filter(([, h, w]) => h < 44 || w < 44)""")
        check(f"design {label}: every top-bar target is at least 44 px", not small, small)
        nb = nav.bounding_box()
        check(f"design {label}: the top bar fits the screen", nb["x"] >= 0 and nb["x"] + nb["width"] <= vw + 0.5, nb)

        # ---- keyboard ----
        p.goto(BASE + "/")
        p.wait_for_selector("#acct:not(.hidden)", timeout=10000)
        p.keyboard.press("Tab")
        first = p.evaluate("document.activeElement.className + '|' + document.activeElement.id")
        p.keyboard.press("Tab")
        second = p.evaluate("document.activeElement.id")
        check(f"design {label}: Tab goes to the logo first, then Make a video", first.startswith("logo") and second == "navNew", (first, second))
        ring = p.evaluate("(() => { const s = getComputedStyle(document.activeElement); return [s.outlineStyle, parseFloat(s.outlineWidth)]; })()")
        check(f"design {label}: the focused link shows a visible ring", ring[0] not in ("none", "") and ring[1] >= 2, ring)

        # ---- the other signed-in pages ----
        for url in ("/videos", "/admin"):
            p.goto(BASE + url)
            p.wait_for_selector("#acct:not(.hidden)", timeout=10000)
            check(f"design {label} {url}: the same pill bar, sticky, with the logo and both links",
                  p.locator("#nav > a.logo").count() == 1 and p.locator("#nav .seg a").count() == 2
                  and p.evaluate("getComputedStyle(document.getElementById('nav')).position") == "sticky")
            check(f"design {label} {url}: no sideways scrolling", not p.evaluate("document.documentElement.scrollWidth > innerWidth"))
        c.close()

    # ---- reduced motion: no scene, and the opening closes up ----
    c = browser.new_context(viewport={"width": 1280, "height": 800}, storage_state=state, reduced_motion="reduce")
    p = c.new_page()
    p.goto(BASE + "/")
    p.wait_for_selector("#acct:not(.hidden)", timeout=10000)
    p.wait_for_timeout(1500)
    check("design reduced motion: no 3D scene in the opening", p.locator("#stage3d-hero").count() == 0)
    check("design reduced motion: the empty scene box takes no room", p.evaluate("getComputedStyle(document.getElementById('heroStage')).display") == "none")
    c.close()

    # ---- long video mode keeps four steps in its own words ----
    c = browser.new_context(viewport={"width": 390, "height": 844}, storage_state=state)
    p = c.new_page()
    p.goto(BASE + "/")
    p.click("#modeLongL")
    check("design: long video mode rewrites all four steps", "long video" in p.locator("#how1").inner_text() and "titles" in p.locator("#how4").inner_text(),
          [p.locator(f"#how{i}").inner_text() for i in range(1, 5)])
    c.close()

    # ---- link preview (signed out, the way a link preview fetches it) ----
    anon = browser.new_context()
    ap = anon.new_page()
    r = ap.goto(BASE + "/login")
    html = ap.content()
    img = ap.get_attribute('meta[property="og:image"]', "content") or ""
    check("design: the sign-in page has a link-preview title, description and picture",
          ap.get_attribute('meta[property="og:title"]', "content") == "Alex's Video Maker"
          and ap.get_attribute('meta[property="og:description"]', "content") and img, img)
    check("design: the preview picture is a full address on this server (filled in per request)", img == BASE + "/assets/og-image.png" and "__ORIGIN__" not in html, img)
    resp = ap.request.get(img)
    check("design: the preview picture loads without signing in, as a PNG", resp.ok and resp.headers.get("content-type", "").startswith("image/png"), resp.status)
    check("design: the large-picture card is asked for", ap.get_attribute('meta[name="twitter:card"]', "content") == "summary_large_image")
    anon.close()

    check("design: no JavaScript errors", not errors, errors[:3])
