"""The Gmail searches that fill data/maildrop/, built from the vetting rules.

The routine fetches mail through the Gmail connector, which only works inside
a Claude session, so the pipeline cannot search Gmail itself. What it can do
is say exactly what to search for. The searches here are derived from the same
rules `senders.py` uses to decide what may be read, so the two cannot drift:

* the school's own domains (``DOMAINS`` in its profile, subdomains and all),
* its outbound broadcast platforms (``BROADCAST_DOMAINS``),
* at a law school, the firm domains ``senders.firm_domains()`` derives from
  data/firms.json,
* every sender and mailing list added with ``./fff sources``.

A school with no profile in schools/ gets only the last.

A search is a superset. Gmail cannot tell a netID from an office or a reply
from an announcement, so ``select`` applies the rest of senders.py's envelope
rules to the search results before anything is fetched: mail the vetting
would refuse on sight is never written to disk.

The window runs from the last successful pull, recorded in data/mail-pull.json
by ``--done`` once every message has been saved, minus a day of overlap
(Gmail reads ``after:`` dates as midnight Pacific; anything fetched twice is
skipped by message id). With no record it looks back seven days.

A message is fetched whole -- the original, HTML and headers -- when the
connector can hand it over as a file: every flyer broadcast the profile
names (Georgetown's OSL Weekly Broadcast), whose flyers are images plain
text loses, and anything else of ``RAW_MIN_BYTES``
or more. Smaller originals come back inline as base64 that would have to be
copied out by hand, so those are read as plain text, which keeps every word.
``--select`` says which is which, and ``--broadcasts`` says which saved
broadcasts ./fff broadcast has not read yet, so none is processed twice.

    ./fff mailquery                    the searches, one per line
    ./fff mailquery --select FILE      which messages to fetch (JSON in, JSON out)
    ./fff mailquery --broadcasts EML...  saved broadcasts not yet processed
    ./fff mailquery --done             record a successful pull
"""

from __future__ import annotations

import email.utils
import json
import os
import sys
from datetime import datetime, timedelta

import school
import senders
import sources
import store

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, "data", "mail-pull.json")
MAILDROP = os.path.join(ROOT, "data", "maildrop")

LOOKBACK = timedelta(days=7)
# Gmail's after: takes a date and reads it as midnight Pacific, so a pull at
# 9 am Eastern would otherwise lose the small hours of the day it ran.
OVERLAP = timedelta(days=1)

# Kept well inside what Gmail accepts. The connector sends the query in a
# request URL, and percent-encoding roughly doubles the braces, colons and
# spaces that make up most of it.
MAX_QUERY_LEN = 900

PREFIX = "after:{date} -in:sent -in:chats"

# The connector saves a RAW result to a file only when it is too big to
# return inline: measured on 10 Oct 2026, a 52 KB message was saved and a
# 30 KB one came back as ~40,000 characters of base64. Set above the line,
# with a fallback to plain text in the routine for anything that still comes
# back inline. Mailchimp newsletters, the OSL broadcast and the big
# invitations clear it; CampusGroups posts (20-30 KB) do not.
RAW_MIN_BYTES = 60_000


# --- the window ------------------------------------------------------------

def last_pull(path: str = STATE):
    """When the last pull finished, or None if there is no usable record."""
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh).get("last_pull")
        return datetime.fromisoformat(value)
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def record_pull(when: datetime = None, path: str = STATE) -> datetime:
    when = when or datetime.now().astimezone()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"last_pull": when.isoformat(timespec="seconds")}, fh)
        fh.write("\n")
    os.replace(tmp, path)
    return when


def since(now: datetime = None, path: str = STATE):
    """(date to search after, the pull it continues from or None)."""
    now = now or datetime.now().astimezone()
    previous = last_pull(path)
    start = previous if previous is not None else now - LOOKBACK
    return (start - OVERLAP).date(), previous


# --- the senders -----------------------------------------------------------

def allowed_senders(conn) -> list:
    """Senders you added, and any allowed by hand in the register."""
    rows = conn.execute(
        "SELECT address FROM senders WHERE verdict = 'allow' ORDER BY address")
    found = {r["address"] for r in rows if "@" in (r["address"] or "")}
    return sorted(found | set(sources.load()["senders"]))


