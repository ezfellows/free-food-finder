"""SQLite persistence for Free Food Finder.

One table, `events`, holding the merged view across every source. Ingesters
call `upsert()`; the web app and notifier read through `upcoming()`.

De-duplication matters here: the same pizza shows up in a listserv email, on
the org's page, and on the LiveWhale calendar. Events are keyed by a
fingerprint of normalised title + start date, so the three collapse into one
row and the richest description wins.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "events.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            TEXT PRIMARY KEY,
    fingerprint   TEXT NOT NULL,
    source        TEXT NOT NULL,
    source_id     TEXT,
    title         TEXT NOT NULL,
    description   TEXT,
    location      TEXT,
    org           TEXT,
    url           TEXT,
    starts_at     TEXT,
    ends_at       TEXT,
    all_day       INTEGER DEFAULT 0,
    venue_class   TEXT,
    confidence    INTEGER DEFAULT 0,
    tier          TEXT,
    has_food      INTEGER DEFAULT 0,
    meals         TEXT,
    reason        TEXT,
    signals       TEXT,
    rsvp_required INTEGER DEFAULT 0,
    restricted    INTEGER DEFAULT 0,
    network_score  INTEGER DEFAULT 0,
    network_kind   TEXT,
    firms          TEXT,
    practice_areas TEXT,
    network_reason TEXT,
    flyer_id      TEXT,
    rsvp_url      TEXT,
    first_seen    TEXT,
    last_seen     TEXT,
    dismissed     INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_start ON events(starts_at);
CREATE INDEX IF NOT EXISTS idx_fp    ON events(fingerprint);
CREATE INDEX IF NOT EXISTS idx_food  ON events(has_food, starts_at);

-- Flyer images pulled out of broadcast emails. One row per distinct image,
-- keyed by a hash of the bytes, so the same flyer recurring across weekly
-- broadcasts is fetched and read exactly once.
CREATE TABLE IF NOT EXISTS flyers (
    id           TEXT PRIMARY KEY,      -- sha256(bytes)[:16]
    url          TEXT,                  -- CDN url it was last seen at
    message_id   TEXT,                  -- email of first sighting
    week_of      TEXT,                  -- that email's Date header
    byte_size    INTEGER,
    width        INTEGER,
    height       INTEGER,
    declared_w   INTEGER,               -- width= attribute in the email HTML
    kind         TEXT,                  -- flyer | logo | banner | unknown
    ocr_text     TEXT,
    ocr_conf     INTEGER DEFAULT 0,     -- 0-100, mean line confidence
    ocr_lines    TEXT,                  -- json, with boxes
    ocr_rev      INTEGER,               -- Vision revision, so an OS shift shows
    qr_payload   TEXT,                  -- raw, and NEVER fetched
    qr_count     INTEGER DEFAULT 0,
    rsvp_url     TEXT,                  -- http(s) only, scheme-checked
    rsvp_raw     TEXT,                  -- unresolved tracking wrapper, if any
    read_by      TEXT DEFAULT 'none',   -- none | vision | claude
    reading      TEXT,                  -- json: transcription from the review pass
    review_note  TEXT,                  -- why it was queued
    reviewed_at  TEXT,
    event_id     TEXT,
    first_seen   TEXT,
    last_seen    TEXT,
    seen_count   INTEGER DEFAULT 1,
    error        TEXT
);

-- A flyer can appear at several URLs across re-uploads; a URL maps to exactly
-- one flyer. This is what stops week two re-downloading 78 images.
CREATE TABLE IF NOT EXISTS flyer_urls (
    url        TEXT PRIMARY KEY,
    flyer_id   TEXT,
    first_seen TEXT,
    last_seen  TEXT,
    seen_weeks INTEGER DEFAULT 1
);

-- Broadcast emails already processed, so a re-run is cheap.
CREATE TABLE IF NOT EXISTS broadcasts (
    message_id   TEXT PRIMARY KEY,
    subject      TEXT,
    received_at  TEXT,
    images       INTEGER DEFAULT 0,
    flyers       INTEGER DEFAULT 0,
    events       INTEGER DEFAULT 0,
    processed_at TEXT
);

-- The verdict on one student submission. The posts themselves live inside the
-- published page, which republishes itself when someone submits; this records
-- only what was decided about them, so a rebuild knows which to carry, which
-- to promote into a real event, and which to drop.
CREATE TABLE IF NOT EXISTS submissions (
    post_id     TEXT PRIMARY KEY,
    by_name     TEXT,
    title       TEXT,
    starts_at   TEXT,
    verdict     TEXT DEFAULT '',   -- '', 'verified' or 'rejected'
    reason      TEXT,
    checked_at  TEXT,
    event_id    TEXT               -- the event it became, once promoted
);

-- A field a human has ruled on, and the last word on it.
--
-- The caption pass is deliberately repeatable, so anything it can derive it
-- re-derives every run. That is right when the sources are all it has, and
-- wrong the moment a person who was actually in the room says otherwise: a
-- plain UPDATE gets silently reverted the next morning. The APALSA First Look
-- was the case that showed it -- the email caption said 5:30, the flyer said
-- 6:00, the reader said 6:00, and the caption would have won again by the next morning's run.
--
-- These are re-asserted after every pass that can overwrite them, so they are
-- the last word regardless of what ran in between.
CREATE TABLE IF NOT EXISTS corrections (
    event_id   TEXT,
    field      TEXT,
    value      TEXT,
    why        TEXT,
    at         TEXT,
    PRIMARY KEY (event_id, field)
);

-- Every address the ingester has ever seen, with the evidence for and against
-- reading it. Kept because the alternative is a hand-maintained list of
-- org-sounding words, which goes stale every time a new student org forms and
-- fails silently -- the events simply never appear. Evidence accumulates
-- instead: a sender that keeps arriving through a mailing list is an
-- organisation whatever it calls itself.
CREATE TABLE IF NOT EXISTS senders (
    address      TEXT PRIMARY KEY,
    first_seen   TEXT,
    last_seen    TEXT,
    messages     INTEGER DEFAULT 0,
    bulk_seen    INTEGER DEFAULT 0,    -- times it carried bulk markers
    listserv_seen INTEGER DEFAULT 0,   -- times it wrote to a student-body list
    org_name     TEXT,                 -- name observed in its footers
    last_reason  TEXT,                 -- why it was last allowed or refused
    verdict      TEXT DEFAULT ''       -- '', 'allow' or 'deny', set by hand
);

CREATE TABLE IF NOT EXISTS ingest_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    source    TEXT,
    ran_at    TEXT,
    found     INTEGER,
    added     INTEGER,
    updated   INTEGER,
    error     TEXT
);

-- One row per email the pipeline has read, and what came of it -- so "did
-- it catch X?" is answered by `./fff maillog`, not by re-reading the inbox.
-- Rewritten on every refresh, which re-reads the whole maildrop. Local only:
-- nothing here is published. Mail the search turned up but never fetched
-- (a classmate, a reply) is not in it at all.
CREATE TABLE IF NOT EXISTS mail_log (
    mail_key    TEXT PRIMARY KEY,  -- Gmail id, else Message-ID, else file name
    file        TEXT,
    received    TEXT,              -- the message's own Date
    sender      TEXT,
    org         TEXT,              -- who it is from, as the page credits it
    subject     TEXT,
    saved_as    TEXT,              -- 'original' or 'plain text'
    verdict     TEXT,              -- 'admitted', 'refused' or 'broadcast'
    reason      TEXT,
    events      TEXT,              -- JSON list of the event ids it fed
    below       INTEGER DEFAULT 0, -- events found but under the listing bar
    first_seen  TEXT,
    last_seen   TEXT
);
"""

