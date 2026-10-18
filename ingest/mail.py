"""Mailbox ingester for law-school listserv announcements.

This is where most law-school free food actually lives: student orgs email the
class, they rarely post to the university calendar.

Credentials
-----------
No password is ever stored in this repo or in config.json. The config names a
*source* for the secret and this module fetches it at runtime:

    "keychain_service": "free-food-finder"   -> macOS Keychain (preferred)
    "password_env":     "FFF_IMAP_PASSWORD"  -> environment variable

Store one yourself with (you type the password, it is never echoed):

    security add-generic-password -s free-food-finder -a you@example.edu -w

Offline mode
------------
Point `maildrop` at a directory of .eml files to develop and test the whole
pipeline with no mailbox access at all. `samples/` ships with a few.
"""

from __future__ import annotations

import email
import email.utils
import imaplib
import os
import re
from urllib.parse import parse_qs, urlparse
import subprocess
import sys
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from html.parser import HTMLParser

sys.path.insert(0, __file__.rsplit("/", 2)[0])

import config                                       # noqa: E402
import newsletter                                   # noqa: E402
import personal                                     # noqa: E402
import school                                       # noqa: E402
import senders                                      # noqa: E402
import sources                                      # noqa: E402
import store                                        # noqa: E402
import titles                                       # noqa: E402
from dateparse import find_event_datetime           # noqa: E402
from network import annotate, worth_storing         # noqa: E402
from score import _clean, score_event               # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LIST_RX = re.compile(r"<([^>]+)>")
# Listservs bolt these onto every subject line; they are not part of the event.
SUBJECT_NOISE = re.compile(
    r"^\s*(?:(?:re|fwd?)\s*:\s*|\[[^\]]{1,40}\]\s*)+", re.I)


def load_config() -> dict:
    return config.load()


def get_password(cfg: dict):
    """Resolve the mailbox password from Keychain or environment. Never logged."""
    service = cfg.get("keychain_service")
    account = cfg.get("user")
    if service and account:
        try:
            out = subprocess.run(
                ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
                capture_output=True, text=True, timeout=15)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    env = cfg.get("password_env")
    if env and os.environ.get(env):
        return os.environ[env]
    return None


def _decode(value: str) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (UnicodeDecodeError, LookupError, ValueError):
        return value


