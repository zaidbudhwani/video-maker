"""Titles, hook, description and CTA without an API key.

Everything is pulled from her own sentences, so it already sounds like her.
Also saves ai_prompt.txt: paste it into the free claude.ai chat for polished options.
"""
import json
import re
import shutil
import sys
from pathlib import Path

from . import formats
from .config import PROMPTS_DIR
from .writer import (FAIR_HOUSING, HYPE, PHONE, PLATFORMS, build_prompt, checklist, contact_info, disclosure_text,
                     finish_copy, fh_hits, fh_regex, hint_names, hype_hits, is_property, known_places, settings_format,
                     short_title, term_regex)

FILLER = re.compile(r"^(so|and|um|uh|okay|ok|well|now|alright|hi|hey)[, ]+", re.I)

DANGLING = {"a", "an", "the", "in", "on", "at", "to", "for", "of", "with", "and", "or", "but", "then",
            "i", "we", "you", "is", "are", "some", "my", "our", "your",
            "very", "really", "so", "just", "that", "this", "it", "was", "be", "have", "you're", "it's"}

# Words a short line (a hook, a title) must not stop on: it would read as cut off. 'and', 'why', 'should', 'what'...
HOOK_TAIL = DANGLING | {"within", "from", "by", "as", "if", "when", "about", "because", "who", "which", "than", "also", "they",
                        "we", "he", "she", "there", "his", "her", "their", "its", "are", "up", "out", "into", "over",
                        "after", "before", "while", "where", "what", "how", "why", "should", "could", "would", "will",
                        "can", "do", "does", "did", "not", "had", "has", "have", "been", "were", "am", "than", "from"}

# Kept for older callers: the closing lines now come from formats.FORMATS[...]["cta"]
CTA_PROPERTY = formats.FORMATS["house_tour"]["cta"]
CTA_GENERAL = formats.FORMATS["recipe"]["cta"]


def sentences(text):
    text = re.sub(r"\s+-", "-", text)
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    out = []
    for p in parts:
        if len(p.split()) >= 3:
            p = FILLER.sub("", p).strip()
            out.append(p[:1].upper() + p[1:])   # "So the appraiser..." -> "The appraiser..."
    return out


def shorten(s, max_words):
    """Fit to max_words, preferring to end at a natural clause break."""
    s = s.rstrip(".!,")
    if len(s.split()) <= max_words:
        return s
    clauses = re.split(r"(?<=,)\s+", s)
    out = ""
    for c in clauses:
        cand = (out + " " + c).strip()
        if len(cand.split()) > max_words:
            break
        out = cand
    if len(out.split()) >= 4:
        return out.rstrip(",")
    words = s.split()[:max_words]
    # don't end on a dangling word ("...a home in")
    while len(words) > 3 and words[-1].lower().strip(",") in HOOK_TAIL:
        words.pop()
    return " ".join(words).rstrip(",")


def title_case(s):
    small = {"a", "an", "the", "and", "or", "of", "to", "in", "on", "at", "for", "with", "is"}
    w = s.split()
    return " ".join(x if (i and x.lower() in small) else x[:1].upper() + x[1:] for i, x in enumerate(w))


def disclosure():
    return disclosure_text() or None


def places_said(transcript, hints=None):
    """Towns she said in the video or typed for it, as hashtag words ('Cedar Rapids' -> 'CedarRapids'), first one first."""
    low = transcript.lower()
    typed = " ".join(hint_names(hints)).lower()
    found = [p.replace(" ", "") for p in known_places()
             if re.search(rf"\b{re.escape(p.lower())}\b", low) or re.search(rf"\b{re.escape(p.lower())}\b", typed)]
    return found


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")   # after a full stop


def platform_captions(title, hook, desc, cta, prop, transcript="", fmt=None, hints=None):
    """No-AI version: the same words reshaped to each platform's habits. Hashtags only name places she said."""
    fmt = fmt or ("house_tour" if prop else "tips")
    base = list(formats.FORMATS[fmt]["hashtags"])
    places = places_said(transcript, hints)
    place_tags = ["#" + p for p in places[:1]]
    tags = base[:1] + place_tags + base[1:]
    if "acreage" in transcript.lower() and fmt == "house_tour":
        tags.append("#AcreageLife")
    tags = list(dict.fromkeys(tags))
    first = re.split(r"(?<=[.!?])\s+", desc.strip())[0].rstrip(".")
    first = first if first.endswith(("?", "!")) else first + "."
    return {
        "youtube_shorts": f"{first} {' '.join(tags[:2])} #Shorts",
        "tiktok": f"{hook.rstrip('.')} {' '.join(tags[:4])}",
        # dict.fromkeys drops the hook when it is the same sentence as the first line
        "instagram_reels": "\n".join(dict.fromkeys(([] if first.startswith(hook.rstrip(".?!")) else [hook.rstrip(".") + "."]) + [first, cta])) + "\n\n" + " ".join(tags[:5]),
        "facebook_reels": f"{' '.join(_SENT_SPLIT.split(desc.strip())[:2])} {cta} {tags[0]}",
    }


