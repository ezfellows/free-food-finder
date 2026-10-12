"""Tests for the generic org-page ingester."""
import os, sys, unittest
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"
from ingest.orgs import from_jsonld, from_text_blocks
from docket import pretty_org

REF = datetime(2026, 9, 2)

JSONLD_PAGE = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"WebPage","name":"Events"},
 {"@type":"Event","name":"Immigration Law Panel",
  "startDate":"2026-09-18T12:00:00-04:00","endDate":"2026-09-18T13:00:00-04:00",
  "description":"Panel discussion. Free lunch will be provided for attendees.",
  "location":{"@type":"Place","name":"Hotung 2000",
              "address":{"streetAddress":"600 New Jersey Ave NW"}},
  "url":"https://example.org/events/immigration"},
 {"@type":"Event","name":"Moot Court Tryouts",
  "startDate":"2026-09-19T09:00:00-04:00",
  "description":"Sign up for tryouts. No food.",
  "location":"McDonough 200"}
]}
</script></head><body>ignored</body></html>
"""

BLOCK_PAGE = """
<html><body>
<div class="event"><h3>Pizza and Policy</h3>
<p>Join us Wednesday, September 23 at 12:00pm in Gewirz 12th Floor.
Free pizza will be provided for all attendees.</p></div>
<div class="event"><h3>Study Group</h3>
<p>Weekly con law study group, Thursdays in the library. Bring your own snacks.</p></div>
<div class="event"><p>Our annual gala is coming up soon. Details to follow.</p></div>
</body></html>
"""


class TestJsonLd(unittest.TestCase):
    def setUp(self):
        self.events = from_jsonld(JSONLD_PAGE, "Test Org", "https://example.org/events")

    def test_extracts_nested_graph_events(self):
        titles = [e["title"] for e in self.events]
        self.assertIn("Immigration Law Panel", titles)
        self.assertIn("Moot Court Tryouts", titles)

    def test_exact_times_from_structured_data(self):
        panel = next(e for e in self.events if e["title"] == "Immigration Law Panel")
        self.assertEqual(panel["starts_at"], "2026-09-18T12:00:00")
        self.assertEqual(panel["ends_at"], "2026-09-18T13:00:00")
        self.assertEqual(panel["tier"], "confirmed")
        self.assertEqual(panel["meals"], ["lunch"])

    def test_place_object_flattened(self):
        panel = next(e for e in self.events if e["title"] == "Immigration Law Panel")
        self.assertIn("Hotung", panel["location"])
        self.assertEqual(panel["venue_class"], "home")

    def test_no_food_event_still_extracted_but_not_food(self):
        moot = next(e for e in self.events if e["title"] == "Moot Court Tryouts")
        self.assertFalse(moot["has_food"])


class TestTextBlocks(unittest.TestCase):
    def setUp(self):
        self.events = from_text_blocks(BLOCK_PAGE, "Test Org",
                                       "https://example.org", ref=REF)

    def test_only_food_blocks_with_dates_survive(self):
        self.assertEqual(len(self.events), 1)
        self.assertIn("Pizza and Policy", self.events[0]["title"])

    def test_parsed_datetime(self):
        self.assertEqual(self.events[0]["starts_at"][:16], "2026-09-23T12:00")

    def test_byo_block_rejected(self):
        self.assertNotIn("Study Group", " ".join(e["title"] for e in self.events))

    def test_dateless_block_rejected(self):
        self.assertNotIn("gala", " ".join(e["title"] for e in self.events).lower())

    def test_empty_page(self):
        self.assertEqual(from_text_blocks("<html></html>", "x", "u", REF), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestGenericOrgNames(unittest.TestCase):
    """An "organisation" that names no organisation.

    OCR reads a flyer footer and hands back "LAW ORGANIZATION", which is true
    of every host on the page and identifies none of them. Left in, it becomes
    a tag on the card and an entry in the filter menu nobody can use.
    """

    def test_generic_names_are_dropped(self):
        from docket import pretty_org
        for junk in ("LAW ORGANIZATION", "Law Organisation", "organization",
                     "student org", "Georgetown Law", "club"):
            self.assertEqual(pretty_org(junk), "", junk)

    def test_real_names_are_untouched(self):
        from docket import pretty_org
        for name in ("SIPLA", "APALSA", "TAX COUNCIL GEORGETOWN LAW",
                     "Barrister's Council",
                     "Georgetown Plaintiffs' Law Association"):
            self.assertEqual(pretty_org(name), name)


class TestOverlongOrgNames(unittest.TestCase):
    """OCR reads a flyer's whole header as the organisation.

    "GEORGETOWN LAW BARRISTER'S COUNCIL ALTERNATIVE DISPUTE RESOLUTION" is a
    banner, not a name, and in a nowrap tag it dragged the card 118px past a
    phone screen.
    """

    def test_a_banner_is_cut_to_a_name(self):
        got = pretty_org("GEORGETOWN LAW BARRISTER'S COUNCIL "
                         "ALTERNATIVE DISPUTE RESOLUTION")
        self.assertLessEqual(len(got), 42)
        self.assertIn("BARRISTER", got)

    def test_the_campus_prefix_goes_first(self):
        """It is a given on this page."""
        self.assertEqual(
            pretty_org("Georgetown Law Asian Pacific American "
                       "Law Students Association"),
            "Asian Pacific American Law Students")

    def test_it_never_cuts_mid_word(self):
        got = pretty_org("Corporate Crime and Anti-Corruption "
                         "Student Association")
        self.assertFalse(got.endswith("-"))
        self.assertTrue(got.split()[-1].isalpha() or "-" in got.split()[-1])

    def test_a_short_name_is_untouched(self):
        for name in ("SIPLA", "TEXAS CLUB", "Women of Color Collective",
                     "APALSA"):
            self.assertEqual(pretty_org(name), name)
