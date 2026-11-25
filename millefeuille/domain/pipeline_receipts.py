"""Idempotent immutable artifacts, explicitly not provider admission intents."""

from millefeuille.domain import standalone_writeback as n


def save(path, value):
    if path.exists():
        n.require(n.load(path) == value)
    else:
        n.save_new(path, value)


def admission_records(request_path):
    """Validate durable admission chain; a dispatch reservation is ambiguous."""
    root = request_path.with_name(request_path.name + ".admission")
    if not root.exists():
        return []
    paths = sorted(root.iterdir())
    n.require(
        [p.name for p in paths] == [f"{i:06d}.json" for i in range(1, len(paths) + 1)]
    )
    records = []
    pin = n.sha(n.read_bytes(request_path))
    for i, path in enumerate(paths, 1):
        value = n.load(path)
        n.require(
            set(value) == {"schema", "request_sha256", "sequence", "state", "reason"}
        )
        n.require(value["schema"] == "millefeuille-transport-admission/v1")
        n.require(
            value["request_sha256"] == pin
            and type(value["sequence"]) is int
            and value["sequence"] == i
        )
        n.require(value["state"] in {"NOT_STARTED", "DISPATCH_RESERVED"})
        n.require(value["reason"] in {"Paused", "ResourceFloorError", "admitted"})
        n.require(
            (value["state"] == "DISPATCH_RESERVED") == (value["reason"] == "admitted")
        )
        n.require(not records or records[-1]["state"] == "NOT_STARTED")
        records.append(value)
    return records


def proven_not_started(request_path):
    records = admission_records(request_path)
    return bool(records) and records[-1]["state"] == "NOT_STARTED"


def _reserve_request(request_path, request, admission):
    if request_path.exists():
        n.require(n.load(request_path) == request and proven_not_started(request_path))
    admission()  # no new request or allowance consumed on first refusal
    if not request_path.exists():
        n.save_new(request_path, request)


def reserve_request(request_path, request, admission):
    from millefeuille.domain.postrun_reuse import forbid_new_provider

    forbid_new_provider()
    from millefeuille.domain.request_recovery import reservation

    with reservation(request_path, request):
        return _reserve_request(request_path, request, admission)


def dispatch_reserved(request_path, admission):
    from millefeuille.domain.postrun_reuse import forbid_new_provider

    forbid_new_provider()
    from millefeuille.domain.request_recovery import verify_dispatch

    verify_dispatch(request_path)
    """Second immediate admission, then durable dispatch intent before transport.

    Only locally raised typed admission refusals prove no transport was called.
    A crash or any other failure without that proof remains ambiguous.
    """
    from millefeuille.domain.pipeline_guards import Paused
    from millefeuille.domain.pipeline_resources import ResourceFloorError

    records = admission_records(request_path)
    n.require(not records or records[-1]["state"] == "NOT_STARTED")
    root = request_path.with_name(request_path.name + ".admission")
    root.mkdir(mode=0o700, exist_ok=True)
    sequence = len(records) + 1

    def record(state, reason):
        n.save_new(
            root / f"{sequence:06d}.json",
            {
                "schema": "millefeuille-transport-admission/v1",
                "request_sha256": n.sha(n.read_bytes(request_path)),
                "sequence": sequence,
                "state": state,
                "reason": reason,
            },
        )

    try:
        admission()
    except (Paused, ResourceFloorError) as error:
        record("NOT_STARTED", type(error).__name__)
        raise
    record("DISPATCH_RESERVED", "admitted")
    from millefeuille.domain.native_physical import activate
    activate(request_path)


def _capture_generation_failure(request_path, error, stage, *, _capture_token=None):
    """Exclusive immutable observation. Never settles or releases an unknown intent."""
    from millefeuille.clients.direct_generation import (
        DirectGenerationError,
        safe_diagnostic,
    )

    n.require(isinstance(error, DirectGenerationError))
    n.require(
        stage
        in {"summary", "correction", "review", "classification", "taxonomy-category"}
    )
    request_pin = n.sha(n.read_bytes(request_path))
    records = admission_records(request_path)
    admission = None
    if records:
        last = records[-1]
        path = (
            request_path.with_name(request_path.name + ".admission")
            / f"{last['sequence']:06d}.json"
        )
        admission = {
            "sequence": last["sequence"],
            "state": last["state"],
            "receipt_sha256": n.sha(n.read_bytes(path)),
        }
    value = {
        "schema": "millefeuille-generation-failure-observation/v1",
        "request_sha256": request_pin,
        "stage": stage,
        "native_admission": admission,
        "exception_class": "DirectGenerationError",
        "diagnostic": safe_diagnostic(error.diagnostic),
        "observation_only": True,
        "provider_outcome": "unknown",
        "retry": "never-automatically",
    }
    capture_scope = None
    n.require(_capture_token is None)
    path = request_path.with_name(request_path.name + ".failure.json")
    n.require(len(n.canonical(value)) <= 8192)
    n.save_new(path, value)
    import hashlib

    receipt_sha = hashlib.sha256(n.read_bytes(path)).hexdigest()
    error.generation_failure_sha256 = receipt_sha
    return error.generation_failure_sha256


