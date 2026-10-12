"""Free-food detection and scoring.

Turns arbitrary event text (calendar entry, listserv email, org page) into a
structured verdict: is there free food, how sure are we, and why.

Design notes
------------
* Everything is explainable. `signals` records each phrase that moved the
  score, so the UI can say "because it said 'lunch will be provided'".
* Weights are additive and clamped. One STRONG phrase is enough on its own;
  three WEAK ones are not.
* Negatives are proximity-gated. A book talk where "the book is available for
  purchase" is not a paid-food event, and a conference with a "$25
  registration fee" may still hand out free sandwiches. A negative only counts
  when it sits near a food word.
* Confidence answers "is there free food" ONLY. Whether you can realistically
  walk there is a separate axis (`venue_class`) so the UI can filter on it
  without corrupting the food judgement.
"""

from __future__ import annotations

import re
import unicodedata

import school

CONFIRMED = "confirmed"   # text explicitly promises provided food
LIKELY = "likely"         # named food items or catering language
POSSIBLE = "possible"     # event genre that usually has food
UNLIKELY = "unlikely"


def _rx(*alts: str) -> "re.Pattern[str]":
    return re.compile(r"(?<![\w-])(?:" + "|".join(alts) + r")(?![\w-])", re.I)


# --- positive signals ----------------------------------------------------

# Actual foods, not just the word "food". Flyers say "free tacos" and
# "empanadas will be served"; enumerating only food/lunch/pizza scored those
# as a passing mention rather than a promise.
EDIBLE = (
    r"food|lunch(?:es)?|dinner|breakfast|brunch|snacks?|refreshments?|meals?|"
    r"pizza|tacos?|burritos?|quesadillas?|nachos?|bagels?|donuts?|doughnuts?|"
    r"pastr(?:y|ies)|croissants?|muffins?|sandwiches?|subs|wraps?|salads?|"
    # Party food by its own name. APALSA's mentorship social advertised
    # "sliders, chips & guac, chicken skewers" and scored nothing.
    r"sliders?|skewers?|guac(?:amole)?|appetizers?|hummus|samosas?|"
    r"spring\s+rolls?|dumplings?|tacos?|quesadillas?|charcuterie|"
    r"cookies?|brownies?|cupcakes?|cake|pie|empanadas?|dumplings?|noodles?|"
    r"curry|wings|barbecue|bbq|burgers?|hot\s+dogs?|fries|"
    r"ice\s+cream|churros?|popcorn|chips|candy|fruit|treats?|desserts?|"
    r"coffee|tea|drinks?|beverages?|charcuterie|hors\s+d['’]?oeuvres?|"
    r"bubble\s+tea|boba|smoothies?|lemonade|milkshakes?|"
    # Dishes by name. A student posting "what is on offer" writes what is on
    # the table -- "pad thai, fried rice, potstickers" -- with none of the
    # promising verbs a flyer uses, and scored as no food at all.
    r"pad\s+thai|fried\s+rice|pot\s?stickers?|pots?ickers?|spring\s+rolls?|"
    r"sushi|ramen|pho|bibimbap|falafel|shawarma|gyros?|kebabs?|"
    r"biryani|samosas?|pupusas?|arepas?|paella|poke|"
    r"boxed?\s+lunch(?:es)?|catering|"
    # Campus flyers name the vendor, not the food: "Chipotle Provided" says
    # more about what is on offer than "lunch provided" does.
    r"chipotle|cava|sweetgreen|panera|chick-?fil-?a|shake\s+shack|"
    r"&\s*pizza|\bpot?tbelly|halal\s+guys|dunkin|starbucks|insomnia\s+cookies")

