"""Upload-side screens (web/index.html: header, upload page, clip list, voice-over, recorder, options, working and failure
screens, install hint, the example video) checked in a real browser, phone-sized. Works on its own:

    E2E_PORT=8031 E2E_ONLY=screens_upload venv/Scripts/python tests/e2e_site.py

One real video is made (about a minute) with a voice-over RECORDED in the browser (Chrome's fake microphone plays the
real recipe voice), a names box, a video kind and 'keep my pictures on this computer', so every new control is checked
all the way into the finished video's report. Screenshots (430x900) go to tests/shots/su-*.png.
"""
import json
import secrets
import shutil
import subprocess
import time
from pathlib import Path

J = {"content-type": "application/json"}
PHONE = {"width": 430, "height": 900}
IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
             "Version/17.4 Mobile/15E148 Safari/604.1")


import re  # noqa: E402


def _alpha(color):
    """Opacity of a computed 'rgba(r, g, b, a)' colour (1 for 'rgb(...)'): a finishing CSS transition reports tiny non-zero values."""
    m = re.search(r",\s*([0-9.]+)\)\s*$", color)
    return float(m.group(1)) if m else 1.0


def run(page, ctx):
    out = ctx.DATA / "out"
    try:
        _run(page, ctx)
    finally:   # leave the shared throwaway site as found: other parts read every copy.json they can see
        for f in (out / "demo-tour-moved").glob("*"):   # remove files one by one: never walk into the original
            f.unlink(missing_ok=True)
        (out / "demo-tour-moved").rmdir() if (out / "demo-tour-moved").exists() else None
        (out / "_demo.txt").unlink(missing_ok=True)


