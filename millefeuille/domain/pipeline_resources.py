"""20 GiB floor plus bounded admitted worker/revision headroom."""
import os, shutil
from pathlib import Path
from millefeuille.domain import standalone_writeback as n
from millefeuille.domain.pipeline_guards import check_pause, create_pause
FLOOR = 20 * 1024**3

class ResourceFloorError(RuntimeError):
    """Global operational stop before admitting another effect."""


def existing(path):
    path = Path(path)
    while not path.exists():
        path = path.parent
    return path


def check(output, store, workers=1, revisions=0, *, ignore_pause=False):
    n.require(type(workers) is int and 1 <= workers <= 4)
    n.require(type(revisions) is int and revisions >= 0)
    stop = Path(output) / "PAUSE"
    if not ignore_pause:
        check_pause(stop)
    reservations = [
        (output, workers * (1024**3 + revisions * 16 * 1024**2)),
        (store, workers * (revisions + 1) * 32 * 1024**2),
    ]
    devices = {}
    for path, size in reservations:
        ancestor = existing(path)
        device = os.stat(ancestor).st_dev
        if device not in devices:
            devices[device] = [ancestor, 0]
        devices[device][1] += size
    if any(
        shutil.disk_usage(path).free < FLOOR + size for path, size in devices.values()
    ):
        if Path(output).exists() and not stop.exists():
            create_pause(stop)
        raise ResourceFloorError("20 GiB free floor plus admitted headroom unavailable")


def admit(run, store):
    from millefeuille.domain.request_recovery import _normal_stage_scope, _root
    _normal_stage_scope(_root(Path(run)))
    output = run.parent.parent.parent
    policy_path = output / "PIPELINE-POLICY.json"
    policy = n.load(policy_path) if policy_path.exists() else {}
    n.require(not any(k in policy for k in ("runtime_budget", "successor", "run_authority")))
    check(
        output,
        store,
        policy.get("concurrency", 1),
        policy.get("max_revision_rounds", 0),
    )



def before_native_work(path, *, record=True):
    from millefeuille.domain.request_recovery import _normal_stage_scope, _root
    path = Path(path)
    _normal_stage_scope(_root(path))
    output = path
    while output.parent != output and output.name != "runs":
        output = output.parent
    output = output.parent.parent if output.name == "runs" else Path(path)
    policy_path = output / "PIPELINE-POLICY.json"
    policy = n.load(policy_path) if policy_path.exists() else {}
    n.require(not any(k in policy for k in ("runtime_budget", "successor", "run_authority")))
    check(output, output, policy.get("concurrency", 1), policy.get("max_revision_rounds", 0))
