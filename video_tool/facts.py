"""Property facts in the copy, checked against what she said: bedrooms, bathrooms, price and square feet.

writer.unsaid_numbers() only asks "did she say this number anywhere?". That cannot catch a swap: "3 bedrooms and 5 bathrooms"
turned into "5 bedrooms and 3 bathrooms" uses only numbers she said. This asks "did she say it about THIS thing?".

It is a focused safeguard, NOT complete factual verification. It reads only these four kinds of fact, in the usual ways people
write and say them (digits, number words, "2.5 baths", "two and a half baths", "$350k", "twenty-three hundred square feet").
Anything it cannot read, it leaves alone; anything it can read but cannot confirm becomes a review note for her to check.
"""
import re

_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
         "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SCALE = {"thousand": 1000, "million": 1000000}
_WORD = "|".join([*_ONES, *_TENS, "hundred", *_SCALE])
# a number: digits (2,300 / 2.5) or number words (three, twenty-three, two thousand three hundred and fifty)
NUM = rf"(?:\d[\d,]*(?:\.\d+)?|(?:{_WORD})(?:[\s-]+(?:and\s+)?(?:{_WORD}))*)"

BEDROOMS, BATHROOMS, PRICE, SQFT = "bedrooms", "bathrooms", "price", "square feet"
LABEL = {BEDROOMS: "bedrooms", BATHROOMS: "bathrooms", PRICE: "price", SQFT: "square feet"}

_BED = re.compile(rf"(?<![\w.,$])({NUM})[\s-]*(?:(?:full|large|spacious|total)\s+)?(?:bed(?:room)?s?|bdrms?|br|bd)\b", re.I)
_BATH = re.compile(rf"(?<![\w.,$])({NUM})((?:\s+and\s+a\s+half)?)[\s-]*(full\s+|half\s+|three[\s-]quarter\s+|3/4\s+)?"
                   rf"(?:bath(?:room)?s?|ba|bth)\b", re.I)
_HALF_ALONE = re.compile(r"(?<![\w.])(?:an?\s+|one\s+)?half[\s-]+bath(?:room)?s?\b", re.I)
_PRICE_SYMBOL = re.compile(r"\$\s*(\d[\d,]*(?:\.\d+)?)\s*(k|m|mil|thousand|million)?(?![\w])", re.I)
_PRICE_DOLLARS = re.compile(rf"(?<![\w.,$])({NUM})(?:\s+(thousand|million))?\s+dollars?\b", re.I)
_SQFT = re.compile(rf"(?<![\w.,$])({NUM})\s*\+?\s*(?:sq\.?\s*ft\.?|sq\.?\s*feet|sqft|sf\b|square[\s-]+(?:feet|foot|ft))", re.I)
_HAS_THREE_QUARTER = re.compile(r"three[\s-]quarter|3/4\s+bath", re.I)


def _value(s):
    """'2,300' -> 2300, '2.5' -> 2.5, 'twenty-three hundred' -> 2300, 'three hundred and fifty thousand' -> 350000. None if unreadable."""
    s = s.strip().lower()
    if re.match(r"\d", s):
        try:
            v = float(s.replace(",", ""))
        except ValueError:
            return None
        return int(v) if v == int(v) else v
    total = cur = 0
    seen = False
    for w in re.split(r"[\s-]+", s):
        if w == "and":
            continue
        seen = True
        if w in _ONES:
            cur += _ONES[w]
        elif w in _TENS:
            cur += _TENS[w]
        elif w == "hundred":
            cur = (cur or 1) * 100
        elif w in _SCALE:
            total += (cur or 1) * _SCALE[w]
            cur = 0
        else:
            return None
    return total + cur if seen else None


def _scaled(v, suffix):
    if v is None:
        return None
    s = (suffix or "").lower()
    return v * (1000 if s in ("k", "thousand") else 1000000 if s in ("m", "mil", "million") else 1)


