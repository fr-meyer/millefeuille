"""Ordinary native routing; historical budget and qualification routes are excluded."""
import os
from pathlib import Path
from contextvars import ContextVar
from millefeuille.domain import standalone_writeback as n
PROFILE = 'native-sdk-bridge/v1'
STRICT = 'strict-sdk/v1'
ENV = 'MILLEFEUILLE_EXECUTION_PROFILE'
_QUALIFICATION = ContextVar('native_sdk_explicit_qualification', default=None)

def selected():
    value = os.environ.get(ENV, STRICT)
    n.require(value in (STRICT, PROFILE))
    return value == PROFILE


def worker_argv():
    return ['node', str(Path(__file__).parents[1] / 'clients' / 'native_sdk_worker.mjs')]


def qualification_state():
    return _QUALIFICATION.get()
