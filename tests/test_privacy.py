"""The published page must carry nothing personal.

Two separate things could leak, and both have leaked before:

* A **personal footprint on firm names**. An early version carried how many
  alumni the owner knew at each firm, and which of them he had emailed. Firm
  names are public and useful; anything about the person running the docket
  is their job search. Old rows can still carry it, so it is scrubbed on read.
* **Filesystem paths**, which contain `/Users/<name>`. Flyers live on disk,
  so a page that referenced them by path would publish a home directory.

These scan whole payloads rather than named fields, so a change that
reintroduces either somewhere new still trips.
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
import store

store.DB_PATH = os.path.join(tempfile.mkdtemp(), "privacy.db")

from ingest import mail                              # noqa: E402
import docket                                        # noqa: E402
import publish                                       # noqa: E402
from ingest.mail import from_maildrop                # noqa: E402
from network import score_network                    # noqa: E402

PRIVATE_KEYS = ("contacts", "contacted", "responded", "referrals", "markets")
PRIVATE_PHRASES = ("alumni you can name", "already emailed", "date_sent",
                   "referral")
SAMPLES = os.path.join(os.path.dirname(__file__), "..", "samples")


class TestScorerLeaksNothing(unittest.TestCase):
    def test_firm_entries_have_only_a_name(self):
        verdict = score_network("1L Reception",
                                "Covington & Burling attorneys will attend.", "")
        self.assertTrue(verdict["firms"])
        for firm in verdict["firms"]:
            self.assertEqual(set(firm.keys()), {"name"})

    def test_score_network_takes_no_alumni_argument(self):
        """The parameter is gone, so it cannot be wired back by accident."""
        import inspect
        self.assertNotIn("alumni_idx",
                         inspect.signature(score_network).parameters)


class TestPublishedPage(unittest.TestCase):
    """The real artefact: the HTML that goes on the internet."""

    @classmethod
    def setUpClass(cls):
        cls.conn = store.connect()
        for event in from_maildrop(SAMPLES):
            store.upsert(cls.conn, event)
        # A legacy row in the pre-scrub shape, to prove reads are cleaned and
        # not merely that fresh writes happen to be clean.
        store.upsert(cls.conn, {
            "title": "Legacy Firm Reception",
            "starts_at": "2099-06-01T18:00:00", "source": "broadcast",
            "tier": "confirmed", "confidence": 95, "has_food": True,
            "meals": ["snacks"], "venue_class": "law",
            "location": "Hotung 2000", "flyer_id": "ab12cd34ef567890",
            "rsvp_url": "https://forms.gle/abc",
            "network_score": 100, "network_kind": "firm",
            "firms": [{"name": "Latham & Watkins", "contacts": 214,
                       "contacted": 3, "responded": 1, "referrals": 2,
                       "markets": ["dc", "ny"]}],
            "network_reason": "1 firm(s) named; 214 alumni you can name"})
        cls.built = publish.build(cls.conn, days=99999)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_the_page_actually_has_a_firm_event_in_it(self):
        """Guard the guard: a vacuous page would pass everything below."""
        self.assertIn("Latham", self.built["html"])

    def test_no_private_keys_in_the_page(self):
        for key in PRIVATE_KEYS:
            self.assertNotIn(f'"{key}"', self.built["html"], key)

    def test_no_private_phrases_in_the_page(self):
        page = self.built["html"].lower()
        for phrase in PRIVATE_PHRASES:
            self.assertNotIn(phrase, page, phrase)

    def test_no_home_directory_in_the_page(self):
        self.assertNotIn("/Users/", self.built["html"])
        self.assertNotIn(os.path.expanduser("~"), self.built["html"])

    def test_flyers_are_referenced_by_relative_path(self):
        for published, source in self.built["files"].items():
            self.assertFalse(published.startswith("/"), published)
            self.assertNotIn("..", published)
            self.assertTrue(source.endswith(".card.jpg"), source)

    def test_events_carry_no_flyer_id_only_a_published_path(self):
        for event in self.built["events"]:
            self.assertNotIn("flyer_id", event)

    def test_ics_feed_leaks_nothing(self):
        text = docket.build_ics(self.conn, days=99999).lower()
        for phrase in PRIVATE_PHRASES:
            self.assertNotIn(phrase, text)
        self.assertNotIn("/users/", text)


class TestScrubbingLegacyRows(unittest.TestCase):
    """Rows written before the alumni data was removed still carry it."""

    def test_legacy_firm_dicts_reduce_to_names(self):
        legacy = [{"name": "Latham & Watkins", "contacts": 214,
                   "contacted": 3, "markets": ["dc"]}]
        self.assertEqual(docket.public_firms(legacy),
                         [{"name": "Latham & Watkins"}])
        self.assertNotIn("214", docket.firm_summary(legacy))

    def test_legacy_reason_strings_are_scrubbed(self):
        self.assertEqual(
            docket.public_reason("explicitly networking; 1 firm(s) named; "
                                 "214 alumni you can name"),
            "explicitly networking; 1 firm(s) named")

    def test_the_useful_half_of_the_reason_survives(self):
        cleaned = docket.public_reason("reception; 12 alumni you can name")
        self.assertIn("reception", cleaned)

    def test_plain_strings_and_junk(self):
        self.assertEqual(docket.public_firms(["Milbank"]),
                         [{"name": "Milbank"}])
        self.assertEqual(docket.public_firms(None), [])
        self.assertEqual(docket.public_firms([{"contacts": 5}]), [])
        self.assertEqual(docket.firm_summary(None), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestPerRecipientLinksAreNeverPublished(unittest.TestCase):
    """`click.info.georgetown.edu` links carry a token naming the recipient.

    Following one registers a click as the person whose mailbox it came from.
    `broadcast.unwrap` already refuses to resolve them -- but the mail path
    took the first link in the body as the event's link, so an OSL broadcast
    would have published dozens of them for readers to click.
    """

    def test_a_tracking_link_is_skipped(self):
        self.assertEqual(
            mail.public_link(["https://click.info.georgetown.edu/?qs=ABB7abc"]),
            "")

    def test_the_next_real_link_is_used_instead(self):
        self.assertEqual(
            mail.public_link(["https://click.info.georgetown.edu/?qs=ABB7abc",
                              "https://forms.gle/abc"]),
            "https://forms.gle/abc")

    def test_ordinary_links_are_untouched(self):
        for url in ("https://georgetown.campusgroups.com/x",
                    "https://www.law.georgetown.edu/event",
                    "https://forms.gle/abc"):
            self.assertEqual(mail.public_link([url]), url)

    def test_a_lookalike_host_is_not_trusted_either(self):
        self.assertEqual(
            mail.public_link(["https://a.click.info.georgetown.edu/?qs=x"]), "")


class TestRecipientIdentifyingParameters(unittest.TestCase):
    """A tracking link is not always on a tracking host.

    CampusGroups wraps every link in its newsletters as
    `/click?email_links_id=...&member_id=12345678`, on
    `georgetown.campusgroups.com` -- a host that is otherwise a perfectly
    good destination. The parameter is what names the reader, not the host.
    """

    def test_a_member_id_disqualifies_a_link(self):
        self.assertEqual(mail.public_link([
            "https://georgetown.campusgroups.com/click"
            "?email_links_id=522485&member_id=12345678"]), "")

    def test_the_same_host_without_one_is_fine(self):
        url = "https://georgetown.campusgroups.com/group/sipla"
        self.assertEqual(mail.public_link([url]), url)

    def test_mailchimp_links_are_never_published(self):
        self.assertEqual(mail.public_link([
            "https://gulcapalsa.us21.list-manage.com/unsubscribe"
            "?u=353f&e=0123456789"]), "")

    def test_the_first_clean_link_still_wins(self):
        self.assertEqual(mail.public_link([
            "https://georgetown.campusgroups.com/click?member_id=1",
            "https://forms.gle/abc"]), "https://forms.gle/abc")


class TestMailchimpBrowserCopies(unittest.TestCase):
    """Every Private Sector Pulse opens with "View this email in your
    browser", a mailchi.mp link carrying the reader's `e=` subscriber hash."""

    def test_the_browser_copy_is_never_published(self):
        self.assertEqual(mail.public_link([
            "https://mailchi.mp/ba36b2df4739/private-sector-pulse?e=abcdef0123"]),
            "")

    def test_a_symplicity_rsvp_link_is_fine(self):
        """Its `id` names the event, not the reader."""
        url = ("https://law-georgetown-csm.symplicity.com/students/"
               "?s=event&ss=ws&mode=form&id=1489f897b35c45af62d4b51b36f13f8c")
        self.assertEqual(mail.public_link([url]), url)


