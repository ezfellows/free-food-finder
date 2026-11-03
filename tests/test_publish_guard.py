"""The page builder substitutes placeholders by string replacement.

If a placeholder name is ever spelled out inside the page's own JavaScript --
in code or even in a comment -- the build splices the entire event feed into
the middle of the script and the page dies with a syntax error. It happened
once; this stops it happening again.
"""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"
import publish

PLACEHOLDERS = ("__EVENTS__", "__SUBMITTED__", "__TEMPLATE__")


def app_script():
    """The page's own <script> block, excluding the JSON data tags."""
    template = publish.TEMPLATE
    return template[template.find("<script>"):]


class TestPlaceholdersDoNotLeakIntoCode(unittest.TestCase):
    def test_no_placeholder_appears_in_the_app_script(self):
        script = app_script()
        for name in PLACEHOLDERS:
            self.assertNotIn(name, script,
                             f"{name} written out inside the page script")

    def test_each_placeholder_appears_exactly_once_in_the_template(self):
        for name in PLACEHOLDERS:
            self.assertEqual(publish.TEMPLATE.count(name), 1, name)

    def test_built_page_has_no_placeholders_left(self):
        import store
        import tempfile
        conn = store.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
        built = publish.build(conn, days=1)
        conn.close()
        for name in PLACEHOLDERS:
            self.assertNotIn(name, built["html"], name)

    def test_self_template_keeps_its_placeholders(self):
        """The embedded copy must stay a template, or self-publishing breaks."""
        import base64
        import store
        import tempfile
        conn = store.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
        built = publish.build(conn, days=1)
        conn.close()
        blob = re.search(r'id="self-template">([A-Za-z0-9+/=]+)<',
                         built["html"]).group(1)
        template = base64.b64decode(blob).decode("utf-8")
        self.assertTrue(template.startswith("<!doctype html>"))
        for name in PLACEHOLDERS:
            self.assertIn(name, template, name)
        self.assertNotIn("<!doctype html>", template[10:],
                         "template must not nest a second document")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestSubmissionsSurviveARebuild(unittest.TestCase):
    """Submissions live inside the published page. A rebuild that does not
    carry them across deletes everything anyone has posted, so the extraction
    has to work on a real built page and fail safe on anything else."""

    def built_page(self, submitted):
        import store
        import tempfile
        conn = store.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
        html = publish.build(conn, days=1, submitted=submitted)["html"]
        conn.close()
        path = os.path.join(tempfile.mkdtemp(), "live.html")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)
        return path

    def test_round_trips_through_a_real_page(self):
        state = {"posts": [{"id": "a1", "by": "Dana R", "title": "Bagels",
                            "start": "2099-01-01T12:00", "place": "Hotung 2001",
                            "note": "", "link": "", "at": "2026-09-16T10:00:00"}],
                 "blocked": ["spammer"]}
        carried = publish.carried_submissions(self.built_page(state))
        self.assertEqual(carried["posts"], state["posts"])
        self.assertEqual(carried["blocked"], ["spammer"])

    def test_survives_json_that_contains_script_like_text(self):
        state = {"posts": [{"id": "a2", "by": "x", "title": "</script> & <b>",
                            "start": "2099-01-01T12:00", "place": "", "note": "",
                            "link": "", "at": ""}], "blocked": []}
        carried = publish.carried_submissions(self.built_page(state))
        self.assertEqual(len(carried["posts"]), 1)

    def test_missing_or_broken_input_starts_empty_rather_than_failing(self):
        for path in (None, "/nonexistent/live.html"):
            self.assertEqual(publish.carried_submissions(path),
                             {"posts": [], "blocked": []})

    def test_a_page_without_the_block_starts_empty(self):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "other.html")
        with open(path, "w") as fh:
            fh.write("<html><body>not our page</body></html>")
        self.assertEqual(publish.carried_submissions(path)["posts"], [])


