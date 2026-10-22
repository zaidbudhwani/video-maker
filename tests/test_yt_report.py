"""Offline checks for the YouTube report: no Google account, no network.

    venv\\Scripts\\python tests\\test_yt_report.py

Fake clients return responses shaped like the documented YouTube Data API v3 / Analytics API v2 ones.
This proves the parsing and the report maths, NOT that Google accepts our requests: only a real run can.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from video_tool import yt_report as Y

fails = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  - ' + str(detail) if detail else ''}")
    if not ok:
        fails.append(name)


class Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeYouTube:
    """3 uploads across 2 pages of the uploads playlist."""
    def channels(self):
        return self

    def playlistItems(self):
        return self

    def videos(self):
        return self

    def list(self, **kw):
        if kw.get("mine"):
            return Call(lambda: {"items": [{"snippet": {"title": "The Example Homestead"}, "statistics": {"subscriberCount": "412"},
                                            "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}}}]})
        if "playlistId" in kw:
            page = kw.get("pageToken")
            return Call(lambda: {"items": [{"contentDetails": {"videoId": "a"}}, {"contentDetails": {"videoId": "b"}}], "nextPageToken": "p2"}
                        if not page else {"items": [{"contentDetails": {"videoId": "c"}}]})
        meta = {"a": ("Solon Iowa Home Tour | Rec Room, Deck & More", "2026-10-01T15:00:00Z", "PT46S"),
                "b": ("Fall Maintenance Tips", "2026-08-10T15:00:00Z", "PT5M12S"),
                "c": ("Never watched", "2026-09-20T15:00:00Z", "PT1M5S")}
        return Call(lambda: {"items": [{"id": i, "snippet": {"title": meta[i][0], "publishedAt": meta[i][1]},
                                        "contentDetails": {"duration": meta[i][2]}, "status": {"privacyStatus": "public"}}
                                       for i in kw["id"].split(",")]})


class FakeAnalytics:
    def __init__(self, reject_content_type=False):
        self.reject, self.asked = reject_content_type, []

    def reports(self):
        return self

    def query(self, **kw):
        self.asked.append(kw["dimensions"])
        if self.reject and "creatorContentType" in kw["dimensions"]:
            def boom():
                raise RuntimeError("HttpError 400: The query is not supported")
            return Call(boom)
        with_type = "creatorContentType" in kw["dimensions"]
        heads = ["video"] + (["creatorContentType"] if with_type else []) + Y.METRICS.split(",")
        rows = [["a"] + (["SHORTS"] if with_type else []) + [900, 600, 410, 27, 59.5, 40, 6, 9, 5, 1],
                ["b"] + (["VIDEO_ON_DEMAND"] if with_type else []) + [200, 150, 380, 114, 36.5, 8, 1, 2, 1, 0]]
        return Call(lambda: {"columnHeaders": [{"name": h} for h in heads], "rows": rows})


print("Durations")
check("PT46S", Y.iso_seconds("PT46S") == 46)
check("PT5M12S", Y.iso_seconds("PT5M12S") == 312)
check("PT1H2M3S", Y.iso_seconds("PT1H2M3S") == 3723)
check("empty / missing", Y.iso_seconds(None) == 0)

print("Fetch (normal)")
ch, vids = Y.fetch(FakeYouTube(), FakeAnalytics(), date(2026, 6, 1), date(2026, 10, 5))
by = {v["id"]: v for v in vids}
check("channel name and subscribers", ch == {"title": "The Example Homestead", "subscribers": "412"}, ch)
check("all 3 uploads found across 2 pages", len(vids) == 3, len(vids))
check("newest first", [v["id"] for v in vids] == ["a", "c", "b"])
check("numbers attached to the right video", by["a"]["views"] == 900 and by["b"]["estimatedMinutesWatched"] == 380)
check("Short vs long-form from YouTube's own type", by["a"]["kind"] == "Short" and by["b"]["kind"] == "Long-form")
check("no-views video is guessed from its length and marked", by["c"]["kind"] == "Short?" and "views" not in by["c"], by["c"]["kind"])

print("Fetch (YouTube rejects the content-type breakdown)")
an = FakeAnalytics(reject_content_type=True)
_, vids2 = Y.fetch(FakeYouTube(), an, date(2026, 6, 1), date(2026, 10, 5))
by2 = {v["id"]: v for v in vids2}
check("falls back to the plain query", an.asked == ["video,creatorContentType", "video"], an.asked)
check("still has the numbers", by2["a"]["views"] == 900)
check("types are guesses and say so", by2["a"]["kind"] == "Short?" and by2["b"]["kind"] == "Long-form?", (by2["a"]["kind"], by2["b"]["kind"]))

print("Titles: the tool's vs what she posted")
pool = {"Solon Iowa Home Tour | Rec Room, Deck & More", "This is My Go-to Smoothie Recipe"}
check("posted exactly = unchanged", Y.title_use("Solon Iowa Home Tour | Rec Room, Deck & More", pool) == "unchanged")
check("case and spacing don't matter", Y.title_use("solon iowa home tour  |  rec room, deck & more", pool) == "unchanged")
check("small edit = edited", Y.title_use("Solon Iowa Home Tour | Rec Room & Deck", pool) == "edited")
check("her own title = not ours", Y.title_use("Puppies Picture Time", pool) == "")

print("Report")
for v in vids:
    v["use"] = Y.title_use(v["title"], pool)
page = Y.render(ch, vids, date(2026, 6, 1), date(2026, 10, 5), date(2026, 9, 15), False)
check("real report has no DEMO banner", "DEMO" not in page)
check("lists every video", all(v["title"].split(" |")[0] in page for v in vids))
check("escapes titles safely", "<script" not in Y.render(ch, [{**vids[0], "title": "<script>x</script>"}], date(2026, 6, 1), date(2026, 10, 5), date(2026, 9, 15), False))
s_before = Y.summarize(vids, lambda v: v["published"] < "2026-09-15")
s_after = Y.summarize(vids, lambda v: v["published"] >= "2026-09-15")
check("before/after split counts", (s_before["videos"], s_after["videos"]) == (1, 2), (s_before["videos"], s_after["videos"]))
check("views total", Y.summarize(vids, lambda v: True)["views"] == 1100)
dch, dv = Y.demo(date(2026, 9, 15))
dpage = Y.render(dch, dv, date(2026, 6, 1), date(2026, 10, 5), date(2026, 9, 15), True)
check("demo report is loudly labelled", dpage.count("DEMO") >= 3 and "made up" in dpage)

print(f"\n{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
