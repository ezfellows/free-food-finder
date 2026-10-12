"""The senders and mailing lists you have chosen to read events from.

Your school's profile decides which addresses are organisations. Some event
mail comes from places it cannot know: a club's Gmail account, a professor
who emails a talk series, a mailing list you joined. Add those here.

    ./fff sources                                   what you have added
    ./fff sources add club@gmail.com                one sender
    ./fff sources add-list events@lists.example.edu "Events list"
    ./fff sources remove club@gmail.com

A **sender** is one address. Its mail is read even if the address looks like
a person's.

A **list** is a mailing list (a listserv) you are on. Everything posted to it
went to everyone on it, so a post is read whoever wrote it -- and its events
are credited to the list, never to the classmate who pressed send. A message
counts as posted to the list when the list's address is in its To, Cc or
From, or in its List-Id or List-Post headers, which covers almost every list
server.

Adding a source widens only *who* may be read. What a message says is still
checked on every message: an ID number, "your callback interview", a calendar
invitation or a reply to you alone keeps it off the page, whoever sent it. A
sender refused with `./fff senders deny` stays refused, even on a list.

Both kinds live in data/config.json under "sources", which you may also edit
by hand:

    "sources": {
      "senders": ["club@gmail.com"],
      "lists": {"events@lists.example.edu": "Events list"}
    }
"""

from __future__ import annotations

import email.utils
import os
import re
import sys
from contextlib import contextmanager

import config

ADDRESS = re.compile(r"[^@\s<>\"',;:]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")

# Where a list puts its own address. Mailman and Google Groups name the list
# in List-Id ("<events.lists.example.edu>") and List-Post ("<mailto:...>");
# most also address the post To the list, and some rewrite From to it.
LIST_FIELDS = ("To", "Cc", "Bcc", "Delivered-To", "X-Original-To", "From")

_cache = {"key": None, "value": None}
_override = []


def _normal(address) -> str:
    return str(address or "").strip().strip("<>").lower()


def _read() -> dict:
    raw = config.load().get("sources") or {}
    if not isinstance(raw, dict):
        raise SystemExit('"sources" in %s should be {"senders": [...], '
                         '"lists": {...}}' % config.path())
    found = {_normal(a) for a in raw.get("senders") or []}
    lists = raw.get("lists") or {}
    if isinstance(lists, (list, tuple)):          # a plain list of addresses
        lists = {a: "" for a in lists}
    return {"senders": sorted(a for a in found if ADDRESS.fullmatch(a)),
            "lists": {_normal(a): str(n or "").strip()
                      for a, n in sorted(lists.items())
                      if ADDRESS.fullmatch(_normal(a))}}


def load() -> dict:
    """{"senders": [address], "lists": {address: name}}, read when it changes."""
    if _override:
        return _override[-1]
    target = config.path()
    try:
        key = (target, os.stat(target).st_mtime_ns)
    except OSError:
        key = (target, None)
    if _cache["key"] != key:
        _cache["value"] = _read()
        _cache["key"] = key
    return _cache["value"]


@contextmanager
def using(senders=(), lists=None):
    """Sources for the length of a `with` block, whatever the file says."""
    _override.append({"senders": sorted(_normal(a) for a in senders),
                      "lists": {_normal(a): n for a, n in (lists or {}).items()}})
    try:
        yield
    finally:
        _override.pop()


# --- asking --------------------------------------------------------------

def is_sender(address: str) -> bool:
    return _normal(address) in load()["senders"]


def _list_named(addresses):
    lists = load()["lists"]
    for address in addresses:
        if address in lists:
            return address, lists[address]
    return None


def list_for(msg):
    """(address, name) of the list you added that this was posted to, or None."""
    lists = load()["lists"]
    if not lists or msg is None:
        return None
    seen = []
    for field in LIST_FIELDS:
        for value in msg.get_all(field) or []:
            seen += [_normal(a) for _, a in email.utils.getaddresses([value]) if a]
    post = re.search(r"mailto:([^>\s?]+)", msg.get("List-Post") or "", re.I)
    if post:
        seen.append(_normal(post.group(1)))
    list_id = re.search(r"<([^>]+)>", msg.get("List-Id") or "")
    if list_id:
        ident = list_id.group(1).strip().lower()
        seen += [a for a in lists if a.replace("@", ".") == ident]
    return _list_named(seen)


def envelope_list(sender: str, recipients) -> tuple:
    """`list_for`, for a search result: only its addresses, no headers."""
    if not load()["lists"]:
        return None
    return _list_named([_normal(sender)] + [_normal(a) for a in recipients or []])


