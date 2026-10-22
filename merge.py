"""Is it the same event? Folding a second sighting into the first.

The store already merges two sightings when they print the same words on the
same day (`store.fingerprint`), and drops a newsletter's copy of a listing
another source holds at the same minute (`store.dedupe_slots`). Neither
catches a group chat. A GroupMe post calls an event what its members call
it -- "game night" for the "SIPLA x Statutes & Ladders Game Night", "ACS" for
the American Constitution Society -- and often gives no time at all.

So a post is matched on what two listings of one event share:

* the same day, always;
* start times no more than `CLOSE` minutes apart, when both give one;
* and one of: the same room; most of the shorter title's distinctive words;
  or the same organisation (by name or by its initials) with at least one
  distinctive word in common, or at the very same minute.

A **secondhand** source -- one that restates what an organisation announced
elsewhere, as a chat does -- never decides a field another source states. Its
listing is folded into the other: the other keeps its title and time, gains
whatever it lacked (an end, a room, a sign-up link), and gains the post's
words for the scorers, so "free dinner (Arepa Zone)" counts as food for an
email that never mentioned any. Each fold is recorded in the `merges` table
with the reasons, for `./fff merges`.

It runs both ways round. `./fff add` folds a post into an event already
stored; `fold_secondhand`, on every refresh, folds a post stored first into
the email or flyer that arrives after it.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import campus
import store

# Sources that repeat an announcement made elsewhere.
SECONDHAND = ("groupme",)
CLOSE = 45            # minutes: "6PM" in a chat, 6:00 on the flyer

# Words every event title has, which say nothing about which event it is.
GENERIC = set("""
a an and the with for of at on in to by from our your their us we you join
come presents present presented hosted host hosting co cohosted x vs
event events night evening day week weekly tonight today tomorrow annual
panel discussion talk conversation chat fireside meeting gbm general body
lunch learn dinner breakfast brunch snack snacks food free reception happy
hour mixer social info information session workshop series program
law legal georgetown gulc student students club society association org
organization organisation 1l 2l 3l
""".split())

_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> set:
    return {w for w in _WORD.findall((text or "").lower().replace("&", " "))
            if w not in GENERIC and not w.isdigit()}


def _initials(name: str) -> str:
    """"American Constitution Society" -> "acs"; "Women's Legal Alliance"
    -> "wla". Small words are skipped, as organisations skip them."""
    return "".join(w[0] for w in _WORD.findall((name or "").lower())
                   if w not in {"of", "the", "and", "for", "at", "in", "s"})


def _org_key(name: str) -> str:
    return " ".join(_WORD.findall((name or "").lower()))


def same_org(a: str, b: str) -> bool:
    """One organisation under two names: in full, or by its initials."""
    ka, kb = _org_key(a), _org_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    short, full = sorted((ka, kb), key=len)
    if " " not in short and len(short) >= 2:
        return short == _initials(full)
    return short in full


def _start(event: dict):
    """(day, minutes past midnight or None when no time is given)."""
    raw = event.get("starts_at") or ""
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return raw[:10], None
    if event.get("all_day"):
        return raw[:10], None
    return raw[:10], when.hour * 60 + when.minute


def _place(event: dict) -> str:
    where = campus.locate(event.get("location") or "")
    if not where.get("room"):
        return ""
    return (where.get("place") or "").strip().lower()


def same_event(a: dict, b: dict) -> list:
    """Why a and b are one event, or [] when they are not."""
    day_a, min_a = _start(a)
    day_b, min_b = _start(b)
    if not day_a or day_a != day_b:
        return []
    timed = min_a is not None and min_b is not None
    if timed and abs(min_a - min_b) > CLOSE:
        return []
    reasons = ["same day"]
    if timed:
        reasons.append("same time" if min_a == min_b
                       else "%d min apart" % abs(min_a - min_b))

    room_a, room_b = _place(a), _place(b)
    room = bool(room_a) and room_a == room_b
    shared = _words(a.get("title")) & _words(b.get("title"))
    overlap = _overlap(a, b)
    org = same_org(a.get("org"), b.get("org")) or bool(
        _org_named_in_title(a, b) or _org_named_in_title(b, a))

    if room:
        reasons.append("same room")
    if shared:
        reasons.append("titles share " + ", ".join(sorted(shared)))
    if org:
        reasons.append("same organisation")
    if (room or overlap >= 0.6 or (org and shared)
            or (org and timed and min_a == min_b)):
        return reasons
    return []


def _overlap(a: dict, b: dict) -> float:
    """How much of the shorter title's distinctive words the other shares."""
    wa, wb = _words(a.get("title")), _words(b.get("title"))
    shorter = min(len(wa), len(wb))
    return len(wa & wb) / shorter if shorter else 0.0


