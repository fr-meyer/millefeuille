"""Internal actual Zotero HTTP adapter. No discovery/auth/root/CLI entry point.
Explicit injected secret only; network-free tests use httpx.MockTransport.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import math
import re
import time

import httpx

from millefeuille.domain.millefeuille import MillefeuilleContractError as Error
from millefeuille.domain.writeback_core import _hash, _key, _snapshot, _wire


class _DefinitiveRefusalError(Error):
    """Server explicitly refused mutation; do not attribute matching state to us."""


class _AmbiguousWriteError(Error):
    """No verifiable acknowledgement; no retry or ownership inference."""


@dataclass(frozen=True)
class _Acknowledgement:
    version: int
    status: int = 204


def _identity(value):
    if (
        set(value) != {"library_type", "library_id", "key"}
        or value["library_type"] not in ("user", "group")
        or not isinstance(value["library_id"], str)
        or not re.fullmatch(r"[1-9][0-9]{0,14}", value["library_id"])
    ):
        raise Error("invalid remote identity")
    _key(value["key"])
    return dict(value)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Error("duplicate JSON field")
        result[key] = value
    return result


def _version(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,15}", value):
        raise Error("invalid remote version")
    version = int(value)
    if version > 9007199254740991:
        raise Error("remote version exceeds safe integer")
    return version


def _project(identity, raw, version):
    """No dropped raw proof: preserve envelope bytes separately from semantics."""
    if type(version) is not int or not 1 <= version <= 9007199254740991:
        raise Error("invalid projection version")
    value = json.loads(
        raw,
        object_pairs_hook=_pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(Error("nonfinite JSON")),
    )
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("data"), dict)
        or not isinstance(value.get("library"), dict)
    ):
        raise Error("remote envelope missing")
    lib, data = value["library"], value["data"]
    if (
        lib.get("type") != identity["library_type"]
        or type(lib.get("id")) is not int
        or str(lib["id"]) != identity["library_id"]
        or value.get("key") != identity["key"]
        or data.get("key") != identity["key"]
        or type(value.get("version")) is not int
        or type(data.get("version")) is not int
        or value["version"] != version
        or data["version"] != version
    ):
        raise Error("remote identity/version mismatch")
    semantic = dict(data)
    del semantic["key"], semantic["version"]
    modified = semantic.pop("dateModified", None)
    if not isinstance(modified, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", modified
    ):
        raise Error("invalid server modification timestamp")
    datetime.strptime(modified, "%Y-%m-%dT%H:%M:%SZ")
    snapshot = _snapshot({**identity, "version": version, "data": semantic})
    proof = {
        "schema": "millefeuille-zotero-read-proof/v1",
        "identity": identity,
        "status": 200,
        "header_version": version,
        "raw_utf8": raw.decode("utf-8"),
        "raw_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "snapshot_digest": _hash(snapshot),
    }
    return snapshot, proof


def _checked_observation(value):
    if not isinstance(value, dict) or set(value) != {"snapshot", "proof"}:
        raise Error("authoritative observation required")
    proof = value["proof"]
    if (
        set(proof)
        != {
            "schema",
            "identity",
            "status",
            "header_version",
            "raw_utf8",
            "raw_sha256",
            "snapshot_digest",
        }
        or proof["schema"] != "millefeuille-zotero-read-proof/v1"
        or proof["status"] != 200
    ):
        raise Error("read proof contract")
    raw = proof["raw_utf8"].encode("utf-8")
    if len(raw) > 65536:
        raise Error("proof byte cap")
    snapshot, expected = _project(
        _identity(proof["identity"]), raw, proof["header_version"]
    )
    if expected != proof or snapshot != value["snapshot"]:
        raise Error("raw/projection commitment mismatch")
    return snapshot, proof


@dataclass(repr=False)
class _HTTPBoundary:
    secret: str = field(repr=False)
    transport: object = field(default=None, repr=False)
    _reads: int = field(default=0, init=False)
    _writes: int = field(default=0, init=False)

    def __post_init__(self):
        if not isinstance(self.secret, str) or not re.fullmatch(
            r"[A-Za-z0-9]{1,128}", self.secret
        ):
            raise Error("explicit credential required")

    def __repr__(self):
        return "<private Zotero HTTP boundary>"

    async def _exchange(
        self, method, identity, deadline, body=None, expected_version=None
    ):
        collection = method == "GET-COLLECTION"
        if collection:
            method = "GET"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("remote budget expired")
        # Reserve a small part of the SAME deadline for both cleanup phases.
        operation_deadline = deadline - min(0.05, remaining / 5)
        loop = asyncio.get_running_loop()
        outer_end = loop.time() + remaining
        op_end = loop.time() + max(0, operation_deadline - time.monotonic())
        headers = {
            "Zotero-API-Key": self.secret,
            "Zotero-API-Version": "3",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Cache-Control": "no-cache, no-store",
            "Pragma": "no-cache",
        }
        if method == "PATCH":
            headers["If-Unmodified-Since-Version"] = str(expected_version)
            headers["Content-Type"] = "application/json"
        prefix = "users" if identity["library_type"] == "user" else "groups"
        url = f"https://api.zotero.org/{prefix}/{identity['library_id']}/items/{identity['key']}"
        if collection:
            url = url.replace("/items/", "/collections/")
        if self.secret in url:
            raise Error("credential/identity collision")
        client = httpx.AsyncClient(
            transport=self.transport
            if self.transport is not None
            else httpx.AsyncHTTPTransport(retries=0, trust_env=False),
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(remaining),
        )
        response = None
        request = None
        chunks = bytearray()
        primary = None
        result = None
        try:
            async with asyncio.timeout_at(op_end):
                request = client.build_request(
                    method, url, headers=headers, content=body
                )
                response = await client.send(request, stream=True)
                if (
                    len(response.headers) > 32
                    or sum(len(k) + len(v) for k, v in response.headers.raw) > 8192
                ):
                    raise Error("remote header cap")
                status = response.status_code
                if method == "PATCH" and status in (
                    400,
                    401,
                    403,
                    404,
                    409,
                    412,
                    413,
                    415,
                    422,
                    428,
                    429,
                ):
                    raise _DefinitiveRefusalError("remote mutation refused")
                if status != (204 if method == "PATCH" else 200):
                    raise Error("unexpected remote status")
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise Error("encoded remote body denied")
                if (
                    method == "GET"
                    and response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                    != "application/json"
                ):
                    raise Error("JSON content type required")
                versions = response.headers.get_list("last-modified-version")
                if len(versions) != 1:
                    raise Error("remote version header required")
                version = _version(versions[0])
                chunks = bytearray()
                async for chunk in response.aiter_raw():
                    if len(chunks) + len(chunk) > 65536:
                        raise Error("remote byte cap")
                    chunks.extend(chunk)
                if self.secret.encode() in chunks:
                    raise Error("credential echoed in response")
                length = response.headers.get("content-length")
                if length is not None and (
                    not length.isdecimal() or int(length) != len(chunks)
                ):
                    raise Error("remote length mismatch")
                if method == "PATCH":
                    if chunks or version <= expected_version:
                        raise Error("invalid mutation acknowledgement")
                    result = _Acknowledgement(version)
                else:
                    projector = _project
                    if collection:
                        from millefeuille.domain.writeback_live_collection import (
                            project_collection,
                        )

                        projector = project_collection
                    snapshot, proof = projector(identity, bytes(chunks), version)
                    result = {"snapshot": snapshot, "proof": proof}
                chunks.clear()
        except BaseException as exc:
            primary = exc
        finally:
            headers.clear()
            if request is not None:
                request.headers.pop("Zotero-API-Key", None)
            closers = [client.aclose()]
            if response is not None:
                closers.append(response.aclose())
            try:
                # Start owned closers before an already-expired timeout can
                # cancel them without executing their release paths. Observe
                # every result so one close error cannot orphan its sibling.
                cleanup = asyncio.gather(*closers, return_exceptions=True)
                async with asyncio.timeout_at(outer_end):
                    outcomes = await cleanup
                for outcome in outcomes:
                    if isinstance(outcome, BaseException):
                        raise outcome
            except BaseException as exc:
                if primary is None:
                    primary = exc
        chunks.clear()
        if primary is not None:
            result = None
            if isinstance(primary, _DefinitiveRefusalError):
                raise _DefinitiveRefusalError("remote mutation refused") from None
            if method == "PATCH":
                raise _AmbiguousWriteError(
                    "mutation acknowledgement unavailable"
                ) from None
            raise Error("remote read unavailable") from None
        if time.monotonic() >= deadline:
            raise Error("remote deadline expired")
        return result

    def _call(self, method, identity, deadline, **kwargs):
        identity = _identity(identity)
        if (
            type(deadline) not in (int, float)
            or not math.isfinite(deadline)
            or deadline > time.monotonic() + 10.1
        ):
            raise Error("bounded absolute deadline required")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise Error("sync core cannot run inside async loop")
        if self.transport is None:
            from millefeuille.domain.writeback_http_process import _contained_exchange

            return _contained_exchange(
                self.secret, method, identity, deadline, **kwargs
            )
        # Explicit in-process MockTransport injection is only a test seam.
        return asyncio.run(self._exchange(method, identity, deadline, **kwargs))


class _HTTPReader(_HTTPBoundary):
    def read(self, identity, *, deadline):
        if self._reads >= 2:
            raise Error("read budget exhausted")
        self._reads += 1
        return self._call("GET", identity, deadline)


class _HTTPWriter(_HTTPBoundary):
    def patch(self, identity, *, expected_version, delta, deadline):
        if self._writes >= 1:
            raise Error("mutation budget exhausted")
        if (
            type(expected_version) is not int
            or expected_version < 1
            or set(delta) != {"tags", "collections"}
        ):
            raise Error("exact conditional PATCH required")
        # Validate tags/collection structure before any request.
        _snapshot(
            {
                **_identity(identity),
                "version": expected_version,
                "data": {"itemType": "journalArticle", **delta},
            }
        )
        body = _wire(delta)
        if len(body) > 65536:
            raise Error("mutation byte cap")
        self._writes += 1
        return self._call(
            "PATCH", identity, deadline, body=body, expected_version=expected_version
        )
