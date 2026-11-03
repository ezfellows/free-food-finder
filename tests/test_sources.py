"""Senders and lists you add are read; what they say is still checked.

`./fff sources` widens who may be read, for the mail a school's profile cannot
know about: a club's Gmail account, a professor's talk series, a listserv.
These hold both halves of that promise. A post to a list you added is read
whoever wrote it, and credited to the list, never to the classmate who sent
it. And an ID number, a callback interview or a calendar invitation keeps a
message off the page however trusted its source.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from email.message import EmailMessage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"

import mailquery              # noqa: E402
import school                 # noqa: E402
import senders                # noqa: E402
import sources                # noqa: E402
import store                  # noqa: E402
from ingest import mail       # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST = "lawevents@googlegroups.com"


def post(frm="Alex Doe <alex.doe@example.edu>", to=LIST, subject="Pizza and Policy",
         body="Pizza and Policy, Thursday October 15 at 12:30 pm in Room 204. "
              "Free pizza will be served.", **headers):
    msg = EmailMessage()
    msg["From"] = frm
    if to:
        msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = "Mon, 12 Oct 2026 09:00:00 -0400"
    msg["Message-ID"] = "<%s@example.edu>" % abs(hash((frm, to, subject, body)))
    for name, value in headers.items():
        msg[name.replace("_", "-")] = value
    msg.set_content(body)
    return msg


def verdict(msg, conn=None):
    address = senders.address_of(msg["From"])
    return senders.classify(msg, msg.get_content(), sources.standing(conn, address))


class Isolated(unittest.TestCase):
    """No school, so nothing is admitted unless a source says so."""

    lists = {LIST: "Law Events"}
    senders_added = ("chessclub@gmail.com",)

    def setUp(self):
        for context in (school.using(None),
                        sources.using(senders=self.senders_added, lists=self.lists)):
            context.__enter__()
            self.addCleanup(context.__exit__, None, None, None)


class TestAListYouAdded(Isolated):
    def test_a_classmates_post_is_read(self):
        found = verdict(post())
        self.assertTrue(found["allow"], found["reason"])
        self.assertIn("a list you added", found["reason"])

    def test_and_credited_to_the_list_not_the_classmate(self):
        self.assertEqual(mail.org_of(post()), "Law Events")

    def test_a_list_that_rewrites_from_to_itself(self):
        msg = post(frm=f"Alex Doe via Law Events <{LIST}>", to="")
        self.assertTrue(verdict(msg)["allow"])
        self.assertEqual(mail.org_of(msg), "Law Events")

    def test_a_list_that_only_says_so_in_its_headers(self):
        msg = post(to="undisclosed-recipients:;",
                   List_Id="Law Events <lawevents.googlegroups.com>")
        self.assertTrue(verdict(msg)["allow"])
        msg = post(to="undisclosed-recipients:;",
                   List_Post=f"<mailto:{LIST}>")
        self.assertTrue(verdict(msg)["allow"])

    def test_a_reply_on_the_list_is_still_a_post_to_everyone(self):
        self.assertTrue(verdict(post(subject="Re: Pizza and Policy"))["allow"])

    def test_an_org_posting_there_keeps_its_own_name(self):
        """Once a profile can tell an office from a person, that is."""
        msg = post(frm="Student Bar Association <lawsba@georgetown.edu>")
        with school.using("georgetown_law"):
            self.assertTrue(verdict(msg)["allow"])
            self.assertEqual(mail.org_of(msg), "Student Bar Association")
        # With no profile, any sender might be a person, so the list is
        # credited: never a name that could be a classmate's.
        self.assertEqual(mail.org_of(msg), "Law Events")

    def test_without_the_list_the_same_post_is_refused(self):
        with sources.using():
            self.assertFalse(verdict(post())["allow"])


class TestWhatAMessageSaysIsStillChecked(Isolated):
    def test_an_id_number(self):
        msg = post(body="Reminder: your Banner ID is needed to register.")
        self.assertFalse(verdict(msg)["allow"])

    def test_a_message_about_one_reader(self):
        msg = post(body="Alex, your callback interview is confirmed for Friday.")
        self.assertFalse(verdict(msg)["allow"])

    def test_a_calendar_invitation(self):
        msg = post(subject="Invitation: Study group @ Thu Oct 15, 2026")
        self.assertFalse(verdict(msg)["allow"])

    def test_a_sender_refused_by_hand_stays_refused_on_a_list(self):
        conn = store.connect(os.path.join(tempfile.mkdtemp(), "s.db"))
        self.addCleanup(conn.close)
        store.set_sender_verdict(conn, "alex.doe@example.edu", "deny")
        self.assertFalse(verdict(post(), conn)["allow"])


class TestASenderYouAdded(Isolated):
    def test_is_read(self):
        msg = post(frm="Chess Club <chessclub@gmail.com>", to="reader1@georgetown.edu")
        found = verdict(msg)
        self.assertTrue(found["allow"], found["reason"])
        self.assertIn("a sender you added", found["reason"])

    def test_and_one_allowed_earlier_in_the_register_still_is(self):
        conn = store.connect(os.path.join(tempfile.mkdtemp(), "s.db"))
        self.addCleanup(conn.close)
        store.set_sender_verdict(conn, "chair@example.org", "allow")
        msg = post(frm="chair@example.org", to="reader1@georgetown.edu")
        self.assertTrue(verdict(msg, conn)["allow"])

    def test_but_not_a_reply_to_you(self):
        msg = post(frm="chessclub@gmail.com", to="reader1@georgetown.edu",
                   subject="Re: your question")
        self.assertFalse(verdict(msg)["allow"])


class TestAlongsideAProfile(unittest.TestCase):
    def test_a_school_list_still_refuses_a_person_but_your_list_does_not(self):
        """The profile's lists keep their rule; one you chose is yours to trust."""
        msg = post(frm="zz101@georgetown.edu", to="lawstudentsall@georgetown.edu")
        with school.using("georgetown_law"), sources.using():
            self.assertFalse(verdict(msg)["allow"])
        with school.using("georgetown_law"), sources.using(lists={LIST: ""}):
            self.assertTrue(verdict(post(frm="zz101@georgetown.edu"))["allow"])
            self.assertEqual(mail.org_of(post(frm="zz101@georgetown.edu")), "lawevents")