def credit(address: str, name: str) -> str:
    """How the page names a list: its name, else its mailbox."""
    return name or address.split("@")[0]


def standing(conn, address: str) -> str:
    """'deny', 'allow' or '' for a sender.

    A refusal by hand (`./fff senders deny`) wins. Then a sender you added
    here, or one allowed by hand in the register before this file existed.
    """
    import store
    verdict = store.sender_verdict(conn, address) if conn is not None else ""
    if verdict:
        return verdict
    return "allow" if is_sender(address) else ""


# --- changing --------------------------------------------------------------

def _edit(change) -> None:
    data = config.load()
    current = _read()
    change(current)
    data["sources"] = {"senders": sorted(set(current["senders"])),
                       "lists": dict(sorted(current["lists"].items()))}
    config.save(data)


def _valid(address: str) -> str:
    address = _normal(address)
    if not ADDRESS.fullmatch(address):
        raise ValueError(f"not an email address: {address!r}")
    return address


def add_sender(address: str) -> str:
    address = _valid(address)
    _edit(lambda s: s["senders"].append(address))
    return address


def add_list(address: str, name: str = "") -> str:
    address = _valid(address)
    _edit(lambda s: s["lists"].__setitem__(address, (name or "").strip()))
    return address


def remove(address: str) -> list:
    """Stop reading this address, as a sender, a list, or both."""
    address = _normal(address)
    gone = []
    current = _read()
    if address in current["senders"]:
        gone.append("sender")
    if address in current["lists"]:
        gone.append("list")
    if gone:
        def change(s):
            s["senders"] = [a for a in s["senders"] if a != address]
            s["lists"].pop(address, None)
        _edit(change)
    return gone


# --- ./fff sources ---------------------------------------------------------

USAGE = """usage:
  ./fff sources                              what you have added
  ./fff sources add <address>                read this sender's mail
  ./fff sources add-list <address> [name]    read what is posted to this list
  ./fff sources remove <address>             stop reading a sender or list"""


def show(conn) -> None:
    import senders
    import store
    mine = load()
    earlier = [r["address"] for r in store.known_senders(conn)
               if r["verdict"] == "allow" and r["address"] not in mine["senders"]]
    print("Senders (%d):" % (len(mine["senders"]) + len(earlier)))
    for address in mine["senders"]:
        print("  " + address)
    for address in earlier:
        print("  %s   (allowed earlier with ./fff senders allow)" % address)
    if not (mine["senders"] or earlier):
        print("  none")
    print("Lists (%d):" % len(mine["lists"]))
    for address, name in mine["lists"].items():
        print("  %s   credited as %r" % (address, credit(address, name)))
    if not mine["lists"]:
        print("  none")
    # What the vetting turned away that still looks like an announcement: it
    # carried bulk markers, but its address meant nothing to the profile.
    maybe = [r for r in store.known_senders(conn, only_refused=True)
             if r["bulk_seen"] and not r["verdict"]
             and not senders.looks_personal(r["address"])][:8]
    if maybe:
        print("\nRefused lately, though they mail like organisations. "
              "To read one:  ./fff sources add <address>")
        for r in maybe:
            print("  %-44s %3d msgs  %s" % (r["address"], r["messages"],
                                            r["org_name"] or ""))


def main(argv: list) -> int:
    import school
    import senders
    import store
    command = argv[0] if argv else ""
    try:
        if command == "":
            show(store.connect())
        elif command == "add" and len(argv) == 2:
            address = add_sender(argv[1])
            print(f"added sender {address}")
            # Only a school's own rule can say an address is a person's;
            # with no profile, every address "looks" like one.
            if school.active().PERSON and senders.looks_personal(address):
                print("  It looks like a person's address. Their announcements "
                      "will be read; anything\n  written to you alone is "
                      "still kept off the page.")
        elif command == "add-list" and len(argv) in (2, 3):
            address = add_list(argv[1], argv[2] if len(argv) == 3 else "")
            name = load()["lists"].get(address, "")
            print(f"added list {address}: posts to it will be read and "
                  f"credited to {credit(address, name)!r}")
        elif command == "remove" and len(argv) == 2:
            gone = remove(argv[1])
            conn = store.connect()
            if store.sender_verdict(conn, argv[1]) == "allow":
                store.set_sender_verdict(conn, argv[1], "")
                gone.append("sender allowed earlier")
            print(f"removed {_normal(argv[1])} ({', '.join(gone)})" if gone
                  else f"{_normal(argv[1])} was not a source")
        else:
            print(USAGE, file=sys.stderr)
            return 2
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
