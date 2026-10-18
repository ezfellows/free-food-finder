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
    r"(?:(?:early|now)\s+)?"
    r"(?:by|before|no\s+later\s+than|due)\s*:?\s*"
    r"([^.;!\n]{3,60})", re.I)

# The same cut-off, worded the other ways round. Georgetown's own Flo Recruit
# invitations say "(Registration Deadline: Friday, October 16 at 6pm ET)";
# others "The deadline to register is midnight on Friday, 10/2" and "RSVPs are
# due October 16". Each captures the clause that holds the date.
DEADLINE_WORDINGS = (
    DEADLINE_RX,
    re.compile(r"(?:rsvp|registration|sign\s*-?\s*up)\s+deadline\s*"
               r"(?:is\s*)?:?\s*([^.;!\n)]{3,60})", re.I),
    re.compile(r"deadline\s+to\s+(?:rsvp|register|sign\s*-?\s*up)\s*"
               r"(?:is\s*)?:?\s*([^.;!\n)]{3,60})", re.I),
    re.compile(r"(?:rsvps?|registration|sign\s*-?\s*ups?)\s+(?:closes|ends)\s*"
               r"(?:on\s*)?:?\s*([^.;!\n)]{3,60})", re.I),
    re.compile(r"(?:rsvps?|registrations?)\s+(?:is|are)\s+due\s*"
               r"(?:by\s+|on\s+)?:?\s*([^.;!\n)]{3,60})", re.I),
)


def deadline(text: str, ref=None):
    """The RSVP cut-off, as an ISO date, or "" when none is stated.

    The first one the text states, whichever way it is worded.
    """
    found = []
    for wording in DEADLINE_WORDINGS:
        for match in wording.finditer(text or ""):
            when, _, _, confidence = find_event_datetime(match.group(1), ref)
            if when is not None and confidence >= 60:
                found.append((match.start(), when))
                break
    if not found:
        return ""
    return min(found, key=lambda f: f[0])[1].date().isoformat()


# Asked for in words that tell the reader to act. A passing mention is not an
# ask: "remote participation is available after registration" describes a
# webinar, "learn how to register for classes" is the registrar's business,
# and a "registered student organization" is not an instruction.
RSVP_ASKED = _rx(
    r"rsvp(?:s|'?d|ing)?",
    r"register\s+(?:here|now|today|online|early|in\s+advance|at|by|via|"
    r"through|using|on)",
    r"register\s+for\s+(?:this|the|our)\s+(?:event|program|session|panel|"
    r"reception|webinar|lunch|dinner|talk|lecture|workshop|symposium)",
    r"registration\s+(?:link|form|page|deadline|required|"
    r"is\s+(?:required|now\s+open|open|through|by|via))",
    r"click\s+here\s+to\s+register", r"(?:please|must)\s+register",
    r"pre-?regist(?:er|ration)",
    r"sign\s*-?\s*up\s+(?:here|now|by|at|via|using|link|form|required|"
    r"is\s+required)",
    r"please\s+sign\s*-?\s*up",
    r"reserve\s+(?:a|your)\s+(?:seat|spot|place|ticket)",
    r"save\s+(?:a|your)\s+(?:seat|spot)")

# A reception: the format a 1L is sent to. The title saying so is the
# plainest case ("Kirkland 1L Reception"). A talk with one afterwards is told
# apart -- "Reception and book signing to follow" means an hour of lecture
# first. Where the lift opens ("12th Floor Reception") and where you sign in
# (a reception desk) are not events, and "The Reception of Foreign Judgments"
# is a seminar.
RECEPTION_TITLE = re.compile(
    r"\breceptions?\b(?!\s+(?:of|desk|area|staff|counter|hall|room)\b)", re.I)
