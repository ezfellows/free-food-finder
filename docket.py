"""Everything the website needs from the database.

Named `docket`, not `site`: `site` is a standard-library module that Python
imports at startup, so a local `site.py` is shadowed and never loads.

It holds the ingestion entry point (`refresh`), the calendar feed, the
shaping of each event for display, and -- importantly -- the scrubbing that
keeps anything personal off the published page.

Campus and requirement details are computed at render time rather than stored.
They are pure functions of an event's own text (`campus.locate`,
`details.extract`), so keeping them here means a change to the walking table
or the dress-code patterns applies to every event already in the database,
with no migration and no re-ingest.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import campus
import config
import details
import flyers
import merge
import school
import senders
import store
import timing
from ingest import livewhale, mail, orgs

ROOT = os.path.dirname(os.path.abspath(__file__))

# Calendar group slugs are machine names ("studentaffairscapitolcampus"); the
# profile's CALENDAR_GROUP_NAMES gives the readable ones. The flyer
# broadcast's label is the profile's too: "OSL flyer" at Georgetown.
SOURCE_NAMES = {"livewhale": "Calendar", "mail": "Email", "org": "Org page",
                "broadcast": "Flyer", "groupme": "GroupMe"}
NETWORK_LABELS = {"recruiting": "Recruiting", "firm": "Firm in the room",
                  "practitioner": "Lawyers present", "alumni": "Alumni"}
TIER_LABELS = {"confirmed": "Food confirmed", "likely": "Food likely",
               "possible": "Food possible"}

# Words an early version wrote into the networking reason about the owner's own
# contacts at a firm. Nothing writes them now; an old row that still carries
# them is scrubbed on read, because who you know is not page content.
PRIVATE_REASON_RX = re.compile(
    r"\s*;?\s*\d+\s+alumni\s+you\s+can\s+name|\s*;?\s*\d+\s+already\s+emailed",
    re.I)


def load_config() -> dict:
    return config.load()


# --- display shaping -----------------------------------------------------

# An "organisation" that names no organisation. OCR reads the words off a
# flyer's footer and hands back "LAW ORGANIZATION", which is true of every
# host on the page and identifies none of them -- the same objection
# `titles.py` makes to a title that says nothing specific. Left in, it becomes
# a tag on the card and an entry in the filter menu that no one can use.
GENERIC_ORGS = {
    "law organization", "law organisation", "organization", "organisation",
    "student organization", "student organisation", "law org", "student org",
    "student group", "law school", "student", "students", "club", "society",
    "association",
}


def generic_orgs() -> set:
    """GENERIC_ORGS, and what the school calls itself (its GENERIC_ORGS)."""
    return GENERIC_ORGS | {o.lower() for o in school.active().GENERIC_ORGS}


def pretty_org(org: str) -> str:
    org = (org or "").strip()
    if not org:
        return ""
    if org.lower().strip(".,") in generic_orgs():
        return ""
    if " " in org or any(c.isupper() for c in org):
        return _shorten_org(org)
    return school.active().CALENDAR_GROUP_NAMES.get(org.lower(), "")


# Longer than this is a banner, not a name: OCR reads a flyer's whole header
# as the org, and "Georgetown Law Barrister's Council Alternative Dispute
# Resolution" is not what anyone calls them.
ORG_TAG_MAX = 42


def _shorten_org(org: str) -> str:
    """The name a reader would use, out of whatever the flyer printed."""
    if len(org) <= ORG_TAG_MAX:
        return org
    # The school's own name in front of an org is a given on this page:
    # "Georgetown Law X" is X. The profile's ORG_PREFIX says what to trim.
    prefix = school.active().ORG_PREFIX
    trimmed = (re.sub(prefix, "", org, flags=re.I).strip() if prefix
               else org)
    if trimmed and len(trimmed) <= ORG_TAG_MAX:
        return trimmed
    # Otherwise keep whole words up to the cap rather than cutting mid-word.
    words, out = (trimmed or org).split(), []
    for word in words:
        if len(" ".join(out + [word])) > ORG_TAG_MAX:
            break
        out.append(word)
    return " ".join(out) or (trimmed or org)[:ORG_TAG_MAX]


def pretty_source(source: str) -> str:
    """'livewhale,broadcast' -> 'Calendar + OSL flyer'."""
    names = dict(SOURCE_NAMES)
    names["broadcast"] = ((school.active().FLYERS or {}).get("label")
                          or SOURCE_NAMES["broadcast"])
    labels = []
    for part in (source or "").split(","):
        label = names.get(part.strip(), part.strip())
        if label and label not in labels:
            labels.append(label)
    return " + ".join(labels)


def public_firms(firms) -> list:
    """Firm names only, whatever shape they were stored in.

    Rows written before the alumni data was removed still carry counts, so
    scrubbing on read means old data cannot leak while the database catches up.
    """
    out = []
    for f in firms or []:
        name = f.get("name") if isinstance(f, dict) else str(f)
        if name:
            out.append({"name": name})
    return out


def public_reason(reason: str) -> str:
    return PRIVATE_REASON_RX.sub("", reason or "").strip(" ;")


# Text read out of an email can carry the mailbox it was read from: the
# CampusGroups footer ("Email sent by <student> to <reader>"), a netID, or a
# link wrapped with the reader's own tracking token (`click.info ... ?qs=`,
# `member_id=`). The page publishes no raw description; this cleans the
# fields it does publish, in case a splitter lifted one of those into a title
# or a place.
EMAIL_SENT_BY_RX = re.compile(r"\bemail\s+sent\s+by\b.*", re.I | re.S)
URL_RX = re.compile(r"https?://[^\s<>()\[\]\"']+", re.I)
ADDRESS_RX = re.compile(r"[\w.+-]+ ?@ ?[\w-]+(?:\.[\w-]+)+")


def public_text(text: str) -> str:
    text = EMAIL_SENT_BY_RX.sub("", text or "")
    text = URL_RX.sub(
        lambda m: m.group(0) if mail.public_link([m.group(0)]) else "", text)
    text = ADDRESS_RX.sub(
        lambda m: "" if senders.looks_personal(m.group(0).replace(" ", ""))
        else m.group(0), text)
    return re.sub(r"\s{2,}", " ", text).strip(" ,;")


# --- the RSVP tracker ----------------------------------------------------

# A firm in the room makes it a firm event, whoever hosts it: a student org's
# Big Law panel is where a 1L meets Latham. The Office of Career Strategy's
# 1L Law Firm Receptions name no single firm, so the title has to say it.
FIRM_TITLE_RX = re.compile(r"\b(?:law\s+firms?|multi-?firm|big\s*law)\b", re.I)


def rsvp_track(event: dict) -> str:
    """"firm" or "school": the tracker tab an event is listed under."""
    if (event.get("firms") or event.get("network_kind") == "firm"
            or FIRM_TITLE_RX.search(event.get("title") or "")):
        return "firm"
    return "school"


# Where to sign up, out of an email. A link in an email can carry the reader:
# CampusGroups wraps every one with the reader's member_id, a Google Form can
# arrive filled in with a name, and a ride voucher link is money. So this is a
# short list of shapes known to be the same for every reader, not a list of
# shapes known to be bad -- anything not on it is never published.
SIGNUP_LINK_RX = re.compile(
    r"(?:rsvp|regist\w*|sign\s*-?\s*up|reserve)\b[^\n]{0,60}?"
    r"(https?://[^\s<>()\[\]\"']+)", re.I)


def _on(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def signup_page(url: str) -> bool:
    """True when `url` is an event-wide sign-up page on a known site."""
    try:
        parts = urlparse(url)
        query = parse_qs(parts.query, keep_blank_values=True)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or parts.fragment:
        return False
    host, path, keys = (parts.hostname or "").lower(), parts.path, set(query)
    if _on(host, "florecruit.com"):
        # One page per event: /v2/app/<firm>/registration/events/<n>/register
        return not keys and bool(re.fullmatch(
            r"/v2/app/[\w-]+/registration/events/\d+/register/?", path))
    if host == "forms.gle":
        return not keys and bool(re.fullmatch(r"/[\w-]+", path))
    if host == "docs.google.com":
        # `entry.<n>=` fills a field in, sometimes with the reader's name.
        return keys <= {"usp"} and bool(re.fullmatch(
            r"/forms/d/(?:e/)?[\w-]+/viewform", path))
    if host in ("forms.office.com", "forms.cloud.microsoft"):
        return ("id" in keys and keys <= {"id", "origin", "route"}
                and path.lower() == "/pages/responsepage.aspx")
    if _on(host, "campusgroups.com"):
        # An event's own page. /click is the wrapper that names the reader.
        return (keys <= {"id"} and not path.lower().startswith("/click")
                and bool(re.search(r"rsvp|event", path, re.I)))
    if _on(host, "symplicity.com"):
        # `id` names the event (see test_privacy); the sign-in page has none.
        return (path.rstrip("/") == "/students" and "id" in keys
                and keys <= {"s", "ss", "mode", "id"})
    return False


def rsvp_link(description: str) -> str:
    """The sign-up link an email gives, when it is safe to publish, or ""."""
    for match in SIGNUP_LINK_RX.finditer(description or ""):
        url = match.group(1).rstrip(".,;:!?*")
        if mail.public_link([url]) == url and signup_page(url):
            return url
    return ""


def firm_summary(firms) -> str:
    firms = public_firms(firms)
    if not firms:
        return ""
    summary = firms[0]["name"]
    if len(firms) > 1:
        summary += f" +{len(firms) - 1} more firm{'s' if len(firms) > 2 else ''}"
    return summary


def humanise(start_iso: str, now=None) -> str:
    """'in 40 min', 'tomorrow 12:00' — what you actually want to read."""
    try:
        start = datetime.fromisoformat(start_iso)
    except (TypeError, ValueError):
        return ""
    now = now or datetime.now()
    mins = int((start - now).total_seconds() // 60)
    if mins < 0:
        return "happening now" if mins > -120 else "past"
    if mins < 60:
        return f"in {mins} min"
    if mins < 8 * 60:
        return f"in {mins // 60}h {mins % 60:02d}m"
    if start.date() == now.date():
        return f"today {start:%H:%M}"
    if start.date() == (now + timedelta(days=1)).date():
        return f"tomorrow {start:%H:%M}"
    if (start - now).days < 7:
        return f"{start:%a} {start:%H:%M}"
    return f"{start:%a %-d %b}"


# --- assembling events for the site --------------------------------------

def build_events(conn, days: int = 30, min_tier: str = "possible",
                 mode: str = "both") -> list:
    """Every publishable event, soonest first.

    A published page has no viewer to personalise for, so there is nothing
    to filter per person. Events on another campus are dropped outright rather than
    filtered in the browser: the reader is on the docket's own campus, and at
    Georgetown, Main Campus is a shuttle ride, so those events were only ever
    noise.
    """
    rows = store.upcoming(conn, days=days, min_tier=min_tier, venue="any",
                          mode=mode)
    out = []
    for row in rows:
        event = dict(row)
        org = pretty_org(event.get("org"))
        # For LiveWhale the raw `org` is the publishing group slug ("law",
        # "live"); for a flyer it is the student org's own name, and the
        # source is what identifies it as the school's.
        source = (event.get("source") or "")
        group = "broadcast" if "broadcast" in source else (event.get("org") or "")
        event["location"] = public_text(event.get("location"))
        place = campus.locate(event["location"], group,
                              event.get("description") or "")
        # Mail and the flyer broadcast are vetted before they reach here, and
        # a GroupMe post is added by hand from an org's own chat, so an event
        # from any of them is the school's wherever it is being held.
        school_source = (source in ("mail", "broadcast", "groupme")
                         or "broadcast" in source or "groupme" in source)
        if not campus.keep_event(place, group, event.get("network_kind") or "",
                                 school_source=school_source):
            continue

        try:
            ref = datetime.fromisoformat(event["starts_at"])
        except (TypeError, ValueError):
            ref = None
        practical = details.extract(event.get("title") or "",
                                    event.get("description") or "", ref)

        event["org"] = org
        event["title"] = public_text(event.get("title")) or "Untitled event"
        # Building AND room: "McDonough 208", not "McDonough Hall". The room
        # is the only part that matters once you are in the lift.
        event["place"] = public_text(place["place"])
        event["venue"] = place["venue"]
        event["room"] = place["room"]
        # Carried as a flag rather than left for the page to infer from the
        # words in `place`: "Virtual" is a label this code chose, and a reader
        # filtering on it should not depend on that label never changing.
        event["virtual"] = place["campus"] == "virtual"
        # How far the reader has to go, which the page could not say before:
        # everything looked like it was in the main building.
        event["campus"] = place["campus"]
        event["when"] = humanise(event["starts_at"])
        # What each source said about the clock, so a genuine disagreement
        # between the poster and the email reaches the reader rather than
        # being settled silently in favour of whichever rule ran last.
        event["time_note"] = timing.note_for(timing.assess(conn, event))
        # A food verdict a person supplied has no phrase to quote, so it says
        # where it came from instead.
        event["food_note"] = store.correction_reason(conn, event["id"],
                                                     ("has_food", "tier"))
        event["source_label"] = pretty_source(event.get("source"))
        event["firms"] = public_firms(event.get("firms"))
        event["firm_summary"] = firm_summary(event["firms"])
        event["reason"] = event.get("reason") or ""
        event["network_reason"] = public_reason(event.get("network_reason"))
        event["notes"] = practical["notes"]
        event["dress_code"] = practical["dress_code"]
        event["rsvp_by"] = practical["rsvp_by"]
        event["reception"] = practical["reception"]
        event["rsvp_needed"] = practical["rsvp"]
        event["rsvp_closed"] = practical["rsvp_closed"]
        event["perks"] = [dict(p, phrase=public_text(p["phrase"]))
                          for p in practical["perks"]]
        # "Lyft codes ... to those who register at least 48 hours before":
        # the last moment to sign up and still get the ride.
        cuts = [ref - timedelta(hours=p["lead_hours"])
                for p in practical["perks"] if p["lead_hours"] and ref]
        event["perk_by"] = (min(cuts).isoformat(timespec="minutes")
                            if cuts else "")
        event["signals"] = [s.get("phrase") or s.get("label")
                            for s in (event.get("signals") or [])
                            if s.get("weight", 0) > 0][:4]
        # A filesystem path would carry /Users/<name> onto a public page.
        fid = event.pop("flyer_id", "")
        event["flyer"] = fid or ""
        # A flyer's QR code is stored at ingest; an email's sign-up link is
        # read here, so the list of sites it may come from applies to every
        # event already stored. The stored link meets the same rule: a QR
        # code is as often a shortener (bit.ly, qr.codes), a spreadsheet or
        # the organiser's own editing link as a sign-up form, and the reader
        # can still scan the flyer itself.
        stored = event.get("rsvp_url") or ""
        event["rsvp_url"] = ((stored if signup_page(stored) else "")
                             or (rsvp_link(event.get("description"))
                                 if "mail" in source else ""))
        event["track"] = rsvp_track(event)
        try:
            start = datetime.fromisoformat(event["starts_at"])
            event["when_absolute"] = (start.strftime("%a %-d %b, all day")
                                      if event.get("all_day")
                                      else start.strftime("%a %-d %b, %-I:%M %p"))
        except (TypeError, ValueError):
            event["when_absolute"] = ""
        out.append(event)
    return out


def flyer_manifest(events, published_dir: str = "flyers") -> dict:
    """{published path: source path} for the flyers the page references.

    Uses the 420px card rendition. Artifacts serve supporting files from their
    own origin, so the page can reference them by relative path -- no base64
    inflation, and the image stays a real image.
    """
    manifest = {}
    for event in events:
        fid = event.get("flyer")
        if not fid:
            continue
        card = flyers.paths(fid)["card"]
        if os.path.exists(card):
            manifest[f"{published_dir}/{fid}.jpg"] = card
            event["flyer_src"] = f"{published_dir}/{fid}.jpg"
        else:
            event["flyer_src"] = ""
    return manifest


# --- calendar feed -------------------------------------------------------

def build_ics(conn, days: int = 30, min_tier: str = "likely") -> str:
    """A subscribable calendar, with a 20-minute alarm on every event."""
    events = build_events(conn, days=days, min_tier=min_tier)

    def esc(text):
        return (str(text or "").replace("\\", "\\\\").replace(";", "\\;")
                .replace(",", "\\,").replace("\n", "\\n"))

    # Times are stored as the school's wall clock. With no time zone in the
    # profile they go out "floating": read in whatever zone the calendar is.
    tz = school.active().TIMEZONE
    zone = f";TZID={tz}" if tz else ""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Free Food Finder//EN",
             "CALSCALE:GREGORIAN", "X-WR-CALNAME:Free Food Finder",
             "METHOD:PUBLISH"]
    for e in events:
        try:
            start = datetime.fromisoformat(e["starts_at"])
            end = (datetime.fromisoformat(e["ends_at"]) if e["ends_at"]
                   else start + timedelta(hours=1))
        except (TypeError, ValueError):
            continue
        meals = ", ".join(e.get("meals") or []) or "event"
        where = e.get("place") or e.get("location") or ""
        lines += [
            "BEGIN:VEVENT", f"UID:{e['id']}@free-lunch-docket",
            f"DTSTAMP:{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
            f"DTSTART{zone}:{start:%Y%m%dT%H%M%S}",
            f"DTEND{zone}:{end:%Y%m%dT%H%M%S}",
            f"SUMMARY:{esc('[' + meals + '] ' + e['title'])}",
            f"LOCATION:{esc(where)}",
            f"DESCRIPTION:{esc(' | '.join(x for x in (e.get('reason'), ' '.join(e.get('notes') or []), e.get('rsvp_url')) if x))}",
            "BEGIN:VALARM", "TRIGGER:-PT20M", "ACTION:DISPLAY",
            f"DESCRIPTION:{esc(meals + ': ' + e['title'])}", "END:VALARM",
            "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


# --- ingestion -----------------------------------------------------------

def refresh(cfg: dict = None) -> dict:
    """Run every configured ingester, then prune. The pipeline entry point."""
    cfg = cfg if cfg is not None else load_config()
    conn = store.connect()
    results = []
    if school.active().CALENDAR:
        results.append(livewhale.run(conn, days=int(cfg.get("horizon_days", 30))))
    if cfg.get("imap") or (cfg.get("mail") or {}).get("maildrop"):
        results.append(mail.run(conn, cfg))
    if cfg.get("orgs"):
        results.append(orgs.run(conn, cfg))
    # A chat's post stored before the email or flyer that announces the same
    # event becomes part of that listing, not a second one.
    folded = merge.fold_secondhand(conn)
    store.prune(conn)
    flyers.prune(conn)
    # Last, so a source that re-sends a field a person has already ruled on
    # does not quietly win it back.
    corrected = store.apply_corrections(conn)
    conn.close()
    return {"at": datetime.now().isoformat(timespec="seconds"),
            "results": results,
            "folded_into_another_listing": folded,
            "corrections_reapplied": corrected}
