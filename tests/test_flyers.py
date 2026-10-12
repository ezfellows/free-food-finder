"""Flyer identity, header reading, classification and storage.

Everything here runs without network, Swift or sips. The image headers are
built inline rather than checked in as binary fixtures -- the same approach
`makeicons.py` takes when it writes valid PNGs out of `zlib` and `struct`.
"""
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"
import flyers
import store


def jpeg(width, height, with_app0=True):
    """A JPEG header just real enough to carry dimensions."""
    out = b"\xff\xd8"
    if with_app0:                      # the marker walker must step over this
        payload = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        out += b"\xff\xe0" + struct.pack(">H", len(payload) + 2) + payload
    sof = (b"\x08" + struct.pack(">HH", height, width) + b"\x03"
           + b"\x01\x11\x00\x02\x11\x01\x03\x11\x01")
    out += b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof
    return out + b"\xff\xd9"


def png(width, height):
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
            + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
            + b"\x00" * 8)


def gif(width, height):
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\xf7\x00\x00" * 2


class TestImageSize(unittest.TestCase):
    def test_jpeg(self):
        self.assertEqual(flyers.image_size(jpeg(563, 750)), (563, 750))

    def test_jpeg_without_app0(self):
        self.assertEqual(flyers.image_size(jpeg(120, 60, with_app0=False)),
                         (120, 60))

    def test_png(self):
        self.assertEqual(flyers.image_size(png(1080, 1350)), (1080, 1350))

    def test_gif(self):
        self.assertEqual(flyers.image_size(gif(1, 1)), (1, 1))

    def test_garbage_returns_none(self):
        for bad in (b"", b"\x00", b"not an image at all", None,
                    b"\xff\xd8" + b"\xff" * 30):
            self.assertIsNone(flyers.image_size(bad), repr(bad)[:30])


