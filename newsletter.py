"""Pull the several events out of one round-up email.

A student org does not send an email per event. It sends a weekly bulletin
with five of them stacked up, and a subject -- "Plaintiffs' Law Weekly" --
that names none:

    Halloway Quint: Suing Over No-Poach Agreements 9/23/26
    5:30 - 6:30 pm Hotung 3015. A Halloway Quint associate walks...

    What Trial Boutiques Are Hiring For 9/24/26 4:30 pm Online Only.

Read as one message that is one event with a newsletter for a title. Read the
way it is written -- each date is an event, and the words in front of it are
its name -- it is four.

So dates are the anchors. Everything from the end of the last event up to a
date is that event's title; everything after it, to the next date, is its
detail.

The hard part is not finding dates, it is **refusing** most of them. The same
bulletin carries a job board:

    * Quillfeather, Vane, Larkspur & Brisk - Closes Sep 30, 2026
    * Ashgrove Labor Fellowship - closes Sep 30, 2026
    * Brindle & Moss - closes Oct 5, 2026, 2L 2027 Summer Associate

Anchoring on those would put nine law firms on the docket as though you could
turn up to them. A date is only an event when nothing nearby says it is a
closing date, and when the text around it looks like something happening in a
place rather than a row in a list.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

import titles
from dateparse import _find_dates, find_event_datetime
from score import _clean

# Only dates a human wrote out. `_find_dates` also reports bare weekdays (40)
# and "tomorrow" (60), which are far too common in prose to start an event on:
# "Happy Monday" would open one in every newsletter that has ever been sent.
MIN_ANCHOR_CONFIDENCE = 70

# Two dates almost touching are one date written as a range: "September 18-19",
# "9/29 - 9/30". Kept deliberately small. A wider window merges events that are
# merely adjacent, and in a densely written bulletin two events can be fifty
# characters apart -- what separates them is that the second one has a NAME in
# front of it, which is tested in `segments` rather than guessed at here.
SAME_DATE_WINDOW = 24

# A deadline is not an event. Checked in the words just before the date, which
# is where the qualifier always sits: "closes Sep 30", "due October 2",
# "applications open Aug 1", "RSVP by Friday".
DEADLINE_BEFORE = re.compile(
    r"\b(?:closes?|closing|close\s+date|due|deadline|expires?|"
    r"apply\s+by|applications?\s+(?:due|close|open)|submit\s+by|"
    r"rsvp\s+by|register\s+by|sign\s*-?\s*up\s+by|respond\s+by|"
    r"no\s+later\s+than|opens?|opened|posted|rolling|"
    r"start\s+date|end\s+date|by)\s*:?\s*$", re.I)

# The same, when the qualifier trails the date: "September 30 deadline".
DEADLINE_AFTER = re.compile(r"^\s*(?:deadline|cut-?off|due)\b", re.I)

# Markdown and table wreckage from mail-to-text conversion. Removed before
# anything is read, or every title starts with a pipe.
# Bullets and table cells become line breaks, not spaces. They are the only
# thing separating one item from the next in a "Save the Dates" list, and
# flattening them runs the previous event's room into the next event's name,
# so a firm reception's title came out starting with a room number.
NOISE = [
    (re.compile(r"\[\]\(https?://[^)]*\)"), " "),    # empty tracked links
    (re.compile(r"\[([^\]]*)\]\(https?://[^)]*\)"), r"\1"),
    # Not the dash inside a time range. "12:00 pm - 1:30 pm" is how CFLO
    # writes every lunch panel, and reading its dash as a bullet broke the
    # range in two: the end time was lost and the parser's default hour put
    # the Paul, Weiss panel as ending at 1:00. `(?=\S)` stops the whitespace
    # run backtracking to let a second space slip past the lookahead.
    (re.compile(r"(?:^|\s)[*\u2022\u2013\u2014-]\s+(?=\S)"
                r"(?!\d{1,2}(?::\d{2})?\s*[ap]\.?m\b|\d{1,2}:\d{2})", re.M | re.I),
     "\n"),                                          # bullets
    (re.compile(r"\|"), "\n"),                       # table cell walls
    (re.compile(r"#{1,6}\s*"), "\n"),                # markdown headings
    (re.compile(r"\*+"), ""),                         # leftover emphasis
    (re.compile(r"[ \t]{2,}"), " "),
    (re.compile(r"\n[ \t]+"), "\n"),
    (re.compile(r"\n{3,}"), "\n\n"),
]

# Where a title may start: the end of the previous sentence, a line break, or
# a bullet. Without this the title runs back into the last event's blurb.
# A sentence end, not any full stop. An unqualified `.` also matched inside
# "zz1538@georgetown.edu", so the window for the next title began at "edu" and
# forty OSL events were titled "edu ADR Bootcamp", "com BLSA October GBM".
# A real sentence break is followed by space or end of text; a domain dot
# never is.
BOUNDARY = re.compile(r"[.!?](?=\s|$)|[\n•]|(?:^|\s)[-*–—]\s")

# A blank line: how a bulletin separates one entry from the next.
PARAGRAPH = re.compile(r"\n\s*\n")

# Leading wreckage on a title once it has been cut out of running text.
LEAD_TRIM = " \t*#|-–—:;.,!?)]>•"

# The section label a bulletin puts above its list. Mail-to-text conversion
# loses the line break that separated it, so it arrives welded to the front of
# the first event: "Upcoming Events Halloway Quint: Suing Over No-Poach...".
# Left on, the title reads as a newsletter heading and the event -- the first
# one in every section, which is usually the soonest -- is thrown away.
SECTION_WORDS = (
    r"upcoming\s+events?|events?\s+this\s+week|this\s+week|"
    r"announcements?|reminders?|professional\s+opportunities|"
    r"opportunities|save\s+the\s+dates?|job\s+postings?|"
    r"upcoming\s+\w+\s+programming|programming|sponsor\s+of\s+the\s+week|"
    r"alumni\s+spotlight|additional\s+resources|1L\s+representatives|"
    r"other\s+firm\s+events?\s*(?:&|and)\s*programs?")

# The same labels, searched rather than anchored: the last event in a section
# otherwise runs on into whatever follows, and what follows here is the job
# board -- which then makes the event look like a job listing and drops it.
SECTION = re.compile(r"\s(?:%s)\b" % SECTION_WORDS, re.I)

HEADING = re.compile(
    r"^(?:%s|upcoming|news|calendar|"
    r"what'?s\s+(?:on|next|happening))\b[\s:.\u2013\u2014-]*" % SECTION_WORDS
    # Labels that are a whole line and nothing else: OPICS's "NEXT WEEK"
    # over the GPLA coffee chats, "EVENT DETAILS" over GEMA's date line.
    + r"|^(?:next\s+(?:week|meeting)|(?:event\s+)?details|(?:external\s+)?events)"
    r"[\s:.]*$",
    re.I)

# A list row rather than something happening: "Firm Name - closes Sep 30,
# 2026, 2L 2027 Summer Associate". These carry no time and no place.
LISTING = re.compile(
    r"\b(?:summer\s+associate|term-?time\s+clerk|fellowship|"
    r"rolling|1L\s+2026|2L\s+20\d\d|3L\s+20\d\d|"
    r"application|apply\s+here|open\s+until|unknown\s+close)\b", re.I)


def tidy(text: str) -> str:
    """The body with mail-to-text conversion artefacts taken out."""
    out = text or ""
    for pattern, replacement in NOISE:
        out = pattern.sub(replacement, out)
    return out


def _as_date(ref):
    """`_find_dates` works in dates; callers hand us the email's timestamp."""
    if isinstance(ref, datetime):
        return ref.date()
    return ref if isinstance(ref, date) else date.today()


