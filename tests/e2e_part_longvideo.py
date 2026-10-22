"""Shorts from a long video, in a real browser at phone width (430 x 900).

  1. the upload choice: 'What are you making?', two videos refused, a short video refused, the 150 s sample accepted with an
     honest time line, voice-over and arrows hidden;
  2. the result page for a finished Shorts job (a copy of an engine run placed in the throwaway data folder): labels, no
     'Part N of M', where each Short came from, the YouTube card, the captions file, 'How many Shorts?' in the change panel;
  3. ONE real run through the site with the 150 s Home Value sample, 2 Shorts. The key is blank for this whole test run
     (see start_server in e2e_site.py), so nothing of hers goes to any AI.

Run only this part:  set E2E_PORT=8181 & set E2E_ONLY=longvideo & venv\\Scripts\\python tests\\e2e_site.py"""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path


def _engine_run(root):
    """An engine run in Shorts mode (read only: only copied from)."""
    for d in sorted((root / "out").iterdir()):
        try:
            rep = json.loads((d / "report.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rep.get("mode") == "highlights" and all((d / n).exists() for n in ("longform.json", "long.srt", "final_part1.mp4", "copy.json")):
            return d
    return None


def _make_fixture(ctx, job_id, shorts=3):
    src = _engine_run(ctx.ROOT)
    if src is None:
        return False
    out = Path(ctx.DATA) / "out"
    d = out / job_id
    d.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if not f.is_file() or f.name not in ("report.json", "copy.json", "longform.json", "long.srt", "transcript.txt", "words.json",
                                             "captions_part1.ass", "final_part1.mp4"):
            continue
        if f.suffix in (".mp4", ".ass"):
            try:
                os.link(f, d / f.name)
                continue
            except OSError:
                pass
        shutil.copy2(f, d / f.name)   # small files are copied, never linked: they are rewritten below
    rep = json.loads((d / "report.json").read_text(encoding="utf-8"))
    copy = json.loads((d / "copy.json").read_text(encoding="utf-8"))
    base, cbase = rep["parts"][0], copy["parts"][0]
    rep["parts"], copy["parts"] = [], []
    for k in range(1, shorts + 1):
        if k > 1:
            shutil.copy2(d / "final_part1.mp4", d / f"final_part{k}.mp4")
            shutil.copy2(d / "captions_part1.ass", d / f"captions_part{k}.ass")
        rep["parts"].append({**base, "file": f"final_part{k}.mp4", "start_at": 190 + 60 * (k - 1), "end_at": 232 + 60 * (k - 1),
                             "why": f"Reason number {k}: you answer one clear question."})
        copy["parts"].append({**cbase, "label": f"Short {k}", "part": k, "of": shorts, "file": f"final_part{k}.mp4",
                              "titles": [f"Short {k} title one", f"Short {k} title two", f"Short {k} title three"]})
    rep["settings"] = {**rep.get("settings", {}), "highlights": shorts}
    rep["original"] = 448
    (d / "report.json").write_text(json.dumps(rep), encoding="utf-8")
    (d / "copy.json").write_text(json.dumps(copy), encoding="utf-8")
    (out / f"{job_id}.owner").write_text("tester", encoding="utf-8")
    (out / f"{job_id}.settings.json").write_text(json.dumps({"highlights": shorts}), encoding="utf-8")
    return True


def run(page, ctx):
    check, expect, SHOTS = ctx.check, ctx.expect, ctx.SHOTS
    import sys
    sys.path.insert(0, str(ctx.ROOT))
    from video_tool.config import FFMPEG
    page.set_viewport_size({"width": 430, "height": 900})
    tmp = Path(ctx.DATA) / "lv-media"
    tmp.mkdir(exist_ok=True)

    def make(name, seconds, codec):
        out = tmp / name
        if not out.exists():
            args = [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=s=160x120:d={seconds}:r=5", "-f", "lavfi",
                    "-i", f"sine=f=300:d={seconds}", "-shortest"]
            args += ["-c:v", "libvpx", "-b:v", "100k", "-c:a", "libopus"] if codec == "webm" else ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"]
            subprocess.run([*args, str(out)], capture_output=True)
        return out

    tiny_webm, tiny_mp4 = make("tiny.webm", 6, "webm"), make("tiny.mp4", 6, "mp4")
    sample = ctx.ROOT / "samples" / "tips" / "Home Value.mp4"

    # ------------------------------------------------------------------ 1. the upload choice
    print("1. Upload page: what are you making?", flush=True)
    page.goto(ctx.BASE)
    expect(page.locator("#drop")).to_be_visible()
    box = page.locator("#modeCard").bounding_box()
    check("the page asks 'What are you making?' on the first phone screen, no scrolling",
          "what are you making?" in page.locator("#modeCard").inner_text().lower()
          and box["y"] + box["height"] <= 900, box)
    check("'A video from my clips' is picked by default", page.is_checked('input[name="mode"][value="clips"]'))
    check("both choices are named in her words", "A video from my clips" in page.locator("#modeCard").inner_text()
          and "Shorts from a long video" in page.locator("#modeCard").inner_text())
    check("no 'How many Shorts?' until she picks Shorts", not page.locator("#shortsN").is_visible())
    page.screenshot(path=SHOTS / "lv-1-upload-default.png")
    page.locator("#modeLongL").click()
    check("picking Shorts from a long video explains what happens", "find the best moments" in page.locator("#longExplain").inner_text()
          and "title, chapters and description" in page.locator("#longExplain").inner_text())
    page.set_input_files("#files", [str(tiny_webm)])
    expect(page.locator("#clips li.clip")).to_have_count(1)
    expect(page.locator("#shortsN")).to_be_visible()
    check("'How many Shorts?' starts at 3", page.input_value("#shortsN") == "3")
    check("the voice-over box is hidden", not page.locator("#voice").is_visible())
    check("the clip arrows are hidden", not page.locator('#clips button[aria-label^="Move"]').first.is_visible())
    check("the clips-only extras are hidden (move line, picture sharing)", not page.locator("#private").is_visible())
    # a short video: refused nicely (the browser can read a webm; if it can't, the server says it after the upload)
    page.wait_for_function("document.querySelector('#pickErr').textContent.length > 0", timeout=15000)
    msg = page.locator("#pickErr").inner_text()
    check("a 6 second video: 'short enough to be a Short already. Use A video from my clips'",
          "short enough to be a Short already" in msg and "A video from my clips" in msg, msg)
    check("...and Make my Shorts is switched off", page.is_disabled("#go"))
    page.screenshot(path=SHOTS / "lv-2-upload-too-short.png")
    page.click("#toClips")
    check("'Use A video from my clips instead' goes back to the normal page", page.is_checked('input[name="mode"][value="clips"]')
          and page.locator("#voice").is_visible())
    page.locator("#modeLongL").click()
    # two videos: refused
    page.set_input_files("#files", [str(tiny_mp4)])
    expect(page.locator("#clips li.clip")).to_have_count(2)
    msg = page.locator("#pickErr").inner_text()
    check("two videos: 'Pick just one long video for this'", msg.startswith("Pick just one long video for this"), msg)
    check("...and Make my Shorts is switched off", page.is_disabled("#go"))
    page.screenshot(path=SHOTS / "lv-3-upload-two-videos.png")
    # start again with the real 150 s sample
    page.click("#reset")
    page.click("#resetYes")
    page.set_input_files("#files", [str(sample)])
    expect(page.locator("#clips li.clip")).to_have_count(1)
    page.wait_for_timeout(1500)
    check("the 150 s sample is accepted (no message, button ready)", page.locator("#pickErr").inner_text() == "" and page.is_enabled("#go"),
          page.locator("#pickErr").inner_text())
    tline = page.locator("#longTime").inner_text()
    check("an honest time line with a number of minutes and 'leave this page and come back'",
          "minute" in tline and "leave this page and come back" in tline and "takes about" in tline, tline)
    # the number changes the estimate
    page.fill("#shortsN", "5")
    t5 = page.locator("#longTime").inner_text()
    page.fill("#shortsN", "1")
    t1 = page.locator("#longTime").inner_text()
    check("more Shorts take longer in the time line", t5 != t1, f"{t1} / {t5}")
    page.fill("#shortsN", "9")
    check("9 Shorts: asked for a number from 1 to 5, button off", "1 to 5" in page.locator("#pickErr").inner_text() and page.is_disabled("#go"))
    page.fill("#shortsN", "")
    check("an empty number: the same, no crash", "1 to 5" in page.locator("#pickErr").inner_text() and page.is_disabled("#go"))
    page.fill("#shortsN", "3")
    check("back to 3: ready again", page.is_enabled("#go") and page.locator("#go").inner_text() == "Make my Shorts")
    page.screenshot(path=SHOTS / "lv-4-upload-ready.png", full_page=True)

    # ------------------------------------------------------------------ 2. the result page for a finished Shorts job
    print("2. Result page for a Shorts job", flush=True)
    if not _make_fixture(ctx, "web-lv-fixture", 3):
        check("an engine run in Shorts mode exists to build the result page from", False)
    else:
        page.goto(ctx.BASE + "/?job=web-lv-fixture")
        page.wait_for_function("!document.getElementById('done').classList.contains('hidden')", timeout=30000)
        body = page.inner_text("body")
        check("title is 'Your Shorts are ready'", page.locator("#readyTitle").inner_text() == "Your Shorts are ready")
        check("three players, each with 'Short k of 3'", page.locator("#players .pcard").count() == 3 and all(
            f"Short {k} of 3" in page.locator("#players").inner_text() for k in (1, 2, 3)))
        check("never 'Part N of M'", "Part 1 of" not in body and "Part 2 of" not in body and " parts" not in body.lower().replace("apart", ""), "")
        check("never the 'your video was long so it was split' explanation", "was long, so I split" not in body and "split it into" not in body)
        check("the note says Shorts were cut from the long video", "I cut 3 Shorts from your 7:28 video" in page.locator("#splitNote").inner_text(),
              page.locator("#splitNote").inner_text())
        check("each Short says where it came from (M:SS to M:SS of your video)",
              "From 3:10 to 3:52 of your video" in body and "From 4:10 to 4:52 of your video" in body, "")
        check("...and why, in her words", "Reason number 1" in body and "Reason number 3" in body)
        check("save buttons say 'Save Short 1'", "Save Short 1" in body or page.locator("#players .big").first.inner_text().startswith("Save"))
        check("tabs per Short exist and the first is chosen", page.locator("#partTabs .tab").count() == 3
              and page.locator("#partTabs .tab").first.get_attribute("aria-selected") == "true")
        page.locator('#partTabs .tab[data-k="2"]').click()
        page.wait_for_timeout(400)
        check("tapping Short 2 shows Short 2's own titles", "Short 2 title one" in page.locator("#copy").inner_text()
              and "Showing the words for Short 2 of 3" in page.locator("#copy").inner_text(), page.locator("#copy").inner_text()[:120])
        check("...and its platform captions are labelled with the Short", "(short 2)" in page.locator("#platforms").inner_text().lower(),
              page.locator("#platforms").inner_text()[:80])
        # the YouTube card
        expect(page.locator("#ytCard")).to_be_visible()
        yt = page.locator("#ytCard").inner_text()
        check("card 'For your long video on YouTube' is there", "For your long video on YouTube" in yt)
        kit = page.evaluate("fetch(`/api/jobs/web-lv-fixture/longform`).then(r => r.json())")
        check("it shows the first title idea and the others behind 'More title ideas'", kit["titles"][0] in yt and "More title ideas" in yt)
        check("the chapters are a readable list", page.locator("#ytCard .chapters li").count() == len(kit["chapters"]) and len(kit["chapters"]) >= 1,
              f"{page.locator('#ytCard .chapters li').count()} vs {len(kit['chapters'])}")
        check("a Copy button for each title, the description and the tags (one for the whole description)",
              page.locator("#ytCard .field").count() >= 3 and page.locator("#ytCard .field", has_text="Description").locator("button.copybtn").count() == 1)
        check("tags are shown", "tags" in yt.lower())
        srt = page.locator("#srtLink")
        check("'Download captions file (.srt)' is there", "Download captions file (.srt)" in srt.inner_text())
        with page.expect_download() as dl:
            srt.click()
        d = dl.value
        text = Path(d.path()).read_text(encoding="utf-8")
        check("the .srt downloads and has captions in it", d.suggested_filename.endswith(".srt") and "-->" in text, d.suggested_filename)
        check("'Before you post' still shows", page.locator("#checkCard").is_visible() and "Before you post" in page.locator("#checkCard").inner_text())
        check("the post pack card is there and names the long-video files", "captions file (.srt)" in page.locator("#packCard").inner_text())
        # the Change panel
        page.locator("#fbOpen").click()
        expect(page.locator("#fbCard")).to_be_visible()
        check("the Change panel offers 'How many Shorts?' set to 3", page.locator("#shortsGroup").is_visible() and page.input_value("#shortsBox") == "3")
        check("it does not offer clip matching or clip order", not page.locator("#matchGroup").is_visible() and not page.locator("#orderGroup").is_visible()
              and not page.locator("#startGroup").is_visible())
        check("captions options are still there", page.locator('.opt[data-k="size"]').first.is_visible())
        page.fill("#shortsBox", "2")
        check("choosing 2 Shorts says what will change and enables Remake", "2 Shorts" in page.locator("#summary").inner_text()
              and page.is_enabled("#remake"), page.locator("#summary").inner_text())
        page.fill("#shortsBox", "9")
        check("9 is refused in plain words and nothing will change", "1 to 5" in page.locator("#fbErr").inner_text()
              and page.locator("#summary").inner_text() == "", page.locator("#fbErr").inner_text())
        page.fill("#shortsBox", "3")
        page.locator('.opt[data-k="size"][data-v="1"]').click()
        check("bigger captions still work as a normal change", "bigger captions" in page.locator("#summary").inner_text())
        page.screenshot(path=SHOTS / "lv-5-result-change-panel.png", full_page=True)
        page.reload()
        page.wait_for_function("!document.getElementById('done').classList.contains('hidden')", timeout=30000)
        page.wait_for_timeout(800)
        page.screenshot(path=SHOTS / "lv-6-result-top.png")
        page.screenshot(path=SHOTS / "lv-7-result-full.png", full_page=True)
        # My videos
        page.goto(ctx.BASE + "/videos")
        page.wait_for_selector(".row")
        rows_text = page.inner_text("#list")
        check("My videos shows the job as one video with 3 Shorts", "3 Shorts from a long video" in rows_text, rows_text[:200])
        check("...with a Download button per Short", "Download Short 1 of 3" in rows_text and "Download Short 3 of 3" in rows_text)
        check("...and no 'Part'", "Part 1 of" not in rows_text)
        page.screenshot(path=SHOTS / "lv-8-my-videos.png", full_page=True)

    # ------------------------------------------------------------------ 3. one real run through the site
    print("3. One real run: Home Value sample, 2 Shorts, no AI (blank key)", flush=True)
    page.goto(ctx.BASE)
    page.locator("#modeLongL").click()
    page.set_input_files("#files", [str(sample)])
    expect(page.locator("#clips li.clip")).to_have_count(1)
    page.fill("#shortsN", "2")
    page.wait_for_timeout(1000)
    page.click("#go")
    t0 = time.time()
    shots_taken = set()
    while time.time() - t0 < ctx.MAKE_TIMEOUT / 1000:
        if page.locator("#done").is_visible() or page.locator("#failed").is_visible():
            break
        if page.locator("#working").is_visible():
            step = page.locator("#step").inner_text()
            for key, nm in (("Listening", "lv-9-working-listening"), ("Making Short", "lv-10-working-making")):
                if key in step and nm not in shots_taken:
                    shots_taken.add(nm)
                    page.screenshot(path=SHOTS / f"{nm}.png")
                    check(f"working screen shows '{step[:60]}' and an honest time line",
                          "minute" in page.locator("#waitNote").inner_text() or "being made first" in page.locator("#waitNote").inner_text(),
                          page.locator("#waitNote").inner_text())
        page.wait_for_timeout(1500)
    made = page.locator("#done").is_visible()
    check("the Shorts were made through the website", made, "" if made else page.locator("#failMsg").inner_text().replace("\n", " | "))
    if not made:
        return
    print(f"   (took {time.time() - t0:.0f}s)", flush=True)
    check("the working screen showed the listening step at some point", "lv-9-working-listening" in shots_taken, sorted(shots_taken))
    jid = page.evaluate("jobId")
    st = page.evaluate("fetch(`/api/jobs/${jobId}`).then(r => r.json())")
    rep = st["report"]
    check("the report is in Shorts mode with 1 or 2 Shorts", rep.get("mode") == "highlights" and 1 <= len(rep["parts"]) <= 2, rep.get("mode"))
    check("title is 'Your Shorts are ready'", page.locator("#readyTitle").inner_text() == "Your Shorts are ready")
    page.wait_for_function("document.querySelector('#players video').readyState >= 1", timeout=30000)
    dims = page.evaluate("[document.querySelector('#players video').videoWidth, document.querySelector('#players video').videoHeight, document.querySelector('#players video').duration]")
    check("the first Short is vertical 1080x1920 and under 60 s", dims[0] == 1080 and dims[1] == 1920 and dims[2] < 60, dims)
    expect(page.locator("#ytCard")).to_be_visible()
    check("the YouTube card shows 3 title ideas and chapters", page.locator("#ytCard .field.title").count() == 3
          and page.locator("#ytCard .chapters li").count() >= 1, page.locator("#ytCard").inner_text()[:200])
    check("the post pack downloads with the long video's files", page.evaluate(
        "fetch(`/api/jobs/${jobId}/postpack`).then(r => r.arrayBuffer()).then(b => b.byteLength)") > 100000)
    body = page.inner_text("body")
    check("no 'Part N of M' and no AI claim on a no-AI run", "Part 1 of" not in body and "AI" not in page.locator("#ytSrc").inner_text())
    page.screenshot(path=SHOTS / "lv-11-real-result-top.png")
    page.screenshot(path=SHOTS / "lv-12-real-result-full.png", full_page=True)
    # the lost page: coming back to it still works
    page.goto(ctx.BASE + f"/?job={jid}")
    page.wait_for_function("!document.getElementById('done').classList.contains('hidden')", timeout=30000)
    check("opening the finished job again shows the same result", page.locator("#readyTitle").inner_text() == "Your Shorts are ready")
    page.set_viewport_size({"width": 1280, "height": 800})
