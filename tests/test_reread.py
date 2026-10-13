"""Every refresh reads every email in the maildrop again. That must add nothing.

Through October 2026 each refresh stored seven email copies of events the
calendar or a flyer already listed, and then deleted them again as duplicates.
It also stored an email event from six weeks earlier, which the same refresh
then pruned. None of it reached the page, but every refresh reported eight
events added.
"""
import copy
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"

import json                   # noqa: E402

import store                  # noqa: E402
from ingest import mail       # noqa: E402


def soon(days, hour=12):
    return (datetime.now() + timedelta(days=days)).replace(
        hour=hour, minute=30, second=0, microsecond=0).isoformat()


def event(title, org, location, starts_at, source="mail"):
    return {"title": title, "org": org, "location": location,
            "starts_at": starts_at, "source": source, "tier": "likely",
            "confidence": 60}


class Maildrop:
    """Stands in for the maildrop: each email, and the events read out of it."""

    def __init__(self, **emails):
        self.emails = emails

    def read(self, directory, conn=None, log=None):
        out = []
        for key, events in self.emails.items():
            log[key] = {"key": key, "file": key + ".eml", "verdict": "admitted",
                        "events": [], "below": 0}
            # A fresh copy every time, as reading the file again gives.
            out += [dict(copy.deepcopy(ev), _mail_key=key) for ev in events]
        return out


class Reread(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "r.db"))
        self.addCleanup(self.conn.close)

    def list(self, *events):
        """What the calendar or a flyer already has."""
        for ev in events:
            store.upsert(self.conn, ev)

    def read(self, maildrop):
        with mock.patch.object(mail, "from_maildrop", maildrop.read):
            stats = mail.run(self.conn, {"mail": {"maildrop": "/nonexistent/maildrop"}})
        self.assertIsNone(stats["error"])
        return {k: stats[k] for k in ("added", "updated", "already_listed", "deduped")}

    def titles(self):
        return sorted(r["title"] for r in self.conn.execute("SELECT title FROM events"))

    def fed(self, key):
        row = self.conn.execute("SELECT events FROM mail_log WHERE mail_key = ?",
                                (key,)).fetchone()
        return json.loads(row["events"])


class TestAnEmailThatRepeatsAListing(Reread):
    def setUp(self):
        super().setUp()
        # The AASV meeting is credited to its own org on the flyer and to the
        # Office of Student Life in the bulletin; both say McDonough 200.
        self.list(event("AASV General Body Meeting", "Advocates Against Sexual Violence",
                        "McDonough 200", soon(4, 15), source="broadcast"),
                  # ILSA's flyer and newsletter share an org and a minute, no words.
                  event("Interested in immigration & refugee law?", "ILSA", "",
                        soon(5, 15), source="broadcast"))
        self.maildrop = Maildrop(
            bulletin=[event("Advocates Against Sexual Violence fall GBM",
                            "Office of Student Life", "McDonough 200", soon(4, 15))],
            ilsa=[event("joint GBM with IRAP", "ILSA", "", soon(5, 15))],
            antitrust=[event("Cohen Milstein: Federal Antitrust Law",
                             "Antitrust Law Society", "McDonough 140", soon(6, 17))])

    def test_is_never_stored(self):
        self.assertEqual(self.read(self.maildrop),
                         {"added": 1, "updated": 0, "already_listed": 2, "deduped": 0})
        self.assertEqual(self.titles(), ["AASV General Body Meeting",
                                         "Cohen Milstein: Federal Antitrust Law",
                                         "Interested in immigration & refugee law?"])

    def test_reading_everything_again_adds_nothing(self):
        self.read(self.maildrop)
        before = self.titles()
        self.assertEqual(self.read(self.maildrop),
                         {"added": 0, "updated": 1, "already_listed": 2, "deduped": 0})
        self.assertEqual(self.titles(), before)

    def test_the_mail_log_names_the_listing_it_repeats(self):
        """`./fff maillog` answers "was it caught?" with the event on the page."""
        self.read(self.maildrop)
        flyer = self.conn.execute("SELECT id FROM events WHERE title = ?",
                                  ("AASV General Body Meeting",)).fetchone()["id"]
        self.assertEqual(self.fed("bulletin"), [flyer])

    def test_a_dismissed_listing_holds_nothing_here_either(self):
        """10 Oct 2026: three flyer copies were dismissed in favour of the email."""
        flyer = self.conn.execute("SELECT id FROM events WHERE title = ?",
                                  ("AASV General Body Meeting",)).fetchone()["id"]
        store.record_correction(self.conn, flyer, "dismissed", "1", "duplicate")
        self.assertEqual(self.read(self.maildrop)["already_listed"], 1)
        self.assertIn("Advocates Against Sexual Violence fall GBM", self.titles())


class TestTheOrderThingsArrive(Reread):
    def test_a_copy_stored_before_its_listing_goes_once(self):
        maildrop = Maildrop(gcla=[event("GCLA Movie Night", "Georgetown Criminal Law "
                                        "Association", "McDonough 200", soon(3, 18))])
        self.assertEqual(self.read(maildrop)["added"], 1)
        # The calendar lists it the next day, under a different name.
        self.list(event("Criminal Law Movie Night", "law", "McDonough 200", soon(3, 18),
                        source="livewhale"))
        self.assertEqual(self.read(maildrop),
                         {"added": 0, "updated": 1, "already_listed": 0, "deduped": 1})
        self.assertEqual(self.read(maildrop),
                         {"added": 0, "updated": 0, "already_listed": 1, "deduped": 0})

    def test_a_merge_that_moves_the_listing_is_waited_for(self):
        """An email matching a calendar entry by title gives it its own room.

        The panel's calendar entry says McDonough 200; GIELS's email about it
        says McDonough 140, and wins. Judged before that merge, GCLA's movie
        night in McDonough 200 at the same minute would look like a repeat of
        the panel and never be listed.
        """
        when = soon(3, 18)
        self.list(event("Trade Remedies Panel", "law", "McDonough 200", when,
                        source="livewhale"))
        maildrop = Maildrop(
            gcla=[event("GCLA Movie Night", "Georgetown Criminal Law Association",
                        "McDonough 200", when)],
            giels=[event("Trade Remedies Panel", "Georgetown International Economic "
                         "Law Society", "McDonough 140", when)])
        self.assertEqual(self.read(maildrop),
                         {"added": 1, "updated": 1, "already_listed": 0, "deduped": 0})
        self.assertIn("GCLA Movie Night", self.titles())


class TestAnEventGoneBy(Reread):
    def test_is_not_stored_again(self):
        old = (datetime.now() - timedelta(days=store.KEEP_DAYS + 10)).isoformat(
            timespec="seconds")
        maildrop = Maildrop(gi_bill=[event("GI Bill paperwork deadline",
                                           "Military Law Society", "", old)])
        self.assertEqual(self.read(maildrop)["added"], 0)
        self.assertEqual(self.titles(), [])

    def test_expired_is_what_prune_deletes(self):
        def ago(days):
            return (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        for title, days in (("Gone", store.KEEP_DAYS + 1), ("Kept", store.KEEP_DAYS - 1)):
            self.list(event(title, "Some Org", "", ago(days), source="livewhale"))
        self.assertEqual((store.expired(ago(store.KEEP_DAYS + 1)),
                          store.expired(ago(store.KEEP_DAYS - 1)), store.expired(None)),
                         (True, False, False))
        store.prune(self.conn)
        self.assertEqual(self.titles(), ["Kept"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
