"""Every email the pipeline has read, and what came of it.

The answer to "did it catch X?" without opening the inbox: which emails were
saved whole and which as plain text, which the vetting admitted or refused and
why, and which events each one fed. Built by `./fff refresh` from the maildrop
(see `ingest.mail.run`); mail the search turned up but never fetched -- a
classmate, a reply -- is not in it.

    ./fff maillog              the last 14 days, newest first
    ./fff maillog 30           the last 30 days
    ./fff maillog --empty      admitted, but nothing listed from it
    ./fff maillog --refused    refused, with the reason
    ./fff maillog <words>      only mail whose sender or subject has them
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta

import store

SHOWN = 6          # event titles listed under one email before "+N more"


def _when(iso: str):
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def rows(conn, days: int = 14, only: str = "", words: str = "") -> list:
    cutoff = datetime.now().astimezone() - timedelta(days=days)
    out = []
    for row in conn.execute("SELECT * FROM mail_log"):
        r = dict(row)
        when = _when(r["received"])
        if when is not None and when < cutoff:
            continue
        r["ids"] = json.loads(r["events"] or "[]")
        if only == "refused" and r["verdict"] != "refused":
            continue
        if only == "empty" and not (r["verdict"] == "admitted" and not r["ids"]):
            continue
        hay = " ".join((r["sender"] or "", r["org"] or "", r["subject"] or "")).lower()
        if words and not all(w in hay for w in words.lower().split()):
            continue
        r["when"] = when
        out.append(r)
    out.sort(key=lambda r: r["when"] or datetime.min.astimezone(), reverse=True)
    return out


def _events(conn, ids: list) -> list:
    out = []
    for eid in ids:
        ev = conn.execute("SELECT title, starts_at, dismissed FROM events "
                          "WHERE id = ?", (eid,)).fetchone()
        if ev is None:
            out.append("(merged into another listing)")
            continue
        start = _when(ev["starts_at"])
        label = "%s (%s)" % (ev["title"][:60],
                             start.strftime("%-d %b") if start else "no date")
        out.append(label + (" -- dismissed" if ev["dismissed"] else ""))
    return out


def summary(found: list) -> str:
    admitted = [r for r in found if r["verdict"] == "admitted"]
    return ("%d emails -- %d saved whole, %d as plain text; %d admitted "
            "(%d listed nothing), %d refused, %d broadcasts" % (
                len(found), sum(r["saved_as"] == "original" for r in found),
                sum(r["saved_as"] == "plain text" for r in found), len(admitted),
                sum(not r["ids"] for r in admitted),
                sum(r["verdict"] == "refused" for r in found),
                sum(r["verdict"] == "broadcast" for r in found)))


def render(conn, found: list) -> str:
    lines = [summary(found), ""]
    for r in found:
        when = r["when"].strftime("%a %-d %b") if r["when"] else "no date"
        who = r["org"] or r["sender"] or "unknown sender"
        # CampusGroups sends every org's mail from one no-reply address, so
        # the address says nothing the org name does not.
        if (r["sender"] and r["org"] and r["sender"] not in r["org"]
                and not r["sender"].startswith("no-reply@")):
            who = "%s <%s>" % (r["org"], r["sender"])
        subject = " ".join((r["subject"] or "").split()) or "(no subject)"
        lines.append("%-10s %s" % (when, subject[:80]))
        if r["verdict"] == "refused":
            fate = "refused: %s" % r["reason"]
        elif r["ids"]:
            fate = "%s · %d event%s" % (r["verdict"], len(r["ids"]),
                                         "" if len(r["ids"]) == 1 else "s")
        else:
            fate = "%s · nothing listed" % (r["verdict"] or "not vetted")
        if r["below"]:
            fate += " (%d under the listing bar)" % r["below"]
        if r["verdict"] == "broadcast" and r["reason"]:
            fate += " · " + r["reason"]
        lines.append("%-10s %s · %s · %s" % ("", who[:64], r["saved_as"], fate))
        titles = _events(conn, r["ids"])
        if titles:
            more = len(titles) - SHOWN
            shown = " · ".join(titles[:SHOWN]) + (" · +%d more" % more if more > 0 else "")
            lines.append("%-10s %s" % ("", shown))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list) -> int:
    days, only, words = 14, "", []
    for arg in argv:
        if arg.isdigit():
            days = int(arg)
        elif arg in ("--empty", "--refused"):
            only = arg[2:]
        elif arg.startswith("-"):
            print("usage: ./fff maillog [DAYS] [--empty | --refused] [WORDS]",
                  file=sys.stderr)
            return 2
        else:
            words.append(arg)
    conn = store.connect()
    found = rows(conn, days, only, " ".join(words))
    if not found:
        print("no mail logged in the last %d days%s" % (
            days, " matching that" if (only or words) else
            " -- the log fills on the next ./fff refresh"))
        return 0
    sys.stdout.write(render(conn, found))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
