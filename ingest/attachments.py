"""Flyers attached to ordinary mail.

The SBA's Weekly Update attaches its flyers -- "Costume Contest 2026.png",
"Board Game Night & Trivia Mixer 11.2.26.pdf" -- and
`ingest/mail.py` reads only text, so those events reached the docket only when
a flyer newsletter happened to carry them too. This reads them with the flyer
pipeline: the same OCR, the same review queue, the same rules for links.

Three limits:

* **Only bytes already in the message.** A remote `<img>` is never fetched:
  in ordinary mail it is a sender's tracking pixel as often as a flyer, and
  the newsletter that does need fetching has its own reader.
* **Only mail the vetting admits.** The same verdict `ingest/mail.py` reaches,
  without logging the sender a second time.
* **Only the last two weeks.** Every refresh reads the maildrop again; an
  attachment already on file is skipped outright -- even recording it again
  would keep it in the review queue -- and one in older mail is a flyer for
  an event that is over, which nobody should be asked to review.
"""

from __future__ import annotations

import email
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flyers                                        # noqa: E402
import senders                                       # noqa: E402
import sources                                       # noqa: E402
import store                                         # noqa: E402
from ingest import broadcast, mail                   # noqa: E402

RECENT_DAYS = broadcast.REVIEW_DAYS

# Attachments that are somebody's papers, not a poster.
PRIVATE_FILE = re.compile(
    r"\b(?:resume|résumé|cv|transcript|invoice|receipt|boarding)\b", re.I)


def attachments_of(msg, message_id: str) -> list:
    """Image and PDF attachments as flyer blocks, PDFs rendered to a page."""
    out = [b for b in broadcast.attached_flyers(msg, message_id)
           if not PRIVATE_FILE.search(b["url"])]
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        name = part.get_filename() or ""
        if (part.get_content_type() != "application/pdf"
                and not name.lower().endswith(".pdf")):
            continue
        if PRIVATE_FILE.search(name):
            continue
        try:
            data = part.get_payload(decode=True) or b""
        except (TypeError, ValueError):
            continue
        png = flyers.pdf_to_png(data)
        if len(png) < flyers.MIN_BYTES:
            continue
        out.append({"url": "attachment:%s/%s" % (message_id or "msg", name or len(data)),
                    "data": png, "caption": "",
                    "alt": broadcast._filename_caption(name), "declared_w": None})
    return out


def admitted(conn, msg) -> bool:
    address = senders.address_of(msg.get("From", ""))
    return senders.classify(msg, mail.body_text(msg),
                            sources.standing(conn, address))["allow"]


def run(conn=None, cfg: dict = None, today: datetime = None) -> dict:
    own = conn is None
    conn = conn or store.connect()
    cfg = cfg if cfg is not None else mail.load_config()
    stats = {"source": "attachments", "found": 0, "added": 0, "updated": 0,
             "flyers": 0, "queued": 0, "error": None}
    since = (today or datetime.now()) - timedelta(days=RECENT_DAYS)
    drop = (cfg.get("mail") or {}).get("maildrop") or ""
    if drop and not os.path.isabs(drop):
        drop = os.path.join(mail.ROOT, drop)
    try:
        for name in sorted(os.listdir(drop) if os.path.isdir(drop) else []):
            if not name.lower().endswith(".eml"):
                continue
            with open(os.path.join(drop, name), "rb") as fh:
                msg = email.message_from_bytes(fh.read())
            # The flyer newsletter's attachments are read with the rest of it.
            if broadcast.is_broadcast(msg):
                continue
            ref = broadcast.received_at(msg)
            if ref < since:
                continue
            message_id = (msg.get("Message-ID") or name).strip().strip("<>").strip()
            blocks = [b for b in attachments_of(msg, message_id)
                      if not flyers.known_url(conn, b["url"])]
            if not blocks or not admitted(conn, msg):
                continue
            week_of = ref.strftime("%Y-%m-%d")
            fresh = []
            for block in blocks:
                stats["found"] += 1
                meta = flyers.store_bytes(block["data"])
                weeks = flyers.note_url(conn, block["url"], meta["id"])
                meta.update(url=block["url"], message_id=message_id, week_of=week_of,
                            declared_w=None, seen_weeks=weeks, caption=block["alt"])
                meta["kind"] = flyers.classify(meta)
                if flyers.record(conn, meta) == "added":
                    fresh.append(meta)
            stats["flyers"] += len(fresh)
            broadcast.read_flyers(conn, fresh, ref, message_id, week_of, stats)
    except (OSError, RuntimeError, ValueError) as exc:
        stats["error"] = f"{type(exc).__name__}: {exc}"
    store.log_run(conn, "attachments", stats["found"], stats["added"],
                  stats["updated"], stats["error"])
    if own:
        conn.close()
    return stats
