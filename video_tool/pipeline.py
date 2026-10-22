"""Dump and go: python -m video_tool.pipeline <folder>

Folder = numbered clips (01.mp4, 02.mp4...) + an optional voice-over (voice.m4a).
No voice-over = talking to camera: the clips' own sound is used, and long videos
are split into parts under 60s.
Result = out/<job_id>/final.mp4 (or final_part1.mp4, final_part2.mp4...) + copy.json.

Long video mode (setting "highlights" or --highlights N, ONE long talking clip): up to N Shorts are cut from its best
moments (final_part1.mp4...), plus a YouTube post for the whole video (longform.json) and its captions (long.srt).
"""
import argparse
import json
import subprocess
import re
import math
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from . import (ai, assemble, captions, denoise, facecrop, ff, final_checks, highlights, layout, local_copy, longform, match, music, own_audio,
               quality, settings, silence, transcribe, writer)
from .config import (FFMPEG, AUDIO_RATE, FPS, HEIGHT, MAX_SECONDS, OUT_DIR, PEAK_LIMIT_DB, TRUE_PEAK_TARGET, VOICE_LUFS,
                     WIDTH)
from .errors import AlexError
from .inputs import find_inputs


def choose_track(folder, opts, no_music=False):
    """The music for this video: her pick (setting music_track), else a track chosen from the upload folder's name. A
    remake keeps the folder, so it keeps the track; different videos rotate through the installed tracks."""
    if no_music or not opts.get("music_on"):
        return None
    return music.pick_track(opts.get("music_track", ""), seed=Path(folder).resolve().name)


