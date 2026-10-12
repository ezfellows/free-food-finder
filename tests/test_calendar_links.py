"""What a reader can do with an event once they have found it.

Each card links to Google Calendar and Outlook with the event filled in. Links,
not a calendar file: a page in the artifact sandbox may hand a viewer images,
documents, archives and text, but not an .ics. The RSVP deadline counts down as
the page is read, and the footer names every source the events came from.
"""
import os
import sys
import unittest
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"

import json                   # noqa: E402

import publish                # noqa: E402
import school                 # noqa: E402

EVENT = {"title": "Pizza & Policy: \"What's Next?\" #2", "starts_at": "2026-10-15T12:30:00",
         "ends_at": "2026-10-15T13:45:00", "all_day": False,
         "place": "McDonough 208", "org": "Federalist Society",
         "rsvp_url": "https://forms.gle/example123"}


def query(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


class TestGoogleCalendar(unittest.TestCase):
    def test_a_timed_event_in_the_schools_own_zone(self):
        q = query(publish.calendar_links(EVENT, "America/New_York")["google"])
        self.assertEqual(q["action"], "TEMPLATE")
        self.assertEqual(q["dates"], "20261015T123000/20261015T134500")
        self.assertEqual(q["ctz"], "America/New_York")
        self.assertEqual(q["location"], "McDonough 208")

    def test_the_title_survives_its_punctuation(self):
        q = query(publish.calendar_links(EVENT)["google"])
        self.assertEqual(q["text"], EVENT["title"])

    def test_the_host_and_the_rsvp_go_in_the_details(self):
        q = query(publish.calendar_links(EVENT)["google"])
        self.assertIn("Hosted by Federalist Society", q["details"])
        self.assertIn("https://forms.gle/example123", q["details"])

    def test_no_end_means_an_hour(self):
        q = query(publish.calendar_links(dict(EVENT, ends_at=None))["google"])
        self.assertEqual(q["dates"], "20261015T123000/20261015T133000")

    def test_an_end_before_the_start_is_not_believed(self):
        q = query(publish.calendar_links(dict(EVENT, ends_at="2026-10-15T09:00:00"))["google"])
        self.assertEqual(q["dates"], "20261015T123000/20261015T133000")

    def test_an_all_day_event_ends_the_morning_after(self):
        q = query(publish.calendar_links(dict(EVENT, all_day=True, ends_at=None),
                                         "America/New_York")["google"])
        self.assertEqual(q["dates"], "20261015/20261016")
        self.assertNotIn("ctz", q)

    def test_no_zone_leaves_the_time_as_written(self):
        self.assertNotIn("ctz", query(publish.calendar_links(EVENT, "")["google"]))

    def test_no_start_no_links(self):
        self.assertEqual(publish.calendar_links(dict(EVENT, starts_at="")), {})


class TestOutlook(unittest.TestCase):
    def test_a_timed_event(self):
        q = query(publish.calendar_links(EVENT)["outlook"])
        self.assertEqual((q["startdt"], q["enddt"]),
                         ("2026-10-15T12:30:00", "2026-10-15T13:45:00"))
        self.assertEqual(q["subject"], EVENT["title"])
        self.assertEqual(q["rru"], "addevent")

    def test_an_all_day_event(self):
        q = query(publish.calendar_links(dict(EVENT, all_day=True))["outlook"])
        self.assertEqual((q["startdt"], q["enddt"], q["allday"]),
                         ("2026-10-15", "2026-10-16", "true"))


class TestOnThePage(unittest.TestCase):
    def test_every_event_carries_its_links(self):
        data = json.loads(publish.payload([dict(EVENT, id="e1", source_label="Email")])
                          .replace("<\\/", "</"))
        self.assertTrue(data[0]["cal"]["google"].startswith(publish.GOOGLE_CALENDAR))
        self.assertTrue(data[0]["cal"]["outlook"].startswith(publish.OUTLOOK_CALENDAR))

    def test_the_card_offers_them(self):
        self.assertIn("Google Calendar</a> or <a", publish.TEMPLATE)

    def test_the_deadline_is_worked_out_as_the_page_is_read(self):
        """It cannot be decided at build time: the page is read for days."""
        self.assertIn("function rsvpTag(e)", publish.TEMPLATE)
        self.assertIn('"RSVP closed"', publish.TEMPLATE)
        self.assertIn("var deadline = rsvpTag(e);", publish.TEMPLATE)


class TestTheFooterNamesEverySource(unittest.TestCase):
    def test_georgetown(self):
        text = publish.sources_html(school.load("georgetown_law"))
        self.assertTrue(text.startswith("Everything here is read out of the emails "
                                        "student organisations, school offices and "
                                        "employers send to the school, "))
        self.assertIn("public events calendar and the Office of Student Life weekly "
                      "broadcast, whose flyers are images and are read with OCR.", text)

    def test_a_school_with_mail_only(self):
        self.assertEqual(publish.sources_html(school.load(None)),
                         "Everything here is read out of the emails student "
                         "organisations, school offices and employers send to the "
                         "school.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
