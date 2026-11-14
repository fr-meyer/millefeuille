"""Per-file transactions in a trusted, private, single-cooperating-writer namespace.

No protection against malicious same-UID/root or uncooperative writers. No replay.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat

from millefeuille.domain.millefeuille import MillefeuilleContractError as Error
from millefeuille.domain.publication_journal import validate_snapshot
from millefeuille.domain.secure_io import _open_directory_path_no_follow
from millefeuille.domain.semantic_acceptance import safe_ref

CONTRACT = "caller-owned-private-single-cooperating-writer/v1"


def _encode(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _meta(s):
    return [
        s.st_dev,
        s.st_ino,
        None if stat.S_ISDIR(s.st_mode) else s.st_nlink,
        stat.S_IMODE(s.st_mode),
        s.st_uid,
        s.st_gid,
    ]


def _private(fd, directory=False):
    s = os.fstat(fd)
    if (
        s.st_uid != os.geteuid()
        or s.st_gid != os.getegid()
        or stat.S_IMODE(s.st_mode) != (0o700 if directory else 0o600)
        or not (stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode))
        or (not directory and s.st_nlink != 1)
    ):
        raise Error(
            "requires caller-owned private directory/file and single-link files"
        )
    return _meta(s)


def _ref(ref):
    if not isinstance(ref, str) or safe_ref(ref).as_posix() != ref:
        raise Error("canonical relative ref required")
    return ref.split("/")


def _parent(rootfd, ref):
    parts = _ref(ref)
    fd = os.dup(rootfd)
    try:
        _private(fd, True)
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
            _private(fd, True)
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def _observe(parent, name, cap):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    except FileNotFoundError:
        return None, None
    try:
        meta = _private(fd)
        before_stat = os.fstat(fd)
        if before_stat.st_size > cap:
            raise Error("file exceeds byte cap")
        data = bytearray()
        while True:
            chunk = os.read(fd, min(65536, cap + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > cap:
                raise Error("file exceeds byte cap")
        after_stat = os.fstat(fd)
        if _meta(after_stat) != meta or any(
            getattr(before_stat, field) != getattr(after_stat, field)
            for field in ("st_size", "st_mtime_ns", "st_ctime_ns")
        ):
            raise Error("file identity changed")
        return {"identity": meta, "sha256": hashlib.sha256(data).hexdigest()}, bytes(
            data
        )
    finally:
        os.close(fd)


def _write(fd, data):
    while data:
        size = os.write(fd, data)
        if size <= 0:
            raise Error("short write")
        data = data[size:]


def _new(parent, name, payload):
    fd = os.open(
        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
    )
    try:
        os.fchmod(fd, 0o600)
        _write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(parent)


@contextmanager
def _namespace(root, contract):
    if contract != CONTRACT or os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise Error(
            "explicit exclusive namespace contract and POSIX capability required"
        )
    import fcntl

    root = Path(root)
    if not root.is_absolute() or ".." in root.parts:
        raise Error("absolute root required")
    fd = _open_directory_path_no_follow(root, label="exclusive root")
    try:
        identity = _private(fd, True)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise Error("exclusive directory flock unavailable or contended") from exc

        def check():
            fresh = _open_directory_path_no_follow(root, label="exclusive root")
            try:
                if _private(fresh, True) != identity:
                    raise Error("root identity changed")
            finally:
                os.close(fresh)

        yield fd, check
    finally:
        os.close(fd)


def _positive(**caps):
    if any(type(x) is not int or x < 1 for x in caps.values()):
        raise Error("positive finite caps required")


def _verify_canonical(rootfd, refs, parents, receipts, cap, message):
    """Observe current canonical paths, never just detached directory handles."""
    for ref, identity, expected in zip(refs, parents, receipts, strict=True):
        fresh, leaf = _parent(rootfd, ref)
        try:
            if _private(fresh, True) != identity:
                raise Error("parent identity changed")
            if _observe(fresh, leaf, cap)[0] != expected:
                raise Error(message)
        finally:
            os.close(fresh)


def _verify_transaction(rootfd, ref, held, journal):
    parent, name = _parent(rootfd, ref)
    fresh = current_journal = None
    try:
        fresh = os.open(
            name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
        if _private(fresh, True) != _private(held, True):
            raise Error("transaction directory identity changed")
        current_journal = os.open(
            "journal.jsonl", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fresh
        )
        if _private(current_journal) != _private(journal):
            raise Error("transaction journal identity changed")
    finally:
        for fd in (current_journal, fresh, parent):
            if fd is not None:
                os.close(fd)


def publish_snapshot(
    *,
    root,
    outputs,
    approved_refs,
    transaction_ref,
    namespace_contract,
    max_files,
    max_bytes,
    max_aux_paths,
    max_aux_bytes,
    max_journal_bytes,
    fault=None,
):
    """Consume a new directory, preserving all evidence on any failure.

    Aux paths include directory, journal, N stages and N rollback stages.
    fault(label) is a synchronous synthetic-test hook, not authority.
    """
    _positive(
        max_files=max_files,
        max_bytes=max_bytes,
        max_aux_paths=max_aux_paths,
        max_aux_bytes=max_aux_bytes,
        max_journal_bytes=max_journal_bytes,
    )
    hit = fault or (lambda label: None)
    with _namespace(root, namespace_contract) as (rfd, check):
        outputs = validate_snapshot(
            root=root,
            outputs=outputs,
            approved_refs=approved_refs,
            max_files=max_files,
            max_bytes=max_bytes,
        )
        _ref(transaction_ref)
        if any(
            x.ref == transaction_ref
            or x.ref.startswith(transaction_ref + "/")
            or transaction_ref.startswith(x.ref + "/")
            for x in outputs
        ):
            raise Error("transaction overlaps output")
        tparent, tname = _parent(rfd, transaction_ref)
        handles = []
        tfd = jfd = None
        try:
            try:
                os.stat(tname, dir_fd=tparent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise Error("transaction name consumed")
            records = []
            for i, item in enumerate(outputs):
                pfd, name = _parent(rfd, item.ref)
                handles.append((pfd, name, _private(pfd, True)))
                before, data = _observe(pfd, name, max_bytes)
                if data != item.before_bytes:
                    raise Error("stale before-image")
                records.append(
                    {
                        "ref": item.ref,
                        "before": before,
                        "before_base64": base64.b64encode(data).decode()
                        if data is not None
                        else None,
                        "stage": f"stage-{i}",
                        "rollback": f"rollback-{i}",
                        "sha256": item.sha256,
                    }
                )
            header = {
                "event": "reserved",
                "schema": "exclusive-publication/v1",
                "root": _private(rfd, True),
                "transaction_ref": transaction_ref,
                "parents": [h[2] for h in handles],
                "outputs": records,
                "max_bytes": max_bytes,
                "max_journal_bytes": max_journal_bytes,
            }
            journal_bound = len(_encode(header)) + len(outputs) * 8192 + 8192
            if (
                2 + 2 * len(outputs) > max_aux_paths
                or journal_bound > max_journal_bytes
                or journal_bound
                + sum(len(x.payload) + len(x.before_bytes or b"") for x in outputs)
                > max_aux_bytes
            ):
                raise Error("auxiliary path/byte cap exceeded")
            check()
            os.mkdir(tname, 0o700, dir_fd=tparent)
            os.fsync(tparent)
            tfd = os.open(
                tname, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=tparent
            )
            _private(tfd, True)
            _new(tfd, "journal.jsonl", _encode(header))
            jfd = os.open(
                "journal.jsonl", os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, dir_fd=tfd
            )

            def event(value):
                raw = _encode(value)
                if os.fstat(jfd).st_size + len(raw) > max_journal_bytes:
                    raise Error("journal cap exceeded")
                _write(jfd, raw)
                os.fsync(jfd)

            for item, rec in zip(outputs, records, strict=True):
                _new(tfd, rec["stage"], item.payload)
            posts = []
            for i, (item, rec) in enumerate(zip(outputs, records, strict=True)):
                hit(f"before:{i}")
                check()
                for k, (pfd, name, identity) in enumerate(handles):
                    fresh, _ = _parent(rfd, outputs[k].ref)
                    try:
                        if (
                            _private(fresh, True) != identity
                            or _private(pfd, True) != identity
                        ):
                            raise Error("parent identity changed")
                    finally:
                        os.close(fresh)
                    observed, _ = _observe(pfd, name, max_bytes)
                    if observed != (posts[k] if k < i else records[k]["before"]):
                        raise Error("output identity changed")
                staged, staged_bytes = _observe(tfd, rec["stage"], max_bytes)
                if staged_bytes != item.payload:
                    raise Error("staging payload drift")
                event(
                    {
                        "event": "intent",
                        "index": i,
                        "ref": item.ref,
                        "expected_before": rec["before"],
                        "temp_post": staged,
                    }
                )
                hit(f"intent:{i}")
                pfd, name, identity = handles[i]
                check()
                fresh, _ = _parent(rfd, item.ref)
                try:
                    if _private(fresh, True) != identity:
                        raise Error("parent identity changed")
                finally:
                    os.close(fresh)
                _verify_transaction(rfd, transaction_ref, tfd, jfd)
                if (
                    _observe(pfd, name, max_bytes)[0] != rec["before"]
                    or _observe(tfd, rec["stage"], max_bytes)[0] != staged
                ):
                    raise Error("immediate publication precondition drift")
                if rec["before"] is None:
                    os.link(
                        rec["stage"],
                        name,
                        src_dir_fd=tfd,
                        dst_dir_fd=pfd,
                        follow_symlinks=False,
                    )
                    os.unlink(rec["stage"], dir_fd=tfd)
                else:
                    os.replace(rec["stage"], name, src_dir_fd=tfd, dst_dir_fd=pfd)
                os.fsync(tfd)
                os.fsync(pfd)
                hit(f"effect:{i}")
                post, data = _observe(pfd, name, max_bytes)
                if data != item.payload or post != staged:
                    raise Error("postimage verification failed")
                event({"event": "post", "index": i, "post": post})
                posts.append(post)
                hit(f"after:{i}")
            check()
            _verify_transaction(rfd, transaction_ref, tfd, jfd)
            _verify_canonical(
                rfd,
                [x.ref for x in outputs],
                [h[2] for h in handles],
                posts,
                max_bytes,
                "terminal postimage changed",
            )
            for pfd, _, _ in handles:
                os.fsync(pfd)
            hit("before:complete")
            check()
            _verify_transaction(rfd, transaction_ref, tfd, jfd)
            _verify_canonical(
                rfd,
                [x.ref for x in outputs],
                [h[2] for h in handles],
                posts,
                max_bytes,
                "terminal postimage changed",
            )
            event({"event": "complete"})
            hit("after:complete")
            return tuple(posts)
        finally:
            for fd in (jfd, tfd, tparent, *(h[0] for h in handles)):
                if fd is not None:
                    os.close(fd)


def _validated_journal(
    raw, *, root_identity, transaction_ref, max_bytes, max_journal_bytes
):
    """Validate the complete finite grammar and all before-images before mutation."""
    try:
        if not isinstance(raw, bytes) or not raw.endswith(bytes([10])):
            raise Error("invalid or partial journal; reconcile explicitly")
        events = [json.loads(line) for line in raw.splitlines()]
        if not events or len(events) > 130:
            raise Error("invalid journal event count")
        header = events[0]
        records = header["outputs"]
        if (
            header["event"] != "reserved"
            or header["schema"] != "exclusive-publication/v1"
            or header["root"] != root_identity
            or header["transaction_ref"] != transaction_ref
            or type(header["max_bytes"]) is not int
            or not 0 < header["max_bytes"] <= max_bytes
            or type(header["max_journal_bytes"]) is not int
            or not 0 < header["max_journal_bytes"] <= max_journal_bytes
            or not isinstance(records, list)
            or not 0 < len(records) <= 64
            or len(header["parents"]) != len(records)
        ):
            raise Error("journal context mismatch")
        refs, beforeimages = [], {}
        total = 0

        def receipt(value):
            identity = value["identity"]
            sha = value["sha256"]
            if (
                not isinstance(identity, list)
                or len(identity) != 6
                or any(type(x) is not int or x < 0 for x in identity)
                or identity[2:] != [1, 0o600, os.geteuid(), os.getegid()]
                or not isinstance(sha, str)
                or len(sha) != 64
                or any(c not in "0123456789abcdef" for c in sha)
            ):
                raise Error("invalid journal identity receipt")

        for i, rec in enumerate(records):
            _ref(rec["ref"])
            refs.append(rec["ref"])
            if (
                rec["stage"] != f"stage-{i}"
                or rec["rollback"] != f"rollback-{i}"
                or not isinstance(rec["sha256"], str)
                or len(rec["sha256"]) != 71
                or not rec["sha256"].startswith("sha256:")
                or any(c not in "0123456789abcdef" for c in rec["sha256"][7:])
            ):
                raise Error("invalid journal staging identity")
            if rec["before"] is None:
                if rec["before_base64"] is not None:
                    raise Error("invalid absent before-image")
                beforeimages[i] = None
            else:
                receipt(rec["before"])
                data = base64.b64decode(rec["before_base64"], validate=True)
                total += len(data)
                if (
                    total > header["max_bytes"]
                    or hashlib.sha256(data).hexdigest() != rec["before"]["sha256"]
                ):
                    raise Error("invalid or excessive journal before-image")
                beforeimages[i] = data
        if (
            len(set(refs)) != len(refs)
            or any(
                other.startswith(ref + "/")
                for ref in refs
                for other in refs
                if ref != other
            )
            or any(
                ref == transaction_ref
                or ref.startswith(transaction_ref + "/")
                or transaction_ref.startswith(ref + "/")
                for ref in refs
            )
        ):
            raise Error("overlapping journal destinations")
        if any(
            isinstance(e, dict) and str(e.get("event", "")).startswith("rollback")
            for e in events[1:]
        ):
            raise Error("rollback already consumed; reconcile explicitly")
        intents, posts = [], {}
        pending = None
        complete = False
        for event in events[1:]:
            if complete:
                raise Error("events after terminal completion")
            kind = event["event"]
            if kind == "intent":
                i = event["index"]
                if (
                    pending is not None
                    or type(i) is not int
                    or i != len(posts)
                    or i >= len(records)
                    or event["ref"] != records[i]["ref"]
                    or event["expected_before"] != records[i]["before"]
                ):
                    raise Error("invalid journal intent sequence")
                receipt(event["temp_post"])
                if event["temp_post"]["sha256"] != records[i]["sha256"][7:]:
                    raise Error("journal payload hash mismatch")
                intents.append(i)
                pending = event
            elif kind == "post":
                i = event["index"]
                if (
                    pending is None
                    or type(i) is not int
                    or i != pending["index"]
                    or event["post"] != pending["temp_post"]
                ):
                    raise Error("invalid journal post sequence")
                posts[i] = event["post"]
                pending = None
            elif kind == "complete":
                if pending is not None or len(posts) != len(records):
                    raise Error("premature journal completion")
                complete = True
            else:
                raise Error("unknown journal event")
        if pending is not None:
            raise Error("uncertain effect requires operator reconciliation")
        return header, records, intents, posts, beforeimages
    except Error:
        raise
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        raise Error("invalid or partial journal; reconcile explicitly") from exc


def rollback_snapshot(
    *,
    root,
    transaction_ref,
    namespace_contract,
    max_journal_bytes,
    max_bytes,
    fault=None,
):
    """Explicit one-shot exact-postimage rollback; uncertain windows refuse.

    Interrupted rollback requires reconciliation, never automatic retry.
    """
    _positive(max_journal_bytes=max_journal_bytes, max_bytes=max_bytes)
    hit = fault or (lambda label: None)
    with _namespace(root, namespace_contract) as (rfd, check):
        parent, name = _parent(rfd, transaction_ref)
        tfd = jfd = None
        handles = []
        try:
            tfd = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
            )
            _private(tfd, True)
            _, raw = _observe(tfd, "journal.jsonl", max_journal_bytes)
            header, records, intents, posts, beforeimages = _validated_journal(
                raw,
                root_identity=_private(rfd, True),
                transaction_ref=transaction_ref,
                max_bytes=max_bytes,
                max_journal_bytes=max_journal_bytes,
            )
            for i, rec in enumerate(records):
                pfd, target = _parent(rfd, rec["ref"])
                handles.append((pfd, target))
                if _private(pfd, True) != header["parents"][i]:
                    raise Error("parent identity changed")
                expected = posts.get(i, rec["before"])
                if _observe(pfd, target, max_bytes)[0] != expected:
                    raise Error("rollback refuses unrelated changes")
            for rec in records:
                try:
                    os.stat(rec["rollback"], dir_fd=tfd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise Error("rollback staging name consumed")
            jfd = os.open(
                "journal.jsonl", os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, dir_fd=tfd
            )

            def event(value):
                raw = _encode(value)
                if os.fstat(jfd).st_size + len(raw) > header["max_journal_bytes"]:
                    raise Error("journal cap exceeded")
                _write(jfd, raw)
                os.fsync(jfd)

            expected_current = [
                posts.get(i, rec["before"]) for i, rec in enumerate(records)
            ]
            _verify_transaction(rfd, transaction_ref, tfd, jfd)
            event({"event": "rollback-start"})
            for i in reversed(intents):
                check()
                rec = records[i]
                pfd, target = handles[i]
                fresh, _ = _parent(rfd, rec["ref"])
                try:
                    if _private(fresh, True) != header["parents"][i]:
                        raise Error("parent identity changed")
                finally:
                    os.close(fresh)
                if _observe(pfd, target, max_bytes)[0] != posts[i]:
                    raise Error("rollback refuses unrelated changes")
                before = rec["before_base64"]
                restore_receipt = None
                if before is not None:
                    data = beforeimages[i]
                    if (
                        len(data) > max_bytes
                        or hashlib.sha256(data).hexdigest() != rec["before"]["sha256"]
                    ):
                        raise Error("invalid before-image")
                    _new(tfd, rec["rollback"], data)
                    restore_receipt, _ = _observe(tfd, rec["rollback"], max_bytes)
                event(
                    {"event": "rollback-intent", "index": i, "expected_post": posts[i]}
                )
                hit(f"rollback-before:{i}")
                check()
                _verify_transaction(rfd, transaction_ref, tfd, jfd)
                _verify_canonical(
                    rfd,
                    [x["ref"] for x in records],
                    header["parents"],
                    expected_current,
                    max_bytes,
                    "rollback refuses unrelated changes",
                )
                if (
                    before is not None
                    and _observe(tfd, rec["rollback"], max_bytes)[0] != restore_receipt
                ):
                    raise Error("rollback staging identity changed")
                if before is None:
                    os.unlink(target, dir_fd=pfd)
                else:
                    os.replace(rec["rollback"], target, src_dir_fd=tfd, dst_dir_fd=pfd)
                os.fsync(pfd)
                os.fsync(tfd)
                hit(f"rollback-effect:{i}")
                observed, data = _observe(pfd, target, max_bytes)
                if data != (base64.b64decode(before) if before is not None else None):
                    raise Error("rollback readback failed")
                expected_current[i] = observed
                event({"event": "rollback-post", "index": i, "observed": observed})
                hit(f"rollback-after:{i}")
            hit("rollback-before:complete")
            check()
            _verify_transaction(rfd, transaction_ref, tfd, jfd)
            _verify_canonical(
                rfd,
                [x["ref"] for x in records],
                header["parents"],
                expected_current,
                max_bytes,
                "rollback refuses unrelated changes",
            )
            event({"event": "rollback-complete"})
            return tuple(records[i]["ref"] for i in reversed(intents))
        finally:
            for fd in (jfd, tfd, parent, *(h[0] for h in handles)):
                if fd is not None:
                    os.close(fd)
