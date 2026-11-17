"""Targeted installed-wheel regression suite; never adds the checkout to sys.path."""

import collections
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import unittest
from unittest.mock import patch
import zipfile


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def flat(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flat(item)
        else:
            yield item


def outcome(code, data, policy):
    terminal = [e for e in data["events"] if e["outcome"] != "subtest-error"]
    skips = [
        {"id": e["id"], "reason": e["reason"]}
        for e in terminal
        if e["outcome"] == "skip"
    ]
    valid = (
        data["tests"] == len(policy["expected_ids"])
        and collections.Counter(e["id"] for e in terminal)
        == collections.Counter(policy["expected_ids"])
        and sorted(skips, key=lambda e: e["id"]) == policy["skips"]
        and all(e["outcome"] in ("ok", "skip") for e in data["events"])
        and data["success"]
    )
    return code if code else int(not valid)


def main():
    root, wheel, kind, head = sys.argv[1:]
    root, wheel = Path(root).resolve(), Path(wheel).resolve()
    ci = root / ".github/ci"
    guard = load("installed_guard", ci / "installed_guard.py")
    need, sha = guard.need, guard.sha
    policy = json.loads((ci / "installed-policy.json").read_text())
    pins = json.loads((ci / "source-pins.json").read_text())
    need(sys.flags.isolated and sys.dont_write_bytecode, "requires -I -B")
    need(sys.platform == "linux" and os.getuid() != 0, "Linux normal user required")
    need(
        subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip()
        == head,
        "source head mismatch",
    )
    for name, digest in pins["ci_inputs"].items():
        need(sha(root / name) == digest, "CI input drift")
    package = {n: h for n, h in pins["files"].items() if n.startswith("millefeuille/")}
    with zipfile.ZipFile(wheel) as archive:
        actual = {
            n: hashlib.sha256(archive.read(n)).hexdigest()
            for n in archive.namelist()
            if n.startswith("millefeuille/") and not n.endswith("/")
        }
    need(actual == package, "wheel package inventory mismatch")
    site = Path(sysconfig.get_path("purelib")).resolve()
    installed_names = {
        str(p.relative_to(site))
        for p in (site / "millefeuille").rglob("*")
        if p.is_file() and p.suffix != ".pyc"
    }
    need(installed_names == set(package), "extra or missing installed package file")
    for name, digest in package.items():
        need(not (site / name).is_symlink(), "installed package symlink")
        need(sha(site / name) == digest, "installed package mismatch")
    output = root / ".ci-output"
    output.mkdir(exist_ok=True)
    receipt = output / ("installed-suite-" + kind + ".json")
    report = {
        "source_head": head,
        "kind": kind,
        "wheel_sha256": sha(wheel),
        "policy_sha256": sha(ci / "installed-policy.json"),
        "package_files": len(package),
        "fixture_hashes": policy["fixtures"],
        "expected_ids": policy["expected_ids"],
        "status": "INCOMPLETE",
    }
    code = 1
    with tempfile.TemporaryDirectory(prefix="mi-", dir="/tmp") as temporary:
        scratch = Path(temporary)
        need(not scratch.is_relative_to(root), "scratch overlaps checkout")
        fixtures = scratch / "fixtures"
        fixtures.mkdir()
        for name, digest in policy["fixtures"].items():
            need(name.startswith(("tests/", "specs/")), "unsafe fixture name")
            need(".." not in Path(name).parts, "unsafe fixture traversal")
            need(sha(root / name) == digest, "fixture source drift")
            target = fixtures / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / name, target)
        helpers = scratch / "helpers"
        helpers.mkdir()
        for name in ("installed_guard.py", "suite_runner.py", "suite_observations.py"):
            shutil.copyfile(ci / name, helpers / name)
        origins = scratch / "origins"
        origins.mkdir()
        config = {
            "site": str(site),
            "fixtures": str(fixtures),
            "package": package,
            "origins": str(origins),
        }
        # Only copied helpers/fixtures and interpreter-derived paths are admitted.
        guard = load("copied_guard", helpers / "installed_guard.py")
        os.chdir(fixtures)
        os.environ["TMPDIR"] = str(scratch)
        os.environ["HOME"] = str(scratch)
        tempfile.tempdir = str(scratch)
        original = guard.install(config)
        try:
            from millefeuille.domain import _failure_observations as diagnostics
            from millefeuille.domain import secure_io

            need(diagnostics.token() is None, "observer not default OFF")
            runner = load("installed_runner", helpers / "suite_runner.py")
            loader = unittest.TestLoader()
            suite = loader.loadTestsFromNames(policy["modules"])
            ids = [test.id() for test in flat(suite)]
            report["discovered_ids"] = ids
            need(
                not loader.errors and sorted(ids) == policy["expected_ids"],
                "exact installed discovery mismatch",
            )
            ledger = {
                "schema": "qualification-suite-ledger/v1",
                "occurrences": ids,
                "skips": policy["skips"],
                "capabilities": runner.obs.profile(secure_io._supports_no_follow()),
            }
            suite_receipt = output / ("installed-events-" + kind + ".json")
            with patch.object(unittest.TestLoader, "discover", return_value=suite):
                code = runner.main({"ledger": ledger, "receipt": str(suite_receipt)})
            data = json.loads(suite_receipt.read_text())
            report["suite"] = data
            report["runner_exit"] = code
            code = outcome(code, data, policy)
            # Preserve actual test result before any later provenance failure.
            receipt.write_text(json.dumps(report, indent=2) + chr(10))
            for name, digest in policy["fixtures"].items():
                need(sha(fixtures / name) == digest, "fixture changed during tests")
            for name, digest in package.items():
                need(sha(site / name) == digest, "installed bytes changed")
            need(
                not list(origins.glob("*.violation")),
                "child application shadow rejected",
            )
            rows = [
                json.loads(line)
                for p in sorted(origins.glob("*.jsonl"))
                for line in p.read_text().splitlines()
            ]
            for name, module in list(sys.modules.items()):
                if name == "millefeuille" or name.startswith("millefeuille."):
                    guard.check_origin(name, module.__file__, config)
            for row in rows:
                guard.check_origin(row["module"], row["origin"], config)
            report["imports"] = rows
            report["import_processes"] = len(list(origins.glob("*.jsonl")))
            report["status"] = "PASS" if code == 0 else "FAIL"
        except BaseException as exc:
            report["verification_error_type"] = type(exc).__name__
            report["status"] = "FAIL"
            raise
        finally:
            subprocess.Popen = original
            receipt.write_text(json.dumps(report, indent=2) + chr(10))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
