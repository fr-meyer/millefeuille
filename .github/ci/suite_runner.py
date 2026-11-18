"""Pinned runner: explicit capability profile and structured unittest outcomes."""

import collections
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

s = importlib.util.spec_from_file_location(
    "bound_suite_observations", Path(__file__).with_name("suite_observations.py")
)
obs = importlib.util.module_from_spec(s)
s.loader.exec_module(obs)


def main(config):
    ledger = obs.validate_ledger(config["ledger"])
    from millefeuille.domain import secure_io

    obs.need(
        obs.profile(secure_io._supports_no_follow()) == ledger["capabilities"],
        "capability mismatch",
    )

    def flat(suite):
        for t in suite:
            if isinstance(t, unittest.TestSuite):
                yield from flat(t)
            else:
                yield t

    loader = unittest.TestLoader()
    suite = loader.discover("tests", top_level_dir=".")
    obs.need(not loader.errors, "collection errors")
    ids = [t.id() for t in flat(suite)]
    obs.need(
        collections.Counter(ids) == collections.Counter(ledger["occurrences"]),
        "discovered occurrences",
    )
    events = []
    try:
        from millefeuille.domain import _failure_observations as diagnostics
    except ImportError:
        diagnostics = None  # Older synthetic helper fixtures have no observer.
    diagnostic_bytes = 0

    class Result(unittest.TextTestResult):
        def startTest(self, test):  # noqa: N802 - unittest callback names
            self.diagnostic_test_bytes = 0
            self.diagnostic_owner = None
            self.diagnostic_suspension = None
            super().startTest(test)
            if diagnostics is not None:
                try:
                    self.diagnostic_owner = diagnostics.acquire()
                except Exception:
                    try:  # noqa: SIM105 - preserve reviewed exception boundary
                        self.diagnostic_suspension = diagnostics.suspend()
                    except Exception:
                        pass

        def stopTest(self, test):  # noqa: N802 - unittest callback names
            if diagnostics is not None:
                try:
                    diagnostics.release(self.diagnostic_owner)
                    diagnostics.resume(self.diagnostic_suspension)
                except Exception:
                    pass
            super().stopTest(test)

        def diagnostic(self):
            nonlocal diagnostic_bytes
            if diagnostics is None:
                return
            try:
                data = diagnostics.drain_owned(self.diagnostic_owner)
                if data is None:
                    return
                raw = json.dumps(
                    data, sort_keys=True, separators=(",", ":"), ensure_ascii=True
                )
                frame = chr(10) + "PRIVATE FAILURE OBSERVATION " + raw + chr(10)
                size = len(frame.encode("ascii"))
                if (
                    self.diagnostic_test_bytes + size > 8192
                    or diagnostic_bytes + size > 65536
                ):
                    return
                self.diagnostic_test_bytes += size
                diagnostic_bytes += size
                self.stream.write(frame)
                self.stream.flush()
            except Exception:
                pass

        def immediate(self, kind, test, details):
            try:
                self.stream.write(
                    chr(10) + "IMMEDIATE " + kind + ": " + test.id() + chr(10) + details
                )
                self.stream.flush()
            except Exception:
                pass  # Best effort; never bypass lifecycle capture guard.

        def addSuccess(self, test):  # noqa: N802 - unittest callback names
            events.append({"id": test.id(), "outcome": "ok"})
            super().addSuccess(test)

        def addSkip(self, test, reason):  # noqa: N802 - unittest callback names
            events.append({"id": test.id(), "outcome": "skip", "reason": reason})
            super().addSkip(test, reason)

        def addError(self, test, err):  # noqa: N802 - unittest callback names
            events.append({"id": test.id(), "outcome": "error"})
            super().addError(test, err)
            self.immediate("ERROR", test, self.errors[-1][1])
            self.diagnostic()

        def addFailure(self, test, err):  # noqa: N802 - unittest callback names
            events.append({"id": test.id(), "outcome": "failure"})
            super().addFailure(test, err)
            self.immediate("FAIL", test, self.failures[-1][1])
            self.diagnostic()

        def addSubTest(self, test, subtest, err):  # noqa: N802 - unittest callback names
            if err is not None:
                events.append({"id": test.id(), "outcome": "subtest-error"})
            super().addSubTest(test, subtest, err)
            if err is not None:
                self.immediate(
                    "SUBTEST",
                    subtest,
                    (
                        self.failures
                        if issubclass(err[0], test.failureException)
                        else self.errors
                    )[-1][1],
                )
                self.diagnostic()

    result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
    data = {
        "schema": "qualification-suite-result/v1",
        "ledger_sha256": hashlib.sha256(
            json.dumps(ledger, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "tests": result.testsRun,
        "events": events,
        "success": result.wasSuccessful(),
    }
    with Path(config["receipt"]).open("x") as stream:
        json.dump(data, stream, sort_keys=True)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(json.loads(Path(sys.argv[1]).read_bytes())))
