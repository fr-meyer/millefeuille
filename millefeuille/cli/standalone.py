"""Explicit standalone, one-paper processing. No Zotero mutation surface."""

from __future__ import annotations
from millefeuille.credentials import CredentialError, command_credential_scope, credential_value

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
    from millefeuille.domain.paper_intake import extract_and_analyze

    if getattr(args, "status", False):
        from millefeuille.domain.paper_intake import retained_papers
        if not args.item_key or not os.environ.get("ZOTERO_LIBRARY_ID", "").isdigit():
            raise ProcessError("Local status requires --item-key and numeric ZOTERO_LIBRARY_ID")
        paths = retained_papers(args)
        return {"item_key": args.item_key, "status": "retained-do-not-replay" if paths else "not-started",
                "paper_paths": [str(path) for path in paths], "provider_calls": 0,
                "files_written": 0}
    from millefeuille.domain import native_sdk_bridge as bridge

    if not args.legacy_analysis_only:
        os.environ.setdefault(bridge.ENV, bridge.PROFILE)
        if not bridge.selected():
            raise ProcessError("Ordinary scientific processing uses the native OpenClaw SDK")
    if args.writeback and (not args.live or args.legacy_analysis_only or args.defer_final):
        raise ProcessError("--writeback requires live clean scientific processing")
    if args.max_revision_rounds < 0:
        raise ProcessError("Revision rounds must be nonnegative")
    if args.writeback and args.library_type != "user":
        raise ProcessError("Scientific note publication supports user libraries")
    if args.writeback:
        from millefeuille.domain.clean_inventory import build
        from millefeuille.domain.clean_taxonomy import current
        from millefeuille.domain.standalone_writeback import load
        store = _safe_path(args.taxonomy_store)
        registry = current(store)
        inventory = build(args.historical_taxonomy, registry["registry_id"],
                          {"library_type": args.library_type,
                           "library_id": os.environ.get("ZOTERO_LIBRARY_ID", ""),
                           "item_keys": [args.item_key]})
        initial = load(store / "initialization.json")["initial_registry"]
        seed = initial.get("seed_provenance")
        if seed is not None and not any(
                row["source_sha256"] == seed["raw_sha256"]
                and row["semantic_sha256"] == seed["semantic_sha256"]
                for row in inventory["sources"]):
            raise ProcessError("Provide the actual seed taxonomy with --historical-taxonomy")
        # Inputs are actual taxonomy provenance, never an owner-grant document.
        # The detailed ancestry/tag checks are repeated against fresh final output.
        if not credential_value("ZOTERO_WRITE_KEY", stage="writeback").strip():
            raise ProcessError("Set ZOTERO_WRITE_KEY before publication processing")
    result = extract_and_analyze(args)
    if not args.writeback or "run_dir" not in result:
        return result
    from millefeuille.domain.scientific_note import prepare_note
    from millefeuille.domain.scientific_completion import complete
    from millefeuille.domain.scientific_revision import correct, science_path
    from millefeuille.domain.independent_review import ScientificUnresolved
    from millefeuille.domain.clean_final import prepare, execute
    from millefeuille.domain.clean_cards import export
    from millefeuille.domain.pipeline_guards import Lane, check_pause
    from millefeuille.domain.pipeline_receipts import save
    run = _safe_path(result["run_dir"])
    store = _safe_path(args.taxonomy_store)
    revision = 0
    with Lane(store):
        while True:
            check_pause(run.parent.parent.parent / "PAUSE")
            try:
                prepared = prepare_note(run, store, revision=revision)
                break
            except ScientificUnresolved as finding:
                if (finding.receipt["decision"] != "REVISE"
                        or revision >= args.max_revision_rounds):
                    raise
                if not correct(run, revision + 1, revision, finding.receipt, store=store):
                    raise
                revision += 1
                context = prepare(run, store, revision=revision)
                asyncio.run(execute(context, live=True))
                save(science_path(run, revision).parent / "classification-completed.json",
                     {"card": export(context)})
        publication = complete(run, store, inputs={"historical": args.historical_taxonomy,
                                                  "lineage": []},
                               prepared=prepared, retain_pdfs=True, operator_live=True)
    return {**result, "publication": publication,
            "status": "scientific-publication-verified-pdf-retained"}


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
        "OCR and analysis; --writeback additionally requests independently reviewed note/tag publication.",
    )
    parser.add_argument("--tag", default="millefeuille")
    parser.add_argument("--max-items", type=int, default=1)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--status", action="store_true", help="Local retained-data status only; no credentials or network")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--writeback", action="store_true",
                        help="Publish only after independent scientific review and fresh readback")
    parser.add_argument("--max-revision-rounds", type=int, default=1)
    parser.add_argument("--historical-taxonomy", type=Path, action="append", default=[],
                        help="Existing historical taxonomy input for semantic tag reconciliation")
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
    parser.add_argument("--defer-final", action="store_true")
    parser.add_argument("--shared-stop", type=Path)
    parser.add_argument("--library-type", choices=("user", "group"), default="user")
    parser.add_argument("--item-key", help="Pin the exact tagged parent item")
    parser.add_argument("--attachment-key", help="Require the exact discovered PDF attachment")
    parser.add_argument("--expected-item-version", type=int)
    parser.add_argument("--expected-attachment-version", type=int)
    parser.add_argument("--expected-pdf-size", type=int)
    parser.add_argument("--expected-pdf-md5", help="Bind the accepted PDF MD5 to fresh Zotero metadata")
    args = parser.parse_args(argv)
    try:
        with command_credential_scope(args, 'process'):
            # httpx INFO logs include signed redirect URLs, never enable these here.
            old_level = logging.getLogger("httpx").level
            logging.getLogger("httpx").setLevel(logging.WARNING)
            try:
                result = _operate(args)
                print(json.dumps(result, ensure_ascii=False, allow_nan=False))
                return 0
            except CredentialError:
                raise
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
    except CredentialError as exc:
        print(exc.code, file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    sys.exit(run_process_cli(sys.argv[1:]))