_STOP = re.compile(r"\b(the|a|an|and|with|for|of|at|on|in|to|our|your|annual|\d{4})\b", re.I)

# Networking kinds worth surfacing: someone you could usefully talk to is
# expected to be present. "academic" and "none" are excluded.
NETWORK_KINDS = ("recruiting", "firm", "practitioner", "alumni")


def connect(path: str = None) -> sqlite3.Connection:
    """Open the database, creating the schema if needed.

    `path` is resolved at call time, not bound as a default: a default
    argument would capture DB_PATH at import and silently ignore any later
    reassignment, which is exactly how the test suite ended up writing into
    the live database.
    """
    # `FFF_DB` points a subprocess somewhere else -- a test running `./fff`
    # as a command cannot reassign DB_PATH from outside. Unset in normal use.
    path = path or os.environ.get("FFF_DB") or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# Columns added after the first release. CREATE TABLE IF NOT EXISTS silently
# leaves an existing table alone, so new columns have to be added by hand.
_ADDED_COLUMNS = {
    "network_score": "INTEGER DEFAULT 0",
    "network_kind": "TEXT",
    "firms": "TEXT",
    "practice_areas": "TEXT",
    "network_reason": "TEXT",
    "flyer_id": "TEXT",
    "rsvp_url": "TEXT",
    # Who posted it, when a student did. Set only on promoted submissions, so
    # it doubles as the flag that an event came from a person rather than a
    # feed -- which is what the page tags and what `dedupe_slots` must not
    # treat as a duplicate of the scraped event it corroborates.
    "submitted_by": "TEXT",
}

