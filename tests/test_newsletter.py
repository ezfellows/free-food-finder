"""Pulling several events out of one round-up, and refusing the job board.

The body used here is a real Georgetown Plaintiffs' Law Association bulletin,
trimmed and with the sender's name removed. It is the right fixture because it
contains both things at once: five events worth publishing, and ten rows of a
job board whose closing dates would become ten fake events if anchored on.
"""

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"

import newsletter  # noqa: E402

REF = datetime(2026, 9, 14, 23, 2)

BULLETIN = (
    "Hi all, Great seeing you all at our first General Body Meeting. "
    "## Upcoming Events "
    "[Tomorrow!] Dana Whitfield: Representing plaintiffs in the Supreme Court "
    "9/15 Tues. 6:00 pm - 7:00 pm, McD 207. Food will be provided. RSVP Here. "
    "Bar Review with the People's Parity Project 9/17 Thurs. at the Admiral. "
    "Cohen Milstein: Federal Antitrust Law to Protect Workers' Rights 9/23/26 "
    "5:00 - 6:00 pm Hotung 2001. Hear Cohen Milstein associate discuss how the "
    "firm has used federal antitrust law. RSVP here. "
    "What Hiring Plaintiff Firms Are Looking For 9/24/26 4:00 pm Virtual Event. "
    "Join the American Association for Justice for a candid session. "
    "Burns Charest at OPICS Networking Chat 9/25/26. Meetings held virtually. "
    "Slots are available from 12 - 1:30 PM. "
    "## Professional Opportunities "
    "* Bredhoff & Kaiser - Rolling, 2L 2027 Summer Associate "
    "* Walkup, Melodia, Kelly & Schoenberger - Closes Sep 30, 2026 "
    "* Edelson PC - Opened Aug 1, unknown close date, 2L 2027 Summer Associate "
    "* Peggy Browning Fellowship - closes Sep 30, 2026, multiple "
    "* Goldfarb & Lipman - closes Oct 2, 2026, 2L 2027 Summer Associate "
    "## 1L Representatives Fill out this form by October 2nd at 5 pm to apply."
)


class TestSplittingABulletin(unittest.TestCase):
    def setUp(self):
        self.events = newsletter.split(BULLETIN, REF)
        self.titles = [e["title"] for e in self.events]

    def test_every_real_event_is_found(self):
        self.assertEqual(len(self.events), 5, self.titles)

    def test_each_one_keeps_its_own_name(self):
        for expected in ("Dana Whitfield", "Bar Review", "Cohen Milstein",
                         "What Hiring Plaintiff Firms", "Burns Charest"):
            self.assertTrue(any(expected in t for t in self.titles),
                            f"{expected} missing from {self.titles}")

    def test_each_one_keeps_its_own_date(self):
        days = sorted(e["starts_at"].strftime("%m-%d") for e in self.events)
        self.assertEqual(days, ["09-15", "09-17", "09-23", "09-24", "09-25"])

    def test_the_section_heading_is_not_part_of_the_first_title(self):
        """Mail-to-text loses the line break, welding the label to the event.

        Left on, the title reads as a newsletter heading and is discarded --
        and the first event in a section is usually the soonest one.
        """
        first = [t for t in self.titles if "Dana Whitfield" in t][0]
        self.assertNotIn("Upcoming Events", first)


class TestTheJobBoardIsRefused(unittest.TestCase):
    """Ten closing dates that would become ten events you cannot attend."""

    def test_no_firm_becomes_an_event(self):
        titles = [e["title"] for e in newsletter.split(BULLETIN, REF)]
        for firm in ("Bredhoff", "Walkup", "Edelson", "Peggy Browning",
                     "Goldfarb"):
            self.assertFalse(any(firm in t for t in titles),
                             f"{firm} was published as an event")

    def test_a_closing_date_is_recognised_as_a_deadline(self):
        for phrase in ("Applications close Sep 30, 2026",
                       "closes Oct 2, 2026", "due October 2",
                       "RSVP by September 21", "apply by Oct 1",
                       "Start date: October 29, 2026"):
            position = newsletter._anchors(phrase, REF)[0][0]
            self.assertTrue(newsletter.is_deadline(phrase, position), phrase)

    def test_but_a_real_date_is_not(self):
        for phrase in ("Panel on September 23 at 5pm",
                       "Reception 9/24/26 6:00 pm Hotung 2001"):
            position = newsletter._anchors(phrase, REF)[0][0]
            self.assertFalse(newsletter.is_deadline(phrase, position), phrase)


