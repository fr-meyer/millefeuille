"""Native filesystem fences and fair local admission, no background service."""
from millefeuille.credentials import credential_value

from contextlib import contextmanager
import os
import re
import stat
from pathlib import Path
import fcntl
import threading
import time
from millefeuille.domain import standalone_writeback as n


class Paused(Exception):
    """No new stage admitted; already started effects must settle."""


def check_pause(path):
    if path and os.path.lexists(path):
        raise Paused("pipeline paused before stage admission")


class Lane:
    def __init__(self, store):
        self.path = store / "deferred-final-lane.lock"
        self.store = store
        self.condition = threading.Condition()
        self.next = 0
        self.serving = 0
        self.cancelled = set()
        self.local = threading.local()

    @contextmanager
    def paper(self, run):
        self.local.run = run
        try:
            with self:
                yield self
        finally:
            self.local.run = None

    def _advance(self):
        with self.condition:
            self.serving += 1
            while self.serving in self.cancelled:
                self.cancelled.remove(self.serving)
                self.serving += 1
            self.condition.notify_all()

    def __enter__(self):
        start = time.monotonic()
        with self.condition:
            ticket = self.next
            self.next += 1
            try:
                self.condition.wait_for(lambda: ticket == self.serving)
            except BaseException:
                if ticket == self.serving:
                    self._advance()
                else:
                    self.cancelled.add(ticket)
                raise
        stream = None
        try:
            stream = self.path.open("a")
            fcntl.flock(stream, fcntl.LOCK_EX)
            self.local.stream = stream
            self.local.wait_seconds = time.monotonic() - start
            self.local.admitted = time.monotonic()
            self.local.started_at = time.time()
            return self
        except BaseException as original:
            try:
                if stream is not None:
                    try:
                        stream.close()
                    except BaseException as cleanup:
                        original.add_note(
                            "lane entry close failure: " + type(cleanup).__name__
                        )
            finally:
                self._advance()
            raise

    def __exit__(self, exc_type, exc, traceback):
        import uuid

        failure = None
        try:
            root = self.store / "pipeline-lane-observations"
            root.mkdir(mode=0o700, exist_ok=True)
            run = getattr(self.local, "run", None)
            n.save_new(
                root / (uuid.uuid4().hex + ".json"),
                {
                    "run_dir": str(run) if run else None,
                    "paper_dir": str(run.parent.parent) if run else None,
                    "started_at": self.local.started_at,
                    "ended_at": time.time(),
                    "wait_seconds": self.local.wait_seconds,
                    "elapsed_seconds": time.monotonic() - self.local.admitted,
                },
            )
        except BaseException as error:
            failure = error
        finally:
            try:
                try:
                    fcntl.flock(self.local.stream, fcntl.LOCK_UN)
                finally:
                    self.local.stream.close()
            except BaseException as error:
                if failure is None:
                    failure = error
            finally:
                self._advance()
        if exc is not None:
            if failure is not None:
                exc.add_note(
                    "lane cleanup/observation failure: " + type(failure).__name__
                )
            return False
        if failure is not None:
            raise failure
        return False