SCHOOL_IN_LIST = re.compile(r"\b(?:good |great |top |nearby |local )?schools?(?: districts?)?\s*,\s*", re.I)
SCHOOL_AT_END = re.compile(r",?\s*(?:and|or)\s+(?:good |great |top |nearby |local )?schools?(?: districts?)?\b", re.I)
_SCHOOL = term_regex("school")


def without_schools(sents):
    """For a property video, keep schools out of the copy even when she says them (Fair Housing).

    "close to schools, parks, and restaurants" -> "close to parks, and restaurants". A sentence that is
    still about schools after that is dropped. Her spoken words and burned-in captions are untouched.
    """
    out = []
    for s in sents:
        s = SCHOOL_AT_END.sub("", SCHOOL_IN_LIST.sub("", s))
        if not _SCHOOL.search(s):
            out.append(re.sub(r"\s{2,}", " ", s).strip())
    return out or [s for s in sents if not _SCHOOL.search(s)]


# Telling a story ("He goes, can I tell you exactly...", "I'm like, no way"): the words are somebody else's, so they make
# a poor title or hook once taken out of the story
QUOTED = re.compile(r"^(?:and |so |then |but )?(?:he|she|they|i|we|you)(?:'m| am| was| were)?\s+(?:goes|go|like|says|say|said|asked|asks)\b", re.I)


def usable_sentences(sents, unsure=()):
    """Sentences fit to be a title or a hook: not a quote out of a story, and without a word the speech-to-text was
    unsure about (a misheard word in a title is easy to miss). Falls back to all of them when nothing is left."""
    bad = {re.sub(r"\W+", "", w).lower() for w in unsure if re.sub(r"\W+", "", str(w))}

    def has_bad(sentence):
        return any(re.sub(r"\W+", "", t).lower() in bad for t in sentence.split())

    good = [x for x in sents if not QUOTED.match(x) and not has_bad(x)]
    return good or [x for x in sents if not QUOTED.match(x)] or sents


def _pick_hook(sents):
    """A hook of 8 words or fewer that is a whole thought when she has one, else a clean cut of her first line."""
    short = [s for s in sents[:6] if 3 <= len(s.rstrip(".!?").split()) <= 8 and not re.search(r"\d{3}", s)]
    questions = [s for s in short if s.endswith("?")]
    if questions:
        return questions[0].rstrip(".")
    first_q = next((s for s in sents if s.endswith("?")), None)
    if first_q:
        return shorten(first_q, 8)
    if short:
        return short[0].rstrip(".!")   # a whole sentence beats a cut one
    if sents and len(sents[0].rstrip(".!?").split()) <= 8:
        return sents[0].rstrip(".!")
    return shorten(sents[0], 8)


def make_copy(transcript, fmt=None, hints=None, unsure=()):
    """unsure: words the speech-to-text was not sure it heard; sentences with them stay out of the title and the hook."""
    sents = sentences(transcript) or [transcript.strip()]
    fmt = fmt if fmt in formats.FORMATS else formats.resolve(transcript, settings_format())
    prop = is_property(fmt, None, transcript)
    if prop:
        sents = without_schools(sents) or sents

    pool = usable_sentences(sents, unsure)
    questions = [s for s in pool if s.endswith("?")]
    # phone numbers and addresses make poor titles
    title_src = [s for s in pool if not re.search(r"\d{3}", s)] or pool
    picks = []
    place = (places_said(transcript, hints) or [None])[0]
    if fmt == "house_tour" and place:
        picks.append(f"Home Tour in {re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', place)}")
    for s in [title_src[0], *(questions[:1]), *(title_src[1:2]), title_src[-1]]:
        t = title_case(shorten(s, 9))
        if t not in picks:
            picks.append(t)
    titles = []
    for t in picks + [title_case(shorten(s, 9)) for s in title_src] + [title_case(shorten(s, 6)) for s in title_src] +             [title_case(shorten(s, 4)) for s in title_src]:
        if t and t.lower() not in {x.lower() for x in titles}:      # three different ideas, never the same one twice
            titles.append(t)
    titles = titles[:3]
    while len(titles) < 3:                                          # a very short transcript: repeat rather than leave a gap
        titles.append(titles[0] if titles else "Talking It Through")

    hook = _pick_hook(pool)
    desc = " ".join(sents[:3])
    cta = formats.FORMATS[fmt]["cta"]

    notes = []
    for t in titles[:1] if titles else []:
        shaky = [w for w in unsure if re.search(rf"(?<![A-Za-z0-9]){re.escape(str(w))}(?![A-Za-z0-9])", " ".join([*titles, hook]), re.I)]
        if shaky:
            notes.append(f"I wasn't sure I heard \"{shaky[0]}\" right and it's in the title or hook. Please check it before posting.")
    text = " ".join([*titles, hook, desc])
    notes += [f"Hype word in her transcript: {w!r}. Consider rewording." for w in hype_hits(text)]
    if prop:
        notes += [f"Fair Housing check: {w!r} appears. Describe the place, not who it suits."
                  for w in fh_hits(text) if w != "school"]
        if _SCHOOL.search(text):
            notes.append("Fair Housing check: mentions schools. Safer to leave out of the description.")

    copy = {
        "source": "auto (from her own words, no AI)",
        "topic": "property_or_location" if prop else "general",
        "titles": titles,
        "hook": hook,
        "description": desc,
        "cta": cta,
        "captions": platform_captions(titles[0], hook, desc, cta, prop, transcript, fmt, hints),
        "review_notes": notes,
    }
    return finish_copy(copy, fmt, prop, 0.0, disclosure_text())


