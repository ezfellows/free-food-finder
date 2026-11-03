"""Checking what students post before it counts as an event.

A submission arrives as one person's word: a title, a time, a room, sometimes
a link. It goes on the page immediately -- that is the point of letting people
post -- but it goes on marked unverified, and it stays a post rather than an
event until something has actually been checked.

This module does the checking that can be done without a human, and reports
what cannot. Nothing here decides to publish or delete: it gathers evidence
and says what the evidence supports, so the review step is a judgement made on
stated grounds rather than a guess.

Four things are worth knowing about a post, and all four are answerable from
what is already in the project:

* **Does the room exist?** `campus.locate` knows the buildings and their room
  numbering. "McDonough 205" resolves; "McDonough 9000" does not.
* **Is the time plausible?** In the future, and not so far out that it is a
  typo for a date that has passed.
* **Does the note describe food?** `score.score_event` is the same judge the
  scraped feed gets, so a post saying "free pizza" earns the same tag as a
  flyer saying it.
* **Does anything corroborate it?** A scraped event in the same slot from the
  same org is the strongest evidence a post is real -- and also means the
  docket already has it.

A link is evidence too, but only when it points somewhere already trusted. A
submitted URL is attacker-controlled in principle, so the routine opens one
only on a domain the school already uses and judges anything else on its shape.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse

import campus
import school
from score import score_event

# Somewhere the school already sends people. A link here can be opened to see
# whether it resolves; anything else is judged on its shape and flagged. The
# school's own domains come from its profile (LINK_DOMAINS).
TRUSTED_HOSTS = (
    "campusgroups.com", "docs.google.com", "forms.gle",
    "drive.google.com", "linktr.ee", "florecruit.com", "flo.link",
    "eventbrite.com",
)


def trusted_hosts() -> tuple:
    return tuple(school.active().LINK_DOMAINS) + TRUSTED_HOSTS

# A shortener hides its destination, which is the whole objection to it.
SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd",
              "buff.ly", "rebrand.ly", "shorturl.at")

MAX_AHEAD = timedelta(days=200)

# Words too common to mean two titles describe the same event.
COMMON = {"first", "meeting", "general", "body", "event", "student", "students",
          "with", "week", "annual", "fall", "spring",
          "lunch", "panel", "session", "club", "society", "reception"}


def common_words() -> set:
    """COMMON, and the school's own name words: every title has them."""
    return COMMON | {w.lower() for w in school.active().COMMON_WORDS}


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def link_standing(url: str) -> dict:
    """What can be said about a submitted link without following it."""
    url = (url or "").strip()
    if not url:
        return {"kind": "none", "note": "no link given"}
    if not re.match(r"^https?://", url):
        return {"kind": "bad", "note": "not an http(s) link"}
    host = _host(url)
    if not host:
        return {"kind": "bad", "note": "no host in the link"}
    if any(host == s or host.endswith("." + s) for s in SHORTENERS):
        return {"kind": "opaque",
                "note": f"shortener ({host}); destination unknown"}
    if any(host == t or host.endswith("." + t) for t in trusted_hosts()):
        return {"kind": "trusted", "note": host, "host": host}
    return {"kind": "unknown", "note": f"unrecognised domain ({host})"}


def read_posts(path: str) -> list:
    """The submissions embedded in a published page."""
    try:
        with open(path, encoding="utf-8") as fh:
            html = fh.read()
    except OSError:
        return []
    marker = '<script type="application/json" id="submitted-data">'
    start = html.find(marker)
    if start < 0:
        return []
    start += len(marker)
    end = html.find("</script>", start)
    try:
        state = json.loads(html[start:end])
    except ValueError:
        return []
    posts = state.get("posts") if isinstance(state, dict) else None
    return posts if isinstance(posts, list) else []


def _when(post: dict):
    raw = (post.get("start") or "").strip()
    for shape in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw[:len(shape) + 2], shape)
        except ValueError:
            continue
    return None


