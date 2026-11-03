"""The event log: what counts as the row an event was already stored in.

Every name, address and id here is invented.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"
import store                                         # noqa: E402


def event(**kw):
    base = {"title": "Pumpkin Carving Careers Panel TODAY!", "source": "mail",
            "source_id": "gm-0000aaaa@gmail-connector",
            "starts_at": "2099-10-30T15:30:00", "description": "Candy and careers."}
    base.update(kw)
    return base


class TestARetitledEntryIsTheSameRow(unittest.TestCase):
    """The fingerprint is built from the title. When the way a title is read
    improves, the same entry of the same email must land in the same row --
    or it is listed twice, and without the rulings on the first."""

    def setUp(self):
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "store.db"))
        self.addCleanup(self.conn.close)
        store.upsert(self.conn, event())
        self.first = store.stored_id(self.conn, event())

    def rows(self):
        return self.conn.execute(
            "SELECT id, title, dismissed FROM events ORDER BY rowid").fetchall()

    def test_the_cleaner_title_updates_the_row(self):
        store.upsert(self.conn, event(title="Pumpkin Carving Careers Panel"))
        self.assertEqual([(r["id"], r["title"]) for r in self.rows()],
                         [(self.first, "Pumpkin Carving Careers Panel")])

    def test_a_ruling_on_the_row_survives(self):
        store.record_correction(self.conn, self.first, "dismissed", "1",
                                "the 30th is the application deadline")
        store.upsert(self.conn, event(title="Pumpkin Carving Careers Panel"))
        self.assertEqual([r["dismissed"] for r in self.rows()], [1])

    def test_two_sessions_of_one_email_stay_two(self):
        store.upsert(self.conn, event(title="Evening Session",
                                      starts_at="2099-10-30T18:00:00"))
        self.assertEqual(len(self.rows()), 2)

    def test_another_source_is_not_this_entry(self):
        store.upsert(self.conn, event(title="Careers Panel", source="livewhale"))
        self.assertEqual(len(self.rows()), 2)

    def test_an_ambiguous_match_is_not_taken(self):
        """Two rows already share the entry and the minute: neither is
        guessed at, and the event is stored as before."""
        store.upsert(self.conn, event(title="A Second Listing",
                                      source_id="gm-0000cccc@gmail-connector"))
        self.conn.execute("UPDATE events SET source_id = ?",
                          ("gm-0000bbbb@gmail-connector",))
        self.assertEqual(len(self.rows()), 2)
        store.upsert(self.conn, event(title="A Third Reading",
                                      source_id="gm-0000bbbb@gmail-connector"))
        self.assertEqual(len(self.rows()), 3)



class TestOnlyOneEntryIsTheSameRow(unittest.TestCase):
    """The fallback matches one entry's source id. Where a source id is
    shared -- an org page, a flyer with two talks -- two events stay two."""

    def setUp(self):
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "shared.db"))
        self.addCleanup(self.conn.close)

    def titles(self):
        return sorted(r[0] for r in self.conn.execute("SELECT title FROM events"))

    def test_two_events_on_one_org_page(self):
        for title in ("Kestrel Society Mixer", "Kestrel Society Book Talk"):
            store.upsert(self.conn, event(title=title, source="org",
                                          source_id="https://kestrel.example.org/events"))
        self.assertEqual(self.titles(), ["Kestrel Society Book Talk", "Kestrel Society Mixer"])

    def test_a_row_written_this_pass_is_not_matched(self):
        first = event(title="Patent Week: Litigation Talk", source="broadcast",
                      source_id="review#f00d")
        store.upsert(self.conn, first)
        mine = {store.stored_id(self.conn, first)}
        second = event(title="Patent Week: Prosecution Talk", source="broadcast",
                       source_id="review#f00d")
        self.assertEqual(store.stored_id(self.conn, second, exclude=mine), "")
        store.upsert(self.conn, second, exclude=mine)
        self.assertEqual(len(self.titles()), 2)

    def test_stored_id_finds_a_ruled_row_the_way_upsert_did(self):
        """A title ruling keeps the old fingerprint; the row is still found."""
        store.upsert(self.conn, event())
        eid = store.stored_id(self.conn, event())
        store.record_correction(self.conn, eid, "title", "Kestrel Careers Panel", "the flyer")
        store.apply_corrections(self.conn)
        retitled = event(title="Pumpkin Carving Careers Panel")
        self.assertEqual(store.upsert(self.conn, retitled), "updated")
        self.assertEqual(store.stored_id(self.conn, retitled), eid)



class TestARuledStartStillFindsItsRow(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "ruled.db"))
        self.addCleanup(self.conn.close)

    def test_a_retitled_reread_of_a_row_with_a_ruled_start(self):
        store.upsert(self.conn, event())
        eid = store.stored_id(self.conn, event())
        store.record_correction(self.conn, eid, "starts_at", "2099-10-30T16:00:00", "the flyer")
        retitled = event(title="Pumpkin Carving Careers Panel")
        self.assertEqual(store.upsert(self.conn, retitled), "updated")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        self.assertEqual(store.stored_id(self.conn, retitled), eid)

    def test_a_ruled_link_is_stored_as_checked(self):
        store.upsert(self.conn, event())
        eid = store.stored_id(self.conn, event())
        store.record_correction(self.conn, eid, "rsvp_url",
                                "  https://forms.gle/Kestrel123  ", "the club's form")
        self.assertEqual(self.conn.execute("SELECT rsvp_url FROM events WHERE id = ?",
                                           (eid,)).fetchone()[0], "https://forms.gle/Kestrel123")



class TestANamedRowIsTheRowMeant(unittest.TestCase):
    def test_a_flyer_reading_corrects_its_own_listing_not_an_emails(self):
        conn = store.connect(os.path.join(tempfile.mkdtemp(), "named.db"))
        self.addCleanup(conn.close)
        store.upsert(conn, event(title="Kestrel Open House", source="mail",
                                 source_id="m-1#0"))
        store.upsert(conn, event(title="FIRST TREET", source="broadcast",
                                 source_id="b#f1", flyer_id="f1"))
        mine = store.stored_id(conn, event(title="FIRST TREET", source="broadcast",
                                           source_id="b#f1"))
        store.upsert(conn, event(id=mine, title="Kestrel Open House", source="broadcast",
                                 source_id="review#f1", flyer_id="f1"))
        rows = {r[0]: r[1] for r in conn.execute("SELECT id, title FROM events")}
        self.assertEqual(rows[mine], "Kestrel Open House")
        self.assertEqual(len(rows), 2)        # the email's row is untouched; folded later


if __name__ == "__main__":
    unittest.main(verbosity=2)