def _anchors(text: str, ref):
    """Positions where an event plausibly starts, strongest date first."""
    found = [(pos, day, conf)
             for pos, day, conf in _find_dates(text, _as_date(ref))
             if conf >= MIN_ANCHOR_CONFIDENCE]
    found.sort(key=lambda a: a[0])
    kept = []
    for anchor in found:
        if kept and anchor[0] - kept[-1][0] < SAME_DATE_WINDOW:
            continue
        kept.append(anchor)
    return kept


def is_deadline(text: str, position: int) -> bool:
    """Whether the date at `position` is a cut-off rather than an occasion."""
    before = text[max(0, position - 40):position]
    if DEADLINE_BEFORE.search(before):
        return True
    return bool(DEADLINE_AFTER.match(text[position:position + 30]))


# "Details:", "When:" or "Date & Time:" right before a date, and the weekday
# that comes with it.
FIELD_LABEL = re.compile(
    r"\b(?:details|when|date|time|date\s*(?:&|and)\s*time)\s*:\s*"
    r"(?:(?:mon|tues|wednes|thurs|fri|satur|sun)day,?\s*)?$", re.I)


def _title_span(text: str, start: int, position: int):
    """(title, offset) -- the words in front of the date and where they begin.

    The offset matters as much as the title. A segment's detail otherwise runs
    all the way to the next date, which swallows the next event's name along
    with it -- so one event's description ends with another event's title, and
    anything true of that title becomes true of this event.
    """
    window = text[start:position]
    # A field label is not a name, and where the name is depends on where the
    # label is. The SBA's Weekly Update ends each entry's blurb with
    # "Details:" and then the day and time, so the words in front of the date
    # were "Details: Thursday" -- published as the title of the Election Night
    # Watch Party, and too short to keep for Bar Review x GLE, which was
    # dropped. There the name heads the entry. A label that opens its own
    # line -- MLS's "When:", OCS's "+ Date & Time:" -- has it on the line above.
    label = FIELD_LABEL.search(window)
    if label:
        line_at = window.rfind("\n", 0, label.start()) + 1
        lead = window[line_at:label.start()].rstrip()
        if not _strip_symbols(lead.strip(LEAD_TRIM + "+")):
            above = _name_above(window, line_at)
            if above:
                return above[0], start + above[1]
            # No heading above: the label stays the title, as it always was.
            # It is refused, but it still ends the entry before -- which a
            # nameless "Date: Friday, October 23" block in the OSL broadcast
            # would otherwise join, moving the Jones Day chat to its 11am.
        elif lead[-1] in ".!?" or unicodedata.category(lead[-1]) == "So":
            window = window[:label.start()]
        # And a label in mid-phrase is no label: "pick the most convenient date:".
    last = None
    for match in BOUNDARY.finditer(window):
        last = match
    offset = start + (last.end() if last is not None else 0)
    title = _tidy_title(window[last.end():] if last is not None else window)
    if title:
        return title, offset
    # Nothing between the last full stop and the date. Plenty of entries are
    # written the other way round -- "Harvest Potluck. Bring a dish and meet
    # the new board. September 19, 4:00 PM" -- with the name first and the
    # date last, so the window before the date is a blank.
    #
    # The name is the first sentence of THIS entry, and a bulletin separates
    # entries by blank line. Measuring from the last paragraph break rather
    # than from the start of the window matters when the window opens in the
    # middle of the previous entry, which it does whenever two events share a
    # date -- and in a weekly bulletin they constantly do.
    para = None
    for match in PARAGRAPH.finditer(window):
        para = match
    entry_at = para.end() if para is not None else 0
    entry = window[entry_at:]
    first = BOUNDARY.search(entry)
    opening = _tidy_title(entry[:first.start()] if first else entry)
    # Unless that first line is the section label over the entry. CFLO puts
    # "Other Firm Events & Programs" on the line above a Davis Polk event's
    # name, in the same paragraph, and the label was published as that
    # event's name. The name is the next line.
    if not opening and first and HEADING.match(
            _strip_symbols(_clean(entry[:first.start()]).strip(LEAD_TRIM))):
        rest = entry[first.end():]
        second = BOUNDARY.search(rest)
        opening = _tidy_title(rest[:second.start()] if second else rest)
        entry_at += first.end()
    # Or unless the entry opens with a sentence under a heading of its own.
    # GIAS sets the event's name on a line, then a blank one, then a sentence
    # urging the reader to come, and only then "📅" and the date -- and the
    # sentence was the title.
    if opening and _prose(opening):
        above = _name_above(window, entry_at)
        if above:
            return above[0], start + above[1]
    return (opening, start + entry_at) if opening else ("", offset)