# Columns added to `flyers` after its first release. The loop below was
# written before there was anything to put in it, so adding one is one line.
_FLYER_COLUMNS = {
    # The organiser's own line of text under their flyer in the email. Typed,
    # not OCR'd, so it is the authority on when and where.
    "caption": "TEXT",
}


def _migrate(conn) -> None:
    have = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    for column, decl in _ADDED_COLUMNS.items():
        if column not in have:
            conn.execute(f"ALTER TABLE events ADD COLUMN {column} {decl}")
    if _FLYER_COLUMNS:
        have = {r[1] for r in conn.execute("PRAGMA table_info(flyers)")}
        for column, decl in _FLYER_COLUMNS.items():
            if column not in have:
                conn.execute(f"ALTER TABLE flyers ADD COLUMN {column} {decl}")
    # venue_class used Georgetown's own names for its campuses. It now names
    # them by role, as campus.py does: the docket's campus and another one.
    conn.execute("UPDATE events SET venue_class = 'home' WHERE venue_class = 'law'")
    conn.execute("UPDATE events SET venue_class = 'away' WHERE venue_class = 'main'")
    # Indexes on migrated columns must be created here, after the ALTERs.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_net "
                 "ON events(network_score, starts_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_flyer ON events(flyer_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_flyer_event "
                 "ON flyers(event_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_flyer_queue "
                 "ON flyers(read_by, kind)")
    conn.commit()