RECEPTION_AFTER = _rx(
    r"receptions?\s+(?:(?:and|&)\s+[\w\s-]{1,30}?\s+)?(?:to\s+|will\s+)?follows?",
    r"receptions?\s+after(?:wards?|\s+the\s+\w+)",
    r"followed\s+by\s+(?:an?\s+|the\s+)?(?:[\w-]+\s+){0,3}receptions?",
    r"then\s+(?:enjoy|participate\s+in|join(?:\s+us\s+for)?|enter|attend|"
    r"stay\s+for|head\s+to)\s+(?:an?\s+|the\s+|our\s+)?(?:[\w-]+\s+){0,3}receptions?",
    r"(?:panel|talk|lecture|discussion|program|presentation|conversation|"
    r"session|keynote|fireside\s+chat)s?\s+(?:and|&|\+|with)\s+(?:an?\s+)?"
    r"(?:[\w-]+\s+)?receptions?")
RECEPTION_HELD = _rx(
    r"(?:join\s+us\s+for|host(?:s|ing)?|invites?\s+you\s+to|"
    r"invite\s+the\s+[\w\s]{1,20}?\s+to|attend|enjoy)\s+"
    r"(?:an?\s+|our\s+|the\s+)?(?:[\w-]+\s+){0,4}receptions?",
    r"(?:networking|cocktail|evening|welcome|holiday|alumni|1L|2L|3L|"
    r"celebratory|closing|opening|wine|dessert|champagne)\s+receptions?",
    # "5:30 Reception, 7:00 PM performance" and "Reception: 6-8pm".
    r"\d{1,2}(?::\d{2})?\s*(?:[ap]\.?m\.?\s+)?receptions?",
    # Ends at the word: _rx's closing guard refuses the "-" in "6-8pm".
    r"receptions?(?=\s*:\s*\d)")
# A row of a schedule table, whose cells can run together: a calendar entry
# read "| ReceptionContinue the conversation ... over food and drink". So the
# capital of the next cell ends the word here, and only a capitalised
# "Reception" counts.
RECEPTION_ROW = re.compile(
    r"\|\s*Receptions?(?![a-z])(?!\s+(?:of|desk|area|staff|counter|hall|room)\b)")


def reception(title: str, text: str) -> str:
    """"reception", "after" (one follows a talk), or "" when none is named."""
    if RECEPTION_TITLE.search(_clean(title or "")):
        return "reception"
    if RECEPTION_AFTER.search(text or ""):
        return "after"
    if RECEPTION_HELD.search(text or "") or RECEPTION_ROW.search(text or ""):
        return "reception"
    return ""


# "NO REGISTRATION NEEDED!" on the Center for Legal English's flyer.
RSVP_WAIVED = _rx(
    r"no\s+(?:rsvp|registration|sign\s*-?\s*up)s?\s+(?:is\s+)?"
    r"(?:needed|necessary|required)",
    r"(?:rsvp|registration|sign\s*-?\s*up)s?\s+(?:is\s+|are\s+)?not\s+"
    r"(?:needed|necessary|required)",
    r"without\s+(?:an?\s+)?(?:rsvp|registration)")

# Still worth listing -- people who did sign up are going -- but no longer an
# invitation. "RSVPs have closed" ended an IP Big Law Fair entry. "Closes" is
# a deadline still to come, and "RSVP full name" asks for one.
RSVP_CLOSED = _rx(
    r"(?:rsvps?|registration|sign\s*-?\s*ups?)\s+(?:(?:has|have)\s+)?"
    r"(?:now\s+)?closed",
    r"(?:rsvps?|registration|sign\s*-?\s*ups?)\s+(?:is|are)\s+(?:now\s+)?"
    r"(?:closed|full)",
    r"(?:event|reception|panel|program)\s+is\s+(?:now\s+)?"
    r"(?:full|at\s+capacity)",
    r"sold\s+out")

