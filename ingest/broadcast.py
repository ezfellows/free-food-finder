"""Turn a flyer newsletter into events.

Which newsletter that is belongs to the school's profile (FLYERS): at
Georgetown, the Office of Student Life's OSL Weekly Broadcast. It is a
Mailchimp campaign whose events exist only as pictures: 78 remote `<img>` on
a CDN, and a `text/plain` alternative that is pure
boilerplate with not one food word in it. `ingest/mail.py` is useless here --
`body_text()` returns tracking links -- so this module works from the HTML,
fetches the flyers, and reads them.

The input contract is deliberately **a .eml file on disk**. A scheduled Claude
session using the Gmail connector is the intended producer; saving the email by
hand is the guaranteed fallback. Neither needs code changes here.

Two rules the rest of the design leans on:

* **Python touches the 168KB of HTML; the review pass touches image files.**
  `pending()` emits a few hundred bytes per flyer. Nothing hands an email, a
  database or a scoring rule to a language model.
* **A reading is never a verdict.** OCR and the review pass report what the
  flyer *says*; `score.py` remains the only thing that decides whether that
  means free food, so `reason`/`signals` stay quotable and the existing tests
  keep their meaning.
"""

from __future__ import annotations

import email
import email.utils
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flyers                                        # noqa: E402
import ocr                                           # noqa: E402
import school                                        # noqa: E402
import store                                         # noqa: E402
from dateparse import find_event_datetime            # noqa: E402
from ingest.mail import guess_location               # noqa: E402
from network import annotate, worth_storing          # noqa: E402
from score import FOOD_WORD, _clean, score_event     # noqa: E402
import caption as captions                           # noqa: E402
import senders                                       # noqa: E402
import titles                                        # noqa: E402

IMG_RX = re.compile(r"<img[^>]*>", re.I)
ATTR_RX = {
    "src": re.compile(r'src\s*=\s*"([^"]+)"', re.I),
    "alt": re.compile(r'alt\s*=\s*"([^"]*)"', re.I),
    "width": re.compile(r'width\s*=\s*"?(\d+)', re.I),
    "style_w": re.compile(r"width:\s*(\d+)\s*px", re.I),
}



def _flyers() -> dict:
    """The profile's flyer newsletter, or {} for a school that has none."""
    return school.active().FLYERS or {}


def brand_lines() -> set:
    """Letterhead that appears on nearly every flyer and is never its title."""
    return {line.lower() for line in _flyers().get("letterhead", ())}

# Generic titles that mean nothing without the org that is holding them.
GENERIC_TITLES = re.compile(
    r"^(general body meeting|gbm|info session|information session|meeting|"
    r"kickoff|kick-?off|open house|social|mixer|first meeting|welcome)",
    re.I)

# A flyer's headline picks up debris: decorative bullets from the layout, the
# date set directly beneath it, and OCR noise off a stylised glyph.
LEAD_JUNK_RX = re.compile(r"^[^\w(\"']+")
TRAIL_JUNK_RX = re.compile(r"[\s:;,\-–—•~*_/\\|]+$")
TRAILING_DATE_RX = re.compile(
    r"\s+(?:on\s+)?(?:mon|tues?|wednes|thurs?|fri|satur|sun)day,?\s+"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{0,2}"
    r"(?:st|nd|rd|th)?\s*$", re.I)
TRAILING_ORG_VERB_RX = re.compile(
    r"\s+(invites?|presents?|welcomes?|hosts?|proudly)\s*$", re.I)
# A word of mostly punctuation and stray capitals is an OCR artefact.
NOISE_TOKEN_RX = re.compile(r"^[^\w]*[\"'`~^]+[^\w]*\w{0,2}$")

TITLE_TOP_FRACTION = 0.8       # titles live in the upper part of a flyer
HEIGHT_TOLERANCE = 0.35        # lines within this are the same type size
LINE_GAP_FACTOR = 1.2          # and this close vertically are one heading

MIN_OCR_CONF = 55
MIN_LINES = 4
MIN_WORDS = 15
MIN_DATE_CONF = 60


# --- reading the email ---------------------------------------------------

def is_broadcast(msg) -> bool:
    """Whether this is the school's flyer newsletter (FLYERS in its profile)."""
    flyers = _flyers()
    sender = (msg.get("From") or "").lower()
    subject = (msg.get("Subject") or "").lower()
    return (any(s in sender for s in flyers.get("senders", ()))
            or any(s in subject for s in flyers.get("subjects", ())))