def fingerprint(title: str, starts_at: str) -> str:
    """Stable key for the same real-world event seen through different sources."""
    t = _STOP.sub(" ", (title or "").lower())
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = " ".join(sorted(t.split()))          # word order varies between sources
    day = (starts_at or "")[:10]
    return hashlib.sha1(f"{t}|{day}".encode()).hexdigest()[:16]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert(conn: sqlite3.Connection, ev: dict) -> str:
    """Insert or merge one scored event. Returns 'added' or 'updated'."""
    fp = fingerprint(ev.get("title", ""), ev.get("starts_at", ""))
    eid = ev.get("id") or fp
    row = conn.execute("SELECT * FROM events WHERE fingerprint = ?", (fp,)).fetchone()
    # An event's id is the fingerprint it was first seen under. A ruling on
    # its title moves the fingerprint on, so the next time the same email is
    # read its fingerprint finds nothing -- and inserting under the old id
    # failed on the primary key and took the whole mail pass down with it.
    # Retitling the Antitrust Association's "Event featuring Ropes & Gray" was
    # enough to break every refresh after it. The id still names the event.
    if row is None:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
    now = _now()
    payload = {
        "id": eid, "fingerprint": fp,
        "source": ev.get("source", ""), "source_id": ev.get("source_id", ""),
        "title": ev.get("title", ""), "description": ev.get("description", ""),
        "location": ev.get("location", ""), "org": ev.get("org", ""),
        "url": ev.get("url", ""), "starts_at": ev.get("starts_at"),
        "ends_at": ev.get("ends_at"), "all_day": int(bool(ev.get("all_day"))),
        "venue_class": ev.get("venue_class", "unknown"),
        "confidence": int(ev.get("confidence", 0)), "tier": ev.get("tier", "unlikely"),
        "has_food": int(bool(ev.get("has_food"))),
        "meals": json.dumps(ev.get("meals") or []),
        "reason": ev.get("reason", ""), "signals": json.dumps(ev.get("signals") or []),
        "rsvp_required": int(bool(ev.get("rsvp_required"))),
        "restricted": int(bool(ev.get("restricted"))),
        "network_score": int(ev.get("network_score", 0)),
        "network_kind": ev.get("network_kind", "none"),
        "firms": json.dumps(ev.get("firms") or []),
        "practice_areas": json.dumps(ev.get("practice_areas") or []),
        "network_reason": ev.get("network_reason", ""),
        "flyer_id": ev.get("flyer_id", ""),
        "rsvp_url": ev.get("rsvp_url", ""),
        "last_seen": now,
    }

    if row is None:
        payload["first_seen"] = now
        cols = ", ".join(payload)
        conn.execute(f"INSERT INTO events ({cols}) VALUES ({', '.join('?' * len(payload))})",
                     list(payload.values()))
        conn.commit()
        return "added"

    # Merge: keep the most confident verdict and the longest description, so a
    # terse calendar stub gets enriched by the listserv email that explains it.
    if row["confidence"] >= payload["confidence"]:
        for k in ("confidence", "tier", "has_food", "meals", "reason", "signals",
                  "venue_class", "rsvp_required", "restricted"):
            payload[k] = row[k]
    # Networking is judged independently of food, so it merges on its own
    # score: a richer email can raise the networking read without disturbing
    # an already-confident food verdict, and vice versa.
    if (row["network_score"] or 0) >= payload["network_score"]:
        for k in ("network_score", "network_kind", "firms", "practice_areas",
                  "network_reason"):
            payload[k] = row[k]
    if len(row["description"] or "") > len(payload["description"] or ""):
        payload["description"] = row["description"]
    # Fill-if-empty: a calendar entry merging into a flyer event must not
    # blank the flyer or the RSVP link it arrived with.
    for k in ("location", "url", "org", "ends_at", "flyer_id", "rsvp_url"):
        if not payload.get(k) and row[k]:
            payload[k] = row[k]
    if row["source"] and payload["source"] not in (row["source"] or "").split(","):
        payload["source"] = ",".join(sorted(set((row["source"] or "").split(",")
                                                + [payload["source"]])))
    # A person's ruling outranks what the source says again. Writing the
    # source's value back only for `apply_corrections` to undo it made every
    # refresh report the same rulings as changes, and left the row wrong in
    # between.
    ruled = corrected_fields(conn, row["id"]) & set(payload)
    for k in ruled:
        payload[k] = row[k]
    if ruled & {"title", "starts_at"}:
        payload["fingerprint"] = row["fingerprint"]
    payload["id"] = row["id"]
    sets = ", ".join(f"{k} = ?" for k in payload if k != "id")
    conn.execute(f"UPDATE events SET {sets} WHERE id = ?",
                 [v for k, v in payload.items() if k != "id"] + [payload["id"]])
    conn.commit()
    return "updated"