STRONG = [
    # One optional modifier between the offer and the food, because flyers
    # write "free bubble tea", "free Thai lunch", "free hot breakfast". More
    # than one word apart and "free" is usually promising something else.
    (_rx(r"free\s+(?:\w+\s+)?(?:" + EDIBLE + r")"), 55, "free food stated"),
    (_rx(r"(?:" + EDIBLE + r")\s+"
         r"(?:will\s+be\s+|is\s+|are\s+|to\s+be\s+)?"
         r"(?:provided|served|available|furnished)"),
     55, "food provided"),
    (_rx(r"(?:we(?:'ll| will)\s+(?:be\s+)?)?(?:provid\w+|serv\w+)\s+"
         r"(?:free\s+)?(?:food|lunch|dinner|breakfast|pizza|snacks?|refreshments?)"),
     50, "food provided"),
    (_rx(r"complimentary\s+\w*\s*(?:food|lunch|dinner|breakfast|beverages?|treats?|drinks?|"
         r"snacks?|refreshments?|coffee)"), 50, "complimentary food"),
    (_rx(r"lunch\s+(?:is\s+|will\s+be\s+)?(?:provided|served|included|on\s+us)",
         r"(?:boxed?|bagged)\s+lunch(?:es)?"), 55, "lunch provided"),
    # The plainest way anyone says it, and it scored zero: the Antitrust
    # Association's Ropes & Gray panel said "Of course, there will be food"
    # and was published as having none. The rules all read
    # "<food> <verb>"; this is the same sentence with the words the other
    # way round.
    (_rx(r"there\s+(?:will\s+be|'ll\s+be|is|are)\s+"
         r"(?:free\s+|some\s+|light\s+|plenty\s+of\s+)?(?:" + EDIBLE + r")"),
     50, "food provided"),
    # How a flyer invites you to eat, rather than announcing that food exists.
    # "LALSA Asado. Enjoy great food and celebrate our community" scored zero.
    (_rx(r"(?:enjoy|grab|help\s+yourself\s+to)\s+"
         r"(?:some\s+|great\s+|delicious\s+|free\s+|a\s+)?(?:" + EDIBLE + r")",
         r"join\s+us\s+for\s+(?:some\s+|free\s+|a\s+)?(?:" + EDIBLE + r")",
         # "Have a drink and eat some food (on us, because we're providing a
         # large tab)" -- PPP's happy hour, which scored as a social with no
         # food. Telling the reader to eat is offering the food.
         r"\beat\s+(?:some\s+|great\s+|free\s+)?(?:" + EDIBLE + r")"),
     45, "food offered"),
    # The meal is the event. "Dinner with Paul, Weiss", "Paul, Weiss 1L
    # Dinner" and "BLSA Alumni Brunch" named nothing but the meal, and every
    # rule here wants a serving verb beside it, so all three scored as having
    # no food. Likely rather than confirmed: that a dinner has food is certain,
    # that a reader's plate is paid for is not said.
    (_rx(r"\b(?:dinner|brunch|breakfast|luncheon)\s+with\b",
         r"\b(?:1Ls?|2Ls?|alumni|networking|welcome|annual|firm|holiday|"
         r"fall|spring|winter|summer)\s+"
         r"(?:dinner|brunch|breakfast|luncheon|picnic|barbecue|bbq|cookout)\b"),
     45, "the meal is the event"),
    # Food introduced by what the event has rather than by a serving verb:
    # "a social featuring YUMMY food". One optional adjective between, the
    # same allowance "free ___ food" already gets.
    (_rx(r"featuring\s+(?:\w+\s+){0,2}(?:" + EDIBLE + r")"),
     45, "food offered"),
    (_rx(r"cater\w+"), 45, "catered"),
    (_rx(r"food\s+and\s+(?:drinks?|beverages?)", r"(?:drinks?|beverages?)\s+and\s+food"),
     45, "food and drink"),
]

ITEMS = [
    (_rx(r"pizza"), 34, "pizza"),
    (_rx(r"bagels?", r"donuts?", r"doughnuts?", r"pastries", r"croissants?", r"muffins?"),
     30, "baked goods"),
    (_rx(r"tacos?", r"burritos?", r"chipotle", r"cava", r"sandwiches?", r"subs", r"salad\s+bar",
         r"noodles?", r"dumplings?", r"empanadas?",
         # Dishes by name. A student answering "what is on offer" writes what
         # is on the table -- "pad thai, fried rice, potstickers" -- with none
         # of the promising verbs a flyer uses, and it scored as no food.
         r"pad\s+thai", r"fried\s+rice", r"pot\s?stickers?", r"pots?ickers?",
         r"spring\s+rolls?", r"sushi", r"ramen", r"\bpho\b", r"bibimbap",
         r"falafel", r"shawarma", r"gyros?", r"kebabs?", r"biryani",
         r"samosas?", r"pupusas?", r"arepas?", r"paella", r"poke\s+bowls?",
         r"wings", r"quesadillas?", r"nachos?"), 30, "meal items"),
    (_rx(r"cookies?", r"brownies?", r"cupcakes?", r"ice\s+cream", r"candy", r"popcorn",
         r"chips", r"boba", r"bubble\s+tea", r"smoothies?"), 20, "snacks"),
    (_rx(r"hors\s+d['’]?oeuvres?", r"charcuterie", r"buffet", r"potluck"), 34, "spread"),
    (_rx(r"refreshments?", r"light\s+(?:fare|bites|refreshments?|breakfast|lunch)", r"snacks?",
         r"treats?"), 28, "refreshments"),
    (_rx(r"coffee\s+and\s+\w+", r"continental\s+breakfast", r"breakfast\s+spread"), 30, "breakfast"),
]

