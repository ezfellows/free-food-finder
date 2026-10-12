"""The line of text the organiser wrote underneath their own flyer.

Every image in the OSL broadcast carries one, in the `alt` attribute and again
in a caption cell below it. They follow a house grammar:

    SALDF's First Book Club... September 16th, 3-4 pm, McDonough 220, zzz90@...
    Are Geofence Warrants Constitutional? ... Wednesday, September 16
        5:45pm-7:00pm in Hotung 2001.

This matters more than it looks. The flyer is a picture, so every fact taken
off it is a guess Vision made about some pixels -- and display type is where
that guess is weakest, which is exactly where the times live. The caption is
typed by the person running the event. It is not a second opinion, it is the
first-hand one.

So the caption grounds the structured fields -- when, where, what it is called
-- and the flyer keeps the job it is actually good at: the wording that says
there is food, and the artwork a reader can check for themselves. Where the
two disagree, nothing is silently picked: `compare()` reports it and the event
goes to the review queue, because a start time that is wrong by half an hour
is worse than one that is missing.
"""

from __future__ import annotations

import re

import campus
import titles
from dateparse import find_event_datetime
from score import _clean

# "contact x@y.edu", "with questions and accommodation requests", bare emails.
# Stripped before anything else: an address ending in digits reads as a room
# number, and "September 11th, 2026 through..." reads as a second event.
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
CONTACT = re.compile(
    r"[,.;|]?\s*(?:and\s+)?(?:please\s+)?contact\b[^,.;|]*|"
    r"[,.;|]?\s*with\s+questions[^,.;|]*|"
    r"[,.;|]?\s*(?:for\s+)?(?:questions\s+(?:and|or)\s+)?"
    r"accommodation\s+requests?[^.]*|"
    r"[,.;|]?\s*for\s+more\s+info(?:rmation)?\b[^,.;|]*|"
    r"[,.;|]?\s*rsvp\b[^,.;|]*", re.I)

# The sales pitch that follows the contact line. Left in, it becomes the
# venue: one caption resolves to "McDermott Will & Schulte , so please RSVP as
# soon as possible to secure your spot!". Everything from here to the end goes.
PITCH = re.compile(
    r"(?:\.|,)?\s*(?:space\s+is\s+limited|capacity|seats?\s+are\s+limited|"
    r"so\s+please\b|don'?t\s+miss|we\s+hope\s+to\s+see|join\s+us\s+for\s+an?\b|"
    r"all\s+are\s+welcome|open\s+to\s+all\b|this\s+event\s+is\b).*$", re.I | re.S)

# Where the descriptive half ends and the logistics begin.
DATE_START = re.compile(
    r"\b(?:on\s+)?(?:mon|tues?|wednes|thurs?|fri|satur|sun)day\b|"
    r"\b(?:mon|tue|wed|thu|fri|sat|sun)\.\s|"
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d|"
    r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|"
    r"\bdate\s*:", re.I)

# Mailchimp captions sometimes use labelled fields instead of commas.
LABEL = re.compile(r"\b(?:date|time|location|place|where|when)\s*:\s*", re.I)

TRIM = " ,.;:|-–— "


# Everything that is a "when", removed so that what is left is the "where".
# Without this the year becomes the room: "September 14, 2026. McDonough 344"
# resolves to McDonough 2026, because 2026 is a four-digit number and Hotung's
# rooms really are numbered 2000 and 6005, so the venue table cannot rule it
# out on shape alone. Here it can be ruled out on position.
WHEN_BITS = [
    # The year is only ever removed as part of the date it belongs to. A bare
    # four-digit rule would read "in Hotung 2001" as a year and leave the
    # event in an unnumbered Hotung -- those really are the room numbers.
    re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?\s*(?:[-–—]|\bto\b)\s*"
               r"\d{1,2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?", re.I),
    re.compile(r"\b\d{1,2}:\d{2}\s*(?:[ap]\.?m\.?)?", re.I),
    re.compile(r"\b\d{1,2}\s*[ap]\.?m\.?", re.I),
    re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*"
               r"\d{1,2}(?:st|nd|rd|th)?(?:\s*,?\s*20\d{2})?", re.I),
    re.compile(r"\b(?:mon|tues?|wednes|thurs?|fri|satur|sun)day\b", re.I),
    re.compile(r"\b(?:mon|tue|wed|thu|fri|sat|sun)\.", re.I),
    re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"),
    re.compile(r"\b(?:from|at|on|in|starting|beginning|through|until)\b", re.I),
]