EMAIL_OR_URL = re.compile(r"\b(?:https?://)?(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|us|io|co|realty|homes|biz|info)\b", re.I)
PRICE = re.compile(r"\$\s?\d[\d,.]*|\b\d[\d,]*\s?(?:dollars|thousand|million)\b", re.I)
ADDRESS = re.compile(r"\b\d{2,5}\s+(?:[NSEW]\.?\s+)?[A-Z][\w.]*(?:\s+[A-Z][\w.]*)*\s+(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ln|Lane|Ct|Court|Blvd|Way|Pl|Circle|Cir)\b")


def _digits10(s):
    d = re.sub(r"\D", "", s)
    return d[-10:] if len(d) >= 10 else d


def _host(url):
    return re.sub(r"^(?:https?://)?(?:www\.)?", "", url.strip().lower()).rstrip("/.")


def contact_notes(transcript):
    """Compare the phone numbers and web addresses she SAYS to the ones on file (prompts/contact.txt)."""
    info, notes = contact_info(), []
    if info["phone"]:
        want = _digits10(info["phone"])
        for m in PHONE.finditer(transcript):
            if _digits10(m.group(0)) != want:
                notes.append(f"You say the phone number {m.group(0).strip()}, but the number on file for you is {info['phone']}. "
                             "It is in the captions too, so please check which one is right.")
    if info["website"]:
        want = _host(info["website"])
        for m in EMAIL_OR_URL.finditer(transcript):
            if _host(m.group(0)) != want:
                notes.append(f"You say the website {m.group(0).strip()}, but the website on file for you is {info['website']}. "
                             "It is in the captions too, so please check which one is right.")
    return list(dict.fromkeys(notes))


def transcript_notes(transcript, topic):
    """Things in HER OWN WORDS (which are burned into the video as captions) she should look at before posting."""
    notes = []
    if topic == "property_or_location":
        for w in fh_hits(transcript):
            m = fh_regex(w).search(transcript)
            if m:
                a = transcript.rfind(".", 0, m.start()) + 1
                around = re.split(r"[.?!,]", transcript[max(a, m.start() - 60):m.end() + 60])
                said = next((s for s in around if fh_regex(w).search(s)), m.group(0)).strip()
                notes.append(f"You say \"{said[:70]}\" in the video, so it's in the captions too. Fair Housing guidance "
                             "is to describe the place, not who it suits. Consider re-recording that line.")
    for w in hype_hits(transcript):
        m = re.search(rf"[^.?!,]*{term_regex(w).pattern}[^.?!,]*", transcript, re.I)
        if m:
            notes.append(f"You say \"{m.group(0).strip()[:70]}\" in the video. It's in the captions too, and it isn't in your usual style.")
    notes += contact_notes(transcript)
    on_file = _digits10(contact_info()["phone"])
    numbers = sorted({m.group(0) for rx in (PHONE, PRICE, ADDRESS) for m in rx.finditer(transcript)
                      if not (rx is PHONE and on_file and _digits10(m.group(0)) == on_file)})
    if numbers:
        notes.append("Check these in the captions, because speech-to-text can mishear digits: " + ", ".join(numbers[:6]) + ".")
    return notes


def part_title(title, n, total):
    """'Title (Part 2 of 3)': the base is trimmed so the label always fits and is never cut."""
    suffix = f" (Part {n} of {total})"
    base = re.sub(r"\s*\(Part \d+ of \d+\)\s*$", "", title).strip()
    room = 95 - len(suffix)
    if len(base) > room:
        cut = base[:room]
        i = cut.rfind(" ")
        base = (cut[:i] if i > 15 else cut).rstrip(" ,;:|-")
    return base + suffix


