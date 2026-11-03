"""Downloading the school's calendar, and asking again when it does not come.

A whole feed arrives in a second or two. On 12 Oct 2026 Georgetown's went
silent for the full 45 seconds and answered normally minutes later; the
refresh had given up, and that day's new calendar events were not listed.

No test here waits or touches the network: the server and the clock are both
stand-ins, and time passes only when the code under test spends it.
"""
import http.client
import io
import os
import socket
import sys
import tempfile
import types
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Every rule under test was learned on Georgetown; see schools/.
os.environ["FFF_SCHOOL"] = "georgetown_law"
# ...and none of them reads your own settings.
os.environ["FFF_CONFIG"] = "/nonexistent/free-food-finder/config.json"

import store                  # noqa: E402
from ingest import livewhale  # noqa: E402

FEED = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
        "UID:pizza-1@example.edu\r\nSUMMARY:Pizza with the Antitrust Society\r\n"
        "DTSTART;TZID=America/New_York:20261015T123000\r\n"
        "DTEND;TZID=America/New_York:20261015T133000\r\n"
        "LOCATION:McDonough 200\r\nDESCRIPTION:Lunch will be served.\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n")

SILENT = "silent"


def http_error(code):
    # With a body, as a server's has, so that it can be closed afterwards.
    return urllib.error.HTTPError("https://calendar.example.edu/feed.ics",
                                  code, "", {}, io.BytesIO(b""))


class Server:
    """Stands in for one request to the feed, answering from a script.

    SILENT keeps the caller waiting for as long as it said it would wait.
    An exception fails at once; anything else is the feed.
    """

    def __init__(self, *answers):
        self.script = answers
        self.answers = list(answers)
        self.now = 0.0
        self.waits = []          # how long each request was prepared to wait
        self.pauses = []

    # The two halves of `time` that `download` uses.
    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.pauses.append(seconds)
        self.now += seconds

    def fetch(self, days=30, timeout=livewhale.TIMEOUT):
        self.waits.append(timeout)
        answer = self.answers.pop(0)
        if answer is SILENT:
            self.now += timeout
            raise socket.timeout("The read operation timed out")
        self.now += 1.5
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def __enter__(self):
        self.patches = [
            mock.patch.object(livewhale, "fetch", self.fetch),
            mock.patch.object(livewhale, "time", types.SimpleNamespace(
                monotonic=self.monotonic, sleep=self.sleep))]
        for patch in self.patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in self.patches:
            patch.stop()
        # Python 3.12 and later warn about an HTTP error whose response was
        # never closed.
        for answer in self.script:
            if isinstance(answer, urllib.error.HTTPError):
                answer.close()


class TestAskingAgain(unittest.TestCase):
    def download(self, server):
        stats = {}
        with server:
            text = livewhale.download(35, stats)
        return text, stats["attempts"]

    def test_a_silent_server_is_asked_again(self):
        server = Server(SILENT, FEED)
        self.assertEqual(self.download(server), (FEED, 2))
        self.assertEqual(server.pauses, [livewhale.PAUSE])

    def test_a_server_error_is_asked_again(self):
        self.assertEqual(self.download(Server(http_error(503), http_error(502), FEED)),
                         (FEED, 3))

    def test_a_download_cut_short_is_asked_again(self):
        self.assertEqual(self.download(Server(http.client.IncompleteRead(b"BEGIN"), FEED)),
                         (FEED, 2))

    def test_a_dropped_connection_is_asked_again(self):
        self.assertEqual(self.download(Server(ConnectionResetError(54, "reset"), FEED)),
                         (FEED, 2))

    def test_a_feed_that_moved_is_not(self):
        """A 404 is an answer: asking again changes nothing."""
        server = Server(http_error(404), FEED)
        with server, self.assertRaises(urllib.error.HTTPError):
            livewhale.download(35)
        self.assertEqual((len(server.waits), server.pauses), (1, []))

    def test_a_profile_with_no_calendar_is_not(self):
        server = Server(ValueError("the school's profile names no calendar"), FEED)
        with server, self.assertRaises(ValueError):
            livewhale.download(35)
        self.assertEqual(len(server.waits), 1)


class TestTheRoutinesTwoMinutes(unittest.TestCase):
    """The routine's shell command has two minutes, and mail comes after."""

    def test_a_server_that_stays_silent_is_given_up_on_in_time(self):
        server = Server(SILENT, SILENT, SILENT)
        with server, self.assertRaises(socket.timeout):
            livewhale.download(35)
        self.assertLessEqual(server.now, livewhale.PATIENCE)
        # The second attempt waits only as long as is left.
        self.assertEqual(server.waits, [45, 40])

    def test_quick_failures_get_three_attempts(self):
        server = Server(*[ConnectionResetError(54, "reset")] * 3)
        with server, self.assertRaises(ConnectionResetError):
            livewhale.download(35)
        self.assertEqual(len(server.waits), livewhale.ATTEMPTS)

    def test_the_whole_budget_fits(self):
        self.assertLessEqual(livewhale.PATIENCE, 120 - 20,
                             "leave the mail and the rest of the refresh time to run")


class TestTheRefresh(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(os.path.join(tempfile.mkdtemp(), "c.db"))
        self.addCleanup(self.conn.close)

    def logged(self):
        return self.conn.execute(
            "SELECT found, added, error FROM ingest_log").fetchall()[-1]

    def test_a_second_attempt_lists_the_events(self):
        with Server(SILENT, FEED):
            stats = livewhale.run(self.conn, days=35, keep_all=True)
        self.assertEqual((stats["added"], stats["attempts"], stats["error"]),
                         (1, 2, None))

    def test_giving_up_says_how_often_it_tried(self):
        """The routine reports any error the refresh prints, so this is what
        its summary says when the calendar still fails."""
        with Server(SILENT, SILENT):
            stats = livewhale.run(self.conn, days=35)
        # The error is "timeout" before Python 3.10 and "TimeoutError" after.
        self.assertEqual(stats["error"], f"{socket.timeout.__name__}: The read "
                                         "operation timed out (gave up after 2 attempts)")
        self.assertEqual(tuple(self.logged()), (0, 0, stats["error"]))

    def test_a_download_cut_short_does_not_end_the_refresh(self):
        """IncompleteRead is no OSError: uncaught, the mail was never read."""
        with Server(*[http.client.IncompleteRead(b"BEGIN")] * 3):
            stats = livewhale.run(self.conn, days=35)
        self.assertTrue(stats["error"].startswith("IncompleteRead"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
