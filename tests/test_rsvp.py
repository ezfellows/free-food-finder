"""The RSVP tracker: which tab an event sits under, and which sign-up links
out of an email are safe to publish.

A link in an email can carry the reader. CampusGroups wraps every link with
the reader's member_id, a Google Form can arrive filled in with a name, and a
ride voucher link is money. So the page publishes a sign-up link from an
email only when it is an event-wide page on a known site, and the refusals
here matter more than the acceptances.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"
import docket                                        # noqa: E402
import store                                         # noqa: E402

FLO = "https://florecruit.com/v2/app/examplefirm/registration/events/101234/register"


class TestWhichTab(unittest.TestCase):
    """A firm in the room makes it a firm event, whoever hosts it."""

    def track(self, **event):
        return docket.rsvp_track(event)

    def test_a_named_firm(self):
        self.assertEqual(self.track(title="Antitrust Careers Panel",
                                    firms=[{"name": "Latham & Watkins"}]), "firm")

    def test_firm_networking_with_no_firm_recognised(self):
        self.assertEqual(self.track(title="Happy Hour", firms=[],
                                    network_kind="firm"), "firm")

    def test_the_title_says_so(self):
        """The 1L Law Firm Receptions name no single firm."""
        for title in ("Private Sector 1L Law Firm Reception",
                      "Howdy Hoyas Multi-Firm Panel & Networking",
                      "MLS Big Law Panel", "APALSA BigLaw 101"):
            self.assertEqual(self.track(title=title, firms=[],
                                        network_kind="recruiting"), "firm", title)

    def test_everything_else_is_the_law_school(self):
        for event in ({"title": "Hart Lecture", "network_kind": "academic"},
                      {"title": "Jobs with Judges", "network_kind": "recruiting"},
                      {"title": "Firmly Rooted: A Gardening Club Social"},
                      {}):
            self.assertEqual(docket.rsvp_track(event), "school", event)


class TestSignUpPages(unittest.TestCase):
    """The short list of shapes that are the same for every reader."""

    def test_accepted(self):
        for url in (
                FLO,
                FLO + "/",
                "https://www.florecruit.com/v2/app/gtownlaw/registration/events/"
                "100002/register/",
                "https://forms.gle/AbCdEf123456",
                "https://docs.google.com/forms/d/e/1FAIpQLSexampleexample/viewform",
                "https://docs.google.com/forms/d/e/1FAIpQLSexample/viewform?usp=sf_link",
                "https://forms.cloud.microsoft/Pages/ResponsePage.aspx?id=AbC123",
                "https://forms.office.com/pages/responsepage.aspx?id=AbC&route=shorturl",
                "https://georgetown.campusgroups.com/rsvp?id=123456",
                "https://law-georgetown-csm.symplicity.com/students/"
                "?s=event&ss=ws&mode=form&id=1489f897b35c45af62d4b51b36f13f8c"):
            self.assertTrue(docket.signup_page(url), url)

    def test_refused(self):
        for url in (
                # The reader's own click wrapper and unsubscribe link.
                "https://georgetown.campusgroups.com/click?email_links_id=5&member_id=87654321",
                "https://georgetown.campusgroups.com/unsubscribe?mailings_id=1&uid=99",
                "https://georgetown.campusgroups.com/click?id=5",
                "https://georgetown.campusgroups.com/click/rsvp?id=5",
                # A form filled in with the reader's name.
                "https://docs.google.com/forms/d/e/1FAIpQLSexample/viewform"
                "?usp=pp_url&entry.123=Jane+Doe",
                # A ride voucher is money.
                "https://r.uber.com/AbC123xYz",
                "https://www.lyft.com/i/ABC123",
                # A destination nobody can see.
                "https://tinyurl.com/MLSABigLawPanel",
                # A hiring platform's tokens.
                "https://examplefirm.hiringplatform.com/AbCdEfGhIjKlMnOp/QrStUvWxYz012345/en",
                # Flo Recruit, but not an event's own page.
                FLO + "?token=abc",
                "https://florecruit.com/v2/app/examplefirm/invite/AbCdEfGhIjKlMnOp",
                "https://help.florecruit.com/en/articles/AbCdEfGhIjKlMnOp",
                # A lookalike host.
                "https://florecruit.com.example.net/v2/app/x/registration/events/1/register",
                # The career site's sign-in page names no event.
                "https://law-georgetown-csm.symplicity.com/students/index.php?signin_tab=0",
                "https://law-georgetown-csm.symplicity.com/students/?id=1&uid=7",
                "https://law-georgetown-csm.symplicity.com/students/?s=home",
                "https://law-georgetown-csm.symplicity.com/students/app/jobs/detail/AbCdEf?id=5",
                # Somewhere else entirely, however harmless.
                "https://www.eventbrite.com/e/example-tickets-123",
                "javascript:alert(1)", ""):
            self.assertFalse(docket.signup_page(url), url)


class TestTheLinkOutOfAnEmail(unittest.TestCase):
    def test_the_link_after_an_rsvp_word(self):
        self.assertEqual(docket.rsvp_link(
            "Kirkland 1L Reception, 5:30pm. RSVP Here (" + FLO + ")"), FLO)

    def test_a_link_not_offered_for_signing_up_is_not_taken(self):
        self.assertEqual(docket.rsvp_link(
            "Read more about the firm at https://forms.gle/AbCdEf123456"), "")

    def test_a_refused_link_does_not_stop_the_next_one(self):
        text = ("RSVP HERE[](https://georgetown.campusgroups.com/click?"
                "email_links_id=5&member_id=87654321) or register here (" + FLO + ")")
        self.assertEqual(docket.rsvp_link(text), FLO)

    def test_trailing_punctuation_is_not_part_of_the_link(self):
        self.assertEqual(docket.rsvp_link(
            "Register here: https://forms.gle/AbCdEf123456*."),
            "https://forms.gle/AbCdEf123456")

    def test_a_voucher_link_beside_the_sign_up_is_never_taken(self):
        self.assertEqual(docket.rsvp_link(
            "Registration Link: https://r.uber.com/AbC123xYz"), "")

    def test_nothing_at_all(self):
        for text in (None, "", "Free pizza."):
            self.assertEqual(docket.rsvp_link(text), "")


class TestAStoredLinkMeetsTheRuleToo(unittest.TestCase):
    """A flyer's QR code is stored as its RSVP link. It is as often a
    shortener, a spreadsheet or the organiser's editing link as a form."""

    @classmethod
    def setUpClass(cls):
        cls.conn = store.connect(os.path.join(tempfile.mkdtemp(), "stored.db"))
        common = {"tier": "confirmed", "confidence": 95, "has_food": True,
                  "meals": ["snacks"], "venue_class": "law",
                  "location": "Hotung 2000", "starts_at": "2099-04-01T18:00:00"}
        for title, source, url, text in (
                ("Shortened", "broadcast", "https://bit.ly/abcdef", ""),
                ("Editing Link", "broadcast",
                 "https://docs.google.com/forms/d/1AbCdEfGhIjKlMnOpQrStUv/edit", ""),
                ("A Spreadsheet", "broadcast",
                 "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUv", ""),
                ("A Real Form", "broadcast", "https://forms.gle/AbCdEf123456", ""),
                ("Email Fallback", "mail", "https://qr.codes/AbC123",
                 "Free snacks. RSVP Here (" + FLO + ")")):
            store.upsert(cls.conn, dict(common, title=title, source=source,
                                        rsvp_url=url, description=text))
        cls.links = {e["title"]: e["rsvp_url"]
                     for e in docket.build_events(cls.conn, days=99999)}

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_all_five_are_built(self):
        """Guard the guard."""
        self.assertEqual(len(self.links), 5, self.links)

    def test_what_is_not_a_sign_up_page_is_dropped(self):
        for title in ("Shortened", "Editing Link", "A Spreadsheet"):
            self.assertEqual(self.links[title], "", title)

    def test_a_form_is_kept(self):
        self.assertEqual(self.links["A Real Form"], "https://forms.gle/AbCdEf123456")

    def test_an_email_falls_back_to_the_link_it_gives(self):
        self.assertEqual(self.links["Email Fallback"], FLO)

    def test_the_stored_link_itself_is_left_alone(self):
        """Dropped from the page, not from the record: a person may still
        want to see what the flyer pointed at."""
        row = self.conn.execute("SELECT rsvp_url FROM events WHERE title = ?",
                                ("Shortened",)).fetchone()
        self.assertEqual(row[0], "https://bit.ly/abcdef")