def add_part_copy(job_dir, outputs, use_ai, unsure=(), mode=None):
    """A long talking video is posted as Part 1, Part 2... and each part needs its own title and captions, written from
    what is said in THAT part. Adds copy["parts"]; the top-level copy stays for the whole video. Each part has a
    "label" ('Part 2 of 3') and its titles end with it.

    mode="highlights" (long video mode): the parts are separate Shorts cut from different moments, not slices of one
    video, so no title says "Part N of M" and the label is just "Short N"."""
    from . import writer
    job_dir = Path(job_dir)
    path = job_dir / "copy.json"
    copy = json.loads(path.read_text(encoding="utf-8"))
    parts, total, extra_cost, extra_notes = [], len(outputs), 0.0, []
    for n, o in enumerate(outputs, 1):
        sub = None
        if use_ai and o.get("text"):
            sub_dir = job_dir / f"_part{n}"
            sub_dir.mkdir(exist_ok=True)
            try:
                _, sub = writer.write_copy(o["text"], sub_dir, fmt=copy.get("format"))
            except Exception:   # the AI is down or left parts out: this part gets the plain version
                sub = None
            finally:
                shutil.rmtree(sub_dir, ignore_errors=True)
        if sub is None:
            sub = make_copy(o.get("text") or "", fmt=copy.get("format"),
                            unsure=[u["word"] for u in unsure if u.get("part") == n])
        part = {k: sub[k] for k in ("titles", "hook", "description", "cta", "captions") if k in sub}
        if mode == "highlights":
            part["titles"] = [re.sub(r"\s*\(Part \d+ of \d+\)\s*$", "", t).strip() for t in part["titles"]]
            part["label"] = f"Short {n}"
        else:
            part["titles"] = [part_title(t, n, total) for t in part["titles"]]
            part["label"] = f"Part {n} of {total}"
        part["part"], part["of"] = n, total
        if o.get("file"):
            part["file"] = o["file"]
        parts.append(part)
        extra_cost += float(sub.get("ai_cost_usd") or 0)
        extra_notes += [f"{'Short' if mode == 'highlights' else 'Part'} {n}: {x}" for x in (sub.get("review_notes") or []) if sub.get("source") != "auto (from her own words, no AI)"]
    copy["parts"] = parts
    copy["ai_cost_usd"] = round(float(copy.get("ai_cost_usd") or 0) + extra_cost, 4)
    if extra_notes:
        copy["review_notes"] = (copy.get("review_notes") or []) + [n for n in extra_notes if n not in (copy.get("review_notes") or [])]
    path.write_text(json.dumps(copy, indent=2, ensure_ascii=False), encoding="utf-8")
    return parts


def attach_review_notes(job_dir, transcript, extra=()):
    """Add the things-to-check list to copy.json, whether the AI or the fallback wrote it. Also fills the format, the
    'Before you post' list and the cost when an older copy.json (a kept remake) lacks them."""
    path = Path(job_dir) / "copy.json"
    copy = json.loads(path.read_text(encoding="utf-8"))
    have = copy.get("review_notes") or []
    copy["review_notes"] = have + [n for n in [*extra, *transcript_notes(transcript, copy.get("topic"))] if n not in have]
    if not copy.get("format") or copy["format"] not in formats.FORMATS:
        copy["format"] = formats.resolve(transcript, settings_format())
    copy["format_label"] = formats.label(copy["format"])
    if not copy.get("checklist"):
        copy["checklist"] = checklist(copy.get("topic") == "property_or_location", copy["format"])
    copy.setdefault("ai_cost_usd", 0.0)
    path.write_text(json.dumps(copy, indent=2, ensure_ascii=False), encoding="utf-8")
    return copy["review_notes"]


def write_local_copy(transcript, job_dir, unsure=()):
    job_dir = Path(job_dir)
    fmt = formats.resolve(transcript, settings_format())
    copy = make_copy(transcript, fmt, unsure=unsure)
    path = job_dir / "copy.json"
    path.write_text(json.dumps(copy, indent=2, ensure_ascii=False), encoding="utf-8")

    examples = (PROMPTS_DIR / "examples.md").read_text(encoding="utf-8")
    prompt = build_prompt(transcript, examples, None, fmt) + (
        "\n\nReply with: 3 title options, a hook (8 words or fewer), a short description, and a one-line CTA.")
    (job_dir / "ai_prompt.txt").write_text(prompt, encoding="utf-8")
    return path, copy


if __name__ == "__main__":
    job = Path(sys.argv[1])
    _, c = write_local_copy((job / "transcript.txt").read_text(encoding="utf-8"), job)
    print(json.dumps(c, indent=2, ensure_ascii=False))
