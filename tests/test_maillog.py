"""Originals read like Gmail's plain text, and the log says what each email became.

From 10 Oct 2026 the routine saves every email of 60 KB or more whole, so an
HTML-only original -- CampusGroups sends nothing else -- reaches the splitter
instead of Gmail's rendering of it. These hold the two to the same result, on
the shapes that broke the first converter: an alt attribute with a ">" in it,
and a cell whose heading, date and room must stay one entry.
"""

import email
import email.utils
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from email.message import EmailMessage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"

import maillog                 # noqa: E402
import store                   # noqa: E402
from ingest import mail        # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ITEM = ('<tr><td class="cgWidgetTextContent"><h2><span>{title}</span></h2>\n'
        '<div><b>🗓️ {when}</b><br />\n<b>📍{where}</b></div>\n<br />\n'
        '<span>{blurb}</span></td></tr>')

NEWSLETTER = (
    '<html><head><style>p {{margin:0}}</style></head><body><center>'
    '<img alt="" src="https://georgetown.campusgroups.com/read/abc.png">'
    '<table><tbody>{items}</tbody></table>'
    # CampusGroups' footer, as ACS's 9 Oct original has it: the org above
    # "Georgetown University", which is how the org gets its credit.
    '<div><strong><span>Military Law Society</span></strong>'
    '<p style="margin:0">Georgetown University </p></div>'
    '<div>Email sent by Some Student &lt;ab123@georgetown.edu&gt; to '
    'reader1@georgetown.edu</div><a href="https://georgetown.campusgroups.com/'
    'unsubscribe?uid=1">Unsubscribe</a></center></body></html>')


def newsletter(*items):
    return NEWSLETTER.format(items="".join(ITEM.format(**i) for i in items))


class TestHtmlReadsLikeGmail(unittest.TestCase):
    def test_attributes_never_leak_into_the_text(self):
        text = mail.html_to_text('<p><img alt="Upcoming > 14 OCT" src="x.png" '
                                 'style="max-width:1024px">Big Law Panel</p>')
        self.assertNotIn("src=", text)
        self.assertNotIn("max-width", text)
        self.assertIn("Big Law Panel", text)

    def test_a_cell_keeps_its_title_with_its_date(self):
        text = mail.html_to_text(newsletter(
            {"title": "Big Law Panel", "when": "October 14th | 5-7:30 PM",
             "where": "McDonough 411", "blurb": "Dinner will be provided."}))
        line = next(l for l in text.splitlines() if "Big Law Panel" in l)
        self.assertIn("## Big Law Panel", line)
        self.assertIn("October 14th", line)
        self.assertIn("McDonough 411", line)

    def test_links_read_as_gmail_writes_them(self):
        text = mail.html_to_text('<a href="https://forms.gle/abc">RSVP here</a>')
        self.assertEqual(text, "RSVP here[](https://forms.gle/abc)")

    def test_paragraphs_outside_tables_stay_apart(self):
        self.assertEqual(mail.html_to_text("<p>One &amp; two</p>\n<p>Three</p>"),
                         "One & two\n\nThree")

    def test_an_html_only_newsletter_splits_into_its_events(self):
        """MAVRC's gave none until a cell stopped breaking on every paragraph."""
        msg = EmailMessage()
        msg["From"] = "Veteran Services <no-reply@campusgroups.georgetown.edu>"
        msg["To"] = "reader1@georgetown.edu"
        msg["Subject"] = "MAVRC Newsletter: Employer Chats, and More!"
        msg["Date"] = "Fri, 09 Oct 2026 11:45:03 -0400"
        msg.set_content(newsletter(
            {"title": "Army Ten-Miler Watch Party", "when": "October 11th | 8AM-11:30AM",
             "where": "Key Bridge", "blurb": "Coffee and breakfast food will be provided!"},
            {"title": "Reserve Excellence Celebration", "when": "October 16th | 6PM",
             "where": "1775 Liberty Drive", "blurb": "A reception with dinner."}),
            subtype="html")
        got = {(e["title"][:30], e["starts_at"][:13]) for e in mail.messages_to_events(msg)}
        self.assertIn(("Army Ten-Miler Watch Party", "2026-10-11T08"), {(t[:26], s) for t, s in got})
        self.assertIn(("Reserve Excellence Celebration", "2026-10-16T18"), got)


def write(directory, name, msg):
    with open(os.path.join(directory, name), "wb") as fh:
        fh.write(msg.as_bytes())


