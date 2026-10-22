"""Talking-to-camera mode: no separate voice-over, the clips' own sound is used.

Pauses are cut out of picture and sound together (jump cuts), and anything
longer than one Short is split into parts at sentence breaks.
"""
import difflib
import math
import re
from pathlib import Path

from . import facecrop, ff, layout, settings, silence, transcribe
from .assemble import ENCODE, FRAME, check_clip
from .config import AUDIO_RATE, FPS, HEIGHT, MAX_SECONDS, SCALER, TONEMAP, WIDTH
from .errors import AlexError, NoSpeechError

MIN_PART = 15.0  # never make a part shorter than this unless the whole video is


FIT = (f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease:flags={SCALER},"
       f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={FPS},format=yuv420p")


def is_vertical(info):
    v = next(s for s in info["streams"] if s.get("codec_type") == "video")
    w, h = v["width"], v["height"]
    rot = next((abs(int(d.get("rotation", 0))) for d in v.get("side_data_list", []) if "rotation" in d), 0)
    if rot in (90, 270):
        w, h = h, w
    return h / w >= 1.5


def horizontal_frame(clip, info, mode):
    """Horizontal (16:9) video, talking to camera. Landscape clips fill the frame (a clip wider than 16:9 is cut around
    her face, as in a vertical video). Upright and square clips are shown whole with plain side bars, or, when she chose
    "fill the screen", one fixed crop of the top and bottom that keeps her face in. Never stretched, never moving."""
    W, H = layout.size()
    w, h = facecrop.display_size(info)
    if mode == "fit":
        return layout.fit_filter(), {"mode": "whole frame", "why": "shown whole as requested"}
    if layout.is_landscape(w, h):
        if mode == "auto":
            crop, why = facecrop.plan_crop(clip, info, (W, H))
            if crop:
                print(f"    framing: face-centred crop ({why})")
                return f"{crop},setsar=1,fps={FPS},format=yuv420p", {"mode": "face-centred", "why": why}
        return layout.fill_filter(), {"mode": "full screen", "why": ""}
    if layout.shows_whole(w, h):
        print("    framing: whole frame with bars at the sides (upright clip)")
        return layout.fit_filter(), {"mode": "whole frame", "why": "upright clip, shown whole"}
    crop, why = facecrop.plan_rows_crop(clip, info, (W, H)) if mode == "auto" else (None, "centre of the picture")
    print(f"    framing: fills the screen, top and bottom cut ({why})")
    if not crop:
        return layout.fill_filter(), {"mode": "full screen", "why": why}
    return f"{crop},setsar=1,fps={FPS},format=yuv420p", {"mode": "full screen, top and bottom cut", "why": why}


def normalize_av(clip, out, hdr):
    """Like voice-over mode, but keep the clip's audio (or add silence).

    A person talking to camera must stay in frame: square or landscape clips get a
    crop centred on her face, or are shown whole with black bars if that can't work.
    """
    info = ff.probe(clip)
    mode = settings.get("framing")
    framing = {"mode": "full screen", "why": ""}
    if layout.horizontal():
        frame, framing = horizontal_frame(clip, info, mode)
    elif is_vertical(info) or mode == "fill":
        frame = FRAME
    elif mode == "fit":
        frame = FIT
        framing = {"mode": "whole frame", "why": "asked for"}
    else:  # auto: keep her face in a full-screen crop, else show the whole frame
        crop, why = facecrop.plan_crop(clip, info)
        print(f"    framing: {'face-centred crop' if crop else 'whole frame with bars'} ({why})")
        frame = f"{crop},setsar=1,fps={FPS},format=yuv420p" if crop else FIT
        framing = {"mode": "face-centred" if crop else "whole frame", "why": why}
    vf = (TONEMAP if hdr else "") + frame
    silent = [] if ff.has_stream(info, "audio") else ["-f", "lavfi", "-i", f"anullsrc=r={AUDIO_RATE}:cl=stereo"]
    maps = ["-map", "0:v:0", "-map", "0:a:0" if not silent else "1:a:0", "-shortest"]
    ff.run(["-xerror", "-i", clip, *silent, *maps, "-vf", vf, *ENCODE,
            "-c:a", "aac", "-b:a", "192k", "-ar", AUDIO_RATE, "-ac", "2", out],
           file=clip, what="Normalizing clip (the file may be corrupt)")
    return framing


def join(clips, job):
    norm = job / "normalized"
    norm.mkdir(exist_ok=True)
    outs = []
    join.framing = []
    for i, clip in enumerate(clips, 1):
        dur, hdr = check_clip(clip)
        print(f"  normalizing {clip.name} ({dur:.1f}s{', HDR->SDR' if hdr else ''})")
        out = norm / f"{i:02d}.mp4"
        join.framing.append({"clip": clip.name, **normalize_av(clip, out, hdr)})
        outs.append(out)
    (job / "concat.txt").write_text("".join(f"file 'normalized/{p.name}'\n" for p in outs), encoding="utf-8")
    ff.run(["-f", "concat", "-safe", "0", "-i", "concat.txt", "-c", "copy", "joined.mp4"],
           cwd=job, what="Joining clips")
    return job / "joined.mp4"


def cut_pauses(job, segs):
    """Keep only the spoken segments, cutting video and audio together."""
    parts = "".join(f"[0:v]trim={a}:{b},setpts=PTS-STARTPTS[v{i}];"
                    f"[0:a]atrim={a}:{b},asetpts=PTS-STARTPTS{silence.join_fades(i, a, b, len(segs))}[a{i}];"
                    for i, (a, b) in enumerate(segs))
    chain = "".join(f"[v{i}][a{i}]" for i in range(len(segs)))
    graph = f"{parts}{chain}concat=n={len(segs)}:v=1:a=1[v][a]"
    # Long graphs go in a file: Windows caps command lines at ~32k characters
    (job / "cut_graph.txt").write_text(graph, encoding="utf-8")
    ff.run(["-i", "joined.mp4", "-/filter_complex", "cut_graph.txt", "-map", "[v]", "-map", "[a]", *ENCODE,
            "-c:a", "aac", "-b:a", "192k", "-ar", AUDIO_RATE, "trimmed.mp4"], cwd=job, what="Cutting pauses")
    return job / "trimmed.mp4"


def split_points(words, total):
    """Split into the fewest parts under MAX_SECONDS (15 minutes for a horizontal video), cutting between sentences."""
    MAX_SECONDS = layout.max_seconds()   # noqa: N806
    if total <= MAX_SECONDS:
        return [(0.0, total)]
    n = math.ceil(total / MAX_SECONDS)
    # a cut sits halfway through the pause after a sentence (or, failing that, any pause)
    sentence = [(w["end"] + words[i + 1]["start"]) / 2 for i, w in enumerate(words[:-1])
                if w["word"].endswith((".", "?", "!"))]
    anygap = [(w["end"] + words[i + 1]["start"]) / 2 for i, w in enumerate(words[:-1])]
    parts, start = [], 0.0
    while total - start > MAX_SECONDS:
        remaining = math.ceil((total - start) / MAX_SECONDS)
        ideal = start + (total - start) / remaining
        limit = start + MAX_SECONDS
        cut = None
        # prefer a sentence end near the even split; settle for any pause if none is close
        for cands, slack in ((sentence, 8.0), (anygap, 999.0)):
            ok = [c for c in cands if start + MIN_PART <= c <= limit and abs(c - ideal) <= slack]
            if ok:
                cut = min(ok, key=lambda c: abs(c - ideal))
                break
        cut = cut or limit
        parts.append((round(start, 3), round(cut, 3)))
        start = cut
    parts.append((round(start, 3), round(total, 3)))
    return parts


# ---------- retakes: she says a line, stumbles, and says it again ----------

RETAKE_MIN_WORDS = 6      # shorter lines repeat on purpose ("location, location, location")
RETAKE_SIMILAR = 0.85     # how alike the two takes read (letters); the words must also be 90% the same and differ in no number or content word
RETAKE_WITHIN = 45.0      # seconds between the start of the first take and the start of the repeat
SENTENCE_GAP = 0.8        # a pause this long also ends a sentence when Whisper wrote no full stop
TERMINAL = (".", "?", "!")


def _norm(text):
    """Lower-case words only: no punctuation, no fillers."""
    toks = re.sub(r"[^\w' ]+", " ", text.lower().replace("-", " ")).split()
    return [t.strip("'") for t in toks if t.strip("'") and t.strip("'") not in silence.FILLERS]


def sentences(words):
    """[(first index, one past last index)] of the sentences in a word list."""
    out, start = [], 0
    for i, w in enumerate(words):
        last = i == len(words) - 1
        if last or w["word"].endswith(TERMINAL) or words[i + 1]["start"] - w["end"] >= SENTENCE_GAP:
            out.append((start, i + 1))
            start = i + 1
    return out


# Words that can differ between two takes of the same sentence without changing what it says
_SMALL = {"a", "an", "the", "and", "or", "but", "so", "then", "of", "to", "in", "on", "at", "it", "its", "is", "are", "was",
          "that", "this", "i", "you", "we", "just", "really", "very", "actually", "basically", "like", "well", "now", "also"}
_NUMBER_WORDS = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
                 "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
                 "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "thousand", "million", "half",
                 "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
                 "single", "double", "triple", "once", "twice"}


