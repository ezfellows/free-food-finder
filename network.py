"""Networking and recruiting value of an event.

A second, independent axis alongside the food score. The two are genuinely
orthogonal: a firm reception is both, a departmental pizza lunch is only food,
an alumni coffee chat is only networking. Scoring them separately means
neither judgement contaminates the other, and the page can be filtered by
either.

What actually matters to a law student is who will be standing in the room,
so named firms carry the most weight.

Deliberately absent: anything about the reader. Firm names are public -- a
Covington reception is public information and useful to every student -- but
how many alumni *you* know at a firm, and which of them you have already
emailed, is your job search. The page is shared, so nothing like that may
enter the scoring path, the database or the page.
"""

from __future__ import annotations

import re

import contacts
import school
from score import _clean, _rx

# --- tiers ---------------------------------------------------------------

RECRUITING = "recruiting"     # explicitly about hiring you
FIRM = "firm"                 # named firms in the room
PRACTITIONER = "practitioner" # working lawyers, unnamed
ALUMNI = "alumni"
ACADEMIC = "academic"         # scholarly; networking is incidental
NONE = "none"

# Explicit hiring pipeline. For a 1L this is the whole ballgame.
RECRUIT_SIGNALS = [
    (_rx(r"on-?campus\s+interview\w*", r"\bOCI\b", r"callback\s+interview\w*",
         r"screening\s+interview\w*"), 60, "on-campus interviews"),
    (_rx(r"summer\s+associate\w*", r"summer\s+program", r"1L\s+(?:diversity\s+)?"
         r"(?:fellowship|program|scholars?)", r"diversity\s+fellowship"),
     55, "summer associate pipeline"),
    (_rx(r"info(?:rmation)?\s+session", r"employer\s+(?:info|panel|table|showcase)",
         r"firm\s+(?:info|presentation|showcase)"), 45, "employer info session"),
    (_rx(r"career\s+fair", r"job\s+fair", r"recruit\w+\s+(?:event|fair|season)",
         r"interview\s+program",
         # SIPLA's "IP Big Law Fair": firms at tables, which is the thing
         # itself, under a name no other rule knew.
         r"big\s*law\s+fair"), 50, "career fair"),
    # A count of firms in one room is a firm fair, whatever it is called.
    # "Network with over 30 law firms" -- OCS's 1L Law Firm Receptions, the
    # biggest networking nights of the semester -- named no single firm, so
    # it earned nothing but "reception" and was not tagged as networking.
    (_rx(r"(?:over|more\s+than|at\s+least|nearly)\s+\d{1,3}\s+"
         r"(?:\w+\s+)?(?:law\s+)?firms",
         r"\d{1,3}\+\s+(?:\w+\s+)?(?:law\s+)?firms",
         r"\d{1,3}\s+(?:sponsoring|participating|attending|visiting)\s+"
         r"(?:law\s+)?firms"),
     50, "many firms attending"),
    (_rx(r"resum[eé]\s+(?:review|workshop|drop)", r"mock\s+interview",
         r"cover\s+letter\s+(?:review|workshop|feedback|clinic)",
         r"application\s+workshop", r"hiring\s+(?:timeline|process)",
         # How a firm describes its own recruiting session. "recruiting
         # landscape" and "interview process" were both uncovered, so King &
         # Spalding's "Big Law Basics" scored on its name alone.
         r"recruit\w+\s+(?:landscape|process|timeline|overview|basics)",
         r"interview\s+(?:process|tips|prep\w*)",
         r"navigating\s+(?:the\s+)?(?:interview|recruit\w+|application)",
         # How student orgs name the same session: JLSA's "Big Law 101",
         # OutLaw's "BigLaw Demystified".
         r"big\s*law\s+(?:101|basics|demystified|recruit\w*)"),
     30, "application help"),
    # An event whose whole premise is that the employers in it are hiring.
    # "Partners from actively hiring plaintiff firms" scored as nothing more
    # than practising lawyers, which is true and beside the point.
    (_rx(r"actively\s+hiring", r"hiring\s+partners?",
         r"firms?\s+(?:that\s+are\s+|who\s+are\s+)?(?:actively\s+)?hiring",
         r"who(?:'s| is)\s+hiring", r"employers?\s+(?:are\s+)?hiring"),
     45, "employers hiring"),
    (_rx(r"clerkship\w*", r"judicial\s+(?:internship|externship|clerk)"),
     40, "clerkships"),
]

