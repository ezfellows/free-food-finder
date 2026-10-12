"""Regression tests for the food scorer.

Cases here are the ones that actually bit us against live Georgetown data.
Run: python3 -m unittest discover -s tests -t .
"""
import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"
from score import score_event


class TestPositives(unittest.TestCase):
    def test_free_pizza_is_confirmed(self):
        v = score_event("Free Pizza Friday", "Come grab a slice.", "McDonough 200")
        self.assertEqual(v["tier"], "confirmed")
        self.assertTrue(v["has_food"])

    def test_lunch_provided(self):
        v = score_event("Fiscal Futures Series", "Lunch will be provided.", "Hotung 2000")
        self.assertEqual(v["tier"], "confirmed")
        self.assertEqual(v["meal"], "lunch")

    def test_catered_reception(self):
        v = score_event("HRI Annual Welcome Reception",
                        "Join us for a catered reception with food and drinks.",
                        "500 First Street NW")
        self.assertEqual(v["tier"], "confirmed")


class TestProximityGatedNegatives(unittest.TestCase):
    """The bug that motivated proximity gating: a book on sale is not food."""

    def test_book_for_purchase_does_not_veto_refreshments(self):
        v = score_event(
            "Disrupted City: A Book Talk",
            "Professor Lindqvist discusses his book. Copies will be available for "
            "purchase and signing after the talk. Refreshments will be provided.",
            "Mortara Center")
        self.assertEqual(v["tier"], "confirmed")
        self.assertTrue(v["has_food"])

    def test_bake_sale_is_vetoed(self):
        v = score_event("SBA Bake Sale", "Cookies and brownies for sale, $2 each.",
                        "McDonough Lobby")
        self.assertEqual(v["tier"], "unlikely")

    def test_brown_bag_is_vetoed(self):
        v = score_event("Brown Bag Lunch Series",
                        "Bring your own lunch and join the discussion.", "Hotung 1000")
        self.assertEqual(v["tier"], "unlikely")

    def test_registration_fee_does_not_kill_provided_lunch(self):
        v = score_event("Health Equity Conference",
                        "Registration fee is $25. Lunch will be served to all attendees.",
                        "111 Massachusetts Ave NW")
        self.assertTrue(v["has_food"])

    def test_explicit_no_food(self):
        v = score_event("Faculty Workshop", "Please note: no food will be provided.",
                        "McDonough 496")
        self.assertEqual(v["tier"], "unlikely")


class TestMealPicking(unittest.TestCase):
    def test_serving_verb_wins_over_first_mention(self):
        v = score_event("Annual Conference",
                        "Coffee available at 8am for early arrivals. "
                        "Lunch will be served at noon.", "Gewirz 12th Floor")
        self.assertEqual(v["meal"], "lunch")

    def test_coffee_hour_is_breakfast(self):
        v = score_event("Coffee Hour with Dean Castillo",
                        "Enjoy a complimentary beverage and treat.",
                        "The Whisk, McDonough Hall")
        self.assertEqual(v["meal"], "breakfast")


class TestVenue(unittest.TestCase):
    def test_law_building(self):
        self.assertEqual(score_event("x", "y", "McDonough Hall")["venue_class"], "home")

    def test_main_campus(self):
        self.assertEqual(score_event("x", "y", "Healy Hall")["venue_class"], "away")

    def test_group_slug_overrides(self):
        v = score_event("x", "y", "", group="studentaffairscapitolcampus")
        self.assertEqual(v["venue_class"], "home")

    def test_venue_does_not_change_food_verdict(self):
        """Distance is a separate axis from whether food exists."""
        law = score_event("Talk", "Free lunch provided.", "McDonough 200")
        main = score_event("Talk", "Free lunch provided.", "Healy Hall")
        self.assertEqual(law["confidence"], main["confidence"])
        self.assertEqual(law["tier"], main["tier"])