class _HtmlText(HTMLParser):
    """The words of an HTML email laid out the way Gmail's plain text lays them.

    Tags go whole, attributes and all. The regex in `score._clean` stops at
    the first ">" it meets, so an image whose alt text held one leaked `src=`
    and `style=` into the body, and the splitter read them as an event title
    ("14 OCT\\" src=\\" style=\\"max-width:1024px" -- MLS's plan of the week).

    The layout matters as much as the words. `newsletter.split` separates
    entries at blank lines and reads `##` and `|` as breaks, because every
    email it was built on came through Gmail's plain text: a table row is a
    paragraph, cells are walled with pipes, a heading is `##`. Paragraphs
    inside a cell stay in that row's paragraph -- a CampusGroups item puts its
    title, date and room in one cell, and splitting them apart orphans the
    title from its date. MAVRC's newsletter gave fourteen events from Gmail's
    text and none from a version that broke on every paragraph.
    """

    HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    BLOCKS = {"p", "div", "br", "ul", "ol", "center", "blockquote"}
    SKIP = {"script", "style", "title"}
    LINE, PARA = 1, 2

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skipping, self.tables, self.href = [], 0, 0, ""

    def _break(self, kind):
        self.out.append(kind)

    def _space_or(self, kind):
        # Inside a table cell Gmail runs paragraphs together on one line: the
        # cell is the entry, and a CampusGroups item keeps its title, date and
        # room in one. Outside tables a paragraph is a paragraph.
        self.out.append(" " if self.tables else kind)

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1
        elif tag == "table":
            self.tables += 1
            self._break(self.PARA)
        elif tag == "tr":
            self._break(self.PARA)
        elif tag in ("td", "th"):
            self.out.append(" | ")
        elif tag in self.HEADINGS:
            self._space_or(self.LINE)
            self.out.append("#" * int(tag[1]) + " ")
        elif tag == "li":
            self._space_or(self.LINE)
            self.out.append("* ")
        elif tag in self.BLOCKS:
            self._space_or(self.PARA if tag != "br" else self.LINE)
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            self.href = href if href.lower().startswith(("http://", "https://")) else ""

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skipping = max(0, self.skipping - 1)
        elif tag == "table":
            self.tables = max(0, self.tables - 1)
            self._break(self.PARA)
        elif tag == "tr":
            self._break(self.PARA)
        elif tag in self.HEADINGS:
            self._space_or(self.LINE)
        elif tag in self.BLOCKS or tag == "li":
            self._space_or(self.PARA if tag != "br" else self.LINE)
        elif tag == "a" and self.href:
            self.out.append("[](%s) " % self.href)
            self.href = ""

    def handle_data(self, data):
        # Whitespace in markup is one space, newlines included; only the tags
        # make lines. Kept as typed, the source's own line breaks doubled the
        # breaks after every <br> into blank lines -- paragraph ends to the
        # splitter, which then found no event with both a title and a date.
        if not self.skipping:
            self.out.append(re.sub(r"\s+", " ", data))

    def text(self) -> str:
        # Breaks swallow the whitespace around them: "</h2> <div>" is one
        # line break, not a line holding a single space -- which read as a
        # blank line, and so as the end of the entry.
        marks = {self.LINE: "\x01", self.PARA: "\x02"}
        raw = "".join(marks[i] if isinstance(i, int) else i for i in self.out)
        raw = re.sub(r"[ \t\x01\x02]*[\x01\x02][ \t\x01\x02]*",
                     lambda m: "\n\n" if "\x02" in m.group(0) else "\n", raw)
        lines = (" ".join(line.split()) for line in raw.split("\n"))
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_to_text(html: str) -> str:
    parser = _HtmlText()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:                    # markup too broken to parse: old way
        return re.sub(r"<[^>]+>", " ", html or "")
    return parser.text()


def body_text(msg) -> str:
    """Prefer text/plain; fall back to stripped HTML. Skips attachments."""
    plain, html = [], []
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if part.get_content_maintype() == "multipart":
            continue
        if "attachment" in (part.get("Content-Disposition") or "").lower():
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            raw = part.get_payload(decode=True) or b""
            text = raw.decode(part.get_content_charset() or "utf-8", errors="replace")
        except (LookupError, ValueError):
            continue
        (plain if ctype == "text/plain" else html).append(text)
    return _clean("\n".join(plain) if plain
                  else "\n".join(html_to_text(h) for h in html))


# Hosts whose links carry a token identifying the recipient. Following one --
# or publishing it for a reader to follow -- registers a click as the person
# whose mailbox it came from. `unwrap` already refuses to resolve these; this
# keeps them from being published as an event's link in the first place. A
# school's own tracking hosts (Georgetown's `click.info.georgetown.edu`) are
# in its profile as TRACKING_HOSTS.
PER_RECIPIENT_HOSTS = (
    # Mailchimp's own links carry an `e=` subscriber hash. The parameter name
    # is too common to blocklist outright, and nothing on this host is ever a
    # destination worth publishing.
    "list-manage.com",
    # Mailchimp's hosted copy of the same email. Every Private Sector Pulse
    # opens with "View this email in your browser" pointing here, carrying
    # the same `e=` subscriber hash.
    "mailchi.mp",
)


# Query parameters that name the recipient. CampusGroups wraps every link in
# its newsletters as `/click?email_links_id=...&member_id=12345678` -- on
# `<school>.campusgroups.com`, which is otherwise a perfectly good
# destination, so the host alone cannot decide it.
RECIPIENT_PARAMS = ("member_id", "qs", "uid", "subscriber_id", "recipient_id")


