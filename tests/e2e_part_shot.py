""""Your words and your clips" with "Change this shot" on the result page (web/index.html: #shotCard, #shotList).
Works on its own:

    E2E_PORT=8301 E2E_ONLY=shot venv/Scripts/python tests/e2e_site.py

Every /api/jobs/... answer is given by the test (page.route): the job, its clips, its phrase rows (/shots), a real
playable 20 s video for the jump and the thumbnails, and the feedback request (recorded, answered with a made-up next
job). No pipeline run starts and nothing is sent anywhere."""
import json
import subprocess
import tempfile
from pathlib import Path

J = {"content-type": "application/json"}


def run(page, ctx):
    import sys
    sys.path.insert(0, str(ctx.ROOT))
    from video_tool.config import FFMPEG
    BASE, check = ctx.BASE, ctx.check
    errors, feedback = [], []
    media = Path(tempfile.mkdtemp(prefix="e2e-shot-"))
    vid = media / "v.mp4"   # four 5 s colour blocks: what each clip "looks like"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=270x480:r=15:d=5", "-f", "lavfi",
                    "-i", "color=c=green:s=270x480:r=15:d=5", "-f", "lavfi", "-i", "color=c=blue:s=270x480:r=15:d=5",
                    "-f", "lavfi", "-i", "color=c=yellow:s=270x480:r=15:d=5", "-filter_complex", "[0][1][2][3]concat=n=4:v=1:a=0",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "15", str(vid)], capture_output=True)
    vbytes = vid.read_bytes()

    tl = [{"clip": "01.mp4", "start": 0.0, "end": 5.0}, {"clip": "02.mp4", "start": 5.0, "end": 10.0},
          {"clip": "03.mp4", "start": 10.0, "end": 15.0, "pinned": True}, {"clip": "04.mp4", "start": 15.0, "end": 20.0}]

    def report(mode="voice", extra=None):
        r = {"job_id": "sh1", "mode": mode, "length": 20.0, "music": None, "denoised": False, "copy": "ok",
             "parts": [{"file": "final.mp4", "length": 20.0, "size_mb": 1.0}], "settings": {"format": "auto"},
             "timeline": tl, "warnings": [], "not_shown": [], "uncertain_words": []}
        r.update(extra or {})
        return r

    rows = [{"start": 0.0, "end": 4.0, "text": "This is the front of the house.", "clip": 1, "slot_start": 0, "slot_end": 5, "match": "possible match", "shown_score": 8, "best_score": 9, "pinned": False},
            {"start": 4.0, "end": 9.0, "text": "Here is the big kitchen.", "clip": 2, "slot_start": 5, "slot_end": 10, "match": "not sure", "pinned": False},
            {"start": 9.0, "end": 14.0, "text": "The living room gets the light.", "clip": 3, "slot_start": 10, "slot_end": 15, "match": "no footage for this", "pinned": True},
            {"start": 14.0, "end": 20.0, "text": "Out back is the deck.", "clip": 4, "slot_start": 15, "slot_end": 20, "match": None, "pinned": False}]
    copy = {"titles": ["A title"], "hook": "Hi.", "description": "A description.", "cta": "Message me.", "format": "other",
            "format_label": "Video", "captions": {}, "checklist": [], "review_notes": []}
    tr = {"burned_in": True, "parts": ["Hello there."], "text": "Hello there."}
    clips4 = [{"n": i, "name": f"{nm}.mov"} for i, nm in enumerate(("Front", "Kitchen", "Living", "Deck"), 1)]
    jobs = {"sh1": (report(), clips4), "sh-talk": (report("own_audio"), clips4),
            "sh-one": (report(extra={"timeline": tl[:1]}), clips4[:1])}

    def serve_video(route):
        rng, n = route.request.headers.get("range"), len(vbytes)
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            a, b = int(a or 0), (min(int(b), n - 1) if b else n - 1)
            return route.fulfill(status=206, headers={"content-type": "video/mp4", "accept-ranges": "bytes",
                                                      "content-range": f"bytes {a}-{b}/{n}"}, body=vbytes[a:b + 1])
        return route.fulfill(status=200, headers={"content-type": "video/mp4", "accept-ranges": "bytes"}, body=vbytes)

    def handle(route):
        req = route.request
        m = req.url.split(BASE, 1)[1].split("?", 1)[0].split("/")   # ['', 'api', 'jobs', id, name]
        if len(m) < 4 or m[3] not in jobs and m[3] != "sh-next":
            return route.continue_()
        rep, cl = jobs.get(m[3], jobs["sh1"])
        if len(m) == 4 and req.method == "GET":
            return route.fulfill(status=200, headers=J, body=json.dumps({"status": "done", "report": rep, "copy": copy, "transcript": tr}))
        name = m[4]
        if name == "clips":
            return route.fulfill(status=200, headers=J, body=json.dumps({"clips": cl}))
        if name == "shots":
            return route.fulfill(status=200, headers=J, body=json.dumps({"rows": rows}))
        if name == "feedback":
            feedback.append(json.loads(req.post_data or "{}"))
            return route.fulfill(status=200, headers=J, body=json.dumps({"ok": True, "job_id": "sh-next", "applied": ["Clip 4 at 0:04"]}))
        if name.endswith(".mp4"):
            return serve_video(route)
        if name == "thumb.jpg":
            return route.fulfill(status=404, headers=J, body="{}")
        return route.fulfill(status=200, headers=J, body="{}")

    p = page.context.new_page()
    p.set_viewport_size({"width": 390, "height": 844})
    p.emulate_media(reduced_motion="reduce")
    p.on("pageerror", lambda e: errors.append(str(e)))
    p.route("**/api/jobs/**", handle)

    def open_job(job_id):
        p.goto(BASE + "/?job=" + job_id)
        p.wait_for_function("() => !document.getElementById('done').classList.contains('hidden')", timeout=20000)

    print("Change this shot", flush=True)
    open_job("sh1")
    p.wait_for_selector("#shotCard:not(.hidden)", timeout=10000)
    items = p.locator("#shotList > li")
    check("shot: one row per phrase", items.count() == 4, items.count())
    texts = p.locator("#shotList .said").all_inner_texts()
    check("shot: rows show her words", texts == [f'"{r["text"]}"' for r in rows], texts)
    mws = p.locator("#shotList .mw").all_inner_texts()
    check("shot: plain match words from the scores", "Possible match" in mws[0] and "Not sure" in mws[1] and "No footage for this" in mws[2], mws)
    check("shot: no scores -> no confidence word", not any(w in mws[3] for w in ("Possible match", "Not sure", "No footage")) and "Clip 4 (Deck.mov)" in mws[3], mws[3])
    check("shot: her earlier choice is marked", "your choice" in mws[2] and "your choice" not in mws[1], mws)
    check("shot: rows name the clip on screen", "Clip 2 (Kitchen.mov)" in mws[1], mws[1])
    p.wait_for_function("() => document.querySelectorAll('#shotList .shot img.cthumb').length === 4", timeout=20000)
    check("shot: every row gets the clip's small picture", p.locator("#shotList .shot img.cthumb").count() == 4)
    small = p.evaluate("[...document.querySelectorAll('#shotList .shot, #shotList .change')].map(b => b.getBoundingClientRect().height).filter(h => h < 44).length")
    check("shot: rows and Change buttons are at least 44 px tall", small == 0, small)

    check("phone: shot page fits 390px", p.evaluate("document.documentElement.scrollWidth <= innerWidth"))
    p.locator("#shotCard").scroll_into_view_if_needed()
    p.screenshot(path=ctx.SHOTS / "phone-shot-list.png")
    p.emulate_media(reduced_motion="reduce")
    check("phone: reduced motion disables result flash", p.evaluate("(() => { const e=document.createElement('div'); e.className='flash'; document.body.append(e); const a=getComputedStyle(e).animationName; e.remove(); return a === 'none'; })()"))
    check("phone: reduced motion removes decorative canvas", p.locator("#heroStage canvas").count() == 0)

    check("shot: conservative labels without raw score clutter", "AI score:" not in p.inner_text("#shotCard") and "not checked by a person" in p.inner_text("#shotCard"))
    check("shot: explains swap honestly", "choosing an already-used clip can swap two shots" in p.inner_text("#shotCard") and "Everything else stays the same" not in p.inner_text("#shotCard"))

    # the jump
    p.locator("#shotList .shot").nth(3).click()
    p.wait_for_function("() => Math.abs(document.querySelector('#players video').currentTime - 14) < 0.6", timeout=10000)
    check("shot: tapping a row jumps the video to that moment", True)
    p.evaluate("document.querySelector('#players video').pause()")
    p.locator("#shotList .shot").nth(1).click()
    p.wait_for_function("() => Math.abs(document.querySelector('#players video').currentTime - 4) < 0.6", timeout=10000)
    check("shot: ...and another row to its moment", True)
    p.evaluate("document.querySelector('#players video').pause()")

    # the picker
    row = items.nth(1)
    row.locator(".change").click()
    pick = row.locator(".picker button")
    names = pick.all_inner_texts()
    check("shot: the picker offers her OTHER clips", names == ["Clip 1 (Front.mov)", "Clip 3 (Living.mov)", "Clip 4 (Deck.mov)"], names)
    check("shot: the picker uses the clip pictures", row.locator(".picker img.cthumb").count() == 3)
    check("shot: Change says it is open", row.locator(".change").get_attribute("aria-expanded") == "true")
    check("shot: nothing is sent before she picks", p.is_disabled("#shotList li:nth-child(2) .go") and not feedback)
    tall = p.evaluate("[...document.querySelectorAll('#shotList .picker button, #shotList .go, #shotList .cancel')].every(b => b.getBoundingClientRect().height >= 44)")
    check("shot: picker buttons are at least 44 px", tall)
    focus_ring = p.evaluate("(() => { const b = document.querySelector('#shotList .picker button'); return document.activeElement === b; })()")
    check("shot: the first clip choice gets the keyboard focus", focus_ring)
    p.keyboard.press("Tab")
    ring = p.evaluate("getComputedStyle(document.activeElement).outlineStyle")
    check("shot: keyboard focus is visible", ring not in ("none", ""), ring)
    p.screenshot(path=ctx.SHOTS / "phone-shot-picker-focus.png")
    check("phone: picker fits 390px", p.evaluate("document.documentElement.scrollWidth <= innerWidth"))
    row.locator(".cancel").click()
    check("shot: Cancel returns focus to Change this shot", row.locator(".change").evaluate("e => e === document.activeElement"))
    check("shot: Cancel closes the picker", row.locator(".picker").count() == 0 and row.locator(".change").get_attribute("aria-expanded") == "false")
    row.locator(".change").click()
    pick = row.locator(".picker button")
    pick.nth(2).click()
    check("shot: tapping a clip selects only it", pick.evaluate_all("bs => bs.map(b => b.getAttribute('aria-pressed'))") == ["false", "false", "true"])
    row.locator(".go").click()
    p.wait_for_function("() => !document.getElementById('working').classList.contains('hidden') || document.querySelector('#versionTag .version')", timeout=15000)
    body = feedback[-1] if feedback else {}
    check("shot: the remake asks for that clip at that phrase, nothing else",
          body.get("changes") == {"pins": [{"start": 4.0, "end": 9.0, "clip": 4}]} and body.get("remake") is True
          and body.get("remember") is False and not body.get("comment"), body)

    # who sees it
    open_job("sh-talk")
    p.wait_for_timeout(800)
    check("shot: hidden for a talking-to-camera video", p.locator("#shotCard").is_hidden())
    open_job("sh-one")
    p.wait_for_timeout(800)
    check("shot: hidden when there is only one clip", p.locator("#shotCard").is_hidden())

    # Phase 3: clip labels in shot rows, picker, and reorder/remake UI
    clips_labeled = [
        {"n": 1, "name": "Front.mov", "label": "front porch"},
        {"n": 2, "name": "Kitchen.mov", "label": "kitchen <script>alert(1)</script>"},
        {"n": 3, "name": "Living.mov", "label": ""},
        {"n": 4, "name": "Deck.mov", "label": "back deck"},
    ]
    rows_labeled = [
        {**rows[0], "label": "front porch"},
        {**rows[1], "label": "kitchen <script>alert(1)</script>"},
        {**rows[2], "label": ""},
        {**rows[3], "label": "back deck"},
    ]
    tl_labeled = [
        {**tl[0], "label": "front porch"},
        {**tl[1], "label": "kitchen <script>alert(1)</script>"},
        {**tl[2]},
        {**tl[3], "label": "back deck"},
    ]
    jobs["sh-lbl"] = (report(extra={"timeline": tl_labeled}), clips_labeled)
    orig_rows = list(rows)
    rows[:] = rows_labeled
    open_job("sh-lbl")
    p.wait_for_selector("#shotCard:not(.hidden)", timeout=10000)
    mws_lbl = p.locator("#shotList .mw").all_inner_texts()
    check("shot: clip label shown in shot row and escaped safely", "front porch" in mws_lbl[0] and "<script>" in mws_lbl[1], mws_lbl[:2])
    p.locator("#didBox summary").click()
    did_txt = p.locator("#didList").inner_text()
    check("shot: clip label shown in Which clip is where", "front porch" in did_txt and "back deck" in did_txt, did_txt)
    p.locator("#shotList > li").nth(2).locator(".change").click()
    pick_lbl = p.locator("#shotList > li").nth(2).locator(".picker button .pname").all_text_contents()
    check("shot: picker buttons include clip labels", any("front porch" in x for x in pick_lbl) and any("back deck" in x for x in pick_lbl), pick_lbl)
    # Check reorderList label input in Not quite right?
    p.locator("#fbOpen").click()
    p.locator("#moreOpts summary").click()
    lbl_inputs = p.locator("#reorderList input.clip-label")
    check("shot: reorder list has editable clip label inputs", lbl_inputs.count() == 4 and lbl_inputs.nth(0).input_value() == "front porch")
    lbl_inputs.nth(2).fill("sunny living room")
    ch_val = p.evaluate("changes()")
    check("shot: editing a clip label in Remake populates changes().clip_labels",
          ch_val.get("clip_labels", {}).get("3") == "sunny living room", ch_val)
    rows[:] = orig_rows

    # Upload screen: clipLabels WeakMap follows exact File across arrow moves and deletions
    p.goto(BASE + "/")
    p.wait_for_selector("#drop")
    c_files = []
    for nm in ("a.mp4", "b.mp4", "c.mp4"):
        fp = media / nm
        fp.write_bytes(vbytes)
        c_files.append(str(fp))
    p.set_input_files("#files", c_files)
    up_inputs = p.locator("#clips input.clip-label")
    check("upload: each clip row has a 44px+ clip-label input",
          up_inputs.count() == 3 and p.evaluate("[...document.querySelectorAll('#clips input.clip-label')].every(i => i.getBoundingClientRect().height >= 44)"))
    up_inputs.nth(0).fill("first room")
    up_inputs.nth(1).fill("second room")
    up_inputs.nth(2).fill("third room")
    # Move clip 1 down -> order becomes [b.mp4, a.mp4, c.mp4]
    p.locator("#clips li").nth(0).locator("button[aria-label='Move clip 1 down']").click()
    vals_after_move = [up_inputs.nth(i).input_value() for i in range(3)]
    check("upload: moving a clip with arrows keeps each label attached to its File",
          vals_after_move == ["second room", "first room", "third room"], vals_after_move)
    # Remove the middle clip (a.mp4) -> remaining are [b.mp4, c.mp4]
    p.locator("#clips li").nth(1).locator("button[aria-label='Remove clip 2']").click()
    vals_after_del = [up_inputs.nth(i).input_value() for i in range(2)]
    check("upload: deleting a clip keeps remaining clip labels attached to their Files",
          vals_after_del == ["second room", "third room"], vals_after_del)

    check("shot: no JavaScript errors", not errors, "; ".join(errors[:3]))
    p.close()
