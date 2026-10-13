"""Portable launch contracts; real pwsh/CMD/Python fixtures require Windows CI."""

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).absolute().parent
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
import sdk_bootstrap as sdk  # noqa: E402 - Explicit owned -I script root.

COMSPEC = r"C:\Windows\System32\cmd.exe"
VCVARS = (
    r"C:\Program Files (x86)\Microsoft Visual Studio\2026\Build Tools"
    r"\VC\Auxiliary\Build\vcvars64.bat"
)
SOURCE = r"D:\a\repo with spaces & (group) !\references\sdk_rename_reference.c"
BUILD = r"D:\a\_temp\SDK build with spaces & (group) !"
OBJECT = BUILD + r"\mf_sdk_rename.obj"
DLL = BUILD + r"\mf_sdk_rename.dll"
SPACE_VALUE = "argument with spaces & (group) !"
TAIL_VALUE = "C:\\fixture with spaces\\"
QUOTE_VALUE = 'quoted "value"'
EXPECTED_PAYLOAD = (
    'call "C:\\Program Files (x86)\\Microsoft Visual Studio\\2026\\Build Tools'
    '\\VC\\Auxiliary\\Build\\vcvars64.bat"'
    ' >nul && cl.exe /nologo /W4 /WX /LD /MT "D:\\a\\repo with spaces & (group) !'
    '\\references\\sdk_rename_reference.c"'
    ' /Fo"D:\\a\\_temp\\SDK build with spaces & (group) !\\mf_sdk_rename.obj"'
    ' /link /OUT:"D:\\a\\_temp\\SDK build with spaces & (group) !\\mf_sdk_rename.dll"'
)
RECORDER = """import json, os, subprocess, sys
from pathlib import Path
mode, output, *values = sys.argv[1:]
if mode == "spawn":
    # Same list2cmdline/Windows CRT path used by the unchanged worker launcher.
    child = [sys.executable, "-I", "-B", __file__, "capture", output, *values]
    raise SystemExit(subprocess.run(child, shell=False, check=False).returncode)
if mode != "capture":
    raise RuntimeError("fixed capture mode required")
with Path(output).open("x", encoding="utf-8") as stream:
    row = {"values": values, "marker": os.environ.get("MF_SDK_LAUNCH_FIXTURE")}
    json.dump(row, stream)
"""
BATCH = (
    '@echo off\r\nset "MF_SDK_LAUNCH_FIXTURE=environment spaces & (group) !"\r\n'
    "exit /b 0\r\n"
)


