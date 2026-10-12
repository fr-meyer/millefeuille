"""Proposed native Windows-only worker; root must authorize a real route first."""

import functools
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time
import types
import unittest


def main():
    source_root, scratch, manifest_path = map(Path, sys.argv[1:4])
    assert os.name == "nt" and sys.platform == "win32"
    assert sys.version_info[:2] == (3, 13) and sys.maxsize > 2**32
    manifest_raw = manifest_path.read_bytes()
    assert hashlib.sha256(manifest_raw).hexdigest() == sys.argv[4]
    manifest = json.loads(manifest_raw)
    sys.stdout = (scratch / "stdout.log").open("x", encoding="utf-8")
    sys.stderr = (scratch / "stderr.log").open("x", encoding="utf-8")
    fixture_root = scratch / "fixtures"
    fixture_root.mkdir()
    tempfile.tempdir = str(fixture_root)
    started = time.monotonic()

    def audit(event, arguments):
        if event.startswith(("socket.", "urllib.", "subprocess.")):
            raise RuntimeError("network and new child processes are forbidden")
        if event in {"os.system", "os.spawn", "os.exec"}:
            raise RuntimeError("process launch is forbidden")

    sys.addaudithook(audit)
    for name in ("millefeuille", "millefeuille.domain"):
        module = types.ModuleType(name)
        module.__path__ = []
        sys.modules[name] = module
    loaded = []
    for record in manifest["sources"]:
        path = source_root / record["path"]
        raw = path.read_bytes()
        assert len(raw) == record["bytes"]
        assert hashlib.sha256(raw).hexdigest() == record["sha256"]
        module = types.ModuleType(record["module"])
        module.__file__ = str(path)
        sys.modules[record["module"]] = module
        exec(compile(raw, str(path), "exec"), module.__dict__)
        loaded.append({key: record[key] for key in ("module", "bytes", "sha256")})
    tests = sys.modules["collagan_windows_tests"]
    # Test-only fixture lifecycle: retain native failure files and aliases.
    # No native IO, validator, writer, sharing or alias API is replaced.
    tests.tempfile = types.SimpleNamespace(
        TemporaryDirectory=functools.partial(
            tempfile.TemporaryDirectory, dir=str(fixture_root), delete=False
        )
    )
    suite = unittest.defaultTestLoader.loadTestsFromName(
        "collagan_windows_tests.NativeWindowsQualification"
    )
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
    receipt = {
        "schema": "millefeuille.native-windows-three-control-worker/v1",
        "platform": sys.platform,
        "windows": platform.win32_ver(),
        "python": sys.version,
        "loaded": loaded,
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skips": len(result.skipped),
        "skip_reasons": [reason for _, reason in result.skipped],
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "fixture_lifecycle_override": "native fixtures retained; actual IO unchanged",
        "production_dispatch": False,
        "application_candidate_qualified": False,
        "private_provider_broker_model_operations": 0,
    }
    with (scratch / "worker-result.json").open("x", encoding="utf-8") as output:
        json.dump(receipt, output, sort_keys=True)
        output.write("\n")
    return (
        0
        if result.wasSuccessful() and result.testsRun == 3 and not result.skipped
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