class TestOnTheBuiltEvents(unittest.TestCase):
    """What the page is handed, end to end from the database."""

    @classmethod
    def setUpClass(cls):
        cls.conn = store.connect(os.path.join(tempfile.mkdtemp(), "rsvp.db"))
        common = {"tier": "confirmed", "confidence": 95, "has_food": True,
                  "meals": ["dinner"], "venue_class": "law",
                  "network_score": 80, "network_kind": "firm",
                  "firms": [{"name": "Latham & Watkins"}],
                  "location": "Hotung 2000"}
        store.upsert(cls.conn, dict(
            common, source="mail", title="Example Firm 1L Reception",
            starts_at="2099-03-10T18:00:00",
            description="Dinner served. Lyft codes will be provided to those "
                        "who register at least 48 hours before the event. "
                        "RSVP Here (" + FLO + ")"))
        store.upsert(cls.conn, dict(
            common, source="mail", title="Flyer Link Wins",
            starts_at="2099-03-11T18:00:00", rsvp_url="https://forms.gle/Stored1",
            description="Dinner served. RSVP Here (" + FLO + ")"))
        store.upsert(cls.conn, dict(
            common, source="broadcast", title="Flyer Text Only",
            starts_at="2099-03-12T18:00:00", firms=[], network_kind="academic",
            description="Dinner served. RSVP Here (" + FLO + ")"))
        events = docket.build_events(cls.conn, days=99999)
        cls.by_title = {e["title"]: e for e in events}

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_all_three_are_built(self):
        """Guard the guard."""
        self.assertEqual(len(self.by_title), 3, list(self.by_title))

    def test_an_email_gives_its_sign_up_link(self):
        self.assertEqual(self.by_title["Example Firm 1L Reception"]["rsvp_url"], FLO)

    def test_a_stored_link_wins(self):
        self.assertEqual(self.by_title["Flyer Link Wins"]["rsvp_url"],
                         "https://forms.gle/Stored1")

    def test_only_email_is_read_for_a_link(self):
        """A flyer's link comes from its QR code, read when it was stored."""
        self.assertEqual(self.by_title["Flyer Text Only"]["rsvp_url"], "")

    def test_the_tracker_fields(self):
        event = self.by_title["Example Firm 1L Reception"]
        self.assertTrue(event["rsvp_needed"])
        self.assertFalse(event["rsvp_closed"])
        self.assertEqual(event["track"], "firm")
        self.assertEqual([p["label"] for p in event["perks"]], ["Lyft voucher"])
        # 48 hours before 6pm on the 10th.
        self.assertEqual(event["perk_by"], "2099-03-08T18:00")
        self.assertEqual(self.by_title["Flyer Text Only"]["track"], "school")
        self.assertEqual(self.by_title["Flyer Text Only"]["perk_by"], "")
        self.assertEqual(event["reception"], "reception")
        self.assertEqual(self.by_title["Flyer Text Only"]["reception"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
