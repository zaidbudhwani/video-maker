"""YouTube channel report for the class metrics: posting pace, views, watch time, Shorts vs long-form,
before vs with the tool, and how many of the tool's titles she posted unchanged.

    venv\\Scripts\\python -m video_tool.yt_report --demo                 layout check with made-up numbers
    venv\\Scripts\\python -m video_tool.yt_report --tool-start 2026-10-06  her real channel (needs sign-in)

Read-only: it asks Google for the two "readonly" permissions and can never post, edit or delete.
Real data needs yt/client_secret.json (the maintainer makes it once, see docs/SETUP_YOUTUBE.md) and then Alex approving
access in her browser; the approval is saved in yt/token.json. Both files are gitignored.
Output: out/youtube_report/report.html and videos.csv.
"""
import argparse
import csv
import difflib
import html
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import OUT_DIR, ROOT

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly",
          "https://www.googleapis.com/auth/yt-analytics.readonly"]
YT_DIR = ROOT / "yt"
REPORT_DIR = OUT_DIR / "youtube_report"
METRICS = ("views,engagedViews,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
           "likes,comments,shares,subscribersGained,subscribersLost")
SHORT_MAX_SECONDS = 180   # YouTube counts vertical/square videos up to 3 minutes as Shorts


# ---------- Google sign-in and fetch ----------

def credentials():
    """Saved approval if there is one, else open the browser for her to approve (read-only)."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    secret, token = YT_DIR / "client_secret.json", YT_DIR / "token.json"
    if not secret.exists():
        raise SystemExit(f"Missing {secret}. Follow docs/SETUP_YOUTUBE.md (the maintainer does this once), or use --demo.")
    creds = Credentials.from_authorized_user_file(str(token), SCOPES) if token.exists() else None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:   # in Google's "Testing" mode the approval lapses after 7 days
            creds = None
    if not creds or not creds.valid:
        print("A browser window will open. Sign in as the channel owner and click Allow (read-only access).")
        creds = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES).run_local_server(port=0)
        token.write_text(creds.to_json(), encoding="utf-8")
    return creds


def iso_seconds(duration):
    """'PT1M5S' -> 65"""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration or "")
    d, h, mi, s = (int(x or 0) for x in m.groups()) if m else (0, 0, 0, 0)
    return d * 86400 + h * 3600 + mi * 60 + s


def fetch(youtube, analytics, start, end):
    """Every upload with its details and its numbers for start..end. Returns (channel, videos)."""
    ch = youtube.channels().list(part="snippet,contentDetails,statistics", mine=True).execute()["items"][0]
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, page = [], None
    while True:
        r = youtube.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=50, pageToken=page).execute()
        ids += [i["contentDetails"]["videoId"] for i in r.get("items", [])]
        page = r.get("nextPageToken")
        if not page:
            break
    videos = {}
    for i in range(0, len(ids), 50):
        r = youtube.videos().list(part="snippet,contentDetails,statistics,status", id=",".join(ids[i:i + 50])).execute()
        for v in r.get("items", []):
            videos[v["id"]] = {"id": v["id"], "title": v["snippet"]["title"],
                               "published": v["snippet"]["publishedAt"][:10],
                               "seconds": iso_seconds(v["contentDetails"].get("duration")),
                               "privacy": v.get("status", {}).get("privacyStatus", ""), "kind": None}

    def query(dimensions):
        return analytics.reports().query(ids="channel==MINE", startDate=str(start), endDate=str(end), metrics=METRICS,
                                         dimensions=dimensions, sort="-views", maxResults=200).execute()
    try:
        rep = query("video,creatorContentType")
    except Exception:   # some channels reject this pairing; Shorts are then guessed from length
        rep = query("video")
    cols = [c["name"] for c in rep.get("columnHeaders", [])]
    for row in rep.get("rows", []):
        d = dict(zip(cols, row))
        v = videos.get(d["video"])
        if v:
            v.update({k: d[k] for k in METRICS.split(",") if k in d})
            v["kind"] = {"SHORTS": "Short", "VIDEO_ON_DEMAND": "Long-form", "LIVE_STREAM": "Live"}.get(d.get("creatorContentType"))
    if len(rep.get("rows", [])) >= 200:
        print("NOTE: more than 200 videos had views in this range; only the top 200 have numbers. Use a shorter range.")
    for v in videos.values():
        if not v["kind"]:   # no views in range, or type unknown: guess from length and say so
            v["kind"] = "Short?" if v["seconds"] <= SHORT_MAX_SECONDS else "Long-form?"
    channel = {"title": ch["snippet"]["title"], "subscribers": ch["statistics"].get("subscriberCount", "hidden")}
    return channel, sorted(videos.values(), key=lambda v: v["published"], reverse=True)


# ---------- what the tool wrote vs what she posted ----------

def tool_titles():
    titles = set()
    for f in OUT_DIR.glob("*/copy.json"):
        try:
            c = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        titles.update(t.strip() for t in c.get("titles", []) if t)
        for p in c.get("parts", []):
            titles.update(t.strip() for t in p.get("titles", []) if t)
    return titles


def title_use(title, pool):
    """'unchanged' if she posted a tool title as written, 'edited' if close, '' if it isn't one of ours."""
    norm = lambda s: re.sub(r"\s+", " ", s).strip().lower()
    t = norm(title)
    if any(t == norm(p) for p in pool):
        return "unchanged"
    best = max((difflib.SequenceMatcher(None, t, norm(p)).ratio() for p in pool), default=0)
    return "edited" if best >= 0.6 else ""


