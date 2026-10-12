"""Tests for pulling event datetimes out of listserv prose."""
import sys, os, unittest
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"
from dateparse import find_event_datetime

REF = datetime(2026, 9, 2, 9, 0)          # a Wednesday


def parse(text, ref=REF):
    return find_event_datetime(text, ref)


class TestTypicalListservText(unittest.TestCase):
    def test_full_announcement(self):
        s, e, allday, conf = parse(
            "Join us Wednesday, Sept 9 from 12-1pm in Hotung 2000 for free pizza!")
        self.assertEqual(s, datetime(2026, 9, 9, 12, 0))
        self.assertEqual(e, datetime(2026, 9, 9, 13, 0))
        self.assertFalse(allday)
        self.assertGreater(conf, 80)

    def test_meridiem_carries_back_across_range(self):
        s, e, _, _ = parse("Lunch talk September 15, 12-1pm")
        self.assertEqual((s.hour, e.hour), (12, 13))

    def test_range_crossing_noon(self):
        """'11-1pm' means 11am to 1pm, not 11pm to 1pm."""
        s, e, _, _ = parse("Open house on October 3, 11-1pm")
        self.assertEqual((s.hour, e.hour), (11, 13))

    def test_noon_keyword(self):
        s, _, _, _ = parse("Panel on Sept 20 at noon")
        self.assertEqual(s.hour, 12)

    def test_numeric_date(self):
        s, _, _, _ = parse("Reception 9/18 at 5:30 PM")
        self.assertEqual((s.month, s.day, s.hour, s.minute), (9, 18, 17, 30))

    def test_relative_tomorrow(self):
        s, _, _, _ = parse("Free bagels tomorrow at 8:30am")
        self.assertEqual(s.date(), datetime(2026, 9, 3).date())

    def test_bare_weekday_resolves_forward(self):
        s, _, _, _ = parse("Pizza this Friday at 6pm")
        self.assertEqual(s.date(), datetime(2026, 9, 4).date())


class TestDisambiguation(unittest.TestCase):
    def test_explicit_date_beats_rsvp_weekday(self):
        """'RSVP by Friday' must not become the event date."""
        s, _, _, _ = parse("RSVP by Friday. The lunch is September 16 at 12pm.")
        self.assertEqual(s.date(), datetime(2026, 9, 16).date())

    def test_year_rolls_forward(self):
        s, _, _, _ = parse("Banquet January 20 at 6pm")
        self.assertEqual(s.year, 2027)

    def test_explicit_year_respected(self):
        s, _, _, _ = parse("Symposium March 4, 2027 at 9am")
        self.assertEqual((s.year, s.month, s.day), (2027, 3, 4))


class TestNoiseRejection(unittest.TestCase):
    def test_room_number_is_not_a_time(self):
        s, _, _, _ = parse("Talk on Sept 9 at 12pm in Room 200")
        self.assertEqual(s.hour, 12)

    def test_url_digits_ignored(self):
        s, _, _, _ = parse(
            "Details at https://law.georgetown.edu/2019/11/05/x. Event Sept 9, 12pm.")
        self.assertEqual((s.month, s.day, s.hour), (9, 9, 12))

    def test_weekday_abbrev_not_matched_inside_words(self):
        """'mon' in 'monitor', 'sat' in 'satisfaction' are not weekdays."""
        for word in ("We will monitor the situation.",
                     "To our satisfaction, the sunshine held."):
            self.assertEqual(parse(word)[0], None, word)

    def test_no_date_returns_nothing(self):
        s, e, allday, conf = parse("Free pizza in the student lounge while it lasts.")
        self.assertIsNone(s)
        self.assertEqual(conf, 0)

    def test_date_without_time_is_all_day(self):
        s, e, allday, conf = parse("Career fair on September 25 in the atrium.")
        self.assertTrue(allday)
        self.assertEqual(s.date(), datetime(2026, 9, 25).date())

    def test_garbage_does_not_raise(self):
        for bad in ("", "\x00\x01", "Feb 31 at 99:99pm", "13/45"):
            find_event_datetime(bad, REF)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestBareTimesOnFlyers(unittest.TestCase):
    """Flyers give times with no meridiem: "3:30-4:30 @ MCDONOUGH 425".

    Read literally that is half past three in the morning, which no law school
    event has ever been. Hours 1-7 resolve to the afternoon; 8-11 are left
    alone because morning events are real.
    """

    def test_bare_afternoon_hour_becomes_pm(self):
        self.assertEqual(parse("Meeting September 16 at 3:30")[0].hour, 15)

    def test_bare_range_yields_both_ends(self):
        start, end, _, _ = parse("Meeting September 16, 3:30-4:30 @ McDonough 425")
        self.assertEqual((start.hour, start.minute), (15, 30))
        self.assertEqual((end.hour, end.minute), (16, 30))

    def test_morning_hours_are_left_alone(self):
        self.assertEqual(parse("Breakfast September 16 at 9:00")[0].hour, 9)
        self.assertEqual(parse("Bagels September 16 at 8:30")[0].hour, 8)

    def test_noon_and_afternoon_24h_unchanged(self):
        self.assertEqual(parse("Talk September 16 at 12:30")[0].hour, 12)
        self.assertEqual(parse("Panel September 16 at 13:00")[0].hour, 13)
        self.assertEqual(parse("Panel September 16 at 18:00")[0].hour, 18)

    def test_explicit_meridiem_still_wins(self):
        self.assertEqual(parse("Meeting September 16 at 3:30pm")[0].hour, 15)
        self.assertEqual(parse("Meeting September 16 at 7:00am")[0].hour, 7)

    def test_evening_bare_range(self):
        start, end, _, _ = parse("Dinner September 16 5:15-6:30")
        self.assertEqual((start.hour, end.hour), (17, 18))


