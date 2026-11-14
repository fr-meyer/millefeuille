"""Source proposal only. Stdlib driver, no product import, no implicit execution."""
import argparse
import base64
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import threading
import time
import tomllib
import urllib.request
import venv
import zipfile

import capture

HEAD = "f0dc10a9e3ff005ff442f14e5f5129b7515dcd0f"
TREE = "61c19a3dedb27c677050dd91e1d9013fc1d06909"
CAP = 1536 * 1024 * 1024


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_bytes(data):
    return (json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False,
                       allow_nan=False) + "\n").encode("utf-8")


def save(path, data):
    path.write_bytes(canonical_bytes(data))


def binding(raw):
    return dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


DIAGNOSTICS = {
    b"UnicodeEncodeError": "UNICODE_ENCODE_ERROR",
    b"UnicodeDecodeError": "UNICODE_DECODE_ERROR",
    b"UnicodeError": "UNICODE_ERROR",
    b"LookupError": "LOOKUP_ERROR",
    b"ModuleNotFoundError": "MODULE_NOT_FOUND_ERROR",
    b"ImportError": "IMPORT_ERROR",
    b"PermissionError": "PERMISSION_ERROR",
    b"FileNotFoundError": "FILE_NOT_FOUND_ERROR",
    b"MemoryError": "MEMORY_ERROR",
}


def diagnostic_class(raw):
    found = {DIAGNOSTICS[token] for token in DIAGNOSTICS
             if re.search(rb"(?:^|[\r\n])" + token + rb":", raw)}
    return next(iter(found)) if len(found) == 1 else "UNKNOWN"


class PhaseError(RuntimeError):
    pass


class Runner:
    def __init__(self, root, env):
        self.root, self.env, self.records = root, env, []
        self.started = time.monotonic()

    def journal(self):
        save(self.root / "COMMANDS.json", self.records)

    def action(self, phase, fn):
        rec = dict(phase=phase, state="STARTED", outcome=None, returncode=None, cleanup="not-applicable")
        self.records.append(rec)
        self.journal()
        try:
            result = fn()
            rec["outcome"] = "OK"
            return result
        except BaseException:
            rec["outcome"] = "ACTION_FAILURE"
            raise PhaseError("sanitized non-command action failure") from None
        finally:
            rec["state"] = "FINISHED"
            self.journal()

    def run(self, phase, args, cwd=None, output=None):
        capture.require(re.fullmatch(r"[a-z0-9-]{1,64}", phase), "unsafe phase label")
        rec = dict(phase=phase,state="STARTED",outcome=None,returncode=None,
                   cleanup="not-needed",stderr=None)
        self.records.append(rec)
        self.journal()
        child,readers,raw = None,[],bytearray()
        exceeded,drain_failed = threading.Event(),threading.Event()
        err_sample,err_hash,err_count = bytearray(),hashlib.sha256(),[0]
        def drain_stdout():
            try:
                while True:
                    chunk=child.stdout.read(65536)
                    if not chunk: return
                    if len(raw)+len(chunk)>capture.MAX_BYTES:
                        exceeded.set();child.kill();return
                    raw.extend(chunk)
            except BaseException:
                drain_failed.set()
        def drain_stderr():
            try:
                while True:
                    chunk=child.stderr.read(65536)
                    if not chunk: return
                    err_count[0]+=len(chunk)
                    err_hash.update(chunk)
                    err_sample.extend(chunk[:max(0,32768-len(err_sample))])
                    if err_count[0]>1048576:
                        exceeded.set();child.kill();return
            except BaseException:
                drain_failed.set()
        try:
            capture.require(time.monotonic()-self.started<900,"cooperative stage deadline")
            child=subprocess.Popen([str(x) for x in args],cwd=cwd or self.root,
                env=self.env,stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if output else subprocess.DEVNULL,stderr=subprocess.PIPE)
            readers=[threading.Thread(target=drain_stderr,daemon=True)]
            if output: readers.append(threading.Thread(target=drain_stdout,daemon=True))
            for reader in readers: reader.start()
            child.wait(timeout=180)
            for reader in readers: reader.join(timeout=5)
            if exceeded.is_set() or drain_failed.is_set() or any(r.is_alive() for r in readers):
                rec["outcome"]="OUTPUT_CAP_OR_DRAIN"
                raise PhaseError("sanitized phase output failure")
            if child.returncode:
                rec["outcome"]="NONZERO"
                raise PhaseError("sanitized phase nonzero")
            rec["outcome"]="OK"
            if output: output.write_bytes(raw)
            return bytes(raw)
        except subprocess.TimeoutExpired:
            rec["outcome"]="TIMEOUT"
            raise PhaseError("sanitized phase timeout") from None
        except OSError:
            rec["outcome"]="LAUNCH_OR_IO_FAILURE" if child is None else "IO_FAILURE"
            raise PhaseError("sanitized phase I/O failure") from None
        except BaseException:
            rec["outcome"]=rec["outcome"] or "VALIDATION_OR_CONTROL_FAILURE"
            raise
        finally:
            try:
                if child:
                    if child.poll() is None:
                        child.kill();child.wait(timeout=5)
                    rec["returncode"]=child.returncode
                    for reader in readers: reader.join(timeout=5)
                    if any(r.is_alive() for r in readers):
                        raise PhaseError("drain cleanup incomplete")
                    if output: child.stdout.close()
                    child.stderr.close()
                    rec["cleanup"]="DIRECT_CHILD_REAPED"
            except BaseException:
                rec["cleanup"]="FAILED"
                rec["outcome"]="CLEANUP_FAILURE"
                raise PhaseError("sanitized cleanup failure") from None
            finally:
                rec["stderr"]=dict(observed_bytes=err_count[0],sha256=err_hash.hexdigest(),
                    truncated=err_count[0]>32768,stream_complete=child is not None and not drain_failed.is_set()
                        and not exceeded.is_set() and not any(r.is_alive() for r in readers),
                    diagnostic_class=diagnostic_class(bytes(err_sample)))
                rec["state"]="FINISHED"
                self.journal()


