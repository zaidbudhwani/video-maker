"""Content lane unit tests: formats, Fair Housing and fact checks, disclosure, contact notes, post pack, ideas.
Run: venv/Scripts/python tests/test_content.py   (no API calls; uses saved jobs in out/ when they exist)"""
import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from video_tool import captions, formats, local_copy, postpack, settings, writer  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)


def transcript(job):
    p = ROOT / "out" / job / "transcript.txt"
    return p.read_text(encoding="utf-8") if p.exists() else None


# ---------------------------------------------------------------- formats
check("formats: exactly the five ids", list(formats.FORMATS) == ["house_tour", "recipe", "tips", "market_update", "qa"])
check("formats: ids match settings.FORMAT_KEYS", set(settings.FORMAT_KEYS) == set(formats.FORMATS) | {"auto"})
for fid, f in formats.FORMATS.items():
    check(f"formats: {fid} has every field", all(k in f for k in ("label", "property", "title_pattern", "cta", "hashtags", "film_list"))
          and 3 <= len(f["hashtags"]) <= 6 and 4 <= len(f["film_list"]) <= 9 and all(h.startswith("#") for h in f["hashtags"]))
for job, want in (("demo-tour-moved", "house_tour"), ("demo-recipe", "recipe"), ("demo-tips", "tips")):
    t = transcript(job)
    if t:
        check(f"guess_format on her real {job} transcript", formats.guess_format(t) == want, formats.scores(t))
check("guess_format: market words", formats.guess_format("The housing market this month: inventory is up, median prices are flat and buyers have more choice.") == "market_update")
check("guess_format: question words", formats.guess_format("Great question. People ask me this all the time. The answer is simple, so the answer is yes.") == "qa")
check("guess_format: empty text is the general one", formats.guess_format("") == "tips")
check("guess_format: 'homemade' alone is not a house tour", formats.guess_format("I made homemade cookies, add flour and sugar and butter, bake in the oven") == "recipe")
check("resolve: her pick beats the guess", formats.resolve("bake in the oven with flour and sugar", "qa") == "qa")
check("resolve: auto guesses", formats.resolve("bake in the oven with flour and sugar and butter", "auto") == "recipe")

# ---------------------------------------------------------------- Fair Housing matching
check("fh: family-friendly caught", "family-friendly" in writer.fh_hits("A family-friendly street"))
check("fh: hashtag unpacked", writer.fh_hits("Nice. #FamilyFriendly") != [])
check("fh: schools plural caught", "school" in writer.fh_hits("Close to schools and parks"))
check("fh: schoolhouse allowed", writer.fh_hits("Restored one room schoolhouse") == [])
check("fh: preschool allowed", writer.fh_hits("A preschool sign out front") == [])
check("fh: 'safe' caught as a word", "safe" in writer.fh_hits("A safe place to live"))
check("fh: safe room allowed", writer.fh_hits("There is a safe room in the basement") == [])
check("fh: 'safely' (not the word safe) is not caught", writer.fh_hits("walk safely") == [])
check("fh: church caught", "church" in writer.fh_hits("Next to a church"))
check("fh: master bath caught", "master bath" in writer.fh_hits("Updated master bath"))
check("fh: perfect for families caught", "perfect for" in writer.fh_hits("Perfect for young families"))
check("fh: perfect for grilling is about the place", writer.fh_hits("A large deck, perfect for grilling") == [])
check("fh: integrated appliances is fine", writer.fh_hits("integrated appliances in the kitchen") == [])
check("fh: starter home caught", "starter home" in writer.fh_hits("A starter home"))
check("fh: 'exclusively' is not 'exclusive'", writer.fh_hits("sold exclusively online") == [])
check("hype: ultimately is not ultimate", writer.hype_hits("ultimately it is a nice house") == [])
check("hype: ultimate caught", writer.hype_hits("The ultimate home") == ["ultimate"])
check("hype: epicenter is not epic", writer.hype_hits("near the epicenter") == [])
check("property: homemade is not home", not writer.looks_like_property("homemade smoothie with a plot twist, lot of fruit"))
check("property: three real words", writer.looks_like_property("The home has 3 bedrooms and a garage."))