class TestKeepingTheLivePage(unittest.TestCase):
    """`./fff live`: the copy of the published page a rebuild reads posts
    from. Unlike reading it, keeping it fails loudly: a wrong file must never
    replace the last good copy."""

    built_page = TestSubmissionsSurviveARebuild.built_page

    def setUp(self):
        import tempfile
        self.dest = os.path.join(tempfile.mkdtemp(), "live.html")
        with open(self.dest, "w", encoding="utf-8") as fh:
            fh.write("the copy kept last time")

    def test_the_page_is_kept_with_its_counts(self):
        state = {"posts": [{"id": "a3", "by": "x", "title": "Bagels",
                            "start": "2099-01-01T12:00", "place": "", "note": "",
                            "link": "", "at": ""}], "blocked": ["spammer"]}
        source = self.built_page(state)
        got = publish.save_live(source, self.dest)
        self.assertEqual((got["posts"], got["blocked"]), (1, 1))
        with open(source, encoding="utf-8") as a, open(self.dest, encoding="utf-8") as b:
            self.assertEqual(a.read(), b.read())
        self.assertEqual(publish.carried_submissions(self.dest)["posts"], state["posts"])
        self.assertFalse(os.path.exists(self.dest + ".part"))

    def test_anything_else_is_refused_and_the_last_copy_kept(self):
        import tempfile
        for text in ("<html><body>not our page</body></html>",
                     '<script type="application/json" id="submitted-data">{oops</script>'):
            source = os.path.join(tempfile.mkdtemp(), "other.html")
            with open(source, "w") as fh:
                fh.write(text)
            with self.assertRaises(ValueError):
                publish.save_live(source, self.dest)
            with open(self.dest, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "the copy kept last time")
            self.assertFalse(os.path.exists(self.dest + ".part"))

    def test_the_command_keeps_and_refuses(self):
        """Pointed at a scratch copy (FFF_LIVE), so that even a broken
        refusal can never overwrite your real data/live.html."""
        import subprocess
        import tempfile
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ, FFF_LIVE=self.dest)

        def live(*args):
            return subprocess.run([os.path.join(root, "fff"), "live", *args],
                                  capture_output=True, text=True, env=env)

        source = os.path.join(tempfile.mkdtemp(), "other.html")
        with open(source, "w") as fh:
            fh.write("<html><body>not our page</body></html>")
        for args in ([source], []):
            out = live(*args)
            self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("not saved", live(source).stdout)
        with open(self.dest, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "the copy kept last time")
        out = live(self.built_page({"posts": [], "blocked": []}))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(json.loads(out.stdout)["saved"], self.dest)


class TestScriptIsNotClosedEarly(unittest.TestCase):
    """A closing script tag anywhere inside a script ends it -- comments too.

    The HTML tokenizer does not parse JavaScript. It scans for the closing
    tag, so one written inside a string or a `//` comment ends the element
    there and silently discards the rest of the program. That is not a syntax
    error you see in Python; it is a blank page in a browser.
    """

    def closer(self):
        # Assembled, so this test file cannot trip the check it performs.
        return "</" + "script>"

    def test_the_app_script_contains_no_closing_tag_of_its_own(self):
        script = publish.TEMPLATE[publish.TEMPLATE.find("<script>"):]
        body = script[: script.find(self.closer())]
        self.assertNotIn(self.closer(), body)

    def test_the_built_page_has_one_closer_per_script(self):
        html = publish.page([], "1 January 2026", {"posts": [], "blocked": []})
        opens = len(re.findall(r"<script[\s>]", html))
        closes = html.count(self.closer())
        self.assertEqual(opens, closes,
                         "unbalanced script tags: a stray closing tag is "
                         "ending a block early")

    def test_every_script_block_is_balanced(self):
        """Each script's braces close -- proof nothing was truncated."""
        html = publish.page([], "1 January 2026", {"posts": [], "blocked": []})
        for m in re.finditer(r"<script(?![^>]*type=\"(?:application/json|"
                             r"text/plain)\")[^>]*>(.*?)" + self.closer(),
                             html, re.S):
            js = m.group(1)
            stripped = re.sub(r"//[^\n]*", "", js)
            stripped = re.sub(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'",
                              '""', stripped)
            self.assertEqual(stripped.count("{"), stripped.count("}"),
                             "unbalanced braces -- script was cut short")