# Rides and travel a host pays for. Only an offer counts: "arrive by cab,
# Uber, or Metro" is advice, "recruiting for Uber" is an employer, a wardrobe
# reimbursement is not travel, and "four hours for the stipend" pays for
# volunteering. Each pattern also takes the few words saying what the offer
# is for ("Uber vouchers for travel home after the event"), so the quote on
# the page says something; it stops at the first full stop, which is also
# where any link would begin.
_AMOUNT = r"(?:\$\s?\d{1,4}(?:\.\d\d)?\s+(?:off\s+)?(?:an?\s+)?)?"
_FOR = r"(?:\s+(?:for|to|toward|towards)\s+[^.;,!?]{0,40})?"
_RIDE = r"\b(?P<brand>uber|lyft|rideshare|ride[\s-]share|car\s+service|taxi|cab)"
PERKS = [
    (re.compile(_AMOUNT + _RIDE + r"\s+(?:ride\s+)?(?:vouchers?|codes?|credits?|"
                r"promo(?:\s+codes?)?|gift\s+cards?)\b(?:\s+code\b)?" + _FOR, re.I),
     "voucher"),
    (re.compile(r"\b(?:vouchers?|codes?|credits?)\s+(?:for|toward|towards|on)\s+"
                r"(?:an?\s+|your\s+)?" + _RIDE + r"\b" + _FOR, re.I), "voucher"),
    (re.compile(_RIDE + r"s?\s+(?:rides?\s+)?(?:will\s+be\s+|is\s+|are\s+)?"
                r"(?:provided|covered|reimbursed|paid\s+for|complimentary)\b" + _FOR,
                re.I), "ride"),
    (re.compile(r"\b(?:cover|reimburse|pay\s+for)\s+(?:your\s+|the\s+cost\s+of\s+"
                r"(?:your\s+)?)?" + _RIDE + r"\b" + _FOR, re.I), "ride"),
    (re.compile(_AMOUNT + r"\btravel\s+(?:stipends?|grants?|awards?|"
                r"subsid(?:y|ies))\b" + _FOR, re.I), "stipend"),
    (re.compile(_AMOUNT + r"\bstipends?\s+(?:for|to\s+cover|toward|towards)\s+"
                r"(?:your\s+)?travel\b" + _FOR, re.I), "stipend"),
    (re.compile(r"\b(?:travel|transportation)\s+(?:costs?\s+|expenses?\s+)?"
                r"(?:\([^)]{0,80}\)\s*)?(?:will\s+be\s+|are\s+|is\s+)?(?:fully\s+)?"
                r"(?:provided|covered|reimbursed|paid\s+for)\b" + _FOR, re.I),
     "travel"),
    (re.compile(r"\b(?:travel\s+reimbursements?|reimburse(?:ment)?\s+(?:for\s+|of\s+)?"
                r"(?:your\s+|reasonable\s+)?(?:travel|transportation|mileage|"
                r"airfare|train\s+fare))\b" + _FOR, re.I), "travel"),
]
PERK_LABELS = {"stipend": "Travel stipend", "travel": "Travel covered",
               "ride": "Ride covered"}
BRANDS = {"uber": "Uber", "lyft": "Lyft"}

# An offer withdrawn is not an offer: "we are unable to provide Uber
# vouchers", "travel will not be reimbursed". Read in the offer's own clause.
NOT_OFFERED = re.compile(
    r"\b(?:not|unable|cannot|can't|won't|isn't|aren't|unfortunately)\b|"
    r"\bno\s+(?:uber|lyft|rides?|vouchers?|codes?|travel|transportation|"
    r"stipends?|reimbursements?)\b", re.I)

# The offer's condition, read from the same sentence. "Lyft codes will be
# provided to those who register at least 48 hours before the local event
# start time"; "a $20 off Uber voucher code will be provided to all confirmed
# attendees".
WITH_RSVP = _rx(
    r"(?:to|for)\s+(?:all\s+)?(?:confirmed|registered|rsvp'?d)\s+"
    r"(?:attendees|guests|students|participants)",
    r"(?:those|students|attendees|anyone|everyone)\s+who\s+"
    r"(?:register|rsvp|sign\s*-?\s*up|have\s+registered)",
    r"if\s+you\s+(?:register|rsvp|sign\s*-?\s*up)",
    r"with\s+(?:your\s+|an?\s+)?(?:rsvp|registration)",
    r"upon\s+(?:registration|rsvp)")
