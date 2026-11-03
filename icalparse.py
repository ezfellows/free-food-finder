"""Minimal RFC 5545 reader -- enough for the calendar feeds we consume.

Deliberately not a full iCalendar implementation. It handles line unfolding,
property parameters, text unescaping, and the three DTSTART shapes that real
feeds emit (floating local, explicit TZID, and UTC 'Z').
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:                                  # pragma: no cover
    ZoneInfo = None

import school

_UNFOLD = re.compile(r"\r?\n[ \t]")
_LINE = re.compile(r"^(?P<name>[A-Za-z0-9-]+)(?P<params>(?:;[^:\r\n]*)*):(?P<value>.*)$")


def _tz(name: str):
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(name)
    except Exception:
        return None


def unescape(value: str) -> str:
    """Undo RFC 5545 TEXT escaping."""
    out, i = [], 0
    while i < len(value):
        c = value[i]
        if c == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append({"n": "\n", "N": "\n", ",": ",", ";": ";", "\\": "\\"}.get(nxt, nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse_datetime(value: str, params: dict, default_tz: str = None):
    """Return (naive_local_datetime, is_all_day) or (None, False).

    Local means the school's wall clock: its profile's TIMEZONE, or this
    computer's own when the profile names none.
    """
    value = (value or "").strip()
    if not value:
        return None, False
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        try:
            return datetime.strptime(value[:8], "%Y%m%d"), True
        except ValueError:
            return None, False
    m = re.fullmatch(r"(\d{8})T(\d{6})(Z?)", value)
    if not m:
        return None, False
    try:
        dt = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None, False
    if m.group(3) == "Z":
        # UTC on the wire -> convert into the school's wall clock.
        if default_tz is None:
            default_tz = school.active().TIMEZONE
        dt = dt.replace(tzinfo=timezone.utc)
        target = _tz(default_tz) if default_tz else None
        if target is None:
            return dt.astimezone().replace(tzinfo=None), False
        return dt.astimezone(target).replace(tzinfo=None), False
    return dt, False        # already local wall time (with or without TZID)


def parse(text: str) -> list:
    """Yield VEVENT dicts. Unknown properties are kept as raw strings."""
    text = _UNFOLD.sub("", text or "")
    events, current = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if line == "BEGIN:VEVENT":
            current = {"_params": {}}
            continue
        if line == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
            continue
        if current is None:
            continue
        m = _LINE.match(line)
        if not m:
            continue
        name = m.group("name").upper()
        params = {}
        for part in filter(None, m.group("params").split(";")):
            if "=" in part:
                k, v = part.split("=", 1)
                params[k.upper()] = v.strip('"')
        value = m.group("value")
        # Repeated properties (CATEGORIES, ATTACH) accumulate.
        if name in current and name in ("CATEGORIES", "ATTACH", "ATTENDEE"):
            current[name] = current[name] + "," + value
        else:
            current[name] = value
            current["_params"][name] = params
    return events


def event_times(ev: dict):
    """(starts_at ISO, ends_at ISO, all_day) for one parsed VEVENT."""
    p = ev.get("_params", {})
    start, all_day = parse_datetime(ev.get("DTSTART", ""), p.get("DTSTART", {}))
    # A feed entry with no DTEND has no stated end; inventing an hour printed
    # a guess on the page as though the calendar had said it.
    end, _ = parse_datetime(ev.get("DTEND", ""), p.get("DTEND", {}))
    return (start.isoformat(timespec="seconds") if start else None,
            end.isoformat(timespec="seconds") if end else None,
            all_day)