class TestAnEventBuriedInProse(unittest.TestCase):
    """Not every org mail is a list. Some announce one event mid-sentence."""

    BODY = ("Dear ILSA Members, Happy Wednesday! We noticed and fixed an issue "
            "with the RSVP form for our joint GBM with IRAP on Wednesday "
            "September 23 at 3:30 pm in McDonough 140. Please RSVP so we know "
            "how much pizza to order!")

    def test_the_event_is_found(self):
        events = newsletter.split(self.BODY, REF)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["starts_at"].strftime("%m-%d %H:%M"),
                         "09-23 15:30")

    def test_the_title_is_the_event_not_the_sentence(self):
        title = newsletter.split(self.BODY, REF)[0]["title"]
        self.assertIn("GBM", title)
        self.assertNotIn("fixed an issue", title)
        self.assertLess(len(title.split()), 8, title)

    def test_a_sentence_with_no_event_in_it_is_still_refused(self):
        body = ("Thank you all for coming along and helping out with "
                "everything we needed doing on September 23.")
        self.assertEqual(newsletter.split(body, REF), [])


class TestAnchoring(unittest.TestCase):
    def test_a_bare_weekday_never_starts_an_event(self):
        """Otherwise "Happy Monday" opens one in every newsletter sent."""
        self.assertEqual(newsletter._anchors("Happy Monday, hope you are well",
                                             REF), [])

    def test_one_event_written_with_a_range_is_one_event(self):
        text = "Symposium September 18-19, 2026 in Hart Auditorium"
        self.assertEqual(len(newsletter._anchors(text, REF)), 1)

    def test_an_empty_body_yields_nothing(self):
        self.assertEqual(newsletter.split("", REF), [])
        self.assertEqual(newsletter.split("no dates here at all", REF), [])


class TestTidy(unittest.TestCase):
    def test_table_and_markdown_wreckage_is_removed(self):
        messy = "| |\n| ## Event Name [](https://x.test/a) 9/23/26 5pm |\n| |"
        out = newsletter.tidy(messy)
        self.assertNotIn("|", out)
        self.assertNotIn("](http", out)
        self.assertIn("Event Name", out)


if __name__ == "__main__":
    unittest.main()


class TestTheContentsListDatesAnEntry(unittest.TestCase):
    """A bulletin states each date once, at the top.

    The entry below says only "3:30 to 4:15pm in McDonough 200. Free Insomnia
    Cookies" -- an hour and no calendar. With no date in its own text there is
    no anchor, so the splitter never saw it and the free cookies were
    invisible.
    """

    REF = datetime(2026, 9, 18)
    INDEX = ("9/23 - Advocates Against Sexual Violence - GBM\n"
             "9/23 - SIPLA - Mentorship Meetup\n\n")
    AASV = ("Advocates Against Sexual Violence fall General Body Meeting, "
            "3:30 to 4:15pm in McDonough 200. Free Insomnia Cookies, make a "
            "collage, and learn more about AASV. Contact zz768@georgetown.edu")

    def test_the_entry_is_dated_from_the_index(self):
        got = newsletter.split(self.INDEX + self.AASV, self.REF)
        self.assertEqual(len(got), 1, got)
        self.assertEqual(got[0]["starts_at"].date().isoformat(), "2026-09-23")
        self.assertEqual(got[0]["starts_at"].strftime("%H:%M"), "15:30")

    def test_the_food_survives_into_the_detail(self):
        got = newsletter.split(self.INDEX + self.AASV, self.REF)
        self.assertIn("Insomnia Cookies", got[0]["detail"])

    def test_an_entry_with_its_own_date_is_not_duplicated(self):
        body = (self.INDEX + "SIPLA Mentorship Meetup, September 23 from "
                "3:30 p.m. to 4:30 p.m. Hotung Patio.\n\n" + self.AASV)
        titles_found = [e["title"] for e in newsletter.split(body, self.REF)]
        self.assertEqual(len(titles_found), len(set(titles_found)))
        self.assertEqual(len(titles_found), 2, titles_found)

    def test_a_paragraph_the_index_does_not_name_is_left_alone(self):
        """The host's name has to appear, or any stray time would be dated."""
        body = self.INDEX + "Doors open at 5:00pm for the reading room."
        self.assertEqual(newsletter.split(body, self.REF), [])

    def test_no_index_means_no_rescue(self):
        self.assertEqual(newsletter.split(self.AASV, self.REF), [])