def facts(text):
    """{'bedrooms': {3}, 'bathrooms': {2, 2.5}, 'price': {350000}, 'square feet': {2300}, '_unclear': {'bathrooms'}} for one text."""
    t = text or ""
    out = {BEDROOMS: set(), BATHROOMS: set(), PRICE: set(), SQFT: set()}
    unclear = set()
    for m in _BED.finditer(t):
        v = _value(m.group(1))
        if v is not None and v < 100:
            out[BEDROOMS].add(v)
    # bathrooms: plain counts, full counts, half baths (each counts 0.5), "two and a half baths"
    plain, full, half = [], [], 0.0
    spans = []
    for m in _BATH.finditer(t):
        v = _value(m.group(1))
        if v is None or v >= 100:
            continue
        kind = (m.group(3) or "").strip().lower()
        spans.append(m.span())
        if kind.startswith(("three", "3/4")):
            unclear.add(BATHROOMS)
        elif kind == "half":
            half += 0.5 * v
        elif m.group(2):
            plain.append(v + 0.5)
        elif kind == "full":
            full.append(v)
        else:
            plain.append(v)
    for m in _HALF_ALONE.finditer(t):
        if not any(a <= m.start() < b for a, b in spans):
            half += 0.5
    if _HAS_THREE_QUARTER.search(t):
        unclear.add(BATHROOMS)
    out[BATHROOMS] |= set(plain) | set(full)
    if half:
        out[BATHROOMS] |= {f + half for f in (full or plain)} | ({half} if not (full or plain) else set())
    for m in _PRICE_SYMBOL.finditer(t):
        out[PRICE].add(_scaled(_value(m.group(1)), m.group(2)))
    for m in _PRICE_DOLLARS.finditer(t):
        out[PRICE].add(_scaled(_value(m.group(1)), m.group(2)))
    out[PRICE].discard(None)
    for m in _SQFT.finditer(t):
        v = _value(m.group(1))
        if v is not None and v >= 100:
            out[SQFT].add(v)
    out["_unclear"] = unclear
    return out


def _acceptable(values, category):
    """What she could mean: each value she said, plus the total when she gave several parts ("3 up and 1 down" -> 4)."""
    vals = set(values)
    if category in (BEDROOMS, SQFT, BATHROOMS) and len(values) >= 2:
        vals.add(sum(values))
    return vals


def mismatches(copy_text, transcript, extra_sources=()):
    """What the copy says about bedrooms, bathrooms, price and square feet that her words do not back up.

    -> [{'category', 'copy': value, 'said': [values she said for it], 'kind': 'mismatch' | 'unverified'}]
    'mismatch': she said a different value for the same thing. 'unverified': the copy states it and she never said it."""
    said_all = facts(" ".join([transcript or "", *extra_sources]))
    copy_all = facts(copy_text)
    found = []
    for cat in (BEDROOMS, BATHROOMS, PRICE, SQFT):
        if cat in said_all["_unclear"] or cat in copy_all["_unclear"]:
            continue    # a three-quarter bath and the like: not something this check can judge
        said = said_all[cat]
        ok = _acceptable(said, cat)
        for v in sorted(copy_all[cat]):
            if any(abs(v - s) < 0.001 for s in ok):
                continue
            found.append({"category": cat, "copy": v, "said": sorted(said), "kind": "mismatch" if said else "unverified"})
    return found


def _show(cat, v):
    if cat == PRICE:
        return f"${v:,.0f}"
    if cat == SQFT:
        return f"{v:,.0f} square feet"
    return f"{v:g} {'bedroom' if v == 1 and cat == BEDROOMS else cat}"


def items(copy_text, transcript, extra_sources=()):
    """check_items-style entries: [{'kind', 'ai', 'plain'}] for the copy checker. The plain text is what Alex reads."""
    out = []
    for m in mismatches(copy_text, transcript, extra_sources):
        cat = m["category"]
        v, said = _show(cat, m["copy"]), " or ".join(_show(cat, s) for s in m["said"])
        if m["kind"] == "mismatch":
            out.append({"kind": "fact_value",
                        "ai": f"Says {v}, but she said {said}. Use only what she said for the {LABEL[cat]}.",
                        "plain": f"The copy says {v}, but I heard you say {said}. Please check it."})
        else:
            out.append({"kind": "fact_value",
                        "ai": f"States {v}, but she never said the {LABEL[cat]}. Only use facts from her words.",
                        "plain": f"The copy says {v}, and I could not find where you said the {LABEL[cat]}. Please check it."})
    return out