GENRE = [
    (_rx(r"reception"), 22, "reception"),
    (_rx(r"coffee\s+hour", r"tea\s+time", r"chaplains?['’]?\s+tea"), 24, "coffee hour"),
    (_rx(r"open\s+house", r"meet\s+and\s+greet", r"mixer", r"social\s+hour", r"happy\s+hour"),
     20, "social"),
    (_rx(r"luncheon", r"lunch\s+(?:talk|panel|series|session|seminar)", r"lunch\s*&\s*learn",
         r"lunch\s+and\s+learn"), 24, "lunch event"),
    (_rx(r"banquet", r"gala", r"welcome\s+back", r"kick-?off", r"orientation"), 16, "gathering"),
    (_rx(r"tabling", r"info\s*session", r"information\s+session"), 12, "tabling"),
]

# --- negative signals ----------------------------------------------------

# Contextual negatives carry a (mode, window) gate saying where a food word
# must appear for the negative to be about food at all.
#   "back" - the purchased thing is named BEFORE the phrase:
#            "Copies of the book will be available for purchase" is not food.
#   "fwd"  - the food is named AFTER: "bring your own lunch".
#   "both" - either side.
NEG_CONTEXTUAL = [
    (_rx(r"brown\s*bag", r"bring\s+your\s+own", r"byo\w*", r"lunch\s+on\s+your\s+own",
         r"pack\s+a\s+lunch"), -60, "bring your own food", ("both", 60)),
    (_rx(r"for\s+purchase", r"for\s+sale", r"pay\s+your\s+own", r"dutch\s+treat"),
     -50, "food costs money", ("back", 46)),
    (_rx(r"registration\s+fee", r"admission\s+fee", r"\$\d+", r"fee\s+of"),
     -25, "paid event", ("both", 90)),
]

# Count wherever they appear -- these name a food cost on their own.
NEG_ABSOLUTE = [
    # Denials have to speak the same vocabulary as the promises. This rule
    # knew only the literal word "food", so "no lunch provided" scored exactly
    # what "Lunch will be provided" scored -- confirmed, 100 -- on a site whose
    # entire job is saying whether there is free food.
    (_rx(r"no\s+(?:free\s+)?(?:" + EDIBLE + r")",
         r"(?:" + EDIBLE + r")\s+(?:will|shall)\s+not\s+be",
         r"(?:will|shall)\s+not\s+be\s+(?:provid\w+|serv\w+|offer\w+)"
         r"(?:\s+\w+){0,2}\s+(?:" + EDIBLE + r")",
         r"(?:" + EDIBLE + r")\s+(?:is|are)\s+not\s+(?:provided|served|included)",
         r"there\s+(?:will|shall)\s+not\s+be\s+(?:any\s+)?(?:" + EDIBLE + r")",
         r"without\s+(?:" + EDIBLE + r")"),
     -70, "no food"),
    (_rx(r"bake\s+sale", r"cash\s+bar", r"concessions"), -50, "food costs money"),
    (_rx(r"sold\s+out", r"waitlist\s+only", r"capacity\s+reached"), -25, "may be full"),
]

FOOD_WORD = _rx(
    r"food", r"lunch(?:es|eon)?", r"dinner", r"breakfast", r"brunch", r"pizza", r"snacks?",
    r"refreshments?", r"meals?", r"drinks?", r"beverages?", r"coffee", r"tea", r"cater\w+",
    r"bagels?", r"donuts?", r"pastries", r"cookies?", r"sandwiches?", r"buffet", r"reception",
    r"hors", r"treats?", r"dessert", r"eat", r"dining", r"pizza",
)

PROVIDE_VERB = re.compile(
    r"provid\w+|serv(?:ed|ing|e)|available|included|complimentary|free|cater\w+|hosted", re.I)

