"""Stdlib provenance projection, no product imports or resolver execution."""
import hashlib
import json
import re
from urllib.parse import urlsplit

MAX_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 512


class Invalid(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise Invalid(message)


def name(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value),
            "invalid distribution name")
    return re.sub(r"[-_.]+", "-", value).lower()


def version(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9.!+_-]{1,128}", value), "invalid version")
    return value


def load(path):
    raw = path.read_bytes()
    require(len(raw) <= MAX_BYTES, "input byte cap")
    def pairs(items):
        result = {}
        for k, v in items:
            require(k not in result, "duplicate JSON key")
            result[k] = v
        return result
    return json.loads(raw, object_pairs_hook=pairs)


def public_url(value):
    require(isinstance(value, str) and len(value) <= 4096, "invalid URL")
    p = urlsplit(value)
    require(p.scheme == "https" and p.hostname in {"files.pythonhosted.org", "pypi.org"}
            and p.username is None and p.password is None and p.port in {None, 443}
            and not p.query and not p.fragment, "non-public/credential URL rejected")
    require(re.fullmatch(r"/[A-Za-z0-9._~%/+-]*", p.path) is not None, "invalid URL path")
    return {"host": p.hostname, "path": p.path, "scheme": "https"}


def report(data, local_wheel=None):
    require(data.get("version") == "1", "unsupported pip report")
    rows = data.get("install")
    require(isinstance(rows, list) and 0 < len(rows) <= MAX_RECORDS, "report record cap")
    found = {}
    for row in rows:
        meta = row["metadata"]
        key, ver = name(meta["name"]), version(meta["version"])
        require(key not in found, "duplicate normalized distribution")
        info = row.get("download_info", {})
        require(not info.get("dir_info") and not info.get("vcs_info"), "editable/VCS source not accepted")
        url = info.get("url", "")
        if local_wheel and url == local_wheel["uri"]:
            origin = {"kind": "exact-local-built-wheel", "sha256": local_wheel["sha256"]}
        else:
            origin = {"kind": "downloaded-input", **public_url(url)}
        digest = info.get("archive_info", {}).get("hashes", {}).get("sha256")
        require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest), "missing SHA256")
        if origin["kind"] == "exact-local-built-wheel":
            require(digest == origin["sha256"], "local wheel hash mismatch")
        filename = urlsplit(url).path.rsplit("/", 1)[-1]
        is_wheel = filename.endswith(".whl")
        found[key] = dict(version=ver, sha256=digest, origin=origin,
                          input_kind="wheel" if is_wheel else "sdist-or-other-archive",
                          installed_wheel_hash="NOT_CAPTURED" if not is_wheel else digest)
    return found


def inventory(data):
    require(isinstance(data, list) and 0 < len(data) <= MAX_RECORDS, "inventory record cap")
    result = {}
    for row in data:
        key = name(row["name"])
        require(key not in result, "duplicate inventory distribution")
        result[key] = version(row["version"])
    return result


def reconcile_phases(phases, local_wheel=None):
    require(isinstance(phases, dict) and 0 < len(phases) <= 4, "phase count")
    projected, merged = {}, {}
    for phase, raw in phases.items():
        require(isinstance(phase, str) and re.fullmatch(r"[a-z0-9-]{1,64}", phase), "phase label")
        rows = report(raw, local_wheel)  # Retains within-report duplicate/hash checks.
        projected[phase] = rows
        for key, row in rows.items():
            if key in merged:
                current = merged[key]
                require(all(current[field] == row[field] for field in
                            ["version", "sha256", "origin", "input_kind", "installed_wheel_hash"]),
                        "conflicting cross-phase artifact")
                current["roles"].append(phase)
            else:
                merged[key] = dict(row, roles=[phase])
    return merged, projected


def validate(reports, inspected, installed, local_wheel=None):
    require(inspected.get("version") == "1", "unsupported pip inspect")
    inspect_rows = inventory([dict(name=r["metadata"]["name"], version=r["metadata"]["version"])
                              for r in inspected["installed"]])
    inv = inventory(installed)
    require(inspect_rows == inv, "inspect/inventory mismatch")
    phase_inputs = None
    if isinstance(reports, dict):
        merged, phase_inputs = reconcile_phases(reports, local_wheel)
    else:
        # Legacy untagged callers retain their strict duplicate semantics.
        merged = {}
        for value in reports:
            for key, row in report(value, local_wheel).items():
                require(key not in merged, "duplicate cross-report distribution")
                merged[key] = row
    require(set(inv) == set(merged), "unreported or uninstalled distribution")
    for key in inv:
        require(inv[key] == merged[key]["version"], "resolved/installed version mismatch")
    unknown = ["ensurepip bootstrap input hashes and executable/toolchain origin not fully captured",
               "external index resolver metadata responses not content-locked",
               "PEP517 ephemeral build requirements for any downloaded sdist not observable in top-level pip report"]
    sdists = [key for key, row in merged.items() if row["input_kind"] != "wheel"]
    return dict(status="CAPTURED_FINITE_INPUT_HASH_AND_INSTALLED_INVENTORY",
                distributions=merged, phase_inputs=phase_inputs, installed=inv, downloaded_sdist_names=sdists,
                unknown=unknown, complete_hermetic_lock=False,
                complete_downloaded_report_input_hashes=True,
                exhaustive_installed_metadata_inventory=True,
                installed_package_byte_equivalence="SEPARATE_PREEXISTING_PROOF_NOT_REEXECUTED")


SDISTS = {"antlr4-python3-runtime": "4.9.3", "bibtexparser": "1.4.4"}


def source_policy(rows):
    for key, row in rows.items():
        if row["input_kind"] != "wheel":
            require(SDISTS.get(key) == row["version"], "unexpected source distribution/version")
    return rows


def build_requirements(values, tool_versions):
    # Intentionally small grammar; unknown/dynamic requirements fail, not install.
    require(isinstance(values, list) and len(values) <= 8, "build requirement bound")
    accepted = []
    for value in values:
        require(isinstance(value, str), "build requirement type")
        match = re.fullmatch(r"(setuptools|wheel)(?:>=(\d+(?:\.\d+)*))?", value)
        require(match is not None, "unreported build requirement")
        key, minimum = match.groups()
        require(key in tool_versions, "build tool not captured")
        if minimum:
            actual = tool_versions[key].split(".")
            require(all(x.isdigit() for x in actual), "unsupported build tool version")
            left, right = tuple(map(int, actual)), tuple(map(int, minimum.split(".")))
            n = max(len(left), len(right))
            require(left + (0,) * (n-len(left)) >= right + (0,) * (n-len(right)), "build tool too old")
        accepted.append(value)
    return accepted


def built_join(summary, builds):
    expected = set(summary["downloaded_sdist_names"])
    require(set(builds) == expected, "missing/unexpected source build receipt")
    for key, build in builds.items():
        row = summary["distributions"][key]
        require(build["input_sha256"] == row["sha256"] and build["version"] == row["version"],
                "build input/version mismatch")
        require(re.fullmatch(r"[0-9a-f]{64}", build.get("wheel_sha256", "")) is not None,
                "missing built wheel SHA256")
        row["installed_wheel_hash"] = build["wheel_sha256"]
    summary["build_receipts"] = builds
    summary["unknown"] = ["ensurepip/bootstrap/interpreter input provenance not complete",
                           "resolver index metadata responses not locked; not a hermetic lock",
                           "third-party wheel bytes beyond metadata inventory not attested"]
    return summary