def html_part(msg) -> str:
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""


# Mailchimp's caption block puts the same sentence in a text cell under the
# image. The alt attribute carries it on every image in a real broadcast and
# the cell on most; they agree where both exist. The cell is the fallback for
# an image whose alt was left empty.
CAPTION_CELL = re.compile(
    r'class="mcnTextContent"[^>]*>(.*?)</td>', re.I | re.S)
TAGS = re.compile(r"<[^>]+>")


def caption_after(html: str, position: int) -> str:
    """The caption cell that follows the image at `position`, if any."""
    window = html[position:position + 1200]
    match = CAPTION_CELL.search(window)
    if not match:
        return ""
    return _clean(unescape(TAGS.sub(" ", match.group(1))))[:400]


def flyer_blocks(html: str) -> list:
    """Every candidate image in the email, de-duplicated, in document order."""
    out, seen = [], set()
    html = html or ""
    for match in IMG_RX.finditer(html):
        tag = match.group(0)
        src = (ATTR_RX["src"].search(tag) or [None, ""])[1]
        if not src or src in seen:
            continue
        seen.add(src)
        width = (ATTR_RX["width"].search(tag)
                 or ATTR_RX["style_w"].search(tag) or [None, None])[1]
        out.append({
            "url": src,
            "caption": caption_after(html, match.end()),
            # The organiser's own description of the event. Capped generously:
            # the longest in a real broadcast runs to ~305 characters, and the
            # tail is where the room number tends to sit.
            "alt": _clean((ATTR_RX["alt"].search(tag) or [None, ""])[1])[:400],
            "declared_w": int(width) if width else None,
        })
    return out


# What an organiser adds to a filename that is not part of the event's name.
FILE_NOISE = re.compile(
    r"\b(?:flyer|poster|graphic|image|final|v\d+|draft|updated?|"
    r"\d{1,2}[.\-/]\d{1,2}(?:[.\-/]\d{2,4})?|20\d\d)\b|[_\-]+", re.I)


def _filename_caption(name: str) -> str:
    """The event's name out of an attachment's filename, or "".

    "Community Solutions to Reducing Gun Violence Flyer.png" names its event;
    the word "Flyer" is not part of that name. Left on, it became a title --
    "World of Choices Flyer" was published beside the real World of Choices.
    """
    stem = os.path.splitext(name or "")[0]
    cleaned = _clean(FILE_NOISE.sub(" ", stem))
    # Two words or fewer is a label, not a name: "Flyer", "GBM 2".
    return cleaned[:400] if len(cleaned.split()) >= 3 else ""


def attached_flyers(msg, message_id: str = "") -> list:
    """Images attached to the message, as blocks with their bytes in hand.

    Most broadcasts host their artwork remotely and reference it with <img
    src>. A student org writing its own email attaches the flyer instead --
    Georgetown Defenders sent five that way, and `flyer_blocks` found none of
    them, because there was nothing to fetch.

    The filename is kept as the caption. Organisers name these things
    usefully: "Community Solutions to Reducing Gun Violence Flyer.png",
    "NOLA Interest Meeting 2026.jpg".
    """
    out = []
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if part.get_content_maintype() != "image":
            continue
        try:
            data = part.get_payload(decode=True) or b""
        except (TypeError, ValueError):
            continue
        if len(data) < flyers.MIN_BYTES:
            continue
        name = part.get_filename() or ""
        # A stable identity for the dedupe register. There is no URL, and the
        # bytes already hash to the flyer id, so this only has to be unique
        # and repeatable for the same attachment in the same message.
        ref = "attachment:%s/%s" % (message_id or "msg", name or len(data))
        out.append({
            "url": ref,
            "data": data,
            "caption": "",
            "alt": _filename_caption(name),
            "declared_w": None,
        })
    return out


def received_at(msg) -> datetime:
    """The email's Date header -- the anchor for every relative date.

    This must not fall back to `now()` silently: reading Monday's broadcast on
    Wednesday with the wrong anchor lands every "this Thursday" a week late.
    """
    raw = msg.get("Date")
    if raw:
        try:
            parsed = email.utils.parsedate_to_datetime(raw)
            return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        except (TypeError, ValueError):
            pass
    return datetime.now()


