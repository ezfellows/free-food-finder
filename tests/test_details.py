"""Practical requirements: what to wear, what to bring, when to RSVP.

Everything is extraction, never inference. If the text does not say it, the
field stays empty -- a wrong dress code is worse than none.
"""
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"
import details

REF = datetime(2026, 9, 16)


class TestDressCode(unittest.TestCase):
    def test_recognised_codes(self):
        cases = [("Business formal required", "Business formal"),
                 ("business professional attire", "Business formal"),
                 ("Business casual, please", "Business casual"),
                 ("Cocktail attire", "Cocktail attire"),
                 ("Black tie optional", "Black tie"),
                 ("Dress to impress!", "Dress to impress")]
        for text, expected in cases:
            self.assertEqual(details.extract("", text, REF)["dress_code"],
                             expected, text)

    def test_strictest_wins_when_several_appear(self):
        """'business formal (no jeans, not business casual)' is formal."""
        verdict = details.extract("", "Business formal, not business casual", REF)
        self.assertEqual(verdict["dress_code"], "Business formal")

    def test_nothing_stated_means_nothing_shown(self):
        self.assertEqual(details.extract("Pizza", "Come grab a slice", REF)
                         ["dress_code"], "")


class TestBring(unittest.TestCase):
    def test_resume_collection_is_caught(self):
        for text in ("Resumes will be collected at the door",
                     "Bring copies of your resume",
                     "Resume collection for summer associate roles",
                     "bring a résumé"):
            self.assertIn("Bring a resume",
                          details.extract("", text, REF)["bring"], text)

    def test_other_items(self):
        verdict = details.extract("", "Bring business cards and your student ID",
                                  REF)
        self.assertIn("Bring business cards", verdict["bring"])
        self.assertIn("Bring your student ID", verdict["bring"])


class TestDeadline(unittest.TestCase):
    def test_rsvp_by_a_date(self):
        self.assertEqual(
            details.extract("", "RSVP by Friday, September 25.", REF)["rsvp_by"],
            "2026-09-25")

    def test_register_by(self):
        self.assertEqual(
            details.extract("", "Please register by September 20", REF)["rsvp_by"],
            "2026-09-20")

    def test_rsvp_without_a_date_is_not_a_deadline(self):
        """'RSVP by scanning the QR code' states no date."""
        self.assertEqual(
            details.extract("", "RSVP by scanning the QR code", REF)["rsvp_by"], "")

    def test_no_rsvp_at_all(self):
        self.assertEqual(details.extract("", "Just show up", REF)["rsvp_by"], "")


class TestAccessAndNotes(unittest.TestCase):
    def test_access_conditions(self):
        verdict = details.extract("", "Space is limited. Registration required. "
                                      "1Ls only.", REF)
        for note in ("Space is limited", "Registration required", "1Ls only"):
            self.assertIn(note, verdict["access"])

    def test_notes_read_in_the_order_you_need_them(self):
        """Dress, then what to carry, then what stops you at the desk."""
        verdict = details.extract("", "Business formal. Bring a resume. "
                                      "RSVP by September 25. Space is limited.",
                                  REF)
        self.assertEqual(verdict["notes"][0], "Business formal")
        self.assertEqual(verdict["notes"][1], "Bring a resume")
        self.assertIn("RSVP by 25 Sep", verdict["notes"])
        self.assertIn("Space is limited", verdict["notes"])

    def test_notes_are_capped(self):
        verdict = details.extract("", "Business formal. Bring a resume. Bring "
                                      "business cards. Bring a laptop. Bring your "
                                      "student ID. Guests welcome. Space is "
                                      "limited. Registration required. Ticketed. "
                                      "Hybrid.", REF)
        self.assertLessEqual(len(verdict["notes"]), 6)

    def test_quiet_event_has_no_notes(self):
        self.assertEqual(details.extract("Lunch talk", "Free pizza.", REF)
                         ["notes"], [])

    def test_garbage_input(self):
        for bad in (None, "", 12345, "<script>x</script>"):
            details.extract(bad, bad, REF)