# Working lawyers in the room, even when no firm is named.
PRACTITIONER_SIGNALS = [
    (_rx(r"practitioners?", r"attorneys?\s+from", r"lawyers?\s+from",
         r"partners?\s+(?:from|at)", r"in-?house\s+counsel", r"general\s+counsel",
         r"of\s+counsel",
         # Who is coming, said as an invitation. PPP's happy hour is
         # "inviting PPP DC attorneys ... meet some attorneys in a casual
         # environment" and scored as networking with nobody in the room.
         r"meet\s+(?:with\s+)?(?:\w+\s+){0,2}(?:attorneys|lawyers)",
         r"invit\w+\s+(?:\w+\s+){0,3}(?:attorneys|lawyers)"),
     34, "practising lawyers"),
    (_rx(r"enforcement\s+officials?", r"regulators?", r"agency\s+officials?",
         r"government\s+attorneys?", r"\bDOJ\b", r"\bFTC\b", r"\bSEC\b"),
     28, "government lawyers"),
    (_rx(r"industry\s+(?:professionals?|leaders?|experts?)", r"policy\s+leaders?",
         r"senior\s+leaders?"), 18, "industry figures"),
    # The format is the guest list: students rotate between tables of
    # lawyers. "Save the date for a health law speed networking event" says
    # nothing else, and scored as networking with nobody to network with.
    (_rx(r"speed[\s-]+networking"), 20, "speed networking"),
]

ALUMNI_SIGNALS = [
    (_rx(r"alumni", r"alumnae", r"alums?\b", r"graduates?\s+(?:of|from)"),
     34, "alumni present"),
    (_rx(r"reunion", r"homecoming"), 16, "reunion"),
]

# Formats where talking to strangers is the expected behaviour.
FORMAT_SIGNALS = [
    (_rx(r"networking", r"network\s+with"), 40, "explicitly networking"),
    (_rx(r"reception", r"mixer", r"happy\s+hour", r"cocktail"), 26, "reception"),
    (_rx(r"coffee\s+chat", r"meet\s*(?:and|&)\s*greet", r"office\s+hours\s+with",
         r"fireside\s+chat", r"roundtable"), 24, "small format"),
    (_rx(r"open\s+house", r"drop-?in"), 14, "open house"),
]

# Scholarly events. Real networking happens, but it is a by-product.
ACADEMIC_SIGNALS = [
    (_rx(r"symposium", r"convening", r"conference", r"summit"), 20, "symposium"),
    (_rx(r"colloquium", r"lecture", r"seminar", r"workshop", r"panel",
         r"book\s+talk"), 10, "academic session"),
]

NEGATIVE_SIGNALS = [
    (_rx(r"prospective\s+students?", r"admitted\s+students?", r"applicants?\s+only",
         r"admissions?\s+(?:event|session|webinar)"), -45, "for applicants, not students"),
    (_rx(r"faculty\s+only", r"staff\s+only", r"by\s+invitation", r"invite\s+only",
         r"closed\s+(?:session|event)"), -40, "closed event"),
    (_rx(r"virtual\s+only", r"online\s+only", r"webinar"), -22, "virtual"),
]

PRACTICE_AREAS = [
    (_rx(r"antitrust", r"competition\s+law", r"merger\s+review"), "antitrust"),
    (_rx(r"privacy", r"data\s+protection", r"cyber\w*", r"technology\s+law",
         r"\bAI\b", r"artificial\s+intelligence"), "tech & privacy"),
    (_rx(r"intellectual\s+property", r"\bIP\b", r"patent\w*", r"trademark\w*",
         r"copyright"), "IP"),
    (_rx(r"securities", r"capital\s+markets", r"\bM&A\b", r"mergers?\s+and\s+acquisitions",
         r"private\s+equity", r"corporate\s+law"), "corporate"),
    (_rx(r"litigation", r"trial\s+practice", r"appellate", r"moot\s+court"), "litigation"),
    (_rx(r"administrative\s+(?:law|state)", r"regulatory", r"governance",
         r"rule\s+of\s+law"), "administrative"),
    (_rx(r"tax\b", r"taxation"), "tax"),
    # "environmental", not "environment": PPP's happy hour promised attorneys
    # "in a casual environment" and was tagged as environmental law.
    (_rx(r"environmental\w*", r"climate", r"energy\s+law"), "environmental"),
    (_rx(r"human\s+rights", r"international\s+law", r"national\s+security"),
     "international"),
    (_rx(r"health\s+(?:law|policy)", r"\bFDA\b", r"public\s+health"), "health"),
    (_rx(r"criminal\s+(?:law|justice)", r"prosecut\w+", r"public\s+defen\w+"),
     "criminal"),
    (_rx(r"immigration", r"asylum", r"refugee"), "immigration"),
    (_rx(r"labor", r"labour", r"employment\s+law"), "labor & employment"),
    (_rx(r"pro\s+bono", r"public\s+interest", r"legal\s+aid"), "public interest"),
]