# A line that is one of an entry's fields rather than its name.
FIELD_LINE = re.compile(
    r"^(?:location|where|when|date|time|details|rsvp|cost|costs|register|"
    r"contact|format|hosted\s+by|target\s+audience|speakers?|food)\b\s*:", re.I)

# A street address: the last entry's location, run on from its "Location:".
# In one CFLO issue the previous entry's street address stood directly above
# the next event's name.
ADDRESS = re.compile(
    r"^\d+\s+(?:[A-Z0-9][\w.]*\s+){0,4}(?:st|street|ave|avenue|rd|road|blvd|"
    r"way|pl|place|dr|drive|ct|court|nw|ne|sw|se)\b|\b(?:D\.?C\.?|[A-Z]{2})\s+\d{5}\b",
    re.I)

# A line that asks the reader to do something, or credits someone: the
# entry's furniture, not its name. "Register for the panel" sat above the
# Human Rights Institute's description; "Moderated by ..." above its date.
ACTION_LINE = re.compile(
    r"^(?:register|rsvp|sign\s*up|click|apply|learn\s+more|read\s+more|"
    r"watch|view|see\s+(?:the\s+)?flyer)\b", re.I)
CREDIT_LINE = re.compile(
    r"^(?:moderated|introduc\w+|hosted|co-?hosted|presented|sponsored|"
    r"organi[sz]ed|featuring|with\s+remarks)\b", re.I)

