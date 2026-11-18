"""Private trusted-runner diagnostics; inert unless explicitly activated.

No callbacks, I/O, environment switches, dynamic exception rendering or locks.
Tokens bind late broker-thread results to their originating test generation.
"""

from collections import deque

_state = None
_states = []  # At most three live rings, eight records each.
_owners = []  # At most two registered runner owners.
_blocked = False
_suspension = None
_NO_PRIOR = object()
_STAGES = frozenset(
    {
        "setup",
        "spawn",
        "pipes",
        "decode",
        "exit",
        "proof",
        "ack",
        "request",
        "evidence",
        "journal",
        "authority",
        "preparation",
        "execute",
        "readback",
    }
)
_REASONS = frozenset(
    {
        "unconfirmed",
        "refused",
        "proof",
        "proof-deadline",
        "ack",
        "ack-deadline",
        "broker-denial",
    }
)
_CATEGORIES = {
    TimeoutError: "timeout",
    ValueError: "value",
    TypeError: "type",
    KeyError: "key",
    OSError: "os",
    RuntimeError: "runtime",
    KeyboardInterrupt: "interrupt",
    SystemExit: "exit",
}


def _protected(state):
    return any(state is owner["prior"] or state is owner["state"] for owner in _owners)


def _retire(state):
    if state is None or _protected(state):
        return
    for index, live in enumerate(_states):
        if live is state:
            del _states[index]
            state["records"] = deque(maxlen=8)
            state["dropped"] = 0
            return


def _fresh():
    if len(_states) >= 3:
        return None
    state = {"records": deque(maxlen=8), "dropped": 0}
    _states.append(state)
    return state


def activate():
    global _state
    if _blocked or _suspension is not None:
        return
    _retire(_state)
    _state = _fresh()


def deactivate():
    global _state
    if _suspension is not None:
        return
    current = _state
    _state = None
    _retire(current)


def acquire():
    """Bounded runner ownership; overflow suppresses until registered owner exits."""
    global _state, _blocked
    if _suspension is not None:
        return None
    if _blocked or len(_owners) >= 2 or len(_states) >= 4:
        _blocked = True
        _state = None
        return None
    prior = _state
    state_count, owner_count = len(_states), len(_owners)
    try:
        state = _fresh()
        owner = {"prior": prior, "state": state}
        _owners.append(owner)
        _state = state
        return owner
    except BaseException:
        # Roll back only this acquisition; keep the original failure unchanged.
        del _owners[owner_count:]
        del _states[state_count:]
        _state = prior
        raise


def release(owner):
    global _state, _blocked
    if owner is None or not _owners or _owners[-1] is not owner:
        return
    current = _state
    _owners.pop()
    _state = owner["prior"]
    _blocked = False
    _retire(current)
    _retire(owner["state"])
    owner["prior"] = None
    owner["state"] = None


def drain_owned(owner):
    if owner is None or not _owners or _owners[-1] is not owner:
        return None
    return _drain(owner["state"])


def suspend():
    """No allocation/ring/owner borrowing if runner acquisition raises."""
    global _state, _suspension
    if _suspension is not None:
        return None
    prior = _state if _state is not None else _NO_PRIOR
    _suspension = prior
    _state = None
    return prior


def resume(ticket):
    global _state, _suspension
    if ticket is None or ticket is not _suspension:
        return
    _state = None if ticket is _NO_PRIOR else ticket
    _suspension = None


def token():
    return _state


def record(
    origin,
    *,
    boundary,
    stage,
    reason,
    exception=None,
    created=None,
    exited=None,
    clean=None,
    cancelled=None,
    expired=None,
    frame=None,
    restoration=None,
    consumption=None,
    cause="unknown",
    method=None,
):
    try:
        if origin is None or origin is not _state:
            return
        if type(boundary) is not str or boundary not in ("supervisor", "broker"):
            return
        if type(stage) is not str or len(stage) > 24 or stage not in _STAGES:
            return
        if type(reason) is not str or len(reason) > 24 or reason not in _REASONS:
            return
        if type(cause) is not str or cause not in {
            "unknown",
            "fault-0",
            "fault-1",
            "fault-2",
            "fault-3",
            "fault-4",
            "fault-5",
            "fault-6",
            "fault-7",
            "fault-8",
            "fault-9",
            "fault-10",
        }:
            return
        if method is not None and (
            type(method) is not str or method not in ("GET", "GET-COLLECTION", "PATCH")
        ):
            return
        values = (
            created,
            exited,
            clean,
            cancelled,
            expired,
            frame,
            restoration,
            consumption,
        )
        if any(value is not None and type(value) is not bool for value in values):
            return
        # Exact built-in class identity only. Never inspect tainted object text.
        category = next(
            (label for cls, label in _CATEGORIES.items() if type(exception) is cls),
            "none" if exception is None else "other",
        )
        row = {
            "boundary": boundary,
            "stage": stage,
            "reason": reason,
            "category": category,
            "created": created,
            "exited": exited,
            "clean": clean,
            "cancelled": cancelled,
            "expired": expired,
            "frame": frame,
            "restoration": restoration,
            "consumption": consumption,
            "cause": cause,
            "method": method,
        }
        if len(origin["records"]) == 8:
            origin["dropped"] = min(65535, origin["dropped"] + 1)
        origin["records"].append(row)
    except Exception:
        pass  # Telemetry never overrides an ordinary application failure.


def drain():
    return _drain(_state)


def _drain(state):
    if state is None:
        return None
    records = list(state["records"])
    state["records"].clear()
    dropped = state["dropped"]
    state["dropped"] = 0
    return (
        {
            "schema": "private-failure-observation/v1",
            "records": records,
            "dropped": dropped,
        }
        if records or dropped
        else None
    )