class TestTheMailLog(unittest.TestCase):
    """One row per email read: how it was saved, the verdict, what it fed."""

    @classmethod
    def setUpClass(cls):
        cls.db = os.path.join(tempfile.mkdtemp(), "log.db")
        cls.conn = store.connect(cls.db)
        cls.drop = tempfile.mkdtemp()
        sent = email.utils.format_datetime(datetime(2026, 10, 8, 12, 0).astimezone())

        plain = EmailMessage()                       # as tools/make_eml.py builds it
        plain["From"] = "cflo@georgetown.edu"
        plain["To"] = "reader1@georgetown.edu"
        plain["Subject"] = "Sidley 1L Reception"
        plain["Date"] = sent
        plain.set_content(
            "Join CFLO and Sidley attorneys for a 1L reception on Wednesday, "
            "October 14, 2026 from 5:30 pm to 7:30 pm at Sidley DC, 1501 K Street "
            "NW. Food and drinks will be served. Unsubscribe")
        write(cls.drop, "gm-1a11000000000001.eml", plain)

        original = EmailMessage()                    # as it left the mail server
        original["Received"] = "from mail4.campusgroups.com by mx.google.com"
        original["From"] = "Some Student <no-reply@campusgroups.georgetown.edu>"
        original["To"] = "reader1@georgetown.edu"
        original["Subject"] = "MLS Big Law Panel"
        original["Date"] = sent
        original["Message-ID"] = "<mls@campusgroups.com>"
        original.set_content(newsletter(
            {"title": "Big Law Panel", "when": "Wednesday, October 14th, 5 - 7:30 PM",
             "where": "McDonough 411",
             "blurb": "Network with attorneys from five firms. Dinner from CAVA "
                      "will be provided."}), subtype="html")
        write(cls.drop, "gm-1a11000000000002.eml", original)

        person = EmailMessage()
        person["From"] = "A Classmate <ab123@georgetown.edu>"
        person["To"] = "reader1@georgetown.edu"
        person["Subject"] = "Coffee?"
        person["Date"] = sent
        person.set_content("Want to grab coffee on Tuesday, October 13 at 3pm?")
        write(cls.drop, "gm-1a11000000000003.eml", person)

        broadcast = EmailMessage()
        broadcast["From"] = "Office of Student Life <lawstudentlife@info.georgetown.edu>"
        broadcast["To"] = "reader1@georgetown.edu"
        broadcast["Subject"] = "OSL Weekly Broadcast - October 9, 2026"
        broadcast["Date"] = sent
        broadcast["Message-ID"] = "<osl1009@xt.local>"
        broadcast.set_content("<p>flyers</p>", subtype="html")
        write(cls.drop, "osl-2026-10-09.eml", broadcast)

        cls.stats = mail.run(cls.conn, {"mail": {"maildrop": cls.drop}})
        cls.log = {r["mail_key"]: dict(r) for r in
                   cls.conn.execute("SELECT * FROM mail_log")}

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_every_file_is_logged_once(self):
        self.assertIsNone(self.stats["error"])
        self.assertEqual(set(self.log), {"gm-1a11000000000001", "gm-1a11000000000002",
                                         "gm-1a11000000000003", "osl1009@xt.local"})

    def test_how_each_was_saved(self):
        self.assertEqual(self.log["gm-1a11000000000001"]["saved_as"], "plain text")
        self.assertEqual(self.log["gm-1a11000000000002"]["saved_as"], "original")

    def test_admitted_mail_names_the_events_it_fed(self):
        import json
        for key in ("gm-1a11000000000001", "gm-1a11000000000002"):
            row = self.log[key]
            self.assertEqual(row["verdict"], "admitted", key)
            ids = json.loads(row["events"])
            self.assertTrue(ids, key)
            for eid in ids:
                self.assertIsNotNone(self.conn.execute(
                    "SELECT 1 FROM events WHERE id = ?", (eid,)).fetchone(), key)

    def test_refused_mail_says_why(self):
        row = self.log["gm-1a11000000000003"]
        self.assertEqual(row["verdict"], "refused")
        # The personal fail-safe runs before the address rules, so it speaks.
        self.assertEqual(row["reason"], "one person writing to one reader, "
                                        "with nothing marking it a broadcast")
        self.assertEqual(row["events"], "[]")

    def test_a_broadcast_is_left_to_broadcast_and_said_so(self):
        row = self.log["osl1009@xt.local"]
        self.assertEqual(row["verdict"], "broadcast")
        self.assertIn("not read yet", row["reason"])

    def test_the_org_is_credited_not_the_student_who_sent_it(self):
        self.assertEqual(self.log["gm-1a11000000000002"]["org"], "Military Law Society")

    def test_a_second_refresh_keeps_one_row_each(self):
        mail.run(self.conn, {"mail": {"maildrop": self.drop}})
        count = self.conn.execute("SELECT COUNT(*) FROM mail_log").fetchone()[0]
        self.assertEqual(count, 4)

    def test_the_view_filters(self):
        everything = maillog.rows(self.conn, days=100000)
        self.assertEqual(len(everything), 4)
        refused = maillog.rows(self.conn, days=100000, only="refused")
        self.assertEqual([r["subject"] for r in refused], ["Coffee?"])
        self.assertEqual(len(maillog.rows(self.conn, days=100000, words="sidley")), 1)
        page = maillog.render(self.conn, everything)
        self.assertIn("4 emails", page)
        self.assertIn("refused: one person writing to one reader", page)

    def test_the_command(self):
        env = dict(os.environ, FFF_DB=self.db)
        out = subprocess.run([os.path.join(ROOT, "fff"), "maillog", "100000", "--refused"],
                             cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Coffee?", out.stdout)
        self.assertNotIn("Sidley 1L Reception", out.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