# --- turning a reading into event fields ---------------------------------

def _usable_lines(reading: dict, top_fraction: float = TITLE_TOP_FRACTION) -> list:
    out = []
    for line in (reading or {}).get("lines") or []:
        text = (line.get("text") or "").strip()
        if (line.get("y") or 0) > top_fraction:
            continue
        if text.lower().strip(" .,•~*-") in brand_lines():
            continue
        if sum(c.isalpha() for c in text) < 3:
            continue
        out.append(line)
    return out


def tidy(text: str) -> str:
    """Trim the debris a flyer headline collects, without rewording it."""
    text = _clean(text)
    text = LEAD_JUNK_RX.sub("", text)
    text = TRAILING_DATE_RX.sub("", text)      # the date sits under the title
    words = text.split()
    while words and NOISE_TOKEN_RX.match(words[-1]):
        words.pop()                            # OCR noise off a stylised glyph
    text = " ".join(words)
    return TRAIL_JUNK_RX.sub("", text).strip()


def _phrases(reading: dict) -> list:
    """Merge the flyer's lines back into the phrases it was set in.

    Vision returns one observation per line, so a heading typeset across three
    lines arrives as three fragments. Adjacent lines of similar type size are
    one phrase -- which is what stops a title reading "CLUB" and an org
    reading "LAW SOCIETY" when the flyer says "DANCE CLUB" and "ENVIRONMENTAL
    LAW SOCIETY".
    """
    lines = _usable_lines(reading)
    if not lines:
        return []
    groups, current = [], [lines[0]]
    for previous, line in zip(lines, lines[1:]):
        ph, h = previous.get("h") or 0, line.get("h") or 0
        gap = (line.get("y") or 0) - ((previous.get("y") or 0) + ph)
        same_size = abs(h - ph) <= HEIGHT_TOLERANCE * max(h, ph, 1e-6)
        adjacent = gap <= LINE_GAP_FACTOR * max(h, ph)
        if same_size and adjacent:
            current.append(line)
        else:
            groups.append(current)
            current = [line]
    groups.append(current)
    out = []
    for group in groups:
        text = tidy(" ".join(x["text"].strip() for x in group))
        if text:
            out.append({"text": text,
                        "height": max(x.get("h") or 0 for x in group),
                        "y": min(x.get("y") or 0 for x in group),
                        "words": len(text.split())})
    return out


def merged_title(reading: dict) -> str:
    """The flyer's headline: its biggest phrase, not its first line.

    The first line is the letterhead.
    """
    phrases = _phrases(reading)
    if not phrases:
        return ""
    best = max(phrases, key=lambda p: p["height"] * (1.3 if p["words"] >= 2 else 1.0))
    return best["text"][:140]


# Phrases that open with a verb or a connective are prose about the org, not
# its name.
SENTENCE_START_RX = re.compile(
    r"^(join|link|visit|come|see|scan|rsvp|sign|register|preparing|hosted|"
    r"presented|featuring|learn|meet|check|follow|our|the|this|all|for|with|"
    r"a|an)\b", re.I)

ORG_RX = re.compile(
    r"\b(society|association|club|committee|coalition|caucus|council|"
    r"alliance|union|chapter|organization|\w*lsa|\w*bsa|sba)\b", re.I)


def org_from(reading: dict) -> str:
    """The hosting org, read off the flyer's own branding.

    Scans merged phrases rather than raw lines, so a name set across two lines
    comes back whole, and picks the TALLEST candidate rather than the longest:
    branding is set larger than body copy, whereas the longest match is
    invariably a sentence that happens to mention the org ("Join an APALSA
    facilitated conversation about...").
    """
    title = merged_title(reading)
    best, best_height = "", -1.0
    for phrase in _phrases(reading):
        text = phrase["text"]
        # An organisation is a short noun phrase, not a sentence.
        if text == title or not 2 <= phrase["words"] <= 7 or len(text) < 6:
            continue
        if text.lower() in brand_lines() or not ORG_RX.search(text):
            continue
        if SENTENCE_START_RX.match(text):
            continue
        candidate = tidy(TRAILING_ORG_VERB_RX.sub("", text))
        if candidate and phrase["height"] > best_height:
            best, best_height = candidate, phrase["height"]
    return best[:80]


