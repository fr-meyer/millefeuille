"""Bounded diagnostic text only; never case results or native qualification."""

import hashlib
import json
import re

from staged_replacement import Refusal

STREAM_CAP = 32768
MESSAGE_CAP = 2048


ADMISSION_LINE_CAP = 1792
MAX_DIAGNOSTIC_PIDS = 32
U32_MAX = (1 << 32) - 1
U64_MAX = (1 << 64) - 1


def number(value, maximum=U64_MAX, minimum=0):
    """Only bounded native integers enter numeric diagnostics; no string coercion."""
    return value if type(value) is int and minimum <= value <= maximum else None


def admission_record(limits, *, origin, context=None):
    context = context or {}
    pid = number(limits.get("pid"), U32_MAX)
    member = limits.get("membership")
    member = member if type(member) is bool else None
    active = number(limits.get("active"), U32_MAX)
    assigned = number(limits.get("assigned"), U32_MAX)
    raw_ids = limits.get("pids")
    ids = None
    count = None
    if type(raw_ids) in (list, tuple):
        count = len(raw_ids)
        ids = [number(value, U32_MAX) for value in raw_ids[:MAX_DIAGNOSTIC_PIDS]]
    complete = (
        ids is not None
        and count <= MAX_DIAGNOSTIC_PIDS
        and all(value is not None for value in ids)
    )
    comparisons = {
        "membership": member,
        "active": None if active is None else active == 1,
        "assigned": None if assigned is None else assigned == 1,
        "pids": None if not complete or pid is None else ids == [pid],
    }
    result = {
        "schema": "millefeuille.job-admission-numeric/v1",
        "origin": origin
        if origin in {"worker-default-null", "controller-explicit"}
        else "unspecified",
        "expected": {"membership": True, "active": 1, "assigned": 1, "pids": [pid]},
        "observed": {
            "membership": member,
            "active": active,
            "assigned": assigned,
            "pids": ids,
            "pid_list_count": number(count, U32_MAX),
            "pids_complete": complete,
            "python_or_created_pid": pid,
            "win32_pid": number(context.get("win32_pid"), U32_MAX),
            "python_win32_pid_equal": None
            if pid is None or number(context.get("win32_pid"), U32_MAX) is None
            else pid == context["win32_pid"],
        },
        "comparisons": comparisons,
        "mismatches": [key for key, value in comparisons.items() if value is False],
        "unavailable": [key for key, value in comparisons.items() if value is None],
        "accounting": {
            "before_total": number(limits.get("total_processes"), U32_MAX),
            "before_terminated": number(limits.get("terminated"), U32_MAX),
            "after_active": number(context.get("after_active"), U32_MAX),
            "after_total": number(context.get("after_total"), U32_MAX),
            "after_terminated": number(context.get("after_terminated"), U32_MAX),
            "active_stable": None
            if active is None or number(context.get("after_active"), U32_MAX) is None
            else active == context["after_active"],
        },
        "context": {
            key: number(context.get(key), maximum, minimum)
            for key, minimum, maximum in (
                ("window_ns", 0, U64_MAX),
                ("started_ns", 0, U64_MAX),
                ("ended_ns", 0, U64_MAX),
                ("membership_raw", -(1 << 31), (1 << 31) - 1),
                ("query_error_code", 0, U32_MAX),
                ("last_query_kind", 0, U32_MAX),
                ("last_query_bytes", 0, U32_MAX),
                ("pid_query_capacity", 0, 64),
                ("pid_query_return_length", 0, U32_MAX),
                ("pid_query_attempts", 0, 3),
                ("pid_query_assigned", 0, U32_MAX),
                ("pid_query_returned", 0, U32_MAX),
                ("pid_query_ok", 0, 1),
            )
        },
        "resources": {
            key: number(limits.get(key), maximum)
            for key, maximum in (
                ("memory", U64_MAX),
                ("process_memory", U64_MAX),
                ("active_limit", U32_MAX),
                ("flags", U32_MAX),
                ("affinity", U64_MAX),
                ("child_affinity", U64_MAX),
                ("cpu_flags", U32_MAX),
                ("cpu_rate", U32_MAX),
                ("expected_cpu_rate", U32_MAX),
            )
        },
        "case_outcomes_established": False,
        "native_qualified": False,
    }
    return result


class AdmissionRefusal(Refusal):
    def __init__(self, message, limits, *, origin, context=None):
        super().__init__(message)
        self.numeric = admission_record(limits, origin=origin, context=context)


def admission_line(record):
    """Keep all composite operands in the first retained stderr text segment."""
    compact = dict(record)
    encoded = json.dumps(compact, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("ascii")) > ADMISSION_LINE_CAP:
        # Resources/API trace are supplementary. Composite values, PIDs and
        # before/after accounting remain complete, even with 32 maximum PIDs.
        compact.pop("resources", None)
        compact.pop("context", None)
        compact["supplementary_fields_omitted"] = True
        encoded = json.dumps(compact, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("ascii")) > ADMISSION_LINE_CAP:
        raise Refusal("numeric admission diagnostic exceeds fixed schema bound")
    return encoded


def emit_admission(record, stream):
    stream.write(admission_line(record) + "\n")
    stream.flush()


def safe_text(value, substitutions=()):
    try:
        text = str(value)
    except BaseException:  # noqa: BLE001 - Broken exception formatting cannot defeat custody.
        text = "[unprintable diagnostic]"
    for original, label in substitutions:
        text = text.replace(str(original), label)
    text = re.sub(r"(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]+", "[TOKEN]", text)
    text = re.sub(r"(?i)Bearer\s+[^\s]+", "Bearer [TOKEN]", text)
    text = text.replace("\r\n", "\n")
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
    record = {
        "schema": "millefeuille.worker-entry-error/v1",
        "phase": safe_text(phase)[:64],
        "type": type(error).__name__[:64],
        "frames": frames,
        "frames_truncated": frames_seen > 6 or current is not None,
        "message": safe_text(error, substitutions),
        "case_outcomes_established": False,
        "native_qualified": False,
    }
    if isinstance(error, AdmissionRefusal):
        record["job_admission"] = error.numeric
    return record


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
    if isinstance(error, AdmissionRefusal):
        emit_admission(error.numeric, stream)
    stream.write(json.dumps(record, sort_keys=True) + "\n")
    stream.flush()
    return record
