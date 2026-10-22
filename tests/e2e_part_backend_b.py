"""Backend B checks against the real running site: upload extras and file checks (with a real voice-over recorded the way
a browser does, as .webm), a real video made with 'keep my pictures private', the post pack of that video, posting log,
week and reminder, formats, ideas, the example video, and the phone-app files with a real service worker.
Works on its own:  E2E_PORT=8072 E2E_ONLY=backend_b venv/Scripts/python tests/e2e_site.py
Everything is made up inside the throwaway data folder; the example video is a link/copy of out/demo-tour-moved."""
import io
import json
import os
import secrets
import shutil
import subprocess
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

J = {"content-type": "application/json"}


def run(page, ctx):
    try:
        _run(page, ctx)
    finally:   # leave the shared throwaway site as found: other parts read every copy.json they can see
        out = ctx.DATA / "out"
        shutil.rmtree(out / "demo-tour-moved", ignore_errors=True)
        (out / "_demo.txt").unlink(missing_ok=True)


def _run(page, ctx):
    BASE, out, ROOT = ctx.BASE, ctx.DATA / "out", ctx.ROOT
    check, api = ctx.check, page.request
    import sys
    sys.path.insert(0, str(ROOT))
    from video_tool.config import FFMPEG
    check("backend B: page is signed in", api.get(f"{BASE}/api/me").json()["user"] == "tester")
    media = ctx.DATA / "media-b"
    media.mkdir(exist_ok=True)

    def ff(*args):
        return subprocess.run([FFMPEG, "-y", "-v", "error", *args], capture_output=True).returncode == 0

    def post(a, path, body):
        return a.post(f"{BASE}{path}", data=json.dumps(body), headers=J)

    # ---- a second throwaway user ----
    pw2 = secrets.token_urlsafe(10)
    check("second user made", post(api, "/api/admin/users", {"username": "second", "password": pw2}).ok)
    ctx2 = page.context.browser.new_context()
    check("second user signs in", post(ctx2.request, "/api/login", {"username": "second", "password": pw2}).ok)
    api2 = ctx2.request

    # ---- media made with ffmpeg ----
    good = media / "good.mp4"
    ff("-f", "lavfi", "-i", "testsrc=s=320x240:d=1:r=15", "-f", "lavfi", "-i", "sine=f=300:d=1", "-shortest",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(good))
    audio_mp4, trunc, silent, junk = media / "a.mp4", media / "t.mp4", media / "s.m4a", media / "j.m4a"
    ff("-f", "lavfi", "-i", "sine=f=300:d=2", "-c:a", "aac", str(audio_mp4))
    trunc.write_bytes(good.read_bytes()[:good.stat().st_size // 2])
    ff("-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "3", "-c:a", "aac", str(silent))
    junk.write_bytes(b"not audio" * 100)

    def upload(a, clips, voice=None, **extra):
        body = {"clips": [{"name": n, "size": p.stat().st_size} for n, p in clips],
                "voice": {"name": voice[0], "size": voice[1].stat().st_size} if voice else None, **extra}
        r = post(a, "/api/uploads", body)
        if not r.ok:
            return r, None
        j = r.json()
        for slot, p in zip(j["slots"], [p for _, p in clips] + ([voice[1]] if voice else [])):
            data = p.read_bytes()
            for off in range(0, len(data), j["chunk"]):   # pieces, like the page sends them
                rr = a.put(f"{BASE}/api/uploads/{j['job_id']}/{slot['slot']}?offset={off}", data=data[off:off + j["chunk"]])
                assert rr.ok, rr.text()
        return a.post(f"{BASE}/api/uploads/{j['job_id']}/finish"), j["job_id"]

    # ---- file checks on the real server ----
    for label, clips, voice, want in (
            ("audio-only clip", [("Hall.mov", good), ("Note.mp4", audio_mp4)], None, ["Clip 2 (Note.mp4)", "no picture"]),
            ("truncated clip", [("Broken.mp4", trunc)], None, ["Clip 1 (Broken.mp4)", "finished downloading"]),
            ("silent voice", [("a.mov", good)], ("Muted.m4a", silent), ["couldn't hear anything", "Muted.m4a"]),
            ("unreadable voice", [("a.mov", good)], ("Bad.m4a", junk), ["Bad.m4a", "can't be read"])):
        r, _ = upload(api, clips, voice)
        msg = r.json().get("detail", "") if r.status == 400 else r.text()
        check(f"backend B: {label} is refused in plain English", r.status == 400 and all(w in msg for w in want) and "\\" not in msg, msg)
    r, _ = upload(api, [("a.mov", good)], hints=["not text"])
    check("backend B: junk hints are a plain 400", r.status == 400, r.text())
    r = post(api, "/api/uploads", {"clips": [{"name": "a.mov", "size": 9}], "format": "poem"})
    check("backend B: unknown format is a plain 400", r.status == 400 and "kind of video" in r.json()["detail"], r.text())

    # ---- formats and ideas ----
    fm = api.get(f"{BASE}/api/formats").json()
    check("backend B: /api/formats lists the kinds without 'auto'", [f["id"] for f in fm] == ["house_tour", "recipe", "tips", "market_update", "qa"], fm)
    t0 = time.time()
    ideas = api.get(f"{BASE}/api/ideas?n=5").json()["ideas"]
    check("backend B: /api/ideas gives 5 ideas with no key (built-in list)", len(ideas) == 5 and all(len(i["shots"]) == 4 for i in ideas), ideas)
    check("backend B: ideas are signed-in only", page.context.browser.new_context().request.get(f"{BASE}/api/ideas").status == 401)

    # ---- a real video: clips from the recipe sample, voice recorded as .webm, private + hints ----
    clips = sorted(ctx.SAMPLE.glob("*.mp4"))
    voice_src = ctx.SAMPLE / "voice.m4a"
    webm = media / "Browser recording.webm"
    p = subprocess.run([FFMPEG, "-v", "error", "-i", str(voice_src), "-c:a", "libopus", "-b:a", "48k", "-f", "webm", "-"], capture_output=True)
    webm.write_bytes(p.stdout)   # streamed, like MediaRecorder: no length in the header
    check("backend B: a browser-style .webm voice-over was made", webm.stat().st_size > 1000)
    r, job = upload(api, [(c.name, c) for c in clips], (webm.name, webm), hints="  Ellis Park Dr,   Shellsburg ", format="recipe", private=True)
    check("backend B: real upload with .webm voice, hints, format and private is accepted", r.ok and job, r.text())
    if not (r.ok and job):
        return
    t0, st = time.time(), {}
    while time.time() - t0 < 300:
        st = api.get(f"{BASE}/api/jobs/{job}").json()
        if st.get("status") in ("done", "failed"):
            break
        time.sleep(3)
    check("backend B: the video was made from the .webm voice-over", st.get("status") == "done", st.get("error"))
    if st.get("status") != "done":
        return
    rs = st["report"]["settings"]
    check("backend B: report.json settings show private pictures, hints and format",
          rs.get("privacy") == "private" and rs.get("match_clips") is False and rs.get("hints") == "Ellis Park Dr, Shellsburg" and rs.get("format") == "recipe", rs)
    check("backend B: no pictures were taken for matching (no frames folder)", not (out / job / "match").exists(), os.listdir(out / job))
    check("backend B: the video is the right size and under 60 s", 5 < st["report"]["length"] < 60, st["report"].get("length"))
    row = next(v for v in api.get(f"{BASE}/api/videos").json()["videos"] if v["job_id"] == job)
    check("backend B: My videos says the pictures stayed private", row["private"] is True, row)

    # ---- post pack of the real video ----
    r = api.get(f"{BASE}/api/jobs/{job}/postpack")
    z = zipfile.ZipFile(io.BytesIO(r.body()))
    check("backend B: post pack of the real video", r.ok and sorted(z.namelist()) == ["alt-text.txt", "captions.srt", "cover.jpg", "final.mp4", "post-text.txt"], z.namelist())
    check("backend B: the pack's video is the finished one", z.getinfo("final.mp4").file_size == (out / job / "final.mp4").stat().st_size)
    check("backend B: another user can't download it", api2.get(f"{BASE}/api/jobs/{job}/postpack").status == 404)
    check("backend B: nothing left behind in the temp pack folder", not list((out / "_packs").glob("*.zip")))

    # ---- posting log, week, reminder on the real video ----
    today = datetime.now().date()
    v = post(api, f"/api/jobs/{job}/posting", {"status": "posted", "platform": "tiktok"}).json()
    check("backend B: posting log saved", v["status"] == "posted" and v["posted"] == [{"platform": "tiktok", "date": today.isoformat()}], v)
    check("backend B: second user can't see it", api2.get(f"{BASE}/api/jobs/{job}/posting").status == 404)
    w = api.get(f"{BASE}/api/posting/week").json()
    check("backend B: week counts it", w["count_this_week"] >= 1 and any(p["job"] == job for p in w["posted"]) and w["goal_min"] == 3, w)
    check("backend B: second user's week is empty", api2.get(f"{BASE}/api/posting/week").json()["count_this_week"] == 0)
    ics = api.get(f"{BASE}/api/posting/reminder.ics", params={"day": (today + timedelta(days=1)).isoformat(), "title": "Hi\r\nATTENDEE:x"})
    check("backend B: .ics reminder has no injected line", ics.ok and "text/calendar" in ics.headers["content-type"] and "\nATTENDEE" not in ics.text(), ics.text())

    # ---- the example video ----
    src = ROOT / "out" / "demo-tour-moved"
    check("backend B: demo is hidden while _demo.txt is missing", not api.get(f"{BASE}/api/demo").json()["available"])
    if src.is_dir():
        d = out / "demo-tour-moved"
        d.mkdir(exist_ok=True)
        for f in src.iterdir():
            if f.is_file() and f.name.startswith(("final", "copy", "report", "captions", "transcript")):
                try:
                    os.link(f, d / f.name)
                except OSError:
                    shutil.copy2(f, d / f.name)
        (out / "_demo.txt").write_text("demo-tour-moved", encoding="utf-8")
        dm = api.get(f"{BASE}/api/demo").json()
        check("backend B: demo is available with the finished-job shape", dm["available"] and dm["job"]["status"] == "done" and "copy" in dm["job"]
              and dm["job"]["video_urls"] == ["/api/demo/video/1"], list(dm.get("job") or {}))
        check("backend B: second user sees the same example", api2.get(f"{BASE}/api/demo").json()["available"])
        page.goto(BASE)
        vid = page.evaluate("""async () => {
            const v = document.createElement('video'); v.muted = true; v.src = '/api/demo/video/1'; v.preload = 'metadata';
            await new Promise((res, rej) => { v.onloadedmetadata = res; v.onerror = () => rej('error'); setTimeout(() => rej('timeout'), 15000); });
            return [v.videoWidth, v.videoHeight, v.duration]; }""")
        check("backend B: the example video plays in the browser, vertical", vid[0] < vid[1] and vid[2] > 5, vid)
        check("backend B: the example's thumbnail loads", api.get(f"{BASE}/api/demo/thumb").headers["content-type"] == "image/jpeg")
        check("backend B: the example can't be deleted or listed",
              api.delete(f"{BASE}/api/jobs/demo-tour-moved").status == 404
              and all(x["job_id"] != "demo-tour-moved" for x in api.get(f"{BASE}/api/videos").json()["videos"]))
        for bad in ("/api/demo/video/..%2f..%2fusers.json", "/api/demo/video/2", "/api/demo/video/x"):
            check(f"backend B: {bad} serves nothing", api.get(f"{BASE}{bad}").status in (404, 422))
    else:
        check("backend B: example video fixture found (skipped)", True)

    # ---- phone-app files: signed out, then a real service worker ----
    anon = page.context.browser.new_context()
    man = anon.request.get(f"{BASE}/manifest.webmanifest")
    check("backend B: manifest works signed out", man.ok and man.json()["short_name"] == "Video Maker")
    check("backend B: icons and sw.js work signed out", all(anon.request.get(f"{BASE}{u}").ok for u in ("/assets/icon-192.png", "/assets/icon-512.png", "/sw.js")))
    check("backend B: the rest still needs sign-in", anon.request.get(f"{BASE}/api/demo").status == 401 and anon.request.get(f"{BASE}/api/formats").status == 401)
    page.goto(BASE)
    reg = page.evaluate("""async () => {
        const r = await navigator.serviceWorker.register('/sw.js', {scope: '/'});
        const sw = r.installing || r.waiting || r.active;
        await new Promise(res => { if (sw.state === 'activated') res(); else sw.addEventListener('statechange', () => sw.state === 'activated' && res()); setTimeout(res, 8000); });
        await navigator.serviceWorker.ready;
        return {scope: new URL(r.scope).pathname, state: sw.state}; }""")
    check("backend B: service worker installs with scope '/'", reg["scope"] == "/" and reg["state"] == "activated", reg)
    page.reload()
    cached = page.evaluate("""async () => { const out = {}; for (const k of await caches.keys()) out[k] = (await (await caches.open(k)).keys()).map(r => new URL(r.url).pathname); return out; }""")
    check("backend B: the worker cached only the two icons", list(cached.values()) == [["/assets/icon-192.png", "/assets/icon-512.png"]] or
          (len(cached) == 1 and sorted(next(iter(cached.values()))) == ["/assets/icon-192.png", "/assets/icon-512.png"]), cached)
    page.reload()
    page.wait_for_function("navigator.serviceWorker.controller !== null", timeout=10000)
    # with the worker in control, API calls and videos must still go to the server every time
    first = page.evaluate("async () => (await fetch('/api/posting/week')).json()")
    post(api, f"/api/jobs/{job}/posting", {"status": "posted", "platform": "youtube_shorts"})
    second = page.evaluate("async () => (await fetch('/api/posting/week')).json()")
    check("backend B: with the worker active, /api/ answers are live, not cached", len(second["posted"]) == len(first["posted"]) + 1, (first, second))
    ok = page.evaluate("async () => { const r = await fetch('/api/jobs/%s/final.mp4', {headers: {Range: 'bytes=0-9'}}); return [r.status, (await r.arrayBuffer()).byteLength]; }" % job)
    check("backend B: with the worker active, a video range request still works", ok == [206, 10], ok)
    cached2 = page.evaluate("""async () => { const o = []; for (const k of await caches.keys()) for (const r of await (await caches.open(k)).keys()) o.push(new URL(r.url).pathname); return o; }""")
    check("backend B: nothing from /api/ or videos was ever cached", all(p.startswith("/assets/") for p in cached2), cached2)
    page.screenshot(path=ctx.SHOTS / "backend_b-worker.png")
    # uploads still work with the worker in control (start and send one piece from the page itself)
    up = page.evaluate("""async () => {
        const s = await fetch('/api/uploads', {method: 'POST', headers: {'content-type': 'application/json'},
                              body: JSON.stringify({clips: [{name: 'a.mov', size: 4}], private: true})});
        const j = await s.json();
        const p = await fetch(`/api/uploads/${j.job_id}/${j.slots[0].slot}?offset=0`, {method: 'PUT', body: new Uint8Array([1, 2, 3, 4])});
        return [s.status, p.status, (await p.json()).received]; }""")
    check("backend B: uploads still work with the worker in control", up == [200, 200, 4], up)
    page.evaluate("async () => { for (const r of await navigator.serviceWorker.getRegistrations()) await r.unregister(); for (const k of await caches.keys()) await caches.delete(k); }")
