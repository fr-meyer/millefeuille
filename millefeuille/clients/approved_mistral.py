"""Authority-bound candidate. No SDK import, retry, ambient credential or live probe.

The subprocess is the deadline boundary, not HTTPX's per-read timeout. Tests that
replace OwnedHTTPWorker qualify factory logic only, never real network behavior.
"""

import contextlib
import fcntl
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from ..domain.ocr_lifecycle import (
    ApprovedGraph,
    OperatorHMACVerifier,
    canonical,
    deny,
    digest,
    multipart,
)
from ..domain.ocr_request_budget import OCRBudgetError

# Self-contained child: token arrives only over a private pipe. No shell, SDK,
# environment credentials, proxy, redirects or retries. Parent bounds stdout.
_WORKER = r"""import base64, json, sys
import httpx

try:
    raw = sys.stdin.buffer.read(3000001)
    if len(raw) > 3000000:
        raise ValueError()
    q = json.loads(raw)
    with httpx.Client(
        transport=httpx.HTTPTransport(retries=0),
        trust_env=False,
        follow_redirects=False,
    ) as c:
        with c.stream(
            q["method"],
            q["url"],
            content=base64.b64decode(q["body"]),
            headers={
                "Authorization": "Bearer " + q["token"],
                "Content-Type": q["content_type"],
                "Accept-Encoding": "identity",
            },
            timeout=q["timeout"],
        ) as r:
            if (
                not 200 <= r.status_code < 300
                or r.headers.get("content-encoding", "identity").lower() != "identity"
            ):
                raise ValueError()
            out = bytearray()
            for chunk in r.iter_raw(chunk_size=4096):
                if len(out) + len(chunk) > q["cap"]:
                    raise ValueError()
                out.extend(chunk)
            sys.stdout.buffer.write(bytes(out))
except BaseException:
    sys.exit(73)
"""


class OwnedHTTPWorker:
    def send(self, method, url, body, content_type, token, cap, seconds):
        import base64
        import selectors

        if seconds <= 0:
            deny()
        envelope = canonical(
            {
                "method": method,
                "url": url,
                "body": base64.b64encode(body).decode(),
                "content_type": content_type,
                "token": token,
                "cap": cap,
                "timeout": seconds,
            }
        )
        if len(envelope) > 3000000:
            deny()
        deadline = time.monotonic() + seconds
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", _WORKER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env={"LANG": "C.UTF-8"},
            start_new_session=True,
        )
        output = bytearray()
        selector = selectors.DefaultSelector()
        try:
            os.set_blocking(process.stdin.fileno(), False)
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE)
            selector.register(process.stdout, selectors.EVENT_READ)
            offset = 0
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    deny()
                for key, _ in selector.select(remaining):
                    if key.fileobj is process.stdin:
                        written = os.write(
                            process.stdin.fileno(), envelope[offset : offset + 4096]
                        )
                        offset += written
                        if offset == len(envelope):
                            selector.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        chunk = os.read(
                            process.stdout.fileno(), min(4096, cap + 1 - len(output))
                        )
                        if not chunk:
                            selector.unregister(process.stdout)
                        elif len(output) + len(chunk) > cap:
                            deny()
                        else:
                            output.extend(chunk)
            if process.wait(timeout=max(0.001, deadline - time.monotonic())) != 0:
                deny()
            if time.monotonic() >= deadline:
                deny()
            return bytes(output)
        except BaseException:
            raise OCRBudgetError("wire-outcome-unknown-no-retry") from None
        finally:
            selector.close()
            import signal

            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            for stream in (process.stdin, process.stdout):
                stream.close()


def _json(raw):
    # Reuse A05's strict UTF8, duplicate-key/nonfinite/depth/node checks lazily.
    from .mistral_client import bounded_json

    return bounded_json(raw)


def _object(value, required, optional=()):
    if (
        type(value) is not dict
        or not set(required) <= value.keys()
        or not value.keys() <= set(required) | set(optional)
    ):
        deny()
    return value