def footprint(root):
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def verify_receipts(root, require_success=True):
    raw=(root/"OUTCOME.json").read_bytes()
    outcome=json.loads(raw)
    capture.require(outcome.get("schema")=="p09-outcome/v4","outcome schema")
    capture.require(raw==canonical_bytes(outcome),"noncanonical outcome")
    journal=(root/"COMMANDS.json").read_bytes()
    capture.require(outcome["commands"]==binding(journal),"commands binding mismatch")
    capture.require(journal==canonical_bytes(json.loads(journal)),"noncanonical commands")
    summary=root/"SUMMARY.json"
    if outcome["qualification_success"]:
        capture.require(outcome["status"]=="SUCCESS_FINITE_PROVENANCE"
                        and outcome["summary_absent"] is False,"success status")
        data=summary.read_bytes()
        capture.require(outcome["summary"]==binding(data),"summary binding mismatch")
        capture.require(data==canonical_bytes(json.loads(data)),"noncanonical summary")
    else:
        capture.require(outcome["status"]=="FAILED" and outcome["summary"] is None
            and outcome["summary_absent"] and not summary.exists(),"failed summary contract")
    if require_success: capture.require(outcome["qualification_success"],"failed stage")
    return outcome


def finalize(root, result, records, failure=None, cap=CAP):
    summary=root/"SUMMARY.json"
    summary.unlink(missing_ok=True)
    journal=canonical_bytes(records)
    (root/"COMMANDS.json").write_bytes(journal)
    try:
        capture.require(failure is None,"stage failed")
        capture.require(all(x["state"]=="FINISHED" and x["outcome"]=="OK" for x in records),
                        "unsuccessful phase")
        projected=canonical_bytes(result)
        outcome=dict(schema="p09-outcome/v4",status="SUCCESS_FINITE_PROVENANCE",
            qualification_success=True,commands=binding(journal),summary=binding(projected),
            summary_absent=False)
        final=canonical_bytes(outcome)
        existing=footprint(root)-len(journal)
        if (root/"OUTCOME.json").exists(): existing-=(root/"OUTCOME.json").stat().st_size
        capture.require(existing+len(projected)+len(journal)+len(final)<=cap,"projected retention cap")
        summary.write_bytes(projected)
        capture.require(summary.read_bytes()==projected and
            (root/"COMMANDS.json").read_bytes()==journal,"persisted bytes differ")
        (root/"OUTCOME.json").write_bytes(final)
        capture.require((root/"OUTCOME.json").read_bytes()==final,"outcome persisted bytes differ")
        verify_receipts(root)
    except BaseException:
        summary.unlink(missing_ok=True)
        actual=(root/"COMMANDS.json").read_bytes()
        failed=dict(schema="p09-outcome/v4",status="FAILED",qualification_success=False,
            cause=failure or "FINAL_VALIDATION_OR_RETENTION",commands=binding(actual),
            summary=None,summary_absent=True)
        save(root/"OUTCOME.json",failed)
        verify_receipts(root,require_success=False)
        raise PhaseError("stage failed; sanitized OUTCOME authoritative") from None


