"""Networking scorer and the firm registry.

The firm-detection cases are the ones that actually misfired against live
Georgetown data: people's surnames colliding with firm names.
"""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"
import contacts
from network import score_network, worth_storing


class TestFirmDetection(unittest.TestCase):
    def find(self, text):
        return contacts.find_firms(text)

    def test_multiword_firm_names(self):
        self.assertEqual(self.find("Join Gibson Dunn attorneys for a reception"),
                         ["Gibson, Dunn & Crutcher"])
        self.assertIn("Covington & Burling",
                      self.find("Covington & Burling LLP info session"))

    def test_punctuation_variants_all_resolve(self):
        for spelling in ("Gibson, Dunn & Crutcher", "Gibson Dunn",
                         "Gibson, Dunn"):
            self.assertIn("Gibson, Dunn & Crutcher",
                          self.find(f"A reception with {spelling} attorneys"))

    def test_single_word_firm_needs_legal_context(self):
        self.assertEqual(self.find("A partner at Milbank, summer associate program"),
                         ["Milbank"])
        self.assertEqual(self.find("We walked past the venable old oak tree."), [])

    def test_given_name_before_surname_is_a_person(self):
        """'Ambassador Jane Sullivan' is not Sullivan & Cromwell."""
        for text in ("Ambassador Jane Sullivan, a law partner, will speak",
                     "Moderated by Ruth Morrison, Director of the law program",
                     "a conversation with VA CTO Owen Wilkinson about the law"):
            self.assertEqual(self.find(text), [], text)

    def test_surname_after_comma_is_still_a_person(self):
        """Journalistic style: introduce in full, then refer by surname."""
        self.assertEqual(
            self.find("VA CTO Owen Wilkinson spoke. Later, Wilkinson led "
                      "law reform efforts at the Office."), [])

    def test_true_firm_reference_survives_nearby_person(self):
        """The hard case: a person AND a real firm in the same sentence."""
        self.assertEqual(
            self.find("Ambassador Sullivan is a retired law partner at "
                      "Mayer Brown LLP, co-chair of the firm's practice."),
            ["Mayer Brown"])

    def test_common_surnames_alone_are_never_firms(self):
        self.assertEqual(self.find("Morgan drove to Davis for the weekend."), [])
        self.assertEqual(self.find("The King and I opens Friday."), [])

    def test_empty_input(self):
        self.assertEqual(self.find(""), [])
        self.assertEqual(self.find(None), [])


class TestTheFirmRegistry(unittest.TestCase):
    def test_registry_loaded(self):
        self.assertGreater(len(contacts.REGISTRY), 50)

    def test_a_missing_registry_degrades_quietly(self):
        original = contacts.REGISTRY_FILE
        try:
            contacts.REGISTRY_FILE = "/nonexistent/firms.json"
            self.assertEqual(contacts.firm_registry(), [])
        finally:
            contacts.REGISTRY_FILE = original


