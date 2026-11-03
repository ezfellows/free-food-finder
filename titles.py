"""Judge whether an event title is actually a title.

OCR does not fail loudly. It returns something, and the something goes on the
page: "FIRST TREET" for First Street, "Mentorstip Meetup", "Wednesday,
September 16'" where the headline should be, or a banner phrase repeated four
times because the flyer set it as a border.

None of that is detectable by confidence score -- Vision was 97% sure about
every one of them. It is detectable by reading the title and asking whether it
reads like the name of an event. That is what this does, and it explains
itself: each problem comes back with a reason, so the review pass knows what
to look for instead of re-reading the whole flyer blind.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache

import school
from dateparse import find_event_datetime

DICT_PATHS = ("/usr/share/dict/words", "/usr/dict/words")

# Names: student orgs, law firms, places. The school's own -- its buildings,
# its orgs, its neighbourhoods -- are its profile's PROPER_NOUNS, and
# `proper()` is the two together. Two properties matter. They are
# never misspellings, and -- because a firm is "Sheppard Mullin" and a building
# is "Edward Bennett Williams" -- the word beside one is usually part of the
# same name and must not be spell-checked either. That neighbour rule is why
# this is a separate set from the vocabulary below: "Mentorstip" sits next to
# "Meetup", and exempting it would throw away a real catch.
PROPER = {
    "apalsa", "blsa", "menalsa", "salsa", "saldf", "ilsa", "irap", "sba",
    "lalsa", "aldf", "philadelphia", "manhattan", "sheppard", "mullin",
    "weil", "gotshal", "manges", "skadden", "arps", "slate", "meagher",
    "flom", "latham", "watkins", "kirkland", "ellis", "covington",
    "burling", "simpson", "thacher", "bartlett", "mcdermott", "emery",
    "schulte", "roth", "zabel", "clifford", "chance", "groom", "sterne",
    "kessler", "goldstein", "wilmerhale", "arnold", "porter", "steptoe",
    "venable", "crowell", "moring", "hogan", "lovells", "jenner", "block",
    "sidley", "austin", "paul", "weiss", "rifkind", "wharton", "garrison",
    "cravath", "swaine", "moore", "sullivan", "cromwell", "davis", "polk",
    "wardwell", "debevoise", "plimpton", "milbank", "cleary", "gottlieb",
    "gibson", "dunn", "crutcher", "vinson", "elkins", "orrick", "mofo",
    "morrison", "foerster", "pryor", "cashman", "kramer", "levin",
    "naftalis", "frankel", "willkie", "farr", "gallagher", "herrington",
    "sutcliffe", "shearman", "fried", "harris", "shriver", "jacobson",
    "akin", "gump", "strauss", "hauer", "feld", "wilson", "sonsini",
    "goodrich", "rosati", "ropes", "gray", "duane", "morris", "reed",
    "smith", "katten", "muchin", "rosenman", "seyfarth", "shaw", "quinn",
    "emanuel", "urquhart", "latino", "latina", "latinx",
}

# Vocabulary the 1934 word list simply does not have. These are ordinary words
# -- they confer nothing on their neighbours.
VOCABULARY = {
    "adr", "gbm", "oci", "llm", "rsvp", "hsr", "eco", "cbm",
    "biglaw", "pre-law", "clinic", "clinics", "externship", "pro", "bono",
    "networking", "mentorship", "fireside", "wellness", "webinar", "mixer",
    "meetup", "bootcamp", "podcast", "livestream", "healthcare", "fintech",
    "startup", "startups", "coffee", "chats",
}


def proper() -> frozenset:
    """PROPER, and the school's own names."""
    return school.active().derive("titles.proper", lambda p: frozenset(
        PROPER | {w.lower() for w in p.PROPER_NOUNS}))


def known() -> frozenset:
    """Every word the misspelling check must leave alone."""
    return school.active().derive("titles.known",
                                  lambda p: proper() | VOCABULARY)