def _file(value):
    _object(
        value,
        ("id",),
        (
            "object",
            "bytes",
            "created_at",
            "filename",
            "purpose",
            "sample_type",
            "source",
            "num_lines",
            "mimetype",
            "deleted",
        ),
    )
    if not isinstance(value["id"], str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,128}", value["id"]
    ):
        deny()
    for key, item in value.items():
        if key in {"bytes", "created_at", "num_lines"}:
            if item is not None and (type(item) is not int or item < 0):
                deny()
        elif key == "deleted":
            if type(item) is not bool:
                deny()
        elif item is not None and not isinstance(item, str):
            deny()
    return value["id"]


class ApprovedMistralLifecycle:
    """Only construct through approved(); all graph liability reserved before wire.

    Trusted verifier and token resolver are operator composition-root capabilities,
    not fields accepted from request JSON. Ledger path must be private custody.
    """

    def __init__(self, *args, **kwargs):
        deny()

    @classmethod
    def approved(cls, graph, capability, *, verifier, token_resolver, ledger):
        if (
            type(graph) is not ApprovedGraph
            or type(verifier) is not OperatorHMACVerifier
        ):
            deny()
        verifier.verify(
            graph, capability, required_seconds=graph.wall_seconds + 2
        )  # BEFORE resolver/ledger/wire
        try:
            token = token_resolver(graph.account, graph.credential_fingerprint)
        except BaseException:
            raise OCRBudgetError("credential-resolver-refused") from None
        if (
            type(token) is not str
            or not re.fullmatch(r"[A-Za-z0-9._~-]{1,8192}", token)
            or digest(token.encode()) != graph.credential_fingerprint
        ):
            deny()
        verifier.verify(graph, capability, required_seconds=graph.wall_seconds + 2)
        self = object.__new__(cls)
        self.graph, self.capability, self.verifier = graph, capability, verifier
        self.__token = token
        self._worker = OwnedHTTPWorker()
        self._started = time.monotonic()
        self._deadline = self._started + graph.wall_seconds
        self._ordinary_deadline = self._deadline - graph.finalization_seconds
        self._states = {
            d.document + ":" + k: "reserved"
            for d in graph.documents
            for k in ("upload", "signed_url", "ocr", "status", "delete")
        }
        self.receipts = {}
        self._dependencies = {}
        self._upload_bindings = {}
        self._run_lock = threading.Lock()
        self._fd = os.open(
            Path(ledger), os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._inode = os.fstat(self._fd)
            self._path = Path(ledger)
            self._append(
                {
                    "schema": "approved-ocr-ledger/v1",
                    "graph": graph.binding,
                    "reserved_calls": graph.calls,
                    "reserved_cost": graph.max_cost_microusd,
                    "reserved_read": graph.max_read_bytes,
                    "reserved_write": graph.max_write_bytes,
                    "operations": self._states,
                }
            )
            parent = os.open(self._path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        except BaseException:
            os.close(self._fd)
            raise OCRBudgetError("ledger-reservation-refused") from None
        return self

    def _append(self, event):
        raw = canonical(event) + b"\n"
        if os.lseek(self._fd, 0, os.SEEK_END) + len(raw) > 65536:
            deny()
        offset = 0
        while offset < len(raw):
            written = os.write(self._fd, raw[offset:])
            if written <= 0:
                deny()
            offset += written
        os.fsync(self._fd)

    def close(self):
        self.__token = ""
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def _send(
        self,
        document,
        kind,
        path,
        body=b"",
        content_type="application/json",
        sources=(),
    ):
        if self._fd is None:
            deny()
        self.verifier.verify(self.graph, self.capability)
        current = os.stat(self._path, follow_symlinks=False)
        if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (
            self._inode.st_dev,
            self._inode.st_ino,
        ):
            deny()
        key = document.document + ":" + kind
        deadline = (
            self._deadline if kind in {"status", "delete"} else self._ordinary_deadline
        )
        if kind == "status":
            # Reconciliation must not consume the deletion's final call window.
            deadline -= self.graph.call_seconds
        if (
            self._states[key] != "reserved"
            or len(body) > self.graph.max_request_bytes
            or time.monotonic() >= deadline
        ):
            deny()
        method = {
            "upload": "POST",
            "signed_url": "GET",
            "ocr": "POST",
            "status": "GET",
            "delete": "DELETE",
        }[kind]
        dependencies = self._dependencies.get(document.document, {})
        expected_path = {"upload": "/v1/files", "ocr": "/v1/ocr"}.get(kind)
        expected_sources = ()
        if kind != "upload":
            if (
                "file_id" not in dependencies
                or self._states[document.document + ":upload"] != "validated"
            ):
                deny()
            file_path = "/v1/files/" + dependencies["file_id"]
            expected_path = (
                "/v1/ocr"
                if kind == "ocr"
                else file_path + ("/url" if kind == "signed_url" else "")
            )
            expected_sources = (dependencies["upload_hash"],)
        if kind == "ocr":
            if self._states[document.document + ":signed_url"] != "validated":
                deny()
            expected_sources += (dependencies["url_hash"],)
            if body != dependencies["ocr_payload"]:
                deny()
        if kind == "upload" and (
            digest(body),
            content_type,
        ) != self._upload_bindings.get(document.document):
            deny()
        if kind != "upload" and content_type != "application/json":
            deny()
        if path != expected_path or tuple(sources) != expected_sources:
            deny()
        if kind in {"signed_url", "status", "delete"} and body:
            deny()
        url = "https://api.mistral.ai" + path
        binding = digest(
            canonical(
                {
                    "graph": self.graph.binding,
                    "document": document.document,
                    "kind": kind,
                    "method": method,
                    "url": url,
                    "content_type": content_type,
                    "payload_sha256": digest(body),
                    "sources": list(sources),
                }
            )
        )
        # Unknown is durable before spawning the only possible sender.
        self._append({"operation": key, "state": "unknown", "request_sha256": binding})
        self._states[key] = "unknown"
        try:
            raw = self._worker.send(
                method,
                url,
                body,
                content_type,
                self.__token,
                self.graph.max_response_bytes,
                min(self.graph.call_seconds, deadline - time.monotonic()),
            )
            if type(raw) is not bytes or len(raw) > self.graph.max_response_bytes:
                deny()
        except BaseException:
            raise OCRBudgetError("wire-outcome-unknown-no-retry") from None
        receipt = {
            "wire_observed": True,
            "semantic_status": "not-parsed",
            "raw_sha256": digest(raw),
            "raw_bytes": len(raw),
        }
        self.receipts[key] = receipt
        self._append(dict(operation=key, state="wire-observed", **receipt))
        self._states[key] = "wire-observed"
        return raw

    def _parse(self, document, kind, raw, parser):
        key = document.document + ":" + kind
        try:
            value = parser(_json(raw))
        except BaseException:
            self.receipts[key]["semantic_status"] = "parse-failed"
            self._append({"operation": key, "state": "parse-failed"})
            raise OCRBudgetError("invalid-provider-response") from None
        self.receipts[key]["semantic_status"] = "validated"
        self._append({"operation": key, "state": "validated"})
        self._states[key] = "validated"
        return value

    def run_document(self, document_id, data):
        """Serialize owned lifecycle; a competing caller never waits or sends."""
        if not self._run_lock.acquire(blocking=False):
            deny()
        try:
            return self._run_document(document_id, data)
        finally:
            self._run_lock.release()

    def _run_document(self, document_id, data):
        """No replay; finalize only this document's observed file ID."""
        document = next(
            (d for d in self.graph.documents if d.document == document_id), None
        )
        if (
            document is None
            or type(data) is not bytes
            or len(data) != document.size
            or digest(data) != document.sha256
        ):
            deny()
        body, content_type = multipart(document, data)
        self._upload_bindings[document.document] = (digest(body), content_type)
        raw = self._send(document, "upload", "/v1/files", body, content_type)
        file_id = self._parse(document, "upload", raw, _file)
        upload_hash = digest(raw)
        self._dependencies[document.document] = {
            "file_id": file_id,
            "upload_hash": upload_hash,
        }
        path = "/v1/files/" + file_id
        try:
            raw = self._send(
                document, "signed_url", path + "/url", sources=(upload_hash,)
            )

            def signed(value):
                _object(value, ("url",))
                url = value["url"]
                if (
                    not isinstance(url, str)
                    or len(url) > 16384
                    or any(ord(c) < 33 for c in url)
                ):
                    deny()
                parts = urlsplit(url)
                if (
                    parts.scheme != "https"
                    or not parts.hostname
                    or parts.username
                    or parts.password
                    or parts.fragment
                ):
                    deny()
                return url

            signed_url = self._parse(document, "signed_url", raw, signed)
            url_hash = digest(raw)
            payload = canonical(
                {
                    "model": self.graph.model,
                    "document": {"type": "document_url", "document_url": signed_url},
                }
            )
            self._dependencies[document.document].update(
                url_hash=url_hash, ocr_payload=payload
            )
            raw = self._send(
                document, "ocr", "/v1/ocr", payload, sources=(upload_hash, url_hash)
            )
            result = self._parse(document, "ocr", raw, validate_ocr)
            return result
        finally:
            # Both attempts are explicitly reserved. Failure of status never
            # creates a new edge, never prevents the already-approved deletion.
            try:
                raw = self._send(document, "status", path, sources=(upload_hash,))

                def same_file(value):
                    if _file(value) != file_id:
                        deny()
                    return value

                self._parse(document, "status", raw, same_file)
            finally:
                raw = self._send(document, "delete", path, sources=(upload_hash,))

                def deleted(value):
                    _object(value, ("id", "deleted"), ("object",))
                    if _file(value) != file_id or value["deleted"] is not True:
                        deny()
                    return True

                self._parse(document, "delete", raw, deleted)


def validate_ocr(value):
    """Conservative installed-SDK response subset; unknown structured assets refuse."""
    _object(value, ("model", "pages"), ("usage_info", "document_annotation"))
    if not isinstance(value["model"], str) or not re.fullmatch(
        r"mistral-ocr-(?!latest$)[A-Za-z0-9][A-Za-z0-9._-]{0,95}", value["model"]
    ):
        deny()
    if value.get("document_annotation") is not None:
        deny()
    pages = value["pages"]
    if type(pages) is not list or not 1 <= len(pages) <= 32:
        deny()
    total = 0
    for index, page in enumerate(pages):
        _object(
            page,
            ("index", "markdown"),
            ("images", "dimensions", "tables", "hyperlinks", "header", "footer"),
        )
        if (
            type(page["index"]) is not int
            or page["index"] != index
            or type(page["markdown"]) is not str
        ):
            deny()
        total += len(page["markdown"].encode())
        if len(page["markdown"].encode()) > 65536 or total > 262144:
            deny()
        for field in ("images", "tables", "hyperlinks"):
            if field in page and (type(page[field]) is not list or page[field]):
                deny()
        for field in ("header", "footer"):
            if page.get(field) is not None and type(page[field]) is not str:
                deny()
        dimensions = page.get("dimensions")
        if dimensions is not None:
            _object(dimensions, ("dpi", "height", "width"))
            if any(
                type(v) is not int or not 0 < v <= 100000 for v in dimensions.values()
            ):
                deny()
    if "usage_info" in value:
        usage = _object(value["usage_info"], ("pages_processed",), ("doc_size_bytes",))
        if type(usage["pages_processed"]) is not int or usage["pages_processed"] != len(
            pages
        ):
            deny()
        size = usage.get("doc_size_bytes")
        if size is not None and (type(size) is not int or not 0 <= size <= 2**20):
            deny()
    return value
