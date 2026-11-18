"""Explicit standalone, one-paper processing. No Zotero mutation surface."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import urlencode, urlsplit
import uuid

import httpx

from millefeuille.domain.writeback_http import _identity, _pairs, _project

PDF_CAP = 50_000_000
JSON_CAP = 1_000_000


class ProcessError(ValueError):
    """Operator-safe message, never a remote payload or authenticated URL."""


def _json(raw):
    return json.loads(raw, object_pairs_hook=_pairs)


def _save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(
            value,
            stream,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
            default=lambda obj: (
                str(obj)
                if isinstance(obj, Path)
                else (_ for _ in ()).throw(TypeError("Non-JSON adapter result"))
            ),
        )
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9]{8}", value):
        raise ProcessError("Zotero returned an invalid item identity")
    return value


def _safe_path(path):
    path = Path(os.path.abspath(path))
    for part in (*reversed(path.parents), path):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ProcessError("Use a trusted output path without symlinks/junctions")
    return path


async def _get(url, secret, *, cap, pdf=False):
    # Caller constructs API URLs; credentials never cross a storage redirect.
    headers = {"Accept-Encoding": "identity", "Zotero-API-Version": "3"}
    if secret:
        headers["Zotero-API-Key"] = secret
    async with asyncio.timeout(60):
        async with httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
            timeout=20,
        ) as client:
            for hop in range(2):
                async with client.stream("GET", url, headers=headers) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        if not pdf or hop:
                            raise ProcessError("Unexpected Zotero redirect; no retry")
                        target = response.headers.get("location", "")
                        parts = urlsplit(target)
                        # Zotero-managed storage only, never arbitrary attachment URLs.
                        if (
                            parts.scheme != "https"
                            or parts.username
                            or parts.password
                            or parts.port not in (None, 443)
                            or parts.fragment
                            or parts.hostname
                            not in {
                                "zotero.s3.amazonaws.com",
                                "files.zotero.net",
                                "zotero.s3.us-east-1.amazonaws.com",
                                "zoterofilestorage.s3.us-east-1.amazonaws.com",
                            }
                        ):
                            host = parts.hostname or "missing"
                            safe_host = (
                                host
                                if re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host)
                                else "redacted"
                            )
                            raise ProcessError(
                                "Unrecognized Zotero storage redirect host="
                                + safe_host
                                + "; URL/query withheld; no provider call made"
                            )
                        url = target
                        headers = {"Accept-Encoding": "identity"}
                        continue
                    if response.status_code != 200:
                        raise ProcessError(
                            f"Zotero read failed (HTTP {response.status_code}); "
                            "no retry"
                        )
                    if (
                        response.headers.get("content-encoding", "identity")
                        != "identity"
                    ):
                        raise ProcessError("Compressed transport body refused")
                    length = response.headers.get("content-length")
                    if length and (not length.isdigit() or int(length) > cap):
                        raise ProcessError("Zotero response exceeds byte budget")
                    result = bytearray()
                    async for chunk in response.aiter_raw():
                        if len(result) + len(chunk) > cap:
                            raise ProcessError("Zotero response exceeds byte budget")
                        result.extend(chunk)
                    return bytes(result)
    raise ProcessError("Zotero response unavailable")


async def _discover(base, secret, tag, library_type, library_id, selected_key):
    query = urlencode(
        {
            "tag": tag,
            "limit": 1,
            "sort": "dateAdded",
            "direction": "desc",
            "format": "json",
        }
    )
    if selected_key:
        raw = await _get(f"{base}/items/{selected_key}", secret, cap=65536)
        rows = [_json(raw)]
    else:
        raw = await _get(f"{base}/items/top?{query}", secret, cap=JSON_CAP)
        rows = _json(raw)
    if not isinstance(rows, list) or len(rows) > 1:
        raise ProcessError("Discovery exceeded the one-paper bound")
    if not rows:
        return None
    row = rows[0]
    key = _key(row.get("key"))
    identity = _identity(
        {"library_type": library_type, "library_id": library_id, "key": key}
    )
    if selected_key and key != selected_key:
        raise ProcessError("Selected item key mismatch")
    # Reuse the repository's strict Zotero envelope/provenance validator.
    snapshot, proof = _project(identity, json.dumps(row).encode(), row.get("version"))
    data = snapshot["data"]
    if data.get("itemType") in ("attachment", "note") or tag not in {
        x.get("tag") for x in data.get("tags", []) if isinstance(x, dict)
    }:
        raise ProcessError("Selected item is not a parent paper with the exact tag")
    children_raw = await _get(
        f"{base}/items/{key}/children?limit=11&format=json", secret, cap=JSON_CAP
    )
    children = _json(children_raw)
    if not isinstance(children, list) or len(children) > 10:
        raise ProcessError("Attachment discovery exceeds ten children; narrow manually")
    pdfs = []
    for child in children:
        child_data = child.get("data", {})
        if child_data.get("itemType") != "attachment":
            continue
        if child_data.get("contentType") != "application/pdf":
            continue
        attachment = _key(child.get("key"))
        if child_data.get("parentItem") != key:
            raise ProcessError("Attachment parent mismatch")
        if child_data.get("linkMode") not in ("imported_file", "imported_url"):
            raise ProcessError("PDF is not a Zotero-stored file; upload it first")
        library = child.get("library", {})
        if (
            child_data.get("key") != attachment
            or type(child.get("version")) is not int
            or child["version"] < 1
            or child_data.get("version") != child["version"]
            or library.get("type") != library_type
            or str(library.get("id")) != library_id
        ):
            raise ProcessError("Attachment library/version identity mismatch")
        pdfs.append(child)
    if len(pdfs) != 1:
        raise ProcessError(
            "Exactly one stored PDF is required; resolve missing/multiple PDFs"
        )
    return {
        "item": row,
        "item_proof": proof,
        "attachment": pdfs[0],
        "discovery_raw_utf8": raw.decode("utf-8"),
        "children_raw_utf8": children_raw.decode("utf-8"),
        "discovery_sha256": hashlib.sha256(raw).hexdigest(),
        "children_sha256": hashlib.sha256(children_raw).hexdigest(),
    }


def _executor(value):
    if value is None:
        executable = shutil.which("codex")
        if executable is None:
            raise ProcessError(
                "Install Codex CLI on PATH and run codex login, or supply "
                "--executor-command JSON_ARGV before OCR spend"
            )
        # A local authentication-status check, not a generation request.
        # Never capture/print credentials or forward provider API-key fallbacks.
        environment = {
            key: val
            for key, val in os.environ.items()
            if key
            not in {
                "OPENAI_API_KEY",
                "CODEX_API_KEY",
                "ZOTERO_READ_KEY",
                "ZOTERO_WRITE_KEY",
                "MISTRAL_API_KEY",
            }
        }
        try:
            status = subprocess.run(
                [executable, "login", "status"],
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ProcessError(
                "Codex login status unavailable; authenticate first"
            ) from None
        if status.returncode != 0:
            raise ProcessError("Run codex login with your ChatGPT subscription first")
        # None selects the frozen analysis adapter native Codex route.
        # Its strict authentication/model checks remain authoritative.
        return None
    try:
        argv = _json(value)
    except (ValueError, TypeError):
        raise ProcessError(
            "--executor-command must be a JSON array of strings"
        ) from None
    if (
        not isinstance(argv, list)
        or not argv
        or len(argv) > 64
        or any(not isinstance(x, str) or not x or "\0" in x for x in argv)
    ):
        raise ProcessError("--executor-command must be a nonempty JSON argv array")
    executable = shutil.which(argv[0])
    if executable is None:
        raise ProcessError(
            "Executor executable not found; install/authenticate it first"
        )
    return [executable, *argv[1:]]


def _artifact(value, name, root):
    path = _safe_path(value.get(name, ""))
    if (
        not path.is_relative_to(root)
        or not path.is_file()
        or not 0 < path.stat().st_size <= 100_000_000
    ):
        raise ProcessError(f"Adapter did not produce a nonempty local {name}")
    return {
        "ref": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _operate(args):
    if args.max_items != 1:
        raise ProcessError("This bounded command requires --max-items 1")
    if not args.tag or len(args.tag) > 128 or any(ord(c) < 32 for c in args.tag):
        raise ProcessError("Provide one nonempty exact tag, at most 128 characters")
    root = _safe_path(args.output_dir)
    executor = None
    clean = not args.legacy_analysis_only
    if args.live:
        if clean:
            from millefeuille.domain.clean_evidence import analyze as analyze_paper
            from millefeuille.domain.clean_final import generation_preflight
            from millefeuille.domain.clean_taxonomy import current

            if args.taxonomy or args.final_taxonomy:
                raise ProcessError("Clean pipeline rejects historical taxonomy priors")
            registry = current(args.taxonomy_store)
            generation_preflight()
            if args.executor_command:
                raise ProcessError("Clean pipeline uses packaged adapter")
            if registry["entries"] and not os.environ.get("OPENAI_API_KEY", "").strip():
                raise ProcessError("Decisions credential required before OCR")
        else:
            if not args.legacy_analysis_only and not os.environ.get("OPENAI_API_KEY"):
                raise ProcessError(
                    "Final classification requires OPENAI_API_KEY before OCR spend"
                )
            executor = _executor(args.executor_command)
            if not args.taxonomy:
                raise ProcessError(
                    "--live requires --taxonomy PATH before provider spend"
                )
            taxonomy = _safe_path(args.taxonomy)
            if not taxonomy.is_file() or taxonomy.stat().st_size > JSON_CAP:
                raise ProcessError("Taxonomy must be a local JSON file under 1 MB")
            from millefeuille.domain.taxonomy import load_taxonomy_registry

            registry = load_taxonomy_registry(taxonomy)
            if registry["status"] != "released":
                raise ProcessError("Use a released taxonomy registry")
            with taxonomy.open("rb") as stream:
                taxonomy_bytes = stream.read(JSON_CAP + 1)
            if len(taxonomy_bytes) > JSON_CAP:
                raise ProcessError("Taxonomy changed beyond byte budget")
            from millefeuille.domain.direct_analysis import analyze_paper
        if not os.environ.get("MISTRAL_API_KEY", "").strip():
            raise ProcessError("Set MISTRAL_API_KEY for live OCR")
        from millefeuille.clients.direct_mistral import extract_pdf
    library = os.environ.get("ZOTERO_LIBRARY_ID", "")
    secret = os.environ.get("ZOTERO_READ_KEY", "")
    if not re.fullmatch(r"[1-9][0-9]{0,14}", library):
        raise ProcessError("Set ZOTERO_LIBRARY_ID to the numeric library ID")
    if not re.fullmatch(r"[A-Za-z0-9]{1,128}", secret):
        raise ProcessError("Set ZOTERO_READ_KEY to a library-readable API key")
    if args.item_key:
        _key(args.item_key)
    prefix = "users" if args.library_type == "user" else "groups"
    base = f"https://api.zotero.org/{prefix}/{library}"
    selected = asyncio.run(
        _discover(base, secret, args.tag, args.library_type, library, args.item_key)
    )
    if selected is None:
        return {
            "status": "no-matching-paper",
            "provider_calls": 0,
            "zotero_writeback": "none",
        }
    item, attachment = selected["item"], selected["attachment"]
    key, attachment_key = item["key"], attachment["key"]
    if not args.live:
        return {
            "status": "read-only-discovery",
            "item_key": key,
            "attachment_key": attachment_key,
            "provider_calls": 0,
            "files_written": 0,
            "zotero_writeback": "none",
            "live_requires": [
                "Packaged subscription adapter credentials"
                if clean
                else "Reviewed subscription adapter",
                "--taxonomy-store EXPLICIT_INITIALIZED_STORE"
                if clean
                else "--taxonomy PATH",
                "MISTRAL_API_KEY",
                "--live",
            ],
        }
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    paper = root / f"{args.library_type}-{library}-{key}"
    try:
        paper.mkdir(mode=0o700)
    except FileExistsError:
        raise ProcessError(
            "Paper reservation already exists. Inspect its journal; do not retry "
            "uncertain provider work or change output roots to bypass it."
        ) from None
    run_id = uuid.uuid4().hex
    run = paper / "runs" / run_id
    run.parent.mkdir(mode=0o700)
    run.mkdir(mode=0o700)
    binding = {
        "run_id": run_id,
        "library_type": args.library_type,
        "library_id": library,
        "item_key": key,
        "attachment_key": attachment_key,
        "item_version": item["version"],
        "attachment_version": attachment["version"],
        "tag": args.tag,
        "max_items": 1,
        "zotero_writeback": "none",
    }
    _save(paper / "reservation.json", binding)
    _save(run / "00-selected.json", selected)
    _save(run / "01-download-started.json", binding)
    pdf = asyncio.run(
        _get(f"{base}/items/{attachment_key}/file", secret, cap=PDF_CAP, pdf=True)
    )
    if not pdf.startswith(b"%PDF-"):
        raise ProcessError("Downloaded attachment is not a PDF")
    pdf_path = paper / "source.pdf"
    with pdf_path.open("xb") as stream:
        os.chmod(pdf_path, 0o600)
        stream.write(pdf)
        stream.flush()
        os.fsync(stream.fileno())
    digest = hashlib.sha256(pdf).hexdigest()
    _save(
        paper / "manifest.json",
        {
            "schema_version": "millefeuille-standalone-source-pack/v1",
            **binding,
            "created_at": datetime.now(UTC).isoformat(),
            "source": {"ref": "source.pdf", "sha256": digest, "byte_size": len(pdf)},
            "provenance": {
                "intake_method": "live-zotero-api",
                "selection_ref": f"runs/{run_id}/00-selected.json",
            },
            "verification": "locally-hashed-live-download-not-independent-checksum",
        },
    )
    del pdf
    taxonomy_copy = None
    if not clean:
        taxonomy_copy = run / "taxonomy.json"
        with taxonomy_copy.open("xb") as stream:
            os.chmod(taxonomy_copy, 0o600)
            stream.write(taxonomy_bytes)
        if load_taxonomy_registry(taxonomy_copy) != registry:
            raise ProcessError("Taxonomy changed after preflight")
    _save(
        run / "02-ocr-started.json",
        {**binding, "source_sha256": digest, "model": "mistral-ocr-latest"},
    )
    ocr = extract_pdf(
        pdf_path,
        run / "ocr",
        model="mistral-ocr-latest",
        timeout_seconds=180.0,
        max_pages=250,
        max_input_bytes=PDF_CAP,
    )
    if ocr.get("requested_model") != "mistral-ocr-latest" or not ocr.get(
        "returned_model"
    ):
        raise ProcessError("OCR model provenance missing or mismatched")
    ocr_files = {
        name: _artifact(ocr, name, run)
        for name in ("fulltext_path", "evidence_path", "raw_response_path")
    }
    _save(run / "03-ocr-completed.json", {"files": ocr_files, "result": ocr})
    _save(
        run / "04-analysis-started.json",
        {**binding, "model": "openai/gpt-6-astra", "reasoning": "xhigh"},
    )
    analysis = analyze_paper(
        ocr["fulltext_path"],
        run / "analysis",
        title=item["data"].get("title", ""),
        paper_id=f"zotero-{key}",
        executor_argv=executor,
        model="openai/gpt-6-astra",
        reasoning="xhigh",
        timeout_seconds=300,
        taxonomy_path=taxonomy_copy,
    )
    if (
        analysis.get("model") != "openai/gpt-6-astra"
        or analysis.get("reasoning") != "xhigh"
    ):
        raise ProcessError("Analysis model provenance mismatch")
    files = {
        name: _artifact(analysis, name, run)
        for name in (
            ("summary_path", "evidence_path")
            if clean
            else ("summary_path", "classification_path", "evidence_path")
        )
    }
    _save(run / "05-analysis-completed.json", {"files": files, "result": analysis})
    if clean:
        from millefeuille.domain.clean_final import execute, prepare

        context = prepare(run, args.taxonomy_store)
        asyncio.run(execute(context, live=True))
    result = {
        "status": "local-processing-complete",
        **binding,
        "run_dir": str(run),
        "source_sha256": digest,
        "canonical_stage_acceptance": "not-claimed",
    }
    if clean:
        from millefeuille.domain.clean_cards import export

        result["card"] = export(context, args.memory_dir)
        result["retention"] = "preserved-pending-cloud-hash-qualification"
        _save(run / "06-completed.json", result)
        return result
    _save(run / "06-completed.json", result)
    result.update(
        finalize_completed_run(
            run,
            keep_pdf=args.keep_pdf,
            memory_dir=args.memory_dir,
            project_comment=args.memory_project_comment,
        )
    )
    return result


def finalize_completed_run(
    run_dir, *, keep_pdf=False, memory_dir=None, project_comment=None
):
    """Finalize real completed artifacts without repeating OCR or model calls."""
    from millefeuille.domain.paper_cards import export_paper_card
    from millefeuille.domain.standalone_retention import retain

    card = export_paper_card(
        run_dir, memory_dir=memory_dir, project_comment=project_comment
    )
    retention = (
        {"status": "kept-by-explicit-request"}
        if keep_pdf
        else retain(run_dir, live=True)
    )
    return {"paper_card": card, "retention": retention}


def run_process_cli(argv):
    parser = argparse.ArgumentParser(
        prog="millefeuille process",
        description="Discover one tagged paper read-only; "
        "--live explicitly approves local PDF, "
        "OCR and analysis work, never Zotero writes.",
    )
    parser.add_argument("--tag", default="millefeuille")
    parser.add_argument("--max-items", type=int, default=1)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--keep-pdf",
        action="store_true",
        help="Explicitly retain both local PDFs after successful processing",
    )
    parser.add_argument(
        "--memory-dir",
        type=Path,
        help="Optional explicit directory for searchable paper-card exports",
    )
    parser.add_argument(
        "--memory-project-comment",
        help="Optional project identity comment on exported memory lines",
    )
    parser.add_argument("--executor-command", metavar="JSON_ARGV")
    parser.add_argument("--taxonomy", type=Path)
    parser.add_argument("--final-taxonomy", type=Path)
    parser.add_argument("--taxonomy-store", type=Path)
    parser.add_argument("--legacy-analysis-only", action="store_true")
    parser.add_argument("--library-type", choices=("user", "group"), default="user")
    parser.add_argument("--item-key", help="Pin the exact tagged parent item")
    args = parser.parse_args(argv)
    # httpx INFO logs include signed redirect URLs, never enable these here.
    old_level = logging.getLogger("httpx").level
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        result = _operate(args)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except ProcessError as exc:
        print(f"millefeuille process: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        # Provider/network exceptions may contain credentials, text or signed URLs.
        print(
            f"millefeuille process: {type(exc).__name__}; stopped without retry. "
            "Check configuration and retained local journal/evidence. "
            "A started stage without completion has an uncertain outcome.",
            file=sys.stderr,
        )
        return 2
    finally:
        logging.getLogger("httpx").setLevel(old_level)


if __name__ == "__main__":
    sys.exit(run_process_cli(sys.argv[1:]))