# ---------- demo data (clearly labelled) ----------

def demo(tool_start):
    import random
    rnd = random.Random(7)
    videos, d = [], date.today()
    names = ["Solon Iowa Home Tour", "Rainy Day on the Acreage", "My Go-to Smoothie", "Why Your Mortgage Payment Went Up",
             "Puppies at 7 Weeks", "Fall Maintenance Tips", "Shellsburg Home Tour", "Home Value Tip"]
    for i in range(38):
        with_tool = d >= tool_start
        d -= timedelta(days=rnd.choice([1, 2]) if with_tool else rnd.choice([5, 7, 9]))
        short = rnd.random() < (0.9 if with_tool else 0.6)
        views = rnd.randint(300, 2600) if short else rnd.randint(80, 600)
        secs = rnd.randint(22, 58) if short else rnd.randint(140, 620)
        videos.append({"id": f"demo{i}", "title": f"DEMO {names[i % len(names)]} #{i + 1}", "published": str(d),
                       "seconds": secs, "privacy": "public", "kind": "Short" if short else "Long-form",
                       "views": views, "engagedViews": int(views * 0.7), "estimatedMinutesWatched": int(views * secs * 0.6 / 60),
                       "averageViewDuration": int(secs * 0.6), "averageViewPercentage": round(rnd.uniform(45, 85) if short else rnd.uniform(20, 45), 1),
                       "likes": views // 22, "comments": views // 140, "shares": views // 95,
                       "subscribersGained": views // 150, "subscribersLost": views // 900,
                       "use": rnd.choice(["unchanged", "unchanged", "edited", ""]) if with_tool else ""})
    return {"title": "DEMO CHANNEL (made-up data)", "subscribers": "DEMO"}, videos


# ---------- report ----------

def monday(d):
    return d - timedelta(days=d.weekday())


def summarize(videos, pred):
    rows = [v for v in videos if pred(v)]
    views = sum(v.get("views", 0) for v in rows)
    pct = [v["averageViewPercentage"] for v in rows if v.get("averageViewPercentage")]
    return {"videos": len(rows), "views": views, "minutes": sum(v.get("estimatedMinutesWatched", 0) for v in rows),
            "avg_pct": round(sum(pct) / len(pct), 1) if pct else None,
            "likes": sum(v.get("likes", 0) for v in rows), "comments": sum(v.get("comments", 0) for v in rows),
            "shares": sum(v.get("shares", 0) for v in rows),
            "subs": sum(v.get("subscribersGained", 0) - v.get("subscribersLost", 0) for v in rows),
            "views_each": round(views / len(rows)) if rows else 0}


