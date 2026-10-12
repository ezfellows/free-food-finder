"""Pull an event date and time out of free-form English.

Calendar feeds hand us structured timestamps. Listserv emails do not -- they
say "Join us Tuesday, Sept 9 from 12-1pm in Hotung 2000". This module turns
that back into datetimes.

Approach: find every date candidate and every time candidate with their
positions in the text, rank dates by how explicit they are, then attach the
time that sits nearest the winning date. Positions matter because a message
often names several dates ("RSVP by Friday, event is Sept 9").
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6,
            "mon": 0, "tue": 1, "tues": 1, "wed": 2, "weds": 2, "thu": 3,
            "thur": 3, "thurs": 3, "fri": 4, "sat": 5, "sun": 6}

_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
_WD = "|".join(sorted(WEEKDAYS, key=len, reverse=True))

# Confidence ranks how explicit a date is; ties break on position in the text.
RX_MONTH_DAY = re.compile(
    rf"\b(?:(?P<wd>{_WD})\.?,?\s+)?(?P<mon>{_MON})\.?\s+(?P<day>\d{{1,2}})"
    rf"(?:st|nd|rd|th)?(?:,?\s+(?P<year>20\d{{2}}))?\b", re.I)
RX_DAY_MONTH = re.compile(
    rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<mon>{_MON})\.?"
    rf"(?:,?\s+(?P<year>20\d{{2}}))?\b", re.I)
RX_NUMERIC = re.compile(r"\b(?P<m>\d{1,2})/(?P<d>\d{1,2})(?:/(?P<y>\d{2,4}))?\b")
RX_RELATIVE = re.compile(r"\b(?P<word>today|tonight|tomorrow)\b", re.I)
RX_WEEKDAY = re.compile(rf"\b(?:(?P<qual>this|next|coming)\s+)?(?P<wd>{_WD})\b", re.I)

RX_TIME = re.compile(
    r"\b(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>[ap])\.?\s*m\.?\b", re.I)
RX_NOON = re.compile(r"\b(?P<word>noon|midday|midnight)\b", re.I)
RX_24H = re.compile(r"\b(?P<h>[01]?\d|2[0-3]):(?P<m>\d{2})\b(?!\s*[ap]\.?m)", re.I)
# "3:30-4:30 @ MCDONOUGH 425" -- flyers routinely give a range with no
# meridiem at all, which the meridiem-anchored range pattern cannot see.
RX_BARE_RANGE = re.compile(
    r"\b(?P<h1>[01]?\d|2[0-3]):(?P<m1>\d{2})\s*(?:-|–|—|to|until|till)\s*"
    r"(?P<h2>[01]?\d|2[0-3]):(?P<m2>\d{2})\b(?!\s*[ap]\.?m)", re.I)
# "12-1pm", "12:00 - 1:30 PM", "from 12 to 1pm"
RX_RANGE = re.compile(
    r"\b(?:from\s+)?(?P<h1>\d{1,2})(?::(?P<m1>\d{2}))?\s*(?P<ap1>[ap]\.?\s*m\.?)?\s*"
    r"(?:-|–|—|to|until|till)\s*"
    r"(?P<h2>\d{1,2})(?::(?P<m2>\d{2}))?\s*(?P<ap2>[ap]\.?\s*m\.?)\b", re.I)

RX_URL = re.compile(r"https?://\S+|www\.\S+")
RX_ROOM = re.compile(r"\b(?:room|rm|suite|floor|hall)\s*#?\s*\d+\b", re.I)


def _strip_noise(text: str) -> str:
    """Blank out URLs and room numbers so their digits never look like times."""
    text = RX_URL.sub(lambda m: " " * len(m.group(0)), text or "")
    return RX_ROOM.sub(lambda m: " " * len(m.group(0)), text)


def _resolve_year(month: int, day: int, ref: date, explicit_year=None):
    """Pick the year that puts the event nearest the reference date, forward."""
    if explicit_year:
        y = int(explicit_year)
        if y < 100:
            y += 2000
        try:
            return date(y, month, day)
        except ValueError:
            return None
    for y in (ref.year, ref.year + 1, ref.year - 1):
        try:
            cand = date(y, month, day)
        except ValueError:
            continue
        # Listserv mail announces the near future; allow a few days of slack
        # for messages read late.
        if -7 <= (cand - ref).days <= 300:
            return cand
    try:
        return date(ref.year, month, day)
    except ValueError:
        return None


def _find_dates(text: str, ref: date):
    """[(position, date, confidence)] -- higher confidence is more explicit."""
    found = []
    for m in RX_MONTH_DAY.finditer(text):
        mon = MONTHS.get(m.group("mon").lower())
        d = _resolve_year(mon, int(m.group("day")), ref, m.group("year"))
        if d:
            found.append((m.start(), d, 100 if m.group("wd") else 90))
    for m in RX_DAY_MONTH.finditer(text):
        mon = MONTHS.get(m.group("mon").lower())
        d = _resolve_year(mon, int(m.group("day")), ref, m.group("year"))
        if d:
            found.append((m.start(), d, 85))
    for m in RX_NUMERIC.finditer(text):
        mo, dy = int(m.group("m")), int(m.group("d"))
        if 1 <= mo <= 12 and 1 <= dy <= 31:
            d = _resolve_year(mo, dy, ref, m.group("y"))
            if d:
                found.append((m.start(), d, 70))
    for m in RX_RELATIVE.finditer(text):
        w = m.group("word").lower()
        found.append((m.start(), ref + timedelta(days=1 if w == "tomorrow" else 0), 60))
    for m in RX_WEEKDAY.finditer(text):
        wd = WEEKDAYS.get(m.group("wd").lower())
        if wd is None:
            continue
        ahead = (wd - ref.weekday()) % 7
        if m.group("qual") and m.group("qual").lower() == "next":
            ahead += 7
        elif ahead == 0:
            ahead = 0 if not m.group("qual") else 7
        found.append((m.start(), ref + timedelta(days=ahead), 40))
    return found


def _assume_afternoon(hour: int) -> int:
    """Resolve a bare hour the way a campus flyer means it.

    A flyer that says "3:30" means half past three in the afternoon; nothing
    on a law school calendar starts at 03:30. Hours 8-11 are left alone --
    morning events are real, and a breakfast flyer says "9:00".
    """
    return hour + 12 if 1 <= hour <= 7 else hour


def _mk_time(h, m, ap):
    h, m = int(h), int(m or 0)
    if ap:
        ap = ap.replace(".", "").replace(" ", "").lower()[0]
        if ap == "p" and h != 12:
            h += 12
        elif ap == "a" and h == 12:
            h = 0
    if not 0 <= h <= 23 or not 0 <= m <= 59:
        return None
    return h, m


def _find_times(text: str):
    """[(position, (h, m), (h, m) or None, confidence)] for start/end pairs.

    A range is also matched, separately, as the two bare times inside it, and
    those halves must not compete with the whole. `find_event_datetime` picks
    the time nearest the date it settled on, so on a poster reading
    "SAT 10 AM - 6 PM SEPT. 19" the standalone "6 PM" sits closer to the date
    than the range does and won -- publishing an all-day bootcamp as a 6pm
    event. Recording each range's span and dropping the times inside it
    settles that by construction rather than by tuning distances.
    """
    found = []
    spans = []
    for m in RX_RANGE.finditer(text):
        ap2 = m.group("ap2")
        # "12-1pm": the meridiem on the end applies to the start too, unless
        # doing so would put the start after the end (e.g. "11-1pm").
        start = _mk_time(m.group("h1"), m.group("m1"), m.group("ap1") or ap2)
        end = _mk_time(m.group("h2"), m.group("m2"), ap2)
        if start and end and start > end and not m.group("ap1"):
            alt = _mk_time(m.group("h1"), m.group("m1"),
                           "a" if ap2.strip().lower().startswith("p") else "p")
            if alt and alt <= end:
                start = alt
        if start:
            found.append((m.start(), start, end, 100))
            spans.append((m.start(), m.end()))

    def inside_a_range(pos: int) -> bool:
        return any(lo <= pos < hi for lo, hi in spans)

    for m in RX_TIME.finditer(text):
        if inside_a_range(m.start()):
            continue
        t = _mk_time(m.group("h"), m.group("m"), m.group("ap"))
        if t:
            found.append((m.start(), t, None, 80))
    for m in RX_NOON.finditer(text):
        if inside_a_range(m.start()):
            continue
        w = m.group("word").lower()
        found.append((m.start(), (0, 0) if w == "midnight" else (12, 0), None, 75))
    for m in RX_BARE_RANGE.finditer(text):
        start = _mk_time(m.group("h1"), m.group("m1"), None)
        end = _mk_time(m.group("h2"), m.group("m2"), None)
        if not start or not end:
            continue
        start = (_assume_afternoon(start[0]), start[1])
        end = (_assume_afternoon(end[0]), end[1])
        if inside_a_range(m.start()):
            continue
        found.append((m.start(), start, end, 70))
        spans.append((m.start(), m.end()))
    for m in RX_24H.finditer(text):
        if inside_a_range(m.start()):
            continue
        t = _mk_time(m.group("h"), m.group("m"), None)
        if t:
            found.append((m.start(), (_assume_afternoon(t[0]), t[1]), None, 50))
    return found


def find_event_datetime(text: str, ref=None):
    """Best (start, end, all_day, confidence) for the event described in `text`.

    `ref` anchors relative expressions -- pass the email's Date header.
    Returns (None, None, False, 0) when no date can be found.
    """
    ref = ref or datetime.now()
    ref_date = ref.date() if isinstance(ref, datetime) else ref
    clean = _strip_noise(text)

    dates = _find_dates(clean, ref_date)
    if not dates:
        return None, None, False, 0
    # Most explicit wins; earliest mention breaks ties (subject lines lead).
    pos, day, dconf = max(dates, key=lambda t: (t[2], -t[0]))

    times = _find_times(clean)
    if not times:
        return (datetime.combine(day, datetime.min.time()), None, True,
                min(dconf, 60))

    # Attach the time nearest the winning date mention.
    tpos, start_t, end_t, tconf = min(times, key=lambda t: (abs(t[0] - pos), -t[3]))
    start = datetime.combine(day, datetime.min.time()).replace(
        hour=start_t[0], minute=start_t[1])
    # No end unless the text states one. This used to default to an hour
    # after the start, and the page printed it as fact: "5:30pm-6:30pm" for an
    # Arnold & Porter reception whose invitation says only "5:30 pm". A reader
    # deciding whether to leave class needs the difference between "ends at
    # 6:30" and "nobody said". The page shows the start alone.
    end = None
    if end_t:
        end = datetime.combine(day, datetime.min.time()).replace(
            hour=end_t[0], minute=end_t[1])
        if end <= start:
            end += timedelta(days=1)
    return start, end, False, min(100, (dconf + tconf) // 2)
