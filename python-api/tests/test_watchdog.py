#!/usr/bin/env python3
"""
The stall watchdog used to kill a search that had printed nothing for five
minutes. That rule threw away real work: the engine prints between steps, and a
1-hop protein query on a throttled free-tier CPU can spend minutes inside one
step while still minutes from finishing. It now also samples the engine's CPU
time and only believes a stall when the process is silent *and* idle.

Run:  cd python-api && python3 -m pytest tests/test_watchdog.py -q
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_TEST_DIR = tempfile.mkdtemp(prefix="scigraph_watchdog_test_")
os.environ["BILLING_DB"] = str(Path(_TEST_DIR) / "billing.db")
os.environ["SEARCH_DB"] = str(Path(_TEST_DIR) / "search.db")
os.environ["EXPORTS_DIR"] = str(Path(_TEST_DIR) / "exports")
os.environ["WORKSPACE_DIR"] = str(Path(_TEST_DIR) / "workspace")
os.environ["STATIC_DIR"] = str(Path(_TEST_DIR) / "ui")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as engine_app  # noqa: E402


class CpuSampling(unittest.TestCase):
    """The signal the whole decision rests on has to be real."""

    def test_it_reads_cpu_time_for_this_process(self):
        seconds = engine_app._process_cpu_seconds(os.getpid())
        self.assertIsNotNone(seconds, "could not read our own CPU time")
        self.assertGreater(seconds, 0.0)
        # Running the test burns CPU, so a second reading must not go backwards.
        busy = sum(i * i for i in range(200000))
        later = engine_app._process_cpu_seconds(os.getpid())
        self.assertIsNotNone(later)
        self.assertGreaterEqual(later, seconds)
        self.assertIsInstance(busy, int)

    def test_cpu_time_advances_when_the_process_works(self):
        """The distinction the watchdog depends on: idle vs busy."""
        before = engine_app._process_cpu_seconds(os.getpid())
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            sum(i * i for i in range(50_000))
        after = engine_app._process_cpu_seconds(os.getpid())
        self.assertGreater(
            after - before, 0.05,
            "CPU time did not advance during two seconds of real work")

    def test_an_unknown_process_reads_as_cannot_tell(self):
        """None means 'unknown', never 'stopped' — treating it as zero would
        make every sample look like a dead engine."""
        self.assertIsNone(engine_app._process_cpu_seconds(4_000_000))

    def test_a_sleeping_child_is_reported_but_makes_no_progress(self):
        """A child that is alive but idle must read as idle, not as working."""
        import subprocess
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(child.kill)
        first = engine_app._process_cpu_seconds(child.pid)
        self.assertIsNotNone(first)
        time.sleep(1.5)
        second = engine_app._process_cpu_seconds(child.pid)
        self.assertLess(abs(second - first), 0.05,
                        "an idle child appeared to be using CPU")


class StallDecision(unittest.TestCase):
    """The rule itself, isolated from the subprocess plumbing."""

    def setUp(self):
        self._timeout = engine_app.STALL_TIMEOUT
        self._ceiling = engine_app.MAX_SEARCH_SECONDS
        self.addCleanup(lambda: setattr(engine_app, "STALL_TIMEOUT", self._timeout))
        self.addCleanup(
            lambda: setattr(engine_app, "MAX_SEARCH_SECONDS", self._ceiling))

    def test_a_silent_idle_engine_is_stalled(self):
        self.assertTrue(engine_app._is_stalled(quiet_for=901, running_for=1000))

    def test_a_busy_engine_is_never_stalled_however_long_it_is_quiet(self):
        """The regression, at the point it actually happened.

