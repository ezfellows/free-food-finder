"""The OSL broadcast ingester, with no network and no Vision.

`samples/09-osl-broadcast.eml` is synthetic and trimmed, but it reproduces the
structural trap of the real thing: the events are only in remote images, the
`text/plain` alternative is boilerplate with no food words in it, and every
anchor is wrapped in an opaque `?qs=` tracking redirect.
"""
import email
import os
import struct
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"
import flyers
import ocr
import store
import caption as captions
from ingest import broadcast

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "samples",
                      "09-osl-broadcast.eml")


def png(width, height, salt=b""):
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
            + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
            + b"\x00" * 8 + b"x" * 4000 + salt)


def line(text, y, h=0.05, conf=0.95, x=0.1):
    return {"text": text, "conf": conf, "x": x, "y": y, "w": 0.6, "h": h}


FLYER_READING = {
    "ok": True, "ocr_rev": 3, "width": 1200, "height": 1600, "barcodes": [
        {"payload": "https://forms.gle/realdestination", "symbology": "VNBarcodeSymbologyQR",
         "x": 0.6, "y": 0.8, "w": 0.2, "h": 0.15}],
    "lines": [
        line("GEORGETOWN LAW", 0.03, h=0.02),
        line("PIZZA AND POLICY", 0.10, h=0.11),
        line("Environmental Law Society", 0.24, h=0.045),
        line("Wednesday, September 16 at 12:00pm", 0.34),
        line("Hotung 2000", 0.42),
        line("Free pizza will be provided for all attendees.", 0.52),
        line("Scan the code to RSVP in advance.", 0.60),
    ],
}

LOGO_READING = {"ok": True, "ocr_rev": 3, "width": 120, "height": 60,
                "barcodes": [], "lines": [line("Office of Student Life", 0.4)]}