HYBRID_RX = _rx(r"in[\s-]+person", r"hybrid", r"or\s+on\s+zoom",
                r"zoom\s*(?:/|or|and)\s*in[\s-]+person")
ONLINE_RX = _rx(r"virtual", r"online\s+only", r"zoom", r"webinar", r"livestream", r"web\s*ex",
                r"remote\s+only")

# --- venue awareness -----------------------------------------------------

MEALS = [
    (_rx(r"breakfast", r"bagels?", r"donuts?", r"continental", r"coffee\s+hour", r"pastries"),
     "breakfast"),
    (_rx(r"lunch", r"luncheon", r"boxed?\s+lunch", r"noon"), "lunch"),
    (_rx(r"dinner", r"banquet", r"supper", r"gala"), "dinner"),
    (_rx(r"snacks?", r"refreshments?", r"treats?", r"cookies?", r"dessert", r"reception",
         r"happy\s+hour", r"hors"), "snacks"),
]

RSVP = _rx(r"rsvp", r"registration\s+(?:required|is\s+required)",
           r"register\s+(?:here|at|by|in\s+advance)", r"sign\s*-?\s*up\s+(?:required|here)",
           r"space\s+is\s+limited", r"limited\s+(?:seating|spots|capacity)", r"first\s+come",
           r"while\s+supplies\s+last")

RESTRICTED = _rx(r"members?\s+only", r"1Ls?\s+only", r"2Ls?\s+only", r"3Ls?\s+only",
                 r"by\s+invitation", r"invite\s+only", r"faculty\s+only", r"staff\s+only",
                 r"llm\s+only", r"for\s+\w+\s+students\s+only")

URL_RX = re.compile(r"https?://[^\s<>\"')\]]+")


