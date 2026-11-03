"""A title that does not read like a title is a read failure, not a verdict."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"

import titles  # noqa: E402

# Catching OCR damage means comparing against an English word list. macOS has
# one at /usr/share/dict/words; many Linux machines do not, and there the
# check stands down rather than guessing (test_no_dictionary_means_no_opinion).
needs_words = unittest.skipUnless(
    titles.dictionary(), "no word list at /usr/share/dict/words")


class TestMisspelling(unittest.TestCase):
    @needs_words
    def test_ocr_damage_is_caught(self):
        for word in ("Mentorstip", "TREET", "EVENI", "Recepiton"):
            self.assertTrue(titles.looks_misspelled(word), word)

    def test_plurals_are_not_damage(self):
        # The system word list holds base forms only. Without de-inflection
        # every plural on the page reads as a one-edit typo.
        for word in ("Futures", "Talks", "Things", "Perspectives", "Grants",
                     "Careers", "Panels", "Networking", "Sessions"):
            self.assertFalse(titles.looks_misspelled(word), word)

    def test_proper_nouns_survive(self):
        for word in ("Buzbee", "Skadden", "Gewirz", "Latino", "Meetup",
                     "BRAZLA", "Hotung", "McDonough"):
            self.assertFalse(titles.looks_misspelled(word), word)

    def test_short_words_are_never_judged(self):
        self.assertFalse(titles.looks_misspelled("GBM"))
        self.assertFalse(titles.looks_misspelled("1L"))

    def test_no_dictionary_means_no_opinion(self):
        titles.dictionary.cache_clear()
        real, titles.DICT_PATHS = titles.DICT_PATHS, ("/nonexistent",)
        try:
            self.assertFalse(titles.looks_misspelled("Mentorstip"))
        finally:
            titles.DICT_PATHS = real
            titles.dictionary.cache_clear()


class TestProperNames(unittest.TestCase):
    """A name runs over several words; only some are ever in a word list."""

    def test_the_word_beside_a_known_name_is_part_of_it(self):
        v = titles.assess("Charting Your 1L Year with Sheppard Mullin",
                          source="broadcast")
        self.assertTrue(v["ok"], v["issues"])

    def test_and_so_are_the_middle_words_of_a_long_firm_name(self):
        # About spelling only: a bare firm name is separately (and rightly)
        # flagged as naming an organisation rather than an event.
        for firm in ("Skadden Arps Slate Meagher & Flom",
                     "Weil Gotshal & Manges Reception",
                     "Simpson Thacher & Bartlett Coffee Chats"):
            v = titles.assess(firm, source="broadcast")
            damage = [i for i in v["issues"] if "OCR damage" in i]
            self.assertEqual(damage, [], f"{firm}: {damage}")

    @needs_words
    def test_but_ordinary_vocabulary_confers_nothing(self):
        # "Meetup" is in KNOWN but is not a name, so it must not shield the
        # damaged word beside it -- that would lose the catch this exists for.
        self.assertIn("Mentorstip", titles._damaged_words("Mentorstip Meetup"))


class TestAssess(unittest.TestCase):
    def test_banner_repetition_is_stripped(self):
        v = titles.assess("KESTREL'S LANTERN GUILD " * 4, source="broadcast")
        self.assertEqual(v["clean"], "KESTREL'S LANTERN GUILD")
        self.assertTrue(any("repeated" in i for i in v["issues"]))

    def test_but_what_follows_the_repetition_is_kept(self):
        # The words after the border are the ones that say what is happening.
        v = titles.assess("KESTREL'S LANTERN GUILD " * 4 + "GENERAL BODY MEETING",
                          source="broadcast")
        self.assertEqual(v["clean"],
                         "KESTREL'S LANTERN GUILD GENERAL BODY MEETING")

    def test_a_date_is_not_a_title(self):
        v = titles.assess("Wednesday, September 16'", source="broadcast")
        self.assertTrue(any("date" in i for i in v["issues"]))

    def test_a_title_that_is_only_a_date_is_not_peeled(self):
        # Stripping the date off a title that IS a date leaves "Wednesday",
        # which reads like a real title and hides the failure.
        v = titles.assess("Wednesday, September 16", source="broadcast")
        self.assertEqual(v["clean"], "Wednesday, September 16")
        self.assertFalse(v["ok"])

    def test_trailing_time_ranges_come_off_too(self):
        v = titles.assess("SIPLA Mentorship Meetup SEPT 24 | 3:30 PM",
                          source="broadcast")
        self.assertEqual(v["clean"], "SIPLA Mentorship Meetup")

    def test_appended_date_comes_off(self):
        v = titles.assess("MOCK INTERVIEW Thursday, September 17, 2026",
                          source="broadcast")
        self.assertEqual(v["clean"], "MOCK INTERVIEW")

    def test_leading_junk(self):
        v = titles.assess("x O Sheppard Charting Your 1L Year Panel",
                          source="broadcast")
        self.assertEqual(v["clean"], "O Sheppard Charting Your 1L Year Panel")

    def test_generic_only(self):
        for bad in ("General Body Meeting", "FIRST LOOK", "Info Session"):
            self.assertFalse(titles.assess(bad, source="broadcast")["ok"], bad)

    def test_a_firm_wordmark_is_not_an_event(self):
        for name in ("PRYOR CASHMAN", "Skadden Arps", "Weil Gotshal"):
            v = titles.assess(name, source="broadcast")
            self.assertFalse(v["ok"], name)
            self.assertTrue(any("organisation" in i for i in v["issues"]), name)

    def test_but_a_name_plus_an_event_is_fine(self):
        for good in ("APALSA Open House", "Pryor Cashman Lunch Panel",
                     "BrazLA Student Mock Interview"):
            self.assertTrue(titles.assess(good, source="broadcast")["ok"], good)

    def test_a_real_title_passes(self):
        for good in ("BLSA General Body Meeting",
                     "Charting Your 1L Year with Sheppard Mullin",
                     "Latino Political Power in Flux",
                     "The Fall 2026 Fiscal Futures Series"):
            v = titles.assess(good, source="broadcast")
            self.assertTrue(v["ok"], f"{good}: {v['issues']}")


class TestSourceGating(unittest.TestCase):
    """A calendar title is what a human typed. Only OCR gets spell-checked."""

    @needs_words
    def test_calendar_titles_are_not_spell_checked(self):
        typed = titles.assess("Halvorsen Mentorstip Lecture", source="livewhale")
        self.assertTrue(typed["ok"])
        scanned = titles.assess("Halvorsen Mentorstip Lecture", source="broadcast")
        self.assertFalse(scanned["ok"])

    def test_sense_checks_apply_to_every_source(self):
        self.assertFalse(titles.assess("FIRST LOOK", source="livewhale")["ok"])

    @needs_words
    def test_unknown_source_is_checked(self):
        self.assertFalse(titles.assess("Mentorstip Meetup")["ok"])


class TestRepair(unittest.TestCase):
    def test_recoverable_damage_does_not_reach_the_queue(self):
        r = titles.repair("KESTREL'S LANTERN GUILD " * 4, source="broadcast")
        self.assertTrue(r["ok"])
        self.assertTrue(r["repaired"])
        self.assertEqual(r["fixed"], "KESTREL'S LANTERN GUILD")

    @needs_words
    def test_unrecoverable_damage_does(self):
        r = titles.repair("FIRST TREET", source="broadcast")
        self.assertFalse(r["ok"])
        self.assertTrue(any("TREET" in i for i in r["issues"]))

    def test_a_good_title_is_left_alone(self):
        r = titles.repair("Charting Your 1L Year with Sheppard Mullin",
                          source="broadcast")
        self.assertTrue(r["ok"])
        self.assertFalse(r["repaired"])

    def test_empty(self):
        self.assertFalse(titles.repair("")["ok"])


class TestSuspicious(unittest.TestCase):
    @needs_words
    def test_reads_the_source_off_the_event(self):
        events = [{"title": "Mentorstip Meetup", "source": "livewhale"},
                  {"title": "Mentorstip Meetup", "source": "broadcast"}]
        flagged = titles.suspicious(events)
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0][0]["source"], "broadcast")


if __name__ == "__main__":
    unittest.main()


class TestOrdinaryWordsTheDictionaryLacks(unittest.TestCase):
    """The system word list is old and incomplete.

    "women" is not in it -- an irregular plural it never carried -- so the
    Women of Color Collective's picnic was reported as possible OCR damage on
    the word "Women". Several other everyday words in this domain are missing
    the same way.
    """

    def test_they_are_not_misspellings(self):
        for word in ("Women", "resumes", "networking", "snacks", "bootcamp",
                     "externship", "LLM", "mentorship"):
            self.assertFalse(titles.looks_misspelled(word), word)

    @needs_words
    def test_a_real_mangling_is_still_caught(self):
        # "Recepshun" is not here: it is four edits from "reception" and the
        # checker only looks two deep. A pre-existing limit, not a new one.
        for word in ("Mentorstip", "Networkinng", "Confrence", "Recepton"):
            self.assertTrue(titles.looks_misspelled(word), word)

    def test_the_corrected_picnic_title_reads_cleanly(self):
        verdict = titles.assess("Women of Color Collective GBM Picnic",
                                None, "broadcast")
        self.assertTrue(verdict["ok"], verdict.get("issues"))


class TestSentencesFromTheEmail(unittest.TestCase):
    """A newsletter split at the wrong line takes the sentence before an entry
    as its title. Every name and event here is invented."""

    def sentence(self, title):
        return [i for i in titles.assess(title, None, "mail")["issues"]
                if i.startswith("reads like a sentence")]

    def test_how_a_sentence_talks(self):
        for title in ("In other news, we have Trivia-ween next Friday",
                      "We\u2019re delighted to host you at our first Resume Review",
                      "A quick reminder that the Tax Clinic Panel is",
                      "Hi all, the Kestrel Society GBM",
                      "There will be pizza at the Chess Club GBM",
                      "Speaking of events, we'll be hosting a Moot Court Mixer"):
            self.assertTrue(self.sentence(title), title)

    def test_names_that_merely_talk_to_you(self):
        for title in ("Catch Me If You Can: Can Satellites Track Illegal Fishing?",
                      "So You Think You Want to Clerk?",
                      "You're Invited: 1L Reception with Hale & Partners",
                      "We the People: A Constitution Day Reading",
                      "Hiking Club Info Session", "Thanksgiving Potluck",
                      "Panel on Trade Remedies next Wednesday"):
            self.assertEqual(self.sentence(title), [], title)


class TestDeadlines(unittest.TestCase):
    """Something to do by a time is not somewhere to be at one.

    "Don't forget to upload your writing sample by EOD tomorrow" names a
    thing and a day, and passed for a title until the "by" with a time after
    it was read for what it is.
    """

    def test_a_deadline_is_not_an_event(self):
        for title in ("Don't forget to upload your writing sample by EOD tomorrow",
                      "Remember to upload your writing sample by EOD",
                      "Applications due Friday", "Cover letters due by 5pm",
                      "1L Writing Competition Packet Due 10:00 pm TONIGHT 10/2"):
            verdict = titles.repair(title, None, "mail")
            self.assertFalse(verdict["ok"], title)
            self.assertTrue(verdict["issues"][0].startswith("a deadline"), title)

    def test_taking_the_date_off_does_not_mend_one(self):
        """Without "10/3", "Cover letters due" still announces nothing."""
        self.assertFalse(titles.repair("Cover letters due 10/3", None, "mail")["ok"])

    def test_a_visit_is_not_a_deadline(self):
        for title in ("Stop by Today for Free Coffee", "Drop by Friday for Donuts"):
            self.assertTrue(titles.repair(title, None, "mail")["ok"], title)

    def test_nor_is_an_rsvp_on_a_real_event(self):
        self.assertTrue(titles.repair("Pizza with the Chess Club, RSVP by Friday",
                                      None, "mail")["ok"])

    def test_by_and_due_in_ordinary_names(self):
        for title in ("Panel hosted by Hale & Partners", "Due Process Clinic Open House",
                      "Lunch Sponsored by Monday Partners", "Coffee by the Lake",
                      "Judicial Clerkship Panel - October 1"):
            self.assertTrue(titles.repair(title, None, "mail")["ok"], title)

    def test_the_old_judgement_is_still_to_be_had(self):
        """For the caller that must know a title failed on this alone."""
        self.assertTrue(titles.repair("Remember to upload your writing sample by EOD",
                                      None, "mail", deadlines=False)["ok"])


class TestANameIsNotASentenceCutShort(unittest.TestCase):
    """A sentence cut at its date trails off; an event's name that ends on the
    same word does not. Each of these names was dropped once (October 2026)."""

    def check(self, title, ok, fixed=None):
        got = titles.repair(title, None, "mail")
        self.assertEqual(got["ok"], ok, (title, got["issues"]))
        if fixed is not None:
            self.assertEqual(got["fixed"], fixed)

    def test_a_name_that_ends_on_next_or_through(self):
        for title in ("Affirmative Action After SFFA: What's next?",
                      "Women in Law: Where Do We Go Next", "Halloween Drive Thru",
                      "Courtroom Walk Through"):
            self.check(title, True, title)

    def test_the_word_a_date_was_cut_from_comes_off(self):
        """'Final GBM next [Wednesday]' is still the GBM."""
        self.check("Final GBM next", True, "Final GBM")
        self.check("Long-overdue GBM next", True, "Long-overdue GBM")

    def test_a_sentence_cut_at_its_date(self):
        for title in ("Coats will be collected through", "Now through",
                      'The Quiet Docket Event First, the Kestrel Society is co-hosting a talk called '
                      '"The Quiet Docket: What Small Claims Courts Teach Us About Access to Justice" on'):
            self.check(title, False)

    def test_a_name_that_ends_on_a_day(self):
        for title in ("Lawyers of Tomorrow", "Antitrust Today", "Investing in Tomorrow!"):
            self.check(title, True, title)

    def test_the_announcements_day_comes_off(self):
        self.check("Housing Clinic Townhall Today!", True, "Housing Clinic Townhall")
        self.check("Hale & Partners x Kestrel Society Panel and Reception Today", True,
                   "Hale & Partners x Kestrel Society Panel and Reception")
        self.check("TOMORROW: Kestrel Health Law Q&A", True, "Kestrel Health Law Q&A")

    def test_a_sentence_about_the_day_is_refused(self):
        for title in ("Our GBM is tomorrow", "Workshop a week from today",
                      "The Health Law panel is TONIGHT"):
            self.check(title, False)

    def test_a_capitalised_name_ending_on_a_day_keeps_it(self):
        self.check("Civil Rights Today", True, "Civil Rights Today")

    def test_a_day_word_is_kept_when_without_it_the_title_names_nothing(self):
        self.check("General Body Meeting Tonight!", True, "General Body Meeting Tonight!")
        self.check("Info Session Today!", True, "Info Session Today!")

    def test_each_way_a_title_asks_a_question(self):
        self.check("Thinking about clerking next?", True)          # the question mark
        self.check("Clerkships: What Judges Look For", True)       # a short last clause
        self.check("Clerkships: what's next", True)                # "what's" is "what"

    def test_a_leading_day_word_stays_when_the_rest_is_generic(self):
        self.check("TOMORROW: Info Session", True, "TOMORROW: Info Session")

    def test_a_question_keeps_its_last_word(self):
        self.check("Clerkships: what's next", True, "Clerkships: what's next")

    def test_the_day_word_guards(self):
        self.check("Antitrust Today!", True, "Antitrust Today!")              # one word left
        self.check("Pizza with the Dean - Today", True, "Pizza with the Dean")   # set off by a mark