class TestNetworkScoring(unittest.TestCase):
    def test_recruiting_event(self):
        v = score_network("Kirkland & Ellis 1L Diversity Fellowship Info Session",
                          "Meet our summer associate team.", "Hotung 2000")
        self.assertEqual(v["network_kind"], "recruiting")
        self.assertTrue(v["worth_attending"])
        self.assertIn("1L", v["audience"])

    def test_firm_named_without_recruiting_language(self):
        v = score_network("Antitrust Roundtable",
                          "A discussion with attorneys from Gibson Dunn.",
                          "McDonough 200")
        self.assertEqual(v["network_kind"], "firm")
        self.assertEqual(v["firms"][0]["name"], "Gibson, Dunn & Crutcher")
        self.assertIn("antitrust", v["practice_areas"])

    def test_practitioner_event(self):
        v = score_network("Global Antitrust Enforcement Symposium",
                          "Enforcement officials and leading practitioners.",
                          "Hart Auditorium")
        self.assertEqual(v["network_kind"], "practitioner")

    def test_purely_academic_is_not_worth_attending(self):
        v = score_network("Faculty Colloquium on Legal Theory",
                          "A seminar on jurisprudence.", "McDonough 496")
        self.assertEqual(v["network_kind"], "academic")
        self.assertFalse(v["worth_attending"])

    def test_admissions_event_is_excluded(self):
        """Prospective-student events are not networking for a current student."""
        v = score_network("Prospective Students Info Session",
                          "Learn about applying to Georgetown Law.", "")
        self.assertFalse(v["worth_attending"])

    def test_closed_event_penalised(self):
        open_ev = score_network("Firm Reception", "Networking with attorneys.", "")
        closed = score_network("Firm Reception",
                               "Networking with attorneys. By invitation only.", "")
        self.assertLess(closed["network_score"], open_ev["network_score"])

    def test_firms_carry_names_only(self):
        """Firm names only: nothing about who the reader knows there."""
        v = score_network("Reception", "Latham & Watkins attorneys from the DC office.", "")
        self.assertEqual(v["firms"], [{"name": "Latham & Watkins"}])

    def test_more_firms_raises_score(self):
        one = score_network("Reception", "Attorneys from Latham & Watkins.", "")
        two = score_network("Reception",
                            "Attorneys from Latham & Watkins and Gibson Dunn.", "")
        self.assertGreater(two["network_score"], one["network_score"])

    def test_no_signals(self):
        v = score_network("Moot Court Practice", "Bring your notes.", "")
        self.assertEqual(v["network_kind"], "none")
        self.assertFalse(v["worth_attending"])

    def test_orthogonal_to_food(self):
        """Food and networking are independent axes."""
        from score import score_event
        text = ("Kirkland & Ellis info session for 1Ls. "
                "No food will be provided.")
        self.assertTrue(score_network("Info Session", text, "")["worth_attending"])
        self.assertFalse(score_event("Info Session", text, "")["has_food"])

    def test_garbage_input(self):
        for bad in (None, "", 12345, "<script>x</script>"):
            score_network(bad, bad, bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestLawRelevanceGate(unittest.TestCase):
    """Generic phrases like 'information session' match study-abroad briefings
    exactly as well as recruiting events; something must be about law."""

    def test_study_abroad_info_session_is_not_recruiting(self):
        v = score_network("Bolivia 2027 Centennial Lab Information Session",
                          "Learn about the semester programme in La Paz.", "")
        self.assertFalse(v["law_relevant"])
        self.assertNotEqual(v["network_kind"], "recruiting")

    def test_firm_name_establishes_relevance_alone(self):
        v = score_network("Information Session",
                          "An information session with Gibson Dunn.", "")
        self.assertTrue(v["law_relevant"])

    def test_doctrinal_language_counts_as_legal(self):
        """A debate on administrative deference never says the word 'law'."""
        v = score_network("Lunch Debate: Chevron After Loper Bright",
                          "A debate on administrative deference and the "
                          "doctrine's future.", "Hotung 2000")
        self.assertTrue(v["law_relevant"])

    def test_law_group_counts_as_relevant(self):
        v = score_network("Reception", "Join us for a reception.", "",
                          org="studentaffairscapitolcampus")
        self.assertTrue(v["law_relevant"])


class TestSampleNetworkingEmails(unittest.TestCase):
    """The shipped .eml fixtures, end to end."""

    @classmethod
    def setUpClass(cls):
        from ingest.mail import from_maildrop
        path = os.path.join(os.path.dirname(__file__), "..", "samples")
        cls.by_title = {e["title"]: e for e in from_maildrop(path)}

    def get(self, fragment):
        for title, ev in self.by_title.items():
            if fragment.lower() in title.lower():
                return ev
        self.fail(f"no sample matching {fragment!r}")

    def test_firm_reception_scores_on_both_axes(self):
        e = self.get("Covington")
        self.assertTrue(e["has_food"])
        self.assertEqual(e["network_kind"], "recruiting")
        self.assertEqual(e["firms"][0]["name"], "Covington & Burling")
        self.assertIn("antitrust", e["practice_areas"])

    def test_clerkship_panel_has_no_food_but_high_network_value(self):
        """Explicitly food-free, and the most valuable event in the set."""
        e = self.get("Clerkship")
        self.assertFalse(e["has_food"])
        self.assertEqual(e["network_kind"], "recruiting")
        self.assertGreaterEqual(len(e["firms"]), 2)

    def test_clerkship_panel_would_survive_ingest(self):
        """It must not be dropped for failing the food filter."""
        from network import worth_storing
        self.assertTrue(worth_storing(self.get("Clerkship")))


class TestShortFirmNames(unittest.TestCase):
    """BigLaw is routinely named by a short first word -- "Meet Weil
    attorneys" on a real Georgetown flyer. A six-character floor missed those
    entirely, but four characters lets ordinary English in, so short tokens
    need an unmistakable firm word beside them."""

    def find(self, text):
        return contacts.find_firms(text)

    def test_short_firm_name_with_firm_context(self):
        self.assertEqual(self.find("Meet Weil attorneys over coffee and ask "
                                   "about their groups"), ["Weil, Gotshal & Manges"])
        self.assertEqual(self.find("attorneys from Ropes will attend"),
                         ["Ropes & Gray"])

    def test_short_name_without_firm_context_is_ignored(self):
        """'law' and 'panel' are everywhere here; they are not enough."""
        self.assertEqual(self.find("with Paul about the law school panel"), [])
        self.assertEqual(self.find("Weil is a lovely surname"), [])

    def test_common_english_words_never_resolve_alone(self):
        """'Will' is unique among firms and still matches 'will host'."""
        self.assertEqual(self.find("The panel will host a discussion"), [])
        self.assertEqual(self.find("A case will be argued by counsel"), [])
        self.assertEqual(self.find("The king and the best lawyers"), [])

    def test_full_name_still_resolves(self):
        self.assertEqual(self.find("Paul Weiss attorneys will host"),
                         ["Paul, Weiss"])

    def test_meet_is_a_lead_in_not_a_given_name(self):
        """'Meet Weil' is an invitation; 'Meet' must not read as a forename."""
        self.assertTrue(self.find("Meet Weil attorneys"))
        self.assertFalse(self.find("Ambassador Jane Sullivan, a law partner"))


class TestFirmListsAreNotNames(unittest.TestCase):
    """How these events actually advertise: a comma-separated list of firms."""

    APALSA = ("Attending firms: Paul, Weiss, Troutman Pepper Locke, "
              "Cleary Gottlieb, Jones Day, Winston Taylor, Cooley, Orrick, "
              "Groom Law Group, Wilson Sonsini, Sidley Austin, and more.")

    def test_a_word_ending_in_a_title_is_not_a_title(self):
        """`PERSON_TITLES` was anchored only at the end, so "firms" matched
        on "ms" -- the word most likely to introduce firms suppressed them."""
        for word in ("firms", "alums", "programs", "teams", "python"):
            self.assertFalse(contacts.PERSON_TITLES.search(word), word)
        for word in ("mr", "prof", "dean", "judge"):
            self.assertTrue(contacts.PERSON_TITLES.search(word), word)

    def test_firms_survive_the_word_that_introduces_them(self):
        self.assertIn("Skadden, Arps", contacts.find_firms("firms: Skadden"))
        self.assertIn("Covington & Burling",
                      contacts.find_firms("Participating programs: Covington attorneys"))

    def test_the_whole_attending_list_is_found(self):
        """Each firm was being read as the surname of the one before it, and
        the context window could not reach "Attending firms:" by the fourth
        entry -- so the single-word firms at the tail were dropped."""
        found = contacts.find_firms(self.APALSA)
        for name in ("Cleary Gottlieb", "Jones Day", "Cooley", "Orrick",
                     "Sidley Austin", "Wilson Sonsini"):
            self.assertIn(name, found, name)

    def test_a_person_is_still_a_person(self):
        self.assertEqual(contacts.find_firms("Ambassador Jane Sullivan speaks"), [])
        self.assertEqual(contacts.find_firms("Judge Milbank presides"), [])

    def test_a_named_firm_does_not_license_every_surname(self):
        found = contacts.find_firms(
            "Cleary Gottlieb will host. Nina Cooley, a 2L, will speak.")
        self.assertIn("Cleary Gottlieb", found)
        self.assertNotIn("Cooley", found)


class TestOnlyTheNameAFirmGoesBy(unittest.TestCase):
    """A firm is called by its first word alone -- Sidley, Norton -- never by
    a later one. Each case below tagged the wrong firm on the live page."""

    def test_later_words_of_a_firm_name_are_ordinary_words(self):
        for text, wrong in (
                ("Kirkland & Ellis Open House: meet each practice group.",
                 "Groom Law Group"),
                ("Graduate studies seminar for Fulbright scholars, with alumni.",
                 "Norton Rose Fulbright"),
                ("Happy hour with attorneys from an Austin-based law firm.",
                 "Sidley Austin"),
                ("A chance to meet attorneys at the firm's office.",
                 "Clifford Chance")):
            self.assertNotIn(wrong, contacts.find_firms(text), text)

    def test_the_first_word_still_names_the_firm(self):
        self.assertIn("Sidley Austin", contacts.find_firms("Meet Sidley attorneys."))
        self.assertIn("Norton Rose Fulbright",
                      contacts.find_firms("Norton attorneys will host the reception."))

    def test_another_firm_s_full_name_is_not_this_one(self):
        """Not every firm is in the registry: a speaker from Davis Wright
        Tremaine is not Davis Polk."""
        text = ("Panel with Gibson, Dunn & Crutcher. Speakers: Wren Halloway, "
                "Davis Wright Tremaine LLP.")
        self.assertNotIn("Davis Polk & Wardwell", contacts.find_firms(text))
        self.assertIn("Davis Polk & Wardwell",
                      contacts.find_firms("Davis Polk attorneys will attend."))
        self.assertIn("Latham & Watkins",
                      contacts.find_firms("Join attorneys from Latham & Watkins LLP."))

    def test_a_row_of_logos_is_still_a_list(self):
        """MENALSA's flyer ran its logos together."""
        found = contacts.find_firms(
            "Hear from Big Law attorneys. Akin FRESHFIELDS LATHAM&WATKINS LLP Milbank")
        self.assertIn("Latham & Watkins", found)
        self.assertIn("Freshfields", found)


class TestGovernmentEventsAreNotDropped(unittest.TestCase):
    """DOJ Honors is a headline 1L pipeline event.

    `PRACTITIONER_SIGNALS` scored DOJ/FTC/SEC at +28 for government lawyers
    while `LEGAL_RELEVANCE` had no agency terms and took 35 off for having
    nothing legal about it -- one table contradicting the other. The result
    netted out under `worth_storing`'s floor, so these never reached the
    database at all.
    """

    def test_the_agencies_are_law_relevant(self):
        for title in ("DOJ Honors Program info session",
                      "SEC Enforcement Division Info Session"):
            got = score_network(title=title, description="", location="")
            self.assertTrue(got["law_relevant"], title)
            self.assertNotEqual(got["network_kind"], "none", title)

    def test_they_are_stored(self):
        got = score_network(title="DOJ Honors Program info session",
                            description="", location="")
        event = dict(got, title="DOJ Honors Program info session")
        self.assertTrue(worth_storing(event))

    def test_an_unrelated_event_is_still_not_law_relevant(self):
        got = score_network(title="Movie night and board games",
                            description="", location="")
        self.assertEqual(got["network_kind"], "none")


class TestTheNetworkTitleSeam(unittest.TestCase):
    """The same doubled-title seam the food scorer had.

    "Milbank Recruiting 101 - Georgetown" doubled reads
    "... Georgetown . Milbank ...". A capitalised word before a one-word firm
    marks it as somebody's surname, and one such occurrence suppresses every
    mention of that firm in the message -- so the firm that sent the email
    vanished from its own event.
    """

    def test_a_firm_survives_a_title_ending_in_a_capitalised_word(self):
        got = score_network(title="Milbank Recruiting 101 - Georgetown",
                            description="Milbank will host a webinar for 1Ls.",
                            location="")
        self.assertIn("Milbank", [f["name"] for f in got["firms"]])

    def test_no_firm_is_invented_across_the_seam(self):
        """A title whose end and start would spell a firm only once doubled.

        "Day Jones" joined by a space becomes "Day Jones Day Jones", and
        "Jones Day" appears in the middle -- a firm that is nowhere in the
        title. A full stop cannot be crossed.
        """
        got = score_network(title="Day Jones", description="", location="")
        self.assertEqual([f["name"] for f in got["firms"]], [])


class TestAFirmsOwnWebinar(unittest.TestCase):
    """Being online is a fact about the room, not a reason to discount it.

    The "virtual" penalty holds back incidental online noise. A named firm
    running its own session is the opposite: King & Spalding's own recruiting
    webinar scored 30 for the firm and -22 for being online, netting 8 --
    under the storage floor, so it never reached the database.
    """

    def test_the_online_penalty_is_not_held_against_a_named_firm(self):
        got = score_network(
            title="Recruiting Basics with King & Spalding",
            description="King & Spalding associates give a frank tour of "
                        "this year's recruiting landscape. Online webinar.",
            location="Online")
        self.assertGreaterEqual(got["network_score"], 34)
        # "recruiting" once the recruiting wording is also recognised; "firm"
        # on the firm name alone. Either is a professional kind, which is what
        # keeps it out of the bin.
        self.assertIn(got["network_kind"], ("firm", "recruiting"))

    def test_a_webinar_with_no_firm_is_still_discounted(self):
        got = score_network(title="Wellness webinar",
                            description="A webinar about mindfulness. Online.",
                            location="Online")
        self.assertEqual(got["network_kind"], "none")

    def test_how_a_firm_describes_its_own_recruiting_session(self):
        """"recruiting landscape" and "interview process" were uncovered;
        only "recruiting event/fair/season" and "hiring process" were."""
        for text in ("a candid tour of this year's recruiting landscape",
                     "navigating the interview process, step by step",
                     "a recruiting timeline overview",
                     "interview tips from our attorneys"):
            got = score_network(title="Session", description=text, location="")
            self.assertIn("application help", got["network_reason"], text)


class TestACountOfFirmsIsAFirmFair(unittest.TestCase):
    """OCS's 1L firm receptions named no single firm.

    "Network with over 30 law firms" -- the biggest networking nights of the
    semester for a 1L -- earned nothing but "reception" and was not tagged
    as networking at all.
    """

    def kind(self, text):
        return score_network(title="Reception", description=text,
                             location="")["network_kind"]

    def test_counted_firms(self):
        for text in ("Network with over 30 law firms",
                     "a reception with more than 20 law firms",
                     "Drinks with attorneys from our 10 sponsoring firms"):
            self.assertEqual(self.kind(text), "recruiting", text)

    def test_numbers_that_are_not_a_count_of_firms(self):
        for text in ("a history of the firm over 30 years",
                     "our 3 favourite firms of the year were discussed"):
            self.assertEqual(self.kind(text), "none", text)

    def test_network_as_a_verb(self):
        got = score_network(title="Reception",
                            description="Network with attorneys after the panel",
                            location="")
        self.assertIn("explicitly networking", got["network_reason"])


class TestFirmsOnCflosNewsletter(unittest.TestCase):
    """The 25 September 2026 CFLO issue listed twelve firm events.

    Four named no firm and four more scored under the storage floor, so the
    most useful email of the week put five events on the page.
    """

    def test_mofo_is_morrison_foerster(self):
        self.assertEqual(
            contacts.find_firms("Ask a MoFo Funds Lawyer "
                                "Anything"), ["Morrison Foerster"])

    def test_inside_is_not_a_first_name(self):
        self.assertEqual(
            contacts.find_firms("Peek Inside Milbank's Litigation "
                                "Practice"), ["Milbank"])

    def test_a_field_label_is_not_a_first_name(self):
        """One "Location: Milbank DC" used to suppress every Milbank."""
        self.assertEqual(
            contacts.find_firms("Peek Inside Milbank’s Practice. "
                                "Location: Milbank DC"), ["Milbank"])

    def test_a_person_is_still_a_person(self):
        self.assertEqual(contacts.find_firms("Jill Milbank, Director"), [])

    def test_a_firm_in_the_title_clears_the_floor(self):
        got = score_network(title="An Afternoon at Paul, Weiss",
                            description="Thursday, October 1, Gewirz 12",
                            location="Gewirz 12", org="cflo")
        self.assertEqual(got["network_kind"], "firm")
        self.assertTrue(worth_storing(got))

    def test_a_firm_only_in_passing_earns_no_title_credit(self):
        """A contact address in a footer is not the event's host."""
        passing = score_network(title="Mentorship Meetup",
                                description="Questions? Ask a partner at "
                                            "Paul, Weiss.", location="")
        titled = score_network(title="Mentorship Meetup with Paul, Weiss",
                               description="Questions? Ask a partner at "
                                           "Paul, Weiss.", location="")
        self.assertNotIn("firm in the title", passing["network_reason"])
        self.assertIn("firm in the title", titled["network_reason"])
        self.assertEqual(titled["network_score"] - passing["network_score"], 10)

    def test_inviting_attorneys_is_practitioners(self):
        got = score_network(
            title="Networking Happy Hour",
            description="We are inviting local public interest attorneys to "
                        "join us after class. Come meet a few attorneys "
                        "in a relaxed environment.",
            location="Wunder Garten", org="People's Parity Project")
        self.assertEqual(got["network_kind"], "practitioner")
        self.assertTrue(worth_storing(got))


class TestHowStudentOrgsNameRecruiting(unittest.TestCase):
    def reason(self, title):
        return score_network(title=title, description="", location="",
                             org="law")["network_reason"]

    def test_a_big_law_fair_is_a_career_fair(self):
        self.assertIn("career fair", self.reason("Kestrel Society BigLaw Fair"))

    def test_big_law_101_is_application_help(self):
        for title in ("Chess Club Big Law 101", "BigLaw Demystified"):
            self.assertIn("application help", self.reason(title), title)


class TestTheLawSchoolsOwnRooms(unittest.TestCase):
    """GYA's career fair never says "law"; it is in the McDonough Atrium."""

    FAIR = ("meet practitioners working on school equity, foster care, "
            "youth mentoring, family support and children's advocacy")

    def test_a_law_building_makes_it_a_law_event(self):
        got = score_network(title="Annual Youth Advocacy Career Fair",
                            description=self.FAIR, location="McDonough Atrium",
                            org="Georgetown Youth Advocates")
        self.assertTrue(got["law_relevant"])
        self.assertEqual(got["network_kind"], "recruiting")

    def test_the_business_schools_mcdonough_does_not(self):
        got = score_network(title="Annual Youth Advocacy Career Fair",
                            description=self.FAIR,
                            location="Rafik B. Hariri Building",
                            org="Georgetown Youth Advocates")
        self.assertFalse(got["law_relevant"])

    def test_speed_networking_has_people_in_it(self):
        got = score_network(title="Speed Networking for Future Health Lawyers",
                            description="Save the date.", location="Hotung 2001",
                            org="Health Law Society")
        self.assertEqual(got["network_kind"], "practitioner")


class TestCoverLetters(unittest.TestCase):
    def reason(self, title):
        return score_network(title=title, description="", location="",
                             org="law")["network_reason"]

    def test_a_cover_letter_review_is_application_help(self):
        self.assertIn("application help",
                      self.reason("Kestrel Society Cover Letter Review"))


class TestShoutedFlyers(unittest.TestCase):
    """OCR'd flyers are all capitals, so capitals prove nothing there."""

    FLYER = ("Student Life Office: approved flyer. PRIDE LAW STUDENT "
             "ORGANIZATION COVINGTON X PRIDE LAW TRIVIA NIGHT Teams of four, "
             "prizes for the winners, then time to connect with Covington attorneys")

    def test_a_shouted_firm_is_still_a_firm(self):
        self.assertEqual(contacts.find_firms(self.FLYER), ["Covington & Burling"])

    def test_a_shouted_speaker_is_still_a_person(self):
        self.assertEqual(contacts.find_firms(
            "General Counsel, KVF WREN QUINN Senior Policy Director"), [])

    def test_a_shouted_title_still_marks_a_person(self):
        self.assertEqual(contacts.find_firms("FEATURING JUDGE COVINGTON, "
                                             "a conversation with lawyers"), [])

    def test_ordinary_case_is_unchanged(self):
        self.assertEqual(contacts.find_firms("Jill Milbank, Director"), [])

    def test_a_casual_environment_is_not_environmental_law(self):
        got = score_network(title="Happy Hour", description="Meet attorneys "
                            "in a low-key environment.", location="")
        self.assertNotIn("environmental", got["practice_areas"])


class TestABareFirmInTheTitle(unittest.TestCase):
    """CFLO's Skadden evening (21 Oct 2026) scored 30, under the page's
    floor: its title named the firm bare and was read on its own, and a bare
    surname with no legal word beside it is not trusted -- though the email
    the title headed said the event was at Skadden's office."""

    BODY = ("CFLO Events: Chart Your Course at Skadden. Wednesday, October 21, "
            "5:30 pm - 7:00 pm ET. Location: Skadden DC (the firm's office "
            "downtown), 1440 New York Ave NW.")

    def test_it_counts_once_the_body_has_named_the_firm(self):
        verdict = score_network("Chart Your Course at Skadden", self.BODY, "", "CFLO")
        self.assertIn("firm in the title", verdict["network_reason"])
        self.assertGreaterEqual(verdict["network_score"], 34)

    def test_the_bare_title_alone_is_still_not_enough(self):
        verdict = score_network("Chart Your Course at Skadden", "", "", "CFLO")
        self.assertEqual(verdict["firms"], [])

    def test_a_person_with_the_surname_is_still_a_person(self):
        self.assertEqual(contacts.find_firms("Dinner with Professor Skadden",
                                             established={"Skadden, Arps"}), [])


class TestACaseNamesAParty(unittest.TestCase):
    """ACS's panel on Trump v. Slaughter (14 Oct 2026) put the case's name in
    its title, and every "Slaughter" on the flyer was read as Slaughter and
    May -- a firm tag on a constitutional-law panel."""

    # An invented flyer laid out like the real one: the approval stamp, the
    # logo and the title run together. "Office" in the stamp is the legal
    # word that puts the title's "Slaughter" in reach of a firm.
    FLYER = ("AMERICAN CONSTITUTION SOCIETY ACS Approved: Student Life Office "
             "RSVP AT THE LINK Georgetown Law Agencies after "
             "Slaughter Trump v. Slaughter, Removal Power, and the "
             "Future of the Independent Commissions Wren Halloway Ash "
             "Pell Professor of Law General Counsel")

    def test_a_party_to_a_case_is_not_a_firm(self):
        verdict = score_network("Agencies after Slaughter",
                                self.FLYER, "Hotung 2001",
                                "American Constitution Society")
        self.assertEqual(verdict["firms"], [])

    def test_either_side_of_the_v(self):
        for text in ("Trump v. Slaughter, with practising attorneys",
                     "Slaughter v. Trump, with practising attorneys",
                     "Trump vs Slaughter panel with partners"):
            self.assertEqual(contacts.find_firms(text), [], text)

    def test_the_firm_named_in_full_still_counts(self):
        self.assertEqual(contacts.find_firms(
            "Slaughter and May attorneys will attend the reception"),
            ["Slaughter and May"])

    def test_a_bare_firm_without_a_caption_still_counts(self):
        self.assertIn("Skadden, Arps",
                      contacts.find_firms("Join Skadden attorneys for a reception"))


class TestAFirmsWholeNameAndItsNeighbours(unittest.TestCase):
    """Found in review (October 2026): a firm named in full, or next to
    another firm in a list, was read as some other firm and dropped."""

    def found(self, text):
        return contacts.find_firms(text)

    def test_a_one_word_firms_legal_name_runs_on(self):
        self.assertEqual(self.found("Lunch with attorneys from Orrick, Herrington & Sutcliffe LLP."),
                         ["Orrick"])
        self.assertEqual(self.found("Reception hosted by Freshfields Bruckhaus Deringer LLP attorneys."),
                         ["Freshfields"])

    def test_the_next_firm_in_a_list_is_not_a_tail(self):
        self.assertIn("Skadden, Arps",
                      self.found("Hear from attorneys at Skadden, Milbank LLP and Cooley LLP."))

    def test_a_hyphenated_name(self):
        self.assertEqual(self.found("Join attorneys from Curtis, Mallet-Prevost LLP for a reception."),
                         ["Curtis, Mallet-Prevost"])

    def test_what_a_merged_firm_is_still_called(self):
        self.assertEqual(self.found("Join attorneys from Perkins Coie for a networking reception."),
                         ["Ashurst Perkins Coie"])
        self.assertIn("McDermott Will & Emery",
                      self.found("Partner firms: Jones Day and McDermott Will & Schulte."))

    def test_a_firm_listed_twice_in_the_registry_is_still_one_firm(self):
        """Counted twice, "Cleary" looked shared and named nobody."""
        self.assertIn("Cleary Gottlieb",
                      self.found("Corporate and Antitrust at Cleary: a panel of attorneys."))

    def test_another_firm_sharing_the_first_word_is_still_refused(self):
        """Davis Wright Tremaine is in the registry now (2 Nov 2026), so the
        firm it guards against is an invented one."""
        self.assertEqual(self.found("Wren Halloway, Latham Brook Carver LLP, will speak on the panel."),
                         [])
        self.assertEqual(self.found("Wren Halloway, Davis Wright Tremaine LLP, will speak on the panel."),
                         ["Davis Wright Tremaine"])


class TestTheFirmsAddedInNovember(unittest.TestCase):
    """The registry gained 46 firms on 2 Nov 2026. Their names carry words
    that are everything else too: a number, given names, a foundation."""

    def found(self, text):
        return contacts.find_firms(text)

    def test_a_number_is_not_three_crowns(self):
        self.assertEqual(self.found("Three attorneys from the firm will join the panel."), [])
        self.assertEqual(self.found("Speakers: Wren Halloway (Three Crowns LLP)."), ["Three Crowns"])

    def test_given_names_are_not_firms(self):
        self.assertEqual(self.found("Justice Jackson will speak with attorneys."), [])
        self.assertEqual(self.found("Duane, a partner at the firm, will host."), [])
        self.assertIn("Jackson Walker", self.found("Kestrel Society X Jackson Walker Trivia Night"))

    def test_a_foundation_is_not_k_and_l_gates(self):
        self.assertEqual(self.found("Counsel from the Gates Foundation will join the panel."), [])
        self.assertEqual(self.found("Lunch with K&L Gates attorneys."), ["K&L Gates"])

    def test_initials_need_their_ampersand(self):
        """"K&L" alone was also "KL" and "K L"."""
        self.assertEqual(self.found("KL attorneys will host the reception."), [])
        self.assertEqual(self.found("AO attorneys will host the reception."), [])
        self.assertEqual(self.found("A&O attorneys will host the reception."), ["A&O Shearman"])

    def test_a_given_name_does_not_make_a_list(self):
        """Every word of every firm's name counted as a firm word, so once
        Bryan Cave was known, "Bryan Sullivan" read as a list of two firms."""
        self.assertNotIn("bryan", contacts.FIRM_WORDS)
        self.assertNotIn("Sullivan & Cromwell",
                         self.found("Bryan Sullivan, a partner at the firm, will speak."))

    def test_a_list_still_runs_through_a_new_firm(self):
        found = self.found("Firms joining us this year: Axinn, Cleary, Cooley, and more.")
        for name in ("Axinn", "Cleary Gottlieb", "Cooley"):
            self.assertIn(name, found)

    def test_a_rebranded_firm_by_its_old_name(self):
        self.assertEqual(self.found("Join attorneys from Seyfarth Shaw for lunch."), ["Seyfarth"])
        self.assertEqual(self.found("Panel with Watts Guerra LLP partners."), ["Guerra LLP"])
        self.assertEqual(self.found("Reception with Fenwick & West attorneys."), ["Fenwick"])

    def test_two_foleys_are_two_firms(self):
        self.assertEqual(self.found("Panel with Foley Hoag attorneys."), ["Foley Hoag"])
        self.assertEqual(self.found("Panel with Foley & Lardner attorneys."), ["Foley & Lardner"])
        self.assertEqual(self.found("Panel with Foley attorneys."), [])

    def test_a_job_title_or_a_bracket_is_an_affiliation(self):
        """A speaker's "Partner, Dentons" and a moderator's "(Freshfields)"
        read the job title and the speaker as given names, and the firm as
        their surname."""
        self.assertEqual(self.found("Speakers: Wren Halloway, Partner, Dentons; Ash Pell."), ["Dentons"])
        self.assertEqual(self.found("The panel will be moderated by Wren Halloway (Freshfields)."),
                         ["Freshfields"])
        self.assertEqual(self.found("Ash Pell, Fellow Hausfeld - Nov 3rd * Wren Halloway, Associate"),
                         ["Hausfeld"])

    def test_a_surname_a_professor_carries(self):
        self.assertEqual(self.found("Professor Greenberg on corporate law."), [])
        self.assertEqual(self.found("Guerra will speak with associates."), [])


class TestStoredTagsAreRechecked(unittest.TestCase):
    """A tag stored before the matcher learned better is taken back on what
    is still to come -- and only a tag this text gave."""

    def setUp(self):
        import json, tempfile, store
        self.json, self.store = json, store
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "recheck.db"))
        self.addCleanup(self.conn.close)

    def stored(self, title, firms, description="", location="", starts_at="2099-03-10T17:00:00"):
        from network import score_network
        ev = {"title": title, "description": description, "location": location,
              "org": "", "source": "mail", "starts_at": starts_at}
        ev.update(score_network(title=title, description=description, location=location))
        ev["firms"] = [{"name": n} for n in firms]
        ev["network_kind"] = "firm"
        self.store.upsert(self.conn, ev)
        return self.conn.execute("SELECT id FROM events WHERE title = ?", (title,)).fetchone()[0]

    def firms(self, eid):
        row = self.conn.execute("SELECT firms, network_kind FROM events WHERE id = ?", (eid,)).fetchone()
        return [f["name"] for f in self.json.loads(row["firms"])], row["network_kind"]

    def test_a_word_the_firm_does_not_go_by_loses_its_tag(self):
        import network
        fair = self.stored("Kirkland & Ellis Open House",
                           ["Groom Law Group", "Kirkland & Ellis"],
                           "Meet attorneys from each practice group.")
        talk = self.stored("Graduate Studies Seminar", ["Norton Rose Fulbright"],
                           location="Fulbright Korea")
        self.assertEqual(network.recheck(self.conn), 2)
        self.assertEqual(self.firms(fair)[0], ["Kirkland & Ellis"])
        firms, kind = self.firms(talk)
        self.assertEqual(firms, [])
        self.assertNotEqual(kind, "firm")

    def test_an_ordinary_word_of_the_name_is_no_sign(self):
        """'Lunch will be served' says nothing of McDermott Will & Emery."""
        import network
        lunch = self.stored("Antitrust Careers Lunch and Learn", ["McDermott Will & Emery"],
                            "Lunch will be served.")
        self.assertEqual(network.recheck(self.conn), 0)
        self.assertEqual(self.firms(lunch)[0], ["McDermott Will & Emery"])

    def test_the_firm_that_sent_it_keeps_its_tag(self):
        import network
        # The text alone would not name Davis Polk ("Davis Wright Tremaine
        # LLP" is another firm), but Davis Polk sent the email. The firm the
        # text does name is added beside it.
        eid = self.stored("Panel with Davis Wright Tremaine LLP partners",
                          ["Davis Polk & Wardwell"])
        self.conn.execute("UPDATE events SET network_reason = "
                          "'sent by Davis Polk & Wardwell; panel' WHERE id = ?", (eid,))
        self.conn.commit()
        self.assertEqual(network.recheck(self.conn), 1)
        self.assertEqual(self.firms(eid)[0], ["Davis Polk & Wardwell", "Davis Wright Tremaine"])

    def test_a_firm_named_bare_stays(self):
        import network
        # The title alone has no legal word, so the matcher does not find
        # Willkie in it; the tag came from context the email gave.
        eid = self.stored("Navigating Your Path With Willkie",
                          ["Willkie Farr & Gallagher"])
        self.assertEqual(network.recheck(self.conn), 0)
        self.assertEqual(self.firms(eid)[0], ["Willkie Farr & Gallagher"])

    def test_a_firm_this_text_never_mentions_came_from_elsewhere(self):
        import network
        eid = self.stored("1L Reception", ["Cooley"], "Join us for a reception.")
        self.assertEqual(network.recheck(self.conn), 0)
        self.assertEqual(self.firms(eid)[0], ["Cooley"])

    def test_the_past_is_left_alone(self):
        import network
        self.stored("Old Practice Area Fair", ["Groom Law Group"],
                    "Meet each practice group.", starts_at="2000-03-10T17:00:00")
        self.assertEqual(network.recheck(self.conn), 0)

    def test_a_firm_the_registry_learned_is_added(self):
        """A flyer someone has read is not read again, so a firm the registry
        learned since (Three Crowns, 2 Nov 2026) is added by the recheck."""
        import network
        eid = self.stored("Investment Arbitration Panel", [],
                          "Speakers: Wren Halloway, Partner, Three Crowns LLP.")
        self.assertEqual(network.recheck(self.conn), 1)
        self.assertEqual(self.firms(eid)[0], ["Three Crowns"])
        self.assertEqual(network.recheck(self.conn), 0, "a second pass changes nothing")

    def test_but_not_to_the_past(self):
        import network
        self.stored("Old Arbitration Panel", [], "Speakers: Wren Halloway, Three Crowns LLP.",
                    starts_at="2000-03-10T17:00:00")
        self.assertEqual(network.recheck(self.conn), 0)

    def test_an_added_firm_never_lowers_the_verdict(self):
        import network
        eid = self.stored("Investment Arbitration Panel", [],
                          "Speakers: Wren Halloway, Partner, Three Crowns LLP.")
        self.conn.execute("UPDATE events SET network_score = 99, network_kind = 'practitioner' "
                          "WHERE id = ?", (eid,))
        self.conn.commit()
        network.recheck(self.conn)
        row = self.conn.execute("SELECT network_score, network_kind FROM events WHERE id = ?",
                                (eid,)).fetchone()
        self.assertEqual((row["network_score"], row["network_kind"]), (99, "practitioner"))
        self.assertEqual(self.firms(eid)[0], ["Three Crowns"])
