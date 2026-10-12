"""The last line before anything reaches a public page.

Every case is drawn from the mailbox this reads. The distinction under test is
not privacy wording -- it is whether a message went to one reader or to the
school, because the same sentence means different things in each. "Dear Alex"
opens a CampusGroups newsletter sent to four hundred people.
"""

import os
import sys
import unittest
from email.message import Message

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"

import personal  # noqa: E402

BULK = "View this email in your browser\n%s\nUnsubscribe"


def message(frm, to="reader1@georgetown.edu", subject="", bcc=None):
    msg = Message()
    msg["From"] = frm
    msg["To"] = to
    if bcc:
        msg["Bcc"] = bcc
    msg["Subject"] = subject
    return msg


class TestWhatIsPersonal(unittest.TestCase):
    def block(self, frm="lawdean@info.georgetown.edu", subject="", body=""):
        verdict = personal.assess(message(frm, subject=subject), body)
        self.assertTrue(verdict["personal"], f"{subject or body[:40]!r} allowed")
        return verdict["reason"]

    def test_a_calendar_invitation(self):
        reason = self.block(subject="Invitation: APALSA First Look @ Thu Sep 17")
        self.assertIn("calendar", reason)

    def test_an_individual_determination(self):
        for body in ("Congratulations, you have been selected for the Scholars "
                     "reception.",
                     "You have been nominated for the Blume Fellowship.",
                     "We are pleased to offer you a place on the programme."):
            self.block(body=body)

    def test_something_only_one_person_can_have(self):
        for body in ("Your callback interview with Covington is confirmed.",
                     "Your appointment with Dean Okafor is at 2pm.",
                     "Thank you for registering for the symposium.",
                     "We are moving forward with your candidacy.",
                     "Unfortunately we will not advance your candidacy."):
            self.block(body=body)

    def test_advice_about_candidacies_in_general(self):
        """The 8 Oct 2026 Pulse, to every 1L, was refused for this."""
        text = ("Take the interview preparation process seriously as the "
                "interview can greatly impact your candidacy.")
        self.assertFalse(personal.assess_text(text, bulk=True)["personal"])
        # The excuse covers that phrase only, not the rest of the message.
        self.assertTrue(personal.assess_text(
            text + " Your callback interview with Covington is confirmed.",
            bulk=True)["personal"])

    def test_an_invitation_qualified_as_private(self):
        self.block(body="You are personally invited to a private dinner.")
        self.block(body="You are invited to an exclusive reception.")

    def test_one_person_writing_to_one_reader(self):
        verdict = personal.assess(
            message("jdoe@orrick.com", subject="Re: Coffee Chat"),
            "Hi Alex, happy to chat next week.")
        self.assertTrue(verdict["personal"])


class TestWhatIsNot(unittest.TestCase):
    """A fail-safe that eats the feed protects nothing anyone will read."""

    def allow(self, frm="no-reply@campusgroups.georgetown.edu", subject="",
              body=""):
        verdict = personal.assess(message(frm, subject=subject), body)
        self.assertFalse(verdict["personal"],
                         f"{subject or body[:40]!r} blocked: {verdict['reason']}")

    def test_a_newsletter_that_greets_you_by_name(self):
        # CampusGroups opens every mail "Dear Alex". It still went to 400 people.
        self.allow(body=BULK % "Dear Alex, our GBM is Tuesday at 5pm.")

    def test_an_ordinary_open_invitation(self):
        # Half the student orgs open this way; blocking it empties the feed.
        self.allow(body=BULK % "You're invited to our first GBM! Pizza provided.")

    def test_public_rsvp_language(self):
        for phrase in ("Reserve your spot at the reception.",
                       "Save your seat for the panel.",
                       "We are delighted to invite you to the colloquium.",
                       "Submit your application by October 2."):
            self.allow(body=BULK % phrase)

    def test_a_restricted_but_broadcast_event(self):
        """"Members only" describes the event, not the envelope.

        details.py publishes these as tags a reader can act on. Blocking on
        the wording would delete real events that others can attend.
        """
        for phrase in ("Skills training for current ADR members only.",
                       "By invitation only.", "1Ls only. Space is limited.",
                       "Exclusively for SSP alumni."):
            self.allow(body=BULK % phrase)

    def test_a_role_account_writing_to_one_subscriber(self):
        """`lawapalsa@` mails each member their own copy."""
        self.allow(frm="lawapalsa@georgetown.edu",
                   body="Our GBM is Tuesday at 5pm in McDonough 205.")


class TestBulkDetection(unittest.TestCase):
    def test_markers_in_the_body(self):
        for marker in ("Unsubscribe", "View this email in your browser",
                       "Email sent by ILSA", "manage your preferences"):
            self.assertTrue(personal.is_bulk(None, f"Hello. {marker}"), marker)

    def test_a_list_header(self):
        msg = message("x@georgetown.edu")
        msg["List-Unsubscribe"] = "<https://example.test/u>"
        self.assertTrue(personal.is_bulk(msg, "Hello."))

    def test_a_plain_note_is_not_bulk(self):
        self.assertFalse(personal.is_bulk(None, "Hi, are you free Thursday?"))


