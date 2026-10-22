"""The kinds of video Alex makes, and what each one needs: title shape, closing line, hashtags, what to film.

Title patterns come from her real YouTube titles (prompts/examples.md, "Video titles"). Nothing here is AI:
guess_format() is a cheap keyword score so the tool can pick a sensible format without a key.
"""
import re

FORMATS = {
    "house_tour": {
        "label": "House tour",
        "property": True,
        "title_pattern": ("Place name + what it is, parts separated by ' | '. Her real titles: "
                          "'Hidden Gem in Shellsburg Iowa | Rural Home with Pool House | Home Tour', "
                          "'10+ Acreage Near Cedar Rapids | Updated Historic Farmhouse in Van Horne Iowa', "
                          "'Home Tour: 220 Gaddis Blvd NE, Walford'. Only name a place or address she says "
                          "in the video. Never say sold or for sale unless she says it."),
        "cta": "Want to see it in person or have questions? Send me a message. I would be honored to help.",
        "hashtags": ["#IowaRealEstate", "#HomeTour", "#RealEstate", "#IowaHomes"],
        "film_list": [
            "Outside of the home from the street",
            "The front door and the entry",
            "Kitchen",
            "Living room and dining area",
            "Primary bedroom and its bathroom",
            "The other bedrooms and bathrooms",
            "Basement, garage or any other space",
            "Yard, deck or the view",
            "Record your voice-over after, saying what each room is",
        ],
    },
    "recipe": {
        "label": "Recipe",
        "property": False,
        "title_pattern": ("A plain food voice: name the dish and say it the way she would at the counter "
                          "('My go-to smoothie'). She has no recipe titles on her channel yet, so keep it "
                          "short, warm and honest. No real estate wording."),
        "cta": "Follow along for more. I share a little bit of everything here.",
        "hashtags": ["#Recipe", "#HomeCooking", "#FromScratch", "#IowaLife"],
        "film_list": [
            "The ingredients lined up together",
            "Step 1, the first thing you add",
            "Step 2, the middle step",
            "Step 3, the last step",
            "The finished dish, close up",
            "You taking the first bite or pouring a glass",
        ],
    },
    "tips": {
        "label": "Tips",
        "property": False,
        "title_pattern": ("A number or a plain promise, then the topic. Her real titles: "
                          "'Considering a Move? Be Prepared by Doing 3 Things to Do Now.', "
                          "'Getting Ready for Winter in Iowa. Fall Maintenance Tips.', "
                          "'Watch This Video Before You Buy a Country Home or Acreage.'. "
                          "Only use a number she says."),
        "cta": "If you are thinking about a move and want to talk it through, send me a message.",
        "hashtags": ["#RealEstateTips", "#HomeOwnerTips", "#IowaRealEstate", "#HomeBuyingTips"],
        "film_list": [
            "You introducing the topic in one sentence",
            "Tip 1, with something to show if you have it",
            "Tip 2",
            "Tip 3",
            "Tip 4 or 5 if you have them",
            "You saying the one thing to remember",
        ],
    },
    "market_update": {
        "label": "Market update",
        "property": True,
        "title_pattern": ("Short and plain, with the word 'Market Update'. Her real titles: "
                          "'Being Prepared! Market Update!' and the description line "
                          "'It's a hot market! Quick Tip to be competitive'. Never give a number she did not say."),
        "cta": "Questions about what this means for you? Send me a message and we can talk it through.",
        "hashtags": ["#MarketUpdate", "#IowaRealEstate", "#RealEstateMarket", "#CedarRapids"],
        "film_list": [
            "You to camera: where and when this update is for",
            "What you are seeing with homes on the market right now",
            "What is happening with prices or interest rates, in your words",
            "A street, sign or neighborhood shot that goes with it",
            "What a buyer should do about it",
            "What a seller should do about it",
        ],
    },
    "qa": {
        "label": "Question and answer",
        "property": False,
        "title_pattern": ("Say the question the way a client asks it. Her real titles: "
                          "'The Purpose of an Open House', "
                          "'Watch This Video Before You Buy a Country Home or Acreage.' "
                          "and her description line 'So where do you start?'."),
        "cta": "Have a question of your own? Send it to me and I will answer it in a video.",
        "hashtags": ["#AskARealtor", "#RealEstateQuestions", "#IowaRealEstate", "#HomeBuying"],
        "film_list": [
            "You reading or saying the question out loud",
            "The short answer, in one or two sentences",
            "The reason behind the answer",
            "An example from a client or your own home, no names",
            "You saying what to do next",
        ],
    },
}

