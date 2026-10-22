"""Automatic clip order on the website: she picks clips in any order and they play in the order she filmed them,
unless she used the arrows herself."""
import shutil
import tempfile
from pathlib import Path

ORIGINAL = ["01.mp4", "02.mp4", "03.mp4"]          # the recipe clips, in the order they were filmed
RENAMED = {"01.mp4": "c.mp4", "02.mp4": "b.mp4", "03.mp4": "a.mp4"}   # names that sort the opposite way


def _report(page):
    return page.evaluate("fetch(`/api/jobs/${jobId}`).then(r => r.json()).then(j => j.report)")


def _order(page):
    """Her names for the clips, in the order the video plays them."""
    return page.evaluate("fetch(`/api/jobs/${jobId}/clips`).then(r => r.json()).then(j => j.clips.map(c => c.name))")


def run(page, ctx):
    tmp = Path(tempfile.mkdtemp(prefix="alex-autosort-"))
    try:
        for old, new in RENAMED.items():
            shutil.copy2(ctx.SAMPLE / old, tmp / new)
        voice = ctx.SAMPLE / "voice.m4a"
        files = [str(tmp / n) for n in ("a.mp4", "b.mp4", "c.mp4")] + [str(voice)]

        print("Auto order: shuffled names, no arrows touched", flush=True)
        page.goto(ctx.BASE)
        page.set_input_files("#files", files)
        ctx.expect(page.locator("#clips li.clip")).to_have_count(3)
        ctx.check("the page promises to put the clips in order for her", "in the order you filmed them" in page.locator("#orderHelp").inner_text())
        page.screenshot(path=ctx.SHOTS / "autosort-1-picked.png")
        page.click("#go")
        made = ctx.wait_for_video(page)
        ctx.check("video was made from clips picked in the wrong order", made, "" if made else page.locator("#failMsg").inner_text())
        if made:
            rep = _report(page)
            played = _order(page)
            ctx.check("clips play in filming order (c, b, a), not the order picked (a, b, c)", played == ["c.mp4", "b.mp4", "a.mp4"], played)
            ctx.check("report says the clips were put in order", bool((rep.get("clip_order") or {}).get("moved")), rep.get("clip_order"))
            ctx.check("'What I did' tells her", "in the order you filmed them" in page.locator("#notes").inner_text(), page.locator("#notes").inner_text()[:160])
            page.screenshot(path=ctx.SHOTS / "autosort-2-result.png", full_page=True)

        print("Auto order: she sets the order herself", flush=True)
        page.goto(ctx.BASE)
        page.set_input_files("#files", files)
        ctx.expect(page.locator("#clips li.clip")).to_have_count(3)
        # a, b, c -> move c up twice -> c, a, b ; then that is HER order and must be used exactly
        page.locator("#clips li.clip").nth(2).locator("button").first.click()
        page.locator("#clips li.clip").nth(1).locator("button").first.click()
        names = page.locator("#clips li.clip .fname").all_inner_texts()
        ctx.check("her arrows changed the list", names == ["c.mp4", "a.mp4", "b.mp4"], names)
        ctx.check("the page now says it is using her order", "Using the order you set" in page.locator("#orderHelp").inner_text())
        page.click("#go")
        made = ctx.wait_for_video(page)
        ctx.check("video was made in her own order", made, "" if made else page.locator("#failMsg").inner_text())
        if made:
            rep = _report(page)
            played = _order(page)
            ctx.check("her order is kept exactly (c, a, b)", played == ["c.mp4", "a.mp4", "b.mp4"], played)
            ctx.check("nothing claims the tool re-sorted them", not (rep.get("clip_order") or {}).get("moved"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
