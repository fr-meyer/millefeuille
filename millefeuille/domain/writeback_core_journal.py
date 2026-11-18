"""Internal durable single-parent mutation core; no HTTP or authority issuance."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
import uuid

from millefeuille.domain.millefeuille import MillefeuilleContractError as Error
from millefeuille.domain.writeback_core import _hash, _remaining, _snapshot, _wire
from millefeuille.domain.writeback_http import (
    _Acknowledgement,
    _checked_observation,
    _DefinitiveRefusalError,
)


def _validate_audit(value):
    fields = {
        "schema",
        "operation",
        "execution_audit_digest",
        "plan_digest",
        "receipt_digest",
        "status",
        "reason",
        "mutation_attempts",
        "observed",
        "acknowledgement",
    }
    if (
        set(value) != fields
        or value["schema"] != "millefeuille-writeback-core-audit/v2"
    ):
        raise Error("audit contract fields")
    from millefeuille.domain.writeback_core import _sha

    _sha(value["plan_digest"])
    _sha(value["receipt_digest"])
    allowed = {
        "execute": {"written", "skipped", "needs-review"},
        "rollback": {"rolled-back", "skipped", "needs-review"},
    }
    if (
        value["operation"] not in allowed
        or value["status"] not in allowed[value["operation"]]
    ):
        raise Error("audit operation/status mismatch")
    if type(value["mutation_attempts"]) is not int or value[
        "mutation_attempts"
    ] not in (0, 1):
        raise Error("audit operation cap")
    if value["operation"] == "rollback":
        _sha(value["execution_audit_digest"])
    elif value["execution_audit_digest"] is not None:
        raise Error("execution cannot claim rollback authority")
    if value["observed"] is not None:
        _snapshot(value["observed"])
    if value["status"] != "needs-review" and value["observed"] is None:
        raise Error("success requires independent observation")
    if value["reason"] not in (
        "already-exact",
        "independently-verified",
        "conflict-ambiguous-or-unverified",
    ):
        raise Error("audit reason")
    ack = value["acknowledgement"]
    if ack is not None and (
        set(ack) != {"status", "version"}
        or ack["status"] != 204
        or type(ack["version"]) is not int
        or ack["version"] < 1
    ):
        raise Error("audit acknowledgement contract")
    if value["status"] in ("written", "rolled-back") and (
        ack is None or ack["version"] != value["observed"]["version"]
    ):
        raise Error("verified ownership acknowledgement required")
    return value


_SCHEMA = (
    "CREATE TABLE metadata (version INTEGER NOT NULL, journal_id TEXT NOT NULL)",
    "CREATE TABLE receipts (id TEXT PRIMARY KEY,digest TEXT UNIQUE,"
    "plan TEXT,operation TEXT,UNIQUE(plan,operation))",
    "CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    "plan TEXT,receipt TEXT,kind TEXT,payload TEXT)",
)


@contextmanager
def _held(path, commitment=None):
    if os.name != "posix" or not all(
        hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY", "geteuid")
    ):
        raise Error("protected journals require POSIX no-follow descriptors")
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise Error("canonical absolute journal path required")
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    file_fd = None
    try:
        for component in path.parent.parts[1:]:
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory,
            )
            os.close(directory)
            directory = next_fd
        parent = os.fstat(directory)
        if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise Error("private owned journal directory required")
        if commitment is not None:
            file_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            detail = os.fstat(file_fd)
            if (
                not stat.S_ISREG(detail.st_mode)
                or detail.st_uid != os.geteuid()
                or stat.S_IMODE(detail.st_mode) != 0o600
                or detail.st_nlink != 1
                or [parent.st_dev, parent.st_ino] != commitment["directory_identity"]
                or [detail.st_dev, detail.st_ino] != commitment["file_identity"]
                or commitment["path"] != str(path)
                or commitment["uid"] != os.geteuid()
            ):
                raise Error("journal privacy/namespace/identity drift")
        yield directory, file_fd
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(directory)


def _provision_journal(path):
    """Explicit ONE-TIME local provisioning. Persist returned commitment OUTSIDE
    ledger in independently trusted configuration. No automatic recovery/reset.
    Any failure leaves the created file quarantined, never deleted/reinitialized.
    """
    if os.name != "posix" or not all(
        hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY", "geteuid")
    ):
        raise Error("protected journals require POSIX no-follow descriptors")
    path = Path(path)
    with _held(path) as (directory, _):
        fd = os.open(
            path.name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        try:
            parent, detail = os.fstat(directory), os.fstat(fd)
            commitment = {
                "version": 1,
                "journal_id": uuid.uuid4().hex,
                "path": str(path),
                "uid": os.geteuid(),
                "directory_identity": [parent.st_dev, parent.st_ino],
                "file_identity": [detail.st_dev, detail.st_ino],
            }
            with _held(path, commitment):
                db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)
                try:
                    db.execute("PRAGMA synchronous=FULL")
                    db.execute("BEGIN IMMEDIATE")
                    for statement in _SCHEMA:
                        db.execute(statement)
                    db.execute(
                        "INSERT INTO metadata VALUES (?,?)",
                        (1, commitment["journal_id"]),
                    )
                    with _held(path, commitment):
                        db.commit()
                finally:
                    db.close()
            with _held(path, commitment):
                os.fsync(fd)
                os.fsync(directory)
            # Validate complete initialization before issuing the commitment.
            journal = _Journal(path, commitment)
            journal._sync()
            return commitment
        finally:
            os.close(fd)


class _Journal:
    def __init__(self, path, commitment):
        self.path = Path(path)
        self.commitment = json.loads(_wire(commitment))
        if (
            set(self.commitment)
            != {
                "version",
                "journal_id",
                "path",
                "uid",
                "directory_identity",
                "file_identity",
            }
            or type(self.commitment["version"]) is not int
            or self.commitment["version"] != 1
            or type(self.commitment["uid"]) is not int
            or any(
                not isinstance(self.commitment[key], list)
                or len(self.commitment[key]) != 2
                or any(
                    type(part) is not int or part < 0 for part in self.commitment[key]
                )
                for key in ("directory_identity", "file_identity")
            )
            or not isinstance(self.commitment["journal_id"], str)
            or len(self.commitment["journal_id"]) != 32
        ):
            raise Error("trusted journal commitment required")
        db = self._db()
        db.close()

    def _validate_schema(self, db):
        if db.execute("PRAGMA journal_mode").fetchone() != ("delete",):
            raise Error("journal mode drift")
        rows = db.execute(
            "SELECT name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        expected = sorted((statement.split()[2], statement) for statement in _SCHEMA)
        if rows != expected or db.execute(
            "SELECT version,journal_id FROM metadata"
        ).fetchall() != [(1, self.commitment["journal_id"])]:
            raise Error("journal schema/version/ID mismatch")

    def _db(self):
        with _held(self.path, self.commitment):
            db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=2)
            try:
                db.execute("PRAGMA synchronous=FULL")
                self._validate_schema(db)
                with _held(self.path, self.commitment):
                    pass
                return db
            except BaseException:
                db.close()
                raise

    def _commit(self, db):
        with _held(self.path, self.commitment):
            self._validate_schema(db)
            db.commit()
            with _held(self.path, self.commitment):
                self._validate_schema(db)

    def validate(self):
        db = self._db()
        db.close()

    def _sync(self):
        self.validate()
        with _held(self.path, self.commitment) as (directory, fd):
            # Fsync held validated descriptors, never a newly substituted inode.
            os.fsync(fd)
            with _held(self.path, self.commitment):
                os.fsync(directory)
            with _held(self.path, self.commitment):
                pass
        self.validate()

    def consume(self, plan, authority):
        authority.check(plan, authority.operation)
        db = self._db()
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO receipts VALUES (?,?,?,?)",
                (
                    authority.receipt_id,
                    authority.receipt_digest,
                    plan.digest,
                    authority.operation,
                ),
            )
            db.execute(
                "INSERT INTO events(plan,receipt,kind,payload) VALUES (?,?,?,?)",
                (
                    plan.digest,
                    authority.receipt_digest,
                    "consumed",
                    _wire(
                        {
                            "operation": authority.operation,
                            "expires_at": authority.expires_at,
                            "execution_audit_digest": authority.execution_audit_digest,
                            "plan": plan.value(),
                        }
                    ).decode(),
                ),
            )
            self._commit(db)
        finally:
            db.close()
        self._sync()

    def event(self, plan, authority, kind, payload):
        if len(_wire(payload)) > 200000:
            raise Error("journal evidence cap")
        db = self._db()
        try:
            db.execute(
                "INSERT INTO events(plan,receipt,kind,payload) VALUES (?,?,?,?)",
                (plan.digest, authority.receipt_digest, kind, _wire(payload).decode()),
            )
            self._commit(db)
        finally:
            db.close()
        self._sync()

    def execution(self, plan, audit_digest):
        db = self._db()
        try:
            rows = db.execute(
                "SELECT payload FROM events "
                "WHERE plan=? AND kind='result' ORDER BY seq",
                (plan.digest,),
            ).fetchall()
        finally:
            db.close()
        for row in rows:
            value = _validate_audit(json.loads(row[0]))
            if (
                _hash(value) == audit_digest
                and value["operation"] == "execute"
                and value["status"] == "written"
            ):
                return value
        raise Error("exact verified execution audit missing")


def _run(*, plan, authority, journal, writer, reader, rollback=False):
    """reader.read(identity, deadline); writer.patch(identity,version,delta,deadline).

    Distinct read transport must fetch fresh authoritative data, never cached
    mutation responses. Adapter must enforce its deadline/zero retry contract.
    Python interfaces are not a sandbox or a verified root authority boundary.
    """
    if writer is reader:
        raise Error("independent reader required")
    operation = "rollback" if rollback else "execute"
    authority.check(plan, operation)
    p = plan.value()
    before = p["before"]
    identity = {k: before[k] for k in ("library_type", "library_id", "key")}
    expected = before
    target = p["after_data"]
    if rollback:
        audit = journal.execution(plan, authority.execution_audit_digest)
        expected = audit["observed"]
        if expected["data"] != p["after_data"]:
            raise Error("execution ownership mismatch")
        target = before["data"]
    journal.consume(plan, authority)
    deadline = time.monotonic() + p["limits"]["seconds"]
    acknowledgement = None
    attempted = False
    observed = None
    status = "needs-review"
    reason = "not-verified"
    try:
        _remaining(deadline)
        current, proof = _checked_observation(reader.read(identity, deadline=deadline))
        _remaining(deadline)
        journal.event(
            plan, authority, "before-read", {"snapshot": current, "proof": proof}
        )
        if current != expected:
            raise Error("version or before-state conflict")
        if current["data"] == target:
            status = "skipped"
            observed = current
            reason = "already-exact"
        else:
            authority.check(plan, operation)
            _remaining(deadline)
            delta = {"tags": target["tags"], "collections": target["collections"]}
            journal.event(
                plan,
                authority,
                "mutation-intent",
                {
                    "identity": identity,
                    "expected_version": current["version"],
                    "delta": delta,
                },
            )
            _remaining(deadline)
            authority.check(plan, operation)
            journal.validate()
            attempted = True
            try:
                ack = writer.patch(
                    identity,
                    expected_version=current["version"],
                    delta=delta,
                    deadline=deadline,
                )
                if (
                    not isinstance(ack, _Acknowledgement)
                    or ack.status != 204
                    or type(ack.version) is not int
                    or ack.version <= current["version"]
                ):
                    raise Error("missing mutation acknowledgement")
                acknowledgement = {"status": ack.status, "version": ack.version}
                journal.event(plan, authority, "acknowledgement", acknowledgement)
            except _DefinitiveRefusalError:
                journal.event(plan, authority, "definitive-refusal", {"retry": False})
                raise
            except Exception:
                # May already have applied. Never repeat the mutation or rebase.
                journal.event(plan, authority, "ambiguous-response", {"retry": False})
            _remaining(deadline)
            observed, proof = _checked_observation(
                reader.read(identity, deadline=deadline)
            )
            _remaining(deadline)
            journal.event(
                plan, authority, "after-read", {"snapshot": observed, "proof": proof}
            )
            if (
                any(observed[k] != identity[k] for k in identity)
                or observed["data"] != target
                or acknowledgement is None
                or observed["version"] != acknowledgement["version"]
            ):
                raise Error("independent readback mismatch")
            status = "rolled-back" if rollback else "written"
            reason = "independently-verified"
    except Exception:
        status = "needs-review"
        reason = "conflict-ambiguous-or-unverified"
    audit = {
        "schema": "millefeuille-writeback-core-audit/v2",
        "operation": operation,
        "execution_audit_digest": authority.execution_audit_digest,
        "plan_digest": plan.digest,
        "receipt_digest": authority.receipt_digest,
        "status": status,
        "reason": reason,
        "mutation_attempts": int(attempted),
        "observed": observed,
        "acknowledgement": acknowledgement,
    }
    _validate_audit(audit)
    journal.event(plan, authority, "rollback-result" if rollback else "result", audit)
    digest = _hash(audit)
    if rollback:
        return {"audit": audit, "audit_digest": digest}
    result = {
        "schema_version": "millefeuille-zotero-writeback-result/v0.1",
        "mode": "approved-live",
        "status": status,
        "paper_id": p["paper_id"],
        "run_id": p["run_id"],
        "source_hash": p["source_hash"],
        "source_item_version": p["source_item_version"],
        "item_version": 0 if observed is None else observed["version"],
        "operation_count": int(attempted),
        "receipt_digest": authority.receipt_digest,
        "audit_digest": digest,
    }
    result["integrity"] = {"algorithm": "sha256", "content_digest": _hash(result)}
    return {"result": result, "audit": audit, "audit_digest": digest}