def qualify_title(title: str, org: str) -> str:
    """"General Body Meeting" says nothing without whose body it is."""
    if title and org and GENERIC_TITLES.match(title.strip()):
        if org.lower() not in title.lower():
            return f"{org}: {title}"[:140]
    return title


def needs_review(reading: dict, food: dict, date_conf: int, title: str,
                 qr_count: int, rsvp_url: str, title_issues=()):
    """Why this flyer should go to the review queue, or None if it is settled."""
    if not (reading or {}).get("ok"):
        return "vision could not read it"
    conf = ocr.confidence(reading)
    lines = reading.get("lines") or []
    words = sum(len((ln.get("text") or "").split()) for ln in lines)
    if conf < MIN_OCR_CONF:
        return f"low ocr confidence ({conf})"
    if len(lines) < MIN_LINES or words < MIN_WORDS:
        return "too little text recognised"
    if date_conf < MIN_DATE_CONF:
        return "no confident date"
    if not title:
        return "no title found"
    # A title that does not read like a title means the read went wrong
    # somewhere the confidence score cannot see -- Vision was 97% sure of
    # "FIRST TREET". Send it to be looked at rather than publishing it.
    if title_issues:
        return "title needs checking: " + "; ".join(title_issues[:2])
    # A garbled read scores 'unlikely' for the same reason a genuinely
    # food-free event does. When the words point at food but the verdict does
    # not, that is a read failure, not a verdict -- send it to be looked at.
    if food.get("tier") == "unlikely" and FOOD_WORD.search(ocr.text_of(reading)):
        return "food words but no food verdict"
    if qr_count and not rsvp_url:
        return "qr detected but not decoded"
    return None


def build_event(fid: str, title: str, text: str, start, end, all_day: bool,
                org: str, rsvp_url: str, week_of: str, source_id: str,
                location: str = "") -> dict:
    """Assemble an event dict. Scoring happens here and nowhere else.

    `location` is a room someone read off the flyer, and it has to reach the
    scorer. It used to be applied after this returned, so the verdict was
    computed against a location guessed from the text instead -- NOLA's
    "FREE FOOD!" flyer names McDonough 208 and says "Zoom / In Person", the
    guess found no room, "Zoom" made it online-only, and the food was dropped.
    """
    location = location or guess_location(text)
    verdict = score_event(title=title, description=text, location=location,
                          source="broadcast")
    event = {
        "source": "broadcast", "source_id": source_id,
        "title": title, "description": text[:4000], "location": location,
        "org": org, "url": "",
        "starts_at": start.isoformat(timespec="seconds") if start else None,
        "ends_at": end.isoformat(timespec="seconds") if end else None,
        "all_day": bool(all_day),
        "flyer_id": fid, "rsvp_url": flyers.valid_url(rsvp_url),
        **{k: verdict[k] for k in ("venue_class", "confidence", "tier",
                                   "has_food", "meals", "reason", "signals",
                                   "rsvp_required", "restricted")},
        **annotate(title, text, location, org),
    }
    return event


# --- tracking links ------------------------------------------------------

WRAPPER_KEYS = ("url", "u", "redirect", "target", "destination", "link", "r")


def unwrap(url: str) -> str:
    """Pull a real destination out of a generic click-wrapper, if it is there.

    Works for ESPs that put the destination in a query parameter. It will not
    work on `click.info.georgetown.edu`, whose `qs=` is an opaque token -- that
    is expected, and why the QR code is the primary RSVP source.
    """
    if not flyers.valid_url(url):
        return ""
    try:
        query = parse_qs(urlparse(url).query)
    except ValueError:
        return ""
    for key in WRAPPER_KEYS:
        for candidate in query.get(key, []):
            found = flyers.valid_url(unquote(candidate))
            if found:
                return found
    return ""


def resolve_tracking(url: str, timeout: int = 15) -> str:
    """Follow one redirect to find the true destination. OFF by default.

    The `qs=` token is per-recipient: resolving it tells Georgetown's mail
    platform that this person clicked this link at this time, and feeds their
    engagement scoring. A click you make is yours; a click the ingester makes
    at 3am is fabricated. HEAD does not help -- the redirect hit is what gets
    counted. Never call this on a URL that came out of a QR code.
    """
    if not flyers.valid_url(url):
        return ""

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": flyers.UA})
    try:
        with opener.open(req, timeout=timeout) as resp:
            return flyers.valid_url(resp.headers.get("Location") or "")
    except urllib.error.HTTPError as exc:
        return flyers.valid_url(exc.headers.get("Location") or "")
    except (urllib.error.URLError, OSError, ValueError):
        return ""