def _clean(text: str) -> str:
    """Normalise unicode, strip HTML/iCal escaping, collapse whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</tr>", " ", text, flags=re.I)
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&#160;", " "), ("&#8217;", "'"),
                 ("&rsquo;", "'"), ("&lsquo;", "'"), ("&quot;", '"'), ("&#39;", "'"),
                 ("&ldquo;", '"'), ("&rdquo;", '"'), ("&mdash;", "-"), ("&ndash;", "-")):
        text = text.replace(a, b)
    text = text.replace("\\,", ",").replace("\;", ";").replace("\\n", " ").replace("\\\\", "")
    return re.sub(r"\s+", " ", text).strip()


def _near(text: str, match, rx, window: int = 90, mode: str = "both") -> bool:
    """True if `rx` occurs within `window` chars of `match`, on the given side."""
    if mode == "back":
        segment = text[max(0, match.start() - window):match.start()]
    elif mode == "fwd":
        segment = text[match.end():match.end() + window]
    else:
        segment = text[max(0, match.start() - window):min(len(text), match.end() + window)]
    return bool(rx.search(segment))


def _scan(text, table, signals, kind, gated=False) -> int:
    """Sum rule weights. Gated tables carry a (mode, window) food-proximity test."""
    total = 0
    for rule in table:
        rx, weight, label = rule[0], rule[1], rule[2]
        mode, window = rule[3] if len(rule) > 3 else ("both", 90)
        for m in rx.finditer(text):
            if gated and not _near(text, m, FOOD_WORD, window, mode):
                continue                      # negative was about something else
            total += weight
            signals.append({"kind": kind, "label": label, "weight": weight,
                            "phrase": _clean(m.group(0))[:60]})
            break                             # each rule fires at most once
    return total


# Order meals as they occur in a day so the primary meal is the earliest one.
DAY_ORDER = {"breakfast": 0, "lunch": 1, "snacks": 2, "dinner": 3}


def _pick_meals(body: str):
    """Return every meal on offer, earliest first.

    Events routinely serve more than one ("Breakfast and lunch will be
    served"), so collapsing to a single label loses real information. A meal
    counts when it appears next to a serving verb; failing that, the single
    best-scoring mention is used.
    """
    scores = {}
    for rx, name in MEALS:
        score = 0
        for m in rx.finditer(body):
            score += 1
            if _near(body, m, PROVIDE_VERB, window=60):
                score += 5      # "lunch will be served" beats a passing mention
        if score:
            scores[name] = score
    if not scores:
        return []
    served = [n for n, s in scores.items() if s >= 6]
    if not served:
        served = [max(scores, key=lambda n: scores[n])]
    return sorted(served, key=lambda n: DAY_ORDER.get(n, 9))


def classify_venue(location: str = "", body: str = "", group: str = "") -> str:
    """'home' (the docket's own campus), 'away' (another campus of the
    university, a shuttle ride), or 'unknown'.

    Read from the school's profile: the buildings and landmarks that
    `campus.locate` uses. The location is asked first, then the body.

    Apostrophes are stripped first: feeds write "Regent's Hall", "Regents
    Hall" and "Regents’ Hall" interchangeably, and a possessive should not
    decide whether you walk downstairs or ride a shuttle across town.
    """
    profile = school.active()
    location = re.sub(r"['’`]", "", location or "")
    body = re.sub(r"['’`]", "", body or "")
    if group and group.lower() in {g.lower() for g in profile.HOME_GROUPS}:
        return "home"
    for text in (location, body):
        if any(pattern.search(text) for pattern, _, _ in profile.venues):
            return "home"
        if profile.away.search(text):
            return "away"
    return "unknown"


def score_event(title: str = "", description: str = "", location: str = "",
                source: str = "", url: str = "", group: str = "",
                is_online: bool = False) -> dict:
    """Score one event. Returns a verdict dict; never raises on odd input."""
    title, description, location = _clean(title), _clean(description), _clean(location)
    # Title text counts twice: "Free Pizza Friday" is a stronger claim than the
    # same words buried in paragraph four of a newsletter.
    #
    # Joined on a full stop, not a space. Every rule here is built from `\s+`
    # runs, so a bare space let a pattern straddle the join: the title "Lunch
    # will NOT be served" produced the phrase "served Lunch" across its own two
    # copies and was published as confirmed food, quoting a phrase that appears
    # in no source. A stop cannot be crossed by `\s+`.
    body = " . ".join([title, title, description, location])

    signals: list = []
    raw = 0
    raw += _scan(body, STRONG, signals, "strong")
    raw += _scan(body, ITEMS, signals, "item")
    raw += _scan(body, GENRE, signals, "genre")
    raw += _scan(body, NEG_ABSOLUTE, signals, "negative")
    raw += _scan(body, NEG_CONTEXTUAL, signals, "negative", gated=True)

    has_strong = any(s["kind"] == "strong" for s in signals)
    has_item = any(s["kind"] == "item" for s in signals)

    # Online-only kills the food, but only when there is no physical room.
    # "Zoom / In Person" is a choice of two rooms, not an online-only event.
    # Hybrid wording keeps the food on the table for whoever turns up.
    hybrid = bool(HYBRID_RX.search(body))
    online_only = bool(is_online or (ONLINE_RX.search(body) and not location
                                     and not hybrid))
    if online_only:
        raw -= 45
        signals.append({"kind": "negative", "label": "online only", "weight": -45,
                        "phrase": "virtual"})

    confidence = max(0, min(100, raw))
    meals = _pick_meals(body)

    # No hard veto on cost signals: they simply subtract. "Free pizza AND a
    # $2 bake sale" should net out rather than be silently discarded.
    # A denial is a statement, not a weak signal. Leaving it to arithmetic
    # meant a long enough flyer could outvote its own "no lunch provided" --
    # which is exactly the sentence a reader needs obeyed. Only the denial rule
    # counts here: "cash bar" and "sold out" say something about cost and
    # capacity, not about whether food exists.
    denied = any(s["kind"] == "negative" and s["label"] == "no food"
                 for s in signals)
    if online_only or denied:
        tier = UNLIKELY
    elif has_strong and confidence >= 50:
        tier = CONFIRMED
    elif confidence >= 45 or (has_item and confidence >= 34):
        tier = LIKELY
    elif confidence >= 22:
        tier = POSSIBLE
    else:
        tier = UNLIKELY

    return {
        "confidence": confidence,
        "tier": tier,
        "has_food": tier in (CONFIRMED, LIKELY),
        "venue_class": classify_venue(location, body, group),
        "meal": meals[0] if meals else None,
        "meals": meals,
        "rsvp_required": bool(RSVP.search(body)),
        "restricted": bool(RESTRICTED.search(body)),
        "signals": sorted(signals, key=lambda s: -abs(s["weight"])),
        "reason": "; ".join(dict.fromkeys(s["label"] for s in signals))[:120] or "no food signals",
        "links": URL_RX.findall(description)[:3],
        "source": source,
        "url": url,
    }
