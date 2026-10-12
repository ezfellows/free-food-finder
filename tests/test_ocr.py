"""The Vision bridge, exercised without Swift.

`_run` is the only place this module touches a subprocess, so replacing it
covers every path. Fixtures in `samples/vision/` are JSONL text -- one captured
from the real helper, the rest hand-written for cases a capture cannot produce
on demand.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"
import ocr

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "samples", "vision")


def fixture(name):
    with open(os.path.join(FIXTURES, name)) as fh:
        return fh.read()


class Patched(unittest.TestCase):
    """Force the helper to look available and return canned output."""

    def setUp(self):
        self._run, self._binary = ocr._run, ocr.binary
        self._state = dict(ocr._state)
        ocr.binary = lambda refresh=False: "/fake/flyer_ocr"

    def tearDown(self):
        ocr._run, ocr.binary = self._run, self._binary
        ocr._state.clear()
        ocr._state.update(self._state)

    def serve(self, text):
        ocr._run = lambda paths, timeout: text


class TestCapturedReading(Patched):
    def test_real_helper_output_parses(self):
        self.serve(fixture("withqr.jsonl"))
        out = ocr.read_images(["/fixtures/withqr.jpg"])
        reading = out["/fixtures/withqr.jpg"]
        self.assertTrue(reading["ok"])
        self.assertEqual(reading["ocr_rev"], 3)
        self.assertTrue(ocr.text_of(reading))
        self.assertTrue(ocr.payloads(reading))

    def test_payloads_are_returned_raw(self):
        """QR payloads are stored and shown, never fetched and never rewritten."""
        self.serve(fixture("withqr.jsonl"))
        reading = ocr.read_image("/fixtures/withqr.jpg")
        for payload in ocr.payloads(reading):
            self.assertIsInstance(payload, str)


class TestReadingOrder(Patched):
    """Vision reports bottom-left-origin boxes; the helper flips and sorts.

    If that ever regresses, every flyer reads bottom-up and the title
    heuristics silently pick the footer.
    """

    def test_lines_arrive_top_to_bottom_then_left_to_right(self):
        self.serve(fixture("ordering.jsonl"))
        reading = ocr.read_image("/fixtures/order.jpg")
        # The fixture is deliberately stored out of order; text_of preserves
        # the order the helper emitted, so assert on the fixture's own y values.
        lines = sorted(reading["lines"],
                       key=lambda l: (round(l["y"], 2), l["x"]))
        self.assertEqual([l["text"] for l in lines][0], "TITLE")
        self.assertEqual([l["text"] for l in lines][-1], "BOTTOM LINE")

    def test_y_is_distance_from_the_top(self):
        self.serve(fixture("ordering.jsonl"))
        reading = ocr.read_image("/fixtures/order.jpg")
        title = next(l for l in reading["lines"] if l["text"] == "TITLE")
        footer = next(l for l in reading["lines"] if l["text"] == "BOTTOM LINE")
        self.assertLess(title["y"], footer["y"])


class TestConfidence(Patched):
    def test_mean_of_line_confidences(self):
        self.serve(fixture("lowconf.jsonl"))
        reading = ocr.read_image("/fixtures/lowconf.jpg")
        self.assertEqual(ocr.confidence(reading), 30)     # (0.31 + 0.28) / 2

    def test_no_lines_is_zero_not_a_crash(self):
        self.assertEqual(ocr.confidence({"lines": []}), 0)
        self.assertEqual(ocr.confidence({}), 0)
        self.assertEqual(ocr.confidence(None), 0)

    def test_text_of_empty(self):
        self.assertEqual(ocr.text_of(None), "")
        self.assertEqual(ocr.payloads(None), [])


class TestFailureModes(Patched):
    """Every failure returns a dict. Nothing here may raise."""

    def test_helper_did_not_run(self):
        ocr._run = lambda paths, timeout: None
        reading = ocr.read_image("/fixtures/a.jpg")
        self.assertFalse(reading["ok"])
        self.assertIn("did not run", reading["error"])

    def test_garbage_stdout(self):
        self.serve("this is not json\n{also not\n")
        reading = ocr.read_image("/fixtures/a.jpg")
        self.assertFalse(reading["ok"])
        self.assertEqual(reading["error"], "no output")

    def test_one_bad_line_does_not_lose_the_others(self):
        """JSONL exists so a fault on one image keeps the rest usable."""
        good = json.dumps({"path": "/fixtures/b.jpg", "ok": True,
                           "lines": [{"text": "hi", "conf": 1.0}],
                           "barcodes": []})
        self.serve("{broken\n" + good + "\n")
        out = ocr.read_images(["/fixtures/a.jpg", "/fixtures/b.jpg"])
        self.assertFalse(out["/fixtures/a.jpg"]["ok"])
        self.assertTrue(out["/fixtures/b.jpg"]["ok"])

    def test_missing_path_is_reported_not_dropped(self):
        self.serve(json.dumps({"path": "/fixtures/a.jpg", "ok": True,
                               "lines": [], "barcodes": []}))
        out = ocr.read_images(["/fixtures/a.jpg", "/fixtures/never.jpg"])
        self.assertIn("/fixtures/never.jpg", out)
        self.assertFalse(out["/fixtures/never.jpg"]["ok"])

    def test_error_line_from_the_helper_is_preserved(self):
        self.serve(fixture("lowconf.jsonl"))
        out = ocr.read_images(["/fixtures/failed.jpg"])
        self.assertFalse(out["/fixtures/failed.jpg"]["ok"])
        self.assertIn("unsupported", out["/fixtures/failed.jpg"]["error"])

    def test_empty_input(self):
        self.assertEqual(ocr.read_images([]), {})
        self.assertEqual(ocr.read_images(None), {})


class TestUnavailable(unittest.TestCase):
    """With no Swift the pipeline still runs; only the free tier is missing."""

    def setUp(self):
        self._binary = ocr.binary
        self._state = dict(ocr._state)
        ocr.binary = lambda refresh=False: None
        ocr._state["error"] = "swiftc not found"

    def tearDown(self):
        ocr.binary = self._binary
        ocr._state.clear()
        ocr._state.update(self._state)

    def test_available_is_false(self):
        self.assertFalse(ocr.available())

    def test_every_path_still_comes_back(self):
        out = ocr.read_images(["/a.jpg", "/b.jpg"])
        self.assertEqual(set(out), {"/a.jpg", "/b.jpg"})
        for reading in out.values():
            self.assertFalse(reading["ok"])
            self.assertIn("swiftc", reading["error"])


class TestAToolchainUpdate(unittest.TestCase):
    """October 2026: a macOS 27 SDK arrived with a compiler that cannot read
    it. The helper built a month earlier still ran, but its stamp named the
    old SDK, so it was thrown away and every flyer failed. Older SDKs sat
    beside the new one the whole time."""

    def setUp(self):
        import tempfile
        from unittest import mock
        root = tempfile.mkdtemp()
        sdks = os.path.join(root, "SDKs")
        os.makedirs(sdks)
        for name in ("MacOSX27.0.sdk", "MacOSX26.5.sdk", "MacOSX15.4.sdk"):
            os.makedirs(os.path.join(sdks, name))
        os.symlink(os.path.join(sdks, "MacOSX26.5.sdk"), os.path.join(sdks, "MacOSX26.sdk"))
        os.symlink(os.path.join(sdks, "MacOSX27.0.sdk"), os.path.join(sdks, "MacOSX.sdk"))
        self.sdks = sdks
        self.default = os.path.join(sdks, "MacOSX27.0.sdk")
        bin_dir = os.path.join(root, "bin")
        patches = [mock.patch.object(ocr, "BIN_DIR", bin_dir),
                   mock.patch.object(ocr, "BIN_PATH", os.path.join(bin_dir, "flyer_ocr")),
                   mock.patch.object(ocr, "STAMP_PATH", os.path.join(bin_dir, "flyer_ocr.stamp")),
                   mock.patch.object(ocr, "SWIFTC", "/usr/bin/swiftc"),
                   mock.patch.object(ocr, "_sdk_path", lambda: self.default),
                   mock.patch.object(ocr.subprocess, "run", self.fake_swiftc),
                   mock.patch.dict(ocr._state, {"checked": False, "binary": None, "error": None})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.builds = []
        self.working = ("26.5",)

    def fake_swiftc(self, args, **kwargs):
        import subprocess
        sdk = args[args.index("-sdk") + 1]
        self.builds.append(os.path.basename(sdk))
        if any(v in sdk for v in self.working):
            with open(args[args.index("-o") + 1], "w") as fh:
                fh.write("#!/bin/sh\n")
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(
            args, 1, "", "error: cannot suppress '~Copyable' on generic parameter")

    def keep(self, stamp):
        os.makedirs(ocr.BIN_DIR, exist_ok=True)
        with open(ocr.BIN_PATH, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(ocr.BIN_PATH, 0o755)
        with open(ocr.STAMP_PATH, "w") as fh:
            fh.write(stamp)

    def test_the_default_sdk_first_then_the_rest_newest_first(self):
        names = [os.path.basename(p) for p in ocr._sdks()]
        self.assertEqual(names, ["MacOSX27.0.sdk", "MacOSX26.5.sdk", "MacOSX15.4.sdk"])

    def test_a_failed_build_is_retried_against_an_older_sdk(self):
        self.assertEqual(ocr.binary(refresh=True), ocr.BIN_PATH)
        self.assertEqual(self.builds, ["MacOSX27.0.sdk", "MacOSX26.5.sdk"])
        with open(ocr.STAMP_PATH) as fh:
            self.assertEqual(fh.read(), ocr._stamp())

    def test_a_helper_from_this_source_survives_an_sdk_nothing_can_build(self):
        self.working = ()
        self.keep(ocr._stamp().split(":")[0] + ":/an/older/MacOSX.sdk")
        self.assertEqual(ocr.binary(refresh=True), ocr.BIN_PATH)

    def test_but_not_one_built_from_other_source(self):
        self.working = ()
        self.keep("0" * 64 + ":/an/older/MacOSX.sdk")
        self.assertIsNone(ocr.binary(refresh=True))
        self.assertIn("Copyable", ocr.last_error())


class TestDoctor(unittest.TestCase):
    def test_reports_without_raising(self):
        info = ocr.doctor()
        for key in ("swiftc", "sdk", "helper", "available"):
            self.assertIn(key, info)


if __name__ == "__main__":
    unittest.main(verbosity=2)