# A title made only of these says nothing: which body? whose first look?
# The labels a newsletter prints beside an event's details. "Date and Time:
# Thursday, September 24th, 7 PM" put "Date and Time" on the page as the name
# of APALSA's mentorship social.
FORM_LABEL = re.compile(
    r"^\s*(?:date|time|date\s+(?:and|&)\s+time|when|where|location|venue|"
    r"place|rsvp|details?|format|contact|cost|price|dress\s+code)\s*:?\s*$",
    re.I)

GENERIC = {
    "meeting", "general", "body", "gbm", "first", "look", "info", "session",
    "kickoff", "kick-off", "social", "mixer", "welcome", "open", "house",
    "lunch", "event", "club", "lab", "panel", "talk", "chat", "week",
}

# A name does not end on a word that is waiting for the next one. Captions
# are often whole sentences -- "Join us for board games this Friday..." -- and
# cutting one at its date leaves a fragment that reads like a title until you
# notice it does not say what the event is.
DANGLING = {
    "this", "that", "these", "those", "the", "a", "an", "and", "or", "but",
    "with", "at", "for", "in", "on", "of", "to", "from", "by", "our", "us",
    "your", "their", "is", "are", "was", "will", "be", "been", "we", "you",
    "join", "come", "featuring", "presents", "presented", "hosted", "about",
}

# A sentence cut at its date: "Coats will be collected through
# [November 3rd]", "Final GBM next [Wednesday]" -- each the shape of
# something once listed as an event.
# Only in lower case, as running text has them: an event's name capitalises
# its last word ("What's Next", "Halloween Drive Thru", "Courtroom Walk
# Through"), and those are names.
CUT_SHORT = {"through", "thru", "next", "until", "till", "via", "including"}

# A newsletter subject is not an event name. A weekly round-up carries five
# events and a headline that names none of them, so taking the subject as the
# title publishes "Trivia Night and More Ways to Get Involved!" as though
# it were something you could attend.
BULLETIN = re.compile(
    r"\b(?:weekly|newsletter|bulletin|digest|round-?up|recap)\b|"
    r"\band\s+more\b|&\s*more\b|\bplus\s+(?:other|more)\b|"
    r"\bupcoming\b[^,]{0,32}\bevents?\b|\bthis\s+week\b|\bweek\s+\d+\b|"
    r"^\s*(?:rsvp|reminder|action\s+required|announcing|introducing|"
    r"save\s+the\s+dates?)\b|"
    r"\bsave\s+the\s+dates?\b|\bevents?\s+(?:this|next)\s+week\b",
    re.I)

# A sentence from the body of an email is not a name either. A newsletter
# split at the wrong line takes the line before an entry as its title: a
# panel in one APALSA letter was listed under the aside before it, which
# announced a Halloween party ("In other news, we have Trivia-ween next
# Friday", say). What gives it away is how it talks -- an aside to open
# with, "we have", "there will be".
CHATTER = re.compile(
    r"^\s*(?:in\s+other\s+news|also|and\s+finally|don['\u2019]?t\s+forget|"
    r"as\s+always|(?:as\s+a|just\s+a|a\s+quick|quick)\s+reminder|"
    r"hi|hello|hey|dear|thanks|thank\s+you|please|make\s+sure|be\s+sure)\b|"
    r"\bwe\s+(?:have|are|will|would|hope|want|can)\b|\bwe['\u2019](?:re|ll|ve)\b|"
    r"\bthere\s+(?:will\s+be|is|are)\b|\bthere['\u2019]s\b",
    re.I)

# A deadline is something to do by a time, not somewhere to be at one. "Don't
# forget to upload your writing sample by EOD tomorrow" names a thing and a
# day and passed for a title; what gives it away is a "by" or a "due" with a
# time after it.
_WHEN = (r"(?:EOD|EOB|COB|close\s+of\s+business|end\s+of\s+(?:the\s+)?(?:day|week)|"
         r"noon|midnight|tonight|today|tomorrow|"
         r"(?:this|next)\s+(?:week(?:end)?|month|"
         r"(?:mon|tues|wednes|thurs|fri|satur|sun)day)|"
         r"(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?\b|"
         r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d|"
         r"\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?|\d{1,2}/\d{1,2})")
