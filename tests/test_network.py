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
        v = score_network("Reception", "With attorneys from Latham & Watkins.", "")
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
        self.assertEqual(self.find("Meet Weil attorneys and learn about their "
                                   "practice areas"), ["Weil, Gotshal & Manges"])
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
    running its own session is the opposite: King & Spalding's "Big Law
    Basics" scored 30 for the firm and -22 for being online, netting 8 --
    under the storage floor, so it never reached the database.
    """

    def test_the_online_penalty_is_not_held_against_a_named_firm(self):
        got = score_network(
            title="Big Law Basics with King & Spalding",
            description="Join King & Spalding attorneys for an inside look at "
                        "today's recruiting landscape. Online webinar.",
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
        for text in ("an inside look at today's recruiting landscape",
                     "navigating the interview process with confidence",
                     "a recruiting timeline overview",
                     "interview tips from our attorneys"):
            got = score_network(title="Session", description=text, location="")
            self.assertIn("application help", got["network_reason"], text)


class TestACountOfFirmsIsAFirmFair(unittest.TestCase):
    """OCS's 1L Law Firm Receptions named no single firm.

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
                     "Meet attorneys from 10 sponsoring firms"):
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
            contacts.find_firms("Day in the Life of a MoFo Capital Markets "
                                "Attorney"), ["Morrison Foerster"])

    def test_inside_is_not_a_first_name(self):
        self.assertEqual(
            contacts.find_firms("Step Inside Milbank's Financial "
                                "Restructuring Practice"), ["Milbank"])

    def test_a_field_label_is_not_a_first_name(self):
        """One "Location: Milbank DC" used to suppress every Milbank."""
        self.assertEqual(
            contacts.find_firms("Step Inside Milbank’s Practice. "
                                "Location: Milbank DC"), ["Milbank"])

    def test_a_person_is_still_a_person(self):
        self.assertEqual(contacts.find_firms("Jill Milbank, Director"), [])

    def test_a_firm_in_the_title_clears_the_floor(self):
        got = score_network(title="Life & Practice at Paul, Weiss",
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
            description="We are inviting PPP DC attorneys and other attorneys "
                        "across public interest fields. Meet some attorneys "
                        "in a casual environment.",
            location="Wunder Garten", org="People's Parity Project")
        self.assertEqual(got["network_kind"], "practitioner")
        self.assertTrue(worth_storing(got))


class TestHowStudentOrgsNameRecruiting(unittest.TestCase):
    def reason(self, title):
        return score_network(title=title, description="", location="",
                             org="law")["network_reason"]

    def test_a_big_law_fair_is_a_career_fair(self):
        self.assertIn("career fair", self.reason("SIPLA IP Big Law Fair"))

    def test_big_law_101_is_application_help(self):
        for title in ("JLSA Big Law 101", "BigLaw Demystified"):
            self.assertIn("application help", self.reason(title), title)


class TestTheLawSchoolsOwnRooms(unittest.TestCase):
    """GYA's career fair never says "law"; it is in the McDonough Atrium."""

    FAIR = ("meet practitioners working in education, juvenile justice, "
            "youth defense, child welfare and children's rights")

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
        got = score_network(title="Health Law Speed Networking",
                            description="Save the date.", location="Hotung 2001",
                            org="Health Law Society")
        self.assertEqual(got["network_kind"], "practitioner")


class TestCoverLetters(unittest.TestCase):
    def reason(self, title):
        return score_network(title=title, description="", location="",
                             org="law")["network_reason"]

    def test_a_cover_letter_review_is_application_help(self):
        self.assertIn("application help",
                      self.reason("Howdy Hoyas Cover Letter Review"))


class TestShoutedFlyers(unittest.TestCase):
    """OCR'd flyers are all capitals, so capitals prove nothing there."""

    FLYER = ("Office of Student Life Approved by Georgetown Law LGBTQIA+ STUDENT "
             "ORGANIZATION COVINGTON X OUTLAW HAPPY HOUR Join us for drinks and "
             "bites at The Dubliner to connect with Covington attorneys")

    def test_a_shouted_firm_is_still_a_firm(self):
        self.assertEqual(contacts.find_firms(self.FLYER), ["Covington & Burling"])

    def test_a_shouted_speaker_is_still_a_person(self):
        self.assertEqual(contacts.find_firms(
            "General Counsel, USCCB WILLIAM QUINN Senior Policy Director"), [])

    def test_a_shouted_title_still_marks_a_person(self):
        self.assertEqual(contacts.find_firms("FEATURING JUDGE COVINGTON, "
                                             "a conversation with lawyers"), [])

    def test_ordinary_case_is_unchanged(self):
        self.assertEqual(contacts.find_firms("Jill Milbank, Director"), [])

    def test_a_casual_environment_is_not_environmental_law(self):
        got = score_network(title="Happy Hour", description="Meet attorneys "
                            "in a casual environment.", location="")
        self.assertNotIn("environmental", got["practice_areas"])


class TestABareFirmInTheTitle(unittest.TestCase):
    """CFLO's "Carve Your Path at Skadden" (21 Oct 2026) scored 30, under the
    page's floor: the title was read on its own, and a bare surname with no
    legal word beside it is not trusted -- though the email it heads said the
    event was at Skadden's office."""

    BODY = ("CFLO Events: Carve Your Path at Skadden. Wednesday, October 21, "
            "6:00 pm - 7:30 pm. Location: Skadden DC (the firm's Washington "
            "office), 1440 New York Ave NW.")

    def test_it_counts_once_the_body_has_named_the_firm(self):
        verdict = score_network("Carve Your Path at Skadden", self.BODY, "", "CFLO")
        self.assertIn("firm in the title", verdict["network_reason"])
        self.assertGreaterEqual(verdict["network_score"], 34)

    def test_the_bare_title_alone_is_still_not_enough(self):
        verdict = score_network("Carve Your Path at Skadden", "", "", "CFLO")
        self.assertEqual(verdict["firms"], [])

    def test_a_person_with_the_surname_is_still_a_person(self):
        self.assertEqual(contacts.find_firms("Dinner with Professor Skadden",
                                             established={"Skadden, Arps"}), [])


class TestACaseNamesAParty(unittest.TestCase):
    """ACS's "The Slaughter of Agency Independence" (14 Oct 2026) cited Trump
    v. Slaughter, and every "Slaughter" on the flyer was read as Slaughter and
    May -- a firm tag on a constitutional-law panel."""

    # The flyer's own opening. "Office" in the approval stamp is the legal
    # word that put the title's "Slaughter" in reach of a firm.
    FLYER = ("ACS AMERICAN CONSTITUTION Office of Student Life Approved by RSVP "
             "SOCIETY Georgetown Law HERE! Georgetown University Law Center The "
             "Slaughter of Agency Independence Trump v. Slaughter, the "
             "Politicization of the Federal Civil Service, and the Death of "
             "Administrative Legislative Supremacy Helen Ashby Arun "
             "Mehta Professor of Law General Counsel")

    def test_a_party_to_a_case_is_not_a_firm(self):
        verdict = score_network("The Slaughter of Agency Independence",
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
