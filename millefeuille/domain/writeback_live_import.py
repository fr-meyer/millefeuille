"""Attended custody import of already accepted exact materialization.

Source engineering only. No command automatically imports, accepts, or provisions.
The independent administrator receipt must already exist under fixed control root.
"""

import os
from pathlib import Path

from millefeuille.domain.millefeuille import MillefeuilleContractError as Error
from millefeuille.domain.writeback_core import _hash, _wire
from millefeuille.domain.writeback_live import load_evidence
from millefeuille.domain.writeback_live_broker import CONTROL
from millefeuille.domain.writeback_live_files import decode, protected_read
from millefeuille.domain.writeback_live_science import validate_acceptance

ATTESTATION = CONTROL / "science-attestation.json"


def import_attended(*, confirmed=False):
    """Authenticate existing original administrator config and science receipt.

    Input custody is root-verified by load_evidence; this never imports arbitrary
    operator paths. Artifacts must have been independently staged with immutable
    original raw-source proof. Existing attestation denies, including quarantine.
    """
    if os.geteuid() != 0 or confirmed is not True:
        raise Error("attended root import required")
    config_raw = protected_read(CONTROL / "config.json")
    config = decode(config_raw)
    plan = load_evidence(config)
    authenticated = decode(protected_read(CONTROL / "science-acceptance-receipt.json"))
    receipt_digest = validate_acceptance(
        plan, config["files"], authenticated, authenticated
    )
    attestation = {
        "schema": "millefeuille-writeback-science-custody/v1",
        "plan_digest": plan.digest,
        "files_digest": _hash(config["files"]),
        "science_receipt_digest": receipt_digest,
    }
    if (
        protected_read(CONTROL / "config.json") != config_raw
        or load_evidence(config) != plan
    ):
        raise Error("custody changed during import")
    # EXCL quarantine marker: any interruption is visible and cannot be auto-adopted.
    fd = os.open(
        ATTESTATION, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600
    )
    try:
        view = memoryview(_wire(attestation))
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError("attestation write failed")
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    parent = os.open(
        Path(ATTESTATION).parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    )
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    if decode(protected_read(ATTESTATION)) != attestation:
        raise Error("attestation readback mismatch")
    return attestation