# --- the run -------------------------------------------------------------

def run(conn=None, path: str = None, cfg: dict = None, limit: int = None) -> dict:
    """Process one .eml. Returns the usual ingester stats dict."""
    own = conn is None
    conn = conn or store.connect()
    cfg = cfg or {}
    stats = {"source": "broadcast", "found": 0, "added": 0, "updated": 0,
             "flyers": 0, "queued": 0, "error": None}
    try:
        with open(path, "rb") as fh:
            msg = email.message_from_bytes(fh.read())
        html = html_part(msg)
        if not html:
            raise RuntimeError("no text/html part; nothing to read")

        # Vetted before a single image is fetched. A flyer pipeline pointed at
        # the wrong mailbox would download and publish whatever it found, and
        # the fetching is the irreversible half.
        verdict = senders.classify(msg, html_part(msg)[:4000])
        if not verdict["allow"]:
            stats["error"] = "not published: " + verdict["reason"]
            store.log_run(conn, "broadcast", 0, 0, 0, stats["error"])
            if own:
                conn.close()
            return stats

        message_id = (msg.get("Message-ID") or os.path.basename(path)).strip("<> ")
        ref = received_at(msg)
        week_of = ref.strftime("%Y-%m-%d")

        blocks = [b for b in flyer_blocks(html) if flyers.looks_worth_fetching(b)]
        # Attachments carry their own bytes, so the pre-fetch gate -- which
        # judges an <img> tag's markup -- has nothing to say about them.
        blocks += attached_flyers(msg, message_id)
        if limit:
            blocks = blocks[:limit]
        stats["found"] = len(blocks)

        fresh, known = [], []
        for block in blocks:
            url = block["url"]
            existing = flyers.known_url(conn, url)
            if existing:
                flyers.note_url(conn, url, existing)
                # The caption still comes through for a flyer we already have.
                # Artwork is not re-read -- that is what keeps a recurring
                # "this Thursday" pinned to one date -- but the organiser's
                # own words are cheap to take and may not have been there
                # (or may not have been parsed) the first time round.
                flyers.record(conn, {"id": existing, "url": url,
                                     "caption": block.get("caption")
                                     or block.get("alt") or ""})
                known.append(existing)
                continue
            data = block.get("data")
            if data is None:
                data = flyers.fetch(url)
                time.sleep(flyers.FETCH_GAP)
            if not data:
                continue
            meta = flyers.store_bytes(data)
            weeks = flyers.note_url(conn, url, meta["id"])
            meta.update(url=url, message_id=message_id, week_of=week_of,
                        declared_w=block.get("declared_w"), seen_weeks=weeks,
                        caption=block.get("caption") or block.get("alt") or "")
            meta["kind"] = flyers.classify(meta)
            if flyers.record(conn, meta) == "added":
                fresh.append(meta)
        stats["flyers"] = len(fresh)

        # Only new flyers are read. A known flyer keeps its stored date and
        # event, which is what stops a recurring "this Thursday" resolving to
        # a different Thursday in every weekly broadcast.
        paths = [flyers.paths(m["id"])["original"] for m in fresh]
        readings = ocr.read_images(paths) if paths else {}

        for meta in fresh:
            fid = meta["id"]
            reading = readings.get(flyers.paths(fid)["original"], {})
            kind = flyers.classify(meta, reading)
            flyers.save_reading(conn, fid, reading, kind=kind)
            if kind != "flyer":
                continue

            text = _clean(ocr.text_of(reading))
            mend = titles.repair(merged_title(reading), ref, "broadcast")
            title = qualify_title(mend["fixed"], org_from(reading))
            start, end, all_day, date_conf = find_event_datetime(text, ref)
            place = guess_location(text)

            # The caption is typed by the organiser; the flyer is pixels. So
            # the caption decides when and where, and the flyer keeps the job
            # it is good at -- the wording that promises food, and the artwork
            # a reader can check. Where they disagree, neither wins silently.
            said = captions.read(meta.get("caption") or "", ref)
            changes, clash = captions.ground(
                {"starts_at": start.isoformat() if start else "",
                 "location": place}, said, MIN_DATE_CONF)
            if changes.get("starts_at"):
                start = datetime.fromisoformat(changes["starts_at"])
                all_day, date_conf = False, said["date_confidence"]
                end = (datetime.fromisoformat(changes["ends_at"])
                       if changes.get("ends_at") else None)
            place = changes.get("location", place)
            if said["title"]:
                title = said["title"]

            food = score_event(title=title, description=f"{text} {said['text']}",
                               location=place)
            rsvp = next((flyers.valid_url(p) for p in ocr.payloads(reading)
                         if flyers.valid_url(p)), "")

            why = needs_review(reading, food, date_conf, title,
                               len(ocr.payloads(reading)), rsvp,
                               mend["issues"] + clash)
            if why:
                conn.execute("UPDATE flyers SET review_note = ? WHERE id = ?",
                             (why, fid))
                conn.commit()
                stats["queued"] += 1
                continue

            event = build_event(fid, title, text, start, end, all_day,
                                org_from(reading), rsvp, week_of,
                                f"{message_id}#{fid}")
            if place:
                event["location"] = place
            if event["tier"] == "unlikely" and not worth_storing(event):
                continue
            stats[store.upsert(conn, event)] += 1
            row = conn.execute("SELECT id FROM events WHERE flyer_id = ?",
                               (fid,)).fetchone()
            if row:
                flyers.link_event(conn, fid, row[0])

        # Captions arrive with every run, including for artwork already on
        # file, so this is where a caption that was missing (or a parser that
        # has since improved) reaches the events it should have grounded.
        grounded = reground(conn, ref)
        stats["regrounded"] = grounded["changed"]
        stats["queued"] += grounded["queued"]

        conn.execute(
            "INSERT INTO broadcasts (message_id, subject, received_at, images, "
            "flyers, events, processed_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(message_id) DO UPDATE SET processed_at = excluded.processed_at",
            (message_id, (msg.get("Subject") or "")[:200], ref.isoformat(),
             stats["found"], stats["flyers"], stats["added"],
             datetime.now().isoformat(timespec="seconds")))
        conn.commit()
    except (OSError, RuntimeError, ValueError) as exc:
        stats["error"] = f"{type(exc).__name__}: {exc}"
    store.log_run(conn, "broadcast", stats["found"], stats["added"],
                  stats["updated"], stats["error"])
    if own:
        conn.close()
    return stats


