"""Flyers attached to ordinary mail, with no Vision and no network."""
import os
import struct
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"
import flyers
import ocr
import store
from ingest import attachments


def png(width, height, salt=b""):
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
            + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
            + b"\x00" * 8 + b"x" * 4000 + salt)


def line(text, y, h=0.05):
    return {"text": text, "conf": 0.95, "x": 0.1, "y": y, "w": 0.6, "h": h}


READING = {"ok": True, "ocr_rev": 3, "width": 1200, "height": 1600, "barcodes": [],
           "lines": [line("GEORGETOWN LAW", 0.03, h=0.02),
                     line("PUMPKIN CARVING SOCIAL", 0.10, h=0.11),
                     line("Student Bar Association", 0.24, h=0.045),
                     line("Thursday, November 12 at 12:30pm", 0.34),
                     line("McDonough Atrium", 0.42),
                     line("Free pizza and cider will be provided for all.", 0.52),
                     line("Bring a friend and your best carving ideas.", 0.60)]}

MINIMAL_PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
               b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
               b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 400]>>endobj\n"
               b"trailer<</Root 1 0 R>>\n%%EOF\n")


class Harness(unittest.TestCase):
    NOW = datetime(2026, 11, 3, 9, 0)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.drop = os.path.join(self.tmp, "maildrop")
        os.makedirs(self.drop)
        self._dir, self._read, self._fetch = flyers.FLYER_DIR, ocr.read_images, flyers.fetch
        flyers.FLYER_DIR = os.path.join(self.tmp, "flyers")
        self.conn = store.connect(os.path.join(self.tmp, "t.db"))
        self.read = []

        def fake_read(paths, timeout=None):
            self.read.extend(paths)
            return {p: dict(READING, path=p) for p in paths}

        def forbidden(*args, **kwargs):
            raise AssertionError("an email's remote images must never be fetched")

        ocr.read_images, flyers.fetch = fake_read, forbidden

    def tearDown(self):
        flyers.FLYER_DIR, ocr.read_images, flyers.fetch = self._dir, self._read, self._fetch
        self.conn.close()

    def drop_mail(self, name, files=(), sender="lawsba@georgetown.edu",
                  to="undisclosed-recipients:;", days_ago=1, html=""):
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = to
        msg["Subject"] = "[SBA Weekly Update] Happy November!"
        msg["Message-ID"] = "<%s@example.com>" % name
        msg["Date"] = format_datetime(self.NOW - timedelta(days=days_ago))
        msg.set_content("Hello, Hoyas! This week's flyers are attached to this message.")
        if html:
            msg.add_alternative(html, subtype="html")
        for filename, data, maintype, subtype in files:
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
        with open(os.path.join(self.drop, name + ".eml"), "wb") as fh:
            fh.write(bytes(msg))

    def run_pass(self):
        return attachments.run(self.conn, {"mail": {"maildrop": self.drop}}, today=self.NOW)


class TestAttachedFlyers(Harness):
    FLYER = ("Pumpkin Carving Social Flyer.png", png(1200, 1600, b"pumpkin"), "image", "png")

    def test_an_attached_flyer_is_read_into_a_listing(self):
        self.drop_mail("sba", [self.FLYER])
        stats = self.run_pass()
        self.assertEqual((stats["found"], stats["flyers"], stats["error"]), (1, 1, None))
        row = self.conn.execute("SELECT title, flyer_id FROM events").fetchone()
        self.assertIsNotNone(row)
        self.assertTrue(row["flyer_id"])

    def test_the_next_refresh_reads_nothing_again(self):
        """Every refresh reads the maildrop again. Recording the attachment
        again would move its last sighting, and keep it in the review queue."""
        self.drop_mail("sba", [self.FLYER])
        self.run_pass()
        seen = self.conn.execute("SELECT last_seen, seen_count FROM flyers").fetchone()
        self.read.clear()
        stats = self.run_pass()
        self.assertEqual((stats["found"], self.read), (0, []))
        self.assertEqual(tuple(self.conn.execute("SELECT last_seen, seen_count FROM flyers").fetchone()),
                         tuple(seen))

    def test_old_mail_is_left_alone(self):
        """A flyer in mail older than the review window is for an event that
        is over; nobody should be asked to review it."""
        self.drop_mail("old", [self.FLYER], days_ago=attachments.RECENT_DAYS + 2)
        self.assertEqual(self.run_pass()["found"], 0)
        self.assertEqual(self.read, [])

    def test_mail_the_vetting_refuses_is_not_read(self):
        self.drop_mail("personal", [self.FLYER], sender="wren.halloway@gmail.com",
                       to="reader@example.com")
        self.assertEqual(self.run_pass()["found"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM flyers").fetchone()[0], 0)

    def test_somebody_s_papers_are_not_read(self):
        self.drop_mail("cv", [("Wren Halloway Resume.png", png(1200, 1600, b"cv"), "image", "png")])
        self.assertEqual(self.run_pass()["found"], 0)

    def test_a_remote_image_is_never_fetched(self):
        """The fake fetch raises: only bytes already in the message are read."""
        self.drop_mail("remote", html='<img src="https://cdn.example.com/flyer.png" width="600">')
        self.assertEqual(self.run_pass()["found"], 0)


@unittest.skipUnless(flyers.SIPS, "rendering a PDF needs macOS's sips")
class TestPdfFlyers(unittest.TestCase):
    def test_the_first_page_is_rendered(self):
        self.assertTrue(flyers.is_image(flyers.pdf_to_png(MINIMAL_PDF)))

    def test_anything_else_is_refused(self):
        """An image sips would happily convert is still not a PDF."""
        self.assertEqual(flyers.pdf_to_png(b"<html>not a pdf</html>"), b"")
        page = flyers.pdf_to_png(MINIMAL_PDF)
        self.assertEqual(flyers.pdf_to_png(page), b"")


class TestNotAPdf(unittest.TestCase):
    def test_only_a_pdf_is_a_pdf(self):
        self.assertTrue(flyers.is_pdf(MINIMAL_PDF))
        self.assertFalse(flyers.is_pdf(png(10, 10)))


if __name__ == "__main__":
    unittest.main()
