"""Tests for turning listserv email into scored events."""
import email, os, sys, unittest
from email.message import EmailMessage
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"
from ingest import mail
from ingest.mail import SUBJECT_NOISE, from_maildrop, guess_location, org_of

SAMPLES = os.path.join(os.path.dirname(__file__), "..", "samples")


def msg(raw):
    return email.message_from_string(raw)


class TestSubjectCleaning(unittest.TestCase):
    def test_strips_list_tag_and_reply_prefixes(self):
        for raw, want in [
            ("[GULC-ANNOUNCE] Lunch Debate", "Lunch Debate"),
            ("Re: Free pizza today", "Free pizza today"),
            ("Fwd: [ELS] Re: Panel", "Panel"),
            ("RE:  Reception", "Reception"),
        ]:
            self.assertEqual(SUBJECT_NOISE.sub("", raw).strip(), want, raw)

    def test_does_not_eat_words_starting_with_re(self):
        """'Resume Review' must survive intact."""
        for subject in ("Resume Review Drop-In Hours", "Reception tonight",
                        "Refreshments provided", "Fwder Society Mixer"):
            self.assertEqual(SUBJECT_NOISE.sub("", subject).strip(), subject)


class TestOrgAttribution(unittest.TestCase):
    def test_display_name_beats_shared_list_id(self):
        m = msg("From: Georgetown Federalist Society <fedsoc@law.georgetown.edu>\n"
                "List-Id: GULC Announcements <gulc-announce.law.georgetown.edu>\n"
                "Subject: x\n\nbody")
        self.assertEqual(org_of(m), "Georgetown Federalist Society")

    def test_falls_back_to_list_id(self):
        m = msg("From: <noreply@law.georgetown.edu>\n"
                "List-Id: <blsa.law.georgetown.edu>\nSubject: x\n\nbody")
        self.assertEqual(org_of(m), "blsa")


class TestLocation(unittest.TestCase):
    def test_named_law_venues(self):
        self.assertIn("Hotung", guess_location("join us in Hotung 2000 at noon"))
        self.assertIn("McDonough", guess_location("McDonough Hall lobby"))
        self.assertIn("Hart", guess_location("in Hart Auditorium on Friday"))

    def test_no_venue(self):
        self.assertEqual(guess_location("somewhere on campus"), "")