# What is left of "contact zz1977@georgetown.edu" when the clause is removed
# before the address is: the clause pattern stops at the first full stop, so
# it eats "contact zz1977@georgetown" and leaves ".edu" sitting where a room
# number should be. Addresses come out first, and this sweeps up any orphan.
ORPHAN_TLD = re.compile(r"[,.;|]?\s*\.?\b(?:edu|com|org|gov)\b\.?", re.I)


def strip_contact(text: str) -> str:
    """The caption without its contact tail."""
    out = EMAIL.sub(" ", text or "")
    out = CONTACT.sub(" ", out)
    out = PITCH.sub(" ", out)
    out = ORPHAN_TLD.sub(" ", out)
    return _clean(out).strip(TRIM)


def split(text: str):
    """(description, logistics) -- the halves either side of the first date."""
    body = strip_contact(text)
    match = DATE_START.search(body)
    if not match:
        return body, ""
    head = body[:match.start()].strip(TRIM)
    head = LABEL.sub(" ", head).strip(TRIM)
    return _clean(head), body[match.start():].strip(TRIM)


def when(text: str, ref=None):
    """(start, end, all_day, confidence) read from the caption's logistics."""
    _, logistics = split(text)
    if not logistics:
        return None, None, False, 0
    return find_event_datetime(LABEL.sub(" ", logistics), ref)


def place_text(text: str) -> str:
    """The caption's logistics with every date and time taken out."""
    _, logistics = split(text)
    remainder = LABEL.sub(" ", logistics or "")
    for pattern in WHEN_BITS:
        remainder = pattern.sub(" ", remainder)
    return _clean(remainder).strip(TRIM)


def where(text: str) -> dict:
    """The place, resolved through the same venue table the feeds use."""
    remainder = place_text(text)
    if not remainder:
        return {"place": "", "venue": "", "room": "", "campus": "unknown",
                "on_campus": True}
    return campus.locate(remainder)


def title(text: str, ref=None) -> str:
    """The caption's own name for the event, or "" when it has none.

    The description half is usually the title, but not always: a caption that
    reads like a sentence ("Come dance with us this Friday...") leaves behind
    a fragment, not a name. `titles.assess` already knows the difference, so
    the judgement is made there rather than duplicated here.
    """
    head, _ = split(text)
    if not head:
        return ""
    head = head.strip(TRIM)
    verdict = titles.assess(head, ref, "caption")
    return head if verdict["ok"] else ""


def read(text: str, ref=None) -> dict:
    """Everything the caption states. Absent fields come back empty."""
    start, end, all_day, confidence = when(text, ref)
    place = where(text)
    return {
        "title": title(text, ref),
        "starts_at": start.isoformat(timespec="seconds") if start else "",
        "ends_at": end.isoformat(timespec="seconds") if end else "",
        "all_day": all_day,
        "date_confidence": confidence,
        "location": place.get("place", ""),
        "room": place.get("room", ""),
        "campus": place.get("campus", "unknown"),
        "text": strip_contact(text),
    }


def _hhmm(value: str) -> str:
    return (value or "")[11:16]


