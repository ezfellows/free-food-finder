"""Provenance: every event must say which feed it came from."""
import os, sys, tempfile, unittest
from datetime import datetime, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"
import store
from docket import pretty_source


class TestSourceLabels(unittest.TestCase):
    def test_single_sources(self):
        self.assertEqual(pretty_source("livewhale"), "Calendar")
        self.assertEqual(pretty_source("mail"), "Email")
        self.assertEqual(pretty_source("org"), "Org page")
        self.assertEqual(pretty_source("broadcast"), "OSL flyer")

    def test_merged_sources(self):
        self.assertEqual(pretty_source("livewhale,mail"), "Calendar + Email")
        self.assertEqual(pretty_source("livewhale,mail,org"),
                         "Calendar + Email + Org page")

    def test_duplicates_collapse(self):
        self.assertEqual(pretty_source("mail,mail"), "Email")

    def test_unknown_source_passes_through(self):
        self.assertEqual(pretty_source("slack"), "slack")

    def test_empty(self):
        self.assertEqual(pretty_source(""), "")
        self.assertEqual(pretty_source(None), "")



class TestSourceSurvivesMerge(unittest.TestCase):
    """The same event seen twice should be one row crediting both feeds."""

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "t.db")
        self.conn = store.connect(self.path)

    def tearDown(self):
        self.conn.close()

    def base(self, **over):
        # Relative to now, not a fixed date. This read 2026-09-18T12:00, and
        # at 12:00 on 2026-09-18 it began failing: `store.upcoming` lists
        # events from now forward, so the fixture aged into the past and took
        # three tests with it.
        soon = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S")
        ev = {"title": "Immigration Law Panel", "starts_at": soon,
              "description": "short", "location": "Hotung 2000", "tier": "likely",
              "confidence": 50, "has_food": True, "meals": ["lunch"],
              "venue_class": "law", "source": "livewhale"}
        ev.update(over)
        return ev

    def test_two_feeds_collapse_to_one_row_with_both_sources(self):
        self.assertEqual(store.upsert(self.conn, self.base()), "added")
        self.assertEqual(
            store.upsert(self.conn, self.base(
                source="mail", description="a much longer description from the "
                                           "listserv email explaining the event",
                confidence=90, tier="confirmed")),
            "updated")
        rows = store.upcoming(self.conn, days=400, min_tier="likely")
        self.assertEqual(len(rows), 1)
        self.assertEqual(pretty_source(rows[0]["source"]), "Calendar + Email")

    def test_merge_keeps_richer_description_and_verdict(self):
        store.upsert(self.conn, self.base())
        store.upsert(self.conn, self.base(source="mail", confidence=90,
                                          tier="confirmed",
                                          description="x" * 200))
        row = store.upcoming(self.conn, days=400, min_tier="likely")[0]
        self.assertEqual(row["tier"], "confirmed")
        self.assertEqual(len(row["description"]), 200)

    def test_same_source_twice_is_not_duplicated(self):
        store.upsert(self.conn, self.base())
        store.upsert(self.conn, self.base())
        row = store.upcoming(self.conn, days=400, min_tier="likely")[0]
        self.assertEqual(pretty_source(row["source"]), "Calendar")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestConnectHonoursDbPath(unittest.TestCase):
    """Regression: a default argument bound DB_PATH at import, so tests wrote
    into the production database no matter what they set."""

    def test_reassigned_db_path_is_used(self):
        import tempfile
        original = store.DB_PATH
        target = os.path.join(tempfile.mkdtemp(), "redirected.db")
        try:
            store.DB_PATH = target
            conn = store.connect()
            conn.close()
            self.assertTrue(os.path.exists(target))
        finally:
            store.DB_PATH = original

    def test_explicit_path_still_wins(self):
        import tempfile
        target = os.path.join(tempfile.mkdtemp(), "explicit.db")
        store.connect(target).close()
        self.assertTrue(os.path.exists(target))


