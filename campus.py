"""Where an event is, said the way you would say it out loud.

A university calendar mixes campuses. Georgetown's puts the Capitol Campus,
where the law school is, beside Main Campus in Burleith, 4km and a shuttle
ride away -- and including those buried the events a law student can actually
reach. This module sorts them. The docket is for one campus, its "home"; the
school's profile (schools/) names that campus's buildings and the landmarks of
any other, which reads as "away".

The output that matters is `place` — the building **and the room**. Feeds write
the same room a dozen ways ("MCD 208", "MCDONOUGH 425", "HOTUNG 6005"), and
tidying that to a bare building name throws away the only part you need once
you are in the lift.
"""

from __future__ import annotations

import re

import school

# Sources that only ever publish home-campus events, beside the calendar
# groups the profile names. The flyer broadcast is the school's own
# student-life mailing, so everything in it is here.
HOME_SOURCES = {"broadcast"}

# Events that earn a place despite being across town: a firm recruiting at
# law students is worth the shuttle in a way that Drag Bingo is not.
LAW_NETWORKING_KINDS = ("recruiting", "firm")

# A room is a three- or four-digit number, a floor, or a named space. The
# digit form is anchored to avoid swallowing a street number or a year.
ROOM_PATTERNS = [
    # A pair of suites is one place, not the first of two numbers. Matched
    # before the bare-digits rule, which would otherwise report "Suites 100
    # and 120" as room 100 and send half the visitors to the wrong door.
    re.compile(r"\b(suites?\s*#?\s*[0-9]{1,4}[A-Za-z]?\s*(?:&|and|-|–)\s*"
               r"#?\s*[0-9]{1,4}[A-Za-z]?)\b", re.I),
    re.compile(r"\broom\s*#?\s*((?:LL)?[0-9]{1,4}[A-Za-z]?)\b", re.I),
    # Lower level: "111 Mass Ave Room LL201", where Freshfields held its 1L
    # panel. The digits are glued to the letters, so the bare-digits rule
    # below never sees them.
    re.compile(r"\b(LL-?[0-9]{2,4}[A-Za-z]?)\b", re.I),
    # No year guard here: Hotung's rooms really are 2000, 2001, 6005, and
    # rejecting anything that looks like a year threw away real room numbers.
    # A location string almost never contains a year.
    # Not a street number: "801 17th Street NW" reported room 801, and
    # "1301 K Street NW" room 1301. A room number is never followed by a
    # street word.
    re.compile(r"\b([0-9]{3,4}[A-Za-z]?)\b(?!\s*(?:st\b|street|ave\b|avenue|"
               r"rd\b|road|blvd|boulevard|pl\b|place|way\b|dr\b|drive|"
               r"ct\b|court|ln\b|lane|[NS][EW]\b))", re.I),
    re.compile(r"\b(\d{1,2}(?:st|nd|rd|th)\s+floor)\b", re.I),
    re.compile(r"\b(?:suite|ste\.?)\s*([0-9A-Za-z-]{1,6})\b", re.I),
]

# Named spaces any building might have. The profile adds its own (SPACES),
# searched first and across the whole location, because the building pattern
# may already have consumed the name ("The Whisk, McDonough Hall") and The
# Whisk is the part you actually walk to. Spaces with names of their own
# (PROPER_SPACES) read "The Whisk, McDonough", not "McDonough The Whisk".
# Generic ones read the other way round: "McDonough Lobby".
GENERIC_SPACES = (r"faculty\s+lounge", r"student\s+lounge", r"lobby", r"atrium",
                  r"courtyard", r"terrace", r"multi-?purpose\s+room")

URL = re.compile(r"https?://\S+|\bwww\.\S+", re.I)

# An online event has no campus, so the walking-distance rule cannot judge it
# and would drop it by default. Recognising it lets `keep_event` apply the
# same recruiting test that already earns an away event its place.
VIRTUAL = re.compile(
    r"\bvirtual(?:ly)?\b|\bonline\b|\bwebinar\b|\bzoom\b|\bteams\s+meeting\b|"
    r"\bgoogle\s+meet\b|\bflo\s*recruit\b|\bremote(?:ly)?\b|"
    r"\bheld\s+virtually\b|\bvia\s+zoom\b", re.I)