def _is_number(tok):
    return any(ch.isdigit() for ch in tok) or tok in _NUMBER_WORDS


def _same_content(ta, tb):
    """True when two token lists say the same thing: they differ only by small words, a spelling or ending
    ('gutter'/'gutters') or a stutter, never by a number or a different word. 'check the roof' and 'check the
    gutters', or 'three bedrooms' and 'four bedrooms', are parallel lines, not one line said twice."""
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        x, y = ta[i1:i2], tb[j1:j2]
        if any(_is_number(t) for t in x + y):
            return False
        if op in ("delete", "insert"):
            if not all(t in _SMALL for t in x + y):
                return False
        elif len(x) == len(y):
            if not all(u == v or difflib.SequenceMatcher(None, u, v).ratio() >= 0.8 for u, v in zip(x, y)):
                return False
        elif not all(t in _SMALL for t in x + y):
            return False
    return True


def _alike(a, b):
    """Is b a second take of a? Near-identical wording with no differing content word or number, or the first take
    is a sentence that broke off (no full stop) and the repeat carries on from the same words."""
    ta, tb = _norm(a), _norm(b)
    if len(ta) < RETAKE_MIN_WORDS or len(tb) < RETAKE_MIN_WORDS:
        return False
    chars = difflib.SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()
    toks = difflib.SequenceMatcher(None, ta, tb).ratio()
    if chars >= RETAKE_SIMILAR and toks >= 0.9 and _same_content(ta, tb):
        return True
    # a stumble: the first take stops part-way (no full stop) and the repeat starts with exactly the same words
    return (not a.rstrip().endswith(TERMINAL) and len(tb) > len(ta) and ta == tb[:len(ta)]) and len(ta) >= RETAKE_MIN_WORDS