def upcoming(conn, days: int = 14, min_tier: str = "likely",
             venue: str = "any", include_dismissed: bool = False,
             mode: str = "food", min_network: int = 34) -> list:
    """Events from now forward, soonest first.

    `mode` picks the axis: "food" applies the food tier, "network" the
    networking threshold, and "both" returns anything qualifying on either.
    The two judgements never gate each other -- an info session with no food
    is still worth knowing about.
    """
    order = {"confirmed": 3, "likely": 2, "possible": 1, "unlikely": 0}
    floor = order.get(min_tier, 2)
    now = datetime.now()
    horizon = (now + timedelta(days=days)).isoformat()
    # Bound from two hours ago, not midnight: an event that started recently
    # may still have food out, but one that ended this morning is not
    # "upcoming" and reads as "past" in the UI, which helps nobody.
    floor_time = (now - timedelta(hours=2)).isoformat()
    sql = ("SELECT * FROM events WHERE starts_at IS NOT NULL "
           "AND starts_at >= ? AND starts_at <= ?")
    args = [floor_time, horizon]
    if not include_dismissed:
        sql += " AND dismissed = 0"
    rows = conn.execute(sql + " ORDER BY starts_at ASC", args).fetchall()

    out = []
    for r in rows:
        d = dict(r)
        food_ok = order.get(d["tier"], 0) >= floor
        # Only the professional kinds count as networking. An academic
        # colloquium has incidental networking value but showing it here
        # would bury the events where lawyers are actually in the room.
        net_ok = ((d["network_score"] or 0) >= min_network
                  and (d["network_kind"] or "none") in NETWORK_KINDS)
        if mode == "food" and not food_ok:
            continue
        if mode == "network" and not net_ok:
            continue
        if mode == "both" and not (food_ok or net_ok):
            continue
        if venue == "home" and d["venue_class"] == "away":
            continue
        d["meals"] = json.loads(d["meals"] or "[]")
        d["signals"] = json.loads(d["signals"] or "[]")
        d["firms"] = json.loads(d["firms"] or "[]")
        d["practice_areas"] = json.loads(d["practice_areas"] or "[]")
        d["matches_food"] = food_ok
        d["matches_network"] = net_ok
        out.append(d)
    return out


def log_run(conn, source: str, found=0, added=0, updated=0, error=None) -> None:
    conn.execute("INSERT INTO ingest_log (source, ran_at, found, added, updated, error) "
                 "VALUES (?,?,?,?,?,?)", (source, _now(), found, added, updated, error))
    conn.commit()


def note_mail(conn, entry: dict) -> None:
    """Record what became of one email, keeping when it was first seen."""
    now = _now()
    conn.execute(
        "INSERT INTO mail_log (mail_key, file, received, sender, org, subject, "
        "saved_as, verdict, reason, events, below, first_seen, last_seen) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(mail_key) DO UPDATE SET "
        "file=excluded.file, received=excluded.received, sender=excluded.sender, "
        "org=excluded.org, subject=excluded.subject, saved_as=excluded.saved_as, "
        "verdict=excluded.verdict, reason=excluded.reason, events=excluded.events, "
        "below=excluded.below, last_seen=excluded.last_seen",
        (entry["key"], entry.get("file", ""), entry.get("received", ""),
         entry.get("sender", ""), entry.get("org", ""), entry.get("subject", ""),
         entry.get("saved_as", ""), entry.get("verdict", ""), entry.get("reason", ""),
         json.dumps(sorted(set(entry.get("events") or []))),
         int(entry.get("below") or 0), now, now))


def stored_id(conn, ev: dict) -> str:
    """The id of the row `upsert` wrote `ev` into, found the way it found it."""
    fp = fingerprint(ev.get("title", ""), ev.get("starts_at", ""))
    row = conn.execute("SELECT id FROM events WHERE fingerprint = ?", (fp,)).fetchone()
    if row is None:
        row = conn.execute("SELECT id FROM events WHERE id = ?",
                           (ev.get("id") or fp,)).fetchone()
    return row[0] if row else ""


# How long an event is kept once it has started. Every email is read again on
# every refresh, so one about a talk from six weeks ago was stored again each
# time, only for the same refresh to prune it.
KEEP_DAYS = 30


