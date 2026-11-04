"""Step 2: normalize every clip, join in order, fit to the voice length.

Clips are longer than the voice -> cut the end.
Clips are shorter -> hold the last frame and report the shortfall (never loop).
Dead footage at the start of a clip (camera still swinging up, black frames) is skipped.
"""
import json
import numpy as np
from pathlib import Path

from . import deadstart, facecrop, ff, layout, match, quality, settings
from .config import FPS, HDR_TRANSFERS, HEIGHT, MAX_OVERRUN_RATIO, MAX_SHORTFALL_RATIO, SCALER, TONEMAP, WIDTH
from .errors import AlexError

FRAME = (f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase:flags={SCALER},"
         f"crop={WIDTH}:{HEIGHT},setsar=1,fps={FPS},format=yuv420p")
ENCODE = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
          "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]


def check_clip(clip):
    info = ff.probe(clip)
    video = [s for s in info["streams"] if s.get("codec_type") == "video"]
    if not video:
        raise AlexError("This file has no picture in it, so it can't be used as a clip.", clip)
    if info["duration"] < 0.3:
        raise AlexError("This clip is too short to use (under half a second). It may not have saved properly.", clip)
    return info["duration"], video[0].get("color_transfer") in HDR_TRANSFERS


def frame_for(clip):
    """The picture filter for a voice-over clip. Vertical: always FRAME (fills the 1080x1920 frame, as before).
    Horizontal: landscape clips fill 1920x1080; upright and square clips are shown whole with side bars, unless
    she chose to fill the screen (then the top and bottom are cut). Never stretched."""
    if not layout.horizontal():
        return FRAME
    w, h = facecrop.display_size(ff.probe(clip))
    return layout.fit_filter() if layout.shows_whole(w, h) else layout.fill_filter()


def normalize(clip, out, hdr, length=None, start=0.0):
    # Rotation metadata is applied automatically by ffmpeg. Clip audio is dropped:
    # the voice-over is the only sound. -xerror makes a corrupt clip fail here.
    vf = (TONEMAP if hdr else "") + frame_for(clip)
    seek = ["-ss", f"{start:.3f}"] if start else []  # before -i: fast, and frame-accurate when re-encoding
    trim = ["-t", f"{length:.3f}"] if length else []
    ff.run(["-xerror", *seek, "-i", clip, *trim, "-vf", vf, "-an", *ENCODE, out], file=clip,
           what="Normalizing clip (the file may be corrupt)")


MAX_SHIFT = 4.0   # how far into a clip the steadier start may be; Claude judged the first few seconds


def use_steadier_part(clip, out, hdr, skip, usable, slot, shake_now):
    """Swap `out` for a later, steadier stretch of the same clip. Returns (seconds in, new shake) or (0, old).

    A stabilizer was tried first and barely helped (her "shake" is mostly fast walking moves) while
    cropping the picture. Starting on a calmer stretch of the same clip fixes it with no effects.
    Each candidate is built and measured for real: a quick estimate on the raw clip was off by
    +-0.5, which is the whole margin on a borderline clip.
    """
    spare = min(usable - slot, MAX_SHIFT)
    if spare < 0.5:
        return 0.0, shake_now
    n = min(4, int(spare / 0.5))
    best, best_off, trial = shake_now, 0.0, out.with_name(out.stem + "_try.mp4")
    keep = out.with_name(out.stem + "_best.mp4")
    try:
        for off in (spare * k / n for k in range(1, n + 1)):
            normalize(clip, trial, hdr, slot, skip + off)
            score = quality.shake_score(quality.frames(trial))
            if score < best:
                best, best_off = score, off
                trial.replace(keep)
        if best < shake_now * 0.95:   # worth it only if clearly steadier
            keep.replace(out)
            return round(best_off, 2), best
        return 0.0, shake_now
    finally:
        trial.unlink(missing_ok=True)
        keep.unlink(missing_ok=True)


SNAP_WINDOW = 3.0   # move a cut up to this far to land between sentences
MIN_SLOT = 1.5      # every clip gets at least this much screen time


