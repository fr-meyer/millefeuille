"""Contract tests for the acyclic paper-card/index lifecycle."""

from __future__ import annotations

import copy
import ctypes
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, ValidationError

from millefeuille.domain import index_fixtures
from millefeuille.domain.card_index_contract import (
    load_paper_card_artifact,
    validate_paper_card_identity,
)
from millefeuille.domain.millefeuille import (
    MillefeuilleContractError,
    PaperCardRecord,
)

SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "specs"
    / "millefeuille-pipeline"
    / "paper-card.schema.json"
)


def _common_payload() -> dict[str, object]:
    return {
        "paper_id": "zotero-ITEM1",
        "identity": {
            "title": "Fixture Paper",
            "source_hash": "sha256:" + ("a" * 64),
        },
        "one_line_thesis": "A concise thesis.",
        "primary_contribution": "A clear contribution.",
        "evidence_refs": ["../summaries/hierarchical-summary.json"],
        "model_provenance": {"profile_id": "fixture-card"},
    }


def _v01_payload() -> dict[str, object]:
    return {
        **_common_payload(),
        "schema_version": "millefeuille-paper-card/v0.1",
        "index_status": [{"lane": "openkb", "status": "skipped"}],
    }


def _v02_planned_payload() -> dict[str, object]:
    return {
        **_common_payload(),
        "schema_version": "millefeuille-paper-card/v0.2",
        "run_id": "run-fixture",
        "index_state": {
            "phase": "planned",
            "lanes": [
                {"lane": "openkb", "status": "pending"},
                {"lane": "pageindex", "status": "pending"},
            ],
        },
    }


class TestPaperCardIndexStateContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        cls.validator = Draft202012Validator(schema)

    def test_v01_remains_schema_and_runtime_compatible(self):
        payload = _v01_payload()

        self.validator.validate(payload)
        normalized = PaperCardRecord.from_dict(payload).to_dict()

        self.assertEqual(normalized, payload)
        self.assertNotIn("index_state", normalized)

        legacy_without_source = copy.deepcopy(payload)
        legacy_without_source["identity"]["source_hash"] = None
        validate_paper_card_identity(
            legacy_without_source,
            paper_id="zotero-ITEM1",
            run_id="any-run-is-legacy-compatible",
            source_hash="sha256:" + ("f" * 64),
        )

    def test_v02_planned_and_observed_states_match_schema_and_runtime(self):
        planned = _v02_planned_payload()
        observed = {
            **planned,
            "index_state": {
                "phase": "observed",
                "status_ref": "../index/index-status.json",
                "lanes": [
                    {"lane": "openkb", "status": "skipped"},
                    {"lane": "pageindex", "status": "previewed"},
                ],
            },
        }

        for payload in (planned, observed):
            with self.subTest(phase=payload["index_state"]["phase"]):
                self.validator.validate(payload)
                self.assertEqual(
                    PaperCardRecord.from_dict(payload).to_dict(),
                    payload,
                )

    def test_v02_cannot_embed_final_status_in_planned_state(self):
        payload = _v02_planned_payload()
        payload["index_state"]["lanes"][0]["status"] = "written"

        with self.assertRaises(ValidationError):
            self.validator.validate(payload)
        with self.assertRaisesRegex(
            MillefeuilleContractError,
            "planned status",
        ):
            PaperCardRecord.from_dict(payload)

    def test_v02_rejects_legacy_index_status(self):
        payload = {
            **_v02_planned_payload(),
            "index_status": [{"lane": "openkb", "status": "skipped"}],
        }

        with self.assertRaises(ValidationError):
            self.validator.validate(payload)
        with self.assertRaisesRegex(
            MillefeuilleContractError,
            "index_state instead of index_status",
        ):
            PaperCardRecord.from_dict(payload)

    def test_v02_rejects_unknown_top_level_and_nested_fields(self):
        for location in (
            "top-level",
            "identity",
            "model-provenance",
            "index-state",
        ):
            payload = copy.deepcopy(_v02_planned_payload())
            if location == "top-level":
                payload["unexpected"] = True
            elif location == "identity":
                payload["identity"]["unexpected"] = True
            elif location == "model-provenance":
                payload["model_provenance"]["unexpected"] = True
            else:
                payload["index_state"]["unexpected"] = True
            with self.subTest(location=location):
                with self.assertRaises(ValidationError):
                    self.validator.validate(payload)
                with self.assertRaises(MillefeuilleContractError):
                    PaperCardRecord.from_dict(payload)

    def test_v02_rejects_boundary_whitespace_without_normalizing(self):
        mutations = {
            "paper_id": lambda payload: payload.__setitem__(
                "paper_id", " zotero-ITEM1"
            ),
            "title": lambda payload: payload["identity"].__setitem__(
                "title", "Fixture Paper "
            ),
            "profile": lambda payload: payload["model_provenance"].__setitem__(
                "profile_id", " fixture-card"
            ),
            "evidence": lambda payload: payload["evidence_refs"].__setitem__(
                0, "../summaries/hierarchical-summary.json "
            ),
        }
        for field_name, mutate in mutations.items():
            payload = copy.deepcopy(_v02_planned_payload())
            mutate(payload)
            with self.subTest(field=field_name):
                with self.assertRaises(ValidationError):
                    self.validator.validate(payload)
                with self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "boundary whitespace",
                ):
                    PaperCardRecord.from_dict(payload)

    def test_v02_run_artifact_rejects_noncanonical_json_whitespace(self):
        with tempfile.TemporaryDirectory() as tempdir:
            card_path = Path(tempdir) / "paper-card.json"
            card_path.write_bytes(json.dumps(_v02_planned_payload()).encode("utf-8"))

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "canonical JSON bytes",
            ):
                load_paper_card_artifact(card_path)

    def test_transaction_capture_rejects_external_hardlink(self):
        with tempfile.TemporaryDirectory() as tempdir:
            card_path = Path(tempdir) / "displaced-card.json"
            hardlink_path = Path(tempdir) / "external-card-link.json"
            card_path.write_bytes(
                (
                    json.dumps(_v02_planned_payload(), indent=2, sort_keys=True) + "\n"
                ).encode("utf-8")
            )
            try:
                os.link(card_path, hardlink_path)
            except OSError as exc:
                self.skipTest(f"hardlink creation is unavailable: {exc}")

            with self.assertRaisesRegex(MillefeuilleContractError, "singly linked"):
                index_fixtures._capture_file_entry(
                    card_path,
                    "displaced paper card",
                )

    def test_v02_requires_exact_single_or_aggregate_source_hash(self):
        aggregate = copy.deepcopy(_v02_planned_payload())
        aggregate["identity"]["source_hash"] = "sha256-aggregate:" + ("b" * 64)

        self.validator.validate(aggregate)
        self.assertEqual(PaperCardRecord.from_dict(aggregate).to_dict(), aggregate)

        invalid_payloads = []
        missing = copy.deepcopy(_v02_planned_payload())
        missing["identity"].pop("source_hash")
        invalid_payloads.append(missing)
        malformed = copy.deepcopy(_v02_planned_payload())
        malformed["identity"]["source_hash"] = "sha256-aggregate:" + ("B" * 64)
        invalid_payloads.append(malformed)
        for payload in invalid_payloads:
            with self.subTest(source_hash=payload["identity"].get("source_hash")):
                with self.assertRaises(ValidationError):
                    self.validator.validate(payload)
                with self.assertRaises(MillefeuilleContractError):
                    PaperCardRecord.from_dict(payload)

    def test_schema_and_runtime_reject_duplicate_lane_names(self):
        planned = copy.deepcopy(_v02_planned_payload())
        planned["index_state"]["lanes"].append({"lane": "openkb", "status": "pending"})
        observed = copy.deepcopy(_v02_planned_payload())
        observed["index_state"] = {
            "phase": "observed",
            "status_ref": "../index/index-status.json",
            "lanes": [
                {"lane": "openkb", "status": "skipped"},
                {"lane": "pageindex", "status": "previewed"},
                {"lane": "openkb", "status": "written"},
            ],
        }

        for payload in (planned, observed):
            with self.subTest(phase=payload["index_state"]["phase"]):
                with self.assertRaises(ValidationError):
                    self.validator.validate(payload)
                with self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "lane values must be unique",
                ):
                    PaperCardRecord.from_dict(payload)

    def test_v02_direct_construction_enforces_nested_exactness(self):
        payload = _v02_planned_payload()
        base = {
            "paper_id": payload["paper_id"],
            "run_id": payload["run_id"],
            "identity": payload["identity"],
            "one_line_thesis": payload["one_line_thesis"],
            "primary_contribution": payload["primary_contribution"],
            "evidence_refs": payload["evidence_refs"],
            "index_state": payload["index_state"],
            "model_provenance": payload["model_provenance"],
            "schema_version": payload["schema_version"],
        }

        cases = (
            {**base, "identity": {**payload["identity"], "unexpected": True}},
            {
                **base,
                "model_provenance": {
                    **payload["model_provenance"],
                    "unexpected": True,
                },
            },
            {**base, "identity": ["not", "an", "object"]},
            {**base, "model_provenance": ["not", "an", "object"]},
        )
        for kwargs in cases:
            with (
                self.subTest(
                    identity_type=type(kwargs["identity"]).__name__,
                    provenance_type=type(kwargs["model_provenance"]).__name__,
                ),
                self.assertRaises(MillefeuilleContractError),
            ):
                PaperCardRecord(**kwargs)

    @unittest.skipUnless(os.name == "nt", "Windows reparse metadata regression")
    def test_card_index_parent_boundary_rejects_windows_reparse_directory(self):
        with patch(
            "millefeuille.domain.index_fixtures.os.lstat",
        ) as lstat_mock:
            directory_stat = SimpleNamespace(
                st_mode=0o040755,
                st_file_attributes=getattr(
                    index_fixtures.stat,
                    "FILE_ATTRIBUTE_REPARSE_POINT",
                    0x0400,
                ),
                st_reparse_tag=0,
            )
            lstat_mock.return_value = directory_stat
            with self.assertRaisesRegex(MillefeuilleContractError, "reparse points"):
                index_fixtures._require_real_directory(
                    Path("C:/synthetic/junction/cards"),
                    "paper card parent",
                )

    @unittest.skipUnless(os.name == "nt", "Windows reparse metadata regression")
    def test_windows_atomic_boundary_rejects_reparse_file_entry(self):
        reparse_file = SimpleNamespace(
            st_mode=0o100600,
            st_file_attributes=getattr(
                index_fixtures.stat,
                "FILE_ATTRIBUTE_REPARSE_POINT",
                0x0400,
            ),
            st_reparse_tag=0,
        )
        with (
            patch(
                "millefeuille.domain.index_fixtures.os.lstat",
                return_value=reparse_file,
            ),
            self.assertRaisesRegex(MillefeuilleContractError, "non-reparse"),
        ):
            index_fixtures._require_non_reparse_regular_entry(
                Path("C:/synthetic/card.json"),
                "paper card",
            )

    @unittest.skipUnless(os.name == "nt", "Windows LastError regression")
    def test_windows_atomic_replace_reports_captured_last_error(self):
        observed_last_error: list[int] = []

        class ReplaceFileFailure:
            argtypes = None
            restype = None

            def __call__(self, *_args):
                observed_last_error.append(ctypes.get_last_error())
                ctypes.set_last_error(1234)
                return 0

        class Kernel32Failure:
            ReplaceFileW = ReplaceFileFailure()

        with (
            patch.object(
                index_fixtures.ctypes,
                "WinDLL",
                return_value=Kernel32Failure(),
            ),
            self.assertRaisesRegex(MillefeuilleContractError, "Windows error 1234"),
        ):
            index_fixtures._windows_replace_file(
                Path("C:/synthetic/card.json"),
                Path("C:/synthetic/staged.json"),
                Path("C:/synthetic/backup.json"),
            )

        self.assertEqual(observed_last_error, [0])


if __name__ == "__main__":
    unittest.main()