class BootstrapContracts(unittest.TestCase):
    def test_exact_sdk_command_with_spaces_and_shell_path_characters(self):
        self.assertEqual(sdk.sdk_payload(VCVARS, SOURCE, OBJECT, DLL), EXPECTED_PAYLOAD)

    def test_one_raw_cmd_outer_pair_without_crt_escape_characters(self):
        expected = (
            '"C:\\Windows\\System32\\cmd.exe" /d /v:off /s /c "'
            + EXPECTED_PAYLOAD
            + '"'
        )
        self.assertEqual(sdk.raw_cmd_line(COMSPEC, EXPECTED_PAYLOAD), expected)
        self.assertNotIn('\\"', expected)
        # CMD /S removes only the first/last command quote pair.
        tail = expected.split(" /c ", 1)[1]
        self.assertEqual(tail[1:-1], EXPECTED_PAYLOAD)

    def test_expansion_and_command_line_injection_paths_refuse(self):
        for path in [
            r"C:\%TEMP%\vcvars.bat",
            r"C:\caret^\vcvars.bat",
            'C:\\bad"path',
            "C:\\tab\tpath",
            "C:\\line\npath",
            "C:\\nul\0path",
        ]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                sdk.cmd_path(path)

    def test_relative_unc_and_traversal_paths_refuse(self):
        for path in [
            "vcvars.bat",
            r"C:vcvars.bat",
            r"\\host\share\vcvars.bat",
            r"\\?\C:\vcvars.bat",
            r"C:\safe\..\vcvars.bat",
        ]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                sdk.cmd_path(path)

    def test_multiline_payload_refuses(self):
        for payload in ["", "echo one\r\necho two", "echo one\0"]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                sdk.raw_cmd_line(COMSPEC, payload)

    def test_actual_non_windows_runtime_refuses_without_launch(self):
        if os.name == "nt":
            self.skipTest("this refusal is specifically the portable host boundary")
        with mock.patch.object(sdk.subprocess, "run") as launch:
            with self.assertRaises(RuntimeError):
                sdk.run_cmd_payload(COMSPEC, EXPECTED_PAYLOAD, BUILD)
            launch.assert_not_called()

    def test_subprocess_boundary_is_string_executable_and_shell_false(self):
        # This verifies the call contract only, never Windows parsing or success.
        with (
            mock.patch.object(sdk.os, "name", "nt"),
            mock.patch.object(sdk.sys, "platform", "win32"),
            mock.patch.object(
                sdk.subprocess, "run", return_value=mock.Mock(returncode=17)
            ) as launch,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            code = sdk.run_cmd_payload(COMSPEC, EXPECTED_PAYLOAD, BUILD)
        self.assertEqual(code, 17)
        args, kwargs = launch.call_args
        self.assertEqual(args, (sdk.raw_cmd_line(COMSPEC, EXPECTED_PAYLOAD),))
        self.assertEqual(kwargs["executable"], COMSPEC)
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["cwd"], BUILD)
        self.assertFalse(
            json.loads(output.getvalue())["crt_argument_serialization_applied_to_cmd"]
        )

    def test_fixture_source_never_compiles_or_dispatches_native_controls(self):
        self.assertNotIn("cl.exe", RECORDER)
        self.assertNotIn("ctypes", RECORDER)
        self.assertNotIn("NtSetInformationFile", RECORDER)


def fixture_paths(root):
    return [
        root / name
        for name in (
            "sdk_bootstrap.py",
            "test_sdk_bootstrap.py",
            "argv recorder.py",
            "fake SDK environment.bat",
            "fake SDK failing environment.bat",
        )
    ]


def prepare_fixture(root, source):
    if os.name != "nt":
        raise RuntimeError("fixture preparation requires the Windows CI shell")
    root.mkdir()
    records = {
        "sdk_bootstrap.py": (source / "sdk_bootstrap.py").read_bytes(),
        "test_sdk_bootstrap.py": (source / "test_sdk_bootstrap.py").read_bytes(),
        "argv recorder.py": RECORDER.encode("utf-8"),
        "fake SDK environment.bat": BATCH.encode("ascii"),
        "fake SDK failing environment.bat": BATCH.replace(
            "exit /b 0", "exit /b 17"
        ).encode("ascii"),
    }
    if sum(map(len, records.values())) + 4096 > 65536:
        raise RuntimeError("launcher fixture reservation exceeds 64 KiB")
    for name, data in records.items():
        with (root / name).open("xb") as output:
            output.write(data)
    print(
        json.dumps(
            {
                "launcher_fixture_prepared": True,
                "file_bytes": sum(map(len, records.values())),
                "native_controls_executed": 0,
            }
        )
    )


