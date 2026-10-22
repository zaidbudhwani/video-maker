"""Step 1: voice-over -> word-level transcript (words.json)."""
import json
import sys
from pathlib import Path

from . import ff, settings
from .config import PROMPTS_DIR, WHISPER_MODEL
from .errors import AlexError, NoSpeechError


def check_voice(voice):
    info = ff.probe(voice)
    if not ff.has_stream(info, "audio"):
        raise AlexError("There is no sound in this file, so there is nothing to put captions on.", voice)
    vol = ff.max_volume_db(voice)
    if vol.get("max_volume", -91) < -45:
        raise AlexError("This recording is silent, so there are no words to caption. Please check it recorded properly.", voice)
    return info["duration"]


PROMPT = "Alex Morgan, Example Realty. Um, so, uh, I was like, hmm, let me think."   # shows Whisper that fillers are kept


def vocabulary():
    """Towns and terms from prompts/vocabulary.txt ('Tiffin' must not be heard as 'Tifton')."""
    try:
        lines = (PROMPTS_DIR / "vocabulary.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    return ", ".join(l.strip() for l in lines if l.strip() and not l.startswith("#"))


HOTWORD_CHARS = 700   # a longer list starts to bend Whisper toward odd words


def hotwords(hints=None):
    """What Whisper should expect: her names for THIS video first, then the standing vocabulary.

    Hints go in as hotwords only (never the initial prompt), so they bias spelling without being repeated back.
    """
    hints = " ".join(str(hints if hints is not None else settings.get("hints") or "").split())[:400]
    parts = [hints.strip(" ,;")] if hints.strip(" ,;") else []
    room = HOTWORD_CHARS - sum(len(x) + 2 for x in parts)
    vocab = vocabulary()
    if len(vocab) > room:   # cut at a whole entry, never mid-word
        vocab = vocab[:max(room, 0)].rsplit(",", 1)[0]
    if vocab:
        parts.append(vocab)
    return ", ".join(parts) or None


def _plain(text):
    return " ".join("".join(c if c.isalnum() else " " for c in text.lower()).split())


def speech_segments(segments, hot=None):
    """Drop what Whisper makes up over wind, silence or music: no real speech, and it isn't sure of itself either.
    Also drops the case where it just repeats the hint text it was given (the prompt or the hotwords)."""
    hot_plain = _plain(hot or "")
    for seg in segments:
        text = seg.text.strip()
        if seg.no_speech_prob > 0.8 and seg.avg_logprob < -1.0:
            continue
        if len(text) > 8 and text.lower().rstrip(".") in PROMPT.lower():
            continue
        plain = _plain(text)
        if hot_plain and len(plain) > 8 and plain in hot_plain and (seg.no_speech_prob > 0.4 or seg.avg_logprob < -0.7):
            continue   # an echo of her hint list, not something she said
        yield seg


def _same_hints(old_job, hints):
    """A remake can reuse the old transcript unless she has since given different names to listen for."""
    try:
        before = (old_job / "words_hints.txt").read_text(encoding="utf-8")
    except OSError:
        before = ""
    return before == hints or not hints and not before


def _clock(t):
    m, s = divmod(int(t), 60)
    return f"{m}:{s:02d}"


def transcribe(voice, job_dir, reuse=None, progress=False, carry_text=True):
    """reuse = an earlier job folder made from the same files: skip Whisper (remakes).
    progress = print a '  heard 2:10 of 7:05' line about every 10% (long video mode: the listening takes minutes).
    carry_text = False stops each stretch from being guessed from the one before it. A long recording needs that: otherwise
    the full stops fade after the first minutes and a repeated line can be repeated again and again."""
    voice, job_dir = Path(voice), Path(job_dir)
    hints = settings.get("hints")
    if reuse and (Path(reuse) / "words.json").exists() and _same_hints(Path(reuse), hints):
        words = json.loads((Path(reuse) / "words.json").read_text(encoding="utf-8"))
        (job_dir / "words.json").write_text(json.dumps(words, indent=1), encoding="utf-8")
        (job_dir / "transcript.txt").write_text(" ".join(w["word"] for w in words), encoding="utf-8")
        (job_dir / "words_hints.txt").write_text(hints, encoding="utf-8")
        print("  (reusing the transcript from the last version)")
        return words
    length = check_voice(voice)
    from faster_whisper import WhisperModel  # slow import, only when needed

    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    hot = hotwords(hints)
    segments, _ = model.transcribe(str(voice), language="en", word_timestamps=True, vad_filter=False,
                                   initial_prompt=PROMPT, hotwords=hot, condition_on_previous_text=carry_text)
    if progress:
        segments = _with_progress(segments, length)
    words = [
        {"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3),
         "p": round(float(getattr(w, "probability", 1.0) or 0.0), 3)}   # how sure Whisper was: used to flag words to check
        for seg in speech_segments(segments, hot) for w in (seg.words or []) if w.word.strip()
    ]
    # Whisper splits "go-to" / phone numbers into "go" "-to"; glue them back
    glued = []
    for w in words:
        if glued and w["word"].startswith("-"):
            glued[-1] = {**glued[-1], "word": glued[-1]["word"] + w["word"], "end": w["end"],
                         "p": min(glued[-1].get("p", 1.0), w.get("p", 1.0))}
        else:
            glued.append(w)
    words = glued
    if not words:
        raise NoSpeechError("I couldn't hear anyone talking in this recording. Please check that the voice-over is the right one.")
    (job_dir / "words.json").write_text(json.dumps(words, indent=1), encoding="utf-8")
    (job_dir / "words_hints.txt").write_text(hints, encoding="utf-8")
    (job_dir / "transcript.txt").write_text(" ".join(w["word"] for w in words), encoding="utf-8")
    return words


def _with_progress(segments, length):
    """Pass segments through, printing how far the listening has got."""
    shown = -1
    for seg in segments:
        step = int(min(seg.end, length) / max(length, 1) * 10)
        if step > shown and step < 10:
            shown = step
            print(f"  heard {_clock(seg.end)} of {_clock(length)}", flush=True)
        yield seg


# ---------- words worth a second look ----------

SURE_ENOUGH = 0.45      # an ordinary word Whisper was less sure of than this is listed
SURE_NAMES = 0.6        # numbers and names matter more, so they are listed sooner
MAX_UNCERTAIN = 12
PLAIN = {"the", "and", "you", "that", "this", "with", "for", "are", "was", "but", "not", "have", "has", "from", "they",
         "will", "your", "what", "all", "can", "out", "about", "there", "their", "when", "just", "like", "been", "then"}


_TOKENIZER = []   # [] = not tried yet, [None] = not available


def _single_token(text):
    """True when Whisper has one token for this word: everyday words are, 'brazer' and street names are not.
    None when the tokenizer can't be found (the caller then falls back to word length)."""
    if not _TOKENIZER:
        try:
            from faster_whisper.utils import download_model
            from tokenizers import Tokenizer

            folder = download_model(WHISPER_MODEL, local_files_only=True)
            _TOKENIZER.append(Tokenizer.from_file(str(Path(folder) / "tokenizer.json")))
        except Exception:
            _TOKENIZER.append(None)
    tk = _TOKENIZER[0]
    if tk is None:
        return None
    return len(tk.encode(" " + text.lower(), add_special_tokens=False).ids) == 1


def _everyday(text):
    one = _single_token(text)
    return (len(text) < 5) if one is None else (one or len(text) < 5)


def uncertain_words(words):
    """Words in the captions Whisper was unsure of, lowest first: [{'word', 'at', 'p'}].

    Numbers (an address, a price) and capitalised names (streets, towns) are listed at p < 0.6,
    other words only when much less sure. Words from words.json written before 'p' existed are skipped.
    """
    out = []
    for i, w in enumerate(words):
        p = w.get("p")
        text = w["word"].strip(" ,.;:!?\"'()-")
        if p is None or not text:
            continue
        starts_sentence = i == 0 or words[i - 1]["word"].endswith((".", "?", "!"))
        name = text[0].isupper() and not starts_sentence and text not in ("I", "I'm", "I'll", "I've", "I'd")
        number = any(c.isdigit() for c in text)
        if (p < SURE_NAMES and (name or number)) or (p < SURE_ENOUGH and text.lower() not in PLAIN and not _everyday(text)):
            out.append({"word": text, "at": round(w["start"], 2), "p": round(float(p), 2)})
    return sorted(out, key=lambda d: d["p"])[:MAX_UNCERTAIN]


if __name__ == "__main__":
    out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
    ws = transcribe(sys.argv[1], out)
    print(f"{len(ws)} words, {ws[0]['start']}s -> {ws[-1]['end']}s")
    print(" ".join(w["word"] for w in ws))
