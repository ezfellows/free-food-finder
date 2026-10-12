"""Schema migration against a database created by an earlier release.

`_migrate` carries real weight now that flyer columns depend on it, and the
suite had never exercised it against an old schema. The traps it guards:

* `CREATE TABLE IF NOT EXISTS` leaves an existing table alone, so a column
  added to SCHEMA never reaches a database that already exists.
* An index declared in SCHEMA that references one of those columns fails
  outright on the old database -- indexes on migrated columns must be created
  inside `_migrate`, after the ALTERs.
* `upsert` has to carry the new columns in its payload AND in the
  fill-if-empty merge, or a second source silently blanks them.
"""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"
import store

# The events table exactly as it stood before flyer support. Pasted rather
# than generated: the point is to pin a real historical shape.
OLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            TEXT PRIMARY KEY,
    fingerprint   TEXT NOT NULL,
    source        TEXT NOT NULL,
    source_id     TEXT,
    title         TEXT NOT NULL,
    description   TEXT,
    location      TEXT,
    org           TEXT,
    url           TEXT,
    starts_at     TEXT,
    ends_at       TEXT,
    all_day       INTEGER DEFAULT 0,
    venue_class   TEXT,
    confidence    INTEGER DEFAULT 0,
    tier          TEXT,
    has_food      INTEGER DEFAULT 0,
    meals         TEXT,
    reason        TEXT,
    signals       TEXT,
    rsvp_required INTEGER DEFAULT 0,
    restricted    INTEGER DEFAULT 0,
    network_score  INTEGER DEFAULT 0,
    network_kind   TEXT,
    firms          TEXT,
    practice_areas TEXT,
    network_reason TEXT,
    first_seen    TEXT,
    last_seen     TEXT,
    notified_at   TEXT,
    dismissed     INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    device_id    TEXT PRIMARY KEY,
    venue        TEXT    DEFAULT 'law',
    min_tier     TEXT    DEFAULT 'likely',
    meals        TEXT    DEFAULT '[]',
    lead_minutes INTEGER DEFAULT 90,
    quiet_start  INTEGER,
    quiet_end    INTEGER,
    push_enabled INTEGER DEFAULT 0,
    updated_at   TEXT
);
"""

LEGACY_ROW = {
    "id": "legacy0000000001",
    "fingerprint": "legacy0000000001",
    "source": "livewhale",
    "title": "Panel With Lunch",
    "description": "Lunch will be provided.",
    "location": "Hotung 2000",
    "starts_at": "2099-03-01T12:00:00",
    "tier": "confirmed",
    "confidence": 95,
    "has_food": 1,
    "meals": '["lunch"]',
    "venue_class": "law",
}


def old_database():
    """A database as an earlier release left it, with one row in it."""
    path = os.path.join(tempfile.mkdtemp(), "old.db")
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    cols = ", ".join(LEGACY_ROW)
    conn.execute(f"INSERT INTO events ({cols}) "
                 f"VALUES ({', '.join('?' * len(LEGACY_ROW))})",
                 list(LEGACY_ROW.values()))
    conn.commit()
    conn.close()
    return path


class TestMigration(unittest.TestCase):
    def setUp(self):
        self.path = old_database()
        self.conn = store.connect(self.path)          # runs SCHEMA + _migrate

    def tearDown(self):
        self.conn.close()

    def columns(self, table="events"):
        return {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}

    def indexes(self):
        return {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'")}

    def test_flyer_columns_are_added(self):
        self.assertIn("flyer_id", self.columns())
        self.assertIn("rsvp_url", self.columns())

    def test_earlier_columns_still_added(self):
        """The network columns migrate the same way; don't regress them."""
        for column in ("network_score", "network_kind", "firms",
                       "practice_areas", "network_reason"):
            self.assertIn(column, self.columns())

    def test_index_on_a_migrated_column_exists(self):
        """Declaring this in SCHEMA would fail outright on the old database."""
        self.assertIn("idx_flyer", self.indexes())
        self.assertIn("idx_net", self.indexes())

    def test_new_tables_are_created(self):
        tables = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table in ("flyers", "flyer_urls", "broadcasts"):
            self.assertIn(table, tables)

    def test_legacy_row_survives_intact(self):
        row = self.conn.execute("SELECT * FROM events WHERE id = ?",
                                (LEGACY_ROW["id"],)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["title"], "Panel With Lunch")
        self.assertEqual(row["tier"], "confirmed")
        self.assertIsNone(row["flyer_id"])

    def test_migration_is_idempotent(self):
        self.conn.close()
        self.conn = store.connect(self.path)
        self.conn = store.connect(self.path)
        self.assertIn("flyer_id", self.columns())
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)