class TestTheFlyerToggle(unittest.TestCase):
    """The page-wide switch, and the single flyer inside "Why it is listed".

    Both exist on purpose. The toggle brings back all 22 posters at once; with
    it off, one flyer is still one tap away on the event itself. The pair is
    easy to half-break, because the flyer is rendered in two places and the
    toggle is wired through six -- and a stray `render()` line pointing at a
    button that is not there throws and blanks the whole page.
    """

    def page(self):
        return publish.page([], "1 January 2026", {"posts": [], "blocked": []})

    def test_the_switch_is_wired_end_to_end(self):
        for name in ("flyerToggle", "state.flyers", "docket-flyers"):
            self.assertIn(name, publish.TEMPLATE, f"{name} missing")

    def test_the_built_page_has_the_button(self):
        self.assertIn('id="flyerToggle"', self.page())

    def test_a_flyer_is_never_rendered_twice_on_one_card(self):
        # The card shows it when the toggle is on; `why()` shows it when the
        # toggle is off. Both unguarded would double it up.
        self.assertIn("e.flyer && state.flyers", publish.TEMPLATE)
        self.assertIn("e.flyer && !state.flyers", publish.TEMPLATE)

class TestFlyerIsAdvertised(unittest.TestCase):
    """With the Flyers toggle off the image is inside "Why it is listed",
    so without a tag nothing tells a reader there is one to check."""

    def test_an_event_with_a_flyer_is_tagged(self):
        self.assertIn('if (e.flyer) out.push(["verified", "Flyer supplied"])',
                      publish.TEMPLATE)

    def test_and_the_panel_says_where_it_is(self):
        script = publish.TEMPLATE
        self.assertIn("<dt>Flyer</dt>", script)
        self.assertIn("shown on the card", script)
        self.assertIn("shown below", script)


class TestOrgAndFirmTags(unittest.TestCase):
    def test_the_org_is_tagged_not_only_shown_in_the_location(self):
        self.assertIn('out.push(["org", e.org])', publish.TEMPLATE)

    def test_a_firm_is_styled_apart_from_generic_networking(self):
        """A firm name is what a reader hunts for; "Lawyers present" is not."""
        script = publish.TEMPLATE
        self.assertIn('out.push(["firm", f])', script)
        self.assertIn(".tag.firm{", script)
        self.assertIn(".tag.org{", script)

    def test_practice_areas_are_no_longer_capped(self):
        # With a filter that can select any of them, showing the first two
        # made the third look like it was missing.
        self.assertNotIn("(e.areas || []).slice(0,2)", publish.TEMPLATE)


class TestTagFilter(unittest.TestCase):
    def page(self):
        return publish.page([], "1 January 2026", {"posts": [], "blocked": []})

    def test_the_menu_is_in_the_page(self):
        html = self.page()
        self.assertIn('id="facets"', html)
        self.assertIn('id="facetBody"', html)

    def test_every_group_is_declared(self):
        script = publish.TEMPLATE
        for group in ("Organisation", "Firm", "Practice area", "Food",
                      "Requirements"):
            self.assertIn(group, script)

    def test_any_within_a_group_all_across_them(self):
        """Ticking a second firm widens; ticking an area as well narrows."""
        script = publish.TEMPLATE
        window = script[script.index("function matchesFacets"):]
        self.assertIn("FACETS.every(", window[:600])
        self.assertIn(".some(", window[:600])

    def test_the_filter_is_in_visible_so_every_view_follows(self):
        window = publish.TEMPLATE[publish.TEMPLATE.index("function visible()"):]
        self.assertIn("!matchesFacets(e)", window[:1400])

    def test_counts_ignore_the_facet_filter(self):
        """Otherwise ticking a box makes its own count vanish."""
        script = publish.TEMPLATE
        self.assertIn("function beforeFacets()", script)
        self.assertIn("renderFacets(beforeFacets())", script)

    def test_selections_are_serialised_as_a_real_array(self):
        # A Set has no `length`, so the array-like path returns [] and every
        # selection is silently lost on reload.
        script = publish.TEMPLATE
        self.assertIn("Array.from(state.facets", script)
        self.assertNotIn("[].slice.call(state.facets", script)

    def test_the_tag_vocabulary_is_defined_before_the_state_uses_it(self):
        """`readFacets()` runs inside the state literal; a later `var` is
        undefined and the whole page fails to render."""
        script = publish.TEMPLATE
        self.assertLess(script.index("var FACETS = ["),
                        script.index("facets: readFacets()"))


