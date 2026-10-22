"""End-to-end test of the website in a real browser (Playwright).

    venv\\Scripts\\python tests\\e2e_site.py            (add --show to watch the browser)

It starts its OWN copy of the site on port 8011 with a throwaway data folder and a throwaway
test account, so real accounts, videos and saved settings are never touched. The AI key is
blanked for the run, so it costs nothing (titles come from the no-AI fallback).

What it does, the way Alex would: sign in (and hold the peek eye), upload the real recipe clips
plus voice-over, wait for the video, check the result page, ask for bigger captions and remake,
open My videos, then upload a broken clip and check the error is readable and nothing is lost.
"""
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("E2E_PORT", "8011"))   # parallel runs (several agents) each pick their own port
BASE = f"http://localhost:{PORT}"
AI = "--ai" in sys.argv   # keep the real API key and test only the AI path (costs a few cents)
SAMPLE = ROOT / "samples" / "recipe"
SHOTS = ROOT / "tests" / "shots"
MAKE_TIMEOUT = 12 * 60 * 1000   # a short video takes about a minute here, a long talking one 4-9

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  - ' + str(detail) if detail else ''}", flush=True)


def start_server(data):
    env = {**os.environ, "ALEX_DATA_DIR": str(data), "ALEX_HOSTED": "1",   # hosted = login required, like the public link
           "ADMIN_USER": "tester", "ADMIN_PASSWORD": PASSWORD, "ANTHROPIC_API_KEY": "", "APP_PASSWORD": ""}
    if AI:   # the real key from .env: this run costs a few cents
        env.pop("ANTHROPIC_API_KEY")
    log = open(data / "server.log", "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "web.app:app", "--port", str(PORT)],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(60):
        try:
            if urllib.request.urlopen(f"{BASE}/healthz", timeout=2).status == 200:
                return proc
        except OSError:
            time.sleep(0.5)
    proc.kill()
    raise SystemExit("Test server didn't start. See " + str(data / "server.log"))


def wait_for_video(page):
    """Wait for the result (or the error card) and say which one showed."""
    page.wait_for_function("""() => !document.getElementById('done').classList.contains('hidden')
                                 || !document.getElementById('failed').classList.contains('hidden')""",
                           timeout=MAKE_TIMEOUT)
    return page.locator("#done").is_visible()


def run_parts(page, only=None):
    """Plug-in checks: every tests/e2e_part_<name>.py defining run(page, ctx) is run, signed in, after the base flow.
    E2E_ONLY=<name> runs just that part (fast loop while building it). A part must work on its own: it does its own
    page.goto / uploads and must not depend on what an earlier part left behind."""
    import importlib.util
    ctx = type("Ctx", (), dict(BASE=BASE, ROOT=ROOT, SHOTS=SHOTS, SAMPLE=SAMPLE, PASSWORD=PASSWORD, DATA=DATA, expect=staticmethod(expect),
                               check=staticmethod(check), wait_for_video=staticmethod(wait_for_video),
                               video_facts=staticmethod(video_facts), MAKE_TIMEOUT=MAKE_TIMEOUT))
    for f in sorted((ROOT / "tests").glob("e2e_part_*.py")):
        name = f.stem[len("e2e_part_"):]
        if only and only != name:
            continue
        spec = importlib.util.spec_from_file_location(f.stem, f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        print(f"Part: {name}", flush=True)
        try:
            mod.run(page, ctx)
        except Exception as e:   # one part failing must not hide the others
            check(f"part '{name}' ran to the end", False, f"{type(e).__name__}: {str(e).splitlines()[0][:200]}")
            page.screenshot(path=SHOTS / f"crash-{name}.png", full_page=True)


def run(page):
    # ---- 1. sign in ----
    print("1. Sign in", flush=True)
    page.goto(BASE)
    check("signed-out visitor is sent to the login page", "/login" in page.url, page.url)
    page.fill("#u", "tester")
    page.fill("#p", PASSWORD)
    eye = page.locator('.peek[data-for="p"]')
    box = eye.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    shown = page.get_attribute("#p", "type")
    page.screenshot(path=SHOTS / "1-login-peek-held.png")
    page.mouse.up()
    check("peek eye shows the password only while held", shown == "text" and page.get_attribute("#p", "type") == "password",
          f"held={shown}, released={page.get_attribute('#p', 'type')}")
    page.click("#b")
    page.wait_for_url(BASE + "/")
    check("signed in and on the upload page", page.locator("#drop").is_visible())
    if AI:
        run_ai_tour(page)
        return
    if os.environ.get("E2E_ONLY"):
        run_parts(page, os.environ["E2E_ONLY"])
        return

    # ---- 2. upload real clips + voice-over ----
    print("2. Upload the recipe clips and voice-over", flush=True)
    files = sorted(SAMPLE.glob("*.mp4")) + [SAMPLE / "voice.m4a"]
    page.set_input_files("#files", [str(f) for f in files])
    expect(page.locator("#clips li.clip")).to_have_count(3)
    check("3 clips listed in order", page.locator("#clips li.clip span:not(.dur)").all_inner_texts() == ["01.mp4", "02.mp4", "03.mp4"])
    check("voice-over recognised", "voice.m4a" in page.locator("#voice").inner_text())
    check("'remember for all future videos' starts unticked", not page.is_checked("#remember"))
    page.screenshot(path=SHOTS / "2-files-chosen.png")
    page.click("#go")
    t0 = time.time()
    made = wait_for_video(page)
    check("video was made", made, "" if made else page.locator("#failMsg").inner_text())
    if not made:
        return
    print(f"   (took {time.time() - t0:.0f}s)", flush=True)

    # ---- 3. the result page ----
    print("3. Result page", flush=True)
    video = page.locator("#players video").first
    page.wait_for_function("document.querySelector('#players video').readyState >= 1", timeout=30000)
    dur = page.evaluate("document.querySelector('#players video').duration")
    check("video plays and is under 60 seconds", 5 < dur < 60, f"{dur:.1f}s")
    w, h = page.evaluate("[document.querySelector('#players video').videoWidth, document.querySelector('#players video').videoHeight]")
    check("video is vertical 1080x1920", (w, h) == (1080, 1920), f"{w}x{h}")
    check("'Your video is ready' heading", page.locator(".ready-bar h2").inner_text() == "Your video is ready")
    check("titles are shown", page.locator("#copy .field.title").count() == 3)
    check("captions for 4 platforms (a tab for each, one caption showing)", page.locator("#platformTabs .tab").count() == 4 and page.locator("#platforms .field").count() == 1)
    dl = page.locator("#players a[download]").first.get_attribute("href")
    resp = page.request.get(BASE + dl)
    check("download link returns an MP4", resp.ok and resp.headers.get("content-type") == "video/mp4" and len(resp.body()) > 100_000,
          f"{len(resp.body()) / 1e6:.1f} MB")
    page.screenshot(path=SHOTS / "3-result.png", full_page=True)

    # ---- 4. feedback -> remake ----
    print("4. Feedback: bigger captions, remake", flush=True)
    first_job = page.evaluate("jobId")
    size_before = page.evaluate("cur.caption_size")
    check("the change buttons are tucked behind one button", page.locator("#fbOpen").is_visible()
          and not page.locator("#fbCard").is_visible())
    page.click("#fbOpen")
    page.click('.opt[data-k="size"][data-v="1"]')
    check("change is summarised before remaking", "bigger captions" in page.locator("#summary").inner_text())
    page.click("#remake")
    page.wait_for_function("() => !document.getElementById('working').classList.contains('hidden')", timeout=15000)
    remade = wait_for_video(page)
    check("remake finished", remade, "" if remade else page.locator("#failMsg").inner_text())
    if remade:
        check("captions are bigger in the new version", page.evaluate("cur.caption_size") > size_before,
              f"{size_before} -> {page.evaluate('cur.caption_size')}")
        check("it is a new version, not the same video", page.evaluate("jobId") != first_job)
        check("'Version 2' label", "Version 2" in page.locator("#versionTag").inner_text())
        page.screenshot(path=SHOTS / "4-remade.png", full_page=True)

    # ---- 5. My videos ----
    print("5. My videos dashboard", flush=True)
    page.goto(BASE + "/videos")
    page.wait_for_selector(".row")
    check("dashboard shows the newest version only", page.locator(".row").count() == 1, page.locator(".row").count())
    page.click("#older")
    check("'Show earlier versions' brings back the first one", page.locator(".row").count() == 2, page.locator(".row").count())
    check("a remake isn't counted as a new video", page.locator("#sTotal").text_content() == "1", page.locator("#sTotal").text_content())
    check("version chips shown", "Version 2" in page.locator(".list").inner_text() and "Version 1" in page.locator(".list").inner_text())
    page.wait_for_function("[...document.querySelectorAll('img.thumb')].every(i => i.complete && i.naturalWidth > 0)", timeout=30000)
    check("thumbnails load", True)
    page.screenshot(path=SHOTS / "5-my-videos.png", full_page=True)
    page.locator(".row a.btn", has_text="Open").first.click()
    page.wait_for_function("() => !document.getElementById('done').classList.contains('hidden')", timeout=20000)
    check("'Open' brings the video back", page.locator("#players video").count() == 1)

    # ---- 6. a broken clip: readable error, nothing lost ----
    print("6. Broken clip", flush=True)
    broken = Path(tempfile.mkdtemp()) / "Kitchen walk-through.mp4"
    broken.write_bytes((SAMPLE / "03.mp4").read_bytes()[:200_000])
    page.goto(BASE)
    page.set_input_files("#files", [str(SAMPLE / "03.mp4"), str(broken)])
    expect(page.locator("#clips li.clip")).to_have_count(2)
    asked = []
    page.once("dialog", lambda d: (asked.append(d.message), d.accept()))   # there is no browser pop-up any more
    check("the empty voice-over row explains what happens instead of a pop-up", "sound recorded in your clips" in page.locator("#voice").inner_text())
    page.click("#go")
    check("no browser pop-up for a video with no voice-over", not asked, asked[0][:60] if asked else "")
    made = wait_for_video(page)
    msg = page.locator("#failMsg").inner_text() if not made else ""
    check("broken clip is reported, not turned into a bad video", not made)
    check("error names her file, with no folder paths", "Kitchen walk-through.mp4" in msg and "C:\\" not in msg
          and "Users" not in msg and "Traceback" not in msg, msg.replace("\n", " | "))
    page.wait_for_timeout(500)   # let the card finish fading in
    page.screenshot(path=SHOTS / "6-error.png")
    page.click("#retry")
    check("her clips are still in the list after the error", page.locator("#clips li.clip").count() == 2)

    # ---- 7. someone else can't see her videos ----
    print("7. Privacy", flush=True)
    anon = page.context.browser.new_context()
    r = anon.request.get(f"{BASE}/api/jobs/{first_job}")
    check("signed-out request for her video is refused", r.status == 401, r.status)
    anon.close()

    run_parts(page)
    if "--full" in sys.argv:
        run_tour(page)
        run_talking(page)


def run_ai_tour(page):
    """The tour with the real API key: Claude times and reorders the clips and writes the copy."""
    print("AI run: house tour with Claude (uses the real key)", flush=True)
    tour = ROOT / "samples" / "tour"
    clips = sorted(tour.glob("*.mp4"))
    page.set_input_files("#files", [str(f) for f in clips] + [str(tour / "voice.m4a")])
    expect(page.locator("#clips li.clip")).to_have_count(len(clips))
    page.click("#go")
    t0 = time.time()
    made = wait_for_video(page)
    check("tour video was made with AI on", made, "" if made else page.locator("#failMsg").inner_text().replace("\n", " | "))
    if not made:
        return
    print(f"   (took {time.time() - t0:.0f}s)", flush=True)
    status = page.evaluate("fetch(`/api/jobs/${jobId}`).then(r => r.json())")
    report, copy = status["report"], status.get("copy") or {}
    cm = report.get("clip_match") or {}
    check("Claude scored the clips against her words", cm.get("score_matched") is not None, cm.get("reason") or f"cost ${cm.get('cost_usd')}")
    check("clip matching was used", cm.get("used") is True, cm.get("reason", ""))
    moved = report.get("moved") or []
    flagged = [w.get("message", "")[:70] for w in report.get("warnings", []) if "looks like the part" in w.get("message", "")]
    print(f"   scores: her order {cm.get('score_matched')}, reordered {cm.get('score_reordered')}, "
          f"even split {cm.get('score_even')}; moved {len(moved)}, flagged {len(flagged)}", flush=True)
    # Claude's scores vary a little from run to run, so a reorder isn't guaranteed. What must hold:
    # a clip that looks out of place is either moved or flagged, never both and never silently ignored.
    notes = page.locator("#notes").inner_text()
    check("the page shows the moves it made", not moved or "Moved" in notes or "Reordered" in notes, f"{len(moved)} moved")
    check("the page shows the clips it flagged", not flagged or "looks like the part" in notes, f"{len(flagged)} flagged")
    used = [t["clip"] for t in report.get("timeline", [])]
    check("every clip still appears exactly once", sorted(used) == [c.name for c in clips], used)
    check("copy was written by Claude, not the fallback", not copy.get("source"), copy.get("source", "AI"))
    if copy.get("source"):   # say why, from the job's own log (the data folder is deleted after the run)
        job = page.evaluate("jobId")
        log = (DATA / "out" / f"{job}.log").read_text(encoding="utf-8", errors="replace")
        for line in log.splitlines():
            if any(k in line for k in ("writer", "AI ", "broke", "Writing", "rules", " - ")):
                print("   log:", line.strip()[:300], flush=True)
    text = " ".join([*copy.get("titles", []), copy.get("hook", ""), copy.get("description", ""), *(copy.get("captions") or {}).values()])
    check("3 titles and 4 platform captions", len(copy.get("titles", [])) == 3 and len(copy.get("captions") or {}) == 4)
    check("hook is 8 words or fewer", 0 < len(copy.get("hook", "").split()) <= 8, copy.get("hook"))
    check("no mention of schools (Fair Housing)", "school" not in text.lower())
    check("YouTube caption ends up with #Shorts", "#shorts" in (copy.get("captions") or {}).get("youtube_shorts", "").lower())
    facts = video_facts(page)
    check("one vertical video under 60s", len(facts) == 1 and facts[0][0] < 60 and (facts[0][1], facts[0][2]) == (1080, 1920), facts)
    print("   title 1:", copy.get("titles", ["?"])[0], flush=True)
    page.screenshot(path=SHOTS / "ai-tour.png", full_page=True)

    # The opt-in "double-check": Claude scores the clips twice and the two are averaged
    print("AI run: remake with the clip matching double-checked", flush=True)
    check("first video scored the clips once", cm.get("passes") == 1, cm.get("passes"))
    page.click("#fbOpen")
    page.click("#moreOpts summary")
    check("double-check is offered and off by default", "Double-check" in page.locator("#twiceToggle").inner_text())
    page.click("#twiceToggle")
    check("the summary says what will change", "double-check clip matching" in page.locator("#summary").inner_text())
    page.click("#remake")
    page.wait_for_function("() => !document.getElementById('working').classList.contains('hidden')", timeout=15000)
    remade = wait_for_video(page)
    check("double-checked remake finished", remade, "" if remade else page.locator("#failMsg").inner_text())
    if remade:
        r2 = page.evaluate("fetch(`/api/jobs/${jobId}`).then(r => r.json()).then(j => j.report)")
        cm2 = r2.get("clip_match") or {}
        check("the remake scored the clips twice", cm2.get("passes") == 2 and (cm2.get("cost_usd") or 0) > 0,
              f"passes {cm2.get('passes')}, cost ${cm2.get('cost_usd')}")
        check("every clip still appears exactly once after the remake",
              sorted(t["clip"] for t in r2.get("timeline", [])) == [c.name for c in clips])
        page.click("#fbOpen")
        page.click("#moreOpts summary")
        check("the button now offers to switch it back", "once" in page.locator("#twiceToggle").inner_text())


def video_facts(page):
    """[(seconds, width, height)] for every player on the result page."""
    page.wait_for_function("[...document.querySelectorAll('#players video')].every(v => v.readyState >= 1)", timeout=60000)
    return page.evaluate("[...document.querySelectorAll('#players video')].map(v => [v.duration, v.videoWidth, v.videoHeight])")


def run_tour(page):
    """Her 11-clip house tour with a voice-over: many clips, one under-60s video."""
    print("8. House tour: 11 clips + voice-over", flush=True)
    tour = ROOT / "samples" / "tour"
    clips = sorted(tour.glob("*.mp4"))
    page.goto(BASE)
    page.set_input_files("#files", [str(f) for f in clips] + [str(tour / "voice.m4a")])
    expect(page.locator("#clips li.clip")).to_have_count(len(clips))
    check("all 11 tour clips listed in order", page.locator("#clips li.clip span:not(.dur)").all_inner_texts() == [c.name for c in clips])
    page.click("#go")
    t0 = time.time()
    made = wait_for_video(page)
    check("tour video was made", made, "" if made else page.locator("#failMsg").inner_text().replace("\n", " | "))
    if not made:
        return
    print(f"   (took {time.time() - t0:.0f}s)", flush=True)
    facts = video_facts(page)
    check("tour is one video under 60 seconds", len(facts) == 1 and 20 < facts[0][0] < 60, [round(f[0], 1) for f in facts])
    check("tour is vertical 1080x1920", all((w, h) == (1080, 1920) for _, w, h in facts))
    notes = page.locator("#notes").inner_text()
    check("says extra footage was cut", "unused footage" in notes, notes.replace("\n", " | ")[:160])
    check("tour has titles and 4 platform captions", page.locator("#copy .field.title").count() == 3
          and page.locator("#platformTabs .tab").count() == 4)
    report = page.evaluate("fetch(`/api/jobs/${jobId}`).then(r => r.json()).then(j => j.report)")
    used = [t["clip"] for t in report.get("timeline", [])]
    check("every tour clip appears in the video", len(used) == len(clips), f"{len(used)} of {len(clips)} clips used")
    ends = [t["end"] for t in report.get("timeline", [])]
    check("clip times run in order and end with the voice", ends == sorted(ends) and abs(ends[-1] - report["length"]) < 0.5,
          f"last clip ends {ends[-1] if ends else '?'}s, video {report['length']}s")
    page.screenshot(path=SHOTS / "8-tour.png", full_page=True)


def run_talking(page):
    """Her 150s talking-to-camera video, no voice-over: uses the clip's own sound and splits into parts."""
    print("9. Talking to camera: one long clip, no voice-over", flush=True)
    clip = ROOT / "samples" / "tips" / "Home Value.mp4"
    page.goto(BASE)
    page.set_input_files("#files", [str(clip)])
    expect(page.locator("#clips li.clip")).to_have_count(1)
    check("page says it will use the clip's own sound", "sound recorded in your clips" in page.locator("#voice").inner_text())
    page.once("dialog", lambda d: d.accept())   # only shown when there are several clips; harmless otherwise
    page.click("#go")
    t0 = time.time()
    made = wait_for_video(page)
    check("talking video was made", made, "" if made else page.locator("#failMsg").inner_text().replace("\n", " | "))
    if not made:
        return
    print(f"   (took {time.time() - t0:.0f}s)", flush=True)
    facts = video_facts(page)
    check("long video is split into several parts", len(facts) >= 2, f"{len(facts)} parts")
    check("every part is under 60 seconds", all(5 < d < 60 for d, _, _ in facts), [round(d, 1) for d, _, _ in facts])
    check("every part is vertical 1080x1920", all((w, h) == (1080, 1920) for _, w, h in facts))
    check("parts add up to about the whole clip", 120 < sum(d for d, _, _ in facts) < 155, f"{sum(d for d, _, _ in facts):.0f}s of 150s")
    notes = page.locator("#splitNote").inner_text() + " " + page.locator("#notes").inner_text()
    check("says it was split and used the clip's sound", "split it into" in notes and "sound from your clips" in notes,
          notes.replace("\n", " | ")[:160])
    check("each part has its own download button", page.locator("#players a[download]").count() == len(facts))
    report = page.evaluate("fetch(`/api/jobs/${jobId}`).then(r => r.json()).then(j => j.report)")
    check("report marks it as talking-to-camera", report.get("mode") == "own_audio", report.get("mode"))
    page.screenshot(path=SHOTS / "9-talking.png", full_page=True)


def main():
    global PASSWORD
    PASSWORD = secrets.token_urlsafe(12)          # throwaway, only ever lives in this run
    data = Path(tempfile.mkdtemp(prefix="alex-e2e-"))
    global DATA
    DATA = data
    SHOTS.mkdir(parents=True, exist_ok=True)
    server = start_server(data)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless="--show" not in sys.argv)
            # The site now sends a security policy, and Playwright's wait_for_function() needs eval, so every context the tests
            # open ignores the policy unless a part asks otherwise (the fixer part passes bypass_csp=False to test it for real).
            _new_context = browser.new_context
            browser.new_context = lambda **kw: _new_context(**{"bypass_csp": True, **kw})
            page = browser.new_context(viewport={"width": 430, "height": 900}).new_page()   # phone-sized, like hers
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            try:
                run(page)
            except Exception as e:   # a step that times out is a failed check, not a crash of the test run
                check("test ran to the end", False, f"{type(e).__name__}: {str(e).splitlines()[0][:200]}")
                page.screenshot(path=SHOTS / "crash.png", full_page=True)
            check("no JavaScript errors on any page", not errors, "; ".join(errors[:3]))
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(data, ignore_errors=True)
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed. Screenshots: {SHOTS}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # the page uses symbols a Windows console can't print
    sys.exit(main())
