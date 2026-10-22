"""The two "not enough footage" panels on the result page, drawn from fixtures (page.route), phone-sized.

    E2E_PORT=8034 E2E_ONLY=short_footage venv/Scripts/python tests/e2e_site.py

  1. "Your footage is shorter than your words": rep.short_footage rows, tap to seek, button back to the upload page;
  2. "You filmed more than you talked about": a big cut_from_end with a short voice-over, button back to the upload page;
  3. neither panel when there is nothing to say (and not on the example view).
"""
import json
import subprocess
import sys

J = {"content-type": "application/json"}
PHONE = {"width": 430, "height": 900}


def run(page, ctx):
    BASE, SHOTS, ROOT = ctx.BASE, ctx.SHOTS, ctx.ROOT
    check = ctx.check
    sys.path.insert(0, str(ROOT))
    from video_tool.config import FFMPEG
    media = ctx.DATA / "media-sf"
    media.mkdir(exist_ok=True)
    tiny = media / "tiny.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=270x480:rate=15:duration=40",
                    "-f", "lavfi", "-i", "sine=frequency=330:duration=40", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(tiny)], capture_output=True)
    data = tiny.read_bytes()
    jpg = media / "poster.jpg"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(tiny), "-frames:v", "1", str(jpg)], capture_output=True)

    errors = []
    p = page.context.new_page()
    p.set_viewport_size(PHONE)
    p.on("pageerror", lambda e: errors.append(str(e)))

    base_report = {"mode": "voice", "length": 31.2, "copy": "ok", "parts": [{"file": "final.mp4", "length": 31.2, "size_mb": 12}],
                   "settings": {}, "timeline": [], "warnings": [], "not_shown": [], "uncertain_words": [], "moved": [],
                   "clips_total": 40.0, "voice": 31.2, "cut_from_end": 0, "short_by": 0, "skipped_clips": []}
    copy = {"titles": ["A", "B", "C"], "hook": "Hook", "description": "d", "cta": "c", "checklist": [],
            "captions": {"youtube_shorts": "y #Shorts", "tiktok": "t", "instagram_reels": "i", "facebook_reels": "f"}}
    tr = {"burned_in": True, "parts": ["Come inside."], "text": "Come inside."}
    short = [{"at": 24.1, "said": "and a great view of the backyard. I love the large deck", "clip": "Clip 10 (Deck.mov)",
              "need": 7.9, "have": 5.0, "add": 3}]
    jobs = {
        "sf1": {"status": "done", "report": {**base_report, "short_footage": short}, "copy": copy, "transcript": tr},
        "sf2": {"status": "done", "report": {**base_report, "clips_total": 116.6, "voice": 31.2, "cut_from_end": 85.0}, "copy": copy, "transcript": tr},
        "sf3": {"status": "done", "report": {**base_report, "short_footage": [], "cut_from_end": 3.0}, "copy": copy, "transcript": tr},
        # a long voice-over: asking for a longer one would break the one-minute limit, so no panel
        "sf4": {"status": "done", "report": {**base_report, "clips_total": 200.0, "voice": 55.0, "cut_from_end": 145.0}, "copy": copy, "transcript": tr},
        # a talking video is not voice-over mode: no "longer voice memo" advice
        "sf5": {"status": "done", "report": {**base_report, "mode": "own_audio", "clips_total": 116.6, "voice": 31.2, "cut_from_end": 85.0}, "copy": copy, "transcript": tr},
    }

    def handle(route):
        req = route.request
        path = req.url.split(BASE, 1)[1].split("?", 1)[0]
        m = path.split("/")
        if path.startswith("/api/jobs/") and len(m) == 4 and req.method == "GET":
            return route.fulfill(status=200, headers=J, body=json.dumps(jobs[m[3]])) if m[3] in jobs else route.continue_()
        if len(m) == 5 and m[1] == "api" and m[2] == "jobs" and m[4].endswith(".mp4"):
            return route.fulfill(status=200, headers={"content-type": "video/mp4", "accept-ranges": "bytes"}, body=data)
        if len(m) == 5 and m[4] == "thumb.jpg":
            return route.fulfill(status=200, headers={"content-type": "image/jpeg"}, body=jpg.read_bytes())
        if len(m) == 5 and m[4] in ("clips", "posting", "event"):
            return route.fulfill(status=200, headers=J, body=json.dumps({"clips": [], "status": "draft", "posted": [], "date": None, "ok": True}))
        return route.continue_()

    p.route("**/api/**", handle)

    def open_job(job_id):
        p.goto(BASE + "/?job=" + job_id)
        p.wait_for_function("() => !document.getElementById('done').classList.contains('hidden')", timeout=20000)
        p.wait_for_selector("#players video")

    def panel_titles():
        return [t.strip() for t in p.locator("#panels .heads h2").all_inner_texts()]

    print("short_footage 1: footage shorter than her words", flush=True)
    open_job("sf1")
    titles = panel_titles()
    check("panel: 'Your footage is shorter than your words' shows", "Your footage is shorter than your words" in titles, titles)
    row = p.locator("#panels .heads .seek").first
    txt = row.inner_text()
    check("row: says how long she talks and how long the clip is", "7s" in txt or "8s" in txt, txt)
    check("row: names the clip as she named it, once, and how much more to film", "Deck.mov" in txt and "((" not in txt and "3 more seconds" in txt, txt)
    check("row: starts at the right moment", row.inner_text().startswith("0:24"), txt)
    check("row: no developer words or file paths", "/" not in txt.replace("Clip 10 (Deck.mov)", "") and "json" not in txt.lower(), txt)
    p.screenshot(path=SHOTS / "sf-1-short-footage.png", full_page=True)
    p.get_by_role("button", name="Add more footage and make it again").click()
    p.wait_for_function("() => !document.getElementById('pick').classList.contains('hidden')", timeout=10000)
    note = p.locator("#pickNote").inner_text()
    check("button: back to the upload page with a plain note", "extra footage" in note and "Make my video" in note, note)

    print("short_footage 2: filmed more than she talked about", flush=True)
    open_job("sf2")
    titles = panel_titles()
    check("panel: 'You filmed more than you talked about' shows", "You filmed more than you talked about" in titles, titles)
    body = p.locator("#panels .heads").filter(has_text="You filmed more than you talked about").inner_text()
    check("panel: gives her numbers in minutes and seconds", "1:57" in body and "0:31" in body, body)
    check("panel: says to keep it under a minute", "under a minute" in body, body)
    p.screenshot(path=SHOTS / "sf-2-more-voice.png", full_page=True)
    p.get_by_role("button", name="Add a longer voice memo").click()
    p.wait_for_function("() => !document.getElementById('pick').classList.contains('hidden')", timeout=10000)
    note = p.locator("#pickNote").inner_text()
    check("button: back to the upload page and asks for the longer voice memo", "longer voice memo" in note and "Make my video" in note, note)

    print("short_footage 3: nothing to say", flush=True)
    for jid, why in (("sf3", "small leftover"), ("sf4", "voice-over already near a minute"), ("sf5", "a talking video")):
        open_job(jid)
        titles = panel_titles()
        check(f"no footage panels for {why}", "Your footage is shorter than your words" not in titles
              and "You filmed more than you talked about" not in titles, titles)

    check("no JavaScript errors", not errors, "; ".join(errors[:3]))
    p.close()
