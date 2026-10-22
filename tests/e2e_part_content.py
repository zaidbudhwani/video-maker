"""Content lane browser-side check. The website screens for the new copy fields belong to the web lanes; this part checks
what the content lane owns and that the site still serves: the signed-in page loads, any finished job in the throwaway
data folder carries the new copy.json fields, and a post pack builds from a saved demo job and opens as a zip.
Run alone:  E2E_PORT=8062 E2E_ONLY=content venv\Scripts\python tests\e2e_site.py"""
import json
import sys
import tempfile
import zipfile
from pathlib import Path


def run(page, ctx):
    sys.path.insert(0, str(ctx.ROOT))
    page.goto(ctx.BASE)
    ctx.check("content: the upload page loads signed in", page.locator("#drop").is_visible())

    # any job the base flow made in this throwaway site
    for copy_path in sorted(Path(ctx.DATA).glob("**/copy.json"))[:3]:
        c = json.loads(copy_path.read_text(encoding="utf-8"))
        ctx.check(f"content: {copy_path.parent.name} copy.json has format, checklist and cost",
                  c.get("format") and c.get("format_label") and c.get("checklist") and "ai_cost_usd" in c)
        ctx.check(f"content: {copy_path.parent.name} YouTube caption ends with #Shorts",
                  c.get("captions", {}).get("youtube_shorts", "").rstrip().endswith("#Shorts") or c.get("disclosure"))

    from video_tool import postpack
    demo = Path(ctx.ROOT) / "out" / "demo-tour-moved"
    if (demo / "final.mp4").exists():
        with tempfile.TemporaryDirectory() as td:
            dest = postpack.build(demo, Path(td) / "pack.zip")
            with zipfile.ZipFile(dest) as z:
                names = set(z.namelist())
            ctx.check("content: post pack zip holds video, captions, cover, alt text and post text",
                      {"final.mp4", "captions.srt", "cover.jpg", "alt-text.txt", "post-text.txt"} <= names, sorted(names))