def public_link(links) -> str:
    """The first link safe to put in front of a reader, or ""."""
    for url in links or []:
        try:
            parts = urlparse(url)
        except ValueError:
            continue
        host = (parts.hostname or "").lower()
        if any(host == h or host.endswith("." + h)
               for h in PER_RECIPIENT_HOSTS + tuple(school.active().TRACKING_HOSTS)):
            continue
        try:
            query = parse_qs(parts.query)
        except ValueError:
            continue
        if any(name in query for name in RECIPIENT_PARAMS):
            continue
        return url
    return ""


def body_lines(msg) -> str:
    """The same body, with its line breaks intact.

    `body_text` runs the result through `_clean`, which collapses every run of
    whitespace to a single space -- right for scoring, fatal for the footer
    patterns below, which are line-anchored. Collapsed, a CampusGroups footer
    reads "... Treasurer Antitrust & Competition Law Association Georgetown
    University Email sent by ...", the footer pattern cannot match, and every
    one of the subscribed orgs is credited to "no-reply" instead of its own
    name.
    """
    plain, html = [], []
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if part.get_content_maintype() == "multipart":
            continue
        if "attachment" in (part.get("Content-Disposition") or "").lower():
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            raw = part.get_payload(decode=True) or b""
            text = raw.decode(part.get_content_charset() or "utf-8",
                              errors="replace")
        except (LookupError, ValueError):
            continue
        (plain if ctype == "text/plain" else html).append(text)
    joined = ("\n".join(plain) if plain
              else "\n".join(html_to_text(h) for h in html))
    # Trailing spaces only: the line structure is the whole point here.
    return "\n".join(line.rstrip() for line in joined.splitlines())


# CampusGroups relays every student org through one address, so the envelope
# says "no-reply" for all of them. The org's own name is in the footer.
SENT_BY = re.compile(
    r"Email sent by\s+([^<\n]{3,60}?)\s*(?:<\s*([^>\s]+)\s*>|$)", re.I | re.M)


def _cg_footer(profile):
    """The org's name as a heading, above the profile's CAMPUSGROUPS_FOOTER."""
    if not profile.CAMPUSGROUPS_FOOTER:
        return school.NEVER
    return re.compile(r"^\*?([A-Z][^*\n]{4,60}?)\*?\s*$\s*^"
                      + re.escape(profile.CAMPUSGROUPS_FOOTER) + r"\s*$", re.M)


def relayed_org(body: str) -> str:
    """The organisation behind a CampusGroups relay, or "" when not one.

    The footer names it twice: once as a heading above the university's name
    (the profile's CAMPUSGROUPS_FOOTER -- "Georgetown University"), and once
    in "Email sent by NAME <address>". The heading is preferred because the
    second is sometimes the officer who pressed send.

    When it falls through to "Email sent by", the **address** decides, never
    the name. Judging by name needs a list of org-sounding words, and plenty
    of student orgs have none: "Hoya Advocates", "OutLaw", "If/When/How" read
    exactly like people. The address does not have that problem: the
    profile's PERSON rule says what a person's address looks like -- every
    Georgetown netID carries a digit and no role account does.
    """
    match = school.active().derive("mail.cg_footer", _cg_footer).search(body or "")
    if match:
        return _clean(match.group(1))[:60]
    match = SENT_BY.search(body or "")
    if not match:
        return ""
    name = _clean(match.group(1))
    address = senders.address_of(match.group(0))
    if address and senders.looks_personal(address):
        return ""
    return name[:60] if name else ""