class TestOnline(unittest.TestCase):
    def test_virtual_with_no_room_is_unlikely(self):
        v = score_event("Webinar", "Join us on Zoom. Snacks not included.", "")
        self.assertEqual(v["tier"], "unlikely")

    def test_hybrid_with_room_keeps_food(self):
        v = score_event("Panel", "Attend in person or via Zoom. Lunch provided.",
                        "Hotung 2000")
        self.assertTrue(v["has_food"])


class TestFlags(unittest.TestCase):
    def test_rsvp_and_restriction(self):
        v = score_event("1L Mixer", "Free food! RSVP required. 1Ls only.", "Gewirz")
        self.assertTrue(v["rsvp_required"])
        self.assertTrue(v["restricted"])

    def test_garbage_input_does_not_raise(self):
        for bad in (None, "", "<script>x</script>", "\\x00", 12345):
            score_event(bad, bad, bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestMultipleMeals(unittest.TestCase):
    """Events often serve more than one meal; don't collapse to a single label."""

    def test_breakfast_and_lunch_both_reported(self):
        v = score_event("Building Bridges Conference",
                        "Networking throughout the day. Breakfast and lunch will be served.",
                        "111 Massachusetts Ave NW")
        self.assertEqual(v["meals"], ["breakfast", "lunch"])
        self.assertEqual(v["meal"], "breakfast")   # earliest in the day

    def test_single_meal_still_scalar(self):
        v = score_event("Panel", "Lunch will be provided.", "Hotung 2000")
        self.assertEqual(v["meals"], ["lunch"])

    def test_no_meal_signal(self):
        self.assertEqual(score_event("Moot Court Practice", "Bring notes.", "")["meals"], [])


class TestVenuePunctuation(unittest.TestCase):
    """Feeds spell the same building several ways; punctuation must not matter."""

    def test_possessive_apostrophe_variants(self):
        for spelling in ("Regent's Hall - Fourth Floor Patio", "Regents Hall",
                         "Regent’s Hall", "Regents' Hall"):
            self.assertEqual(score_event("Planting Party", "Bagels provided.",
                                         spelling)["venue_class"], "away", spelling)

    def test_law_building_unaffected(self):
        self.assertEqual(score_event("x", "y", "The Whisk, McDonough Hall")["venue_class"],
                         "home")


class TestVendorsAndDrinks(unittest.TestCase):
    """Flyers name the vendor and the drink, not the abstract category."""

    def confirmed(self, text):
        return score_event(title="Talk", description=text,
                           location="McDonough 141")["tier"]

    def test_drinks_that_are_a_whole_offering(self):
        for text in ("free bubble tea", "free boba", "free smoothies",
                     "lemonade will be served"):
            self.assertNotEqual(self.confirmed(text), "unlikely", text)

    def test_a_vendor_name_is_a_food_word(self):
        for text in ("Chipotle Provided", "Cava catered",
                     "free Sweetgreen", "Insomnia Cookies provided"):
            self.assertNotEqual(self.confirmed(text), "unlikely", text)

    def test_one_modifier_may_sit_between_free_and_the_food(self):
        for text in ("free Thai lunch", "free hot breakfast", "free bubble tea"):
            self.assertNotEqual(self.confirmed(text), "unlikely", text)

    def test_but_free_still_has_to_be_about_food(self):
        # The modifier slot must not turn every "free" into a free meal.
        for text in ("free parking", "free admission", "free tote bags",
                     "gluten free options", "free and open to the public",
                     "Copies available for purchase"):
            self.assertEqual(self.confirmed(text), "unlikely", text)


class TestADenialIsNotAWeakSignal(unittest.TestCase):
    """The negation vocabulary must match the promise vocabulary.

    `NEG_ABSOLUTE` knew only the literal word "food", so "no lunch provided"
    scored exactly what "Lunch will be provided" scored -- confirmed, 100 --
    on a site whose whole job is saying whether there is free food.
    """

    def tier(self, text, title="Panel"):
        return score_event(title=title, description=text, location="")["tier"]

    def test_a_named_meal_can_be_denied(self):
        for text in ("no lunch provided", "No dinner will be served",
                     "we will not be providing lunch", "no pizza",
                     "no snacks", "no coffee", "without breakfast"):
            self.assertEqual(self.tier(text), "unlikely", text)

    def test_the_original_wording_still_works(self):
        self.assertEqual(self.tier("no food provided"), "unlikely")
        self.assertEqual(self.tier("No refreshments will be served"), "unlikely")

    def test_a_promise_is_untouched(self):
        self.assertEqual(self.tier("Lunch will be provided"), "confirmed")
        self.assertEqual(self.tier("free pizza and boba"), "confirmed")
        self.assertEqual(self.tier("Dinner will be served"), "confirmed")

    def test_a_denial_outranks_arithmetic(self):
        """A long enough flyer used to outvote its own "no lunch" sentence."""
        padded = ("Join us for our annual celebration with speakers and a "
                  "reception and a buffet of conversation. " * 3
                  + " Please note: no lunch will be provided.")
        self.assertEqual(self.tier(padded), "unlikely")

    def test_cost_signals_are_not_denials(self):
        """"Cash bar" says food costs money, not that there is none."""
        self.assertNotEqual(
            score_event(title="Reception", description="free pizza, cash bar",
                        location="")["tier"], "unlikely")


class TestTheTitleSeamIsNotSearchable(unittest.TestCase):
    """The title is scored twice, and the join between the copies was a target.

    Every rule is built from `\\s+` runs, so a bare space let a pattern straddle
    the seam: "Lunch will NOT be served" produced the phrase "served Lunch"
    across its own two copies and published as confirmed, quoting evidence that
    appears in no source.
    """

    def test_a_negated_title_does_not_self_match(self):
        for title in ("Lunch will NOT be served", "Dinner is NOT provided"):
            got = score_event(title=title, description="", location="")
            self.assertEqual(got["tier"], "unlikely", title)

    def test_no_signal_quotes_a_phrase_spanning_the_seam(self):
        got = score_event(title="Lunch will NOT be served", description="",
                          location="")
        for signal in got["signals"]:
            self.assertNotIn("served Lunch", signal.get("phrase", ""))

    def test_a_real_title_still_scores(self):
        self.assertEqual(
            score_event(title="Free Pizza Friday", description="",
                        location="")["tier"], "confirmed")


class TestPlainStatementsOfFood(unittest.TestCase):
    """The words in the other order.

    Every rule read "<food> <verb>" -- "lunch will be provided", "food is
    served". Said the other way round, "there will be food", nothing matched
    at all: the Antitrust Association's Ropes & Gray panel promised food in
    those exact words and was published as having none.
    """

    def verdict(self, text):
        return score_event(title="Panel", description=text, location="")

    def test_there_will_be_food(self):
        for text in ("Of course, there will be food.", "There will be food",
                     "there will be pizza", "There will be light refreshments",
                     "There are snacks"):
            self.assertIn(self.verdict(text)["tier"],
                          ("confirmed", "likely"), text)

    def test_the_same_sentence_negated(self):
        for text in ("There will not be food", "there will be no food",
                     "There will not be any refreshments"):
            self.assertEqual(self.verdict(text)["tier"], "unlikely", text)

    def test_a_meal_in_a_title_is_still_not_a_promise(self):
        """"Lunch with the Dean" names the hour, not a free lunch."""
        self.assertEqual(self.verdict("Lunch with the Dean")["tier"], "unlikely")


class TestInvitationsToEat(unittest.TestCase):
    """A flyer invites you to eat; it does not announce that food exists.

    "LALSA Asado. Enjoy great food and celebrate our community" scored zero,
    on a site whose whole job is finding the asado.
    """

    def tier(self, text):
        return score_event(title="Event", description=text, location="")["tier"]

    def test_an_invitation_counts(self):
        for text in ("LALSA Asado. Enjoy great food and celebrate our community.",
                     "Enjoy great food", "Join us for pizza",
                     "Help yourself to snacks"):
            self.assertIn(self.tier(text), ("confirmed", "likely"), text)

    def test_the_same_verbs_without_food_do_not(self):
        for text in ("Enjoy the panel discussion",
                     "Join us for a discussion of antitrust law",
                     "Join us for the moot court final"):
            self.assertEqual(self.tier(text), "unlikely", text)

    def test_a_denial_still_wins(self):
        self.assertEqual(self.tier("no food to enjoy"), "unlikely")


class TestFoodNamedRatherThanServed(unittest.TestCase):
    """Party food announced by name, and food introduced by "featuring".

    APALSA's mentorship social read "featuring YUMMY food (sliders, chips &
    guac, chicken skewers, & more)" and scored unlikely: no serving verb, and
    none of those dishes were in the vocabulary.
    """

    def tier(self, text):
        return score_event(title="Social", description=text, location="")["tier"]

    def test_featuring_food(self):
        for text in ("featuring YUMMY food (sliders, chips & guac, "
                     "chicken skewers, & more)",
                     "a reception featuring light snacks",
                     "featuring free pizza"):
            self.assertIn(self.tier(text), ("confirmed", "likely"), text)

    def test_featuring_something_that_is_not_food(self):
        for text in ("featuring a panel of attorneys",
                     "featuring speakers from three firms",
                     "featuring a documentary screening"):
            self.assertEqual(self.tier(text), "unlikely", text)

    def test_party_food_by_name(self):
        # A conjunction between the dish and the verb is a separate limit:
        # "hummus and pita available" needs the word nearest the verb to be
        # food, and "pita" is not vocabulary. Not chased here.
        for text in ("sliders and chicken skewers will be served",
                     "chips and guac provided", "hummus provided"):
            self.assertIn(self.tier(text), ("confirmed", "likely"), text)

    def test_the_singular_of_a_plural_already_known(self):
        """"pastries provided" worked; "pastry provided" did not."""
        self.assertIn(self.tier("Coffee or pastry provided by APALSA"),
                      ("confirmed", "likely"))

    def test_a_denial_still_wins(self):
        self.assertEqual(self.tier("no food featuring anything"), "unlikely")


class TestHybridIsNotOnlineOnly(unittest.TestCase):
    """NOLA's flyer said "FREE FOOD!" and "Zoom / In Person".

    With no room found in the text, "Zoom" alone made it online-only and the
    food was thrown away -- for an event with pizza in McDonough 208.
    """

    def tier(self, text, location=""):
        return score_event(title="Meeting", description=text,
                           location=location)["tier"]

    def test_hybrid_wording_keeps_the_food(self):
        for text in ("FREE FOOD! Zoom or in person.", "FREE FOOD! Zoom / In Person",
                     "Free pizza for all. Hybrid event.",
                     "Free pizza. Join in-person or on Zoom."):
            self.assertEqual(self.tier(text), "confirmed", text)

    def test_online_only_still_loses_it(self):
        self.assertEqual(self.tier("FREE FOOD! Join on Zoom."), "unlikely")
        self.assertEqual(self.tier("Free pizza. Online only."), "unlikely")


class TestTheMealIsTheEvent(unittest.TestCase):
    """"Dinner with Paul, Weiss" is a dinner, and scored as having no food.

    Every rule wanted a serving verb beside the food word. An event named for
    its meal has none, because the name is the promise.
    """

    def tier(self, title, text=""):
        return score_event(title=title, description=text)["tier"]

    def test_named_meals(self):
        for title in ("Dinner with Paul, Weiss", "Paul, Weiss 1L Dinner",
                      "BLSA Alumni Brunch", "Breakfast with the Dean",
                      "O'Neill Institute Fall Brunch & Hike"):
            self.assertEqual(self.tier(title), "likely", title)

    def test_a_meal_word_in_passing_is_not_one(self):
        for title in ("Ask me about my lunch with Paul",
                      "Dinner reservations closed", "Brunch recipes talk"):
            self.assertEqual(self.tier(title), "unlikely", title)

    def test_being_told_to_eat(self):
        """PPP: "Have a drink and eat some food (on us...)"."""
        self.assertEqual(
            self.tier("Networking Happy Hour",
                      "Have a drink and eat some food (on us, because we're "
                      "providing a large tab)"), "confirmed")