class Harness(unittest.TestCase):
    """Replace both I/O boundaries: the CDN and the Vision helper."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._flyer_dir = flyers.FLYER_DIR
        flyers.FLYER_DIR = os.path.join(self.tmp, "flyers")
        self.conn = store.connect(os.path.join(self.tmp, "t.db"))

        self._fetch, self._read, self._sleep = flyers.fetch, ocr.read_images, broadcast.time.sleep
        self.fetched = []
        broadcast.time.sleep = lambda *_: None

        def fake_fetch(url, timeout=20):
            self.fetched.append(url)
            if "spacer" in url:
                return None                       # too small to be an image
            if "logo" in url:
                return png(120, 60, salt=b"logo")
            return png(1200, 1600, salt=url.encode()[-12:])

        flyers.fetch = fake_fetch
        ocr.read_images = self.fake_read

        # Resolving a tracking link must never happen during a run.
        self._resolve = broadcast.resolve_tracking
        broadcast.resolve_tracking = self._forbidden

    def tearDown(self):
        flyers.FLYER_DIR = self._flyer_dir
        flyers.fetch, ocr.read_images = self._fetch, self._read
        broadcast.resolve_tracking, broadcast.time.sleep = self._resolve, self._sleep
        self.conn.close()

    def _forbidden(self, *args, **kwargs):
        raise AssertionError("resolve_tracking must not fire during a run")

    def fake_read(self, paths, timeout=None):
        out = {}
        for path in paths:
            row = self.conn.execute(
                "SELECT width FROM flyers WHERE id = ?",
                (os.path.basename(path).split(".")[0],)).fetchone()
            small = row and (row["width"] or 0) < 400
            out[path] = dict(LOGO_READING if small else FLYER_READING, path=path)
        return out

    def run_sample(self):
        return broadcast.run(conn=self.conn, path=SAMPLE)


class TestDetection(unittest.TestCase):
    def test_recognises_the_broadcast(self):
        with open(SAMPLE, "rb") as fh:
            msg = email.message_from_bytes(fh.read())
        self.assertTrue(broadcast.is_broadcast(msg))

    def test_does_not_claim_the_other_samples(self):
        folder = os.path.dirname(SAMPLE)
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".eml") or name.startswith("09-"):
                continue
            with open(os.path.join(folder, name), "rb") as fh:
                msg = email.message_from_bytes(fh.read())
            self.assertFalse(broadcast.is_broadcast(msg), name)

    def test_plain_text_really_is_useless_here(self):
        """The premise of the whole module: no food words outside the images."""
        from ingest.mail import body_text
        with open(SAMPLE, "rb") as fh:
            msg = email.message_from_bytes(fh.read())
        from score import score_event
        self.assertFalse(score_event("x", body_text(msg), "")["has_food"])

    def test_blocks_carry_declared_width(self):
        with open(SAMPLE, "rb") as fh:
            msg = email.message_from_bytes(fh.read())
        blocks = broadcast.flyer_blocks(broadcast.html_part(msg))
        self.assertEqual([b["declared_w"] for b in blocks], [120, 563, 1, 562])

    def test_date_header_anchors_relative_dates(self):
        with open(SAMPLE, "rb") as fh:
            msg = email.message_from_bytes(fh.read())
        self.assertEqual(broadcast.received_at(msg).date(),
                         datetime(2026, 9, 11).date())


class TestRun(Harness):
    def test_produces_events_from_flyers_only(self):
        stats = self.run_sample()
        self.assertIsNone(stats["error"])
        titles = [r[0] for r in self.conn.execute(
            "SELECT title FROM events WHERE source = 'broadcast'")]
        self.assertTrue(titles)
        for title in titles:
            self.assertNotIn("Office of Student Life", title)

    def test_narrow_and_tiny_images_are_never_fetched(self):
        self.run_sample()
        self.assertFalse([u for u in self.fetched if "logo" in u or "spacer" in u])

    def test_logo_is_classified_not_evented(self):
        self.run_sample()
        kinds = dict(self.conn.execute(
            "SELECT kind, COUNT(*) FROM flyers GROUP BY kind"))
        self.assertEqual(kinds.get("flyer"), 2)

    def test_event_carries_flyer_and_rsvp(self):
        self.run_sample()
        row = self.conn.execute(
            "SELECT * FROM events WHERE source = 'broadcast' LIMIT 1").fetchone()
        self.assertTrue(row["flyer_id"])
        self.assertEqual(row["rsvp_url"], "https://forms.gle/realdestination")

    def test_food_verdict_comes_from_the_scorer(self):
        self.run_sample()
        row = self.conn.execute(
            "SELECT * FROM events WHERE source = 'broadcast' LIMIT 1").fetchone()
        self.assertEqual(row["tier"], "confirmed")
        self.assertIn("free food stated", row["reason"])

    def test_title_is_the_headline_not_the_letterhead(self):
        self.run_sample()
        titles = [r[0] for r in self.conn.execute(
            "SELECT title FROM events WHERE source = 'broadcast'")]
        self.assertIn("pizza and policy", titles[0].lower())
        self.assertNotIn("georgetown law", titles[0].lower())

    def test_the_caption_grounds_the_time_and_room(self):
        """The email text is typed; the flyer is pixels. The text wins."""
        self.run_sample()
        row = self.conn.execute(
            "SELECT * FROM events WHERE source = 'broadcast' LIMIT 1").fetchone()
        self.assertEqual(row["starts_at"][:16], "2026-09-16T12:00")
        self.assertEqual(row["ends_at"][:16], "2026-09-16T13:00")
        self.assertIn("Hotung 2000", row["location"])

    def test_the_caption_title_is_preferred_over_the_ocr_casing(self):
        self.run_sample()
        title = self.conn.execute(
            "SELECT title FROM events WHERE source = 'broadcast'").fetchone()[0]
        self.assertEqual(title, "Pizza and Policy")

    def test_the_caption_is_kept_on_the_flyer(self):
        self.run_sample()
        caption = self.conn.execute(
            "SELECT caption FROM flyers WHERE kind = 'flyer' "
            "AND caption != '' LIMIT 1").fetchone()
        self.assertIsNotNone(caption)
        self.assertIn("Hotung 2000", caption[0])

    def test_a_contradicted_time_goes_to_review(self):
        """A flyer and its caption disagreeing is not something to guess at."""
        said = {"starts_at": "2026-09-16T17:30:00", "ends_at": "",
                "room": "", "date_confidence": 90}
        clash = captions.compare(said, "2026-09-16T18:00:00", "Hotung 2000")
        self.assertTrue(clash)
        self.assertIn("17:30", clash[0])
        self.assertIn("18:00", clash[0])

    def test_second_run_refetches_nothing(self):
        self.run_sample()
        first = len(self.fetched)
        stats = self.run_sample()
        self.assertEqual(len(self.fetched), first)      # url memory held
        self.assertEqual(stats["flyers"], 0)

    def test_second_run_bumps_seen_count(self):
        self.run_sample()
        self.run_sample()
        counts = [r[0] for r in self.conn.execute(
            "SELECT seen_count FROM flyers")]
        self.assertTrue(all(c >= 2 for c in counts), counts)

    def test_broadcast_is_recorded(self):
        self.run_sample()
        row = self.conn.execute("SELECT * FROM broadcasts").fetchone()
        self.assertEqual(row["message_id"], "osl-fixture-0001@info.georgetown.edu")

    def test_missing_file_is_an_error_not_a_crash(self):
        stats = broadcast.run(conn=self.conn, path="/nope/missing.eml")
        self.assertIsNotNone(stats["error"])


class TestReviewQueue(Harness):
    def test_unreadable_flyer_is_queued_not_dropped(self):
        ocr.read_images = lambda paths, timeout=None: {
            p: {"ok": False, "path": p, "lines": [], "barcodes": [],
                "error": "vision unavailable"} for p in paths}
        stats = self.run_sample()
        self.assertGreater(stats["queued"], 0)
        self.assertTrue(broadcast.pending(self.conn))

    def test_pending_payload_is_small_and_has_no_email_in_it(self):
        ocr.read_images = lambda paths, timeout=None: {
            p: {"ok": False, "path": p, "lines": [], "barcodes": [],
                "error": "x"} for p in paths}
        self.run_sample()
        for row in broadcast.pending(self.conn):
            self.assertEqual(set(row), {"id", "image", "reason", "week_of",
                                        "ocr_hint", "qr_payload"})
            self.assertLess(len(str(row)), 1200)

    def test_food_words_without_a_verdict_are_queued(self):
        """A garbled read scores 'unlikely' for the same reason a genuinely
        food-free flyer does. That is a read failure, not a verdict."""
        reading = {"ok": True, "ocr_rev": 3, "barcodes": [], "lines": [
            line("SOMETHING", 0.1, h=0.1),
            line("free piZZa at the thing", 0.3),
            line("come along everyone", 0.4),
            line("more words here to pass the length gate", 0.5)]}
        why = broadcast.needs_review(
            reading, {"tier": "unlikely"}, 90, "SOMETHING", 0, "")
        self.assertEqual(why, "food words but no food verdict")

    def test_settled_flyer_is_not_queued(self):
        why = broadcast.needs_review(
            FLYER_READING, {"tier": "confirmed"}, 100, "PIZZA AND POLICY",
            1, "https://forms.gle/x")
        self.assertIsNone(why)

    def test_qr_detected_but_not_decoded_is_queued(self):
        why = broadcast.needs_review(
            FLYER_READING, {"tier": "confirmed"}, 100, "T", 1, "")
        self.assertEqual(why, "qr detected but not decoded")


class TestApplyReadings(Harness):
    def flyer(self):
        self.run_sample()
        return self.conn.execute("SELECT id FROM flyers WHERE kind='flyer' "
                                 "LIMIT 1").fetchone()[0]

    def test_a_correction_replaces_the_event_it_corrects(self):
        """One flyer, one event -- even when the fix changes the title.

        The fingerprint is built from the title, so a corrected title is a
        new fingerprint. Without superseding, the page shows the mistake and
        its correction side by side.
        """
        fid = self.flyer()
        first = [{"id": fid, "title": "FIRST TREET",
                  "starts_at": "2026-09-16T15:30:00",
                  "body_text": "open house, snacks provided"}]
        broadcast.apply_readings(self.conn, first)
        self.assertEqual(self.events_for(fid), ["FIRST TREET"])

        fixed = [{"id": fid, "title": "Student Health Services Open House",
                  "starts_at": "2026-09-16T15:30:00",
                  "body_text": "open house, snacks provided"}]
        stats = broadcast.apply_readings(self.conn, fixed)
        self.assertEqual(self.events_for(fid),
                         ["Student Health Services Open House"])
        self.assertEqual(stats["superseded"], 1)

    def test_superseding_relinks_the_flyer(self):
        """A flyer left pointing at a deleted event stops being re-groundable."""
        fid = self.flyer()
        broadcast.apply_readings(self.conn, [
            {"id": fid, "title": "INTERVIEWS",
             "starts_at": "2026-09-22T12:00:00", "body_text": "free lunch"}])
        link = self.conn.execute(
            "SELECT event_id FROM flyers WHERE id = ?", (fid,)).fetchone()[0]
        alive = self.conn.execute(
            "SELECT 1 FROM events WHERE id = ?", (link,)).fetchone()
        self.assertIsNotNone(alive, "flyer points at an event that is gone")

    def test_superseding_clears_what_pointed_at_the_old_event(self):
        fid = self.flyer()
        broadcast.apply_readings(self.conn, [
            {"id": fid, "title": "FIRST TREET",
             "starts_at": "2026-09-16T15:30:00", "body_text": "snacks"}])
        stale = self.conn.execute(
            "SELECT id FROM events WHERE flyer_id = ?", (fid,)).fetchone()[0]
        broadcast.apply_readings(self.conn, [
            {"id": fid, "title": "Student Health Services Open House",
             "starts_at": "2026-09-16T15:30:00", "body_text": "snacks"}])
        left = self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE id = ?", (stale,)).fetchone()[0]
        self.assertEqual(left, 0)

    def test_a_flyer_may_carry_more_than_one_event(self):
        """A firm's "patent week" is one page advertising two talks."""
        fid = self.flyer()
        stats = broadcast.apply_readings(self.conn, [
            {"id": fid, "title": "Patent Prosecution vs Patent Litigation",
             "starts_at": "2026-09-29T12:00:00", "body_text": "free lunch"},
            {"id": fid, "title": "Anatomy of a Patent Suit",
             "starts_at": "2026-09-30T15:30:00", "body_text": "free bubble tea"},
        ])
        self.assertEqual(len(self.events_for(fid)), 2)

    def events_for(self, fid):
        return [r[0] for r in self.conn.execute(
            "SELECT title FROM events WHERE flyer_id = ? ORDER BY rowid", (fid,))]

    def test_a_correction_keeps_the_fingerprint_in_step(self):
        """The fingerprint is how a calendar entry finds this row and merges.

        Correcting a title without recomputing it leaves the row unfindable
        by the thing the fingerprint is derived from, so the same event from
        another feed lands beside it instead of merging into it.
        """
        fid = self.flyer()
        broadcast.apply_readings(self.conn, [
            {"id": fid, "title": "INTERVIEWS",
             "starts_at": "2026-09-22T12:00:00", "body_text": "free lunch"}])
        self.conn.execute(
            "UPDATE flyers SET caption = ? WHERE id = ?",
            ("CFLO Speed Interviews, September 22, 2026, 12:00-1:00 pm, "
             "McDonough 201, contact cflo@georgetown.edu", fid))
        self.conn.commit()
        broadcast.reground(self.conn, datetime(2026, 9, 11, 13, 20))
        row = self.conn.execute(
            "SELECT title, starts_at, fingerprint FROM events "
            "WHERE flyer_id = ?", (fid,)).fetchone()
        self.assertEqual(row["title"], "CFLO Speed Interviews")
        self.assertEqual(row["fingerprint"],
                         store.fingerprint(row["title"], row["starts_at"]))

    def test_a_human_ruling_outranks_the_caption(self):
        """The APALSA First Look case.

        The email caption said 5:30, the flyer said 6:00, and the reader who
        went said 6:00. The caption normally wins, and would have won back
        every morning -- a plain UPDATE is not a correction, it is a change
        waiting to be undone.
        """
        fid = self.flyer()
        broadcast.apply_readings(self.conn, [
            {"id": fid, "title": "APALSA First Look",
             "starts_at": "2026-09-17T18:00:00",
             "ends_at": "2026-09-17T19:00:00", "body_text": "drinks and food"}])
        self.conn.execute(
            "UPDATE flyers SET caption = ? WHERE id = ?",
            ("APALSA First Look, September 17 2026, 5:30-7:00PM, "
             "McDonough Atrium", fid))
        self.conn.commit()
        ref = datetime(2026, 9, 11, 13, 20)

        # Without a ruling the caption takes it, which is the documented rule.
        broadcast.reground(self.conn, ref)
        row = self.conn.execute(
            "SELECT id, starts_at FROM events WHERE flyer_id = ?",
            (fid,)).fetchone()
        self.assertEqual(row["starts_at"][11:16], "17:30")

        store.record_correction(self.conn, row["id"], "starts_at",
                                "2026-09-17T18:00:00", "reader was there")
        for _ in range(3):
            broadcast.reground(self.conn, ref)
        after = self.conn.execute(
            "SELECT starts_at, fingerprint, title FROM events WHERE id = ?",
            (row["id"],)).fetchone()
        self.assertEqual(after["starts_at"][11:16], "18:00")
        self.assertEqual(after["fingerprint"],
                         store.fingerprint(after["title"], after["starts_at"]))

    def test_the_caption_end_time_does_not_outlive_the_start_it_came_with(self):
        """One caption, one clock reading: the pair stands or falls together.

        Keeping the caption's end after its start has been overruled staples
        two different readings into one event.
        """
        fid = self.flyer()
        broadcast.apply_readings(self.conn, [
            {"id": fid, "title": "Evening Panel",
             "starts_at": "2026-09-17T18:00:00",
             "ends_at": "2026-09-17T19:00:00", "body_text": "dinner served"}])
        self.conn.execute(
            "UPDATE flyers SET caption = ? WHERE id = ?",
            ("Evening Panel, September 17 2026, 5:30-6:30PM, McDonough 201",
             fid))
        self.conn.commit()
        ref = datetime(2026, 9, 11, 13, 20)
        row = self.conn.execute(
            "SELECT id FROM events WHERE flyer_id = ?", (fid,)).fetchone()
        store.record_correction(self.conn, row["id"], "starts_at",
                                "2026-09-17T18:00:00", "reader was there")
        broadcast.reground(self.conn, ref)
        after = self.conn.execute(
            "SELECT starts_at, ends_at FROM events WHERE id = ?",
            (row["id"],)).fetchone()
        self.assertEqual(after["starts_at"][11:16], "18:00")
        # 18:30 would be the caption's hour pinned to nothing it measured.
        self.assertEqual(after["ends_at"][11:16], "19:00")

    def test_regrounding_twice_changes_nothing_the_second_time(self):
        fid = self.flyer()
        broadcast.apply_readings(self.conn, [
            {"id": fid, "title": "INTERVIEWS",
             "starts_at": "2026-09-22T12:00:00", "body_text": "free lunch"}])
        self.conn.execute(
            "UPDATE flyers SET caption = ? WHERE id = ?",
            ("CFLO Speed Interviews, September 22, 2026, 12:00-1:00 pm, "
             "McDonough 201", fid))
        self.conn.commit()
        ref = datetime(2026, 9, 11, 13, 20)
        broadcast.reground(self.conn, ref)
        again = broadcast.reground(self.conn, ref)
        self.assertEqual(again["changed"], 0)

    def test_applies_a_transcription(self):
        fid = self.flyer()
        stats = broadcast.apply_readings(self.conn, [{
            "id": fid, "title": "Reviewed Event",
            "starts_at": "2026-09-20T17:00:00",
            "location": "Gewirz 12", "org": "Some Society",
            "food_text": "Free tacos will be served.",
            "body_text": "Come along.",
            "rsvp_url": "https://forms.gle/reviewed"}])
        self.assertEqual(stats["found"], 1)
        row = self.conn.execute(
            "SELECT * FROM events WHERE title = 'Reviewed Event'").fetchone()
        self.assertEqual(row["tier"], "confirmed")     # scored, not asserted
        self.assertEqual(row["rsvp_url"], "https://forms.gle/reviewed")

    def test_dangerous_rsvp_url_is_rejected(self):
        """The bad scheme must not survive. Falling back to the flyer's own
        QR link is fine -- that one was scheme-checked when it was decoded."""
        fid = self.flyer()
        broadcast.apply_readings(self.conn, [{
            "id": fid, "title": "Sneaky", "starts_at": "2026-09-20T17:00:00",
            "body_text": "x", "rsvp_url": "javascript:alert(1)"}])
        row = self.conn.execute(
            "SELECT * FROM events WHERE title = 'Sneaky'").fetchone()
        self.assertNotIn("javascript", (row["rsvp_url"] or "").lower())
        self.assertTrue((row["rsvp_url"] or "").startswith("https://"))

    def test_dangerous_url_never_reaches_the_flyer_record(self):
        fid = self.flyer()
        broadcast.apply_readings(self.conn, [{
            "id": fid, "title": "Sneaky2", "starts_at": "2026-09-20T17:00:00",
            "body_text": "x", "rsvp_url": "javascript:alert(1)"}])
        import json as _json
        stored = _json.loads(self.conn.execute(
            "SELECT reading FROM flyers WHERE id = ?", (fid,)).fetchone()[0])
        self.assertEqual(stored.get("rsvp_url", ""), "")

    def test_absurd_title_is_capped(self):
        fid = self.flyer()
        broadcast.apply_readings(self.conn, [{
            "id": fid, "title": "T" * 9000, "starts_at": "2026-09-20T17:00:00",
            "body_text": "x"}])
        longest = self.conn.execute(
            "SELECT MAX(LENGTH(title)) FROM events").fetchone()[0]
        self.assertLessEqual(longest, 140)

    def test_unknown_keys_are_dropped_not_stored(self):
        fid = self.flyer()
        broadcast.apply_readings(self.conn, [{
            "id": fid, "title": "Keyed", "starts_at": "2026-09-20T17:00:00",
            "body_text": "x", "tier": "confirmed", "has_food": True,
            "confidence": 100}])
        import json as _json
        stored = _json.loads(self.conn.execute(
            "SELECT reading FROM flyers WHERE id = ?", (fid,)).fetchone()[0])
        for forbidden in ("tier", "has_food", "confidence"):
            self.assertNotIn(forbidden, stored)

    def test_unreadable_produces_no_event(self):
        fid = self.flyer()
        before = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        broadcast.apply_readings(self.conn, [{"id": fid, "title": "x",
                                              "unreadable": True}])
        after = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(before, after)

    def test_unknown_flyer_is_skipped(self):
        stats = broadcast.apply_readings(self.conn, [{"id": "0" * 16,
                                                      "title": "Ghost"}])
        self.assertEqual(stats["skipped"], 1)

    def test_garbage_input_does_not_raise(self):
        broadcast.apply_readings(self.conn, [None, "nonsense", 42, {}])