def org_of(msg) -> str:
    """Best guess at the sending organisation.

    The From display name wins: on a shared announcements list every org posts
    under the same List-Id, so the list tells you nothing about who is feeding
    you. List-Id is only a fallback for senders with no display name.
    """
    name, addr = email.utils.parseaddr(_decode(msg.get("From", "")))
    # A post to a list you added is the list's when a person wrote it, or
    # when the list rewrote From to itself ("Jane via Events"): the page names
    # who is putting the event on, never which classmate pressed send.
    listed = sources.list_for(msg)
    if listed and (senders.looks_personal(addr) or addr.lower() == listed[0]):
        return sources.credit(*listed)[:60]
    if name.strip():
        return name.strip()[:60]
    list_id = msg.get("List-Id") or msg.get("List-Post") or ""
    m = LIST_RX.search(list_id)
    if m:
        return m.group(1).split(".")[0]
    if list_id.strip():
        return _clean(list_id)[:60]
    local = (addr.split("@")[0] if addr else "").strip()
    # The Gmail connector hands over the bare address, so the mailbox name is
    # all there is -- and an org's short mailbox is its acronym. CFLO's twelve
    # firm events were tagged "cflo", which reads like a typo on the page.
    if local.isalpha() and len(local) <= 6:
        return local.upper()
    return local[:60]


# A firm's recruiting mailbox writing to the 1L class is a recruiting event by
# construction, whatever vocabulary the invitation happens to use. Bass Berry's
# open house says only "invites 1L law students" and scored 14 -- nothing in the
# text is about hiring, because the hiring is the premise. Who sent it is the
# evidence the body omits.
RECRUIT_WORDS = re.compile(
    r"\b1Ls?\b|\b2Ls?\b|recruit\w*|summer\s+associate|open\s+house|"
    r"reception|career|hiring|interview|resume", re.I)


def firm_signal(msg, body: str, fields: dict) -> dict:
    """Credit a firm's own recruiting mail for being exactly that."""
    firm = senders.firm_recruiting(senders.address_of(msg.get("From", "")))
    if not firm:
        return fields
    # `firm_recruiting` returns either a real firm name or the generic note
    # that the ADDRESS looks like recruiting. Only the latter needs the display
    # name as a fallback -- taking it always tagged Milbank as "Milbank
    # Recruiting", which is the mailbox, not the firm.
    named = ("" if firm.startswith("a firm") else firm) or senders.org_name(msg)
    out = dict(fields)
    known = [f.get("name") if isinstance(f, dict) else f
             for f in (out.get("firms") or [])]
    if named and named not in known:
        out["firms"] = (out.get("firms") or []) + [{"name": named}]
    recruiting = bool(RECRUIT_WORDS.search(f"{msg.get('Subject','')} {body}"))
    if (out.get("network_kind") or "none") in ("none", "academic"):
        out["network_kind"] = "recruiting" if recruiting else "firm"
    out["network_score"] = max(int(out.get("network_score") or 0), 55)
    # The host is the firm. `org_of` takes the From display name, which on
    # recruiting mail is the coordinator -- publishing "Jane Roe Doe"
    # as the organisation names a person who is doing her job, not the body
    # putting on the event.
    if named:
        out["host_org"] = named
    reason = out.get("network_reason") or ""
    note = "sent by %s" % named if named else "sent by a firm recruiting address"
    out["network_reason"] = f"{note}; {reason}" if reason else note
    return out


def vet(msg, body: str, conn=None) -> dict:
    """Classify a sender, consulting and updating the register."""
    address = senders.address_of(msg.get("From", ""))
    standing = sources.standing(conn, address)
    verdict = senders.classify(msg, body, standing)
    if conn is not None:
        store.note_sender(
            conn, address,
            bulk=personal.is_bulk(msg, body),
            listserv=senders.to_a_listserv(msg),
            org_name=relayed_org(body_lines(msg)) or senders.org_name(msg, address),
            reason=verdict["reason"])
    return verdict