class TestMagicBytes(unittest.TestCase):
    def test_accepts_real_formats(self):
        for data in (jpeg(10, 10), png(10, 10), gif(10, 10)):
            self.assertTrue(flyers.is_image(data))

    def test_rejects_svg(self):
        """SVG is XML with a scripting surface; never embed it in a page."""
        self.assertFalse(flyers.is_image(
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>x()</script></svg>'))

    def test_rejects_html_error_page(self):
        """A CDN will serve an error page as image/jpeg without blinking."""
        self.assertFalse(flyers.is_image(b"<!DOCTYPE html><html>404</html>"))

    def test_rejects_empty(self):
        self.assertFalse(flyers.is_image(b""))
        self.assertFalse(flyers.is_image(None))


class TestUrlHygiene(unittest.TestCase):
    def test_rejects_dangerous_schemes(self):
        for bad in ("javascript:alert(1)", "data:text/html,<script>x</script>",
                    "file:///etc/passwd", "//evil.example.com/x",
                    "ftp://host/x", "", None, "not a url", "https://"):
            self.assertEqual(flyers.valid_url(bad), "", repr(bad))

    def test_accepts_http_and_https(self):
        for good in ("https://forms.gle/abc", "http://example.org/rsvp?id=2"):
            self.assertEqual(flyers.valid_url(good), good)

    def test_rejects_absurd_length(self):
        self.assertEqual(flyers.valid_url("https://x.example/" + "a" * 3000), "")


class TestIdentityAndPaths(unittest.TestCase):
    def test_id_is_deterministic_and_content_addressed(self):
        a, b = jpeg(563, 750), jpeg(563, 751)
        self.assertEqual(flyers.flyer_id(a), flyers.flyer_id(a))
        self.assertNotEqual(flyers.flyer_id(a), flyers.flyer_id(b))
        self.assertEqual(len(flyers.flyer_id(a)), 16)

    def test_paths_shard_on_first_two_chars(self):
        p = flyers.paths("ab12cd34ef567890")
        self.assertTrue(p["dir"].endswith("/ab"))
        self.assertTrue(p["original"].endswith("ab12cd34ef567890.jpg"))
        self.assertTrue(p["web"].endswith(".web.jpg"))
        self.assertTrue(p["card"].endswith(".card.jpg"))


class TestPreFetchGate(unittest.TestCase):
    def gate(self, url, declared=None):
        return flyers.looks_worth_fetching({"url": url, "declared_w": declared})

    def test_skips_named_chrome(self):
        for name in ("logo.png", "footer-divider.gif", "spacer.gif",
                     "social-instagram.png", "icon_bullet.png"):
            self.assertFalse(self.gate(f"https://cdn.example/{name}"), name)

    def test_skips_narrow_images(self):
        self.assertFalse(self.gate("https://cdn.example/a.jpg", declared=120))

    def test_accepts_body_width_images(self):
        self.assertTrue(self.gate("https://cdn.example/a.jpg", declared=563))

    def test_unknown_width_is_allowed_through(self):
        """OCR is free, so an unknown width gets the benefit of the doubt."""
        self.assertTrue(self.gate("https://cdn.example/a.jpg"))

    def test_rejects_bad_url(self):
        self.assertFalse(self.gate("javascript:alert(1)", declared=563))


class TestClassify(unittest.TestCase):
    def reading(self, lines, words_each=6):
        return {"ok": True,
                "lines": [{"text": " ".join(["word"] * words_each)}
                          for _ in range(lines)]}

    def test_text_volume_decides(self):
        meta = {"byte_size": 90_000, "width": 563, "height": 750}
        self.assertEqual(flyers.classify(meta, self.reading(8)), "flyer")
        self.assertEqual(flyers.classify(meta, self.reading(2)), "logo")

    def test_tiny_files_are_chrome(self):
        self.assertEqual(flyers.classify({"byte_size": 500}), "logo")

    def test_recurring_images_become_chrome(self):
        """An image in three broadcasts is furniture, whatever it looks like."""
        meta = {"byte_size": 90_000, "width": 563, "height": 750,
                "seen_weeks": 3}
        self.assertEqual(flyers.classify(meta, self.reading(10)), "logo")

    def test_extreme_aspect_is_a_banner(self):
        meta = {"byte_size": 40_000, "width": 1200, "height": 100}
        self.assertEqual(flyers.classify(meta), "banner")

    def test_declared_width_is_provisional_before_ocr(self):
        meta = {"byte_size": 90_000, "width": 563, "height": 750,
                "declared_w": 563}
        self.assertEqual(flyers.classify(meta), "flyer")

    def test_unknown_without_evidence(self):
        self.assertEqual(flyers.classify({"byte_size": 90_000}), "unknown")

    def test_handles_empty_input(self):
        self.assertEqual(flyers.classify(None), "unknown")


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "t.db"))

    def tearDown(self):
        self.conn.close()

    def meta(self, fid="ab12cd34ef567890"):
        return {"id": fid, "url": "https://cdn.example/a.jpg",
                "byte_size": 90_000, "width": 563, "height": 750,
                "kind": "flyer", "week_of": "2026-09-11"}

    def test_record_then_seen(self):
        self.assertEqual(flyers.record(self.conn, self.meta()), "added")
        self.assertEqual(flyers.record(self.conn, self.meta()), "seen")
        row = flyers.get(self.conn, "ab12cd34ef567890")
        self.assertEqual(row["seen_count"], 2)

    def test_url_memory_counts_weeks(self):
        url = "https://cdn.example/a.jpg"
        self.assertIsNone(flyers.known_url(self.conn, url))
        self.assertEqual(flyers.note_url(self.conn, url, "ab12cd34ef567890"), 1)
        self.assertEqual(flyers.note_url(self.conn, url, "ab12cd34ef567890"), 2)
        self.assertEqual(flyers.known_url(self.conn, url), "ab12cd34ef567890")

    def test_save_reading_extracts_qr_and_confidence(self):
        flyers.record(self.conn, self.meta())
        flyers.save_reading(self.conn, "ab12cd34ef567890", {
            "ok": True, "ocr_rev": 3,
            "lines": [{"text": "FREE PIZZA", "conf": 0.9},
                      {"text": "Hotung 2000", "conf": 0.8}],
            "barcodes": [{"payload": "https://forms.gle/abc"}]})
        row = flyers.get(self.conn, "ab12cd34ef567890")
        self.assertIn("FREE PIZZA", row["ocr_text"])
        self.assertEqual(row["ocr_conf"], 85)
        self.assertEqual(row["rsvp_url"], "https://forms.gle/abc")
        self.assertEqual(row["read_by"], "vision")

    def test_a_dangerous_qr_payload_never_becomes_an_rsvp_url(self):
        flyers.record(self.conn, self.meta())
        flyers.save_reading(self.conn, "ab12cd34ef567890", {
            "ok": True, "lines": [{"text": "x", "conf": 0.9}],
            "barcodes": [{"payload": "javascript:alert(1)"}]})
        row = flyers.get(self.conn, "ab12cd34ef567890")
        self.assertEqual(row["rsvp_url"], "")
        self.assertIn("javascript", row["qr_payload"])   # kept, never linked