DEADLINE = re.compile(r"\b(by|due)\s+(?:(?:by|on)\s+)?" + _WHEN, re.I)
# ...but not every "by" is one. A visit is no deadline -- "Stop by today for
# free coffee" -- nor is the host of the event, and an RSVP date on a real
# event's name -- "Pizza with Latham, RSVP by Friday" -- is left to the checks
# that already deal with it.
NOT_A_DEADLINE = re.compile(
    r"\b(?:stop|drop|come|swing|pop|stopping|dropping|coming|swinging|"
    r"hosted|sponsored|presented|organi[sz]ed|led|moderated|"
    r"rsvp|register|registration|sign\s*-?\s*up|respond)\s+$", re.I)


def deadline(title: str) -> str:
    """The words that make this a deadline -- "by EOD", "due 10/3" -- or ""."""
    for match in DEADLINE.finditer(title or ""):
        if (match.group(1).lower() == "by"
                and NOT_A_DEADLINE.search(title[:match.start()])):
            continue
        return match.group(0)
    return ""


# A title framed as a question ends on a preposition quite legitimately:
# "What Appellate Judges Are Hoping For", "Which Clinic Should I Sign Up
# For?". The dangling-word check is for fragments, not for these.
INTERROGATIVE = {"what", "how", "why", "where", "when", "who", "whom",
                 "which", "whose", "is", "are", "should", "can", "do", "does"}

DATE_LIKE = re.compile(
    r"^\s*(?:mon|tues?|wednes|thurs?|fri|satur|sun)day\b|"
    r"^\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d",
    re.I)

# When a flyer's headline runs into its date line, the date arrives glued to
# the end of the title. Each pattern strips one trailing fragment; they are
# applied repeatedly, because "... THU. 10/8 | 4:30 - 5:30" is three of them.
TRAILING_WHEN = [
    # Thursday, September 17, 2026
    re.compile(r"[\s|,\-–—]*\b(?:mon|tues?|wednes|thurs?|fri|satur|sun)day,?\s+"
               r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*"
               r"\d{1,2}(?:st|nd|rd|th)?(?:,?\s*20\d{2})?\s*$", re.I),
    # 5:15 - 6:15 PM  /  12:10-1:10PM
    re.compile(r"[\s|,\-–—]*\b\d{1,2}:\d{2}\s*(?:[ap]\.?m\.?)?\s*[-–—]\s*"
               r"\d{1,2}:\d{2}\s*(?:[ap]\.?m\.?)?\s*$", re.I),
    # 5:15 PM
    re.compile(r"[\s|,\-–—]*\b\d{1,2}:\d{2}\s*[ap]\.?m\.?\s*$", re.I),
    # WED. 9/16   /   9/16/26
    re.compile(r"[\s|,\-–—]*\b(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s*"
               r"\d{1,2}/\d{1,2}(?:/\d{2,4})?\s*$", re.I),
    re.compile(r"[\s|,\-–—]+\d{1,2}/\d{1,2}(?:/\d{2,4})?\s*$"),
    # A bare month left dangling: "Patent Week: September 29 - September"
    re.compile(r"[\s|,:\-–—]*\b(?:january|february|march|april|may|june|july|"
               r"august|september|october|november|december)\s*$", re.I),
    # SEPT 24 | 3:30 PM  -- a bare month and day at the very end
    re.compile(r"[\s|,\-–—]*\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
               r"[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s*20\d{2})?\s*$", re.I),
]

# A day counted from the email, which is wrong by the time anyone reads the
# page: "Meet Boise-Based Law Firm Hale & Partners Tomorrow!". Only when it
# is plainly an announcement's word -- shouted, exclaimed, set off by a mark,
# in running text's lower case, or after the kind of event it is ("Panel and
# Reception Today") -- since "Lawyers of Tomorrow" and "Antitrust Today" are
# names.
TRAILING_DAY = re.compile(
    r"(?P<mark>\s*[|,:\-–—]+)?\s*\b(?P<word>tomorrow|today|tonight)\s*(?P<bang>!+)?[.!]*\s*$",
    re.I)
