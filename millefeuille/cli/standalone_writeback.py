"""Explicit CLI for one additive standalone note, never parent mutation."""
from millefeuille.credentials import CredentialError, command_credential_scope

import argparse
import asyncio
import json

from millefeuille.domain import standalone_writeback as note
from millefeuille.domain.standalone_writeback import execute
from millefeuille.domain.standalone_writeback import prepare as legacy_prepare


def prepare(run_dir, item_key, library_id, approval_manifest):
    approval = note.load(approval_manifest)
    revision = approval["items"][0].get("revision", 0) if approval.get("items") else 0
    from millefeuille.domain.scientific_revision import science_path

    if approval["schema"] == "millefeuille-standalone-note-approval/v1":
        return legacy_prepare(run_dir, item_key, library_id, approval_manifest)
    ordinary = approval["schema"] == "millefeuille-scientific-note-evidence/v1"
    note.require(ordinary or approval["schema"] == "millefeuille-clean-science-note-approval/v1")
    if ordinary:
        note.require(approval["acceptance_kind"] == "automated-independent-review")
    else:
        note.require(approval["approved"] is True and approval["owner"] == "gateway-owner")
    note.require(
        approval["library_type"] == "user" and approval["library_id"] == library_id
    )
    note.require(len(approval["items"]) == 1)
    if approval.get("acceptance_kind") == "automated-independent-review":
        from millefeuille.clients.direct_generation import validate_response

        if not ordinary:
            owner_path = approval["owner_authority"]
            owner = note.load(owner_path)
            note.require(
                note.sha(note.read_bytes(owner_path)) == approval["owner_authority_sha256"]
            )
            note.require(owner["approved"] is True and owner["owner"] == "gateway-owner")
            note.require(item_key in owner["keys"] and owner["library_id"] == library_id)
        review_path = note.safe_path(approval["review"])
        receipt = note.load(review_path)
        note.require(
            note.sha(note.read_bytes(review_path)) == approval["review_sha256"]
        )
        note.require(
            receipt["decision"] == "PASS"
            and receipt["reviewer_kind"] == "independent-model-request"
        )
        from millefeuille.domain.request_recovery import chosen_paths

        request_path, response_path = chosen_paths(
            review_path.parent / "review-request.json"
        )
        request = note.load(request_path)
        response = note.load(response_path)
        validate_response(request, response)
        note.require(receipt["provider_request_id"] == response["provider_request_id"])
        note.require(
            note.sha(note.read_bytes(response_path)) == receipt["response_sha256"]
        )
        note.require(
            request["task"] == "independent-review" and "transport_evidence" in response
        )
        note.require(
            note.sha(note.read_bytes(request_path)) == receipt["request_sha256"]
        )
        note.require(
            response["output"]["complete_source_read"] is True
            and response["output"]["complete_note_payload_read"] is True
        )
        note.require(
            receipt["hashes"]["fulltext"]
            == note.sha(
                note.read_bytes(
                    note.safe_path(run_dir) / "analysis/fulltext.txt", 64 * 1024 * 1024
                )
            )
        )
        note.require(
            receipt["hashes"]["scientific"]
            == note.sha(
                note.read_bytes(science_path(note.safe_path(run_dir), revision))
            )
        )
        note.require(response["output"]["hashes"] == receipt["hashes"])
        note.require(
            response["output"]["decision"] == "PASS"
            and response["output"]["material_findings"] == []
        )
        note.require(
            receipt["hashes"]["payload"] == approval["items"][0]["payload_sha256"]
        )
        note.require(
            receipt["hashes"]["note"] == approval["items"][0]["note_html_sha256"]
        )

    entry = approval["items"][0]
    run = note.safe_path(run_dir)
    paper = run.parent.parent
    from millefeuille.domain.clean_evidence import project
    from millefeuille.domain.clean_final import prepare as final_prepare
    from millefeuille.domain.clean_final import verify
    from millefeuille.domain.manifest_lineage import validate

    manifest, selected, lineage = validate(run)
    note.require(entry["manifest_lineage"] == lineage)
    note.require(entry["manifest_sha256"] == lineage["manifest_sha256"])
    note.require(entry["run_dir"] == str(run) and entry["run_id"] == run.name)
    note.require(entry["item_key"] == item_key == manifest["item_key"])
    note.require(
        str(manifest["library_id"]) == library_id and manifest["library_type"] == "user"
    )
    note.require(manifest["max_items"] == 1 and manifest["zotero_writeback"] == "none")
    original, attachment = selected["item"], selected["attachment"]
    note.item_identity(original, item_key, library_id)
    note.item_identity(attachment, entry["attachment_key"], library_id)
    note.require(entry["parent_version"] == original["version"])
    note.require(entry["attachment_version"] == attachment["version"])
    note.require(entry["parent_data_sha256"] == note.digest(original["data"]))
    note.require(entry["attachment_data_sha256"] == note.digest(attachment["data"]))
    note.require(entry["source_sha256"] == manifest["source"]["sha256"])
    required = {
        "00-selected.json",
        "03-ocr-completed.json",
        "analysis/scientific.json",
        "analysis/summary-request.json",
        "analysis/summary-response.json",
        "analysis/fulltext.txt",
    }
    ocr = note.load(run / "03-ocr-completed.json")
    for pin in ocr["files"].values():
        required.add(pin["ref"])
        note.require(entry["run_files"][pin["ref"]] == pin["sha256"])
    note.require(required <= set(entry["run_files"]))
    for ref, pin in entry["run_files"].items():
        from pathlib import Path

        relative = Path(ref)
        note.require(not relative.is_absolute() and ".." not in relative.parts)
        note.require(note.sha(note.read_bytes(run / relative, 64 * 1024 * 1024)) == pin)
    ocr_evidence = note.load(run / ocr["files"]["evidence_path"]["ref"])
    note.require(ocr_evidence["source_sha256"] == entry["source_sha256"])
    note.require(
        ocr_evidence["requested_model"] == "mistral-ocr-latest"
        and ocr_evidence["returned_model"]
    )
    evidence = project(run, revision=revision)
    note.require(evidence["paper_id"] == "zotero-" + item_key)
    note.require(entry["scientific_sha256"] == note.digest(evidence))
    # Missing/incomplete final is a hard refusal, never generated by writeback.
    final = verify(final_prepare(run, entry["taxonomy_store"], revision=revision))
    note.require(entry["final_sha256"] == note.digest(final))
    note.require(entry["final_lock_identity"] == final["final_lock_identity"])
    identity = "sha256:" + note.digest(
        {
            "operation": "clean-science-summary-note/v1",
            "library_id": library_id,
            "item_key": item_key,
            "run_id": run.name,
            "scientific_sha256": entry["scientific_sha256"],
            "final_sha256": entry["final_sha256"],
        }
    )
    note.require(entry["identity"] == identity)
    raw = note.read_bytes(entry["payload_path"])
    note.require(note.sha(raw) == entry["payload_sha256"])
    payload = note.parse(raw)
    note.require(isinstance(payload, list) and len(payload) == 1)
    value = payload[0]
    note.require(
        set(value)
        == {"itemType", "parentItem", "note", "tags", "collections", "relations"}
    )
    note.require(value["itemType"] == "note" and value["parentItem"] == item_key)
    note.require(value["tags"] == [{"tag": "millefeuille-summary"}])
    note.require(value["collections"] == [] and value["relations"] == {})
    note.require(note.sha(value["note"].encode()) == entry["note_html_sha256"])
    marker = "Millefeuille writeback identity: " + identity
    note.require(value["note"].count(marker) == 1)
    parser = note.SafeHTML(convert_charrefs=True)
    parser.feed(value["note"])
    parser.close()
    return {
        "run": run,
        "paper": paper,
        "entry": entry,
        "payload": payload,
        "original": original,
        "attachment": attachment,
        "marker": marker,
        "binding": note.digest(entry),
        "library": library_id,
        "key": item_key,
    }


def run_standalone_writeback_cli(argv=None):
    parser = argparse.ArgumentParser(prog="millefeuille standalone-writeback")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--item-key", required=True)
    parser.add_argument("--library-id", required=True)
    parser.add_argument("--approval-manifest", required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    try:
        with command_credential_scope(args, 'standalone-writeback'):
            try:
                context = prepare(
                    args.run_dir, args.item_key, args.library_id, args.approval_manifest
                )
                result = asyncio.run(execute(context, args.live))
            except CredentialError:
                raise
            except Exception as exc:
                result = {
                    "status": "refused",
                    "note_key": None,
                    "error_class": type(exc).__name__,
                }
            print(json.dumps(result, sort_keys=True))
            return (
                0
                if result["status"]
                in {"planned-read-only", "written-verified", "already-written-verified"}
                else 2
            )
    except CredentialError as exc:
        print(exc.code, file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run_standalone_writeback_cli())