def messages_to_events(msg, default_ref=None, conn=None, outcome=None) -> list:
    """Every event in one email: usually none or one, sometimes five.

    A round-up is the normal shape of org mail, so this is the entry point and
    `message_to_event` is the single-event case inside it. The subject is
    tried first -- a message that announces one event names it there, and the
    subject is better written than any sentence in the body. Only when that
    yields nothing is the body split, which is what a bulletin needs.
    """
    vetted = False
    if conn is not None:
        verdict = vet(msg, body_text(msg), conn)
        if outcome is not None:          # for the mail log: why, not just whether
            outcome.update(verdict)
        if not verdict["allow"]:
            return []
        vetted = True
    # A body that describes two or more dated events is a round-up, whatever
    # its subject says. Trying the subject first read "Private Sector Pulse -
    # September 24, 2026" as a single event on the 24th -- the issue date --
    # and never looked at the six events inside it, among them both 1L Law
    # Firm Receptions. Counting what the body holds decides it by shape, which
    # does not depend on knowing every newsletter's name in advance.
    #
    # Measured across the maildrop before this changed: it reclassified the
    # Pulse and SIPLA's weekly and left every one-event email alone -- Bass
    # Berry, Weil, Fried Frank, Milbank and the LSDAD reminder each split to at
    # most one event, so they keep their better-written subject titles.
    refused = []
    parts = _events_from_body(msg, default_ref, vetted, refused)
    # A deadline the body announces is no event and is not listed, but it was
    # counted here before it was refused, and still is: "[PPP] Networking
    # Happy Hour + World of Choices" also asks for a form by Friday. Uncounted,
    # the happy hour took the subject's name, and under it was listed twice.
    if len(parts) + len(refused) >= 2:
        return parts
    single = message_to_event(msg, default_ref, vetted)
    if single:
        return [single]
    return parts


def _events_from_body(msg, default_ref=None, vetted=False, refused=None) -> list:
    subject = SUBJECT_NOISE.sub("", _decode(msg.get("Subject", ""))).strip()
    body = body_text(msg)
    if not vetted and not senders.allowed(msg, f"{subject}\n{body}"):
        return []

    received = _received_at(msg, default_ref)
    out = []
    bulk = personal.is_bulk(msg, body)
    # Split on the body with its line breaks intact. A bulletin separates its
    # entries by paragraph and nothing else, and `body_text` collapses every
    # run of whitespace -- leaving the splitter to guess boundaries from full
    # stops, which put the tail of one entry on the front of the next ("edu
    # ADR Bootcamp", "Contact cflo@georgetown.edu CFLO Speed Interviews").
    # Scoring still uses the flat text; only the splitting needs the shape.
    for part in newsletter.split(body_lines(msg), received, refused):
        text = f"{part['title']} {part['detail']}"
        # A bulletin can carry one personal paragraph among five public ones.
        if personal.assess_text(text, bulk)["personal"]:
            continue
        verdict = score_event(title=part["title"], description=part["detail"],
                              location="", source="mail", url="")
        location = guess_location(text) or street_address(text)
        if location:
            verdict["venue_class"] = score_event(
                title=part["title"], description=part["detail"],
                location=location)["venue_class"]
        org = relayed_org(body_lines(msg)) or org_of(msg)
        out.append({
            "source": "mail",
            # Position keeps five events out of one message distinct: without
            # it they share a source id and the last one overwrites the rest.
            "source_id": "%s#%d" % ((msg.get("Message-ID") or "").strip("<> "),
                                    part["position"]),
            "title": part["title"],
            "description": part["detail"][:4000],
            "location": location,
            "org": org,
            "url": public_link(verdict.get("links")),
            "starts_at": part["starts_at"].isoformat(timespec="seconds"),
            "ends_at": (part["ends_at"].isoformat(timespec="seconds")
                        if part["ends_at"] else None),
            "all_day": part["all_day"],
            "date_confidence": part["date_confidence"],
            **{k: verdict[k] for k in ("venue_class", "confidence", "tier",
                                       "has_food", "meals", "reason", "signals",
                                       "rsvp_required", "restricted")},
            **firm_signal(msg, part["detail"],
                          annotate(part["title"], part["detail"], location, org)),
        })
    return out


def _received_at(msg, default_ref=None):
    received = default_ref
    if msg.get("Date"):
        try:
            received = email.utils.parsedate_to_datetime(msg["Date"])
            if received.tzinfo:
                received = received.replace(tzinfo=None)
        except (TypeError, ValueError):
            pass
    return received or datetime.now()