class TestALinkIsNeverATitle(unittest.TestCase):
    """"RSVP here (https://law-georgetown-csm.symplicity.com/...)" became the
    title of the first 1L Law Firm Reception, because the link sat between the
    event's name and its date."""

    def test_urls_and_link_words_are_stripped(self):
        self.assertEqual(
            newsletter._tidy_title(
                "RSVP here (https://law-georgetown-csm.symplicity.com/x)"), "")
        self.assertEqual(
            newsletter._tidy_title(
                "Firm Receptions https://example.com/rsvp"), "Firm Receptions")


class TestLabelsAreNotNames(unittest.TestCase):
    """What a newsletter prints around an event is not the event's name."""

    def test_a_leading_date_label_is_dropped(self):
        """"9/28: Big Law Basics" could never merge with a clean copy of the
        same event, because the label changed its fingerprint."""
        self.assertEqual(
            newsletter._tidy_title("9/28: Big Law Basics with King & Spalding"),
            "Big Law Basics with King & Spalding")
        self.assertEqual(newsletter._tidy_title("10/5 - Inside IP Litigation"),
                         "Inside IP Litigation")

    def test_a_form_label_is_not_a_title(self):
        import titles
        for label in ("Date and Time", "Location:", "When", "RSVP", "Dress code"):
            self.assertFalse(titles.assess(label, None, "mail")["ok"], label)

    def test_a_real_title_is_untouched(self):
        import titles
        self.assertTrue(titles.assess("Kirkland Coffee Cart", None, "mail")["ok"])


class TestCflosLayout(unittest.TestCase):
    """CFLO's newsletter: one line each for name, date, time and room.

    Taken from the 25 September 2026 issue, links removed. Two things went
    wrong on it. The dash in "12:00 pm - 1:30 pm" was read as a bullet, which
    broke the range in two, so every lunch panel ended at the parser's default
    hour instead of when CFLO said. And the section label "Other Firm Events &
    Programs" was published as the name of the Davis Polk event beneath it.
    """

    BODY = (
        "Life & Practice at Paul, Weiss\nThursday, October 1\n"
        "12:00 pm - 1:30 pm\nLocation: Gewirz 12\nRSVP\n** here\n\n"
        "Choosing a Practice Group with White & Case\nMonday, October 19\n"
        "6:00 pm- 8:30 pm\nLocation: GULC Campus (room TBD)\nRSVP ** here\n.\n\n"
        "Other Firm Events & Programs\n"
        "Investment Management Careers with Davis Polk\nTuesday, October 6th\n"
        "4:00-6:00pm\nLocation: Sport & Fitness Center Lobby\nRSVP ** Here\n.\n")

    def parts(self):
        return {p["title"]: p for p in
                newsletter.split(self.BODY, datetime(2026, 9, 25))}

    def test_a_spaced_time_range_keeps_its_end(self):
        panel = self.parts()["Life & Practice at Paul, Weiss"]
        self.assertEqual(panel["starts_at"], datetime(2026, 10, 1, 12, 0))
        self.assertEqual(panel["ends_at"], datetime(2026, 10, 1, 13, 30))

    def test_an_unspaced_range_is_unchanged(self):
        panel = self.parts()["Choosing a Practice Group with White & Case"]
        self.assertEqual(panel["ends_at"], datetime(2026, 10, 19, 20, 30))

    def test_the_section_label_is_not_the_event_name(self):
        parts = self.parts()
        self.assertNotIn("Other Firm Events & Programs", parts)
        self.assertIn("Investment Management Careers with Davis Polk", parts)

    def test_a_real_bullet_still_separates_items(self):
        """Only a dash in front of a clock time is spared."""
        def denoise(text):
            for rx, repl in newsletter.NOISE:
                text = rx.sub(repl, text)
            return text
        self.assertNotIn("- ", denoise(
            "Upcoming:\n- Bar Review, 9/17\n- Speed Networking, 10/13"))
        self.assertIn("12:00 pm - 1:30 pm", denoise("Oct 1, 12:00 pm - 1:30 pm"))
        self.assertIn("3:30 - 5:30 P.M.", denoise("Sept 28 | 3:30 - 5:30 P.M."))
