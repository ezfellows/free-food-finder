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
    # First words that are not what anyone calls the firm: a number ("Three
    # attorneys" is not Three Crowns), given names (Bryan Cave, Duane Morris,
    # Jackson Walker -- and Justice Jackson), and surnames a professor or a
    # foundation is likelier to carry (Gates, Holland, Greenberg, Rothschild,
    # which begins Fox Rothschild once "Fox" is too short to count).
    "three", "bryan", "duane", "jackson", "gates", "holland", "greenberg",
    "rothschild", "haynes", "squire", "ballard", "joseph", "sanford",
    "murphy", "berger", "guerra",
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
    # A CFLO title inviting readers inside Milbank's restructuring practice
    # read "Inside" as a given name and Milbank as its surname, so CFLO's
    # Milbank reception named no firm at all.
    "inside",
    # Field labels. "Location: Milbank DC" is where the event is, and reading
    # "Location" as a first name made every mention of Milbank in the same
    # message somebody's surname.
    "location", "venue", "where", "host", "hosts", "sponsor", "sponsors",
    # A speaker's job title, then the firm: "Wren Halloway, Partner, Dentons",
    # "Fellow Hausfeld". Capitalised, so they read as given names too.
    "partner", "associate", "shareholder", "fellow", "principal",
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


# A case caption. ACS's flyer for its panel on Trump v. Slaughter (14 Oct
# 2026) put the case's name in the title, and with "Professor of Law" and
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
    # A firm in brackets after a name is where that person works: "moderated
    # by Ash Pell (Freshfields)" made Freshfields the moderator's surname.
    if text[:start].rstrip().endswith("("):
        return False
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
    # flyers run the host straight into the event: on OutLaw's Covington
    # happy-hour flyer the shouted word ORGANIZATION ran into COVINGTON,
    # which made ORGANIZATION a first name and Covington its surname, so
    # every mention of Covington in the flyer, "Covington attorneys"
    # included, was taken to be that person.
    # Deliberately a list rather than "any capitalised word in shouted text":
    # speaker lists are shouted too, and a shouted speaker surnamed QUINN,
    # beside a job title, is a person, not Quinn Emanuel.
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
# but Morrison & Foerster is "MoFo", and a CFLO listing for a MoFo
# capital-markets session named no firm without it.
NICKNAMES = {
    "Morrison Foerster": ("MoFo",),
    # Merged firms still go by their old names, and the registry's first word
    # is not the one people use: "Perkins Coie", not "Ashurst".
    "Ashurst Perkins Coie": ("Perkins Coie",),
    "A&O Shearman": ("Allen & Overy", "Shearman & Sterling"),
    "Herbert Smith Freehills Kramer": ("HSF Kramer",),
    "McDermott Will & Emery": ("McDermott Will & Schulte",),
    # Rebrands, and the longer names still in use for them.
    "Fenwick": ("Fenwick & West",),
    "Seyfarth": ("Seyfarth Shaw",),
    "Littler": ("Littler Mendelson",),
    "Mintz": ("Mintz Levin",),
    "Finnegan": ("Finnegan Henderson",),
    "Guerra LLP": ("Watts Guerra",),
    "ArentFox Schiff": ("Arent Fox", "Schiff Hardin"),
    "Haynes Boone": ("Haynes and Boone",),
    "BakerHostetler": ("Baker Hostetler", "Baker & Hostetler"),
    "Bryan Cave Leighton Paisner": ("BCLP",),
    "Davis Wright Tremaine": ("DWT",),
    "Fish & Richardson": ("Fish and Richardson",),
    "Gjerset & Lorenz": ("Gjerset and Lorenz",),
    "Sanford Heisler Sharp McKnight": ("SHSM",),
    "Cozen O'Connor": ("Cozen O\u2019Connor",),
}


def _token_counts(names) -> dict:
    """How many firms carry each name word."""
    counts = defaultdict(int)
    # Each firm once: the registry lists Cleary Gottlieb twice, and counting
    # both made "Cleary" look shared, so a bare "at Cleary" named nobody.
    for name in set(names):
        for token in set(t.lower() for t in _tokens(name)):
            counts[token] += 1
    return counts


def _weak_key(name: str, counts: dict) -> str:
    """The one word a firm goes by alone -- Sidley, Skadden, Norton -- or ""."""
    # Only its first word. Its later words are ordinary ones -- "Practice
    # Group" made Groom Law Group of a Kirkland fair, a Fulbright scholarship
    # made Norton Rose Fulbright of a graduate-studies talk, "Austin-based"
    # made Sidley Austin of a Texas firm -- and nobody calls a firm by them.
    for token in _tokens(name)[:1]:
        low = token.lower()
        if counts[low] != 1 or low in TOKEN_STOPLIST:
            return ""
        if len(token) <= SHORT_TOKEN_MAX and low in SHORT_STOPLIST:
            return ""
        return token
    return ""