# A street address: a number, some words, a street word, optionally a
# quadrant (Washington's NW). Somewhere a reader can actually go -- which is
# the difference between "off campus" and "we have no idea".
STREET_ADDRESS = re.compile(
    r"\b\d{1,5}\s+[A-Za-z0-9.'\- ]{2,40}?\b"
    r"(?:st|street|ave|avenue|rd|road|blvd|boulevard|pl|place|way|dr|drive|"
    r"ct|court|ln|lane|plaza|circle|cir)\b\.?"
    r"(?:\s*,?\s*(?:N|S)\.?(?:E|W)\.?\b)?", re.I)

APOSTROPHES = re.compile(r"['’`]")


def _patterns(profile) -> dict:
    """The profile's place words, compiled once."""
    spaces = list(profile.SPACES) + list(GENERIC_SPACES)
    return {
        "named": re.compile(r"\b(" + "|".join(spaces) + r")\b", re.I),
        "proper": (re.compile(r"^(" + "|".join(profile.PROPER_SPACES) + ")", re.I)
                   if profile.PROPER_SPACES else school.NEVER),
        # Organisers write the building and the room as one token --
        # "McD520", "Hotung1000". Without a space the room pattern's word
        # boundary never fires and the room is lost.
        "glued": (re.compile(r"\b(" + "|".join(map(re.escape, profile.ROOM_PREFIXES))
                             + r")\s*[-]?\s*(\d{3,4}[a-z]?)\b", re.I)
                  if profile.ROOM_PREFIXES else None),
        "home_groups": {g.lower() for g in profile.HOME_GROUPS} | HOME_SOURCES,
    }


def _here() -> tuple:
    profile = school.active()
    return profile, profile.derive("campus", _patterns)


def _unglue(text: str, glued) -> str:
    if glued is None:
        return text or ""
    return glued.sub(lambda m: f"{m.group(1)} {m.group(2)}", text or "")


def _title(text: str) -> str:
    """Title-case a named space without mangling short words."""
    return " ".join(w if w.isupper() and len(w) > 1 else w.capitalize()
                    for w in text.split())


def _room(text: str, whole: str = "", named=None) -> str:
    """The room, floor or named space in a location string, if there is one."""
    for pattern in ROOM_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    if named is None:
        named = _here()[1]["named"]
    found = named.search(whole or text)
    return _title(found.group(1)) if found else ""


