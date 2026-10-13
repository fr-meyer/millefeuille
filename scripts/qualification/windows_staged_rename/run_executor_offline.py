"""Bounded offline unit evidence; no native API dispatch or fixture filesystem."""

import hashlib
import io
import json
import resource
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).absolute().parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
DENIED = []


def audit(event, _args):
    if event.startswith(("socket.", "subprocess.")) or event in {
        "os.system",
        "os.posix_spawn",
        "os.spawn",
        "os.exec",
        "ctypes.dlopen",
    }:
        DENIED.append(event)
        raise RuntimeError(
            "offline suite forbids network/process/native-library dispatch"
        )


def records():
    names = (
        "staged_replacement.py",
        "native_contract.py",
        "test_staged_replacement.py",
        "diagnostic_core.py",
        "native_worker.py",
        "native_controller.py",
        "build_manifest.py",
        "test_diagnostic_executor.py",
        "run_executor_offline.py",
        "references/probe_contract.py",
        "references/__init__.py",
        "references/sdk_rename_reference.c",
    )
    return [
        {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name in names
        for data in [(ROOT / name).read_bytes()]
    ]


def main():
    # Load ctypes itself before audit: diagnostic DLL/native constructors remain forbidden.
    import ctypes

    _ = ctypes.sizeof(ctypes.c_uint32)
    sys.addaudithook(audit)
    before, started, output = records(), time.monotonic(), io.StringIO()
    suite = unittest.TestSuite()
    subcases = []

    class CountResult(unittest.TextTestResult):
        def addSubTest(self, test, subtest, error):
            subcases.append(
                {"test": str(test), "case": str(subtest), "passed": error is None}
            )
            super().addSubTest(test, subtest, error)

    for module in ("test_staged_replacement", "test_diagnostic_executor"):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromName(module))
    result = unittest.TextTestRunner(
        stream=output, verbosity=2, resultclass=CountResult
    ).run(suite)
    elapsed, after = time.monotonic() - started, records()
    evidence = {
        "schema": "millefeuille.ten-case-executor-offline-evidence/v1",
        "tests": result.testsRun,
        "subcases": len(subcases),
        "failed_subcases": sum(not r["passed"] for r in subcases),
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skips": len(result.skipped),
        "network_process_native_library_attempts": DENIED,
        "elapsed_seconds": elapsed,
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "python": sys.version,
        "platform": sys.platform,
        "sources": before,
        "source_hashes_stable": before == after,
        "native_windows_execution": False,
        "independent_source_review": False,
        "production_windows_writes_enabled": False,
        "private_input_bytes_read": 0,
    }
    evidence["passed"] = (
        result.wasSuccessful()
        and not DENIED
        and before == after
        and elapsed < 15
        and evidence["peak_rss_bytes"] < 134217728
    )
    (ROOT / "executor-offline-tests.log").write_bytes(output.getvalue().encode())
    (ROOT / "executor-offline-evidence.json").write_bytes(
        (json.dumps(evidence, sort_keys=True, indent=2) + "\n").encode()
    )
    print(
        json.dumps(
            {key: value for key, value in evidence.items() if key != "sources"},
            sort_keys=True,
        )
    )
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