def twins(a: dict, b: dict) -> list:
    """Why two first-hand listings are one event, or [].

    Two emails, or the calendar and an email, are sources of equal standing,
    so neither is folded into the other on the looser test a chat post meets.
    They must start at the very same minute and share most of the shorter
    title's distinctive words -- "Drinan Lecture on Human Rights" and
    "2026-2027 Drinan Lecture on Human Rights: Racial Justice" -- and must
    not name two different rooms.
    """
    day_a, min_a = _start(a)
    day_b, min_b = _start(b)
    if not day_a or day_a != day_b or min_a is None or min_a != min_b:
        return []
    room_a, room_b = _place(a), _place(b)
    if room_a and room_b and room_a != room_b:
        return []
    if _overlap(a, b) < 0.6:
        return []
    shared = _words(a.get("title")) & _words(b.get("title"))
    reasons = ["same day", "same time", "titles share " + ", ".join(sorted(shared))]
    if room_a and room_a == room_b:
        reasons.insert(2, "same room")
    return reasons


def _org_named_in_title(a: dict, b: dict) -> bool:
    """"ACS Halloween Movie Night" names the American Constitution Society."""
    org = b.get("org") or ""
    initials = _initials(org)
    title = set(_WORD.findall((a.get("title") or "").lower()))
    return len(initials) >= 2 and initials in title


def find(conn, event: dict, among_secondhand: bool = True):
    """(row, reasons) for the stored event `event` is a sighting of, or
    (None, []). Another source's listing is preferred to a post's."""
    day = (event.get("starts_at") or "")[:10]
    if not day:
        return None, []
    best = (None, [], -1)
    for row in conn.execute(
            "SELECT * FROM events WHERE substr(starts_at, 1, 10) = ? "
            "AND NOT COALESCE(dismissed, 0)", (day,)):
        if event.get("id") and row["id"] == event["id"]:
            continue
        posted = secondhand(row["source"])
        if posted and not among_secondhand:
            continue
        reasons = same_event(event, dict(row))
        if not reasons:
            continue
        rank = len(reasons) + (0 if posted else 10)
        if rank > best[2]:
            best = (row, reasons, rank)
    return best[0], best[1]


def secondhand(source: str) -> bool:
    parts = [p for p in (source or "").split(",") if p]
    return bool(parts) and all(p in SECONDHAND for p in parts)


def fold(conn, row, event: dict, reasons=()) -> str:
    """Fold `event` (a sighting) into the stored `row`. Returns row's id.

    The stored listing keeps its title and time. It gains what it lacked,
    the sighting's source, and the sighting's words; and it takes the
    sighting's food or networking verdict only where that one is surer.
    """
    current = dict(row)
    changes = {}
    for k in ("ends_at", "location", "org", "url", "rsvp_url", "flyer_id"):
        if not current.get(k) and event.get(k):
            changes[k] = event[k]
    # A date with no time yet takes the post's time.
    if current.get("all_day") and event.get("starts_at") and not event.get("all_day"):
        changes["starts_at"] = event["starts_at"]
        changes["all_day"] = 0
    text = (event.get("description") or "").strip()
    if text and text not in (current.get("description") or ""):
        changes["description"] = ((current.get("description") or "").rstrip()
                                  + "\n\n" + text).strip()
    if int(event.get("confidence") or 0) > int(current.get("confidence") or 0):
        for k in ("confidence", "tier", "has_food", "reason", "venue_class",
                  "rsvp_required", "restricted"):
            if k in event:
                changes[k] = int(bool(event[k])) if k in (
                    "has_food", "rsvp_required", "restricted") else event[k]
        for k in ("meals", "signals"):
            if k in event:
                changes[k] = json.dumps(event[k] or [])
    if int(event.get("network_score") or 0) > int(current.get("network_score") or 0):
        changes["network_score"] = int(event["network_score"])
        for k in ("network_kind", "network_reason"):
            if k in event:
                changes[k] = event[k]
        for k in ("firms", "practice_areas"):
            if k in event:
                changes[k] = json.dumps(event[k] or [])
    sources = set((current.get("source") or "").split(",")) | {event.get("source") or ""}
    merged = ",".join(sorted(s for s in sources if s))
    if merged != current.get("source"):
        changes["source"] = merged
    # A person's ruling still outranks every source, and a ruled start
    # owns the all-day flag with it.
    ruled = store.corrected_fields(conn, current["id"])
    if "starts_at" in ruled:
        ruled = ruled | {"all_day"}
    for k in ruled:
        changes.pop(k, None)
    if changes:
        changes["last_seen"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        sets = ", ".join(f"{k} = ?" for k in changes)
        conn.execute(f"UPDATE events SET {sets} WHERE id = ?",
                     list(changes.values()) + [current["id"]])
    conn.execute(
        "INSERT OR REPLACE INTO merges (kept_id, title, source, starts_at, "
        "reasons, at) VALUES (?, ?, ?, ?, ?, ?)",
        (current["id"], event.get("title") or "", event.get("source") or "",
         event.get("starts_at") or "", "; ".join(reasons),
         datetime.now(timezone.utc).isoformat(timespec="seconds")))
    conn.commit()
    return current["id"]


def add(conn, event: dict):
    """Store a sighting: folded into the event it repeats, or added.

    Returns ("merged", id, reasons) or (store.upsert's verdict, id, []).
    """
    exact = store.stored_id(conn, event)
    if exact:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (exact,)).fetchone()
        # The email's own title, on the same day: the store counts that as the
        # same event, so it folds like any other match. Upserted instead, the
        # post wrote its own time over the email's -- "6-8 PM" in the chat
        # moved a 5:30 panel to 6. Only a post stored alone is upserted.
        if row is not None and not secondhand(row["source"]):
            reasons = same_event(event, dict(row)) or ["same title, same day"]
            return "merged", fold(conn, row, event, reasons), reasons
    else:
        row, reasons = find(conn, event)
        if row is not None:
            return "merged", fold(conn, row, event, reasons), reasons
    verdict = store.upsert(conn, event)
    return verdict, store.stored_id(conn, event), []


