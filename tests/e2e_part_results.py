"""The class report on the team page: numbers, spreadsheet download, admin only."""


def run(page, ctx):
    print("Results report", flush=True)
    r = page.request.get(ctx.BASE + "/api/admin/results")
    ctx.check("admin can read the results", r.ok, r.status)
    data = r.json()
    groups = {g["user"]: g for g in data["groups"]}
    ctx.check("there is an 'All' group", "All" in groups, list(groups))
    allg = groups["All"]
    for key in ("videos", "remakes", "posted", "avg_build_min", "median_review_min", "avg_by_hand_min", "avg_saved_min", "weeks"):
        ctx.check(f"results carry '{key}'", key in allg)
    ctx.check("nothing says 0 minutes when it was never measured", allg["avg_saved_min"] is None or allg["avg_saved_min"] != 0 or allg["saved_counted"] > 0)

    c = page.request.get(ctx.BASE + "/api/admin/results.csv")
    ctx.check("spreadsheet download is a csv file", c.ok and "text/csv" in c.headers.get("content-type", "") and "attachment" in c.headers.get("content-disposition", ""), c.headers.get("content-type"))
    first = c.text().split("\r\n")[0]
    ctx.check("spreadsheet has the expected columns", first.startswith("job,user,made_at,week") and "saved_min" in first, first)
    ctx.check("spreadsheet holds no titles or captions", "title" not in first and "caption" not in first)

    anon = page.context.browser.new_context()
    ctx.check("signed-out visitors are refused", anon.request.get(ctx.BASE + "/api/admin/results").status == 401)
    ctx.check("...and the spreadsheet too", anon.request.get(ctx.BASE + "/api/admin/results.csv").status == 401)
    anon.close()

    page.goto(ctx.BASE + "/admin")
    page.wait_for_function("document.getElementById('results') && !/Loading/.test(document.getElementById('results').innerText)", timeout=15000)
    text = page.locator("#results").inner_text()
    ctx.check("the team page shows the results card", "Everyone" in text or "No finished videos yet" in text, text[:120])
    if allg["videos"]:
        ctx.check("it explains how the numbers are worked out", "How this is worked out" in text)
        ctx.check("it says how many videos each average rests on", "videos have all three numbers" in text)
    ctx.check("the spreadsheet link is on the page", page.locator('a[href="/api/admin/results.csv"]').count() == 1)
    page.screenshot(path=ctx.SHOTS / "results-1-team-page.png", full_page=True)

    # the card with real-looking numbers (the data endpoint is stubbed so this works on an empty test site too)
    import json
    fixture = {"groups": [
        {"user": "All", "videos": 3, "remakes": 1, "posted": 1, "avg_build_min": 2.2, "median_review_min": 35.0, "review_counted": 1,
         "avg_by_hand_min": 105.0, "by_hand_counted": 2, "avg_saved_min": 82.5, "saved_counted": 1,
         "weeks": [{"week": "2026-W42", "made": 2, "posted": 1}, {"week": "2026-W43", "made": 1, "posted": 0}], "best_week_made": 2},
        {"user": "<img src=x onerror=alert(1)>", "videos": 1, "remakes": 0, "posted": 0, "avg_build_min": None, "median_review_min": None,
         "review_counted": 0, "avg_by_hand_min": None, "by_hand_counted": 0, "avg_saved_min": None, "saved_counted": 0, "weeks": [], "best_week_made": 1}],
        "videos": []}
    page.route("**/api/admin/results", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(fixture)))
    page.goto(ctx.BASE + "/admin")
    page.wait_for_function("document.getElementById('results') && !/Loading/.test(document.getElementById('results').innerText)", timeout=15000)
    text = page.locator("#results").inner_text()
    ctx.check("with data: everyone's numbers show", "Everyone" in text and "82.5 min" in text and "2.2 min" in text and "35 min" in text, text[:200])
    ctx.check("with data: each average says how many videos it rests on", "1 video has all three numbers" in text, text[:300])
    ctx.check("with data: weeks are listed", "2026-W42: made 2, posted 1" in text)
    ctx.check("with data: unmeasured numbers show a dash, not zero", "–" in text)
    ctx.check("a name with HTML in it is shown as text, not run", page.locator("#results img").count() == 0 and "<img" in text)
    page.screenshot(path=ctx.SHOTS / "results-2-with-data.png", full_page=True)
    page.unroute("**/api/admin/results")