class TestUpsertCarriesFlyerFields(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(old_database())

    def tearDown(self):
        self.conn.close()

    def event(self, **over):
        ev = {"title": "Flyer Event", "starts_at": "2099-04-01T17:00:00",
              "source": "broadcast", "tier": "confirmed", "confidence": 90,
              "has_food": True, "meals": ["snacks"], "venue_class": "law",
              "flyer_id": "ab12cd34ef567890",
              "rsvp_url": "https://forms.gle/abc"}
        ev.update(over)
        return ev

    def stored(self):
        return self.conn.execute(
            "SELECT * FROM events WHERE title = 'Flyer Event'").fetchone()

    def test_round_trip(self):
        self.assertEqual(store.upsert(self.conn, self.event()), "added")
        row = self.stored()
        self.assertEqual(row["flyer_id"], "ab12cd34ef567890")
        self.assertEqual(row["rsvp_url"], "https://forms.gle/abc")

    def test_merge_does_not_blank_the_flyer(self):
        """A calendar entry merging in must not wipe the flyer it came with."""
        store.upsert(self.conn, self.event())
        store.upsert(self.conn, self.event(source="livewhale", flyer_id="",
                                           rsvp_url="", confidence=95))
        row = self.stored()
        self.assertEqual(row["flyer_id"], "ab12cd34ef567890")
        self.assertEqual(row["rsvp_url"], "https://forms.gle/abc")

    def test_a_later_flyer_can_fill_an_empty_field(self):
        store.upsert(self.conn, self.event(flyer_id="", rsvp_url=""))
        store.upsert(self.conn, self.event())
        self.assertEqual(self.stored()["flyer_id"], "ab12cd34ef567890")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestDedupeSlots(unittest.TestCase):
    """A newsletter restating an event another source already carries."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn = store.connect(os.path.join(self.tmp, "d.db"))

    def add(self, **kw):
        event = {"title": "x", "starts_at": "2026-09-23T15:30:00", "org": "ILSA",
                 "source": "mail", "tier": "possible", "confidence": 50}
        event.update(kw)
        store.upsert(self.conn, event)

    def titles(self):
        return sorted(r[0] for r in self.conn.execute("SELECT title FROM events"))

    def test_the_mail_copy_goes_and_the_flyer_stays(self):
        self.add(title="Interested in immigration & refugee law?",
                 source="broadcast", flyer_id="abc123")
        self.add(title="joint GBM with IRAP", source="mail")
        self.assertEqual(store.dedupe_slots(self.conn), 1)
        self.assertEqual(self.titles(), ["Interested in immigration & refugee law?"])

    def test_never_the_other_way_round(self):
        """Only mail is ever removed; it is the source that restates."""
        self.add(title="Flyer version", source="broadcast")
        store.dedupe_slots(self.conn)
        self.assertIn("Flyer version", self.titles())

    def test_two_different_talks_at_the_same_hour_both_survive(self):
        """The calendar feed labels everything with one placeholder org.

        An earlier rule matched any two events sharing a slot and an org, and
        deleted unrelated talks that merely started at noon on the same day.
        """
        self.add(title="G+JI Research Colloquium", org="live",
                 source="livewhale", starts_at="2026-10-08T12:00:00")
        self.add(title="Digital Accessibility Ethics", org="live",
                 source="livewhale", starts_at="2026-10-08T12:00:00")
        self.assertEqual(store.dedupe_slots(self.conn), 0)
        self.assertEqual(len(self.titles()), 2)

    def test_a_mail_event_nothing_else_covers_is_kept(self):
        self.add(title="Cohen Milstein: Federal Antitrust Law", source="mail",
                 starts_at="2026-09-23T17:00:00")
        self.assertEqual(store.dedupe_slots(self.conn), 0)
        self.assertEqual(len(self.titles()), 1)


class TestCorrections(unittest.TestCase):
    """A human ruling on one field, and what it is allowed to touch."""

    def setUp(self):
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "c.db"))
        store.upsert(self.conn, {"title": "Panel", "source": "broadcast",
                                 "starts_at": "2026-10-01T17:30:00",
                                 "tier": "confirmed"})
        self.eid = self.conn.execute("SELECT id FROM events").fetchone()["id"]

    def test_a_ruling_is_applied_when_it_is_recorded(self):
        store.record_correction(self.conn, self.eid, "starts_at",
                                "2026-10-01T18:00:00", "reader was there")
        row = self.conn.execute("SELECT starts_at FROM events WHERE id = ?",
                                (self.eid,)).fetchone()
        self.assertEqual(row["starts_at"], "2026-10-01T18:00:00")

    def test_reapplying_undoes_whatever_ran_in_between(self):
        store.record_correction(self.conn, self.eid, "location",
                                "McDonough Atrium", "reader was there")
        self.conn.execute("UPDATE events SET location = ? WHERE id = ?",
                          ("Hotung 2001", self.eid))
        self.conn.commit()
        self.assertEqual(store.apply_corrections(self.conn), 1)
        row = self.conn.execute("SELECT location FROM events WHERE id = ?",
                                (self.eid,)).fetchone()
        self.assertEqual(row["location"], "McDonough Atrium")
        # Nothing left to do, so a second pass reports no work.
        self.assertEqual(store.apply_corrections(self.conn), 0)

    def test_a_scored_field_cannot_be_ruled_on_by_hand(self):
        """Correcting a tier would hide the scorer's bug, not fix it."""
        for field in ("tier", "has_food", "network_kind", "id"):
            with self.assertRaises(ValueError):
                store.record_correction(self.conn, self.eid, field, "x")

    def test_a_ruling_keeps_the_fingerprint_in_step(self):
        store.record_correction(self.conn, self.eid, "title", "Evening Panel")
        row = self.conn.execute(
            "SELECT title, starts_at, fingerprint FROM events WHERE id = ?",
            (self.eid,)).fetchone()
        self.assertEqual(row["fingerprint"],
                         store.fingerprint(row["title"], row["starts_at"]))

    def test_a_ruling_on_a_vanished_event_is_ignored_not_fatal(self):
        store.record_correction(self.conn, self.eid, "location", "Hotung")
        self.conn.execute("DELETE FROM events WHERE id = ?", (self.eid,))
        self.conn.commit()
        self.assertEqual(store.apply_corrections(self.conn), 0)


class TestARetitledEventIsReadAgain(unittest.TestCase):
    """The email behind a corrected event is read again on every refresh.

    Retitling a mail event moved its fingerprint, the next read found nothing
    under the old one, and inserting it again under its old id failed on the
    primary key -- which aborted the whole mail pass.
    """

    def setUp(self):
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "r.db"))
        self.event = {"title": "Event featuring Ropes & Gray", "source": "mail",
                      "org": "Antitrust", "location": "McDonough 220",
                      "starts_at": "2026-09-30T17:30:00", "tier": "confirmed"}
        store.upsert(self.conn, dict(self.event))
        self.eid = self.conn.execute("SELECT id FROM events").fetchone()["id"]
        store.record_correction(self.conn, self.eid, "title",
                                "Ropes & Gray Panel and Reception",
                                "the OSL caption names it")

    def test_reading_the_email_again_does_not_fail(self):
        self.assertEqual(store.upsert(self.conn, dict(self.event)), "updated")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) n FROM events").fetchone()["n"], 1)

    def test_the_ruling_is_not_overwritten_in_between(self):
        store.upsert(self.conn, dict(self.event))
        row = self.conn.execute("SELECT title, fingerprint FROM events").fetchone()
        self.assertEqual(row["title"], "Ropes & Gray Panel and Reception")
        self.assertEqual(row["fingerprint"], store.fingerprint(
            "Ropes & Gray Panel and Reception", "2026-09-30T17:30:00"))
        self.assertEqual(store.apply_corrections(self.conn), 0)

    def test_unruled_fields_still_update(self):
        store.upsert(self.conn, dict(self.event, location="McDonough 200"))
        row = self.conn.execute("SELECT location FROM events").fetchone()
        self.assertEqual(row["location"], "McDonough 200")