# ---------------------------------------------------------------- numbers and facts
tr = "Call Alex, 555-010-0123. This home has three bedrooms, a half bath and 2,300 square feet. We bought it in 2019. twenty-five years old."
check("numbers: digits and words both count", writer.number_values(tr) >= {3, 2300, 2019, 25})
check("numbers: 'one' alone is not a number", writer.number_values("one of the best") == set())
check("numbers: part label and hashtags ignored", writer.number_values("Great (Part 2 of 3) #Top5") == set())
check("numbers: 2.5 equals 2.50", writer.number_values("2.5 baths") == writer.number_values("2.50 baths"))
check("numbers: said ones pass", writer.unsaid_numbers("3 bedrooms and 2300 sq ft since 2019", tr) == [])
check("numbers: invented count caught", writer.unsaid_numbers("Five bedrooms and a new roof in 2024", tr) == ["2024", "5"] or
      set(writer.unsaid_numbers("Five bedrooms and a new roof in 2024", tr)) == {"5", "2024"})
check("numbers: phone she said passes", writer.unsaid_numbers("Call 555-010-0123", tr) == [])
check("numbers: on-file phone passes even if not said", writer.unsaid_numbers("Call 555-010-0123", "nothing here") == [])
check("numbers: wrong phone caught", writer.unsaid_numbers("Call 555-010-9999", tr) == ["555-010-9999"])
check("numbers: typed names count as said", writer.unsaid_numbers("700 Lakeview Ct", "a house", ["700 Lakeview Ct"]) == [])
check("claims: invented sold caught", "sold" in writer.unsaid_claims("Just sold!", "a house"))
check("claims: suite upgrade caught", "suite" in writer.unsaid_claims("with a primary suite", "the primary bedroom has its own bathroom"))
check("claims: said claim passes", writer.unsaid_claims("open house Sunday", "come to the open house") == [])

good = {"topic": "property_or_location", "titles": ["Home Tour in Solon", "Rec Room and Garage", "Three Bedrooms"],
        "hook": "A home with a rec room", "description": "A home in Solon with three bedrooms.", "cta": "Send me a message.",
        "captions": {"youtube_shorts": "A home in Solon. #HomeTour #Shorts", "tiktok": "Solon home #Solon",
                     "instagram_reels": "Solon home\n\n#Solon", "facebook_reels": "A Solon home #Solon"}}
tour = transcript("demo-tour-moved") or "Are you looking for an affordable home in Solon? three bedrooms, rec room, garage and a deck."
check("check: clean copy has no problems", writer.check(good, "", tour) == [], writer.check(good, "", tour))
bad = json.loads(json.dumps(good))
bad["description"] = "A family-friendly home in Solon with five bedrooms, amazing views, near Cedar Rapids."
items = writer.check_items(bad, "", tour)
kinds = {i["kind"] for i in items}
check("check: fair housing, hype, number and place all caught", {"fair_housing", "hype", "number", "place"} <= kinds, kinds)
check("check: plain notes have no jargon or paths", all("\\" not in i["plain"] and "regex" not in i["plain"].lower() for i in items))
check("check: missing field reported", writer.check({"topic": "general"}, "", "")[0].startswith("These parts were missing"))
check("check: recipe copy is not held to Fair Housing", writer.check({**good, "topic": "general", "description": "Perfect for families, a safe snack."}, "", "smoothie", prop=False) == [] or
      all("Fair Housing" not in p for p in writer.check({**good, "topic": "general", "description": "safe snack"}, "", "smoothie", prop=False)))