def message_to_event(msg, default_ref=None, vetted=False) -> dict:
    """Turn one email into a scored event dict, or {} if it names no date.

    The sender is vetted before anything is read. This mailbox carries loan
    statements and a classmate's outline alongside the newsletters, and only
    the sender tells them apart -- both arrive from georgetown.edu addressed
    to one reader.
    """
    subject = SUBJECT_NOISE.sub("", _decode(msg.get("Subject", ""))).strip()
    body = body_text(msg)
    # `vetted` means the caller already ran `vet`, which consults the standing
    # verdict from `./fff senders`. Re-checking here without it silently
    # overrides an allowlisted sender -- an address admitted by hand produced
    # no events at all until this was threaded through.
    if not vetted and not senders.allowed(msg, f"{subject}\n{body}"):
        return {}

    # An email subject usually carries the date -- "Judicial Clerkship Panel -
    # October 1" -- which is worth stripping, not rejecting. `repair` does the
    # fixable part and reports only what survives it. What survives here is
    # mostly the round-up: a weekly newsletter names none of the five events
    # it carries, so its subject on the docket would read as an event you
    # could turn up to.
    mend = titles.repair(subject, None, "mail")
    if not mend["ok"]:
        return {}
    subject = mend["fixed"]

    # Run again on what will actually be published. The message as a whole may
    # be a legitimate broadcast while the passage taken out of it is not.
    if personal.assess_text(f"{subject} {body}",
                            personal.is_bulk(msg, body))["personal"]:
        return {}
    received = _received_at(msg, default_ref)

    # Subject first: the date and the hook usually live there, and position
    # is what the date parser uses to break ties.
    text = f"{subject}\n{body}"
    start, end, all_day, dconf = find_event_datetime(text, received)
    if start is None:
        return {}

    verdict = score_event(title=subject, description=body, location="",
                          source="mail", url="")
    net = firm_signal(msg, body, annotate(subject, body, "", org_of(msg)))
    location = guess_location(text) or street_address(text)
    if location:
        verdict["venue_class"] = score_event(
            title=subject, description=body, location=location)["venue_class"]
    return {
        "source": "mail",
        "source_id": (msg.get("Message-ID") or "").strip("<> "),
        "title": subject or "(no subject)",
        "description": body[:4000],
        "location": location,
        "org": net.get("host_org") or relayed_org(body_lines(msg)) or org_of(msg),
        "url": public_link(verdict.get("links")),
        "starts_at": start.isoformat(timespec="seconds"),
        "ends_at": end.isoformat(timespec="seconds") if end else None,
        "all_day": all_day,
        "date_confidence": dconf,
        **{k: verdict[k] for k in ("venue_class", "confidence", "tier", "has_food",
                                   "meals", "reason", "signals", "rsvp_required",
                                   "restricted")},
        **{k: v for k, v in net.items() if k != "host_org"},
    }


def _mail_places(profile):
    """The profile's two place patterns: a named venue, then "in ROOM"."""
    venue = re.compile(profile.MAIL_VENUE, re.I) if profile.MAIL_VENUE else school.NEVER
    return venue, re.compile(profile.MAIL_ROOM, re.I)


# A firm reception is at the firm's office, and the address is written out in
# full: "801 17th Street NW, Washington, DC". None of the patterns above know
# it, because they know the school's buildings -- so the event read as "Room
# to be announced" while the address sat in the body.
STREET_RX = re.compile(
    r"\b(\d{2,5}\s+(?:[A-Z0-9][\w.'-]*\s+){0,3}"
    r"(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|Place|Pl\.?|"
    r"Drive|Dr\.?|Plaza|Square|Sq\.?|Way|Circle|Center|Centre)"
    r"(?:\s+(?:NW|NE|SW|SE|N|S|E|W))?)\b")


def street_address(text: str) -> str:
    match = STREET_RX.search(text or "")
    return match.group(1).strip(" ,.") if match else ""