def find_retakes(words):
    """Lines said twice in a row: the earlier take is cut, the LAST one stays.

    Conservative on purpose. A retake is a sentence of at least 6 words repeated (85% alike) as the next
    sentence, or the one after when only a short false start sits between, within 45 seconds.
    Said three times in a row it is emphasis, not a retake, so nothing is cut.
    Returns [{"i0", "i1", "said", "removed_at", "kept_at"}]: words[i0:i1] go, the repeat starts at i1.
    """
    sents = sentences(words)
    text = [" ".join(w["word"] for w in words[a:b]) for a, b in sents]
    found, k = [], 0
    while k < len(sents):
        hit = None
        for j in (k + 1, k + 2):
            if j >= len(sents) or words[sents[j][0]]["start"] - words[sents[k][0]]["start"] > RETAKE_WITHIN:
                break
            if j == k + 2 and len(_norm(text[k + 1])) >= RETAKE_MIN_WORDS:
                break   # a whole sentence of other content sits between: not a restart
            if _alike(text[k], text[j]):
                hit = j
                break
        if hit is None:
            k += 1
            continue
        def again(n):   # the line said yet again right after, close enough in time to be the same run
            return (n + 1 < len(sents) and _alike(text[n], text[n + 1])
                    and words[sents[n + 1][0]]["start"] - words[sents[n][0]]["start"] <= RETAKE_WITHIN)

        if again(hit):   # three in a row: deliberate emphasis
            while again(hit):
                hit += 1
            k = hit + 1
            continue
        i0, i1 = sents[k][0], sents[hit][0]
        found.append({"i0": i0, "i1": i1, "said": text[k].strip(), "removed_at": round(words[i0]["start"], 2),
                      "kept_at": round(words[i1]["start"], 2)})
        k = hit
    return found