class TestMoreDeadlineWordings(unittest.TestCase):
    """The same cut-off, worded the other ways round. Each is from a real
    email the extractor used to miss."""

    REF = datetime(2026, 10, 21)

    def by(self, text):
        return details.extract("", text, self.REF)["rsvp_by"]

    def test_a_labelled_deadline(self):
        """The Office of Career Strategy's Flo Recruit sign-ups."""
        self.assertEqual(self.by("October 19-23 (Registration Deadline: Friday, "
                                 "October 16 at 6pm ET)"), "2026-10-16")
        self.assertEqual(self.by("RSVP deadline: October 12"), "2026-10-12")

    def test_the_deadline_to_register_is(self):
        self.assertEqual(self.by("The deadline to register is midnight on "
                                 "Friday, 10/2"), "2026-10-02")

    def test_register_early_by(self):
        self.assertEqual(self.by("Register early by October 6"), "2026-10-06")

    def test_registration_closes(self):
        self.assertEqual(self.by("Registration closes on October 18"),
                         "2026-10-18")

    def test_rsvps_are_due(self):
        self.assertEqual(self.by("RSVPs are due October 19"), "2026-10-19")

    def test_a_dateless_ask_does_not_hide_a_dated_one_after_it(self):
        self.assertEqual(self.by("RSVP by scanning the QR code below. "
                                 "Register by October 17."), "2026-10-17")

    def test_the_first_stated_deadline_wins(self):
        self.assertEqual(self.by("RSVP by October 14. Registration closes on "
                                 "October 18."), "2026-10-14")

    def test_an_accommodation_request_is_not_an_rsvp(self):
        self.assertEqual(self.by("Accommodation requests related to a disability "
                                 "should be made by October 19"), "")


class TestAnRsvpIsAskedFor(unittest.TestCase):
    """Words that tell the reader to act, not words that mention signing up."""

    def asked(self, text):
        return details.extract("", text, REF)["rsvp"]

    def test_real_asks(self):
        for text in ("Click Here to Register for the Event",
                     "RSVP required. Seating is limited",
                     "Please RSVP and contact the board with questions",
                     "Registration is through Flo Recruit, linked from the flyer",
                     "Please sign up using the registration link here",
                     "Scan the code to reserve your seat",
                     "RSVP HERE"):
            self.assertTrue(self.asked(text), text)

    def test_a_stated_deadline_is_an_ask(self):
        self.assertTrue(self.asked("Sign-up closes October 30"))

    def test_mentions_that_ask_nothing(self):
        for text in ("Remote participation is available after registration",
                     "Learn how to register for spring classes",
                     "Open to every registered student organization",
                     "Free pizza in the lounge"):
            self.assertFalse(self.asked(text), text)

    def test_an_rsvp_waived_in_so_many_words(self):
        """The Center for Legal English's flyer."""
        for text in ("NO REGISTRATION NEEDED! Just come along",
                     "RSVP not required", "Come without an RSVP"):
            self.assertFalse(self.asked(text), text)


class TestSignUpsHaveClosed(unittest.TestCase):
    def closed(self, text):
        return details.extract("", text, REF)["rsvp_closed"]

    def test_closed(self):
        for text in ("RSVPs have closed", "Registration is now closed",
                     "Registration is full", "The reception is at capacity"):
            self.assertTrue(self.closed(text), text)
            self.assertTrue(details.extract("", text, REF)["rsvp"], text)

    def test_still_open(self):
        """"Closes" is a deadline still to come; "RSVP full name" asks for one."""
        for text in ("Registration closes on October 18",
                     "RSVP full name and class year", "RSVP here"):
            self.assertFalse(self.closed(text), text)