class TestUpcomingExcludesFinishedEvents(unittest.TestCase):
    """An event that ended this morning is not 'upcoming'; it rendered as
    'past' in the list, which is worse than not showing it at all."""

    def setUp(self):
        import tempfile
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "t.db"))

    def tearDown(self):
        self.conn.close()

    def add(self, title, when):
        store.upsert(self.conn, {
            "title": title, "starts_at": when.isoformat(timespec="seconds"),
            "tier": "confirmed", "confidence": 90, "has_food": True,
            "meals": ["lunch"], "venue_class": "law", "source": "livewhale"})

    def titles(self):
        return [e["title"] for e in store.upcoming(self.conn, days=30,
                                                   min_tier="likely")]

    def test_this_mornings_event_is_gone(self):
        from datetime import datetime, timedelta
        self.add("Finished hours ago", datetime.now() - timedelta(hours=6))
        self.assertNotIn("Finished hours ago", self.titles())

    def test_just_started_event_still_shows(self):
        from datetime import datetime, timedelta
        self.add("Started 30 min ago", datetime.now() - timedelta(minutes=30))
        self.assertIn("Started 30 min ago", self.titles())

    def test_future_event_shows(self):
        from datetime import datetime, timedelta
        self.add("Later today", datetime.now() + timedelta(hours=3))
        self.assertIn("Later today", self.titles())


class TestRoomDedupe(unittest.TestCase):
    """A bulletin's copy of an event another source already has.

    The org key cannot catch it: a relayed newsletter is credited to the
    relay, so AASV's meeting is "Advocates Against Sexual Violence" on its
    flyer and "Office of Student Life" in the broadcast carrying it. Both say
    McDonough 200 at 15:30.
    """

    def setUp(self):
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "d.db"))
        self.when = (datetime.now() + timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:00")

    def add(self, title, source, org, location):
        store.upsert(self.conn, {"title": title, "source": source, "org": org,
                                 "location": location, "starts_at": self.when,
                                 "tier": "likely"})

    def titles(self):
        return sorted(r["title"] for r in
                      self.conn.execute("SELECT title FROM events"))

    def test_the_mail_copy_goes(self):
        self.add("AASV General Body Meeting", "broadcast",
                 "Advocates Against Sexual Violence", "McDonough 200")
        self.add("Advocates Against Sexual Violence fall GBM", "mail",
                 "Office of Student Life", "McDonough 200")
        self.assertEqual(store.dedupe_slots(self.conn), 1)
        self.assertEqual(self.titles(), ["AASV General Body Meeting"])

    def test_a_different_room_is_a_different_event(self):
        self.add("ILSA GBM", "broadcast", "ILSA", "McDonough 140")
        self.add("AASV GBM", "mail", "Office of Student Life", "McDonough 200")
        self.assertEqual(store.dedupe_slots(self.conn), 0)
        self.assertEqual(len(self.titles()), 2)

    def test_the_rule_stays_one_directional(self):
        """Only the mail copy is ever removed."""
        self.add("Flyer copy", "broadcast", "Some Org", "McDonough 200")
        self.add("Calendar copy", "livewhale", "live", "McDonough 200")
        self.assertEqual(store.dedupe_slots(self.conn), 0)

    def test_a_dismissed_copy_holds_nothing(self):
        """10 Oct 2026: the flyer copies of MLS's Big Law Panel, GPLA's coffee
        talk and If/When/How's panel were dismissed in favour of the email
        copies -- and the next refresh deleted the email copies for sharing
        their slot, so all three left the page."""
        self.add("MLS BigLaw Panel", "broadcast", "Military Law Society", "McDonough 411")
        self.add("MLS Big Law Panel", "mail", "Military Law Society", "McDonough 411")
        flyer = self.conn.execute(
            "SELECT id FROM events WHERE source = 'broadcast'").fetchone()[0]
        store.record_correction(self.conn, flyer, "dismissed", "1", "duplicate")
        self.assertEqual(store.dedupe_slots(self.conn), 0)
        self.assertIn("MLS Big Law Panel", self.titles())

    def test_a_room_that_does_not_resolve_is_not_a_key(self):
        """"Georgetown Law" is a building with no room -- not a slot."""
        self.add("Flyer copy", "broadcast", "Some Org", "Georgetown Law")
        self.add("Mail copy", "mail", "Office of Student Life", "Georgetown Law")
        self.assertEqual(store.dedupe_slots(self.conn), 0)
