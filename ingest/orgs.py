"""Generic web-page ingester for student org and department pages.

Org pages have no common format, so this tries two strategies per URL:

1. schema.org Event JSON-LD. Plenty of sites (WordPress event plugins,
   Squarespace, Wix) emit it, and when present it gives exact start times --
   far better than guessing from prose.
2. Failing that, split the visible text into blocks and keep any block that
   names both a date and food. Blocks are the unit because a single page
   usually lists many events, and scoring the whole page would smear the
   signals from one event across all of them.

Add pages in data/config.json:

    "orgs": [
      {"name": "Federalist Society", "url": "https://example.org/events"}
    ]
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime

sys.path.insert(0, __file__.rsplit("/", 2)[0])

import store                                        # noqa: E402
from dateparse import find_event_datetime           # noqa: E402
from network import annotate, worth_storing         # noqa: E402
from score import _clean, score_event               # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

JSONLD_RX = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)
# Headings are NOT split points: a heading titles the block that follows it,
# so splitting there would orphan every title from its own event.
BLOCK_SPLIT_RX = re.compile(
    r"</(?:p|div|li|section|article|tr)>|<br\s*/?>\s*<br\s*/?>", re.I)
HEADING_RX = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.I | re.S)
MARK = "\x01"          # sentinel wrapping heading text through tag-stripping
TAG_RX = re.compile(r"<[^>]+>")
MAX_BYTES = 3_000_000


def fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(MAX_BYTES).decode(
            resp.headers.get_content_charset() or "utf-8", errors="replace")


def _walk_jsonld(node, out):
    """Collect every schema.org Event object, however deeply nested."""
    if isinstance(node, list):
        for item in node:
            _walk_jsonld(item, out)
    elif isinstance(node, dict):
        types = node.get("@type") or ""
        types = types if isinstance(types, list) else [types]
        if any("event" in str(t).lower() for t in types) and node.get("name"):
            out.append(node)
        for value in node.values():
            if isinstance(value, (list, dict)):
                _walk_jsonld(value, out)


def _place_name(place) -> str:
    if isinstance(place, str):
        return place
    if isinstance(place, dict):
        name = place.get("name") or ""
        addr = place.get("address")
        if isinstance(addr, dict):
            addr = " ".join(str(addr.get(k, "")) for k in
                            ("streetAddress", "addressLocality"))
        return _clean(f"{name} {addr or ''}")
    return ""


def from_jsonld(html: str, org: str, page_url: str) -> list:
    events = []
    for blob in JSONLD_RX.findall(html):
        try:
            data = json.loads(blob.strip())
        except ValueError:
            continue
        found = []
        _walk_jsonld(data, found)
        for node in found:
            title = _clean(node.get("name"))
            desc = _clean(node.get("description"))
            loc = _place_name(node.get("location"))
            start = _parse_iso(node.get("startDate"))
            if not title or not start:
                continue
            end = _parse_iso(node.get("endDate"))
            verdict = score_event(title=title, description=desc, location=loc,
                                  source="org", url=node.get("url") or page_url)
            events.append(_to_row(verdict, org, title, desc, loc,
                                  node.get("url") or page_url, start, end))
    return events


def _parse_iso(value):
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
                "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text[:len("2026-09-09T12:00:00+00:00")], fmt)
            return dt.replace(tzinfo=None)
        except ValueError:
            continue
    return None


def _to_row(verdict, org, title, desc, loc, url, start, end):
    return {
        "source": "org", "source_id": url, "title": title,
        "description": desc[:4000], "location": loc, "org": org, "url": url,
        "starts_at": start.isoformat(timespec="seconds"),
        "ends_at": end.isoformat(timespec="seconds") if end else None,
        "all_day": False,
        **{k: verdict[k] for k in ("venue_class", "confidence", "tier", "has_food",
                                   "meals", "reason", "signals", "rsvp_required",
                                   "restricted")},
        **annotate(title, desc, loc, org),
    }


def from_text_blocks(html: str, org: str, page_url: str, ref=None) -> list:
    """Fallback: score each visible text block on its own."""
    body = re.sub(r"<(script|style|nav|footer)[^>]*>.*?</\1>", " ", html,
                  flags=re.I | re.S)
    body = HEADING_RX.sub(lambda m: f" {MARK}{m.group(1)}{MARK} ", body)
    events, seen = [], set()
    for chunk in BLOCK_SPLIT_RX.split(body):
        text = _clean(TAG_RX.sub(" ", chunk))
        heading = ""
        parts = text.split(MARK)
        if len(parts) >= 3:
            heading = _clean(parts[1])[:90]
            text = _clean(" ".join(parts[0::2]) + " " + heading)
        text = text.replace(MARK, " ").strip()
        if not (40 <= len(text) <= 1200):
            continue
        verdict = score_event(title=text[:90], description=text, location="",
                              source="org", url=page_url)
        if not verdict["has_food"]:
            continue
        start, end, all_day, dconf = find_event_datetime(text, ref)
        if start is None or dconf < 60:
            continue                    # a date we cannot trust is worse than none
        title = (heading or text[:90]).rstrip(" .,-")
        key = (title.lower(), start.date())
        if key in seen:
            continue
        seen.add(key)
        row = _to_row(verdict, org, title, text, "", page_url, start, end)
        row["all_day"] = all_day
        events.append(row)
    return events


def run(conn=None, cfg=None, min_tier="likely") -> dict:
    own = conn is None
    conn = conn or store.connect()
    cfg = cfg or {}
    pages = cfg.get("orgs") or []
    stats = {"source": "orgs", "found": 0, "added": 0, "updated": 0, "error": None}
    errors = []
    rank = {"confirmed": 3, "likely": 2, "possible": 1, "unlikely": 0}

    for page in pages:
        url, name = page.get("url"), page.get("name") or ""
        if not url:
            continue
        try:
            html = fetch(url)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            errors.append(f"{name or url}: {type(exc).__name__}")
            continue
        found = from_jsonld(html, name, url) or from_text_blocks(html, name, url)
        stats["found"] += len(found)
        for ev in found:
            if (rank.get(ev["tier"], 0) < rank.get(min_tier, 2)
                    and not worth_storing(ev)):
                continue
            stats[store.upsert(conn, ev)] += 1

    if errors:
        stats["error"] = "; ".join(errors[:5])
    store.log_run(conn, "orgs", stats["found"], stats["added"], stats["updated"],
                  stats["error"])
    if own:
        conn.close()
    return stats


if __name__ == "__main__":
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "config.json")
    try:
        with open(path) as fh:
            config = json.load(fh)
    except (OSError, ValueError):
        config = {}
    print(run(cfg=config))
