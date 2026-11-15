"""Finite actual synthetic lifecycle and full workload inventory.

Run in a fresh interpreter, without importing any test fixture or clearing
sys.modules. Trusted admission sizes resources to the complete code/input workload.
An inventory alone is never positive lifecycle acceptance.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import stat
import sys
import time
import tracemalloc

ROOT = Path(__file__).absolute().parents[1]
ROOTS = (
    "millefeuille.domain.semantic_review_orchestrator",
    "millefeuille.domain.semantic_publication_orchestrator",
    "millefeuille.domain.classification_output_plan",
    "millefeuille.domain.release_preflight",
)
WORKER_SECONDS = float(os.environ.get("MF_WORKER_SECONDS", "180"))
if not math.isfinite(WORKER_SECONDS) or not 0 < WORKER_SECONDS <= 300:
    raise ValueError("invalid finite worker deadline")
WORKER_STARTED = time.monotonic()
WORKER_DEADLINE = WORKER_STARTED + WORKER_SECONDS

MAX_SOURCE_BYTES = 16777216
MAX_ENVIRONMENT_BYTES = 67108864


def identity(value):
    return [
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    ]


def capture(path, allowance):
    path = Path(path).absolute()
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > allowance:
        raise ValueError("closure nonregular/size bound: " + str(path))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if identity(os.fstat(fd)) != identity(before):
            raise ValueError("closure opened identity drift")
        data = bytearray()
        while len(data) <= before.st_size:
            chunk = os.read(fd, min(65536, before.st_size + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) != before.st_size or identity(path.lstat()) != identity(before):
            raise ValueError("closure source changed while captured")
    finally:
        os.close(fd)
    return bytes(data), {
        "path": str(path),
        "identity": identity(before),
        "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
    }


def module_path(module):
    candidate = ROOT.joinpath(*module.split("."))
    if candidate.with_suffix(".py").is_file():
        return candidate.with_suffix(".py")
    if (candidate / "__init__.py").is_file():
        return candidate / "__init__.py"
    return None


def static_closure():
    pending = list(ROOTS)
    records, edges, external = {}, [], set()
    total = 0
    while pending:
        module = pending.pop()
        if module in records:
            continue
        path = module_path(module)
        if path is None:
            raise ValueError("unresolved first-party import: " + module)
        if (
            time.monotonic() >= WORKER_DEADLINE
            or len(records) * 4 > 32768
            or sum(len(x) for x in pending) + len(edges) * 256 > 2097152
        ):
            raise ValueError("discovery open/metadata/time reservation exhausted")
        raw, record = capture(path, min(2097152, MAX_SOURCE_BYTES - total))
        total += len(raw)
        record["module"] = module
        records[module] = record
        package = module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
        for i in range(1, len(module.split("."))):
            pending.append(".".join(module.split(".")[:i]))
        for node in ast.walk(ast.parse(raw, filename=str(path))):
            imports = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                target = (
                    importlib.util.resolve_name(
                        "." * node.level + (node.module or ""), package
                    )
                    if node.level
                    else node.module or ""
                )
                imports = [target]
                imports += [
                    target + "." + alias.name
                    for alias in node.names
                    if module_path(target + "." + alias.name) is not None
                ]
            elif isinstance(node, ast.Call) and (
                getattr(node.func, "id", "") == "__import__"
                or getattr(node.func, "attr", "") == "import_module"
            ):
                if (
                    not node.args
                    or not isinstance(node.args[0], ast.Constant)
                    or not isinstance(node.args[0].value, str)
                ):
                    raise ValueError("unresolved dynamic import: " + module)
                name = node.args[0].value
                imports = [
                    importlib.util.resolve_name(name, package)
                    if name.startswith(".")
                    else name
                ]
            for target in imports:
                if target == "millefeuille" or target.startswith("millefeuille."):
                    pending.append(target)
                    edges.append({"from": module, "to": target, "line": node.lineno})
                elif target:
                    external.add(target.split(".")[0])
    _, config_record = capture(ROOT / "pyproject.toml", 2097152)
    config_record["module"] = "configuration:pyproject"
    controller_raw, controller = capture(Path(__file__).absolute(), 2097152)
    if total + len(controller_raw) > MAX_SOURCE_BYTES:
        raise ValueError("measurement cumulative source bytes")
    controller["module"] = "controller:millefeuille_offline_closure"
    code = sorted(
        [*records.values(), controller, config_record], key=lambda row: row["path"]
    )
    return (
        code,
        sorted(edges, key=lambda row: (row["from"], row["line"], row["to"])),
        sorted(external),
    )


def runtime_lock(external_roots):
    executable, executable_record = capture(Path(sys.executable).resolve(), 16777216)
    del executable
    mapping = importlib.metadata.packages_distributions()
    distributions = []
    for name in sorted(
        {dist for root in external_roots for dist in mapping.get(root, [])}
    ):
        dist = importlib.metadata.distribution(name)
        files = list(dist.files or [])
        if not files:
            raise ValueError("dependency file records unavailable: " + name)
        pins = [
            entry
            for entry in files
            if str(entry).endswith((".dist-info/RECORD", ".dist-info/METADATA"))
        ]
        if len(pins) < 2:
            raise ValueError("dependency metadata/RECORD unavailable: " + name)
        records = []
        for entry in pins:
            _, record = capture(Path(dist.locate_file(entry)).absolute(), 2097152)
            records.append(record)
        distributions.append(
            {"name": name, "version": dist.version, "metadata": records}
        )
    loaded = []
    total = 0
    paths = set()
    for module in tuple(sys.modules.values()):
        value = getattr(module, "__file__", None)
        if value and Path(value).is_absolute():
            path = Path(value)
            if path.suffix == ".pyc" and path.with_suffix(".py").is_file():
                path = path.with_suffix(".py")
            if str(path) in paths or path.is_relative_to(ROOT):
                continue
            paths.add(str(path))
            reported = path
            reported_identity = identity(reported.lstat())
            path = reported.resolve(strict=True)
            _, record = capture(path, min(MAX_ENVIRONMENT_BYTES - total, 16777216))
            if identity(reported.lstat()) != reported_identity:
                raise ValueError("loaded dependency path changed")
            record["reported_path"] = str(reported)
            record["reported_path_identity"] = reported_identity
            total += record["identity"][5]
            loaded.append(record)
    return {
        "python": sys.version,
        "implementation": sys.implementation.name,
        "executable": executable_record,
        "distributions": distributions,
        "loaded_external_files": sorted(loaded, key=lambda row: row["path"]),
        "limits": {"loaded_external_total_bytes": MAX_ENVIRONMENT_BYTES},
        "authenticity": (
            "local byte/identity pin only; not distribution signature authentication"
        ),
    }


def prepare_fixture(root):
    """Separate preparation command, never imported by a measured worker."""
    sys.path.insert(0, str(ROOT))
    from tests.fixtures.offline_core_source_fixture import plan_inputs, prepare
    from tests.test_persistence_guards import locked_evidence

    root.mkdir(mode=0o700)
    for scenario in [
        "success-readback",
        "fault-reconciliation",
        "rollback",
        "classification",
    ]:
        target = root / scenario
        target.mkdir(mode=0o700)
        case = prepare(target)
        args = plan_inputs(case)
        args.pop("resolved_materials")
        args["summary"] = args["summary"].to_dict()
        resolved = case["resolved"]
        for ref in ["reports", "classification/decision-records"]:
            (resolved.run_dir / ref).mkdir(parents=True, exist_ok=True)
        evidence = {
            **locked_evidence(resolved.run_id),
            "schema_version": "millefeuille-classification-fixture-evidence/v0.1",
            "confidence": "high",
            "evidence_refs": ["cards/paper-card.json"],
        }
        (target / "classification-evidence.json").write_text(json.dumps(evidence))
        (target / "case.json").write_text(
            json.dumps(
                {
                    "args": args,
                    "resolve": {
                        "source_pack_root": str(resolved.source_pack_dir.parent.parent),
                        "run_id": resolved.run_id,
                        "paper_id": resolved.paper_id,
                    },
                },
                default=lambda value: (
                    {"$fixture_bytes_hex": value.hex()}
                    if isinstance(value, bytes)
                    else (_ for _ in ()).throw(TypeError(type(value).__name__))
                ),
            )
        )
        for value in [target, *target.rglob("*")]:
            value.chmod(0o700 if value.is_dir() else 0o600)
    print(
        json.dumps(
            {
                "status": "FIXTURE_PREPARATION_PASS",
                "root": str(root),
                "branches": 4,
                "provider_calls": 0,
                "private_reads": 0,
            }
        )
    )


def reservation(paths):
    sizes = [p.lstat().st_size for p in paths]
    meta = sum(len(str(p).encode()) + 512 for p in paths)
    return {
        "read_bytes": min(16777216, max(1, sum(sizes) * 2)),
        "opens": max(1, len(paths) * 2),
        "metadata_bytes": max(1, meta * 2),
        "memory_bytes": max(1048576, max(sizes, default=0) * 6 + meta * 12),
        "seconds": max(1, int(min(180, WORKER_DEADLINE - time.monotonic()))),
    }


def lifecycle(scenario, fixture_root):
    started = time.monotonic()
    if any(n == "tests" or n.startswith("tests.") for n in sys.modules):
        raise ValueError("worker contaminated by test imports")
    sys.pycache_prefix = str(fixture_root / "unused-pycache")
    sys.path.insert(0, str(ROOT))
    code, edges, external = static_closure()
    tracemalloc.start()
    for module in ROOTS:
        importlib.import_module(module)
    from millefeuille.domain.acceptance_output_plan import json_bytes
    from millefeuille.domain.exclusive_publication import CONTRACT
    from millefeuille.domain.millefeuille import AcceptanceSummaryRecord
    from millefeuille.domain.semantic_acceptance import digest
    from millefeuille.domain.semantic_publication_orchestrator import (
        capture_bound_manifest,
        namespace_binding,
        plan_identity,
        prepare_workload_admission,
        publish_reviewed_plan,
        rollback_binding,
        rollback_reviewed_plan,
        verify_bound_manifest,
        verify_publication_readback,
    )
    from millefeuille.domain.semantic_review_orchestrator import (
        plan_reviewed_acceptance,
    )
    from millefeuille.domain.stage_runtime import resolve_run_artifacts

    branch = fixture_root / scenario
    if not branch.is_dir() or branch.is_symlink():
        raise ValueError("owned prepared branch required")
    reads = set()
    audit_events = 0
    opening = []
    audit_enabled = [True]
    real_open = os.open

    def measured_open(path, flags, mode=0o777, *, dir_fd=None):
        if isinstance(path, (str, bytes)):
            value = Path(os.fsdecode(path))
            base = (
                Path(os.readlink("/proc/self/fd/" + str(dir_fd)))
                if dir_fd is not None
                else Path.cwd()
            )
            opening.append(str(value if value.is_absolute() else base / value))
        else:
            opening.append(None)
        try:
            return real_open(path, flags, mode, dir_fd=dir_fd)
        finally:
            opening.pop()

    os.open = measured_open

    def audit(event, args):
        nonlocal audit_events
        if not audit_enabled[0]:
            return
        if event in {
            "socket.connect",
            "socket.getaddrinfo",
            "subprocess.Popen",
            "os.system",
        }:
            raise ValueError("network/process effects forbidden in synthetic worker")
        if event != "open" or not isinstance(args[0], (str, bytes)):
            return
        name = Path(
            opening[-1] if opening and opening[-1] else os.fsdecode(args[0])
        ).absolute()
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        if name.is_relative_to(branch) or name.is_relative_to(ROOT):
            audit_events += 1
            if audit_events > 32768 or sum(len(x) + 64 for x in reads) > 2097152:
                raise ValueError("actual I/O audit reservation exhausted")
            if not flags & (
                os.O_WRONLY | os.O_RDWR | os.O_DIRECTORY | getattr(os, "O_PATH", 0)
            ):
                reads.add(str(name))

    sys.addaudithook(audit)
    case = json.loads(
        (branch / "case.json").read_bytes(),
        object_hook=lambda value: (
            bytes.fromhex(value["$fixture_bytes_hex"])
            if set(value) == {"$fixture_bytes_hex"}
            else value
        ),
    )
    resolved = resolve_run_artifacts(**case["resolve"])
    args = case["args"]
    args["summary"] = AcceptanceSummaryRecord.from_dict(args["summary"])
    # Same explicit reviewed source-budget shape as the canonical fixture's
    # accepted positive test, rather than the wrapper's small default1024opens.
    # Preparation was a separate charged invocation; this is a fresh consumer
    # scope, not a claim to transfer the producer's unavailable runtime ledger.
    from millefeuille.domain.evidence_source_adapter import ReadBudget

    source_budget = ReadBudget(
        max_opens=16384,
        max_scans=16384,
        max_seconds=max(0.001, WORKER_DEADLINE - time.monotonic()),
    )
    proof = plan_reviewed_acceptance(
        resolved_materials=resolved, budget=source_budget, **args
    )
    plan = proof["exact_plan"]
    if len(plan) != 6 or not all(x["passed"] for x in proof["evaluation"]["scenarios"]):
        raise ValueError("actual eight-scenario/six-output positive required")
    run = resolved.run_dir
    targets = {run / x.ref for x in plan}
    input_paths = []
    for directory, dirs, names in os.walk(branch, followlinks=False):
        if any((Path(directory) / d).is_symlink() for d in dirs):
            raise ValueError("fixture directory symlink")
        for name in sorted(names):
            if time.monotonic() >= WORKER_DEADLINE:
                raise ValueError("fixture discovery deadline")
            path = Path(directory) / name
            if path not in targets:
                input_paths.append(path)
            if len(input_paths) * 4 > 32768:
                raise ValueError("fixture discovery open reservation exhausted")
    input_paths.sort()
    code_paths = [Path(row["path"]) for row in code]
    ia = prepare_workload_admission(
        input_paths,
        roots=[branch],
        context="synthetic-inputs:" + scenario,
        reservations=reservation(input_paths),
    )
    ca = prepare_workload_admission(
        code_paths,
        roots=[ROOT],
        context="pinned-first-party-and-controller",
        reservations=reservation(code_paths),
    )
    if len(ca.entries) <= 64:
        raise ValueError("must exercise legitimate workload above legacy64")
    ib = capture_bound_manifest(input_paths, admission=ia)
    cb = capture_bound_manifest(code_paths, admission=ca)
    before = {
        x.ref: (run / x.ref).read_bytes() if (run / x.ref).exists() else None
        for x in plan
    }
    checkpoints = [
        "source_review",
        "eight_scenarios",
        "six_output_plan",
        "workload_admitted",
    ]
    if (
        sum(r["identity"][5] for r in code) + sum(r["identity"][5] for r in ib["files"])
        > 16777216
    ):
        raise ValueError("combined code/input resource reservation exhausted")
    scopes = []

    def publish(outputs, journal, fault=None):
        if tracemalloc.get_traced_memory()[1] > MAX_SOURCE_BYTES * 16:
            raise ValueError(
                "measured worker memory reservation exhausted before effect"
            )
        refs = [x.ref for x in outputs]
        scope = {
            "schema": "millefeuille-reviewed-publication-scope/v2",
            "approved": True,
            "approval_identity": "synthetic-local-owner",
            "uses": 1,
            "root": str(run),
            "journal": journal,
            "plan_identity": plan_identity(outputs),
            "approved_refs": refs,
            "namespace_binding": namespace_binding(run, refs),
            "namespace_contract": CONTRACT,
            "input_binding": ib,
            "code_binding": cb,
            "input_admission": ia.binding(),
            "code_admission": ca.binding(),
            "limits": {
                "max_files": 16,
                "max_bytes": 1048576,
                "max_aux_paths": 34,
                "max_aux_bytes": 2097152,
                "max_journal_bytes": 524288,
            },
        }
        scopes.append(scope)
        receipt = publish_reviewed_plan(
            exact_plan=outputs,
            approved_scope=scope,
            namespace=run,
            journal=journal,
            input_binding=ib,
            code_binding=cb,
            input_admission=ia,
            code_admission=ca,
            approved_scope_identity=digest(json_bytes(scope)),
            fault=fault,
        )
        readback = verify_publication_readback(
            receipt=receipt, approved_scope=scope, input_admission=ia, code_admission=ca
        )
        if readback["status"] != "pass":
            raise ValueError("actual publication readback failed")
        return scope

    if scenario == "fault-reconciliation":

        def fault(label):
            if label == "after:0":
                raise RuntimeError("synthetic-postimage-recorded-fault")

        try:
            publish(plan, "acceptance-transaction", fault)
        except RuntimeError as exc:
            if str(exc) != "synthetic-postimage-recorded-fault":
                raise
        else:
            raise ValueError("fault hook not reached")
        checkpoints.append("fault_after_recorded_postimage")
    else:
        publish(plan, "acceptance-transaction")
        checkpoints += ["six_outputs_published", "six_outputs_readback"]
    if scenario in ("fault-reconciliation", "rollback"):
        scope = scopes[0]
        approval = {
            "operation": "rollback",
            "approval_identity": "synthetic-local-owner",
            "binding": rollback_binding(scope),
        }
        rollback_reviewed_plan(
            approved_scope=scope,
            rollback_approval=approval,
            approved_rollback_identity=digest(json_bytes(approval)),
            input_admission=ia,
            code_admission=ca,
        )
        for ref, expected in before.items():
            path = run / ref
            if (path.read_bytes() if path.exists() else None) != expected:
                raise ValueError("rollback preimage content reconciliation failed")
        checkpoints += [
            "separate_rollback_approval",
            "rollback_executed",
            "preimage_readback",
        ]
    if scenario == "classification":
        import tomllib

        from millefeuille.domain.classification_output_plan import (
            plan_classification_from_evidence,
        )
        from millefeuille.domain.release_preflight import (
            no_writeback_binding,
            write_release_candidate_preflight,
        )

        outputs = plan_classification_from_evidence(
            evidence_path=branch / "classification-evidence.json", **case["resolve"]
        )
        publish(outputs, "classification-transaction")
        current = resolve_run_artifacts(**case["resolve"])
        passed = {
            s.name.value
            for s in current.stage_manifest.stages
            if s.status.value == "passed"
        }
        if not {"acceptance", "classify"}.issubset(passed):
            raise ValueError(
                "semantic acceptance/classification lifecycle did not pass"
            )
        version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
            "version"
        ]
        disposition = {
            "schema": "millefeuille-writeback-disposition/v2",
            "enabled": False,
            "disposition": "explicit_approved_no_writeback",
            "remote_operations": 0,
            "release_preflight_consumer_result": "pass",
            "approval_identity": "synthetic-local-owner",
            "code_manifest": cb,
            "input_manifest": ib,
            "code_admission": ca.binding(),
            "input_admission": ia.binding(),
            "binding": no_writeback_binding(
                resolved=current, current_version=version, candidate_version=None
            ),
        }
        result = write_release_candidate_preflight(
            **case["resolve"],
            repo_root=ROOT,
            writeback_disposition=disposition,
            approved_disposition_identity=digest(json_bytes(disposition)),
            input_admission=ia,
            code_admission=ca,
        )
        if result.readiness != "ready-for-rc-review":
            raise ValueError("actual no-writeback release-preflight consumer failed")
        checkpoints += [
            "accepted_before_classification",
            "released_taxonomy_classification",
            "classification_readback",
            "explicit_no_writeback",
            "release_preflight_consumed",
        ]
    verify_bound_manifest(ib, admission=ia)
    verify_bound_manifest(cb, admission=ca)
    observed = sorted(
        {
            str(Path(m.__file__).absolute())
            for n, m in tuple(sys.modules.items())
            if (n == "millefeuille" or n.startswith("millefeuille."))
            and getattr(m, "__file__", None)
        }
    )
    if set(observed) - {str(p) for p in code_paths}:
        raise ValueError(
            "unexpected imported first-party code outside complete static closure"
        )
    known = {str(p) for p in code_paths + input_paths} | {str(p) for p in targets}
    known |= {str(run / ref) for scope in scopes for ref in scope["approved_refs"]}
    known |= {
        str(run / "reports/release-candidate-preflight.json"),
        str(run / "reports/release-candidate-preflight.md"),
    }
    unexpected = [
        p
        for p in reads
        if p not in known
        and not any(
            Path(p).is_relative_to(run / name)
            for name in ["acceptance-transaction", "classification-transaction"]
        )
    ]
    if unexpected:
        raise ValueError("unaccounted actual input/code reads: " + repr(unexpected))
    if any(n == "tests" or n.startswith("tests.") for n in sys.modules):
        raise ValueError("worker imported tests")
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    lock = runtime_lock(external)
    duration = time.monotonic() - started
    if time.monotonic() >= WORKER_DEADLINE:
        raise ValueError("clean lifecycle worker deadline")
    record = {
        "schema": "millefeuille-offline-lifecycle-proof/v2",
        "status": "PASS",
        "scenario": scenario,
        "checkpoints": checkpoints,
        "static_code_files": code,
        "static_import_edges": edges,
        "runtime_lock": lock,
        "input_manifest": ib,
        "code_admission": ca.binding(),
        "input_admission": ia.binding(),
        "transaction_scopes": [
            {
                "sha256": digest(json_bytes(x)),
                "journal": x["journal"],
                "approved_refs": x["approved_refs"],
                "namespace_preimage": x["namespace_binding"],
            }
            for x in scopes
        ],
        "observed_first_party_paths": observed,
        "actual_scoped_reads": sorted(reads),
        "audit_open_events": audit_events,
        "admitted_worker_seconds": WORKER_SECONDS,
        "consumer_source_budget": {
            "read_bytes": source_budget.read_bytes,
            "opens": source_budget.opens,
            "scans": source_budget.scans,
            "max_read_bytes": source_budget.max_read_bytes,
            "max_opens": source_budget.max_opens,
            "max_scans": source_budget.max_scans,
            "preparation_accounting": "separate invocation; no counter transfer",
        },
        "code_count": len(code),
        "input_count": len(input_paths),
        "observed_modules": len(observed),
        "code_bytes": sum(r["identity"][5] for r in code),
        "input_bytes": sum(r["identity"][5] for r in ib["files"]),
        "observed_tracemalloc_peak_bytes": peak,
        "worker_memory_reservation_bytes": MAX_SOURCE_BYTES * 16,
        "duration_seconds": duration,
        "tests_imported": False,
        "dependencies_omitted": False,
        "provider_calls": 0,
        "publication_attempts": len(scopes),
        "lifecycle_positive_verified": True,
        "qualification": (
            "owned synthetic only; hashes not authenticity/scientific validity"
        ),
    }
    record["proof_sha256"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    print(json.dumps(record, sort_keys=True, separators=(",", ":")))
    print(
        json.dumps(
            {
                k: record[k]
                for k in [
                    "status",
                    "scenario",
                    "checkpoints",
                    "code_count",
                    "input_count",
                    "observed_modules",
                    "code_bytes",
                    "input_bytes",
                    "duration_seconds",
                    "proof_sha256",
                ]
            }
        )
    )
    audit_enabled[0] = False
    os.open = real_open
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", type=Path)
    parser.add_argument("--fixture-root", type=Path)
    parser.add_argument(
        "--scenario",
        choices=[
            "success-readback",
            "fault-reconciliation",
            "rollback",
            "classification",
        ],
    )
    parser.add_argument("--classification-suite", action="store_true")
    parser.add_argument("--preparation-suite", action="store_true")
    args = parser.parse_args()
    if args.prepare:
        prepare_fixture(args.prepare)
        if args.preparation_suite:
            import unittest

            suite = unittest.defaultTestLoader.loadTestsFromName(
                "tests.test_semantic_publication_orchestrator"
            )
            result = unittest.TextTestRunner(verbosity=2).run(suite)
            raise SystemExit(0 if result.wasSuccessful() else 1)
    else:
        if not args.scenario or not args.fixture_root:
            parser.error("prepared fixture and scenario required")
        code = lifecycle(args.scenario, args.fixture_root)
        if args.classification_suite:
            import unittest

            names = [
                "tests.test_semantic_publication_orchestrator",
                "tests.test_persistence_guards",
                "tests.test_millefeuille_classification_actions",
                "tests.test_millefeuille_taxonomy_registry",
                "tests.test_millefeuille_exclusive_publication",
                "tests.test_millefeuille_publication_recovery",
                "tests.test_millefeuille_publication_executor",
            ]
            result = unittest.TextTestRunner(verbosity=2).run(
                unittest.defaultTestLoader.loadTestsFromNames(names)
            )
            code = 0 if result.wasSuccessful() else 1
        raise SystemExit(code)
