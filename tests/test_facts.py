"""Property facts in the copy are checked against what she said: bedrooms, bathrooms, price, square feet.

Run: venv\\Scripts\\python tests\\test_facts.py
The number checker (writer.unsaid_numbers) only asks whether a number was said anywhere, so a swap ("3 bedrooms and
5 bathrooms" -> "5 bedrooms and 3 bathrooms") passed it. video_tool/facts.py asks whether it was said about the SAME thing.
This is a focused safeguard, not complete factual verification: these tests show what it catches and what it leaves alone.
No AI is called."""
import os
import sys
from pathlib import Path

os.environ["ANTHROPIC_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_tool import facts, longform, writer   # noqa: E402

FAILS = []
B, BA, P, S = facts.BEDROOMS, facts.BATHROOMS, facts.PRICE, facts.SQFT


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {detail}"))
    if not cond:
        FAILS.append(name)


def case(name, copy, said, want):
    got = [(m["category"], m["copy"], m["kind"]) for m in facts.mismatches(copy, said)]
    check(name, got == want, f"got {got}, wanted {want}")


# --- the reproduced failure: values swapped between two facts
case("swapped bedrooms and bathrooms are both flagged", "A 5 bedroom, 3 bathroom home with a big yard.",
     "This home has 3 bedrooms and 5 bathrooms.", [(B, 5, "mismatch"), (BA, 3, "mismatch")])
check("...while the old number check stays silent about the swap (this is the gap)",
      writer.unsaid_numbers("A 5 bedroom, 3 bathroom home", "This home has 3 bedrooms and 5 bathrooms.") == [])
case("the same facts, said the same way, pass", "3 bedrooms and 5 bathrooms", "3 bedrooms and 5 bathrooms", [])
case("one wrong value among right ones is the only one flagged", "3 bedrooms, 5 bedrooms", "three bedrooms", [(B, 5, "mismatch")])

# --- bathrooms: decimals, halves, fulls
case("2.5 baths matches 'two and a half bathrooms'", "2.5 baths", "two and a half bathrooms", [])
case("3 baths does not match 'two and a half bathrooms'", "3 baths", "two and a half bathrooms", [(BA, 3, "mismatch")])
case("2.5 baths matches two full baths and a half bath", "2.5 baths", "two full baths and a half bath", [])
case("'2 full baths' is fine when she said two full and a half", "2 full baths", "two full baths and a half bath", [])
case("1.5 baths matches '1 full bath and 1 half bath'", "1.5 baths", "1 full bath and 1 half bath", [])
case("a half bath alone is 0.5", "a half bath", "there is a half bath downstairs", [])
case("a three-quarter bath is not judged (the check says nothing)", "3 baths", "two baths and a three-quarter bath", [])

# --- written-out numbers
case("'three bedrooms' matches '3-bedroom'", "3-bedroom home", "it has three bedrooms", [])
case("'five bedroom' matches 5 bedrooms", "5 bedrooms", "a five bedroom house", [])
case("price in words matches $350,000 and $350k", "$350,000 and $350k", "listed at three hundred fifty thousand dollars", [])
case("a different price is flagged", "$305,000", "listed at three hundred fifty thousand dollars", [(P, 305000, "mismatch")])
case("$1.2 million matches $1,200,000", "$1.2 million", "priced at $1,200,000", [])
case("square feet in words ('twenty-three hundred') match 2,300 sq ft", "2,300 sq ft", "twenty-three hundred square feet", [])
case("'two thousand three hundred' matches 2,300 sqft", "2,300 sqft", "two thousand three hundred square feet", [])
case("different square footage is flagged", "3,200 square feet", "about 2,300 square feet", [(S, 3200, "mismatch")])

# --- legitimate repeats and parts
case("the same value repeated in the copy is fine", "3 bedroom! Yes, 3 bedrooms.", "three bedrooms, yes three bedrooms", [])
case("3 upstairs + 1 downstairs allows 4 bedrooms", "4 bedrooms", "3 bedrooms upstairs and 1 bedroom downstairs", [])
case("a sum is not allowed for a price", "$450,000", "$300,000 and $150,000", [(P, 450000, "mismatch")])

# --- cannot be confirmed: a plain review note, not a claim of falsehood
case("stated in the copy but never said -> unverified", "3 bedrooms", "come see this lovely kitchen", [(B, 3, "unverified")])
case("a spoken decimal it cannot read is 'unverified', not silently passed", "$1.2M", "one point two million", [(P, 1200000, "unverified")])

# --- things that must not be mistaken for these facts
case("garage, acres, years, minutes and phone numbers are ignored",
     "built in 1998, 2 car garage on 2 acres, 3 minutes away. Call 555-010-0123", "we are 3 minutes away from town", [])
case("plain copy with no facts passes", "A lovely home.", "A lovely home.", [])
case("a price is not read as square feet", "$2,300 a month", "twenty three hundred dollars a month", [])

# --- what Alex reads
items = facts.items("5 bedroom, 3 bathroom, $305,000, 3,200 sq ft", "3 bedrooms and 5 bathrooms, listed at $350,000, about 2,300 square feet")
plains = [i["plain"] for i in items]
check("four review notes, each naming both values in plain words",
      len(plains) == 4 and "The copy says 5 bedrooms, but I heard you say 3 bedrooms. Please check it." in plains
      and any("$305,000" in p and "$350,000" in p for p in plains) and all(p.endswith("Please check it.") for p in plains), plains)
check("the notes never claim the copy is wrong, they ask her to check", all("wrong" not in p.lower() for p in plains), plains)
check("the unverified note says so plainly",
      facts.items("3 bedrooms", "a lovely kitchen")[0]["plain"] == "The copy says 3 bedrooms, and I could not find where you said the bedrooms. Please check it.")

# --- wired in: the copy checker and the long video kit checker
copy = {"titles": ["A 5 bedroom, 3 bathroom home"], "hook": "Come see it.", "description": "This home is lovely.", "cta": "Message me.",
        "topic": "property_or_location", "captions": {k: "ok" for k in writer.PLATFORMS}}
tr = "This home has 3 bedrooms and 5 bathrooms."
got = [i for i in writer.check_items(copy, "", tr) if i["kind"] == "fact_value"]
check("writer.check_items now reports the swap (2 notes)", len(got) == 2, got)
good = dict(copy, titles=["A 3 bedroom, 5 bathroom home"])
check("writer.check_items stays quiet when the facts match", not [i for i in writer.check_items(good, "", tr) if i["kind"] == "fact_value"])
kit = {"titles": ["5 bedroom, 3 bathroom tour", "Another look", "A third title"], "body": "A home tour.", "cta": "Message me.",
       "chapters": [], "tags": []}
check("the long video kit checker reports the swap too",
      any("5 bedrooms" in p for p in longform._problems(kit, tr, True, "", [])), longform._problems(kit, tr, True, "", []))
check("an empty transcript does not crash it", facts.mismatches("3 bedrooms", "") == [{"category": B, "copy": 3, "said": [], "kind": "unverified"}])
check("empty copy does not crash it", facts.mismatches("", "3 bedrooms") == [] and facts.mismatches(None, None) == [])

print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED'}")
sys.exit(1 if FAILS else 0)
