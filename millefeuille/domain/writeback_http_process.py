"""Linux/main-thread request containment; no unbounded Popen context-manager wait."""

import ctypes
import json
import os
from pathlib import Path
import selectors
import signal
import struct
import subprocess
import sys
import threading
import time

from millefeuille.clients.openclaw_model_client import _kill_process_tree
from millefeuille.domain.writeback_core import _wire

_LOCK = threading.Lock()


def _worker_command():
    # Fixed trusted implementation, never selected by request/operator input.
    return [
        sys.executable,
        "-I",
        "-B",
        str(Path(__file__).with_name("writeback_http_worker.py")),
    ]


def _alive(group):
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    return True


def _contained_exchange(secret, method, identity, deadline, **kwargs):
    from millefeuille.domain.millefeuille import MillefeuilleContractError as Error
    from millefeuille.domain.writeback_http import (
        _Acknowledgement,
        _AmbiguousWriteError,
        _checked_observation,
        _DefinitiveRefusalError,
        _pairs,
    )

    if method not in ("GET", "GET-COLLECTION", "PATCH"):
        raise Error("contained HTTP operation unsupported")
    if (
        sys.platform != "linux"
        or threading.current_thread() is not threading.main_thread()
    ):
        raise Error("contained HTTP requires Linux main-thread supervisor")
    if signal.getsignal(signal.SIGCHLD) not in (signal.SIG_DFL, None):
        raise Error("external child reaper unsupported")
    remaining = deadline - time.monotonic()
    if remaining < 0.15:
        raise Error("insufficient contained HTTP cleanup budget")
    from millefeuille.domain import _failure_observations as observations

    observation = observations.token()
    stage = "setup"
    exit_confirmed = None
    frame_complete = None
    restoration_failed = False
    expired = None
    cause = "unknown"
    process = None
    primary = None
    result = None
    clean = False
    cleanup_failed = False
    old_handlers = {}
    cancelled = False
    refusal_seen = False
    libc = None
    previous = None
    subreaper = False
    selector = None
    output = bytearray()
    locked = False
    original_mask = None
    managed = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}

    def observe(reason):
        try:  # noqa: SIM105 - preserve explicit cancellation boundary
            observations.record(
                observation,
                boundary="supervisor",
                stage=stage,
                reason=reason,
                exception=primary,
                created=process is not None,
                exited=exit_confirmed,
                clean=clean,
                cancelled=cancelled,
                expired=expired,
                frame=frame_complete,
                restoration=restoration_failed,
                cause=cause,
                method=method,
            )
        except Exception:
            pass

    def cancel(_signum, _frame):
        nonlocal cancelled
        cancelled = True

    def drain_cancellation():
        nonlocal cancelled
        for _ in range(64):
            if not signal.sigpending().intersection(managed):
                break
            if signal.sigtimedwait(managed, 0) is not None:
                cancelled = True

    try:
        # Inherited exception-raising handlers cannot interrupt owned setup.
        original_mask = signal.pthread_sigmask(signal.SIG_BLOCK, managed)
        if not _LOCK.acquire(timeout=max(0, deadline - time.monotonic() - 0.1)):
            cause = "fault-0"
            raise Error("contained HTTP busy")
        locked = True
        for number in managed:
            old_handlers[number] = signal.getsignal(number)
            signal.signal(number, cancel)
        libc = ctypes.CDLL(None, use_errno=True)
        previous = ctypes.c_int()
        selector = selectors.DefaultSelector()
        if libc.prctl(37, ctypes.byref(previous), 0, 0, 0) != 0:
            cause = "fault-1"
            raise Error("subreaper inspection failed")
        if libc.prctl(36, 1, 0, 0, 0) != 0:
            cause = "fault-2"
            raise Error("subreaper containment unavailable")
        subreaper = True
        drain_cancellation()
        signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)
        if cancelled:
            raise TimeoutError()
        reserve = min(0.5, max(0.1, (deadline - time.monotonic()) / 4))
        operation_end = deadline - reserve
        if time.monotonic() >= operation_end:
            raise TimeoutError()
        body = kwargs.get("body")
        payload = _wire(
            {
                "method": method,
                "identity": identity,
                "deadline": operation_end,
                "secret": secret,
                "body": None if body is None else body.decode("utf-8"),
                "expected_version": kwargs.get("expected_version"),
            }
        )
        if len(payload) > 131072:
            cause = "fault-3"
            raise Error("worker input cap")
        pending = memoryview(struct.pack("!I", len(payload)) + payload)
        # No secret in arguments/environment, no temp files or stderr capture.
        stage = "spawn"
        process = subprocess.Popen(
            _worker_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            env={"PATH": os.defpath, "LANG": "C.UTF-8"},
        )
        if time.monotonic() >= operation_end:
            raise TimeoutError()
        assert process.stdin is not None and process.stdout is not None
        for stream in (process.stdin, process.stdout):
            os.set_blocking(stream.fileno(), False)
        stage = "pipes"
        selector.register(process.stdin, selectors.EVENT_WRITE)
        selector.register(process.stdout, selectors.EVENT_READ)
        expected = None
        while selector.get_map():
            left = operation_end - time.monotonic()
            if left <= 0 or cancelled:
                raise TimeoutError()
            for key, _ in selector.select(min(left, 0.05)):
                stream = key.fileobj
                if stream is process.stdin:
                    count = os.write(stream.fileno(), pending)
                    pending = pending[count:]
                    if not pending:
                        selector.unregister(stream)
                        stream.close()
                else:
                    chunk = os.read(stream.fileno(), 65536)
                    if not chunk:
                        selector.unregister(stream)
                        stream.close()
                        continue
                    if len(output) + len(chunk) > 200004:
                        cause = "fault-4"
                        raise Error("worker output cap")
                    output.extend(chunk)
                    if len(output) >= 4 and expected is None:
                        expected = struct.unpack("!I", output[:4])[0]
                        if expected > 200000:
                            cause = "fault-5"
                            raise Error("worker output frame cap")
                    if expected is not None and len(output) > expected + 4:
                        cause = "fault-6"
                        raise Error("worker trailing output")
                    if expected is not None and len(output) == expected + 4:
                        frame_complete = True
                        if secret.encode() in output:
                            cause = "fault-7"
                            raise Error("worker credential echo denied")
                        refusal_seen = json.loads(
                            output[4:], object_pairs_hook=_pairs
                        ) == {"kind": "refused"}
        if len(output) < 4 or len(output) != 4 + expected:
            cause = "fault-8"
            raise Error("worker incomplete output")
        stage = "decode"
        response = json.loads(output[4:], object_pairs_hook=_pairs)
        if response == {"kind": "refused"}:
            raise _DefinitiveRefusalError("remote mutation refused")
        if set(response) != {"kind", "result"} or response["kind"] != "ok":
            cause = "fault-9"
            raise Error("worker unconfirmed")
        result = response["result"]
        # The worker must exit successfully, not merely emit a premature result.
        # Observe exit WITHOUT reaping: the reserved leader PID prevents group
        # identity reuse before the finally block terminates owned descendants.
        stage = "exit"
        while True:
            if time.monotonic() >= operation_end or cancelled:
                raise TimeoutError()
            status = os.waitid(
                os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT
            )
            if status is not None:
                if status.si_code != os.CLD_EXITED or status.si_status != 0:
                    cause = "fault-10"
                    raise Error("worker exit unconfirmed")
                exit_confirmed = True
                break
            time.sleep(min(0.005, max(0, operation_end - time.monotonic())))
    except BaseException as exc:
        primary = exc
    finally:
        # Scoped latches cover entry; block delivery throughout restoration.
        if original_mask is not None:
            try:
                signal.pthread_sigmask(signal.SIG_BLOCK, managed)
            except BaseException:
                cleanup_failed = True
        if selector is not None:
            for _ in range(2):
                try:
                    selector.close()
                    break
                except BaseException:
                    cleanup_failed = True
        if process is not None:
            for stream in (process.stdin, process.stdout):
                try:
                    if stream is not None:
                        stream.close()
                except BaseException:
                    cleanup_failed = True
            killed = False
            # Retry only interrupted cleanup while leader identity is reserved.
            for _ in range(8):
                try:
                    _kill_process_tree(process)
                    killed = True
                    break
                except BaseException:
                    cancelled = True
                if time.monotonic() >= deadline:
                    break
            if killed:
                while time.monotonic() < deadline:
                    try:
                        process.poll()
                        while True:
                            try:
                                pid, _ = os.waitpid(-process.pid, os.WNOHANG)
                            except ChildProcessError:
                                break
                            if pid == 0:
                                break
                        if process.returncode is not None and not _alive(process.pid):
                            clean = True
                            break
                    except BaseException:
                        cancelled = True
                    time.sleep(min(0.005, max(0, deadline - time.monotonic())))
        else:
            clean = True
        if subreaper:
            restored = False
            for _ in range(2):
                try:
                    if libc.prctl(36, previous.value, 0, 0, 0) == 0:
                        restored = True
                        break
                except BaseException:
                    cancelled = True
            if not restored:
                restoration_failed = True
                clean = False
        if locked:
            _LOCK.release()
            locked = False
        for number, handler in old_handlers.items():
            restored = False
            for _ in range(2):
                try:
                    signal.signal(number, handler)
                    restored = True
                    break
                except BaseException:
                    cancelled = True
            if not restored:
                restoration_failed = True
                clean = False
        output.clear()
        clean = clean and not cleanup_failed
        if original_mask is not None:
            try:
                drain_cancellation()
            except BaseException:
                clean = False
            try:
                signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)
            except BaseException:
                cancelled = True
    if refusal_seen or isinstance(primary, _DefinitiveRefusalError):
        observe("refused")
        raise _DefinitiveRefusalError("remote mutation refused") from None
    if (
        primary is not None
        or not clean
        or cancelled
        or (expired := time.monotonic() >= deadline)
    ):
        observe("unconfirmed")
        result = None
        if method == "PATCH":
            raise _AmbiguousWriteError("contained mutation unconfirmed") from None
        raise Error("contained read unconfirmed") from None
    if method in ("GET", "GET-COLLECTION"):
        stage = "proof"
        try:
            if method == "GET":
                _checked_observation(result)
            else:
                from millefeuille.domain.writeback_live_collection import (
                    checked_collection,
                )

                checked_collection(result, result["snapshot"])
                if result["proof"]["identity"] != identity:
                    raise Error("contained collection identity mismatch")
        except (ValueError, TypeError, KeyError, AttributeError):
            observe("proof")
            raise Error("contained read proof invalid") from None
        if time.monotonic() >= deadline:
            expired = True
            observe("proof-deadline")
            result = None
            raise Error("contained proof deadline expired")
        return result
    stage = "ack"
    if (
        set(result) != {"version", "status"}
        or result["status"] != 204
        or type(result["version"]) is not int
        or result["version"] <= kwargs["expected_version"]
    ):
        observe("ack")
        raise _AmbiguousWriteError("contained acknowledgement invalid")
    if time.monotonic() >= deadline:
        expired = True
        observe("ack-deadline")
        raise _AmbiguousWriteError("contained acknowledgement deadline expired")
    return _Acknowledgement(result["version"], result["status"])