class TestCalendarShowsTimes(unittest.TestCase):
    def test_pips_carry_a_time_in_both_forms(self):
        # Both are rendered and CSS picks one, so rotating a phone does not
        # need a re-render to widen.
        self.assertIn('class="at long"', publish.TEMPLATE)
        self.assertIn('class="at short"', publish.TEMPLATE)
        self.assertIn("fmtTimeShort", publish.TEMPLATE)

    def test_days_are_stepped_in_collected_order_not_sorted_as_strings(self):
        """dayKey is not zero-padded: "2026-9-7" sorts after "2026-9-17"."""
        self.assertNotIn("Object.keys(byDay).sort()", publish.TEMPLATE)
        self.assertIn("order.indexOf(state.day)", publish.TEMPLATE)

    def test_the_visible_list_is_sorted(self):
        # Submissions are concatenated onto the end of the sorted feed.
        self.assertIn("return when(a) - when(b);", publish.TEMPLATE)


class TestStudentSubmissions(unittest.TestCase):
    """Where the control sits, and what a post says about itself."""

    def page(self):
        return publish.page([], "1 January 2026", {"posts": [], "blocked": []})

    def test_the_submit_control_is_in_the_masthead(self):
        """At the bottom, under the docket and the calendar, nobody finds it."""
        html = self.page()
        masthead = html[html.index('<header class="masthead">'):
                        html.index("</header>")]
        self.assertIn('id="submitBar"', masthead)
        self.assertIn('id="openSubmit"', masthead)
        self.assertIn('id="openModerate"', masthead)

    def test_and_above_the_lead_card(self):
        html = self.page()
        self.assertLess(html.index('id="submitBar"'), html.index('id="lead"'))

    def test_the_name_field_says_it_is_public(self):
        self.assertIn("Shown publicly on every post you make.", self.page())

    def test_a_post_is_attributed_not_credited_as_host(self):
        """`org` is where a HOST goes; a submitter there reads as one."""
        script = publish.TEMPLATE
        self.assertIn("posted by ", script)
        self.assertIn('org: "",', script)

    def test_verification_is_derived_from_the_link(self):
        script = publish.TEMPLATE
        self.assertIn("verified: !!post.link", script)
        self.assertIn("Verified \\u2014 link", script)
        self.assertIn("Unverified \\u2014 no proof", script)

    def test_proof_reads_as_proof_not_as_rsvp(self):
        self.assertIn('e.submitted ? "See proof', publish.TEMPLATE)

    def test_the_hide_toggle_exists_and_defaults_to_showing(self):
        html = self.page()
        self.assertIn('id="postToggle" aria-pressed="true"', html)
        self.assertIn('read(KEY_POSTS, "1")', publish.TEMPLATE)

    def test_a_reader_who_cannot_post_is_told_and_the_control_goes(self):
        """`use()` resolves for readers, so the button appears for them too.

        Reading the page and being able to change it are different grants and
        only the second is testable by trying, so the failure has to be
        handled rather than prevented.
        """
        script = publish.TEMPLATE
        self.assertIn('code === "not_writer" || code === "not_granted"', script)
        # The contract calls these permanent for the view: never retry, hide
        # the affordance. capability_disabled is how a host that cannot run
        # the capability reports in.
        for code in ("not_declared", "capability_disabled",
                     "capability_removed", "consent_required"):
            self.assertIn(code, script)
        self.assertIn("bar.hidden = true", script)
        self.assertNotIn("never sees the controls", script)

    def test_hiding_applies_everywhere_not_just_the_list(self):
        """In `visible`, so the calendar and the lead card follow suit."""
        script = publish.TEMPLATE
        window = script[script.index("function visible()"):]
        self.assertIn("e.submitted && !state.posts", window[:1200])


class TestTimesAreShownWhole(unittest.TestCase):
    """Both ends of an event, everywhere a time appears.

    A start alone answers "can I get there". The end answers "is it worth
    going, with a 4:45 class" -- which is the question that decides it.
    """

    def test_the_span_formatter_exists(self):
        self.assertIn("function fmtSpan", app_script())

    def test_every_time_display_uses_it(self):
        """The rail, the lead card and the calendar, not just one of them.

        Each was a separate call to `fmtTime`, and fixing only the docket is
        the version of this change that looks done and is not.
        """
        script = app_script()
        self.assertIn('class="rail">\' + esc(fmtSpan(e))', script)
        self.assertIn('", " + esc(fmtSpan(e))', script)      # "Next up"
        self.assertIn('esc(fmtSpan(e)) + "</span>"', script)  # calendar pip

    def test_a_shared_meridiem_is_printed_once(self):
        self.assertIn('sameHalf', app_script())

    def test_an_end_that_precedes_its_start_is_ignored(self):
        """Bad data must not render "6:00pm-5:00pm"."""
        self.assertIn("end <= start", app_script())

    def test_a_source_disagreement_reaches_the_why_panel(self):
        script = app_script()
        self.assertIn("e.timeNote", script)
        self.assertIn("<dt>Times</dt>", script)

    def test_the_disagreement_is_styled_as_a_caution(self):
        self.assertIn(".why dd.dispute", publish.TEMPLATE)


