"""Pulling several events out of one round-up, and refusing the job board.

The body used here follows the layout of a real Georgetown Plaintiffs' Law
Association bulletin, but every event, firm and blurb in it is invented. It is
the right fixture because it contains both things at once: five events worth
publishing, and ten rows of a job board whose closing dates would become ten
fake events if anchored on.
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
    "Hi all, Lovely to see so many new faces at our welcome picnic. "
    "## Upcoming Events "
    "[Tomorrow!] Dana Whitfield: Taking a wage-theft case to trial "
    "9/15 Tues. 6:00 pm - 7:00 pm, McD 207. Dinner provided. RSVP Here. "
    "Bar Review with the Renter's Justice Project 9/17 Thurs. at the Admiral. "
    "Halloway Quint: Suing Over No-Poach Agreements 9/23/26 "
    "5:30 - 6:30 pm Hotung 3015. A Halloway Quint associate walks through the "
    "firm's first no-poach class action. RSVP here. "
    "What Trial Boutiques Are Hiring For 9/24/26 4:30 pm Online Only. "
    "Three hiring partners take questions live. "
    "Larkmoor Pell at OPICS Coffee Hour 9/25/26. All chats are on Zoom. "
    "Book a slot from 12 - 1:30 PM. "
    "## Professional Opportunities "
    "* Thistle & Marrow - Rolling, 2L 2027 Summer Associate "
    "* Quillfeather, Vane, Larkspur & Brisk - Closes Sep 30, 2026 "
    "* Halverd PC - Opened Aug 1, 2L 2027 Summer Associate, unknown close "
    "* Ashgrove Labor Fellowship - closes Sep 30, 2026, multiple "
    "* Brindle & Moss - closes Oct 5, 2026, 2L 2027 Summer Associate "
    "## 1L Representatives Send us a short statement by October 2nd at 5 pm to apply."
)


class TestSplittingABulletin(unittest.TestCase):
    def setUp(self):
        self.events = newsletter.split(BULLETIN, REF)
        self.titles = [e["title"] for e in self.events]

    def test_every_real_event_is_found(self):
        self.assertEqual(len(self.events), 5, self.titles)

    def test_each_one_keeps_its_own_name(self):
        for expected in ("Dana Whitfield", "Bar Review", "Halloway Quint",
                         "What Trial Boutiques Are", "Larkmoor Pell"):
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
        for firm in ("Thistle", "Quillfeather", "Halverd", "Ashgrove Labor",
                     "Brindle"):
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

    BODY = ("Dear Harbor Law Society Members, Happy Wednesday! We spotted and "
            "repaired a broken link on the sign-up page for our joint GBM with "
            "the Lantern Guild on Wednesday September 23 at 3:30 pm, upstairs "
            "in Hotung 1010. RSVPs help us order enough pizza!")

    def test_the_event_is_found(self):
        events = newsletter.split(self.BODY, REF)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["starts_at"].strftime("%m-%d %H:%M"),
                         "09-23 15:30")

    def test_the_title_is_the_event_not_the_sentence(self):
        title = newsletter.split(self.BODY, REF)[0]["title"]
        self.assertIn("GBM", title)
        self.assertNotIn("repaired a broken link", title)
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

    The entry below gives a time, a room and the food, and nothing else -- an
    hour and no calendar. With no date in its own text there is no anchor, so
    the splitter never saw it and the free cookies were invisible.
    """

    REF = datetime(2026, 9, 18)
    INDEX = ("9/23 - Students for Coastal Justice Society - GBM\n"
             "9/23 - IP Club - Mentor Coffee Hour\n\n")
    ENTRY = ("Students for Coastal Justice Society fall General Body Meeting, "
             "3:30 to 4:20pm at Hotung 1020. Free frosted sugar cookies, a sign-up "
             "sheet for the beach clean-up, and news on SCJS. Contact zz768@georgetown.edu")

    def test_the_entry_is_dated_from_the_index(self):
        got = newsletter.split(self.INDEX + self.ENTRY, self.REF)
        self.assertEqual(len(got), 1, got)
        self.assertEqual(got[0]["starts_at"].date().isoformat(), "2026-09-23")
        self.assertEqual(got[0]["starts_at"].strftime("%H:%M"), "15:30")

    def test_the_food_survives_into_the_detail(self):
        got = newsletter.split(self.INDEX + self.ENTRY, self.REF)
        self.assertIn("frosted sugar cookies", got[0]["detail"])

    def test_an_entry_with_its_own_date_is_not_duplicated(self):
        body = (self.INDEX + "IP Club Mentor Coffee Hour, September 23 from "
                "3:30 p.m. to 4:30 p.m. out on the Hotung Patio.\n\n" + self.ENTRY)
        titles_found = [e["title"] for e in newsletter.split(body, self.REF)]
        self.assertEqual(len(titles_found), len(set(titles_found)))
        self.assertEqual(len(titles_found), 2, titles_found)

    def test_a_paragraph_the_index_does_not_name_is_left_alone(self):
        """The host's name has to appear, or any stray time would be dated."""
        body = self.INDEX + "Doors open at 5:00pm for the reading room."
        self.assertEqual(newsletter.split(body, self.REF), [])

    def test_no_index_means_no_rescue(self):
        self.assertEqual(newsletter.split(self.ENTRY, self.REF), [])


