"""Explicit operator-only OCR, independent of the protected/legacy adapters.

Call only after approving forwarding this exact PDF to Mistral. No retry or
resume is automatic: an existing reservation requires operator reconciliation.
HTTP timeouts bound individual I/O waits, not a hard process wall deadline.
"""

from datetime import UTC, datetime
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import time


class DirectOCRError(RuntimeError):
    """Safe error without provider payloads, URLs or credentials."""


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _write(path, data):
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _json(path, value):
    _write(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


def _page_count(data):
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        raise DirectOCRError(
            "Install millefeuille[native] for PDF page admission"
        ) from None
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ValueError()
        return len(reader.pages)
    except Exception:
        raise DirectOCRError("Cannot admit encrypted or invalid original PDF") from None


def extract_pdf(
    pdf_path,
    output_dir,
    *,
    api_key=None,
    model="mistral-ocr-latest",
    timeout_seconds=180.0,
    max_pages=250,
    max_input_bytes=50000000,
) -> dict:
    """Retain original, exact OCR wire JSON, page evidence and fulltext privately.

    At most one upload, OCR and delete request. Cleanup has its own timeout,
    capped at 30 seconds, so expiration of the work budget cannot skip deletion.
    Unknown upload outcomes remain reserved and require provider reconciliation.
    The caller owns Zotero identity/approval and source-pack manifest integration.
    POSIX modes are restrictive; Windows callers must supply a private ACL root.
    """
    if (
        type(timeout_seconds) not in (int, float)
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= 3600
        or type(max_pages) is not int
        or not 1 <= max_pages <= 1000
        or type(max_input_bytes) is not int
        or not 1 <= max_input_bytes <= 50000000
        or not isinstance(model, str)
        or not re.fullmatch(r"mistral-ocr-[A-Za-z0-9.-]+", model)
    ):
        raise DirectOCRError("Invalid bounded OCR settings")
    key = api_key if api_key is not None else os.environ.get("MISTRAL_API_KEY")
    if not isinstance(key, str) or not key.strip():
        raise DirectOCRError("MISTRAL_API_KEY is required for explicit direct OCR")
    source = Path(pdf_path)
    if source.is_symlink() or not source.is_file():
        raise DirectOCRError("Original PDF must be a regular non-symlink file")
    with source.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > max_input_bytes:
            raise DirectOCRError("Original PDF exceeds input byte limit")
        data = stream.read(max_input_bytes + 1)
        after = os.fstat(stream.fileno())
    if (
        not data.startswith(b"%PDF-")
        or len(data) > max_input_bytes
        or len(data) != before.st_size
        or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
    ):
        raise DirectOCRError("Invalid or changing original PDF")
    count = _page_count(data)
    if not 1 <= count <= max_pages:
        raise DirectOCRError("Original PDF exceeds page limit or has no pages")

    import httpx
    from mistralai import Mistral

    root = Path(output_dir).absolute()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise DirectOCRError("Output must be a private directory")
    # One atomic persistent reservation per output root, including failures.
    run = root / "direct-mistral"
    try:
        run.mkdir(mode=0o700)
    except FileExistsError:
        raise DirectOCRError(
            "OCR reservation exists; reconcile journal before any new paid call"
        ) from None
    started = time.monotonic()
    journal = {
        "schema_version": "millefeuille-direct-ocr/v1",
        "created_at": datetime.now(UTC).isoformat(),
        "source_sha256": _sha(data),
        "canonical_filename": source.name,
        "input_bytes": len(data),
        "input_page_count": count,
        "requested_model": model,
        "retry_policy": "none; uncertain outcomes require operator reconciliation",
        "timeout_seconds": timeout_seconds,
        "timeout_semantics": "per-I/O; remaining work budget checked between calls",
    }
    _json(run / "reservation.json", journal)
    _write(run / "original.pdf", data)
    file_id = None
    cleanup_ok = False
    failed = False
    failure = None
    stage = "upload"
    raw = None

    def remaining_ms():
        seconds = timeout_seconds - (time.monotonic() - started)
        if seconds <= 0:
            raise DirectOCRError("OCR work budget expired")
        return max(1, int(seconds * 1000))

    def capture(response):
        nonlocal raw
        if response.request.url.path == "/v1/ocr" and response.status_code == 200:
            raw = response.read()
            _write(run / "provider-response.json", raw)

    # Fixed vendor origin; no ambient proxy, redirects, URL fetches or SDK logging.
    with httpx.Client(
        trust_env=False,
        follow_redirects=False,
        transport=httpx.HTTPTransport(retries=0),
        event_hooks={"response": [capture]},
    ) as http:
        client = Mistral(
            api_key=key,
            server_url="https://api.mistral.ai",
            client=http,
            retry_config=None,
            timeout_ms=max(1, int(timeout_seconds * 1000)),
        )
        try:
            _json(run / "upload-started.json", {"outcome": "unknown_until_response"})
            uploaded = client.files.upload(
                file={"file_name": "original.pdf", "content": data},
                purpose="ocr",
                retries=None,
                timeout_ms=remaining_ms(),
            )
            file_id = uploaded.id
            if not isinstance(file_id, str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{1,128}", file_id
            ):
                file_id = None
                raise DirectOCRError(
                    "Invalid upload identity; reconcile provider files"
                )
            _json(run / "upload.json", {"file_id": file_id})
            stage = "ocr"
            _json(run / "ocr-started.json", {"outcome": "unknown_until_response"})
            client.ocr.process(
                model=model,
                document={"type": "file", "file_id": file_id},
                pages=list(range(count)),
                include_image_base64=False,
                table_format="markdown",
                retries=None,
                timeout_ms=remaining_ms(),
            )
            if raw is None:
                raise DirectOCRError("Missing exact provider response")
        except Exception as exc:
            failed = True
            status = getattr(exc, "status_code", None)
            failure = {
                "stage": stage,
                "exception_type": type(exc).__name__,
                "http_status": status if type(status) is int else None,
                "automatic_retry": False,
            }
            # Never persist exception messages/response bodies: they may contain
            # credential-bearing URLs or private document content.
            _json(run / "failure.json", failure)
        finally:
            if file_id is not None:
                try:
                    deleted = client.files.delete(
                        file_id=file_id,
                        retries=None,
                        timeout_ms=max(1, int(min(timeout_seconds, 30) * 1000)),
                    )
                    cleanup_ok = deleted.deleted is True and deleted.id == file_id
                except Exception:
                    cleanup_ok = False
            _json(
                run / "cleanup.json",
                {
                    "file_id": file_id,
                    "status": "deleted" if cleanup_ok else "needs_reconciliation",
                    "automatic_retry": False,
                },
            )
    if failed or not cleanup_ok:
        detail = "cleanup unconfirmed"
        if failure is not None:
            detail = (
                f"{failure['stage']} failed: {failure['exception_type']}"
                + (
                    f" (HTTP {failure['http_status']})"
                    if failure["http_status"] is not None
                    else ""
                )
            )
        raise DirectOCRError(
            f"Direct OCR {detail}; inspect private journal, "
            "reconcile provider files, do not blindly retry"
        ) from None
    try:
        payload = json.loads(raw)
        pages = payload["pages"]
        returned_model = payload["model"]
        if (
            not isinstance(returned_model, str)
            or not returned_model
            or not isinstance(pages, list)
            or len(pages) != count
            or any(
                type(page.get("index")) is not int
                or page["index"] != index
                or not isinstance(page.get("markdown"), str)
                for index, page in enumerate(pages)
            )
        ):
            raise ValueError()
        fulltext = "\n\n".join(
            f"<!-- page {index + 1} -->\n{page['markdown']}"
            for index, page in enumerate(pages)
        ).encode("utf-8")
        if not any(page["markdown"].strip() for page in pages):
            raise ValueError()
    except Exception:
        raise DirectOCRError(
            "Provider response incomplete; raw evidence retained, no automatic retry"
        ) from None
    _write(run / "fulltext.md", fulltext)
    evidence = {
        **journal,
        "provider": "mistral",
        "returned_model": returned_model,
        "page_count": len(pages),
        "pages": pages,
        "usage_info": payload.get("usage_info"),
        "raw_response_sha256": _sha(raw),
        "fulltext_sha256": _sha(fulltext),
        "original_pdf_path": str(run / "original.pdf"),
        "provider_payload_disposition": "retained privately; never commit",
        "cleanup": "deleted",
        "warnings": [
            "SDK does not expose OCR4 word-confidence/include_blocks request options",
            "OCR evidence alone is not classification or exact-value acceptance",
        ],
    }
    _json(run / "evidence.json", evidence)
    return {
        "fulltext_path": str(run / "fulltext.md"),
        "evidence_path": str(run / "evidence.json"),
        "raw_response_path": str(run / "provider-response.json"),
        "requested_model": model,
        "returned_model": returned_model,
        "page_count": len(pages),
    }