class TestTheVirtualLens(unittest.TestCase):
    """Events with nothing to walk to, on their own filter."""

    def test_the_control_exists(self):
        self.assertIn('data-lens="virtual"', publish.TEMPLATE)

    def test_both_filter_sites_apply_it(self):
        """`beforeFacets` feeds the tag counts, `visible` feeds everything the
        reader sees. A lens applied to only one makes the counts disagree with
        the list -- the bug the student-posts toggle already had once."""
        self.assertEqual(app_script().count('state.lens === "virtual"'), 2)

    def test_a_stored_lens_is_checked_against_the_ones_that_exist(self):
        """A reader who last visited before this lens existed still has a
        valid lens; one who selects it must keep it across a reload."""
        self.assertIn('["all", "food", "net", "reception", "virtual"]', app_script())

    def test_the_lens_bar_wraps(self):
        """Six controls do not fit a phone on one row.

        `.bar` wrapping its own children does nothing for the buttons inside
        `.lenses`, so adding the sixth clipped "Student posts" off the right
        edge instead of moving it to a second row.
        """
        css = publish.TEMPLATE[:publish.TEMPLATE.find("<script>")]
        rule = [line for line in css.splitlines()
                if line.startswith(".lenses{")][0]
        self.assertIn("flex-wrap:wrap", rule)

    def test_it_reads_a_flag_not_the_place_wording(self):
        """`place` says "Virtual" because this code chose that word. Matching
        on it would break silently the day the label changes."""
        script = app_script()
        self.assertIn("return e.virtual", script)
        self.assertNotIn('place === "Virtual"', script)


class TestReportedFoodSaysSoOutLoud(unittest.TestCase):
    """A food verdict a person supplied has no phrase in the source to quote.

    The whole page rests on quoting the wording behind a call. When the call
    comes from word of mouth instead, the reader is told that, rather than
    being shown a confident tag with nothing behind it.
    """

    def test_the_note_reaches_the_why_panel(self):
        script = app_script()
        self.assertIn("e.foodNote", script)
        self.assertIn("<dt>Reported</dt>", script)


