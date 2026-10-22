"""Music choice on the upload page and in the Change panel (web/index.html drawMusic). Works on its own:

    E2E_PORT=8335 E2E_ONLY=music venv/Scripts/python tests/e2e_site.py

The track list and the 10-second previews are the REAL routes (the real tracks in assets/music, read only). The upload
start, opening a finished video and the remake are answered by the test (page.route), so no video is made."""
import json
import re

J = {"content-type": "application/json"}


def licence_titles(root):
    rows = {}
    for line in (root / "assets" / "music" / "LICENSES.txt").read_text(encoding="utf-8").splitlines():
        m = re.match(r'^(\S+\.mp3)\s+"([^"]+)"\s+by\s+(.+?)\s*\(', line.strip())
        if m:
            rows[m.group(1)] = (m.group(2), m.group(3))
    return rows


def run(page, ctx):
    BASE, check = ctx.BASE, ctx.check
    titles = licence_titles(ctx.ROOT)
    files = sorted(titles, key=str.lower)
    errors, uploads, sent = [], [], []

    p = page.context.new_page()
    p.set_viewport_size({"width": 390, "height": 844})
    p.on("pageerror", lambda e: errors.append(str(e)))
    print("Music choice", flush=True)

    # ---- the routes themselves ----
    r = p.request.get(BASE + "/api/music")
    tracks = r.json().get("tracks", []) if r.ok else []
    check("music: the site lists the installed tracks with the LICENSES.txt titles",
          [t["file"] for t in tracks] == files and all(titles[t["file"]] == (t["title"], t["artist"]) for t in tracks), tracks)
    r = p.request.get(BASE + "/api/music/" + files[0])
    check("music: a preview is an mp3", r.ok and r.headers.get("content-type") == "audio/mpeg" and len(r.body()) > 20000, (r.status, r.headers.get("content-type")))
    for bad in ("LICENSES.txt", "README.md", "..%2F..%2F.env"):
        check(f"music: nothing else in the folder can be read ({bad})", p.request.get(BASE + "/api/music/" + bad).status == 404)
    stranger = page.context.browser.new_context()
    check("music: signed out, no list and no preview", stranger.request.get(BASE + "/api/music").status == 401
          and stranger.request.get(BASE + "/api/music/" + files[0]).status == 401)
    stranger.close()

    # ---- the upload page ----
    def on_upload(route):
        uploads.append(json.loads(route.request.post_data or "{}"))
        return route.fulfill(status=400, headers=J, body=json.dumps({"detail": "Test stop: nothing uploaded."}))

    p.route("**/api/uploads", on_upload)
    p.goto(BASE + "/")
    p.set_input_files("#files", [str(sorted(ctx.SAMPLE.glob("*.mp4"))[0])])
    p.wait_for_selector("#musicPick:not(.hidden)", timeout=15000)
    rows = p.locator("#musicList .mrow label").all_inner_texts()
    check("upload: Pick one for me, every track by title and artist, and No music",
          rows[0] == "Pick one for me" and rows[-1] == "No music" and len(rows) == len(files) + 2
          and all(titles[f][0] in rows[i + 1] and titles[f][1] in rows[i + 1] for i, f in enumerate(files)), rows)
    check("upload: Pick one for me is chosen at first", p.locator("#musicList input").first.is_checked())
    note = p.inner_text("#musicPick")
    check("upload: honest about copyright (a claim can still happen), no 'can't be claimed' promise",
          "copyright claim" in note and "can't" not in note.lower() and "never" not in note.lower(), note)
    sizes = [b.bounding_box() for b in p.locator("#musicList .play").all()] + [l.bounding_box() for l in p.locator("#musicList .mrow label").all()]
    check("upload: every Play button and choice is at least 44px tall", all(b and b["height"] >= 43.5 for b in sizes), [round(b["height"]) for b in sizes if b])
    play = p.locator("#musicList .play").first
    check("upload: Play says which track it plays", play.get_attribute("aria-label") == f"Play 10 seconds of {titles[files[0]][0]}", play.get_attribute("aria-label"))
    with p.expect_response(lambda resp: "/api/music/" in resp.url, timeout=15000) as got:
        play.click()
    check("upload: Play fetches that track's 10-second preview", got.value.ok and got.value.url.endswith("/api/music/" + files[0]), got.value.url)
    check("upload: the button shows it is playing", play.get_attribute("aria-pressed") == "true" and play.inner_text() == "Stop", play.inner_text())
    p.wait_for_function("() => !player.paused && player.currentTime > 0.15 && player.readyState >= 2", timeout=15000)
    check("audio: preview actually advances unmuted", p.evaluate("!player.muted && player.volume > 0 && player.currentTime > 0"))
    decoded = p.evaluate("""async () => {
      const a = new AudioContext(), out = [];
      try { for (const t of TRACKS) {
        const b = await a.decodeAudioData(await (await fetch('/api/music/' + encodeURIComponent(t.file))).arrayBuffer());
        let peak=0, sum=0; const x=b.getChannelData(0);
        for (const v of x) { peak=Math.max(peak,Math.abs(v)); sum+=v*v; }
        out.push({file:t.file, duration:b.duration, channels:b.numberOfChannels, sampleRate:b.sampleRate, peak, rms:Math.sqrt(sum/x.length)});
      } return out; } finally { await a.close(); }
    }""")
    check("audio: every installed preview decodes to 10 seconds of non-silent PCM", len(decoded) == len(files) and all(9.9 <= d['duration'] <= 10.2 and d['rms'] > 0.0001 for d in decoded), decoded)
    (ctx.SHOTS / "decoded-audio.json").write_text(json.dumps(decoded, indent=2), encoding="utf-8")
    p.locator("#musicPick").scroll_into_view_if_needed()
    p.screenshot(path=ctx.SHOTS / "phone-music-upload-playing.png")
    check("phone: upload music fits 390px", p.evaluate("document.documentElement.scrollWidth <= innerWidth"))
    play.click()
    check("upload: tapping again stops it", play.get_attribute("aria-pressed") == "false" and play.inner_text() == "Play")
    play.focus()
    outline = p.evaluate("getComputedStyle(document.activeElement).outlineStyle")
    p.keyboard.press("Tab")   # move off and back so :focus-visible applies to keyboard focus
    p.keyboard.press("Shift+Tab")
    check("upload: keyboard focus on Play is visible", p.evaluate("getComputedStyle(document.activeElement).outlineStyle") != "none", outline)
    p.set_input_files("#files", [str(sorted(ctx.SAMPLE.glob("*.mp4"))[0])])
    p.wait_for_selector("#clips li.clip")

    def fresh():   # the options (music included) appear once a clip is chosen
        p.goto(BASE + "/")
        p.set_input_files("#files", [str(sorted(ctx.SAMPLE.glob("*.mp4"))[0])])
        p.wait_for_selector("#musicPick:not(.hidden)")
        p.wait_for_selector("#clips li.clip")

    def send_with(index):
        uploads.clear()
        p.locator("#musicList input").nth(index).check()
        p.click("#go")
        p.wait_for_function("() => !document.getElementById('failed').classList.contains('hidden')", timeout=15000)
        return uploads[-1] if uploads else {}

    b = send_with(2)
    check("upload: the picked track is sent with the video", b.get("music") == files[1], b.get("music"))
    fresh()
    b = send_with(len(files) + 1)
    check("upload: No music is sent as none", b.get("music") == "none", b.get("music"))
    fresh()
    b = send_with(0)
    check("upload: Pick one for me is sent as automatic", b.get("music") == "auto", b.get("music"))
    p.unroute("**/api/uploads")

    # a remembered track: the upload page starts from it
    p.route("**/api/prefs", lambda route: route.fulfill(status=200, headers=J, body=json.dumps({"denoise": False, "music": files[-1]}))
            if route.request.method == "GET" else route.continue_())
    fresh()
    p.wait_for_function(f"() => document.querySelectorAll('#musicList input')[{len(files)}].checked", timeout=10000)
    check("upload: her remembered track is chosen at first", p.locator("#musicList input").nth(len(files)).is_checked())
    p.unroute("**/api/prefs")

    # ---- the Change panel ----
    def report(job_id, music):
        return {"job_id": job_id, "mode": "voice", "length": 31.2, "music": music, "denoised": False, "copy": "ok",
                "parts": [{"file": "final.mp4", "length": 31.2, "size_mb": 18.4}], "settings": {"format": "auto", "music_track": ""},
                "timeline": [], "warnings": [], "not_shown": [], "uncertain_words": []}
    copy = {"titles": ["A title"], "hook": "Hi.", "description": "A description.", "cta": "Message me.", "format": "other",
            "format_label": "Video", "captions": {}, "checklist": [], "review_notes": []}
    tr = {"burned_in": True, "parts": ["Hello there."], "text": "Hello there."}
    jobs = {"mu1": report("mu1", files[0]), "mu2": report("mu2", None), "mu3": report("mu3", files[0])}

    def handle(route):
        req = route.request
        m = req.url.split(BASE, 1)[1].split("?", 1)[0].split("/")
        if len(m) == 4 and m[2] == "jobs" and req.method == "GET" and m[3] in jobs:
            return route.fulfill(status=200, headers=J, body=json.dumps({"status": "done", "report": jobs[m[3]], "copy": copy, "transcript": tr}))
        if len(m) == 5 and m[4] == "feedback":
            sent.append(json.loads(req.post_data or "{}"))
            return route.fulfill(status=200, headers=J, body=json.dumps({"job_id": "mu3", "applied": [], "unsupported": [], "remembered": []}))
        if len(m) == 5 and m[4] == "clips":
            return route.fulfill(status=200, headers=J, body=json.dumps({"clips": []}))
        return route.continue_()

    p.route("**/api/jobs/**", handle)

    def open_more(job_id):
        p.goto(BASE + f"/?job={job_id}")
        p.wait_for_function("() => !document.getElementById('done').classList.contains('hidden')", timeout=20000)
        p.click("#fbOpen")
        p.click("#moreOpts summary")
        p.wait_for_selector("#fbMusicList .mrow")
        sent.clear()

    open_more("mu1")
    first = p.locator("#fbMusicList .mrow label").first.inner_text()
    check("change: says which track this video has", first.startswith("Pick one for me (this video has") and titles[files[0]][0] in first, first)
    check("change: the quieter / louder / no music buttons are there", p.locator("#musicLevel").is_visible())
    p.locator("#fbMusicList input").nth(2).check()
    check("change: the summary names the new track", f'music: "{titles[files[1]][0]}"' in p.inner_text("#summary"), p.inner_text("#summary"))
    check("phone: change music fits 390px", p.evaluate("document.documentElement.scrollWidth <= innerWidth"))
    small = p.locator("#fbMusicList .play, #fbMusicList label, #musicLevel button").evaluate_all("es => es.map(e => ({text:e.innerText, w:e.getBoundingClientRect().width, h:e.getBoundingClientRect().height})).filter(r => r.w < 44 || r.h < 44)")
    check("phone: change music controls are 44px in both dimensions", not small, small)
    p.emulate_media(reduced_motion="reduce")
    check("phone: reduced motion disables result flash", p.evaluate("(() => { const e=document.createElement('div'); e.className='flash'; document.body.append(e); const a=getComputedStyle(e).animationName; e.remove(); return a === 'none'; })()"))
    p.locator("#fbMusicList .play").first.focus()
    p.keyboard.press("Tab"); p.keyboard.press("Shift+Tab")
    check("phone: Change panel Play has keyboard focus ring", p.evaluate("getComputedStyle(document.activeElement).outlineStyle !== 'none'"))
    p.screenshot(path=ctx.SHOTS / "music-change-panel.png", full_page=True)
    p.screenshot(path=ctx.SHOTS / "phone-music-change-focus.png")
    p.click("#remake")
    p.wait_for_function("() => document.querySelector('#versionTag .version')", timeout=30000)
    check("change: Remake sends the new track (music already on: no other change)", sent and sent[-1].get("changes") == {"music_track": files[1]}, sent[-1:])

    open_more("mu1")
    p.locator("#fbMusicList input").nth(2).check()
    p.click('.opt[data-k="music"][data-v="off"]')
    check("change: No music wins over a picked track", "no music" in p.inner_text("#summary") and "music:" not in p.inner_text("#summary"), p.inner_text("#summary"))

    open_more("mu2")
    check("change: a video without music still offers the tracks (not the level buttons)",
          p.locator("#fbMusicList").is_visible() and p.locator("#musicLevel").is_hidden())
    check("change: ...with nothing chosen", p.locator("#fbMusicList input:checked").count() == 0)
    p.locator("#fbMusicList input").nth(1).check()
    p.click("#remake")
    p.wait_for_function("() => document.querySelector('#versionTag .version')", timeout=30000)
    check("change: picking a track for it turns music on", sent and sent[-1].get("changes") == {"music_track": files[0], "music_on": True}, sent[-1:])

    check("music: no JavaScript errors", not errors, errors)
    p.close()