class TestALinkIsNeverATitle(unittest.TestCase):
    """An "RSVP here" with its sign-up link in brackets became the title of a
    firm reception, because the link sat between the event's name and its
    date."""

    def test_urls_and_link_words_are_stripped(self):
        self.assertEqual(
            newsletter._tidy_title(
                "RSVP here (https://sign-up.example.edu/x)"), "")
        self.assertEqual(
            newsletter._tidy_title(
                "Firm Receptions https://example.com/rsvp"), "Firm Receptions")


class TestLabelsAreNotNames(unittest.TestCase):
    """What a newsletter prints around an event is not the event's name."""

    def test_a_leading_date_label_is_dropped(self):
        """"9/28: Deal Lawyering Basics" could never merge with a clean copy of
        the same event, because the label changed its fingerprint."""
        self.assertEqual(
            newsletter._tidy_title("9/28: Deal Lawyering Basics with King & Spalding"),
            "Deal Lawyering Basics with King & Spalding")
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

    Laid out like the 25 September 2026 issue; the events are invented. Two
    things went wrong on that issue. The dash in "12:00 pm - 1:30 pm" was read
    as a bullet, which broke the range in two, so every lunch panel ended at
    the parser's default hour instead of when CFLO said. And the section label "Other Firm Events &
    Programs" was published as the name of the Davis Polk event beneath it.
    """

    BODY = (
        "Trial Teams & Training at Paul, Weiss\nThursday, October 1\n"
        "12:00 pm - 1:30 pm\nLocation: Gewirz 12\nRSVP\n** here\n\n"
        "Litigation Rotations at White & Case\nMonday, October 19\n"
        "6:00 pm- 8:30 pm\nLocation: GULC Campus (room TBD)\nRSVP ** here\n.\n\n"
        "Other Firm Events & Programs\n"
        "Private Credit Deals with Davis Polk\nTuesday, October 6th\n"
        "4:00-6:00pm\nLocation: Sport & Fitness Center Lobby\nRSVP ** Here\n.\n")

    def parts(self):
        return {p["title"]: p for p in
                newsletter.split(self.BODY, datetime(2026, 9, 25))}

    def test_a_spaced_time_range_keeps_its_end(self):
        panel = self.parts()["Trial Teams & Training at Paul, Weiss"]
        self.assertEqual(panel["starts_at"], datetime(2026, 10, 1, 12, 0))
        self.assertEqual(panel["ends_at"], datetime(2026, 10, 1, 13, 30))

    def test_an_unspaced_range_is_unchanged(self):
        panel = self.parts()["Litigation Rotations at White & Case"]
        self.assertEqual(panel["ends_at"], datetime(2026, 10, 19, 20, 30))

    def test_the_section_label_is_not_the_event_name(self):
        parts = self.parts()
        self.assertNotIn("Other Firm Events & Programs", parts)
        self.assertIn("Private Credit Deals with Davis Polk", parts)

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


class TestAHeadingThatSaysWhen(unittest.TestCase):
    """A CampusGroups newsletter sets each heading in the same table cell as
    its first sentence, and the plain text runs the two together, as in
    "Trivia Night This Week Our monthly trivia night..." below. The "this
    week" is where the heading ends. Read whole, the entry was dropped, and
    its date with it.
    """

    REF = datetime(2026, 10, 13, 10, 32)

    def split(self, *cells):
        text = "\n\n".join(["| |"] + ["| ## %s |" % cell for cell in cells]
                           + ["*Chess Club*"])
        return [(part["title"], part["starts_at"])
                for part in newsletter.split(text, self.REF)]

    def test_the_name_is_the_heading_in_front_of_this_week(self):
        self.assertEqual(self.split(
            "Trivia Night This Week Our monthly trivia night will be next "
            "Wednesday, October 14, at 7:00 p.m. in McDonough 200. Pizza will "
            "be provided!"), [("Trivia Night", datetime(2026, 10, 14, 19, 0))])

    def test_and_the_food_stays_in_the_detail(self):
        text = ("| ## Trivia Night This Week Our monthly trivia night will be "
                "next Wednesday, October 14, at 7:00 p.m. in McDonough 200. "
                "Pizza will be provided! |")
        self.assertIn("Pizza will be provided",
                      newsletter.split(text, self.REF)[0]["detail"])

    def test_tonight_ends_a_heading_too(self):
        self.assertEqual(self.split(
            "Movie Night Tonight Join the club on Tuesday, October 13, at 8:00 "
            "p.m. in Hotung 2001 for popcorn and a film."),
            [("Movie Night", datetime(2026, 10, 13, 20, 0))])

    def test_a_masthead_is_still_no_event(self):
        """"Events This Week" names the newsletter, not anything to attend."""
        self.assertEqual(self.split(
            "Events This Week Join us next Wednesday, October 14, at 7:00 p.m. "
            "in McDonough 200."), [])

    def test_a_sentence_in_front_of_today_is_no_name(self):
        """A deadline: it speaks to the reader, as no heading does."""
        self.assertEqual(self.split(
            "Remember to send us a writing sample by EOD today so we can pair "
            "you with mentors at the session on Wednesday, October 14, at noon."), [])

    def test_nor_is_the_middle_of_one(self):
        """Cut mid-sentence, the words in front of "today" start in lower case."""
        text = ("| ## Career Week |\n\nand a resume review clinic today, with "
                "feedback from mentors at the session on Wednesday, October 14, "
                "at noon.")
        self.assertNotIn("and a resume review clinic",
                         [part["title"] for part in newsletter.split(text, self.REF)])


class TestADeadlineIsNoEvent(unittest.TestCase):
    """A bulletin's reminder to send something by a day is not an event."""

    REF = datetime(2026, 10, 13, 10, 32)
    TEXT = ("| ## Resume Review So glad you could make it to the mixer! Don't "
            "forget to drop your resume in our inbox by EOD tomorrow (10/14) for feedback. |")

    def test_it_is_not_listed(self):
        self.assertEqual(newsletter.split(self.TEXT, self.REF), [])

    def test_but_the_caller_can_count_it(self):
        refused = []
        newsletter.split(self.TEXT, self.REF, refused)
        self.assertEqual([part["starts_at"] for part in refused],
                         [datetime(2026, 10, 14)])