class TestSearching(Isolated):
    def test_every_way_a_list_post_can_be_found(self):
        conn = store.connect(os.path.join(tempfile.mkdtemp(), "q.db"))
        self.addCleanup(conn.close)
        terms = mailquery.terms(conn)
        for term in (f"from:{LIST}", f"to:{LIST}", f"cc:{LIST}", f"list:{LIST}",
                     "from:chessclub@gmail.com"):
            self.assertIn(term, terms)

    def test_the_envelope_screen_lets_a_list_post_through(self):
        found = mailquery.select([{"id": "1", "sender": "Alex Doe <alex.doe@example.edu>",
                                   "toRecipients": [LIST], "subject": "Pizza and Policy"}],
                                 maildrop=tempfile.mkdtemp())
        self.assertEqual(found["fetch"], ["1"])
        with sources.using():
            found = mailquery.select([{"id": "1", "sender": "alex.doe@example.edu",
                                       "toRecipients": [LIST], "subject": "Pizza"}],
                                     maildrop=tempfile.mkdtemp())
        self.assertEqual(found["fetch"], [])


class TestFromInboxToEvent(Isolated):
    def test_the_event_carries_the_list_and_no_one_s_name(self):
        drop = tempfile.mkdtemp()
        with open(os.path.join(drop, "gm-1a2b3c4d5e6f7a8b.eml"), "wb") as fh:
            fh.write(post().as_bytes())
        conn = store.connect(os.path.join(tempfile.mkdtemp(), "e.db"))
        self.addCleanup(conn.close)
        stats = mail.run(conn, {"mail": {"maildrop": drop}})
        self.assertIsNone(stats["error"])
        rows = [dict(r) for r in conn.execute("SELECT title, org FROM events")]
        self.assertEqual([r["org"] for r in rows], ["Law Events"])
        self.assertNotIn("Alex", json.dumps(rows))
        log = conn.execute("SELECT verdict, reason FROM mail_log").fetchone()
        self.assertEqual(log["verdict"], "admitted")


