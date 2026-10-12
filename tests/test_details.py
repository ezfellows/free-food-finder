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


if __name__ == "__main__":
    unittest.main(verbosity=2)