class TestPerks(unittest.TestCase):
    """Rides and travel the host pays for, from the emails that offered them."""

    def perks(self, text):
        return details.extract("", text, REF)["perks"]

    def test_a_firm_paying_for_the_ride_home(self):
        found = self.perks("We are inviting 1L students from the school and will "
                           "be providing Uber vouchers for travel home after the "
                           "event.")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["label"], "Uber voucher")
        self.assertFalse(found[0]["with_rsvp"])
        self.assertEqual(found[0]["phrase"],
                         "Uber vouchers for travel home after the event")

    def test_an_amount_and_a_condition(self):
        found = self.perks("Transportation Support: A $20 off Uber voucher code "
                           "will be provided to all confirmed attendees to "
                           "support travel to and from our office")[0]
        self.assertEqual(found["label"], "$20 Uber voucher")
        self.assertTrue(found["with_rsvp"])
        self.assertIsNone(found["lead_hours"])
        self.assertIn("to all confirmed attendees", found["phrase"])

    def test_a_lead_time(self):
        found = self.perks("Lyft codes will be provided to those who register at "
                           "least 48 hours before the local event start time")[0]
        self.assertEqual(found["label"], "Lyft voucher")
        self.assertTrue(found["with_rsvp"])
        self.assertEqual(found["lead_hours"], 48)
        self.assertIn("those who register at least 48 hours before",
                      found["phrase"])

    def test_a_lead_time_in_days(self):
        found = self.perks("Rideshare codes go to students who RSVP 2 days "
                           "in advance")[0]
        self.assertEqual(found["lead_hours"], 48)

    def test_travel(self):
        self.assertEqual(self.perks("A $500 travel stipend is available")[0]
                         ["label"], "$500 travel stipend")
        self.assertEqual(self.perks("Travel expenses (airfare, hotel, and most "
                                    "meals) are covered by a donor")[0]["label"],
                         "Travel covered")
        self.assertEqual(self.perks("We will reimburse reasonable travel "
                                    "costs")[0]["label"], "Travel covered")

    def test_one_offer_said_twice_is_one_perk(self):
        found = self.perks("Uber vouchers will be provided. See the Uber voucher "
                           "link below.")
        self.assertEqual([p["label"] for p in found], ["Uber voucher"])

    def test_false_friends(self):
        """None of these offers anything. All but the last are worded as real
        emails worded them; the last is a brand name inside another word."""
        for text in ("We encourage attendees to arrive by cab, Uber, or Metro",
                     "She works in recruiting for uber, lyft, and a defense "
                     "tech startup",
                     "Eligible students may receive up to $100 dollars in "
                     "reimbursement for their professional wardrobe purchases",
                     "a great way to check off your four hours for the stipend",
                     "at no cost to students, apart from a couple of meals and "
                     "ground transportation/parking at your home airport",
                     "Please use public transportation instead of parking",
                     "Help us with occasional transportation needs",
                     "Sponsored by the Huber Credit Union"):
            self.assertEqual(self.perks(text), [], text)

    def test_an_offer_withdrawn(self):
        for text in ("Unfortunately we are unable to provide Uber vouchers.",
                     "Travel will not be reimbursed.",
                     "There are no Lyft codes this year."):
            self.assertEqual(self.perks(text), [], text)

    def test_the_quote_never_carries_a_link_or_an_address(self):
        """A voucher link is money; the page must never carry one."""
        found = self.perks("Please see the Uber voucher link for your arrival "
                           "trip https://r.uber.com/AbC123xYz and the Lyft "
                           "credit for your ride from jdoe@example.com. Ride "
                           "vouchers for https://r.uber.com/AbC123xYz")
        self.assertTrue(found)
        for perk in found:
            self.assertNotIn("http", perk["phrase"])
            self.assertNotIn("uber.com", perk["phrase"].lower())
            self.assertNotIn("@", perk["phrase"])
            self.assertLessEqual(len(perk["phrase"]), 80)

    def test_nothing_offered(self):
        self.assertEqual(self.perks("Free pizza. RSVP by October 3."), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestReceptions(unittest.TestCase):
    """A reception, one after a talk, or none -- from real announcements."""

    def kind(self, title, text=""):
        return details.extract(title, text, REF)["reception"]

    def test_the_title_says_so(self):
        for title in ("Kirkland 1L Reception", "Freshfields 1L Panel & Reception",
                      "Private Sector 1L Law Firm Reception",
                      "BLSA Diversity Fair and Reception open to BLSA Members",
                      "LALSA Networking Reception"):
            self.assertEqual(self.kind(title), "reception", title)

    def test_one_after_a_talk(self):
        """An hour of lecture comes first, so it is told apart."""
        for text in ("Reception and book signing to follow",
                     "Reception & Book Signing to Follow",
                     "Tuesday at 4 pm with reception to follow",
                     "Information panel followed by a networking reception",
                     "Hear from partners on the panel, then enjoy a deliciously "
                     "catered networking reception",
                     "join us for the panel and reception",
                     "a networking reception follows the Day 1 program",
                     "Reception to follow!"):
            self.assertEqual(self.kind("Fall Lecture", text), "after", text)

    def test_held_but_not_in_the_title(self):
        for text in ("The firm is pleased to host a 1L Reception on Monday",
                     "We invite the 1L class to a reception to meet our attorneys",
                     "Guests will enjoy a cocktail reception, dinner and awards",
                     "5:30 Reception, 7:00 PM performance",
                     "Reception: 6-8pm in the atrium"):
            self.assertEqual(self.kind("Annual Gala", text), "reception", text)

    def test_a_schedule_row_whose_cells_ran_together(self):
        """A calendar table read "| ReceptionContinue the conversation"."""
        self.assertEqual(self.kind("Lecture", "Lecture | 7:00 pm | ReceptionContinue "
                                   "the conversation over food and drink"),
                         "reception")

    def test_the_title_wins_over_what_follows(self):
        self.assertEqual(self.kind("Freshfields 1L Panel & Reception",
                                   "Panel, with a reception to follow"),
                         "reception")

    def test_not_an_event(self):
        """A floor's reception area, a sign-in desk, and a seminar topic."""
        for title, text in (
                ("Kirkland Info Session", "1301 Pennsylvania Avenue NW, "
                 "12th Floor Reception. RSVP here"),
                ("Office Hours", "Check in at the reception desk"),
                ("Office Tour", "Wait in the reception area"),
                ("The Reception of Foreign Judgments", "A seminar"),
                ("Reception Desk Hours", ""),
                ("Pizza Lunch", "Free pizza in the lounge"),
                ("Office Hours", "| reception desk on the left")):
            self.assertEqual(self.kind(title, text), "", (title, text))