def _plan_slots_dp(durations, target_len, cands):
    """Exact small DP for proportional slot lengths snapped to candidate boundaries."""
    total = sum(durations)
    n, C, INF = len(durations), len(cands), 1e18
    if n < 2 or C < n + 1 or total <= 0:
        return None
    times, costs = [t for t, _ in cands], [c for _, c in cands]
    ideals = []
    acc = 0.0
    for d in durations[:-1]:
        acc += d
        ideals.append(target_len * acc / total)
    best, back = np.full((n, C), INF), np.zeros((n, C), dtype=int)
    for i in range(n):
        for c in range(1, C):
            if i == n - 1 and c != C - 1:
                continue
            for a in ([0] if i == 0 else range(1, c)):
                length = times[c] - times[a]
                if length < MIN_SLOT - 1e-6 or length > durations[i] + 1e-6:
                    continue
                prev = 0.0 if i == 0 else best[i - 1][a]
                if prev >= INF / 2:
                    continue
                pen = 0.0 if i == n - 1 else abs(times[c] - ideals[i]) + costs[c] * 0.35
                v = prev + pen
                if v < best[i][c]:
                    best[i][c], back[i][c] = v, a
    if best[n - 1][C - 1] >= INF / 2:
        return None
    edges, c = [C - 1], C - 1
    for i in range(n - 1, 0, -1):
        c = back[i][c]
        edges.append(c)
    edges = [0.0] + [times[e] for e in reversed(edges)]
    return [edges[i + 1] - edges[i] for i in range(n)]


def plan_slots(durations, target_len, words):
    """Give every clip a share of the voice so the picture keeps pace with the story.

    Each clip's share is proportional to its length, then each cut is moved to the
    nearest natural phrase or sentence break when clip durations allow it.
    """
    if words and len(durations) > 1:
        phr = match.phrases(words, target_len)
        got = _plan_slots_dp(durations, target_len, match.phrase_cut_points(phr, target_len))
        if got:
            return got
        got = _plan_slots_dp(durations, target_len, match.cut_points(words, phr, target_len))
        if got:
            return got
    words = words or []
    total = sum(durations)
    if total <= 0:
        return None
    def breaks_after(marks):
        return [words[i + 1]["start"] for i, w in enumerate(words[:-1]) if w["word"].endswith(marks)]

    sentence, comma = breaks_after((".", "?", "!")), breaks_after((",", ";", ":"))
    cuts, acc = [], 0.0
    for d in durations[:-1]:
        acc += d
        ideal = target_len * acc / total
        cut = ideal
        for marks in (sentence, comma):  # prefer a sentence end, else a comma
            near = [b for b in marks if abs(b - ideal) <= SNAP_WINDOW]
            if near:
                cut = min(near, key=lambda b: abs(b - ideal))
                break
        cuts.append(cut)
    edges = [0.0, *cuts, target_len]
    # keep cuts in order, every slot at least MIN_SLOT, and no slot longer than its clip
    for i in range(1, len(edges) - 1):
        edges[i] = max(edges[i], edges[i - 1] + MIN_SLOT)
        edges[i] = min(edges[i], edges[i - 1] + durations[i - 1])
    slots = [edges[i + 1] - edges[i] for i in range(len(durations))]
    if slots[-1] > durations[-1] or slots[-1] < MIN_SLOT * 0.5:
        return None  # couldn't fit cleanly; caller falls back to play-in-order
    return slots