# Fixed vocabulary: never exception messages, errno paths, or arbitrary class names.
_CAPTURE_ERROR_CLASSES = frozenset(
    {
        "OSError",
        "PermissionError",
        "FileNotFoundError",
        "FileExistsError",
        "IsADirectoryError",
        "NotADirectoryError",
        "BlockingIOError",
        "WritebackError",
        "RuntimeError",
        "ValueError",
        "TypeError",
        "KeyError",
        "AssertionError",
    }
)


def record_generation_failure(request_path, error, stage, *, _capture_token=None):
    """Best-effort evidence; the caller always rethrows the original generation error."""
    from millefeuille.clients.direct_generation import (
        DirectGenerationError,
        safe_diagnostic,
    )

    n.require(isinstance(error, DirectGenerationError))
    error.diagnostic = safe_diagnostic(error.diagnostic)
    # A stale/partial capture must never advertise a committed receipt reference.
    error.generation_failure_sha256 = None
    error.generation_failure_capture_failed = False
    try:
        return _capture_generation_failure(request_path, error, stage, _capture_token=_capture_token)
    except Exception as capture_error:
        error.generation_failure_sha256 = None
        error.generation_failure_capture_failed = True
        name = type(capture_error).__name__
        error.generation_failure_capture_error_class = (
            name if name in _CAPTURE_ERROR_CLASSES else "CaptureError"
        )
        # No retry, readback retry, unlink, overwrite, intent update or outcome inference.
        return None


def generation_failure_fallback(error):
    """Sanitized non-receipt observation, not a success/no-effect or durable SHA claim."""
    from millefeuille.clients.direct_generation import (
        DirectGenerationError,
        safe_diagnostic,
    )

    if (
        not isinstance(error, DirectGenerationError)
        or getattr(error, "generation_failure_capture_failed", False) is not True
    ):
        return None
    name = getattr(error, "generation_failure_capture_error_class", None)
    return {
        "schema": "millefeuille-generation-failure-fallback/v1",
        "capture_failed": True,
        "capture_error_class": (
            name
            if isinstance(name, str) and name in _CAPTURE_ERROR_CLASSES
            else "CaptureError"
        ),
        "diagnostic": safe_diagnostic(error.diagnostic),
        "observation_only": True,
        "provider_outcome": "unknown",
        "retry": "never-automatically",
    }


def read_generation_failure(request_path, expected_sha256, capture_scope=None):
    """New strict semantic reader; historical Pipeline remains digest-only."""
    from millefeuille.clients.direct_generation import safe_diagnostic
    raw = n.read_bytes(request_path.with_name(request_path.name + ".failure.json"), 8192)
    n.require(n.sha(raw) == expected_sha256)
    value = n.parse(raw)
    fields = {"schema", "request_sha256", "stage", "native_admission", "exception_class", "diagnostic", "observation_only", "provider_outcome", "retry"}
    schema = value.get("schema")
    n.require(schema in {"millefeuille-generation-failure-observation/v1", "millefeuille-generation-failure-observation/v2"})
    n.require(set(value) == (fields | ({"capture_ref"} if schema.endswith("/v2") else set())))
    n.require(value["request_sha256"] == n.sha(n.read_bytes(request_path)))
    n.require(value["stage"] in {"summary", "correction", "review", "classification", "taxonomy-category"})
    n.require(value["exception_class"] == "DirectGenerationError" and value["observation_only"] is True)
    n.require(value["provider_outcome"] == "unknown" and value["retry"] == "never-automatically")
    n.require(safe_diagnostic(value["diagnostic"]) == value["diagnostic"])
    records = admission_records(request_path)
    admission = None
    if records:
        last = records[-1]
        path = request_path.with_name(request_path.name + ".admission") / (f"{last['sequence']:06d}.json")
        admission = {"sequence": last["sequence"], "state": last["state"], "receipt_sha256": n.sha(n.read_bytes(path))}
    n.require(value["native_admission"] == admission)
    n.require(schema == "millefeuille-generation-failure-observation/v1" and capture_scope is None)
    return value
