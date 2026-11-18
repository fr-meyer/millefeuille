"""Fixed isolated HTTP child. Credentials/payload/results use anonymous pipes only."""

import asyncio
import json
from pathlib import Path
import struct
import sys

# Isolated Python excludes cwd/environment imports; bind this exact source tree.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _main():
    from millefeuille.domain import writeback_http as h
    from millefeuille.domain.writeback_core import _wire

    try:
        header = sys.stdin.buffer.read(4)
        if len(header) != 4:
            raise ValueError()
        size = struct.unpack("!I", header)[0]
        if size > 131072:
            raise ValueError()
        raw = sys.stdin.buffer.read(size)
        if len(raw) != size or sys.stdin.buffer.read(1):
            raise ValueError()
        payload = json.loads(raw, object_pairs_hook=h._pairs)
        if set(payload) != {
            "method",
            "identity",
            "deadline",
            "secret",
            "body",
            "expected_version",
        }:
            raise ValueError()
        if payload["method"] not in ("GET", "PATCH", "GET-COLLECTION"):
            raise ValueError()
        boundary = h._HTTPBoundary(payload.pop("secret"))
        body = payload.pop("body")
        kwargs = (
            {}
            if payload["method"] in ("GET", "GET-COLLECTION")
            else {
                "body": body.encode(),
                "expected_version": payload["expected_version"],
            }
        )
        result = asyncio.run(
            boundary._exchange(
                payload["method"],
                h._identity(payload["identity"]),
                payload["deadline"],
                **kwargs,
            )
        )
        if isinstance(result, h._Acknowledgement):
            result = {"version": result.version, "status": result.status}
        response = {"kind": "ok", "result": result}
    except h._DefinitiveRefusalError:
        response = {"kind": "refused"}
    except BaseException:
        response = {"kind": "unconfirmed"}
    wire = _wire(response)
    if len(wire) > 200000:
        wire = b'{"kind":"unconfirmed"}'
    sys.stdout.buffer.write(struct.pack("!I", len(wire)) + wire)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    _main()
