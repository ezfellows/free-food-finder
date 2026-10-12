"""Who may put an event on a page other people read.

Every case here is a real sender from the mailbox this feeds on, because the
distinction that matters -- an org broadcasting to the school versus a person
writing to one reader -- is not visible in the shape of the envelope. Both
arrive from georgetown.edu, addressed to one recipient.
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

import senders  # noqa: E402


def message(frm, to="reader1@georgetown.edu", bcc=None, subject=""):
    msg = Message()
    msg["From"] = frm
    msg["To"] = to
    if bcc:
        msg["Bcc"] = bcc
    msg["Subject"] = subject
    return msg


class TestOrganisationsAreLetIn(unittest.TestCase):
    def allow(self, frm, to="reader1@georgetown.edu", bcc=None, subject=""):
        verdict = senders.classify(message(frm, to, bcc, subject))
        self.assertTrue(verdict["allow"], f"{frm}: {verdict['reason']}")

    def test_office_role_accounts(self):
        for frm in ("lawstudentlife@info.georgetown.edu",
                    "opics@georgetown.edu", "cflo@georgetown.edu",
                    "cimgmt@georgetown.edu", "lawapalsa@georgetown.edu",
                    "lawgias@georgetown.edu",
                    "georgetowndefenders@georgetown.edu",
                    "opicsleadersinresidence@georgetown.edu"):
            self.allow(frm)

    def test_the_campusgroups_platform(self):
        # Sends each member their own copy, addressed to them alone.
        self.allow("no-reply@campusgroups.georgetown.edu",
                   subject="SALDF: WEDNESDAY BOOK CLUB")

    def test_the_campusgroups_platform_mailbox(self):
        # Membership confirmations arrive from the platform's own domain
        # rather than the school's relay. Subscribing to 27 orgs at once
        # produced 27 of these, every one of them refused until the bare
        # domain was allowed alongside the Georgetown subdomain.
        self.allow("admin@campusgroups.com",
                   subject="Welcome to Federalist Society!")

    def test_a_lookalike_of_the_platform_domain_is_not_the_platform(self):
        # Suffix matching is on a dot boundary, so a domain that merely
        # starts with the real one earns nothing.
        self.assertFalse(
            senders.is_broadcast_domain("admin@campusgroups.com.example.net"))
        self.assertFalse(
            senders.is_broadcast_domain("admin@notcampusgroups.com"))

    def test_a_person_on_the_broadcast_domain_is_still_a_broadcast(self):
        # The Dean's office mails the community as zz870@info.georgetown.edu.
        # Nothing personal can be sent through a mail-merge platform.
        self.allow("zz870@info.georgetown.edu",
                   subject="New Guidelines for Religious Observance")

    def test_anything_addressed_to_the_student_body(self):
        self.allow("lawsba@georgetown.edu", to="undisclosed-recipients:;",
                   bcc="lawstudentsall@georgetown.edu",
                   subject="[SBA Weekly Update] Fall Election")


class TestPeopleAreKeptOut(unittest.TestCase):
    def block(self, frm, to="reader1@georgetown.edu", bcc=None, subject="",
              body=""):
        verdict = senders.classify(message(frm, to, bcc, subject), body)
        self.assertFalse(verdict["allow"], f"{frm} was allowed")
        return verdict["reason"]

    def test_a_classmate(self):
        self.block("zzz65@georgetown.edu", subject="CivProSupp")

    def test_a_small_named_group(self):
        self.block("zz5650@georgetown.edu",
                   to="zzz57@georgetown.edu, reader1@georgetown.edu",
                   subject="ACTION REQUIRED: Law Fellow Conference Sign-Up")

    def test_recruiting_correspondence(self):
        # The whole reason personal networking was dropped from this project.
        for frm, subject in (("rroe@debevoise.com", "RE: Brief Chat Request"),
                             ("jdoe@orrick.com",
                              "Re: Coffee Chat Request from a GULC 1L")):
            self.block(frm, subject=subject)

    def test_an_officer_mailing_from_their_own_account(self):
        # Real org business, but sent by a student from a netID. The address
        # cannot be told apart from that student writing to a friend.
        for frm, subject in (("zz155@georgetown.edu", "APALSA GBM Boba Order Form"),
                             ("zz1311@georgetown.edu", "Resume Submission"),
                             ("zz1538@georgetown.edu", "Symposium Reminder")):
            self.block(frm, subject=subject)

    def test_a_calendar_invite_addressed_to_this_reader(self):
        self.block("zzz357@georgetown.edu",
                   subject="Invitation: APALSA First Look @ Thu Sep 17, 2026")

    def test_an_unknown_outside_address(self):
        self.block("someone@gmail.com", subject="Lunch?")


class TestPrivateContentBeatsTheEnvelope(unittest.TestCase):
    """An org address carrying an account number is still carrying one."""

    def test_financial_notices(self):
        for subject in ("Loan Decrease Confirmation Required",
                        "Federal Loan Disbursement Notification",
                        "Your tuition bill is ready"):
            verdict = senders.classify(
                message("lawreg@info.georgetown.edu", subject=subject))
            self.assertFalse(verdict["allow"], subject)
            self.assertIn("personal information", verdict["reason"])

    def test_an_identifier_in_the_body(self):
        verdict = senders.classify(
            message("lawstudentlife@info.georgetown.edu", subject="Newsletter"),
            "Dear Alex, Banner ID: G01234567, your loan has been credited.")
        self.assertFalse(verdict["allow"])

    def test_but_flyer_boilerplate_is_not_private(self):
        """Every flyer footer says this. Vetoing on it empties the feed."""
        verdict = senders.classify(
            message("lawstudentlife@info.georgetown.edu",
                    subject="OSL Weekly Broadcast"),
            "Accommodation requests related to a disability should be made by "
            "9/15 to zzz90@georgetown.edu. All programs and activities are open "
            "to all Georgetown University Law Center students regardless of "
            "race, national origin, sex, gender identity, or disability.")
        self.assertTrue(verdict["allow"], verdict["reason"])

    def test_advice_that_names_your_transcript_among_application_materials(self):
        """The 8 Oct 2026 Pulse was refused whole for this sentence."""
        verdict = senders.classify(
            message("lawcareerstrategy@georgetown.edu",
                    subject="Private Sector Pulse - October 8, 2026"),
            "Employers use these electronic, survey-based tools to measure "
            "non-legal competencies, evaluating the results alongside your "
            "transcript, resume, and interviews.")
        self.assertTrue(verdict["allow"], verdict["reason"])

    def test_your_own_record_is_still_private(self):
        for body in ("Your transcript is now available to download.",
                     "Please find your transcript attached.",
                     "Your grade for Torts has been posted.",
                     # application materials nearby, but a number in reach
                     "With your GPA of 3.71 and your resume, we recommend "
                     "applying.",
                     "Your class rank, 12 of 140, goes on your resume."):
            verdict = senders.classify(
                message("lawcareerstrategy@georgetown.edu", subject="Update"),
                body)
            self.assertFalse(verdict["allow"], body)
            self.assertIn("personal information", verdict["reason"])

    def test_other_private_content_beside_a_resume_is_not_excused(self):
        verdict = senders.classify(
            message("lawcareerstrategy@georgetown.edu", subject="Update"),
            "Attach your resume and your Banner ID to the form.")
        self.assertFalse(verdict["allow"])


class TestOrgNamesWithoutOrgWords(unittest.TestCase):
    """Plenty of student orgs are not an Association, Society or Council.

    Admission never consults the name -- these all arrive through CampusGroups
    or from a role account -- but attribution used to, by matching org-sounding
    words in the "Email sent by" footer. The address answers it instead: every
    Georgetown netID carries a digit and no role account does.
    """

    def test_an_org_with_no_org_word_in_its_name(self):
        from ingest import mail
        for name, address in (("Hoya Advocates", "advocates@georgetown.edu"),
                              ("OutLaw", "outlaw@georgetown.edu"),
                              ("If/When/How", "iwh@georgetown.edu"),
                              ("Law Students for Climate Accountability",
                               "lsca@georgetown.edu")):
            body = f"Email sent by {name} <{address}>"
            self.assertEqual(mail.relayed_org(body), name)

    def test_a_student_is_still_a_student(self):
        from ingest import mail
        for address in ("zz622@georgetown.edu", "zz559@georgetown.edu"):
            body = f"Email sent by Some Person <{address}>"
            self.assertEqual(mail.relayed_org(body), "")

    def test_the_named_example_is_admitted(self):
        """LSCA reaches the inbox through CampusGroups, as every org does."""
        verdict = senders.classify(
            message("no-reply@campusgroups.georgetown.edu",
                    subject="LSCA GBM Tomorrow!"),
            "Join LSCA for our first general body meeting. Unsubscribe")
        self.assertTrue(verdict["allow"], verdict["reason"])


class TestStandingVerdicts(unittest.TestCase):
    """`./fff senders allow` widens the address rules, and only those."""

    def test_an_allowed_address_gets_in(self):
        msg = message("gulc.sipla@gmail.com", subject="Patent Week")
        self.assertFalse(senders.classify(msg, "Patent Week is coming.")["allow"])
        self.assertTrue(
            senders.classify(msg, "Patent Week is coming.", "allow")["allow"])

    def test_but_never_past_the_fail_safe(self):
        """Allowlisting says "this is an organisation", nothing more."""
        for subject, body in (
                ("Next steps", "Your callback interview is confirmed."),
                ("Invitation: dinner @ Fri", "See you there."),
                ("Offer", "Congratulations, you have been selected.")):
            verdict = senders.classify(
                message("gulc.sipla@gmail.com", subject=subject), body, "allow")
            self.assertFalse(verdict["allow"], f"{subject}: {verdict['reason']}")

    def test_a_denied_address_is_refused(self):
        verdict = senders.classify(
            message("lawsba@georgetown.edu", subject="Weekly"),
            "Greetings, Hoyas! Unsubscribe", "deny")
        self.assertFalse(verdict["allow"])
        self.assertIn("by hand", verdict["reason"])


class TestDefaultDeny(unittest.TestCase):
    def test_no_sender_is_refused(self):
        self.assertFalse(senders.classify(Message())["allow"])

    def test_an_unrecognised_sender_is_refused_not_admitted(self):
        # The direction of the default is the whole design: getting it
        # backwards puts someone's loan statement on a public page. The body
        # carries a bulk marker so the personal-invite fail-safe stands down
        # and this exercises the address rule on its own.
        verdict = senders.classify(message("random@example.org"),
                                   "Events this week. Unsubscribe")
        self.assertFalse(verdict["allow"])
        self.assertIn("not a recognised organisation", verdict["reason"])

    def test_and_the_fail_safe_catches_it_first_when_it_is_not_bulk(self):
        verdict = senders.classify(message("random@example.org"), "Lunch?")
        self.assertFalse(verdict["allow"])
        self.assertIn("one reader", verdict["reason"])


if __name__ == "__main__":
    unittest.main()


class TestLawFirmRecruiting(unittest.TestCase):
    """A firm mailing the 1L class is an organisation broadcasting.

    The hard part is that the same firm.com domain also carries a partner
    agreeing to coffee, and that is the reader's own business. Bulk is what
    separates them, not the domain.
    """

    def blast(self, frm, subject, body="Join us. Unsubscribe"):
        msg = Message()
        msg["From"] = frm
        msg["To"] = frm          # to itself: everyone else is bcc'd
        msg["Subject"] = subject
        return senders.classify(msg, body)

    def test_a_recruiting_mailbox_is_recognised(self):
        for frm in ("Recruit@weil.com", "MilbankRecruiting@milbank.com",
                    "US-Recruiting@cgsh.com",
                    "dcattorneyrecruitment@friedfrank.com"):
            verdict = self.blast(frm, "You're Invited: 1L Reception")
            self.assertTrue(verdict["allow"], f"{frm}: {verdict['reason']}")

    def test_and_so_is_a_person_at_a_known_firm(self):
        verdict = self.blast("Jane.Roe.Doe@friedfrank.com",
                             "Fried Frank - 1L Citywide Reception")
        self.assertTrue(verdict["allow"], verdict["reason"])

    def test_but_never_a_one_to_one_note_from_the_same_domain(self):
        """Weil's blast is admitted; Weil writing to this reader is not."""
        msg = message("Recruit@weil.com", subject="Your interview")
        verdict = senders.classify(
            msg, "Hi Alex, your callback interview is confirmed.")
        self.assertFalse(verdict["allow"])

    def test_nor_a_partner_arranging_coffee(self):
        for frm, subject in (("jdoe@orrick.com", "Re: Coffee Chat Request"),
                             ("rroe@debevoise.com", "Brief Chat Request")):
            verdict = senders.classify(message(frm, subject=subject),
                                       "Hi Alex, happy to chat next week.")
            self.assertFalse(verdict["allow"], frm)

    def test_a_firm_domain_resolves_to_its_name(self):
        self.assertEqual(senders.law_firm("x@milbank.com"), "Milbank")
        self.assertEqual(senders.law_firm("x@bassberry.com"),
                         "Bass, Berry & Sims")
        self.assertEqual(senders.law_firm("x@georgetown.edu"), "")

    def test_a_blast_is_recognised_by_being_addressed_to_itself(self):
        import personal
        msg = Message()
        msg["From"] = "Recruit@weil.com"
        msg["To"] = "Recruit@weil.com"
        self.assertTrue(personal.is_bulk(msg, "Hello."))

    def test_transactional_follow_ups_are_not_events(self):
        """Flo Recruit confirmations are addressed to this reader alone."""
        for subject, body in (
                ("Connect with Goodwin!",
                 "Hi Alex, We have received your email from the Office of "
                 "Career Strategy."),
                ("Thank you for attending Morgan Lewis 1L Recruiting Webinar",
                 "Hi Alex, Thank you for attending our recent webinar."),
                ("Registration Confirmation",
                 "Dear Alex, Thank you for registering to attend.")):
            verdict = senders.classify(
                message("firm@floevents.email", subject=subject), body)
            self.assertFalse(verdict["allow"], subject)
