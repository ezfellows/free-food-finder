"""A school with no profile refuses everything; a profile is how it earns rules.

Every other test runs under Georgetown's profile. These run under none, and
under a small made-up one, to hold the promise school.py makes: nothing in the
code assumes Georgetown, and at a school nobody has described, nothing from
anyone's inbox is published unless a person allowed the sender by hand.
"""
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from email.message import Message

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"

import campus                 # noqa: E402
import mailquery              # noqa: E402
import publish                # noqa: E402
import school                 # noqa: E402
import senders                # noqa: E402
import store                  # noqa: E402
from ingest import broadcast, mail   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def message(frm, subject="SBA Bake Sale", to="reader1@georgetown.edu"):
    msg = Message()
    msg["From"] = frm
    msg["To"] = to
    msg["Subject"] = subject
    return msg


def bulk(frm, subject="You're Invited: 1L Reception"):
    """A firm's mailing to the class: addressed to itself, everyone bcc'd."""
    return message(frm, subject, to=frm), "Join us. Unsubscribe"


class TestNoProfile(unittest.TestCase):
    """What Georgetown's rules admit, an unknown school's refuse."""

    def setUp(self):
        context = school.using(None)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def assertGeorgetownOnly(self, msg, body=""):
        with school.using("georgetown_law"):
            self.assertTrue(senders.classify(msg, body)["allow"])
        verdict = senders.classify(msg, body)
        self.assertFalse(verdict["allow"], verdict["reason"])

    def test_an_org_role_account_is_refused(self):
        self.assertGeorgetownOnly(message("lawsba@georgetown.edu"))

    def test_a_broadcast_platform_is_refused(self):
        self.assertGeorgetownOnly(message("no-reply@campusgroups.georgetown.edu",
                                          "SALDF: WEDNESDAY BOOK CLUB"))

    def test_a_firm_mailing_the_class_is_refused(self):
        self.assertGeorgetownOnly(*bulk("Recruit@weil.com"))

    def test_every_address_is_a_persons(self):
        self.assertTrue(senders.looks_personal("lawsba@georgetown.edu"))
        self.assertFalse(senders.is_school("lawsba@georgetown.edu"))
        self.assertFalse(senders.is_listserv("lawstudentsall@georgetown.edu"))

    def test_a_sender_allowed_by_hand_is_admitted(self):
        verdict = senders.classify(message("lawsba@georgetown.edu"), "",
                                   verdict="allow")
        self.assertTrue(verdict["allow"], verdict["reason"])

    def test_but_the_content_veto_still_runs(self):
        verdict = senders.classify(message("lawsba@georgetown.edu"),
                                   "Your Banner ID is below.", verdict="allow")
        self.assertFalse(verdict["allow"])

    def test_and_a_reply_is_still_a_conversation(self):
        verdict = senders.classify(message("lawsba@georgetown.edu",
                                           "Re: Coffee Chat"), "",
                                   verdict="allow")
        self.assertFalse(verdict["allow"])

    def test_nothing_to_search_for_until_someone_is_allowed(self):
        conn = store.connect(os.path.join(tempfile.mkdtemp(), "q.db"))
        self.assertEqual(mailquery.terms(conn), [])
        store.set_sender_verdict(conn, "chess@example.edu", "allow")
        self.assertEqual(mailquery.terms(conn), ["from:chess@example.edu"])

    def test_the_envelope_screen_agrees(self):
        found = mailquery.select([{"id": "1", "sender": "lawsba@georgetown.edu",
                                   "subject": "SBA Bake Sale"}],
                                 maildrop=tempfile.mkdtemp())
        self.assertEqual(found["fetch"], [])

    def test_no_building_is_known(self):
        place = campus.locate("McDonough 208")
        self.assertEqual(place["campus"], "unknown")
        self.assertEqual(place["place"], "McDonough 208")

    def test_but_an_address_and_a_webinar_still_read_as_such(self):
        self.assertEqual(campus.locate("801 17th Street NW")["campus"], "offcampus")
        self.assertEqual(campus.locate("Zoom")["campus"], "virtual")

    def test_no_flyer_newsletter_is_recognised(self):
        msg = message("lawstudentlife@info.georgetown.edu",
                      "OSL Weekly Broadcast - October 9, 2026")
        with school.using("georgetown_law"):
            self.assertTrue(broadcast.is_broadcast(msg))
        self.assertFalse(broadcast.is_broadcast(msg))

    def test_a_tracking_link_is_still_never_published(self):
        """Georgetown's tracking host is unknown here, but its `qs=` is not."""
        self.assertEqual(mail.public_link(
            ["https://click.info.georgetown.edu/?qs=abc123"]), "")
        self.assertEqual(mail.public_link(["https://mailchi.mp/x/y?e=0123456789"]), "")

    def test_the_page_names_no_school(self):
        html = publish.page([], "12 October 2026, 9:00 AM",
                            copy=publish.page_copy({}))
        for word in ("Georgetown", "McDonough", "Capitol", "OSL", "Main Campus"):
            self.assertNotIn(word, html)
        self.assertNotRegex(html, r"__[A-Z_]+__")