def corroboration(conn, post: dict) -> dict:
    """A scraped event in the same slot is the strongest evidence there is."""
    when = _when(post)
    if conn is None or when is None:
        return {}
    rows = conn.execute(
        "SELECT id, title, org, location, source, starts_at FROM events "
        "WHERE starts_at LIKE ? AND COALESCE(submitted_by,'') = ''",
        (when.strftime("%Y-%m-%d") + "%",)).fetchall()
    slot = when.strftime("%H:%M")
    # Two words in common, not one. Matching on a single shared word made
    # "APALSA First GBM" corroborate "SALDF Book Club: First Meeting" on the
    # strength of "first", which is evidence of nothing.
    common = common_words()
    mine = {w for w in re.findall(r"[a-z]{4,}", (post.get("title") or "").lower())
            if w not in common}
    for row in rows:
        theirs = {w for w in re.findall(r"[a-z]{4,}", (row["title"] or "").lower())
                  if w not in common}
        shared = mine & theirs
        if (row["starts_at"] or "")[11:16] == slot and shared:
            return {"id": row["id"], "title": row["title"],
                    "source": row["source"], "how": "same time, shared wording"}
        if len(shared) >= 2:
            return {"id": row["id"], "title": row["title"],
                    "source": row["source"], "how": "shared wording"}
    return {}


def check(post: dict, conn=None, now=None) -> dict:
    """Everything that can be established about one post, and what is left.

    Returns `verdict` -- "ok", "doubtful" or "bad" -- plus the reasons behind
    it. "ok" means nothing contradicts it and something supports it; "doubtful"
    means a human should look; "bad" means it cannot be what it says it is.
    """
    now = now or datetime.now()
    good, doubts, bad = [], [], []

    title = (post.get("title") or "").strip()
    if len(title) < 3:
        bad.append("no title")

    when = _when(post)
    if when is None:
        bad.append("no readable date")
    elif when < now - timedelta(hours=2):
        bad.append("already happened (%s)" % when.strftime("%-d %b %H:%M"))
    elif when > now + MAX_AHEAD:
        doubts.append("more than %d days out" % MAX_AHEAD.days)
    else:
        good.append("date is ahead (%s)" % when.strftime("%-d %b %H:%M"))

    place = (post.get("place") or "").strip()
    if not place:
        doubts.append("no room given")
    else:
        where = campus.locate(place)
        if where.get("campus") == "home":
            good.append("room resolves (%s)" % where["place"])
        elif where.get("campus") == "away":
            bad.append("%s, not within walking distance"
                       % (school.active().AWAY_NAME or "Another campus"))
        else:
            doubts.append("room not recognised (%r)" % place[:32])

    note = (post.get("note") or "").strip()
    if note:
        food = score_event(title=title, description=note, location=place)
        if food.get("tier") in ("confirmed", "likely"):
            good.append("food wording holds up (%s)" % food.get("reason", "")[:40])
        elif food.get("tier") == "unlikely":
            doubts.append("nothing in the note reads as food")

    link = link_standing(post.get("link"))
    if link["kind"] == "trusted":
        good.append("link on a domain the school uses (%s)" % link["note"])
    elif link["kind"] == "none":
        doubts.append("no proof link")
    elif link["kind"] == "bad":
        bad.append(link["note"])
    else:
        doubts.append(link["note"])

    match = corroboration(conn, post)
    if match:
        good.append("matches a scraped event (%s)" % (match["title"] or "")[:40])

    verdict = "bad" if bad else ("ok" if good and not doubts else "doubtful")
    return {"verdict": verdict, "good": good, "doubts": doubts, "bad": bad,
            "link": link, "corroborates": match,
            "starts_at": when.isoformat(timespec="seconds") if when else ""}


def as_event(post: dict, checked: dict) -> dict:
    """A verified post, shaped like every other event so it is scored alike."""
    title = (post.get("title") or "").strip()
    note = (post.get("note") or "").strip()
    place = (post.get("place") or "").strip()
    food = score_event(title=title, description=note, location=place)
    where = campus.locate(place)
    link = post.get("link") or ""
    return {
        "id": "sub-" + str(post.get("id") or ""),
        "source": "submitted",
        "source_id": "submitted#%s" % (post.get("id") or ""),
        "title": title,
        "description": note[:4000],
        "location": where.get("place") or place,
        "org": "",
        "submitted_by": (post.get("by") or "").strip()[:60],
        "url": link if link_standing(link)["kind"] in ("trusted",) else "",
        "rsvp_url": link if link_standing(link)["kind"] == "trusted" else "",
        "starts_at": checked["starts_at"],
        "ends_at": None,
        "all_day": False,
        "date_confidence": 90,
        **{k: food[k] for k in ("venue_class", "confidence", "tier", "has_food",
                                "meals", "reason", "signals", "rsvp_required",
                                "restricted")},
    }