def loudness_filter(job_dir, src, window):
    """Two passes: measure the sound, then apply exactly what is needed. One pass lands 1-2 dB under the target."""
    base = f"loudnorm=I={VOICE_LUFS}:TP={TRUE_PEAK_TARGET}:LRA=11"
    try:
        p = subprocess.run([FFMPEG, "-hide_banner", "-nostats", *window, "-i", src, "-vn", "-af", base + ":print_format=json",
                            "-f", "null", "-"], cwd=job_dir, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300)
        m = json.loads(re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p.stderr, re.S).group(0))
        vals = {k: float(m[k]) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
        if not all(math.isfinite(v) for v in vals.values()):
            return base   # silence or near-silence: nothing to measure
        return (f"{base}:measured_I={vals['input_i']}:measured_TP={vals['input_tp']}:measured_LRA={vals['input_lra']}"
                f":measured_thresh={vals['input_thresh']}:offset={vals['target_offset']}:linear=true")
    except Exception:
        return base   # if measuring fails the single pass still works


# Last safety on the sound before encoding: catches peaks the leveller lets through (its own overshoot, the music added on
# top). Run at 4x the sample rate so it also catches the peaks BETWEEN samples, which is where AAC encoding pushes a file
# over. Only touches the loudest instants; speech level is unchanged. level=0: never turns the sound up.
def peak_guard(ceiling_db):
    return (f"aresample={4 * int(AUDIO_RATE)},alimiter=limit={10 ** (ceiling_db / 20):.4f}:attack=1:release=20:level=0"
            f":latency=1,aresample={AUDIO_RATE}")


PEAK_GUARD = peak_guard(TRUE_PEAK_TARGET)


def measure_loudness(path):
    """Integrated loudness (LUFS) and true peak (dBTP) of a file's sound, as a player decodes it. None if unreadable."""
    p = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128=peak=true",
                        "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    tail = p.stderr[p.stderr.rfind("Summary:"):]
    i, tp = re.search(r"I:\s+(-?[\d.]+|-inf)", tail), re.search(r"Peak:\s+(-?[\d.]+|-inf)", tail)
    if not i or not tp:
        return None
    return {"lufs": float(i.group(1)), "true_peak": float(tp.group(1))}


def enforce_peak(job_dir, out):
    """Re-measure the ENCODED file; if AAC pushed a peak over PEAK_LIMIT_DB, shave just those peaks (picture copied).
    Each try starts again from the first encode, so the finished sound has been re-encoded once at most.
    Returns the true peak after the check, or None if it could not be measured."""
    job_dir = Path(job_dir)
    m = measure_loudness(job_dir / out)
    if not m or not math.isfinite(m["true_peak"]) or m["true_peak"] <= PEAK_LIMIT_DB:
        return m and m["true_peak"]
    first = Path(out).with_name(Path(out).stem + "_loud.mp4")
    tmp = Path(out).with_name(Path(out).stem + "_peak.mp4")
    (job_dir / out).replace(job_dir / first)
    orig = peak = m["true_peak"]
    ceiling = PEAK_LIMIT_DB
    try:
        for k in range(4):   # usually one try. A lower ceiling on the peaks only: speech loudness stays where it was
            ceiling -= peak - PEAK_LIMIT_DB + 0.2
            shave = (peak_guard(ceiling) if k < 3
                     else f"volume={PEAK_LIMIT_DB - 0.5 - orig:.2f}dB")   # last resort: everything down
            ff.run(["-i", first, "-map", "0:v", "-map", "0:a", "-c:v", "copy", "-af", shave,
                    "-c:a", "aac", "-b:a", "192k", "-ar", AUDIO_RATE, "-map_metadata", "-1", "-movflags", "+faststart",
                    tmp], cwd=job_dir, what="Final sound level check")
            m = measure_loudness(job_dir / tmp)
            if not m:
                break
            peak = m["true_peak"]
            if peak <= PEAK_LIMIT_DB:
                break
        if m:
            (job_dir / tmp).replace(job_dir / out)
    finally:
        if not (job_dir / out).exists():
            (job_dir / first).replace(job_dir / out)   # never lose the video over the level check
        (job_dir / first).unlink(missing_ok=True)
        (job_dir / tmp).unlink(missing_ok=True)
    m = measure_loudness(job_dir / out)
    return m and m["true_peak"]


# Optional gentle ducking (setting music_duck, off by default): while she speaks the music bed dips a few more dB under
# the 20 LU it already sits under her voice, and comes back up in her pauses. Sidechain = the levelled voice drives a
# compressor on the music only. Slow attack and a long release so it moves smoothly with her phrases and never "pumps"
# on single syllables. Threshold -22 dBFS, ratio 2: her levelled speech (about -15 dBFS RMS) takes about 4 dB
# off the bed. makeup=1: the music is never turned UP. The voice itself is untouched; the peak guard still runs last.
DUCK = "sidechaincompress=threshold=0.0794:ratio=2:attack=150:release=900:knee=4:detection=rms:link=maximum:makeup=1"


def music_graph(level, music_in, duck):
    """Audio part of the render graph when there is music: voice (levelled) + bed, optional ducking, then the peak guard."""
    if not duck:
        return f"{level}[vo];[vo]{music_in}amix=inputs=2:duration=first:normalize=0,{PEAK_GUARD}[a]"
    return (f"{level},asplit=2[vo][key];{music_in}[key]{DUCK}[bed];"
            f"[vo][bed]amix=inputs=2:duration=first:normalize=0,{PEAK_GUARD}[a]")


MAX_RATE, BUF_SIZE = "8M", "16M"   # keeps a minute of busy footage near 60 MB at most: quick to send to a phone


def render(job_dir, video, ass, out, voice=None, music_wav=None, ss=None, dur=None, duck=None):
    """Burn captions, level the voice, mix optional music, encode for TikTok/Reels/Shorts.

    ss/dur pick one part of a long video. They go on the picture AND the separate
    sound (when there is one), so both start at the same moment and stay in sync.
    """
    window = ["-ss", f"{ss:.3f}", "-t", f"{dur:.3f}"] if ss is not None else []
    args = [*window, "-i", video]
    src = "[0:a]"
    if voice:
        args += [*window, "-i", voice]
        src = "[1:a]"
    level = f"{src}{loudness_filter(job_dir, voice or video, window)},aresample={AUDIO_RATE},aformat=channel_layouts=stereo"
    if music_wav:
        args += ["-i", music_wav]
        if duck is None:
            duck = settings.get("music_duck")
        audio = music_graph(level, f"[{2 if voice else 1}:a]", duck)
    else:
        audio = f"{level},{PEAK_GUARD}[a]"
    graph = f"[0:v]subtitles={ass}[v];{audio}"
    ff.run([*args, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-maxrate", MAX_RATE, "-bufsize", BUF_SIZE,
            "-pix_fmt", "yuv420p", "-r", FPS,
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-c:a", "aac", "-b:a", "192k", "-ar", AUDIO_RATE, "-shortest", "-map_metadata", "-1", "-map_chapters", "-1",
            "-movflags", "+faststart", out], cwd=job_dir, what="Final render")
    return enforce_peak(job_dir, out)


def fix_words(words):
    """Apply her word fixes. A word she fixed is no longer 'uncertain', so it loses Whisper's probability."""
    fixed = captions.apply_fixes(words)
    for a, b in zip(words, fixed):
        if a["word"] != b["word"]:
            b.pop("p", None)
    return fixed


def wide_warnings(clips, timeline, hers, limit=3):
    """Voice-over mode fills the whole frame, so a clip filmed sideways only shows its middle. A plain heads-up."""
    used = {}
    for t in timeline:
        used[t["clip"]] = used.get(t["clip"], 0.0) + (t["end"] - t["start"])
    found = []
    for c in clips:
        if used.get(c.name, 0.0) <= quality.MIN_USED:
            continue
        try:
            w, h = facecrop.display_size(ff.probe(c))
        except Exception:
            continue
        if layout.horizontal():   # a YouTube video: upright clips are shown whole unless she chose to fill the screen
            if layout.shows_whole(w, h) or layout.is_landscape(w, h):
                continue
            shown = layout.shown_share(w, h)
            if shown < 0.75:
                found.append((shown, hers.get(c.name, c.name), "tall"))
            continue
        shown = (WIDTH / HEIGHT) / (w / h)    # share of the width that still shows
        if shown < 0.75:
            found.append((shown, hers.get(c.name, c.name), w > h * 1.05))
    found.sort()
    out = [{"clip": n, "kind": "wide", "message": f"{n} was filmed upright, so its top and bottom are cut off to fill the screen."
            if side == "tall" else f"{n} was filmed sideways, so only the middle of it shows."
            if side else f"{n} is wider than a phone screen, so only the middle of it shows."}
           for _, n, side in found[:limit]]
    if len(found) > limit:
        more = len(found) - limit
        out.append({"clip": None, "kind": "wide", "message": f"{more} more clip{'s were' if more > 1 else ' was'} "
                    f"filmed wider than a phone screen, so only the middle of {'them' if more > 1 else 'it'} shows."})
    return out


def not_shown_report(job, words, length):
    """What she says that none of her clips show, from the clip-matching scores already saved (no AI call)."""
    try:
        data = json.loads((job / "match" / "scores.json").read_text(encoding="utf-8"))
        phr = match.phrases(words, length)
        texts = [p["text"] for p in phr]
        if data.get("phrases") != texts:
            return []
        times = [[p["start"], p["end"]] for p in phr]
        return match.not_shown(data["scores"], times, texts)
    except (OSError, ValueError, KeyError, TypeError):
        return []


def short_footage_report(job, words, length):
    """Things she talks about for longer than her footage of them lasts, from the saved clip scores (no AI call)."""
    try:
        data = json.loads((job / "match" / "scores.json").read_text(encoding="utf-8"))
        phr = match.phrases(words, length)
        texts = [p["text"] for p in phr]
        if data.get("phrases") != texts or not data.get("usable"):
            return []
        times = [[p["start"], p["end"]] for p in phr]
        return match.short_footage(data["scores"], times, texts, data["usable"], data.get("names"))
    except (OSError, ValueError, KeyError, TypeError):
        return []


def verify(final):
    info = ff.probe(final)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = [s for s in info["streams"] if s["codec_type"] == "audio"]
    problems = []
    if (v["width"], v["height"]) != layout.size():
        problems.append(f"size is {v['width']}x{v['height']}")
    if v["codec_name"] != "h264" or not a or a[0]["codec_name"] != "aac":
        problems.append("codecs aren't H.264/AAC")
    if layout.horizontal():
        if info["duration"] >= layout.H_MAX_SECONDS + 0.5:
            problems.append(f"length is {info['duration']:.1f}s (YouTube videos made here are at most 15 minutes)")
    elif info["duration"] >= 60:
        problems.append(f"length is {info['duration']:.1f}s (Shorts must be under 60s)")
    if problems:
        raise AlexError("Finished video failed its final check: " + "; ".join(problems), final)
    return info["duration"]


def finished_warnings(video, ass, words, timeline, hers, peak, part=None, offset=0.0):
    """The finished-video checks (black picture, frozen picture, drift, repeats, captions off the frame, true peak) as
    plain warnings. Read-only; a check that fails to run gives no warning and never stops the video."""
    print("   Checking the finished video (black or frozen picture, repeats, captions in frame)...")
    try:
        found = final_checks.check(video, ass if Path(ass).exists() else None, words,
                                   final_checks.spans_from_timeline(timeline, hers, offset), peak)
    except Exception as e:
        print(f"  finished-video check skipped: {type(e).__name__}: {str(e)[:200]}")
        return []
    for w in found:
        if part:
            w["part"] = part
            w["message"] = f"Part {part}: {w['message']}"
        print(f"  HEADS UP: {w['message']}")
    return found


def youtube_post(job, words, total, no_writer=False):
    """Horizontal (YouTube) video: the same YouTube post writer as long video mode (longform.json, long.srt). Only her
    words go to Claude, and only when a key is set; otherwise the plain post built from her own words."""
    print("6/6 Writing the YouTube post (title, description with chapters, tags)...")
    try:
        kit = longform.build_kit(words, total, key=False if no_writer else None)
    except Exception as e:   # the video is done: never lose it over the post
        print(f"  YouTube post failed, using the plain one: {type(e).__name__}: {str(e)[:300]}")
        kit = longform.fallback_kit(words, total)
    (job / "longform.json").write_text(json.dumps(kit, indent=2, ensure_ascii=False), encoding="utf-8")
    longform.write_long_srt(words, job / "long.srt")
    if kit.get("ai_cost_usd") and (job / "copy.json").exists():
        copy = json.loads((job / "copy.json").read_text(encoding="utf-8"))
        copy["ai_cost_usd"] = round(float(copy.get("ai_cost_usd") or 0) + float(kit["ai_cost_usd"]), 4)
        (job / "copy.json").write_text(json.dumps(copy, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"file": "longform.json", "srt": "long.srt"}


NEEDS_ONE_LONG_VIDEO = "Long video mode needs one long video; I made a normal video instead."


def long_video(a, opts, clips, job, job_id, t0):
    """Long video mode: the Shorts (highlights.run), then the YouTube post and captions for the whole long video."""
    report = highlights.run(a.folder, clips[0], job_id, opts["highlights"], opts, a.reuse, a.no_music, a.no_writer)
    words = fix_words(json.loads((job / "words.json").read_text(encoding="utf-8")))
    total = ff.probe(clips[0])["duration"]
    print("6/6 Writing the YouTube post for the whole video...")
    try:
        kit = longform.build_kit(words, total, key=False if a.no_writer else None)
    except Exception as e:   # the Shorts are done: never lose them over the post
        print(f"  YouTube post failed, using the plain one: {type(e).__name__}: {str(e)[:300]}")
        kit = longform.fallback_kit(words, total)
    (job / "longform.json").write_text(json.dumps(kit, indent=2, ensure_ascii=False), encoding="utf-8")
    longform.write_long_srt(words, job / "long.srt")
    if kit.get("ai_cost_usd") and (job / "copy.json").exists():
        copy = json.loads((job / "copy.json").read_text(encoding="utf-8"))
        copy["ai_cost_usd"] = round(float(copy.get("ai_cost_usd") or 0) + float(kit["ai_cost_usd"]), 4)
        (job / "copy.json").write_text(json.dumps(copy, indent=2, ensure_ascii=False), encoding="utf-8")
    report["longform"] = {"file": "longform.json", "srt": "long.srt"}
    report["seconds_to_build"] = round(time.time() - t0)
    try:   # the website put her clips in the order she filmed them; one clip is never moved, but keep the note shape
        sort_note = json.loads((Path(clips[0]).parent / "auto_sort.json").read_text(encoding="utf-8"))
        if sort_note.get("moved"):
            report["clip_order"] = {"basis": sort_note.get("basis", "filmed"), "moved": sort_note["moved"]}
    except (OSError, ValueError, IndexError):
        pass
    (job / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print()
    for o in report["parts"]:
        print(f"DONE  {job / o['file']}  ({o['length']:.1f}s)")
    print(f"  YouTube post for the whole video ({kit['source']}): {job / 'longform.json'}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Turn Alex's clips into vertical short-form videos (TikTok, Reels, Shorts).")
    ap.add_argument("folder")
    ap.add_argument("--no-music", action="store_true", help="skip the music bed even if a track exists")
    ap.add_argument("--no-writer", action="store_true", help="skip titles/description")
    ap.add_argument("--job-id", help="use this job id instead of folder name + time")
    ap.add_argument("--settings", help="JSON file of one-off setting overrides (from feedback)")
    ap.add_argument("--reuse", help="earlier job folder for the same files: reuse its transcript")
    ap.add_argument("--highlights", type=int, metavar="N", help="long video mode: cut up to N Shorts (0-5) from ONE long talking video")
    a = ap.parse_args(argv)

    t0 = time.time()
    overrides = json.loads(Path(a.settings).read_text(encoding="utf-8")) if a.settings else {}
    if a.highlights is not None:
        overrides["highlights"] = a.highlights
    opts = settings.configure(overrides)
    clips, voice = find_inputs(a.folder)
    job_id = a.job_id or f"{Path(a.folder).resolve().name}-{datetime.now():%Y%m%d-%H%M%S}"
    job = OUT_DIR / job_id
    job.mkdir(parents=True)
    # Where the clips came from, so a feedback remake on the website can find them again
    (OUT_DIR / f"{job_id}.source").write_text(str(Path(a.folder).resolve()), encoding="utf-8")
    mode = "voice-over" if voice else "talking to camera (clips' own sound)"
    print(f"Job {job_id}: {len(clips)} clip(s), {mode}")
    print("0/6 Getting your clips ready...")
    global _JOB
    _JOB = job
    lengths = [assemble.check_clip(c)[0] for c in clips]  # fail fast on a bad clip before the slow steps
    hl_note = None
    if opts["highlights"]:
        if not voice and len(clips) == 1 and lengths[0] > highlights.MIN_LONG:
            settings._current["orientation"] = opts["orientation"] = "vertical"   # Shorts are always upright
            return long_video(a, opts, clips, job, job_id, t0)
        hl_note = {"clip": None, "kind": "highlights", "message": NEEDS_ONE_LONG_VIDEO}
    if layout.horizontal() and not voice and sum(lengths) > layout.max_seconds():
        raise AlexError("A horizontal YouTube video made here can use up to 15 minutes of source clips. "
                        "Split the recording into shorter videos and add one at a time.", clips[0])
    track = choose_track(a.folder, opts, a.no_music)
    fit = {}

    if voice:
        print("1/6 Transcribing voice-over...")
        words = fix_words(transcribe.transcribe(voice, job, a.reuse))
        print(f"2/6 Trimming pauses over {settings.get('pause_gap'):g}s...")
        voice_wav, words, length = silence.trim_silence(voice, words, job)
        print(f"  {ff.probe(voice)['duration']:.1f}s -> {length:.1f}s")
        if layout.horizontal() and length > layout.max_seconds():
            raise AlexError(f"Voice-over is {length / 60:.1f} minutes even after trimming pauses. A YouTube video made here "
                            f"can be at most 15 minutes. Re-record about {(length - layout.max_seconds()) / 60 + 1:.0f} "
                            "minutes shorter, or split it into two videos.", voice)
        if not layout.horizontal() and length > MAX_SECONDS:
            raise AlexError(f"Voice-over is {length:.1f}s even after trimming pauses. Short-form videos must be "
                            f"under 60s. Re-record about {length - MAX_SECONDS + 1:.0f}s shorter.", voice)
        print("3/6 Normalizing and joining clips...")
        video, fit = assemble.assemble(clips, length, job, words, a.reuse)
        parts, voice_in = [(0.0, length)], voice_wav.name
    else:
        print("   Normalizing and joining clips...")
        video, words, parts, fit = own_audio.prepare(clips, job, a.reuse)
        words = fix_words(words)
        voice_in = None

    print("   Checking clip quality (shake, blur, light, audio)...")
    normed = sorted((job / "normalized").glob("*.mp4"))
    names = [t["clip"] for t in fit["timeline"]] if len(fit.get("timeline", [])) == len(normed) \
        else [clips[int(f.stem) - 1].name for f in normed]
    hers = dict(zip([c.name for c in clips], assemble.clip_names(clips)))   # "04.mp4" -> "Clip 4 (her file name)"
    names = [hers.get(n, n) for n in names]
    tl = fit.get("timeline", [])
    used = [t["end"] - t["start"] for t in tl] if len(tl) == len(normed) else None   # talking mode: every clip is in
    warnings = quality.analyze(job, normed, names, voice or job / "joined.mp4",
                               "your voice-over" if voice else "your clips", used)
    if voice:
        warnings += wide_warnings(clips, tl, hers)
    denoised = False
    if opts["denoise"]:
        print("   Removing background noise...")
        cleaned, problem = (denoise.clean(job / voice_in, job, "voice_clean.wav") if voice
                            else denoise.clean_clips(video, job))
        if cleaned:
            voice_in, denoised = cleaned.name, True
        else:
            warnings.append({"clip": None, "kind": "denoise", "message": problem})
    warnings += fit.pop("match_warnings", None) or []
    if hl_note:
        warnings.append(hl_note)
    for w in warnings:
        print(f"  HEADS UP: {w['message']}")

    outputs, unsure = [], []
    for n, (p0, p1) in enumerate(parts, 1):
        tag = f" part {n}/{len(parts)}" if len(parts) > 1 else ""
        name = "final.mp4" if len(parts) == 1 else f"final_part{n}.mp4"
        part_words = [{**w, "start": round(w["start"] - p0, 3), "end": round(w["end"] - p0, 3)}
                      for w in words if p0 <= w["start"] < p1]
        print(f"4/6 Captions{tag}...")
        ass = f"captions{'' if len(parts) == 1 else f'_part{n}'}.ass"
        _, lines = captions.write_ass(part_words, job, ass)
        print(f"  {len(lines)} caption lines")
        print(f"5/6 Music{tag}: {track.name if track else 'none'}")
        bed = music.music_bed(track, p1 - p0, job, f"music_bed_part{n}.wav").name if track else None
        print(f"   Rendering {name}...")
        peak = render(job, Path(video).name, ass, name, voice=voice_in, music_wav=bed,
                      ss=None if voice else p0, dur=None if voice else p1 - p0)
        part_len = verify(job / name)
        warnings += finished_warnings(job / name, job / ass, part_words, tl if voice else None, hers, peak,
                                      n if len(parts) > 1 else None, offset=p0)
        outputs.append({"file": name, "length": round(part_len, 2),
                        "size_mb": round((job / name).stat().st_size / 1048576, 1),
                        "text": " ".join(w["word"] for w in part_words)})
        if settings.get("captions_on"):
            unsure += [{**u, "part": n} for u in transcribe.uncertain_words(part_words)]
    final_len = outputs[0]["length"]

    copy_status = "skipped"
    transcript = " ".join(w["word"] for w in words)  # includes her word fixes
    (job / "transcript.txt").write_text(transcript, encoding="utf-8")
    kept, ai_note, copy_error = False, None, None
    if not layout.horizontal() and not a.no_writer and a.reuse:
        # A remake (bigger captions, different pauses) shouldn't rewrite the titles and captions she may already like:
        # keep them when the words are exactly the same
        old = Path(a.reuse)
        try:
            same = (old / "transcript.txt").read_text(encoding="utf-8").strip() == transcript.strip()
            old_copy = json.loads((old / "copy.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            same, old_copy = False, {}
        if same and old_copy.get("captions"):
            for name in ("copy.json", "ai_prompt.txt"):
                if (old / name).exists():
                    shutil.copy(old / name, job / name)
            copy_status, kept = "ok (kept)", True
            print("6/6 Keeping the titles and captions you already have (the words didn't change)")
    if not a.no_writer and not kept:
        if layout.horizontal():
            local_copy.write_local_copy(transcript, job, unsure=[u["word"] for u in unsure])
            copy_status = "ok (auto)"
        if not layout.horizontal() and writer.has_key():
            print("6/6 Writing titles, hook, description (AI)...")
            try:
                writer.write_copy(transcript, job)
                copy_status = "ok"
            except Exception as e:  # bad key, no credit, network: the video is done, never lose it
                print(f"  AI writer failed, using auto copy instead: {type(e).__name__}: {str(e)[:300]}")
                copy_error = ai.explain(e, "copy")
                ai_note = ("The AI title writer wasn't available this time, so these titles and captions are simpler ones "
                           "built from your own words. They're fine to use, or tap Remake video to try the AI again.")
        if copy_status != "ok" and not layout.horizontal():
            if not writer.has_key():
                print("6/6 Writing titles, hook, description (auto, from her own words)...")
            local_copy.write_local_copy(transcript, job, unsure=[u["word"] for u in unsure])
            copy_status = "ok (auto)"

    if not layout.horizontal() and len(parts) > 1 and (job / "copy.json").exists() and not kept:
        print(f"   Writing a title and captions for each of the {len(parts)} parts...")
        local_copy.add_part_copy(job, outputs, use_ai=writer.has_key() and not ai_note and not a.no_writer, unsure=unsure)
    if (job / "copy.json").exists():
        local_copy.attach_review_notes(job, transcript, [ai_note] if ai_note else ())

    match_error = (fit.get("clip_match") or {}).get("ai_error")
    ai_error = None
    if copy_error and match_error:
        ai_error = {"kind": copy_error["kind"], "message": ai.message(copy_error["kind"], "both")}
    elif copy_error or match_error:
        ai_error = copy_error or match_error
    extra = {}
    if voice:
        extra["not_shown"] = not_shown_report(job, words, length)
        extra["short_footage"] = short_footage_report(job, words, length)
    if layout.horizontal():   # a YouTube video: also the long-video post (title ideas, description with chapters, tags)
        old = Path(a.reuse) if a.reuse else None
        same = old and (old / "transcript.txt").exists() and (old / "transcript.txt").read_text(encoding="utf-8").strip() == transcript.strip()
        longform.write_long_srt(words, job / "long.srt")
        same_timing = old and (old / "long.srt").exists() and (old / "long.srt").read_bytes() == (job / "long.srt").read_bytes()
        if same and same_timing and (old / "longform.json").exists():
            shutil.copy(old / "longform.json", job / "longform.json")
            extra["longform"] = {"file": "longform.json", "srt": "long.srt"}
        else:
            extra["longform"] = youtube_post(job, words, final_len, a.no_writer)
        if (job / "copy.json").exists():
            cp = json.loads((job / "copy.json").read_text(encoding="utf-8"))
            kit = json.loads((job / "longform.json").read_text(encoding="utf-8"))
            cp.update(titles=kit["titles"], description=kit["description"], hook="", cta="",
                      captions={"youtube_shorts": kit["description"]}, orientation="horizontal")
            cp.pop("parts", None)
            (job / "copy.json").write_text(json.dumps(cp, indent=2, ensure_ascii=False), encoding="utf-8")
    skipped = [hers.get(n, n) for n in fit.pop("unused", [])]
    report = {"job_id": job_id, "mode": "voice" if voice else "own_audio", "parts": outputs,
              "final": str(job / outputs[0]["file"]), "length": round(final_len, 2),
              "music": track.name if track else None, "denoised": denoised, "copy": copy_status,
              "seconds_to_build": round(time.time() - t0), "settings": opts, "warnings": warnings,
              "skipped_clips": skipped, "uncertain_words": sorted(unsure, key=lambda u: u["p"])[:12], **extra, **fit}
    if ai_error:
        report["ai_error"] = ai_error
    try:   # the website put her clips in the order she filmed them before the video was made
        sort_note = json.loads((Path(clips[0]).parent / "auto_sort.json").read_text(encoding="utf-8"))
        if sort_note.get("moved"):
            report["clip_order"] = {"basis": sort_note.get("basis", "filmed"), "moved": sort_note["moved"]}
    except (OSError, ValueError, IndexError):
        pass
    (job / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print()
    for o in outputs:
        print(f"DONE  {job / o['file']}  ({o['length']:.1f}s)")
    if skipped:
        print(f"  NOTE: not used (the voice-over was already covered): {', '.join(skipped)}")
    if fit.get("short_by"):
        print(f"  NOTE: clips are {fit['short_by']:.1f}s short. The last frame is held. Add a clip to cover it.")
    if fit.get("cut_from_end"):
        print(f"  {fit['cut_from_end']:.1f}s of extra footage was trimmed so the clips fit the voice.")
    if copy_status == "skipped":
        print("  Copy: skipped")
    else:
        print(f"  Copy ({copy_status}): {job / 'copy.json'}")
        if copy_status == "ok (auto)":
            print(f"  For polished AI titles for free, paste {job / 'ai_prompt.txt'} into claude.ai")
    return 0


_JOB = None


if __name__ == "__main__":
    # Windows consoles and log files default to a code page with no emoji (her copy can use them)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        sys.exit(main())
    except AlexError as e:
        print(f"\nSTOPPED: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception:
        # Not her files: keep the technical detail beside the job, say it plainly, and let the log keep the rest
        import traceback

        try:
            if _JOB is not None and _JOB.exists():
                (_JOB / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        print("\nSomething unexpected went wrong on our side. Her files are fine.", file=sys.stderr)
        raise