def locate(location: str = "", org: str = "", body: str = "") -> dict:
    """Resolve an event's place.

    Returns `place` (building and room), `venue`, `room`, and `campus`, which
    is "home" (the campus this docket is for), "away" (another campus of the
    university), "offcampus", "virtual", or "unknown". `on_campus` is kept as
    a convenience but says only that the event is not positively across town
    -- use `keep_event` to decide whether to publish it.
    """
    profile, rx = _here()
    raw = (location or "").strip()
    clean = _unglue(APOSTROPHES.sub("", raw), rx["glued"])
    org = (org or "").lower()
    haystack = _unglue(APOSTROPHES.sub("", f"{raw} {body or ''}"), rx["glued"])

    def place_for(full, short, remainder):
        room = _room(remainder, clean, rx["named"])
        if room:
            place = (f"{room}, {short}" if rx["proper"].match(room)
                     else f"{short} {room}")
            return {"place": place, "venue": full, "room": room,
                    "campus": "home", "on_campus": True}
        return {"place": full, "venue": full, "room": "",
                "campus": "home", "on_campus": True}

    for pattern, full, short in profile.venues:
        match = pattern.search(clean)
        if match:
            # Look for the room in what is left once the building name is out
            # of the way, so "500 First Street NW" does not report room 500.
            remainder = clean[:match.start()] + " " + clean[match.end():]
            return place_for(full, short, remainder)

    if profile.away.search(haystack):
        return {"place": "", "venue": "", "room": "", "campus": "away",
                "on_campus": False}

    # Read with the links taken out. Flo Recruit is where firms hold virtual
    # coffee chats, and also where nearly every firm takes RSVPs for events in
    # its own office -- so "florecruit.com/.../register" in the RSVP link made
    # Kirkland's reception at 1301 Pennsylvania Avenue, and Wilson Sonsini's
    # open house on K Street, "Virtual".
    spoken = URL.sub(" ", haystack)
    stated = URL.sub(" ", clean)

    # An address in the location field is where the event is. The body can
    # mention an online anything -- a webinar next month, a Zoom link for
    # those who cannot come -- or the school that approved the flyer, and the
    # location still wins.
    if raw and STREET_ADDRESS.search(clean) and not VIRTUAL.search(stated):
        return {"place": raw, "venue": "", "room": "",
                "campus": "offcampus", "on_campus": False}

    # A flyer's approval stamp (the profile's STAMP: "Approved by Georgetown
    # Law" on every OSL flyer) names who approved it, not the room. Only the
    # stamp is taken out: CFLO's "Location: GULC Campus (Room TBD)" is a real
    # statement of where.
    #
    # And when the location field already names a place, the school's name in
    # the blurb is the institution, not a room: a panel "at Martin Luther King
    # Jr. Memorial Library, Room 401-A" whose blurb mentioned Georgetown Law
    # was published as "Georgetown Law 401". A specific building in the body
    # still counts; the bare name of the school does not.
    unstamped = profile.stamp.sub(" ", haystack)
    for pattern, full, short in profile.venues:
        if raw and full == profile.SCHOOL_VENUE:
            continue
        match = pattern.search(unstamped)
        if match:
            return place_for(full, short, clean)

    if VIRTUAL.search(spoken):
        # Said plainly, so nobody sets off to walk there.
        return {"place": "Virtual", "venue": "Virtual", "room": "",
                "campus": "virtual", "on_campus": False}

    if raw and STREET_ADDRESS.search(clean):
        # A real address, just not one of ours. Worth telling a reader apart
        # from "somewhere unnamed": one is a walk they can plan, the other is
        # a blank.
        return {"place": raw, "venue": "", "room": "",
                "campus": "offcampus", "on_campus": False}

    if raw:
        # Named somewhere we cannot place. Show the feed's own words, but
        # leave the campus unknown so `keep_event` decides.
        return {"place": raw, "venue": "", "room": _room(clean, clean, rx["named"]),
                "campus": "unknown", "on_campus": True}

    if org in rx["home_groups"]:
        return {"place": "", "venue": "", "room": "", "campus": "home",
                "on_campus": True}

    return {"place": "", "venue": "", "room": "", "campus": "unknown",
            "on_campus": True}


def keep_event(place: dict, org: str = "", network_kind: str = "",
               school_source: bool = False) -> bool:
    """Whether to publish an event at all.

    Another campus is across town, so its events are dropped -- unless the
    event exists to recruit law students, which is worth the shuttle. An
    unplaceable event is kept only when it came from one of the school's own
    sources; a university-wide calendar is mostly the other campus, and
    defaulting those in was what put Drag Bingo on a law school docket.
    """
    where = (place or {}).get("campus", "unknown")
    law_networking = (network_kind or "") in LAW_NETWORKING_KINDS
    if where == "home":
        return True
    if where == "away":
        return law_networking
    if where == "offcampus":
        # A student org's own event is a school event wherever it is held:
        # APALSA's mentorship social is at Penn Social, a 14-minute walk, and
        # the card says so. `school_source` is set only for mail and the flyer
        # broadcast, both of which are vetted before they get this far.
        return school_source or law_networking
    if where == "virtual":
        # Nothing to walk to, so distance cannot rule it out -- but the docket
        # is not a listing of every webinar. It earns its place the same way
        # an away event does: firms or recruiters in the room.
        return law_networking
    return (school_source or (org or "").lower() in _here()[1]["home_groups"]
            or law_networking)


def is_reachable(location: str = "", org: str = "", body: str = "",
                 network_kind: str = "") -> bool:
    place = locate(location, org, body)
    return keep_event(place, org, network_kind)
