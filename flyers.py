"""Flyer images: fetch, identify, classify, downscale, store.

The OSL Weekly Broadcast carries its events as pictures -- 78 remote images in
a single email, most of them genuine full-width flyers. This module turns those
URLs into files on disk with stable identities, and holds the SQL for the
`flyers` and `flyer_urls` tables.

Two deliberate constraints:

* **No image library.** There is no Pillow, OpenCV or ImageMagick on this
  machine and we are not adding one. Dimensions come from a short header
  reader written here (the same spirit as `makeicons.py`, which writes valid
  PNGs with nothing but `zlib` and `struct`), and resampling is delegated to
  `sips`, which ships with macOS.
* **This module never calls the OCR.** It stores whatever reading it is
  handed. That keeps it importable and fully testable on a machine with no
  Swift, and keeps the dependency graph acyclic.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import struct
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
FLYER_DIR = os.path.join(ROOT, "data", "flyers")

# A scheduled job runs with a minimal PATH, so resolve the tools we shell out
# to absolutely, with `which` only as a fallback for unusual installs.
SIPS = "/usr/bin/sips" if os.path.exists("/usr/bin/sips") else shutil.which("sips")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

MAX_BYTES = 8_000_000
MIN_BYTES = 2_000          # below this it is a spacer or a tracking pixel
FETCH_GAP = 0.2            # seconds between requests; be a good citizen

WEB_WIDTH, WEB_QUALITY = 900, 60     # shown on the page, and read by the review pass
CARD_WIDTH, CARD_QUALITY = 420, 45   # embedded in the published page
CARD_BUDGET = 70_000                 # bytes; retry harder if a card exceeds it

# Filenames that are never an event flyer.
CHROME_RX = re.compile(
    r"(logo|header|footer|spacer|divider|icon|bullet|social|facebook|"
    r"instagram|twitter|linkedin|youtube|pixel|clear\.gif)", re.I)

MIN_DECLARED_W = 400       # email body column is 563; chrome is much narrower
MAX_CHROME_W = 200
MIN_ASPECT, MAX_ASPECT = 0.35, 2.5
CHROME_AFTER_WEEKS = 3     # an image in this many broadcasts is furniture

# A flyer carries a poster's worth of words; a logo carries its own name.
FLYER_MIN_LINES = 6
FLYER_MIN_WORDS = 25


# --- identity and paths --------------------------------------------------

def flyer_id(data: bytes) -> str:
    """Content address. Same 16-hex convention as `store.fingerprint`."""
    return hashlib.sha256(data).hexdigest()[:16]


def paths(fid: str) -> dict:
    """Where a flyer's three renditions live. Nothing else builds these."""
    shard = os.path.join(FLYER_DIR, fid[:2])
    return {"dir": shard,
            "original": os.path.join(shard, f"{fid}.jpg"),
            "web": os.path.join(shard, f"{fid}.web.jpg"),
            "card": os.path.join(shard, f"{fid}.card.jpg")}


# --- dimensions, without an image library --------------------------------