def expired(starts_at, older_than_days: int = KEEP_DAYS) -> bool:
    """Whether `prune` deletes an event that starts then."""
    cutoff = (datetime.now() - timedelta(days=older_than_days)).isoformat()
    # Compared as the DELETE compares them.
    return starts_at is not None and starts_at < cutoff


def prune(conn, older_than_days: int = KEEP_DAYS) -> int:
    cutoff = (datetime.now() - timedelta(days=older_than_days)).isoformat()
    cur = conn.execute("DELETE FROM events WHERE starts_at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


def _slot(event: dict):
    """The minute an event starts and the organisation holding it."""
    if not (event.get("org") or "").strip():
        return None
    return ("slot", (event.get("starts_at") or "")[:16],
            _STOP.sub(" ", event["org"].lower()).strip()[:24])


def _room(event: dict):
    """The same minute in the same room is the same event.

    The org key cannot catch a bulletin's own copy, because a relayed
    newsletter is credited to the relay: the AASV meeting is "Advocates
    Against Sexual Violence" on its flyer and "Office of Student Life" in
    the broadcast that carried it. Both say McDonough 200 at 15:30, and
    two different events cannot be in one room at one minute.
    """
    import campus
    where = campus.locate(event.get("location") or "")
    place = (where.get("place") or "").strip().lower()
    if not place or where.get("campus") != "home" or not where.get("room"):
        return None
    return ("room", (event.get("starts_at") or "")[:16], place)


def listings(conn) -> dict:
    """{slot or room: event id} for every listing a mail copy could repeat.

    Every source but mail, since mail is the one that restates. A dismissed
    listing holds nothing. When the flyer's copy is the one a person threw out
    -- MLS's Big Law Panel, GPLA's coffee talk and If/When/How's panel on
    10 Oct 2026, kept from the email instead -- it still held the slot, so
    the next refresh deleted the email's copy too and the event left the
    page altogether.
    """
    held = {}
    for row in conn.execute(
            "SELECT id, starts_at, org, location FROM events "
            "WHERE COALESCE(starts_at,'') != '' AND COALESCE(source,'') != 'mail' "
            "AND NOT COALESCE(dismissed, 0) ORDER BY starts_at"):
        event = dict(row)
        for key in (_slot(event), _room(event)):
            if key is not None:
                held.setdefault(key, event["id"])
    return held


def repeats(event: dict, held: dict) -> str:
    """The id of the listing a mail event repeats, from `listings`, or ''."""
    if not event.get("starts_at"):
        return ""
    for key in (_slot(event), _room(event)):
        if key in held:
            return held[key]
    return ""


def dedupe_slots(conn) -> int:
    """Drop a newsletter event that repeats one another source already has.

    `fingerprint` collapses duplicates by title and date, which works when two
    feeds print the same name. It cannot help when they do not: the ILSA
    general body meeting is "Interested in immigration & refugee law?" on its
    flyer and "joint GBM with IRAP" in the newsletter announcing it. Same org,
    same minute -- one event, listed twice, sharing no words.

    Only the **mail** copy is ever removed, and only when another source
    already holds that slot. The rule is deliberately one-directional: an
    earlier version of this matched any two events sharing a slot and an org,
    and since the calendar feed labels everything with the same placeholder
    org, it deleted unrelated talks that merely started at the same hour.
    A newsletter is the one source that restates what another already said.

    `ingest.mail.run` asks `repeats` before storing anything, so a copy is
    rarely stored at all. This removes one stored before the listing it
    repeats arrived.
    """
    held = listings(conn)
    removed = 0
    for row in conn.execute(
            "SELECT id, starts_at, org, location FROM events "
            "WHERE source = 'mail' AND COALESCE(starts_at,'') != ''").fetchall():
        if not repeats(dict(row), held):
            continue
        conn.execute("UPDATE flyers SET event_id = '' WHERE event_id = ?",
                     (row["id"],))
        conn.execute("DELETE FROM events WHERE id = ?", (row["id"],))
        removed += 1
    if removed:
        conn.commit()
    return removed


def note_sender(conn, address: str, *, bulk=False, listserv=False,
                org_name="", reason="") -> None:
    """Record that this address wrote, and what the evidence looked like."""
    address = (address or "").strip().lower()
    if not address:
        return
    now = _now()
    conn.execute(
        "INSERT INTO senders (address, first_seen, last_seen, messages, "
        "bulk_seen, listserv_seen, org_name, last_reason) "
        "VALUES (?,?,?,1,?,?,?,?) "
        "ON CONFLICT(address) DO UPDATE SET "
        "  last_seen = excluded.last_seen, "
        "  messages = messages + 1, "
        "  bulk_seen = bulk_seen + excluded.bulk_seen, "
        "  listserv_seen = listserv_seen + excluded.listserv_seen, "
        "  org_name = CASE WHEN COALESCE(senders.org_name,'') = '' "
        "             THEN excluded.org_name ELSE senders.org_name END, "
        "  last_reason = excluded.last_reason",
        (address, now, now, int(bool(bulk)), int(bool(listserv)),
         org_name or "", reason or ""))
    conn.commit()


def sender_verdict(conn, address: str) -> str:
    """'allow', 'deny' or '' -- a standing decision made by hand."""
    row = conn.execute("SELECT verdict FROM senders WHERE address = ?",
                       ((address or "").strip().lower(),)).fetchone()
    return (row["verdict"] or "") if row else ""


def set_sender_verdict(conn, address: str, verdict: str) -> None:
    address = (address or "").strip().lower()
    if verdict not in ("allow", "deny", ""):
        raise ValueError("verdict must be allow, deny or empty")
    now = _now()
    conn.execute(
        "INSERT INTO senders (address, first_seen, last_seen, verdict) "
        "VALUES (?,?,?,?) ON CONFLICT(address) DO UPDATE SET "
        "verdict = excluded.verdict", (address, now, now, verdict))
    conn.commit()


def known_senders(conn, only_refused=False) -> list:
    rows = conn.execute(
        "SELECT * FROM senders ORDER BY messages DESC, address").fetchall()
    if not only_refused:
        return rows
    return [r for r in rows
            if (r["verdict"] or "") != "allow"
            and "from a" not in (r["last_reason"] or "")]


def note_submission(conn, post: dict, verdict: str = "", reason: str = "",
                    event_id: str = "") -> None:
    """Record what was decided about a submission, and why."""
    post_id = str(post.get("id") or "").strip()
    if not post_id:
        return
    conn.execute(
        "INSERT INTO submissions (post_id, by_name, title, starts_at, "
        "verdict, reason, checked_at, event_id) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(post_id) DO UPDATE SET "
        "  verdict = excluded.verdict, reason = excluded.reason, "
        "  checked_at = excluded.checked_at, "
        "  event_id = CASE WHEN excluded.event_id != '' "
        "             THEN excluded.event_id ELSE submissions.event_id END",
        (post_id, (post.get("by") or "")[:60], (post.get("title") or "")[:120],
         post.get("start") or "", verdict, reason or "", _now(), event_id))
    conn.commit()


# What a person is allowed to overrule.
#
# Two kinds. The first four are what a reader can see is wrong by standing in
# the room. The last three are what the sources never said: an organiser tells
# you there will be food though the flyer does not mention it, or the entry
# names no host because the bulletin put it in an index instead.
#
# Correcting a score to paper over a scorer bug would hide the bug -- so a
# food correction must carry a `why`, and that `why` is shown to readers in
# place of a quoted phrase, because there is no phrase to quote. The reader
# can then weigh "reported by the organiser" for what it is.
CORRECTABLE = ("starts_at", "ends_at", "location", "title",
               "org", "has_food", "tier", "dismissed")

# Correcting these is a claim about the world, not a reading of the text, so
# it has to be attributed.
#
# `dismissed` belongs here too. Deleting a badly extracted event does not
# stick: its source email is re-read on every refresh and the event comes
# straight back -- four did, three days after they were deleted. A dismissal
# recorded as a correction is re-asserted after every pass instead.
NEEDS_REASON = ("has_food", "tier", "dismissed")


def record_correction(conn, event_id: str, field: str, value: str,
                      why: str = "") -> None:
    """Record a human ruling on one field, and apply it now."""
    if field not in CORRECTABLE:
        raise ValueError("%s is not correctable; try one of %s"
                         % (field, ", ".join(CORRECTABLE)))
    if field in NEEDS_REASON and not (why or "").strip():
        raise ValueError(
            "%s needs a reason: it is a claim about the world rather than a "
            "reading of the text, and readers are shown where it came from"
            % field)
    conn.execute(
        "INSERT INTO corrections (event_id, field, value, why, at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(event_id, field) DO UPDATE SET "
        "value = excluded.value, why = excluded.why, at = excluded.at",
        (event_id, field, value, why, datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    apply_corrections(conn, event_id)


def correction_reason(conn, event_id: str, fields=()) -> str:
    """Why a person overruled one of `fields` on this event, if they did."""
    for row in conn.execute(
            "SELECT field, why FROM corrections WHERE event_id = ?",
            (event_id,)):
        if row["field"] in fields and (row["why"] or "").strip():
            return row["why"].strip()
    return ""


def corrected_fields(conn, event_id: str) -> set:
    """Which fields on this event a person has already ruled on."""
    return {r["field"] for r in conn.execute(
        "SELECT field FROM corrections WHERE event_id = ?", (event_id,))}


def apply_corrections(conn, event_id: str = "") -> int:
    """Re-assert every recorded ruling. Safe to repeat; run it last.

    Returns the number of fields that actually differed, so a caller can say
    whether a pass had tried to overwrite one.
    """
    sql = "SELECT * FROM corrections"
    args = []
    if event_id:
        sql += " WHERE event_id = ?"
        args = [event_id]
    applied = 0
    for row in conn.execute(sql, args).fetchall():
        if row["field"] not in CORRECTABLE:
            continue                       # a field retired since it was set
        event = conn.execute("SELECT * FROM events WHERE id = ?",
                             (row["event_id"],)).fetchone()
        # Compared as text. An integer column read back as 1 never equalled
        # the stored "1", so every correction to has_food was re-applied --
        # and reported as a change -- on every single pass.
        if event is None or str(event[row["field"]] if event[row["field"]]
                                is not None else "") == str(row["value"] or ""):
            continue
        changes = {row["field"]: row["value"]}
        # Same reason the caption pass recomputes it: the fingerprint is built
        # from the title and the start date, and a row whose fingerprint no
        # longer matches its own contents stops merging with the calendar's
        # copy of the same event and lands beside it instead.
        if row["field"] in ("title", "starts_at"):
            fresh = fingerprint(
                row["value"] if row["field"] == "title" else (event["title"] or ""),
                row["value"] if row["field"] == "starts_at" else (event["starts_at"] or ""))
            taken = conn.execute(
                "SELECT 1 FROM events WHERE fingerprint = ? AND id != ?",
                (fresh, event["id"])).fetchone()
            if not taken:
                changes["fingerprint"] = fresh
        sets = ", ".join(f"{k} = ?" for k in changes)
        conn.execute(f"UPDATE events SET {sets} WHERE id = ?",
                     list(changes.values()) + [event["id"]])
        applied += 1
    if applied:
        conn.commit()
    return applied


def submission_verdicts(conn) -> dict:
    """{post_id: dict} for every submission a verdict has been recorded on.

    Plain dicts, not `sqlite3.Row`. A Row indexes like a mapping but has no
    `.get`, and the build reaches for `.get("verdict")` on a post nobody has
    ruled on -- so the first rejection ever recorded crashed the build rather
    than dropping the post.
    """
    return {r["post_id"]: dict(r)
            for r in conn.execute("SELECT * FROM submissions")}