def list_terms() -> list:
    """Every way Gmail can find a post to a list you added.

    `to:` and `cc:` for a list that addresses its posts to itself, `list:` for
    one that only says so in its headers, `from:` for one that rewrites the
    sender to the list's own address.
    """
    out = []
    for address in sources.load()["lists"]:
        out += [f"from:{address}", f"to:{address}", f"cc:{address}",
                f"list:{address}"]
    return out


def terms(conn=None, firm_labels=None) -> list:
    """Every search term, in a stable order and without repeats.

    A school with no profile has none of the first three, so only the
    senders and lists you added are searched for.
    """
    profile = school.active()
    out = ["from:" + d for d in profile.DOMAINS]
    out += ["from:" + d for d in sorted(profile.BROADCAST_DOMAINS)]
    if profile.LAW_FIRM_MAIL:
        labels = (sorted(senders.firm_domains()) if firm_labels is None
                  else sorted(firm_labels))
        out += ["from:%s.com" % label for label in labels if label]
    if conn is not None:
        out += ["from:" + a for a in allowed_senders(conn)]
    else:
        out += ["from:" + a for a in sources.load()["senders"]]
    out += list_terms()
    return list(dict.fromkeys(out))


def queries(after, from_terms, max_len: int = MAX_QUERY_LEN) -> list:
    """The terms OR-ed together in as few searches as fit under max_len."""
    prefix = PREFIX.format(date=after.strftime("%Y/%m/%d"))
    out, group = [], []

    def render(items):
        return "%s {%s}" % (prefix, " ".join(items))

    for term in from_terms:
        if group and len(render(group + [term])) > max_len:
            out.append(render(group))
            group = []
        group.append(term)
    if group:
        out.append(render(group))
    return out


# --- what to fetch ---------------------------------------------------------

def _addresses(value) -> list:
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    return [a.lower() for _, a in email.utils.getaddresses(value) if a]


def verdict_for(message: dict, conn=None, maildrop: str = MAILDROP):
    """(fetch?, reason) for one message from a search result.

    Only what senders.py would refuse from the envelope alone is skipped here.
    Anything that depends on the body -- bulk markers, private content, the
    personal fail-safe -- is decided at ingest, as it always was.
    """
    mid = str(message.get("id") or "").strip()
    labels = set(message.get("labelIds") or message.get("labels") or [])
    if not mid:
        return False, "no message id"
    if labels & {"SENT", "DRAFT"}:
        return False, "your own message"
    if os.path.exists(os.path.join(maildrop, "gm-%s.eml" % mid)):
        return False, "already in the maildrop"

    sender = senders.address_of(message.get("sender") or message.get("from") or "")
    if not sender:
        return False, "no sender"
    standing = sources.standing(conn, sender)
    if standing == "deny":
        return False, "refused by hand (./fff senders deny)"

    to = (_addresses(message.get("toRecipients") or message.get("to"))
          + _addresses(message.get("ccRecipients"))
          + _addresses(message.get("bccRecipients")))
    listed = sources.envelope_list(sender, to)
    listserv = bool(listed) or any(senders.is_listserv(a) for a in to)
    broadcast = senders.is_broadcast_domain(sender)

    if senders.looks_personal(sender) and standing != "allow" and not listed:
        return False, "sent by a person, not an organisation"
    if not (broadcast or listserv or senders.is_school(sender)
            or senders.firm_recruiting(sender) or standing == "allow"):
        return False, "not a sender the vetting accepts"
    subject = message.get("subject") or ""
    hit = senders.PRIVATE_SUBJECT.search(subject)
    if hit and not listserv:
        return False, "addressed to one person (%r)" % hit.group(0).strip()
    return True, "fetch"


def _is_broadcast(message: dict) -> bool:
    """broadcast.py's own test, applied to a search result's envelope."""
    from email.message import Message
    from ingest.broadcast import is_broadcast
    msg = Message()
    msg["From"] = " ".join(str(message.get("sender") or "").split())
    msg["Subject"] = " ".join(str(message.get("subject") or "").split())
    return is_broadcast(msg)