def guess_location(text: str) -> str:
    venue, room = school.active().derive("mail.places", _mail_places)
    m = venue.search(text) or room.search(text)
    return _clean(m.group(0))[:80] if m else ""


# --- sources -------------------------------------------------------------

GMAIL_FILE = re.compile(r"^(gm-[0-9a-f]{8,})\.eml$", re.I)


def mail_key(name: str, msg) -> str:
    """The log's name for an email: stable whether it was saved whole or not.

    A fetched message is `gm-<Gmail id>.eml` either way; its Message-ID is
    not stable, because a plain-text copy is given one of our own.
    """
    match = GMAIL_FILE.match(name)
    if match:
        return match.group(1)
    return (msg.get("Message-ID") or "").strip().strip("<>").strip() or name


def log_entry(name: str, msg) -> dict:
    try:
        when = email.utils.parsedate_to_datetime(msg.get("Date"))
        received = when.astimezone().isoformat(timespec="minutes")
    except (TypeError, ValueError, IndexError):
        received = ""
    return {"key": mail_key(name, msg), "file": name, "received": received,
            "sender": senders.address_of(msg.get("From", "")),
            "org": relayed_org(body_lines(msg)) or org_of(msg),
            "subject": _decode(msg.get("Subject", "")),
            # The connector's plain-text copy is built here and never passed
            # through a mail server; an original always has.
            "saved_as": "original" if msg.get_all("Received") else "plain text",
            "verdict": "", "reason": "", "events": [], "below": 0}


def broadcast_outcome(conn, msg) -> dict:
    """What ./fff broadcast made of a broadcast: its own events and readings."""
    mid = (msg.get("Message-ID") or "").strip().strip("<>").strip()
    if conn is None or not mid:
        return {"verdict": "broadcast", "reason": ""}
    done = conn.execute("SELECT 1 FROM broadcasts WHERE message_id = ?",
                        (mid,)).fetchone()
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM events WHERE substr(source_id, 1, ?) = ? "
        "OR flyer_id IN (SELECT id FROM flyers WHERE message_id = ?)",
        (len(mid), mid, mid))]
    return {"verdict": "broadcast", "events": ids,
            "reason": ("flyers read by ./fff broadcast" if done
                       else "not read yet: ./fff broadcast <file>")}


def from_maildrop(directory: str, conn=None, log=None) -> list:
    """Parse every .eml in a directory.

    `log`, when given, is filled with one entry per file for the mail log;
    each event carries `_mail_key` so the caller can say which row it became.
    """
    # Deferred: broadcast imports from this module.
    from ingest.broadcast import is_broadcast
    events = []
    if not os.path.isdir(directory):
        return events
    for name in sorted(os.listdir(directory)):
        if not name.lower().endswith(".eml"):
            continue
        with open(os.path.join(directory, name), "rb") as fh:
            msg = email.message_from_bytes(fh.read())
        entry = log_entry(name, msg) if log is not None else None
        # An OSL Weekly Broadcast is read by ./fff broadcast, flyers and all.
        # Read here as well, it went in twice: once from its flyers, and again
        # as a text bulletin whose captions run into each other and put events
        # in the wrong rooms.
        if is_broadcast(msg):
            if entry is not None:
                entry.update(broadcast_outcome(conn, msg))
                log[entry["key"]] = entry
            continue
        outcome = {}
        found = messages_to_events(msg, conn=conn, outcome=outcome)
        if entry is not None:
            if outcome:
                entry["verdict"] = "admitted" if outcome.get("allow") else "refused"
                entry["reason"] = outcome.get("reason", "")
            log[entry["key"]] = entry
            for ev in found:
                ev["_mail_key"] = entry["key"]
        events.extend(found)
    return events


