"""Whether an event's clock can be trusted, and who said so.

The cases here are the real ones. APALSA First Look is the event whose email
caption said 5:30 and whose poster said 6:00, where the poster was right. ADR
Bootcamp is the opposite: the poster reads "SAT 10 AM - 6 PM" and OCR turned
that into a 6pm start, so the caption was right and the machine was wrong.
Both must be reported, and only one of them may be shown to a reader.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"

import store      # noqa: E402
import timing     # noqa: E402


class Harness(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.addCleanup(self.conn.close)

    def event(self, **kw):
        base = {"title": "Panel", "source": "broadcast", "tier": "confirmed",
                "starts_at": "2026-09-17T18:00:00",
                "ends_at": "2026-09-17T19:00:00"}
        base.update(kw)
        store.upsert(self.conn, base)
        return self.conn.execute(
            "SELECT * FROM events WHERE title = ?", (base["title"],)).fetchone()

    def flyer(self, event, caption="", reading=None, ocr_text="",
              week_of="2026-09-11"):
        self.conn.execute(
            "INSERT INTO flyers (id, event_id, caption, reading, ocr_text, "
            "week_of, kind) VALUES (?,?,?,?,?,?,?)",
            ("f" + event["id"][:8], event["id"], caption,
             json.dumps(reading) if reading else "", ocr_text, week_of,
             "flyer"))
        self.conn.execute("UPDATE events SET flyer_id = ? WHERE id = ?",
                          ("f" + event["id"][:8], event["id"]))
        self.conn.commit()


class TestReadingTheSources(Harness):
    def test_a_transcription_is_preferred_over_the_ocr_text(self):
        """Someone who looked at the poster beats a regex over its text."""
        event = self.event()
        self.flyer(event,
                   reading={"starts_at": "2026-09-17T18:00:00",
                            "ends_at": "2026-09-17T19:00:00"},
                   ocr_text="doors at 9 PM sharp")
        found = timing.readings(self.conn, event)
        self.assertEqual(found["flyer_from"], "transcription")
        self.assertEqual(found["flyer"]["start"][11:16], "18:00")

    def test_the_ocr_text_is_read_when_there_is_no_transcription(self):
        """Only 12 of 36 attached flyers carry one, so this is the usual path."""
        event = self.event()
        self.flyer(event, ocr_text="ADR BOOTCAMP SAT 10 AM - 6 PM SEPT. 19")
        found = timing.readings(self.conn, event)
        self.assertEqual(found["flyer_from"], "OCR")
        self.assertTrue(found["flyer"]["start"])

    def test_an_event_with_no_flyer_has_nothing_to_compare(self):
        found = timing.readings(self.conn, self.event())
        self.assertEqual(found["flyer"], {"start": "", "end": ""})
        self.assertEqual(found["caption"], {"start": "", "end": ""})


class TestDisagreement(Harness):
    def test_the_apalsa_case_is_reported(self):
        event = self.event(title="APALSA First Look")
        self.flyer(event,
                   caption="APALSA First Look, September 17 2026, "
                           "5:30-7:00PM, McDonough Atrium",
                   reading={"starts_at": "2026-09-17T18:00:00",
                            "ends_at": "2026-09-17T19:00:00"})
        found = timing.assess(self.conn, event)
        self.assertFalse(found["agree"])
        self.assertTrue(any("5:30pm" in i for i in found["issues"]))

    def test_a_wide_gap_is_named_as_the_known_ocr_failure(self):
        """A poster naming only its closing hour reads as an evening event.

        `dateparse` no longer splits "10 AM - 6 PM" into its halves, so the
        gap has to come from artwork that really does state one time -- which
        is the remaining shape of this failure.
        """
        event = self.event(title="ADR BOOTCAMP",
                           starts_at="2026-09-19T10:00:00",
                           ends_at="2026-09-19T18:00:00")
        self.flyer(event,
                   caption="ADR Bootcamp, September 19 2026, 10:00AM-6:00PM, "
                           "Hotung 2001",
                   ocr_text="ADR BOOTCAMP SEPT. 19 HOTUNG 2001 UNTIL 6 PM")
        found = timing.assess(self.conn, event)
        self.assertFalse(found["agree"])
        self.assertTrue(any("END time for its start" in n
                            for n in found["notes"]), found["notes"])

    def test_a_range_on_the_poster_now_agrees_with_the_caption(self):
        """The ADR Bootcamp case, after the parser stopped splitting ranges.

        The poster and the email said the same thing all along; only the
        reading of the poster disagreed.
        """
        event = self.event(title="ADR BOOTCAMP",
                           starts_at="2026-09-19T10:00:00",
                           ends_at="2026-09-19T18:00:00")
        self.flyer(event,
                   caption="ADR Bootcamp, September 19 2026, 10:00AM-6:00PM, "
                           "Hotung 2001",
                   ocr_text="ADR BOOTCAMP SAT 10 AM - 6 PM SEPT. 19 HOTUNG 2001")
        found = timing.assess(self.conn, event)
        self.assertEqual(found["issues"], [])
        self.assertTrue(found["agree"])

    def test_two_readings_on_different_days_are_not_a_time_dispute(self):
        """One poster for a week of events carries one reading.

        Comparing the Monday event against the Wednesday reading made SIPLA's
        Patent Week look 27 hours out from its own artwork.
        """
        event = self.event(title="Patent Week: Monday",
                           starts_at="2026-09-29T12:00:00",
                           ends_at="2026-09-29T13:00:00")
        self.flyer(event,
                   caption="Patent Week, September 29 2026, 12:00-1:00PM",
                   reading={"starts_at": "2026-09-30T15:30:00"})
        found = timing.assess(self.conn, event)
        self.assertEqual(found["issues"], [])

    def test_agreement_is_silence(self):
        event = self.event()
        self.flyer(event,
                   caption="Panel, September 17 2026, 6:00-7:00PM",
                   reading={"starts_at": "2026-09-17T18:00:00",
                            "ends_at": "2026-09-17T19:00:00"})
        found = timing.assess(self.conn, event)
        self.assertTrue(found["agree"])
        self.assertEqual(timing.note_for(found), "")


class TestWhatReachesTheReader(Harness):
    def test_a_transcribed_disagreement_is_shown(self):
        event = self.event(title="APALSA First Look")
        self.flyer(event,
                   caption="APALSA First Look, September 17 2026, 5:30-7:00PM",
                   reading={"starts_at": "2026-09-17T18:00:00"})
        note = timing.note_for(timing.assess(self.conn, event))
        self.assertIn("6:00pm", note)
        self.assertIn("5:30pm", note)

    def test_an_ocr_guess_is_never_quoted_to_a_reader(self):
        """Telling someone "the flyer reads 6:00pm" when it reads
        "10 AM - 6 PM" repeats a parsing bug as though it were printed."""
        event = self.event(title="ADR BOOTCAMP",
                           starts_at="2026-09-19T10:00:00",
                           ends_at="2026-09-19T18:00:00")
        self.flyer(event,
                   caption="ADR Bootcamp, September 19 2026, 10:00AM-6:00PM",
                   ocr_text="ADR BOOTCAMP SEPT. 19 UNTIL 6 PM")
        found = timing.assess(self.conn, event)
        self.assertTrue(found["issues"])          # still reported for review
        self.assertEqual(timing.note_for(found), "")   # but never published

    def test_a_ruling_ends_the_argument(self):
        event = self.event(title="APALSA First Look")
        self.flyer(event,
                   caption="APALSA First Look, September 17 2026, 5:30-7:00PM",
                   reading={"starts_at": "2026-09-17T18:00:00"})
        store.record_correction(self.conn, event["id"], "starts_at",
                                "2026-09-17T18:00:00", "reader was there")
        found = timing.assess(self.conn, event)
        self.assertEqual(timing.note_for(found), "")


class TestCompleteness(Harness):
    def test_a_missing_end_is_reported_when_a_source_gives_one(self):
        event = self.event(ends_at=None)
        self.flyer(event, caption="Panel, September 17 2026, 6:00-7:00PM")
        found = timing.assess(self.conn, event)
        self.assertTrue(any("no end time published" in i
                            for i in found["issues"]), found["issues"])

    def test_an_all_day_event_has_no_clock_to_check(self):
        """"12:00am" is a worse answer than saying nothing."""
        event = self.event(starts_at="2026-09-22T00:00:00", ends_at=None,
                           all_day=1)
        found = timing.assess(self.conn, event)
        self.assertEqual(found["span"], "all day")
        self.assertEqual(found["issues"], [])


class TestFormatting(unittest.TestCase):
    def test_a_span_prints_both_ends(self):
        self.assertEqual(
            timing._span("2026-09-17T18:00:00", "2026-09-17T19:00:00"),
            "6:00pm-7:00pm")

    def test_a_span_with_no_end_prints_the_start_alone(self):
        self.assertEqual(timing._span("2026-09-17T18:00:00", ""), "6:00pm")

    def test_an_end_before_its_start_is_not_a_span(self):
        self.assertEqual(
            timing._span("2026-09-17T18:00:00", "2026-09-17T17:00:00"),
            "6:00pm")


if __name__ == "__main__":
    unittest.main()


class TestARuledStartSettlesAllDay(unittest.TestCase):
    """An email gave the date and no time, so the event was stored all day; a
    ruling then gave it 12:30. The flag outlived the ruling, and the next
    refresh -- reading the same email -- set it back every time."""

    def setUp(self):
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "allday.db"))
        self.addCleanup(self.conn.close)
        self.lunch = {"title": "Leaders in Residence Lunch & Learn",
                      "starts_at": "2099-10-30T00:00:00", "all_day": True,
                      "source": "mail", "location": "McDonough 300"}
        store.upsert(self.conn, self.lunch)
        self.id = store.stored_id(self.conn, self.lunch)

    def all_day(self):
        return self.conn.execute("SELECT all_day FROM events WHERE id = ?",
                                 (self.id,)).fetchone()[0]

    def test_a_time_ruled_in_clears_it(self):
        store.record_correction(self.conn, self.id, "starts_at",
                                "2099-10-30T12:30:00", "the 13 Oct email gives 12:30")
        self.assertEqual(self.all_day(), 0)

    def test_and_the_email_read_again_does_not_set_it_back(self):
        store.record_correction(self.conn, self.id, "starts_at",
                                "2099-10-30T12:30:00", "the 13 Oct email gives 12:30")
        store.upsert(self.conn, self.lunch)
        self.assertEqual(self.all_day(), 0)
        self.assertEqual(store.apply_corrections(self.conn), 0)

    def test_a_flag_left_wrong_is_put_right_on_the_next_pass(self):
        store.record_correction(self.conn, self.id, "starts_at",
                                "2099-10-30T12:30:00", "the 13 Oct email gives 12:30")
        self.conn.execute("UPDATE events SET all_day = 1 WHERE id = ?", (self.id,))
        self.assertEqual(store.apply_corrections(self.conn), 1)
        self.assertEqual(self.all_day(), 0)

    def test_a_bare_date_makes_it_all_day(self):
        event = {"title": "Fall Fest", "starts_at": "2099-10-27T15:30:00",
                 "source": "mail"}
        store.upsert(self.conn, event)
        eid = store.stored_id(self.conn, event)
        store.record_correction(self.conn, eid, "starts_at", "2099-10-27",
                                "the time was a guess")
        row = self.conn.execute("SELECT all_day FROM events WHERE id = ?",
                                (eid,)).fetchone()
        self.assertEqual(row[0], 1)

    def test_midnight_leaves_it_as_it_was(self):
        store.record_correction(self.conn, self.id, "starts_at",
                                "2099-10-31T00:00:00", "moved a day")
        self.assertEqual(self.all_day(), 1)