# Words a name never has and a sentence addressed to the reader does.
# Not "your": "Plot Your Summer at Skadden", "Launching Your 1L Summer".
READER_WORDS = frozenset({"i", "we", "us", "you", "our", "please", "don't"})


def _prose(title: str) -> bool:
    """Whether a would-be title reads as a sentence: it speaks to someone, or
    runs too long for a name."""
    words = re.findall(r"[A-Za-z']+", title.replace("\u2019", "'"))
    return len(words) > 14 or any(w.lower() in READER_WORDS for w in words)


def _line_kind(line: str):
    """(kind, name) for one line above a date: "name", "blurb" (prose that
    may sit between a heading and its date), "skip" (blank, a link, a call to
    register) or "stop" (a field or another date: the entry before)."""
    name = _tidy_title(line)
    bare = _strip_symbols(_clean(URL_IN_TITLE.sub(" ", line)).strip(LEAD_TRIM + "+"))
    if not name or ACTION_LINE.match(bare):
        return "skip", ""
    if FIELD_LINE.match(bare) or ADDRESS.search(bare) or any(
            conf >= MIN_ANCHOR_CONFIDENCE
            for _p, _d, conf in _find_dates(line, date.today())):
        return "stop", ""
    # A line ending in a full stop or a comma is a sentence, or the wrapped
    # end of one; so is a line that starts in lower case. A line with no
    # capital at all is a note: "flyer attached".
    tail = _clean(URL_IN_TITLE.sub(" ", line)).rstrip(" (")
    if (tail[-1:] in ".,;" or not re.match(r"[A-Z0-9\"\u201c'(]", name)
            or CREDIT_LINE.match(bare) or _prose(name)
            or not titles.assess(name, None, "mail")["ok"]):
        return "blurb", ""
    return "name", name


def _name_above(window: str, at: int):
    """(name, offset) of the heading on the lines above `at`, or None.

    Blank lines and one blurb may come between -- a heading, a blank line, a
    paragraph about the event, then its date -- but not a field line or
    another date: the lines past those belong to the entry before.
    """
    end, blurbs = at, 0
    while end > 0:
        begin = window.rfind("\n", 0, end - 1) + 1
        line = window[begin:end]
        end = begin
        kind, name = _line_kind(line)
        if kind == "stop":
            return None
        if kind == "skip":
            continue
        if kind == "name":
            # "GIAS Panel Discussion:" over "Treaty Claims and Climate
            # Policy" is one name on two lines.
            before = window[:begin].rstrip()
            prev_at = before.rfind("\n") + 1
            prev = before[prev_at:]
            if prev.rstrip().endswith(":"):
                head_kind, head = _line_kind(prev.rstrip().rstrip(":"))
                if head_kind == "name":
                    return "%s: %s" % (head, name), prev_at
            return name, begin
        blurbs += 1
        if blurbs > 1:
            return None
    return None


# A link is never part of an event's name. An "RSVP here" with its sign-up
# link in brackets became the title of a firm reception, because the link
# sat between the event's name and its date.
URL_IN_TITLE = re.compile(r"\(?\s*https?://\S+\s*\)?|\bwww\.\S+", re.I)
LINK_WORDS = re.compile(r"\b(?:rsvp|register|sign\s*up|click)\s+here\b\s*:?",
                        re.I)


# "9/28: Deal Lawyering Basics with King & Spalding" -- a newsletter's own date
# label, left on the front of the name. The date is already on the event; kept
# in the title it also changed the fingerprint, so this could never merge with
# a clean copy of the same event and was published beside it.
LEADING_DATE = re.compile(r"^\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?\s*[:\-–—.]\s*")


def _strip_symbols(title: str) -> str:
    """The title without the emoji a bulletin sets around each entry
    ("\U0001F57A Bar Review x GLE", "MLS Oktoberfest \U0001F37B"). A currency
    sign stays: "$5 Lunch"."""
    def mark(ch):
        return unicodedata.category(ch) in ("So", "Sk") or ch in "\ufe0f\u200d "
    at, end = 0, len(title)
    while at < end and mark(title[at]):
        at += 1
    while end > at and mark(title[end - 1]):
        end -= 1
    return title[at:end]


