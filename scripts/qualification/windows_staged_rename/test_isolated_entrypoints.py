"""Actual -I import regression; only a tiny owned ambient-package fixture."""

import contextlib
import ctypes
import hashlib
import io
import json
import resource
import runpy
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).absolute().parent
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
        raise RuntimeError("isolated import regression forbids external dispatch")


def main():
    if not sys.flags.isolated or not sys.dont_write_bytecode:
        raise RuntimeError("invoke this regression with python -I -B")
    _ = ctypes.sizeof(ctypes.c_uint32)
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    sys.addaudithook(audit)
    started, cases = time.monotonic(), []
    pins = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in (
            "build_manifest.py",
            "native_worker.py",
            "native_controller.py",
            "diagnostic_core.py",
            "references/__init__.py",
            "references/probe_contract.py",
        )
    }
    with tempfile.TemporaryDirectory(prefix="mf-import-only-", dir="/tmp") as temporary:
        ambient = Path(temporary)
        package = ambient / "references"
        package.mkdir()
        (package / "__init__.py").write_bytes(
            b'raise AssertionError("ambient package imported")\n'
        )
        (package / "probe_contract.py").write_bytes(
            b'raise AssertionError("ambient reference imported")\n'
        )
        fixture_bytes = sum(path.stat().st_size for path in package.iterdir())
        sys.path.append(str(ambient))
        try:
            for entry in (
                "build_manifest.py",
                "native_worker.py",
                "native_controller.py",
            ):
                sys.path[:] = [part for part in sys.path if part != str(ROOT)]
                for module in tuple(sys.modules):
                    if (
                        module
                        in {
                            "build_manifest",
                            "native_worker",
                            "native_controller",
                            "diagnostic_core",
                            "staged_replacement",
                        }
                        or module == "references"
                        or module.startswith("references.")
                    ):
                        sys.modules.pop(module)
                sys.argv = [str(ROOT / entry), "--help"]
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    try:
                        runpy.run_path(str(ROOT / entry), run_name="__main__")
                    except SystemExit as error:
                        if error.code != 0:
                            raise
                    else:
                        raise RuntimeError("expected argparse help terminal exit")
                actual = Path(sys.modules["references"].__file__)
                reference = Path(sys.modules["references.probe_contract"].__file__)
                if (
                    actual != ROOT / "references/__init__.py"
                    or reference != ROOT / "references/probe_contract.py"
                ):
                    raise RuntimeError("ambient package won over pinned references")
                cases.append(
                    {
                        "entry": entry,
                        "isolated": True,
                        "help_exit": 0,
                        "package": str(actual),
                        "reference": str(reference),
                        "help_bytes": len(output.getvalue().encode()),
                        "passed": True,
                    }
                )
        finally:
            sys.path.remove(str(ambient))
    elapsed = time.monotonic() - started
    stable = all(
        hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
        for name, digest in pins.items()
    )
    result = {
        "schema": "millefeuille.actual-isolated-import-regression/v1",
        "cases": cases,
        "tests": len(cases),
        "passed": len(cases) == 3 and stable and not DENIED and elapsed < 10,
        "source_hashes": pins,
        "source_hashes_stable": stable,
        "elapsed_seconds": elapsed,
        "network_process_native_library_attempts": DENIED,
        "owned_import_fixture_bytes": fixture_bytes,
        "windows_fixture_files_created": 0,
        "private_input_bytes_read": 0,
        "native_windows_execution": False,
    }
    (ROOT / "isolated-import-evidence.json").write_bytes(
        (json.dumps(result, sort_keys=True, indent=2) + "\n").encode()
    )
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in {"cases", "source_hashes"}
            },
            sort_keys=True,
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
