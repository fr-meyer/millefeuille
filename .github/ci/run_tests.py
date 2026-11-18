"""CI adapter for the reviewed, bounded failure-only unittest runner."""

import collections
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
BRIDGE = Path(__file__).with_name("suite_runner.py")
spec = importlib.util.spec_from_file_location("ci_suite_runner", BRIDGE)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def run(suite, receipt):
    from millefeuille.domain import secure_io

    ledger = {
        "schema": "qualification-suite-ledger/v1",
        "occurrences": [test.id() for test in flatten(suite)],
        "skips": [],
        "capabilities": runner.obs.profile(secure_io._supports_no_follow()),
    }
    # Supply the already-discovered suite; no second collection or test filtering.
    with patch.object(unittest.TestLoader, "discover", return_value=suite):
        code = runner.main({"ledger": ledger, "receipt": str(receipt)})
    data = json.loads(receipt.read_text())
    if collections.Counter(e["id"] for e in data["events"]) != collections.Counter(
        ledger["occurrences"]
    ):
        raise RuntimeError("CI test occurrence mismatch")
    return code


def self_test():
    from millefeuille.domain import _failure_observations as observations

    class Control(unittest.TestCase):
        def test_success(self):
            observations.record(
                observations.token(),
                boundary="broker",
                stage="evidence",
                reason="broker-denial",
            )

        def test_failure(self):
            self.test_success()
            self.fail("fixed intentional CI bridge control")

    Control.__module__ = "tests.ci"
    Control.__qualname__ = "Control"
    for name, expected in (("test_success", 0), ("test_failure", 1)):
        output = io.StringIO()
        with (
            tempfile.TemporaryDirectory() as temporary,
            contextlib.redirect_stderr(output),
        ):
            code = run(
                unittest.TestSuite([Control(name)]),
                Path(temporary) / "receipt.json",
            )
        if code != expected:
            raise RuntimeError("CI bridge changed unittest outcome")
        frames = output.getvalue().count("PRIVATE FAILURE OBSERVATION ")
        if frames != expected or observations.token() is not None:
            raise RuntimeError("CI bridge output/lifetime mismatch")
    print("CI bridge positive and negative controls passed")


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
        self_test()
        return 0
    loader = unittest.TestLoader()
    suite = loader.discover("tests", top_level_dir=".")
    if loader.errors:
        # Emit only bounded repository module identifiers, never raw exception
        # messages, tracebacks, environment values, or imported source content.
        failed_modules = sorted(
            {
                test.id()
                for test in flatten(suite)
                if isinstance(test, unittest.loader._FailedTest)
            }
        )
        safe_modules = [
            name
            for name in failed_modules[:100]
            if len(name) <= 240
            and all(
                char.isascii() and (char.isalnum() or char in "._") for char in name
            )
        ]
        print(json.dumps({"collection_error_modules": safe_modules}), flush=True)
        raise RuntimeError("CI collection errors")
    output = ROOT / ".ci-output"
    output.mkdir(exist_ok=True)
    return run(suite, output / "tests.json")


if __name__ == "__main__":
    raise SystemExit(main())