class TestTheReadersMailboxStaysOut(unittest.TestCase):
    """What an email says about the mailbox it was read from.

    The page of 8 October 2026 published seven `click.info ... ?qs=` links, a
    CampusGroups `member_id=` link and the reader's own address -- in an
    "Email sent by <student> to <reader>" footer -- all inside raw
    descriptions the page never even displayed.
    """

    TRACES = ("click.info.georgetown.edu", "qs=ABB7", "member_id=",
              "email sent by", "rd123@georgetown.edu", "zz999@georgetown.edu")

    @classmethod
    def setUpClass(cls):
        cls.conn = store.connect(os.path.join(tempfile.mkdtemp(), "traces.db"))
        footer = ("Contact zz999@georgetown.edu. "
                  "https://click.info.georgetown.edu/?qs=ABB7InYiOjEsImQiOj "
                  "Apply HERE (https://georgetown.campusgroups.com/click?"
                  "email_links_id=524942&member_id=87654321) "
                  "Email sent by A Student to rd123@georgetown.edu Unsubscribe")
        common = {"source": "mail", "org": "OutLaw", "tier": "confirmed",
                  "confidence": 95, "has_food": True, "meals": ["snacks"],
                  "venue_class": "law", "network_score": 70,
                  "network_kind": "firm"}
        store.upsert(cls.conn, dict(
            common, title="Speed Networking", starts_at="2099-07-01T17:00:00",
            location="Hotung 2001",
            description="Speed Networking, 5pm, Hotung 2001. " + footer))
        # A splitter that lifts the footer into the title or the place.
        store.upsert(cls.conn, dict(
            common, title="Milbank Reception " + footer,
            starts_at="2099-07-02T18:00:00",
            location="Hotung 2001, contact zz999@georgetown.edu"))
        cls.html = publish.build(cls.conn, days=99999)["html"]

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_both_events_are_on_the_page(self):
        """Guard the guard: a page without them would pass vacuously."""
        self.assertIn("Speed Networking", self.html)
        self.assertIn("Milbank Reception", self.html)

    def test_no_trace_of_the_mailbox(self):
        page = self.html.lower()
        for trace in self.TRACES:
            self.assertNotIn(trace.lower(), page, trace)

    def test_ordinary_links_and_office_addresses_survive(self):
        text = docket.public_text(
            "RSVP (https://forms.gle/abc123) or write outlaw@law.georgetown.edu")
        self.assertIn("https://forms.gle/abc123", text)
        self.assertIn("outlaw@law.georgetown.edu", text)