def _run(page, ctx):
    BASE, SHOTS, SAMPLE, ROOT = ctx.BASE, ctx.SHOTS, ctx.SAMPLE, ctx.ROOT
    check = ctx.check
    out = ctx.DATA / "out"
    import sys
    sys.path.insert(0, str(ROOT))
    from video_tool.config import FFMPEG
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    shot = lambda p, name, full=False: p.screenshot(path=SHOTS / f"su-{name}.png", full_page=full)
    media = ctx.DATA / "media-su"
    media.mkdir(exist_ok=True)

    def sh(*args):
        return subprocess.run([FFMPEG, "-y", "-v", "error", *args], capture_output=True).returncode == 0

    clips = sorted(SAMPLE.glob("*.mp4"))
    voice = SAMPLE / "voice.m4a"

    # ---------------------------------------------------------------- 1. first look
    print("screens-upload 1: the first screen", flush=True)
    page.goto(BASE)
    page.evaluate("localStorage.clear()")
    page.goto(BASE)
    page.wait_for_selector("#drop")
    check("su: drop title on a phone says 'Add your clips'", page.locator("#dropTitle .phone-only").is_visible()
          and page.locator("#dropTitle .wide-only").is_hidden() and "Add your clips" in page.locator("#dropTitle").inner_text())
    check("su: the phone hint explains voice memos",
          "voice memo" in page.locator("#drop .hint .phone-only").inner_text())
    page.set_viewport_size({"width": 1000, "height": 800})
    check("su: on a wide screen it says 'Drop your files here'", page.locator("#dropTitle .wide-only").is_visible()
          and "Drop your files here" in page.locator("#dropTitle").inner_text())
    page.set_viewport_size(PHONE)
    tips = page.locator("#tips li").first
    check("su: tips are plain sentences, not pills",
          page.evaluate("(e) => { const s = getComputedStyle(e); return s.borderTopWidth === '0px' && s.borderRadius === '0px'; }", tips.element_handle()))
    check("su: no 'Admin' or user name jumps in late: nav is there from the start with room reserved",
          page.locator("#nav").bounding_box()["height"] >= 52)
    ok_nav = page.evaluate("""() => [...document.querySelectorAll('#nav a:not(.hidden), #nav button#acctBtn')]
        .filter(e => e.offsetParent).map(e => [e.textContent.trim(), Math.round(e.getBoundingClientRect().height)])""")
    check("su: nav targets are at least 44 px tall", ok_nav and all(h >= 44 for _, h in ok_nav), ok_nav)
    page.wait_for_function("document.getElementById('navUser').textContent === 'tester'")
    check("su: nav has 'Make a video' and 'My videos'", page.locator("#navNew").inner_text() == "Make a video"
          and page.locator("#navVideos").inner_text() == "My videos")
    page.click("#acctBtn")
    check("su: the account menu shows Sign out, and the Team page for an admin",
          page.locator("#signout").is_visible() and page.locator("#adminLink").is_visible())
    shot(page, "01-account-menu")
    page.click("h1")
    check("su: clicking elsewhere closes the menu", page.locator("#acctMenu").is_hidden())
    check("su: the page has the phone-app tags", page.get_attribute('link[rel="manifest"]', "href") == "/manifest.webmanifest"
          and page.get_attribute('meta[name="theme-color"]', "content") == "#2c76ae")
    page.wait_for_function("navigator.serviceWorker.getRegistration().then(r => !!r)", timeout=15000)
    check("su: the home-screen worker registered", True)
    check("su: the example link is hidden when there is no example", page.locator("#exampleWrap").is_hidden())
    page.wait_for_function("document.getElementById('kindCard') && !document.getElementById('kindCard').classList.contains('hidden')")
    check("su: video kinds come from the site", page.locator("#kind option").count() == 6
          and "House tour" in page.locator("#kind").inner_text(), page.locator("#kind").inner_text().replace("\n", " | "))
    shot(page, "02-empty", full=True)

    # a second, non-admin person never sees Admin
    pw2 = secrets.token_urlsafe(10)
    created = page.request.post(f"{BASE}/api/admin/users", data=json.dumps({"username": "su2", "password": pw2}), headers=J)
    c2 = page.context.browser.new_context(viewport=PHONE)
    c2.request.post(f"{BASE}/api/login", data=json.dumps({"username": "su2", "password": pw2}), headers=J)
    p2 = c2.new_page()
    p2.goto(BASE)
    p2.wait_for_function("document.getElementById('navUser').textContent === 'su2'")
    p2.click("#acctBtn")
    check("su: a non-admin sees no Admin link", created.ok and p2.locator("#adminLink").is_hidden() and p2.locator("#signout").is_visible())
    c2.close()

    # ---------------------------------------------------------------- 2. picking files
    print("screens-upload 2: clips, voice-over, order", flush=True)
    page.set_input_files("#files", [str(f) for f in clips] + [str(voice)])
    page.wait_for_selector("#clips li.clip")
    check("su: clip rows read 'Clip N' with the file name small underneath",
          page.locator("#clips li.clip .who b").all_inner_texts() == ["Clip 1", "Clip 2", "Clip 3"]
          and page.locator("#clips li.clip .fname").all_inner_texts() == ["01.mp4", "02.mp4", "03.mp4"])
    sizes = page.evaluate("[...document.querySelectorAll('#clips .icon-btn')].map(b => { const r = b.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; })")
    check("su: every arrow and remove button is at least 44 x 44", sizes and all(w >= 44 and h >= 44 for w, h in sizes), sizes[:3])
    check("su: the how-to-order hint is shown", "arrows" in page.locator("#orderHelp").inner_text())
    page.wait_for_function("document.querySelectorAll('#clips img.cthumb').length === 3", timeout=20000)
    check("su: thumbnails appear", True)
    page.wait_for_function("document.getElementById('lenLine') && /voice-over/.test(document.getElementById('lenLine').textContent)", timeout=15000)
    line = page.locator("#lenLine").inner_text()
    check("su: clips-versus-voice line is shown ('Clips N s, voice-over N s')", line.startswith("Clips ") and "voice-over" in line, line)
    vtxt = page.locator("#voice").inner_text()
    check("su: the voice-over's length is on its row before uploading", ":" in vtxt and "voice.m4a" in vtxt, vtxt.replace("\n", " | "))
    check("su: order hint about moving a clip is shown with a voice-over", "may move it" in page.locator("#moveLine").inner_text())
    page.check("#private")
    check("su: ...and changes when pictures stay private", "stay in the order shown" in page.locator("#moveLine").inner_text())
    page.uncheck("#private")
    check("su: the one-line nudge sits above the button", "Tap Make my video" in page.locator("#readyLine").inner_text())
    st = page.evaluate("""() => { const d = getComputedStyle(document.getElementById('drop')), g = getComputedStyle(document.getElementById('go'));
        return [d.borderTopStyle, d.backgroundColor, g.backgroundColor, document.getElementById('go').getBoundingClientRect().height]; }""")
    check("su: 'add more' is a light outlined button (not dashed) and Make my video is the strong one",
          st[0] == "solid" and (st[1] == "transparent" or _alpha(st[1]) < 0.05) and st[2] != "rgba(0, 0, 0, 0)" and st[3] >= 44, st)   # fully transparent, whatever colour a finishing transition reports
    check("su: the page is not greyed by a fade (opacity-only, short)", page.evaluate("getComputedStyle(document.getElementById('pick')).animationDuration") in ("0.12s", "0.1s", "0.15s"))
    shot(page, "03-files-chosen", full=True)

    # no voice-over wording, no browser pop-up
    page.click("#rmVoice")
    check("su: no voice-over reads plainly", "No voice-over. We'll use the sound recorded in your clips." in page.locator("#voice").inner_text())
    check("su: the line about clip order hides without a voice-over", page.locator("#moveLine").is_hidden())
    page.wait_for_function("/in total/.test(document.getElementById('lenLine').textContent)")
    check("su: with no voice the line gives the clips' total", "in total" in page.locator("#lenLine").inner_text())
    shot(page, "04-no-voice", full=True)

    # Start over asks first
    page.click("#reset")
    check("su: Start over asks 'Remove all your clips?'", page.locator("#resetAsk").is_visible() and "Remove all your clips?" in page.locator("#resetAsk").inner_text())
    page.click("#resetNo")
    check("su: Cancel keeps the clips", page.locator("#clips li.clip").count() == 3 and page.locator("#resetAsk").is_hidden())
    page.click("#reset")
    shot(page, "05-start-over-asks")
    page.click("#resetYes")
    check("su: Remove empties the list", page.locator("#list").is_hidden() and page.locator("#clips li.clip").count() == 0)

    # moving, removing
    page.set_input_files("#files", [str(f) for f in clips])
    page.click('#clips li.clip:nth-child(1) button[aria-label="Move clip 1 down"]')
    check("su: the down arrow moves a clip", page.locator("#clips li.clip .fname").all_inner_texts() == ["02.mp4", "01.mp4", "03.mp4"])
    page.click('#clips li.clip:nth-child(3) button[aria-label="Remove clip 3"]')
    check("su: remove takes one clip out", page.locator("#clips li.clip").count() == 2)
    page.evaluate("clearAll()")

    # lastModified ordering, as the page's own function sees it
    res = page.evaluate("""() => {
      const mk = (n, t) => new File(["x"], n, { lastModified: t }), base = Date.UTC(2026, 5, 1), out = {};
      notes = []; out.timed = sortFresh([mk("IMG_2.MOV", base + 120000), mk("IMG_1.MOV", base + 240000), mk("IMG_3.MOV", base)]).map(f => f.name); out.note = notes.join(" ");
      notes = []; out.close = sortFresh([mk("b.mp4", base + 2000), mk("a.mp4", base + 5000)]).map(f => f.name); out.closeNotes = notes.length;
      notes = []; out.same = sortFresh([mk("b.mp4", base), mk("a.mp4", base)]).map(f => f.name);
      notes = []; out.epoch = sortFresh([mk("b.mp4", 0), mk("a.mp4", 600000)]).map(f => f.name);
      notes = []; out.agree = sortFresh([mk("IMG_1.MOV", base), mk("IMG_2.MOV", base + 90000)]).map(f => f.name); out.agreeNotes = notes.length;
      notes = []; out.one = sortFresh([mk("only.mp4", base)]).map(f => f.name); notes = [];
      return out; }""")
    check("su: clips go in the order they were filmed when the dates are clearly spread out",
          res["timed"] == ["IMG_3.MOV", "IMG_2.MOV", "IMG_1.MOV"] and "order you filmed them" in res["note"], res)
    check("su: dates only seconds apart fall back to the file names", res["close"] == ["a.mp4", "b.mp4"] and res["closeNotes"] == 0, res["close"])
    check("su: identical or missing dates fall back to the file names", res["same"] == ["a.mp4", "b.mp4"] and res["epoch"] == ["a.mp4", "b.mp4"], res)
    check("su: when dates and names agree there is nothing to explain", res["agree"] == ["IMG_1.MOV", "IMG_2.MOV"] and res["agreeNotes"] == 0 and res["one"] == ["only.mp4"])

    # a dropped folder is opened
    page.evaluate("""() => {
      const f = n => new File(["abc"], n, { type: "video/mp4" });
      const fe = n => ({ isFile: true, isDirectory: false, file: ok => ok(f(n)) });
      const de = kids => ({ isFile: false, isDirectory: true, createReader: () => { let d = false; return { readEntries: ok => { if (d) ok([]); else { d = true; ok(kids); } } }; } });
      const ev = new Event("drop", { cancelable: true });
      ev.dataTransfer = { items: [{ webkitGetAsEntry: () => de([fe("b.mp4"), fe(".DS_Store"), fe("a.mp4"), de([fe("c.mov")])]) }], files: [] };
      document.getElementById("drop").dispatchEvent(ev); }""")
    page.wait_for_function("document.querySelectorAll('#clips li.clip').length === 3")
    check("su: a dropped folder gives its videos (hidden files left out)", page.locator("#clips li.clip .fname").all_inner_texts() == ["a.mp4", "b.mp4", "c.mov"])
    page.evaluate("clearAll()")
    page.set_input_files("#files", [str(SAMPLE / "01.mp4")])
    page.evaluate("""() => { const f = new File(["abc"], "note.zip"); addFiles([f]); }""")
    check("su: a zip is explained, not silently ignored", "zip file" in page.locator("#pickNote").inner_text())
    page.evaluate("clearAll()")

    # ---------------------------------------------------------------- 3. warnings about length
    print("screens-upload 3: length warnings", flush=True)
    long_voice = media / "long.m4a"
    short_voice = media / "tiny.m4a"
    sh("-f", "lavfi", "-i", "sine=f=300:d=70", "-c:a", "aac", str(long_voice))
    page.set_input_files("#files", [str(SAMPLE / "03.mp4")])
    page.set_input_files("#voiceFile", [str(long_voice)])
    page.wait_for_function("/60 seconds at most/.test(document.getElementById('voiceLine').textContent)", timeout=15000)
    check("su: a voice-over over a minute gets a plain warning before uploading", "70 s" in page.locator("#voiceLine").inner_text(), page.locator("#voiceLine").inner_text())
    page.wait_for_function("/shorter than your voice-over/.test(document.getElementById('lenLine').textContent)", timeout=15000)
    check("su: clips shorter than the voice-over are called out", "Add another clip" in page.locator("#lenLine").inner_text(), page.locator("#lenLine").inner_text())
    shot(page, "06-warnings", full=True)
    page.evaluate("clearAll()")
    sh("-f", "lavfi", "-i", "sine=f=300:d=2", "-c:a", "aac", str(short_voice))
    page.set_input_files("#files", [str(SAMPLE / "01.mp4"), str(SAMPLE / "02.mp4"), str(SAMPLE / "03.mp4")])
    page.set_input_files("#voiceFile", [str(short_voice)])
    page.wait_for_function("/a lot more footage/.test(document.getElementById('lenLine').textContent)", timeout=15000)
    check("su: far more footage than voice-over is mentioned", True, page.locator("#lenLine").inner_text())
    page.evaluate("clearAll()")
    page.evaluate("""() => { for (let i = 0; i < 21; i++) clips.push(new File(["x"], `c${i}.mp4`)); render(); }""")
    check("su: a very long list of clips gets a calm heads-up", "21 clips" in page.locator("#lenLine").inner_text())
    page.evaluate("clearAll()")

    # ---------------------------------------------------------------- 4. kind of video, film list, ideas
    print("screens-upload 4: kind of video, film list, ideas", flush=True)
    page.select_option("#kind", "recipe")
    page.wait_for_selector("#filmList input")
    n_items = page.locator("#filmList input").count()
    check("su: choosing a kind shows its 'What should I film?' list", page.locator("#film").is_visible() and 4 <= n_items <= 9, n_items)
    page.locator("#filmList label").nth(0).click()
    page.locator("#filmList label").nth(2).click()
    stored = page.evaluate("JSON.parse(localStorage.getItem('film:recipe'))")
    check("su: ticks are kept in the browser, per day", stored["t"] == [0, 2] and len(stored["d"]) == 10, stored)
    shot(page, "07-film-list", full=True)
    page.reload()
    page.wait_for_function("document.querySelectorAll('#kind option').length === 6")
    page.select_option("#kind", "recipe")
    check("su: the ticks are still there after a reload",
          page.evaluate("[...document.querySelectorAll('#filmList input')].map(i => i.checked)")[:3] == [True, False, True])
    page.evaluate("localStorage.setItem('film:recipe', JSON.stringify({d: '2001-01-01', t: [0, 1]}))")
    page.select_option("#kind", "auto")
    page.select_option("#kind", "recipe")
    check("su: yesterday's ticks start clean", not any(page.evaluate("[...document.querySelectorAll('#filmList input')].map(i => i.checked)")))
    page.select_option("#kind", "auto")
    check("su: 'Let the Video Maker decide' hides the list", page.locator("#film").is_hidden())

    # browser memory blocked: the page still works
    p3 = page.context.new_page()
    errs3 = []
    p3.on("pageerror", lambda e: errs3.append(str(e)))
    p3.add_init_script("""Storage.prototype.getItem = () => { throw new Error('blocked'); }; Storage.prototype.setItem = () => { throw new Error('blocked'); };
                          Storage.prototype.removeItem = () => { throw new Error('blocked'); };""")
    p3.goto(BASE)
    p3.wait_for_function("document.querySelectorAll('#kind option').length === 6")
    p3.select_option("#kind", "house_tour")
    p3.wait_for_selector("#filmList input")
    p3.locator("#filmList label").first.click()
    check("su: with browser memory blocked the page and film list still work", not errs3 and p3.locator("#drop").is_visible(), errs3[:2])
    p3.close()

    # ideas: loading, success, failure
    page.reload()
    page.wait_for_function("document.querySelectorAll('#kind option').length === 6")
    page.route("**/api/ideas*", lambda r: (time.sleep(1.2), r.continue_()))
    page.click("#ideasBtn")
    check("su: ideas shows a loading state", page.locator("#ideasBtn").is_disabled() and "Thinking" in page.locator("#ideasMsg").inner_text())
    page.wait_for_selector("#ideas .idea", timeout=30000)
    page.unroute("**/api/ideas*")
    check("su: ideas arrive with a title and a filming list", page.locator("#ideas .idea").count() >= 3
          and page.locator("#ideas .idea ul li").count() >= 6 and page.locator("#ideasBtn").is_enabled(), page.locator("#ideas .idea b").first.inner_text())
    shot(page, "08-ideas", full=True)
    page.locator("#ideas .idea button").first.click()
    check("su: 'Use this idea' picks its kind of video", page.locator("#kind").input_value() != "auto" and page.locator("#film").is_visible())
    page.route("**/api/ideas*", lambda r: r.fulfill(status=500, body="{}"))
    page.click("#ideasBtn")
    page.wait_for_function("/couldn't come up with ideas/.test(document.getElementById('ideasMsg').textContent)")
    check("su: a failed ideas request says so calmly and can be tried again", page.locator("#ideasBtn").is_enabled())
    shot(page, "09-ideas-failed")
    page.unroute("**/api/ideas*")

    # ---------------------------------------------------------------- 5. words box + privacy sentence
    page.reload()
    page.set_input_files("#files", [str(SAMPLE / "01.mp4")])
    check("su: names box is limited to 400 characters and says what it is for",
          page.get_attribute("#hints", "maxlength") == "400" and "names or places" in page.locator("#list").inner_text().lower())
    priv = page.locator("#private").locator("xpath=..").inner_text()
    check("su: the privacy box says plainly what is sent to Claude and why",
          "Don't share any pictures from my clips" in priv and "AI helper" in priv and "match your clips to your words" in priv and "Claude" not in priv and "computer" not in priv, priv.replace("\n", " "))
    page.evaluate("clearAll()")

    # ---------------------------------------------------------------- 6. failure screen + working screen text
    print("screens-upload 5: failure and polling", flush=True)
    page.set_input_files("#files", [str(SAMPLE / "03.mp4"), str(SAMPLE / "02.mp4")])
    page.evaluate("fail('This clip won\\'t open: Clip 2 (Kitchen walk-through.mp4). It may be damaged.', 'web-x')")
    check("su: failure keeps help visible and the buttons are clearly different",
          "Need help?" in page.locator("#helpLine").inner_text() and page.locator("#helpLine").is_visible()
          and page.locator("#again").inner_text() == "Make it again with the same clips" and page.locator("#retry").inner_text() == "Back to my clips")
    shot(page, "10-failed")
    page.click("#retry")
    check("su: 'Back to my clips' returns to the clips", page.locator("#pick").is_visible() and page.locator("#clips li.clip").count() == 2)
    # a lost video stops the spinner after a few answers
    t0 = time.time()
    page.route("**/api/jobs/web-gone*", lambda r: r.fulfill(status=404, content_type="application/json", body='{"detail":"Job not found."}'))
    page.evaluate("show('working'); poll('web-gone')")
    page.wait_for_function("!document.getElementById('failed').classList.contains('hidden')", timeout=30000)
    took = time.time() - t0
    check("su: a video the site no longer knows ends in a plain message", "We can't find that video any more" in page.locator("#failMsg").inner_text(), page.locator("#failMsg").inner_text())
    check("su: ...after a few tries, not on the first miss and not forever", 3 < took < 20, f"{took:.1f}s")
    page.unroute("**/api/jobs/web-gone*")
    # a busy site (500) is not a lost video
    hits = {"n": 0}

    def flaky(route):
        hits["n"] += 1
        if hits["n"] <= 2:
            return route.fulfill(status=502, body="bad gateway")
        route.fulfill(status=200, content_type="application/json", body='{"status":"failed","error":"Clip 1 (a.mp4) can\'t be read.","can_retry":false}')
    page.route("**/api/jobs/web-flaky*", flaky)
    page.evaluate("show('working'); poll('web-flaky')")
    page.wait_for_function("!document.getElementById('failed').classList.contains('hidden')", timeout=30000)
    check("su: a busy site just means waiting, then the real answer shows", "can't be read" in page.locator("#failMsg").inner_text() and hits["n"] >= 3, hits)
    page.unroute("**/api/jobs/web-flaky*")
    # upload cannot reach the site
    page.evaluate("show('pick')")
    page.route("**/api/uploads", lambda r: r.abort())
    page.click("#go")
    page.wait_for_function("!document.getElementById('failed').classList.contains('hidden')", timeout=30000)
    msg = page.locator("#failMsg").inner_text()
    check("su: when the computer isn't answering the message says so and where help is",
          "isn't answering" in msg and "Need help?" in msg and "C:\\" not in msg, msg.replace("\n", " | "))
    page.unroute("**/api/uploads")
    page.evaluate("clearAll()")

    # ---------------------------------------------------------------- 7. install hint on a phone
    print("screens-upload 6: install hint", flush=True)
    c3 = page.context.browser.new_context(viewport=PHONE, user_agent=IPHONE_UA, is_mobile=True, has_touch=True)
    c3.request.post(f"{BASE}/api/login", data=json.dumps({"username": "tester", "password": ctx.PASSWORD}), headers=J)
    p4 = c3.new_page()
    p4.goto(BASE)
    p4.wait_for_selector("#install:not(.hidden)", timeout=10000)
    check("su: a phone sees the quiet home-screen hint", "Add to Home Screen" in p4.locator("#installText").inner_text())
    shot(p4, "11-install-hint", full=True)
    p4.reload()
    p4.wait_for_selector("#drop")
    p4.wait_for_timeout(1500)
    check("su: ...and only once", p4.locator("#install").is_hidden())
    p4.evaluate("localStorage.removeItem('installHint')")
    p4.reload()
    p4.wait_for_selector("#install:not(.hidden)", timeout=10000)
    p4.click("#installNo")
    check("su: 'Got it' hides the hint", p4.locator("#install").is_hidden())
    c3.close()
    page.reload()
    page.wait_for_timeout(1500)
    check("su: a computer is not shown the hint", page.locator("#install").is_hidden())

    # ---------------------------------------------------------------- 8. recorder: hidden when it can't work
    print("screens-upload 7: recorder", flush=True)
    p5 = page.context.new_page()
    p5.add_init_script("delete window.MediaRecorder;")
    p5.goto(BASE)
    p5.set_input_files("#files", [str(SAMPLE / "01.mp4")])
    check("su: no recording support hides the Record button but keeps 'Choose a voice recording'",
          p5.locator("#recGo").count() == 0 and p5.locator("#voicePick").is_visible())
    p5.close()
    p5 = page.context.new_page()
    p5.add_init_script("navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('no', 'NotAllowedError'));")
    p5.goto(BASE)
    p5.set_input_files("#files", [str(SAMPLE / "01.mp4")])
    p5.click("#recGo")
    p5.wait_for_selector("#voice .help-line.warn")
    check("su: a blocked microphone is explained in plain words", "microphone" in p5.locator("#voice .help-line").inner_text()
          and p5.locator("#voicePick").is_visible(), p5.locator("#voice .help-line").inner_text())
    shot(p5, "12-mic-blocked", full=True)
    p5.close()
    # a voice file the browser did not make: any ending
    page.reload()
    page.set_input_files("#files", [str(SAMPLE / "01.mp4")])
    webm = media / "memo.webm"
    sh("-i", str(voice), "-t", "5", "-c:a", "libopus", str(webm))
    page.set_input_files("#voiceFile", [str(webm)])
    page.wait_for_function("/memo.webm/.test(document.getElementById('voice').textContent) && /0:0/.test(document.getElementById('voice').textContent)", timeout=15000)
    check("su: 'Choose a voice recording' accepts a .webm and shows its length", True, page.locator("#voice").inner_text().replace("\n", " | "))
    page.evaluate("clearAll()")

    # ---------------------------------------------------------------- 9. a real recording, a real video
    print("screens-upload 8: record a voice-over in the browser and make the video (about a minute)", flush=True)
    wav = media / "speech.wav"
    sh("-i", str(voice), "-t", "9", "-ac", "1", "-ar", "48000", str(wav))
    browser2 = page.context.browser.browser_type.launch(args=[
        "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", f"--use-file-for-fake-audio-capture={wav}"])
    try:
        c4 = browser2.new_context(viewport=PHONE, permissions=["microphone"], bypass_csp=True)
        c4.request.post(f"{BASE}/api/login", data=json.dumps({"username": "tester", "password": ctx.PASSWORD}), headers=J)
        p6 = c4.new_page()
        errs6, bodies, puts = [], [], []
        p6.on("pageerror", lambda e: errs6.append(str(e)))
        p6.on("request", lambda r: bodies.append(r.post_data) if r.method == "POST" and r.url.endswith("/api/uploads") else None)
        p6.on("request", lambda r: puts.append(r.url) if r.method == "PUT" else None)
        p6.goto(BASE)
        p6.set_input_files("#files", [str(f) for f in clips[1:]])   # 32 s of clips for a 9 s voice-over
        p6.wait_for_selector("#recGo")
        check("su: Record my voice-over is offered", p6.locator("#recGo").is_visible())
        p6.click("#recGo")
        p6.wait_for_selector("#recStop")
        check("su: recording shows a red dot and a timer", p6.locator("#voice .dot").is_visible() and p6.locator("#recTime").is_visible())
        check("su: Make my video waits while recording", p6.locator("#go").is_disabled())
        p6.wait_for_function("/0:0[3-9]|0:[1-5]/.test(document.getElementById('recTime').textContent)", timeout=15000)
        shot(p6, "13-recording")
        p6.click("#recStop")
        p6.wait_for_selector("#voice audio")
        vt = p6.locator("#voice").inner_text()
        check("su: after Stop she can play it back, and it has a length", p6.locator("#voice audio").is_visible() and "Your recording" in vt and ":" in vt, vt.replace("\n", " | "))
        check("su: the recording is named like a voice file the site accepts",
              p6.evaluate("voice.name") in ("Voice recording.webm", "Voice recording.m4a", "Voice recording.ogg") and p6.evaluate("voice.size") > 1000, p6.evaluate("voice.name"))
        shot(p6, "14-recorded", full=True)
        p6.click("#recRedo")
        p6.wait_for_selector("#recStop")
        p6.wait_for_timeout(2500)
        p6.click("#recStop")
        p6.wait_for_selector("#voice audio")
        check("su: Record again replaces it", p6.locator("#voice audio").count() == 1 and p6.evaluate("voiceSecs") < 6, p6.evaluate("voiceSecs"))
        # redo once more and keep it a bit longer, so the words are in it
        p6.click("#recRedo")
        p6.wait_for_selector("#recStop")
        p6.wait_for_timeout(8500)
        p6.click("#recStop")
        p6.wait_for_selector("#voice audio")
        # the new controls
        p6.fill("#hints", "Ellis Park Dr, Shellsburg")
        p6.select_option("#kind", "recipe")
        p6.check("#private")
        shot(p6, "15-ready", full=True)
        p6.click("#go")
        p6.wait_for_selector("#working:not(.hidden)")
        p6.wait_for_function("/Working for 0:0/.test(document.getElementById('elapsed').textContent)", timeout=10000)
        check("su: the working screen shows the time so far", True, p6.locator("#elapsed").inner_text())
        check("su: while uploading she is told to keep the page open (not that it is safe to close)",
              "Keep this page open" in p6.locator("#waitNote").inner_text() and p6.locator("#safeLine").is_hidden())
        p6.wait_for_selector("#safeLine:not(.hidden)", timeout=120000)
        safe = p6.locator("#safeLine").inner_text()
        check("su: once uploaded it says 'Safe to close this page' with a link to My videos",
              "Safe to close this page" in safe and p6.locator("#safeLine a").get_attribute("href") == "/videos", safe)
        shot(p6, "16-working")
        time.sleep(2.2)
        el2 = p6.locator("#elapsed").inner_text()
        check("su: the time keeps counting", el2.startswith("Working for 0:") and el2 != "Working for 0:00", el2)
        body = json.loads(bodies[0]) if bodies else {}
        check("su: the upload request carries every new control",
              body.get("hints") == "Ellis Park Dr, Shellsburg" and body.get("format") == "recipe" and body.get("private") is True
              and body.get("denoise") in (True, False) and body["voice"]["name"].startswith("Voice recording."), {k: v for k, v in body.items() if k != "clips"})
        check("su: the recording was sent in pieces like any voice file", any("/voice." in u for u in puts), [u.split("/")[-1][:20] for u in puts][:5])
        made = ctx.wait_for_video(p6)
        check("su: the video with a recorded voice-over was made", made, "" if made else p6.locator("#failMsg").inner_text())
        if made:
            check("su: the heading logo is out of the way on the result", p6.locator("#hdr").is_hidden())
            rep = p6.evaluate("fetch(`/api/jobs/${jobId}`).then(r => r.json()).then(j => j.report)")
            s = rep.get("settings") or {}
            check("su: the report shows the names, kind and privacy choice", s.get("hints") == "Ellis Park Dr, Shellsburg"
                  and s.get("format") == "recipe" and s.get("privacy") == "private", {k: s.get(k) for k in ("hints", "format", "privacy", "match_clips")})
            check("su: no pictures were sent for matching in private mode", not (rep.get("clip_match") or {}).get("used"), (rep.get("clip_match") or {}).get("reason"))
            shot(p6, "17-result", full=False)
        check("su: no JavaScript errors during the recording run", not errs6, errs6[:2])
        c4.close()
    finally:
        browser2.close()

    # ---------------------------------------------------------------- 10. the example video
    print("screens-upload 9: the example video", flush=True)
    src = ROOT / "out" / "demo-tour-moved"
    if not (src / "final.mp4").exists():
        check("su: example fixture (out/demo-tour-moved) is on this machine", False, "skipped")
    else:
        d = out / "demo-tour-moved"
        d.mkdir(parents=True, exist_ok=True)
        for name in ("final.mp4", "report.json", "copy.json", "words.json", "transcript.txt", "captions.ass"):
            if (src / name).exists():
                (d / name).unlink(missing_ok=True)   # another part may have left a hard link to the original here
                shutil.copy2(src / name, d / name)
        (out / "_demo.txt").write_text("demo-tour-moved", encoding="utf-8")
        seen = []
        p7 = page.context.new_page()
        p7.on("request", lambda r: seen.append(r.method + " " + r.url))
        p7.on("pageerror", lambda e: errors.append(str(e)))
        p7.goto(BASE)
        p7.wait_for_selector("#exampleBtn:visible", timeout=10000)
        check("su: 'See an example' shows when there is one", "example" in p7.locator("#exampleBtn").inner_text().lower())
        shot(p7, "18-example-link", full=True)
        p7.click("#exampleBtn")
        p7.wait_for_selector("#done:not(.hidden)")
        p7.wait_for_selector("#players video")
        check("su: the example opens in the same result view, clearly marked", p7.locator("#exampleBar").is_visible() and "Example" in p7.locator("#exampleBar").inner_text())
        check("su: its video comes from the example route", p7.get_attribute("#players video", "src") == "/api/demo/video/1"
              and "/api/demo/thumb" in (p7.get_attribute("#players video", "poster") or ""))
        check("su: titles and platform captions show", p7.locator("#copy .field.title").count() >= 1 and p7.locator("#platforms .field").count() >= 1)
        check("su: nothing that changes or sends is on the example",
              p7.locator("#fbOpen").is_hidden() and p7.locator("#fbCard").is_hidden() and p7.locator(".save").count() == 0 or p7.locator(".save").first.is_hidden())
        check("su: the example never asks for /api/jobs (it belongs to nobody)", not [u for u in seen if "/api/jobs/" in u and "/api/jobs/web-" not in u], [u for u in seen if "/api/jobs/" in u][:3])
        p7.wait_for_function("document.querySelector('#players video').readyState >= 1", timeout=30000)
        check("su: the example video plays", p7.evaluate("document.querySelector('#players video').duration") > 5)
        shot(p7, "19-example", full=True)
        p7.click("#exampleLeave")
        p7.wait_for_selector("#drop")
        check("su: 'Make my own video' goes back to the start", p7.locator("#pick").is_visible() and p7.locator("#exampleWrap").is_visible())
        p7.close()

    # ---------------------------------------------------------------- 11. dark mode
    page.reload()
    page.set_input_files("#files", [str(f) for f in clips] + [str(voice)])
    page.wait_for_selector("#clips li.clip")
    page.emulate_media(color_scheme="dark")
    page.wait_for_timeout(300)
    shot(page, "20-dark", full=True)
    page.emulate_media(color_scheme="light")
    page.evaluate("clearAll()")
    check("su: no JavaScript errors on the upload page", not errors, errors[:3])