# "Event: <the name, in capitals>. Date: ..." -- the OSL broadcast labels the
# name as well.
LEADING_LABEL = re.compile(r"^(?:event(?:\s+name)?|title)\s*:\s*", re.I)


def _tidy_title(window: str) -> str:
    window = LINK_WORDS.sub(" ", URL_IN_TITLE.sub(" ", window or ""))
    window = LEADING_DATE.sub("", window)
    title = _strip_symbols(_clean(window).strip(LEAD_TRIM)).strip(LEAD_TRIM)
    title = LEADING_LABEL.sub("", title)
    # A title that is nothing but the section label is a section label. It is
    # returned empty so the caller rejects it, rather than publishing
    # "Upcoming SIPLA Programming" as though it were an event.
    return HEADING.sub("", title).strip(LEAD_TRIM)


def _until_next_section(text: str) -> str:
    """An event's detail stops where the next section of the bulletin starts."""
    match = SECTION.search(text)
    return text[:match.start()] if match else text


# The nouns an event is called by. Used only to rescue a title from prose:
# "...repaired a broken link on the sign-up page for our joint GBM with the
# Lantern Guild on Wednesday September 23" is a sentence, and the event
# inside it starts at "GBM". Anything shorter than this is left alone -- a title that already
# reads as a name must not be trimmed.
EVENT_NOUN = re.compile(
    r"\b(?:GBM|general\s+body\s+meeting|meeting|panel|reception|mixer|"
    r"chat|talk|lecture|social|lunch|dinner|breakfast|brunch|happy\s+hour|"
    r"bootcamp|workshop|symposium|conference|fair|clinic|info\s+session|"
    r"open\s+house|book\s+club|meetup|week|series|screening|tour|"
    # The plain word, which is how an org actually writes it: a sentence
    # saying it will hold an event with a named firm on a given day. Generic
    # on its own, but `titles.repair` still has to accept whatever comes out,
    # so it earns its place by rescuing real events rather than inventing names.
    r"event|session)\b", re.I)

# A failed title with fewer words than this has nothing worth digging out.
# This was 12, which is longer than most prose titles actually are: the
# Ropes & Gray sentence came to eleven words and was dropped untried. Nothing
# is published on the strength of this number alone -- every candidate still
# goes through `titles.repair` below.
PROSE_WORDS = 4

# Verbs and pronouns that open a sentence but never open a title. Kept local
# rather than added to `titles.DANGLING`, which the title checker uses to
# decide a title is a fragment -- teaching it that "having" is a throwaway
# word would change every assessment, not just this rescue.
LEAD_FILLER = frozenset({
    "having", "hosting", "holding", "we", "us", "they", "there", "this",
    "that", "it", "its", "will", "also", "additionally", "next",
    "upcoming",
})


# "The Space Law Society", "Georgetown Environmental Law Society" -- the run
# of capitalised words a sentence opens with, which names the host.
PROPER_RUN = re.compile(
    r"^(?:The\s+)?((?:[A-Z][\w&'.-]*)(?:\s+(?:of|for|and|&|[A-Z][\w&'.-]*)){0,4})")


def _opening_proper_noun(title: str) -> str:
    match = PROPER_RUN.match((title or "").strip())
    if not match:
        return ""
    name = _clean(match.group(1))
    # One capitalised word is as likely to be the first word of a sentence as
    # a name; two or more is a body with a name.
    return name if len(name.split()) >= 2 else ""


def _rescue_from_prose(title: str, ref=None) -> str:
    """A shorter title taken from the tail of a sentence, or "" if none works.

    Only tried when the extracted title is long enough to be prose rather than
    a name. The event noun nearest the date is where the name begins; anything
    before it is the sentence that happened to carry it.
    """
    words = title.split()
    if len(words) <= PROSE_WORDS:
        return ""
    last = None
    for match in EVENT_NOUN.finditer(title):
        last = match
    if last is None:
        return ""
    # Include the word or two in front of the noun: "joint GBM", "first
    # General Body Meeting" read better than the bare noun.
    head = title[:last.start()].split()
    lead = " ".join(head[-2:]) if head else ""
    candidate = _clean(f"{lead} {title[last.start():]}").strip(LEAD_TRIM)
    # The tail of a sentence ends on whatever word preceded the date -- "GBM
    # with the Lantern Guild on". Those trailing joins are dropped here rather than in
    # `titles`, where a dangling word is a signal that the whole thing is a
    # fragment and trimming it would hide the problem.
    words = candidate.split()
    while words and words[-1].lower() in titles.DANGLING:
        words.pop()
    while words and words[0].lower() in (titles.DANGLING | LEAD_FILLER):
        words.pop(0)
    mend = titles.repair(" ".join(words), ref, "mail")
    if not mend["ok"]:
        # "General Body Meeting" says nothing on its own, and the checker is
        # right to refuse it -- but the host is usually the subject of the
        # very sentence it came from: a society announcing that it will hold
        # its general body meeting, with dinner. Putting the two together
        # rescues the event instead of dropping it.
        host = _opening_proper_noun(title)
        if host and not " ".join(words).lower().startswith(host.lower()):
            mend = titles.repair(f"{host} {' '.join(words)}", ref, "mail")
        if not mend["ok"]:
            return ""
    # A title cut from mid-sentence starts on whatever word happened to be
    # there, often a lower-case "event". Capitalised only when the first
    # word is plainly lower case, so an acronym like "cLE Panel" or a name
    # like "iPhone Law Clinic" keeps the spelling its organiser chose.
    fixed = mend["fixed"]
    first = fixed.split(" ", 1)[0] if fixed else ""
    if first and first.islower():
        fixed = fixed[0].upper() + fixed[1:]
    return fixed