class TestTheRsvpTracker(unittest.TestCase):
    """The list of events that ask for a sign-up, beside the docket."""

    def page(self):
        return publish.page([], "1 January 2026", {"posts": [], "blocked": []})

    def css(self):
        return publish.TEMPLATE[:publish.TEMPLATE.find("<script>")]

    def test_it_is_on_the_page_with_both_tabs(self):
        html = self.page()
        self.assertIn('<section id="rsvps" class="rsvps"', html)
        self.assertIn('aria-controls="rsvpBody"', html)
        self.assertIn('data-track="firm"', html)
        self.assertIn('data-track="school"', html)
        self.assertIn('id="rsvpList"', html)

    def test_it_sits_between_the_next_up_card_and_the_docket(self):
        """On a phone, that is where the folding panel goes."""
        html = self.page()
        self.assertLess(html.index('id="lead"'), html.index('id="rsvps"'))
        self.assertLess(html.index('id="rsvps"'), html.index('id="views"'))

    def test_on_a_wide_screen_it_moves_beside_the_docket_and_stays(self):
        css = self.css()
        wide = css[css.index("@media (min-width:960px)"):]
        self.assertIn("grid-template-columns:minmax(0,760px) 300px", wide)
        self.assertIn(".wrap > .rsvps{grid-column:2", wide)
        self.assertIn("position:sticky", wide)
        self.assertIn("overflow:auto", wide)

    def test_beside_the_docket_it_never_waits_on_the_script(self):
        """A script told of a widening can miss it -- a hidden or throttled
        tab is never told -- and the sidebar collapsed to nothing, with no
        way to open it. So the stylesheet alone shows it there."""
        css = self.css()
        wide = css[css.index("@media (min-width:960px)"):]
        self.assertIn("#rsvps .rsvpbody{display:block", wide)
        self.assertIn(".rsvphead{display:none}", wide)
        self.assertIn(".rsvps:not(.open) .rsvpbody{display:none}", css)
        self.assertNotIn("min-width:960px", app_script())

    def test_a_short_docket_never_opens_gaps_between_its_sections(self):
        """The tracker spans every row; only the last may grow to fit it."""
        wide = self.css()[self.css().index("@media (min-width:960px)"):]
        self.assertIn("grid-template-rows:repeat(9,auto) 1fr", wide)
        self.assertIn("grid-row:2 / -1", wide)

    def test_the_folding_panel_and_the_tab_are_remembered(self):
        script = app_script()
        self.assertIn('read(KEY_RSVP_OPEN, "0")', script)
        self.assertIn("write(KEY_RSVP_OPEN", script)
        self.assertIn('read(KEY_RSVP_TAB, "firm")', script)
        self.assertIn("write(KEY_RSVP_TAB", script)
        self.assertIn('["firm", "school"].indexOf(state.track)', script)

    def test_ticks_go_through_the_guarded_storage_helpers(self):
        """Storage can throw (a private window, blocked site data). `read`
        and `write` already catch it; a bare localStorage call would not."""
        script = app_script()
        self.assertIn('read(KEY_RSVPED, "[]")', script)
        self.assertIn("write(KEY_RSVPED", script)
        self.assertEqual(script.count("localStorage."), 2)

    def test_ticks_for_events_no_longer_listed_are_dropped(self):
        self.assertIn("listed.has(id)", app_script())

    def test_student_posts_are_left_out(self):
        """No announcement, so no deadline to read: the rows come from the
        scraped feed, not from `everything()`."""
        script = app_script()
        window = script[script.index("function rsvpRows()"):]
        self.assertIn("return EVENTS.filter(", window[:400])

    def test_the_countdown_is_worked_out_as_the_page_is_read(self):
        script = app_script()
        self.assertIn("renderLead(visible()); renderRsvps();", script)
        self.assertIn("var now = Date.now();",
                      script[script.index("function rsvpRows()"):][:200])

    def test_a_redraw_with_nothing_new_leaves_the_rows_alone(self):
        """Replacing them every minute would take the keyboard focus away."""
        self.assertIn("if (html !== lastRsvps)", app_script())

    def test_the_footer_says_what_the_list_is(self):
        self.assertIn("The RSVP list holds every event whose announcement asks "
                      "you to", self.page())


class TestThePerkTag(unittest.TestCase):
    def test_it_is_styled_in_every_theme(self):
        css = publish.TEMPLATE[:publish.TEMPLATE.find("<script>")]
        self.assertIn(".tag.perk{", css)
        # Light, the system's dark, and a dark chosen on the page.
        self.assertEqual(css.count("--perk:"), 3)
        self.assertEqual(css.count("--perk-bg:"), 3)

    def test_cards_carry_it_and_the_filter_offers_it(self):
        script = app_script()
        self.assertIn('out.push(["perk", perkText(p)])', script)
        self.assertIn('["perk", "Perks",', script)

    def test_the_why_panel_quotes_it(self):
        self.assertIn("<dt>Perk</dt>", app_script())

    def test_the_legend_explains_it(self):
        self.assertIn('<span class="tag perk">', publish.DEFAULT_ABOUT)

    def test_the_page_is_handed_what_it_needs(self):
        import json
        data = json.loads(publish.payload([{
            "id": "e1", "title": "Reception", "starts_at": "2099-01-01T18:00:00",
            "ends_at": None, "all_day": 0, "rsvp_needed": True,
            "rsvp_closed": False, "track": "firm", "perk_by": "2098-12-30T18:00",
            "perks": [{"label": "Lyft voucher", "with_rsvp": True,
                       "lead_hours": 48, "phrase": "Lyft codes"}]}])
            .replace("<\\/", "</"))[0]
        self.assertTrue(data["rsvpNeeded"])
        self.assertFalse(data["rsvpClosed"])
        self.assertEqual(data["track"], "firm")
        self.assertEqual(data["perkBy"], "2098-12-30T18:00")
        self.assertEqual(data["perks"], [{"label": "Lyft voucher", "withRsvp": True,
                                          "lead": 48, "phrase": "Lyft codes"}])
        self.assertNotIn("desc", data)