LEAD_TIME = re.compile(
    r"(?:at\s+least\s+)?(\d{1,3})\s*(hours?|hrs?|days?)\s+"
    r"(?:before|in\s+advance|ahead|prior\s+to)", re.I)
_SCRUB = re.compile(r"https?://\S*|www\.\S*|\S*@\S*", re.I)


def _span(text: str, start: int, end: int, stops: str) -> tuple:
    """The stretch of text around [start, end) bounded by any of `stops`."""
    left = max(text.rfind(c, 0, start) for c in stops) + 1
    rights = [i for i in (text.find(c, end) for c in stops) if i >= 0]
    return left, (min(rights) if rights else len(text))


def _quote(text: str) -> str:
    """Words for the page: no links, no addresses, at most 80 characters."""
    text = re.sub(r"\s{2,}", " ", _SCRUB.sub("", text)).strip(" ,;:(")
    return text if len(text) <= 80 else text[:79].rsplit(" ", 1)[0] + "…"


def perks(text: str) -> list:
    """Rides and travel the host pays for, as the text states them.

    [{"label": "$20 Uber voucher", "with_rsvp": True, "lead_hours": 48,
      "phrase": "..."}], or [] when nothing is offered.
    """
    text = text or ""
    found, seen = [], set()
    for pattern, kind in PERKS:
        for match in pattern.finditer(text):
            clause = text[slice(*_span(text, match.start(), match.end(), ".;!?"))]
            if NOT_OFFERED.search(clause):
                continue
            brand = (match.groupdict().get("brand") or "").lower()
            if kind == "voucher":
                label = BRANDS.get(brand, "Ride") + " voucher"
            else:
                label = PERK_LABELS[kind]
            amount = re.match(r"\$\s?\d{1,4}(?:\.\d\d)?", match.group(0))
            if amount:
                label = amount.group(0).replace(" ", "") + " " + (
                    label if brand in BRANDS else label[0].lower() + label[1:])
            key = re.sub(r"^\$\S+ ", "", label).lower()
            if key in seen:
                continue
            seen.add(key)

            # What it depends on, from the sentence the offer is in. A stated
            # lead time is a condition too: no RSVP, no voucher.
            left, right = _span(text, match.start(), match.end(), ".!?")
            sentence = text[left:right]
            condition = WITH_RSVP.search(sentence)
            lead = LEAD_TIME.search(sentence)
            hours = None
            if lead:
                hours = int(lead.group(1)) * (
                    24 if lead.group(2).lower().startswith("d") else 1)
            # "those who register" and "at least 48 hours before" are one
            # condition when they sit together; quote it whole.
            terms = [m for m in (condition, lead) if m]
            said = ""
            if terms:
                first = min(terms, key=lambda m: m.start())
                last = max(terms, key=lambda m: m.end())
                said = (sentence[first.start():last.end()]
                        if last.start() - first.end() <= 20
                        else (lead or condition).group(0))
            phrase = match.group(0) + (" … " + said if said else "")
            found.append({"label": label, "with_rsvp": bool(terms),
                          "lead_hours": hours, "phrase": _quote(phrase)})
    return found[:2]


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

    # Asked for, or closed already -- either way an event people sign up for.
    # A stated deadline is an ask whatever else the text says.
    closed = bool(RSVP_CLOSED.search(text))
    asked = bool(RSVP_ASKED.search(text)) and not RSVP_WAIVED.search(text)

    return {"dress_code": dress, "bring": bring, "access": access,
            "rsvp_by": by, "notes": notes[:6],
            "rsvp": bool(by) or asked or closed, "rsvp_closed": closed,
            "perks": perks(text), "reception": reception(title, text)}


FIELDS = ("dress_code", "bring", "access", "rsvp_by", "notes", "rsvp",
          "rsvp_closed", "perks", "reception")
