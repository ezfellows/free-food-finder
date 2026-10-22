"""One event seen twice: a group chat's post and the email or flyer listing.

The posts here are worded the way org chats word them; every name, room and
time is invented.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"
import docket                                        # noqa: E402
import merge                                         # noqa: E402
import store                                         # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def event(**kw):
    base = {"title": "", "starts_at": "2099-03-10T17:00:00", "org": "",
            "location": "", "source": "mail", "description": "",
            "tier": "unlikely", "confidence": 0, "has_food": False,
            "meals": [], "signals": []}
    base.update(kw)
    return base


class TestTheSameOrganisation(unittest.TestCase):
    def test_by_its_initials(self):
        for short, full in (("ACS", "American Constitution Society"),
                            ("WLA", "Women's Legal Alliance"),
                            ("SIPLA", "Student Intellectual Property Law Association")):
            self.assertTrue(merge.same_org(short, full), short)
            self.assertTrue(merge.same_org(full, short), short)

    def test_by_name(self):
        self.assertTrue(merge.same_org("Statutes & Ladders", "statutes ladders"))
        self.assertFalse(merge.same_org("ACS", "Health Law Society"))
        self.assertFalse(merge.same_org("", "ACS"))


class TestTheSameEvent(unittest.TestCase):
    def test_what_a_chat_calls_it(self):
        """"Game night" in the chat; the full name in the email."""
        post = event(title="Game Night", org="Statutes & Ladders", source="groupme")
        mail = event(title="SIPLA x Statutes and Ladders Game Night")
        self.assertTrue(merge.same_event(post, mail))

    def test_initials_in_the_title(self):
        post = event(title="ACS Halloween Movie Night", starts_at="2099-03-10T19:30:00")
        flyer = event(title="Movie Night", org="American Constitution Society",
                      starts_at="2099-03-10T19:30:00", source="broadcast")
        self.assertIn("same organisation", merge.same_event(post, flyer))

    def test_the_same_room_at_the_same_time(self):
        a = event(title="Spooky Screening", location="McDonough 205",
                  starts_at="2099-03-10T19:30:00")
        b = event(title="Halloween Social", location="McDonough 205",
                  starts_at="2099-03-10T19:30:00")
        self.assertIn("same room", merge.same_event(a, b))

    def test_a_post_with_no_time(self):
        post = event(title="WLA Public Interest Panel", all_day=True,
                     starts_at="2099-03-12T00:00:00")
        mail = event(title="Women's Legal Alliance Public Interest Panel",
                     starts_at="2099-03-12T18:00:00")
        self.assertTrue(merge.same_event(post, mail))

    def test_a_rounded_time(self):
        a = event(title="Antitrust Careers Panel", starts_at="2099-03-10T17:00:00")
        b = event(title="Antitrust Careers Panel and Dinner",
                  starts_at="2099-03-10T17:30:00")
        self.assertIn("30 min apart", merge.same_event(a, b))

    def test_not_the_same(self):
        cases = [
            # The same org, hours apart: two events.
            (event(title="ACS GBM", org="ACS", starts_at="2099-03-10T12:00:00"),
             event(title="ACS Happy Hour", org="ACS", starts_at="2099-03-10T19:00:00")),
            # Two lunches at noon with different firms.
            (event(title="Lunch and Learn with Bracewell", starts_at="2099-03-10T12:00:00"),
             event(title="Lunch with White & Case", starts_at="2099-03-10T12:00:00")),
            # Two orgs' movie nights.
            (event(title="Francophone Society Movie Night", org="Francophone Society"),
             event(title="ACS Movie Night", org="American Constitution Society")),
            # Another day.
            (event(title="Game Night", starts_at="2099-03-10T17:00:00"),
             event(title="Game Night", starts_at="2099-03-17T17:00:00")),
            # One org's two events, half an hour apart, sharing no words.
            (event(title="ACS Board Meeting", org="ACS", starts_at="2099-03-10T12:00:00"),
             event(title="Constitution Trivia", org="American Constitution Society",
                   starts_at="2099-03-10T12:30:00")),
        ]
        for a, b in cases:
            self.assertEqual(merge.same_event(a, b), [], (a["title"], b["title"]))


class TestFolding(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "merge.db"))
        self.addCleanup(self.conn.close)
        store.upsert(self.conn, event(
            title="SIPLA x Statutes and Ladders Game Night",
            description="A night of board games."))
        self.post = event(
            title="Game Night", org="Statutes & Ladders", source="groupme",
            location="500 First St NW, Room 750", ends_at="2099-03-10T19:00:00",
            description="Free dinner from a local restaurant.",
            tier="confirmed", confidence=95, has_food=True, meals=["dinner"])

    def only(self):
        rows = self.conn.execute("SELECT * FROM events").fetchall()
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_the_post_fills_what_the_email_lacked(self):
        verdict, _, reasons = merge.add(self.conn, self.post)
        self.assertEqual(verdict, "merged")
        self.assertTrue(reasons)
        row = self.only()
        self.assertEqual(row["title"], "SIPLA x Statutes and Ladders Game Night")
        self.assertEqual(row["starts_at"], "2099-03-10T17:00:00")
        self.assertEqual(row["location"], "500 First St NW, Room 750")
        self.assertEqual(row["ends_at"], "2099-03-10T19:00:00")
        self.assertEqual(row["source"], "groupme,mail")

    def test_the_post_s_food_counts(self):
        merge.add(self.conn, self.post)
        row = self.only()
        self.assertTrue(row["has_food"])
        self.assertEqual(row["tier"], "confirmed")
        self.assertIn("A night of board games.", row["description"])
        self.assertIn("Free dinner", row["description"])

    def test_the_stored_listing_keeps_what_it_states(self):
        store.upsert(self.conn, event(
            title="SIPLA x Statutes and Ladders Game Night",
            location="McDonough 140"))
        merge.add(self.conn, self.post)
        self.assertEqual(self.only()["location"], "McDonough 140")

    def test_adding_the_same_post_again_changes_nothing(self):
        merge.add(self.conn, self.post)
        merge.add(self.conn, self.post)
        row = self.only()
        self.assertEqual(row["description"].count("Free dinner"), 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM merges").fetchone()[0], 1)

    def test_a_ruling_outranks_the_post(self):
        eid = self.only()["id"]
        store.record_correction(self.conn, eid, "location", "Hotung 2000", "the room moved")
        store.apply_corrections(self.conn)
        merge.add(self.conn, self.post)
        self.assertEqual(self.only()["location"], "Hotung 2000")

    def test_a_ruling_on_food_outranks_a_surer_post(self):
        """The post is more confident than the email, so its food verdict would
        win -- except where a person has already ruled."""
        eid = self.only()["id"]
        store.record_correction(self.conn, eid, "has_food", "0",
                                "the organisers said there will be no food")
        merge.add(self.conn, self.post)
        self.assertEqual(str(self.only()["has_food"]), "0")

    def test_a_ruled_all_day_event_stays_all_day(self):
        """A ruling made it all day; a post giving a time must not undo it."""
        eid = self.only()["id"]
        store.record_correction(self.conn, eid, "starts_at", "2099-03-10",
                                "an all-day open house")
        merge.add(self.conn, dict(self.post, starts_at="2099-03-10T17:00:00"))
        self.assertEqual(self.only()["all_day"], 1)

    def test_the_merge_is_on_record(self):
        merge.add(self.conn, self.post)
        record = self.conn.execute("SELECT * FROM merges").fetchone()
        self.assertEqual(record["title"], "Game Night")
        self.assertEqual(record["source"], "groupme")
        self.assertIn("same day", record["reasons"])

    def test_a_post_with_the_email_s_own_title_folds_too(self):
        """Same title, same day is the store's own test of one event. The post
        must still fold in, not write its "6-8 PM" over the email's 5 o'clock."""
        verdict, _, reasons = merge.add(self.conn, dict(
            self.post, title="SIPLA x Statutes and Ladders Game Night",
            starts_at="2099-03-10T18:00:00", ends_at="2099-03-10T20:00:00"))
        self.assertEqual(verdict, "merged")
        self.assertTrue(reasons)
        row = self.only()
        self.assertEqual(row["starts_at"], "2099-03-10T17:00:00")
        self.assertEqual(row["location"], "500 First St NW, Room 750")
        self.assertEqual(row["source"], "groupme,mail")
        self.assertIn("A night of board games.", row["description"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM merges").fetchone()[0], 1)

    def test_a_post_stored_alone_is_updated_in_place(self):
        """Nothing else lists it, so there is nothing to fold into: a repeat of
        the post is the post again."""
        alone = event(title="Halloween Movie Night", org="ACS", source="groupme",
                      starts_at="2099-03-11T19:30:00")
        merge.add(self.conn, alone)
        verdict, _, reasons = merge.add(self.conn, dict(alone, location="McDonough 205"))
        self.assertEqual((verdict, reasons), ("updated", []))
        rows = self.conn.execute(
            "SELECT location FROM events WHERE source = 'groupme'").fetchall()
        self.assertEqual([r[0] for r in rows], ["McDonough 205"])

    def test_a_post_matching_nothing_is_added(self):
        verdict, eid, reasons = merge.add(self.conn, event(
            title="Halloween Movie Night", org="ACS", source="groupme",
            starts_at="2099-03-11T19:30:00"))
        self.assertEqual(verdict, "added")
        self.assertEqual(reasons, [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)


class TestThePostArrivesFirst(unittest.TestCase):
    """The chat posts on Monday; the broadcast with the flyer comes Friday."""

    def setUp(self):
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "first.db"))
        self.addCleanup(self.conn.close)
        _, self.post_id, _ = merge.add(self.conn, event(
            title="ACS Halloween Movie Night", org="American Constitution Society",
            source="groupme", starts_at="2099-03-10T19:30:00",
            description="There will be some fun snacks.",
            tier="likely", confidence=70, has_food=True, meals=["snacks"]))
        store.record_correction(self.conn, self.post_id, "ends_at",
                                "2099-03-10T21:00:00", "the poster says 9pm")
        store.upsert(self.conn, event(
            title="ACS Movie Night", org="Office of Student Life",
            source="broadcast", starts_at="2099-03-10T19:30:00",
            location="McDonough 205"))
        self.folded = merge.fold_secondhand(self.conn)

    def test_the_post_becomes_part_of_the_listing(self):
        self.assertEqual(self.folded, 1)
        rows = self.conn.execute("SELECT * FROM events").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "ACS Movie Night")
        self.assertEqual(rows[0]["source"], "broadcast,groupme")
        self.assertTrue(rows[0]["has_food"])

    def test_a_ruling_on_the_post_moves_with_it(self):
        kept = self.conn.execute("SELECT id FROM events").fetchone()[0]
        self.assertEqual(store.corrected_fields(self.conn, kept), {"ends_at"})
        self.assertEqual(store.corrected_fields(self.conn, self.post_id), set())

    def test_running_it_again_finds_nothing(self):
        self.assertEqual(merge.fold_secondhand(self.conn), 0)


class TestTwins(unittest.TestCase):
    """Two first-hand listings: one event only on the strict test."""

    def test_one_lecture_announced_three_ways(self):
        a = event(title="Kestrel Lecture on Water Rights", location="Hart Auditorium")
        b = event(title="2098-2099 Kestrel Lecture on Water Rights: Rivers in a Dry Century",
                  location="Hart Auditorium")
        c = event(title='"2098\u20132099 Kestrel Lecture on Water Rights"')
        self.assertIn("same room", merge.twins(a, b))
        self.assertTrue(merge.twins(b, c))
        self.assertTrue(merge.twins(a, c))

    def test_not_twins(self):
        cases = [
            # Half an hour apart: a chat post would fold, an email does not.
            (event(title="Antitrust Careers Panel", starts_at="2099-03-10T17:00:00"),
             event(title="Antitrust Careers Panel", starts_at="2099-03-10T17:30:00")),
            # The same session, run in two rooms at once.
            (event(title="1L Resume Review", location="McDonough 140"),
             event(title="1L Resume Review", location="Hotung 2000")),
            # Two lunches at noon, two firms.
            (event(title="Lunch and Learn with Bracewell", starts_at="2099-03-10T12:00:00"),
             event(title="Lunch with White & Case", starts_at="2099-03-10T12:00:00")),
            # The same room and minute, but nothing else in common.
            (event(title="Public Interest Panel", location="Hart Auditorium"),
             event(title="Halloween Costume Contest", location="Hart Auditorium")),
            # Half the shorter title in common is not most of it.
            (event(title="Antitrust Careers Panel"),
             event(title="Antitrust Law Society Movie Night")),
            # No time on one: nothing to say they start together.
            (event(title="Kestrel Lecture on Water Rights", all_day=True,
                   starts_at="2099-03-10T00:00:00"),
             event(title="Kestrel Lecture on Water Rights")),
        ]
        for a, b in cases:
            self.assertEqual(merge.twins(a, b), [], (a["title"], b["title"]))


class TestFoldingDuplicates(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "twins.db"))
        self.addCleanup(self.conn.close)
        self.lecture = event(title="Kestrel Lecture on Water Rights",
                             starts_at="2099-03-10T16:00:00", location="Hart Auditorium",
                             description="A reception follows.")
        store.upsert(self.conn, self.lecture)
        self.flyer = event(
            title="2098-2099 Kestrel Lecture on Water Rights: Rivers in a Dry Century",
            starts_at="2099-03-10T16:00:00", source="broadcast", flyer_id="ab12",
            description="Refreshments will be served.", tier="confirmed",
            confidence=95, has_food=True, meals=["snacks"])
        store.upsert(self.conn, self.flyer)
        store.upsert(self.conn, event(
            title='"Who Owns a River?" Kestrel Lecture on Water Rights',
            starts_at="2099-03-10T16:00:00"))
        # Not a twin: same minute, nothing in common.
        store.upsert(self.conn, event(title="Halloween Costume Contest",
                                      starts_at="2099-03-10T16:00:00"))
        self.hidden = merge.fold_duplicates(self.conn)

    def shown(self):
        return [e["title"] for e in docket.build_events(self.conn, days=99999,
                                                        min_tier="unlikely")]

    def test_one_listing_is_left_and_it_is_the_flyer_s(self):
        self.assertEqual(self.hidden, 2)
        kept = [t for t in self.shown() if "Kestrel" in t]
        self.assertEqual(kept, [self.flyer["title"]])
        self.assertIn("Halloween Costume Contest", self.shown())

    def test_the_listing_gains_what_the_copies_said(self):
        row = self.conn.execute("SELECT * FROM events WHERE flyer_id = 'ab12'").fetchone()
        self.assertIn("A reception follows.", row["description"])
        self.assertEqual(row["location"], "Hart Auditorium")
        self.assertTrue(row["has_food"])

    def test_copies_are_hidden_not_deleted_and_on_record(self):
        hidden = self.conn.execute("SELECT COUNT(*) FROM events WHERE dismissed = 1").fetchone()[0]
        self.assertEqual(hidden, 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM merges").fetchone()[0], 2)

    def test_the_copy_s_email_read_again_stays_hidden(self):
        store.upsert(self.conn, self.lecture)
        self.assertEqual(merge.fold_duplicates(self.conn), 0)
        self.assertEqual([t for t in self.shown() if "Kestrel" in t], [self.flyer["title"]])

    def test_a_ruling_brings_a_copy_back_for_good(self):
        copy = self.conn.execute("SELECT id FROM events WHERE title = ?",
                                 (self.lecture["title"],)).fetchone()[0]
        store.record_correction(self.conn, copy, "dismissed", "0",
                                "a separate talk, not the lecture")
        store.apply_corrections(self.conn)
        self.assertEqual(merge.fold_duplicates(self.conn), 0)
        self.assertIn(self.lecture["title"], self.shown())

    def test_a_ruled_listing_is_the_one_kept(self):
        conn = store.connect(os.path.join(tempfile.mkdtemp(), "ruled.db"))
        self.addCleanup(conn.close)
        store.upsert(conn, self.flyer)
        store.upsert(conn, self.lecture)
        ruled = conn.execute("SELECT id FROM events WHERE title = ?",
                             (self.lecture["title"],)).fetchone()[0]
        store.record_correction(conn, ruled, "ends_at", "2099-03-10T17:30:00",
                                "the email gives the end")
        self.assertEqual(merge.fold_duplicates(conn), 1)
        left = conn.execute("SELECT id FROM events WHERE NOT dismissed").fetchall()
        self.assertEqual([r[0] for r in left], [ruled])


    def test_two_ruled_listings_are_left_for_a_person(self):
        conn = store.connect(os.path.join(tempfile.mkdtemp(), "both.db"))
        self.addCleanup(conn.close)
        store.upsert(conn, self.flyer)
        store.upsert(conn, self.lecture)
        for eid in [r[0] for r in conn.execute("SELECT id FROM events")]:
            store.record_correction(conn, eid, "ends_at", "2099-03-10T17:30:00",
                                    "the email gives the end")
        self.assertEqual(merge.fold_duplicates(conn), 0)


class TestOneEventUnderTwoNames(unittest.TestCase):
    """Same minute, different titles: one event when the rare words or the
    whole text say so. Every name, firm and room is invented."""

    FILLER = [  # what an inbox is mostly made of, so "panel" counts for little
        ("Antitrust Careers Panel", "Join practising attorneys for a panel and lunch."),
        ("ACS General Body Meeting", "Our GBM, with pizza. RSVP please."),
        ("1L Networking Reception", "Meet attorneys at a networking reception."),
        ("Health Law Panel", "A panel of attorneys on health law careers."),
        ("Moot Court Info Session", "Information session for 1Ls, lunch provided."),
        ("Pro Bono Fair", "Meet pro bono organisations and attorneys."),
        ("Study Break", "Snacks in the atrium for everyone."),
        ("Career Panel and Happy Hour", "A panel, then a happy hour with attorneys."),
    ]

    TEMPLATE = ("The Office of Career Strategy invites 1Ls to an evening reception at "
                "the firm's Washington office. Business casual. Transportation vouchers "
                "will be provided to registered students. Space is limited; register "
                "through the career portal.")

    def setUp(self):
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "alike.db"))
        self.addCleanup(self.conn.close)
        for i, (title, text) in enumerate(self.FILLER):
            store.upsert(self.conn, event(title=title, description=text,
                                          starts_at="2099-04-%02dT12:00:00" % (i + 1)))

    def store(self, **kw):
        e = event(**kw)
        store.upsert(self.conn, e)
        return e

    def alike(self, a, b):
        return merge.alike(a, b, merge.rarity(self.conn))

    def test_a_rare_name_in_both_titles(self):
        a = self.store(title="Howdy Hoyas: Meet Austin Firm Kestrel & Vane Tomorrow!",
                       location="McDonough Hall", org="Texas Club",
                       description="Meet Kestrel & Vane attorneys over drinks.")
        b = self.store(title="Kestrel & Vane Health Law Q&A + Happy Hour",
                       location="McDonough Hall", org="Health Law Society",
                       description="A Q&A with Kestrel & Vane, then a happy hour.")
        self.assertIn("titles share kestrel, vane", self.alike(a, b))

    def test_the_same_words_under_a_broken_title(self):
        text = ("The Chess Club is holding its long-overdue general body meeting "
                "on Thursday at 5pm in McDonough 205, with Bishop's Pizza.")
        a = self.store(title="Chess Club General Body Meeting", description=text)
        b = self.store(title="Long-overdue GBM next", description=text)
        self.assertTrue(self.alike(a, b))

    def test_one_room_under_two_names(self):
        a = self.store(title="Choosing a Practice Group with Hale & Partners",
                       location="111 Mass Ave NW Multipurpose Room")
        b = self.store(title="Hale & Partners Choosing a Practice Group Panel",
                       location="111 Mass Ave NW LL201")
        self.assertTrue(self.alike(a, b))

    def test_not_one_event(self):
        cases = [
            # Two language tables at noon.
            (dict(title="Spanish Conversation Table", description="Practise Spanish over coffee."),
             dict(title="Tavola di Conversazione", description="Chiacchiere e caffe, in italiano.")),
            # The same words, in two buildings.
            (dict(title="Kestrel & Vane Reception", location="McDonough 140"),
             dict(title="Kestrel & Vane Reception Remarks", location="Hotung 2000")),
            # The same template, two firms: 61% alike, and still two events.
            (dict(title="Hale & Partners 1L Reception", firms=[{"name": "Hale & Partners"}],
                  description="Hale & Partners. " + self.TEMPLATE),
             dict(title="Ward Quill 1L Reception", firms=[{"name": "Ward Quill"}],
                  description="Ward Quill. " + self.TEMPLATE)),
            # Two rooms of one building, and titles alike but not near-identical.
            (dict(title="Kestrel Vane Moot Court Practice Round", location="McDonough 140"),
             dict(title="Kestrel Vane Moot Court Final Round", location="McDonough 205")),
            # One organisation's two events: one word in common is its name.
            (dict(title="Kestrel Society Mixer", location="McDonough 140"),
             dict(title="Kestrel Society Board Elections", location="McDonough 205")),
            # Half an hour apart.
            (dict(title="Kestrel & Vane Q&A", starts_at="2099-03-10T17:00:00"),
             dict(title="Kestrel & Vane Q&A and Reception", starts_at="2099-03-10T17:30:00")),
        ]
        for a, b in cases:
            self.assertEqual(self.alike(self.store(**a), self.store(**b)), [],
                             (a["title"], b["title"]))

    def test_a_rare_name_is_folded_where_the_titles_alone_are_not(self):
        a = self.store(title="Howdy Hoyas: Meet Austin Firm Kestrel & Vane Tomorrow!",
                       location="McDonough Hall",
                       description="Meet Kestrel & Vane attorneys over drinks.")
        b = self.store(title="Kestrel & Vane Health Law Q&A + Happy Hour",
                       location="McDonough Hall",
                       description="A Q&A with Kestrel & Vane, then a happy hour.")
        self.assertEqual(merge.twins(a, b), [])
        self.assertEqual(merge.fold_duplicates(self.conn), 1)

    def test_the_copy_is_hidden_and_the_better_title_kept(self):
        text = ("The Chess Club is holding its long-overdue general body meeting "
                "on Thursday at 5pm in McDonough 205, with Bishop's Pizza.")
        self.store(title="In other news, we have the Chess Club GBM", description=text)
        self.store(title="Chess Club General Body Meeting", description=text)
        # The broken one was seen first, which would otherwise decide it.
        self.conn.execute("UPDATE events SET first_seen = '2000-01-01' WHERE title LIKE 'In other%'")
        self.conn.commit()
        self.assertEqual(merge.fold_duplicates(self.conn), 1)
        left = [r[0] for r in self.conn.execute(
            "SELECT title FROM events WHERE NOT dismissed AND starts_at LIKE '2099-03-10%'")]
        self.assertEqual(left, ["Chess Club General Body Meeting"])

    def test_close_but_not_enough_goes_to_a_person(self):
        a = self.store(title="Ward Quill Restructuring 1L Reception",
                       description="Meet Ward Quill restructuring attorneys. RSVP by Friday.")
        b = self.store(title="Kestrel Society October Meeting",
                       description="Our October meeting, then the Ward Quill restructuring "
                                   "reception. RSVP by Friday.")
        self.assertEqual(self.alike(a, b), [])
        self.assertEqual(merge.fold_duplicates(self.conn), 0)
        pairs = merge.possible_duplicates(self.conn)
        self.assertEqual([{p[0]["title"], p[1]["title"]} for p in pairs],
                         [{a["title"], b["title"]}])

    def test_a_pair_ruled_apart_is_not_raised_again(self):
        self.test_close_but_not_enough_goes_to_a_person()
        eid = self.conn.execute("SELECT id FROM events WHERE title = ?",
                                ("Kestrel Society October Meeting",)).fetchone()[0]
        store.record_correction(self.conn, eid, "dismissed", "0", "two events")
        self.assertEqual(merge.possible_duplicates(self.conn), [])


class TestAPostHoldsNoSlot(unittest.TestCase):
    """A post holding a slot would keep out the email it repeats."""

    def test_listings_leave_it_out(self):
        conn = store.connect(os.path.join(tempfile.mkdtemp(), "slots.db"))
        self.addCleanup(conn.close)
        store.upsert(conn, event(title="Game Night", org="Statutes & Ladders",
                                 source="groupme"))
        held = store.listings(conn)
        mail = event(title="Statutes and Ladders Game Night", org="Statutes & Ladders")
        self.assertEqual(store.repeats(mail, held), "")


class TestOnThePage(unittest.TestCase):
    def test_the_source_is_named(self):
        self.assertEqual(docket.pretty_source("groupme"), "GroupMe")
        self.assertEqual(docket.pretty_source("groupme,mail"), "GroupMe + Email")

    def test_an_off_campus_post_is_kept_like_an_email(self):
        conn = store.connect(os.path.join(tempfile.mkdtemp(), "page.db"))
        self.addCleanup(conn.close)
        store.upsert(conn, event(
            title="Environmental Org Mixer", source="groupme",
            location="Prost, 919 5th St NW", tier="likely", confidence=70,
            has_food=True, meals=["snacks"]))
        titles = [e["title"] for e in docket.build_events(conn, days=99999)]
        self.assertIn("Environmental Org Mixer", titles)


class TestTheAddCommand(unittest.TestCase):
    """./fff add, end to end, against a database of its own."""

    def run_add(self, events):
        path = os.path.join(tempfile.mkdtemp(), "events.json")
        with open(path, "w") as fh:
            json.dump(events, fh)
        out = subprocess.run([os.path.join(ROOT, "fff"), "add", path],
                             capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(), "cli.db")
        self.env = dict(os.environ, FFF_DB=self.db)
        conn = store.connect(self.db)
        store.upsert(conn, event(title="SIPLA x Statutes and Ladders Game Night"))
        conn.close()

    def test_a_post_is_merged_and_says_where(self):
        out = self.run_add([{"title": "Game Night", "org": "Statutes & Ladders",
                             "source": "groupme", "starts_at": "2099-03-10T17:00:00",
                             "description": "Free dinner."}])
        self.assertIn("merged", out)
        self.assertIn("SIPLA x Statutes and Ladders Game Night", out)

    def test_an_unknown_source_is_refused(self):
        out = self.run_add([{"title": "Game Night", "org": "S&L", "source": "slack",
                             "starts_at": "2099-03-10T17:00:00"}])
        self.assertIn("skipped (source must be one of", out)

    def test_only_a_known_sign_up_page_is_kept(self):
        out = self.run_add([{"title": "Halloween Movie Night", "org": "ACS",
                             "source": "groupme", "starts_at": "2099-03-11T19:30:00",
                             "rsvp_url": "https://www.surveymonkey.com/r/ABC123"}])
        self.assertIn("link left out", out)
        conn = store.connect(self.db)
        self.addCleanup(conn.close)
        row = conn.execute("SELECT rsvp_url FROM events WHERE title = ?",
                           ("Halloween Movie Night",)).fetchone()
        self.assertEqual(row["rsvp_url"], "")


    def test_a_day_with_no_hour_is_listed_as_time_tba(self):
        """Never an hour made up; and an email with one is still all day."""
        self.run_add([{"title": "Dinner Reception with Hale & Partners",
                       "org": "Kestrel Society", "source": "groupme",
                       "starts_at": "2099-03-12", "description": "Free dinner."}])
        conn = store.connect(self.db)
        self.addCleanup(conn.close)
        store.upsert(conn, event(title="Kestrel Open House", all_day=True,
                                 starts_at="2099-03-13T00:00:00"))
        row = conn.execute("SELECT starts_at, all_day FROM events WHERE title = ?",
                           ("Dinner Reception with Hale & Partners",)).fetchone()
        self.assertEqual(tuple(row), ("2099-03-12T00:00:00", 1))
        tba = {e["title"]: e["time_tba"] for e in docket.build_events(
            conn, days=99999, min_tier="unlikely")}
        self.assertTrue(tba["Dinner Reception with Hale & Partners"])
        self.assertFalse(tba["Kestrel Open House"])


    def test_dupes_lists_what_may_be_one_event(self):
        conn = store.connect(self.db)
        for i, (title, text) in enumerate(TestOneEventUnderTwoNames.FILLER):
            store.upsert(conn, event(title=title, description=text,
                                     starts_at="2099-04-%02dT12:00:00" % (i + 1)))
        for title, text in (("Ward Quill Restructuring 1L Reception",
                             "Meet Ward Quill restructuring attorneys. RSVP by Friday."),
                            ("Kestrel Society October Meeting",
                             "Our October meeting, then the Ward Quill restructuring "
                             "reception. RSVP by Friday.")):
            store.upsert(conn, event(title=title, description=text,
                                     starts_at="2099-03-11T18:00:00"))
        conn.close()
        out = subprocess.run([os.path.join(ROOT, "fff"), "dupes"], capture_output=True,
                             text=True, env=self.env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Kestrel Society October Meeting", out.stdout)
        self.assertIn("1 pair(s) that may be one event", out.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