# word or phrase -> weight. Matched on whole words, case-insensitive.
SIGNALS = {
    "house_tour": {
        "bedroom": 2, "bedrooms": 2, "bathroom": 2, "bathrooms": 2, "half bath": 2, "full bathroom": 2,
        "primary bedroom": 3, "kitchen": 1, "garage": 2, "deck": 2, "rec room": 3, "basement": 1,
        "come inside": 3, "upon entry": 3, "heading upstairs": 3, "heading downstairs": 3, "open concept": 3,
        "living room": 1, "dining room": 2, "schedule your tour": 4, "tour": 2, "walk in": 1,
        "acres": 2, "acreage": 2, "square feet": 2, "backyard": 1, "pool house": 2, "attached garage": 2,
        "this home": 2, "this house": 2, "come take a look": 2,
    },
    "recipe": {
        "recipe": 4, "ingredients": 3, "cup": 1, "cups": 1, "tablespoon": 2, "teaspoon": 2, "blend": 2,
        "blender": 2, "stir": 2, "mix": 1, "bake": 2, "oven": 2, "cook": 1, "cinnamon": 2, "flour": 2,
        "sugar": 1, "butter": 1, "delicious": 1, "taste": 1, "smoothie": 3, "protein powder": 2,
        "frozen": 1, "simmer": 2, "chop": 1, "dinner": 1, "bananas": 1, "salt": 1, "pepper": 1,
        "add in": 1, "go-to": 1, "water": 0.5,
    },
    "tips": {
        "tips": 3, "tip": 2, "things to": 2, "things you": 2, "you need to": 1, "keep that in mind": 2,
        "keep in mind": 2, "make sure": 2, "before you": 2, "advice": 2, "mistake": 2, "mistakes": 2,
        "consider": 1, "important": 1, "my top": 3, "number one": 1, "first thing": 1, "second thing": 1,
        "third thing": 1, "what to do": 2, "steps": 1, "appraisals": 2, "appraiser": 2, "appraisers": 2,
        "value": 1, "maintenance": 2, "get ready": 1, "prepare": 1, "prepared": 1, "you should": 1,
        "real estate agent": 1,
    },
    "market_update": {
        "market update": 5, "market": 2, "interest rates": 2, "inventory": 3, "days on market": 3,
        "buyers": 1, "sellers": 1, "prices": 1, "median": 3, "mortgage rates": 3, "housing market": 3,
        "this month": 1, "last month": 1, "this year": 0.5, "competitive": 1, "multiple offers": 2,
    },
    "qa": {
        "question": 3, "questions": 1, "someone asked": 4, "people ask": 4, "you asked": 4,
        "the answer": 3, "great question": 4, "i get asked": 4, "i was asked": 4, "a client asked": 4,
        "what is the": 1, "how do i": 2, "how does": 1, "why do": 1, "so the answer": 3,
    },
}

_COMPILED = {fid: {term: re.compile(r"(?<![\w'])" + re.escape(term).replace(r"\ ", r"\s+") + r"(?![\w'])", re.I)
                   for term in terms} for fid, terms in SIGNALS.items()}


def scores(text):
    """{format id: score} so a caller (or a test) can see why a guess was made."""
    out = {}
    for fid, terms in _COMPILED.items():
        total = 0.0
        for term, rx in terms.items():
            n = len(rx.findall(text or ""))
            if n:
                total += SIGNALS[fid][term] * min(n, 3)   # repeats help, but one word said ten times is not a format
        out[fid] = round(total, 2)
    return out


def guess_format(transcript_text):
    """house_tour | recipe | tips | market_update | qa. No AI; 'tips' when nothing stands out (the most general one)."""
    sc = scores(transcript_text)
    best = max(sc, key=lambda k: (sc[k], k == "tips"))
    if sc[best] < 3:
        return "tips"
    return best


def resolve(transcript_text, chosen=None):
    """The format to use: her pick when she made one (not 'auto'), else the guess."""
    if chosen in FORMATS:
        return chosen
    return guess_format(transcript_text)


def label(format_id):
    return FORMATS.get(format_id, FORMATS["tips"])["label"]
