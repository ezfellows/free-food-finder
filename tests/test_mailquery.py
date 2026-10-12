"""./fff mailquery: the Gmail searches, the window, and what gets fetched.

The searches must cover every sender senders.py can accept and nothing that
needs a body to judge; the selection must keep out what senders.py would
refuse from the envelope alone, because the safest private email is one that
was never written to disk.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"

import mailquery  # noqa: E402
import school     # noqa: E402
import senders    # noqa: E402
import store      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = datetime(2026, 10, 9, 9, 0, tzinfo=timezone(timedelta(hours=-4)))


class TestTheWindow(unittest.TestCase):
    def setUp(self):
        self.state = os.path.join(tempfile.mkdtemp(), "mail-pull.json")

    def test_no_record_looks_back_seven_days(self):
        after, previous = mailquery.since(NOW, self.state)
        self.assertIsNone(previous)
        # Seven days, plus the day of overlap Gmail's date-only after: needs.
        self.assertEqual(after, date(2026, 10, 1))

    def test_a_recorded_pull_is_the_start(self):
        mailquery.record_pull(datetime(2026, 10, 7, 11, 5, tzinfo=NOW.tzinfo),
                              self.state)
        after, previous = mailquery.since(NOW, self.state)
        self.assertEqual(previous.date(), date(2026, 10, 7))
        self.assertEqual(after, date(2026, 10, 6))

    def test_a_damaged_record_falls_back_to_seven_days(self):
        with open(self.state, "w") as fh:
            fh.write("{not json")
        self.assertEqual(mailquery.since(NOW, self.state)[0], date(2026, 10, 1))
        with open(self.state, "w") as fh:
            json.dump({"last_pull": "yesterday"}, fh)
        self.assertEqual(mailquery.since(NOW, self.state)[0], date(2026, 10, 1))

    def test_recording_writes_only_the_timestamp(self):
        mailquery.record_pull(NOW, self.state)
        with open(self.state) as fh:
            self.assertEqual(json.load(fh), {"last_pull": "2026-10-09T09:00:00-04:00"})

    def test_the_state_file_is_ignored_by_git(self):
        out = subprocess.run(["git", "check-ignore", "-q", "data/mail-pull.json"],
                             cwd=ROOT)
        self.assertEqual(out.returncode, 0)


class TestTheSearches(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "q.db"))
        store.set_sender_verdict(self.conn, "gulc.sipla@gmail.com", "allow")
        store.set_sender_verdict(self.conn, "spam@example.com", "deny")

    def test_every_rule_is_a_term(self):
        found = mailquery.terms(self.conn, firm_labels={"milbank", "weil"})
        self.assertIn("from:georgetown.edu", found)
        for domain in school.active().BROADCAST_DOMAINS:
            self.assertIn("from:" + domain, found)
        self.assertIn("from:milbank.com", found)
        self.assertIn("from:weil.com", found)
        self.assertIn("from:gulc.sipla@gmail.com", found)
        self.assertNotIn("from:spam@example.com", found)

    def test_the_real_firm_list_is_used_by_default(self):
        found = mailquery.terms(self.conn)
        for label in senders.firm_domains():
            self.assertIn("from:%s.com" % label, found)

    def test_terms_are_not_repeated(self):
        found = mailquery.terms(self.conn)
        self.assertEqual(len(found), len(set(found)))

    def test_every_search_fits_and_excludes_your_own_mail(self):
        found = mailquery.queries(date(2026, 10, 1), mailquery.terms(self.conn))
        self.assertGreater(len(found), 1, "the firm list alone needs splitting")
        for query in found:
            self.assertLessEqual(len(query), mailquery.MAX_QUERY_LEN)
            self.assertTrue(query.startswith("after:2026/10/01 -in:sent "))

    def test_splitting_loses_and_repeats_nothing(self):
        wanted = ["from:sender%03d.com" % n for n in range(120)]
        found = mailquery.queries(date(2026, 10, 1), wanted, max_len=200)
        seen = [t for q in found for t in q.split("{", 1)[1].rstrip("}").split()]
        self.assertEqual(seen, wanted)

    def test_one_short_list_is_one_search(self):
        found = mailquery.queries(date(2026, 10, 1), ["from:a.com", "from:b.com"])
        self.assertEqual(found, ["after:2026/10/01 -in:sent -in:chats "
                                 "{from:a.com from:b.com}"])


class TestWhatGetsFetched(unittest.TestCase):
    """Only what senders.py might accept, and nothing already saved."""

    def setUp(self):
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "s.db"))
        store.set_sender_verdict(self.conn, "gulc.sipla@gmail.com", "allow")
        store.set_sender_verdict(self.conn, "noisy@georgetown.edu", "deny")
        self.drop = tempfile.mkdtemp()

    def check(self, **message):
        message.setdefault("id", "1a00000000000001")
        message.setdefault("subject", "GBM next Thursday")
        return mailquery.verdict_for(message, self.conn, self.drop)

    def test_an_org_address_is_fetched(self):
        self.assertEqual(self.check(sender="cflo@georgetown.edu"), (True, "fetch"))

    def test_a_broadcast_platform_is_fetched_even_with_a_netid(self):
        ok, _ = self.check(sender="zz870@info.georgetown.edu")
        self.assertTrue(ok)

    def test_a_firm_is_fetched(self):
        ok, _ = self.check(sender="recruit@weil.com",
                           subject="You're invited: 1L Reception")
        self.assertTrue(ok)

    def test_a_sender_allowed_by_hand_is_fetched(self):
        ok, _ = self.check(sender="gulc.sipla@gmail.com")
        self.assertTrue(ok)

    def test_your_own_mail_is_not(self):
        ok, why = self.check(sender="reader1@georgetown.edu", labelIds=["SENT"])
        self.assertFalse(ok)
        self.assertEqual(why, "your own message")

    def test_a_classmate_is_not(self):
        ok, why = self.check(sender="Jane Doe <jd1234@georgetown.edu>")
        self.assertFalse(ok)
        self.assertIn("person", why)

    def test_a_reply_from_a_firm_is_not(self):
        """A partner's reply about coffee is the reader's own business."""
        ok, why = self.check(sender="jdoe@orrick.com", subject="RE: Thank You!")
        self.assertFalse(ok)
        self.assertIn("one person", why)

    def test_a_reply_to_a_student_list_still_is(self):
        ok, _ = self.check(sender="cimgmt@georgetown.edu", subject="Re: Dean's update",
                           toRecipients=["lawstudentsall@georgetown.edu"])
        self.assertTrue(ok)

    def test_an_unknown_domain_is_not(self):
        ok, why = self.check(sender="deals@vendor.example")
        self.assertFalse(ok)
        self.assertIn("vetting", why)

    def test_a_denied_sender_is_not(self):
        ok, why = self.check(sender="noisy@georgetown.edu")
        self.assertFalse(ok)
        self.assertIn("deny", why)

    def test_a_message_already_saved_is_not(self):
        open(os.path.join(self.drop, "gm-1a00000000000001.eml"), "w").close()
        ok, why = self.check(sender="cflo@georgetown.edu")
        self.assertFalse(ok)
        self.assertEqual(why, "already in the maildrop")

    def test_a_search_result_is_read_as_the_connector_returns_it(self):
        result = {"threads": [{"id": "t1", "messages": [
            {"id": "m1", "sender": "cflo@georgetown.edu", "labelIds": ["INBOX"],
             "subject": "New CFLO Events!"},
            {"id": "m2", "sender": "reader1@georgetown.edu", "labelIds": ["SENT"],
             "subject": "Re: New CFLO Events!"},
            {"id": "m1", "sender": "cflo@georgetown.edu", "labelIds": ["INBOX"],
             "subject": "New CFLO Events!"}]}]}
        got = mailquery.select(mailquery._flatten(result), self.conn, self.drop)
        self.assertEqual(got["fetch"], ["m1"])
        self.assertEqual(got["skipped"], [{"id": "m2", "reason": "your own message"}])


BROADCAST = (b"From: Office of Student Life <lawstudentlife@info.georgetown.edu>\r\n"
             b"To: reader1@georgetown.edu\r\n"
             b"Subject: OSL Weekly Broadcast - October 9, 2026\r\n"
             b"Message-ID: <osl-1009@xt.local>\r\n"
             b"Content-Type: text/html; charset=utf-8\r\n\r\n"
             b"<p>Unsubscribe</p>\r\n")


class TestTheBroadcastGoesInOnce(unittest.TestCase):
    """Fetched whole, read by ./fff broadcast, and never again as mail."""

    def setUp(self):
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "b.db"))
        self.drop = tempfile.mkdtemp()
        self.path = os.path.join(self.drop, "gm-osl1009.eml")
        with open(self.path, "wb") as fh:
            fh.write(BROADCAST)

    def test_select_asks_for_it_in_raw(self):
        got = mailquery.select([
            {"id": "b1", "sender": "Office of Student Life <lawstudentlife@info.georgetown.edu>",
             "subject": "OSL Weekly Broadcast - October 9, 2026"},
            {"id": "c1", "sender": "cflo@georgetown.edu", "subject": "Next Week with CFLO!"},
        ], self.conn, self.drop)
        self.assertEqual(got["fetch"], ["b1", "c1"])
        self.assertEqual(got["broadcasts"], ["b1"])

    def test_a_broadcast_already_read_is_not_fetched_again(self):
        self.conn.execute("INSERT INTO broadcasts (message_id, subject) VALUES (?, ?)",
                          ("exported@xt.local", "OSL Weekly Broadcast - October 2, 2026"))
        self.conn.commit()
        got = mailquery.select([
            {"id": "b2", "sender": "lawstudentlife@info.georgetown.edu",
             "subject": "OSL Weekly Broadcast - October 2, 2026"}], self.conn, self.drop)
        self.assertEqual(got["fetch"], [])
        self.assertEqual(got["skipped"][0]["reason"], "broadcast already processed")

    def test_an_unread_broadcast_is_listed_once(self):
        self.assertEqual(mailquery.unprocessed_broadcasts([self.path], self.conn),
                         [self.path])
        self.conn.execute("INSERT INTO broadcasts (message_id, subject) VALUES (?, ?)",
                          ("osl-1009@xt.local", "OSL Weekly Broadcast - October 9, 2026"))
        self.conn.commit()
        self.assertEqual(mailquery.unprocessed_broadcasts([self.path], self.conn), [])

    def test_ordinary_mail_is_not_a_broadcast(self):
        other = os.path.join(self.drop, "gm-cflo.eml")
        with open(other, "wb") as fh:
            fh.write(b"From: cflo@georgetown.edu\r\nSubject: Next Week with CFLO!\r\n\r\nhi\r\n")
        self.assertEqual(mailquery.unprocessed_broadcasts([other], self.conn), [])

    def test_the_mail_ingester_leaves_it_to_broadcast(self):
        from ingest import mail
        seen = []
        real = mail.messages_to_events
        mail.messages_to_events = lambda msg, *a, **k: seen.append(msg["Subject"]) or []
        try:
            mail.from_maildrop(self.drop)
        finally:
            mail.messages_to_events = real
        self.assertEqual(seen, [])


class TestSavingWhatTheConnectorReturned(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import make_eml
        self.make_eml = make_eml
        self.out = tempfile.mkdtemp()
        self.old_out, make_eml.OUT = make_eml.OUT, self.out

    def tearDown(self):
        self.make_eml.OUT = self.old_out

    def result(self, data):
        path = os.path.join(tempfile.mkdtemp(), "result.txt")
        with open(path, "w") as fh:
            json.dump(data, fh)
        return path

    def test_a_raw_result_is_kept_byte_for_byte(self):
        import base64
        raw = base64.urlsafe_b64encode(BROADCAST).decode().rstrip("=")
        path = self.make_eml.from_result(self.result({"id": "1a2b", "raw": raw}))
        self.assertEqual(os.path.basename(path), "gm-1a2b.eml")
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), BROADCAST)

    def test_a_plain_text_result_is_built_with_its_recipients(self):
        import email
        path = self.make_eml.from_result(self.result({
            "id": "3c4d", "sender": "cimgmt@georgetown.edu",
            "toRecipients": ["reader1@georgetown.edu"],
            "ccRecipients": ["lawstudentsall@georgetown.edu"],
            "subject": "Dean's update", "date": "2026-10-08T15:00:00Z",
            "plaintextBody": "Lunch will be provided. Unsubscribe"}))
        with open(path, "rb") as fh:
            msg = email.message_from_bytes(fh.read())
        self.assertEqual(msg["Cc"], "lawstudentsall@georgetown.edu")
        self.assertTrue(senders.to_a_listserv(msg))
        self.assertIn("Lunch will be provided", msg.get_payload(decode=True).decode())


class TestTheCommand(unittest.TestCase):
    def run_fff(self, *args, stdin=None):
        env = dict(os.environ, FFF_DB=os.path.join(tempfile.mkdtemp(), "c.db"))
        return subprocess.run([os.path.join(ROOT, "fff"), "mailquery", *args],
                              capture_output=True, text=True, cwd=ROOT, env=env,
                              input=stdin)

    def test_it_prints_one_search_per_line(self):
        out = self.run_fff()
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.strip().splitlines()
        self.assertTrue(lines)
        self.assertTrue(all(line.startswith("after:") for line in lines))
        self.assertIn("searches for mail after", out.stderr)

    def test_select_reads_json_from_stdin(self):
        out = self.run_fff("--select", "-", stdin=json.dumps(
            [{"id": "zz9", "sender": "lawsba@georgetown.edu", "subject": "Bar Review"}]))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["fetch"], ["zz9"])

    def test_it_is_listed_in_the_usage(self):
        with open(os.path.join(ROOT, "fff")) as fh:
            self.assertIn("|mailquery|", fh.read())


if __name__ == "__main__":
    unittest.main()


class TestOriginalsBySize(unittest.TestCase):
    """Whole when the connector can hand it over as a file, plain text when not.

    Measured 10 Oct 2026: a 52 KB message came back as a file, a 30 KB one as
    ~40,000 characters of base64 inline -- which would have to be copied out
    by hand. The line is drawn above that, at RAW_MIN_BYTES.
    """

    def setUp(self):
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "z.db"))
        self.drop = tempfile.mkdtemp()

    def pick(self, *messages):
        return mailquery.select(list(messages), self.conn, self.drop)

    def test_a_big_newsletter_is_fetched_whole(self):
        got = self.pick({"id": "n1", "sender": "cflo@georgetown.edu",
                         "subject": "Next Week with CFLO!", "sizeEstimate": 92672})
        self.assertEqual(got["fetch"], ["n1"])
        self.assertEqual(got["raw"], ["n1"])
        self.assertEqual(got["broadcasts"], [])

    def test_a_small_post_is_read_as_plain_text(self):
        got = self.pick({"id": "s1", "sender": "no-reply@campusgroups.georgetown.edu",
                         "subject": "ACS October GBM", "sizeEstimate": 30133})
        self.assertEqual(got["fetch"], ["s1"])
        self.assertEqual(got["raw"], [])

    def test_the_line_is_the_constant(self):
        got = self.pick(
            {"id": "a", "sender": "cflo@georgetown.edu", "subject": "One",
             "sizeEstimate": mailquery.RAW_MIN_BYTES - 1},
            {"id": "b", "sender": "cflo@georgetown.edu", "subject": "Two",
             "sizeEstimate": mailquery.RAW_MIN_BYTES})
        self.assertEqual(got["raw"], ["b"])

    def test_an_unknown_size_is_read_as_plain_text(self):
        got = self.pick({"id": "u1", "sender": "cflo@georgetown.edu", "subject": "Hi"})
        self.assertEqual(got["raw"], [])

    def test_a_broadcast_is_whole_whatever_its_size(self):
        got = self.pick({"id": "b1", "sender": "lawstudentlife@info.georgetown.edu",
                         "subject": "OSL Weekly Broadcast - October 9, 2026",
                         "sizeEstimate": 1000})
        self.assertEqual(got["raw"], ["b1"])
        self.assertEqual(got["broadcasts"], ["b1"])

    def test_skipped_mail_is_never_raw(self):
        got = self.pick({"id": "p1", "sender": "ab123@georgetown.edu",
                         "subject": "Coffee?", "sizeEstimate": 250000})
        self.assertEqual(got["fetch"], [])
        self.assertEqual(got["raw"], [])
