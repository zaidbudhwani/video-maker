"""Step 6: transcript -> titles, hook, description, CTA, per-platform captions in Alex's voice.

Only the transcript goes to the AI (never filenames). The disclosure is added by code, not trusted to the
model. Output is checked and retried; if the last try still breaks a soft rule the copy is KEPT and the
problems go to her as plain "check before posting" notes (a warning never stops a video).
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

from . import facts, formats, settings
from .config import CLAUDE_MODEL, PROMPTS_DIR
from .errors import AlexError

HYPE = ["amazing", "stunning", "breathtaking", "incredible", "gorgeous", "spectacular", "must-see",
        "must see", "dream home", "won't last", "don't miss", "unbelievable", "jaw-dropping",
        "game changer", "game-changer", "ultimate", "insane", "epic", "mind-blowing", "luxurious"]
FAIR_HOUSING = ["family-friendly", "family friendly", "safe area", "safe neighborhood", "safe community",
                "perfect for families", "great for kids", "ideal for families", "school",
                "exclusive", "empty nester", "young professionals", "retirees", "bachelor",
                "master bedroom", "master suite", "master bath", "walking distance to church", "christian",
                "integrated neighborhood", "integrated community", "senior",
                "family neighborhood", "kid-friendly", "kid friendly", "good for kids", "growing family", "empty nest",
                "mother-in-law", "mother in law", "man cave", "adult community", "no children", "no kids", "handicap",
                "walk to church", "near church", "safe for", "safest", "safe", "safety", "prestigious",
                "desirable neighborhood", "exclusive neighborhood", "low crime", "bad neighborhood", "good neighborhood",
                "church", "synagogue", "mosque", "starter home", "perfect for", "ideal for", "able-bodied",
                "adults only", "traditional neighborhood", "bachelor pad", "great for families"]
# Phrases that contain a flagged word but are about the building, not about who it suits
FH_ALLOW = ["schoolhouse", "preschool", "safe room", "gun safe", "fire safe"]
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


def term_regex(term):
    """Whole-word match, plural allowed, spaces and hyphens interchangeable ('family friendly' = 'family-friendly')."""
    body = r"[\s-]+".join(re.escape(p) for p in re.split(r"[\s-]+", term.strip()))
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?:s|es)?(?![A-Za-z0-9])", re.I)


# "perfect for grilling" describes the place; "perfect for young families" describes who it suits
_PEOPLE = (r"(?:famil\w*|kids?|children|couples?|retire\w*|seniors?|professionals?|singles?|students?|first[- ]time|young|"
           r"growing|empty|entertainers?|investors?|people|someone|buyers?|those|anyone|everyone|the right)")
_CONTEXT = {t: re.compile(rf"(?<![A-Za-z0-9]){t}\s+(?:[\w'-]+\s+){{0,2}}{_PEOPLE}(?![A-Za-z0-9])", re.I)
            for t in ("perfect for", "ideal for")}
# "the safe at the back" is a box; "a safe place to live" is a claim about the area
_CONTEXT["safe"] = re.compile(r"(?<![A-Za-z0-9])safe\s+(?:place|places|town|city|street|streets|spot|part|haven|living|location|"
                              r"home|house|neighbou?rhood|community|enough|to\s+(?:live|raise|walk)|and\s+(?:quiet|secure|sound))"
                              r"(?![A-Za-z0-9])", re.I)
_FH_RX = [(w, _CONTEXT.get(w) or term_regex(w)) for w in FAIR_HOUSING]
_HYPE_RX = [(w, term_regex(w)) for w in HYPE]
_ALLOW_RX = [term_regex(w) for w in FH_ALLOW]
# A real street name is not a claim about the neighbours: "220 Church St", "School Rd" (capitalised, then a street word)
_STREET_RX = re.compile(r"\b(?:Church|School|Safe|Senior|Exclusive|Chapel|Prestige)\s+(?:St|Street|Rd|Road|Ave|Avenue|Dr|Drive|"
                        r"Ln|Lane|Ct|Court|Blvd|Boulevard|Way|Pkwy|Parkway|Hwy|Highway|Cir|Circle|Pl|Place|Trl|Trail)\b\.?")


def hashtag_words(text):
    """Plain text plus hashtags split into words ('#CedarRapidsHomes' -> 'Cedar Rapids Homes')."""
    tags = " ".join(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t) for t in re.findall(r"#(\w+)", text))
    return text + " " + tags


def _words_of(text):
    return hashtag_words(text).lower()


def fh_regex(term):
    """The pattern used for one Fair Housing word (so a note can show her actual words around it)."""
    return dict(_FH_RX).get(term) or term_regex(term)


def fh_hits(text):
    """Fair Housing risk words in text: whole words only, hashtags unpacked, a tiny allow-list for 'schoolhouse'."""
    t = _STREET_RX.sub(" ", hashtag_words(text))
    for rx in _ALLOW_RX:
        t = rx.sub(" ", t)
    out, seen = [], set()
    for w, rx in _FH_RX:
        key = w.replace("-", " ")   # 'family-friendly' and 'family friendly' are one finding
        if key not in seen and rx.search(t):
            seen.add(key)
            out.append(w)
    return out


def hype_hits(text):
    return [w for w, rx in _HYPE_RX if rx.search(text)]


# Alex (2 Oct 2026): YouTube is home base; the same vertical clip is reused on the other
# platforms "with a different caption for each".
PLATFORMS = {
    "youtube_shorts": "YouTube Shorts",
    "tiktok": "TikTok",
    "instagram_reels": "Instagram Reels",
    "facebook_reels": "Facebook Reels",
}
CAPTION_LIMITS = {"youtube_shorts": 5000, "tiktok": 2200, "instagram_reels": 2200, "facebook_reels": 5000}
TAG_LIMITS = {"instagram_reels": 5, "facebook_reels": 2, "tiktok": 5}
TITLE_MAX = 95

TOOL = {
    "name": "video_copy",
    "description": "Return the YouTube Short copy.",
    "input_schema": {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "enum": ["property_or_location", "general"],
                      "description": "property_or_location if it is about a home, land, a town or an area. "
                                     "general for recipes, tips, personal life."},
            "titles": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3},
            "hook": {"type": "string", "description": "8 words or fewer"},
            "description": {"type": "string"},
            "cta": {"type": "string"},
            "captions": {
                "type": "object",
                "description": "The same video posted on each platform, with a caption written for that platform.",
                "properties": {p: {"type": "string"} for p in PLATFORMS},
                "required": list(PLATFORMS),
            },
        },
        "required": ["topic", "titles", "hook", "description", "cta", "captions"],
    },
}


def has_key():
    from . import ai
    return ai.has_key()


def disclosure_text():
    """The line from prompts/disclosure.txt, or '' while the file still says REPLACE (nothing is added then)."""
    try:
        disc = (PROMPTS_DIR / "disclosure.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return "" if "REPLACE THIS FILE" in disc else disc


def load_prompts():
    disc = disclosure_text()
    examples = (PROMPTS_DIR / "examples.md").read_text(encoding="utf-8")
    if "(paste her real" in examples:
        print("  WARNING: prompts/examples.md still has empty sections. Voice matching will be weaker.")
    return disc, examples


# ---------------------------------------------------------------- contact details (prompts/contact.txt)
def contact_info():
    """{'phone': '555-010-0123', 'website': ''}. Used only to check what she says, never to post anything."""
    out = {"phone": "", "website": ""}
    try:
        lines = (PROMPTS_DIR / "contact.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        m = re.match(r"^\s*#?\s*(phone|website)\s*:\s*(.*?)\s*$", line, re.I)
        if m and m.group(2):
            out[m.group(1).lower()] = m.group(2)
    return out


PROPERTY_HINTS = ("bedroom", "bathroom", "acre", "listing", "house", "home", "property", "garage", "basement", "yard", "deck",
                  "kitchen", "square feet")
_PROP_RX = [re.compile(rf"\b{w}s?\b", re.I) for w in PROPERTY_HINTS]


def looks_like_property(transcript):
    """The Fair Housing check must not depend on the AI labelling the video correctly. Whole words, 3 different ones
    ('homemade' is not 'home')."""
    return sum(bool(rx.search(transcript)) for rx in _PROP_RX) >= 3


def is_property(fmt, topic, transcript):
    """Fair Housing wording rules apply? A recipe only when nothing says it is about a home (a wrong guess or choice of
    "recipe" must not switch the check off for a house tour); a house tour or market update always; else the words decide."""
    if fmt == "recipe":
        return topic == "property_or_location" or looks_like_property(transcript)
    return bool(formats.FORMATS[fmt]["property"] or topic == "property_or_location" or looks_like_property(transcript))


def known_places():
    """Town and county names from prompts/vocabulary.txt (capitalised entries other than her own name)."""
    try:
        lines = (PROMPTS_DIR / "vocabulary.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [l.strip() for l in lines if l.strip() and l.strip()[0].isupper() and not l.startswith("#")
            and l.strip() not in ("Alex", "Example Realty")]


def settings_format():
    try:
        return settings.get("format")
    except KeyError:
        return "auto"


def hint_names(hints=None):
    """Names, streets and places she typed for this video ('Ellis Park Dr, Shellsburg') as a list."""
    if hints is None:
        try:
            hints = settings.get("hints")
        except KeyError:
            hints = ""
    return [h.strip() for h in re.split(r"[,;\n]+", hints or "") if h.strip()]


def invented_places(copy_text, transcript, hints=None):
    """Towns the copy names that she never said (a Solon tour hashtagged #CedarRapids). A place she typed in
    'names and addresses for this video' counts as allowed."""
    said = transcript.lower() + " " + " ".join(hint_names(hints)).lower()
    mine = _words_of(copy_text)
    return [p for p in known_places() if re.search(rf"\b{re.escape(p.lower())}\b", mine)
            and not re.search(rf"\b{re.escape(p.lower())}\b", said)]


# ---------------------------------------------------------------- the prompt
def build_prompt(transcript, examples, fix_notes=None, fmt=None, hints=None):
    fmt = fmt if fmt in formats.FORMATS else formats.resolve(transcript, settings_format())
    f = formats.FORMATS[fmt]
    names = hint_names(hints)
    contact = contact_info()
    reach = f"Her phone on file is {contact['phone']}. " if contact["phone"] else ""
    reach += f"Her website on file is {contact['website']}. " if contact["website"] else ""
    p = f"""You write the YouTube Shorts copy for Alex Morgan, a real estate Broker Associate.
She reviews everything before posting. Write routine copy only: titles, hook, description, CTA.

Her real writing, to match her voice:
<examples>
{examples}
</examples>

This video is a "{f['label']}".
- Title shape: {f['title_pattern']}
- Closing line style (rewrite it in her voice, one short line): {f['cta']}
- Hashtag ideas, use only the ones that fit: {' '.join(f['hashtags'])}

Rules:
- Sound like her: warm, plain, conversational, a little personal. Raw and authentic, never salesy.
- No hype words (amazing, stunning, must-see, dream home, etc).
- Emojis only if her examples use them.
- Hook: 8 words or fewer. It is spoken/shown in the first second, so make it concrete.
- Description: 2-4 short sentences based only on what she says. Do not invent facts.
- Use only her words for features and facts: never upgrade a bedroom to a suite, never add a renovation, a year,
  a count, a measurement or a price she did not say. Every number in your copy must be a number she says.
- Never say or imply the home is for sale, sold, just listed, under contract, price-reduced or having an open house,
  and never give a price, square footage or room counts, unless she says it in the transcript. A tour video is not
  necessarily a listing.
- CTA: one short, low-pressure line. {reach}Only give a phone number or website if she says it in the transcript
  or it is the one on file.
- Captions: one ready-to-paste caption per platform for the SAME video. Same facts, different shape:
  - youtube_shorts: 1-2 sentences, searchable (place name + what it is), then 2-3 hashtags including #Shorts.
  - tiktok: one short, casual line (under 150 characters) plus 3-5 hashtags.
  - instagram_reels: 2-4 short lines with line breaks, a soft CTA, then a blank line and 3-5 hashtags (no more than 5).
  - facebook_reels: 2-3 friendly sentences like she'd write to people who know her, CTA with how to reach
    her if she gives it in the transcript, at most 2 hashtags.
  Hashtags: specific and real (place, topic), lowercase or CamelCase, no hype tags. Only name towns or counties she
  names in the transcript: never add a nearby town she did not say. All rules above apply.
- If the video is about a property, land, a town or an area, follow Fair Housing rules: describe the place,
  never the people it suits (no "family-friendly", "safe area", "perfect for retirees", schools as a people cue).
  Never mention schools or school districts anywhere (titles, captions, hashtags), even if she says them.
  For recipes, tips and personal life this rule does not apply; keep that copy relaxed and natural."""
    if names:
        p += ("\n- She told us these names and places for this video. Spell them exactly like this and you may use "
              "them: " + ", ".join(names) + ".")
    p += f"""

Transcript of her voice-over:
<transcript>
{transcript}
</transcript>"""
    if fix_notes:
        p += "\n\nYour previous attempt broke these rules. Fix them:\n- " + "\n- ".join(fix_notes)
    return p


# ---------------------------------------------------------------- numbers and claims
UNSAID_CLAIMS = [r"for sale", r"just listed", r"new listing", r"\bsold\b", r"price reduc\w*", r"open house", r"under contract",
                 r"\bpending\b", r"sq\.? ?ft", r"square f\w+", r"\$\s?\d", r"\bsuites?\b"]


def unsaid_claims(copy_text, transcript):
    """Status/price claims in the copy that her own words don't contain."""
    said, found = transcript.lower(), []
    for pat in UNSAID_CLAIMS:
        for m in re.finditer(pat, copy_text.lower()):
            if not re.search(pat, said):
                found.append(m.group(0).strip())
    return sorted(set(found))


PHONE = re.compile(r"(?<!\d)\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}(?!\d)")
_UNITS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
          "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
          "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_BIG = {"hundred": 100, "thousand": 1000, "million": 1000000}
_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?|[a-z]+(?:-[a-z]+)?", re.I)


def _canon(s):
    s = s.replace(",", "").rstrip(".")
    try:
        v = float(s)
    except ValueError:
        return None
    return int(v) if v == int(v) else v


def number_values(text):
    """Every number in text as a value: digits ('2,300' -> 2300), number words ('three' -> 3, 'twenty-five' -> 25).
    'one' alone is skipped (it is mostly a pronoun). Part labels, hashtags, ordinals and phone numbers are ignored."""
    t = re.sub(r"#\w+", " ", text)
    t = re.sub(r"\bpart\s+\d+\s+of\s+\d+\b", " ", t, flags=re.I)
    t = PHONE.sub(" ", t)
    t = re.sub(r"\b\d+(?:st|nd|rd|th)\b", " ", t, flags=re.I)
    toks = [m.group(0).lower() for m in _TOKEN.finditer(t)]
    vals, i = set(), 0
    while i < len(toks):
        tok = toks[i]
        if tok[0].isdigit():
            v = _canon(tok)
            if v is not None:
                vals.add(v)
            i += 1
            continue
        parts = tok.split("-")
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        v = None
        if len(parts) == 2 and parts[0] in _TENS and (parts[1] in _UNITS or parts[1] == "one"):
            v = _TENS[parts[0]] + (_UNITS.get(parts[1]) or 1)
        elif len(parts) == 1:
            w = parts[0]
            if w in _TENS:
                v = _TENS[w]
                if nxt in _UNITS or nxt == "one":
                    v += _UNITS.get(nxt) or 1
                    i += 1
            elif w in _UNITS:
                v = _UNITS[w]
                if nxt in _BIG:
                    vals.add(v * _BIG[nxt])
            elif w in _BIG:
                v = _BIG[w]
        if v is not None:
            vals.add(v)
        i += 1
    return vals


def unsaid_numbers(copy_text, transcript, extra_sources=()):
    """Numbers (digits or words) in the copy that never appear in her words (or names she typed). Phone numbers are
    checked on their digits against the transcript and the number on file."""
    source = " ".join([transcript, *extra_sources])
    said = number_values(source)
    bad = [str(v) for v in sorted(number_values(copy_text), key=str) if v not in said]
    sdigits = re.sub(r"\D", "", source) + " " + re.sub(r"\D", "", contact_info()["phone"])
    for m in PHONE.finditer(copy_text):
        if re.sub(r"\D", "", m.group(0)) not in sdigits:
            bad.append(m.group(0))
    return bad


# ---------------------------------------------------------------- the checks
def _all_text(copy):
    return " ".join([*copy["titles"], copy["hook"], copy["description"], copy["cta"], *copy.get("captions", {}).values()])


def check_items(copy, examples, transcript="", prop=None, hints=None):
    """Problems as [{'kind', 'ai', 'plain'}]: 'ai' tells the model what to fix, 'plain' is what Alex would read."""
    missing = [k for k in TOOL["input_schema"]["required"] if not copy.get(k)]
    missing += [f"captions.{p}" for p in PLATFORMS if not (copy.get("captions") or {}).get(p)]
    if missing:
        return [{"kind": "missing", "ai": "These parts were missing, include every one: " + ", ".join(missing),
                 "plain": "Some parts of the copy were missing."}]
    items = []

    def add(kind, ai, plain):
        items.append({"kind": kind, "ai": ai, "plain": plain})

    text = _all_text(copy)
    n = len(copy["hook"].split())
    if n > 8:
        add("hook", f"Hook has {n} words, max is 8: {copy['hook']!r}", "The opening line is a little longer than 8 words.")
    for w in hype_hits(text):
        add("hype", f"Hype word used: {w!r}", f'The wording includes "{w}", which is not how you usually talk. You may want to reword it.')
    if prop is None:
        prop = copy["topic"] == "property_or_location" or looks_like_property(transcript)
    if prop:
        for w in fh_hits(text):
            add("fair_housing", f"Fair Housing risk: {w!r}",
                f'The wording includes "{w}". Fair Housing guidance is to describe the place, not who it suits. '
                "Please change it before posting.")
    for t in copy["titles"]:
        if len(t) > TITLE_MAX:   # YouTube cuts a title past 100 characters
            add("title_length", f"Title is {len(t)} characters, keep it under {TITLE_MAX}: {t!r}",
                "One title is long and YouTube may cut it off.")
    caps = copy.get("captions", {})
    for key, most in TAG_LIMITS.items():
        c = len(re.findall(r"#\w+", caps.get(key, "")))
        if c > most:
            add("hashtags", f"{key} has {c} hashtags, use at most {most}.", f"The {PLATFORMS[key]} caption had too many hashtags.")
    if EMOJI.search(text) and not EMOJI.search(examples):
        add("emoji", "Emojis used, but her examples don't use them.", "The wording has emojis, which you don't usually use.")
    if transcript:
        for w in unsaid_claims(text, transcript):
            add("fact", f"Says {w!r} but she never said that in the video. Only use facts from her words.",
                f'The copy says "{w}", but you did not say that in the video. Please make sure it is true or take it out.')
        for v in unsaid_numbers(text, transcript, hint_names(hints)):
            add("number", f"Uses the number {v!r}, but she never said that number. Every number must be one she says in the video.",
                f"The copy has the number {v}, and I did not hear that number in your video. Please check it.")
        for it in facts.items(text, transcript, hint_names(hints)):   # a number she said, used for the wrong thing
            add(it["kind"], it["ai"], it["plain"])
        for pl in invented_places(text, transcript, hints):
            add("place", f"Names {pl!r}, but she never said that place. Only use places she names.",
                f"The copy names {pl}, and I did not hear you say that place. Please check it.")
    return items


def check(copy, examples, transcript="", prop=None, hints=None):
    return [i["ai"] for i in check_items(copy, examples, transcript, prop, hints)]


# ---------------------------------------------------------------- finishing: tags, limits, disclosure, checklist
_TAGS_AT_END = re.compile(r"(\s*(?:#\w+[ \t]*)+)\s*$")


def _split_tags(caption):
    m = _TAGS_AT_END.search(caption)
    if not m:
        return caption.rstrip(), ""
    return caption[:m.start()].rstrip(), caption[m.start():].rstrip()


def _trim_tags(tags, most):
    found = re.findall(r"#\w+", tags)
    if len(found) <= most:
        return tags
    return ("\n\n" if "\n" in tags else " ") + " ".join(found[:most])


def _cut(body, room):
    """Shorten body to at most `room` characters at a sentence or word end."""
    if len(body) <= room:
        return body
    if room <= 1:
        return ""
    cut = body[:room - 1]
    for sep in (". ", "! ", "? ", "\n"):
        i = cut.rfind(sep)
        if i > room * 0.5:
            return cut[:i + 1].rstrip()
    i = cut.rfind(" ")
    return (cut[:i] if i > 0 else cut).rstrip(" ,;:") + "…"


def fit_caption(caption, key, disc=""):
    """One platform caption: its hashtags kept, #Shorts on YouTube, the disclosure once at the very end, the length limit
    respected by trimming the BODY only."""
    text = caption.strip()
    if disc:
        text = text.replace(disc, "").strip()   # never twice, wherever it was
    body, tags = _split_tags(text)
    if key in TAG_LIMITS and tags:
        tags = _trim_tags(tags, TAG_LIMITS[key])
    if key == "youtube_shorts":   # #Shorts goes last, so the caption ends the way YouTube expects
        tags = re.sub(r"\s*#shorts\b", "", tags, flags=re.I).rstrip() + " #Shorts"
    tail = tags if body else tags.strip()
    extra = len(tail) + (len(disc) + 2 if disc else 0)
    out = _cut(body, CAPTION_LIMITS[key] - extra) + tail
    return out + ("\n\n" + disc if disc else "")


def apply_disclosure(copy, disc=None):
    """Put the disclosure line (if she has one) on every platform caption and the description, once, last. Also makes
    sure each caption is within its platform's limit and YouTube's has #Shorts."""
    disc = disclosure_text() if disc is None else disc
    caps = copy.get("captions") or {}
    for key in PLATFORMS:
        if caps.get(key):
            caps[key] = fit_caption(caps[key], key, disc)
    if copy.get("description"):
        d = copy["description"].replace(disc, "").rstrip() if disc else copy["description"].rstrip()
        copy["description"] = d + ("\n\n" + disc if disc else "")
    copy["disclosure"] = disc
    return copy


def checklist(prop, fmt=None):
    """The fixed 'Before you post' list. Always present, plain words."""
    # The brokerage line is only on the list while a disclosure is set (it is off at her request; nothing adds the name then)
    lines = (["Your brokerage name is on it"] if disclosure_text() else []) + [
             "The home is still for sale" if fmt == "house_tour" else "Anything you mention is still true",
             "Phone number and website are right",
             "Nobody identifiable, no license plates, no mail or family photos in the video"]
    if prop:
        lines.append("Nothing says who the home suits")
    return lines


def short_title(t, limit=100):
    t = " ".join(t.split())
    if len(t) <= limit:
        return t
    cut = t[:limit - 1]
    i = cut.rfind(" ")
    return (cut[:i] if i > 20 else cut).rstrip(" ,;:|-") + "…"


def finish_copy(copy, fmt, prop, cost=0.0, disc=None):
    """Everything both the AI and the no-AI path add to copy.json (so they cannot drift apart)."""
    copy["format"] = fmt
    copy["format_label"] = formats.label(fmt)
    copy["topic"] = "property_or_location" if prop else "general"
    copy["titles"] = [short_title(t) for t in copy["titles"]]
    apply_disclosure(copy, disc)
    copy["checklist"] = checklist(prop, fmt)
    copy["ai_cost_usd"] = round(float(cost or 0), 4)
    return copy


# ---------------------------------------------------------------- the AI call
def write_copy(transcript, job_dir, fmt=None):
    from . import ai

    client = ai.client(timeout=90, max_retries=1)   # a stalled call must not hold the render queue
    if client is None:
        raise AlexError("No Anthropic API key. Copy .env.example to .env and put the key in it.", ".env")
    if not transcript.strip():
        raise AlexError("Transcript is empty, nothing to write copy from.")
    disclosure, examples = load_prompts()
    fmt = fmt if fmt in formats.FORMATS else formats.resolve(transcript, settings_format())
    hints = ", ".join(hint_names())

    notes, cost, items, copy, prop = None, 0.0, [], None, False
    for attempt in (1, 2, 3):
        msg = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=2048, tools=[TOOL],
            tool_choice={"type": "tool", "name": "video_copy"},
            messages=[{"role": "user", "content": build_prompt(transcript, examples, notes, fmt, hints)}],
        )
        try:
            cost += ai.cost(CLAUDE_MODEL, msg.usage)
        except Exception:
            pass
        copy = next(b.input for b in msg.content if b.type == "tool_use")
        prop = is_property(fmt, copy.get("topic"), transcript)
        items = check_items(copy, examples, transcript, prop, hints)
        notes = [i["ai"] for i in items]
        if not notes:
            break
        print(f"  writer attempt {attempt} broke rules: {notes}")
    if items and items[0]["kind"] == "missing":
        raise AlexError("AI copy was missing parts after a retry:\n  - " + "\n  - ".join(notes))

    finish_copy(copy, fmt, prop, cost, disclosure)
    # The last try still broke a rule: keep her copy and say so in plain words instead of throwing it away
    copy["review_notes"] = list(dict.fromkeys(i["plain"] for i in items))
    path = Path(job_dir) / "copy.json"
    path.write_text(json.dumps(copy, indent=2, ensure_ascii=False), encoding="utf-8")
    return path, copy