class TestSampleCorpus(unittest.TestCase):
    """End-to-end over the shipped .eml fixtures."""

    @classmethod
    def setUpClass(cls):
        cls.by_title = {e["title"]: e for e in from_maildrop(SAMPLES)}

    def get(self, fragment):
        for title, ev in self.by_title.items():
            if fragment.lower() in title.lower():
                return ev
        self.fail(f"no sample event matching {fragment!r} in {list(self.by_title)}")

    def test_every_sample_parses(self):
        """Counts only the fixtures this ingester owns.

        Broadcast emails carry their events as images and are handled by
        ingest/broadcast.py; mail.py deliberately yields nothing for them.
        """
        import email as _email
        from ingest.broadcast import is_broadcast
        expected = 0
        for name in os.listdir(SAMPLES):
            if not name.endswith(".eml"):
                continue
            with open(os.path.join(SAMPLES, name), "rb") as fh:
                if not is_broadcast(_email.message_from_bytes(fh.read())):
                    expected += 1
        self.assertEqual(len(self.by_title), expected)

    def test_free_pizza_confirmed(self):
        e = self.get("Chevron")
        self.assertEqual(e["tier"], "confirmed")
        self.assertEqual(e["meals"], ["lunch"])
        self.assertEqual(e["location"], "Hotung 2000")
        self.assertTrue(e["rsvp_required"])

    def test_brown_bag_rejected(self):
        self.assertEqual(self.get("Brown Bag")["tier"], "unlikely")

    def test_bake_sale_rejected(self):
        self.assertEqual(self.get("Bake Sale")["tier"], "unlikely")

    def test_explicit_no_food_rejected(self):
        e = self.get("Resume Review")
        self.assertEqual(e["tier"], "unlikely")
        self.assertTrue(e["title"].startswith("Resume"))

    def test_book_talk_reception_kept(self):
        """Book 'available for purchase' must not veto the catered reception."""
        e = self.get("Book Talk")
        self.assertTrue(e["has_food"])
        self.assertEqual(e["org"], "IP Law Society")

    def test_html_email_parsed(self):
        e = self.get("BLSA")
        self.assertTrue(e["has_food"])
        self.assertNotIn("<p>", e["description"])
        self.assertEqual(e["starts_at"][:16], "2026-09-11T18:00")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestFirmRecruitingEvents(unittest.TestCase):
    """What a firm's own recruiting mail should produce."""

    def message(self, frm, subject, body, to=None):
        msg = email.message.EmailMessage()
        msg["From"] = frm
        msg["To"] = to or frm
        msg["Subject"] = subject
        msg.set_content(body)
        return msg

    def test_the_firm_hosts_it_not_the_coordinator(self):
        """`org_of` takes the From display name, which is the recruiter.

        Publishing "Jane Roe Doe" as the organisation names a person
        doing her job rather than the body putting on the event.
        """
        msg = self.message(
            "Jane Roe Doe <Jane.Roe.Doe@friedfrank.com>",
            "Fried Frank - 1L Citywide Reception - October 8",
            "Join us for a networking reception. Thursday, October 8. "
            "6-7:30 pm. 801 17th Street NW, Washington, DC. Unsubscribe")
        event = mail.message_to_event(msg)
        self.assertTrue(event)
        self.assertIn("Fried", event["org"])
        self.assertNotIn("Jane", event["org"])

    def test_an_off_campus_address_is_kept(self):
        msg = self.message(
            "Recruit@weil.com", "1L Reception",
            "Join us Tuesday, October 6 at 6pm. 2001 M Street NW, "
            "Washington DC. Unsubscribe")
        event = mail.message_to_event(msg)
        self.assertEqual(event["location"], "2001 M Street NW")

    def test_the_sender_alone_makes_it_a_firm_event(self):
        """Bass Berry's open house says only "invites 1L law students".

        Nothing in the text is about hiring, because the hiring is the
        premise. Who sent it is the evidence the body omits.
        """
        msg = self.message(
            "Mary Major <Mary.Major@bassberry.com>",
            "Bass, Berry & Sims 1L Open House",
            "Bass, Berry & Sims invites 1L law students to our 1L Open House "
            "on October 6 at 5:00 pm. Unsubscribe")
        event = mail.message_to_event(msg)
        self.assertTrue(event)
        self.assertIn(event["network_kind"], ("firm", "recruiting"))
        names = [f["name"] if isinstance(f, dict) else f
                 for f in (event.get("firms") or [])]
        self.assertIn("Bass, Berry & Sims", names)

    def test_street_addresses_only_where_there_is_one(self):
        self.assertEqual(mail.street_address("801 17th Street NW"),
                         "801 17th Street NW")
        self.assertEqual(mail.street_address("McDonough 201"), "")


class TestRelayedOrgSurvivesTheBodyExtractor(unittest.TestCase):
    """CampusGroups relays every org through one no-reply address.

    The org's own name is only in the footer, and both footer patterns are
    line-anchored -- but `body_text` collapses every run of whitespace, so by
    the time `relayed_org` saw the body the line breaks were gone. Every one
    of the subscribed orgs would have been credited to "no-reply".
    """

    def message(self, footer):
        msg = EmailMessage()
        msg["From"] = "no-reply@campusgroups.georgetown.edu"
        msg["To"] = "reader1@georgetown.edu"
        msg["Subject"] = "[Antitrust] Upcoming Event!"
        msg.set_content(
            "Hi all, we are having an event featuring Ropes & Gray on "
            "September 30, 5:30 PM-7:00 PM in McDonough 220. There will be "
            "food.\n\n" + footer)
        return msg

    def test_the_heading_above_georgetown_university(self):
        msg = self.message(
            "Antitrust & Competition Law Association\n\nGeorgetown University\n\n"
            "Email sent by Alex Doe <zzz488@georgetown.edu> to reader1@georgetown.edu\n"
            "Unsubscribe")
        self.assertEqual(mail.relayed_org(mail.body_lines(msg)),
                         "Antitrust & Competition Law Association")

    def test_the_collapsed_body_is_what_broke_it(self):
        """`body_text` is still the right input for scoring, and still flat."""
        msg = self.message(
            "Georgetown Energy Law Group\n\nGeorgetown University\n\n"
            "Email sent by Sam Roe <zz2078@georgetown.edu> to reader1@georgetown.edu")
        self.assertNotIn("\n", mail.body_text(msg))
        self.assertIn("\n", mail.body_lines(msg))
        self.assertEqual(mail.relayed_org(mail.body_lines(msg)),
                         "Georgetown Energy Law Group")

    def test_a_personal_sender_in_the_footer_is_not_an_org(self):
        """Falling through to "Email sent by", the address decides."""
        msg = self.message(
            "Email sent by Alex Doe <zzz488@georgetown.edu> to reader1@georgetown.edu")
        self.assertEqual(mail.relayed_org(mail.body_lines(msg)), "")


