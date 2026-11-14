"""Fail-closed, one-shot OCR wire reservations. Hashes bind bytes, not authority."""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import time


class OCRBudgetError(ValueError):
    """Safe refusal without provider payloads or credentials."""


@dataclass(frozen=True)
class Operation:
    id: str
    kind: str
    method: str
    endpoint: str
    input_payload_sha256: str
    max_request_bytes: int
    max_response_bytes: int
    timeout_seconds: float
    max_cost_microusd: int


@dataclass(frozen=True)
class RequestGraph:
    operations: tuple[Operation, ...]
    total_transport_limit: int
    cost_limit_microusd: int
    wall_seconds: float
    cleanup_ids: tuple[str, ...]
    reconciliation_ids: tuple[str, ...]
    authority_ref: str
    account_price_verified: bool = False
    model: str = "mistral-ocr-latest"

    def validate(self) -> None:
        ids = [op.id for op in self.operations]
        if (
            not self.authority_ref
            or not self.account_price_verified
            or self.model != "mistral-ocr-latest"
            or len(set(ids)) != len(ids)
            or not ids
            or self.total_transport_limit < len(ids)
            or self.cost_limit_microusd
            < sum(o.max_cost_microusd for o in self.operations)
            or not math.isfinite(self.wall_seconds)
            or self.wall_seconds <= 0
            or not self.cleanup_ids
            or not self.reconciliation_ids
        ):
            raise OCRBudgetError("unsealed-or-unknown-request-graph")
        for name in self.cleanup_ids + self.reconciliation_ids:
            if name not in ids:
                raise OCRBudgetError("missing-reserved-operation")
        if (
            set(self.cleanup_ids) & set(self.reconciliation_ids)
            or any(
                o.kind != "delete" for o in self.operations if o.id in self.cleanup_ids
            )
            or any(
                o.kind not in {"status", "poll"}
                for o in self.operations
                if o.id in self.reconciliation_ids
            )
        ):
            raise OCRBudgetError("invalid-finalization-reserves")
        for op in self.operations:
            methods = {
                "upload": "POST",
                "signed_url": "GET",
                "ocr": "POST",
                "status": "GET",
                "poll": "GET",
                "delete": "DELETE",
            }
            if (
                op.method != methods.get(op.kind)
                or op.endpoint != "https://api.mistral.ai/v1/" + op.kind
            ):
                raise OCRBudgetError("invalid-operation-route")
            if (
                op.kind
                not in {"upload", "signed_url", "ocr", "status", "poll", "delete"}
                or op.method not in {"GET", "POST", "DELETE"}
                or not op.endpoint.startswith("https://api.mistral.ai/v1/")
                or any(c in op.endpoint for c in "?#@\\")
                or min(op.max_request_bytes, op.max_response_bytes) <= 0
                or op.max_cost_microusd < 0
                or not math.isfinite(op.timeout_seconds)
                or op.timeout_seconds <= 0
                or len(op.input_payload_sha256) != 64
            ):
                raise OCRBudgetError("invalid-operation")


def _binding(graph: RequestGraph) -> str:
    from dataclasses import asdict

    return hashlib.sha256(
        json.dumps(asdict(graph), sort_keys=True).encode()
    ).hexdigest()


def initialize_ledger(graph: RequestGraph, ledger: Path) -> None:
    """Pre-reserve the entire graph, including cleanup and unknown reconciliation."""
    graph.validate()
    state = {
        "schema": "millefeuille-ocr-request-ledger/v2",
        "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "graph": _binding(graph),
        "started": time.monotonic(),
        "reserved_calls": len(graph.operations),
        "reserved_cost_microusd": sum(o.max_cost_microusd for o in graph.operations),
        "operations": {o.id: "reserved" for o in graph.operations},
    }
    fd = os.open(ledger, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        parent = os.open(ledger.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    except BaseException:
        # An incomplete ledger is deliberately not reusable.
        raise


def _read_state(stream, graph):
    raw = stream.read(65537)
    if len(raw) > 65536 or not raw.endswith(b"\n"):
        raise OCRBudgetError("damaged-ledger-no-reset")
    try:
        first, *events = raw.splitlines()
        state = json.loads(first)
        if (
            state["schema"] != "millefeuille-ocr-request-ledger/v2"
            or state["boot"]
            != Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            or state["graph"] != _binding(graph)
        ):
            raise ValueError
        if (
            state["operations"] != {o.id: "reserved" for o in graph.operations}
            or state["reserved_calls"] != len(graph.operations)
            or state["reserved_cost_microusd"]
            != sum(o.max_cost_microusd for o in graph.operations)
            or not isinstance(state["started"], (int, float))
            or not math.isfinite(state["started"])
            or not 0 <= state["started"] <= time.monotonic()
        ):
            raise ValueError
        for line in events:
            event = json.loads(line)
            if state["operations"].get(event["id"]) != event["expected"]:
                raise ValueError
            if (event["expected"], event["target"]) not in {
                ("reserved", "unknown"),
                ("unknown", "unknown"),
                ("unknown", "observed"),
                ("unknown", "refused"),
            }:
                raise ValueError
            state["operations"][event["id"]] = event["target"]
        return state
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError):
        raise OCRBudgetError("damaged-ledger-no-reset") from None


def read_ledger(graph, ledger):
    """Bounded independent reopen; never repairs or reinitializes state."""
    fd = os.open(ledger, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        return _read_state(stream, graph)


def _update(graph, ledger, operation_id, target, expected):
    """Append-only fsynced transitions; torn tails fail closed on reopen.

    No rename/inode-lock race; lock acquisition is nonblocking. A crash before
    fsync can retain unknown liability, never grants replay after unknown.
    """
    graph.validate()
    fd = os.open(ledger, os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(fd, "r+b") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise OCRBudgetError("ledger-busy") from None
        state = _read_state(stream, graph)
        if state["operations"].get(operation_id) != expected:
            raise OCRBudgetError("operation-not-reserved")
        if target == "unknown" and expected == "reserved":
            if time.monotonic() - state["started"] >= graph.wall_seconds:
                raise OCRBudgetError("wall-budget-exhausted")
            op = next(o for o in graph.operations if o.id == operation_id)
            required = {
                "signed_url": {"upload"},
                "ocr": {"upload", "signed_url"},
                "delete": {"upload"},
            }.get(op.kind, set())
            if any(
                not any(
                    o.kind == kind and state["operations"][o.id] == "observed"
                    for o in graph.operations
                )
                for kind in required
            ):
                raise OCRBudgetError("operation-predecessor-not-observed")
        event = json.dumps({"id": operation_id, "expected": expected, "target": target})
        if stream.tell() + len(event.encode()) + 1 > 65536:
            raise OCRBudgetError("ledger-too-large")
        stream.seek(0, os.SEEK_END)
        stream.write((event + "\n").encode())
        stream.flush()
        os.fsync(stream.fileno())


def reserve_operation(
    graph: RequestGraph, operation_id: str, ledger: Path
) -> Operation:
    """Persist maximum unknown liability BEFORE any send. Never implicitly refund."""
    _update(graph, ledger, operation_id, "unknown", "reserved")
    return next(o for o in graph.operations if o.id == operation_id)


def reconcile_operation(
    graph: RequestGraph, operation_id: str, ledger: Path, observation: str
) -> None:
    if observation not in {"observed", "refused", "unknown"}:
        raise OCRBudgetError("invalid-observation")
    _update(graph, ledger, operation_id, observation, "unknown")