# ---------------------------------------------------------------- video ideas
IDEA_TOOL = {
    "name": "video_ideas",
    "description": "Return video ideas for Alex to film.",
    "input_schema": {"type": "object", "properties": {"ideas": {"type": "array", "items": {
        "type": "object",
        "properties": {"title": {"type": "string"},
                       "format": {"type": "string", "enum": list(formats.FORMATS)},
                       "shots": {"type": "array", "items": {"type": "string"}, "minItems": 4, "maxItems": 4}},
        "required": ["title", "format", "shots"]}}}, "required": ["ideas"]},
}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
SEASON = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring", 6: "summer", 7: "summer",
          8: "summer", 9: "fall", 10: "fall", 11: "fall"}

_SEASONAL = {
    "fall": [
        ("Getting Ready for Winter in Iowa: 5 Things to Check Now", "tips",
         ["The gutters and downspouts", "The furnace filter and thermostat", "Outdoor faucets and hoses", "You saying the one thing to do first"]),
        ("What I Look at Before Buying a Country Home", "tips",
         ["The well and septic access", "The roof and the driveway", "The road in", "You saying what to ask the seller"]),
        ("Fall Market Update: What I Am Seeing in Cedar Rapids", "market_update",
         ["You to camera with the date", "A street or sign shot", "What you are seeing with homes on the market", "What a buyer can do now"]),
        ("My Go-To Fall Soup", "recipe",
         ["The ingredients lined up", "Chopping and the pot", "The simmer", "The finished bowl"]),
        ("Tour of an Acreage in the Fall", "house_tour",
         ["The driveway and the outside", "The kitchen", "The primary bedroom", "The yard and the view"]),
    ],
    "winter": [
        ("Is Winter a Good Time to Move? A Straight Answer", "qa",
         ["You saying the question", "The short answer", "The reason behind it", "What to do next"]),
        ("4 Things to Check When a Cold Snap Hits Your Home", "tips",
         ["The pipes under the sink", "The furnace and filter", "The garage door and weather stripping", "You saying what to remember"]),
        ("Winter Market Update", "market_update",
         ["You to camera", "A snowy street shot", "What you are seeing with homes on the market", "What a seller should do"]),
        ("My Go-To Winter Chili", "recipe",
         ["The ingredients lined up", "Browning and the pot", "The simmer", "The finished bowl"]),
        ("Winter Home Tour", "house_tour",
         ["The front entry", "The living room", "The kitchen", "The primary bedroom"]),
    ],
    "spring": [
        ("Spring Home Maintenance: 5 Things to Check Around Your House", "tips",
         ["The roof and gutters", "The foundation and drainage", "The air conditioner", "You saying the first thing to do"]),
        ("Spring Market Update: What I Am Seeing", "market_update",
         ["You to camera", "A street shot", "What you are seeing with homes on the market", "What a buyer should know"]),
        ("What Does a Home Inspection Look At?", "qa",
         ["You saying the question", "The short answer", "An example, no names", "What to do next"]),
        ("My Go-To Spring Salad", "recipe",
         ["The ingredients lined up", "Washing and chopping", "Mixing it together", "The finished bowl"]),
        ("Home Tour With a Big Backyard", "house_tour",
         ["The outside from the street", "The kitchen", "The living room", "The backyard"]),
    ],
    "summer": [
        ("Moving in the Summer: 4 Things to Do Before the Truck Comes", "tips",
         ["A packed box with the label showing", "Your utilities checklist", "The walk-through of the old house", "You saying the one thing to remember"]),
        ("Summer Market Update", "market_update",
         ["You to camera", "A street shot", "What you are seeing with homes on the market", "What a seller should do"]),
        ("How Do I Know What My Home Is Worth?", "qa",
         ["You saying the question", "The short answer", "Why appraisals are opinions", "What to do next"]),
        ("My Go-To Summer Grilling Night", "recipe",
         ["The ingredients lined up", "Getting the grill ready", "Cooking it", "The plate"]),
        ("Home Tour With a Great Deck", "house_tour",
         ["The outside from the street", "The kitchen", "The primary bedroom", "The deck"]),
    ],
}
_GENERAL = [
    ("The Purpose of an Open House, in Plain Words", "qa",
     ["You saying the question", "The short answer", "An example, no names", "What to do next"]),
    ("3 Things to Do Before You Start Looking at Homes", "tips",
     ["You to camera", "Thing 1", "Thing 2", "Thing 3"]),
    ("A Day on the Homestead", "tips",
     ["Morning outside", "The animals", "Something you made", "You saying hello"]),
]
_IDEA_BANNED = [r"for sale", r"\bsold\b", r"just listed", r"new listing", r"\bprice", r"under contract", r"\bpending\b"]