def render(channel, videos, start, end, tool_start, is_demo):
    e = html.escape
    f = lambda n: "–" if n is None else f"{n:,}" if isinstance(n, int) else str(n)
    mmss = lambda s: f"{int(s) // 60}:{int(s) % 60:02d}"
    in_range = [v for v in videos if str(start) <= v["published"] <= str(end)]
    is_short = lambda v: v["kind"].startswith("Short")

    kinds = [("Shorts", summarize(in_range, is_short)), ("Long-form", summarize(in_range, lambda v: not is_short(v))),
             ("All", summarize(in_range, lambda v: True))]
    kind_rows = "".join(f"<tr><th>{n}</th><td>{f(s['videos'])}</td><td>{f(s['views'])}</td><td>{f(s['minutes'])}</td>"
                        f"<td>{f(s['avg_pct'])}{'%' if s['avg_pct'] else ''}</td><td>{f(s['likes'])}</td><td>{f(s['comments'])}</td>"
                        f"<td>{f(s['shares'])}</td><td>{s['subs']:+,}</td></tr>" for n, s in kinds)

    before = [v for v in in_range if v["published"] < str(tool_start)]
    after = [v for v in in_range if v["published"] >= str(tool_start)]
    weeks = lambda rows, a, b: max(1, round(((b - a).days + 1) / 7)) if rows else 0
    wb, wa = weeks(before, start, tool_start - timedelta(days=1)), weeks(after, tool_start, end)
    sb, sa = summarize(before, lambda v: True), summarize(after, lambda v: True)
    share = lambda rows: f"{round(100 * sum(is_short(v) for v in rows) / len(rows))}%" if rows else "–"
    unchanged, edited = sum(v.get("use") == "unchanged" for v in after), sum(v.get("use") == "edited" for v in after)
    ba = [("Weeks", wb, wa), ("Videos posted", sb["videos"], sa["videos"]),
          ("Videos per week", f"{sb['videos'] / wb:.1f}" if wb else "–", f"{sa['videos'] / wa:.1f}" if wa else "–"),
          ("Share that are Shorts", share(before), share(after)),
          ("Average views per video", f(sb["views_each"]), f(sa["views_each"])),
          ("Average % viewed", f"{sb['avg_pct']}%" if sb["avg_pct"] else "–", f"{sa['avg_pct']}%" if sa["avg_pct"] else "–"),
          ("Tool titles posted unchanged", "–", unchanged), ("Tool titles posted with edits", "–", edited)]
    ba_rows = "".join(f"<tr><th>{n}</th><td>{b}</td><td>{a}</td></tr>" for n, b, a in ba)

    first = monday(start)
    counts = {}
    for v in in_range:
        counts[monday(date.fromisoformat(v["published"]))] = counts.get(monday(date.fromisoformat(v["published"])), 0) + 1
    wk, bars, x = first, [], 0
    top = max([5, *counts.values()])
    while wk <= end:
        c = counts.get(wk, 0)
        h = c / top * 120
        cls = "after" if wk >= monday(tool_start) else "before"
        bars.append(f'<rect class="{cls}" x="{x}" y="{130 - h:.0f}" width="26" height="{max(h, 1):.0f}" rx="3"><title>{c} video(s), week of {wk}</title></rect>'
                    + (f'<text x="{x + 13}" y="{124 - h:.0f}" text-anchor="middle">{c}</text>' if c else "")
                    + f'<text class="wk" x="{x + 13}" y="146" text-anchor="middle">{wk.strftime("%m/%d")}</text>')
        x += 34
        wk += timedelta(days=7)
    goal_y = 130 - 3 / top * 120
    chart = (f'<svg viewBox="0 0 {max(x, 100)} 152" role="img" aria-label="Videos posted per week">'
             f'<line class="goal" x1="0" x2="{x}" y1="{goal_y:.0f}" y2="{goal_y:.0f}"/><text class="wk" x="0" y="{goal_y - 3:.0f}">goal: 3 a week</text>'
             + "".join(bars) + "</svg>")

    vid_rows = "".join(
        f"<tr><td>{e(v['published'])}</td><td class='t'>{e(v['title'])}</td><td>{e(v['kind'])}</td><td>{mmss(v['seconds'])}</td>"
        f"<td>{f(v.get('views'))}</td><td>{f(v.get('estimatedMinutesWatched'))}</td>"
        f"<td>{(str(v['averageViewPercentage']) + '%') if v.get('averageViewPercentage') else '–'}</td>"
        f"<td>{f(v.get('likes'))}</td><td>{f(v.get('comments'))}</td><td>{f(v.get('shares'))}</td>"
        f"<td>{e(v.get('use') or '')}</td></tr>" for v in in_range)
    banner = ('<div class="demo">DEMO: every number on this page is made up to check the layout. It is not Alex\'s channel.</div>'
              if is_demo else "")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{'DEMO - ' if is_demo else ''}YouTube report</title><style>