def build_aliases(registry: list) -> dict:
    """{canonical name: (strong_pattern, weak_pattern)}.

    Strong forms are multi-word and unambiguous on sight: the full registry
    name with flexible punctuation, so "Gibson, Dunn & Crutcher" also matches
    "Gibson Dunn". Weak forms are single distinctive tokens, which only count
    when legal context sits nearby.
    """
    counts = _token_counts(name for _, name in registry)

    def compile_any(alts):
        if not alts:
            return None
        body = "|".join(sorted(set(alts), key=len, reverse=True))
        try:
            return re.compile(r"(?<![\w-])(?:" + body + r")(?![\w-])", re.I)
        except re.error:
            return None

    out = {}
    for name in dict.fromkeys(name for _, name in registry):
        parts = [re.escape(p) for p in re.split(r"[^A-Za-z']+", name) if p]
        if not parts:
            continue
        # A hyphen too: "Curtis, Mallet-Prevost" is written with one.
        sep = r"[\s,&'\-]*"
        strong = [sep.join(parts)]
        if len(parts) >= 2 and len(parts[0]) == len(parts[1]) == 1:
            # Initials are written with their ampersand: "K&L", "A&O". With
            # the loose separator "K&L" alone was also "KL" and "K L".
            strong.append(parts[0] + r"\s*&\s*" + parts[1])
        elif len(parts) >= 2:
            strong.append(sep.join(parts[:2]))
        key = _weak_key(name, counts)
        weak = [re.escape(key)] if key else []
        if len(parts) == 1:
            # Single-word firms (Milbank, Venable) have no strong form at all.
            strong, weak = [], strong + weak
        strong = strong + [re.escape(n) for n in NICKNAMES.get(name, ())]
        pair = (compile_any(strong), compile_any(weak))
        if pair[0] or pair[1]:
            out[name] = pair
    return out


# A firm the registry lacks can share its first word with one it has. GEMA's
# symposium flyer listed "Wren Halloway, Davis Wright Tremaine LLP", and the
# bare "Davis" made it Davis Polk. A name that runs on, capitalised word by
# word, into an LLP is that firm's name -- unless the words are the
# registry firm's own ("Latham & Watkins LLP").
ANOTHER_FIRM = re.compile(
    r"^((?:[\s,&]+[A-Z][\w'\u2019-]*){1,3})[\s,]+(?:LLP|L\.L\.P\.|LLC|PLLC|P\.C\.)(?!\w)")


def _names_another_firm(text: str, match, name: str, aliases: dict) -> bool:
    run = ANOTHER_FIRM.match(text[match.end():match.end() + 80])
    if not run:
        return False
    own = {w.lower() for w in re.split(r"[^A-Za-z]+", name) if w}
    # A one-word firm's registry name is short for its legal name, which runs
    # on: "Orrick, Herrington & Sutcliffe LLP", "Freshfields Bruckhaus
    # Deringer LLP". Only a first word shared with another firm is in doubt.
    if len(own) == 1:
        return False
    words = re.findall(r"[A-Za-z][\w'\u2019-]*", run.group(1))
    # A list runs firms together -- "FRESHFIELDS LATHAM&WATKINS LLP", "Skadden,
    # Milbank LLP" -- and there the words after the name are the next firm we
    # know, not the rest of this one's.
    for other, (strong, weak) in aliases.items():
        if other == name:
            continue
        if strong and strong.search(run.group(0)):
            return False
        if weak and words and weak.fullmatch(words[0]):
            return False
    return any(w.lower() not in own for w in words)


def find_firms(text: str, aliases: dict = None, established=()) -> list:
    """Canonical firm names mentioned in the text.

    A multi-word match stands on its own. A bare-surname match must have legal
    context within `CONTEXT_WINDOW` characters, so an email about a firm
    reception resolves and a sentence about someone named Morgan does not.

    `established` names firms the surrounding text has already settled: a
    title is read on its own, and one that names Skadden bare carries no
    legal word, though the email it heads may say where Skadden's office is.
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
            if _names_another_firm(text, m, name, aliases):
                continue
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


def keyed_words(name: str) -> list:
    """The words of a firm's name that a bare mention could ever have been
    read from: not a stoplisted word ("Will", "Case", "Rose"), and not one
    another firm shares ("Kramer")."""
    shared = defaultdict(int)
    for other in ALIASES:
        for token in set(t.lower() for t in _tokens(other)):
            shared[token] += 1
    return [t for t in _tokens(name)
            if t.lower() not in TOKEN_STOPLIST and shared[t.lower()] <= 1
            and not (len(t) <= SHORT_TOKEN_MAX and t.lower() in SHORT_STOPLIST)]

def list_words(registry: list) -> frozenset:
    """Words a firm's name can end on in a list, for the list check in
    `_looks_like_a_person`: its last word, the second of its two-word form
    ("Davis Polk"), and the word it goes by alone ("Skadden, Milbank").

    Not every word of every name. "Bryan" begins Bryan Cave Leighton Paisner
    and is a given name everywhere else; counted as a firm word it made
    "Bryan Sullivan, a partner" a list, and Sullivan & Cromwell a firm there.
    """
    names = list(dict.fromkeys(name for _, name in registry))
    counts = _token_counts(names)
    out = set()
    for name in names:
        for form in (name,) + NICKNAMES.get(name, ()):
            words = [w for w in re.split(r"[^A-Za-z]+", form.lower()) if w]
            out.update(words[-1:] + words[1:2])
        out.add(_weak_key(name, counts).lower())
    return frozenset(w for w in out if len(w) > 2)


# Built once, after the registry exists.
FIRM_WORDS = list_words(REGISTRY)
