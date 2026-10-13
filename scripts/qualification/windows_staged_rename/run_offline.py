"""Bounded offline test/evidence runner, confined to this unpublished prototype."""

import hashlib
import io
import json
import platform
import resource
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DENIED_EVENTS = []


def audit(event, _args):
    if event.startswith(("socket.", "subprocess.")) or event in {
        "os.system",
        "os.posix_spawn",
        "os.spawn",
        "os.exec",
    }:
        DENIED_EVENTS.append(event)
        raise RuntimeError("offline prototype forbids network/process dispatch")


def file_record(path):
    data = path.read_bytes()
    return {
        "path": str(path),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def requests():
    from native_contract import OPERATION_KINDS, NativeMoveRequest, NativeObjectBinding
    from staged_replacement import PENDING

    leaves = {
        "old-to-journal": ("stage-manifest.json", "old-0"),
        "stage-to-live": ("next-0", "stage-manifest.json"),
        "rollback-new-to-journal": ("stage-manifest.json", "failed-0"),
        "rollback-old-to-live": ("old-0", "stage-manifest.json"),
        "barrier-to-committed-journal": (PENDING, "committed-barrier"),
        "barrier-to-rolled-back-journal": (PENDING, "rolled-back-barrier"),
        "barrier-to-aborted-history": (PENDING, "a" * 64 + "-aborted-barrier"),
        "barrier-restore-to-run": ("committed-barrier", PENDING),
    }
    result = []
    for operation, kind in OPERATION_KINDS.items():
        for api in ("NtSetInformationFile:10", "SDK:SetFileInformationByHandle:3"):
            binding = NativeObjectBinding(
                123,
                "1" * 32,
                kind,
                1,
                3 if kind == "regular-file" else 0,
                hashlib.sha256(b"old").hexdigest() if kind == "regular-file" else None,
            )
            request = NativeMoveRequest(
                operation,
                api,
                0x1111,
                0x2222,
                0x3333,
                binding,
                "2" * 32,
                "3" * 32,
                *leaves[operation],
                run_root_handle_token=0x4444,
                run_file_id_128="4" * 32,
            )
            packet = request.packet()
            encoded = request.candidate_amd64_buffer()
            packet["candidate_buffer"] = {
                "bytes": len(encoded),
                "hex": encoded.hex(),
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "layout_qualified": False,
            }
            result.append(packet)
    return result


def main():
    sys.dont_write_bytecode = True
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    sys.addaudithook(audit)
    import test_staged_replacement as tests
    from staged_replacement import (
        MAX_EVIDENCE_BYTES,
        MAX_FILE_BYTES,
        MAX_GROUP_BYTES,
        MAX_HISTORY_JOURNALS,
        MAX_OUTPUTS,
        MAX_PLAN_BYTES,
    )

    sources = [
        ROOT / name
        for name in (
            "staged_replacement.py",
            "native_contract.py",
            "test_staged_replacement.py",
        )
    ]
    before = [file_record(path) for path in sources]
    output = io.StringIO()
    started = time.monotonic()
    suite = unittest.defaultTestLoader.loadTestsFromModule(tests)
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    elapsed = time.monotonic() - started
    after = [file_record(path) for path in sources]
    packet_path = ROOT / "native_request_contract.json"
    packet_path.write_bytes(
        (
            json.dumps(
                {
                    "schema": "millefeuille.staged-Windows-native-request-preparation/v1",
                    "sample_only_no_live_handles": True,
                    "native_execution_authorized": False,
                    "native_adapter_qualified": False,
                    "production_windows_writes_enabled": False,
                    "requests": requests(),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    )
    log_path = ROOT / "offline-tests.log"
    log_path.write_bytes(output.getvalue().encode("utf-8"))
    memory_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform != "darwin":
        memory_bytes *= 1024
    evidence = {
        "schema": "millefeuille.staged-Windows-synthetic-prototype-evidence/v1",
        "status": "passed"
        if result.wasSuccessful()
        and not result.skipped
        and not DENIED_EVENTS
        and before == after
        and elapsed < 15
        and memory_bytes < 134217728
        else "failed",
        "runtime": {
            "platform": sys.platform,
            "python": sys.version,
            "machine": platform.machine(),
            "native_Windows": False,
        },
        "tests": {
            "tests_run": result.testsRun,
            "failures": len(result.failures),
            "errors": len(result.errors),
            "skips": len(result.skipped),
            "forward_fault_cases": len(tests.FORWARD_FAULT_CASES),
            "rollback_fault_cases": len(tests.ROLLBACK_FAULT_CASES),
            "forward_fault_points": tests.FORWARD_FAULT_CASES,
            "rollback_fault_points": tests.ROLLBACK_FAULT_CASES,
            "elapsed_seconds": round(elapsed, 6),
        },
        "bounds": {
            "CPU_seconds_hard_limit": 20,
            "test_wall_seconds_asserted": 15,
            "peak_RSS_bytes": memory_bytes,
            "peak_RSS_acceptance_bytes": 134217728,
            "per_model_evidence_bytes": MAX_EVIDENCE_BYTES,
            "output_count": MAX_OUTPUTS,
            "file_bytes": MAX_FILE_BYTES,
            "group_new_bytes": MAX_GROUP_BYTES,
            "plan_bytes": MAX_PLAN_BYTES,
            "history_journals": MAX_HISTORY_JOURNALS,
        },
        "network_or_process_events_attempted": DENIED_EVENTS,
        "sources_unchanged_during_tests": before == after,
        "source_records": after,
        "test_log": file_record(log_path),
        "native_request_packet": file_record(packet_path),
        "retained_references": [
            file_record(path) for path in sorted((ROOT / "references").glob("*"))
        ],
        "read_only_context": {
            "repository": "fr-meyer/millefeuille",
            "local_safe_split_path": str(
                ROOT.parent / "pr175-portable-linux-split-candidate"
            ),
            "local_head_observed_before_work": "dd6f0124a6f05e1a8e5319834375dba0785a074b",
            "local_tree_observed_before_work": "596afdc5a500c963a8d9fdf73027a9f693d1b459",
            "remote_state_not_rechecked": True,
        },
        "model_contract": {
            "trusted_cooperating_owner": True,
            "hostile_same_owner_namespace_confinement": False,
            "old_live_file_bytes_never_written": True,
            "meaningful_group_commit_installs_new_object_identities": True,
            "already_open_old_handles_retain_old_generation": True,
            "raw_readers_may_observe_missing_or_mixed_group_members": True,
            "ASCII_case_insensitive_namespace_only": True,
            "directory_and_input_identity_bindings_preserved": True,
            "pending_and_nonterminal_history_block_cooperating_readers_and_restart": True,
            "journal_terminal_acknowledgement_is_abstract_not_native_durable_IO": True,
        },
        "native_qualification_gaps": [
            "Positive native file and empty-directory no-replace rename baselines, actual SDK layout, granted DELETE access and parent/source sharing remain unproved.",
            "Neither native relative-root API is selected or qualified; class10/SDK class3 requests are prepared only, and STATUS_PENDING/late failure must remain unresolved until identity reconciliation.",
            "File ID, link/reparse, case-insensitive collision, held-name and input/root/parent drift behavior require native testing.",
            "FlushFileBuffers/directory flush, durable plan/terminal acknowledgement and restart/crash recovery are not modeled as established native guarantees.",
            "The additional unfinished-history reader/writer admission must be implemented with bounded durable records before integration; this model's state fields are synthetic.",
            "No host-privilege, ACL, private namespace, outside-link exclusion or same-owner hostile concurrency guarantee is provided.",
            "Production integration, full same-head Windows tests, resource controller/native accounting and authorized CI qualification remain undone.",
        ],
        "preservation": {
            "existing_PR175_PR176_candidates_receipts_branches_edited": False,
            "network_calls": 0,
            "installs": 0,
            "GCP_calls": 0,
            "Windows_desktop_actions": 0,
            "paid_model_or_broker_calls": 0,
            "private_input_reads": 0,
            "private_outputs": 0,
            "pushes_PR_updates_CI_runs": 0,
            "production_windows_writes_enabled": False,
            "private_ledger_consumed_unchanged": 255111459,
            "private_ledger_limit_unchanged": 310378496,
        },
    }
    evidence_path = ROOT / "offline-evidence.json"
    evidence_path.write_bytes(
        (json.dumps(evidence, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )
    print(
        json.dumps(
            {
                "status": evidence["status"],
                "tests": result.testsRun,
                "forward_fault_cases": len(tests.FORWARD_FAULT_CASES),
                "rollback_fault_cases": len(tests.ROLLBACK_FAULT_CASES),
                "seconds": round(elapsed, 6),
                "evidence": str(evidence_path),
            },
            sort_keys=True,
        )
    )
    return 0 if evidence["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