EVENT_NOUN = {
    "reception", "panel", "panels", "townhall", "hall", "lecture", "talk",
    "talks", "meeting", "gbm", "lunch", "luncheon", "dinner", "breakfast",
    "brunch", "hour", "session", "sessions", "fair", "workshop", "party",
    "social", "mixer", "networking", "event", "seminar", "symposium",
    "conference", "forum", "discussion", "celebration", "gathering", "house",
    "tour", "screening", "night", "coffee", "chat", "roundtable", "training",
    "clinic", "q&a", "a", "showcase", "info", "drop-in", "tabling",
}
# What a name ends on before its "Tomorrow": "Lawyers of Tomorrow".
NAME_BEFORE_DAY = {"of", "for", "in", "to", "into", "about", "beyond", "the",
                   "and", "&"}


SENTENCE_VERB = re.compile(r"\b(?:will|is|are|was|were|be|been|can|should|must)\b", re.I)
# The word a heading is cut at when its date follows: "Final GBM next
# [Wednesday]". Not "on" or "from": peeled, "Workshop a week from [today]" and
# a sentence that ended "...on" read as names.
CUT_AT_DATE = re.compile(r"\s+(?:%s)\s*[.,!]*\s*$" % "|".join(sorted(CUT_SHORT)))


def _strip_cut(text: str) -> str:
    """'Final GBM next [Wednesday]': the word the date was cut from, in
    running text's lower case, comes off; the entry is kept by its name."""
    m = CUT_AT_DATE.search(text)
    if not m or len(WORD.findall(text[:m.start()])) < 2:
        return text
    # "Clerkships: what's next" asks, and keeps its last word.
    words = WORD.findall(text)
    clause = WORD.findall(re.split(r"[:|–—]|\s-\s", text)[-1]) or words
    if text.rstrip().endswith("?") or any(
            p[0].lower().split("'")[0] in INTERROGATIVE for p in (words, clause) if p):
        return text
    # "Coats will be collected through": a sentence, refused as cut short.
    if SENTENCE_VERB.search(text[:m.start()]):
        return text
    return text[:m.start()]


def _strip_day(text: str) -> str:
    """The title without a trailing day word, or the title as it was."""
    m = TRAILING_DAY.search(text)
    if not m:
        return text
    left = text[:m.start()].rstrip(" ,-–—|:")
    words = WORD.findall(left)
    if len(words) < 2 or words[-1].lower() in NAME_BEFORE_DAY:
        return text
    # "Open House Today!" -- without its day it names nothing, and the
    # title check would refuse what was a listing.
    if all(w.lower() in GENERIC for w in words):
        return text
    word = m.group("word")
    # Lower case is running text ("Our GBM is tomorrow"); a name capitalises.
    marked = (m.group("mark") or m.group("bang") or word.isupper() or word.islower()
              or words[-1].lower() in EVENT_NOUN or left.rstrip().endswith("Q&A"))
    return left if marked else text

# The same at the front, set off by a mark: "TOMORROW: Kestrel Health Law
# Q&A". The mark is required -- "Today's Supreme Court" is a name.
LEADING_WHEN = re.compile(r"^\s*(?:tomorrow|today|tonight)\s*[:!|\-–—]+\s*", re.I)


def strip_when(text: str):
    """Peel date and time fragments off the end. Returns (text, stripped?)."""
    out, stripped = text, False
    lead = LEADING_WHEN.sub("", out)
    # "TOMORROW: Info Session" -- without its day it names nothing.
    if lead and lead != out and not all(w.lower() in GENERIC for w in WORD.findall(lead)):
        out, stripped = lead, True
    for _ in range(4):
        for strip in TRAILING_WHEN + [_strip_day, _strip_cut]:
            trimmed = (strip.sub("", out) if hasattr(strip, "sub")
                       else strip(out)).strip(" ,-–—|")
            # Never strip the whole thing: a title that is only a date is a
            # different problem, reported separately.
            if trimmed and trimmed != out:
                out, stripped = trimmed, True
                break
        else:
            break
    return out, stripped

