"""Explicit attended provisioning only. Never imported by service startup."""

import os

from millefeuille.domain.millefeuille import MillefeuilleContractError as Error
from millefeuille.domain.writeback_core import _wire
from millefeuille.domain.writeback_core_journal import _provision_journal
from millefeuille.domain.writeback_live_broker import COMMITMENT, JOURNAL
from millefeuille.domain.writeback_live_files import protected_read


def provision_attended(*, confirmed_new_namespace=False):
    """Called only by reviewed administrator ceremony, not operator CLI.

    Existing commitment OR ledger denies. Failure retains quarantine, no deletion,
    reset, adoption or re-provision. Administrator separately pins returned digest
    into config; service never derives the original commitment from current ledger.
    """
    if os.geteuid() != 0 or confirmed_new_namespace is not True:
        raise Error("explicit attended root provisioning required")
    # A sentinel proves the independently prepared control namespace is trusted.
    if (
        protected_read(COMMITMENT.parent / "provision-approved", limit=64)
        != b"new-writeback-journal-v1"
    ):
        raise Error("attended provisioning approval absent")
    fd = os.open(
        COMMITMENT, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    try:
        commitment = _provision_journal(JOURNAL)
        raw = _wire(commitment)
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("commitment write failed")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    directory = os.open(COMMITMENT.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    if protected_read(COMMITMENT) != raw:
        raise Error("commitment readback mismatch")
    import hashlib

    return "sha256:" + hashlib.sha256(raw).hexdigest()