# The CLS weekly newsletter of 13 Oct 2026 sets each heading in the same
# table cell as its first sentence, and the plain text runs the two together:
# a heading, then "This Week", then the sentence that gives the date. Read
# whole it is a sentence the title checks rightly refuse --
# "this week" is how a newsletter names itself -- and the entry was dropped.
# But a "this week" or a "tonight" is where such a heading ends: the words in
# front of it are the event's name, and the date already says when.
WHEN_WORDS = re.compile(
    r"\s(?:this\s+week(?:end)?|next\s+week|tonight|today|tomorrow)\b", re.I)

# Words a heading has no use for and a sentence can hardly do without.
SENTENCE_WORDS = frozenset({"i", "we", "you", "us", "our", "your", "their",
                            "is", "are", "was", "will", "be", "been", "please"})


def _heading_ended_by_when(body, begins, position, following, ref=None):
    """(name, detail) for an entry whose heading ends at its "this week".

    None unless the words in front of it pass as a name. Tried only once the
    other ways of finding a title have failed, so no entry they already read
    changes. The detail is cut again too: "this week" is also a section label,
    and the cut that stops an entry at the next section stopped this one
    inside its own heading -- two words long, the date gone with the rest.
    """
    when = WHEN_WORDS.search(body, begins, position)
    if not when:
        return None
    name = _tidy_title(body[begins:when.start()])
    # A heading starts with a capital and speaks to no one; a sentence does
    # neither. A reminder to send something in by EOD tomorrow is a
    # deadline, and the words in front of its "tomorrow" are no event's name.
    said = {w.lower() for w in re.findall(r"[A-Za-z']+", name)}
    if not name[:1].isupper() or said & SENTENCE_WORDS:
        return None
    mend = titles.repair(name, ref, "mail")
    if not mend["ok"]:
        return None
    detail = _clean(body[begins:when.end()]
                    + _until_next_section(body[when.end():following]))[:1200]
    if LISTING.search(f"{mend['fixed']} {detail[:160]}"):
        return None
    return mend["fixed"], detail


def _title_before(text: str, start: int, position: int) -> str:
    return _title_span(text, start, position)[0]


# "9/21 - Chess Club - GBM". The contents list a bulletin opens with, which
# names the host for every event in it. Entries themselves often do not: one
# org's gave only the words "General Body Meeting" with a date, a time and a
# room, so the title is rejected as generic and the event is dropped -- while
# the index two screens above says exactly whose meeting it is.
INDEX_LINE = re.compile(
    r"^\s*(\d{1,2})/(\d{1,2})(?:\s*-\s*\d{1,2}/\d{1,2})?\s*[-–]\s*"
    r"([^-\n]{2,60}?)\s*[-–]\s*([^\n]{2,80})\s*$", re.M)


def index_hosts(text: str, ref=None) -> dict:
    """{(month, day): [(host, name)]} from the bulletin's contents list."""
    out = {}
    for match in INDEX_LINE.finditer(text or ""):
        month, day = int(match.group(1)), int(match.group(2))
        host = _clean(match.group(3))
        name = _clean(match.group(4))
        if host and name:
            out.setdefault((month, day), []).append((host, name))
    return out


