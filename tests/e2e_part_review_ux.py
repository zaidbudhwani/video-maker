"""The review helpers in a real browser: first-run tips (once per account), "Using your last settings" on the upload
page with its one-tap reset, the trust line and "Time my review" on the result page. Works on its own:

    E2E_PORT=8373 E2E_ONLY=review_ux venv/Scripts/python tests/e2e_site.py

Fresh accounts are made through the Team page's route, and small finished videos are written straight into the test
server's throwaway data folder, so the real routes answer. No pipeline run, no AI, nothing sent anywhere."""
import json
import secrets

PHONE = {"width": 390, "height": 844}
PHRASES = ["Here is the big kitchen island", "The large deck out back", "The finished basement with a bar",
           "Call me today for a showing", "The primary bedroom has a walk-in closet"]


def run(page, ctx):
    BASE, check = ctx.BASE, ctx.check
    out = ctx.DATA / "out"
    browser = page.context.browser
    errors = []

    def account():
        name, pw = "rux" + secrets.token_hex(3), secrets.token_urlsafe(12)
        r = page.request.post(f"{BASE}/api/admin/users", data={"username": name, "password": pw})
        assert r.ok, r.text()
        return name, pw

    def sign_in(name, pw, viewport=PHONE):
        c = browser.new_context(viewport=viewport, reduced_motion="reduce")
        pg = c.new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(f"{BASE}/login")
        pg.fill("#u", name)
        pg.fill("#p", pw)
        pg.click("#b")
        pg.wait_for_url(BASE + "/")
        return c, pg

    def finished(jid, owner, clip_match, scores=None):
        d = out / jid
        d.mkdir(parents=True, exist_ok=True)
        rep = {"job_id": jid, "mode": "voice", "length": 20.0, "parts": [{"file": "final.mp4", "length": 20.0}],
               "settings": {}, "warnings": [], "timeline": [], "not_shown": [], "uncertain_words": [], "seconds_to_build": 60,
               "clip_match": clip_match}
        (d / "report.json").write_text(json.dumps(rep), encoding="utf-8")
        (out / f"{jid}.owner").write_text(owner, encoding="utf-8")
        if scores:
            (d / "match").mkdir(exist_ok=True)
            (d / "match" / "scores.json").write_text(json.dumps({"phrases": PHRASES, "scores": scores}), encoding="utf-8")

    # ---------------- first-run tips
    print("First-run tips", flush=True)
    name, pw = account()
    c1, p1 = sign_in(name, pw)
    p1.wait_for_function("() => !document.getElementById('tipsCard').classList.contains('hidden')", timeout=10000)
    tips = p1.locator("#tipsCard li").all_inner_texts()
    check("tips: a new account sees three short tips", len(tips) == 3 and all(len(t) < 140 for t in tips), tips)
    check("tips: plain words (no file or setting names)", not any(x in " ".join(tips) for x in ("_", ".json", "API", "setting")), tips)
    done = p1.locator("#tipsDone")
    check("tips: the close button is at least 44 px tall", done.bounding_box()["height"] >= 44, done.bounding_box())
    p1.route("**/api/tips-seen", lambda r: r.fulfill(status=503, content_type="application/json", body='{"detail":"test failure"}'))
    p1.click("#tipsDone")
    p1.wait_for_function("document.getElementById('tipsMsg').textContent.includes('try again')")
    check("tips: a failed save leaves the tips and retry available", p1.locator("#tipsCard").is_visible() and p1.locator("#tipsDone").is_enabled())
    p1.unroute("**/api/tips-seen")
    p1.focus("#tipsDone")
    p1.keyboard.press("Enter")
    p1.wait_for_selector("#tipsCard.hidden", state="attached")
    check("tips: closing them with the keyboard hides them", p1.locator("#tipsCard").is_hidden())
    p1.wait_for_timeout(500)
    p1.reload()
    p1.wait_for_timeout(1200)
    check("tips: never again after a reload", p1.locator("#tipsCard").is_hidden())
    c1.close()
    c2, p2 = sign_in(name, pw)
    p2.wait_for_timeout(1200)
    check("tips: never again on another device", p2.locator("#tipsCard").is_hidden())
    c2.close()

    # ---------------- remembered settings on the upload page
    print("Using your last settings", flush=True)
    name, pw = account()
    c3, p3 = sign_in(name, pw)
    p3.wait_for_timeout(1000)
    check("saved: nothing remembered, nothing shown", p3.locator("#savedCard").is_hidden())
    finished("rux-" + name, name, {"used": False, "reason": "no API key"})
    r = p3.request.post(f"{BASE}/api/jobs/rux-{name}/feedback",
                        data={"changes": {"caption_size": 92, "remove_fillers": False, "denoise": True}, "remember": True,
                              "comment": "", "remake": False})
    check("saved: remembered through the existing route", r.ok, r.text()[:200])
    p3.reload()
    p3.wait_for_function("() => !document.getElementById('savedCard').classList.contains('hidden')", timeout=10000)
    items = p3.locator("#savedList li").all_inner_texts()
    check("saved: her remembered settings are listed in plain words",
          set(items) == {"Bigger captions", 'Keep the "um"s and "uh"s', "Remove background noise"}, items)
    check("saved: the noise box starts ticked from her saved choice", p3.is_checked("#denoise"))
    box = p3.locator("#savedCard").bounding_box()
    check("saved: the card fits a phone screen", box["x"] >= 0 and box["x"] + box["width"] <= PHONE["width"], box)
    reset = p3.locator("#savedReset")
    check("saved: the reset is at least 44 px tall", reset.bounding_box()["height"] >= 44, reset.bounding_box())
    reset.click()
    p3.wait_for_function("() => !document.getElementById('savedMsg').classList.contains('hidden')", timeout=10000)
    check("saved: one tap resets, and says so", "normal settings" in p3.inner_text("#savedMsg") and p3.locator("#savedList li").count() == 0,
          p3.inner_text("#savedMsg"))
    check("saved: ...the saved settings really are gone", p3.request.get(f"{BASE}/api/prefs").json()["saved"] == [])
    p3.reload()
    p3.wait_for_timeout(1200)
    check("saved: after a reset nothing is shown", p3.locator("#savedCard").is_hidden())
    # (the page's own width is not checked here: the account name in the top bar can be wider than a phone for a
    #  long user name, which is older than this part and not about these cards)

    # ---------------- the trust line
    print("Trust line", flush=True)
    scores = [[[9, 1, 1, 5, 1], [8, 2, 2, 5, 2]], [[1, 8, 2, 5, 3], [1, 7, 1, 5, 1]]]
    finished("rux-m-" + name, name, {"used": True}, scores)
    finished("rux-n-" + name, name, {"used": False, "reason": "no API key"})

    def result(jid):
        p3.goto(f"{BASE}/?job={jid}")
        p3.wait_for_function("() => !document.getElementById('done').classList.contains('hidden')", timeout=20000)

    result("rux-m-" + name)
    check("trust: from the saved matching, in her words", "estimates available footage" in p3.inner_text("#trustLine") and "I matched" not in p3.inner_text("#trustLine") and not any(ch.isdigit() for ch in p3.inner_text("#trustLine")),
          p3.inner_text("#trustLine"))
    result("rux-n-" + name)
    check("trust: no matching ran, said plainly with no made-up numbers",
          "order and available length" in p3.inner_text("#trustLine") and not any(ch.isdigit() for ch in p3.inner_text("#trustLine")), p3.inner_text("#trustLine"))
    check("trust: the first version has no Compare button", p3.locator("#compareBtn").is_hidden())

    # ---------------- Time my review
    print("Time my review", flush=True)
    btn = p3.locator("#timerBtn")
    check("timer: offered on the result page, 44 px tall", btn.is_visible() and btn.inner_text() == "Time my review" and btn.bounding_box()["height"] >= 43.5, btn.bounding_box())
    btn.click()
    p3.wait_for_timeout(2300)
    check("timer: it shows it is timing", btn.inner_text() == "Stop the timer" and "Timing your review: 0:0" in p3.inner_text("#timerTxt"),
          (btn.inner_text(), p3.inner_text("#timerTxt")))
    p3.reload()   # the stopwatch survives coming back to the page
    p3.wait_for_function("() => !document.getElementById('done').classList.contains('hidden')", timeout=20000)
    check("timer: still running after a reload", p3.locator("#timerBtn").inner_text() == "Stop the timer")
    failed_times = []
    def fail_time(route):
        failed_times.append(route.request.post_data_json)
        route.fulfill(status=503, content_type="application/json", body='{"detail":"test failure"}')
    p3.route("**/review-time", fail_time)
    p3.click("#timerBtn")
    p3.wait_for_function("document.getElementById('timerBtn').textContent === 'Retry saving time'")
    check("timer: failed save keeps the stopped duration", p3.evaluate("+localStorage.getItem(timerKey(jobId) + ':pending')") == failed_times[0]['seconds'])
    p3.reload()
    p3.wait_for_function("document.getElementById('timerBtn').textContent === 'Retry saving time'")
    check("timer: unsaved time survives reload", "Not saved yet" in p3.inner_text("#timerTxt"))
    p3.unroute("**/review-time")
    with p3.expect_response(lambda r: r.url.endswith("/review-time")) as resp:
        p3.click("#timerBtn")
    sent = resp.value.request.post_data_json
    check("timer: retry sends the original duration and same session", sent == failed_times[0], sent)
    check("timer: seconds and retry identity are sent", set(sent) == {"seconds", "session_id"} and 2 <= sent["seconds"] <= 30, sent)
    p3.wait_for_function("() => document.getElementById('timerTxt').textContent.startsWith('Saved:')", timeout=10000)
    check("timer: she is told it was saved", "Saved: 0:0" in p3.inner_text("#timerTxt") and btn.inner_text() == "Time my review", p3.inner_text("#timerTxt"))
    rows = page.request.get(f"{BASE}/api/admin/results").json()["videos"]
    row = next((v for v in rows if v["job"] == "rux-n-" + name), {})
    check("timer: the minutes reach the class report", row.get("timed_review_min") and row["timed_review_min"] > 0, row)
    csv_text = page.request.get(f"{BASE}/api/admin/results.csv").text()
    check("timer: ...and the CSV", "timed_review_min" in csv_text.splitlines()[0])
    p3.reload()
    p3.wait_for_function("() => !document.getElementById('done').classList.contains('hidden')", timeout=20000)
    check("timer: the saved minutes are shown when she comes back", "timed" in p3.inner_text("#timerTxt") and "so far" in p3.inner_text("#timerTxt"),
          p3.inner_text("#timerTxt"))
    p3.screenshot(path=ctx.SHOTS / "review-ux-result.png", full_page=True)

    # A response lost after a successful server save must not duplicate the session on retry.
    p3.click("#timerBtn")
    p3.wait_for_timeout(1100)
    def lose_response(route):
        route.fetch()   # save actually succeeds
        route.abort()   # the browser never receives its acknowledgement
    p3.route("**/review-time", lose_response)
    p3.click("#timerBtn")
    p3.wait_for_function("document.getElementById('timerBtn').textContent === 'Retry saving time'")
    before = p3.request.get(f"{BASE}/api/jobs/rux-n-{name}").json()['review_minutes']
    p3.unroute("**/review-time")
    p3.click("#timerBtn")
    p3.wait_for_function("document.getElementById('timerTxt').textContent.startsWith('Saved:')")
    after = p3.request.get(f"{BASE}/api/jobs/rux-n-{name}").json()['review_minutes']
    check("timer: lost acknowledgement retry is counted once", before == after and after > 0, (before, after))
    check("phone: review result fits 390px", p3.evaluate("document.documentElement.scrollWidth <= innerWidth"), p3.evaluate("[...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > innerWidth + 1).map(e => ({id:e.id,cls:e.className,right:e.getBoundingClientRect().right})).slice(0,12)"))
    p3.emulate_media(reduced_motion="reduce")
    p3.locator("#reviewRow").scroll_into_view_if_needed()
    p3.keyboard.press("Tab")
    check("phone: reduced motion keeps timer usable", p3.locator("#timerBtn").is_enabled())
    p3.screenshot(path=ctx.SHOTS / "review-ux-phone.png")

    # the Team page shows it
    tp = page.context.new_page()
    tp.goto(f"{BASE}/admin")
    tp.wait_for_function("() => document.querySelector('.timed-review')", timeout=15000)
    check("team page: the class report shows the timed review", "review she timed herself" in tp.inner_text("body"))
    tp.close()
    c3.close()
    check("review ux: no JavaScript errors", not errors, errors)
