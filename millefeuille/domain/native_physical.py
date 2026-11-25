"""Ordinary pending-state fence; historical effect-budget activation is refused."""
from contextvars import ContextVar
from pathlib import Path
from millefeuille.domain import standalone_writeback as n
_PENDING = ContextVar("native_pending_physical_request", default=None)

def activate(path):
    from millefeuille.domain import request_recovery as recovery
    recovery._normal_stage_scope(recovery._root(Path(path)))
    n.require(_PENDING.get() is None or _PENDING.get().get("closed") is True)
    request = n.load(path)
    n.require("physical_attempt" not in request and "runtime_budget" not in request)


def pending_active():
    state = _PENDING.get()
    return state is not None and not state["closed"]
