"""Bounded offline evidence processor. Trusted manifest is NOT derived from logs."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.fixtures.evidence_transport import Reassembler, canonical  # noqa: E402
from tests.fixtures.f05_receipts import METHOD_SEAMS, SOURCE_PATHS, accept  # noqa: E402

MAX_TOTAL = 128 * 1024 * 1024
MAX_MEMBER = 24 * 1024 * 1024


def manifest_check(m):
    if m["schema"] != "millefeuille-evidence-manifest/v1":
        raise ValueError("manifest schema")
    if set(m["source_sha256"]) != set(SOURCE_PATHS):
        raise ValueError("manifest source set")
    if len(m["cells"]) != 6 or len({c["job"] for c in m["cells"]}) != 6:
        raise ValueError("six distinct cells required")
    for key in ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        if not isinstance(m[key], str) or not m[key]:
            raise ValueError("missing trusted identity")
    for digest in [
        m["worker_sha256"],
        *m["source_sha256"].values(),
        *m.get("s04_source_sha256", {}).values(),
    ]:
        if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
            raise ValueError("manifest digest")
    if not re.fullmatch("[0-9a-f]{40}", m["GITHUB_SHA"]):
        raise ValueError("trusted head format")
    if not m["GITHUB_RUN_ID"].isdigit() or not m["GITHUB_RUN_ATTEMPT"].isdigit():
        raise ValueError("trusted run format")
    for c in m["cells"]:
        job = c["job"]
        if PurePosixPath(job).name != job or job in ("", ".", "..") or "\\" in job:
            raise ValueError("unsafe job")
        r = c["runtime"]
        if set(r) != {
            "GITHUB_SHA",
            "GITHUB_RUN_ID",
            "GITHUB_RUN_ATTEMPT",
            "RUNNER_OS",
            "python",
        }:
            raise ValueError("runtime schema")
        if any(not isinstance(v, str) or not v for v in r.values()):
            raise ValueError("runtime incomplete")
        if any(
            r[k] != m[k] for k in ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")
        ):
            raise ValueError("runtime external binding")
        if set(c["methods"]) != set(METHOD_SEAMS) or len(c["methods"]) != 2:
            raise ValueError("two exact methods")
        if r["RUNNER_OS"] not in ("Linux", "Windows", "macOS") or not re.fullmatch(
            r"3[.][0-9]+", r["python"]
        ):
            raise ValueError("runtime cell format")
        cases = c.get("s04_case_ids", [])
        if cases and (
            r["RUNNER_OS"] != "Linux" or len(cases) != 5 or len(set(cases)) != 5
        ):
            raise ValueError("five S04 cases on Linux only")

    pairs = [(c["runtime"]["RUNNER_OS"], c["runtime"]["python"]) for c in m["cells"]]
    if set(pairs) != {
        (os_name, version)
        for os_name in ("Linux", "Windows")
        for version in ("3.11", "3.12", "3.13")
    }:
        raise ValueError("exact six-cell matrix required")


def validate_archive(logzip, manifest):
    manifest_check(manifest)
    if Path(logzip).stat().st_size > MAX_TOTAL:
        raise ValueError("compressed archive bound")
    cells = {c["job"]: c for c in manifest["cells"]}
    found, members = {}, set()
    started = time.monotonic()
    with zipfile.ZipFile(logzip) as archive:
        infos = archive.infolist()
        if len(infos) > 1024 or sum(i.file_size for i in infos) > MAX_TOTAL:
            raise ValueError("archive aggregate bound")
        names = [i.filename for i in infos]
        if len(names) != len(set(names)):
            raise ValueError("duplicate ZIP members")
        for info in infos:
            if info.file_size > MAX_MEMBER or info.flag_bits & 1:
                raise ValueError("archive member bound/encryption")
            match = re.fullmatch(r"([^/]+)/[0-9]+_Run unit tests[.]txt", info.filename)
            if not match:
                continue  # job-level copies are never parsed
            job = match[1]
            if job not in cells or job in members:
                raise ValueError("unexpected/ambiguous canonical step")
            members.add(job)
            cell = cells[job]
            assembler = Reassembler()
            used = 0
            with archive.open(info) as stream:
                while True:
                    line = stream.readline(65537)
                    if not line:
                        break
                    used += len(line)
                    if (
                        len(line) > 65536
                        or used > MAX_MEMBER
                        or time.monotonic() - started > 60
                    ):
                        raise ValueError("log byte/line/time bound")
                    item = assembler.feed(line)
                    if item is None:
                        continue
                    kind, payload, receipt = item
                    if receipt.get("runtime") != cell["runtime"]:
                        raise ValueError("external runtime binding")
                    if kind == "F05":
                        identifier = receipt["test"]
                        accept(
                            payload,
                            source_sha256=manifest["source_sha256"],
                            runtime=cell["runtime"],
                            method=identifier,
                            worker_sha256=manifest["worker_sha256"],
                        )
                    else:
                        identifier = receipt["case_id"]
                        if identifier not in cell.get("s04_case_ids", []):
                            raise ValueError("unexpected S04 case")
                        expected = manifest["s04_source_sha256"]
                        if not expected or receipt.get("source_sha256") != expected:
                            raise ValueError("S04 source binding")
                    key = (job, kind, identifier)
                    if key in found and found[key]["payload"] != payload.decode():
                        raise ValueError("conflicting duplicate receipt")
                    found[key] = {
                        "job": job,
                        "kind": kind,
                        "identifier": identifier,
                        "runtime": cell["runtime"],
                        "member": info.filename,
                        "payload": payload.decode(),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
            assembler.finish()
            if used != info.file_size:
                raise ValueError("member length mismatch")
    expected = {
        (c["job"], "F05", method) for c in cells.values() for method in c["methods"]
    }
    expected |= {
        (c["job"], "S04", case)
        for c in cells.values()
        for case in c.get("s04_case_ids", [])
    }
    if members != set(cells) or set(found) != expected:
        raise ValueError("missing/extra evidence")
    return {
        "schema": "millefeuille-evidence-archive/v1",
        "status": "F05 custody accepted; S04 opaque, semantics pending parent review",
        "receipts": [found[k] for k in sorted(found)],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    with open(args.manifest, "rb") as stream:
        raw = stream.read(128 * 1024 + 1)
    if len(raw) > 128 * 1024:
        raise ValueError("manifest bound")
    manifest = json.loads(raw)
    result = validate_archive(args.zip, manifest)
    # No archive path is ever extracted. Only one fixed generated filename.
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    with (output / "evidence.json").open("xb") as stream:
        stream.write(canonical(result))


if __name__ == "__main__":
    main()