class TestPerPassage(unittest.TestCase):
    """A bulletin can carry one personal paragraph among five public ones."""

    def test_the_personal_paragraph_is_the_only_one_refused(self):
        public = "Cohen Milstein: Federal Antitrust Law, Hotung 2001. Open to all."
        private = "Your callback interview with Covington, their DC office."
        self.assertFalse(personal.assess_text(public, bulk=True)["personal"])
        self.assertTrue(personal.assess_text(private, bulk=True)["personal"])


if __name__ == "__main__":
    unittest.main()


class TestRegisteredAttendeesOnly(unittest.TestCase):
    """Mail to the people who already signed up is not an announcement.

    "TIME CHANGE: 1L Lunch with Dean Moreno" went to eleven named 1Ls: free
    food, a room number, and nobody else invited. It reached the fail-safe
    marked BULK, because `BULK_BODY` matches "you are receiving this" -- the
    opening of every newsletter, and also the opening of the sentence that
    proved this one was closed. Publishing it would have advertised a private
    lunch and put ten other students' netIDs in reach.
    """

    def message(self, frm="lawstudentlife@georgetown.edu", to=None, subject=""):
        msg = Message()
        msg["From"] = frm
        msg["To"] = to or ", ".join(
            f"{x}@georgetown.edu" for x in
            ("acb348", "mq166", "jpm430", "msw129", "reader1"))
        msg["Subject"] = subject
        return msg

    def test_the_dean_bailin_lunch_is_blocked(self):
        body = ("Good morning,\n\nIf you are receiving this email, you have "
                "registered for Lunch with Dean Moreno today (9/18). Please "
                "arrive at 12:45pm in Hotung 3101.")
        verdict = personal.assess(
            self.message(subject="TIME CHANGE: 1L Lunch with Dean Moreno"), body)
        self.assertTrue(verdict["personal"], verdict)

    def test_being_told_you_are_receiving_it_does_not_make_it_public(self):
        """The bulk marker must not license the closed-event wording."""
        body = "If you are receiving this email, you have registered for the trip."
        self.assertTrue(personal.is_bulk(self.message(), body))
        self.assertTrue(personal.assess(self.message(), body)["personal"])

    def test_other_confirmation_wordings(self):
        for body in ("You are confirmed for the site visit on Friday.",
                     "Your registration for the clinic intake session is complete.",
                     "You signed up for the 1L mentorship breakfast."):
            self.assertTrue(personal.assess(self.message(), body)["personal"], body)

    def test_a_public_invitation_still_passes(self):
        for body in ("You're invited to our first GBM! Pizza will be provided.",
                     "Register here to join us for the panel. All are welcome.",
                     "Reserve your spot for the alumni reception.",
                     "Sign up to attend the ADR Bootcamp."):
            self.assertFalse(personal.assess(self.message(), body)["personal"], body)


class TestCourtesyIsNotIntimacy(unittest.TestCase):
    """"Thank you for attending" means different things in different mail.

    In a one-to-one note it precedes something only this reader gets. At the
    top of an org newsletter it is a pleasantry about last night's meeting --
    and the Antitrust Association's newsletter went on to announce a Ropes &
    Gray panel with three attorneys and food, which is precisely what this
    site is for. Blocking on the greeting threw the event away.
    """

    def bulk_org_mail(self, body):
        msg = Message()
        msg["From"] = "no-reply@campusgroups.georgetown.edu"
        msg["To"] = "reader1@georgetown.edu"
        msg["Subject"] = "[Antitrust] Upcoming Event!"
        return personal.assess(msg, body + "\nUnsubscribe")

    def one_to_one(self, body):
        msg = Message()
        msg["From"] = "recruiting@examplefirm.com"
        msg["To"] = "reader1@georgetown.edu"
        msg["Subject"] = "Following up"
        return personal.assess(msg, body)

    def test_the_ropes_and_gray_newsletter_survives(self):
        verdict = self.bulk_org_mail(
            "Hi all, thank you for attending! We are having an event featuring "
            "Ropes & Gray on September 30, 5:30 PM-7:00 PM in McDonough 220. "
            "Of course, there will be food.")
        self.assertFalse(verdict["personal"], verdict)

    def test_the_same_words_one_to_one_still_block(self):
        verdict = self.one_to_one(
            "Thank you for attending our reception. It was a pleasure.")
        self.assertTrue(verdict["personal"], verdict)

    def test_a_closed_event_is_blocked_even_in_bulk(self):
        """Unlike courtesy, "you have registered" names a closed audience."""
        verdict = self.bulk_org_mail(
            "If you are receiving this email, you have registered for the trip.")
        self.assertTrue(verdict["personal"], verdict)