def from_imap(cfg: dict, conn=None) -> list:
    """Fetch recent messages over IMAP. Raises RuntimeError with a clear cause."""
    host = cfg.get("host")
    user = cfg.get("user")
    if not host or not user:
        raise RuntimeError("imap config needs 'host' and 'user'")
    password = get_password(cfg)
    if not password:
        raise RuntimeError(
            "no mailbox password available. Store one with:\n"
            f"  security add-generic-password -s {cfg.get('keychain_service','free-food-finder')} "
            f"-a {user} -w\n"
            "or set the environment variable named by 'password_env'.")

    since = (datetime.now() - timedelta(days=int(cfg.get("since_days", 14))))
    events = []
    conn = imaplib.IMAP4_SSL(host, int(cfg.get("port", 993)))
    try:
        conn.login(user, password)
        for folder in cfg.get("folders") or ["INBOX"]:
            try:
                status, _ = conn.select(f'"{folder}"', readonly=True)
                if status != "OK":
                    continue
                status, data = conn.search(None, "SINCE", since.strftime("%d-%b-%Y"))
                if status != "OK":
                    continue
                for uid in (data[0].split() if data and data[0] else []):
                    status, raw = conn.fetch(uid, "(RFC822)")
                    if status != "OK" or not raw or not raw[0]:
                        continue
                    events.extend(messages_to_events(
                        email.message_from_bytes(raw[0][1]), conn=conn))
            except imaplib.IMAP4.error:
                continue
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return events


def run(conn=None, cfg=None, min_tier="possible") -> dict:
    own = conn is None
    conn = conn or store.connect()
    cfg = cfg if cfg is not None else load_config()
    stats = {"source": "mail", "found": 0, "added": 0, "updated": 0,
             "already_listed": 0, "deduped": 0, "error": None}
    try:
        events, log = [], {}
        drop = (cfg.get("mail") or {}).get("maildrop")
        if drop:
            events += from_maildrop(
                drop if os.path.isabs(drop) else os.path.join(ROOT, drop), conn, log)
        if cfg.get("imap"):
            events += from_imap(cfg["imap"], conn)
        if not events and not drop and not cfg.get("imap"):
            raise RuntimeError("no mail source configured (see data/config.example.json)")
        stats["found"] = len(events)
        rank = {"confirmed": 3, "likely": 2, "possible": 1, "unlikely": 0}

        def keep(ev, key):
            stats[store.upsert(conn, ev)] += 1
            if key in log:
                log[key]["events"].append(store.stored_id(conn, ev))

        new = []
        for ev in events:
            key = ev.pop("_mail_key", None)
            if (rank.get(ev["tier"], 0) < rank.get(min_tier, 1)
                    and not worth_storing(ev)):
                if key in log:
                    log[key]["below"] += 1
                continue
            if store.expired(ev.get("starts_at")):
                continue
            if store.stored_id(conn, ev):
                keep(ev, key)
            else:
                new.append((ev, key))
        # Every email is read again on every refresh. One that repeats what
        # the calendar or a flyer already lists was stored each time, only
        # for the sweep below to delete it again -- seven on every refresh
        # in October 2026, each counted as added. So an event not yet stored
        # is checked first, and only once the rest have merged in: a merge
        # can move the listing it would repeat, since an email that matches
        # a calendar entry by title gives that entry its own org and room.
        held = store.listings(conn)
        for ev, key in new:
            listing = "" if store.stored_id(conn, ev) else store.repeats(ev, held)
            if not listing:
                keep(ev, key)
                continue
            stats["already_listed"] += 1
            if key in log:
                log[key]["events"].append(listing)
        # A copy stored before the listing it repeats arrived -- the calendar
        # entry or the flyer came later -- goes now.
        stats["deduped"] = store.dedupe_slots(conn)
        for entry in log.values():
            store.note_mail(conn, entry)
        conn.commit()
    except (RuntimeError, imaplib.IMAP4.error, OSError) as exc:
        stats["error"] = f"{type(exc).__name__}: {exc}"
    store.log_run(conn, "mail", stats["found"], stats["added"], stats["updated"],
                  stats["error"])
    if own:
        conn.close()
    return stats


if __name__ == "__main__":
    print(run())