def _name_from_index(hosts: dict, day, title: str) -> str:
    """"Chess Club General Body Meeting" for an entry that only said "GBM".

    The index is only consulted for a title the checker has already refused,
    and only on the entry's own date, so it adds a host rather than inventing
    an event.
    """
    if not hosts or day is None:
        return ""
    entries = hosts.get((day.month, day.day)) or []
    if not entries:
        return ""
    words = {w for w in re.findall(r"[a-z]{3,}", (title or "").lower())}
    initials = "".join(w[0] for w in (title or "").split() if w[:1].isalpha())
    for host, name in entries:
        plain = re.findall(r"[a-z]{3,}", name.lower())
        # The index names the same event as the entry when they share a word,
        # or when the index's short form is the entry's initials ("GBM" for
        # "General Body Meeting").
        if (words & set(plain)
                or name.replace(" ", "").lower() == initials.lower()):
            return _clean(f"{host} {title}")[:90]
    # One event on that date and nothing to disagree with.
    if len(entries) == 1:
        return _clean(f"{entries[0][0]} {title}")[:90]
    return ""


def segments(text: str, ref=None, refused=None) -> list:
    """Every event the body describes, in the order it describes them.

    Each is `{"title", "detail", "position"}`. Nothing is scored here and no
    date is resolved: that stays with `dateparse` and `score`, so one bulletin
    and one single-event email go through exactly the same judgement.

    An entry whose title is refused for being a deadline, and for nothing
    else -- "Please fill out this form by this Friday" -- is not an event, and
    goes to `refused` instead when the caller passes a list.
    """
    body = tidy(text)
    anchors = _anchors(body, ref)
    hosts = index_hosts(text, ref)

    # Titles first, with the offset each one starts at. The window a title is
    # cut from runs from the PREVIOUS date to this one -- "...end of the last
    # blurb. This event's name" -- so it is measured from `position`, not from
    # where the segment ends. Measuring it forward leaves every window after
    # the first one empty.
    spans, previous_end = [], 0
    for position, _day, _conf in anchors:
        title, offset = _title_span(body, previous_end, position)
        # Nothing in front of this date but the last event's blurb, so it is
        # the same event restated -- a second date in a range, or a reminder
        # of the one just described. Fold it into what came before.
        if not title and spans:
            continue
        spans.append((position, title, offset))
        previous_end = position

    out = []
    for index, (position, title, _offset) in enumerate(spans):
        # The entry begins where its title does, which is not always before
        # the date: "Harvest Potluck. Bring a dish and meet the new board.
        # September 19, 4:00 PM". Starting the detail at the date threw away
        # the sentence that said there was food at all.
        begins = min(_offset, position) if _offset else position
        # Stop at the next event's NAME, not the next event's date.
        following = (spans[index + 1][2] if index + 1 < len(spans)
                     else len(body))
        detail = _clean(_until_next_section(body[begins:following]))[:1200]
        if is_deadline(body, position):
            continue

        # Only the head of the detail belongs to this event: the last event in
        # a section runs on into whatever follows it, and in this newsletter
        # what follows is a job board.
        if LISTING.search(f"{title} {detail[:160]}"):
            continue
        mend = titles.repair(title, ref, "mail")
        if not mend["ok"] and titles.deadline(title):
            passed = titles.repair(title, ref, "mail", deadlines=False)
            if passed["ok"]:
                if refused is not None:
                    refused.append({"title": passed["fixed"], "detail": detail,
                                    "position": position})
                continue
        rescued = not mend["ok"]
        fixed = mend["fixed"] if mend["ok"] else _rescue_from_prose(title, ref)
        if not fixed:
            heading = _heading_ended_by_when(body, begins, position, following, ref)
            if heading:
                fixed, detail = heading
        if not fixed:
            # Last resort: the contents list at the top names the host.
            day = next((d for pos, d, _c in anchors if pos == position), None)
            named = _name_from_index(hosts, day, title)
            if named:
                repaired = titles.repair(named, ref, "mail")
                fixed = repaired["fixed"] if repaired["ok"] else ""
        if not fixed:
            # A sentence cut at its date ("Donations will be accepted
            # through") is still an entry of this email, refused like a
            # deadline: dropped silently, a two-entry email read as one
            # event, with one entry's day and the other's hours.
            if (refused is not None and mend["issues"] and all(
                    i.startswith("ends mid-phrase") for i in mend["issues"])):
                refused.append({"title": title, "detail": detail,
                                "position": position, "cut": True})
            continue
        out.append({"title": fixed, "detail": detail, "position": position,
                    "rescued": rescued})

    # A sentence about an event the bulletin lists properly is not a second
    # one. MLS's update opened with a sentence correcting a firm happy hour
    # on 18 November, followed by an earlier issue's date stamp -- rescued
    # from that sentence, the happy hour was listed again, on the 26th of
    # October, beside its real entry.
    listed = {p["title"].lower() for p in out if not p["rescued"]}
    out = [p for p in out if not (p["rescued"] and p["title"].lower() in listed)]
    for p in out:
        del p["rescued"]

    out.extend(_entries_dated_only_by_the_index(
        body, anchors, hosts, ref, {part["position"] for part in out}))
    out.sort(key=lambda part: part["position"])
    return out


