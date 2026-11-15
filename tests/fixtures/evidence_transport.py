"""Stdlib-only bounded log transport. No production imports."""

import base64
import hashlib
import json
import os
import re
import sys
import time

PREFIX = "MILLEFEUILLE_EVIDENCE_FRAME_V1:"
MAX_RECEIPT_BYTES = 3 * 1024 * 1024
MAX_LINE_BYTES = 8192
CHUNK = 5400
MAX_FRAMES = 600
MAX_RECEIPTS = 64
MAX_NOISE_LINES = 32
MAX_NOISE_BYTES = 16 * 1024
FRAME_MARKER = b"MILLEFEUILLE_EVIDENCE"
TIMESTAMP = re.compile(rb"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z ")
PROGRESS = re.compile(rb"test_[A-Za-z0-9_]+ \([^\r\n()]{1,512}\) \.{3} ")


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse(payload):
    if len(payload) > MAX_RECEIPT_BYTES:
        raise ValueError("receipt size")
    value = json.loads(
        payload,
        object_pairs_hook=_pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
    )
    if not isinstance(value, dict) or canonical(value) != payload:
        raise ValueError("noncanonical receipt")
    return value


def runtime_identity():
    return {
        **{
            k: os.environ.get(k)
            for k in ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "RUNNER_OS")
        },
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


def frame_receipt(kind: str, receipt: dict) -> list[str]:
    if kind not in ("F05", "S04"):
        raise ValueError("kind")
    payload = canonical(receipt)
    if not payload or len(payload) > MAX_RECEIPT_BYTES:
        raise ValueError("receipt size")
    digest = hashlib.sha256(payload).hexdigest()
    count = (len(payload) + CHUNK - 1) // CHUNK
    frames = []
    for index in range(count):
        frame = {
            "kind": kind,
            "id": digest,
            "sha256": digest,
            "index": index,
            "count": count,
            "byte_length": len(payload),
            "payload": base64.b64encode(
                payload[index * CHUNK : (index + 1) * CHUNK]
            ).decode("ascii"),
        }
        line = PREFIX + canonical(frame).decode("ascii")
        if len(line.encode("utf-8")) > MAX_LINE_BYTES:
            raise ValueError("frame bound")
        frames.append(line)
    return frames


class Reassembler:
    """Strict ordered frames; complete duplicates remain visible to caller."""

    def __init__(self):
        self.pending = None
        self.parts = []
        self.completed = 0
        self.noise_lines = self.noise_bytes = 0
        self.started = time.monotonic()

    def feed(self, line):
        if time.monotonic() - self.started > 30:
            raise ValueError("transport time bound")
        # Accept observed timestamp/progress envelopes, not arbitrary prefixes.
        raw = line.rstrip(b"\r\n")
        stamp = TIMESTAMP.match(raw)
        if stamp:
            raw = raw[stamp.end() :]
        progress = PROGRESS.match(raw)
        if progress:
            raw = raw[progress.end() :]
        if not raw.startswith(PREFIX.encode()):
            if (
                FRAME_MARKER in line
                or b"EVIDENCE_FRAME" in line
                or b"FRAME_V1:" in line
            ):
                raise ValueError("malformed frame marker/envelope")
            if self.pending is not None:
                self.noise_lines += 1
                self.noise_bytes += len(line)
                if (
                    self.noise_lines > MAX_NOISE_LINES
                    or self.noise_bytes > MAX_NOISE_BYTES
                ):
                    raise ValueError("interframe noise bound")
            return None
        if len(raw) > MAX_LINE_BYTES or raw.count(PREFIX.encode()) != 1:
            raise ValueError("frame line bound")
        frame = parse(raw[len(PREFIX) :])
        if set(frame) != {
            "kind",
            "id",
            "sha256",
            "index",
            "count",
            "byte_length",
            "payload",
        }:
            raise ValueError("frame schema")
        if frame["kind"] not in ("F05", "S04"):
            raise ValueError("frame kind")
        for key in ("index", "count", "byte_length"):
            if type(frame[key]) is not int:
                raise ValueError("frame integer")
        if not 1 <= frame["byte_length"] <= MAX_RECEIPT_BYTES:
            raise ValueError("receipt bound")
        if not 1 <= frame["count"] <= MAX_FRAMES:
            raise ValueError("frame count")
        if frame["count"] != (frame["byte_length"] + CHUNK - 1) // CHUNK:
            raise ValueError("frame count/length")
        identity = tuple(
            frame[k] for k in ("kind", "id", "sha256", "count", "byte_length")
        )
        if self.pending is None:
            self.pending = identity
            self.noise_lines = self.noise_bytes = 0
        if identity != self.pending or frame["index"] != len(self.parts):
            raise ValueError("reordered/conflicting frames")
        chunk = base64.b64decode(frame["payload"], validate=True)
        expected = min(CHUNK, frame["byte_length"] - frame["index"] * CHUNK)
        if (
            len(chunk) != expected
            or base64.b64encode(chunk).decode() != frame["payload"]
        ):
            raise ValueError("chunk length/encoding")
        self.parts.append(chunk)
        if len(self.parts) != frame["count"]:
            return None
        payload = b"".join(self.parts)
        digest = hashlib.sha256(payload).hexdigest()
        if digest != frame["sha256"] or digest != frame["id"]:
            raise ValueError("receipt hash")
        receipt = parse(payload)
        self.completed += 1
        if self.completed > MAX_RECEIPTS:
            raise ValueError("receipt count bound")
        self.pending, self.parts = None, []
        return frame["kind"], payload, receipt

    def finish(self):
        if self.pending is not None:
            raise ValueError("truncated receipt")
