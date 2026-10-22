"""Screens lane checks: My videos, sign-in and Team page, in a real phone-sized browser.
Works on its own:  E2E_PORT=8032 E2E_ONLY=screens_pages venv/Scripts/python tests/e2e_site.py
It seeds small made-up finished videos (a 1-second test picture) in the throwaway data folder, plus a second throwaway
account (not an admin) to see what a non-admin sees. Everything it adds is removed again at the end."""
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

J = {"content-type": "application/json"}


def run(page, ctx):
    out = ctx.DATA / "out"
    made = []
    old = page
    page = old.context.new_page()   # a fresh page: an earlier part can leave a one-shot dialog handler on the shared one
    page.on("pageerror", lambda e: ctx.check("screens: no JavaScript error on the page", False, str(e)[:200]))
    try:
        _run(page, ctx, out, made)
    finally:
        page.close()   # leave the shared throwaway site as found: other parts read every copy.json they can see
        for j in made:
            shutil.rmtree(out / j, ignore_errors=True)
            for ext in ("owner", "parent", "source"):
                (out / f"{j}.{ext}").unlink(missing_ok=True)
        (out / "failures.jsonl").unlink(missing_ok=True)


def _run(page, ctx, out, made):
    BASE, SH, check, api = ctx.BASE, ctx.SHOTS, ctx.check, page.request
    sys.path.insert(0, str(ctx.ROOT))
    from video_tool.config import FFMPEG
    check("screens: page is signed in as the admin", api.get(f"{BASE}/api/me").json()["admin"] is True)

    tiny = ctx.DATA / "scr-tiny.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=270x480:d=2:r=15", "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", str(tiny)], check=True)

    def seed(job, owner, title, parent=None, parts=1, warnings=0, minutes_ago=10):
        d = out / job
        d.mkdir(parents=True, exist_ok=True)
        names = [f"final_part{i + 1}.mp4" for i in range(parts)] if parts > 1 else ["final.mp4"]
        for n in names:
            shutil.copy(tiny, d / n)
        rep = {"job_id": job, "mode": "voice", "parts": [{"file": n} for n in names], "length": 2.0, "seconds_to_build": 60,
               "warnings": [{"clip": None, "kind": "shake", "message": "x"}] * warnings, "timeline": [{}, {}, {}]}
        (d / "report.json").write_text(json.dumps(rep), encoding="utf-8")
        (d / "copy.json").write_text(json.dumps({"titles": [title]}), encoding="utf-8")
        (out / f"{job}.owner").write_text(owner, encoding="utf-8")
        if parent:
            (out / f"{job}.parent").write_text(parent, encoding="utf-8")
        t = time.time() - minutes_ago * 60
        os.utime(d / "report.json", (t, t))
        made.append(job)

    # ---- a second, ordinary account ----
    pw2 = secrets.token_urlsafe(10)
    check("screens: second account made", api.post(f"{BASE}/api/admin/users", data=json.dumps({"username": "scrsecond", "password": pw2}), headers=J).ok)
    ctx2 = page.context.browser.new_context(viewport={"width": 430, "height": 900})
    check("screens: second account signs in", ctx2.request.post(f"{BASE}/api/login", data=json.dumps({"username": "scrsecond", "password": pw2}), headers=J).ok)
    page2 = ctx2.new_page()

    seed("scr-a1", "tester", "Seed Alpha tour", minutes_ago=300)
    seed("scr-a2", "tester", "Seed Alpha tour", parent="scr-a1", warnings=1, minutes_ago=200)
    seed("scr-p1", "tester", "Seed Parts talk", parts=2, minutes_ago=100)
    seed("scr-s1", "scrsecond", "Second person video", minutes_ago=50)

    # ---- My videos: the list comes first, one row per video, numbers tucked away ----
    page.goto(BASE + "/videos")
    page.wait_for_selector(".row")
    page.wait_for_function("[...document.querySelectorAll('img.thumb')].every(i => i.complete)", timeout=30000)
    titles = page.locator(".row .title").all_inner_texts()
    check("My videos: newest version only by default", titles.count("Seed Alpha tour") == 1 and "Seed Parts talk" in titles, titles)
    check("My videos: admin sees only their own by default (Only mine)", "Second person video" not in titles, titles)
    check("My videos: the numbers are tucked away under 'Your numbers'",
          not page.locator("#sTotal").is_visible() and page.locator("#numbersFold summary").inner_text() == "Your numbers")
    fams = len({v["first_version"] for v in api.get(f"{BASE}/api/videos").json()["videos"] if v["owner"] == "tester"})
    check("My videos: no search or sort until there are 6 videos", (fams >= 6) == page.locator("#tools").is_visible(), fams)
    check("My videos: 'Only mine' filter shown to the admin", page.locator("#owner").is_visible()
          and "Only mine" in page.locator("#owner").inner_text())
    check("My videos: one Download button per part, named by part",
          page.locator(".row", has_text="Seed Parts talk").locator("a[download]").all_inner_texts() == ["Download Part 1 of 2", "Download Part 2 of 2"])
    check("My videos: a single video has a plain Download", page.locator(".row", has_text="Seed Alpha tour").locator("a[download]").all_inner_texts() == ["Download"])
    row_a = page.locator(".row", has_text="Seed Alpha tour").first
    chips = row_a.locator(".chip").all_inner_texts()
    check("My videos: plain chips (Things to check, Version 2, Draft)", "Things to check" in chips and "Version 2" in chips and "Draft" in chips
          and not any("heads-up" in c or " of 2" in c for c in chips), chips)
    check("My videos: the saved-here note", "saved here until you delete them" in page.locator(".note").inner_text())
    page.screenshot(path=SH / "screens-1-list.png", full_page=True)

    page.click("#older")
    check("My videos: 'Show earlier versions' shows the first version", page.locator(".row", has_text="Seed Alpha tour").count() == 2
          and page.locator(".row.older", has_text="Seed Alpha tour").locator(".chip", has_text="Version 1").count() == 1)
    page.screenshot(path=SH / "screens-2-older.png", full_page=True)
    page.click("#older")
    check("My videos: 'Hide earlier versions' puts it away again", page.locator(".row", has_text="Seed Alpha tour").count() == 1)

    page.click("#numbersFold summary")
    check("Your numbers: build time and remakes are shown to the admin", page.locator("#sBuild").is_visible() and page.locator("#sRemakes").is_visible())
    page.screenshot(path=SH / "screens-3-numbers.png", full_page=True)
    page.click("#numbersFold summary")

    # ---- posting log ----
    row_p = page.locator(".row", has_text="Seed Parts talk")
    row_p.locator("button", has_text="Mark reviewed").click()
    page.locator(".row", has_text="Seed Parts talk").locator(".chip", has_text="Reviewed").wait_for()
    check("Posting: 'Mark reviewed' shows Reviewed", True)
    row_p = page.locator(".row", has_text="Seed Parts talk")
    row_p.locator("button", has_text="Mark posted").click()
    page.select_option("[data-plat]", "tiktok")
    page.screenshot(path=SH / "screens-4-mark-posted.png", full_page=True)
    page.click("[data-save]")
    chip = page.locator(".row", has_text="Seed Parts talk").locator(".chip.posted")
    chip.wait_for()
    check("Posting: 'Mark posted' shows Posted to TikTok", chip.inner_text() == "Posted to TikTok")
    check("Posting: the server remembers it", api.get(f"{BASE}/api/jobs/scr-p1/posting").json()["status"] == "posted")
    page.wait_for_function("document.getElementById('weekText').innerText.includes('Posted')")
    wt = page.locator("#weekText").inner_text()
    check("Week strip: 'Posted N of 3-5 this week'", "of 3–5 this week" in wt and "Posted 0" not in wt, wt)
    page.screenshot(path=SH / "screens-5-week.png", full_page=True)
    reminders = page.locator("#free a[data-day]")
    if reminders.count():
        with page.expect_download() as dl:
            reminders.first.click()
        path = dl.value.path()
        ics = Path(path).read_text(encoding="utf-8")
        check("Remind me: downloads a calendar file with her video's name", "BEGIN:VCALENDAR" in ics and "Post your video" in ics and dl.value.suggested_filename.endswith(".ics"), dl.value.suggested_filename)
    else:
        check("Remind me: nothing to remind on a week with no free days left (Sunday evening)", "free" not in wt)
    page.locator(".row", has_text="Seed Parts talk").locator("[data-set=draft]").click()
    page.locator(".row", has_text="Seed Parts talk").locator(".chip", has_text="Draft").wait_for()
    check("Posting: 'Back to draft' undoes it", True)

    # ---- delete ----
    seen = []
    page.once("dialog", lambda d: (seen.append(d.message), d.dismiss()))
    page.locator(".row", has_text="Seed Parts talk").locator("[data-del]").click()
    check("Delete: plain confirm text", seen and seen[0] == "This removes the video and its saved clips. It can't be undone.", seen)
    check("Delete: cancelling keeps the video", api.get(f"{BASE}/api/jobs/scr-p1").ok)
    page.once("dialog", lambda d: d.accept())
    page.locator(".row", has_text="Seed Parts talk").locator("[data-del]").click()
    page.wait_for_function("!document.body.innerText.includes('Seed Parts talk')")
    check("Delete: it is gone from the list and from the computer", api.get(f"{BASE}/api/jobs/scr-p1").status == 404)
    page.once("dialog", lambda d: d.accept())
    page.locator(".row", has_text="Seed Alpha tour").locator("[data-del]").click()
    page.wait_for_function("!document.body.innerText.includes('Seed Alpha tour')")
    check("Delete: removing a video removes every version of it", api.get(f"{BASE}/api/jobs/scr-a1").status == 404 and api.get(f"{BASE}/api/jobs/scr-a2").status == 404)

    # ---- six or more videos: search and sort appear ----
    for i in range(6):
        seed(f"scr-m{i}", "tester", f"Many {i}", minutes_ago=30 + i)
    page.goto(BASE + "/videos")
    page.wait_for_selector(".row")
    check("My videos: search and sort appear with 6 or more videos", page.locator("#tools").is_visible())
    page.fill("#q", "Many 3")
    check("My videos: search finds a title", page.locator(".row .title").all_inner_texts() == ["Many 3"])
    page.screenshot(path=SH / "screens-6-search.png", full_page=True)
    page.fill("#q", "")
    page.select_option("#owner", "*")
    check("My videos: 'Everyone's videos' shows the second account too (with a name)",
          "Second person video" in page.locator(".list").inner_text() and "scrsecond" in page.locator(".list").inner_text())
    page.screenshot(path=SH / "screens-7-everyone.png", full_page=True)

    # ---- what a non-admin sees ----
    page2.goto(BASE + "/videos")
    page2.wait_for_selector(".row")
    check("Non-admin: sees only their own video", page2.locator(".row .title").all_inner_texts() == ["Second person video"])
    check("Non-admin: no Team page link, no Only-mine filter",
          "Team page" not in page2.locator("#who").inner_text() and not page2.locator("#owner").is_visible())
    page2.click("#numbersFold summary")
    check("Non-admin: build time and remakes are hidden", not page2.locator("#sBuild").is_visible() and not page2.locator("#sRemakes").is_visible()
          and page2.locator("#sTotal").is_visible())
    page2.screenshot(path=SH / "screens-8-nonadmin.png", full_page=True)
    check("Non-admin: the Team page is refused", page2.request.get(f"{BASE}/admin").status == 403 or "Team page" not in page2.request.get(f"{BASE}/admin").text())

    # ---- account: change password with the peek eye, and Back to normal ----
    page2.click("#acct summary")
    eye = page2.locator('.peek[data-for="pwNew"]')
    page2.fill("#pwNew", "abcdefgh1")
    box = eye.bounding_box()
    page2.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page2.mouse.down()
    held = page2.get_attribute("#pwNew", "type")
    page2.mouse.up()
    check("Account: every password box has a hold-to-peek eye",
          held == "text" and page2.get_attribute("#pwNew", "type") == "password" and page2.locator("#acct .peek").count() == 3)
    page2.fill("#pwOld", "wrong wrong")
    page2.fill("#pwNew2", "abcdefgh1")
    page2.click("#pwBtn")
    page2.wait_for_function("document.getElementById('pwMsg').innerText.length > 0")
    msg = page2.locator("#pwMsg").inner_text()
    check("Account: a wrong current password gets a plain message", "current password" in msg and "Traceback" not in msg, msg)
    page2.fill("#pwOld", pw2)
    page2.fill("#pwNew2", "different1")
    page2.click("#pwBtn")
    check("Account: two new passwords that differ are caught", "don't match" in page2.locator("#pwMsg").inner_text())
    page2.fill("#pwNew2", "abcdefgh1")
    page2.click("#pwBtn")
    page2.wait_for_function("document.getElementById('pwMsg').innerText.includes('Done')")
    page2.screenshot(path=SH / "screens-9-account.png", full_page=True)
    fresh = page.context.browser.new_context()
    check("Account: the new password signs in and the old one does not",
          fresh.request.post(f"{BASE}/api/login", data=json.dumps({"username": "scrsecond", "password": "abcdefgh1"}), headers=J).ok
          and fresh.request.post(f"{BASE}/api/login", data=json.dumps({"username": "scrsecond", "password": pw2}), headers=J).status == 401)
    fresh.close()
    check("Account: she stays signed in on this phone", page2.request.get(f"{BASE}/api/me").json()["user"] == "scrsecond")
    page2.once("dialog", lambda d: d.accept())
    page2.click("#resetPrefs")
    page2.wait_for_function("document.getElementById('prefsMsg').innerText.length > 0")
    check("Account: 'Back to normal' works", "normal settings" in page2.locator("#prefsMsg").inner_text(), page2.locator("#prefsMsg").inner_text())

    # ---- sign-in page ----
    anon = page.context.browser.new_context(viewport={"width": 430, "height": 900})
    lp = anon.new_page()
    lp.goto(BASE + "/login")
    check("Sign-in: username box is set up for phones", lp.get_attribute("#u", "autocomplete") == "username"
          and lp.get_attribute("#u", "autocorrect") == "off" and lp.get_attribute("#u", "spellcheck") == "false"
          and lp.get_attribute("#p", "autocomplete") == "current-password")
    check("Sign-in: a help line is always there", "Forgot your password" in lp.locator("#help").inner_text() and lp.locator("#help").is_visible())
    check("Sign-in: phone-app files are linked", lp.locator('link[rel=manifest]').count() == 1)
    lp.fill("#u", "tester")
    lp.fill("#p", "nope-nope")
    lp.click("#b")
    lp.wait_for_function("document.getElementById('e').innerText.length > 0")
    check("Sign-in: wrong password message is plain", lp.locator("#e").inner_text() == "That username or password isn't right. Try again.", lp.locator("#e").inner_text())
    lp.screenshot(path=SH / "screens-10-login-error.png", full_page=True)
    anon.close()

    # ---- Team page ----
    (out / "failures.jsonl").write_text(json.dumps({"time": "2026-01-02T10:00:00", "job": "scr-m1", "user": "tester",
                                                    "shown": "One of your clips couldn't be read.", "detail": "ValueError: made up for the test"}) + "\n", encoding="utf-8")
    page.goto(BASE + "/admin")
    page.wait_for_selector("#health .fact")
    check("Team page: named 'Team page'", page.title().startswith("Team page") and page.locator("h1").inner_text() == "Team page")
    check("Team page: health facts (disk, last video, AI helper)", all(w in page.locator("#health").inner_text() for w in ("GB", "last video made", "AI helper")))
    page.wait_for_selector("#fails .card")
    check("Team page: failed videos card shows what she saw and a technical fold",
          "couldn't be read" in page.locator("#fails").inner_text() and page.locator("#fails details summary").inner_text() == "Technical detail")
    check("Team page: the developer sentence is tucked under 'For the developer'", not page.locator("text=Paste this list into a Claude session").is_visible())
    page.screenshot(path=SH / "screens-11-team.png", full_page=True)
    page.fill("#nu", "scrsecond")
    page.fill("#np", "longenough1")
    page.click("#add")
    check("Team page: adding a name that exists points to 'Reset a password'", "Reset a password" in page.locator("#addMsg").inner_text(), page.locator("#addMsg").inner_text())
    page.fill("#nu", "scrthird")
    page.click("#add")
    page.wait_for_function("document.getElementById('addMsg').innerText.includes('can now sign in')")
    page.wait_for_function("[...document.querySelectorAll('#rn option')].some(o => o.value === 'scrthird')")
    check("Team page: a new person can be added", True)
    page.select_option("#rn", "scrthird")
    page.fill("#rp", "freshpass22")
    eye = page.locator('.peek[data-for="rp"]')
    box = eye.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    held = page.get_attribute("#rp", "type")
    page.mouse.up()
    check("Team page: both password boxes have the peek eye", held == "text" and page.locator(".peek").count() == 2)
    page.click("#reset")
    page.wait_for_function("document.getElementById('resetMsg').innerText.includes('changed')")
    t = page.context.browser.new_context()
    check("Team page: the reset password works", t.request.post(f"{BASE}/api/login", data=json.dumps({"username": "scrthird", "password": "freshpass22"}), headers=J).ok)
    t.close()
    page.screenshot(path=SH / "screens-12-team-done.png", full_page=True)
    ctx2.close()
    api.get(f"{BASE}/api/me")
