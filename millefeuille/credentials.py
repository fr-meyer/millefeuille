"""Invocation-local static credentials. No SDK auth, network or global env writes."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import unicodedata
from types import MappingProxyType

STAGES = MappingProxyType({
    "local": (), "science": (), "discovery": ("ZOTERO_READ_KEY",),
    "ocr": ("MISTRAL_API_KEY",), "decisions": ("OPENAI_API_KEY",),
    "writeback": ("ZOTERO_READ_KEY", "ZOTERO_WRITE_KEY"),
})
SOURCES = MappingProxyType({
    "MISTRAL_API_KEY": ("litellm-mistral-api-key", "latest"),
    "ZOTERO_READ_KEY": ("zotero-millefeuille-read-key", "latest"),
    "OPENAI_API_KEY": ("litellm-openai-images-api-key", "1"),
    "ZOTERO_WRITE_KEY": ("zotero-millefeuille-write-key", None),
})
_OWNER_UID, _OWNER_GID = 1000, 1003
_ROOT_GID = 1003
OWNERSHIP_PROFILES = MappingProxyType({"host-1003": (1000, 1003, 1003),
                                      "container-local": (1000, 1003, 1000)})
PRINCIPAL = "openclaw-secret-resolver@openclaw-family-gateway.iam.gserviceaccount.com"
DEFAULT_ROOT = "/home/node/.openclaw/private/gcp-sm-runtime"
_SCOPE = ContextVar("millefeuille_credentials", default=None)
_EMPTY = MappingProxyType({})
_GENERATION = re.compile(r"\.generation-[A-Za-z0-9_-]+\Z")


class CredentialError(ValueError):
    """Fixed public codes only, never embed OS/provider errors or values."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code):
    raise CredentialError(code) from None


def credential_source():
    source = os.environ.get("MILLEFEUILLE_CREDENTIAL_SOURCE", "legacy-env")
    if source not in ("legacy-env", "gcp-runtime"):
        _fail("CREDENTIAL_SCOPE_DENIED")
    # A runtime override is not authority for implicit legacy fallback.
    if "MILLEFEUILLE_CREDENTIAL_RUNTIME_ROOT" in os.environ and source != "gcp-runtime":
        _fail("CREDENTIAL_SCOPE_DENIED")
    return source


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail("CREDENTIAL_GENERATION_INCOMPLETE")
            result[key] = value
        return result
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda _: _fail("CREDENTIAL_GENERATION_INCOMPLETE"))
    except (UnicodeError, ValueError, TypeError):
        pass
    _fail("CREDENTIAL_GENERATION_INCOMPLETE")