def compare(caption_read: dict, ocr_start, ocr_location: str) -> list:
    """What the caption and the flyer disagree about.

    Only differences a reader would act on. A room recorded two ways is not a
    conflict; a start time half an hour apart is the difference between
    arriving on time and arriving to an empty room.
    """
    issues = []
    cap_start = caption_read.get("starts_at") or ""
    if cap_start and ocr_start:
        other = ocr_start if isinstance(ocr_start, str) else ocr_start.isoformat()
        if cap_start[:10] != other[:10]:
            issues.append(f"the email says {cap_start[:10]}, "
                          f"the flyer says {other[:10]}")
        elif _hhmm(cap_start) != _hhmm(other):
            issues.append(f"the email says it starts at {_hhmm(cap_start)}, "
                          f"the flyer says {_hhmm(other)}")

    cap_room = (caption_read.get("room") or "").lower()
    ocr_room = (campus.locate(ocr_location or "").get("room") or "").lower()
    if cap_room and ocr_room and cap_room != ocr_room:
        issues.append(f"the email says room {cap_room}, "
                      f"the flyer says room {ocr_room}")
    return issues


def _time_of(value: str) -> str:
    return (value or "")[11:16]


def _has_clock(said: dict) -> bool:
    """Whether the caption actually stated a time, not just a date."""
    return bool(said.get("starts_at")) and not said.get("all_day") \
        and _time_of(said["starts_at"]) != "00:00"


def ground(current: dict, said: dict, min_confidence: int = 60) -> tuple:
    """(changes, clashes) -- what the caption may correct, and what it cannot.

    The caption usually wins, but not unconditionally, because three of its
    failure modes make an event worse rather than better:

    * It states a date and no time. Taken whole that replaces a real 6pm with
      midnight, so the date is taken and the flyer keeps the clock.
    * It contains a typo. "Hoting Patio" resolves to nowhere, while the
      flyer's "Hotung Patio" resolves to a building we know, so an
      unrecognisable place never displaces a recognisable one.
    * It is less specific than the flyer. "McDonough 141" is true of the
      BrazLA panel but the interviews are in 156, 160 and 164. A caption that
      names a different room does not overwrite; it raises a conflict.
    """
    changes, clashes = {}, []
    now_start = current.get("starts_at") or ""
    now_place = current.get("location") or ""

    if said.get("starts_at") and said.get("date_confidence", 0) >= min_confidence:
        if _has_clock(said):
            if said["starts_at"][:16] != now_start[:16]:
                changes["starts_at"] = said["starts_at"]
                if said.get("ends_at"):
                    changes["ends_at"] = said["ends_at"]
                # Both sources named a time and they are not the same time.
                # The caption is taken, because it was typed rather than
                # photographed -- but a reader turning up on the strength of
                # this deserves to know the flyer on the wall says otherwise.
                if now_start and said["starts_at"][:10] == now_start[:10]:
                    clashes.append(
                        f"the email says it starts at {_time_of(said['starts_at'])}, "
                        f"the flyer says {_time_of(now_start)}")
                elif now_start:
                    clashes.append(f"the email says {said['starts_at'][:10]}, "
                                   f"the flyer says {now_start[:10]}")
        elif now_start and said["starts_at"][:10] != now_start[:10]:
            # A better day, on the flyer's clock.
            changes["starts_at"] = said["starts_at"][:10] + now_start[10:]
        elif not now_start:
            changes["starts_at"] = said["starts_at"]

        # An end time the flyer never printed. Common: the artwork shows one
        # time in display type and the caption spells out the range, so the
        # event shows up on the calendar with no idea how long it runs.
        if (said.get("ends_at") and not current.get("ends_at")
                and "ends_at" not in changes):
            start_now = changes.get("starts_at", now_start)
            if start_now and said["ends_at"][:10] == start_now[:10]:
                changes["ends_at"] = said["ends_at"]

    place = said.get("location") or ""
    if place:
        known = said.get("campus") == "home"
        room = (said.get("room") or "").lower()
        here = campus.locate(now_place)
        here_room = (here.get("room") or "").lower()
        if not now_place:
            changes["location"] = place
        elif not known and here.get("campus") == "home":
            pass                      # a typo must not unseat a real building
        elif room and room in now_place.lower():
            pass                      # the flyer already says it, in more detail
        elif room and here_room and room != here_room:
            clashes.append(f"the email says room {room}, "
                           f"the flyer says room {here_room}")
        elif known:
            changes["location"] = place
    return changes, clashes