# A clock with no calendar: a start and end time and a room, but no date.
BARE_TIME = re.compile(
    r"\b\d{1,2}(?::\d{2})?\s*(?:[apAP]\.?[mM]\.?)"
    r"|\b\d{1,2}:\d{2}\b")


def _entries_dated_only_by_the_index(body, anchors, hosts, ref, taken) -> list:
    """Entries that state an hour but never a date.

    A bulletin lists the date once, in the contents at the top, and the entry
    below gives only a time, a room and the free cookies. With no date in its
    own text there is no anchor, so `segments` never sees it and the event is
    lost -- one student org's general body meeting, cookies and all, was
    invisible for exactly this reason.

    Matched on the host's name, which the index gives and the entry repeats,
    so a paragraph is only dated by an index line that is plainly about it.
    """
    if not hosts:
        return []
    dated = {position for position, _day, _conf in anchors}
    found = []
    for match in re.finditer(r"[^\n]+", body):
        start, end = match.start(), match.end()
        if any(start <= position < end for position in dated):
            continue                       # it carries its own date
        if any(start <= position < end for position in taken):
            continue
        para = match.group(0).strip()
        if len(para) < 30 or not BARE_TIME.search(para):
            continue
        hit = _index_match(hosts, para)
        if not hit:
            continue
        day, host, name = hit
        title = _tidy_title(para.split(".")[0])
        mend = titles.repair(title, ref, "mail")
        if not mend["ok"]:
            mend = titles.repair(_clean(f"{host} {name}"), ref, "mail")
        if not mend["ok"]:
            continue
        found.append({"title": mend["fixed"],
                      "detail": _clean(para)[:1200],
                      "position": start,
                      "day": day})
    return found


def _index_match(hosts: dict, para: str):
    """(day, host, name) for the index line this paragraph belongs to."""
    words = set(re.findall(r"[a-z]{4,}", para.lower()))
    if not words:
        return None
    best = None
    for day, entries in hosts.items():
        for host, name in entries:
            host_words = set(re.findall(r"[a-z]{4,}", host.lower()))
            if not host_words:
                continue
            shared = host_words & words
            # Most of the host's name has to appear, so "BLSA - GBM" does not
            # claim every paragraph that happens to say "meeting".
            if len(shared) >= max(2, len(host_words) - 1):
                score = len(shared)
                if best is None or score > best[0]:
                    best = (score, day, host, name)
    if best is None:
        return None
    _score, day, host, name = best
    from datetime import date
    return date(_YEAR_FOR(day), day[0], day[1]), host, name


def _YEAR_FOR(day, ref=None):
    """The bulletin's own year. Weekly mail never spans a year boundary far."""
    from datetime import datetime
    return (ref or datetime.now()).year


def split(text: str, ref=None, refused=None) -> list:
    """Segments that also carry a resolved date. Ready to be scored.

    `refused`, when given, receives the deadlines `segments` turned away that
    carry a date too: each would have been an entry here before deadlines were
    refused as titles.
    """
    events, turned_away = [], []
    for part in segments(text, ref, turned_away) + [
            dict(part, refused=True) for part in turned_away]:
        # The date lives at the head of the detail; the title is searched too
        # so "Bar Review ... 9/17 Thurs." still resolves when the date sits in
        # the title's own tail.
        if part.get("day") is not None:
            # The date came from the contents list; only the clock is in the
            # entry itself. Parsed together so the time still has to be read
            # out of the text rather than assumed.
            start, end, all_day, confidence = find_event_datetime(
                "%s %s" % (part["day"].strftime("%B %-d %Y"), part["detail"]),
                ref)
        else:
            start, end, all_day, confidence = find_event_datetime(
                f"{part['title']} {part['detail']}", ref)
        if start is None or confidence < 60:
            continue
        if part.get("refused"):
            if refused is not None:
                refused.append(dict(part, starts_at=start))
            continue
        events.append(dict(part, starts_at=start, ends_at=end,
                           all_day=all_day, date_confidence=confidence))
    return events