def clip_names(clips):
    """What Alex calls each clip: its place in her list, plus the name of the file she uploaded."""
    try:  # the web page saves uploads as 01, 02...; her own names are kept beside them
        orig = json.loads((Path(clips[0]).parent / "original_names.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        orig = [c.name for c in clips]
    return [f"Clip {i + 1} ({orig[i]})" if i < len(orig) else f"Clip {i + 1}" for i in range(len(clips))]


def apply_pins(clips, checked, slots, pins):
    """Her "Change this shot" choices, applied to the finished plan (matched or even). Returns (checked, slots, pinned)
    where pinned = {"applied": [...], "skipped": [...]} for the report."""
    index = {str(c): i for i, c in enumerate(clips)}
    usable = [0.0] * len(clips)
    for c, d, _, _ in checked:
        usable[index[str(c)]] = d
    order = [index[str(c)] for c, _, _, _ in checked]
    if slots:
        new_order, new_slots, applied, skipped = match.apply_pins(order, slots, usable, pins)
    else:   # her clips play in full, one after another: only the order can change, lengths are not a limit
        new_order, _, applied, skipped = match.apply_pins(order, [usable[i] for i in order],
                                                          [float("inf")] * len(clips), pins)
        new_slots = None
    by_index = {index[str(t[0])]: t for t in checked}
    for a in applied:
        print(f"  your choice: Clip {a['clip']} at {a['start']:.1f}s")
    for s in skipped:
        print(f"  couldn't use your choice of Clip {s['pin']['clip']} at {s['pin']['start']:.1f}s: {s['why']}")
    return [by_index[i] for i in new_order], new_slots, {"applied": applied, "skipped": skipped}


def assemble(clips, target_len, job_dir, words=None, reuse=None):
    job_dir = Path(job_dir)
    norm_dir = job_dir / "normalized"
    norm_dir.mkdir(exist_ok=True)

    check_starts = settings.get("skip_dead_start")
    if check_starts and not deadstart.AVAILABLE:
        print("  note: PySceneDetect isn't installed, so shaky clip starts are kept "
              "(pip install --no-deps -r requirements-nodeps.txt)")
        check_starts = False
    checked = []  # (clip, usable seconds, hdr, seconds skipped at the start)
    for clip in clips:
        dur, hdr = check_clip(clip)
        skip, why = deadstart.find_start(clip, dur, hdr) if check_starts else (0.0, "")
        if skip:
            print(f"  {clip.name}: dead start ({why}), the clip will begin {skip:.1f}s in")
        checked.append((clip, dur - skip, hdr, skip))
    total = sum(d for _, d, _, _ in checked)
    skipped = sum(s for _, _, _, s in checked)
    if total < target_len * MAX_SHORTFALL_RATIO:
        left_out = f" (not counting {skipped:.1f}s of shaky or dark clip starts left out)" if skipped else ""
        raise AlexError(f"Your clips add up to {total:.0f} seconds{left_out} but the voice-over is {target_len:.0f} seconds, "
                        f"so there isn't enough picture to cover it. About {target_len - total:.0f} more seconds of clips would do it.")
    if total > target_len * MAX_OVERRUN_RATIO:
        raise AlexError(f"Your clips add up to {total:.0f} seconds but the voice-over is only {target_len:.0f} seconds, "
                        "so almost all of the footage would be left out. Please check that the voice-over belongs with these clips.")

    raw_labels = settings.clean_clip_labels(settings.get("clip_labels"), max_clips=len(clips))
    label_by_clip = {str(clips[i]): raw_labels.get(str(i + 1), "") for i in range(len(clips))}
    ordered_labels = [label_by_clip.get(str(c), "") for c, _, _, _ in checked]

    slots, clip_match, match_warnings = None, None, []
    if words and total > target_len and len(checked) > 1:
        slots = plan_slots([d for _, d, _, _ in checked], target_len, words)
        if settings.get("match_clips"):
            print("  matching clips to what you say...")
            matched, clip_match, match_warnings = match.plan(checked, words, target_len, slots, job_dir, reuse,
                                                             clip_names(clips), labels=ordered_labels)
            if matched:
                slots = matched
                order = clip_match.get("order")
                if order:   # Claude found clips that belong at a different moment: play them there
                    checked = [checked[i] for i in order]
                    for line in clip_match.get("moved", []):
                        print(f"  {line}")
            else:
                print(f"  kept the even split ({clip_match.get('reason', '')})")

    pre_pin_mid = match.mid_phrase_cuts(slots, words, target_len) if (words and slots) else []
    pins, pinned = settings.get("pins"), None
    if pins and len(checked) > 1:
        checked, slots, pinned = apply_pins(clips, checked, slots, pins)
        match_warnings = list(match_warnings) + [
            {"clip": None, "kind": "pin", "message": f"I couldn't show Clip {s['pin']['clip']} at "
             f"{int(s['pin']['start'] // 60)}:{int(s['pin']['start'] % 60):02d} as you asked: {s['why']}. "
             "The rest of your changes are in."} for s in pinned["skipped"]]

    normed, used, timeline, steadied, unused = [], 0.0, [], [], []
    for i, (clip, dur, hdr, skip) in enumerate(checked, 1):
        out = norm_dir / f"{i:02d}.mp4"
        after = f" after the skipped {skip:.1f}s" if skip else ""
        lbl = label_by_clip.get(str(clip), "")
        if slots:
            print(f"  {clip.name}: {used:.1f}s-{used + slots[i - 1]:.1f}s of the voice "
                  f"(first {slots[i - 1]:.1f}s of {dur:.1f}s{after}{', HDR->SDR' if hdr else ''})")
            normalize(clip, out, hdr, slots[i - 1], skip)
            row = {"clip": clip.name, "start": round(used, 2), "end": round(used + slots[i - 1], 2),
                   "skipped_start": skip}
            if lbl:
                row["label"] = lbl
            timeline.append(row)
            used += slots[i - 1]
        else:
            # Not enough footage to share out: play in order, cut the end / hold last frame
            if used >= target_len:
                print(f"  skipping {clip.name} (voice already covered)")
                unused.append(clip.name)
                continue
            print(f"  normalizing {clip.name} ({dur:.1f}s{after}{', HDR->SDR' if hdr else ''})")
            normalize(clip, out, hdr, start=skip)
            row = {"clip": clip.name, "start": round(used, 2), "end": round(min(used + dur, target_len), 2),
                   "skipped_start": skip}
            if lbl:
                row["label"] = lbl
            timeline.append(row)
            used += dur
        if slots and settings.get("stabilize"):
            shake = quality.shake_score(quality.frames(out))
            if shake > quality.SHAKE_PX:
                off, now = use_steadier_part(clip, out, hdr, skip, dur, slots[i - 1], shake)
                if off:
                    print(f"    {clip.name} was shaky at the start, using a steadier part {off:.1f}s in "
                          f"(shake {shake:.1f} -> {now:.1f})")
                    steadied.append(clip.name)
        if pinned and any(a["pos"] == i - 1 for a in pinned["applied"]) and timeline and timeline[-1]["clip"] == clip.name:
            timeline[-1]["pinned"] = True   # her own choice for this moment
        normed.append(out)
    (job_dir / "timeline.json").write_text(json.dumps(timeline, indent=1), encoding="utf-8")

    ends = [t["end"] for t in timeline]   # differences of the saved ends, so rounding never builds up over many clips
    tl_slots = [b - a for a, b in zip([0.0] + ends, ends)]
    mid_cuts = match.mid_phrase_cuts(tl_slots, words, target_len) if (words and len(tl_slots) > 1) else []
    if mid_cuts:
        n_mid = len(mid_cuts)
        by_pin = bool(pinned and pinned.get("applied") and len(mid_cuts) > len(pre_pin_mid))
        why = ("to fit your pinned shot choice" if by_pin
               else "because a clip was too short to reach the next pause in your voice-over")
        match_warnings = list(match_warnings) + [{
            "clip": None, "kind": "cut",
            "message": f"{n_mid} cut{'s' if n_mid != 1 else ''} had to land mid-sentence {why}."
        }]

    listing = job_dir / "concat.txt"
    listing.write_text("".join(f"file 'normalized/{p.name}'\n" for p in normed), encoding="utf-8")
    joined = job_dir / "joined.mp4"
    ff.run(["-f", "concat", "-safe", "0", "-i", listing.name, "-c", "copy", joined.name],
           cwd=job_dir, what="Joining clips")

    shortfall = max(0.0, target_len - total)
    vf = f"tpad=stop_mode=clone:stop_duration={shortfall + 0.1:.3f}," if shortfall else ""
    fitted = job_dir / "video.mp4"
    ff.run(["-i", joined, "-vf", vf + "setpts=PTS-STARTPTS", "-t", f"{target_len:.3f}", *ENCODE, fitted],
           what="Fitting video to voice length")
    return fitted, {"clips_total": round(total, 2), "voice": round(target_len, 2),
                    "cut_from_end": round(max(0.0, total - target_len), 2),
                    "timeline": timeline,
                    "mid_phrase_cuts": mid_cuts,
                    "clip_match": clip_match, "match_warnings": match_warnings,
                    "steadied": steadied, "moved": (clip_match or {}).get("moved", []),
                    "unused": unused, "pins": pinned,
                    "skipped_start": {c.name: s for c, _, _, s in checked if s},
                    "short_by": round(shortfall, 2)}