check("is_property: a smoothie recipe is not held to Fair Housing", writer.is_property("recipe", "general", "blend the banana with milk") is False)
check("is_property: a house video labelled 'recipe' still is (fixer lane)", writer.is_property("recipe", "property_or_location", "bedroom bathroom garage") is True
      and writer.is_property("recipe", "general", "bedroom bathroom kitchen home house") is True)
check("is_property: house tour always", writer.is_property("house_tour", "general", "hello") is True)

# hints (feature 12): names she typed count as allowed places
only_hint = dict(good, description="A home in Hiawatha with three bedrooms.")
check("hints: a place she did not say is flagged", any("Hiawatha" in p for p in writer.check(only_hint, "", tour)))
check("hints: a place she typed is allowed", not any("Hiawatha" in p for p in writer.check(only_hint, "", tour, hints="Hiawatha, Ellis Park Dr")))
check("hints: split on commas", writer.hint_names("Ellis Park Dr, Shellsburg;Hilltop") == ["Ellis Park Dr", "Shellsburg", "Hilltop"])
check("hints: prompt carries them", "Ellis Park Dr" in writer.build_prompt(tour, "x", None, "house_tour", "Ellis Park Dr"))
check("hints: fallback hashtag uses a typed town", "#Hiawatha" in local_copy.make_copy("A home with three bedrooms and a garage and a yard.", hints="Hiawatha")["captions"]["tiktok"])

# ---------------------------------------------------------------- disclosure, limits, #Shorts
DISC = "Alex Morgan, Broker Associate, Example Realty."
c = writer.apply_disclosure(json.loads(json.dumps(good)), DISC)
check("disclosure: on all four captions, last", all(v.endswith(DISC) for v in c["captions"].values()))
check("disclosure: exactly once per caption", all(v.count(DISC) == 1 for v in c["captions"].values()))
check("disclosure: description too", c["description"].count(DISC) == 1 and c["disclosure"] == DISC)
c2 = writer.apply_disclosure(c, DISC)
check("disclosure: applying twice does not double it", all(v.count(DISC) == 1 for v in c2["captions"].values()) and c2["description"].count(DISC) == 1)
check("disclosure: YouTube keeps #Shorts before it", "#Shorts\n\n" + DISC in c["captions"]["youtube_shorts"])
none = writer.apply_disclosure(json.loads(json.dumps(good)), "")
check("disclosure: empty means nothing added", none["captions"]["tiktok"] == "Solon home #Solon" and none["captions"]["youtube_shorts"].endswith("#Shorts"))
long = json.loads(json.dumps(good))
long["captions"]["tiktok"] = ("A very long caption sentence about the home. " * 80) + "#Solon #HomeTour"
t = writer.apply_disclosure(long, DISC)["captions"]["tiktok"]
check("limits: TikTok within 2200", len(t) <= 2200, len(t))
check("limits: body trimmed, hashtags and disclosure kept", "#Solon #HomeTour" in t and t.endswith(DISC))
long["captions"]["instagram_reels"] = "x " * 3000 + "\n\n#a #b #c #d #e #f #g"
i = writer.apply_disclosure(long, DISC)["captions"]["instagram_reels"]
check("limits: Instagram within 2200 and at most 5 hashtags", len(i) <= 2200 and i.count("#") == 5, (len(i), i.count("#")))
y = writer.apply_disclosure({"captions": {"youtube_shorts": "No tags here"}, "description": "d"}, "")["captions"]["youtube_shorts"]
check("shorts: #Shorts added when the AI forgot it", y == "No tags here #Shorts", y)
ys = writer.apply_disclosure({"captions": {"youtube_shorts": "Hi there. #Shorts #A #B"}, "description": "d"}, "")["captions"]["youtube_shorts"]
check("shorts: moved to the end when it came first", ys == "Hi there. #A #B #Shorts", ys)
check("limits: the table", writer.CAPTION_LIMITS == {"youtube_shorts": 5000, "tiktok": 2200, "instagram_reels": 2200, "facebook_reels": 5000})

