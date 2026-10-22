"""What the page tells her after she types a note in "Anything else?" and taps Remake (web/index.html askRemake and the
"Done for you" / "Worth knowing" notes). Works on its own:

    E2E_PORT=8173 E2E_ONLY=feedback_words venv/Scripts/python tests/e2e_site.py

GET /api/jobs/<id> and the feedback request are answered by the test (page.route), so no pipeline run starts and
nothing is sent anywhere. The reader that turns words into settings is tested in tests/test_interpret.py; this part
checks the other half: she is told, in words, what changed, what was already true and what the team still has to build."""
import json

J = {"content-type": "application/json"}


def run(page, ctx):
    BASE = ctx.BASE
    check = ctx.check
    errors, sent, previews = [], [], []
    answer = {}
    clips = []     # what /api/jobs/<id>/clips answers (the Clip order list)

    def report(job_id):
        return {"job_id": job_id, "mode": "voice", "length": 31.2, "music": None, "denoised": False, "copy": "ok",
                "parts": [{"file": "final.mp4", "length": 31.2, "size_mb": 18.4}], "settings": {"format": "auto"},
                "timeline": [], "warnings": [], "not_shown": [], "uncertain_words": []}

    copy = {"titles": ["A title"], "hook": "Hi.", "description": "A description.", "cta": "Message me.", "format": "other",
            "format_label": "Video", "captions": {}, "checklist": [], "review_notes": []}
    tr = {"burned_in": True, "parts": ["Hello there."], "text": "Hello there."}
    jobs = {j: {"status": "done", "report": report(j), "copy": copy, "transcript": tr} for j in ("fw1", "fw2")}

    def handle(route):
        req = route.request
        m = req.url.split(BASE, 1)[1].split("?", 1)[0].split("/")   # ['', 'api', 'jobs', id, name]
        if len(m) == 4 and m[2] == "jobs" and req.method == "GET" and m[3] in jobs:
            return route.fulfill(status=200, headers=J, body=json.dumps(jobs[m[3]]))
        if len(m) == 5 and m[4] == "feedback":
            sent.append(json.loads(req.post_data or "{}"))
            return route.fulfill(status=200, headers=J, body=json.dumps(answer))
        if len(m) == 6 and m[4] == "feedback" and m[5] == "preview":   # the plain preview: what a remake would change
            previews.append(json.loads(req.post_data or "{}"))
            will = list(answer.get("applied") or []) if answer.get("job_id") else []
            return route.fulfill(status=200, headers=J, body=json.dumps({"plan": "tok-1", "will": will, "keep": [], "cant": [],
                                                                         "already": [], "guide": [], "question": None, "changes": {}}))
        if len(m) == 5 and m[4] == "clips":
            return route.fulfill(status=200, headers=J, body=json.dumps({"clips": clips}))
        return route.continue_()

    p = page.context.new_page()
    p.set_viewport_size({"width": 430, "height": 900})
    p.on("pageerror", lambda e: errors.append(str(e)))
    p.route("**/api/jobs/**", handle)

    def type_note_and_remake(text):
        p.goto(BASE + "/?job=fw1")
        p.wait_for_function("() => !document.getElementById('done').classList.contains('hidden')", timeout=20000)
        p.click("#fbOpen")
        p.wait_for_selector("#fbCard:not(.hidden)")
        p.fill("#comment", text)
        sent.clear()
        p.click("#remake")
        if answer.get("job_id"):   # something will change: the preview shows first, the remake starts on Confirm
            p.wait_for_selector("#plan:not(.hidden)", timeout=15000)
            p.click("#planGo")

    print("Feedback in words", flush=True)

    # 1. a note that changed something, was already true in part, and asked for something no setting can do
    answer.clear()
    answer.update({"job_id": "fw2", "applied": ["caption size 88"],
                   "unsupported": ["Already that way: cutting ums.", "make it sparkle"], "remembered": []})
    type_note_and_remake("make the captions bigger, take out the ums and make it sparkle")
    p.wait_for_function("() => document.querySelector('#versionTag .version')", timeout=30000)
    p.wait_for_function("() => !document.getElementById('done').classList.contains('hidden')", timeout=30000)
    check("words: a typed note is previewed first, then sent with the preview's token", bool(previews) and sent and sent[-1].get("plan") == "tok-1", (previews[-1:], sent[-1:]))
    check("words: the typed note is what was sent", bool(sent) and "bigger" in sent[-1].get("comment", "") and sent[-1].get("remake") is True, sent[-1:])
    notes = p.inner_text("#notes")
    check("words: the change that was made is named", "Changed as you asked: caption size 88" in notes, notes)
    check("words: something already true is said so, as plain information",
          p.locator("#notes .chip:not(.warn)", has_text="Already that way: cutting ums.").count() == 1, notes)
    check("words: 'already true' is not sent to the team",
          "sent to the team: Already" not in notes, notes)
    check("words: a request no setting can do is marked as needing the team",
          p.locator("#notes .chip.warn", has_text="Not changed yet, sent to the team: make it sparkle").count() == 1, notes)
    check("words: the changed line sits with what was done for her, the team line with what is worth knowing",
          p.locator("#notes .chips").first.inner_text().count("Changed as you asked") == 1
          and "make it sparkle" not in p.locator("#notes .chips").first.inner_text(), notes)

    # 2. nothing changed and the note was only about things already true: no remake, and she is told why
    answer.clear()
    answer.update({"ok": True, "applied": [], "unsupported": ["Already that way: cutting ums."], "remembered": []})
    type_note_and_remake("take out the ums")
    p.wait_for_function("() => document.getElementById('fbErr').textContent.length > 0", timeout=15000)
    err = p.inner_text("#fbErr")
    check("words: only-already-true note says there is nothing to change", err.startswith("Nothing to change."), err)
    check("words: ...and does not say it was sent to the team", "sent it to the team" not in err, err)
    check("words: ...and no remake started", p.locator("#working").is_hidden())

    # 3. nothing found in the note at all (praise): a plain sentence, not an empty list
    answer.clear()
    answer.update({"ok": True, "applied": [], "unsupported": [], "remembered": []})
    type_note_and_remake("this is great, thanks")
    p.wait_for_function("() => document.getElementById('fbErr').textContent.length > 0", timeout=15000)
    err = p.inner_text("#fbErr")
    check("words: a note with nothing to change says so without an empty list",
          "didn't find anything" in err and not err.rstrip().endswith(":"), err)

    # 4. a request no setting can do, nothing else: she is told it went to the team
    answer.clear()
    answer.update({"ok": True, "applied": [], "unsupported": ["make it sparkle"], "remembered": []})
    type_note_and_remake("make it sparkle")
    p.wait_for_function("() => document.getElementById('fbErr').textContent.length > 0", timeout=15000)
    err = p.inner_text("#fbErr")
    check("words: an impossible-only note says it was sent to the team and names it",
          "sent it to the team" in err and "make it sparkle" in err, err)

    # 5. "show a different clip in the last part": a pointer to Clip order, opened for her, not "sent to the team"
    GUIDE = ("To choose which clip plays where, use Clip order below: move the clip you want to its place, "
             "then tap Make it in this order.")
    clips[:] = [{"n": 1, "name": "kitchen.mp4"}, {"n": 2, "name": "deck.mp4"}, {"n": 3, "name": "yard.mp4"}]
    answer.clear()
    answer.update({"ok": True, "applied": [], "unsupported": [GUIDE], "remembered": []})
    type_note_and_remake("edit the last part to show a different clip")
    p.wait_for_function("() => !document.getElementById('fbGuide').classList.contains('hidden')", timeout=15000)
    check("words: a which-clip-where note shows the Clip order pointer", p.inner_text("#fbGuide") == GUIDE, p.inner_text("#fbGuide"))
    check("words: ...not as 'sent to the team' and not as an error", p.inner_text("#fbErr").strip() == "", p.inner_text("#fbErr"))
    check("words: ...More options opens at Clip order, with her three clips to move",
          p.evaluate("document.getElementById('moreOpts').open") and p.locator("#orderGroup").is_visible()
          and p.locator("#reorderList li").count() == 3, p.locator("#reorderList li").count())
    p.wait_for_timeout(900)
    box = p.locator("#orderGroup").bounding_box()
    check("words: ...and Clip order is scrolled into view", box and 0 <= box["y"] < 900, box)
    check("words: ...no remake was started", p.locator("#working").is_hidden())

    # 6. the same note on a one-clip video: say plainly why there is nothing to choose
    clips[:] = [{"n": 1, "name": "talk.mp4"}]
    type_note_and_remake("edit the last part to show a different clip")
    p.wait_for_function("() => !document.getElementById('fbGuide').classList.contains('hidden')", timeout=15000)
    check("words: on a one-clip video it says there is no other clip to show", "only one clip" in p.inner_text("#fbGuide"), p.inner_text("#fbGuide"))
    clips[:] = []

    jobs["fw1"]["report"]["music"] = "bed.mp3"
    answer.clear()
    answer.update({"job_id": "fw2", "applied": ["music dips while speaking"], "unsupported": [], "remembered": []})
    p.goto(BASE + "/?job=fw1")
    p.wait_for_selector("#done:not(.hidden)")
    p.locator("#fbOpen").click()
    p.locator("#moreOpts summary").click()
    p.locator("#duckToggle").click()
    check("words: ducking appears in the remake summary", "dip music while I speak" in p.inner_text("#summary"))
    sent.clear()
    p.locator("#remake").click()
    p.wait_for_function("document.querySelector('#versionTag .version')")
    check("words: ducking button sends a boolean setting", sent and sent[-1]["changes"].get("music_duck") is True, sent)

    check("words: no JavaScript errors", not errors, errors)
    p.close()
