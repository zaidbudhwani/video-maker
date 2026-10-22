"""Match clips to what she says (Claude vision), voice-over mode.

It decides WHEN each cut lands in the voice-over, so the deck is on screen while she says
"I love the large deck", and (auto_order) moves a clip that plainly belongs at another moment.

Claude sees still frames (labelled "Shot 1, frame 2", never filenames) plus the numbered
phrases of her voice-over, and scores every frame against every phrase. A small search
then picks the cut times that put the best-matching footage under each phrase.

Nothing here can stop a video: no key, an API error, a refusal, or a plan that isn't
clearly better all fall back to the even plan from assemble.plan_slots.
"""
import base64
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import ai, ff, localmatch, qwen, settings
from .config import CLAUDE_MODEL, TONEMAP

VERSION = 1                  # bump when the prompt or frame plan changes (invalidates cached scores)
OFFSETS = (0.5, 2, 4, 7, 11, 16, 23)   # seconds into the usable part of a clip; only clip starts are ever shown
CROP_VF = "scale=288:512:force_original_aspect_ratio=increase,crop=288:512"
FRAME_VF = CROP_VF + ",format=yuvj420p"
VIDEO_CAP = 8_000_000        # qwen_video: all shots together stay under this many bytes in one request
VIDEO_LADDER = ((12, 30, 1.2), (10, 34, 1.2), (8, 36, 1.0), (6, 38, 0.8), (5, 42, 0.6))   # (fps, crf, segment seconds)
MIN_SLOT = 1.5
LONG_PHRASE = 2.0            # sentences longer than this are split at commas
DT = 0.05
CUT_COST = {"sentence": 0.0, "comma": 1.0, "word": 4.0}   # in relevance-seconds (10 = 1s of perfect match)
NEAR_SENTENCE_END = 8.0      # cutting one or two words before a sentence ends looks like a mistake
MAX_FRAMES = 80              # keeps one request around 10 cents at most
GATE_GAIN, GATE_SECONDS = 1.10, 2.0   # use the matched plan only if clearly better than the even one
ANCHOR = 8                   # a clip "belongs" to a phrase when a frame scores at least this
REORDER_GAIN = 1.05          # move clips only if the new order beats hers by this much
MAX_MOVES, MOVE_GAIN = 3, 5.0   # move at most this many clips, each only if it adds this much relevance

PROMPT = """Above are frames from {n} short phone video shots, in the order they will be played, and below are the
numbered phrases of the voice-over that will play over them.

For EVERY frame, score how well that frame would illustrate each phrase, from 0 to 10:
10 = the phrase names exactly what is on screen; 5 = neutral (the phrase doesn't describe anything visible,
e.g. a greeting or call to action, so any shot is fine); 0 = the frame clearly shows something different
from what the phrase describes. Judge only from what is visible.

Phrases:
{phrases}

Return one row per frame (shot number, frame number, and a list of exactly {m} integer scores in phrase order)."""