# --- the review queue ----------------------------------------------------

def pending(conn, limit: int = 25) -> list:
    """Flyers waiting to be looked at. A few hundred bytes each, by design."""
    rows = conn.execute(
        "SELECT id, review_note, week_of, ocr_text, qr_payload FROM flyers "
        "WHERE review_note IS NOT NULL AND review_note != '' "
        "AND read_by != 'claude' ORDER BY first_seen DESC LIMIT ?",
        (limit,)).fetchall()
    out = []
    for row in rows:
        image = flyers.best_image(row["id"])
        if not image:
            continue
        out.append({"id": row["id"], "image": image,
                    "reason": row["review_note"], "week_of": row["week_of"],
                    "ocr_hint": (row["ocr_text"] or "")[:240],
                    "qr_payload": (row["qr_payload"] or "").splitlines()[:1]})
    return out


READING_KEYS = ("id", "title", "starts_at", "ends_at", "all_day", "location",
                "org", "food_text", "body_text", "rsvp_url", "unreadable",
                "not_an_event")
CAPS = {"title": 140, "location": 90, "org": 80, "food_text": 400,
        "body_text": 1500}


def _sanitise(raw: dict) -> dict:
    """Whitelist, clean and cap. Flyer content is untrusted input."""
    out = {}
    for key in READING_KEYS:
        if key not in raw:
            continue
        value = raw[key]
        if key in CAPS:
            out[key] = _clean(value)[:CAPS[key]]
        elif key in ("all_day", "unreadable", "not_an_event"):
            out[key] = bool(value)
        elif key in ("starts_at", "ends_at"):
            try:
                out[key] = datetime.fromisoformat(str(value)).isoformat(
                    timespec="seconds")
            except (TypeError, ValueError):
                continue
        elif key == "rsvp_url":
            out[key] = flyers.valid_url(value)
        else:
            out[key] = _clean(value)[:64]
    return out