# "Information session" is a generic phrase: a study-abroad briefing on
# Bolivia matches it exactly as well as a Kirkland recruiting event. Something
# in the event has to be about law before it counts as legal networking.
LEGAL_RELEVANCE = _rx(
    r"law", r"laws", r"legal", r"attorneys?", r"lawyers?", r"counsel",
    r"\bJD\b", r"\bLL\.?M\.?\b", r"\bbar\b", r"clerkship\w*", r"judicial",
    r"judge", r"court", r"litigation", r"statut\w+", r"regulat\w+",
    r"antitrust", r"compliance", r"general\s+counsel", r"\b1Ls?\b",
    r"\b2Ls?\b", r"\b3Ls?\b", r"paralegal", r"prosecut\w+", r"jurisprudence",
    # Doctrinal vocabulary: a debate on administrative deference after Loper
    # Bright never uses the word "law" and is unmistakably a law-school event.
    r"deference", r"doctrine", r"precedent", r"constitutional", r"amicus",
    r"plaintiffs?", r"defendants?", r"appellate", r"federalist\s+society",
    r"moot\s+court", r"due\s+process", r"certiorari", r"circuit",
)

AUDIENCE = [
    (_rx(r"\b1Ls?\b", r"first-?year\s+students?"), "1L"),
    (_rx(r"\b2Ls?\b", r"second-?year\s+students?"), "2L"),
    (_rx(r"\b3Ls?\b", r"third-?year\s+students?"), "3L"),
    (_rx(r"\bLL\.?M\.?\b"), "LLM"),
]


def _scan(text, table, signals, kind) -> int:
    total = 0
    for rx, weight, label in table:
        if rx.search(text):
            total += weight
            signals.append({"kind": kind, "label": label, "weight": weight})
    return total


def _collect(text, table) -> list:
    return [name for rx, name in table if rx.search(text)]


# The law school's own buildings, as `campus.locate` names them (the
# profile's LAW_BUILDINGS). An event held in one is a law-school event
# whatever its blurb says: Georgetown Youth Advocates' career fair lists
# "juvenile justice, youth defense, child welfare" and never the word "law",
# so a career fair in the McDonough Atrium was scored as having nothing legal
# about it. The home-campus test is what keeps the business school's
# McDonough, on Main Campus, out.
def _law_building(profile):
    if not profile.LAW_BUILDINGS:
        return school.NEVER
    return re.compile(r"^(?:" + "|".join(map(re.escape, profile.LAW_BUILDINGS))
                      + r")\b")


def _in_a_law_building(location: str) -> bool:
    if not location:
        return False
    import campus               # deferred: campus is heavier than this module
    where = campus.locate(location)
    law_building = school.active().derive("network.law_building", _law_building)
    return (where.get("campus") == "home"
            and bool(law_building.match(where.get("place") or "")))


