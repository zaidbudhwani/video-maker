"""The video shape choice on the upload page: Social video (vertical, the default) or YouTube video (horizontal), the
"fill the screen" option for upright clips, and that the upload request carries the choice to the server.

    set E2E_ONLY=horizontal & set E2E_PORT=8341 & venv\\Scripts\\python tests\\e2e_site.py

No video is made: the page's upload request is caught and stopped once its body is read. The server side is checked by
starting an upload with the same body and reading the per-video settings it saved."""
import json


def _pick(page, ctx, files):
    page.goto(ctx.BASE)
    page.set_input_files("#files", files)
    ctx.expect(page.locator("#clips li.clip")).to_have_count(len([f for f in files if f.endswith(".mp4")]))


def _sent(page, ctx):
    """Tap Make my video, catch the upload request, stop it, and return its JSON body."""
    bodies = []

    def catch(route):
        bodies.append(route.request.post_data)
        route.abort()
    page.route("**/api/uploads", catch)
    page.click("#go")
    page.wait_for_function("!document.getElementById('failed').classList.contains('hidden')", timeout=30000)
    page.unroute("**/api/uploads")
    return json.loads(bodies[0]) if bodies else {}


def run(page, ctx):
    check = ctx.check
    page.set_viewport_size({"width": 390, "height": 844})
    page.emulate_media(reduced_motion="reduce")
    clips = [str(ctx.SAMPLE / n) for n in ("01.mp4", "02.mp4")]
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    print("horizontal 1: the choice is on the upload page, vertical by default", flush=True)
    _pick(page, ctx, clips)
    box = page.locator("#shapeBox")
    check("hz: 'What shape of video?' is shown with the clips", box.is_visible() and "what shape of video?" in box.inner_text().lower())
    text = box.inner_text()
    check("hz: both choices in plain words", "Social video (vertical)" in text and "YouTube video (horizontal, 1920x1080)" in text, text)
    check("hz: vertical is picked by default", page.is_checked("#shapeVL input") and not page.is_checked("#shapeHL input"))
    check("hz: the fill-the-screen option is hidden for a vertical video", not page.locator("#tallFillL").is_visible())
    for sel in ("#shapeVL", "#shapeHL"):
        h = page.locator(sel).bounding_box()["height"]
        check(f"hz: {sel} is a 44 px touch target", h >= 44, h)
    body = _sent(page, ctx)
    check("hz: a vertical upload says vertical", body.get("orientation") == "vertical" and body.get("tall_clips") == "bars", body)

    print("horizontal 2: picking YouTube video", flush=True)
    _pick(page, ctx, clips)
    page.click("#shapeHL")
    check("hz: YouTube video is picked and highlighted", page.is_checked("#shapeHL input")
          and "on" in page.get_attribute("#shapeHL", "class") and "on" not in page.get_attribute("#shapeVL", "class"))
    check("hz: upload explains horizontal output and manual split boundary", "horizontal YouTube" in page.locator("#subLine").inner_text()
          and "Split longer" in page.locator("#tipLong").inner_text())
    tall = page.locator("#tallFillL")
    check("hz: the fill-the-screen option appears, explained", tall.is_visible() and "top and bottom" in tall.inner_text()
          and "bars" in tall.inner_text(), tall.inner_text())
    check("hz: fill-the-screen is off by default (upright clips shown whole)", not page.is_checked("#tallFill"))
    check("hz: the fill option is a 44 px touch target", tall.bounding_box()["height"] >= 44, tall.bounding_box())
    body = _sent(page, ctx)
    check("hz: the upload request carries horizontal, bars", body.get("orientation") == "horizontal"
          and body.get("tall_clips") == "bars", body)

    _pick(page, ctx, clips)
    page.click("#shapeHL")
    page.check("#tallFill")
    body = _sent(page, ctx)
    check("hz: with fill ticked the request carries fill", body.get("orientation") == "horizontal"
          and body.get("tall_clips") == "fill", body)

    print("horizontal 3: keyboard", flush=True)
    _pick(page, ctx, clips)
    page.focus("#shapeVL input")
    page.keyboard.press("ArrowDown")
    check("hz: arrow keys move to YouTube video", page.is_checked("#shapeHL input") and page.locator("#tallFillL").is_visible())
    outline = page.evaluate("getComputedStyle(document.activeElement).outlineStyle")
    check("hz: keyboard focus is visible", outline not in ("none", ""), outline)
    page.locator("#shapeBox").scroll_into_view_if_needed()
    check("hz: phone has no horizontal overflow", page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
    check("hz: reduced motion active", page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches"))
    page.screenshot(path=ctx.SHOTS / "horizontal-choice.png")

    print("horizontal 4: the server keeps the choice for this video", flush=True)
    sizes = [{"name": "01.mp4", "size": (ctx.SAMPLE / "01.mp4").stat().st_size}]
    r = page.request.post(f"{ctx.BASE}/api/uploads", data=json.dumps({"clips": sizes, "orientation": "horizontal",
                                                                       "tall_clips": "fill"}),
                          headers={"Content-Type": "application/json"})
    job = r.json().get("job_id") if r.ok else None
    pend = {}
    if job:
        try:
            pend = json.loads((ctx.DATA / "out" / "_uploads" / job / "pending.json").read_text(encoding="utf-8"))
        except OSError:
            pass
    check("hz: the server saved orientation and fill as this video's settings",
          pend.get("extras", {}).get("orientation") == "horizontal" and pend["extras"].get("tall_clips") == "fill",
          (r.status, pend.get("extras")))
    r = page.request.post(f"{ctx.BASE}/api/uploads", data=json.dumps({"clips": sizes, "orientation": "diagonal"}),
                          headers={"Content-Type": "application/json"})
    check("hz: a made-up shape is refused in plain words", r.status == 400 and "YouTube" in r.json().get("detail", ""),
          (r.status, r.text()[:200]))
    check("hz: no page errors", not errors, errors)

    print("horizontal 5: result wording and framing at 390x844", flush=True)
    rep = {"job_id": "hz-fixture", "mode": "own_audio", "length": 100,
           "parts": [{"file": "final.mp4", "length": 100}],
           "settings": {"orientation": "horizontal", "tall_clips": "bars", "framing": "auto"}, "warnings": []}
    cp = {"titles": ["My YouTube video"], "description": "My own words.", "orientation": "horizontal",
          "captions": {"youtube_shorts": "My own words."}}
    kit = {"titles": cp["titles"], "description": cp["description"], "tags": [], "source": "fallback"}
    rep["longform"] = {"file": "longform.json", "srt": "long.srt"}
    def fixture(route):
        url = route.request.url.split("?")[0]
        if url.endswith("/longform"):
            return route.fulfill(json=kit)
        if url.endswith("/hz-fixture"):
            return route.fulfill(json={"status": "done", "report": rep, "copy": cp,
                                       "transcript": {"burned_in": True, "parts": ["My own words."], "text": "My own words."}})
        if url.endswith("/clips"):
            return route.fulfill(json={"clips": []})
        return route.fulfill(status=404, body="")
    page.route("**/api/jobs/hz-fixture**", fixture)
    page.goto(ctx.BASE + "/?job=hz-fixture")
    page.wait_for_selector("#done:not(.hidden)")
    check("hz: result explains horizontal YouTube size", "1920x1080" in page.locator("#notes").inner_text()
          and "TikTok" not in page.locator("#notes").inner_text())
    check("hz: posting tab says YouTube, no Shorts", page.locator("#platformTabs").inner_text() == "YouTube")
    page.wait_for_selector("#ytCard:not(.hidden)")
    check("hz: YouTube kit instructions describe rendered video", "captions are already" in page.locator("#ytCard").inner_text()
          and page.locator("#ytSrtBox").is_hidden())
    page.locator("#ytCard").scroll_into_view_if_needed()
    page.screenshot(path=ctx.SHOTS / "horizontal-result.png")
    page.click("#fbOpen")
    page.evaluate("document.getElementById('moreOpts').open = true")
    check("hz: framing explains bars and crop", "top and bottom" in page.locator("#frameGroup").inner_text()
          and "every clip whole" in page.locator("#frameGroup").inner_text())
    page.click('[data-k="frame"][data-v="fit"]')
    changes = page.evaluate("changes()")
    check("hz: whole-frame change carries bars", changes.get("tall_clips") == "bars" and changes.get("framing") == "fit", changes)
    page.locator("#frameGroup").scroll_into_view_if_needed()
    page.screenshot(path=ctx.SHOTS / "horizontal-framing.png")
    page.unroute("**/api/jobs/hz-fixture**")
    page.emulate_media(reduced_motion="no-preference")   # this page is shared: later parts (motion) need the real setting
    page.set_viewport_size({"width": 430, "height": 900})
