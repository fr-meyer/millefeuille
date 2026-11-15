"""Concrete OCR recipes. Authentication belongs to an externally provisioned verifier.

No credentials, allowance discovery, diagnostic requests or environment defaults.
The legacy synthetic graph deliberately remains a separate API.
"""

from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
import math
import re
import time

from .ocr_request_budget import OCRBudgetError


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def deny():
    raise OCRBudgetError("approved-ocr-authority-or-binding-refused")


def integer(value, low, high):
    return type(value) is int and low <= value <= high


@dataclass(frozen=True)
class DocumentRecipe:
    document: str
    sha256: str
    size: int


@dataclass(frozen=True)
class ApprovedGraph:
    documents: tuple[DocumentRecipe, ...]
    account: str
    credential_fingerprint: str
    allowance_receipt_sha256: str
    price_receipt_sha256: str
    max_cost_microusd: int
    max_request_bytes: int
    max_response_bytes: int
    max_read_bytes: int
    max_write_bytes: int
    wall_seconds: int
    finalization_seconds: int
    call_seconds: int
    model: str = "mistral-ocr-latest"
    recipe: str = "mistral-files-ocr-v1"
    auth_route: str = "explicit-memory-bearer"

    @property
    def calls(self):
        return 5 * len(self.documents)

    @property
    def binding(self):
        return digest(canonical(asdict(self)))

    def validate(self):
        if (
            type(self.documents) is not tuple
            or not 1 <= len(self.documents) <= 8
            or any(type(d) is not DocumentRecipe for d in self.documents)
            or self.model != "mistral-ocr-latest"
            or self.recipe != "mistral-files-ocr-v1"
            or self.auth_route != "explicit-memory-bearer"
            or not isinstance(self.account, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", self.account)
            or not integer(self.max_cost_microusd, 0, 10**9)
            or not integer(self.max_request_bytes, 512, 2**21)
            or not integer(self.max_response_bytes, 128, 2**20)
            or not integer(
                self.max_read_bytes, self.calls * self.max_response_bytes, 2**26
            )
            or not integer(
                self.max_write_bytes, self.calls * self.max_request_bytes, 2**26
            )
            or not integer(self.wall_seconds, 3, 600)
            or not integer(self.finalization_seconds, 1, self.wall_seconds - 1)
            or not integer(self.call_seconds, 1, self.wall_seconds)
            or self.finalization_seconds < 2 * len(self.documents) * self.call_seconds
        ):
            deny()
        identities = set()
        for d in self.documents:
            if (
                not isinstance(d.document, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", d.document)
                or d.document in identities
                or not integer(d.size, 1, 2**20)
                or d.size + 512 > self.max_request_bytes
            ):
                deny()
            identities.add(d.document)
        for value in (
            self.credential_fingerprint,
            self.allowance_receipt_sha256,
            self.price_receipt_sha256,
            *(d.sha256 for d in self.documents),
        ):
            if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
                deny()


@dataclass(frozen=True)
class D03Capability:
    """Externally issued HMAC, not a payload assertion. Never write it to receipts."""

    graph_sha256: str
    account: str
    credential_fingerprint: str
    expires: int
    issued: int
    nonce: str
    signature: str

    def message(self):
        fields = asdict(self)
        del fields["signature"]
        return canonical(fields)


class OperatorHMACVerifier:
    """Composition-root trust boundary; key MUST come from independent operator custody.

    Constructing this with an attacker-chosen key grants no real-world authority.
    Applications must pin this verifier, never accept a key from the capability.
    This module does not provision, retrieve or persist keys.
    """

    def __init__(self, trusted_key: bytes):
        if type(trusted_key) is not bytes or len(trusted_key) < 32:
            deny()
        self.__key = trusted_key

    def verify(self, graph, capability, now=None, *, required_seconds=0):
        graph.validate()
        now = time.time() if now is None else now
        if (
            type(capability) is not D03Capability
            or not integer(capability.issued, 0, 2**53)
            or not integer(capability.expires, 0, 2**53)
            or not math.isfinite(now)
            or type(required_seconds) not in (int, float)
            or not math.isfinite(required_seconds)
            or required_seconds < 0
            or not capability.issued <= now < capability.expires
            or now + required_seconds >= capability.expires
            or capability.expires - capability.issued > 900
            or capability.graph_sha256 != graph.binding
            or capability.account != graph.account
            or capability.credential_fingerprint != graph.credential_fingerprint
            or not isinstance(capability.nonce, str)
            or not re.fullmatch(r"[a-f0-9]{32}", capability.nonce)
            or not isinstance(capability.signature, str)
            or not hmac.compare_digest(
                hmac.new(self.__key, capability.message(), hashlib.sha256).hexdigest(),
                capability.signature,
            )
        ):
            deny()


def multipart(document, data):
    """Fixed framing and filename: no user filenames or header injection."""
    boundary = "mf-" + document.sha256
    body = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="purpose"\r\n\r\nocr\r\n'
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; '
            'filename="document.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
        ).encode()
        + data
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return body, "multipart/form-data; boundary=" + boundary