# the fallback path does the same
orig = writer.disclosure_text
writer.disclosure_text = lambda: DISC
try:
    import video_tool.local_copy as lc
    lc.disclosure_text = writer.disclosure_text
    lcopy = lc.make_copy(tour)
finally:
    writer.disclosure_text = lc.disclosure_text = orig
check("disclosure: fallback copy has it once on every caption", all(v.count(DISC) == 1 and v.endswith(DISC) for v in lcopy["captions"].values()))
check("disclosure: fallback description + field", lcopy["description"].endswith(DISC) and lcopy["disclosure"] == DISC)

# ---------------------------------------------------------------- fallback copy
for job in ("demo-tour-moved", "demo-recipe", "demo-tips"):
    t = transcript(job)
    if not t:
        continue
    cp = local_copy.make_copy(t)
    check(f"fallback {job}: three titles, hook <= 8 words", len(cp["titles"]) == 3 and 1 <= len(cp["hook"].split()) <= 8, cp["hook"])
    check(f"fallback {job}: 4 captions, YouTube ends #Shorts", all(cp["captions"][k] for k in writer.PLATFORMS) and cp["captions"]["youtube_shorts"].endswith("#Shorts"))
    check(f"fallback {job}: format, label, checklist, cost present", cp["format"] in formats.FORMATS and cp["format_label"] and cp["checklist"] and cp["ai_cost_usd"] == 0.0)
    check(f"fallback {job}: no schools in property copy", cp["topic"] != "property_or_location" or "school" not in json.dumps(cp).lower())
    check(f"fallback {job}: no invented sale words", not writer.unsaid_claims(writer._all_text(cp), t))
    check(f"fallback {job}: checklist always includes the people/plates line", any("license plates" in l for l in cp["checklist"]))
    check(f"fallback {job}: fair housing checklist line only for property", ("Nothing says who the home suits" in cp["checklist"]) == (cp["topic"] == "property_or_location"))
recipe = transcript("demo-recipe")
if recipe:
    r = local_copy.make_copy(recipe)
    check("fallback: recipe is a recipe with the recipe tags", r["format"] == "recipe" and "#Recipe" in r["captions"]["tiktok"] and "#IowaRealEstate" not in json.dumps(r["captions"]))
    check("fallback: recipe is not property", r["topic"] == "general")
check("fallback: format she picked is used", local_copy.make_copy("Today we talk about three things you should do.", fmt="qa")["format"] == "qa")
check("fallback: settings format is used when not auto", (settings.configure({"format": "market_update"}), local_copy.make_copy("A short sentence about nothing in particular.")["format"])[1] == "market_update")
settings.configure({})
check("fallback: school removed from copy", "school" not in local_copy.make_copy(tour)["description"].lower())
check("fallback: hook is never longer than 8 words", all(len(local_copy.make_copy(x)["hook"].split()) <= 8 for x in (tour, "Short one here. Another sentence follows that is quite a bit longer than eight words for sure.")))

# ---------------------------------------------------------------- contact notes
saved = writer.contact_info
writer.contact_info = local_copy.contact_info = lambda: {"phone": "555-010-0123", "website": "www.BrokerHomes.com"}
try:
    check("contact: matching phone (other formatting) gives no note", local_copy.contact_notes("Call (555) 010 0123 today") == [])
    n = local_copy.contact_notes("Call 555-010-2745 or visit www.brokerhomez.com")
    check("contact: wrong phone noted in plain words", any("555-010-2745" in x and "555-010-0123" in x for x in n), n)
    check("contact: wrong website noted", any("brokerhomez.com" in x and "BrokerHomes.com" in x for x in n), n)
    check("contact: matching website (no www, different case) ok", local_copy.contact_notes("go to brokerhomes.com") == [])
    check("contact: transcript_notes does not flag the right phone as 'check digits'", not any("555-010-0123" in x for x in local_copy.transcript_notes("Call 555-010-0123", "general")))