def fold_secondhand(conn) -> int:
    """Fold every listing known only from a secondhand source into another
    source's listing of the same event, once one exists. Returns how many."""
    folded = 0
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM events WHERE NOT COALESCE(dismissed, 0) "
        "AND COALESCE(starts_at, '') != ''")]
    for event in rows:
        if not secondhand(event["source"]):
            continue
        row, reasons = find(conn, event, among_secondhand=False)
        if row is None:
            continue
        kept = fold(conn, row, _sighting(event), reasons)
        # A ruling made on the post now applies to the event it was.
        for field in store.corrected_fields(conn, event["id"]):
            if field not in store.corrected_fields(conn, kept):
                conn.execute("UPDATE corrections SET event_id = ? "
                             "WHERE event_id = ? AND field = ?",
                             (kept, event["id"], field))
        conn.execute("UPDATE flyers SET event_id = ? WHERE event_id = ?",
                     (kept, event["id"]))
        conn.execute("DELETE FROM events WHERE id = ?", (event["id"],))
        conn.commit()
        folded += 1
    return folded


def fold_duplicates(conn) -> int:
    """Hide each first-hand listing that is a twin of another (`twins`),
    folded into it. Returns how many.

    The copy is hidden, not deleted: its email is read again on every
    refresh, and a deleted row would simply come back. The listing kept is
    the one a person has ruled on, else the one with a flyer, else the one
    seen first, so the choice does not change from day to day. A listing
    with a ruling of its own is never hidden; a fold that was wrong is
    undone for good with `./fff correct <id> dismissed 0 "<why>"`. Each fold
    is recorded in the `merges` table, for `./fff merges`.
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM events WHERE NOT COALESCE(dismissed, 0) "
        "AND NOT COALESCE(all_day, 0) AND COALESCE(starts_at, '') != '' "
        "AND starts_at >= date('now', '-1 day')")]
    rows = [r for r in rows if not secondhand(r["source"])]
    fields = {r["id"]: store.corrected_fields(conn, r["id"]) for r in rows}
    # Ruled back onto the page is ruled a separate event: kept out entirely.
    rows = [r for r in rows if "dismissed" not in fields[r["id"]]]
    ruled = {r["id"] for r in rows if fields[r["id"]]}
    rows.sort(key=lambda r: (r["id"] not in ruled, not r.get("flyer_id"),
                             r.get("first_seen") or "", r["id"]))
    hidden = set()
    for i, keep in enumerate(rows):
        if keep["id"] in hidden:
            continue
        # A copy may match the kept listing only through another copy: the
        # flyer's full title and a short one, both twins of the plain name.
        group = [keep]
        grew = True
        while grew:
            grew = False
            for copy in rows[i + 1:]:
                if copy["id"] in hidden or copy["id"] in ruled:
                    continue
                reasons = next((r for r in (twins(m, copy) for m in group) if r), [])
                if not reasons:
                    continue
                group.append(copy)
                grew = True
                current = conn.execute("SELECT * FROM events WHERE id = ?",
                                       (keep["id"],)).fetchone()
                fold(conn, current, _sighting(copy), reasons)
                conn.execute("UPDATE flyers SET event_id = ? WHERE event_id = ?",
                             (keep["id"], copy["id"]))
                conn.execute("UPDATE events SET dismissed = 1 WHERE id = ?",
                             (copy["id"],))
                conn.commit()
                hidden.add(copy["id"])
    return len(hidden)


def _sighting(row: dict) -> dict:
    """A stored row in the shape `fold` reads, lists decoded."""
    event = dict(row)
    for k in ("meals", "signals", "firms", "practice_areas"):
        try:
            event[k] = json.loads(row.get(k) or "[]")
        except ValueError:
            event[k] = []
    return event