def supersede(conn, flyer_id: str, keep) -> int:
    """Drop the events this flyer produced that the review pass did not keep.

    `keep` is every event id the pass just wrote for this flyer, not one id.
    Usually that is a single event and this removes the wrong-titled version
    it replaces. But a flyer can legitimately advertise several events -- a
    firm's "patent week" is one page with two talks on it -- and collapsing
    those to whichever was read last would silently drop the other.
    """
    keep = {keep} if isinstance(keep, str) else set(keep)
    stale = [r["id"] for r in conn.execute(
        "SELECT id FROM events WHERE flyer_id = ?", (flyer_id,))
        if r["id"] not in keep]
    for event_id in stale:
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
    if stale:
        # The flyer may still be pointing at one of the rows just deleted --
        # it was linked while both existed. A dangling link is silent: the
        # flyer simply stops being re-groundable, because the event it names
        # cannot be looked up any more.
        current = conn.execute(
            "SELECT event_id FROM flyers WHERE id = ?", (flyer_id,)).fetchone()
        if current is None or current["event_id"] in stale or not current["event_id"]:
            survivor = conn.execute(
                "SELECT id FROM events WHERE flyer_id = ? ORDER BY starts_at "
                "LIMIT 1", (flyer_id,)).fetchone()
            conn.execute("UPDATE flyers SET event_id = ? WHERE id = ?",
                         (survivor["id"] if survivor else "", flyer_id))
        conn.commit()
    return len(stale)


def apply_readings(conn, readings: list, cfg: dict = None) -> dict:
    """Store review-pass transcriptions and turn them into events."""
    stats = {"source": "broadcast-review", "found": 0, "added": 0,
             "updated": 0, "skipped": 0, "superseded": 0, "error": None}
    # flyer id -> every event this batch wrote for it. Cleared out at the end,
    # once the batch is complete, so a flyer carrying two events keeps both.
    kept = {}
    for raw in readings or []:
        if not isinstance(raw, dict):
            continue
        stats["found"] += 1
        reading = _sanitise(raw)
        fid = reading.get("id") or ""
        row = flyers.get(conn, fid)
        if row is None:
            stats["skipped"] += 1
            continue

        conn.execute("UPDATE flyers SET reading = ?, read_by = 'claude', "
                     "reviewed_at = ?, review_note = '' WHERE id = ?",
                     (json.dumps(reading),
                      datetime.now().isoformat(timespec="seconds"), fid))
        conn.commit()

        if reading.get("unreadable") or reading.get("not_an_event"):
            stats["skipped"] += 1
            continue
        title = reading.get("title") or ""
        if not title:
            stats["skipped"] += 1
            continue

        text = " ".join(x for x in (reading.get("food_text"),
                                    reading.get("body_text")) if x)
        ref = datetime.fromisoformat(row["week_of"]) if row["week_of"] else None
        start = end = None
        all_day = bool(reading.get("all_day"))
        if reading.get("starts_at"):
            start = datetime.fromisoformat(reading["starts_at"])
            if reading.get("ends_at"):
                end = datetime.fromisoformat(reading["ends_at"])
        else:
            start, end, all_day, conf = find_event_datetime(text, ref)
            if start is None or conf < MIN_DATE_CONF:
                stats["skipped"] += 1
                continue

        event = build_event(
            fid, title, text, start, end, all_day,
            reading.get("org") or "",
            reading.get("rsvp_url") or row["rsvp_url"] or "",
            row["week_of"] or "", f"review#{fid}",
            location=reading.get("location") or "")
        stats[store.upsert(conn, event)] += 1
        # One flyer, one event. A review pass exists to correct a title, and
        # the fingerprint is built from the title -- so without this the
        # corrected event lands *beside* the wrong one it was meant to replace
        # and the page shows both.
        kept.setdefault(fid, set()).add(
            store.fingerprint(event.get("title", ""),
                              event.get("starts_at", "")))
        linked = conn.execute("SELECT id FROM events WHERE flyer_id = ?",
                              (fid,)).fetchone()
        if linked:
            flyers.link_event(conn, fid, linked[0])
    for flyer_id, keep in kept.items():
        stats["superseded"] += supersede(conn, flyer_id, keep)
    store.log_run(conn, "broadcast-review", stats["found"], stats["added"],
                  stats["updated"], stats["error"])
    return stats


