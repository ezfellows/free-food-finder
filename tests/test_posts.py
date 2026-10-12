"""Checking what students post before it counts as an event.

A post goes on the page the moment it is made -- that is the point of letting
people post -- and stays a post, marked unverified, until something has been
checked. These are the checks.
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"

import posts  # noqa: E402
import store  # noqa: E402

SOON = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
GONE = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")


def post(**kw):
    base = {"id": "p1", "by": "A Student", "title": "Boba and Bar Prep",
            "start": SOON, "place": "McDonough 205",
            "note": "free boba and pad thai", "link": ""}
    base.update(kw)
    return base


class TestLinks(unittest.TestCase):
    def kind(self, url):
        return posts.link_standing(url)["kind"]

    def test_a_school_domain_is_trusted(self):
        for url in ("https://docs.google.com/forms/d/e/x/viewform",
                    "https://georgetown.campusgroups.com/x",
                    "https://forms.gle/abc", "https://linktr.ee/x"):
            self.assertEqual(self.kind(url), "trusted", url)

    def test_a_shortener_hides_its_destination(self):
        """Which is the whole objection to it."""
        self.assertEqual(self.kind("https://bit.ly/3xK9"), "opaque")

    def test_an_unknown_domain_is_not_opened(self):
        self.assertEqual(self.kind("https://random-site.example/x"), "unknown")

    def test_a_non_http_link_is_refused(self):
        for url in ("javascript:alert(1)", "data:text/html,x", "ftp://x/y"):
            self.assertEqual(self.kind(url), "bad", url)


class TestChecking(unittest.TestCase):
    def test_a_good_post_passes(self):
        result = posts.check(post(link="https://forms.gle/abc"))
        self.assertEqual(result["verdict"], "ok", result)

    def test_a_past_event_cannot_be_what_it_says(self):
        result = posts.check(post(start=GONE))
        self.assertEqual(result["verdict"], "bad")
        self.assertTrue(any("already happened" in b for b in result["bad"]))

    def test_main_campus_is_out_of_range(self):
        result = posts.check(post(place="Regents Hall"))
        self.assertEqual(result["verdict"], "bad")

    def test_an_unrecognised_room_is_doubtful_not_fatal(self):
        result = posts.check(post(place="the courtyard behind the thing"))
        self.assertEqual(result["verdict"], "doubtful")

    def test_a_dish_list_counts_as_food(self):
        """A student answering "what is on offer" writes what is on the table.

        "pad thai, fried rice, potstickers" carries none of the promising
        verbs a flyer uses, and used to score as no food at all.
        """
        result = posts.check(post(note="pad thai, fried rice, potsickers, BOBA"))
        self.assertTrue(any("food wording" in g for g in result["good"]), result)


class TestCorroboration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn = store.connect(os.path.join(self.tmp, "p.db"))

    def add(self, title, starts_at):
        store.upsert(self.conn, {"title": title, "starts_at": starts_at,
                                 "source": "broadcast", "tier": "confirmed"})

    def test_one_shared_word_is_evidence_of_nothing(self):
        """"APALSA First GBM" once corroborated "SALDF Book Club: First
        Meeting" on the strength of "first"."""
        self.add("SALDF Book Club: First Meeting", SOON[:11] + "15:00:00")
        found = posts.corroboration(
            self.conn, post(title="APALSA First GBM", start=SOON[:11] + "15:30"))
        self.assertEqual(found, {})

    def test_but_a_real_match_is(self):
        self.add("Boba and Bar Prep with SIPLA", SOON[:11] + "17:00:00")
        found = posts.corroboration(
            self.conn, post(title="Boba and Bar Prep", start=SOON[:11] + "17:00"))
        self.assertTrue(found)


class TestPromotion(unittest.TestCase):
    def test_a_verified_post_becomes_an_ordinary_event(self):
        item = post(link="https://forms.gle/abc")
        event = posts.as_event(item, posts.check(item))
        self.assertEqual(event["source"], "submitted")
        self.assertEqual(event["submitted_by"], "A Student")
        self.assertEqual(event["location"], "McDonough 205")
        self.assertIn(event["tier"], ("confirmed", "likely", "possible"))

    def test_an_untrusted_link_is_not_offered_as_proof(self):
        item = post(link="https://bit.ly/3xK9")
        event = posts.as_event(item, posts.check(item))
        self.assertEqual(event["rsvp_url"], "")


class TestCarrying(unittest.TestCase):
    """What the rebuild keeps on the page once a verdict exists."""

    def test_reviewed_posts_stop_being_carried(self):
        import publish
        page = publish.page([], "1 Jan 2026", {"posts": [
            {"id": "keep", "by": "A", "title": "Unreviewed", "start": SOON},
            {"id": "gone", "by": "B", "title": "Rejected", "start": SOON},
            {"id": "promoted", "by": "C", "title": "Verified", "start": SOON},
        ], "blocked": []})
        path = os.path.join(tempfile.mkdtemp(), "live.html")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(page)
        carried = publish.carried_submissions(path, {
            "gone": {"verdict": "rejected"},
            "promoted": {"verdict": "verified"},
        })
        self.assertEqual([p["id"] for p in carried["posts"]], ["keep"])

    def test_a_verdict_read_back_out_of_the_store_still_works(self):
        """The build asks a recorded verdict for `.get("verdict")`.

        The test above hands it plain dicts, which is why it passed while the
        real path crashed: `submission_verdicts` returned `sqlite3.Row`, and a
        Row indexes like a mapping but has no `.get`. The first rejection ever
        recorded took down the whole build instead of dropping one post.
        """
        import publish
        conn = store.connect(os.path.join(tempfile.mkdtemp(), "v.db"))
        store.note_submission(conn, {"id": "gone", "title": "Rejected"},
                              "rejected", "already happened")
        verdicts = store.submission_verdicts(conn)
        self.assertEqual(verdicts["gone"].get("verdict"), "rejected")

        page = publish.page([], "1 Jan 2026", {"posts": [
            {"id": "keep", "by": "A", "title": "Unreviewed", "start": SOON},
            {"id": "gone", "by": "B", "title": "Rejected", "start": SOON},
        ], "blocked": []})
        path = os.path.join(tempfile.mkdtemp(), "live.html")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(page)
        carried = publish.carried_submissions(path, verdicts)
        self.assertEqual([p["id"] for p in carried["posts"]], ["keep"])


if __name__ == "__main__":
    unittest.main()