def score_network(title: str = "", description: str = "", location: str = "",
                  org: str = "") -> dict:
    """Score one event's networking value. Mirrors score.score_event's shape."""
    title, description = _clean(title), _clean(description)
    location, org = _clean(location), _clean(org)
    # Title weighted twice, as in the food scorer: "Kirkland & Ellis Reception"
    # is a stronger claim than the same words deep in a paragraph.
    #
    # Joined on a full stop for the same reason as `score.score_event`: a bare
    # space made the seam between the two copies searchable. "Milbank
    # Recruiting 101 - Georgetown" doubled reads "... Georgetown Milbank ...",
    # and a capitalised word before a one-word firm marks it as somebody's
    # surname -- which suppressed every mention of Milbank in the message.
    body = " . ".join([title, title, description, location, org])

    signals: list = []
    raw = 0
    raw += _scan(body, RECRUIT_SIGNALS, signals, "recruiting")
    raw += _scan(body, PRACTITIONER_SIGNALS, signals, "practitioner")
    raw += _scan(body, ALUMNI_SIGNALS, signals, "alumni")
    raw += _scan(body, FORMAT_SIGNALS, signals, "format")
    raw += _scan(body, ACADEMIC_SIGNALS, signals, "academic")
    raw += _scan(body, NEGATIVE_SIGNALS, signals, "negative")

    # Names only. No alumni counts: see the module docstring.
    firms = [{"name": name} for name in contacts.find_firms(body)]
    if firms:
        raw += 30 + min(20, 6 * (len(firms) - 1))
        signals.append({"kind": "firm", "weight": 30,
                        "label": f"{len(firms)} firm(s) named"})
        # A firm in the title is the event's host or its subject, not a name
        # in passing. On the firm alone "Life & Practice at Paul, Weiss",
        # "Choosing a Practice Group with White & Case" and Clifford Chance's
        # transactional panel each scored 30 -- under the storage floor -- so
        # CFLO's firm events were dropped while a contact address in a footer
        # scored the same. The title is what separates the two. A firm the
        # body has already established counts when the title names it bare:
        # "Carve Your Path at Skadden" has no legal word of its own.
        if contacts.find_firms(title, established={f["name"] for f in firms}):
            raw += 10
            signals.append({"kind": "firm", "weight": 10,
                            "label": "firm in the title"})
        # The "virtual" penalty exists to hold back incidental online noise.
        # A named firm running its own webinar is the opposite of that: King &
        # Spalding's "Big Law Basics" and Latham's "Inside IP Litigation"
        # scored 30 for the firm and -22 for being online, netting 8 -- under
        # the storage floor, so neither reached the database. Being online is
        # a fact about the room, and the room is not why this one matters.
        for signal in signals:
            if signal.get("label") == "virtual" and signal.get("weight", 0) < 0:
                raw -= signal["weight"]
                signal["weight"] = 0
                signal["label"] = "virtual (not held against a named firm)"

    # Law relevance gates the professional tiers. Firms and law-school groups
    # count on their own; otherwise the text has to mention law somewhere.
    #
    # A signal that already identified government lawyers counts too. Scoring
    # "DOJ Honors Program" at +28 for government lawyers and then -35 for
    # having nothing legal about it is the same table contradicting itself,
    # and it netted out below the storage floor -- so DOJ, FTC and SEC events
    # were dropped from the database entirely, not merely ranked low. Reading
    # it off the signal rather than copying the agency list into
    # LEGAL_RELEVANCE keeps the two from drifting apart again.
    government = any(s.get("label") == "government lawyers" for s in signals)
    law_relevant = bool(firms) or government or bool(LEGAL_RELEVANCE.search(body)) \
        or (org or "").lower() in {g.lower() for g in school.active().LAW_GROUPS} \
        or _in_a_law_building(location)
    if not law_relevant:
        raw -= 35
        signals.append({"kind": "negative", "weight": -35,
                        "label": "nothing legal about it"})

    has_recruit = any(s["kind"] == "recruiting" for s in signals)
    has_pract = any(s["kind"] == "practitioner" for s in signals)
    has_alumni = any(s["kind"] == "alumni" for s in signals)
    academic_only = (not has_recruit and not has_pract and not firms
                     and any(s["kind"] == "academic" for s in signals))

    score = max(0, min(100, raw))

    if has_recruit and score >= 45 and law_relevant:
        kind = RECRUITING
    elif firms:
        kind = FIRM
    elif has_pract and score >= 30 and law_relevant:
        kind = PRACTITIONER
    elif has_alumni and score >= 30 and law_relevant:
        kind = ALUMNI
    elif academic_only and score >= 10:
        kind = ACADEMIC
    else:
        kind = NONE

    return {
        "network_score": score,
        "network_kind": kind,
        "law_relevant": law_relevant,
        "worth_attending": kind in (RECRUITING, FIRM, PRACTITIONER, ALUMNI),
        "firms": firms,
        "practice_areas": _collect(body, PRACTICE_AREAS)[:3],
        "audience": _collect(body, AUDIENCE),
        "network_signals": sorted(signals, key=lambda s: -abs(s["weight"]))[:5],
        "network_reason": "; ".join(
            dict.fromkeys(s["label"] for s in signals))[:120] or "no networking signals",
    }


# Fields every ingester copies onto its event dict.
NETWORK_FIELDS = ("network_score", "network_kind", "firms", "practice_areas",
                  "network_reason")


def annotate(title="", description="", location="", org=""):
    """Networking fields for one event, ready to merge into an ingest row."""
    verdict = score_network(title, description, location, org)
    return {k: verdict[k] for k in NETWORK_FIELDS}


# An event is worth storing if it qualifies on EITHER axis. Without this the
# food filter silently discards every networking-only event -- an info session
# with no refreshments is exactly what a 1L wants and scores "unlikely" for
# food.
STORE_FLOOR = 34


def worth_storing(ev: dict) -> bool:
    return (int(ev.get("network_score") or 0) >= STORE_FLOOR
            and (ev.get("network_kind") or "none") != "none")
