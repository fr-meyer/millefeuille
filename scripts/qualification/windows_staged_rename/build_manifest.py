"""Prepare a fresh source/installed-SDK-DLL manifest. Does not run native APIs."""

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).absolute().parent))

from diagnostic_core import FILE_CAP, SCHEMA, SOURCE_FILES
from staged_replacement import Refusal


def plain_path(path, *, directory=False):
    if not path.is_absolute() or ".." in path.parts or str(path).startswith("\\\\"):
        raise Refusal("absolute plain local path required")
    for parent in path.parents:
        info = parent.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
        ):
            raise Refusal("unsafe path ancestor")
    info = path.lstat()
    if (
        getattr(info, "st_file_attributes", 0) & 0x400
        or (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        is False
    ):
        raise Refusal("unsafe source path kind")
    if not directory and info.st_nlink != 1:
        raise Refusal("source must be a single-link file")
    return info


def read_plain(path, cap):
    before = plain_path(path)
    if not isinstance(cap, int) or cap < 0 or before.st_size > cap:
        raise Refusal("source size exceeds bound")
    # Windows lacks O_NOFOLLOW here: the trusted/cooperating-owner contract and
    # pre/open/post identity checks apply; this is not hostile-ancestor confinement.
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = os.open(path, flags)

    def identity(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink)

    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or identity(before) != identity(opened)
        ):
            raise Refusal("opened source binding mismatch")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            data = source.read(cap + 1)
        held_after, after = os.fstat(descriptor), plain_path(path)
        if (
            len(data) > cap
            or identity(before) != identity(held_after)
            or identity(before) != identity(after)
            or len(data) != before.st_size
        ):
            raise Refusal("source changed during bounded held-descriptor read")
        return data
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--sdk-dll", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not re.fullmatch("[0-9a-f]{40}", args.expected_head):
        raise Refusal("exact lowercase Git head required")
    plain_path(args.source_root, directory=True)
    records = []
    for relative in SOURCE_FILES:
        data = read_plain(args.source_root / relative, FILE_CAP)
        records.append(
            {
                "path": relative,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    sdk = read_plain(args.sdk_dll, 524288)
    manifest = {
        "schema": SCHEMA,
        "expected_head": args.expected_head,
        "files": records,
        "sdk": {
            "path": str(args.sdk_dll),
            "bytes": len(sdk),
            "sha256": hashlib.sha256(sdk).hexdigest(),
        },
        "custody_label_is_not_human_authorization": True,
        "production_windows_writes_enabled": False,
    }
    plain_path(args.output.parent, directory=True)
    if not args.output.is_absolute() or ".." in args.output.parts:
        raise Refusal("plain absolute manifest output required")
    with args.output.open("xb") as output:
        output.write(
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        )


if __name__ == "__main__":
    main()
