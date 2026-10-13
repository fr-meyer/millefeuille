"""Launch the installed SDK through one explicit CMD boundary, never CRT quoting."""

import argparse
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys


def cmd_path(value):
    """Quote a local absolute path; percent expansion cannot be quoted away."""
    if not isinstance(value, str):
        raise ValueError("CMD path must be text")
    path = PureWindowsPath(value)
    if (
        not path.is_absolute()
        or not path.drive.endswith(":")
        or value.startswith("\\\\")
        or ".." in path.parts
        or any(ord(char) < 32 for char in value)
        or any(char in value for char in ('"', "%", "^"))
    ):
        raise ValueError("plain absolute CMD path required; expansion tokens refused")
    return '"' + value + '"'


def sdk_payload(vcvars, source, object_file, dll):
    # CALL returns from the environment batch file into this same CMD process.
    return (
        "call "
        + cmd_path(vcvars)
        + " >nul && cl.exe /nologo /W4 /WX /LD /MT "
        + cmd_path(source)
        + " /Fo"
        + cmd_path(object_file)
        + " /link /OUT:"
        + cmd_path(dll)
    )


def raw_cmd_line(comspec, payload):
    if not payload or any(char in payload for char in ("\0", "\r", "\n")):
        raise ValueError("single fixed CMD payload required")
    # /S removes this one outer quote pair. Inner path quotes reach CMD intact.
    # /V:OFF prevents ! expansion, including in a quoted fixture/build path.
    return cmd_path(comspec) + ' /d /v:off /s /c "' + payload + '"'


def run_cmd_payload(comspec, payload, cwd):
    if os.name != "nt" or sys.platform != "win32":
        raise RuntimeError("actual CMD launch requires Windows")
    command = raw_cmd_line(comspec, payload)
    print(
        json.dumps(
            {
                "schema": "millefeuille.raw-sdk-command/v1",
                "python_argv": sys.argv,
                "executable": comspec,
                "raw_create_process_command_line": command,
                "cmd_payload": payload,
                "cwd": str(cwd),
                "shell": False,
                "crt_argument_serialization_applied_to_cmd": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    # A STRING is intentional: subprocess leaves it unchanged for CreateProcessW.
    # A list/list2cmdline would apply Python/CRT quoting to CMD's different parser.
    result = subprocess.run(
        command,
        executable=comspec,
        shell=False,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vcvars", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--build", required=True)
    args = parser.parse_args()
    if os.name != "nt" or sys.platform != "win32" or sys.version_info[:2] != (3, 13):
        raise RuntimeError("bootstrap requires the configured Windows Python 3.13")
    comspec = os.environ["COMSPEC"]
    build = Path(args.build)
    for path in (comspec, args.vcvars, args.source, args.build):
        cmd_path(path)
    if not build.is_dir() or not all(
        Path(path).is_file() for path in (comspec, args.vcvars, args.source)
    ):
        raise RuntimeError("installed SDK/source/build paths required")
    obj, dll = build / "mf_sdk_rename.obj", build / "mf_sdk_rename.dll"
    if obj.exists() or dll.exists():
        raise RuntimeError("SDK output collision")
    return run_cmd_payload(
        comspec, sdk_payload(args.vcvars, args.source, str(obj), str(dll)), build
    )


if __name__ == "__main__":
    raise SystemExit(main())