class TestAFieldLabelIsNotTheName(unittest.TestCase):
    """Where the name is depends on where the label is (2 Nov 2026).

    Each layout below is a real bulletin's; the words and names are invented.
    """

    REF = datetime(2026, 11, 2, 9, 0)

    def titles(self, body):
        return [(p["title"], p["starts_at"]) for p in newsletter.split(body, self.REF)]

    def test_a_label_at_the_end_of_a_blurb(self):
        """The SBA's Weekly Update: "Details: Thursday" was the title of one
        event, and "Thursday" too short to keep the other."""
        body = ("\U0001F389UPCOMING EVENTS\U0001F389\n\n"
                "\U0001F57A Bar Review x Chess Club! Bring a board and a friend for drinks\n"
                "with the Chess Club. $5 wristbands get you cheaper rounds! Details:\n"
                "Thursday, 11/05 @ 9:00pm-12:30am at Corner Tavern\n\n"
                "\U0001F5F3️ Election Night Watch Party! The Politics Society has saved\n"
                "us seats in front of the big screens all night! Details: Tuesday, 11/3\n"
                "@7:00pm-11:00pm at the Atrium\n")
        self.assertEqual(self.titles(body), [
            ("Bar Review x Chess Club", datetime(2026, 11, 5, 21, 0)),
            ("Election Night Watch Party", datetime(2026, 11, 3, 19, 0))])

    def test_a_label_that_opens_its_line(self):
        """MLS's "When:", OCS's "+ Date & Time:": the name is the line above,
        past a note with no capital in it."""
        body = ("Upcoming Events:\nRiverside Firm Veteran Dinner \U0001F374\nflyer attached\n"
                "When: Tuesday, November 3rd, 6-9pm\nWhere: The Union Club\n"
                "Join veterans practising at the firm for dinner.\n"
                "Harbor Lantern Open Office Hours\n+ Date & Time: Thursday, November 5\n"
                "3:30 PM - 5:00 PM\n+ Location: Hotung 4005\n")
        self.assertEqual(self.titles(body), [
            ("Riverside Firm Veteran Dinner", datetime(2026, 11, 3, 18, 0)),
            ("Harbor Lantern Open Office Hours", datetime(2026, 11, 5, 15, 30))])

    def test_a_nameless_block_still_ends_the_entry_before(self):
        """The OSL broadcast's "Date: Friday, October 23" with no name above
        it was folded into the Jones Day chat before it, which moved to the
        block's 11am."""
        body = ("Hoya Club, One-on-One Chat with Westbrook Attorneys, October 23, 2026, "
                "9am - 11am, Location shared once you RSVP\n\n<https://example.com/x>\n\n"
                "Date: Friday, October 23, 2026. Time: 11:00am-5:00pm. Location: 640 Lantern St NW\n")
        self.assertEqual(self.titles(body), [
            ("Hoya Club, One-on-One Chat with Westbrook Attorneys", datetime(2026, 10, 23, 9, 0))])

    def test_a_date_in_mid_phrase_is_no_label(self):
        body = ("Fellowship Info Session\nTo sign up, pick the most convenient date: "
                "Monday, November 9, 4:00 PM\n")
        for title, _when in self.titles(body):
            self.assertNotEqual(title, "To sign up, pick the most convenient")

    def test_a_heading_above_a_blurb_that_opens_with_a_sentence(self):
        """GIAS: the heading, a blank line, then a sentence addressed to the
        reader, and only after it "\U0001F4C5 November 11"."""
        body = ("EVENTS\nFireside Chat with Tribunal Secretaries\n\n"
                "Don’t skip the last fireside of the term! Two tribunal secretaries "
                "talk about how they got the job. \U0001F4C5 November 11, 2026 (Wednesday) "
                "\U0001F552 4:00-5:00 PM \U0001F4CD Room 141\n\n"
                "SAVE THE DATE\nGIAS Panel Discussion:\nTreaty Claims and Climate Policy\n\n"
                "States and investors keep clashing over climate rules, and treaty tribunals "
                "are now being asked to referee the fight. \U0001F4C5 November 9, 2026 "
                "(Monday) \U0001F554 5:00–6:30 PM\n")
        self.assertEqual(self.titles(body), [
            ("Fireside Chat with Tribunal Secretaries", datetime(2026, 11, 11, 16, 0)),
            ("GIAS Panel Discussion: Treaty Claims and Climate Policy",
             datetime(2026, 11, 9, 17, 0))])

    def test_an_address_is_not_the_next_event_s_name(self):
        """CFLO: a name with "Your" in it read as a sentence, and the street
        address above it became the title."""
        body = ("Inside the Deal Room with Ropes & Gray\nTuesday, October 20\n6:00 pm\n"
                "Location: Ropes & Gray DC\n1750 Lantern Ave NW\nRSVP here\n\n"
                "Plot Your Summer at Skadden\nWednesday, October 21\n6:00 pm - 7:30 pm\n"
                "Location: Skadden DC\n")
        self.assertEqual([t for t, _w in self.titles(body)],
                         ["Inside the Deal Room with Ropes & Gray", "Plot Your Summer at Skadden"])

    def test_an_address_above_a_label_is_the_entry_before(self):
        body = ("Firm Lunch\nWhen: Monday, October 19, 12:00 pm\nWhere: Firm DC\n"
                "1440 New York Ave NW\n\nWhen: Tuesday, October 20, 6:00 pm\nWhere: The Atrium\n")
        self.assertNotIn("1440 New York Ave NW", [t for t, _w in self.titles(body)])

    def test_a_name_that_says_your_is_still_a_name(self):
        """"Your" is in plenty of names; read as a sentence, the heading of
        the section above took this one's place."""
        body = ("Public Interest Week\n\nLaunching Your Career in Public Interest. Hear from "
                "three alumni about their first jobs. October 28, 2026, 3:30 PM, McDonough 200.\n")
        self.assertEqual([t for t, _w in self.titles(body)],
                         ["Launching Your Career in Public Interest"])

    def test_a_section_label_over_the_entry(self):
        body = ("⏭️ NEXT WEEK\nPlaintiffs' Coffee Chats with Practising Attorneys\n"
                "⏰ November 2–16, The Ministry, Sign-ups here\n")
        self.assertEqual([t for t, _w in self.titles(body)],
                         ["Plaintiffs' Coffee Chats with Practising Attorneys"])

    def test_a_sentence_about_an_entry_is_not_a_second_one(self):
        """MLS's update: a sentence correcting the Harbor Happy Hour on 18
        November, followed by an earlier issue's "7 OCT 26" date stamp, had the
        happy hour listed on 26 October too."""
        body = ("Good afternoon! Here is a correction to the Harbor Happy Hour "
                "on 18 November. Our 7 OCT 26 Weekly Dispatch listed it wrongly; the right entry is: "
                "Upcoming Events:\nHarbor Happy Hour \U0001F37B\n"
                "When: Wednesday, November 18th, 6:30 - 8:30 PM\nWhere: The Dubliner\n")
        self.assertEqual(self.titles(body), [("Harbor Happy Hour", datetime(2026, 11, 18, 18, 30))])

    def test_emoji_go_but_a_price_stays(self):
        self.assertEqual(newsletter._strip_symbols("\U0001F57A Bar Review x GLE"), "Bar Review x GLE")
        self.assertEqual(newsletter._strip_symbols("Warrior Yomp 2026 \U0001F5D3️"), "Warrior Yomp 2026")
        self.assertEqual(newsletter._strip_symbols("$5 Lunch"), "$5 Lunch")
        self.assertEqual(newsletter._tidy_title("Event: Film Law Circle 5th Annual Showcase"),
                         "Film Law Circle 5th Annual Showcase")
