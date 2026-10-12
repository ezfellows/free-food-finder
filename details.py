"""Practical requirements buried in an event's text.

Whether there is food tells you if it is worth going. This tells you whether
you can walk in as you are. A firm reception that collects resumes and expects
business formal is a different proposition from a pizza lunch, and the
difference is always stated somewhere in the description or on the flyer -- in
a line nobody reads until they are already downstairs in jeans.

Everything here is extraction, never inference: if the text does not say it,
the field stays empty. A wrong dress code is worse than none.
"""

from __future__ import annotations

import re
from datetime import datetime

from dateparse import find_event_datetime
from score import _clean, _rx

# Ordered strictest first: a flyer saying "business formal (no jeans)" should
# report the formal requirement, not the casual aside.
DRESS_CODES = [
    (_rx(r"black\s+tie(?:\s+optional)?"), "Black tie"),
    (_rx(r"business\s+formal", r"business\s+professional",
         r"professional\s+attire", r"formal\s+attire",
         r"suit\s+(?:and\s+tie|required)"), "Business formal"),
    (_rx(r"cocktail\s+attire", r"semi-?formal"), "Cocktail attire"),
    (_rx(r"business\s+casual", r"smart\s+casual"), "Business casual"),
    (_rx(r"dress\s+to\s+impress"), "Dress to impress"),
    (_rx(r"casual\s+attire", r"come\s+as\s+you\s+are",
         r"no\s+dress\s+code"), "Casual"),
]

# Things to have in your bag. Resume collection is the one that matters most
# at a recruiting event and the one most easily missed.
BRING = [
    (_rx(r"r[eé]sum[eé]\s+(?:collection|drop|review|submission)",
         r"bring\s+(?:a\s+|your\s+|copies\s+of\s+your\s+)?r[eé]sum[eé]s?",
         r"r[eé]sum[eé]s?\s+(?:will\s+be\s+)?collected",
         r"submit\s+your\s+r[eé]sum[eé]"), "Bring a resume"),
    (_rx(r"business\s+cards?"), "Bring business cards"),
    (_rx(r"bring\s+(?:your\s+)?(?:a\s+)?laptop", r"laptop\s+required"),
     "Bring a laptop"),
    (_rx(r"student\s+id", r"\bGOCard\b", r"hoya\s+card"), "Bring your student ID"),
    (_rx(r"bring\s+(?:a\s+)?(?:friend|guest)", r"guests?\s+welcome"),
     "Guests welcome"),
]

ACCESS = [
    (_rx(r"space\s+is\s+limited", r"limited\s+(?:seating|spots|capacity)",
         r"first\s+come", r"while\s+supplies\s+last", r"capacity\s+is\s+capped"),
     "Space is limited"),
    (_rx(r"registration\s+(?:is\s+)?required", r"rsvp\s+required",
         r"must\s+register", r"pre-?registration"), "Registration required"),
    (_rx(r"ticket(?:s|ed)\s+(?:required|only)", r"admission\s+by\s+ticket"),
     "Ticketed"),
    (_rx(r"by\s+invitation", r"invite\s+only", r"members?\s+only"),
     "Invitation only"),
    (_rx(r"\b1Ls?\s+only\b", r"first-?years?\s+only"), "1Ls only"),
    (_rx(r"\b2Ls?\s+only\b"), "2Ls only"),
    (_rx(r"\b3Ls?\s+only\b"), "3Ls only"),
    (_rx(r"hybrid", r"in\s+person\s+(?:and|or)\s+(?:on\s+)?zoom",
         r"zoom\s+(?:option|link)"), "Hybrid"),
    (_rx(r"interpretation|asl|accessib\w+\s+(?:seating|entrance)"),
     "Accessibility noted"),
]

# "RSVP by Friday, September 12" -- the clause, so the date parser only sees
# the deadline and not the event's own date sitting two lines above.
DEADLINE_RX = re.compile(
    r"(?:rsvp|register|registration|sign\s*-?\s*up|apply|submit)\s*"
    r"(?:by|before|no\s+later\s+than|due)\s*:?\s*"
    r"([^.;!\n]{3,60})", re.I)


def deadline(text: str, ref=None):
    """The RSVP cut-off, as an ISO date, or "" when none is stated."""
    match = DEADLINE_RX.search(text or "")
    if not match:
        return ""
    when, _, _, confidence = find_event_datetime(match.group(1), ref)
    if when is None or confidence < 60:
        return ""
    return when.date().isoformat()


def _first(text: str, table):
    for pattern, label in table:
        if pattern.search(text):
            return label
    return ""


def _all(text: str, table) -> list:
    return [label for pattern, label in table if pattern.search(text)]


def extract(title: str = "", description: str = "", ref=None) -> dict:
    """Practical requirements stated in the text. Absent means not stated."""
    text = _clean(f"{title or ''} {description or ''}")
    dress = _first(text, DRESS_CODES)
    bring = _all(text, BRING)
    access = _all(text, ACCESS)
    by = deadline(text, ref)

    # One flat list, in the order you would want to read it walking out of
    # the door: what to wear, what to carry, what might stop you at the desk.
    notes = []
    if dress:
        notes.append(dress)
    notes.extend(bring)
    if by:
        try:
            notes.append("RSVP by " + datetime.fromisoformat(by).strftime("%-d %b"))
        except ValueError:
            pass
    notes.extend(access)

    return {"dress_code": dress, "bring": bring, "access": access,
            "rsvp_by": by, "notes": notes[:6]}


FIELDS = ("dress_code", "bring", "access", "rsvp_by", "notes")