LEADING_JUNK = re.compile(r"^(?:[a-z]\s+){1,3}(?=[A-Z])")
WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")
ALPHABET = "abcdefghijklmnopqrstuvwxyz"


# Ordinary words the system list does not carry -- irregular plurals it never
# had, and vocabulary younger than it. Every one of these turns up in real
# event titles, and each was being reported as possible OCR damage: the Women
# of Color Collective's picnic was flagged on the word "Women".
EXTRA_WORDS = frozenset({
    "women", "men", "children", "people", "alumni", "alumnae",
    "resumes", "resume", "networking", "snacks", "bootcamp", "externship",
    "externships", "internship", "internships", "clerkship", "clerkships",
    "mentorship", "fellowship", "llm", "jd", "gbm", "rsvp", "webinar",
    "wellness", "podcast", "livestream", "meetup", "mixer",
    # Abbreviations and compounds a flyer uses freely and a 1934 word list
    # never saw. "Intro" flagged the CCAC white-collar crime panel.
    "intro", "info", "demystified", "biglaw", "asado", "mocktail",
    "prepping", "onboarding", "upperclassmen", "roundtable", "bootcamps",
    "fireside", "networking", "reception", "symposium",
})


@lru_cache(maxsize=1)
def dictionary() -> frozenset:
    """The system word list, lowercased, plus the words it is missing."""
    for path in DICT_PATHS:
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    return frozenset(
                        w.strip().lower() for w in fh if w.strip()) | EXTRA_WORDS
            except OSError:
                continue
    return frozenset()


def _edits1(word: str):
    """Every string one edit away -- the classic four operations."""
    splits = [(word[:i], word[i:]) for i in range(len(word) + 1)]
    for left, right in splits:
        if right:
            yield left + right[1:]                                  # delete
            for letter in ALPHABET:
                yield left + letter + right[1:]                     # substitute
        if len(right) > 1:
            yield left + right[1] + right[0] + right[2:]            # transpose
        for letter in ALPHABET:
            yield left + letter + right                             # insert


def _stems(word: str):
    """The word and its plausible uninflected forms.

    The system word list is base forms only -- no plurals, no participles --
    so "Futures", "Talks" and "Perspectives" are all absent from it and all
    one edit from a real word. Checking the stem first is what stops every
    plural on the page being reported as OCR damage.
    """
    yield word
    for suffix, replacements in (
            ("'s", [""]), ("s'", [""]),
            ("ies", ["y"]), ("es", ["", "e"]), ("s", [""]),
            ("ing", ["", "e"]), ("ed", ["", "e"]),
            ("ly", [""]), ("ers", ["er", ""]), ("er", ["", "e"]),
    ):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            stem = word[:-len(suffix)]
            for replacement in replacements:
                yield stem + replacement


def looks_misspelled(word: str) -> bool:
    """True when a word is not English but is one edit from being English.

    That combination is the signature of OCR damage rather than a proper
    noun: "Mentorstip" is one substitution from "mentorship", whereas
    "Buzbee" is not near anything. Proper nouns are mostly safe by accident
    and entirely safe by the `known()` list.
    """
    lowered = word.lower().strip("'-")
    if len(lowered) < 5 or lowered in known():
        return False
    words = dictionary()
    if not words:
        return False
    if any(stem in words for stem in _stems(lowered)):
        return False
    # Hyphenated and possessive compounds: judge the parts, not the whole.
    parts = [p for p in re.split(r"[-']", lowered) if len(p) >= 3]
    if len(parts) > 1 and all(
            any(stem in words for stem in _stems(p)) for p in parts):
        return False
    return any(candidate in words for candidate in _edits1(lowered))


