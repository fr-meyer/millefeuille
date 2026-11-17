"""Cloud-only verification; no trusted-host qualification claim."""

import hashlib
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile
import zipfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def source(root, policy):
    for name, expected in policy["files"].items():
        path = PurePosixPath(name)
        require(
            not path.is_absolute() and ".." not in path.parts, "invalid source name"
        )
        expected = policy["ci_overlays"].get(name, expected)
        require(digest((root / name).read_bytes()) == expected, "source pin mismatch")
    print(json.dumps({"source_files_verified": len(policy["files"])}))


def archives(root, policy):
    rows = []
    packages = {
        n: h for n, h in policy["files"].items() if n.startswith("millefeuille/")
    }
    for artifact in sorted((root / "dist").rglob("*")):
        if not artifact.is_file():
            continue
        require(artifact.stat().st_size <= 16 * 1024**2, "artifact size limit")
        if artifact.suffix == ".whl":
            with zipfile.ZipFile(artifact) as archive:
                members = {
                    n: archive.read(n)
                    for n in archive.namelist()
                    if not n.endswith("/")
                }
        elif artifact.name.endswith(".tar.gz"):
            with tarfile.open(artifact) as archive:
                members = {}
                for member in archive.getmembers():
                    require(
                        not member.issym() and not member.islnk(),
                        "archive link refused",
                    )
                    if member.isfile():
                        require(member.size <= 4 * 1024**2, "member size limit")
                        name = str(PurePosixPath(*PurePosixPath(member.name).parts[1:]))
                        with archive.extractfile(member) as stream:
                            members[name] = stream.read()
        else:
            raise RuntimeError("unexpected build artifact")
        require(len(members) <= 4096, "member count limit")
        for name, data in members.items():
            parts = PurePosixPath(name).parts
            require(
                not name.startswith("/") and ".." not in parts, "unsafe archive name"
            )
            metadata = any(p.endswith((".dist-info", ".egg-info")) for p in parts)
            require(
                name in policy["files"]
                or metadata
                or name in {"PKG-INFO", "setup.cfg"},
                "unexpected archive content",
            )
            if name in packages:
                require(digest(data) == packages[name], "packaged source mismatch")
        require(set(packages) <= set(members), "package source omitted")
        rows.append(
            {
                "artifact": str(artifact.relative_to(root)),
                "sha256": digest(artifact.read_bytes()),
                "bytes": artifact.stat().st_size,
                "members": len(members),
            }
        )
    require(len(rows) == 3, "expected source wheel, sdist and sdist-derived wheel")
    output = root / ".ci-output"
    output.mkdir(exist_ok=True)
    (output / "artifacts.json").write_text(json.dumps(rows, indent=2) + chr(10))
    print(json.dumps(rows))


def installed(policy, output):
    distribution = importlib.metadata.distribution("millefeuille")
    count = 0
    for name, expected in policy["files"].items():
        if not name.startswith("millefeuille/"):
            continue
        path = Path(distribution.locate_file(name)).resolve()
        require(
            path.is_relative_to(Path(sys.prefix).resolve()),
            "installed provenance outside environment",
        )
        require(digest(path.read_bytes()) == expected, "installed bytes mismatch")
        count += 1
    probe = """
import importlib,sys
seen=[]
def observe(event,args):
 if event.startswith(('socket.','subprocess.')):
  seen.append(event)
 if event in ('os.system','os.posix_spawn','os.fork'):
  seen.append(event)
sys.addaudithook(observe)
importlib.import_module('millefeuille.cli.main')
from millefeuille.domain import _failure_observations as obs
if seen or obs.token() is not None:
 raise RuntimeError('import effects or observer active')
for prefix in ('requests','urllib3','millefeuille.clients.pageindex_client',
               'millefeuille.clients.pageindex_tree_client'):
 if any(n==prefix or n.startswith(prefix+'.') for n in sys.modules):
  raise RuntimeError('eager provider import')
"""
    with tempfile.TemporaryDirectory() as temporary:
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", probe],
            cwd=temporary,
            capture_output=True,
            timeout=30,
            check=False,
        )
        require(
            result.returncode == 0 and not result.stdout and not result.stderr,
            "installed import/privacy check failed",
        )
        help_result = subprocess.run(
            [str(Path(sys.executable).parent / "millefeuille"), "--help"],
            cwd=temporary,
            capture_output=True,
            timeout=30,
            check=False,
        )
        require(
            help_result.returncode == 0
            and len(help_result.stdout) <= 65536
            and b"usage:" in help_result.stdout.lower(),
            "installed CLI check failed",
        )
        require(
            b"PRIVATE FAILURE OBSERVATION"
            not in help_result.stdout + help_result.stderr,
            "success diagnostics leak",
        )
    report = {
        "installed_source_files": count,
        "provenance": True,
        "offline_import": True,
        "observer_default_off": True,
        "cli": True,
    }
    Path(output).write_text(json.dumps(report, indent=2) + chr(10))
    print(json.dumps(report))


def main():
    mode = sys.argv[1]
    manifest = Path(sys.argv[2]).resolve()
    policy = json.loads(manifest.read_text())
    root = manifest.parents[2]
    if mode == "source":
        source(root, policy)
    elif mode == "archives":
        archives(root, policy)
    elif mode == "installed":
        installed(policy, sys.argv[3])
    else:
        raise RuntimeError("unknown validation mode")


if __name__ == "__main__":
    main()