def reground(conn, ref=None) -> dict:
    """Re-derive when and where from the captions already on file.

    Separate from `run()` on purpose. A run deliberately never re-reads known
    artwork, so a caption that arrives -- or a parser that improves -- after
    the flyer was first stored would otherwise never reach the event. This is
    the pass that applies it, and it is safe to repeat.

    It changes only what the organiser actually wrote down. A field the
    caption is silent about keeps whatever the flyer gave it.
    """
    stats = {"source": "broadcast-reground", "found": 0, "changed": 0,
             "queued": 0, "error": None}
    rows = conn.execute(
        "SELECT f.id, f.caption, f.week_of, f.event_id FROM flyers f "
        "WHERE COALESCE(f.caption, '') != '' AND COALESCE(f.event_id, '') != ''"
    ).fetchall()
    for row in rows:
        event = conn.execute("SELECT * FROM events WHERE id = ?",
                             (row["event_id"],)).fetchone()
        if event is None:
            continue
        stats["found"] += 1
        anchor = ref
        if anchor is None and row["week_of"]:
            try:
                anchor = datetime.fromisoformat(row["week_of"])
            except ValueError:
                anchor = None

        said = captions.read(row["caption"], anchor)
        changes, clash = captions.ground(dict(event), said, MIN_DATE_CONF)

        # A person who was in the room outranks both sources. Dropping the
        # change here rather than letting it write and undoing it afterwards
        # keeps `changed` honest: the pass reports what it really altered.
        ruled = store.corrected_fields(conn, event["id"])
        for field in ruled:
            changes.pop(field, None)
        # The caption reads one clock, so it offers a start and an end
        # together. Keeping the end after the start has been overruled would
        # staple the caption's 7:00 to the reader's 6:00 -- right here by luck,
        # since both say 7:00, and wrong the moment they differ.
        if "starts_at" in ruled and "ends_at" not in ruled:
            changes.pop("ends_at", None)
        # A change equal to what is already stored is not a change. Without
        # this the pass rewrote the same row on every run and reported it.
        changes = {k: v for k, v in changes.items()
                   if k not in event.keys() or (event[k] or "") != (v or "")}

        # A caption title only replaces a broken one. "INTERVIEWS" is what
        # OCR made of a flyer whose caption reads "CFLO Speed Interviews", and
        # that is worth fixing -- but a title the checker is happy with was
        # set from the artwork on purpose and is left alone.
        if said["title"] and said["title"] != (event["title"] or ""):
            current_ok = titles.assess(event["title"] or "", anchor,
                                       event["source"] or "")["ok"]
            if not current_ok and titles.assess(said["title"], anchor,
                                                "caption")["ok"]:
                changes["title"] = said["title"]

        if changes:
            # The fingerprint is built from the title and the start date, and
            # it is how a calendar entry for the same event finds this row and
            # merges instead of landing beside it. Correcting either without
            # recomputing it quietly breaks that -- the row stops being
            # findable by the thing it is a fingerprint of.
            if "title" in changes or "starts_at" in changes:
                fresh = store.fingerprint(
                    changes.get("title", event["title"] or ""),
                    changes.get("starts_at", event["starts_at"] or ""))
                taken = conn.execute(
                    "SELECT 1 FROM events WHERE fingerprint = ? AND id != ?",
                    (fresh, event["id"])).fetchone()
                if not taken:
                    changes["fingerprint"] = fresh
            sets = ", ".join(f"{k} = ?" for k in changes)
            conn.execute(f"UPDATE events SET {sets} WHERE id = ?",
                         list(changes.values()) + [event["id"]])
            stats["changed"] += 1
        if clash:
            conn.execute("UPDATE flyers SET review_note = ? WHERE id = ?",
                         ("caption and flyer disagree: " + "; ".join(clash[:2]),
                          row["id"]))
            stats["queued"] += 1
        if changes or clash:
            conn.commit()
    stats["corrections"] = store.apply_corrections(conn)
    return stats


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: broadcast.py <email.eml> [limit]")
        sys.exit(2)
    raw_cap = sys.argv[2].strip() if len(sys.argv) > 2 else ""
    cap = int(raw_cap) if raw_cap.isdigit() else None
    print(json.dumps(run(path=sys.argv[1], limit=cap), indent=1))