def _damaged_words(text: str) -> list:
    """Words that look like OCR damage, skipping the parts of proper names.

    A name runs over several words and only some of them are ever in a word
    list: "Sheppard" is recognisable, "Mullin" is not, and judging "Mullin" on
    its own reports a false error on a real firm. So a word sitting next to a
    name we do recognise is treated as part of that name.
    """
    words = WORD.findall(text)
    lowered = [w.lower().strip("'-") for w in words]
    names = proper()
    out = []
    for i, word in enumerate(words):
        neighbours = lowered[max(0, i - 1):i] + lowered[i + 1:i + 2]
        if any(n in names for n in neighbours):
            continue
        if looks_misspelled(word):
            out.append(word)
    return out


def repeated_phrase(title: str):
    """A phrase repeated at the start, and what follows the last repeat.

    Returns `(phrase, rest)`. The `rest` is the part that matters and the part
    it is easy to lose: a flyer that stacks its name four times as a border
    then states the event underneath reads "NAME NAME NAME NAME GENERAL BODY
    MEETING", and collapsing that to just "NAME" throws away the only words
    that say what is happening.
    """
    words = title.split()
    if len(words) < 6:
        return None
    for size in range(2, len(words) // 2 + 1):
        head = [w.lower() for w in words[:size]]
        if [w.lower() for w in words[size:size * 2]] != head:
            continue
        count = 1
        while [w.lower() for w in words[count * size:(count + 1) * size]] == head:
            count += 1
        return " ".join(words[:size]), " ".join(words[count * size:])
    return None


# Sources whose titles were read off an image rather than typed by a person.
OCR_SOURCES = ("broadcast", "flyer")


def from_ocr(source: str) -> bool:
    """Whether this title was read off an image.

    Worth gating on. A calendar title is what a human typed into the feed:
    odd, abbreviated or repetitive, it is still what the organiser meant, and
    second-guessing its spelling only produces noise. A flyer title is a
    guess Vision made about some pixels, and that is what these checks are for.
    """
    return any(tag in (source or "").lower() for tag in OCR_SOURCES)


def assess(title: str, ref=None, source: str = "", deadlines: bool = True) -> dict:
    """What is wrong with this title, and why.

    Returns `issues` (short reasons), `ok`, and `clean` -- the title with the
    damage we can safely remove taken off. Nothing is invented: if the real
    title is unrecoverable, `clean` is just the tidied original and the issues
    say so.

    `source` decides which checks run. Everything reads for sense; only a
    title that came out of OCR is also read for transcription damage.

    `deadlines=False` judges a title as this did before deadlines were
    refused, for the caller that needs to know a title failed on that alone.
    """
    raw = (title or "").strip()
    issues, clean = [], raw
    ocr = from_ocr(source) or not source

    if not raw:
        return {"ok": False, "issues": ["no title"], "clean": ""}
    if FORM_LABEL.match(raw):
        return {"ok": False, "issues": ["a form label, not a name (%r)" % raw],
                "clean": raw}

    repetition = repeated_phrase(raw) if ocr else None
    if repetition:
        phrase, rest = repetition
        issues.append(f"banner text repeated ({phrase!r})")
        clean = f"{phrase} {rest}".strip()

    # Order matters. Peeling a trailing date off a title that is *entirely* a
    # date leaves "Wednesday", which is worse than what we started with and
    # hides the real problem. So ask first whether the whole thing is a date.
    is_date = False
    if DATE_LIKE.match(clean):
        start, _, _, confidence = find_event_datetime(clean, ref)
        is_date = start is not None and confidence >= 60
    due = deadline(clean) if deadlines and not is_date else ""
    if is_date:
        issues.append("the title is a date, not a name")
    elif due:
        # Not mended by taking the date off, so the date stays on: "Cover
        # letters due 10/3" without it still announces nothing to attend, and
        # `repair` judging the shortened title would let it through.
        issues.append(f"a deadline, not an event ({due.strip().lower()!r})")
    else:
        clean, stripped = strip_when(clean)
        if stripped:
            issues.append("date appended to the title")

    junk = LEADING_JUNK.match(clean) if ocr else None
    if junk and len(clean) - junk.end() > 8:
        issues.append(f"stray characters at the start ({clean[:junk.end()].strip()!r})")
        clean = clean[junk.end():]

    misspelled = _damaged_words(clean) if ocr else []
    if misspelled:
        issues.append("possible OCR damage: " + ", ".join(sorted(set(misspelled))[:3]))

    words = [w for w in WORD.findall(clean)]
    if len(words) < 2:
        issues.append("too short to identify the event")
    elif all(w.lower() in GENERIC for w in words):
        issues.append("says nothing specific (generic words only)")
    elif all(w.lower().strip("'-") in proper() for w in words):
        # The flyer's biggest text is often the sponsor's wordmark. That names
        # who is hosting, which is not the same as what is happening.
        issues.append("names an organisation, not an event")

    # "What's Next" asks; it does not trail off -- nor does a question after
    # the event's name ("Affirmative Action After SFFA: What's next?").
    # Only a short last clause is the question: a long one is a sentence
    # quoting a title ('...called "The Quiet Docket: What Small Claims
    # Courts Teach Us About Access to Justice" on').
    clause = WORD.findall(re.split(r"[:|–—]|\s-\s", clean)[-1]) or words
    asks = clean.rstrip().endswith("?") or any(
        part[0].lower().split("'")[0] in INTERROGATIVE
        for part in (words, clause if len(clause) <= 6 else []) if part)
    # The last word whole: "Health Law Q&A" ends on "Q&A", not on "a".
    tail = re.findall(r"[A-Za-z][A-Za-z'&\-]*", clean)
    last = tail[-1] if tail else ""
    if last and not asks and (last.lower() in DANGLING or last in CUT_SHORT):
        issues.append(f"ends mid-phrase (on {last!r})")

    bulletin = BULLETIN.search(clean)
    if bulletin:
        issues.append("reads like a newsletter subject, not an event "
                      f"({bulletin.group(0).strip().lower()!r})")
    # "Don't forget to upload your writing sample by Friday" is a sentence
    # because it is a reminder; judged as before deadlines were refused, it is
    # judged as before this was looked for too, or a refused deadline goes
    # uncounted.
    chatter = CHATTER.search(clean) if deadlines else None
    if chatter:
        issues.append("reads like a sentence from the email, not a name "
                      f"({chatter.group(0).strip().lower()!r})")

    trailing_number = re.search(r"\s(\d{1,3})$", clean)
    if trailing_number and len(words) <= 3:
        issues.append(f"stray number at the end ({trailing_number.group(1)})")

    return {"ok": not issues, "issues": issues, "clean": clean.strip()}


def suspicious(events, ref=None) -> list:
    """[(event, assessment)] for every event whose title does not read right."""
    out = []
    for event in events:
        verdict = assess(event.get("title") or "", ref,
                         event.get("source") or "")
        if not verdict["ok"]:
            out.append((event, verdict))
    return out


def repair(title: str, ref=None, source: str = "", deadlines: bool = True) -> dict:
    """Fix what can be fixed mechanically; report what cannot.

    The two passes are the point. A title that is only a banner repeated four
    times is fully recoverable -- strip the repetition and what is left is the
    real title, so there is nothing for a human to look at. A title with a
    letter dropped out of a word is not: no amount of rewriting recovers
    "TREET", and pretending otherwise would publish a confident mistake.

    So: clean, then judge the cleaned version. `fixed` is what to use,
    `issues` is what survived the cleaning, and only a non-empty `issues`
    warrants the review queue.
    """
    first = assess(title, ref, source, deadlines)
    fixed = first["clean"] or (title or "").strip()
    second = assess(fixed, ref, source, deadlines)
    return {"fixed": fixed,
            "issues": second["issues"],
            "ok": not second["issues"],
            "repaired": fixed != (title or "").strip()}