def drop_ranges(words, ranges):
    drop = {i for a, b in ranges for i in range(a, b)}
    return [w for i, w in enumerate(words) if i not in drop]


# ---------- opening: a greeting before the real first line ----------

GREETING = [
    (re.compile(r"^(hi|hey|hello|hiya|heya|howdy|good (morning|afternoon|evening))\b"), 9),
    (re.compile(r"^welcome\b"), 10),
    (re.compile(r"^(it'?s|it is|this is|i'?m|i am|my name is)( me)?,? (alex|alexe|jammy)\b"), 8),
    (re.compile(r"^(alex|alexe) (here|gordon)\b"), 8),
]
TODAY = re.compile(r"^(so |and |well |okay |ok )?(today|in today'?s video|in this video),? (i|we)\b")
OPENING_MAX = 12.0     # seconds: a greeting longer than this is content


def _clauses(words):
    out, start = [], 0
    for i, w in enumerate(words):
        last = i == len(words) - 1
        if last or w["word"].endswith((",", ";", ":", "?", "!", ".")) or words[i + 1]["start"] - w["end"] >= 0.6:
            out.append((start, i + 1))
            start = i + 1
    return out


def find_opening(words):
    """A greeting at the very start ("Hi everybody, it's Alex, welcome back"), and where the real first line begins.

    Returns {"said", "hook_index"} (words[hook_index] is the first word of the hook) or None.
    """
    if len(words) < 12:
        return None
    k, matched = 0, False
    for a, b in _clauses(words[:40])[:4]:
        text = " ".join(_norm(" ".join(w["word"] for w in words[a:b])))
        n = b - a
        if not text:
            k = b          # nothing but fillers
            continue
        hit = any(rx.match(text) and n <= cap for rx, cap in GREETING)
        if not hit and matched and TODAY.match(text) and n <= 8 and words[b - 1]["word"].endswith((".", "!")):
            hit = True      # "Today I want to show you." right after a greeting is more lead-in
        if not hit:
            break
        matched, k = True, b
    if not matched or len(words) - k < 8 or words[k]["start"] - words[0]["start"] > OPENING_MAX:
        return None
    first = next((i for i, w in enumerate(words) if _norm(w["word"])), 0)
    return {"said": " ".join(w["word"] for w in words[first:k]).strip(), "hook_index": k}


def _first_spoken(words, i0, i1):
    return next((w for w in words[i0:i1] if not silence.is_filler(w)), words[i0])


def _at(word, segs):
    got = silence.remap([word], segs)
    return got[0]["start"] if got else 0.0


FORGOT = "I couldn't hear you talking in these clips. Did you forget to add your voice-over?"


def _repeats(sure):
    """Share of distinct words in a run of words: low means the same few words over and over."""
    return len({re.sub(r"\W+", "", w["word"].lower()) for w in sure}) / max(len(sure), 1)