class TestADismissalSurvivesReingest(unittest.TestCase):
    """Deleting a bad extraction does not stick -- the source email is re-read
    and the event comes back. A dismissal recorded as a correction does."""

    def setUp(self):
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "d.db"))
        self.event = {"title": "Date and Time", "source": "mail", "org": "APALSA",
                      "starts_at": "2026-10-01T19:00:00", "tier": "unlikely"}
        store.upsert(self.conn, dict(self.event))
        self.eid = self.conn.execute("SELECT id FROM events").fetchone()["id"]

    def test_it_needs_a_reason(self):
        with self.assertRaises(ValueError):
            store.record_correction(self.conn, self.eid, "dismissed", "1")

    def test_it_holds_when_the_source_sends_it_again(self):
        store.record_correction(self.conn, self.eid, "dismissed", "1",
                                "a form label, not an event")
        store.upsert(self.conn, dict(self.event))          # re-ingested
        store.apply_corrections(self.conn)
        row = self.conn.execute("SELECT dismissed FROM events WHERE id = ?",
                                (self.eid,)).fetchone()
        self.assertEqual(row["dismissed"], 1)

    def test_an_integer_ruling_is_not_reapplied_every_pass(self):
        """1 read back from the column never equalled the stored "1"."""
        store.record_correction(self.conn, self.eid, "has_food", "1",
                                "reported by the organiser")
        self.assertEqual(store.apply_corrections(self.conn), 0)
