"""Where an event is, and whether it is on the right campus.

Georgetown's calendar mixes two campuses 4km apart. Main Campus events in
Burleith buried the events a law student can actually reach, so they are
dropped outright. What survives has to name the ROOM: the building alone is
useless once you are in the lift.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"
import campus


class TestRoomNumbers(unittest.TestCase):
    """The feed writes the same room a dozen ways."""

    def test_abbreviations_resolve_with_the_room_kept(self):
        for spelling, expected in (("MCD 208", "McDonough 208"),
                                   ("MCDONOUGH 425", "McDonough 425"),
                                   ("McDONOUGH 220", "McDonough 220"),
                                   ("McDonough 337", "McDonough 337")):
            self.assertEqual(campus.locate(spelling)["place"], expected, spelling)

    def test_four_digit_rooms_survive(self):
        """Hotung's rooms really are 2000-series; a year guard ate them."""
        for spelling, expected in (("Hotung 2001", "Hotung 2001"),
                                   ("Hotung 2000", "Hotung 2000"),
                                   ("HOTUNG 6005", "Hotung 6005")):
            self.assertEqual(campus.locate(spelling)["place"], expected, spelling)

    def test_floors_and_named_spaces(self):
        self.assertEqual(campus.locate("Gewirz 12th Floor")["place"],
                         "Gewirz 12th Floor")
        self.assertEqual(campus.locate("McDonough Lobby")["place"],
                         "McDonough Lobby")

    def test_a_named_room_reads_before_its_building(self):
        """"The Whisk, McDonough", not "McDonough The Whisk"."""
        self.assertEqual(campus.locate("The Whisk, McDonough Hall")["place"],
                         "The Whisk, McDonough")
        self.assertEqual(campus.locate("Hart Auditorium")["place"],
                         "Hart Auditorium, McDonough")

    def test_a_street_number_is_not_a_room(self):
        self.assertEqual(campus.locate("500 1st Street NW")["place"],
                         "500 First Street NW")

    def test_a_building_with_no_room_keeps_its_full_name(self):
        self.assertEqual(campus.locate("HOTUNG")["place"], "Hotung")

    def test_an_unrecognised_place_is_shown_verbatim(self):
        """Better the feed's own words than a tidier invention. (HFSC is no
        longer an example here -- it is a recognised Main Campus building.)"""
        for spelling in ("Ministry Coffee", "The Cellar, 4th floor"):
            self.assertEqual(campus.locate(spelling)["place"], spelling, spelling)
            self.assertEqual(campus.locate(spelling)["campus"], "unknown")


class TestMainCampusIsExcluded(unittest.TestCase):
    """Assert the campus is positively identified as `main`, not merely that
    the event is dropped -- an unrecognised venue is also dropped, so a broken
    pattern would pass a drop-only assertion while detecting nothing."""

    def test_burleith_venues_are_identified(self):
        for spelling in ("Healy Hall", "ICC Auditorium", "Lauinger Library",
                         "Lohrfink Auditorium, Hariri Building",
                         "White Gravenor", "Red Square", "Riggs Library",
                         "Intercultural Center", "Maguire 304",
                         "Mortara Center Conference Room"):
            self.assertEqual(campus.locate(spelling)["campus"], "away", spelling)
            self.assertFalse(campus.is_reachable(spelling), spelling)

    def test_healey_family_student_center(self):
        """The feed spells it Healey; the filter only knew Healy, and the
        whole building walked through."""
        for spelling in ("Healey Family Student Center, Social Room",
                         "HFSC Herman Meeting Room", "Healy Hall"):
            self.assertEqual(campus.locate(spelling)["campus"], "away", spelling)

    def test_possessive_spellings_do_not_smuggle_one_through(self):
        for spelling in ("Regent's Hall - Fourth Floor Patio", "Regents Hall",
                         "Regents' Hall", "Regent\u2019s Hall"):
            self.assertEqual(campus.locate(spelling)["campus"], "away", spelling)

    def test_a_main_campus_mention_in_the_body_still_excludes(self):
        self.assertFalse(campus.is_reachable("", "", "Join us in Healy Hall"))


class TestLawNetworkingException(unittest.TestCase):
    """Across town is worth the shuttle for a firm recruiting law students."""

    def test_recruiting_survives_main_campus(self):
        main = campus.locate("Hariri Building")
        self.assertTrue(campus.keep_event(main, "live", "recruiting"))
        self.assertTrue(campus.keep_event(main, "live", "firm"))

    def test_a_mere_panel_does_not(self):
        main = campus.locate("Hariri Building")
        for kind in ("practitioner", "alumni", "academic", "none", ""):
            self.assertFalse(campus.keep_event(main, "live", kind), kind)

    def test_capitol_events_need_no_exception(self):
        self.assertTrue(campus.keep_event(campus.locate("MCD 208"), "live", ""))