class TestTheSettings(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(self.path, "w") as fh:
            json.dump({"school": "georgetown_law", "page": {"title": "Mine"}}, fh)
        before = os.environ["FFF_CONFIG"]
        os.environ["FFF_CONFIG"] = self.path
        self.addCleanup(os.environ.__setitem__, "FFF_CONFIG", before)

    def saved(self):
        with open(self.path) as fh:
            return json.load(fh)

    def test_adding_keeps_every_other_setting(self):
        sources.add_list("Events@Lists.Example.edu", "Events")
        sources.add_sender("club@gmail.com")
        sources.add_sender("club@gmail.com")
        data = self.saved()
        self.assertEqual(data["school"], "georgetown_law")
        self.assertEqual(data["page"], {"title": "Mine"})
        self.assertEqual(data["sources"], {"senders": ["club@gmail.com"],
                                           "lists": {"events@lists.example.edu": "Events"}})

    def test_removing(self):
        sources.add_sender("club@gmail.com")
        self.assertEqual(sources.remove("club@gmail.com"), ["sender"])
        self.assertEqual(sources.remove("club@gmail.com"), [])
        self.assertEqual(self.saved()["sources"]["senders"], [])

    def test_only_an_address_is_accepted(self):
        for bad in ("not-an-address", "@gmail.com", "a b@example.edu", ""):
            with self.assertRaises(ValueError, msg=bad):
                sources.add_sender(bad)

    def test_a_plain_list_of_list_addresses_is_understood(self):
        with open(self.path, "w") as fh:
            json.dump({"sources": {"lists": ["events@lists.example.edu"]}}, fh)
        self.assertEqual(sources.load()["lists"], {"events@lists.example.edu": ""})

    def test_the_command(self):
        env = dict(os.environ, FFF_DB=os.path.join(tempfile.mkdtemp(), "c.db"))
        run = lambda *args: subprocess.run([os.path.join(ROOT, "fff"), "sources", *args],
                                           cwd=ROOT, env=env, capture_output=True, text=True)
        out = run("add-list", LIST, "Law Events")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("credited to 'Law Events'", out.stdout)
        self.assertIn("Law Events", run().stdout)
        self.assertEqual(run("add", "nonsense").returncode, 2)
        self.assertIn("removed", run("remove", LIST).stdout)
        self.assertEqual(self.saved()["sources"]["lists"], {})


class TestCandidatesToAdd(unittest.TestCase):
    """`./fff sources` offers the senders refused for who they are, only.

    It listed every law firm the vetting had let in as refused: a firm is let
    in "from" the firm's own name, and the test for a refusal looked for
    "from a". And it offered senders refused for what they wrote, where adding
    the sender is no answer.
    """

    def setUp(self):
        context = sources.using()
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "c.db"))
        self.addCleanup(self.conn.close)
        for address, reason in (
                ("recruiting@halepartners.example.com", "from Hale & Partners"),
                ("events@lawschool.example.edu", "from an organisation address"),
                ("news@clubs.example.org", senders.NOT_RECOGNISED),
                ("registrar@lawschool.example.edu",
                 "written to one reader ('you have registered for')"),
                ("recruit@otherfirm.example.com",
                 "Other Firm LLP writing to one reader, not the class")):
            store.note_sender(self.conn, address, bulk=True, reason=reason)

    def test_a_firm_let_in_is_not_counted_as_refused(self):
        refused = {r["address"] for r in store.known_senders(self.conn, only_refused=True)}
        self.assertEqual(refused, {"news@clubs.example.org",
                                   "registrar@lawschool.example.edu",
                                   "recruit@otherfirm.example.com"})

    def test_only_a_sender_refused_for_its_address_is_offered(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sources.show(self.conn)
        offered = out.getvalue().split("Refused lately", 1)[1]
        self.assertIn("news@clubs.example.org", offered)
        for kept_out in ("halepartners", "registrar@", "otherfirm"):
            self.assertNotIn(kept_out, offered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
