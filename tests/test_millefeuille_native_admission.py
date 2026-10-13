"""Offline negative admission checks; no Windows APIs or child are invoked."""

import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / ".github/scripts/native_windows/controller.py"


def assertion_for(message):
    tree = ast.parse(CONTROLLER.read_text(encoding="utf-8"))
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assert)
        and node.msg is not None
        and (message in ast.unparse(node.msg))
    )


def check_guard(message, **values):
    node = assertion_for(message)
    code = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(code, str(CONTROLLER), "exec"), values)


class NativeAdmissionOfflineTests(unittest.TestCase):
    def test_platform_and_abi_fail_with_named_diagnostics(self):
        with self.assertRaisesRegex(AssertionError, "requires native Windows"):
            check_guard(
                "requires native Windows",
                os=SimpleNamespace(name="posix"),
                sys=SimpleNamespace(platform="darwin"),
            )
        for maxsize, size in ((2**31, 144), (2**63, 120)):
            with (
                self.subTest(maxsize=maxsize, size=size),
                self.assertRaisesRegex(AssertionError, "Windows ABI mismatch"),
            ):
                check_guard(
                    "Windows ABI mismatch",
                    sys=SimpleNamespace(maxsize=maxsize),
                    ctypes=SimpleNamespace(sizeof=lambda value, size=size: size),
                    ExtendedLimit=object,
                )

    def test_route_and_budget_guards_remain_fail_closed(self):
        for route, clearance in (("", "approval"), ("route", "  ")):
            with (
                self.subTest(route=route, clearance=clearance),
                self.assertRaisesRegex(AssertionError, "binding is empty"),
            ):
                check_guard(
                    "binding is empty",
                    args=SimpleNamespace(route_id=route, owner_clearance_id=clearance),
                )
        with self.assertRaisesRegex(AssertionError, "below retained floor"):
            check_guard(
                "below retained floor", args=SimpleNamespace(carried_scratch_bytes=1)
            )
        with self.assertRaisesRegex(AssertionError, "exceeds ceiling"):
            check_guard(
                "plus reserve exceeds ceiling",
                args=SimpleNamespace(carried_scratch_bytes=268435456),
                RESERVE=33554432,
                SCRATCH_CEILING=268435456,
            )

    def test_input_paths_reject_relative_and_traversal(self):
        with self.assertRaisesRegex(AssertionError, "absolute and local"):
            check_guard("absolute and local", path=Path("relative"))
        with self.assertRaisesRegex(AssertionError, "contains traversal"):
            check_guard("input path contains traversal", path=Path("/safe/../outside"))

    def test_exact_pins_accept_lf_and_reject_crlf_or_tamper(self):
        manifest = json.loads((CONTROLLER.parent / "manifest.json").read_text())
        records = manifest["sources"] + [manifest["worker"]]
        self.assertEqual(len(records), 6)
        for record in records:
            path = ROOT / record["path"]
            with self.subTest(path=record["path"]):
                check_guard(
                    "source size mismatch",
                    path=path,
                    relative=record["path"],
                    record=record,
                )
                check_guard(
                    "source hash mismatch",
                    path=path,
                    relative=record["path"],
                    record=record,
                    digest=lambda target: hashlib.sha256(
                        target.read_bytes()
                    ).hexdigest(),
                )
                for mutation in (
                    path.read_bytes().replace(b"\n", b"\r\n"),
                    path.read_bytes() + b"tamper",
                ):
                    with self.assertRaisesRegex(AssertionError, "source hash mismatch"):
                        check_guard(
                            "source hash mismatch",
                            path=path,
                            relative=record["path"],
                            record=record,
                            digest=lambda target, mutation=mutation: hashlib.sha256(
                                mutation
                            ).hexdigest(),
                        )

    def test_workflow_route_and_native_control_requirements_are_preserved(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn(
            "github-actions:${env:GITHUB_RUN_ID}:${env:GITHUB_RUN_ATTEMPT}", workflow
        )
        tree = ast.parse(CONTROLLER.read_text())
        guards = [
            ast.unparse(node.test)
            for node in ast.walk(tree)
            if isinstance(node, ast.Assert)
        ]
        self.assertIn(
            "result['platform'] == 'win32' and result['tests_run'] == 3", guards
        )
        self.assertIn(
            "result['failures'] == result['errors'] == result['skips'] == 0", guards
        )