class TestUnknownLocations(unittest.TestCase):
    def test_a_capitol_group_places_an_event_with_no_room(self):
        self.assertTrue(campus.is_reachable("", "studentaffairscapitolcampus"))
        self.assertTrue(campus.is_reachable("", "broadcast"))

    def test_an_unplaceable_event_is_kept_only_from_a_law_source(self):
        """The university-wide calendar is mostly Burleith. Defaulting its
        unplaceable events in is what put Drag Bingo on a law docket."""
        nowhere = campus.locate("", "", "")
        self.assertEqual(nowhere["campus"], "unknown")
        self.assertTrue(campus.keep_event(nowhere, "broadcast", ""))
        self.assertTrue(campus.keep_event(nowhere, "law", ""))
        self.assertFalse(campus.keep_event(nowhere, "live", ""))
        self.assertFalse(campus.keep_event(nowhere, "dcc", ""))

    def test_an_unplaceable_recruiting_event_is_kept(self):
        self.assertTrue(campus.keep_event(campus.locate("TBD"), "live", "firm"))

    def test_location_beats_the_body(self):
        place = campus.locate("Hotung 2000", "", "co-hosted with Healy Hall staff")
        self.assertEqual(place["place"], "Hotung 2000")
        self.assertTrue(place["on_campus"])

    def test_no_travel_times_are_reported(self):
        """Removed by request; nothing should reintroduce them."""
        self.assertNotIn("walk_minutes", campus.locate("MCD 208"))
        self.assertFalse(hasattr(campus, "walk_note"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestSuitePairs(unittest.TestCase):
    """Two suites are one destination, not the first of two numbers."""

    def test_a_pair_of_suites_stays_whole(self):
        place = campus.locate("500 First Street NW, Suites 100 & 120")
        self.assertIn("100", place["place"])
        self.assertIn("120", place["place"])

    def test_a_glued_building_and_room_is_split(self):
        self.assertEqual(campus.locate("McD344")["place"], "McDonough 344")
        self.assertEqual(campus.locate("Hotung1000")["place"], "Hotung 1000")

    def test_a_single_room_is_unaffected(self):
        self.assertEqual(campus.locate("McDonough 220")["place"], "McDonough 220")
        self.assertEqual(campus.locate("Hotung 2001")["place"], "Hotung 2001")


class TestVirtualEvents(unittest.TestCase):
    """An online event has no campus, so distance cannot judge it."""

    def test_it_is_recognised_and_named(self):
        place = campus.locate("", "", "Virtual Event. Held via Flo Recruit.")
        self.assertEqual(place["campus"], "virtual")
        self.assertEqual(place["place"], "Virtual")

    def test_recruiting_earns_its_place(self):
        place = campus.locate("", "", "Virtual Event with hiring partners.")
        self.assertTrue(campus.keep_event(place, "", "recruiting"))
        self.assertTrue(campus.keep_event(place, "", "firm"))

    def test_but_an_ordinary_webinar_does_not(self):
        place = campus.locate("", "", "This webinar covers legal writing.")
        self.assertFalse(campus.keep_event(place, "", "none"))
        self.assertFalse(campus.keep_event(place, "", "academic"))

    def test_a_real_room_still_wins_over_the_word_online(self):
        place = campus.locate("McDonough 201", "", "Also streamed online.")
        self.assertEqual(place["campus"], "home")
        self.assertEqual(place["place"], "McDonough 201")


class TestTheNortonGreen(unittest.TestCase):
    """The lawn outside McDonough, where the picnics happen.

    Unlisted, it fell through to the "Georgetown Law" branding printed on
    every OSL flyer, and an event on the grass was published as a building.
    """

    def test_it_resolves_to_the_capitol_campus(self):
        where = campus.locate("Eleanor Holmes Norton Green")
        self.assertEqual(where["campus"], "home")
        self.assertEqual(where["place"], "Eleanor Holmes Norton Green")

    def test_the_short_form_too(self):
        self.assertEqual(campus.locate("Norton Green")["campus"], "home")


class TestAnRsvpLinkIsNotAVenue(unittest.TestCase):
    """Firms take RSVPs on Flo Recruit for events in their own offices."""

    KIRKLAND = ("Kirkland 1L Reception Thursday, October 22nd 5:30-7:30pm "
                "Location: Kirkland & Ellis DC 1301 Pennsylvania Avenue NW "
                "RSVP Here (https://florecruit.com/v2/app/kirkland/"
                "registration/events/106999/register)")

    def test_a_florecruit_link_does_not_make_it_virtual(self):
        got = campus.locate("1301 Pennsylvania Avenue NW", "mail", self.KIRKLAND)
        self.assertEqual(got["campus"], "offcampus")

    def test_a_flo_recruit_chat_is_still_virtual(self):
        got = campus.locate("", "mail", "Meetings held virtually via Flo "
                                        "Recruit. Slots from 12 - 1:30 PM.")
        self.assertEqual(got["campus"], "virtual")

    def test_an_address_beats_a_passing_mention_of_zoom(self):
        got = campus.locate("1700 K Street NW", "mail",
                            "Open house at our office. Can't make it? Our "
                            "webinar next month is on Zoom.")
        self.assertEqual(got["campus"], "offcampus")

    def test_an_online_location_is_still_online(self):
        self.assertEqual(campus.locate("Online")["campus"], "virtual")


class TestTheApprovalStampIsNotAVenue(unittest.TestCase):
    """Every OSL flyer says "Approved by Georgetown Law", wherever it is."""

    FLYER = ("OUTLAW Office of Student Life Approved by Georgetown Law "
             "COVINGTON X OUTLAW HAPPY HOUR Join us for drinks and bites at "
             "The Dubliner (4 F St NW)")

    def test_an_address_in_the_location_wins(self):
        got = campus.locate("The Dubliner, 4 F St NW", "broadcast", self.FLYER)
        self.assertEqual(got["campus"], "offcampus")
        self.assertEqual(got["place"], "The Dubliner, 4 F St NW")

    def test_a_named_cafe_is_not_moved_into_the_law_school(self):
        got = campus.locate("Tatte Bakery & Cafe, Capitol Crossing", "broadcast",
                            "Approved by Georgetown Law. Coffee & Conversations "
                            "at Tatte Bakery & Cafe at Capitol Crossing.")
        self.assertNotEqual(got["place"], "Georgetown Law")

    def test_the_location_field_may_still_say_georgetown_law(self):
        self.assertEqual(campus.locate("GULC Campus (Room TBD)")["campus"],
                         "home")

    def test_a_real_room_in_the_body_still_counts(self):
        got = campus.locate("", "broadcast",
                            "Approved by Georgetown Law. GBM in McDonough 208.")
        self.assertEqual(got["campus"], "home")
        self.assertTrue(got["place"].startswith("McDonough"), got["place"])

    def test_a_location_line_in_the_body_still_counts(self):
        got = campus.locate("", "mail", "Freshfields 1L Panel & Reception "
                            "Location: GULC Campus (Room TBD)")
        self.assertEqual(got["campus"], "home")

    def test_the_schools_name_in_a_blurb_does_not_move_a_named_venue(self):
        got = campus.locate("Martin Luther King Jr. Memorial Library, Room 401-A",
                            "live", "A panel with Georgetown Law faculty.")
        self.assertNotEqual(got["campus"], "home")


class TestOneElevenMassAve(unittest.TestCase):
    """111 Massachusetts Ave NW is a Capitol Campus building, however spelled.

    Only the full spelling was known, so "111 Mass Ave 4th Floor Lounge"
    (Capitol Chapters, 15 Oct 2026) was published as "Off campus", and
    Freshfields' room LL201 and White & Case's Multipurpose Room were lost.
    """

    def test_every_spelling_is_ours(self):
        for loc in ("111 Mass Ave, Room LL201", "111 Massachusetts Ave NW",
                    "111 Mass Ave 4th Floor Lounge", "Mass 111 Multipurpose Room",
                    "111 Mass Lobby", "111 Mass Ave NW 2416"):
            self.assertEqual(campus.locate(loc)["campus"], "home", loc)

    def test_the_room_survives(self):
        for loc, room in (("111 Mass Ave, Room LL201", "LL201"),
                          ("111 Massachusetts Ave NW, Room LL201", "LL201"),
                          ("111 Mass Ave 4th Floor Lounge", "4th Floor"),
                          ("Mass 111 Multipurpose Room", "Multipurpose Room"),
                          ("111 Mass Ave NW 2416", "2416")):
            self.assertEqual(campus.locate(loc)["room"], room, loc)

    def test_the_building_number_is_not_a_room(self):
        self.assertNotEqual(campus.locate("Mass 111 Multipurpose Room")["room"], "111")

    def test_a_longer_number_on_the_same_avenue_is_not(self):
        self.assertEqual(campus.locate("1111 Massachusetts Ave NW")["campus"],
                         "offcampus")