def her_titles():
    """Her real YouTube titles from prompts/examples.md (the numbered lines under 'Video titles')."""
    try:
        text = (PROMPTS_DIR / "examples.md").read_text(encoding="utf-8")
    except OSError:
        return []
    sec = re.search(r"## Video titles(.*?)(?:\n---|\n## )", text, re.S)
    out = []
    for line in (sec.group(1) if sec else "").splitlines():
        m = re.match(r"\s*\d+\.\s+(.*)", line)
        if m:
            out.append(re.sub(r"\s{2,}.*$", "", m.group(1)).strip())
    return out


_STREET = re.compile(r"\b\d{2,5}\s+[A-Z][\w.]*(?:\s+[A-Z][\w.]*)*\s+(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ln|Lane|Ct|Court|Blvd|Way|Pl|Circle|Cir)\b")


def _idea_problems(idea):
    text = idea["title"] + " " + " ".join(idea["shots"])
    bad = fh_hits(text) + hype_hits(text) + [p for p in _IDEA_BANNED if re.search(p, text, re.I)]
    if _STREET.search(text):
        bad.append("a street address")   # an idea must never point at a real home
    if idea["title"].strip().lower() in {t.lower() for t in her_titles()}:
        bad.append("one of her old titles")   # ideas are new, not repeats
    return bad