def select(messages: list, conn=None, maildrop: str = MAILDROP) -> dict:
    """What to fetch, and how.

    ``{"fetch": [ids], "raw": [ids], "broadcasts": [ids],
    "skipped": [{"id", "reason"}]}``. ``raw`` is the part of ``fetch`` to
    read whole (RAW) -- every broadcast, and anything of ``RAW_MIN_BYTES`` or
    more; the rest is read as plain text. ``broadcasts`` is the part of
    ``raw`` that ./fff broadcast reads.
    """
    fetch, raw, broadcasts, skipped, seen = [], [], [], [], set()
    for message in messages:
        mid = str(message.get("id") or "")
        if mid in seen:
            continue
        seen.add(mid)
        ok, why = verdict_for(message, conn, maildrop)
        if ok and _is_broadcast(message):
            # A broadcast you exported by hand is saved under its own name,
            # so the maildrop check misses it; the subject, with its date,
            # is what broadcast.run records.
            if conn is not None and conn.execute(
                    "SELECT 1 FROM broadcasts WHERE subject = ?",
                    ((message.get("subject") or "")[:200],)).fetchone():
                ok, why = False, "broadcast already processed"
            else:
                broadcasts.append(mid)
                raw.append(mid)
        if ok:
            fetch.append(mid)
            try:
                size = int(message.get("sizeEstimate") or 0)
            except (TypeError, ValueError):
                size = 0
            if mid not in raw and size >= RAW_MIN_BYTES:
                raw.append(mid)
        else:
            skipped.append({"id": mid, "reason": why})
    return {"fetch": fetch, "raw": raw, "broadcasts": broadcasts,
            "skipped": skipped}


def unprocessed_broadcasts(paths: list, conn) -> list:
    """The saved files that are OSL broadcasts ./fff broadcast has not read.

    Keyed on the Message-ID, exactly as broadcast.run records it, so a copy
    you exported by hand and one fetched here are the same broadcast.
    """
    from ingest.broadcast import is_broadcast
    out = []
    for path in paths:
        with open(path, "rb") as fh:
            msg = email.message_from_bytes(fh.read())
        if not is_broadcast(msg):
            continue
        mid = (msg.get("Message-ID") or os.path.basename(path)).strip("<> ")
        done = conn.execute("SELECT 1 FROM broadcasts WHERE message_id = ?",
                            (mid,)).fetchone()
        if not done:
            out.append(path)
    return out


def _flatten(payload) -> list:
    """Accept a list of messages or the connector's search result as-is."""
    if isinstance(payload, dict):
        payload = payload.get("threads") or payload.get("messages") or []
    out = []
    for item in payload or []:
        if isinstance(item, dict) and "messages" in item:
            out.extend(item["messages"])
        elif isinstance(item, dict):
            out.append(item)
    return out


def main(argv: list) -> int:
    if argv[:1] == ["--done"]:
        when = record_pull()
        print("recorded a successful pull at %s" % when.isoformat(timespec="seconds"))
        return 0
    conn = store.connect()
    if argv[:1] == ["--select"]:
        source = argv[1] if len(argv) > 1 else "-"
        raw = sys.stdin.read() if source == "-" else open(source, encoding="utf-8").read()
        print(json.dumps(select(_flatten(json.loads(raw)), conn), indent=1))
        return 0
    if argv[:1] == ["--broadcasts"]:
        for path in unprocessed_broadcasts(argv[1:], conn):
            print(path)
        return 0
    if argv:
        print("usage: ./fff mailquery [--select FILE | --broadcasts EML... | --done]",
              file=sys.stderr)
        return 2
    after, previous = since()
    found = queries(after, terms(conn))
    if not found:
        print("# nothing to search for: no school profile, and no sources added.\n"
              "# Choose a school in data/config.json (see schools/), or add a\n"
              "# sender or list with ./fff sources.", file=sys.stderr)
    for query in found:
        print(query)
    print("# %d searches for mail after %s (%s)" % (
        len(found), after.isoformat(),
        "last pull %s" % previous.isoformat(timespec="minutes") if previous
        else "no pull recorded; looking back 7 days"), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
