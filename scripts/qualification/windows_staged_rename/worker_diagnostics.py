"""Bounded diagnostic text only; never case results or native qualification."""

import hashlib
import json
import re

STREAM_CAP = 32768
MESSAGE_CAP = 2048


def safe_text(value, substitutions=()):
    try:
        text = str(value)
    except BaseException:  # noqa: BLE001 - Broken exception formatting cannot defeat custody.
        text = "[unprintable diagnostic]"
    for original, label in substitutions:
        text = text.replace(str(original), label)
    text = re.sub(r"(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]+", "[TOKEN]", text)
    text = re.sub(r"(?i)Bearer\s+[^\s]+", "Bearer [TOKEN]", text)
    text = "".join(
        char if char in "\n\t" or 32 <= ord(char) < 127 else "?" for char in text
    )
    return text[:MESSAGE_CAP]


def exception_record(error, phase, substitutions=()):
    # Walk metadata only; no linecache/source reads. Keep six bounded frames.
    frames, current, frames_seen = [], error.__traceback__, 0
    for _ in range(32):
        if current is None:
            break
        frames_seen += 1
        code = current.tb_frame.f_code
        frames.append(
            {
                "file": safe_text(code.co_filename, substitutions)[:256],
                "function": safe_text(code.co_name)[:64],
                "line": current.tb_lineno,
            }
        )
        frames = frames[-6:]
        current = current.tb_next
    return {
        "schema": "millefeuille.worker-entry-error/v1",
        "phase": safe_text(phase)[:64],
        "type": type(error).__name__[:64],
        "frames": frames,
        "frames_truncated": frames_seen > 6 or current is not None,
        "message": safe_text(error, substitutions),
        "case_outcomes_established": False,
        "native_qualified": False,
    }


class BoundedStreams:
    def __init__(self):
        self.buffers = {"stdout": bytearray(), "stderr": bytearray()}
        self.overflow = False
        self.eof = {"stdout": False, "stderr": False}

    def allowance(self, name):
        return STREAM_CAP - len(self.buffers[name])

    def accept(self, name, value):
        if not isinstance(value, bytes):
            raise TypeError("stream bytes required")
        if self.overflow:
            raise RuntimeError("capture overflow; no further bytes accepted")
        remaining = self.allowance(name)
        self.buffers[name].extend(value[:remaining])
        if len(value) > remaining:
            self.overflow = True
            raise RuntimeError("bounded worker diagnostic stream exceeded")

    def record(self, substitutions=()):
        result = {
            "schema": "millefeuille.controller-captured-worker-streams/v1",
            "retention_bytes_per_stream_max": STREAM_CAP,
            "overflow": self.overflow,
            "case_outcomes_established": False,
            "streams": {},
        }
        for name, data in self.buffers.items():
            result["streams"][name] = {
                "bytes_retained": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "text": safe_text(
                    bytes(data).decode("utf-8", "replace"), substitutions
                ),
                "text_bytes_max": MESSAGE_CAP,
                "eof_observed": self.eof[name],
            }
        return result


def emit_error(error, phase, stream, substitutions=()):
    record = exception_record(error, phase, substitutions)
    stream.write(json.dumps(record, sort_keys=True) + "\n")
    stream.flush()
    return record