class TestTheMonthFitsTheScreen(unittest.TestCase):
    def test_a_long_title_cannot_widen_its_day(self):
        """A bare 1fr will not narrow below its content: one title on one
        line held its day's column 535px wide and pushed the month 2,000px
        across a 900px window."""
        css = publish.TEMPLATE[:publish.TEMPLATE.find("<script>")]
        rule = [line for line in css.splitlines() if line.startswith(".grid{")][0]
        self.assertIn("repeat(7,minmax(0,1fr))", rule)


class TestReceptions(unittest.TestCase):
    """A tag and a filter of their own, on the docket and in the RSVP list."""

    def page(self):
        return publish.page([], "1 January 2026", {"posts": [], "blocked": []})

    def test_the_docket_has_a_receptions_filter(self):
        self.assertIn('data-lens="reception"', self.page())

    def test_both_filter_sites_apply_it(self):
        """The tag counts and the list must agree, as with every lens."""
        self.assertEqual(app_script().count('state.lens === "reception"'), 2)

    def test_the_tag_is_first_on_the_card(self):
        script = app_script()
        window = script[script.index("function tags(e)"):]
        self.assertLess(window.index("if (recep) out.push(recep);"),
                        window.index("out.push([\"food\", TIER[e.tier]])"))
        self.assertIn('"Reception to follow"', script)

    def test_it_is_styled_in_every_theme(self):
        css = publish.TEMPLATE[:publish.TEMPLATE.find("<script>")]
        self.assertIn(".tag.recep{", css)
        self.assertIn(".tag.recep.after{", css)
        self.assertEqual(css.count("--recep:"), 3)
        self.assertEqual(css.count("--recep-bg:"), 3)

    def test_the_legend_explains_it(self):
        self.assertIn('<span class="tag recep">', publish.DEFAULT_ABOUT)

    def test_the_rsvp_list_can_show_receptions_only(self):
        html = self.page()
        self.assertIn('data-rshow="all"', html)
        self.assertIn('data-rshow="reception"', html)
        script = app_script()
        self.assertIn('read(KEY_RSVP_SHOW, "all")', script)
        self.assertIn("write(KEY_RSVP_SHOW", script)
        self.assertIn('["all", "reception"].indexOf(state.rshow)', script)

    def test_the_rsvp_filter_comes_before_the_counting(self):
        """So the tab numbers say what the list under them holds."""
        script = app_script()
        window = script[script.index("function renderRsvps()"):]
        self.assertLess(window.index('state.rshow !== "reception" || !!r.e.reception'),
                        window.index("counts[r.track]++"))

    def test_rsvp_rows_carry_the_tag(self):
        script = app_script()
        window = script[script.index("function rsvpRow(r)"):]
        self.assertIn("var recep = recepTag(e);", window[:300])

    def test_the_page_is_handed_it(self):
        import json
        data = json.loads(publish.payload([{
            "id": "e1", "title": "1L Reception", "starts_at": "2099-01-01T18:00:00",
            "ends_at": None, "all_day": 0, "reception": "reception"}])
            .replace("<\\/", "</"))[0]
        self.assertEqual(data["reception"], "reception")


class TestATimeNotYetGiven(unittest.TestCase):
    """A chat post gives the day and no hour: "time TBA", never "all day"."""

    def test_the_page_says_so_wherever_a_time_would_be(self):
        script = app_script()
        self.assertIn('if (e.allDay) return e.timeTba ? "time TBA" : "all day";', script)
        self.assertIn('e.timeTba ? "TBA" : "all day"', script)
        self.assertEqual(script.count("e.allDay && !e.timeTba ?"), 2)

    def test_the_page_is_handed_it(self):
        data = json.loads(publish.payload([{
            "id": "e2", "title": "Dinner Reception", "starts_at": "2099-01-01T00:00:00",
            "ends_at": None, "all_day": 1, "time_tba": True}])
            .replace("<\\/", "</"))[0]
        self.assertTrue(data["allDay"])
        self.assertTrue(data["timeTba"])