finally:
    writer.contact_info = local_copy.contact_info = saved
info = writer.contact_info()
check("contact.txt: phone from her voice-over, website empty until the maintainer fills it", info["phone"] == "555-010-0123" and info["website"] == "")
check("contact: no website on file means no website note", local_copy.contact_notes("visit anything.com") == [])

# ---------------------------------------------------------------- part titles
check("parts: label appended", local_copy.part_title("What an appraiser looks at", 2, 3) == "What an appraiser looks at (Part 2 of 3)")
long_t = local_copy.part_title("A very long title " * 8, 1, 3)
check("parts: long title trimmed, label kept, under 100", long_t.endswith("(Part 1 of 3)") and len(long_t) <= 95, long_t)
check("parts: label not doubled", local_copy.part_title("Title (Part 1 of 3)", 1, 3) == "Title (Part 1 of 3)")
with tempfile.TemporaryDirectory() as td:
    (Path(td) / "copy.json").write_text(json.dumps(local_copy.make_copy(tour)), encoding="utf-8")
    parts = local_copy.add_part_copy(td, [{"file": "final_part1.mp4", "text": tour[:200]}, {"file": "final_part2.mp4", "text": tour[200:]}], use_ai=False)
    saved_copy = json.loads((Path(td) / "copy.json").read_text(encoding="utf-8"))
    check("parts: every title says Part N of M, label field set",
          all(t.endswith(f"(Part {p['part']} of 2)") for p in parts for t in p["titles"]) and [p["label"] for p in parts] == ["Part 1 of 2", "Part 2 of 2"])
    check("parts: written to copy.json with files", [p["file"] for p in saved_copy["parts"]] == ["final_part1.mp4", "final_part2.mp4"])
    local_copy.attach_review_notes(td, tour)
    check("attach_review_notes keeps format and checklist", json.loads((Path(td) / "copy.json").read_text(encoding="utf-8"))["checklist"])

# ---------------------------------------------------------------- SRT + post pack
lines = [{"text": "Hello there", "start": 0.0, "end": 1.5}, {"text": "this is a test", "start": 1.5, "end": 3.2501}]
with tempfile.TemporaryDirectory() as td:
    p = captions.write_srt(lines, Path(td) / "a.srt")
    s = p.read_text(encoding="utf-8")
    check("srt: format", s.startswith("1\n00:00:00,000 --> 00:00:01,500\nHello there\n\n2\n00:00:01,500 --> 00:00:03,250"), repr(s))
    ass_dir = Path(td)
    words = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3} for i, w in enumerate("one two three four five six seven eight nine ten".split())]
    _, got = captions.write_ass(words, ass_dir, "t.ass")
    back = captions.read_ass_lines(ass_dir / "t.ass")
    check("srt: lines read back from .ass equal the burned-in lines", [b["text"] for b in back] == [g["text"] for g in got]
          and all(abs(b["start"] - g["start"]) < 0.011 and abs(b["end"] - g["end"]) < 0.011 for b, g in zip(back, got)))
check("postpack: frame score prefers sharp over blurry", postpack.frame_score(__import__("numpy").tile(__import__("numpy").array([[0, 255], [255, 0]] * 8), (8, 8)).astype("float32") * 0.5 + 60)
      > postpack.frame_score(__import__("numpy").full((128, 128), 120, "float32")))
check("postpack: black frame scores near zero", postpack.frame_score(__import__("numpy").zeros((64, 64), "float32")) == 0.0)
a = postpack.alt_text({"description": "Are you looking for a home? A tour of a ranch with a deck. #Solon", "format_label": "House tour"})
check("alt text: one to two sentences, no hashtags", "#" not in a and a.count(". ") <= 1 and "ranch with a deck" in a, a)
check("alt text with no copy still works", postpack.alt_text({}).startswith("A vertical"))
pt = postpack.post_text({"titles": ["T1", "T2", "T3"], "hook": "H", "captions": {k: f"cap {k}" for k in writer.PLATFORMS}, "checklist": ["x"]}, ["final.mp4"])
check("post text: has every platform and the titles and checklist", all(n.upper() in pt for n in writer.PLATFORMS.values()) and "1. T1" in pt and "[ ] x" in pt)

