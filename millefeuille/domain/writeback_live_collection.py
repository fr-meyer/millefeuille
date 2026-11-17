"""One explicitly budgeted existing-collection GET; no mutation/discovery."""

import hashlib

from millefeuille.domain.millefeuille import MillefeuilleContractError as Error
from millefeuille.domain.writeback_core import _hash
from millefeuille.domain.writeback_http import _HTTPBoundary, _identity
from millefeuille.domain.writeback_live_files import decode


def project_collection(identity, raw, version):
    identity = _identity(identity)
    value = decode(raw)
    if type(version) is not int or not 1 <= version <= 9007199254740991:
        raise Error("collection version invalid")
    if not isinstance(value, dict) or not isinstance(value.get("data"), dict):
        raise Error("collection envelope required")
    data = value["data"]
    if (
        value.get("library") is None
        or value["library"].get("type") != identity["library_type"]
        or type(value["library"].get("id")) is not int
        or str(value["library"]["id"]) != identity["library_id"]
        or value.get("key") != identity["key"]
        or data.get("key") != identity["key"]
        or type(value.get("version")) is not int
        or type(data.get("version")) is not int
        or value["version"] != version
        or data["version"] != version
        or not isinstance(data.get("name"), str)
        or not data["name"]
        or len(data["name"]) > 256
        or (
            data.get("parentCollection") is not False
            and not isinstance(data.get("parentCollection"), str)
        )
    ):
        raise Error("collection identity/version/content invalid")
    if data["parentCollection"] is not False:
        _identity({**identity, "key": data["parentCollection"]})
    snapshot = {
        **identity,
        "version": version,
        "name": data["name"],
        "parentCollection": data["parentCollection"],
    }
    proof = {
        "schema": "millefeuille-collection-read/v1",
        "identity": identity,
        "status": 200,
        "header_version": version,
        "raw_utf8": raw.decode("utf-8"),
        "raw_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "snapshot_digest": _hash(snapshot),
    }
    return snapshot, proof


def checked_collection(observation, expected):
    if set(observation) != {"snapshot", "proof"}:
        raise Error("collection raw proof missing")
    proof = observation["proof"]
    raw = proof["raw_utf8"].encode("utf-8")
    if len(raw) > 65536:
        raise Error("collection proof too large")
    snapshot, derived = project_collection(
        proof["identity"], raw, proof["header_version"]
    )
    if derived != proof or snapshot != observation["snapshot"] or snapshot != expected:
        raise Error("collection mapping drift")
    return observation


class CollectionReader(_HTTPBoundary):
    def read_collection(self, identity, *, deadline):
        if self._reads:
            raise Error("collection read budget exhausted")
        self._reads += 1
        return self._call("GET-COLLECTION", identity, deadline)