def _clean_idea(d):
    try:
        title = " ".join(str(d["title"]).split())[:TITLE_MAX]
        fmt = d["format"] if d["format"] in formats.FORMATS else "tips"
        shots = [" ".join(str(s).split()) for s in d["shots"] if str(s).strip()]
    except (KeyError, TypeError, AttributeError):
        return None
    if not title:
        return None
    pad = [s for s in formats.FORMATS[fmt]["film_list"] if s not in shots]
    return {"title": title, "format": fmt, "shots": (shots + pad)[:4]}


def fallback_ideas(n=5, month=None):
    """n ideas from the built-in seasonal list (this season first, a mix of formats)."""
    month = month if month in range(1, 13) else date.today().month
    order = ["fall", "winter", "spring", "summer"]
    first = order.index(SEASON[month])
    pool = []
    for k in range(4):
        pool += [{"title": t, "format": f, "shots": s} for t, f, s in _SEASONAL[order[(first + k) % 4]]]
        if k == 0:
            pool += [{"title": t, "format": f, "shots": s} for t, f, s in _GENERAL[:1]]
    pool += [{"title": t, "format": f, "shots": s} for t, f, s in _GENERAL[1:]]
    pool = [d for d in pool if not _idea_problems(d)]
    out, kinds = [], set()
    for d in pool:   # one of each format before any repeats
        if d["format"] not in kinds and len(out) < n:
            out.append(d)
            kinds.add(d["format"])
    for d in pool:
        if len(out) >= n:
            break
        if d not in out:
            out.append(d)
    return out[:n]


