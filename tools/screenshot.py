"""The README's screenshots, built from the made-up emails in samples/.

    python3 tools/screenshot.py          writes docs/screenshot-light.png
                                         and docs/screenshot-dark.png

The samples are dated September 2026, and the page hides an event once it is
over. So each email is copied with every date in it, header to body, moved
forward by whole weeks -- each date still falls on the weekday its email
names -- and the copies are read by the same code that reads real mail, with
Georgetown's profile, since the samples are written in its shape. The page is
built from that and photographed by Chrome, running headless with a profile
of its own, once in each colour scheme.

Nothing of yours is read: not data/config.json, not your event log, not your
mail. Needs Google Chrome, or CHROME set to another Chromium browser.
"""
import email.utils
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = tempfile.mkdtemp(prefix="fff-screenshot-")
os.environ["FFF_SCHOOL"] = "georgetown_law"
os.environ["FFF_CONFIG"] = os.path.join(WORK, "config.json")    # never written
os.environ["FFF_DB"] = os.path.join(WORK, "events.db")
sys.path.insert(0, ROOT)

import publish                  # noqa: E402
import store                    # noqa: E402
from ingest import mail         # noqa: E402

SAMPLES = os.path.join(ROOT, "samples")
WIDTH, HEIGHT = 880, 1465       # the browser window, in CSS pixels
SCHEMES = {"light": "--blink-settings=preferredColorScheme=1",
           "dark": "--force-dark-mode"}
CHROMES = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
           "google-chrome", "chromium", "chromium-browser")

MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")
DATE_HEADER = re.compile(r"^Date: (.+)$", re.M)
NAMED = re.compile(r"\b(%s) (\d{1,2})\b" % "|".join(MONTHS))     # September 9
NUMERIC = re.compile(r"\b(1[0-2]|[1-9])/(3[01]|[12][0-9]|[1-9])\b")  # 9/9


def sent(text: str) -> datetime:
    return email.utils.parsedate_to_datetime(DATE_HEADER.search(text).group(1))


def moved(text: str, weeks: int) -> str:
    """The email with every date in it `weeks` later."""
    year = sent(text).year

    def later(month, day):
        try:
            return date(year, month, day) + timedelta(weeks=weeks)
        except ValueError:
            return None

    def named(m):
        d = later(MONTHS.index(m.group(1)) + 1, int(m.group(2)))
        return f"{MONTHS[d.month - 1]} {d.day}" if d else m.group(0)

    def numeric(m):
        d = later(int(m.group(1)), int(m.group(2)))
        return f"{d.month}/{d.day}" if d else m.group(0)

    header = "Date: " + email.utils.format_datetime(sent(text) + timedelta(weeks=weeks))
    text = DATE_HEADER.sub(lambda m: header, text, count=1)
    return NUMERIC.sub(numeric, NAMED.sub(named, text))


def chrome() -> str:
    for candidate in (os.environ.get("CHROME"),) + CHROMES:
        if candidate and (os.path.exists(candidate) or shutil.which(candidate)):
            return candidate
    raise SystemExit("no Chrome found; set CHROME to a Chromium browser")


def main() -> int:
    out = os.path.abspath(sys.argv[1] if len(sys.argv) > 1
                          else os.path.join(ROOT, "docs"))
    names = sorted(n for n in os.listdir(SAMPLES) if n.endswith(".eml"))
    texts = {}
    for name in names:
        with open(os.path.join(SAMPLES, name), encoding="utf-8") as fh:
            texts[name] = fh.read()
    # The first email goes out a day ago at the earliest, so the events it
    # announces are still to come.
    first = min(sent(t) for t in texts.values())
    weeks = max(0, math.ceil((datetime.now(timezone.utc) - timedelta(days=1) - first)
                             / timedelta(weeks=1)))
    drop = os.path.join(WORK, "maildrop")
    os.makedirs(drop)
    for name, text in texts.items():
        with open(os.path.join(drop, name), "w", encoding="utf-8") as fh:
            fh.write(moved(text, weeks))

    conn = store.connect()
    stats = mail.run(conn, {"mail": {"maildrop": drop}})
    if stats["error"]:
        raise SystemExit("reading the samples failed: " + stats["error"])
    built = publish.build(conn)
    conn.close()
    # Folded, as it stays for a reader who has closed it once: the events
    # are the picture.
    html = built["html"].replace('<details class="about" id="about" open>',
                                 '<details class="about" id="about">', 1)
    page = os.path.join(WORK, "page.html")
    with open(page, "w", encoding="utf-8") as fh:
        fh.write(html)

    os.makedirs(out, exist_ok=True)
    for scheme, flag in SCHEMES.items():
        shot = os.path.join(out, f"screenshot-{scheme}.png")
        subprocess.run([chrome(), "--headless", "--hide-scrollbars", flag,
                        "--user-data-dir=" + os.path.join(WORK, "chrome-" + scheme),
                        "--no-first-run", "--no-default-browser-check",
                        "--force-device-scale-factor=2",
                        f"--window-size={WIDTH},{HEIGHT}",
                        # Time for the web fonts to arrive before the picture.
                        "--virtual-time-budget=10000",
                        "--screenshot=" + shot, "file://" + page],
                       check=True, capture_output=True, timeout=120)
        print(shot)
    print(f"{len(built['events'])} events from {len(names)} sample emails, "
          f"moved {weeks} weeks on")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
