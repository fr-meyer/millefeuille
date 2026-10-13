"""Deterministic staged-replacement protocol model, never a Windows backend.

All names, handles, links, locks and flushes below are synthetic. The adapter
models atomic no-replace namespace steps under trusted/cooperating ownership.
It deliberately does not establish hostile same-owner confinement, Windows
handle-relative rename support, share-mode exclusion, or crash durability.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

PENDING = ".millefeuille-metadata-pending"
HISTORY = ".millefeuille-metadata-history"
TRUSTED_CONTRACT = "trusted-cooperating-owner/v1"
PRODUCTION_WINDOWS_WRITES_ENABLED = False
MAX_OUTPUTS = 8
MAX_FILE_BYTES = 65536
MAX_GROUP_BYTES = 262144
MAX_EVIDENCE_BYTES = 1048576
MAX_PLAN_BYTES = 8192
MAX_HISTORY_JOURNALS = 16


class Refusal(RuntimeError):
    """An unsafe, unsupported, collided or changed model condition."""


@dataclass(frozen=True)
class Binding:
    identity: int
    bytes: int
    sha256: str
    links: int


@dataclass
class Node:
    identity: int
    kind: str
    data: bytes = b""
    owner: str | None = None
    children: dict[str, int] = field(default_factory=dict)
    flushes: int = 0
    transaction_state: str | None = None


@dataclass
class Entry:
    path: str
    parent: int
    leaf: str
    before: Binding | None
    old_bytes: bytes | None
    new_bytes: bytes
    number: int
    backup: Binding | None = None
    after: Binding | None = None


@dataclass
class Receipt:
    status: str
    request: str
    run_identity: int
    before: dict[str, dict | None]
    after: dict[str, dict | None]
    pending: bool
    journal_identity: int | None
    rollback_errors: list[str]
    trace: list[str]
    retained_bytes: int
    native_windows_qualified: bool = False
    production_windows_writes_enabled: bool = False
    crash_durability_qualified: bool = False


class TransactionError(Refusal):
    def __init__(self, cause: Exception, receipt: Receipt):
        super().__init__(f"{receipt.status}: {type(cause).__name__}: {cause}")
        self.receipt = receipt
        self.__cause__ = cause


def production_entrypoint(*_args, **_kwargs):
    """No production adapter or activation path is supplied by this prototype."""
    raise Refusal("production Windows metadata writes remain disabled")


def safe_parts(path: str) -> tuple[str, ...]:
    if not isinstance(path, str) or not path or len(path) > 256:
        raise Refusal("invalid relative output path")
    parts = tuple(path.split("/"))
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10)
    }
    if any(
        not p
        or p in (".", "..")
        or p.endswith((" ", "."))
        or any(c in p for c in '\\:*?"<>|\x00')
        or any(ord(c) < 32 for c in p)
        or any(ord(c) > 126 for c in p)
        or p.split(".", 1)[0].upper() in reserved
        or p.casefold().startswith(".millefeuille-metadata-")
        for p in parts
    ):
        raise Refusal("unsafe relative output path")
    return parts


class SyntheticStore:
    """Small in-memory namespace with explicit object IDs and raw fault seams.

    Cooperative callers use ``lock``. Raw helpers exist solely to inject observed
    drift; they are not prevented from introducing outside aliases. No sampled
    guard here claims to exclude hostile alias creation between observations.
    """

    def __init__(self):
        self.nodes: dict[int, Node] = {}
        self.lock = threading.RLock()
        self.writes: list[int] = []
        self._serial = 0
        self.volume = self._new("directory")
        self.run = self.mkdir(self.volume, "run")
        self.outside = self.mkdir(self.volume, "outside")
        self.history = self.mkdir(self.run, HISTORY)
        self.mkdir(self.run, "reports")

    def _new(self, kind: str, *, data=b"", owner=None) -> int:
        self._serial += 1
        n = Node(self._serial, kind, data, owner)
        self.nodes[n.identity] = n
        return n.identity

    def directory(self, identity: int) -> Node:
        n = self.nodes[identity]
        if n.kind != "directory":
            raise Refusal("not a plain directory")
        return n

    def named(self, parent: int, leaf: str) -> int | None:
        return self.directory(parent).children.get(leaf.casefold())

    def mkdir(self, parent: int, leaf: str, *, owner=None) -> int:
        d = self.directory(parent)
        leaf = leaf.casefold()
        if leaf in d.children:
            raise Refusal("directory collision")
        identity = self._new("directory", owner=owner)
        d.children[leaf] = identity
        return identity

    def create_file(self, parent: int, leaf: str, data: bytes, *, owner=None) -> int:
        d = self.directory(parent)
        leaf = leaf.casefold()
        if leaf in d.children:
            raise Refusal("file collision")
        if len(data) > MAX_FILE_BYTES:
            raise Refusal("file byte cap")
        identity = self._new("regular", data=data, owner=owner)
        d.children[leaf] = identity
        return identity

    def links(self, identity: int) -> int:
        return sum(
            list(n.children.values()).count(identity)
            for n in self.nodes.values()
            if n.kind == "directory"
        )

    def binding(self, identity: int, *, single=True) -> Binding:
        n = self.nodes[identity]
        if n.kind != "regular":
            raise Refusal("not a non-reparse regular file")
        links = self.links(identity)
        if single and links != 1:
            raise Refusal("not a single-link file")
        if len(n.data) > MAX_FILE_BYTES:
            raise Refusal("file byte cap")
        return Binding(identity, len(n.data), hashlib.sha256(n.data).hexdigest(), links)

    def named_binding(self, parent: int, leaf: str) -> Binding | None:
        identity = self.named(parent, leaf)
        return None if identity is None else self.binding(identity)

    def resolve(self, path: str) -> tuple[int, str, list[tuple[str, int]]]:
        parts = safe_parts(path)
        parent = self.run
        anchors = [("", self.run)]
        for i, part in enumerate(parts[:-1]):
            identity = self.named(parent, part)
            if identity is None:
                raise Refusal("parent directory missing")
            self.directory(identity)
            parent = identity
            anchors.append(("/".join(parts[: i + 1]), parent))
        return parent, parts[-1], anchors

    def anchor(self, relative: str) -> int:
        current = self.named(self.volume, "run")
        if current is None:
            raise Refusal("run directory missing")
        self.directory(current)
        if relative:
            for part in relative.split("/"):
                current = self.named(current, part)
                if current is None:
                    raise Refusal("parent directory missing")
                self.directory(current)
        return current

    def write_owned(self, identity: int, data: bytes, *, owner: str) -> None:
        n = self.nodes[identity]
        self.binding(identity)
        if n.owner != owner:
            raise Refusal("write ownership drift")
        if len(data) > MAX_FILE_BYTES:
            raise Refusal("file byte cap")
        n.data = data
        self.writes.append(identity)

    def move(self, src: int, leaf: str, dst: int, target: str, identity: int) -> None:
        """Model one atomic no-replace move, with held source identity binding.

        This adapter contract is assumed for protocol tests. A future native
        implementation must prove its handle/name/parent semantics separately.
        """
        source, destination = self.directory(src), self.directory(dst)
        leaf, target = leaf.casefold(), target.casefold()
        if source.children.get(leaf) != identity:
            raise Refusal("source identity drift")
        if target in destination.children:
            raise Refusal("no-replace destination collision")
        destination.children[target] = identity
        del source.children[leaf]

    def flush(self, identity: int) -> None:
        self.nodes[identity].flushes += 1  # Abstract acknowledgement, not fsync proof.

    def retained_bytes(self) -> int:
        return sum(len(n.data) for n in self.nodes.values() if n.kind == "regular")

    def raw_replace(self, parent: int, leaf: str, data: bytes) -> int:
        identity = self._new("regular", data=data, owner="foreign")
        self.directory(parent).children[leaf.casefold()] = identity
        return identity

    def raw_link(self, identity: int, parent: int, leaf: str) -> None:
        leaf = leaf.casefold()
        if leaf in self.directory(parent).children:
            raise Refusal("raw alias collision")
        self.directory(parent).children[leaf] = identity

    def raw_unlink(self, parent: int, leaf: str) -> None:
        del self.directory(parent).children[leaf.casefold()]

    def require_terminal_history(self):
        if self.anchor(HISTORY) != self.history:
            raise Refusal("history directory identity drift")
        if len(self.directory(self.history).children) > MAX_HISTORY_JOURNALS:
            raise Refusal("history count cap; explicit retention/recovery required")
        for identity in self.directory(self.history).children.values():
            node = self.directory(identity)
            if node.transaction_state not in ("committed", "rolled-back"):
                raise Refusal(
                    "unfinished or foreign history; explicit recovery required"
                )

    def read_handle(self, identity: int) -> bytes:
        return self.nodes[identity].data

    def read_group(self, paths: list[str], *, expected_run_identity: int) -> dict:
        with self.lock:
            if self.anchor("") != expected_run_identity:
                raise Refusal("reader run identity drift")
            if self.named(self.run, PENDING) is not None:
                raise Refusal("pending transaction; explicit recovery required")
            self.require_terminal_history()
            result = {}
            for path in paths:
                parent, leaf, _ = self.resolve(path)
                binding = self.named_binding(parent, leaf)
                if binding is None:
                    raise Refusal("group member missing")
                result[path] = self.read_handle(binding.identity)
            if self.named(self.run, PENDING) is not None:
                raise Refusal("pending transaction; explicit recovery required")
            self.require_terminal_history()
            return result


class StagedReplacement:
    def __init__(self, store: SyntheticStore, *, fault: Callable | None = None):
        self.store = store
        self.fault = fault
        self.trace: list[str] = []
        self.entries: list[Entry] = []
        self.anchors: dict[str, int] = {}
        self.inputs: dict[str, Binding] = {}
        self.barrier: int | None = None
        self.journal: int | None = None
        self.barrier_location: tuple[int, str] | None = None
        self.request = ""
        self.owner = ""
        self.used = False
        self.plan: Binding | None = None

    def step(self, name: str, action: Callable):
        self.trace.append("before:" + name)
        if self.fault:
            self.fault("before:" + name, self)
        result = action()
        self.trace.append("after:" + name)
        if self.fault:
            self.fault("after:" + name, self)
        return result

    def revalidate(self):
        for path, identity in self.anchors.items():
            if self.store.anchor(path) != identity:
                raise Refusal("run or parent directory identity drift")
        for path, binding in self.inputs.items():
            parent, leaf, _ = self.store.resolve(path)
            if self.store.named_binding(parent, leaf) != binding:
                raise Refusal("input binding drift")
        if (
            self.journal is not None
            and self.store.named(self.store.history, self.request) != self.journal
        ):
            raise Refusal("journal ownership drift")
        if self.barrier_location is not None:
            parent, name = self.barrier_location
            if self.store.named(parent, name) != self.barrier:
                raise Refusal("barrier ownership drift")

    def evidence_check(self):
        if self.store.retained_bytes() > MAX_EVIDENCE_BYTES:
            raise Refusal("evidence byte cap")

    def receipt(self, status: str, errors=()) -> Receipt:
        after = {}
        for e in self.entries:
            try:
                b = self.store.named_binding(e.parent, e.leaf)
                after[e.path] = None if b is None else asdict(b)
            except Refusal:
                after[e.path] = {"unsafe": True}
        return Receipt(
            status,
            self.request,
            self.store.run,
            {
                e.path: None if e.before is None else asdict(e.before)
                for e in self.entries
            },
            after,
            self.store.named(self.store.run, PENDING) is not None,
            self.journal,
            list(errors),
            list(self.trace),
            self.store.retained_bytes(),
        )

    def execute(
        self,
        outputs: dict[str, bytes],
        *,
        expected_before: dict[str, Binding | None] | None = None,
        expected_inputs: dict[str, Binding] | None = None,
        expected_run_identity: int,
        contract: str = TRUSTED_CONTRACT,
    ) -> Receipt:
        if self.used:
            raise Refusal("transaction objects are single use; never blindly retry")
        self.used = True
        with self.store.lock:
            try:
                self.preflight(
                    outputs,
                    expected_before,
                    expected_inputs or {},
                    expected_run_identity,
                    contract,
                )
                if all(e.old_bytes == e.new_bytes for e in self.entries):
                    self.step("noop.verify", self.verify_noop)
                    self.verify_noop()
                    return self.receipt("noop")
                self.prepare()
                self.commit()
                return self.receipt("committed")
            except Exception as cause:
                errors = self.rollback() if self.barrier is not None else []
                status = (
                    "recovery-required"
                    if errors
                    else (
                        "rolled-back"
                        if self.barrier is not None
                        else "preflight-refused"
                    )
                )
                raise TransactionError(cause, self.receipt(status, errors)) from cause

    def preflight(
        self, outputs, expected_before, expected_inputs, root_identity, contract
    ):
        if contract != TRUSTED_CONTRACT:
            raise Refusal("hostile same-owner confinement is not supplied")
        if not 0 < len(outputs) <= MAX_OUTPUTS:
            raise Refusal("output count cap")
        if any(not isinstance(data, bytes) for data in outputs.values()):
            raise Refusal("outputs must be exact bytes")
        try:
            for data in outputs.values():
                data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise Refusal("metadata output must be UTF-8") from error
        if sum(len(data) for data in outputs.values()) > MAX_GROUP_BYTES:
            raise Refusal("group byte cap")
        if expected_before is not None and set(expected_before) != set(outputs):
            raise Refusal("expected-before keys must exactly cover outputs")
        if len({path.casefold() for path in outputs}) != len(outputs):
            raise Refusal("case-insensitive output name collision")
        if {path.casefold() for path in outputs} & {
            path.casefold() for path in expected_inputs
        }:
            raise Refusal("input/output overlap requires a separate contract")
        if self.store.anchor("") != root_identity or root_identity != self.store.run:
            raise Refusal("run identity drift")
        if self.store.named(self.store.run, PENDING) is not None:
            raise Refusal("pending transaction; explicit recovery required")
        self.store.require_terminal_history()
        self.anchors = {"": self.store.run, HISTORY: self.store.history}
        for i, (path, data) in enumerate(sorted(outputs.items())):
            if len(data) > MAX_FILE_BYTES:
                raise Refusal("file byte cap")
            parent, leaf, anchors = self.store.resolve(path)
            self.anchors.update(anchors)
            before = self.store.named_binding(parent, leaf)
            if expected_before is not None and before != expected_before[path]:
                raise Refusal("expected-before binding drift")
            old = None if before is None else self.store.read_handle(before.identity)
            self.entries.append(Entry(path, parent, leaf, before, old, data, i))
        for path, binding in expected_inputs.items():
            parent, leaf, anchors = self.store.resolve(path)
            self.anchors.update(anchors)
            if self.store.named_binding(parent, leaf) != binding:
                raise Refusal("input binding drift")
            self.inputs[path] = binding
        unchanged = all(e.old_bytes == e.new_bytes for e in self.entries)
        if (
            not unchanged
            and len(self.store.directory(self.store.history).children)
            >= MAX_HISTORY_JOURNALS
        ):
            raise Refusal("history reservation capacity exhausted")
        packet = [
            {
                "path": e.path,
                "before": None if e.before is None else asdict(e.before),
                "after_bytes": len(e.new_bytes),
                "after_sha256": hashlib.sha256(e.new_bytes).hexdigest(),
            }
            for e in self.entries
        ]
        self.request = hashlib.sha256(
            json.dumps(packet, sort_keys=True).encode()
        ).hexdigest()
        self.owner = "transaction:" + self.request
        for leaf in (self.request, self.request + "-aborted-barrier"):
            if self.store.named(self.store.history, leaf) is not None:
                raise Refusal("history reservation collision")
        self.step("preflight.verify", self.revalidate)
        self.evidence_check()
        projected = self.store.retained_bytes()
        if not unchanged:
            projected += (
                sum(len(e.old_bytes or b"") + len(e.new_bytes) for e in self.entries)
                + MAX_PLAN_BYTES
            )
        if projected > MAX_EVIDENCE_BYTES:
            raise Refusal("projected evidence byte cap")

    def prepare(self):
        def barrier_create():
            self.barrier = self.store.mkdir(self.store.run, PENDING, owner=self.owner)
            self.barrier_location = (self.store.run, PENDING)

        self.step("barrier.create", barrier_create)

        def journal_create():
            self.journal = self.store.mkdir(
                self.store.history, self.request, owner=self.owner
            )
            self.store.nodes[self.journal].transaction_state = "preparing"

        self.step("journal.create", journal_create)
        self.step("barrier.flush", lambda: self.store.flush(self.store.run))
        for e in self.entries:
            if e.before is not None:

                def backup(entry=e):
                    identity = self.store.create_file(
                        self.journal,
                        f"before-{entry.number}",
                        entry.old_bytes,
                        owner=self.owner,
                    )
                    entry.backup = self.store.binding(identity)

                self.step(f"backup.create.{e.number}", backup)
                self.step(
                    f"backup.verify.{e.number}",
                    lambda entry=e: self.verify_backup(entry),
                )
                self.step(
                    f"backup.flush.{e.number}",
                    lambda entry=e: self.store.flush(entry.backup.identity),
                )

            def stage_create(entry=e):
                identity = self.store.create_file(
                    self.journal, f"next-{entry.number}", b"", owner=self.owner
                )
                entry.after = self.store.binding(identity)

            self.step(f"stage.create.{e.number}", stage_create)

            def stage_write(entry=e):
                self.store.write_owned(
                    entry.after.identity, entry.new_bytes, owner=self.owner
                )
                entry.after = self.store.binding(entry.after.identity)

            self.step(f"stage.write.{e.number}", stage_write)
            self.step(
                f"stage.verify.{e.number}",
                lambda entry=e: self.verify_stage(entry, staged=True),
            )
            self.step(
                f"stage.flush.{e.number}",
                lambda entry=e: self.store.flush(entry.after.identity),
            )

        def plan_create():
            plan = json.dumps(
                [
                    {
                        "path": e.path,
                        "before": None if e.before is None else asdict(e.before),
                        "backup": None if e.backup is None else asdict(e.backup),
                        "after": asdict(e.after),
                    }
                    for e in self.entries
                ],
                sort_keys=True,
            ).encode()
            if len(plan) > MAX_PLAN_BYTES:
                raise Refusal("plan byte cap")
            identity = self.store.create_file(
                self.journal, "plan.json", plan, owner=self.owner
            )
            self.plan = self.store.binding(identity)

        self.step("plan.create", plan_create)
        self.step("prepared.verify", self.verify_prepared)
        self.step("journal.flush", lambda: self.store.flush(self.journal))
        self.evidence_check()

    def verify_backup(self, e):
        self.revalidate()
        b = self.store.named_binding(self.journal, f"before-{e.number}")
        if (
            b != e.backup
            or b is None
            or self.store.read_handle(b.identity) != e.old_bytes
        ):
            raise Refusal("byte backup ownership or content drift")

    def verify_stage(self, e, *, staged=False):
        self.revalidate()
        b = self.store.named_binding(
            self.journal if staged else e.parent,
            f"next-{e.number}" if staged else e.leaf,
        )
        if (
            b != e.after
            or b is None
            or self.store.read_handle(b.identity) != e.new_bytes
        ):
            raise Refusal("staged or installed output drift")

    def verify_prepared(self):
        self.revalidate()
        for e in self.entries:
            if self.store.named_binding(e.parent, e.leaf) != e.before:
                raise Refusal("old live binding drift")
            if e.before is not None:
                self.verify_backup(e)
            self.verify_stage(e, staged=True)

    def verify_noop(self):
        self.revalidate()
        for e in self.entries:
            if self.store.named_binding(e.parent, e.leaf) != e.before:
                raise Refusal("no-op live binding drift")
            if (
                e.before is None
                or self.store.read_handle(e.before.identity) != e.new_bytes
            ):
                raise Refusal("no-op byte drift")

    def commit(self):
        for e in self.entries:
            self.step(f"old.verify.{e.number}", lambda entry=e: self.verify_old(entry))
            if e.before is not None:
                self.step(f"old.move.{e.number}", lambda entry=e: self.move_old(entry))
            self.step(
                f"new.install.{e.number}", lambda entry=e: self.install_new(entry)
            )
            self.step(
                f"installed.verify.{e.number}", lambda entry=e: self.verify_stage(entry)
            )
            self.step(
                f"output.flush.{e.number}",
                lambda entry=e: self.store.flush(entry.parent),
            )
        self.step("final.verify", self.verify_final)
        self.step("final.journal.flush", lambda: self.store.flush(self.journal))
        self.step(
            "barrier.archive",
            lambda: self.move_barrier(self.journal, "committed-barrier"),
        )
        self.step("history.flush", lambda: self.store.flush(self.store.history))
        self.step("root.flush", lambda: self.store.flush(self.store.run))
        self.step("commit.verify", self.verify_final)
        self.verify_final()
        self.step("commit.acknowledge", lambda: self.acknowledge("committed"))
        self.verify_final()

    def verify_old(self, e):
        self.revalidate()
        if self.store.named_binding(e.parent, e.leaf) != e.before:
            raise Refusal("old live binding drift")
        if e.before is not None:
            self.verify_backup(e)

    def move_old(self, e):
        self.verify_old(e)
        self.store.move(
            e.parent, e.leaf, self.journal, f"old-{e.number}", e.before.identity
        )

    def install_new(self, e):
        self.verify_stage(e, staged=True)
        self.store.move(
            self.journal, f"next-{e.number}", e.parent, e.leaf, e.after.identity
        )

    def verify_final(self):
        self.revalidate()
        for e in self.entries:
            self.verify_stage(e)
            if e.before is not None:
                self.verify_backup(e)
                if (
                    self.store.named_binding(self.journal, f"old-{e.number}")
                    != e.before
                ):
                    raise Refusal("displaced original binding drift")
        if (
            self.plan is None
            or self.store.named_binding(self.journal, "plan.json") != self.plan
        ):
            raise Refusal("plan ownership or content drift")
        self.evidence_check()

    def move_barrier(self, parent, leaf):
        self.revalidate()
        barrier = self.store.directory(self.barrier)
        if (
            barrier.children
            or barrier.owner != self.owner
            or self.store.links(self.barrier) != 1
        ):
            raise Refusal("barrier is not the owned empty single-name directory")
        src, name = self.barrier_location
        self.store.move(src, name, parent, leaf, self.barrier)
        self.barrier_location = (parent, leaf)

    def rollback(self) -> list[str]:
        errors = []
        if self.journal is not None:
            self.store.nodes[self.journal].transaction_state = "recovery-required"
        try:
            self.revalidate()
            if self.barrier_location != (self.store.run, PENDING):
                self.step(
                    "rollback.barrier.restore",
                    lambda: self.move_barrier(self.store.run, PENDING),
                )
        except Exception as error:  # noqa: BLE001 - Any unknown failure retains recovery custody.
            errors.append(str(error))
        if not errors and self.journal is not None:
            for e in reversed(self.entries):
                try:
                    self.revalidate()
                    current = self.store.named_binding(e.parent, e.leaf)
                    if current == e.before:
                        continue
                    if current is not None:
                        if current != e.after:
                            raise Refusal("rollback live ownership drift")
                        self.step(
                            f"rollback.new.withdraw.{e.number}",
                            lambda entry=e: self.withdraw_new(entry),
                        )
                    if e.before is not None:
                        self.step(
                            f"rollback.old.restore.{e.number}",
                            lambda entry=e: self.restore_old(entry),
                        )
                    self.step(
                        f"rollback.output.verify.{e.number}",
                        lambda entry=e: self.verify_rolled_back(entry),
                    )
                    self.step(
                        f"rollback.output.flush.{e.number}",
                        lambda entry=e: self.store.flush(entry.parent),
                    )
                except Exception as error:  # noqa: BLE001 - Continue only independent owned rollback entries.
                    errors.append(str(error))
        if not errors:
            try:
                self.step("rollback.final.verify", self.verify_rollback_final)
                self.step(
                    "rollback.journal.flush",
                    lambda: self.store.flush(self.journal or self.store.run),
                )
                target, name = (
                    (self.journal, "rolled-back-barrier")
                    if self.journal is not None
                    else (self.store.history, self.request + "-aborted-barrier")
                )
                self.step(
                    "rollback.barrier.archive", lambda: self.move_barrier(target, name)
                )
                self.step(
                    "rollback.root.flush", lambda: self.store.flush(self.store.run)
                )
                self.step(
                    "rollback.history.flush",
                    lambda: self.store.flush(self.store.history),
                )
                self.step(
                    "rollback.acknowledge", lambda: self.acknowledge("rolled-back")
                )
                self.verify_rollback_final()
            except Exception as error:  # noqa: BLE001 - Never treat an uncertain rollback as successful.
                errors.append(str(error))
        if errors:
            terminal = self.journal or self.barrier
            self.store.nodes[terminal].transaction_state = "recovery-required"
            # Only the admitted root is touched. Never insert into a foreign run.
            try:
                self.reassert_barrier()
            except Exception as error:  # noqa: BLE001 - Explicitly report uncertain barrier restoration.
                errors.append("barrier-uncertain: " + str(error))
        return errors

    def acknowledge(self, state):
        self.revalidate()
        terminal = self.journal or self.barrier
        self.store.nodes[terminal].transaction_state = state

    def withdraw_new(self, e):
        self.revalidate()
        if self.store.named_binding(e.parent, e.leaf) != e.after:
            raise Refusal("rollback live ownership drift")
        self.store.move(
            e.parent, e.leaf, self.journal, f"failed-{e.number}", e.after.identity
        )

    def restore_old(self, e):
        self.revalidate()
        self.verify_backup(e)
        if self.store.named_binding(self.journal, f"old-{e.number}") != e.before:
            raise Refusal("rollback displaced original ownership drift")
        self.store.move(
            self.journal, f"old-{e.number}", e.parent, e.leaf, e.before.identity
        )

    def verify_rolled_back(self, e):
        self.revalidate()
        if self.store.named_binding(e.parent, e.leaf) != e.before:
            raise Refusal("rollback before-state drift")

    def verify_rollback_final(self):
        self.revalidate()
        for e in self.entries:
            self.verify_rolled_back(e)
            if e.backup is not None:
                self.verify_backup(e)
            if e.after is not None and self.store.binding(e.after.identity) != e.after:
                raise Refusal("retained staged ownership or content drift")
        if (
            self.plan is not None
            and self.store.named_binding(self.journal, "plan.json") != self.plan
        ):
            raise Refusal("plan ownership or content drift")

    def reassert_barrier(self):
        if self.store.anchor("") != self.store.run:
            raise Refusal(
                "foreign run; owned original-root barrier cannot protect replacement"
            )
        if self.store.named(self.store.run, PENDING) is None:

            def marker():
                self.store.mkdir(self.store.run, PENDING, owner=self.owner)

            self.step("recovery.barrier.create", marker)
        self.step("recovery.barrier.flush", lambda: self.store.flush(self.store.run))