for job, names in (("demo-tour-moved", {"final.mp4", "captions.srt", "cover.jpg", "alt-text.txt", "post-text.txt"}),
                   ("demo-tips", {"final_part1.mp4", "final_part2.mp4", "final_part3.mp4", "captions_part1.srt", "captions_part2.srt",
                                  "captions_part3.srt", "cover.jpg", "cover_part2.jpg", "cover_part3.jpg", "alt-text.txt", "post-text.txt"})):
    if not (ROOT / "out" / job / "copy.json").exists():
        continue
    with tempfile.TemporaryDirectory() as td:
        dest = postpack.build(ROOT / "out" / job, Path(td) / "pack.zip")
        z = zipfile.ZipFile(dest)
        # closed below
        got_names = set(z.namelist())
        check(f"postpack {job}: expected files", got_names == names, got_names ^ names)
        check(f"postpack {job}: zip is sound", z.testzip() is None)
        text = "".join(z.read(n).decode("utf-8") for n in got_names if n.endswith((".txt", ".srt")))
        check(f"postpack {job}: no paths, secrets or source names", not any(b in text for b in ("C:\\", "C:/", "Users", ".env", "secret", "transcript", "normalized")))
        z.extract("cover.jpg", td)
        head = (Path(td) / "cover.jpg").read_bytes()[:3]
        check(f"postpack {job}: cover is a real jpeg", head == b"\xff\xd8\xff")
        srt = z.read("captions.srt" if job == "demo-tour-moved" else "captions_part1.srt").decode("utf-8").replace(chr(13) + chr(10), chr(10))
        check(f"postpack {job}: srt has numbered cues", srt.startswith("1\n") and " --> " in srt)
        if job == "demo-tips":
            pt = z.read("post-text.txt").decode("utf-8")
            check("postpack demo-tips: post text has one section per part", all(f"PART {n} OF 3" in pt for n in (1, 2, 3)))
        z.close()

# ---------------------------------------------------------------- ideas
fb = writer.fallback_ideas(5, 10)
check("ideas fallback: five, each with a format and four shots", len(fb) == 5 and all(i["format"] in formats.FORMATS and len(i["shots"]) == 4 and i["title"] for i in fb))
check("ideas fallback: a mix of formats", len({i["format"] for i in fb}) >= 4)
check("ideas fallback: every month, 1 to 12, works and is clean", all(len(writer.fallback_ideas(5, m)) == 5 and
      not any(writer._idea_problems(i) for i in writer.fallback_ideas(5, m)) for m in range(1, 13)))
check("ideas fallback: fall idea in October", any("Winter" in i["title"] or "Fall" in i["title"] for i in fb))
check("ideas fallback: no sale/sold/price/fair-housing words anywhere in the built-in list",
      not any(writer._idea_problems({"title": t, "shots": s}) for season in writer._SEASONAL.values() for t, _, s in season))
check("ideas: a bad AI idea is rejected", writer._idea_problems({"title": "Just listed: perfect for families", "shots": ["a", "b", "c", "d"]}) != [])
check("ideas: _clean_idea pads to four shots", len(writer._clean_idea({"title": "T", "format": "recipe", "shots": ["one"]})["shots"]) == 4)
check("ideas: unknown format becomes tips", writer._clean_idea({"title": "T", "format": "weird", "shots": list("abcd")})["format"] == "tips")

