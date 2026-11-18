"""Canonical stage selection; all source evidence remains verified."""

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_execution import SUMMARY_MODEL_STAGES


def validate_summary_stages(value):
    if value is None:
        return tuple(SUMMARY_MODEL_STAGES)
    if (
        not isinstance(value, (list, tuple))
        or not value
        or any(
            not isinstance(stage, str) or stage not in SUMMARY_MODEL_STAGES
            for stage in value
        )
        or len(set(value)) != len(value)
    ):
        raise MillefeuilleContractError("invalid summary stage selection")
    canonical = tuple(stage for stage in SUMMARY_MODEL_STAGES if stage in value)
    if tuple(value) != canonical:
        raise MillefeuilleContractError("summary stages must use canonical order")
    return canonical


def selected_summary_stages(package):
    if "requested_stages" not in package:
        return tuple(SUMMARY_MODEL_STAGES)
    if package["requested_stages"] is None:
        raise MillefeuilleContractError("null summary stage selection")
    return validate_summary_stages(package["requested_stages"])