def windows_shell_fixture(root, source, values):
    if os.name != "nt" or sys.platform != "win32" or sys.version_info[:2] != (3, 13):
        raise RuntimeError("real fixture requires Windows Python 3.13 from CI pwsh")
    if values != [SPACE_VALUE, TAIL_VALUE, QUOTE_VALUE]:
        raise RuntimeError("pwsh to Python argv did not round-trip")
    for name in ("sdk_bootstrap.py", "test_sdk_bootstrap.py"):
        if (root / name).read_bytes() != (source / name).read_bytes():
            raise RuntimeError("fixture source copy changed")
    recorder, batch = root / "argv recorder.py", root / "fake SDK environment.bat"
    if recorder.read_bytes() != RECORDER.encode() or batch.read_bytes() != BATCH.encode(
        "ascii"
    ):
        raise RuntimeError("owned fixture source changed")
    # Logical argument values may contain quotes; these go to Python's CRT parser.
    # list2cmdline belongs HERE, not around the CMD /C payload.
    extra = [str(root / "source file with spaces.c"), str(root) + "\\"]
    expected = values + extra
    for mode in ("capture", "spawn"):
        result = root / (mode + " argv result.json")
        python_command = subprocess.list2cmdline(
            [sys.executable, "-I", "-B", str(recorder), mode, str(result), *expected]
        )
        payload = "call " + sdk.cmd_path(str(batch)) + " >nul && " + python_command
        code = sdk.run_cmd_payload(os.environ["COMSPEC"], payload, root)
        if code != 0:
            raise RuntimeError("CMD fixture child failed")
        row = json.loads(result.read_bytes())
        if row != {"values": expected, "marker": "environment spaces & (group) !"}:
            raise RuntimeError("CMD environment or Python argv did not round-trip")
    failing_batch = root / "fake SDK failing environment.bat"
    if failing_batch.read_bytes() != BATCH.replace("exit /b 0", "exit /b 17").encode(
        "ascii"
    ):
        raise RuntimeError("owned failing batch changed")
    failed_output = root / "must not be created.json"
    python_command = subprocess.list2cmdline(
        [sys.executable, "-I", "-B", str(recorder), "capture", str(failed_output)]
    )
    payload = "call " + sdk.cmd_path(str(failing_batch)) + " >nul && " + python_command
    if sdk.run_cmd_payload(os.environ["COMSPEC"], payload, root) != 17:
        raise RuntimeError("CMD batch failure status was not preserved")
    if failed_output.exists():
        raise RuntimeError("failed batch did not stop the chained child")
    # The fake batch's environment must stay in that one CMD process tree.
    if os.environ.get("MF_SDK_LAUNCH_FIXTURE") is not None:
        raise RuntimeError("fixture environment escaped its child process")
    files = fixture_paths(root) + [
        root / (mode + " argv result.json") for mode in ("capture", "spawn")
    ]
    total = sum(path.stat().st_size for path in files)
    if total > 65536:
        raise RuntimeError("launcher fixture exceeds 64 KiB")
    print(
        json.dumps(
            {
                "schema": "millefeuille.actual-ci-shell-launcher-fixture/v1",
                "passed": True,
                "pwsh_python_argv_matched": True,
                "cmd_batch_environment_matched": True,
                "cmd_python_argv_matched": True,
                "python_child_crt_argv_matched": True,
                "failed_batch_stopped_child_and_preserved_status": True,
                "fixture_bytes": total,
                "native_controls_executed": 0,
                "compiler_executed_by_fixtures": False,
            }
        )
    )


def fixture_main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-windows-shell-fixture", action="store_true")
    parser.add_argument("--windows-shell-fixture", action="store_true")
    parser.add_argument("--fixture-root", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--space-value")
    parser.add_argument("--tail-value")
    parser.add_argument("--quote-value")
    args = parser.parse_args()
    if args.prepare_windows_shell_fixture == args.windows_shell_fixture:
        raise RuntimeError("exactly one fixture mode required")
    if args.prepare_windows_shell_fixture:
        prepare_fixture(args.fixture_root, args.source_root)
    else:
        windows_shell_fixture(
            args.fixture_root,
            args.source_root,
            [args.space_value, args.tail_value, args.quote_value],
        )


if __name__ == "__main__":
    if any(
        flag in sys.argv
        for flag in ("--prepare-windows-shell-fixture", "--windows-shell-fixture")
    ):
        fixture_main()
    else:
        unittest.main()
