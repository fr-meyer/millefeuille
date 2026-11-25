"""Ordinary admission and authenticated v1 physical-winner readback.
No historical recovery registration or new physical replacement is exposed.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib, os, stat, time
from millefeuille.clients.direct_generation import canonical, digest, validate_response
from millefeuille.domain import standalone_writeback as n
_CONFIG = {}
_READONLY = {}

def _sha(path):
    return hashlib.sha256(n.read_bytes(path, 64 * 1024 * 1024)).hexdigest()


def _relative(root, name):
    value = Path(name)
    n.require(not value.is_absolute() and ".." not in value.parts)
    path = root / value
    n.require(path.resolve() == path)
    return path


def _root(path):
    return next(
        (p for p in Path(path).parents if (p / "RUN-AUTHORITY.json").is_file()), None
    )


def _policy_json(path, expected_pin=None):
    path = n.safe_path(path)
    info = path.stat()
    n.require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600)
    n.require(info.st_uid == os.geteuid())
    raw = n.read_bytes(path, 64 * 1024 * 1024)
    if expected_pin is not None:
        n.require(hashlib.sha256(raw).hexdigest() == expected_pin)
    return n.parse(raw)


def _ref(ref):
    return _policy_json(ref["path"], ref["sha256"])


def _document(path, *, live, expected_pin=None):
    value = _policy_json(path, expected_pin)
    n.require(value["schema"] == "millefeuille-request-recovery-authority/v1")
    spec = value["spec"]
    approval = _ref(value["new_effect_approval"])
    n.require(approval["schema"] == "millefeuille-new-physical-effect-approval/v1")
    n.require(approval["approved"] is True and approval["owner"] == "gateway-owner")
    n.require(approval["spec_sha256"] == hashlib.sha256(canonical(spec)).hexdigest())
    n.require(approval["additional_physical_effects"] is True)
    n.require(spec["old_unknowns_remain_potentially_spent"] is True)
    n.require(spec["initial_ocr"] == spec["initial_science"] == 0)
    n.require(spec["automatic_retry"] is False)
    n.require(0 < spec["expires_unix"] - spec["issued_unix"] <= 86400)
    if live:
        n.require(spec["issued_unix"] <= time.time() <= spec["expires_unix"])
    acceptance = _ref(spec["accepted_source"])
    manifest = _ref(spec["source_manifest"])
    n.require(acceptance["approved"] is True and acceptance["owner"] == "gateway-owner")
    n.require(acceptance["source_manifest_sha256"] == spec["source_manifest"]["sha256"])
    n.require(isinstance(manifest, dict) and len(manifest) >= 778)
    n.require("millefeuille/domain/request_recovery.py" in manifest)
    if live:
        source = Path(__file__).resolve().parents[2]
        n.require(all(_sha(_relative(source, r)) == h for r, h in manifest.items()))
    return spec


def _attempt(original, version=1):
    n.require(type(version) is int and version in (1, 2))
    return original.with_name(original.stem + ".physical-attempts") / f"{version:06d}"


def chosen_paths(original):
    """Resolve genuine chosen response, without writing/authorizing a new attempt."""
    original = Path(original)
    _ordinary_read_scope(original)
    stage = original.name.removesuffix("-request.json")
    attempt = _attempt(original, 2)
    if not (attempt / (stage + "-response.json")).exists():
        attempt = _attempt(original)
    version = int(attempt.name)
    req, resp = attempt / original.name, attempt / (stage + "-response.json")
    if resp.exists():
        binding = n.load(attempt / "BINDING.json")
        n.require(binding["original_request_sha256"] == _sha(original))
        n.require(binding["schema"] == "millefeuille-physical-attempt-binding/v1")
        root = _root(original)
        n.require(binding["original_request"] == str(original.relative_to(root)))
        spec = _document(
            binding["authority_path"],
            live=False,
            expected_pin=binding["authority_sha256"],
        )
        n.require(_sha(binding["authority_path"]) == binding["authority_sha256"])
        entry = next(
            e
            for e in spec["attempts"]
            if e["original_request"] == binding["original_request"]
        )
        n.require(entry["original_request_sha256"] == _sha(original))
        n.require(entry["physical_version"] == version)
        n.require(
            binding["physical_attempt"]
            == {
                "version": version,
                "authority_sha256": binding["authority_sha256"],
                "original_request_sha256": _sha(original),
            }
        )
        request = n.load(req)
        n.require(request["physical_attempt"] == binding["physical_attempt"])
        expected = n.load(original)
        expected["physical_attempt"] = binding["physical_attempt"]
        expected.pop("request_id")
        expected["request_id"] = digest(canonical(expected))
        n.require(request == expected)
        validate_response(request, n.load(resp))
        return req, resp
    return original, original.with_name(stage + "-response.json")








def _ordinary_read_scope(path):
    """Unknown typed physical namespaces are not canonical ordinary responses."""
    path = n.safe_path(path)
    attempts = path.with_name(path.stem + ".physical-attempts")
    if attempts.exists():
        n.require(attempts.is_dir() and not attempts.is_symlink())
        n.require(all(p.name in {"000001", "000002"} and p.is_dir() and not p.is_symlink() for p in attempts.iterdir()))
    return path


def _normal_stage_scope(root):
    if root is None:
        return False
    # An authority-bearing historical programme must never fall through into
    # unbounded ordinary work. No private policy registration is supported here.
    n.require(n.sha(n.read_bytes(Path(root) / "RUN-AUTHORITY.json")) != "6fe41314851f46829b56a8bb3763f0eea15a4b375f78b78c9cdfa1ce80edabcc")
    raise n.WritebackError("Historical authority policy is unavailable in ordinary routes")


def resolve(original, request):
    original = Path(original)
    chosen, response = chosen_paths(original)
    if response.exists():
        actual = n.load(chosen)
        expected = dict(request)
        if chosen != original:
            expected["physical_attempt"] = actual["physical_attempt"]
            expected.pop("request_id")
            expected["request_id"] = digest(canonical(expected))
        n.require(actual == expected)
        from millefeuille.domain.postrun_reuse import verify_existing_response
        verify_existing_response(original, chosen, response)
        return chosen, response, actual
    from millefeuille.domain.postrun_reuse import forbid_new_provider
    forbid_new_provider()
    _normal_stage_scope(_root(original))
    n.require(not os.path.lexists(original.with_name(original.stem + ".physical-attempts")))
    n.require("physical_attempt" not in request)
    return original, response, request


@contextmanager
def reservation(path, request, *, budget_bootstrap=False):
    from millefeuille.domain.postrun_reuse import forbid_new_provider
    forbid_new_provider()
    path = Path(path)
    _normal_stage_scope(_root(path))
    n.require(not budget_bootstrap and not _CONFIG)
    n.require("physical_attempt" not in request)
    _ordinary_read_scope(path)
    n.require(not os.path.lexists(path.with_name(path.stem + ".physical-attempts")))
    yield  # Caller retains real exclusive request/admission journal semantics.


def verify_dispatch(path):
    from millefeuille.domain.postrun_reuse import forbid_new_provider
    forbid_new_provider()
    path = Path(path)
    _normal_stage_scope(_root(path))
    n.require(not _CONFIG)
    n.require("physical_attempt" not in n.load(path))
    n.require(not os.path.lexists(path.with_name(path.stem + ".physical-attempts")))
