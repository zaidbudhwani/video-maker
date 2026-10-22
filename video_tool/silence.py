"""Step 3: cut pauses over 0.7s out of the voice-over, using word timestamps.

The clips are b-roll, not lip-synced, so only the voice is cut. The video is
then built to the trimmed voice length and captions use the trimmed timeline.
"""
import json
from pathlib import Path

from . import ff
from . import settings
from .config import AUDIO_RATE, SILENCE_KEEP


FILLERS = {"um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah", "hmm", "hm", "mm", "mhm"}
FILLER_PAD = 0.04   # tight cut around a removed "um" so it doesn't sound chopped


JOIN_FADE = 0.005   # 5 ms fade on each side of a cut: removes the click where two pieces of sound meet, too short to hear


def join_fades(i, a, b, n):
    """Filter text for kept piece i of n (a..b seconds): a 5 ms fade where it meets another piece, nothing at the
    very start or end of the sound. A hard cut between two waveforms leaves a step that plays as a click."""
    f = ""
    if i > 0:
        f += f",afade=t=in:st=0:d={JOIN_FADE}"
    if i < n - 1 and b - a > 4 * JOIN_FADE:
        f += f",afade=t=out:st={b - a - JOIN_FADE:.4f}:d={JOIN_FADE}"
    return f


def is_filler(w):
    return w["word"].strip(" ,.!?-…").lower() in FILLERS


def clean(words):
    """Words that stay in the video: fillers removed if that setting is on."""
    if not settings.get("remove_fillers"):
        return list(words)
    return [w for w in words if not is_filler(w)] or list(words)


def keep_segments(words, total):
    """Spans of the original audio to keep: long pauses and filler words are cut."""
    gap = settings.get("pause_gap")
    # Room left on each side of a cut pause. Under half the setting, so a cut pause always comes out shorter than it was:
    # with a fixed 0.25 s a "Shorter pauses" setting under 0.5 s made the two pieces overlap and repeat a bit of sound.
    pad = min(SILENCE_KEEP, round(gap * 0.4, 3))
    kept = clean(words)
    filler_times = [(w["start"], w["end"]) for w in words if w not in kept]
    segs = []
    start = max(0.0, kept[0]["start"] - SILENCE_KEEP)
    prev_end = kept[0]["end"]
    for w in kept[1:]:
        had_filler = any(prev_end <= fs < w["start"] for fs, _ in filler_times)
        if w["start"] - prev_end > gap:
            segs.append((start, prev_end + pad))
            start = w["start"] - pad
        elif had_filler:
            segs.append((start, prev_end + FILLER_PAD))
            start = w["start"] - FILLER_PAD
        prev_end = max(prev_end, w["end"])
    segs.append((start, min(total, prev_end + 0.4)))
    return [(round(a, 3), round(b, 3)) for a, b in segs if b - a > 0.05]


def remap(words, segs):
    """Move word times onto the trimmed timeline."""
    out, offset, i = [], 0.0, 0
    for a, b in segs:
        while i < len(words) and words[i]["start"] < b:
            w = words[i]
            out.append({**({"p": w["p"]} if "p" in w else {}), "word": w["word"],
                        "start": round(offset + max(w["start"], a) - a, 3),
                        "end": round(offset + min(w["end"], b) - a, 3)})
            i += 1
        offset += b - a
    return out


def trim_silence(voice, words, job_dir):
    job_dir = Path(job_dir)
    total = ff.probe(voice)["duration"]
    segs = keep_segments(words, total)
    parts = "".join(f"[0:a]atrim={a}:{b},asetpts=PTS-STARTPTS{join_fades(i, a, b, len(segs))}[s{i}];"
                    for i, (a, b) in enumerate(segs))
    chain = "".join(f"[s{i}]" for i in range(len(segs)))
    out = job_dir / "voice_trimmed.wav"
    ff.run(["-i", voice, "-filter_complex", f"{parts}{chain}concat=n={len(segs)}:v=0:a=1,aresample={AUDIO_RATE}[a]",
            "-map", "[a]", "-ac", "1", out], file=voice, what="Silence trim")
    new_words = remap(clean(words), segs)
    length = round(sum(b - a for a, b in segs), 3)
    (job_dir / "words_trimmed.json").write_text(json.dumps(new_words, indent=1), encoding="utf-8")
    (job_dir / "silence_cuts.json").write_text(json.dumps(
        {"original": total, "trimmed": length, "kept": segs}, indent=1), encoding="utf-8")
    return out, new_words, length
