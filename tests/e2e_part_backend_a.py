"""Backend A checks against the real running site: delete, change password, events, admin, prefs reset, malformed
bodies, public help, caching headers, sliding cookie. Works on its own (E2E_ONLY=backend_a).
Everything is made up inside the throwaway data folder."""
import json
import re
import secrets


def run(page, ctx):
    BASE, out = ctx.BASE, ctx.DATA / "out"
    ctx.check("backend A: page is signed in", page.request.get(f"{BASE}/api/me").json()["user"] == "tester")
    api = page.request

    # ---- public help, caching ----
    anon = page.context.browser.new_context()
    r = anon.request.get(f"{BASE}/api/help")
    ctx.check("help endpoint is public and shaped {contact}", r.ok and list(r.json()) == ["contact"], r.text())
    r = anon.request.get(f"{BASE}/api/me")
    ctx.check("signed-out API call is 401 JSON", r.status == 401 and "detail" in r.json())
    r = api.get(f"{BASE}/api/me")
    ctx.check("API JSON is Cache-Control: no-store", "no-store" in r.headers.get("cache-control", ""), r.headers)
    r = api.get(f"{BASE}/videos")
    ctx.check("HTML pages are no-store", "no-store" in r.headers.get("cache-control", ""))

    # ---- a second account ----
    pw2 = secrets.token_urlsafe(10)
    r = api.post(f"{BASE}/api/admin/users", data=json.dumps({"username": "second", "password": pw2}),
                 headers={"content-type": "application/json"})
    ctx.check("admin made a second throwaway user", r.ok, r.text())
    ctx2 = page.context.browser.new_context()
    r = ctx2.request.post(f"{BASE}/api/login", data=json.dumps({"username": "second", "password": pw2}),
                          headers={"content-type": "application/json"})
    ctx.check("second user signs in", r.ok, r.text())
    api2 = ctx2.request
    j = {"content-type": "application/json"}

    # ---- fixtures: two videos each, one family of two for tester ----
    up = out / "_uploads"
    def make(job, owner, src=None, parent=None):
        (out / job).mkdir(parents=True, exist_ok=True)
        (out / job / "report.json").write_text(json.dumps({"settings": {}, "parts": [{"file": "final.mp4"}]}), encoding="utf-8")
        (out / job / "final.mp4").write_bytes(b"x")
        (out / f"{job}.owner").write_text(owner, encoding="utf-8")
        if src:
            (up / src).mkdir(parents=True, exist_ok=True)
            (up / src / "01.mp4").write_bytes(b"c")
            (out / f"{job}.source").write_text(str(up / src), encoding="utf-8")
        if parent:
            (out / f"{job}.parent").write_text(parent, encoding="utf-8")
    make("web-ba-1", "tester", "web-ba-1")
    make("web-ba-2", "tester", "web-ba-1", parent="web-ba-1")
    make("web-ba-3", "second", "web-ba-3")

    # ---- events ----
    r = api.post(f"{BASE}/api/jobs/web-ba-1/event", data=json.dumps({"kind": "copy", "platform": "tiktok"}), headers=j)
    ctx.check("event logged", r.ok and r.json() == {"ok": True}, r.text())
    ctx.check("event line is in out/events.jsonl, with no text of hers",
              any(json.loads(l)["kind"] == "copy" and json.loads(l)["user"] == "tester"
                  for l in (out / "events.jsonl").read_text(encoding="utf-8").splitlines()))
    ctx.check("unknown kind is 400", api.post(f"{BASE}/api/jobs/web-ba-1/event", data=json.dumps({"kind": "x"}), headers=j).status == 400)
    ctx.check("event on another user's video is 404",
              api2.post(f"{BASE}/api/jobs/web-ba-1/event", data=json.dumps({"kind": "copy"}), headers=j).status == 404)

    # ---- delete ----
    ctx.check("other user cannot delete my video", api2.delete(f"{BASE}/api/jobs/web-ba-1?all=1").status == 404 and (out / "web-ba-1").exists())
    for bad in ("..%2f..%2fusers", "%2e%2e", "web-ba-1%2f..%2f..", "web-ba-1%00"):
        r = api.delete(f"{BASE}/api/jobs/{bad}")
        ctx.check(f"traversal id {bad} refused", r.status in (400, 404, 405) and (out / "web-ba-1").exists() and (ctx.DATA / "users.json").exists(), r.status)
    r = api.delete(f"{BASE}/api/jobs/web-ba-2")
    ctx.check("delete one version", r.ok and r.json() == {"ok": True, "removed": 1} and (out / "web-ba-1").exists(), r.text())
    make("web-ba-2", "tester", "web-ba-1", parent="web-ba-1")
    r = api.delete(f"{BASE}/api/jobs/web-ba-2?all=1")
    ctx.check("delete all versions and the saved clips", r.ok and r.json() == {"ok": True, "removed": 2}
              and not (out / "web-ba-1").exists() and not (up / "web-ba-1").exists(), r.text())
    ctx.check("someone else's video survived", (out / "web-ba-3").exists() and (up / "web-ba-3").exists())
    r = api.get(f"{BASE}/api/jobs/web-ba-1")
    ctx.check("deleted video answers a clean 404", r.status == 404 and "detail" in r.json())
    ctx.check("admin can delete another user's video", api.delete(f"{BASE}/api/jobs/web-ba-3?all=1").ok and not (out / "web-ba-3").exists())

    # ---- admin ----
    h = api.get(f"{BASE}/api/admin/health").json()
    ctx.check("health shape", set(h) == {"disk_free_gb", "jobs_total", "jobs_failed", "last_job_at", "key_present", "keep_awake",
                                         "help_contact_set"}, h)
    ctx.check("failures list answers", isinstance(api.get(f"{BASE}/api/admin/failures").json()["items"], list))
    for path in ("/api/admin/health", "/api/admin/failures"):
        ctx.check(f"{path} is admin only", api2.get(f"{BASE}{path}").status == 403)

    # ---- prefs ----
    (ctx.DATA / "users" / "second").mkdir(parents=True, exist_ok=True)
    (ctx.DATA / "users" / "second" / "prefs.json").write_text('{"caption_size": 99}', encoding="utf-8")
    ctx.check("prefs reset", api2.delete(f"{BASE}/api/prefs").json() == {"ok": True}
              and not (ctx.DATA / "users" / "second" / "prefs.json").exists())

    # ---- malformed feedback ----
    make("web-ba-4", "tester", "web-ba-4")
    for body in ('[1]', '"x"', '{nope', ''):
        r = api.post(f"{BASE}/api/jobs/web-ba-4/feedback", data=body, headers=j)
        ctx.check(f"feedback body {body!r} is a 400", r.status == 400, r.status)
    r = api.post(f"{BASE}/api/jobs/web-ba-4/feedback", data=json.dumps({"changes": {"caption_size": "abc"}, "remake": False}), headers=j)
    ctx.check("non-numeric value dropped, no 500", r.ok and r.json()["applied"] == [], r.text())
    r = api.post(f"{BASE}/api/jobs/web-ba-4/feedback", data=json.dumps({"changes": [1]}), headers=j)
    ctx.check("non-dict changes ignored, no 500", r.ok)

    # ---- change password through the real site, then sign in again with the new one ----
    r = api2.post(f"{BASE}/api/change-password", data=json.dumps({"current": "not-it-not-it", "new": "a-new-pass-99"}), headers=j)
    ctx.check("wrong current password is 400", r.status == 400 and "current" in r.json()["detail"], r.text())
    r = api2.post(f"{BASE}/api/change-password", data=json.dumps({"current": pw2, "new": "short"}), headers=j)
    ctx.check("short new password is 400", r.status == 400)
    new = secrets.token_urlsafe(10)
    r = api2.post(f"{BASE}/api/change-password", data=json.dumps({"current": pw2, "new": new}), headers=j)
    ctx.check("change password works", r.ok and r.json() == {"ok": True}, r.text())
    ctx.check("still signed in after the change (cookie re-issued)", api2.get(f"{BASE}/api/me").json()["user"] == "second")
    ctx3 = page.context.browser.new_context()
    ctx.check("old password is refused", ctx3.request.post(f"{BASE}/api/login", data=json.dumps({"username": "second", "password": pw2}),
                                                           headers=j).status == 401)
    ctx.check("new password signs in", ctx3.request.post(f"{BASE}/api/login", data=json.dumps({"username": "second", "password": new}),
                                                         headers=j).ok)

    # ---- the real login page still works in a browser with the new cookie settings ----
    p2 = ctx3.new_page()
    p2.goto(BASE)
    ctx.check("signed-in second user lands on the upload page", p2.locator("#drop").is_visible() and "/login" not in p2.url, p2.url)
    ctx.check("session cookie lasts about 90 days",
              any(c["name"] == "alex_session" and c["expires"] - __import__("time").time() > 85 * 86400 for c in ctx3.cookies()))
    for c in (anon, ctx2, ctx3):
        c.close()