class TestTrackingLinks(unittest.TestCase):
    def test_unwraps_a_query_parameter_wrapper(self):
        self.assertEqual(
            broadcast.unwrap("https://mail.example.net/redir?url="
                             "https%3A%2F%2Fforms.gle%2Fwrapped123"),
            "https://forms.gle/wrapped123")

    def test_opaque_georgetown_token_cannot_be_unwrapped(self):
        """Expected: ?qs= is a token, not an encoded destination."""
        self.assertEqual(
            broadcast.unwrap("https://click.info.georgetown.edu/?qs=OPAQUE"), "")

    def test_never_returns_a_dangerous_scheme(self):
        self.assertEqual(
            broadcast.unwrap("https://x.example/r?url=javascript%3Aalert(1)"), "")

    def test_rejects_junk(self):
        for bad in ("", None, "not a url", "javascript:alert(1)"):
            self.assertEqual(broadcast.unwrap(bad), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestAttachedFlyers(unittest.TestCase):
    """A student org attaches its flyer instead of hosting it.

    `flyer_blocks` reads <img src> tags, so five flyers from Georgetown
    Defenders were invisible: there was nothing to fetch.
    """

    def message(self, *files):
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"] = "georgetowndefenders@georgetown.edu"
        msg["Subject"] = "Weekly"
        msg.set_content("See attached.")
        for name, data in files:
            msg.add_attachment(data, maintype="image", subtype="png",
                               filename=name)
        return msg

    def test_attachments_become_blocks(self):
        blocks = broadcast.attached_flyers(
            self.message(("Gun Violence Flyer.png", b"x" * 5000)), "m1")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["data"], b"x" * 5000)
        self.assertIn("attachment:m1", blocks[0]["url"])

    def test_a_spacer_is_skipped(self):
        self.assertEqual(broadcast.attached_flyers(
            self.message(("pixel.png", b"x" * 10))), [])

    def test_the_filename_names_the_event(self):
        blocks = broadcast.attached_flyers(
            self.message(("Community Solutions to Reducing Gun Violence "
                          "Flyer.png", b"x" * 5000)))
        self.assertEqual(blocks[0]["alt"],
                         "Community Solutions to Reducing Gun Violence")

    def test_the_word_flyer_is_not_part_of_the_name(self):
        """"World of Choices Flyer" was published beside the real event."""
        self.assertEqual(
            broadcast._filename_caption("World of Choices Flyer.jpg"),
            "World of Choices")

    def test_a_filename_that_names_nothing_gives_nothing(self):
        for name in ("flyer.png", "IMG_2043.jpg", "final.jpeg"):
            self.assertEqual(broadcast._filename_caption(name), "", name)


class TestATranscribedRoomReachesTheScorer(Harness):
    """The reviewer's room used to be applied after scoring, so the verdict
    was computed against a room guessed from the text instead."""

    # Borrowed rather than inherited: subclassing TestApplyReadings would run
    # all of its tests a second time under this class's name.
    flyer = TestApplyReadings.flyer

    def test_the_room_read_off_the_flyer_is_scored(self):
        fid = self.flyer()
        broadcast.apply_readings(self.conn, [{
            "id": fid, "title": "NOLA Info Meeting",
            "starts_at": "2026-09-24T17:45:00", "location": "McDonough 208",
            "food_text": "FREE FOOD!",
            "body_text": "Info meeting for the spring break trip. Zoom or in person."}])
        row = self.conn.execute(
            "SELECT tier, location FROM events WHERE title = 'NOLA Info Meeting'"
        ).fetchone()
        self.assertEqual(row["location"], "McDonough 208")
        self.assertEqual(row["tier"], "confirmed")