class TestPrune(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._real_dir = flyers.FLYER_DIR
        flyers.FLYER_DIR = os.path.join(self.tmp, "flyers")
        self.conn = store.connect(os.path.join(self.tmp, "t.db"))

    def tearDown(self):
        flyers.FLYER_DIR = self._real_dir
        self.conn.close()

    def make(self, fid, last_seen):
        p = flyers.paths(fid)
        os.makedirs(p["dir"], exist_ok=True)
        for key in ("original", "web", "card"):
            with open(p[key], "wb") as fh:
                fh.write(b"x" * 100)
        flyers.record(self.conn, {"id": fid, "byte_size": 100})
        self.conn.execute("UPDATE flyers SET last_seen = ? WHERE id = ?",
                          (last_seen, fid))
        self.conn.commit()
        return p

    def test_old_originals_go_but_cards_stay(self):
        p = self.make("aa11111111111111", "2000-01-01T00:00:00+00:00")
        stats = flyers.prune(self.conn, originals_days=30, cards_days=100000)
        self.assertFalse(os.path.exists(p["original"]))
        self.assertFalse(os.path.exists(p["web"]))
        self.assertTrue(os.path.exists(p["card"]))
        self.assertGreater(stats["originals"], 0)

    def test_recent_flyers_untouched(self):
        from datetime import datetime, timezone
        p = self.make("bb11111111111111",
                      datetime.now(timezone.utc).isoformat())
        flyers.prune(self.conn)
        self.assertTrue(os.path.exists(p["original"]))

    def test_row_dropped_only_when_every_file_is_gone(self):
        self.make("cc11111111111111", "2000-01-01T00:00:00+00:00")
        flyers.prune(self.conn, originals_days=30, cards_days=30)
        self.assertIsNone(flyers.get(self.conn, "cc11111111111111"))

    def test_prune_touches_nothing_it_did_not_create(self):
        stranger = os.path.join(self.tmp, "keep-me.txt")
        with open(stranger, "w") as fh:
            fh.write("hello")
        self.make("dd11111111111111", "2000-01-01T00:00:00+00:00")
        flyers.prune(self.conn, originals_days=30, cards_days=30)
        self.assertTrue(os.path.exists(stranger))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestWebPDimensions(unittest.TestCase):
    """WebP is what a screenshot or a Canva export arrives as now.

    The LSDAD flyer came in that way and measured 0x0, so `classify` had
    nothing to judge it on and called it unknown until OCR rescued it. A real
    flyer that small-looking could be discarded as furniture.
    """

    def vp8x(self, width, height):
        body = (b"VP8X" + b"\x00" * 4 + b"\x00" * 4
                + (width - 1).to_bytes(3, "little")
                + (height - 1).to_bytes(3, "little"))
        return b"RIFF" + b"\x00" * 4 + b"WEBP" + body

    def test_an_extended_webp_reports_its_size(self):
        self.assertEqual(flyers.image_size(self.vp8x(1429, 2000)), (1429, 2000))

    def test_a_lossless_webp(self):
        bits = ((800 - 1) | ((600 - 1) << 14)).to_bytes(4, "little")
        data = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"VP8L" + b"\x00" * 4 + b"\x00" + bits
        self.assertEqual(flyers.image_size(data), (800, 600))

    def test_the_other_formats_still_work(self):
        png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 4 + b"IHDR"
               + (640).to_bytes(4, "big") + (480).to_bytes(4, "big"))
        self.assertEqual(flyers.image_size(png), (640, 480))

    def test_junk_is_not_a_size(self):
        self.assertIsNone(flyers.image_size(b"RIFF" + b"\x00" * 4 + b"WEBPxxxx"))