def ideas(n=5, month=None):
    """[{'title','format','shots':[4 short strings]}]: Claude when there is a key (her real titles and the season), else a
    built-in seasonal list. Never says a home is for sale or sold. Suggestions only; nothing is made or posted."""
    n = max(1, min(int(n), 12))
    month = month if month in range(1, 13) else date.today().month
    got = []
    try:
        from . import ai

        client = ai.client(timeout=60, max_retries=1)
        if client is not None:
            _, examples = load_prompts()
            kinds = "\n".join(f"- {k}: {v['label']}. Title shape: {v['title_pattern']}" for k, v in formats.FORMATS.items())
            prompt = f"""Suggest {n} video ideas Alex Morgan (Iowa real estate Broker Associate, Cedar Rapids area) could film on her
phone this week. It is {MONTHS[month - 1]} ({SEASON[month]}) in Iowa, so use the season where it fits. Mix the formats.
Write NEW titles in the way she writes hers (her real titles and words are below). Never copy one of her titles, and never use a street address or the name of a real home. Each idea needs the format id and
exactly 4 short, plain things to film, in order.

Her real writing:
<examples>
{examples}
</examples>

Formats:
{kinds}

Rules: warm, plain, raw. No hype words. Never say or hint that a home is for sale, sold, listed, priced or has an open house.
Fair Housing: describe places and things, never who they suit (no schools, no "family-friendly", no "perfect for").
Do not invent facts or numbers about a real home. Titles under {TITLE_MAX} characters. These are ideas she can ignore."""
            msg = client.messages.create(model=CLAUDE_MODEL, max_tokens=2048, tools=[IDEA_TOOL],
                                         tool_choice={"type": "tool", "name": "video_ideas"},
                                         messages=[{"role": "user", "content": prompt}])
            raw = next(b.input for b in msg.content if b.type == "tool_use").get("ideas") or []
            for d in raw:
                c = _clean_idea(d)
                if c and not _idea_problems(c) and c["title"].lower() not in {g["title"].lower() for g in got}:
                    got.append(c)
    except Exception as e:   # no key, no credit, network: the built-in list is fine
        print(f"  ideas: using the built-in list ({str(e)[:120]})")
        got = []
    if len(got) < n:
        have = {g["title"].lower() for g in got}
        got += [d for d in fallback_ideas(12, month) if d["title"].lower() not in have]
    return got[:n]


if __name__ == "__main__":
    # Windows consoles and log files default to a code page with no emoji (her copy can use them)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    job = Path(sys.argv[1])
    p, c = write_copy((job / "transcript.txt").read_text(encoding="utf-8"), job)
    print(json.dumps(c, indent=2, ensure_ascii=False))