:root{{--bg:#f3f7fb;--card:#ffffff;--ink:#1f1f1f;--muted:#54595f;--line:#dbe5ee;--accent:#2c76ae;--soft:#bcd6ea}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0e1620;--card:#172230;--ink:#e9eff5;--muted:#9fb0c0;--line:#2a3a4b;--accent:#6caedf;--soft:#34506b}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1000px;margin:0 auto;padding:20px 16px 60px}} h1{{font-size:24px;margin:0 0 2px}} h2{{font-size:18px;margin:28px 0 8px}}
.muted{{color:var(--muted);font-size:14px}} .demo{{background:#c0392b;color:#fff;font-weight:700;padding:12px 14px;border-radius:8px;margin-bottom:14px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;overflow-x:auto}}
table{{border-collapse:collapse;width:100%}} th,td{{padding:7px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}}
th:first-child,td:first-child,td.t{{text-align:left}} td.t{{white-space:normal;min-width:220px}} thead th{{font-size:13px;color:var(--muted)}}
svg{{width:100%;max-width:{max(x, 100) * 1.4:.0f}px;height:auto}} rect.before{{fill:var(--soft)}} rect.after{{fill:var(--accent)}}
svg text{{font-size:10px;fill:var(--ink)}} svg text.wk{{fill:var(--muted);font-size:8px}} line.goal{{stroke:var(--muted);stroke-dasharray:3 3}}
</style></head><body><main>{banner}
<h1>{'DEMO - ' if is_demo else ''}YouTube report: {e(channel['title'])}</h1>
<div class="muted">{start} to {end} · subscribers now: {e(str(channel['subscribers']))} · generated {datetime.now():%Y-%m-%d %H:%M}. YouTube's numbers run 2-3 days behind.</div>
<h2>Totals: Shorts vs long-form</h2><div class="card"><table><thead><tr><th></th><th>Videos</th><th>Views</th><th>Minutes watched</th><th>Avg % viewed</th><th>Likes</th><th>Comments</th><th>Shares</th><th>Subs (net)</th></tr></thead><tbody>{kind_rows}</tbody></table></div>
<h2>Before vs with the tool</h2><div class="muted">Tool start date: {tool_start}. Editing hours per video are not on YouTube; take those from the team's timing log.</div>
<div class="card" style="max-width:520px"><table><thead><tr><th></th><th>Before tool</th><th>With tool</th></tr></thead><tbody>{ba_rows}</tbody></table></div>
<h2>Videos posted per week</h2><div class="muted">Her goal is 3 to 5 Shorts a week. Lighter bars are before the tool. Weeks start on Monday.</div><div class="card">{chart}</div>
<h2>Every video in this period</h2><div class="card"><table><thead><tr><th>Posted</th><th>Title</th><th>Type</th><th>Length</th><th>Views</th><th>Min watched</th><th>% viewed</th><th>Likes</th><th>Comments</th><th>Shares</th><th>Tool title</th></tr></thead><tbody>{vid_rows}</tbody></table></div>
<p class="muted">"Views" counts every play since YouTube's 2025 change; the CSV also has engagedViews (the older, stricter count), which is the fairer one for before/after comparisons across that date. "Short?" means the type was guessed from the length. "Tool title" compares each posted title with the titles this tool wrote: "edited" is a rough match, so check those by hand before quoting a count.</p>
</main></body></html>"""


def main(argv=None):
    ap = argparse.ArgumentParser(description="YouTube channel report (read-only).")
    ap.add_argument("--demo", action="store_true", help="made-up data, to check the layout")
    ap.add_argument("--days", type=int, default=120, help="how far back to look (default 120)")
    ap.add_argument("--tool-start", default=str(date.today() - timedelta(days=28)), help="YYYY-MM-DD she started using the tool")
    a = ap.parse_args(argv)
    end = date.today()
    start, tool_start = end - timedelta(days=a.days), date.fromisoformat(a.tool_start)

    if a.demo:
        channel, videos = demo(tool_start)
    else:
        from googleapiclient.discovery import build
        creds = credentials()
        channel, videos = fetch(build("youtube", "v3", credentials=creds),
                                build("youtubeAnalytics", "v2", credentials=creds), start, end)
        pool = tool_titles()
        for v in videos:
            v["use"] = title_use(v["title"], pool) if v["published"] >= str(tool_start) else ""

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    name = "demo" if a.demo else "report"
    (REPORT_DIR / f"{name}.html").write_text(render(channel, videos, start, end, tool_start, a.demo), encoding="utf-8")
    cols = ["published", "title", "kind", "seconds", "privacy", *METRICS.split(","), "use", "id"]
    with open(REPORT_DIR / f"{name}_videos.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(videos)
    print(f"Wrote {REPORT_DIR / (name + '.html')} and {name}_videos.csv ({len(videos)} videos)")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
