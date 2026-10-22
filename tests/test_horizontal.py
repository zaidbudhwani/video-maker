"""Horizontal YouTube output: the setting, the frame and caption geometry, and the upload choice. Fast, no video made.

    venv\\Scripts\\python tests\\test_horizontal.py

The vertical numbers must stay exactly what they were (the vertical video is unchanged); the horizontal ones are new."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = Path(tempfile.mkdtemp(prefix="alex-horiz-"))
os.environ.update(ALEX_DATA_DIR=str(DATA), ALEX_PREFS_FILE=str(DATA / "prefs.json"), ANTHROPIC_API_KEY="",
                  OPENROUTER_API_KEY="", ALEX_HOSTED="1", ADMIN_USER="tester", ADMIN_PASSWORD="admin-pass-123",
                  APP_PASSWORD="", ALEX_AUTO_SLIM="0")

from video_tool import assemble, captions, layout, own_audio, pipeline, settings  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {detail}"))
    if not cond:
        FAILS.append(name)


print("setting")
s = settings.configure({})
check("default is vertical, upright clips shown whole", s["orientation"] == "vertical" and s["tall_clips"] == "bars", s)
check("horizontal accepted", settings.configure({"orientation": "horizontal"})["orientation"] == "horizontal")
for bad in ("sideways", "", None, 1, True, "HORIZONTAL"):
    check(f"bad orientation {bad!r} -> vertical", settings.configure({"orientation": bad})["orientation"] == "vertical")
check("tall_clips fill accepted", settings.configure({"tall_clips": "fill"})["tall_clips"] == "fill")
check("bad tall_clips -> bars", settings.configure({"tall_clips": "stretch"})["tall_clips"] == "bars")
check("orientation is per video (never remembered)", "orientation" not in settings.REMEMBER_KEYS
      and "tall_clips" not in settings.REMEMBER_KEYS)
saved = settings.remember({"orientation": "horizontal", "caption_size": 80}, DATA / "p.json")
check("remember() drops it", saved == ["caption_size"] and "orientation" not in json.loads((DATA / "p.json").read_text()), saved)

print("vertical geometry unchanged")
settings.configure({})
check("size 1080x1920", layout.size() == (1080, 1920))
check("limit 59.5 s", layout.max_seconds() == 59.5)
check("voice-over frame is the old FRAME (no probe)", assemble.frame_for("no-such-file.mp4") == assemble.FRAME)
check("fit filter equals the old FIT", layout.fit_filter() == own_audio.FIT)
check("fill filter equals the old FRAME", layout.fill_filter() == assemble.FRAME)
check("captions: 78 px, 691 px from the bottom, floor 384", layout.caption_geometry() == (78, 691, 384), layout.caption_geometry())
check("caption style line unchanged", captions.style() ==
      "Style: Default,Arial,78,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,6,0,2,150,150,691,1",
      captions.style())

print("horizontal geometry")
settings.configure({"orientation": "horizontal"})
check("size 1920x1080", layout.size() == (1920, 1080))
check("limit about 15 minutes", 890 <= layout.max_seconds() <= 900, layout.max_seconds())
size, margin, floor = layout.caption_geometry()
check("caption size 58 (5% of the height)", size == 58, size)
check("caption sits in the lower third", 0 < margin < 1080 / 3 - 2 * size * 1.2, margin)
check("caption floor inside the frame", 0 < floor <= margin, (floor, margin))
settings.configure({"orientation": "horizontal", "caption_margin": 0.60, "caption_size": 110})
size, margin, _ = layout.caption_geometry()
check("highest, biggest caption still in the lower third", margin + 2 * size * 1.2 < 1080 / 3 + 1080 * 0.1 and margin == 216,
      (size, margin))
settings.configure({"orientation": "horizontal"})
words = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.35} for i, w in enumerate(
    "this home has a big deck and a quiet street out front with lots of trees and a very long sentence".split())]
path, lines = captions.write_ass(words, DATA)
ass = path.read_text(encoding="utf-8")
check("ASS is a 1920x1080 canvas", "PlayResX: 1920\nPlayResY: 1080" in ass)
check("ASS style uses 58 px and the 16:9 margin", ",58," in ass.split("Style: Default")[1].split("\n")[0]
      and ass.split("Style: Default")[1].split("\n")[0].endswith(f",{layout.caption_geometry()[1]},1"))

print("which clips get bars")
check("upright clip: bars", layout.shows_whole(1080, 1920))
check("square clip: bars", layout.shows_whole(1080, 1080))
check("landscape clip: fills", not layout.shows_whole(1920, 1080) and not layout.shows_whole(1440, 1080))
settings.configure({"orientation": "horizontal", "tall_clips": "fill"})
check("fill chosen: upright clip fills", not layout.shows_whole(1080, 1920))
check("upright clip filling 16:9 keeps 32% of its height", abs(layout.shown_share(1080, 1920) - 0.316) < 0.01,
      layout.shown_share(1080, 1920))
settings.configure({"orientation": "vertical"})
check("vertical never uses bars here (unchanged path)", not layout.shows_whole(1080, 1080))

print("voice-over frame per clip")
settings.configure({"orientation": "horizontal"})
probe = lambda w, h: {"streams": [{"codec_type": "video", "width": w, "height": h}]}   # noqa: E731
with mock.patch.object(assemble.ff, "probe", lambda c: probe(1080, 1920)):
    vf = assemble.frame_for("x.mp4")
check("upright clip: scaled to fit and padded, 1920x1080", "force_original_aspect_ratio=decrease" in vf
      and "pad=1920:1080" in vf, vf)
with mock.patch.object(assemble.ff, "probe", lambda c: probe(1920, 1080)):
    vf = assemble.frame_for("x.mp4")
check("landscape clip: covers 1920x1080 (aspect kept)", "force_original_aspect_ratio=increase" in vf
      and "crop=1920:1080" in vf, vf)
check("never stretched: every scale keeps the aspect", all("force_original_aspect_ratio" in f
                                                          for f in (layout.fit_filter(), layout.fill_filter())))

print("length rules")
settings.configure({"orientation": "horizontal"})
words = [{"word": "Word.", "start": t, "end": t + 0.3} for t in range(0, 600)]
check("a 10-minute talk stays one video", own_audio.split_points(words, 600.0) == [(0.0, 600.0)])
words = [{"word": "Word.", "start": t, "end": t + 0.3} for t in range(0, 1200)]
parts = own_audio.split_points(words, 1200.0)
check("internal splitter bounds parts; public horizontal pipeline rejects a 20-minute source", len(parts) == 2 and all(b - a <= 899.5 for a, b in parts), parts)
settings.configure({})
words = [{"word": "Word.", "start": t, "end": t + 0.3} for t in range(0, 100)]
check("vertical still splits at 60 s", len(own_audio.split_points(words, 100.0)) == 2)

print("warnings about cut-off clips")
settings.configure({"orientation": "horizontal"})
clip = Path("01.mp4")
tl = [{"clip": "01.mp4", "start": 0, "end": 5}]
with mock.patch.object(pipeline.ff, "probe", lambda c: probe(1080, 1920)):
    w_bars = pipeline.wide_warnings([clip], tl, {})
    settings.configure({"orientation": "horizontal", "tall_clips": "fill"})
    w_fill = pipeline.wide_warnings([clip], tl, {})
check("bars: no warning (nothing is cut)", w_bars == [], w_bars)
check("fill: plain warning that the top and bottom are cut", len(w_fill) == 1 and "top and bottom" in w_fill[0]["message"], w_fill)

print("upload choice (website)")
from fastapi import HTTPException  # noqa: E402
from web import app as W  # noqa: E402


def extras(body):
    try:
        return W._clean_extras(body)
    except HTTPException as e:
        return ("400", e.detail)


check("missing = vertical (nothing added)", extras({}) == {})
check("vertical = nothing added", extras({"orientation": "vertical"}) == {})
check("horizontal carried", extras({"orientation": "horizontal"}) == {"orientation": "horizontal"})
check("horizontal + fill carried", extras({"orientation": "horizontal", "tall_clips": "fill"})
      == {"orientation": "horizontal", "tall_clips": "fill"})
check("fill without horizontal ignored", extras({"tall_clips": "fill"}) == {})
r = extras({"orientation": "sideways"})
check("unknown shape is a plain 400", r[0] == "400" and "YouTube" in r[1], r)
r = extras({"orientation": "horizontal", "highlights": 3})
check("Shorts from a long video cannot be horizontal", r[0] == "400", r)
r = extras({"orientation": "horizontal", "tall_clips": "stretch"})
check("bad fill choice is a plain 400", r[0] == "400", r)

print("final check boundaries and split edge cases")
settings.configure({"orientation": "horizontal"})
for total in (899.5, 899.6, 900, 1800, 2700.1):
    parts = own_audio.split_points([], total)
    check(f"empty transcript {total}s: contiguous, bounded parts", parts[0][0] == 0 and parts[-1][1] == total
          and all(0 < b-a <= 899.501 for a,b in parts) and all(a[1] == b[0] for a,b in zip(parts,parts[1:])), parts)
for dur, accepted in ((899.5, True), (899.99, True), (900, False)):
    info = {"duration": dur, "streams": [{"codec_type": "video", "width": 1920, "height": 1080, "codec_name": "h264"},
                                       {"codec_type": "audio", "codec_name": "aac"}]}
    with mock.patch.object(pipeline.ff, "probe", return_value=info):
        try:
            pipeline.verify("fake.mp4")
            ok = True
        except Exception:
            ok = False
    check(f"finished length {dur} accepted={accepted}", ok == accepted)
from video_tool import postpack
check("horizontal alt text", "horizontal" in postpack.alt_text({}, "horizontal") and "vertical" not in postpack.alt_text({}, "horizontal"))
info = probe(1080, 1080)
settings.configure({"orientation": "horizontal", "tall_clips": "fill", "framing": "fill"})
with mock.patch.object(own_audio.facecrop, "plan_rows_crop", side_effect=AssertionError("centre mode must not face crop")):
    vf, _ = own_audio.horizontal_frame("fake.mp4", info, "fill")
check("horizontal centre fill does not use face crop", "crop=1920:1080" in vf)
settings.configure({"orientation": "horizontal", "tall_clips": "bars"})
vf, _ = own_audio.horizontal_frame("fake.mp4", info, "auto")
check("horizontal bars wins over auto framing", "pad=1920:1080" in vf)

print("landscape whole-frame and source boundary")
settings.configure({"orientation": "horizontal"})
vf, _ = own_audio.horizontal_frame("fake.mp4", probe(1440, 1080), "fit")
check("explicit fit keeps a 4:3 landscape whole", "force_original_aspect_ratio=decrease" in vf and "pad=1920:1080" in vf)
cfg = DATA / "horizontal.json"
cfg.write_text(json.dumps({"orientation": "horizontal"}), encoding="utf-8")
with mock.patch.object(pipeline, "OUT_DIR", DATA / "boundary"), mock.patch.object(pipeline, "find_inputs", return_value=([Path("one.mp4")], None)), mock.patch.object(assemble, "check_clip", return_value=(900.0, False)), mock.patch.object(own_audio, "prepare", side_effect=AssertionError("must reject before preparing")):
    try:
        pipeline.main([str(DATA), "--settings", str(cfg), "--job-id", "too-long"])
        rejected = False
    except pipeline.AlexError as e:
        rejected = "15 minutes" in str(e) and "Split" in str(e)
check("over-limit horizontal source rejected before normalization", rejected)
# A requested highlights run must clear horizontal before the child workflow starts.
with mock.patch.object(pipeline, "OUT_DIR", DATA / "boundary"), mock.patch.object(pipeline, "find_inputs", return_value=([Path("one.mp4")], None)), mock.patch.object(assemble, "check_clip", return_value=(150.0, False)), mock.patch.object(pipeline, "long_video", return_value=0) as lv:
    pipeline.main([str(DATA), "--settings", str(cfg), "--job-id", "shorts", "--highlights", "2"])
    check("Shorts dispatch clears horizontal", lv.call_args.args[1]["orientation"] == "vertical" and not layout.horizontal())

print("remake keeps per-video settings and maps framing")
import asyncio
from starlette.requests import Request
for mode, tall in (("fit", "bars"), ("fill", "fill")):
    jid = "horizontal-remake-" + mode
    W.OUT.mkdir(parents=True, exist_ok=True)
    (W.OUT / (jid + ".source")).write_text(str(DATA), encoding="utf-8")
    W._settings_arg(jid, {"orientation": "horizontal", "tall_clips": "fill", "privacy": "private"})
    req = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "state": {"user": "tester"}})
    with mock.patch.object(W, "_mine", return_value="tester"), mock.patch.object(W, "_json_body", new=mock.AsyncMock(return_value={"changes": {"framing": mode}, "remake": True})), mock.patch.object(W, "_start") as start:
        response = asyncio.run(W.feedback(jid, req))
    new = W._own_settings(response["job_id"])
    check(f"remake {mode}: horizontal preserved, {tall}", new.get("orientation") == "horizontal" and new.get("tall_clips") == tall and new.get("privacy") == "private", new)

print("horizontal kit, remake timing and remembered framing")
with mock.patch.object(settings, "load_prefs", return_value={"framing": "fit"}):
    check("horizontal upload does not inherit vertical fit", settings.configure({"orientation": "horizontal", "tall_clips": "fill"})["framing"] == "auto")
    check("horizontal explicit fit still wins", settings.configure({"orientation": "horizontal", "framing": "fit"})["framing"] == "fit")
kit = {"titles": ["The complete video"], "description": "The complete YouTube description.", "chapters": [], "tags": [], "source": "fallback", "ai_cost_usd": 0}
spoken = [{"word": w, "start": i, "end": i + .8} for i,w in enumerate("Today I am making a smoothie with fruit and milk.".split())]
out = DATA / "kit-proof"
def rendered(job, video, ass, name, **kwargs):
    (job / name).write_bytes(b"test-only")
with mock.patch.object(pipeline, "OUT_DIR", out), mock.patch.object(pipeline, "find_inputs", return_value=([Path("one.mp4")], None)), mock.patch.object(assemble, "check_clip", return_value=(100, False)), mock.patch.object(own_audio, "prepare", return_value=("video.mp4", spoken, [(0,100)], {"timeline": []})) as prep, mock.patch.object(pipeline.quality, "analyze", return_value=[]), mock.patch.object(pipeline, "render", side_effect=rendered), mock.patch.object(pipeline, "verify", return_value=100), mock.patch.object(pipeline.transcribe, "uncertain_words", return_value=[]), mock.patch.object(pipeline.writer, "write_copy", side_effect=AssertionError("must not call short-form AI writer")), mock.patch.object(pipeline.longform, "build_kit", return_value=kit) as build:
    pipeline.main([str(DATA), "--settings", str(cfg), "--job-id", "kit1", "--no-music"])
    cp = json.loads((out / "kit1" / "copy.json").read_text(encoding="utf-8"))
    check("horizontal copy comes from YouTube kit, no Shorts hashtag", cp["titles"] == kit["titles"] and cp["description"] == kit["description"] and "#shorts" not in json.dumps(cp).lower())
    pipeline.main([str(DATA), "--settings", str(cfg), "--job-id", "kit2", "--no-music", "--reuse", str(out / "kit1")])
    check("same words and timing keep existing YouTube kit", build.call_count == 1)
    prep.return_value = ("video.mp4", [{**w, "start": w["start"]+1, "end": w["end"]+1} for w in spoken], [(0,100)], {"timeline": []})
    pipeline.main([str(DATA), "--settings", str(cfg), "--job-id", "kit3", "--no-music", "--reuse", str(out / "kit1")])
    check("changed timing rebuilds chapters rather than retaining stale timestamps", build.call_count == 2)

settings.configure({})
print()
print(f"{'FAILED: ' + ', '.join(FAILS) if FAILS else 'ALL PASS'}")
sys.exit(1 if FAILS else 0)
