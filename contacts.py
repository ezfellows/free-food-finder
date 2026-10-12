"""Law firms: which ones exist, and when a piece of text names one.

The registry is data/firms.json, a list of [key, name] pairs -- the large
firms a law student is most likely to meet at a reception. Add to it as you
find firms it lacks. Finding a firm in prose is the hard part: "Weil",
"Ropes" and "Akin" are ordinary words and surnames as well as firms, so a
name counts only near words that make it a firm, and never as a person's.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
REGISTRY_FILE = os.path.join(ROOT, "data", "firms.json")

# Words that are firm-name tokens but too generic to key on alone.
TOKEN_STOPLIST = {
    "associates", "partners", "attorneys", "counsel", "national", "american",
    "international", "global", "united", "general", "federal", "washington",
    "chicago", "boston", "denver", "atlanta", "houston", "seattle",
}


def firm_registry() -> list:
    """[(key, name)] for every known firm, or [] if the file is missing."""
    try:
        with open(REGISTRY_FILE, encoding="utf-8") as fh:
            return [tuple(x) for x in json.load(fh)]
    except (OSError, ValueError, TypeError):
        return []


SHORT_TOKEN_MAX = 5     # "Weil", "Ropes", "Akin" -- real names, risky words

# Short firm tokens that are also ordinary English. Uniqueness within the
# registry does not help here: "Will" is unique among firms (McDermott Will &
# Emery) and still matches "will host". These only ever resolve as part of a
# full multi-word name.
SHORT_STOPLIST = {
    "will", "case", "best", "long", "gray", "grey", "white", "brown", "young",
    "king", "wood", "main", "park", "hill", "bell", "rose", "page", "bond",
    "hunt", "ward", "lane", "dean", "reed", "cole", "hale", "ross", "day",
    "may", "york", "cook", "fish", "gold", "stone", "banks", "burns", "field",
}


def _tokens(name: str) -> list:
    # Four characters is the floor: BigLaw is routinely referred to by a short
    # first name ("Meet Weil attorneys"), and a 6-character floor missed those
    # entirely. Short tokens are held to a stricter context test below.
    return [t for t in re.split(r"[^A-Za-z]+", name) if len(t) >= 4]


# A bare surname only counts as a firm when the surrounding text is about
# law. Uniqueness within the registry is not enough on its own: "Morgan" is
# unique among firms and still matches "Morgan drove to Davis for the weekend".
LEGAL_CONTEXT = re.compile(
    r"(?<![\w-])(?:firms?|LLP|LLC|attorneys?|lawyers?|associates?|partners?|"
    r"counsel|of\s+counsel|reception|recruit\w*|interview\w*|info\s*session|"
    r"summer\s+associate|clerk\w*|practice|litigation|transactional|"
    r"antitrust|corporate|pro\s+bono|OCI|callback|networking|alumni|"
    r"office|hiring|1L|2L|3L|law\s+school|panel)(?![\w-])", re.I)

CONTEXT_WINDOW = 70
SHORT_CONTEXT_WINDOW = 28

# A short token needs an unmistakable firm word beside it, not merely legal
# surroundings: "law" and "panel" are everywhere in this corpus, so the loose
# test would let "with Paul about the law" resolve to Paul Weiss.
FIRM_CONTEXT = re.compile(
    r"(?<![\w-])(?:attorneys?|lawyers?|LLP|LLC|firms?|partners?|associates?|"
    r"counsel|office|offices|recruit\w*|summer\s+associate|practice\s+group|"
    r"callback|alumni\s+of)(?![\w-])", re.I)

# Function words that legitimately precede a firm name ("a partner at
# Milbank", "Reception With Venable"). Anything else capitalised in front of a
# bare surname is almost certainly a given name.
LEAD_INS = {
    "at", "from", "with", "and", "by", "of", "for", "to", "the", "a", "an",
    "join", "joins", "hosted", "featuring", "presented", "sponsored", "visit",
    "meet", "meets", "meeting", "hear", "welcome", "welcoming",
    "between", "plus", "versus", "v", "vs", "including", "via", "attorneys",
    "lawyers", "partners", "associates", "firm", "firms", "counsel", "office",
    # "Step Inside Milbank's Financial Restructuring Practice" read "Inside"
    # as a given name and Milbank as its surname, so CFLO's Milbank reception
    # named no firm at all.
    "inside",
    # Field labels. "Location: Milbank DC" is where the event is, and reading
    # "Location" as a first name made every mention of Milbank in the same
    # message somebody's surname.
    "location", "venue", "where", "host", "hosts", "sponsor", "sponsors",
}

# Words that end an organisation's name, and so come straight before whatever
# the organisation is presenting.
ORG_NOUNS = {
    "organization", "organisation", "society", "association", "club",
    "council", "alliance", "coalition", "caucus", "committee", "network",
    "institute", "center", "centre", "project", "collective", "union",
    "presents", "x",
}

# Titles that mark the following capitalised word as a person.
# Anchored at BOTH ends. With only the trailing `$`, `.search()` on a whole
# word matched any word ENDING in a title: "firms" ends in "ms", "programs" in
# "ms", "python" in "hon". So the word most likely to introduce a list of firms
# was the word that suppressed them -- "firms: Skadden" found nothing.
PERSON_TITLES = re.compile(
    r"^(?:dr|prof|professor|ambassador|judge|justice|dean|mr|mrs|ms|mx|hon|"
    r"honorable|rep|representative|sen|senator|gen|general|col|capt|sir|dame|"
    r"chair|chairman|chairwoman|director|president|secretary)\.?$", re.I)


# A case caption. ACS's flyer for "The Slaughter of Agency Independence"
# (14 Oct 2026) cited Trump v. Slaughter, and with "Professor of Law" and
# "General Counsel" in reach every "Slaughter" on it became Slaughter and May:
# a firm tag on a constitutional-law panel. `v` stays in LEAD_INS -- it is not
# a given name -- but a surname on either side of one is a party, not a firm.
CASE_BEFORE = re.compile(r"\b[A-Z][\w.'&-]*\s+(?:[vV][sS]?|[vV]ersus)\.?\s*$")
CASE_AFTER = re.compile(r"^\s+(?:[vV][sS]?|[vV]ersus)\.?\s+[A-Z]")


def _named_in_a_case(text: str, start: int, end: int) -> bool:
    return bool(CASE_BEFORE.search(text[max(0, start - 60):start])
                or CASE_AFTER.match(text[end:end + 30]))


def _looks_like_a_person(text: str, start: int) -> bool:
    """True when the token at `start` is preceded by a name rather than prose.

    "Ambassador Jane Sullivan" and "Ruth Morrison, Director" are people;
    "a partner at Milbank" is a firm. In a law-school corpus the surrounding
    words are legal either way, so the giveaway is the word immediately
    before: a capitalised non-function word is a given name.
    """
    # Trailing punctuation must go first: "Office of Social Innovation,
    # Wilkinson led efforts" is a person referred to by surname after a comma.
    before = text[:start].rstrip().rstrip(",;:()[]\"'\u2019\u201d-").rstrip()
    if not before:
        return False
    m = re.search(r"([A-Za-z][A-Za-z'.\-]*)\s*$", before)
    if not m:
        return False
    word = m.group(1)
    # `LEAD_INS` first. It is an explicit statement that this word is prose,
    # and it should not lose to a heuristic about titles -- "firms" is in that
    # list precisely because it introduces firms, and it was being read as a
    # courtesy title anyway.
    if word.lower() in LEAD_INS:
        return False
    if PERSON_TITLES.search(word):
        return True
    # The last word of an organisation's name is not a first name. OCR'd
    # flyers run the host straight into the event -- "LGBTQIA+ STUDENT
    # ORGANIZATION COVINGTON X OUTLAW HAPPY HOUR" -- which made ORGANIZATION a
    # first name and Covington its surname, so every mention of Covington in
    # the flyer, "Covington attorneys" included, was taken to be that person.
    # Deliberately a list rather than "any capitalised word in shouted text":
    # speaker lists are shouted too, and "WILLIAM QUINN, Senior Policy
    # Director" is a man, not Quinn Emanuel.
    if word.lower() in ORG_NOUNS:
        return False
    # A capitalised word before a firm name usually is a given name -- except
    # when it is another firm. These events advertise as comma-separated lists
    # ("Cleary Gottlieb, Jones Day, Cooley, Orrick"), and reading each firm as
    # the surname of the one before it lost every firm after the first.
    if word.lower().strip(".,&'") in FIRM_WORDS:
        return False
    return word[:1].isupper()


# What firms are called in their own recruiting mail when it is not their
# registry name. Unambiguous on sight, so they need no legal context: nobody
# but Morrison & Foerster is "MoFo", and CFLO's "Day in the Life of a MoFo
# Capital Markets Attorney" named no firm without it.
NICKNAMES = {
    "Morrison Foerster": ("MoFo",),
}


def build_aliases(registry: list) -> dict:
    """{canonical name: (strong_pattern, weak_pattern)}.

    Strong forms are multi-word and unambiguous on sight: the full registry
    name with flexible punctuation, so "Gibson, Dunn & Crutcher" also matches
    "Gibson Dunn". Weak forms are single distinctive tokens, which only count
    when legal context sits nearby.
    """
    counts = defaultdict(int)
    for _, name in registry:
        for token in set(t.lower() for t in _tokens(name)):
            counts[token] += 1

    def compile_any(alts):
        if not alts:
            return None
        body = "|".join(sorted(set(alts), key=len, reverse=True))
        try:
            return re.compile(r"(?<![\w-])(?:" + body + r")(?![\w-])", re.I)
        except re.error:
            return None

    out = {}
    for _, name in registry:
        parts = [re.escape(p) for p in re.split(r"[^A-Za-z']+", name) if p]
        if not parts:
            continue
        sep = r"[\s,&']*"
        strong = [sep.join(parts)]
        if len(parts) >= 2:
            strong.append(sep.join(parts[:2]))
        weak = []
        for token in _tokens(name):
            low = token.lower()
            if counts[low] != 1 or low in TOKEN_STOPLIST:
                continue
            if len(token) <= SHORT_TOKEN_MAX and low in SHORT_STOPLIST:
                continue
            weak.append(re.escape(token))
        if len(parts) == 1:
            # Single-word firms (Milbank, Venable) have no strong form at all.
            strong, weak = [], strong + weak
        strong = strong + [re.escape(n) for n in NICKNAMES.get(name, ())]
        pair = (compile_any(strong), compile_any(weak))
        if pair[0] or pair[1]:
            out[name] = pair
    return out


def find_firms(text: str, aliases: dict = None, established=()) -> list:
    """Canonical firm names mentioned in the text.

    A multi-word match stands on its own. A bare-surname match must have legal
    context within `CONTEXT_WINDOW` characters, so an email about a firm
    reception resolves and a sentence about someone named Morgan does not.

    `established` names firms the surrounding text has already settled: a
    title is read on its own, and "Carve Your Path at Skadden" carries no
    legal word, though the email it heads says where Skadden's office is.
    For those names the context test is waived; the person check is not.
    """
    if not text:
        return []
    aliases = aliases if aliases is not None else ALIASES
    found = []

    # A firm named in full settles what the text is about. These events
    # advertise as lists -- "Cleary Gottlieb, Jones Day, Cooley, Orrick,
    # Groom Law Group" -- and the context window is measured from each token,
    # so "Attending firms:" was out of reach by the fourth entry and the
    # single-word firms at the tail were dropped. A confirmed firm elsewhere
    # in the same text is context enough; the person check still stands.
    firm_established = any(
        strong.search(text) for strong, _ in aliases.values() if strong)

    for name, (strong, weak) in aliases.items():
        if strong and strong.search(text):
            found.append(name)
            continue
        if not weak:
            continue
        # If the text introduces a person with this surname anywhere, then
        # every bare mention of it is that person -- "Owen Wilkinson ...
        # Wilkinson led efforts" is one person, not a litigation boutique.
        occurrences = list(weak.finditer(text))
        if any(_looks_like_a_person(text, m.start()) for m in occurrences):
            continue
        # The same holds for a party to a case: "Trump v. Slaughter" names
        # one, and every other "Slaughter" in the text is that party.
        if any(_named_in_a_case(text, m.start(), m.end()) for m in occurrences):
            continue
        for m in occurrences:
            short = len(m.group(0)) <= SHORT_TOKEN_MAX
            window = SHORT_CONTEXT_WINDOW if short else CONTEXT_WINDOW
            rx = FIRM_CONTEXT if short else LEGAL_CONTEXT
            lo = max(0, m.start() - window)
            hi = min(len(text), m.end() + window)
            if firm_established or name in established or rx.search(text[lo:hi]):
                found.append(name)
                break
    return found


REGISTRY = firm_registry()
ALIASES = build_aliases(REGISTRY)

# Every word that appears in a canonical firm name, for the list check in
# `_looks_like_a_person`. Built once, after the registry exists.
FIRM_WORDS = frozenset(
    w for name in ALIASES
    for w in re.split(r"[^A-Za-z]+", name.lower()) if len(w) > 2)