class TestABulletinIsNotOneEvent(unittest.TestCase):
    """A subject that names a publication is not an event.

    "Private Sector Pulse - September 24, 2026" was read as one event on the
    24th -- the issue date -- because the subject was tried before the body,
    and the six events inside it never surfaced, among them both 1L Law Firm
    Receptions.
    """

    def message(self, subject, body):
        msg = EmailMessage()
        msg["From"] = "lawcareerstrategy@georgetown.edu"
        msg["To"] = "reader1@georgetown.edu"
        msg["Subject"] = subject
        msg["List-Id"] = "careers <careers.list-id.example>"
        msg.set_content(body)
        return msg

    def test_a_body_with_several_events_is_split(self):
        body = ("Davis Polk Investment Management Career Panel\n"
                "Tuesday, October 6, 4:00-6:00 pm, Sports & Fitness Lobby. "
                "A social hour with refreshments will follow.\n\n"
                "Live Firm Prospects Resources Demonstration\n"
                "Wednesday, October 7, 3:30-4:30 pm, McDonough 206.\n")
        got = mail.messages_to_events(
            self.message("Private Sector Pulse - September 24, 2026", body))
        titles_found = [e["title"] for e in got]
        self.assertNotIn("Private Sector Pulse", titles_found)
        self.assertGreaterEqual(len(got), 2, titles_found)

    def test_a_one_event_email_keeps_its_subject_title(self):
        """Measured: Bass Berry, Weil, Fried Frank, Milbank and LSDAD each
        split to at most one event, and keep their better-written subject."""
        body = ("Bass, Berry & Sims invites 1L students to our 1L Open House "
                "on October 6 at 5:00 pm.")
        got = mail.messages_to_events(
            self.message("Bass, Berry & Sims 1L Open House", body))
        self.assertEqual([e["title"] for e in got],
                         ["Bass, Berry & Sims 1L Open House"])


class TestAddingEventsReadByHand(unittest.TestCase):
    """`./fff add` records what a reviewer read out of a newsletter.

    The reading is the reviewer's; the verdicts must still be the code's, so
    an event added by hand is judged exactly like one the splitter found.
    """

    def run_add(self, events):
        import json, subprocess, tempfile
        path = os.path.join(tempfile.mkdtemp(), "e.json")
        with open(path, "w") as fh:
            json.dump(events, fh)
        env = dict(os.environ, FFF_DB=os.path.join(tempfile.mkdtemp(), "a.db"))
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return subprocess.run([os.path.join(root, "fff"), "add", path],
                              capture_output=True, text=True, cwd=root, env=env)

    def test_an_entry_without_an_org_is_refused(self):
        out = self.run_add([{"title": "Coffee", "starts_at": "2026-10-01T12:00:00"}])
        self.assertIn("skipped (missing org)", out.stdout)

    def test_the_command_is_listed(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "fff")) as fh:
            self.assertIn("|add|", fh.read())


class TestAMailboxNameIsAnAcronym(unittest.TestCase):
    """The connector gives no display name; "cflo" is CFLO."""

    def org(self, sender):
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"] = sender
        return mail.org_of(msg)

    def test_a_short_mailbox_is_uppercased(self):
        self.assertEqual(self.org("cflo@georgetown.edu"), "CFLO")

    def test_a_display_name_still_wins(self):
        self.assertEqual(self.org("Equal Justice Foundation <lawejf@georgetown.edu>"),
                         "Equal Justice Foundation")

    def test_a_long_mailbox_is_left_alone(self):
        self.assertEqual(self.org("lawcareerstrategy@georgetown.edu"),
                         "lawcareerstrategy")