def fence(store, authority_path, keys, policy):
    authority = n.load(authority_path)
    root = store / "paper-lifecycle-ownership"
    root.mkdir(mode=0o700, exist_ok=True)
    with (root / "ownership.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for key in keys:
            path = root / (authority["library_id"] + "-" + key + ".json")
            value = {
                "library_id": authority["library_id"],
                "item_key": key,
                "output_dir": str(n.safe_path(authority["output_dir"])),
                "authority_sha256": n.sha(n.read_bytes(authority_path)),
                "policy": policy,
            }
            successors = root / (path.stem + ".successors")
            if successors.exists() and any(successors.iterdir()):
                records = [n.load(p) for p in successors.iterdir()]
                n.require(len(records) == 1 and records[0]["successor"] == value)
                continue
            if path.exists():
                if n.load(path) != value:
                    n.require(n.load(path) == value)
            else:
                n.save_new(path, value)


@contextmanager
def stage(run, name):
    from millefeuille.domain.request_recovery import _normal_stage_scope, _root
    _normal_stage_scope(_root(run))
    check_pause(run.parent.parent.parent / "PAUSE")
    import uuid

    root = run.parent.parent / "pipeline-observations"
    root.mkdir(mode=0o700, exist_ok=True)
    identity = uuid.uuid4().hex
    started = time.time()
    monotonic = time.monotonic()
    n.save_new(
        root / (identity + "-start.json"), {"stage": name, "started_at": started}
    )
    try:
        yield
    finally:
        n.save_new(
            root / (identity + "-end.json"),
            {
                "stage": name,
                "ended_at": time.time(),
                "elapsed_seconds": time.monotonic() - monotonic,
            },
        )


def _marker_role(path):
    """Only native empty stop roles, never a generic private-artifact reader."""
    path = Path(path)
    n.require(path.is_absolute() and path.anchor == "/" and ".." not in path.parts)
    if path.name in {"PAUSE", "PAUSE.before-source-revision"}:
        root = path.parent
    else:
        n.require(path.parent.name == "source-continuations")
        n.require(re.fullmatch(r"[0-9a-f]{64}\.retired-pause", path.name) is not None)
        root = path.parent.parent
    n.require(root != Path("/"))
    return path, root


def _marker_signature(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


@contextmanager
def _marker_parent(path):
    """Walk and retain no-follow descriptors, including the named root ancestry."""
    path, root = _marker_role(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptors = [os.open("/", flags)]
    links = []
    current = Path("/")
    try:
        for part in path.parent.parts[1:]:
            parent = descriptors[-1]
            fd = os.open(part, flags, dir_fd=parent)
            descriptors.append(fd)
            info = os.fstat(fd)
            initial = (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
            links.append((parent, part, fd, initial))
            current = current / part
            if current in {root, path.parent}:
                info = os.fstat(fd)
                n.require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0)
                if current != root:
                    n.require(info.st_dev == os.fstat(descriptors[-2]).st_dev)
        yield descriptors[-1], links
        _marker_ancestry(links)
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def _marker_ancestry(links):
    for parent, name, fd, initial in links:
        entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
        actual = os.fstat(fd)
        n.require(stat.S_ISDIR(entry.st_mode))
        n.require(
            (actual.st_dev, actual.st_ino, actual.st_mode, actual.st_uid, actual.st_gid)
            == initial
        )
        n.require(
            (entry.st_dev, entry.st_ino, entry.st_mode, entry.st_uid, entry.st_gid)
            == (
                actual.st_dev,
                actual.st_ino,
                actual.st_mode,
                actual.st_uid,
                actual.st_gid,
            )
        )


def _marker_file(info, parent):
    n.require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid())
    n.require(info.st_nlink == 1 and info.st_size == 0)
    # Exactly the genuine legacy-readable shape or a new private marker.
    # No group/other writable bits, special bits, or nonempty payloads accepted.
    n.require(stat.S_IMODE(info.st_mode) in {0o600, 0o644})
    n.require(info.st_dev == os.fstat(parent).st_dev)


def validate_pause_root(root):
    """Read-only admission check, before any batch worker can need a stop."""
    with _marker_parent(Path(root) / "PAUSE"):
        pass


def marker_identity(path, *, _durable=False):
    """Read-only identity of an owned EMPTY 0600/legacy-0644 native stop."""
    path, _ = _marker_role(path)
    with _marker_parent(path) as (parent, links):
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        _marker_file(before, parent)
        fd = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            dir_fd=parent,
        )
        try:
            opened = os.fstat(fd)
            _marker_file(opened, parent)
            n.require(_marker_signature(before) == _marker_signature(opened))
            n.require(os.read(fd, 1) == b"")
            if _durable:
                os.fsync(fd)
                os.fsync(parent)
            after = os.fstat(fd)
            named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            n.require(
                _marker_signature(opened)
                == _marker_signature(after)
                == _marker_signature(named)
            )
            _marker_ancestry(links)
            return {
                "sha256": n.sha(b""),
                "device": after.st_dev,
                "inode": after.st_ino,
                "size": after.st_size,
                "mode": stat.S_IMODE(after.st_mode),
                "mtime_ns": after.st_mtime_ns,
                "ctime_ns": after.st_ctime_ns,
            }
        finally:
            os.close(fd)


def create_pause(path):
    """Exclusive durable 0600 creation; never touch/normalize an existing stop."""
    path, _ = _marker_role(path)
    n.require(path.name == "PAUSE")
    with _marker_parent(path) as (parent, links):
        try:
            fd = os.open(
                path.name,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=parent,
            )
        except FileExistsError:
            marker_identity(
                path, _durable=True
            )  # also durably observe a racing creator
            return
        try:
            os.fchmod(fd, 0o600)  # only our new exclusive fd, never an old marker
            opened = os.fstat(fd)
            _marker_file(opened, parent)
            os.fsync(fd)
            n.require(
                _marker_signature(opened)
                == _marker_signature(
                    os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                )
            )
            _marker_ancestry(links)
            os.fsync(parent)
        finally:
            os.close(fd)


def activation_preflight(root, store, cfg):
    """Shared exact normal activation checks; complete genuine environment required."""
    from millefeuille.domain.clean_final import generation_preflight
    from millefeuille.domain.pipeline_resources import check

    check(
        root,
        store,
        cfg.concurrency,
        cfg.max_revision_rounds,
        ignore_pause=True,
    )
    from millefeuille.domain.postrun_reuse import enabled as reuse_only

    if not reuse_only():
        for name in ("MISTRAL_API_KEY", "OPENAI_API_KEY"):
            n.require(bool(credential_value(name, stage="ocr" if name == "MISTRAL_API_KEY" else "decisions")))
        generation_preflight()
    if cfg.scientific_fullcycle:
        n.require(bool(credential_value("ZOTERO_WRITE_KEY", stage="writeback")))