EXAMPLE = types.ModuleType("schools.example_state")
EXAMPLE.__dict__.update(
    NAME="Example State Law", CAMPUS="Downtown Campus",
    DOMAINS=("example.edu",),
    # Here a person's address is first.last; an office's has no dot.
    PERSON=r"\.",
    BROADCAST_DOMAINS=("lists.example.edu",),
    VENUES=((r"smith\s+hall", "Smith Hall", "Smith"),),
    SCHOOL_VENUE="",
    AWAY=r"north\s+campus", AWAY_NAME="North Campus", AWAY_TRAVEL="bus, 20 min",
    ROOM_EXAMPLE="Smith 204", TIMEZONE="America/Los_Angeles",
)


class TestAProfileOfYourOwn(unittest.TestCase):
    def setUp(self):
        context = school.using(EXAMPLE)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def test_a_person_is_refused(self):
        verdict = senders.classify(message("jane.doe@example.edu", "Lunch?"))
        self.assertFalse(verdict["allow"])
        self.assertIn("person", verdict["reason"])

    def test_an_office_is_admitted(self):
        verdict = senders.classify(message("chessclub@example.edu",
                                           "Chess and pizza, Thursday"))
        self.assertTrue(verdict["allow"], verdict["reason"])

    def test_georgetown_means_nothing_here(self):
        self.assertFalse(senders.classify(message("lawsba@georgetown.edu"))["allow"])

    def test_its_buildings_and_rooms(self):
        place = campus.locate("Smith Hall 204")
        self.assertEqual((place["campus"], place["place"]), ("home", "Smith 204"))
        self.assertEqual(campus.locate("North Campus Commons")["campus"], "away")

    def test_the_page_speaks_for_it(self):
        html = publish.page([], "12 October 2026, 9:00 AM",
                            copy=publish.page_copy({}))
        self.assertIn("Example State Law &middot; Downtown Campus", html)
        self.assertIn('placeholder="Smith 204"', html)
        self.assertIn("North Campus \\u00b7 bus, 20 min", html)
        self.assertNotIn("Georgetown", html)

    def test_its_calendar_keeps_its_own_clock(self):
        """A feed in UTC reads in the school's zone, not Georgetown's."""
        import icalparse
        when, _ = icalparse.parse_datetime("20261021T200000Z", {})
        self.assertEqual(when.isoformat(), "2026-10-21T13:00:00")
        with school.using("georgetown_law"):
            when, _ = icalparse.parse_datetime("20261021T200000Z", {})
        self.assertEqual(when.isoformat(), "2026-10-21T16:00:00")

    def test_its_searches(self):
        conn = store.connect(os.path.join(tempfile.mkdtemp(), "q.db"))
        self.assertEqual(mailquery.terms(conn),
                         ["from:example.edu", "from:lists.example.edu"])


class TestChoosingAProfile(unittest.TestCase):
    def test_by_name(self):
        self.assertEqual(school.load("georgetown_law").NAME, "Georgetown Law")
        self.assertFalse(school.load("").configured)

    def test_only_a_profile_name_is_accepted(self):
        for name in ("../data/config", "schools.georgetown_law", "_template",
                     "Georgetown"):
            with self.assertRaises(ValueError, msg=name):
                school.load(name)

    def test_a_missing_profile_says_so(self):
        with self.assertRaisesRegex(ValueError, "no school profile"):
            school.load("no_such_school")

    def test_a_misspelt_setting_is_refused(self):
        bad = types.ModuleType("schools.typo")
        bad.DOMIANS = ("example.edu",)
        with self.assertRaisesRegex(ValueError, "unknown setting DOMIANS"):
            school.load(bad)

    def test_a_setting_of_the_wrong_shape_is_refused(self):
        """A string where a list belongs is a list of its letters."""
        bad = types.ModuleType("schools.shape")
        bad.DOMAINS = "example.edu"
        with self.assertRaisesRegex(ValueError, "DOMAINS should be tuple"):
            school.load(bad)

    def test_the_config_file_chooses_when_the_environment_does_not(self):
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(path, "w") as fh:
            json.dump({"school": "georgetown_law"}, fh)
        env = os.environ.pop("FFF_SCHOOL")
        before, os.environ["FFF_CONFIG"] = os.environ["FFF_CONFIG"], path
        try:
            self.assertEqual(school.chosen(), "georgetown_law")
        finally:
            os.environ["FFF_CONFIG"] = before
            os.environ["FFF_SCHOOL"] = env

    def test_the_template_lists_every_setting_and_nothing_else(self):
        import importlib
        template = importlib.import_module("schools._template")
        named = {k for k in vars(template) if k.isupper() and not k.startswith("_")}
        settings = {k for k in vars(school.Profile) if k.isupper()}
        self.assertEqual(named, settings)
        school.load(template)            # and every one of them is well-formed

    def test_the_command_reports_no_profile(self):
        env = dict(os.environ, FFF_SCHOOL="")
        out = subprocess.run([os.path.join(ROOT, "fff"), "school"], cwd=ROOT,
                             env=env, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("No school profile chosen", out.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