import os  # noqa: E402
saved_key = os.environ.get("ANTHROPIC_API_KEY")
os.environ["ANTHROPIC_API_KEY"] = ""
try:
    ni = writer.ideas(n=5, month=3)
    check("ideas: no key still returns 5 useful ideas", len(ni) == 5 and all(set(i) == {"title", "format", "shots"} for i in ni))
    check("ideas: n is respected", len(writer.ideas(n=3)) == 3)
    try:
        writer.write_copy("some words here", tempfile.gettempdir())
        check("write_copy without a key raises a plain error", False)
    except Exception as e:
        check("write_copy without a key raises a plain error", "API key" in str(e))
finally:
    if saved_key is None:
        os.environ.pop("ANTHROPIC_API_KEY", None)
    else:
        os.environ["ANTHROPIC_API_KEY"] = saved_key

# ---------------------------------------------------------------- the AI loop with a fake client (no API calls)
import types  # noqa: E402
from video_tool import ai  # noqa: E402


class FakeClient:
    def __init__(self, copies):
        self.copies, self.calls, self.prompts = copies, 0, []
        self.messages = self

    def create(self, **kw):
        self.prompts.append(kw["messages"][0]["content"])
        c = self.copies[min(self.calls, len(self.copies) - 1)]
        self.calls += 1
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="tool_use", input=json.loads(json.dumps(c)))],
                                     usage=types.SimpleNamespace(input_tokens=1000, output_tokens=500))


real_client = ai.client
try:
    # 1) every attempt breaks Fair Housing: the copy is KEPT, with plain notes, never thrown away
    fh = json.loads(json.dumps(good))
    fh["description"] = "A family-friendly home in Solon with three bedrooms."
    fake = FakeClient([fh])
    ai.client = lambda **kw: fake
    with tempfile.TemporaryDirectory() as td:
        _, got = writer.write_copy(tour, td)
        check("loop: three attempts then keeps the copy", fake.calls == 3 and got["description"].startswith("A family-friendly"))
        check("loop: problems become plain review notes", any("family-friendly" in n and "Fair Housing" in n for n in got["review_notes"]), got["review_notes"])
        check("loop: the retry prompt carries the fix list", "Fair Housing risk" in fake.prompts[1])
        check("loop: cost is recorded", got["ai_cost_usd"] > 0 and got["format"] == "house_tour" and got["checklist"])
        check("loop: copy.json written", json.loads((Path(td) / "copy.json").read_text(encoding="utf-8"))["format"] == "house_tour")
    # 2) clean on the second try
    fake = FakeClient([fh, good])
    ai.client = lambda **kw: fake
    with tempfile.TemporaryDirectory() as td:
        _, got = writer.write_copy(tour, td)
        check("loop: stops at the first clean attempt", fake.calls == 2 and got["review_notes"] == [])
    # 3) the model leaves parts out every time: that one is an error so the caller falls back
    fake = FakeClient([{"topic": "general"}])
    ai.client = lambda **kw: fake
    try:
        writer.write_copy(tour, tempfile.gettempdir())
        check("loop: missing parts raise so the pipeline can fall back", False)
    except Exception as e:
        check("loop: missing parts raise so the pipeline can fall back", "missing" in str(e))
    # 4) disclosure applied on the AI path too
    old_disc = writer.disclosure_text
    writer.disclosure_text = lambda: DISC
    fake = FakeClient([good])
    ai.client = lambda **kw: fake
    try:
        with tempfile.TemporaryDirectory() as td:
            _, got = writer.write_copy(tour, td)
            check("loop: AI path puts the disclosure once on every caption", all(v.count(DISC) == 1 and v.endswith(DISC) for v in got["captions"].values()))
    finally:
        writer.disclosure_text = old_disc
    # 5) the timeout is the capped one
    seen = {}
    ai.client = lambda **kw: (seen.update(kw), FakeClient([good]))[1]
    with tempfile.TemporaryDirectory() as td:
        writer.write_copy(tour, td)
    check("client: timeout 90 and one retry", seen == {"timeout": 90, "max_retries": 1}, seen)
finally:
    ai.client = real_client

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("ALL PASSED")