class TestARangeIsNotItsHalves(unittest.TestCase):
    """A range also matches as the two bare times inside it.

    `find_event_datetime` picks the time nearest the date it settled on, so
    when the date follows the time the standalone half won: "SAT 10 AM - 6 PM
    SEPT. 19" published the ADR Bootcamp as a 6pm event instead of a full
    Saturday. The halves are now dropped, so position cannot decide it.
    """

    REF = datetime(2026, 9, 11)

    def span(self, text):
        start, end, all_day, _ = find_event_datetime(text, self.REF)
        return (start.strftime("%H:%M") if start else "",
                end.strftime("%H:%M") if end else "")

    def test_the_date_may_follow_the_range(self):
        self.assertEqual(
            self.span("SAT 10 AM - 6 PM SEPT. 19 HOTUNG 2001"),
            ("10:00", "18:00"))

    def test_the_date_may_precede_the_range(self):
        self.assertEqual(self.span("SEPT. 19 SAT 10 AM - 6 PM"),
                         ("10:00", "18:00"))

    def test_the_range_may_be_written_without_spaces(self):
        self.assertEqual(self.span("SAT 10AM-6PM SEPT. 19"),
                         ("10:00", "18:00"))

    def test_a_lone_time_is_still_found(self):
        """The fix drops halves of a range, not every standalone time."""
        self.assertEqual(self.span("Sept 19 at 6 PM")[0], "18:00")

    def test_two_separate_times_still_compete_on_position(self):
        """Times that are not inside a range are unaffected by the change."""
        start, _ = self.span("doors 5:30 PM, talk 6:00 PM, Sept 19")
        self.assertIn(start, ("17:30", "18:00"))


class TestNoEndIsInvented(unittest.TestCase):
    """An end the source never stated is not published.

    The parser used to add an hour to any start with no end, and the page
    printed "5:30pm-6:30pm" for a reception whose invitation said only
    "5:30 pm".
    """

    def test_a_start_alone_has_no_end(self):
        start, end, _, _ = parse("Arnold & Porter Reception, Tuesday, "
                                 "October 6, 5:30 pm")
        self.assertEqual((start.hour, start.minute), (17, 30))
        self.assertIsNone(end)

    def test_a_stated_range_keeps_its_end(self):
        start, end, _, _ = parse("Panel, October 6, 12:00 pm - 1:30 pm")
        self.assertEqual((end.hour, end.minute), (13, 30))

    def test_a_calendar_entry_without_dtend_has_no_end(self):
        import icalparse
        start, end, _ = icalparse.event_times(
            {"DTSTART": "20261006T173000", "_params": {}})
        self.assertTrue(start.startswith("2026-10-06T17:30"))
        self.assertIsNone(end)
