"""Automatic clip order: clips play in the order they were filmed, without her numbering or sorting anything.

    venv\\Scripts\\python tests\\test_clip_order.py

Uses her real tour clips (samples/tour) for the real-file checks and tiny ffmpeg-made clips for the odd cases."""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from video_tool import clip_order, ff  # noqa: E402
from video_tool.config import FFMPEG  # noqa: E402
from video_tool.errors import AlexError  # noqa: E402
from video_tool.inputs import find_inputs  # noqa: E402

failed = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  - ' + str(detail) if detail else ''}", flush=True)
    if not ok:
        failed.append(name)


def item(name, t, secs=5):
    return {"name": name, "time": t, "seconds": secs}


T = 1_790_000_000   # a plausible filming moment

print("parse_time")
check("apple time with zone", abs(clip_order.parse_time("2026-09-22T11:52:29-0500") - clip_order.parse_time("2026-09-22T16:52:29.000000Z")) < 1)
check("plain UTC time", clip_order.parse_time("2026-09-22T16:52:29.000000Z") is not None)
check("garbage is None", clip_order.parse_time("yesterday") is None and clip_order.parse_time("") is None and clip_order.parse_time(None) is None)
check("a phone clock never set (1970) is not trusted", clip_order.parse_time("1970-01-01T00:00:00Z") is None)
check("natural name order (clip2 before clip10)", sorted(["clip10", "clip2", "clip1"], key=clip_order.natural_key) == ["clip1", "clip2", "clip10"])

print("plan_order")
o, b = clip_order.plan_order([item("c", T + 100), item("a", T), item("b", T + 50)])
check("shuffled clips go into filming order", o == [1, 2, 0] and b == "filmed", (o, b))
o, b = clip_order.plan_order([item("a", T), item("b", T + 50), item("c", T + 100)])
check("clips already in order are left alone", o == [0, 1, 2] and b is None, (o, b))
o, b = clip_order.plan_order([item("a", T), item("b", None), item("c", T + 100)])
check("one missing time: her order is kept, nothing guessed", o == [0, 1, 2] and b is None)
o, b = clip_order.plan_order([item("a", T), item("b", T), item("c", T)])
check("every clip says the same moment (an export): her order is kept", o == [0, 1, 2] and b is None)
o, b = clip_order.plan_order([item("b", T + 2, 20), item("a", T, 20)])
check("two 20 s clips 2 s apart can't both be filmed that way: export times, not trusted", b is None, (o, b))
o, b = clip_order.plan_order([item("b", T + 35, 28.6), item("a", T, 95.4)])
check("times that mark when filming ENDED also fit (her recipe clips)", o == [1, 0] and b == "filmed", (o, b))
o, b = clip_order.plan_order([item("b", T + 30, 20), item("a", T, 20)])
check("a normal gap is trusted", o == [1, 0] and b == "filmed")
o, b = clip_order.plan_order([item("only", T)])
check("one clip: nothing to do", o == [0] and b is None)
o, b = clip_order.plan_order([item("b", T + 60), item("a", T), item("c", T + 120)])
check("returns indexes into the order she gave", sorted(o) == [0, 1, 2])
o, b = clip_order.plan_order([item("late", T + 90), item("early", T)])
check("two clips swap", o == [1, 0] and b == "filmed")

tmp = Path(tempfile.mkdtemp(prefix="alex-order-"))
try:
    print("real tour clips with their real filming times")
    src = ROOT / "samples" / "tour"
    originals = sorted(src.glob("*.mp4"))[:5]
    if not originals:
        print("   SKIPPED the real-clip checks: samples/tour is not in this copy (her footage is not in the repository)")
    else:
        times = [clip_order.capture_time(f) for f in originals]
        check("her phone clips carry a filming time", all(times), times)
        check("those times rise with the clip numbers", times == sorted(times))

        shuffled = tmp / "shuffled"
        shuffled.mkdir()
        names = ["IMG_9001.mp4", "IMG_0002.mp4", "IMG_5555.mp4", "IMG_0100.mp4", "IMG_7777.mp4"]
        for f, n in zip(originals, [names[3], names[0], names[4], names[1], names[2]]):
            shutil.copy2(f, shuffled / n)
        # original i is now called: 0 -> IMG_0100, 1 -> IMG_9001, 2 -> IMG_7777, 3 -> IMG_0002, 4 -> IMG_5555
        want = ["IMG_0100.mp4", "IMG_9001.mp4", "IMG_7777.mp4", "IMG_0002.mp4", "IMG_5555.mp4"]
        got, voice = find_inputs(shuffled)
        check("un-numbered clips are put in filming order, not name order", [c.name for c in got] == want, [c.name for c in got])
        check("sorted by name would have been different", sorted(want) != want)

        numbered = tmp / "numbered"
        numbered.mkdir()
        for k, f in enumerate(reversed(originals), 1):   # 01 is the LAST filmed clip: she numbered them that way
            shutil.copy2(f, numbered / f"{k:02d}.mp4")
        got, _ = find_inputs(numbered)
        check("numbers she chose are respected over filming time", [c.name for c in got] == [f"{k:02d}.mp4" for k in range(1, 6)])

    print("odd files")
    odd = tmp / "odd"
    odd.mkdir()
    for n in ("clip10.mp4", "clip2.mp4", "clip1.mp4"):
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=160x284:d=1:r=30", "-pix_fmt", "yuv420p", str(odd / n)], check=True)
    got, _ = find_inputs(odd)
    check("clips with no filming time fall back to natural name order", [c.name for c in got] == ["clip1.mp4", "clip2.mp4", "clip10.mp4"], [c.name for c in got])
    files, basis = clip_order.sort_files(list(odd.glob("*.mp4")))
    check("and say so ('name', not 'filmed')", basis == "name")

    stamped = tmp / "stamped"
    stamped.mkdir()
    for n, when in (("20260922_115221.mp4", "2026-09-22T11:50:00Z"), ("20260922_115001.mp4", "2026-09-22T11:55:00Z")):   # names say one thing, the filming time another
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=160x284:d=1:r=30", "-pix_fmt", "yuv420p",
                        "-metadata", f"creation_time={when}", str(stamped / n)], check=True)
    got, _ = find_inputs(stamped)
    check("camera-timestamp names no longer stop the video; the filming time decides", [c.name for c in got] == ["20260922_115221.mp4", "20260922_115001.mp4"], [c.name for c in got])

    export = tmp / "export"
    export.mkdir()
    for n in ("b.mp4", "a.mp4"):
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=160x284:d=3:r=30", "-pix_fmt", "yuv420p",
                        "-metadata", "creation_time=2026-09-22T11:50:00Z", str(export / n)], check=True)
    got, _ = find_inputs(export)
    check("clips all stamped with one export moment keep name order", [c.name for c in got] == ["a.mp4", "b.mp4"])
    try:
        find_inputs(tmp / "nothing-here")
        check("a missing folder still stops", False)
    except AlexError:
        check("a missing folder still stops", True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("ALL PASSED" if not failed else f"{len(failed)} FAILED: " + "; ".join(failed))
sys.exit(1 if failed else 0)
