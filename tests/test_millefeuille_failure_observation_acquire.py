"""Atomic private observer ownership, including failed and cancelled admission."""

import unittest
from unittest.mock import patch

from millefeuille.domain import _failure_observations as obs


class AcquisitionRollbackTests(unittest.TestCase):
    def setUp(self):
        # Keep any enclosing qualification runner's ownership wholly separate.
        for name, value in (
            ("_state", None),
            ("_states", []),
            ("_owners", []),
            ("_blocked", False),
            ("_suspension", None),
        ):
            manager = patch.object(obs, name, value)
            manager.start()
            self.addCleanup(manager.stop)

    def _exercise(self, target, after, error, prior=False):
        outer = obs.acquire() if prior else None
        prior_token = obs.token()
        if prior:
            obs.record(
                prior_token, boundary="broker", stage="evidence", reason="broker-denial"
            )
        original_states = tuple(obs._states)
        original_owners = tuple(obs._owners)

        class FailingAppend(list):
            remaining = 3

            def append(self, value):
                if self.remaining:
                    self.remaining -= 1
                    if after:
                        super().append(value)
                    raise error
                return super().append(value)

        with patch.object(obs, target, FailingAppend(getattr(obs, target))):
            for _ in range(3):
                with self.assertRaises(type(error)) as caught:
                    obs.acquire()
                self.assertIs(caught.exception, error)
                self.assertIs(obs.token(), prior_token)
                self.assertEqual(len(obs._states), len(original_states))
                self.assertEqual(len(obs._owners), len(original_owners))
                for current, original in zip(obs._states, original_states, strict=True):
                    self.assertIs(current, original)
                for current, original in zip(obs._owners, original_owners, strict=True):
                    self.assertIs(current, original)
                self.assertFalse(obs._blocked)
                self.assertIsNone(obs._suspension)
            healthy = obs.acquire()
            self.assertIsNotNone(healthy)
            obs.record(
                obs.token(), boundary="broker", stage="evidence", reason="broker-denial"
            )
            self.assertEqual(len(obs.drain_owned(healthy)["records"]), 1)
            obs.release(healthy)
            self.assertIs(obs.token(), prior_token)
        if outer is not None:
            self.assertEqual(len(obs.drain_owned(outer)["records"]), 1)
            obs.release(outer)
        self.assertEqual(obs._states, [])
        self.assertEqual(obs._owners, [])
        self.assertIsNone(obs.token())

    def test_owner_append_failure_before_mutation_recovers(self):
        self._exercise("_owners", False, MemoryError("fixed allocation fault"))

    def test_owner_append_failure_after_mutation_recovers(self):
        self._exercise("_owners", True, MemoryError("fixed allocation fault"))

    def test_ring_registration_failure_recovers(self):
        for after in (False, True):
            with self.subTest(after=after):
                self._exercise("_states", after, MemoryError("fixed allocation fault"))

    def test_cancellation_identity_and_recovery(self):
        for target in ("_states", "_owners"):
            for after in (False, True):
                for error_type in (KeyboardInterrupt, SystemExit):
                    with self.subTest(target=target, after=after, error=error_type):
                        self._exercise(target, after, error_type())

    def test_outer_owner_and_records_survive_failed_registration(self):
        for target in ("_states", "_owners"):
            for after in (False, True):
                with self.subTest(target=target, after=after):
                    self._exercise(
                        target, after, MemoryError("fixed fault"), prior=True
                    )


if __name__ == "__main__":
    unittest.main()