class CredentialSession:
    """Lazy one-generation session; construction and empty stages do no stat I/O.

    Owned by one invocation. Independent invocations use separate instances.
    Values never enter process-global os.environ or an SDK child's environment.
    """
    def __init__(self, runtime_root, *, library_id, allowed_stages,
                 clock=time.time, monotonic=time.monotonic, ownership_profile="host-1003"):
        if ownership_profile not in OWNERSHIP_PROFILES:
            _fail("CREDENTIAL_GENERATION_UNSAFE")
        self._ownership_profile = ownership_profile
        self._allowed_stages = frozenset(allowed_stages)
        if not self._allowed_stages <= STAGES.keys():
            _fail("CREDENTIAL_SCOPE_DENIED")
        try:
            self.runtime_root = os.fspath(runtime_root)
        except TypeError:
            _fail("CREDENTIAL_GENERATION_UNSAFE")
        if not isinstance(self.runtime_root, str) or "\x00" in self.runtime_root:
            _fail("CREDENTIAL_GENERATION_UNSAFE")
        self.library_id = str(library_id)
        self.clock, self.monotonic = clock, monotonic
        self._root_fd = self._generation_fd = None
        self._generation = self._metadata = None
        self._cache = {}
        self._started_wall = self._started_mono = None
        self._last_wall = self._last_mono = None
        self._closed = False

    @property
    def allowed_stages(self):
        return self._allowed_stages

    def __repr__(self):
        return "<CredentialSession redacted>"

    def close(self):
        self._cache.clear()
        for name in ("_generation_fd", "_root_fd"):
            fd = getattr(self, name)
            if fd is not None:
                os.close(fd)
                setattr(self, name, None)
        self._closed = True

    def _verify(self, info, *, directory=False, root=False):
        expected = 0o700 if directory else 0o400
        uid, root_gid, generation_gid = OWNERSHIP_PROFILES[self._ownership_profile]
        if self._ownership_profile == "host-1003":
            uid, root_gid, generation_gid = _OWNER_UID, _ROOT_GID, _OWNER_GID
        if (info.st_uid != uid or info.st_gid != (root_gid if root else generation_gid)
                or stat.S_IMODE(info.st_mode) != expected
                or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
                or (not directory and info.st_nlink != 1)):
            _fail("CREDENTIAL_GENERATION_UNSAFE")

    def _read(self, fd, name, limit, *, missing="CREDENTIAL_GENERATION_INCOMPLETE"):
        child = None
        try:
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            info = os.fstat(child)
            self._verify(info)
            if info.st_size > limit:
                _fail("CREDENTIAL_INVALID" if limit == 4096 else "CREDENTIAL_GENERATION_INCOMPLETE")
            data = bytearray()
            while len(data) <= limit:
                block = os.read(child, min(8192, limit + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
            after = os.fstat(child)
            if (len(data) > limit or len(data) != info.st_size
                    or (info.st_size, info.st_mtime_ns, info.st_ctime_ns) !=
                       (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                _fail("CREDENTIAL_GENERATION_UNSAFE")
            return bytes(data)
        except FileNotFoundError:
            _fail(missing)
        except OSError:
            _fail("CREDENTIAL_GENERATION_UNSAFE")
        finally:
            if child is not None:
                os.close(child)

    def _open_root(self):
        path = Path(self.runtime_root)
        if not path.is_absolute() or any(x in (".", "..") for x in self.runtime_root.split("/")):
            _fail("CREDENTIAL_GENERATION_UNSAFE")
        fd = None
        try:
            fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            for part in path.parts[1:]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            self._verify(os.fstat(fd), directory=True, root=True)
            self._root_fd, fd = fd, None
        except FileNotFoundError:
            _fail("CREDENTIAL_RUNTIME_UNAVAILABLE")
        except OSError:
            _fail("CREDENTIAL_GENERATION_UNSAFE")
        finally:
            if fd is not None:
                os.close(fd)

    def _status(self, now, *, initial=False):
        value = _strict_json(self._read(self._root_fd, ".millefeuille-refresh.json", 65536))
        if (not isinstance(value, dict) or value.get("schema") != "millefeuille-credential-refresh/v1"
                or not _number(value.get("checked_at_unix"))
                or value["checked_at_unix"] > now + 30
                or value.get("status") not in ("ok", "failed")):
            _fail("CREDENTIAL_GENERATION_INCOMPLETE")
        # Any observed failed refresh is fail-closed, including a retained old generation.
        if value["status"] == "failed":
            _fail("CREDENTIAL_REFRESH_FAILED")
        if value.get("error_code") is not None:
            _fail("CREDENTIAL_GENERATION_INCOMPLETE")
        if initial and value.get("generation") != self._generation:
            _fail("CREDENTIAL_GENERATION_INCOMPLETE")
        if not isinstance(value.get("generation"), str) or not _GENERATION.fullmatch(value["generation"]):
            _fail("CREDENTIAL_GENERATION_INCOMPLETE")
        if initial and value["checked_at_unix"] < self._metadata["created_at_unix"]:
            _fail("CREDENTIAL_GENERATION_INCOMPLETE")

    def _start(self, now, mono):
        try:
            self._open_root()
            info = os.stat("current", dir_fd=self._root_fd, follow_symlinks=False)
            uid, _, gid = OWNERSHIP_PROFILES[self._ownership_profile]
            if self._ownership_profile == "host-1003":
                uid, gid = _OWNER_UID, _OWNER_GID
            if not stat.S_ISLNK(info.st_mode) or info.st_uid != uid or info.st_gid != gid:
                _fail("CREDENTIAL_GENERATION_UNSAFE")
            name = os.readlink("current", dir_fd=self._root_fd)
            if not _GENERATION.fullmatch(name):
                _fail("CREDENTIAL_GENERATION_UNSAFE")
            self._generation = name
            self._generation_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                          dir_fd=self._root_fd)
            self._verify(os.fstat(self._generation_fd), directory=True)
            meta = _strict_json(self._read(self._generation_fd, ".millefeuille-generation.json", 65536))
            if (not isinstance(meta, dict) or meta.get("schema") != "millefeuille-credential-generation/v1"
                    or meta.get("generation") != name or meta.get("complete") is not True
                    or meta.get("transport") != "metadata-rest" or meta.get("principal") != PRINCIPAL
                    or not isinstance(meta.get("entries"), dict)
                    or not _number(meta.get("created_at_unix"))
                    or not _number(meta.get("expires_at_unix"))):
                _fail("CREDENTIAL_GENERATION_INCOMPLETE")
            created, expires = meta["created_at_unix"], meta["expires_at_unix"]
            if expires != created + 18000 or created > now + 30:
                _fail("CREDENTIAL_GENERATION_INCOMPLETE")
            if now >= expires or now - created > 300:
                _fail("CREDENTIAL_GENERATION_EXPIRED")
            self._metadata = meta
            self._status(now, initial=True)
            self._started_wall, self._started_mono = now, mono
        except CredentialError:
            self.close()
            raise
        except OSError:
            self.close()
            _fail("CREDENTIAL_RUNTIME_UNAVAILABLE")

    def for_stage(self, stage):
        try:
            return self._for_stage(stage)
        except CredentialError:
            self.close()
            raise

    def _for_stage(self, stage):
        if stage not in self.allowed_stages or stage not in STAGES or self._closed:
            _fail("CREDENTIAL_SCOPE_DENIED")
        if not STAGES[stage]:
            return _EMPTY
        now, mono = self.clock(), self.monotonic()
        if not _number(now) or not _number(mono):
            _fail("CREDENTIAL_GENERATION_EXPIRED")
        if self._generation_fd is None:
            self._start(now, mono)
        else:
            if ((self._last_wall is not None and now < self._last_wall)
                    or (self._last_mono is not None and mono < self._last_mono)
                    or now >= self._metadata["expires_at_unix"]
                    or self._started_wall + mono - self._started_mono >= self._metadata["expires_at_unix"]):
                self._cache.clear()
                _fail("CREDENTIAL_GENERATION_EXPIRED")
            self._status(now)
        self._last_wall, self._last_mono = now, mono
        selected = {}
        for key in STAGES[stage]:
            logical, requested = SOURCES[key]
            entry = self._metadata["entries"].get(logical)
            if not isinstance(entry, dict):
                _fail("CREDENTIAL_MISSING")
            resolved = entry.get("resolved_version", "")
            prefix = "projects/1046324571444/secrets/" + logical + "/versions/"
            if (entry.get("state") != "ENABLED" or not isinstance(resolved, str)
                    or not resolved.startswith(prefix) or not re.fullmatch(r"[1-9][0-9]*", resolved[len(prefix):])
                    or (requested is not None and entry.get("requested_version") != requested)
                    or (requested not in (None, "latest") and resolved != prefix + requested)
                    or (requested is None and (not isinstance(entry.get("requested_version"), str)
                                              or not re.fullmatch(r"[1-9][0-9]*", entry["requested_version"])
                                              or resolved != prefix + entry["requested_version"]))):
                _fail("CREDENTIAL_INVALID")
            if key not in self._cache:
                raw = self._read(self._generation_fd, logical, 4096, missing="CREDENTIAL_MISSING")
                try:
                    value = raw.decode("utf-8").rstrip("\r\n")
                except UnicodeError:
                    value = None
                if value is None:
                    _fail("CREDENTIAL_INVALID")
                if not value or any(c.isspace() or ord(c) < 33 or unicodedata.category(c).startswith("C") for c in value):
                    _fail("CREDENTIAL_INVALID")
                self._cache[key] = value
            selected[key] = self._cache[key]
        return MappingProxyType(selected)


@contextmanager
def credential_scope(session):
    token = _SCOPE.set(session)
    try:
        yield session
    finally:
        _SCOPE.reset(token)
        session.close()


def credential_value(name: str, *, stage: str) -> str:
    if name not in STAGES.get(stage, ()):
        _fail("CREDENTIAL_SCOPE_DENIED")
    session = _SCOPE.get()
    if session is not None:
        return session.for_stage(stage)[name]
    if credential_source() == "gcp-runtime":
        _fail("CREDENTIAL_SCOPE_DENIED")
    return os.environ.get(name, "")


def parsed_stages(args, command):
    """Only consume genuine parsed Namespace fields, never raw argv scanning."""
    live = bool(getattr(args, "live", False))
    if command == "process":
        if getattr(args, "status", False):
            return frozenset({"local"})
        stages = {"discovery"}
        if live:
            stages.update(("ocr", "science", "decisions"))
        if live and getattr(args, "writeback", False):
            stages.add("writeback")
        return frozenset(stages)
    if command in ("decisions-classify", "final-classification"):
        if command == "final-classification" and getattr(args, "writeback_authority", None):
            return frozenset({"writeback"} if live else {"local"})
        return frozenset({"decisions", "science"} if live else {"local"})
    if command == "clean-start":
        operation = args.operation
        if operation == "prepare-scientific-note":
            return frozenset({"science"})
        return frozenset({"decisions", "science"} if operation == "live" else
                         {"writeback"} if operation == "writeback-live" else {"local"})
    if command == "standalone-writeback":
        return frozenset({"writeback"} if live else {"local"})
    if command == "standalone-retention":
        return frozenset({"discovery"} if live or getattr(args, "verify_cloud", False) else {"local"})
    return frozenset({"local"})


@contextmanager
def command_credential_scope(args, command):
    source = credential_source()
    if source == "legacy-env":
        yield None
        return
    session = CredentialSession(os.environ.get("MILLEFEUILLE_CREDENTIAL_RUNTIME_ROOT", DEFAULT_ROOT),
                                library_id=os.environ.get("ZOTERO_LIBRARY_ID", ""),
                                allowed_stages=parsed_stages(args, command),
                                ownership_profile=os.environ.get("MILLEFEUILLE_CREDENTIAL_OWNERSHIP_PROFILE", "host-1003"))
    with credential_scope(session):
        # Existing parsed operation preflights writeback before any provider spend.
        yield session


@contextmanager
def native_batch_credential_scope(cfg):
    """Only the existing sequential native scientific Hydra delegate, not legacy OCR/tag routes."""
    if credential_source() == "legacy-env":
        yield None
        return
    processing = cfg.processing
    if (not processing.scientific_summary or processing.dry_run
            or processing.scientific_execution_profile != "native-sdk-bridge/v1"
            or processing.concurrency != 1
            or cfg.download.enabled or cfg.ocr.enabled or cfg.tree_structure.enabled
            or cfg.tag_adding.enabled or cfg.selection_tagging.enabled):
        _fail("CREDENTIAL_SCOPE_DENIED")
    stages = {"discovery", "ocr", "science"}
    if processing.scientific_classification:
        stages.add("decisions")
    if processing.scientific_fullcycle or processing.get("scientific_writeback", False):
        stages.add("writeback")
    session = CredentialSession(os.environ.get("MILLEFEUILLE_CREDENTIAL_RUNTIME_ROOT", DEFAULT_ROOT),
                                library_id=os.environ.get("ZOTERO_LIBRARY_ID", ""),
                                allowed_stages=frozenset(stages),
                                ownership_profile=os.environ.get("MILLEFEUILLE_CREDENTIAL_OWNERSHIP_PROFILE", "host-1003"))
    with credential_scope(session):
        yield session