def check_speech(words, total, windowed=False):
    """She forgot the voice-over: a few stray words are not a talking video.

    Over music or noise Whisper invents words (often from its hint list, e.g. "Hiawatha, Linn County") and is
    almost never sure of them, so only words it was at least 10% sure of count as speech.
    windowed=True (long video mode): a long talk repeats its everyday words naturally, so "the same few words over and over"
    must hold for most of the video (100-word windows), not just add up over thousands of words.
    """
    sure = [w for w in words if w.get("p", 1.0) >= 0.1]
    if windowed and len(sure) > 150:
        wins = [sure[i:i + 100] for i in range(0, len(sure) - 99, 50)]
        looping = sum(_repeats(w) < 0.2 for w in wins) > len(wins) / 2
    else:
        looping = len(sure) >= 10 and _repeats(sure) < 0.2   # "Baa Baa Baa..." over a tone or hum
    if len(sure) < 6 or (total >= 10 and len(sure) / total < 0.3) or looping:
        raise AlexError(FORGOT)


def prepare(clips, job, reuse=None):
    """Returns (video with pauses cut, words on that timeline, parts, report fields)."""
    joined = join(clips, job)
    print("1/6 Transcribing what you say on camera...")
    audio = job / "clip_audio.wav"
    ff.run(["-i", joined, "-vn", "-ac", "1", "-ar", "16000", audio], what="Extracting clip audio")
    try:
        words = transcribe.transcribe(audio, job, reuse)
    except NoSpeechError:
        raise AlexError(FORGOT) from None
    total = ff.probe(joined)["duration"]
    check_speech(words, total)

    print(f"2/6 Trimming pauses over {settings.get('pause_gap'):g}s...")
    fields = {}
    work = words
    retakes = find_retakes(words) if settings.get("cut_retakes") else []
    if retakes:
        work = drop_ranges(words, [(r["i0"], r["i1"]) for r in retakes])
        for r in retakes:
            print(f"  cut a repeated line from {r['removed_at']:.1f}s (kept your last take): {r['said'][:60]}")
    opening = find_opening(work)
    hook_cut = False
    full_segs = silence.keep_segments(work, total)
    if opening:
        hook = work[opening["hook_index"]]
        first = _first_spoken(work, 0, opening["hook_index"])
        hook_at = _at(hook, full_segs)
        hook_cut = bool(settings.get("start_at_hook"))
        fields["opening"] = {"greeting_seconds": round(hook_at - _at(first, full_segs), 2), "said": opening["said"],
                             "hook_at": round(hook_at, 2), "applied": hook_cut}
        print(f"  Opening: '{opening['said']}' then the real first line at {hook_at:.1f}s"
              f"{' (starting there)' if hook_cut else ' (left in, as you recorded it)'}")
        if hook_cut:
            work = work[opening["hook_index"]:]
    segs = silence.keep_segments(work, total) if (retakes or hook_cut) else full_segs
    trimmed = cut_pauses(job, segs)
    if settings.get("cut_retakes"):
        # where each cut now falls in the finished video: at the start of the take that stayed
        fields["retakes"] = []
        for r in retakes:
            kept = _first_spoken(words, r["i1"], len(words))
            if any(w is kept for w in work):
                fields["retakes"].append({"at": round(_at(kept, segs), 2), "said": r["said"],
                                          "removed_at": r["removed_at"]})
    words = silence.remap(silence.clean(work), segs)
    length = ff.probe(trimmed)["duration"]
    print(f"  {total:.1f}s -> {length:.1f}s")
    parts = split_points(words, length)
    if len(parts) > 1:
        print(f"  Too long for one Short, so it will be {len(parts)} parts: "
              + ", ".join(f"{b - a:.0f}s" for a, b in parts))
    return trimmed, words, parts, {"original": round(total, 2), "trimmed": round(length, 2),
                                   "framing": getattr(join, "framing", []), **fields}
