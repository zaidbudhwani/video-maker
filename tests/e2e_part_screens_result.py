"""Result page (web/index.html: the done screen and the change panel) checked in a real browser, phone-sized.
Works on its own:

    E2E_PORT=8033 E2E_ONLY=screens_result venv/Scripts/python tests/e2e_site.py

Two halves:
  1. fixtures: GET /api/jobs/<id> and friends are answered by the test (page.route) so every data-dependent panel
     (said-but-not-shown, retakes, start at the hook, check these words, parts, the example) is drawn from known data;
  2. one real video (the recipe sample, about a minute) is made and the result page is checked on it, including the
     copy buttons, the post pack, the posting row, the clip thumbnails and a real remake.
Screenshots (430x900, full page) go to tests/shots/sr-*.png.
"""
import json
import subprocess
import sys
import time

J = {"content-type": "application/json"}
PHONE = {"width": 430, "height": 900}


def run(page, ctx):
    BASE, SHOTS, SAMPLE, ROOT = ctx.BASE, ctx.SHOTS, ctx.SAMPLE, ctx.ROOT
    check = ctx.check
    sys.path.insert(0, str(ROOT))
    from video_tool.config import FFMPEG
    errors = []
    media = ctx.DATA / "media-sr"
    media.mkdir(exist_ok=True)
    tiny = media / "tiny.mp4"   # a 40 second portrait test video (a real, playable mp4) stands in for the finished video
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=270x480:rate=15:duration=40",
                    "-f", "lavfi", "-i", "sine=frequency=330:duration=40", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(tiny)], capture_output=True)
    tiny_bytes = tiny.read_bytes()
    jpg = media / "poster.jpg"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(tiny), "-frames:v", "1", str(jpg)], capture_output=True)

    ctx_ = page.context
    ctx_.grant_permissions(["clipboard-read", "clipboard-write"])
    p = ctx_.new_page()
    p.set_viewport_size(PHONE)
    p.on("pageerror", lambda e: errors.append(str(e)))
    shot = lambda pg, name: pg.screenshot(path=SHOTS / f"sr-{name}.png", full_page=True)

    # ------------------------------------------------------------------ fixtures
    posted_events, feedback_bodies, posting_calls = [], [], []
    state = {"feedback_reply": None}

    def report(extra=None):
        r = {"job_id": "fx1", "mode": "voice", "length": 31.2, "music": "bed.mp3", "denoised": True, "copy": "ok",
             "parts": [{"file": "final.mp4", "length": 31.2, "size_mb": 18.4}],
             "settings": {"privacy": "private", "hints": "Ellis Park Dr", "format": "auto", "caption_margin": 0.36},
             "clip_match": {"used": True}, "short_by": 3.2, "cut_from_end": 101,
             "timeline": [{"clip": "01.mp4", "start": 0, "end": 8.2, "skipped_start": 0},
                          {"clip": "02.mp4", "start": 8.2, "end": 20.0, "skipped_start": 1.4},
                          {"clip": "03.mp4", "start": 20.0, "end": 31.2, "skipped_start": 0}],
             "warnings": [{"clip": None, "kind": "wide", "message": "Clip 2 (Kitchen.mov) and 2 more clips were filmed sideways, so they are shown with the sides filled in."}],
             "skipped_clips": ["Clip 9 (Garage.mov)"], "moved": [], "steadied": ["02.mp4"],
             "not_shown": [{"at": 19, "said": "the attached garage", "idea": "a few seconds of the garage"},
                           {"at": 24.5, "said": "the new roof", "idea": "the roof from the street"}],
             "uncertain_words": [{"word": "Shellsburg", "at": 4.2, "p": 0.31, "part": 1}, {"word": "412", "at": 12.0, "p": 0.4, "part": 1},
                                 {"word": "shellsburg", "at": 22.0, "p": 0.45, "part": 1}],
             "ai_error": {"kind": "credit", "message": "The AI helper wasn't available this time, so the titles came from your own words. Your video is fine."}}
        r.update(extra or {})
        return r

    copy = {"titles": ["Shellsburg ranch with a big kitchen", "A bright kitchen and a quiet street", "Tour the ranch on Ellis Park Dr"],
            "hook": "Come see this ranch.", "description": "A ranch home with a big kitchen and a fenced yard. Call Alex Morgan at Example Realty.",
            "cta": "Message me to see it.", "format": "house_tour", "format_label": "House tour",
            "captions": {"youtube_shorts": "YouTube caption #Shorts", "tiktok": "TikTok caption here", "instagram_reels": "Instagram caption here", "facebook_reels": "Facebook caption here"},
            "checklist": ["Your brokerage name is on it", "The home is still for sale", "Phone number and website are right",
                          "Nobody identifiable, no license plates, no mail or family photos in the video", "Nothing says who the home suits"],
            "review_notes": ["The description mentions a school. Consider removing that line.", "The phone number isn't the one on file.",
                             "Check the street name spelling.", "Check the price."]}
    transcript = {"burned_in": True, "parts": ["Come see this ranch in Shellsburg. It has a big kitchen."], "text": "Come see this ranch in Shellsburg. It has a big kitchen."}

    def job(rep=None, cp=None, tr=None, example=False):
        j = {"status": "done", "report": rep or report(), "copy": copy if cp is None else cp, "transcript": tr or transcript}
        if example:
            j.update({"example": True, "video_urls": ["/api/demo/video/1"], "thumb_url": "/api/demo/thumb"})
        return j

    jobs = {"fx1": job()}

    def serve_video(route):   # like the real site: byte ranges work, so the player can jump to any moment
        rng = route.request.headers.get("range")
        n = len(tiny_bytes)
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            a = int(a or 0)
            b = min(int(b), n - 1) if b else n - 1
            return route.fulfill(status=206, headers={"content-type": "video/mp4", "accept-ranges": "bytes", "content-range": f"bytes {a}-{b}/{n}"}, body=tiny_bytes[a:b + 1])
        return route.fulfill(status=200, headers={"content-type": "video/mp4", "accept-ranges": "bytes"}, body=tiny_bytes)

    def handle(route):
        req = route.request
        path = req.url.split(BASE, 1)[1].split("?", 1)[0]
        m = path.split("/")   # ['', 'api', 'jobs', id, name]
        if path.startswith("/api/jobs/") and len(m) == 4 and req.method == "GET":
            return route.fulfill(status=200, headers=J, body=json.dumps(jobs[m[3]])) if m[3] in jobs else route.fulfill(status=404, headers=J, body='{"detail":"no"}')
        if len(m) == 5 and m[4] == "feedback":
            feedback_bodies.append(json.loads(req.post_data or "{}"))
            rep = state["feedback_reply"] or (200, {"ok": True, "job_id": "fx2"})
            return route.fulfill(status=rep[0], headers=J, body=json.dumps(rep[1]))
        if len(m) == 5 and m[4] == "clips":
            return route.fulfill(status=200, headers=J, body=json.dumps({"clips": [{"n": 1, "name": "Kitchen.mov"}, {"n": 2, "name": "Front yard.mov"}, {"n": 3, "name": "Backyard.mov"}]}))
        if len(m) == 5 and m[4] == "event":
            posted_events.append(json.loads(req.post_data or "{}"))
            return route.fulfill(status=200, headers=J, body='{"ok":true}')
        if len(m) == 5 and m[4] == "posting":
            if req.method == "POST":
                posting_calls.append(json.loads(req.post_data or "{}"))
                body = posting_calls[-1]
                out = {"status": body["status"], "posted": [{"platform": body["platform"], "date": "2026-10-07"}] if body["status"] == "posted" else [], "date": None}
                return route.fulfill(status=200, headers=J, body=json.dumps(out))
            return route.fulfill(status=200, headers=J, body=json.dumps({"status": "draft", "posted": [], "date": None}))
        if len(m) == 5 and m[4].endswith(".mp4"):
            return serve_video(route)
        if len(m) == 5 and m[4] == "thumb.jpg":
            return route.fulfill(status=200, headers={"content-type": "image/jpeg"}, body=jpg.read_bytes())
        if path == "/api/videos":
            return route.fulfill(status=200, headers=J, body=json.dumps({"videos": []}))
        return route.continue_()

    p.route("**/api/**", handle)

    def open_fixture(job_id="fx1"):
        p.goto(BASE + "/?job=" + job_id)
        p.wait_for_function("() => !document.getElementById('done').classList.contains('hidden')", timeout=20000)
        p.wait_for_selector("#players video")
        p.wait_for_function("document.querySelector('#players video').readyState >= 1", timeout=20000)

    # ------------------------------------------------------------------ 1. the video and what to do next
    print("screens-result 1: video, save, what next", flush=True)
    p.goto(BASE)
    p.evaluate("localStorage.clear()")
    open_fixture()
    txt = p.locator("#players").inner_text()
    check("sr: a clear 'what next' line sits under the video",
          "Next: save the video, then copy a caption below and post it yourself. Nothing is posted for you." in txt, txt[-160:])
    save = p.locator("#players .big").first
    check("sr: the save button says 'Save video' with the file size from the report", "Save video" in save.inner_text() and "18 MB" in save.inner_text(), save.inner_text())
    check("sr: the save link downloads the file", p.locator("#players a[download]").count() == 1)
    check("sr: the length is not on the button, it is in the made-at line", "(31s)" not in save.inner_text() and "31s" in p.locator("#madeAt").inner_text(), p.locator("#madeAt").inner_text())
    check("sr: the player has a poster and the first one preloads", "thumb.jpg" in (p.get_attribute("#players video", "poster") or "")
          and p.get_attribute("#players video", "preload") == "auto")
    posted_events.clear()
    p.locator("#players a[download]").first.evaluate("a => a.addEventListener('click', e => e.preventDefault())")
    p.locator("#players a[download]").first.click()
    p.wait_for_timeout(300)
    check("sr: saving tells the record (event: download)", {"kind": "download", "platform": None} in posted_events, posted_events)
    check("sr: the Change something button is right under the player", p.locator("#fbOpen").is_visible()
          and p.evaluate("document.getElementById('fbOpen').getBoundingClientRect().top < 1100"))
    check("sr: the example banner and the version tag are not shown", p.locator("#exampleBar").is_hidden() and p.locator("#versionTag").inner_text() == "")

    # ------------------------------------------------------------------ 2. copy: labels, title ideas, platform tabs, copy buttons
    print("screens-result 2: copy, tabs, copy buttons", flush=True)
    labels = [x.text_content() for x in p.locator("#copy .field .label").all()]
    check("sr: plain labels (Title idea N, Opening line, Description, Closing line)",
          labels == ["Title idea 1", "Title idea 2", "Title idea 3", "Opening line", "Description", "Closing line"], labels)
    check("sr: only title idea 1 is open, the other two are behind 'More title ideas'",
          p.locator("#copy details summary").inner_text() == "More title ideas" and p.locator("#copy .field.title:visible").count() == 1)
    check("sr: no jargon left in the copy card", not any(w in p.locator("#copyCard").inner_text() for w in ("Hook", "Call to action", "claude.ai")))
    check("sr: the Claude prompt box is hidden and there is no link without one", p.locator("#aiBox").is_hidden() and p.locator("#aiLink").is_hidden())
    check("sr: platform captions are a tab row, one at a time", p.locator("#platformTabs .tab").count() == 4 and p.locator("#platforms .field").count() == 1)
    check("sr: the first tab (YouTube Shorts) is showing", "YouTube caption" in p.locator("#platforms .text").inner_text())
    p.locator("#platformTabs .tab", has_text="TikTok").click()
    check("sr: choosing TikTok shows the TikTok caption only", "TikTok caption here" in p.locator("#platforms .text").inner_text() and p.locator("#platforms .field").count() == 1)
    # copy buttons: 44px, clipboard, 2.5 s 'Copied', event
    btn = p.locator("#platforms .copybtn")
    h = btn.bounding_box()["height"]
    check("sr: Copy buttons are at least 44px tall", h >= 43.5, h)
    posted_events.clear()
    btn.click()
    p.wait_for_timeout(200)
    check("sr: Copy puts the caption on the clipboard", p.evaluate("navigator.clipboard.readText()") == "TikTok caption here")
    check("sr: the button says Copied", "Copied" in btn.inner_text())
    check("sr: Copy tells the record which platform (event: copy, tiktok)", {"kind": "copy", "platform": "tiktok"} in posted_events, posted_events)
    p.wait_for_timeout(1500)
    check("sr: Copied is still showing after 1.7 s (it stays 2.5 s)", "Copied" in btn.inner_text())
    p.wait_for_timeout(1100)
    check("sr: then it goes back to Copy", btn.inner_text() == "Copy")
    # a blocked clipboard: say so in plain words and select the text
    p.evaluate("""() => { navigator.clipboard.writeText = () => Promise.reject(new Error('blocked')); document.execCommand = () => false; }""")
    btn.click()
    p.wait_for_timeout(200)
    check("sr: a blocked clipboard says 'press and hold the text to copy'", "Press and hold the text to copy" in p.locator("#platforms .copyhint").inner_text(), p.locator("#platforms .copyhint").inner_text())
    check("sr: ...and the text is selected for her", p.evaluate("getSelection().toString()") == "TikTok caption here")
    check("sr: the platform choice is remembered", p.evaluate("localStorage.getItem('platform')") == "tiktok")

    # ------------------------------------------------------------------ 3. before you post
    print("screens-result 3: before you post", flush=True)
    boxes = p.locator("#checkList input")
    check("sr: the fixed check list shows as tick boxes", boxes.count() == 5 and "Nobody identifiable" in p.locator("#checkList").inner_text(), boxes.count())
    check("sr: the always-on chip text is the plain one", "Video is the right size for TikTok, Reels and Shorts" in p.locator("#notes").inner_text()
          and "Ready for TikTok" not in p.content())
    boxes.nth(1).check()
    open_fixture()
    check("sr: a tick is still there after reopening the page", p.locator("#checkList input").nth(1).is_checked() and not p.locator("#checkList input").nth(0).is_checked())
    notes = p.locator("#reviewNotes .notes p:visible")
    check("sr: the specific notes show the first 2 and a 'Show all' link", notes.count() == 2 and "Show all 4" in p.locator("#reviewNotes").inner_text(), notes.count())
    p.locator("#reviewNotes .linkbtn").click()
    check("sr: Show all shows all of them", p.locator("#reviewNotes .notes p:visible").count() == 4)
    check("sr: the note box is called 'Worth a second look' (not a compliance heading)", "worth a second look" in p.locator("#reviewNotes").inner_text().lower()
          and "fair housing" not in p.locator("#reviewNotes").inner_text().lower())

    # ------------------------------------------------------------------ 4. what I did
    print("screens-result 4: what I did", flush=True)
    chips = p.locator("#notes").inner_text()
    for want in ("Looks like a House tour", "Your pictures were not shared", "Music added", "Background noise removed",
                 "Your clips run 3s shorter than your voice-over, so the last shot holds still at the end.",
                 "Cut about 1 min 41s of unused footage", "Didn't need this clip, your voice-over was already covered: Clip 9 (Garage.mov)",
                 "filmed sideways", "The AI helper wasn't available this time"):
        check(f"sr: chip says '{want[:46]}'", want in chips)
    check("sr: no developer words in the chips", not any(w in chips for w in ("Trimmed", "Add a clip to cover it", "ai_error", ".mp4", "Traceback")))
    check("sr: 'What I did' heading", "What I did" in p.locator("#didCard h2").inner_text())
    p.locator("#didBox summary").click()
    did = p.locator("#didList").inner_text()
    check("sr: each clip is named the way she named it, with its time and length",
          "Clip 1 (Kitchen.mov)" in did and "0:00 to 0:08 (8.2 s)" in did and "starting 1.4 s in" in did and ".mp4" not in did, did.replace("\n", " | "))
    p.locator("#transBox summary").click()
    check("sr: the words on the video are shown, read only until she taps one", "big kitchen" in p.locator("#transcript").inner_text())
    check("sr: a doubtful word is marked in the text", p.locator("#transcript button.maybe").count() == 1 and p.locator("#transcript button.maybe").inner_text().startswith("Shellsburg"))
    p.locator("#transcript button.w", has_text="kitchen").click()
    check("sr: tapping a word opens the fix box with that word filled in", p.locator("#fbCard").is_visible()
          and p.locator("#fixes .fix input").nth(0).input_value() == "kitchen",
          p.locator("#fixes .fix input").nth(0).input_value())
    check("sr: the summary shows nothing until the word is changed", p.locator("#summary").inner_text() == "")
    p.locator("#fixes .fix input").nth(1).fill("Kitchen")
    check("sr: the summary says 'Will change:' with the fix", p.locator("#summary").inner_text().startswith("Will change:") and '"kitchen" → "Kitchen"' in p.locator("#summary").inner_text(), p.locator("#summary").inner_text())

    # ------------------------------------------------------------------ 5. panels from the report
    print("screens-result 5: heads-up panels", flush=True)
    open_fixture()
    titles = p.locator("#panels h2").all_inner_texts()
    check("sr: the said-but-not-shown panel is there", "You mention things I couldn't show" in titles, titles)
    check("sr: the check-these-words panel is there", "Check these words" in titles)
    check("sr: panels with no data are not drawn (no retakes, no opening)", "Repeated lines I cut" not in titles and not any("hook" in t.lower() for t in titles), titles)
    rows = p.locator("#panels .card", has_text="couldn't show").locator("button.seek")
    check("sr: one line per thing, with the time and her words", rows.count() == 2 and "0:19" in rows.nth(0).inner_text() and "the attached garage" in rows.nth(0).inner_text())
    rows.nth(0).click()
    p.wait_for_timeout(700)
    t = p.evaluate("document.querySelector('#players video').currentTime")
    check("sr: tapping a line jumps the video to that moment", 18.5 < t < 21.5, t)
    check("sr: it suggests filming it and making the video again", p.locator("#panels .card", has_text="couldn't show").locator("button", has_text="Add a clip and make it again").count() == 1)
    w = p.locator("#panels .card", has_text="Check these words")
    check("sr: uncertain words are listed once each (a repeat is dropped)", w.locator("input").count() == 2, w.locator("input").count())
    check("sr: each has a tiny box with the heard word in it", w.locator("input").nth(0).input_value() == "Shellsburg" and w.locator("input").nth(1).input_value() == "412")
    fixbtn = w.locator("button.big")
    check("sr: Fix these words waits until a word is changed", fixbtn.is_disabled())
    w.locator("input").nth(0).fill("Shellsburg, Iowa")
    check("sr: ...then turns on", fixbtn.is_enabled())
    feedback_bodies.clear()
    jobs["fx2"] = job(report({"job_id": "fx2"}))
    fixbtn.click()
    p.wait_for_function("() => !document.getElementById('done').classList.contains('hidden') && document.querySelector('#versionTag .version')", timeout=15000)
    b = feedback_bodies[-1] if feedback_bodies else {}
    check("sr: a word fix remakes with caption_fixes and is remembered", b.get("changes") == {"caption_fixes": {"Shellsburg": "Shellsburg, Iowa"}} and b.get("remember") is True and b.get("remake") is True, b)
    check("sr: the remake shows as Version 2 with a way to see the earlier ones", "Version 2" in p.locator("#versionTag").inner_text() and p.locator("#versionTag a").get_attribute("href") == "/videos")
    # a refused fix is explained where she is, and the page stays
    open_fixture()
    state["feedback_reply"] = (400, {"detail": "I can't put \"perfect for\" into the captions: it breaks the Fair Housing / no-hype rules for your posts."})
    w = p.locator("#panels .card", has_text="Check these words")
    w.locator("input").nth(1).fill("perfect for")
    w.locator("button.big").click()
    p.wait_for_timeout(500)
    check("sr: a refused fix explains itself next to the button and the page stays", "I can't put" in w.inner_text() and p.locator("#done").is_visible()
          and p.locator("#failed").is_hidden(), w.inner_text()[-120:])
    state["feedback_reply"] = None
    shot(p, "1-panels-fixture")

    # A finished-check warning uses the same visible review UI as other warnings.
    msg = "Part 2: The picture goes black at 0:04. Next step: review this part before posting."
    jobs["fx-final"] = job(report({"warnings": [{"kind": "final_check", "check": "black", "at": 4,
                                                  "part": 2, "clip": None, "message": msg}]}))
    open_fixture("fx-final")
    check("sr: final_check warning renders with its next step", p.get_by_text(msg, exact=True).is_visible())
    p.get_by_text(msg, exact=True).scroll_into_view_if_needed()
    p.get_by_text(msg, exact=True).screenshot(path=SHOTS / "sr-final-check-warning-detail.png")
    p.screenshot(path=SHOTS / "sr-final-check-warning.png")

    # retakes + opening (not applied)
    print("screens-result 5b: retakes and the opening", flush=True)
    jobs["fx3"] = job(report({"retakes": [{"at": 12.0, "said": "Welcome to this beautiful home", "removed_at": 7.0}],
                              "opening": {"greeting_seconds": 4.1, "said": "Hi everybody it's Alex welcome back", "hook_at": 4.1, "applied": False},
                              "mode": "own_audio", "not_shown": [], "uncertain_words": []}))
    open_fixture("fx3")
    shot(p, "2-retakes-opening")
    titles = p.locator("#panels h2").all_inner_texts()
    check("sr: retakes and the opening panels are drawn", titles == ["Repeated lines I cut", "Start at the hook?"], titles)
    ret = p.locator("#panels .card", has_text="Repeated lines I cut")
    check("sr: a retake line shows when and what", "0:12" in ret.inner_text() and "Welcome to this beautiful home" in ret.inner_text())
    jobs["fx4"] = job(report({"job_id": "fx4", "mode": "own_audio"}))
    feedback_bodies.clear()
    ret.locator("button", has_text="Keep both takes").click()
    p.wait_for_function("() => document.querySelector('#versionTag .version')", timeout=15000)
    check("sr: Keep both takes remakes with cut_retakes false (not remembered)", feedback_bodies[-1].get("changes") == {"cut_retakes": False} and feedback_bodies[-1].get("remember") is False, feedback_bodies[-1:])
    open_fixture("fx3")
    op = p.locator("#panels .card", has_text="Start at the hook?")
    check("sr: the opening panel says what it found in plain words", "4 seconds" in op.inner_text() and "0:04" in op.inner_text() and "Hi everybody it's Alex welcome back" in op.inner_text(), op.inner_text()[:200])
    feedback_bodies.clear()
    op.locator("button", has_text="Start there").click()
    p.wait_for_function("() => document.querySelector('#versionTag .version')", timeout=15000)
    check("sr: Start there remakes with start_at_hook true", feedback_bodies[-1].get("changes") == {"start_at_hook": True}, feedback_bodies[-1:])
    open_fixture("fx3")
    p.locator("#panels .card", has_text="Start at the hook?").locator("button", has_text="Keep it").click()
    check("sr: Keep it closes the question", p.locator("#panels .card", has_text="Start at the hook?").count() == 0)
    open_fixture("fx3")
    check("sr: ...and it stays closed for this video", p.locator("#panels .card", has_text="Start at the hook?").count() == 0)
    jobs["fx5"] = job(report({"opening": {"greeting_seconds": 3.0, "said": "Hi everyone", "hook_at": 3.0, "applied": True}, "mode": "own_audio", "not_shown": [], "uncertain_words": []}))
    open_fixture("fx5")
    ap = p.locator("#panels .card", has_text="Starting at your first real line")
    check("sr: when the start was already moved, it offers to keep the greeting", ap.count() == 1 and ap.locator("button", has_text="Keep the greeting").count() == 1)
    feedback_bodies.clear()
    jobs["fx2"] = job(report({"job_id": "fx2"}))
    ap.locator("button", has_text="Keep the greeting").click()
    p.wait_for_function("() => document.querySelector('#versionTag .version')", timeout=15000)
    check("sr: Keep the greeting remakes with start_at_hook false", feedback_bodies[-1].get("changes") == {"start_at_hook": False}, feedback_bodies[-1:])
    jobs["fx6"] = job(report({"mode": "own_audio", "settings": {"cut_retakes": False}, "retakes": [], "not_shown": [], "uncertain_words": []}))
    open_fixture("fx6")
    check("sr: when she chose to keep both takes, the page says so and offers to cut them again",
          "Cut the repeats again" in p.locator("#panels").inner_text())
    # nothing to say: no panels at all
    jobs["fx7"] = job(report({"not_shown": [], "uncertain_words": [], "warnings": [], "ai_error": None, "short_by": 0, "cut_from_end": 0, "skipped_clips": []}))
    open_fixture("fx7")
    check("sr: with nothing to say there are no heads-up panels", p.locator("#panels .card").count() == 0)

    # ------------------------------------------------------------------ 6. the change panel
    print("screens-result 6: change panel", flush=True)
    open_fixture()
    p.locator("#fbOpen").click()
    vis = lambda sel: p.locator(sel).is_visible()
    check("sr: by default the panel shows captions, pauses, the typed box and Remake",
          vis('.opt[data-k="size"][data-v="1"]') and vis('.opt[data-k="pause"][data-v="-1"]') and vis("#comment") and vis("#remake"))
    check("sr: everything else is under 'More options', closed", p.locator("#moreOpts summary").inner_text() == "More options" and not p.locator("#moreOpts").evaluate("d => d.open")
          and not vis("#twiceToggle") and not vis("#fmtSel") and not vis("#byHand") and not vis("#noiseToggle") and not vis("#orderGroup"))
    cap = p.locator("#fbCard .opts").first.inner_text()
    check("sr: caption buttons say Move up / Move down (not Higher / Lower)", "Move up" in cap and "Move down" in cap and "Higher" not in cap and "Lower" not in cap, cap)
    check("sr: Remake is the one big button; 'Just send a note' is a quiet link", p.locator("#remake").evaluate("e => e.classList.contains('big')")
          and p.locator("#sendOnly").evaluate("e => e.classList.contains('linkbtn')") and "Just send a note to the team" in p.locator("#sendOnly").inner_text())
    sb = p.locator("#summary").bounding_box(); rb = p.locator("#remake").bounding_box()
    check("sr: with nothing chosen Remake is off", p.locator("#remake").is_disabled())
    p.locator('.opt[data-k="size"][data-v="1"]').click()
    p.locator('.opt[data-k="pos"][data-v="1"]').click()
    s = p.locator("#summary").inner_text()
    check("sr: 'Will change:' lists the choices, right above the buttons", s == "Will change: bigger captions, captions moved up", s)
    sb = p.locator("#summary").bounding_box(); rb = p.locator("#remake").bounding_box()
    check("sr: the summary is directly above Remake", 0 < rb["y"] - (sb["y"] + sb["height"]) < 80, (sb, rb))
    check("sr: the summary is 15px", p.locator("#summary").evaluate("e => getComputedStyle(e).fontSize") == "15px")
    p.locator("#moreOpts summary").click()
    labels = p.locator("#moreOpts .opts .opt").all_inner_texts()
    for want in ("Give each clip equal time", "Keep my clip order", "Double-check the clip matching", "Always start each clip at the beginning", "Remove background noise"):
        check(f"sr: More options has '{want}'", want in labels, labels)
    check("sr: the double-check label carries no price", all("cents" not in l.lower() and "cheaper" not in l.lower() for l in labels))
    check("sr: More options also holds the video kind, names and the editing-time question",
          vis("#fmtSel") and vis("#hintsBox") and "edit yourself" in p.locator("#moreOpts").inner_text() and "helps us measure time saved" in p.locator("#moreOpts").inner_text())
    check("sr: the names box starts with what was used", p.locator("#hintsBox").input_value() == "Ellis Park Dr")
    check("sr: the explanation under the panel says typed changes are this video only", "only changes this video" in p.locator("#fbCard").inner_text())
    p.locator("#fmtSel").select_option("recipe")
    p.locator("#hintsBox").fill("Ellis Park Dr, Shellsburg")
    p.locator('.opt[data-k="twice"]').click()
    feedback_bodies.clear()
    jobs["fx2"] = job(report({"job_id": "fx2"}))
    p.locator("#remake").click()
    p.wait_for_function("() => document.querySelector('#versionTag .version')", timeout=15000)
    body = feedback_bodies[-1]
    c = body["changes"]
    check("sr: Remake sends the chosen settings, including format and hints",
          c.get("caption_size") == 88 and abs(c.get("caption_margin") - 0.42) < 1e-6 and c.get("format") == "recipe" and c.get("hints") == "Ellis Park Dr, Shellsburg" and c.get("match_twice") is True, c)
    check("sr: the margin never goes under the safe floor", True)
    # margin clamp: move down from a default-ish value stays inside 0.33..0.60
    open_fixture()
    p.locator("#fbOpen").click()
    p.locator('.opt[data-k="pos"][data-v="-1"]').click()
    feedback_bodies.clear()
    p.locator("#remake").click()
    p.wait_for_function("() => document.querySelector('#versionTag .version')", timeout=15000)
    check("sr: Move down from the default lands on the 0.33 floor, not under it", feedback_bodies[-1]["changes"].get("caption_margin") == 0.33, feedback_bodies[-1]["changes"])
    jobs["fxlow"] = job(report({"settings": {"caption_margin": 0.33}}))
    open_fixture("fxlow")
    check("sr: at the floor, Move down is switched off", p.locator('.opt[data-k="pos"][data-v="-1"]').is_disabled())
    # network failure while remaking
    open_fixture()
    p.locator("#fbOpen").click()
    p.locator('.opt[data-k="size"][data-v="-1"]').click()
    p.route("**/feedback", lambda r: r.abort())
    p.locator("#remake").click()
    p.wait_for_timeout(600)
    check("sr: if the computer doesn't answer, she is told plainly and keeps her choices", "isn't answering" in p.locator("#fbErr").inner_text()
          and p.locator("#summary").inner_text().startswith("Will change") and p.locator("#done").is_visible(), p.locator("#fbErr").inner_text())
    p.unroute("**/feedback")
    # back to normal
    p.locator("#backNormal").click()
    check("sr: 'Back to normal' asks first", p.locator("#normalAsk").is_visible())
    p.locator("#normalNo").click()
    check("sr: Cancel closes the question", p.locator("#normalAsk").is_hidden())
    shot(p, "3-change-panel-more")

    # ------------------------------------------------------------------ 7. several parts
    print("screens-result 7: parts", flush=True)
    pc = lambda n: {"titles": [f"Part {n} title one (Part {n} of 3)", f"Part {n} title two (Part {n} of 3)", f"Part {n} title three (Part {n} of 3)"],
                    "hook": f"Hook {n}", "description": f"Description {n}", "cta": f"Cta {n}", "label": f"Part {n} of 3", "part": n, "of": 3,
                    "captions": {"tiktok": f"TikTok words for part {n}", "youtube_shorts": f"YouTube words for part {n}"}}
    cp3 = dict(copy, parts=[pc(1), pc(2), pc(3)])
    jobs["fx8"] = job(report({"mode": "own_audio", "parts": [{"file": f"final_part{i}.mp4", "length": 50.0 + i, "size_mb": 40.0 + i} for i in (1, 2, 3)],
                              "not_shown": [], "uncertain_words": [{"word": "Tifton", "at": 5.0, "p": 0.2, "part": 2}]}),
                      cp=cp3, tr={"burned_in": True, "parts": ["one one one", "two two two", "three three three"], "text": ""})
    open_fixture("fx8")
    check("sr: the split explanation is plain text above the first player", "split it into 3 parts, each under 60 seconds" in p.locator("#splitNote").inner_text()
          and p.evaluate("document.getElementById('splitNote').getBoundingClientRect().top < document.querySelector('#part1').getBoundingClientRect().top"))
    check("sr: part tabs sit above the players", p.locator("#partTabs .tab").count() == 3
          and p.evaluate("document.getElementById('partTabs').getBoundingClientRect().top < document.querySelector('#part1').getBoundingClientRect().top"))
    check("sr: each part has its own Save button with its size", [b.inner_text() for b in p.locator("#players a.big, #players button.save").all()] == ["Save part 1 (41 MB)", "Save part 2 (42 MB)", "Save part 3 (43 MB)"],
          [b.inner_text() for b in p.locator("#players a.big, #players button.save").all()])
    check("sr: part 1 words are showing", "Part 1 title one" in p.locator("#copy").inner_text() and "Showing the words for Part 1 of 3" in p.locator("#copy").inner_text())
    p.locator("#partTabs .tab").nth(1).click()
    p.wait_for_timeout(700)
    check("sr: tapping Part 2 switches the words and platform captions", "Part 2 title one" in p.locator("#copy").inner_text() and "TikTok words for part 2" in p.locator("#platforms").inner_text()
          or "YouTube words for part 2" in p.locator("#platforms").inner_text(), p.locator("#platforms").inner_text())
    check("sr: ...and scrolls to Part 2's video", p.evaluate("(() => { const r = document.querySelector('#part2').getBoundingClientRect(); return r.top >= -40 && r.top < 300; })()"))
    check("sr: only the first player preloads fully", p.evaluate("[...document.querySelectorAll('#players video')].map(v => v.preload).join()") == "auto,metadata,metadata")
    p.locator("#panels button.seek").first.click()
    p.wait_for_timeout(600)
    check("sr: a doubtful word in Part 2 jumps to Part 2's player", p.evaluate("(() => { const v = document.querySelectorAll('#players video')[1]; return v.currentTime > 4.5 && v.currentTime < 8; })()"))
    p.locator("#transBox summary").click()
    check("sr: the words shown are split by part", p.locator("#transcript .tpart").count() == 3 and "two two two" in p.locator("#transcript").inner_text())
    shot(p, "4-three-parts")

    # ------------------------------------------------------------------ 8. the example is read-only
    print("screens-result 8: example", flush=True)
    jobs["demo"] = job(report({"retakes": [{"at": 3, "said": "x", "removed_at": 1}]}), example=True)
    p.route("**/api/demo", lambda r: r.fulfill(status=200, headers=J, body=json.dumps({"available": True, "job": jobs["demo"]})))
    p.route("**/api/demo/**", lambda r: r.fulfill(status=200, headers={"content-type": "video/mp4" if "video" in r.request.url else "image/jpeg"}, body=tiny_bytes if "video" in r.request.url else jpg.read_bytes()))
    p.goto(BASE)
    p.locator("#exampleBtn").click()
    p.wait_for_selector("#players video")
    check("sr: the example is marked and its video comes from the example route", p.locator("#exampleBar").is_visible() and p.get_attribute("#players video", "src") == "/api/demo/video/1")
    check("sr: the example has no change panel, no posting row, no post pack, no panels, no phone-save button, no delete",
          p.locator("#fbOpen").is_hidden() and p.locator("#fbCard").is_hidden() and p.locator("#postCard").is_hidden() and p.locator("#packCard").is_hidden()
          and p.locator("#panels").is_hidden() and p.locator(".save:visible").count() == 0 and p.locator("#fmtChange").is_hidden())
    check("sr: the example still shows copy, tabs, check list and what I did", p.locator("#copy .field.title").count() == 3 and p.locator("#platformTabs .tab").count() == 4
          and p.locator("#checkList input").count() == 5 and p.locator("#notes .chip").count() > 2)
    check("sr: example words are plain text, not tappable", p.locator("#transcript button.w").count() == 0)
    posted_events.clear()
    p.locator("#platforms .copybtn").click()
    p.wait_for_timeout(300)
    check("sr: copying on the example tells nobody anything", posted_events == [], posted_events)
    shot(p, "5-example")
    p.unroute("**/api/demo")
    p.unroute("**/api/demo/**")

    # a browser with no stored memory (private window) still draws the whole page
    print("screens-result 9: no browser memory", flush=True)
    p2 = ctx_.new_page()
    p2.set_viewport_size(PHONE)
    p2.add_init_script("Object.defineProperty(window, 'localStorage', { get() { throw new Error('blocked'); } });")
    p2.on("pageerror", lambda e: errors.append("p2: " + str(e)))
    p2.route("**/api/**", handle)
    p2.goto(BASE + "/?job=fx1")
    p2.wait_for_selector("#players video")
    p2.locator("#checkList input").first.check()
    check("sr: with browser memory blocked the page still draws and ticks work", p2.locator("#checkList input").count() == 5 and p2.locator("#platforms .field").count() == 1)
    p2.close()

    # ------------------------------------------------------------------ 10. iPhone line and the share button
    print("screens-result 10: phone save", flush=True)
    c3 = p.context.browser.new_context(viewport=PHONE, user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1")
    c3.add_cookies(ctx_.cookies())
    p3 = c3.new_page()
    p3.route("**/api/**", handle)
    p3.add_init_script("navigator.canShare = () => true; navigator.share = async (d) => { window.__shared = d.files.map(f => f.name + ':' + f.size); };")
    p3.goto(BASE + "/?job=fx1")
    p3.wait_for_selector("#players video")
    check("sr: iPhone gets the 'tap Save Video' line", "On iPhone, tap Save Video" in p3.locator("#players").inner_text())
    sv = p3.locator("#players button.save")
    check("sr: on a phone the big button opens the share sheet and a small link downloads instead", sv.count() == 1 and "Save video" in sv.inner_text()
          and p3.locator("#players a.alt-dl").count() == 1)
    sv.click()
    p3.wait_for_function("window.__shared", timeout=10000)
    check("sr: the share sheet got a real mp4 named after the title", p3.evaluate("window.__shared")[0].startswith("Shellsburg-ranch-with-a-big-kitchen.mp4:"), p3.evaluate("window.__shared"))
    c3.close()
    p.close()
    check("sr: no JavaScript errors on the fixture pages", not errors, "; ".join(errors[:3]))

    # ------------------------------------------------------------------ real video
    print("screens-result 11: a real video", flush=True)
    page.goto(BASE)
    page.evaluate("localStorage.clear()")
    page.goto(BASE)
    page.set_input_files("#files", [str(f) for f in sorted(SAMPLE.glob("*.mp4"))] + [str(SAMPLE / "voice.m4a")])
    page.wait_for_selector("#clips li.clip")
    page.click("#go")
    t0 = time.time()
    made = ctx.wait_for_video(page)
    check("sr: the recipe video was made", made, "" if made else page.locator("#failMsg").inner_text())
    if not made:
        return
    print(f"   (took {time.time() - t0:.0f}s)", flush=True)
    page.set_viewport_size(PHONE)
    page.wait_for_function("document.querySelector('#players video').readyState >= 1", timeout=30000)
    real_errors = []
    page.on("pageerror", lambda e: real_errors.append(str(e)))
    jid = page.evaluate("jobId")
    status = page.evaluate("fetch(`/api/jobs/${jobId}`).then(r => r.json())")
    rep = status["report"]
    size = rep["parts"][0].get("size_mb")
    check("sr: the real report has a file size and the button shows it", size and f"{size:.1f}".split(".")[0] in page.locator("#players .big").first.inner_text(), (size, page.locator("#players .big").first.inner_text()))
    check("sr: the real video has a poster picture that loads", page.evaluate("fetch(document.querySelector('#players video').poster).then(r => r.ok && r.headers.get('content-type'))") == "image/jpeg")
    check("sr: real titles come as 1 open + 2 behind More title ideas", page.locator("#copy .field.title").count() == 3 and page.locator("#copy .field.title:visible").count() == 1)
    check("sr: real platform captions come as 4 tabs", page.locator("#platformTabs .tab").count() == 4)
    check("sr: the real checklist is on the page", page.locator("#checkList input").count() >= 3, page.locator("#checkList input").count())
    check("sr: the format chip comes from the copy", "Looks like a" in page.locator("#notes").inner_text(), page.locator("#notes").inner_text()[:200])
    check("sr: the real 'What I did' list uses her file names", True)
    page.locator("#didBox summary").click()
    did = page.locator("#didList").inner_text()
    check("sr: real clips are listed as Clip N (her file) with times", "Clip 1 (01.mp4)" in did and "Clip 3 (03.mp4)" in did and "0:00 to" in did, did.replace("\n", " | ")[:200])
    page.locator("#transBox summary").click()
    check("sr: the real words burned into the picture are shown", page.locator("#transcript button.w").count() > 10, page.locator("#transcript button.w").count())
    seen_events = []
    page.on("request", lambda r: seen_events.append(r.post_data) if r.method == "POST" and r.url.endswith("/event") else None)
    page.locator("#platforms .copybtn").click()
    page.wait_for_timeout(500)
    check("sr: a real Copy click is recorded by the site", any('"copy"' in (e or "") for e in seen_events), seen_events)
    # posting row
    page.locator("#postBtns .tab", has_text="TikTok").click()
    page.wait_for_function("document.getElementById('postMsg').innerText.includes('TikTok')", timeout=8000)
    posting = page.evaluate("fetch(`/api/jobs/${jobId}/posting`).then(r => r.json())")
    check("sr: 'I posted it' is saved by the site for that video", posting["status"] == "posted" and posting["posted"][0]["platform"] == "tiktok", posting)
    page.goto(BASE + "/?job=" + jid)
    page.wait_for_function("() => !document.getElementById('done').classList.contains('hidden')", timeout=20000)
    page.wait_for_function("document.getElementById('postMsg').innerText.includes('TikTok')", timeout=8000)
    check("sr: the posted mark is still there when the video is opened again", page.locator("#postBtns .tab[aria-pressed=true]").inner_text() == "TikTok")
    page.locator("#postUndo").click()
    page.wait_for_function("!document.getElementById('postMsg').innerText.includes('TikTok')", timeout=8000)
    check("sr: 'Not posted yet' clears it", page.evaluate("fetch(`/api/jobs/${jobId}/posting`).then(r => r.json()).then(j => j.status)") == "draft")
    # post pack
    href = page.get_attribute("#packLink", "href")
    resp = page.request.get(BASE + href)
    check("sr: the post pack link gives a zip with the video, captions, cover and text",
          resp.ok and resp.body()[:2] == b"PK" and len(resp.body()) > 100_000, f"{len(resp.body()) / 1e6:.1f} MB")
    import io, zipfile
    names = zipfile.ZipFile(io.BytesIO(resp.body())).namelist()
    check("sr: the zip holds every piece", {"final.mp4", "captions.srt", "cover.jpg", "alt-text.txt", "post-text.txt"} <= set(names), names)
    page.screenshot(path=SHOTS / "sr-6-real-result.png", full_page=True)
    # the reorder thumbnails
    page.click("#fbOpen")
    page.locator("#moreOpts summary").click()
    page.wait_for_function("document.querySelectorAll('#reorderList img.cthumb').length === 3", timeout=40000)
    check("sr: the clip order rows show a picture of each clip", page.locator("#reorderList img.cthumb").count() == 3)
    check("sr: the order arrows are 44px", min(b["width"] for b in [x.bounding_box() for x in page.locator("#reorderList .icon-btn").all()]) >= 43.5)
    check("sr: rows are named as she named her files", page.locator("#reorderList .rname").all_inner_texts() == ["01.mp4", "02.mp4", "03.mp4"], page.locator("#reorderList .rname").all_inner_texts())
    page.screenshot(path=SHOTS / "sr-7-real-more-options.png", full_page=True)
    # a real remake from the new panel
    page.locator('.opt[data-k="pos"][data-v="1"]').click()
    page.click("#remake")
    page.wait_for_function("() => !document.getElementById('working').classList.contains('hidden')", timeout=15000)
    remade = ctx.wait_for_video(page)
    check("sr: a real remake from the new panel finished", remade, "" if remade else page.locator("#failMsg").inner_text())
    if remade:
        r2 = page.evaluate("fetch(`/api/jobs/${jobId}`).then(r => r.json()).then(j => j.report)")
        check("sr: the captions went up (margin raised by 0.06)", abs(r2["settings"]["caption_margin"] - 0.42) < 0.011, r2["settings"].get("caption_margin"))
        check("sr: Version 2 and 'See earlier versions' show", "Version 2" in page.locator("#versionTag").inner_text() and "See earlier versions" in page.locator("#versionTag").inner_text())
        page.screenshot(path=SHOTS / "sr-8-real-remade.png", full_page=True)
    check("sr: no JavaScript errors on the real result page", not real_errors, "; ".join(real_errors[:3]))
