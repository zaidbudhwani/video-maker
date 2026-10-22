"""Framing choice on the Change panel (web/index.html: #frameGroup, .opt[data-k="frame"], #summary, #remake).
Works on its own:

    E2E_PORT=8171 E2E_ONLY=framing venv/Scripts/python tests/e2e_site.py

GET /api/jobs/<id> and the feedback request are answered by the test (page.route), so no pipeline run starts and
nothing is sent anywhere. The three results: a voice-over (no Framing), a talking-to-camera one and a long video
(Shorts) - both of those show it."""
import json

J = {"content-type": "application/json"}
LABELS = {"auto": "center on me", "fill": "centered crop", "fit": "show whole frame"}


def run(page, ctx):
    BASE = ctx.BASE
    check = ctx.check
    errors, feedback = [], []

    def report(mode, extra=None):
        r = {"job_id": "fr1", "mode": mode, "length": 31.2, "music": None, "denoised": True, "copy": "ok",
             "parts": [{"file": "final.mp4", "length": 31.2, "size_mb": 18.4}], "settings": {"format": "auto"},
             "timeline": [], "warnings": [], "not_shown": [], "uncertain_words": []}
        r.update(extra or {})
        return r

    copy = {"titles": ["A title"], "hook": "Hi.", "description": "A description.", "cta": "Message me.", "format": "other",
            "format_label": "Video", "captions": {}, "checklist": [], "review_notes": []}
    tr = {"burned_in": True, "parts": ["Hello there."], "text": "Hello there."}
    jobs = {
        "fr-voice": {"status": "done", "report": report("voice"), "copy": copy, "transcript": tr},
        "fr-talk": {"status": "done", "report": report("own_audio"), "copy": copy, "transcript": tr},
        "fr-long": {"status": "done", "report": report("highlights", {"settings": {"format": "auto", "highlights": 3}}), "copy": copy, "transcript": tr},
    }

    def handle(route):
        req = route.request
        m = req.url.split(BASE, 1)[1].split("?", 1)[0].split("/")   # ['', 'api', 'jobs', id, name]
        if len(m) == 4 and m[2] == "jobs" and req.method == "GET" and m[3] in jobs:
            return route.fulfill(status=200, headers=J, body=json.dumps(jobs[m[3]]))
        if len(m) == 5 and m[4] == "feedback":
            feedback.append(json.loads(req.post_data or "{}"))
            return route.fulfill(status=200, headers=J, body=json.dumps({"ok": True, "job_id": "fr-next"}))
        if len(m) == 5 and m[4] == "clips":
            return route.fulfill(status=200, headers=J, body='{"clips": []}')
        return route.continue_()

    p = page.context.new_page()
    p.set_viewport_size({"width": 430, "height": 900})
    p.on("pageerror", lambda e: errors.append(str(e)))
    p.route("**/api/jobs/**", handle)

    def open_panel(job_id):
        p.goto(BASE + "/?job=" + job_id)
        p.wait_for_function("() => !document.getElementById('done').classList.contains('hidden')", timeout=20000)
        p.click("#fbOpen")
        p.wait_for_selector("#fbCard:not(.hidden)")
        p.evaluate("document.getElementById('moreOpts').open = true")

    def on_buttons():
        return p.evaluate("[...document.querySelectorAll('.opt[data-k=\"frame\"].on')].map(b => b.dataset.v)")

    # 1. who sees the Framing group
    print("Framing", flush=True)
    open_panel("fr-voice")
    check("framing: hidden for a voice-over video", p.locator("#frameGroup").is_hidden())
    open_panel("fr-talk")
    check("framing: shown for a talking-to-camera video", p.locator("#frameGroup").is_visible())
    open_panel("fr-long")
    check("framing: shown for a long video's Shorts", p.locator("#frameGroup").is_visible())
    check("framing: the three choices are there", p.locator('.opt[data-k="frame"]').evaluate_all("els => els.map(e => e.dataset.v)") == ["auto", "fill", "fit"])

    # 2. each button: selected, named in the summary, Remake enabled; 3. Remake sends that value
    for job_id in ("fr-talk", "fr-long"):
        open_panel(job_id)
        check(f"framing[{job_id}]: nothing picked at first, summary empty, Remake off",
              on_buttons() == [] and p.inner_text("#summary").strip() == "" and p.is_disabled("#remake"))
        for v, label in LABELS.items():
            p.click(f'.opt[data-k="frame"][data-v="{v}"]')
            check(f"framing[{job_id}]: tapping '{v}' selects only it", on_buttons() == [v], on_buttons())
            check(f"framing[{job_id}]: '{v}' shows '{label}' in the summary", label in p.inner_text("#summary"), p.inner_text("#summary"))
            check(f"framing[{job_id}]: '{v}' turns Remake on", p.is_enabled("#remake"))
            feedback.clear()
            p.click("#remake")
            p.wait_for_function("() => document.querySelector('#versionTag .version') || !document.getElementById('working').classList.contains('hidden')", timeout=15000)
            body = feedback[-1] if feedback else {}
            check(f"framing[{job_id}]: Remake with '{v}' sends framing '{v}' and a remake", (body.get("changes") or {}).get("framing") == v and body.get("remake") is True, body)
            check(f"framing[{job_id}]: ...and only that change", list((body.get("changes") or {})) == ["framing"], body.get("changes"))
            open_panel(job_id)

    # 4. tapping the same button again undoes the pick; the page keeps working
    open_panel("fr-talk")
    p.click('.opt[data-k="frame"][data-v="fill"]')
    p.click('.opt[data-k="frame"][data-v="fill"]')
    check("framing: tapping the same button again un-selects it", on_buttons() == [], on_buttons())
    check("framing: ...the summary clears and Remake goes off again", p.inner_text("#summary").strip() == "" and p.is_disabled("#remake"), p.inner_text("#summary"))
    p.click('.opt[data-k="frame"][data-v="auto"]')   # the video's own current value is "auto" (the default)
    check("framing: picking the value it already has does not break the panel", on_buttons() == ["auto"])
    p.click('.opt[data-k="frame"][data-v="fit"]')
    check("framing: switching to another choice moves the selection", on_buttons() == ["fit"] and "show whole frame" in p.inner_text("#summary") and "center on me" not in p.inner_text("#summary"))
    check("framing: no JavaScript errors", not errors, "; ".join(errors[:3]))
    p.close()