VIDEO_PROMPT = """Above are {n} short phone video shots, in the order they will be played. Each shot is a short video made
of consecutive segments (segment 1, segment 2, ...), each taken from a different moment of the original clip. Below are
the numbered phrases of the voice-over that will play over them.

For EVERY segment of every shot, score how well that segment would illustrate each phrase, from 0 to 10:
10 = the phrase names exactly what is on screen; 5 = neutral (the phrase doesn't describe anything visible,
e.g. a greeting or call to action, so any shot is fine); 0 = the segment clearly shows something different
from what the phrase describes. Judge only from what is visible.

Phrases:
{phrases}

Return one row per segment (shot number, segment number as "frame", and a list of exactly {m} integer scores in phrase order)."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["rows"],
          "properties": {"rows": {"type": "array", "items": {
              "type": "object", "additionalProperties": False, "required": ["shot", "frame", "scores"],
              "properties": {"shot": {"type": "integer"}, "frame": {"type": "integer"},
                             "scores": {"type": "array", "items": {"type": "integer"}}}}}}}


def phrases(words, total):
    """Timed phrases: sentences, with long ones split at commas. Gaps belong to the phrase before."""
    sents, cur = [], []
    for w in words:
        cur.append(w)
        if w["word"].endswith((".", "?", "!")):
            sents.append(cur)
            cur = []
    if cur:
        sents.append(cur)
    out = []
    for s in sents:
        long = s[-1]["end"] - s[0]["start"] > LONG_PHRASE
        pieces, cur = [], []
        for w in s:
            cur.append(w)
            if long and w["word"].endswith((",", ";", ":")) and len(cur) >= 3:
                pieces.append(cur)
                cur = []
        if cur:
            if pieces and len(cur) < 3:
                pieces[-1] += cur
            else:
                pieces.append(cur)
        for j, p in enumerate(pieces):
            out.append({"text": " ".join(w["word"] for w in p), "start": p[0]["start"],
                        "sentence_end": j == len(pieces) - 1, "last_words": [w["start"] for w in p[-2:]]})
    for i, p in enumerate(out):
        p["start"] = 0.0 if i == 0 else p["start"]
        p["end"] = out[i + 1]["start"] if i + 1 < len(out) else total
    return out


def frame_times(usable):
    ts = [t for t in OFFSETS if t < usable - 0.3]
    return ts if len(ts) >= 2 else [min(0.3, usable / 4), usable / 2]


def extract_frames(checked, per_clip, out_dir):
    """checked: (clip, usable, hdr, skip). Frames are cropped like the final video so Claude sees what viewers see."""
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = [(i, j, clip, skip + t, hdr) for i, (clip, _, hdr, skip) in enumerate(checked)
            for j, t in enumerate(per_clip[i])]

    def one(job):
        i, j, clip, at, hdr = job
        out = out_dir / f"{i + 1:02d}_{j + 1}.jpg"
        ff.run(["-ss", f"{at:.2f}", "-i", clip, "-frames:v", "1", "-vf", FRAME_VF if not hdr else
                "scale=576:-2," + TONEMAP + FRAME_VF, "-q:v", "4", out], file=clip, what="Grabbing a frame")
        return out

    with ThreadPoolExecutor(4) as ex:
        paths = list(ex.map(one, jobs))
    grid, k = [], 0
    for ts in per_clip:
        grid.append(paths[k:k + len(ts)])
        k += len(ts)
    return grid


def cache_key(checked, per_clip, phr, engine="claude"):
    h = hashlib.sha1(f"{VERSION}|{CLAUDE_MODEL}".encode())
    if engine != "claude":
        model = f"{localmatch.MODEL_ID}|{localmatch.VERSION}" if engine == "clip" else ai.QWEN_MODEL
        h.update(f"|{engine}|{model}".encode())   # claude's key is unchanged, so saved scores still match
    for (clip, _, _, skip), ts in zip(checked, per_clip):
        h.update(f"{Path(clip).stat().st_size}|{skip:.2f}|{ts}".encode())   # size, not name: remakes keep the key
    h.update("|".join(p["text"].lower() for p in phr).encode())
    return h.hexdigest()


def _listing(phr):
    return "\n".join(f"{i + 1}. {p['text']}" for i, p in enumerate(phr))


def build_parts(frames, per_clip, phr, fmt="claude"):
    """The request content: 'Shot k, frame j (t s in):' + the still, then the prompt. fmt: claude | openrouter."""
    content = []
    for k, (paths, ts) in enumerate(zip(frames, per_clip), 1):
        for j, (p, t) in enumerate(zip(paths, ts), 1):
            data = base64.standard_b64encode(Path(p).read_bytes()).decode("ascii")
            content.append({"type": "text", "text": f"Shot {k}, frame {j} ({t:.1f}s in):"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}
                           if fmt == "claude" else
                           {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + data}})
    content.append({"type": "text", "text": PROMPT.format(n=len(frames), phrases=_listing(phr), m=len(phr))})
    return content


def build_video_parts(videos, per_clip, phr):
    """qwen_video: one video per shot (segment j = frame j), then the segment prompt."""
    content = []
    for k, (v, ts) in enumerate(zip(videos, per_clip), 1):
        data = base64.standard_b64encode(Path(v).read_bytes()).decode("ascii")
        content.append({"type": "text", "text": f"Shot {k} ({len(ts)} segments, played one after another):"})
        content.append({"type": "video_url", "video_url": {"url": "data:video/mp4;base64," + data}})
    content.append({"type": "text", "text": VIDEO_PROMPT.format(n=len(videos), phrases=_listing(phr), m=len(phr))})
    return content


def scores_from(data, per_clip, phr):
    """Rows -> scores[clip][frame] = list of 0-10 per phrase, or None where the model skipped one."""
    scores = [[None] * len(ts) for ts in per_clip]
    for r in data["rows"]:
        k, j = r["shot"] - 1, r["frame"] - 1
        if 0 <= k < len(scores) and 0 <= j < len(scores[k]) and len(r["scores"]) == len(phr):
            scores[k][j] = [min(10, max(0, int(x))) for x in r["scores"]]
    return scores


def ask_claude(frames, per_clip, phr):
    client = ai.client(timeout=90.0)
    content = build_parts(frames, per_clip, phr)
    msg = client.messages.create(
        model=CLAUDE_MODEL, max_tokens=16000, messages=[{"role": "user", "content": content}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}})
    if msg.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude stopped early ({msg.stop_reason})")
    data = json.loads(next(b.text for b in msg.content if b.type == "text"))
    scores = scores_from(data, per_clip, phr)
    if any(x is None for row in scores for x in row):
        raise ValueError("Claude's scores came back incomplete")
    usage = {"input_tokens": msg.usage.input_tokens, "output_tokens": msg.usage.output_tokens,
             "cost_usd": ai.cost(CLAUDE_MODEL, msg.usage)}
    return scores, usage


def ask_qwen(engine, frames, per_clip, phr, videos=None):
    """Same job through OpenRouter: still frames (qwen) or one video per shot (qwen_video)."""
    if engine == "qwen_video":
        if not videos:
            raise qwen.QwenError("No shot videos were made for Qwen")
        parts = build_video_parts(videos, per_clip, phr)
    else:
        parts = build_parts(frames, per_clip, phr, "openrouter")
    data, usage = qwen.chat(parts, SCHEMA, timeout=180.0)
    try:
        scores = scores_from(data, per_clip, phr)
    except (KeyError, TypeError, ValueError):
        raise qwen.QwenError("Qwen's scores were not in the expected shape") from None
    if any(x is None for row in scores for x in row):
        raise qwen.QwenError("Qwen's scores came back incomplete")
    return scores, usage


def ask_model(engine, frames, per_clip, phr, videos=None):
    """Score every frame x phrase with the chosen engine -> (scores, usage)."""
    if engine in ("qwen", "qwen_video"):
        return ask_qwen(engine, frames, per_clip, phr, videos)
    if engine == "clip":   # on this laptop: nothing is sent anywhere
        return localmatch.ask(frames, per_clip, phr)
    return ask_claude(frames, per_clip, phr)


def extract_videos(checked, per_clip, out_dir, notes=None):
    """qwen_video: out_dir/NN.mp4 per shot = one short segment at each sampled time (segment j = frame j).

    Same times, HDR tone-mapping and crop as extract_frames; 288x512, no audio, H.264. All shots together are kept
    under VIDEO_CAP bytes by lowering fps/quality or shortening segments; `notes` (a dict) gets what was used.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = out_dir / "videos.json"
    sig = hashlib.sha1(json.dumps([[Path(c).stat().st_size, round(skip, 2), ts, hdr]
                                   for (c, _, hdr, skip), ts in zip(checked, per_clip)]).encode()).hexdigest()
    paths = [out_dir / f"{i + 1:02d}.mp4" for i in range(len(checked))]
    try:
        old = json.loads(meta.read_text(encoding="utf-8"))
        if old.get("sig") == sig and all(p.exists() for p in paths):
            if notes is not None:
                notes.update(old["notes"])
            return paths
    except (OSError, ValueError, KeyError):
        pass

    def build(i, fps, crf, seg):
        clip, usable, hdr, skip = checked[i]
        args, chain = [], []
        for j, t in enumerate(per_clip[i]):
            length = max(0.2, min(seg, usable - t))        # never past the end of the clip
            args += ["-ss", f"{skip + t:.2f}", "-t", f"{length:.2f}", "-i", clip]
            vf = ("scale=576:-2," + TONEMAP if hdr else "") + CROP_VF + f",fps={fps},format=yuv420p,setpts=PTS-STARTPTS"
            chain.append(f"[{j}:v]{vf}[v{j}]")
        n = len(per_clip[i])
        fc = ";".join(chain) + ";" + "".join(f"[v{j}]" for j in range(n)) + f"concat=n={n}:v=1:a=0[out]"
        ff.run([*args, "-filter_complex", fc, "-map", "[out]", "-an", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", crf, "-pix_fmt", "yuv420p", "-movflags", "+faststart", paths[i]],
               file=clip, what="Making a short video of a shot")

    total = 0
    for level, (fps, crf, seg) in enumerate(VIDEO_LADDER):
        with ThreadPoolExecutor(4) as ex:
            list(ex.map(lambda i: build(i, fps, str(crf), seg), range(len(checked))))
        total = sum(p.stat().st_size for p in paths)
        if total <= VIDEO_CAP:
            break
    else:
        raise ValueError(f"The shot videos are too big to send ({total // 1_000_000} MB)")
    got = {"video_fps": fps, "video_crf": crf, "video_segment_s": seg, "video_bytes": total,
           "video_shrunk": level > 0}
    meta.write_text(json.dumps({"sig": sig, "notes": got}), encoding="utf-8")
    if notes is not None:
        notes.update(got)
    return paths


# ---------- choosing the cuts ----------

def ask_claude_times(passes, frames, per_clip, phr, engine="claude", videos=None):
    """Score the clips `passes` times at once and average. One failed pass is fine; all failing raises."""
    if passes <= 1:
        return ask_model(engine, frames, per_clip, phr, videos) + (1,)

    def one(_):
        try:
            return ask_model(engine, frames, per_clip, phr, videos)
        except Exception as e:
            return e
    with ThreadPoolExecutor(passes) as ex:
        got = list(ex.map(one, range(passes)))
    good = [g for g in got if not isinstance(g, Exception)]
    if not good:
        raise got[0]
    scores = [np.mean([np.array(g[0][k], dtype=float) for g in good], axis=0).tolist() for k in range(len(per_clip))]
    usage = {k: round(sum(g[1][k] for g in good), 4) for k in ("input_tokens", "output_tokens", "cost_usd")}
    return scores, usage, len(good)


def cut_points(words, phr, total):
    """Where a cut may land and what it costs: free between sentences, dearer mid-phrase."""
    cands = {round(w["start"], 2): CUT_COST["word"] for w in words[1:]}
    for i, p in enumerate(phr):
        for t in p["last_words"]:
            cands[round(t, 2)] = NEAR_SENTENCE_END if p["sentence_end"] else CUT_COST["word"]
    for i, p in enumerate(phr[1:], 1):
        cands[round(p["start"], 2)] = CUT_COST["sentence" if phr[i - 1]["sentence_end"] else "comma"]
    cands[0.0] = cands[round(total, 2)] = 0.0
    return sorted(cands.items())


def span_score(S_i, ts, a, b, grid, phrase_of):
    """Relevance over voice [a, b) for a clip that starts playing at a: nearest sampled frame per step."""
    lo, hi = int(round(a / DT)), int(round(b / DT))
    fidx = np.abs((grid[lo:hi] - a)[:, None] - np.array(ts)[None, :]).argmin(axis=1)
    return S_i[fidx, phrase_of[lo:hi]]


def solve(S, per_clip, limits, words, phr, total, grid, phrase_of):
    """Best cut times with clips in her order. limits[i] = longest slot clip i may get."""
    cands = cut_points(words, phr, total)
    times, cost = [t for t, _ in cands], [c for _, c in cands]
    n, C, NEG = len(S), len(cands), -1e18
    best, back = np.full((n, C), NEG), np.zeros((n, C), dtype=int)
    for i in range(n):
        for c in range(1, C):
            if i == n - 1 and c != C - 1:
                continue
            for a in ([0] if i == 0 else range(1, c)):
                length = times[c] - times[a]
                if length < MIN_SLOT - 1e-6 or length > limits[i] + 1e-6:
                    continue
                prev = 0.0 if i == 0 else best[i - 1][a]
                if prev <= NEG / 2:
                    continue
                v = prev + float(span_score(S[i], per_clip[i], times[a], times[c], grid, phrase_of).sum() * DT) \
                    - (cost[c] if c != C - 1 else 0.0)
                if v > best[i][c]:
                    best[i][c], back[i][c] = v, a
    if best[n - 1][C - 1] <= NEG / 2:
        return None
    edges, c = [C - 1], C - 1
    for i in range(n - 1, 0, -1):
        c = back[i][c]
        edges.append(c)
    edges = [0.0] + [times[e] for e in reversed(edges)]
    return [edges[i + 1] - edges[i] for i in range(n)]


def grade(slots, S, per_clip, grid, phrase_of):
    """(total relevance, seconds well matched, mean relevance per clip) for a plan."""
    a, tot, good, means = 0.0, 0.0, 0.0, []
    for i, length in enumerate(slots):
        per = span_score(S[i], per_clip[i], a, a + length, grid, phrase_of)
        tot += float(per.sum() * DT)
        good += float((per >= 7).sum() * DT)
        means.append(float(per.mean()) if len(per) else 5.0)
        a += length
    return tot, good, means


def wrong_spot(slots, means, S, phr, names):
    """Warn (at most twice) when a clip clearly shows something she says at a different moment."""
    out, a = [], 0.0
    starts = [sum(slots[:i]) for i in range(len(slots))]
    for i, length in enumerate(slots):
        a = starts[i]
        best_per_phrase = S[i].max(axis=0)
        j = int(best_per_phrase.argmax())
        near_lo, near_hi = starts[max(i - 1, 0)], starts[min(i + 1, len(slots) - 1)] + slots[min(i + 1, len(slots) - 1)]
        if best_per_phrase[j] >= 8 and means[i] < 6 and not near_lo <= phr[j]["start"] < near_hi:
            said = phr[j]["text"].rstrip(".,!?")
            out.append({"clip": names[i], "kind": "match",
                        "message": f"{names[i]} looks like the part where you say \"{said}\", but in your order it "
                                   f"plays at {int(a // 60)}:{int(a % 60):02d}. If that's a mistake, move it in the "
                                   "list and make the video again."})
    return out[:2]


def better_order(S, phr, total, score_of, base):
    """Her order, with clips that clearly show something she says moved to where the plan grades best.

    One clip at a time: try a strongly matched clip in every position, keep the single move that
    helps most, repeat. score_of(order) grades a whole plan (or None if no plan fits), so a move is
    judged by the finished video, not by one frame's score. Scores from Claude wobble a point or two
    between runs; this search gives the same answer anyway.
    """
    n = len(S)
    cur, cur_tot, moved = list(range(n)), base, set()
    strong = [i for i in range(n) if S[i].max() >= ANCHOR]
    for _ in range(MAX_MOVES):
        best = None
        for i in strong:
            if i in moved:
                continue
            rest = [c for c in cur if c != i]
            for pos in range(n):
                o = rest[:pos] + [i] + rest[pos:]
                tot = None if o == cur else score_of(o)
                if tot is not None and (best is None or tot > best[0]):
                    best = (tot, o, i)
        if best is None or best[0] < cur_tot + MOVE_GAIN:
            break
        cur_tot, cur = best[0], best[1]
        moved.add(best[2])
    # Also try moving every strongly matched clip to its phrase at once: sometimes better, sometimes much worse
    keys = []
    for i in range(n):
        best_per_phrase = S[i].max(axis=0)
        j = int(best_per_phrase.argmax())
        keys.append(phr[j]["start"] if best_per_phrase[j] >= ANCHOR else (i + 0.5) / n * total)
    whole = sorted(range(n), key=lambda i: (keys[i], i))
    if whole != cur and (score_of(whole) or 0.0) > cur_tot:
        cur = whole
    return cur


def now_on_its_words(S, phr, before, after, order):
    """Clips that play over the phrase they show in the new plan but didn't in her order.

    Says which clips the move was FOR: pushing one clip back is often how another reaches its words.
    """
    def spans(slots, clips):
        out, a = {}, 0.0
        for c, length in zip(clips, slots):
            out[c] = (a, a + length)
            a += length
        return out
    old, new, hits = spans(before, range(len(S))), spans(after, order), []
    for i in range(len(S)):
        best_per_phrase = S[i].max(axis=0)
        j = int(best_per_phrase.argmax())
        on = lambda span: span[0] < phr[j]["end"] and span[1] > phr[j]["start"]
        if best_per_phrase[j] >= ANCHOR and on(new[i]) and not on(old[i]):
            hits.append(i)
    return hits


def plan(checked, words, target_len, even, job_dir, reuse=None, names=None):
    """Returns (slots or None, info for report.json, warnings). Never raises."""
    t0 = time.time()
    engine = settings.get("match_engine")
    qwen_engine = engine in ("qwen", "qwen_video")
    local = engine == "clip"
    info = {"used": False, "model": localmatch.MODEL_ID if local else ai.QWEN_MODEL if qwen_engine else CLAUDE_MODEL,
            "engine": engine}
    try:
        # The user chose the engine: never quietly use another one (and never send anything without its key)
        if local and not localmatch.available():
            print("  clip matching: the on-laptop matcher's files are missing, so her order with an even split is used")
            return None, {**info, "reason": "the on-laptop matcher's files are missing"}, []
        if not local and not (ai.has_openrouter_key() if qwen_engine else ai.has_key()):
            return None, {**info, "reason": "no OpenRouter key" if qwen_engine else "no API key"}, []
        usable = [d for _, d, _, _ in checked]
        phr = phrases(words, target_len)
        per_clip = [frame_times(u) for u in usable]
        while sum(len(t) for t in per_clip) > MAX_FRAMES and max(len(t) for t in per_clip) > 2:
            per_clip = [t[:-1] if len(t) > 2 else t for t in per_clip]   # drop the latest frames first
        mdir = Path(job_dir) / "match"
        mdir.mkdir(exist_ok=True)
        key = cache_key(checked, per_clip, phr, engine)
        passes, done = (2 if settings.get("match_twice") and not local else 1), 1   # the local matcher never varies

        cached = None
        for src in ([Path(reuse) / "match" / "scores.json"] if reuse else []):
            try:
                data = json.loads(src.read_text(encoding="utf-8"))
                cached = data["scores"] if data.get("key") == key and data.get("passes", 1) >= passes else None
                done = data.get("passes", 1)
            except (OSError, ValueError, KeyError):
                cached = None
        if cached:
            scores, usage = cached, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
            info["cached"] = True
        else:
            videos = None
            if engine == "qwen_video":
                frames, videos = None, extract_videos(checked, per_clip, mdir / "videos", info)
            else:
                frames = extract_frames(checked, per_clip, mdir / "frames")
            scores, usage, done = (ask_claude_times(passes, frames, per_clip, phr, engine, videos) if engine != "claude"
                                   else ask_claude_times(passes, frames, per_clip, phr))
        info["passes"] = done
        (mdir / "scores.json").write_text(json.dumps({"key": key, "engine": engine, "passes": done, "phrases": [p["text"] for p in phr],
                                                      "phrase_times": [[round(p["start"], 2), round(p["end"], 2)] for p in phr],
                                                      "frame_times": per_clip, "scores": scores,
                                                      "usable": [round(float(u), 2) for u in usable],
                                                      "names": list(names) if names else None}), encoding="utf-8")
        info.update(usage)

        S = [np.array(s, dtype=float) for s in scores]
        grid = np.arange(0, target_len, DT)
        phrase_of = np.searchsorted([p["end"] for p in phr], grid, side="right").clip(0, len(phr) - 1)
        # No clip hogs the screen: cap each slot, but relax the cap if the footage can't cover the voice with it
        cap = max(8.0, 1.6 * target_len / len(usable))
        slots = solve(S, per_clip, [min(u, cap) for u in usable], words, phr, target_len, grid, phrase_of) \
            or solve(S, per_clip, usable, words, phr, target_len, grid, phrase_of)
        if not slots:
            return None, {**info, "reason": "no plan fits the clip lengths"}, []

        names = names or [f"Clip {i + 1}" for i in range(len(usable))]
        m_tot, m_good, m_means = grade(slots, S, per_clip, grid, phrase_of)
        info.update(score_matched=round(m_tot, 1), matched_seconds=round(m_good, 1))

        def arranged(order):
            S2, pc2, us2 = [S[i] for i in order], [per_clip[i] for i in order], [usable[i] for i in order]
            sl = (solve(S2, pc2, [min(u, cap) for u in us2], words, phr, target_len, grid, phrase_of)
                  or solve(S2, pc2, us2, words, phr, target_len, grid, phrase_of))
            return (S2, pc2, sl) if sl else None

        def score_of(order):
            got = arranged(order)
            return grade(got[2], got[0], got[1], grid, phrase_of)[0] if got else None

        # Fix her order when a clip plainly belongs elsewhere (the "wrong spot" case), if it grades better
        order = better_order(S, phr, target_len, score_of, m_tot) if settings.get("auto_order") else list(range(len(S)))
        got = arranged(order) if order != list(range(len(S))) else None
        if got:
            S2, pc2, slots2 = got
            r_tot, r_good, r_means = grade(slots2, S2, pc2, grid, phrase_of)
            info["score_reordered"] = round(r_tot, 1)
            if r_tot >= m_tot * REORDER_GAIN:
                info.update(used=True, order=order, moved=[f"Moved {names[i]} to where you talk about it"
                                                     for i in now_on_its_words(S, phr, slots, slots2, order)]
                            or ["Reordered clips to match what you say"],
                            matched_seconds=round(r_good, 1), seconds=round(time.time() - t0, 1))
                return [round(s, 3) for s in slots2], info, wrong_spot(slots2, r_means, S2, phr,
                                                                         [names[i] for i in order])
        if even:
            e_tot, e_good, e_means = grade(even, S, per_clip, grid, phrase_of)
            info.update(score_even=round(e_tot, 1), even_matched_seconds=round(e_good, 1))
            if m_tot < e_tot * GATE_GAIN or m_good < e_good + GATE_SECONDS:
                info.update(reason="even split was already about as good", seconds=round(time.time() - t0, 1))
                return None, info, wrong_spot(even, e_means, S, phr, names)
        info.update(used=True, seconds=round(time.time() - t0, 1))
        return [round(s, 3) for s in slots], info, wrong_spot(slots, m_means, S, phr, names)
    except Exception as e:  # API errors, timeouts, refusals, bad JSON, ffmpeg: the even plan still makes the video
        # Only a real AI failure (key, credit, busy, network) is told to Alex: "no API key" and "no plan fits" are not
        print(f"  clip matching didn't work ({type(e).__name__}: {str(e)[:200]})")
        return None, {**info, "reason": f"{type(e).__name__}: {str(e)[:200]}", **({"ai_error": ai.explain(e, "match")} if ai.is_ai_error(e) else {}),
                      "seconds": round(time.time() - t0, 1)}, []


# ---------- "she says it, no clip shows it" ----------

NOT_SHOWN_BELOW = 4          # the best any frame of any clip scores for a phrase (0-10); 5 = neutral
NOT_SHOWN_SECONDS = 2.0      # and she must dwell on it this long
FILLER_PHRASE = re.compile(
    r"\b(hi|hey|hello|welcome|subscribe|follow|like and|comment|link in|call me|call us|text me|reach out|dm me|"
    r"give me a call|contact me|thanks for|thank you|see you|stay tuned|let me know|questions|call alex|schedule|"
    r"book a|your tour|a showing|phone|email|website|link)\b|\d{3}[-. ]\d{3,4}", re.I)
ABSTRACT = re.compile(r"\b(potential|opportunity|dream|imagine|your own|make this|make it|don'?t miss|won'?t last|appreciate|"
                      r"priced|price|offered|listed|perfect|great|love|enjoy|ready for|come inside|welcome)\b|[$]", re.I)
LEAD_IN = re.compile(r"^(and|so|but|then|also|now|well|okay|ok|plus|there'?s|there is|there are|you'?ll find|you will find|"
                     r"you can see|you'?ll see|you can|we also have|we have|we've got|it also has|it has|it'?s got|this has|"
                     r"this (home|house|property) (also )?(has|includes|features|offers)|the (home|house) (also )?(has|includes)|"
                     r"also|has|have|with|features|featuring|includes|including|check out|"
                     r"look at|here'?s|here is|come see|sometimes|"
                     r"(i|we) (also |then |really )?(add|use|put|have|love|like|include|top|mix|stir|blend|show|got)"
                     r"( in)?( some)?)\b[ ,]*", re.I)
STOP_TAIL = {"and", "but", "so", "that", "which", "where", "when", "because", "with", "is", "are", "was", "has", "have",
             "to", "for", "in", "on", "at", "of", "or", "if", "as", "you", "i", "we", "they", "it", "overlooking", "right", "here", "there", "now", "just", "also"}


def idea_for(text):
    """One plain suggestion of what to film, from her own words: 'A short clip of the attached garage'."""
    t = re.sub(r"\s+", " ", text.strip().strip(".,!?;:\"'"))
    low, peeled = t.lower(), False
    for _ in range(4):   # peel "and there's", "you'll find" style lead-ins
        stripped = LEAD_IN.sub("", low).strip()
        if stripped == low:
            break
        low, peeled = stripped, True
    m = re.search(r"(?:the|a|an|our|this|that|these|those|your|new|brand new|large|big|huge) ", low)
    chunk = low[m.start():] if m else low
    words = []
    for w in chunk.split():
        if words and w.strip(",.;:") in STOP_TAIL:
            break
        words.append(w)
        if w.endswith((",", ";", ":")) or len(words) >= 5:
            break
    chunk = " ".join(words).strip(" ,.;:")
    if not chunk or not (m or peeled) or len(chunk.split()) < (2 if m else 1):
        return f"A short clip that shows \"{' '.join(t.split()[:8])}\""
    return f"A short clip of {chunk}"


SHORT_SCORE = 7              # a clip "shows" a phrase when one of its frames scores at least this
SHORT_LEAD = (5, 2)          # ... or when it is the clear leader for it: at least 5 and 2 points above the next clip
SHORT_SLACK = 0.75           # seconds of her words a clip may overrun before it is worth a note
SHORT_MIN_SPEECH = 2.0       # ignore bits of narration shorter than this


def short_footage(scores, phrase_times, texts, usable, names=None, limit=3):
    """Where she talks about something for longer than the clip that shows it lasts.

    scores[clip][frame] = list of 0-10 per phrase; usable[clip] = seconds of that clip that can be used.
    A clip "owns" a phrase when one frame scores 7 or more, or when it clearly leads the other clips (5 or more and 2 points
    ahead: "perfect for grilling" scores 5-6 on the deck clip and about 0 on every other clip). For each clip, the
    consecutive phrases it owns are added up and compared with the clip's length plus any clip that owns ALL of those phrases.
    Returns [{'at', 'said', 'clip', 'need', 'have', 'add'}], at most `limit`, biggest shortfall kept, in time order.

    Judged by the model's own reading of the clips, so it stays quiet when another clip is believed to show the same thing.
    """
    n = len(texts)
    if not scores or n == 0 or len(phrase_times) != n or len(usable) != len(scores):
        return []
    best = np.full((len(scores), n), -1.0)   # best frame score per clip and phrase
    for i, clip in enumerate(scores):
        for row in clip:
            if row is not None and len(row) == n:
                best[i] = np.maximum(best[i], np.array(row, dtype=float))
    dur = [max(0.0, float(phrase_times[j][1]) - float(phrase_times[j][0])) for j in range(n)]
    owners = []
    for j in range(n):
        col = best[:, j]
        own = {i for i in range(len(scores)) if col[i] >= SHORT_SCORE}
        if len(scores) > 1:
            order = np.argsort(-col)
            top, second = int(order[0]), int(order[1])
            if col[top] >= SHORT_LEAD[0] and col[top] - col[second] >= SHORT_LEAD[1]:
                own.add(top)
        owners.append(own)
    found, seen = [], set()
    for c in range(len(scores)):
        run = []
        for j in range(n + 1):
            if j < n and c in owners[j] and (not run or j - run[-1] <= 2):   # one phrase nobody owns does not end a run
                run.append(j)
                continue
            if run:
                need = sum(dur[k] for k in run if c in owners[k])
                co = set.intersection(*[owners[k] for k in run]) - {c}
                have = float(usable[c]) + sum(float(usable[o]) for o in co)
                key = (run[0], run[-1])
                if need >= SHORT_MIN_SPEECH and need > have + SHORT_SLACK and key not in seen:
                    seen.add(key)
                    said = " ".join(texts[k].strip().rstrip(",;: ") for k in run if c in owners[k])
                    found.append({"at": round(float(phrase_times[run[0]][0]), 1), "said": said,
                                  "clip": names[c] if names and c < len(names) else None,
                                  "need": round(need, 1), "have": round(have, 1), "add": int(np.ceil(need - have))})
            run = [j] if (j < n and c in owners[j]) else []
    found.sort(key=lambda f: (-(f["need"] - f["have"]), f["at"]))
    return sorted(found[:limit], key=lambda f: f["at"])


def not_shown(scores, phrase_times, texts, limit=3):
    """Phrases she dwells on that no frame of any clip comes near showing.

    scores[clip][frame] = list of 0-10 per phrase. Returns [{'at', 'said', 'idea'}], at most `limit`, in time order.
    """
    if not scores or not texts or len(phrase_times) != len(texts):
        return []
    best = np.full(len(texts), -1.0)
    for clip in scores:
        for row in clip:
            if row is None or len(row) != len(texts):
                continue
            best = np.maximum(best, np.array(row, dtype=float))
    found = []
    for j, (t0, t1) in enumerate(phrase_times):
        text = texts[j].strip()
        if best[j] < 0 or best[j] >= NOT_SHOWN_BELOW or t1 - t0 < NOT_SHOWN_SECONDS:
            continue
        if len(text.split()) < 3 or FILLER_PHRASE.search(text) or ABSTRACT.search(text):
            continue
        found.append((best[j], -(t1 - t0), {"at": round(t0, 1), "said": text.rstrip(",;: "), "idea": idea_for(text)}))
    found.sort(key=lambda f: (f[0], f[1]))
    return sorted((f[2] for f in found[:limit]), key=lambda d: d["at"])


# ---------- "Change this shot": her own choice of clip for a moment ----------

def apply_pins(order, slots, usable, pins):
    """Put the clip she chose on screen at each pinned moment, changing as little else as possible.

    order: the clips as played (indices into usable, 0 = her first clip); slots: seconds each one plays;
    usable: seconds each clip can give. A pin {"start", "end", "clip"} (clip 1 = her first clip) is about the
    clip on screen at the middle of start..end. That slot gets her clip. If her clip already plays elsewhere,
    the two swap places (no clip is ever shown twice). Slot times stay the same unless a clip is too short
    for its new slot: then the cut next to it moves just enough, into a neighbour that has footage to spare.
    A pin that can't be done is skipped with a plain reason; it never stops the video.
    Returns (order, slots, applied, skipped): applied = [{"pos", "clip", "start", "end"}], skipped = [{"pin", "why"}].
    """
    order, slots = list(order), [float(s) for s in slots]
    locked, applied, skipped = set(), [], []

    def starts():
        return [sum(slots[:i]) for i in range(len(slots))]

    def make_fit(p):
        """Shrink slot p to its clip's length, giving the extra to the nearest slots that have footage to spare."""
        extra = slots[p] - usable[order[p]]
        if extra <= 1e-6:
            return True
        slots[p] = float(usable[order[p]])
        for j in sorted((j for j in range(len(slots)) if j != p), key=lambda j: (j in locked, abs(j - p), j < p)):
            take = min(extra, usable[order[j]] - slots[j])
            if take > 1e-6:
                slots[j] += take
                extra -= take
            if extra <= 1e-6:
                return True
        return False

    for pin in sorted(pins or [], key=lambda q: q["start"]):
        c = int(pin["clip"]) - 1
        if not 0 <= c < len(usable):
            skipped.append({"pin": pin, "why": f"Clip {pin['clip']} isn't in this video"})
            continue
        mid, st = (pin["start"] + pin["end"]) / 2, starts()
        p = next((i for i in range(len(slots)) if st[i] <= mid < st[i] + slots[i]), None)
        if p is None:
            skipped.append({"pin": pin, "why": "that moment is past the end of the video"})
            continue
        if order[p] == c:
            locked.add(p)
            applied.append({"pos": p, "clip": c + 1})
            continue
        q = order.index(c) if c in order else None
        if q is not None and q in locked:
            skipped.append({"pin": pin, "why": f"Clip {c + 1} is already your choice for another moment"})
            continue
        before = (list(order), list(slots))
        if q is None:
            order[p] = c
        else:
            order[p], order[q] = order[q], order[p]
        ok = make_fit(p) and (q is None or make_fit(q))
        changed = [i for i in range(len(slots)) if order[i] != before[0][i] or abs(slots[i] - before[1][i]) > 1e-6]
        if not ok or any(slots[i] > usable[order[i]] + 1e-6 or slots[i] < MIN_SLOT - 1e-6 for i in changed):
            order, slots = before
            skipped.append({"pin": pin, "why": f"Clip {c + 1} is too short for that part"})
            continue
        locked.add(p)
        applied.append({"pos": p, "clip": c + 1})
    st = starts()
    for a in applied:
        a.update(start=round(st[a["pos"]], 2), end=round(st[a["pos"]] + slots[a["pos"]], 2))
    return order, [round(s, 3) for s in slots], applied, skipped


POSSIBLE_MATCH = 7   # uncalibrated AI score heuristic, not human-verified confidence


def match_word(shown_scores, best_any):
    """The plain word for one row of the review list. shown_scores: the scores (0-10) of the frames of the clip on
    screen for this phrase; best_any: the best score any clip got for it. None when there are no scores."""
    if best_any is None:
        return None
    if best_any < NOT_SHOWN_BELOW:
        return "no footage for this"
    if shown_scores and max(shown_scores) >= POSSIBLE_MATCH:
        return "possible match"
    return "not sure"
