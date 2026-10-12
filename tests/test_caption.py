"""The organiser's own words under the flyer, and when they beat the picture."""

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"

import caption  # noqa: E402

REF = datetime(2026, 9, 11, 13, 20)


class TestStripping(unittest.TestCase):
    def test_the_contact_tail_goes(self):
        out = caption.strip_contact(
            "KALSA GBM, September 16th, 2026 from 4:30pm to 6:30pm McD344 "
            "contact zz1977@georgetown.edu")
        self.assertNotIn("@", out)
        self.assertNotIn("edu", out)
        self.assertIn("McD344", out)

    def test_the_sales_pitch_goes(self):
        out = caption.strip_contact(
            "Tax Council Welcome Reception, Sept 22, 6-8pm, McDermott Will & "
            "Schulte, Contact zz1826@georgetown.edu. Space is limited and "
            "capped at 50 students, so please RSVP as soon as possible!")
        self.assertTrue(out.endswith("McDermott Will & Schulte"), out)


class TestReading(unittest.TestCase):
    def read(self, text):
        return caption.read(text, REF)

    def test_a_typical_caption(self):
        said = self.read("SALDF's First Book Club, reading Lab Dog by Melanie "
                         "Kaplan. September 16th, 3-4 pm, McDonough 220, "
                         "zzz90@georgetown.edu")
        self.assertEqual(said["starts_at"][:16], "2026-09-16T15:00")
        self.assertEqual(said["location"], "McDonough 220")

    def test_the_year_is_not_mistaken_for_a_room(self):
        said = self.read("Law Students for Climate Accountability GBM. "
                         "September 14, 2026, from 5:30 to 6:30 PM. "
                         "McDonough 344.")
        self.assertEqual(said["location"], "McDonough 344")

    def test_but_a_real_hotung_room_survives(self):
        # Hotung's rooms are numbered 2000, 2001, 6005 -- a bare four-digit
        # rule would delete them as years.
        said = self.read("Are Geofence Warrants Constitutional? Panel on "
                         "Wednesday, September 16 5:45pm-7:00pm in Hotung 2001.")
        self.assertEqual(said["location"], "Hotung 2001")
        self.assertEqual(said["starts_at"][:16], "2026-09-16T17:45")

    def test_a_glued_room_is_still_a_room(self):
        said = self.read("APALSA-Orrick Fireside Chat, September 14th, "
                         "12:00-1:00PM, Faculty Lounge McD520.")
        self.assertEqual(said["location"], "McDonough 520")

    def test_a_sentence_is_not_a_title(self):
        said = self.read("Come dance with us this Friday Sept.11th at 4pm "
                         "in Sports & Fitness Studio 3.")
        self.assertEqual(said["title"], "")

    def test_an_empty_caption_says_nothing(self):
        said = self.read("")
        self.assertEqual(said["starts_at"], "")
        self.assertEqual(said["location"], "")
        self.assertEqual(said["title"], "")


class TestGround(unittest.TestCase):
    """Which source wins each field."""

    def test_the_caption_corrects_a_misread_time(self):
        said = self.read_ok("2026-09-17T12:10:00", "McDonough 200")
        changes, _ = caption.ground(
            {"starts_at": "2026-09-16T13:10:00", "location": "Hotung"}, said)
        self.assertEqual(changes["starts_at"], "2026-09-17T12:10:00")

    def test_a_dateless_caption_never_replaces_a_time_with_midnight(self):
        said = {"starts_at": "2026-09-17T00:00:00", "ends_at": "",
                "all_day": True, "date_confidence": 80, "location": "",
                "room": "", "campus": "unknown"}
        changes, _ = caption.ground(
            {"starts_at": "2026-09-17T18:00:00", "location": "McDonough 207"},
            said)
        self.assertNotIn("starts_at", changes)

    def test_but_it_may_still_correct_the_day(self):
        said = {"starts_at": "2026-09-23T00:00:00", "ends_at": "",
                "all_day": True, "date_confidence": 80, "location": "",
                "room": "", "campus": "unknown"}
        changes, _ = caption.ground(
            {"starts_at": "2026-09-18T15:30:00", "location": ""}, said)
        self.assertEqual(changes["starts_at"], "2026-09-23T15:30:00")

    def test_a_typo_does_not_unseat_a_building_we_know(self):
        said = self.read_ok("", "Hoting Patio")
        changes, _ = caption.ground(
            {"starts_at": "", "location": "Hotung Patio"}, said)
        self.assertNotIn("location", changes)

    def test_a_vaguer_room_does_not_overwrite_a_precise_one(self):
        said = self.read_ok("", "McDonough 141")
        changes, clash = caption.ground(
            {"starts_at": "", "location": "McDonough 156, 160 & 164"}, said)
        self.assertNotIn("location", changes)
        self.assertTrue(clash)

    def test_the_flyer_keeps_detail_the_caption_flattens(self):
        said = self.read_ok("", "500 First St NW 100")
        changes, _ = caption.ground(
            {"starts_at": "",
             "location": "500 First Street NW, Suites 100 & 120"}, said)
        self.assertNotIn("location", changes)

    def test_an_end_time_the_flyer_never_printed_is_filled_in(self):
        said = self.read_ok("2026-09-24T15:30:00", "")
        said["ends_at"] = "2026-09-24T16:30:00"
        changes, _ = caption.ground(
            {"starts_at": "2026-09-24T15:30:00", "ends_at": "",
             "location": "Hotung Patio"}, said)
        self.assertEqual(changes["ends_at"], "2026-09-24T16:30:00")
        self.assertNotIn("starts_at", changes)

    def test_but_an_existing_end_time_is_left_alone(self):
        said = self.read_ok("2026-09-24T15:30:00", "")
        said["ends_at"] = "2026-09-24T16:30:00"
        changes, _ = caption.ground(
            {"starts_at": "2026-09-24T15:30:00",
             "ends_at": "2026-09-24T17:00:00", "location": ""}, said)
        self.assertNotIn("ends_at", changes)

    def test_a_missing_location_is_filled_in(self):
        said = self.read_ok("", "McDonough 328")
        changes, _ = caption.ground({"starts_at": "", "location": ""}, said)
        self.assertEqual(changes["location"], "McDonough 328")

    def test_disagreeing_clocks_are_reported_even_when_the_caption_wins(self):
        said = self.read_ok("2026-09-17T17:30:00", "")
        changes, clash = caption.ground(
            {"starts_at": "2026-09-17T18:00:00", "location": ""}, said)
        self.assertEqual(changes["starts_at"], "2026-09-17T17:30:00")
        self.assertTrue(any("17:30" in c and "18:00" in c for c in clash))

    def read_ok(self, starts_at, location):
        place = caption.campus.locate(location) if location else {}
        return {"starts_at": starts_at, "ends_at": "", "all_day": False,
                "date_confidence": 90, "location": place.get("place", ""),
                "room": place.get("room", ""),
                "campus": place.get("campus", "unknown")}


if __name__ == "__main__":
    unittest.main()
