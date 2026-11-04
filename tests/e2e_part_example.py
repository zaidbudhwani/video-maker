r"""Template for a plug-in check file: copy this, rename to e2e_part_<yourpart>.py, delete this one's body.
Loaded by tests/e2e_site.py after the base flow. ctx has BASE, ROOT, SHOTS, SAMPLE, PASSWORD, DATA, expect, check,
wait_for_video, video_facts, MAKE_TIMEOUT. Run only this part with:  set E2E_ONLY=example & venv\Scripts\python tests\e2e_site.py"""


def run(page, ctx):
    page.goto(ctx.BASE)
    ctx.check("example part: the upload page loads", page.locator("#drop").is_visible())
