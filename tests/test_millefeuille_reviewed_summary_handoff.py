"""Original synthetic scientific-section packages only."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.reviewed_summary_handoff import (
    SCHEMA,
    retrieve_reviewed_summary_refs,
)
from tests.platform_capabilities import requires_pinned_root_reads


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


@requires_pinned_root_reads
class ReviewedHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.structure = {"sections": []}
        self.manifest = {
            "schema": SCHEMA,
            "paper_id": "synthetic-paper",
            "run_id": "review-1",
            "files": {},
            "attachments": [],
            "units": [],
        }
        self.provenance = {
            "kind": "current-assistant-review",
            "reviewer": "assistant",
            "review_id": "review-1",
            "original_generated_sha256": digest(b"historical"),
        }
        for key in ("main", "supplement"):
            source = f"source-{key}.md"
            (self.root / source).write_text("Synthetic scientific source " + key)
            self.manifest["attachments"].append(
                {"attachment_key": key, "source_ref": source, "local_page_count": 2}
            )
            for sid, heading in (
                ("methods", "Scientific methods"),
                ("results", "Experimental results"),
            ):
                ref = f"{key}-{sid}.md"
                (self.root / ref).write_text(f"Reviewed synthetic {key} {sid}.")
                self.structure["sections"].append(
                    {
                        "attachment_key": key,
                        "section_id": sid,
                        "heading": heading,
                        "local_pages": [1 if sid == "methods" else 2],
                    }
                )
                self.manifest["units"].append(
                    {"attachment_key": key, "section_id": sid, "summary_ref": ref}
                )
        self.save()

    def save(self):
        (self.root / "structure.json").write_text(json.dumps(self.structure))
        (self.root / "provenance.json").write_text(json.dumps(self.provenance))
        self.manifest["files"] = {
            p.name: digest(p.read_bytes())
            for p in self.root.iterdir()
            if p.name != "manifest.json"
        }
        self.pin()

    def pin(self):
        raw = json.dumps(self.manifest).encode()
        (self.root / "manifest.json").write_bytes(raw)
        self.expected = digest(raw)

    def retrieve(self, **kwargs):
        return retrieve_reviewed_summary_refs(
            package_root=self.root,
            expected_paper_id="synthetic-paper",
            expected_run_id="review-1",
            expected_manifest_sha256=self.expected,
            **kwargs,
        )

    def test_actual_public_refs_and_no_match(self):
        result = self.retrieve(attachment_key="supplement", local_page=1)
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["semantic_answer"], "unsupported")
        self.assertEqual(result["matches"][0]["summary_ref"], "supplement-methods.md")
        self.assertTrue((self.root / result["matches"][0]["summary_ref"]).is_file())
        self.assertEqual(len(self.retrieve()["matches"]), 4)
        self.assertEqual(self.retrieve(section="missing")["status"], "no-match")
        self.assertEqual(self.retrieve(attachment_key="absent")["matches"], [])

    def test_same_local_page_ambiguity(self):
        with self.assertRaisesRegex(MillefeuilleContractError, "ambiguous"):
            self.retrieve(local_page=1)

    def test_every_bound_payload_drift(self):
        for ref in list(self.manifest["files"]):
            with self.subTest(ref=ref):
                target = self.root / ref
                original = target.read_bytes()
                target.write_bytes(original + b"changed")
                with self.assertRaises(MillefeuilleContractError):
                    self.retrieve()
                target.write_bytes(original)

    def test_manifest_and_identity_drift(self):
        raw = (self.root / "manifest.json").read_bytes()
        (self.root / "manifest.json").write_bytes(raw + b" ")
        with self.assertRaisesRegex(MillefeuilleContractError, "manifest drift"):
            self.retrieve()
        self.manifest["run_id"] = "different"
        self.pin()
        with self.assertRaisesRegex(MillefeuilleContractError, "identity drift"):
            self.retrieve()

    def test_incomplete_and_duplicate_units(self):
        unit = self.manifest["units"].pop()
        self.pin()
        with self.assertRaisesRegex(MillefeuilleContractError, "incomplete"):
            self.retrieve()
        self.manifest["units"].append(unit)
        self.manifest["units"].append(unit)
        self.pin()
        with self.assertRaisesRegex(MillefeuilleContractError, "duplicate"):
            self.retrieve()

    def test_unknown_and_missing_entries(self):
        extra = self.root / "unknown"
        extra.write_text("unbound")
        with self.assertRaisesRegex(MillefeuilleContractError, "entries"):
            self.retrieve()
        extra.unlink()
        (self.root / "main-methods.md").unlink()
        with self.assertRaisesRegex(MillefeuilleContractError, "entries"):
            self.retrieve()

    def test_symlink_file_and_root(self):
        target = self.root / "main-methods.md"
        target.unlink()
        target.symlink_to(self.root / "main-results.md")
        with self.assertRaises(MillefeuilleContractError):
            self.retrieve()
        with tempfile.TemporaryDirectory() as outside:
            link = Path(outside) / "package"
            link.symlink_to(self.root, target_is_directory=True)
            with self.assertRaises(MillefeuilleContractError):
                retrieve_reviewed_summary_refs(
                    package_root=link,
                    expected_paper_id="synthetic-paper",
                    expected_run_id="review-1",
                    expected_manifest_sha256=self.expected,
                )

    def test_strict_schemas_and_unused_files(self):
        self.manifest["receipt"] = "not-a-GPT-receipt"
        self.pin()
        with self.assertRaisesRegex(MillefeuilleContractError, "schema"):
            self.retrieve()
        del self.manifest["receipt"]
        (self.root / "unused.md").write_text("unused")
        self.save()
        with self.assertRaisesRegex(MillefeuilleContractError, "unused"):
            self.retrieve()

    def test_distinct_provenance(self):
        self.provenance["kind"] = "published-gpt"
        self.save()
        with self.assertRaisesRegex(MillefeuilleContractError, "distinct"):
            self.retrieve()

    def test_unsafe_and_shared_refs(self):
        self.manifest["units"][0]["summary_ref"] = "../escape.md"
        self.pin()
        with self.assertRaises(MillefeuilleContractError):
            self.retrieve()
        self.manifest["units"][0]["summary_ref"] = "main-results.md"
        self.pin()
        with self.assertRaises(MillefeuilleContractError):
            self.retrieve()

    def test_duplicate_json_keys(self):
        raw = (self.root / "manifest.json").read_bytes()
        raw = b'{"schema":"duplicate",' + raw[1:]
        (self.root / "manifest.json").write_bytes(raw)
        self.expected = digest(raw)
        with self.assertRaisesRegex(MillefeuilleContractError, "duplicate JSON"):
            self.retrieve()

    def test_input_and_size_bounds(self):
        for page in (True, 0, 100001, "1"):
            with self.subTest(page=page), self.assertRaises(MillefeuilleContractError):
                self.retrieve(local_page=page)
        (self.root / "main-methods.md").write_bytes(b"x" * 1048577)
        self.save()
        with self.assertRaisesRegex(MillefeuilleContractError, "exceeds"):
            self.retrieve()

    def test_no_semantic_endpoint(self):
        with self.assertRaises(TypeError):
            self.retrieve(query="Explain the results")

    def test_structure_missing_and_duplicate_sections(self):
        original = list(self.structure["sections"])
        self.structure["sections"].pop()
        self.save()
        with self.assertRaises(MillefeuilleContractError):
            self.retrieve()
        self.structure["sections"] = original + [original[0]]
        self.save()
        with self.assertRaisesRegex(MillefeuilleContractError, "duplicate"):
            self.retrieve()

    def test_extra_fields_in_each_object(self):
        objects = [
            self.provenance,
            self.structure,
            self.structure["sections"][0],
            self.manifest["attachments"][0],
            self.manifest["units"][0],
        ]
        for obj in objects:
            with self.subTest(obj=obj):
                obj["unexpected"] = True
                self.save()
                with self.assertRaisesRegex(MillefeuilleContractError, "schema"):
                    self.retrieve()
                del obj["unexpected"]

    def test_no_writes_on_success_and_refusal(self):
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        self.retrieve()
        with self.assertRaises(MillefeuilleContractError):
            self.retrieve(local_page=1)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