The old watchdog measured silence only, so a search that spent minutes
inside one step was killed while it was working. The timer must be
reset whenever CPU advances — which means a busy engine can never
reach the threshold no matter how long it stays quiet.
        """
        engine_app.MAX_SEARCH_SECONDS = 100_000
        quiet_since = 1_000.0
        cpu_last = 20.0
        # The engine is silent, but each sample shows it burning CPU.
        for elapsed in range(1, 40):
            now = 1_000.0 + elapsed * 10
            quiet_since = engine_app._quiet_since_update(quiet_since, 20.0 + elapsed, cpu_last, now)
            self.assertFalse(
                engine_app._is_stalled(now - quiet_since, now),
                f"a working engine was declared stalled after {elapsed * 10}s of silence")
            self.assertEqual(quiet_since, now, "the quiet timer was not reset")

    def test_the_timer_is_left_alone_when_cpu_cannot_be_read(self):
        """Unknown must not be treated as idle, or a platform without /proc
        would silently go back to killing working searches."""
        for cpu in (None,):
            with self.subTest(cpu=cpu):
                self.assertEqual(
                    engine_app._quiet_since_update(500.0, cpu, 10.0, 900.0), 500.0)

    def test_the_timer_is_not_reset_when_cpu_stops_advancing(self):
        self.assertEqual(
            engine_app._quiet_since_update(500.0, 20.0, 20.0, 900.0), 500.0)
        self.assertEqual(
            engine_app._quiet_since_update(500.0, 19.0, 20.0, 900.0), 500.0)

    def test_printed_output_is_a_sign_of_life_on_its_own(self):
        """The second gap: a chatty engine parked on a slow connector request
        burns no CPU, but it is plainly alive. Measuring only CPU would kill it,
        so a printed line must reset the timer on its own."""
        self.assertEqual(
            engine_app._quiet_since_update(500.0, 20.0, 20.0, 900.0, saw_output=True),
            900.0)

    def test_output_also_saves_an_engine_whose_cpu_cannot_be_read(self):
        """On a platform without /proc, printed output is the only evidence of
        life there is, and it has to count."""
        self.assertEqual(
            engine_app._quiet_since_update(500.0, None, None, 900.0, saw_output=True),
            900.0)

    def test_a_busy_talking_engine_is_never_stalled(self):
        """End to end over the decision, with output arriving every sample."""
        quiet_since = 1_000.0
        for elapsed in range(1, 40):
            now = 1_000.0 + elapsed * 10
            quiet_since = engine_app._quiet_since_update(
                quiet_since, 20.0, 20.0, now, saw_output=True)
            self.assertFalse(engine_app._is_stalled(now - quiet_since, now))
            self.assertEqual(quiet_since, now)

    def test_silence_shorter_than_the_timeout_is_tolerated(self):
        self.assertFalse(engine_app._is_stalled(quiet_for=899, running_for=899))
        self.assertFalse(engine_app._is_stalled(quiet_for=600, running_for=600))

    def test_the_ceiling_applies_even_to_a_busy_engine(self):
        """Otherwise a spinning process holds the one search slot for ever."""
        self.assertTrue(engine_app._is_stalled(quiet_for=0, running_for=3000))
        self.assertTrue(engine_app._is_stalled(quiet_for=0, running_for=5000))

    def test_the_ceiling_is_wider_than_the_stall_timeout(self):
        """A busy engine deserves more than five minutes before giving up."""
        self.assertGreater(engine_app.MAX_SEARCH_SECONDS, engine_app.STALL_TIMEOUT)

    def test_the_thresholds_are_the_documented_ones(self):
        self.assertEqual(engine_app.STALL_TIMEOUT, 900.0)
        self.assertEqual(engine_app.STALL_POLL_SECONDS, 10.0)
        self.assertEqual(engine_app.MAX_SEARCH_SECONDS, 3000.0)
        self.assertLess(
            engine_app.STALL_POLL_SECONDS, engine_app.STALL_TIMEOUT / 10,
            "sampling too coarsely would miss a brief spike of CPU")


class FailureMessage(unittest.TestCase):
    """The message must not repeat the mistake this whole session corrected:
    naming a cause the server never observed."""

    def _stall_message(self) -> str:
        source = Path(engine_app.__file__).read_text(encoding="utf-8")
        start = source.index('state["status"] = "failed"', source.index("stall_detected"))
        return source[start:start + 1200]

    def test_it_does_not_blame_memory(self):
        self.assertNotIn(
            "memory", self._stall_message().lower(),
            "the stall message asserts a cause the watchdog never measured")

    def test_it_says_what_was_actually_observed(self):
        text = self._stall_message().lower()
        self.assertIn("no cpu", text)
        self.assertIn("time limit", text)

    def test_it_reports_the_thresholds_actually_in_force(self):
        """The wording is built from the constants, so an operator is told the
        real limits rather than a number left behind by an earlier change."""
        text = self._stall_message()
        self.assertIn("STALL_TIMEOUT", text,
                      "the message hardcodes a duration instead of reading STALL_TIMEOUT")
        self.assertIn("MAX_SEARCH_SECONDS", text,
                      "the message hardcodes a duration instead of reading MAX_SEARCH_SECONDS")
        # A literal number of minutes in the wording is exactly the drift this
        # guards against: the constants changed once already.
        self.assertNotRegex(text, r"\b\d+\s*minutes?\b")


if __name__ == "__main__":
    unittest.main(verbosity=2)