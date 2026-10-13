"""LiveWhale calendar ingester.

Many universities run their events calendar on LiveWhale, which publishes an
iCal feed with full descriptions -- exactly the text the scorer needs. The
school's profile gives the feed (CALENDAR) and the pattern that reads a
group's slug out of each event's URL (CALENDAR_GROUP).

Server-side filtering is not available on Georgetown's: `group`, `group_id`
and the JSON `fields` parameter are all silently ignored by that instance,
and the group-scoped paths 404. So we pull the whole feed in one request
(~1 MB) and filter locally, which is both simpler and more reliable than
guessing at an undocumented API.
"""

from __future__ import annotations

import http.client
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, __file__.rsplit("/", 2)[0])

import icalparse                                    # noqa: E402
import school                                       # noqa: E402
import store                                        # noqa: E402
from network import annotate, worth_storing         # noqa: E402
from score import score_event                       # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# A whole feed comes back in a second or two. Georgetown's has also gone
# silent for the full 45 seconds and answered normally minutes later
# (12 Oct 2026), and a refresh that gives up lists none of that day's new
# calendar events. So a failed download is tried again -- within a minute
# and a half in all, waits included: the routine runs `./fff refresh` as a
# shell command, which has two minutes by default, and the mail is read
# after the calendar.
TIMEOUT = 45        # seconds one attempt waits on a silent server
ATTEMPTS = 3
PAUSE = 5           # seconds between attempts
PATIENCE = 90       # seconds for every attempt and pause together
SHORTEST = 15       # seconds; an attempt with less left is not worth making


def fetch(days: int = 30, timeout: float = TIMEOUT) -> str:
    feed = school.active().CALENDAR
    if not feed:
        raise ValueError("the school's profile names no calendar (CALENDAR)")
    req = urllib.request.Request(feed.format(days=days), headers={
        "User-Agent": UA, "Accept": "text/calendar, text/plain, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def passing(exc) -> bool:
    """Whether a failed download could go through if asked for again.

    Not when the server answered and refused the request itself: a 404 means
    the feed has moved, and asking again changes nothing.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500 or exc.code in (408, 429)
    # A timeout, a refused or dropped connection, a download cut short.
    return isinstance(exc, (OSError, http.client.HTTPException))


def download(days: int = 30, stats: dict = None) -> str:
    """The feed, asked for again after a failure that could pass.

    `stats`, when given, is told how many attempts it took.
    """
    deadline = time.monotonic() + PATIENCE
    for attempt in range(1, ATTEMPTS + 1):
        if stats is not None:
            stats["attempts"] = attempt
        try:
            return fetch(days, timeout=min(TIMEOUT, deadline - time.monotonic()))
        except Exception as exc:
            left = deadline - time.monotonic() - PAUSE
            if attempt == ATTEMPTS or not passing(exc) or left < SHORTEST:
                raise
        time.sleep(PAUSE)


def group_of(url: str) -> str:
    pattern = school.active().CALENDAR_GROUP
    m = re.match(pattern, url or "") if pattern else None
    return m.group(1) if m else ""


def to_events(ics_text: str) -> list:
    out = []
    for ev in icalparse.parse(ics_text):
        title = icalparse.unescape(ev.get("SUMMARY", ""))
        if not title:
            continue
        desc = icalparse.unescape(ev.get("DESCRIPTION", ""))
        loc = icalparse.unescape(ev.get("LOCATION", ""))
        url = ev.get("URL", "")
        group = group_of(url)
        starts, ends, all_day = icalparse.event_times(ev)
        verdict = score_event(title=title, description=desc, location=loc,
                              source="livewhale", url=url, group=group)
        out.append({
            "source": "livewhale",
            "source_id": ev.get("UID", "") or ev.get("X-LIVEWHALE-ID", ""),
            "title": title, "description": desc, "location": loc,
            "org": group, "url": url,
            "starts_at": starts, "ends_at": ends, "all_day": all_day,
            **{k: verdict[k] for k in ("venue_class", "confidence", "tier", "has_food",
                                       "meals", "reason", "signals", "rsvp_required",
                                       "restricted")},
            **annotate(title, desc, loc, group),
        })
    return out


def run(conn=None, days: int = 30, keep_all: bool = False) -> dict:
    """Fetch, score, and persist. Returns a summary dict."""
    own = conn is None
    conn = conn or store.connect()
    stats = {"source": "livewhale", "found": 0, "added": 0, "updated": 0,
             "attempts": 0, "error": None}
    try:
        events = to_events(download(days, stats))
        stats["found"] = len(events)
        for ev in events:
            # Keep anything plausible on either axis. Filtering on food
            # alone would discard every networking-only event.
            if not keep_all and ev["tier"] == "unlikely" and not worth_storing(ev):
                continue
            stats[store.upsert(conn, ev)] += 1
    # HTTPException too: a download cut short raises IncompleteRead, which is
    # not an OSError, and uncaught it ended the whole refresh before the mail.
    except (OSError, ValueError, http.client.HTTPException) as exc:
        stats["error"] = f"{type(exc).__name__}: {exc}"
        if stats["attempts"] > 1:
            stats["error"] += f" (gave up after {stats['attempts']} attempts)"
    store.log_run(conn, "livewhale", stats["found"], stats["added"],
                  stats["updated"], stats["error"])
    if own:
        conn.close()
    return stats


if __name__ == "__main__":
    print(run(days=int(sys.argv[1]) if len(sys.argv) > 1 else 30))
