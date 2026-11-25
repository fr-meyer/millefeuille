"""Read-only continuation fence and authenticated existing-winner readers.
Historical policy registration/operator modes are not public entrypoints.
"""
from pathlib import Path
import hashlib, os, stat
from millefeuille.domain import standalone_writeback as n
SCHEMA = "millefeuille-zero-provider-continuation/v1"
FACTS = "millefeuille-postrun-observation/v1"
_POLICIES = {}

def _identity(s):
    return {
        k: getattr(s, k)
        for k in (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
    }


def _read_raw(path, *, technical=False, source_receipt=False):
    """Descriptor walk: no ancestor/final symlinks; declared private/technical role."""
    p = Path(path)
    n.require(p.is_absolute() and ".." not in p.parts)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in p.parts[1:-1]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = child
        leaf = os.open(
            p.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=fd,
        )
        try:
            before = os.fstat(leaf)
            n.require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid())
            # Source acceptance receipts alone retain legacy0644 and tightened0600.
            # Generic technical/native PAUSE and private roles stay exact.
            n.require(not source_receipt or not technical)
            allowed_modes = {0o600, 0o644} if source_receipt else {0o644 if technical else 0o600}
            n.require(stat.S_IMODE(before.st_mode) in allowed_modes)
            n.require(before.st_size <= 64 * 1024 * 1024)
            chunks = []
            while True:
                part = os.read(leaf, 1024 * 1024)
                if not part:
                    break
                chunks.append(part)
                n.require(sum(map(len, chunks)) <= 64 * 1024 * 1024)
            raw = b"".join(chunks)
            n.require(_identity(os.fstat(leaf)) == _identity(before))
            n.require(
                _identity(os.stat(p.name, dir_fd=fd, follow_symlinks=False))
                == _identity(before)
            )
        finally:
            os.close(leaf)
    finally:
        os.close(fd)
    return raw


def read_role(ref, *, technical=False):
    raw = _read_raw(ref["path"], technical=technical)
    n.require(hashlib.sha256(raw).hexdigest() == ref["sha256"])
    return n.parse(raw)


def _ref(path, *, technical=False):
    p = n.safe_path(path)
    return {
        "path": str(p),
        "sha256": hashlib.sha256(_read_raw(p, technical=technical)).hexdigest(),
    }


def active_keys(root):
    policy = _POLICIES.get(n.safe_path(root))
    return set(policy["active_keys"]) if policy else None


def enabled():
    return bool(_POLICIES)


def forbid_new_provider():
    """No counter/intent/marker/credential/SDK effect may precede this check."""
    from millefeuille.domain.request_recovery import _READONLY

    n.require(not enabled() and not _READONLY)


def verify_existing_response(original, request_path, response_path):
    """Zero-mode resolution may consume ONLY its approved current winner."""
    if not enabled():
        return
    original = n.safe_path(original)
    contexts = [
        (root, policy)
        for root, policy in _POLICIES.items()
        if original.is_relative_to(root)
    ]
    n.require(len(contexts) == 1)
    root, policy = contexts[0]
    facts = read_role(policy["facts"])
    validate_existing_winner(facts, original, request_path, response_path)


def validate_existing_winner(facts, original, request_path, response_path):
    """Pure fact-bound response/approval identity check; never registers authority."""
    n.require(facts["schema"] == FACTS and facts["observation_only"] is True)
    original = n.safe_path(original)
    winner = facts["winner"]
    n.require(_ref(request_path) == winner["request"])
    n.require(_ref(response_path) == winner["response"])
    n.require(
        original == Path(winner["request"]["path"]).parents[2] / "review-request.json"
    )
    for role in ("binding", "admission", "review", "approval"):
        n.require(_ref(winner[role]["path"]) == winner[role])
    n.require(original.is_relative_to(Path(facts["root"])))
