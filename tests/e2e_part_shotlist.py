"""The "Plan my shots from what I'll say" box under "What should I film this week?" on the upload page. Works on its own:

    E2E_PORT=8361 E2E_ONLY=shotlist venv/Scripts/python tests/e2e_site.py

The test server has no Claude key, so the real /api/shotlist answers with the plain list made on this computer (nothing is
sent anywhere). Checks: she can type her words and get one shot per thing, in plain words, with filming tips; an empty
box gets a plain message, not an error; a Fair Housing word gets a note; touch targets are 44 px; keyboard focus shows."""


def run(page, ctx):
    check = ctx.check
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": 390, "height": 844})
    page.emulate_media(reduced_motion="reduce")
    page.goto(ctx.BASE)
    page.wait_for_selector("#kindCard:not(.hidden)", timeout=15000)
    check("ducking is off by default on upload", not page.locator("#musicDuck").is_checked())
    plan = page.locator("#shotPlan")
    check("shot list: the box sits in the 'What should I film?' area", page.locator("#ideasWrap #shotPlan").count() == 1)
    summary = plan.locator("summary")
    summary.scroll_into_view_if_needed()
    summary.focus()
    page.keyboard.press("Enter")   # opens with the keyboard
    check("shot list: opens from the keyboard", plan.evaluate("d => d.open"))
    check("shot list: keyboard focus is visible on the opener",
          summary.evaluate("e => getComputedStyle(e).outlineStyle !== 'none' && parseFloat(getComputedStyle(e).outlineWidth) >= 2"))
    btn = page.locator("#shotBtn")
    check("shot list: the button is at least 44 px tall", btn.bounding_box()["height"] >= 44, btn.bounding_box())
    check("shot list: the opener is at least 44 px tall", summary.bounding_box()["height"] >= 44, summary.bounding_box())

    btn.click()
    msg = page.locator("#shotMsg")
    ctx.expect(msg).to_contain_text("Type or paste what you plan to say first")
    check("shot list: empty box -> a plain message and the typing box gets focus",
          page.evaluate("document.activeElement.id") == "shotText")

    page.fill("#shotText", "This home has a large deck off the back, and the big open kitchen has a granite island. "
                           "It is a family-friendly yard.")
    btn.click()
    ctx.expect(msg).to_contain_text("shots to film")
    items = page.locator("#planList li")
    texts = [items.nth(i).inner_text() for i in range(items.count())]
    check("shot list: one shot per thing she names, in her order", [t.split("\n")[0] for t in texts] ==
          ["“large deck”", "“big open kitchen”", "“granite island”", "“yard”"], texts)
    check("shot list: each shot says how to film it and for how long",
          all("Film the" in t and "seconds" in t for t in texts) and "Film the deck from far enough back" in texts[0], texts)
    tips = page.locator("#shotTips")
    check("shot list: filming tips are shown", tips.is_visible() and "upright" in tips.inner_text(), tips.inner_text())
    check("shot list: the Fair Housing note is shown in plain words",
          "Fair Housing: describe the place, not who it suits" in tips.inner_text(), tips.inner_text())

    page.fill("#shotText", "Hi everybody, thanks for watching.")
    btn.click()
    ctx.expect(msg).to_contain_text("I didn't find things to film")
    check("shot list: nothing to film -> the list is cleared", page.locator("#planList li").count() == 0)
    check("shot list: no page errors", not errors, errors)
    page.set_viewport_size({"width": 390, "height": 844})
    page.fill("#shotText", "The large deck and the kitchen.")
    btn.click()
    ctx.expect(msg).to_contain_text("shots to film")
    wide = page.evaluate("""[...document.querySelectorAll('#shotPlan, #shotPlan *')]
        .filter(e => e.getBoundingClientRect().right > window.innerWidth + 1).map(e => e.id || e.tagName)""")
    check("shot list: nothing in the box is wider than a phone-width screen", not wide, wide)

    plan.scroll_into_view_if_needed()
    page.screenshot(path=ctx.SHOTS / "shotlist-390.png", full_page=True)
    page.route("**/api/shotlist", lambda r: r.fulfill(status=503, json={"detail": "unavailable"}))
    btn.click()
    ctx.expect(msg).to_contain_text("Try again")
    check("failed request clears stale shots and re-enables the button", page.locator("#planList li").count() == 0
          and btn.is_enabled())
    page.unroute("**/api/shotlist")

    # Capture the actual upload request without starting a pipeline render.
    sent = []
    def upload_request(route):
        sent.append(route.request.post_data_json)
        route.fulfill(status=400, json={"detail": "Stopped after checking the upload choice."})
    page.route("**/api/uploads", upload_request)
    page.set_input_files("#files", [str(ctx.SAMPLE / "01.mp4"), str(ctx.SAMPLE / "voice.m4a")])
    page.wait_for_selector("#clips li.clip")
    page.locator("#musicDuck").check()
    check("ducking can be selected with a real checkbox", page.locator("#musicDuck").is_checked())
    page.locator("#musicDuck").scroll_into_view_if_needed()
    page.screenshot(path=ctx.SHOTS / "ducking-upload-390.png")
    page.locator("#go").click()
    ctx.expect(page.locator("#failMsg")).to_contain_text("Stopped after checking")
    check("ducking upload request carries a true boolean", sent and sent[0].get("music_duck") is True, sent)
    page.unroute("**/api/uploads")