def image_size(data: bytes):
    """(width, height) from a JPEG, PNG or GIF header, or None.

    Reads the bytes we already hold rather than shelling out per image, and --
    the reason it is worth writing -- it runs on any machine, so the
    classification rules that depend on it get real tests instead of being
    macOS-only.
    """
    if not data or len(data) < 12:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        if len(data) >= 24 and data[12:16] == b"IHDR":
            return struct.unpack(">II", data[16:24])
        return None
    if data[:3] == b"GIF":
        return struct.unpack("<HH", data[6:10])
    # WebP, which is what a screenshot or a Canva export arrives as now. The
    # LSDAD flyer came in this way and measured 0x0, so `classify` had nothing
    # to judge it on and fell back to "unknown" until OCR rescued it.
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X" and len(data) >= 30:
            # 24-bit little-endian, stored as width-1 and height-1.
            w = int.from_bytes(data[24:27], "little") + 1
            h = int.from_bytes(data[27:30], "little") + 1
            return w, h
        if chunk == b"VP8L" and len(data) >= 25:
            bits = int.from_bytes(data[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if chunk == b"VP8 " and len(data) >= 30:
            return (struct.unpack("<H", data[26:28])[0] & 0x3FFF,
                    struct.unpack("<H", data[28:30])[0] & 0x3FFF)
        return None
    if data[:2] != b"\xff\xd8":
        return None

    # JPEG: walk the marker segments looking for a Start Of Frame.
    i, n = 2, len(data)
    while i + 3 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if i + 4 > n:
            return None
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        # SOF0-SOF15 carry the dimensions; C4/C8/CC are tables, not frames.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if i + 9 > n:
                return None
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height
        if length < 2:
            return None
        i += 2 + length
    return None


def is_image(data: bytes) -> bool:
    """Trust magic bytes, never Content-Type.

    A CDN will happily serve an HTML error page as image/jpeg, and SVG is XML
    with a scripting surface -- not something to embed in a page we publish.
    """
    return bool(data) and (
        data[:3] == b"\xff\xd8\xff"
        or data[:8] == b"\x89PNG\r\n\x1a\n"
        or data[:4] == b"GIF8")


# --- url hygiene ---------------------------------------------------------

def valid_url(url: str) -> str:
    """Return the url if it is a plain http(s) link, else "".

    Every link reaching an href passes through here. QR payloads and flyer
    text are attacker-influenceable: anyone who can get a flyer into a
    broadcast controls that string.
    """
    if not url or not isinstance(url, str):
        return ""
    url = url.strip()
    if len(url) > 2000:
        return ""
    try:
        parts = urlparse(url)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return url


# --- what is worth fetching ----------------------------------------------

def looks_worth_fetching(block: dict) -> bool:
    """Cheap pre-fetch gate on the email's own markup.

    Deliberately permissive: fetching is cheap and OCR is free, so the
    definitive call is `classify()` after the text is known. This only skips
    what is obviously furniture.
    """
    url = (block or {}).get("url") or ""
    if not valid_url(url):
        return False
    if CHROME_RX.search(os.path.basename(urlparse(url).path)):
        return False
    declared = block.get("declared_w")
    if isinstance(declared, int):
        if declared <= MAX_CHROME_W:
            return False
        if declared >= MIN_DECLARED_W:
            return True
    return True                     # unknown width: let OCR decide


def classify(meta: dict, reading: dict = None) -> str:
    """'flyer' | 'logo' | 'banner' | 'unknown'.

    Text volume is the definitive signal and only exists after OCR: a flyer
    reads as a poster's worth of words, a logo as its own name.
    """
    meta = meta or {}
    # Only judge on size when a size is actually known. Treating absent
    # metadata as chrome would silently discard flyers whose headers we
    # failed to read -- absence of evidence is not evidence of a logo.
    if "byte_size" in meta and (meta["byte_size"] or 0) < MIN_BYTES:
        return "logo"
    if (meta.get("seen_weeks") or 0) >= CHROME_AFTER_WEEKS:
        return "logo"               # in every broadcast, so it is furniture

    width, height = meta.get("width") or 0, meta.get("height") or 0
    if width and height:
        aspect = width / height
        if aspect > MAX_ASPECT or aspect < MIN_ASPECT:
            return "banner"

    if reading and reading.get("ok"):
        lines = reading.get("lines") or []
        words = sum(len((ln.get("text") or "").split()) for ln in lines)
        if len(lines) >= FLYER_MIN_LINES and words >= FLYER_MIN_WORDS:
            return "flyer"
        if len(lines) <= 3:
            return "logo"
        return "unknown"

    declared = meta.get("declared_w")
    if isinstance(declared, int) and declared >= MIN_DECLARED_W:
        return "flyer"              # provisional, pending OCR
    return "unknown"


# --- fetching and storing ------------------------------------------------

def fetch(url: str, timeout: int = 20):
    """Download one image. Returns bytes, or None for anything suspect."""
    if not valid_url(url):
        return None
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "image/*,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(MAX_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if len(data) > MAX_BYTES or len(data) < MIN_BYTES:
        return None
    return data if is_image(data) else None


def store_bytes(data: bytes) -> dict:
    """Write the original and its renditions. Returns metadata."""
    fid = flyer_id(data)
    p = paths(fid)
    os.makedirs(p["dir"], exist_ok=True)
    if not os.path.exists(p["original"]):
        tmp = p["original"] + ".part"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, p["original"])
    size = image_size(data) or (0, 0)
    downscale(p["original"], p["web"], WEB_WIDTH, WEB_QUALITY)
    if downscale(p["original"], p["card"], CARD_WIDTH, CARD_QUALITY):
        # A dense photographic flyer can blow the card budget; one harder pass.
        if os.path.getsize(p["card"]) > CARD_BUDGET:
            downscale(p["original"], p["card"], CARD_WIDTH, 30)
    return {"id": fid, "byte_size": len(data),
            "width": size[0], "height": size[1]}


def downscale(src: str, dest: str, width: int, quality: int) -> bool:
    """Resample with sips. False when sips is missing or the call fails.

    `--resampleWidth`, not `-Z`: `-Z` bounds the larger dimension, so a
    portrait flyer comes out at an unpredictable width, and width is what
    decides legibility in a single column.
    """
    if not SIPS or not os.path.exists(src):
        return False
    tmp = dest + ".part"
    try:
        subprocess.run(
            [SIPS, "--resampleWidth", str(width), "-s", "format", "jpeg",
             "-s", "formatOptions", str(quality), src, "--out", tmp],
            capture_output=True, timeout=45, check=False)
    except (OSError, subprocess.SubprocessError):
        _unlink(tmp)
        return False
    if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        _unlink(tmp)
        return False
    os.replace(tmp, dest)
    return True


def _unlink(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def best_image(fid: str) -> str:
    """The rendition to serve or to hand the review pass; "" if absent."""
    p = paths(fid)
    for key in ("web", "original"):
        if os.path.exists(p[key]):
            return p[key]
    return ""


# --- persistence ---------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def known_url(conn, url: str):
    """The flyer id this URL resolved to before, if we have seen it."""
    row = conn.execute("SELECT flyer_id FROM flyer_urls WHERE url = ?",
                       (url,)).fetchone()
    return row[0] if row else None


def note_url(conn, url: str, fid: str) -> int:
    """Record a URL sighting; returns how many broadcasts it has appeared in."""
    row = conn.execute("SELECT seen_weeks FROM flyer_urls WHERE url = ?",
                       (url,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO flyer_urls (url, flyer_id, first_seen, "
                     "last_seen, seen_weeks) VALUES (?,?,?,?,1)",
                     (url, fid, _now(), _now()))
        conn.commit()
        return 1
    weeks = (row[0] or 1) + 1
    conn.execute("UPDATE flyer_urls SET last_seen = ?, seen_weeks = ?, "
                 "flyer_id = ? WHERE url = ?", (_now(), weeks, fid, url))
    conn.commit()
    return weeks


def get(conn, fid: str):
    return conn.execute("SELECT * FROM flyers WHERE id = ?", (fid,)).fetchone()


def record(conn, meta: dict) -> str:
    """Insert a flyer, or note that a known one was seen again.

    Returns "added" or "seen". A known flyer is never re-read: its stored
    date and event are reused, which is what stops a recurring "THIS THURSDAY"
    flyer resolving to a different Thursday in every weekly broadcast.
    """
    fid = meta["id"]
    row = get(conn, fid)
    if row is not None:
        # A caption can arrive later than the flyer: the same artwork runs
        # again in a later broadcast with the text filled in. Take it when we
        # did not have one, never overwrite one we did.
        conn.execute("UPDATE flyers SET last_seen = ?, seen_count = seen_count + 1"
                     ", url = COALESCE(?, url)"
                     ", caption = CASE WHEN COALESCE(caption, '') = '' "
                     "THEN ? ELSE caption END WHERE id = ?",
                     (_now(), meta.get("url"), meta.get("caption", ""), fid))
        conn.commit()
        return "seen"
    fields = {
        "id": fid, "url": meta.get("url", ""),
        "message_id": meta.get("message_id", ""), "week_of": meta.get("week_of", ""),
        "byte_size": int(meta.get("byte_size") or 0),
        "width": int(meta.get("width") or 0), "height": int(meta.get("height") or 0),
        "declared_w": meta.get("declared_w"), "kind": meta.get("kind", "unknown"),
        "rsvp_raw": meta.get("rsvp_raw", ""), "caption": meta.get("caption", ""),
        "first_seen": _now(), "last_seen": _now(), "seen_count": 1,
    }
    cols = ", ".join(fields)
    conn.execute(f"INSERT INTO flyers ({cols}) "
                 f"VALUES ({', '.join('?' * len(fields))})", list(fields.values()))
    conn.commit()
    return "added"


def save_reading(conn, fid: str, reading: dict, kind: str = None) -> None:
    """Store a Vision reading: text, confidence, QR payloads."""
    import json
    lines = reading.get("lines") or []
    text = "\n".join((ln.get("text") or "") for ln in lines)
    confs = [ln.get("conf") or 0 for ln in lines]
    mean = int(round(100 * sum(confs) / len(confs))) if confs else 0
    barcodes = reading.get("barcodes") or []
    payloads = [b.get("payload") or "" for b in barcodes]
    rsvp = next((valid_url(p) for p in payloads if valid_url(p)), "")
    sets = {
        "ocr_text": text[:8000], "ocr_conf": mean,
        "ocr_lines": json.dumps(lines[:80]), "ocr_rev": reading.get("ocr_rev"),
        "qr_payload": "\n".join(payloads)[:2000], "qr_count": len(barcodes),
        "rsvp_url": rsvp, "read_by": "vision",
        "error": reading.get("error") or "",
    }
    if kind:
        sets["kind"] = kind
    assign = ", ".join(f"{k} = ?" for k in sets)
    conn.execute(f"UPDATE flyers SET {assign} WHERE id = ?",
                 list(sets.values()) + [fid])
    conn.commit()


def link_event(conn, fid: str, event_id: str) -> None:
    conn.execute("UPDATE flyers SET event_id = ? WHERE id = ?", (event_id, fid))
    conn.commit()


def prune(conn, originals_days: int = 30, cards_days: int = 120) -> dict:
    """Drop old bytes. 78 images a week is ~300MB a semester; not optional.

    Originals go first -- they are the bulk. Cards are ~30KB and are what you
    want if you ever question a past verdict, so they stay far longer.
    """
    stats = {"originals": 0, "cards": 0, "rows": 0}
    old = (datetime.now(timezone.utc) - timedelta(days=originals_days)).isoformat()
    older = (datetime.now(timezone.utc) - timedelta(days=cards_days)).isoformat()
    for row in conn.execute("SELECT id, last_seen FROM flyers").fetchall():
        fid, seen = row[0], row[1] or ""
        p = paths(fid)
        if seen < old:
            for key in ("original", "web"):
                if os.path.exists(p[key]):
                    _unlink(p[key])
                    stats["originals"] += 1
        if seen < older:
            if os.path.exists(p["card"]):
                _unlink(p["card"])
                stats["cards"] += 1
            if not any(os.path.exists(p[k]) for k in ("original", "web", "card")):
                conn.execute("DELETE FROM flyers WHERE id = ?", (fid,))
                stats["rows"] += 1
    conn.commit()
    return stats


def counts(conn) -> dict:
    """Summary for `./fff flyers doctor`."""
    out = {"total": 0, "by_kind": {}, "by_read": {}, "queued": 0}
    out["total"] = conn.execute("SELECT COUNT(*) FROM flyers").fetchone()[0]
    out["by_kind"] = dict(conn.execute(
        "SELECT COALESCE(kind,'unknown'), COUNT(*) FROM flyers GROUP BY 1"))
    out["by_read"] = dict(conn.execute(
        "SELECT COALESCE(read_by,'none'), COUNT(*) FROM flyers GROUP BY 1"))
    out["queued"] = conn.execute(
        "SELECT COUNT(*) FROM flyers WHERE review_note IS NOT NULL "
        "AND review_note != '' AND read_by != 'claude'").fetchone()[0]
    return out