def unpack(archive, dest):
    dest.mkdir()
    with tarfile.open(archive) as tf:
        members = tf.getmembers()
        capture.require(len(members) <= 2048 and sum(m.size for m in members) <= 32*1024*1024,
                        "archive limits")
        for m in members:
            p = Path(m.name)
            capture.require(not p.is_absolute() and ".." not in p.parts
                            and (m.isdir() or m.isfile()), "unsafe archive member")
            target = dest / p
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tf.extractfile(m).read())
    children = list(dest.iterdir())
    return children[0] if len(children) == 1 and children[0].is_dir() else dest


def wheel_identity(path, key, ver, expected_hash=None):
    actual = digest(path)
    capture.require(expected_hash is None or actual == expected_hash, "incorrect built-wheel hash")
    with zipfile.ZipFile(path) as z:
        rows = [n for n in z.namelist() if n.endswith(".dist-info/METADATA")]
        capture.require(len(rows) == 1, "wheel metadata cardinality")
        meta = BytesParser().parsebytes(z.read(rows[0]))
        capture.require(capture.name(meta["Name"]) == key and meta["Version"] == ver,
                        "wheel/input metadata mismatch")
    return actual


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PhaseError("download redirect refused")


def download(row, path):
    origin = row["origin"]
    url = "https://" + origin["host"] + origin["path"]
    capture.public_url(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    # No credentials, retry, proxy inheritance or redirect fallback.
    with opener.open(url, timeout=30) as response, path.open("xb") as out:
        total = 0
        while True:
            raw = response.read(65536)
            if not raw:
                break
            total += len(raw)
            capture.require(total <= 128*1024*1024, "download input cap")
            out.write(raw)
    capture.require(digest(path) == row["sha256"], "download SHA256 mismatch")


def pipeline(source, root, run):
    pin = run.run("source-pin", ["git", "-C", source, "rev-parse", "HEAD", "HEAD^{tree}"], output=root/"pin.tmp")
    capture.require(pin.decode().splitlines() == [HEAD, TREE], "source revision mismatch")
    capture.require(not run.run("source-clean", ["git", "-C", source, "status", "--porcelain"], output=root/"clean.tmp").strip(), "dirty source")
    archive = root/"source.tar"
    run.run("source-archive", ["git", "-C", source, "archive", "--format=tar", "--output="+str(archive), HEAD])
    src = unpack(archive, root/"product-source")
    v = root/"venv"
    run.action("fresh-venv-seed", lambda: venv.EnvBuilder(
        with_pip=True, system_site_packages=False, symlinks=False).create(v))
    capture.require("include-system-site-packages = false" in (v/"pyvenv.cfg").read_text().lower(), "site leakage")
    py = v/("Scripts/python.exe" if os.name == "nt" else "bin/python")
    pip = [py, "-m", "pip", "--isolated", "--disable-pip-version-check"]
    net = ["--no-cache-dir", "--retries", "0", "--timeout", "30", "--index-url", "https://pypi.org/simple"]
    inv_code = "import importlib.metadata as m,json;print(json.dumps([{'name':d.metadata['Name'],'version':d.version} for d in m.distributions()]))"
    run.run("seed-inventory", [py,"-B","-c",inv_code], output=root/"seed.json")
    run.run("tool-replace", pip+["install",*net,"--force-reinstall","--upgrade","--only-binary=:all:","--report",root/"tools.json","pip","setuptools","wheel"])
    tool_report = capture.load(root/"tools.json")
    tools = capture.report(tool_report)
    capture.require({"pip","setuptools","wheel"} <= set(tools)
                    <= {"pip","setuptools","wheel","packaging"}, "unexpected tool graph")
    versions = {k:v["version"] for k,v in tools.items()}
    built = root/"product-wheel"
    built.mkdir()
    run.run("product-build", pip+["wheel","--no-index","--no-deps","--no-build-isolation","--wheel-dir",built,src])
    product = list(built.glob("*.whl"))
    capture.require(len(product)==1, "product wheel cardinality")
    product = product[0]
    phash = wheel_identity(product,"millefeuille","0.5.0")
    local = dict(uri=product.as_uri(),sha256=phash)
    # --ignore-installed here is RESOLUTION ONLY, never bootstrap replacement.
    run.run("resolve",pip+["install",*net,"--dry-run","--ignore-installed","--no-build-isolation",
                           "--only-binary=:all:","--no-binary=antlr4-python3-runtime,bibtexparser",
                           "--report",root/"runtime.json",str(product)+"[native]",
                           "antlr4-python3-runtime==4.9.3","bibtexparser==1.4.4"])
    runtime = capture.load(root/"runtime.json")
    rows = capture.source_policy(capture.report(runtime,local))
    phases = {"build-tools": tool_report, "runtime-final-inputs": runtime}
    capture.reconcile_phases(phases,local)  # Fail conflicts BEFORE any final install.
    inputs, wheels = root/"inputs", root/"runtime-wheels"
    inputs.mkdir(); wheels.mkdir()
    paths, receipts, expected_wheels = [], {}, {}
    for key,row in rows.items():
        if row["origin"]["kind"] == "exact-local-built-wheel":
            paths.append(product); expected_wheels[product]=(key,row["version"],phash); continue
        filename = row["origin"]["path"].rsplit("/",1)[-1]
        capture.require(re.fullmatch(r"[A-Za-z0-9_.+-]{1,200}",filename),"unsafe input filename")
        path = inputs/filename
        run.action("download-"+key, lambda: download(row,path))
        if row["input_kind"] == "wheel":
            wheel_identity(path,key,row["version"],row["sha256"])
            paths.append(path); expected_wheels[path]=(key,row["version"],row["sha256"]); continue
        tree = unpack(path,root/("source-"+key))
        pp = tree/"pyproject.toml"
        bs = tomllib.loads(pp.read_text()).get("build-system",{}) if pp.exists() else {
            "requires":["setuptools>=40.8.0"],"build-backend":"setuptools.build_meta:__legacy__"}
        backend = bs.get("build-backend","setuptools.build_meta:__legacy__")
        capture.require(backend in {"setuptools.build_meta","setuptools.build_meta:__legacy__"}
                        and not bs.get("backend-path"),"unreviewed build backend")
        static = capture.build_requirements(bs.get("requires",[]),versions)
        hook = ("import setuptools.build_meta as b,json; "
                + ("h=b.__legacy__; " if backend.endswith(":__legacy__") else "h=b; ")
                + "print('P09HOOK:'+json.dumps(h.get_requires_for_build_wheel({})))")
        out = root/(key+"-hook.tmp")
        raw = run.run("build-requires-"+key,[py,"-B","-c",hook],cwd=tree,output=out)
        markers = [line[8:] for line in raw.decode().splitlines() if line.startswith("P09HOOK:")]
        capture.require(len(markers)==1,"build hook report missing")
        dynamic = capture.build_requirements(json.loads(markers[0]),versions)
        dest = wheels/key; dest.mkdir()
        run.run("source-build-"+key,pip+["wheel","--no-index","--no-deps","--no-build-isolation","--wheel-dir",dest,tree])
        made = list(dest.glob("*.whl")); capture.require(len(made)==1,"built wheel cardinality")
        whash = wheel_identity(made[0],key,row["version"])
        receipts[key]=dict(input_sha256=row["sha256"],version=row["version"],wheel_sha256=whash,
                           static_build_requirements=static,dynamic_build_requirements=dynamic)
        paths.append(made[0])
        expected_wheels[made[0]]=(key,row["version"],whash)
    # Verify all built hashes immediately before exact no-deps/no-index install.
    for key,receipt in receipts.items():
        made = list((wheels/key).glob("*.whl"))[0]
        wheel_identity(made,key,receipt["version"],receipt["wheel_sha256"])
    for path,(key,ver,expected_hash) in expected_wheels.items():
        wheel_identity(path,key,ver,expected_hash)
    run.run("exact-install",pip+["install","--no-index","--no-deps","--no-cache-dir",*paths])
    run.run("dependency-check",pip+["check"])
    run.run("installed-inventory",[py,"-B","-c",inv_code],output=root/"installed.json")
    run.run("pip-inspect",pip+["inspect","--local"],output=root/"inspect.json")
    summary = capture.built_join(capture.validate(phases,capture.load(root/"inspect.json"),
                                                capture.load(root/"installed.json"),local),receipts)
    pure = run.run("purelib",[py,"-B","-c","import sysconfig;print(sysconfig.get_path('purelib'))"],output=root/"pure.tmp").decode().strip()
    members = 0
    with zipfile.ZipFile(product) as z:
        for n in z.namelist():
            if n.startswith("millefeuille/") and not n.endswith("/"):
                capture.require(".." not in Path(n).parts,"unsafe wheel member")
                capture.require((Path(pure)/n).read_bytes()==z.read(n),"installed product bytes differ")
                members += 1
    run.run("platform",[py,"-B","-c","import sys,platform,json;print(json.dumps({'python':sys.version,'platform':platform.platform()}))"],output=root/"platform.json")
    capture.require(not run.run("final-clean",["git","-C",source,"status","--porcelain"],output=root/"final.tmp").strip(),"source changed")
    summary.update(source_head=HEAD,source_tree=TREE,seed_inventory=capture.inventory(capture.load(root/"seed.json")),
                   tool_versions=versions,platform=capture.load(root/"platform.json"),installed_product_members=members,
                   tooling_sha=os.environ.get("TOOLING_SHA","UNBOUND"),
                   tooling_files={p.name:digest(p) for p in [Path(__file__),Path(__file__).with_name("capture.py")]})
    return summary


def main(source, root):
    capture.require(not root.exists(),"fresh root required")
    root.mkdir(mode=0o700)
    os.umask(0o077)
    env = {k:v for k,v in os.environ.items() if k in {"PATH","SYSTEMROOT","WINDIR","COMSPEC","PATHEXT","TEMP","TMP"}}
    home = root/"home"; home.mkdir()
    env.update(HOME=str(home),USERPROFILE=str(home),PYTHONNOUSERSITE="1",PYTHONDONTWRITEBYTECODE="1",
               PIP_CONFIG_FILE=os.devnull,PIP_DISABLE_PIP_VERSION_CHECK="1",
               PYTHONUTF8="1",PYTHONIOENCODING="utf-8")
    run = Runner(root,env)
    result, failure = None, None
    try:
        result = pipeline(source,root,run)
    except BaseException:
        failure = "PIPELINE_FAILURE"  # Never serialize exception strings/URLs/env.
        raise
    finally:
        finalize(root,result,run.records,failure)


if __name__ == "__main__":
    p=argparse.ArgumentParser();p.add_argument("--source",type=Path,required=True);p.add_argument("--root",type=Path,required=True)
    args=p.parse_args()
    try:
        main(args.source.resolve(),args.root.absolute())
    except BaseException:
        # Raw third-party hook/transport exceptions can contain URLs or secrets.
        print("P09_FAILED: consult sanitized OUTCOME/COMMANDS",flush=True)
        raise SystemExit(1) from None
