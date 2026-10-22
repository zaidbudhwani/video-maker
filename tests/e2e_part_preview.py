"""The plain preview before a remake (web/index.html previewRemake, the #plan box). Works on its own:

    E2E_PORT=8334 E2E_ONLY=preview venv/Scripts/python tests/e2e_site.py

The preview itself is the REAL route (POST /api/jobs/<id>/feedback/preview, no key, so the plain-phrase reader): the test
writes a finished video's report into the test site's throwaway data folder. Opening the video (GET /api/jobs/<id>) and
the remake itself (POST .../feedback) are answered by the test (page.route), so no video is made and nothing is sent
anywhere. Checks: a typed note is shown back as I will / I'll keep / I can't with Confirm and Back; nothing starts before
Confirm; Back changes nothing; a vague note gets one question; button-only changes skip the preview."""
import json

J = {"content-type": "application/json"}
JOB = "web-pv-0001"


def run(page, ctx):
    BASE, check = ctx.BASE, ctx.check
    out = ctx.DATA / "out"
    settings = {"caption_size": 78, "caption_margin": 0.36, "pause_gap": 0.7, "music_db": -22.0, "music_on": True, "format": "auto"}
    report = {"job_id": JOB, "mode": "voice", "length": 31.2, "music": None, "denoised": False, "copy": "ok",
              "parts": [{"file": "final.mp4", "length": 31.2, "size_mb": 18.4}], "settings": settings,
              "timeline": [], "warnings": [], "not_shown": [], "uncertain_words": []}
    (out / JOB).mkdir(parents=True, exist_ok=True)
    (out / JOB / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (out / f"{JOB}.owner").write_text("tester", encoding="utf-8")
    copy = {"titles": ["A title"], "hook": "Hi.", "description": "A description.", "cta": "Message me.", "format": "other",
            "format_label": "Video", "captions": {}, "checklist": [], "review_notes": []}
    tr = {"burned_in": True, "parts": ["Hello there."], "text": "Hello there."}
    jobs = {j: {"status": "done", "report": {**report, "job_id": j}, "copy": copy, "transcript": tr} for j in (JOB, "web-pv-0002")}
    errors, sent, previews = [], [], []

    def handle(route):
        req = route.request
        m = req.url.split(BASE, 1)[1].split("?", 1)[0].split("/")
        if len(m) == 4 and m[2] == "jobs" and req.method == "GET" and m[3] in jobs:
            return route.fulfill(status=200, headers=J, body=json.dumps(jobs[m[3]]))
        if len(m) == 5 and m[4] == "feedback":
            sent.append(json.loads(req.post_data or "{}"))
            return route.fulfill(status=200, headers=J, body=json.dumps({"job_id": "web-pv-0002", "applied": ["x"], "unsupported": [], "remembered": []}))
        if len(m) == 6 and m[5] == "preview":
            previews.append(json.loads(req.post_data or "{}"))
        if len(m) == 5 and m[4] == "clips":
            return route.fulfill(status=200, headers=J, body=json.dumps({"clips": []}))
        return route.continue_()   # the preview goes to the real site

    p = page.context.new_page()
    p.set_viewport_size({"width": 390, "height": 844})
    p.on("pageerror", lambda e: errors.append(str(e)))
    p.route("**/api/jobs/**", handle)

    def open_panel():
        p.goto(BASE + f"/?job={JOB}")
        p.wait_for_function("() => !document.getElementById('done').classList.contains('hidden')", timeout=20000)
        p.click("#fbOpen")
        p.wait_for_selector("#fbCard:not(.hidden)")
        sent.clear()
        previews.clear()

    print("Plain preview before a remake", flush=True)

    # 1. a typed note: the preview, nothing started, Back changes nothing
    open_panel()
    p.fill("#comment", "make the captions bigger and add a zoom")
    p.click("#remake")
    p.wait_for_selector("#plan:not(.hidden)", timeout=15000)
    plan = p.inner_text("#plan")
    check("preview: says what it will do, in plain words", "I will:" in plan and "Make the captions bigger" in plan, plan)
    check("preview: says what stays the same", "I'll keep:" in plan and "Your clip order" in plan, plan)
    check("preview: says what it can't do", "I can't do this" in plan and "Add a zoom" in plan, plan)
    check("preview: nothing is remade before Confirm", not sent and p.locator("#working").is_hidden(), sent)
    check("preview: it came from the dry-run route with her note", previews and previews[-1].get("comment") == "make the captions bigger and add a zoom", previews)
    check("preview: Confirm and Back are shown, Remake is out of the way",
          p.locator("#planGo").is_visible() and p.locator("#planBack").is_visible() and p.locator("#remake").is_hidden())
    check("preview: keyboard focus moves to the preview", p.evaluate("document.activeElement && document.activeElement.id") == "planHead")
    sizes = [p.locator(s).bounding_box() for s in ("#planGo", "#planBack")]
    check("preview: Confirm and Back are at least 44px tall", all(b and b["height"] >= 43.5 for b in sizes), sizes)
    p.screenshot(path=ctx.SHOTS / "preview-1-plan.png", full_page=True)
    p.click("#planBack")
    check("Back: the preview closes, nothing is sent, her note is still there",
          p.locator("#plan").is_hidden() and not sent and p.input_value("#comment") == "make the captions bigger and add a zoom"
          and p.locator("#remake").is_visible(), sent)
    check("Back: focus returns to her note", p.evaluate("document.activeElement && document.activeElement.id") == "comment")

    # 2. Confirm starts the remake, with the preview's token
    p.click("#remake")
    p.wait_for_selector("#plan:not(.hidden)", timeout=15000)
    p.click("#planGo")
    p.wait_for_function("() => document.querySelector('#versionTag .version')", timeout=30000)
    body = sent[-1] if sent else {}
    check("Confirm: the remake is asked for, with the token of what she saw", body.get("remake") is True and isinstance(body.get("plan"), str)
          and len(body.get("plan")) == 32 and body.get("comment") == "make the captions bigger and add a zoom", body)

    # 3. editing the note after the preview closes the (now out of date) preview
    open_panel()
    p.fill("#comment", "make the captions bigger")
    p.click("#remake")
    p.wait_for_selector("#plan:not(.hidden)", timeout=15000)
    p.fill("#comment", "make the captions smaller")
    check("an edited note closes the old preview", p.locator("#plan").is_hidden() and not sent)

    # 4. a vague note: one question, no guess; her answer is planned and sent
    open_panel()
    p.fill("#comment", "fix the captions")
    p.click("#remake")
    p.wait_for_selector("#plan:not(.hidden)", timeout=15000)
    check("vague: one question instead of a guess", p.inner_text("#planQ") == "Should the captions be bigger or smaller?"
          and p.locator("#planOpts button").all_inner_texts() == ["Bigger", "Smaller"], p.inner_text("#plan"))
    check("vague: nothing is planned for the captions yet, and Confirm waits for the answer",
          "I will:" not in p.inner_text("#planBody") and p.is_disabled("#planGo"), p.inner_text("#planBody"))
    hs = [b.bounding_box()["height"] for b in p.locator("#planOpts button").all()]
    check("vague: the answers are 44px tall", all(h >= 43.5 for h in hs), hs)
    p.screenshot(path=ctx.SHOTS / "preview-2-question.png", full_page=True)
    p.click("#planOpts button:has-text('Smaller')")
    p.wait_for_function("() => document.getElementById('planAsk').classList.contains('hidden')", timeout=15000)
    check("vague: her answer is sent back as a choice, and the question is not asked again",
          previews[-1].get("answered") == ["captions"] and (previews[-1].get("changes") or {}).get("caption_size") == 68, previews[-1:])
    check("vague: the preview now says it will make them smaller", "Make the captions smaller" in p.inner_text("#planBody")
          and p.is_enabled("#planGo"), p.inner_text("#planBody"))
    p.click("#planGo")
    p.wait_for_function("() => document.querySelector('#versionTag .version')", timeout=30000)
    check("vague: the remake gets her answer", (sent[-1].get("changes") or {}).get("caption_size") == 68 and sent[-1].get("plan"), sent[-1:])

    # 5. buttons only: no preview (the "Will change:" line already says it), straight to the remake
    open_panel()
    p.click('.opt[data-k="size"][data-v="1"]')
    p.click("#remake")
    p.wait_for_function("() => document.querySelector('#versionTag .version')", timeout=30000)
    check("buttons only: no preview, the remake is asked for at once", not previews and sent and sent[-1].get("changes") == {"caption_size": 88}, (previews, sent))

    # 6. reduced motion: the preview still opens (no smooth scrolling needed)
    p.emulate_media(reduced_motion="reduce")
    open_panel()
    p.fill("#comment", "cut the pauses tighter")
    p.click("#remake")
    p.wait_for_selector("#plan:not(.hidden)", timeout=15000)
    check("reduced motion: the preview opens", "Cut more of the pauses" in p.inner_text("#planBody"), p.inner_text("#planBody"))
    p.emulate_media(reduced_motion="no-preference")

    check("preview: no JavaScript errors", not errors, errors)
    p.close()
