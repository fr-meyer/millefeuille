"""Installed-only import guard, also bootstrapped in Python test children."""

import hashlib
import importlib.machinery
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig


def need(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_origin(name, origin, config):
    if name != "millefeuille" and not name.startswith("millefeuille."):
        return
    path = Path(origin).resolve()
    site = Path(config["site"]).resolve()
    need(path.is_relative_to(site), "application shadow outside installed site")
    relative = path.relative_to(site).as_posix()
    need(relative in config["package"], "unbound application module")
    need(sha(path) == config["package"][relative], "application hash mismatch")


def install(config):
    site = Path(config["site"]).resolve()
    fixtures = Path(config["fixtures"]).resolve()
    need(site == Path(sysconfig.get_path("purelib")).resolve(), "wrong venv site")
    need(not (fixtures / "millefeuille").exists(), "fixture shadows application")
    for key in ("PYTHONHOME", "PYTHONPATH"):
        os.environ.pop(key, None)
    allowed = {site, fixtures, Path(__file__).resolve().parent}
    allowed.update(
        Path(sysconfig.get_path(k)).resolve() for k in ("stdlib", "platstdlib")
    )
    allowed.add(Path(sysconfig.get_config_var("DESTSHARED")).resolve())
    # CPython's optional standard-library zip, derived from its trusted prefix.
    allowed.add(
        Path(sys.base_prefix)
        / "lib"
        / (
            "python"
            + str(sys.version_info.major)
            + str(sys.version_info.minor)
            + ".zip"
        )
    )
    need(
        all(Path(p).resolve() in allowed for p in sys.path),
        "unexpected interpreter path",
    )
    sys.path.insert(0, str(fixtures))
    for name, module in list(sys.modules.items()):
        if name == "millefeuille" or name.startswith("millefeuille."):
            check_origin(name, module.__file__, config)
    records = Path(config["origins"])
    record = records / (str(os.getpid()) + ".jsonl")
    # Import failures must not become an accepted expected-refusal test.
    violation = records / (str(os.getpid()) + ".violation")

    class Guard:
        @staticmethod
        def find_spec(fullname, path=None, target=None):
            if fullname != "millefeuille" and not fullname.startswith("millefeuille."):
                return None
            spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
            need(spec is not None, "application module missing")
            try:
                check_origin(fullname, spec.origin, config)
            except BaseException:
                violation.write_text("application import rejected")
                raise
            with record.open("a") as stream:
                stream.write(
                    json.dumps(
                        {
                            "module": fullname,
                            "origin": spec.origin,
                            "sha256": sha(spec.origin),
                        }
                    )
                    + chr(10)
                )
            return spec

    sys.meta_path.insert(0, Guard())
    original = subprocess.Popen
    helper = str(Path(__file__).resolve())
    bound = json.dumps(config)

    class InstalledPopen(original):
        def __init__(self, args, *positional, **kwargs):
            env = dict(os.environ if kwargs.get("env") is None else kwargs["env"])
            for key in ("PYTHONHOME", "PYTHONPATH"):
                env.pop(key, None)
            kwargs["env"] = env
            if (
                isinstance(args, (list, tuple))
                and args
                and str(args[0]) == sys.executable
            ):
                args = list(args)
                # Preserve original -c/-m/script and argv under an isolated bootstrap.
                args = [sys.executable, "-I", "-B", helper, bound, *args[1:]]
            super().__init__(args, *positional, **kwargs)

    subprocess.Popen = InstalledPopen
    return original


def child():
    import runpy

    config = json.loads(sys.argv[1])
    args = sys.argv[2:]
    install(config)
    while args and args[0] in ("-I", "-B", "-u"):
        args.pop(0)
    need(bool(args), "empty child command")
    if args[0] == "-c":
        sys.argv = ["-c", *args[2:]]
        exec(compile(args[1], "<string>", "exec"), {"__name__": "__main__"})
    elif args[0] == "-m":
        sys.argv = args[1:]
        runpy.run_module(args[1], run_name="__main__", alter_sys=True)
    else:
        need(not args[0].startswith("-"), "unsupported child interpreter option")
        sys.argv = args
        runpy.run_path(args[0], run_name="__main__")


if __name__ == "__main__":
    child()
