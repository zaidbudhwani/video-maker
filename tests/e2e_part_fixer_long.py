"""Fixer checks for long video mode in a real browser at phone width: the working screen while it waits, the clock, the
jump to the YouTube post, the 'what to do next' list, the long-video entry in 'Posted it?', the mode-aware header line.

The job status is mocked for the working screen (nothing is made). The result page is built from a copy of an engine run, like
e2e_part_longvideo.py does. Nothing of hers goes to any AI (the key is blank for the whole run).
Run only this part:  set E2E_PORT=8211 & set E2E_ONLY=fixer_long & venv\\Scripts\\python tests\\e2e_site.py"""
import importlib.util
import json
from pathlib import Path

JOB = "web-20260102-030405-000009"


def _status(**kw):
    base = {"status": "running", "eta_minutes": 11, "highlights": 3, "step": "Starting...",
            "long": {"shorts": 3, "video_seconds": 600, "stage": 0, "heard": None, "heard_of": None, "short": None, "of": 3,
                     "progress": 0.0, "minutes_left": 11, "estimate_minutes": 11}}
    long_ = {**base["long"], **kw.pop("long", {})}
    return {**base, **kw, "long": long_}


def run(page, ctx):
    page.set_viewport_size({"width": 430, "height": 900})
    state = {"v": _status(status="queued", ahead=2)}
    page.route(f"**/api/jobs/{JOB}", lambda route: route.fulfill(json=state["v"]))

    # ---- queued behind two other videos
    page.goto(ctx.BASE + f"/?job={JOB}")
    page.wait_for_selector("#working:not(.hidden)")
    page.wait_for_function("document.getElementById('waitNote').textContent.length > 0")
    note = page.locator("#waitNote").inner_text()
    ctx.check("fx: queued: the line says how many are ahead AND how long yours takes", "2 other videos" in note and "about 11 minutes" in note
              and "leave this page" in note, note)
    ctx.check("fx: queued: no step is spinning and Uploading is ticked",
              page.locator("#steps li.active").count() == 0 and page.locator("#steps li.done").count() == 1)
    ctx.check("fx: queued: the bar waits (moving) instead of sitting empty", page.locator(".bar.wait").count() == 1)
    ctx.check("fx: queued: heading says it is waiting", "Waiting for the last video" in page.locator("#step").inner_text(),
              page.locator("#step").inner_text())
    page.screenshot(path=str(Path(ctx.SHOTS) / "fx-working-queued.png"))

    # ---- started, nothing logged yet
    state["v"] = _status(working_seconds=5)
    page.wait_for_function("document.getElementById('step').textContent.indexOf('Getting your video ready') === 0", timeout=8000)
    ctx.check("fx: stage 0: heading and list agree (nothing active)", page.locator("#steps li.active").count() == 0)

    # ---- listening, 6 minutes in: the clock follows the job, not this page
    state["v"] = _status(working_seconds=361, long={"stage": 1, "heard": 120, "heard_of": 600, "progress": 0.1, "minutes_left": 9})
    page.wait_for_function("document.getElementById('elapsed').textContent.indexOf('Working for 6:') === 0", timeout=8000)
    ctx.check("fx: the clock shows the job's own time (6:xx), not 0:0x", True, page.locator("#elapsed").inner_text())
    ctx.check("fx: listening: exactly one step spinning, no moving bar",
              page.locator("#steps li.active").count() == 1 and page.locator(".bar.wait").count() == 0)
    ctx.check("fx: the listening label counts the minutes heard", "2:00 of 10:00" in page.locator("#step").inner_text(),
              page.locator("#step").inner_text())
    page.screenshot(path=str(Path(ctx.SHOTS) / "fx-working-listen.png"))
    page.unroute(f"**/api/jobs/{JOB}")

    # ---- the upload page: header line follows the mode, long video row, tap targets
    page.goto(ctx.BASE + "/")
    page.wait_for_selector("#modeLongL")
    before = page.locator("#subLine").inner_text()
    page.click("#modeLongL")
    after = page.locator("#subLine").inner_text()
    ctx.check("fx: the header line changes in long mode", before != after and "long video" in after and "Shorts" in after, after)
    ctx.check("fx: the AI sentence says it is text only", "text only" in page.locator("#longAiNote").inner_text()
              or "words you say" in page.locator("#longAiNote").inner_text())
    sample = Path(ctx.ROOT) / "samples" / "tips" / "Home Value.mp4"
    if sample and sample.exists():
        page.set_input_files("#files", [str(sample)])
        page.wait_for_selector("#clips li.clip")
        page.wait_for_timeout(1500)
        title = page.locator("#clips li.clip .who b").inner_text()
        ctx.check("fx: the long video row is titled with her file's name", title == sample.name, title)
        h = page.evaluate("document.querySelector('label[for=shortsN]').getBoundingClientRect().height")
        ctx.check("fx: the 'How many Shorts?' label is a 44 px tap target", h >= 43, h)

    # ---- the result page
    spec = importlib.util.spec_from_file_location("lv_fixture", Path(ctx.ROOT) / "tests" / "e2e_part_longvideo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not mod._make_fixture(ctx, "web-fx-long", 2):
        ctx.check("fx: an engine run in Shorts mode exists to build the result page from", False)
        return
    page.goto(ctx.BASE + "/?job=web-fx-long")
    page.wait_for_selector("#done:not(.hidden)", timeout=20000)
    page.wait_for_selector("#ytCard:not(.hidden)", timeout=10000)
    ctx.check("fx: a line near the top says the YouTube post is ready, with a jump to it", page.locator("#ytJump").is_visible()
              and "title" in page.locator("#ytJump").inner_text(), page.locator("#ytJump").inner_text())
    box_top = page.evaluate("document.getElementById('ytJump').getBoundingClientRect().top + window.scrollY")
    card_top = page.evaluate("document.getElementById('ytCard').getBoundingClientRect().top + window.scrollY")
    ctx.check("fx: the jump line sits well above the YouTube card", box_top < card_top - 500, (box_top, card_top))
    page.click("#ytJumpBtn")
    page.wait_for_timeout(900)
    y = page.evaluate("document.getElementById('ytCard').getBoundingClientRect().top")
    ctx.check("fx: the jump brings the YouTube card into view", -50 < y < 850, y)
    txt = page.locator("#ytCard").inner_text()
    ctx.check("fx: the YouTube card says what to do next (upload, paste, captions, link the Shorts)",
              "what to do next" in txt.lower() and "Upload your long video" in txt and "Subtitles" in txt and "Related video" in txt, txt[-500:])
    names = [b.inner_text() for b in page.locator("#postBtns .tab").all()]
    ctx.check("fx: 'Posted it?' has the long video as well as the Shorts", "YouTube long video" in names and "YouTube Shorts" in names, names)
    page.screenshot(path=str(Path(ctx.SHOTS) / "fx-result-yt.png"))
