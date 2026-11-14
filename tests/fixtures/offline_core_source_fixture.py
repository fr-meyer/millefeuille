"""Provider-free synthetic native-v0.2 fixture. No private/scientific authority.

Preparation is separate from measured publication workers. The structural check
is explicitly native-custody-only, NOT full published-summary/platform approval.
"""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil

from millefeuille.domain.acceptance import _build_acceptance_summary
from millefeuille.domain.evidence_answer import answer_evidence, canonical, seal
from millefeuille.domain.evidence_source_adapter import (
    ReadBudget,
    ScopedReadOnlyReader,
    saved_identity,
)
from millefeuille.domain.millefeuille import AcceptanceCheckRecord
from millefeuille.domain.multi_source_upstream import (
    MultiSourceNativeInput,
    plan_multi_source_native_upstream,
)
from millefeuille.domain.semantic_acceptance import ROLES, digest
from millefeuille.domain.semantic_materials import build_material_binding
from millefeuille.domain.semantic_scenario_evaluator import evaluate_frozen_scenarios
from millefeuille.domain.semantic_source_join import (
    _derive_canonical_source_join,
    build_canonical_source_join,
)
from millefeuille.domain.source_packs import (
    RecoveredPdfEvidence,
    write_source_pack_from_recovered_pdfs,
)
from millefeuille.domain.source_scope import MultiSourceScope
from millefeuille.domain.stage_runtime import resolve_run_artifacts
from tests.test_evidence_answer import request
from tests.test_millefeuille_stage_cli import PAPER_ID, RUN_ID, _prepare_fixture_run


def budget():
    return ReadBudget(max_opens=16384, max_scans=16384)


def prepare(root, value="12"):
    root = Path(root).absolute()
    old = root / "old-fixture"
    old.mkdir()
    old_pack_root, old_run = _prepare_fixture_run(str(old))
    old_resolved = resolve_run_artifacts(
        source_pack_root=old_pack_root, item_key="ITEM1", run_id=RUN_ID
    )
    old_summary = _build_acceptance_summary(
        resolved=old_resolved, handoff_path=None, duplicate_scan_path=None
    )
    records, inputs = [], []
    for key, label, text, pages in [
        (
            "MAIN1234",
            "Main",
            (
                "## Page 1\r\n"
                "\r\n"
                "## Method\r\n"
                "Résumé optics use calibrated samples.\r\n"
                "### Procedure\r\n"
                "First rinse, then heat.\r\n"
                "## Page 2\r\n"
                "### Results\r\n"
                "Parent continuation is repeatable.\r\n"
                "| condition | latency |\r\n"
                "| --- | --- |\r\n"
                "| dry | "
            )
            + value
            + " ms under dry conditions |\r\n",
            2,
        ),
        (
            "SUPP5678",
            "Supplement",
            (
                "## Page 1\n"
                "\n"
                "## Interpretation\n"
                "Supplement optics differ under wet conditions.\n"
                "### Procedure\n"
                "First compare, then report.\n"
                "### Context\n"
                "Side-by-side comparison is descriptive only."
            ),
            1,
        ),
    ]:
        pdf = root / (key + ".pdf")
        pdf.write_bytes(("synthetic PDF " + key).encode())
        text_path = root / (key + ".md")
        text_path.write_bytes(text.encode())
        records.append(
            RecoveredPdfEvidence(
                item_key="ITEM1",
                attachment_key=key,
                canonical_filename=label + ".pdf",
                recovered_pdf_path=pdf,
                expected_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
                item_title="Synthetic optics",
                zotero_version=1,
            )
        )
        inputs.append(
            MultiSourceNativeInput(
                key, text_path, hashlib.sha256(text.encode()).hexdigest(), pages, label
            )
        )
    pack_root = root / "source-packs"
    packed = write_source_pack_from_recovered_pdfs(
        evidence_records=records,
        source_pack_root=pack_root,
        created_at="2026-11-13T02:04:00+00:00",
    )
    pack = packed.source_pack_dir
    upstream = plan_multi_source_native_upstream(
        source_pack_root=pack_root,
        item_key="ITEM1",
        paper_id=PAPER_ID,
        scope=MultiSourceScope.from_dict(
            {
                "schema_version": "millefeuille-source-scope/v0.1",
                "source_hash": packed.manifest["source_hash"],
                "sources": [
                    {
                        "attachment_key": v["identity"]["zotero_attachment_key"],
                        "canonical_filename": v["identity"]["canonical_filename"],
                        "sha256": v["sha256"],
                        "source_ref": v["ref"],
                        "zotero_version": v["identity"]["zotero_version"],
                    }
                    for v in packed.manifest["sources"]
                ],
            }
        ),
        inputs=tuple(inputs),
    )
    for output in upstream.outputs:
        target = pack / output.ref
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(output.content)
    run = pack / "analyses/millefeuille" / RUN_ID
    shutil.copytree(old_run, run)
    # Carry existing valid artifact schemas into this exact synthetic source run.
    for path in run.rglob("*.json"):
        text = path.read_text()
        text = text.replace(old_resolved.source_hash, packed.manifest["source_hash"])
        text = text.replace(str(old_pack_root), str(pack_root))
        path.write_text(text)
    native_payload = json.loads(
        (pack / "extractions/native/evidence.json").read_bytes()
    )
    index_path = run / "artifact-index.json"
    index_payload = json.loads(index_path.read_bytes())
    index_payload["artifacts"] = {
        k: v
        for k, v in index_payload["artifacts"].items()
        if v.get("stage") != "extract-ocr"
    }
    old_ref = index_payload["artifacts"]["native_extraction_markdown"]["ref"]
    index_payload["artifacts"]["native_extraction_markdown"]["ref"] = (
        old_ref.split("extractions/native/")[0]
        + native_payload["sources"][0]["native_markdown_ref"]
    )
    index_payload["stages"]["extract-ocr"]["status"] = "skipped"
    index_path.write_bytes(canonical(index_payload))
    stage_path = run / "stage-manifest.json"
    stage_payload = json.loads(stage_path.read_bytes())
    for stage in stage_payload["stages"]:
        if stage.get("name") == "extract-ocr":
            stage["status"] = "skipped"
            stage["inputs"], stage["outputs"] = [], []
            stage["notes"] = ["Unselected OCR remains unqualified."]
    for stage in stage_payload["stages"]:
        if stage.get("name") == "extract-native":
            stage["notes"] = [
                "Synthetic canonical native-text custody; no decoder executed."
            ]
    stage_path.write_bytes(canonical(stage_payload))
    structure = json.loads((pack / "structure/structure.json").read_bytes())[
        "structure"
    ]
    section_locators = [
        "section:" + sec["id"]
        for sec in structure["sections"]
        if sec["title"]
        in {"Method", "Procedure", "Results", "Interpretation", "Context"}
    ]
    for text_file in (run / "summaries/texts").glob("*.md"):
        text_file.write_text(
            "Résumé optics use calibrated samples.\nFirst rinse, then heat.\n"
            + value
            + (
                " ms under dry conditions\n"
                "Supplement optics differ under wet conditions.\n"
            )
        )
    hierarchy_path = run / "summaries/hierarchical-summary.json"
    hierarchy = json.loads(hierarchy_path.read_bytes())
    for entry in hierarchy["summaries"]:
        entry["source_locators"] = section_locators
    hierarchy_path.write_bytes(canonical(hierarchy))
    card_path = run / "cards/paper-card.json"
    card = json.loads(card_path.read_bytes())
    card["identity"].update(title="Synthetic optics", authors=["Synthetic author"])
    card.update(
        one_line_thesis="Résumé optics use calibrated samples.",
        primary_contribution="Parent continuation is repeatable.",
        problem_addressed="Synthetic byte-custody fixture only.",
        method_or_approach="First rinse, then heat.",
        main_results=value + " ms under dry conditions",
        limitations="Side-by-side comparison is descriptive only.",
        classification_clues=["optics"],
    )
    card_path.write_bytes(canonical(card))
    (run / "cards/paper-card.md").write_text(
        "# Synthetic optics\n\n"
        + "\n".join(
            card[k]
            for k in [
                "one_line_thesis",
                "primary_contribution",
                "problem_addressed",
                "method_or_approach",
                "main_results",
                "limitations",
            ]
        )
        + "\n"
    )
    resolved = resolve_run_artifacts(
        source_pack_root=pack_root, item_key="ITEM1", run_id=RUN_ID
    )
    summary = replace(
        old_summary,
        status="pass",
        source_hash=resolved.source_hash,
        checks=[
            AcceptanceCheckRecord(
                name="canonical-native-v02-custody-only",
                status="passed",
                refs=[
                    "source-pack:selected/route.json",
                    "source-pack:structure/structure.json",
                ],
                notes=[
                    (
                        "Only canonical source, route and hierarchical "
                        "structure custody. No published GPT "
                        "summary/provider/native decoder/platform "
                        "qualification."
                    )
                ],
            )
        ],
        counts={"synthetic_attachments": 2, "synthetic_pages": 3},
    )
    provisional = _derive_canonical_source_join(
        resolved=resolved, reviewed_package=None, budget=budget()
    )
    package_root = root / "reviewed"
    package_root.mkdir()
    files, package_attachments, sections, units = {}, [], [], []

    def put(ref, data):
        payload = data if isinstance(data, bytes) else canonical(data)
        (package_root / ref).write_bytes(payload)
        files[ref] = digest(payload)

    for a in provisional.attachments:
        ref = a.attachment_key + ".pdf"
        put(ref, (pack / a.source_ref).read_bytes())
        package_attachments.append(
            {
                "attachment_key": a.attachment_key,
                "source_ref": ref,
                "local_page_count": len(a.pages),
            }
        )
        for s in a.sections:
            sections.append(
                {
                    "section_id": s.canonical_section_id,
                    "heading": s.heading,
                    "attachment_key": a.attachment_key,
                    "local_pages": [p.local_page for p in s.page_ranges],
                }
            )
            ref = a.attachment_key + "-" + s.canonical_section_id + ".txt"
            put(ref, ("Synthetic reviewed " + s.heading).encode())
            units.append(
                {
                    "section_id": s.canonical_section_id,
                    "attachment_key": a.attachment_key,
                    "summary_ref": ref,
                }
            )
    put("structure.json", {"sections": sections})
    put(
        "provenance.json",
        {
            "kind": "current-assistant-review",
            "reviewer": "synthetic-controller",
            "review_id": "offline-core-1",
            "original_generated_sha256": digest(b"synthetic generated predecessor"),
        },
    )
    manifest = {
        "schema": "millefeuille-reviewed-sections/v1",
        "paper_id": resolved.paper_id,
        "run_id": resolved.run_id,
        "files": files,
        "attachments": package_attachments,
        "units": units,
    }
    (package_root / "manifest.json").write_bytes(canonical(manifest))
    package = {
        "package_root": str(package_root),
        "manifest_sha256": digest(canonical(manifest)),
    }
    join = build_canonical_source_join(
        resolved=resolved, reviewed_package=package, budget=budget()
    )
    spans, source_spans = [], {}
    definitions = [
        (
            "method",
            "MAIN1234",
            "Method",
            1,
            "prose",
            "Résumé optics use calibrated samples.",
        ),
        (
            "procedure",
            "MAIN1234",
            "Procedure",
            1,
            "procedure",
            "First rinse, then heat.",
        ),
        (
            "parent",
            "MAIN1234",
            "Method",
            2,
            "prose",
            "Parent continuation is repeatable.",
        ),
        (
            "child",
            "MAIN1234",
            "Results",
            2,
            "prose",
            "Parent continuation is repeatable.",
        ),
        (
            "table",
            "MAIN1234",
            "Results",
            2,
            "table",
            value + " ms under dry conditions",
        ),
        (
            "interpretation",
            "SUPP5678",
            "Interpretation",
            1,
            "prose",
            "Supplement optics differ under wet conditions.",
        ),
        (
            "context",
            "SUPP5678",
            "Context",
            1,
            "prose",
            "Side-by-side comparison is descriptive only.",
        ),
    ]
    definitions.append(
        (
            "supplement-procedure",
            "SUPP5678",
            "Procedure",
            1,
            "procedure",
            "First compare, then report.",
        )
    )
    for sid, key, heading, page, kind, text in definitions:
        a = next(a for a in join.attachments if a.attachment_key == key)
        sec = next(s for s in a.sections if s.heading == heading)
        raw = (pack / a.extraction_ref).read_bytes()
        start = raw.index(text.encode())
        span = {
            "span_id": sid,
            "attachment_key": key,
            "page": page,
            "section_id": sec.canonical_section_id,
            "kind": kind,
            "source_ref": a.extraction_ref,
            "source_sha256": a.source_sha256,
            "byte_start": start,
            "byte_end": start + len(text.encode()),
            "text_sha256": digest(text.encode()),
            "table_coordinates": {
                "table_id": "T1",
                "row_label": "dry",
                "column_label": "latency",
                "unit_literal": "ms",
            }
            if kind == "table"
            else None,
        }
        spans.append(span)
        excerpt = {
            k: span[k]
            for k in [
                "span_id",
                "attachment_key",
                "page",
                "section_id",
                "source_sha256",
                "byte_start",
                "byte_end",
                "text_sha256",
            ]
        }
        excerpt.update(extraction_sha256=a.extraction_sha256, text_utf8=text)
        source_spans[sid] = excerpt
    corpus = {
        "schema_version": "millefeuille-evidence-corpus/v1",
        "paper_id": resolved.paper_id,
        "run_id": resolved.run_id,
        "attachments": [
            {
                "key": a.attachment_key,
                "source_sha256": a.source_sha256,
                "extraction_sha256": a.extraction_sha256,
                "pages": [p.local_page for p in a.pages],
            }
            for a in join.attachments
        ],
        "spans": spans,
        "completeness": {
            "authorized_scope_sha256": digest(canonical(join.to_dict())),
            "expected_spans": len(spans),
            "expected_source_bytes": sum(
                s["byte_end"] - s["byte_start"] for s in spans
            ),
            "all_selected_attachments_inventory_complete": True,
        },
    }
    corpus["manifest_sha256"] = seal(corpus, "manifest_sha256")
    reader_files = {
        a.extraction_ref: {
            "identity": saved_identity(pack / a.extraction_ref),
            "sha256": a.extraction_sha256,
        }
        for a in join.attachments
    }
    ancestors = {
        str(p): saved_identity(p) for ref in reader_files for p in (pack / ref).parents
    }
    reader = ScopedReadOnlyReader(
        root=pack, files=reader_files, ancestors=ancestors, reviewed_package=package
    )
    questions = []
    for terms, kind, selection, intent, relation in [
        (["calibrated"], "prose", {}, "explain", "none"),
        (
            ["compare"],
            "procedure",
            {"attachment_keys": ["SUPP5678"]},
            "procedure",
            "none",
        ),
        ([value, "dry"], "table", {}, "numeric", "none"),
        (["optics"], "prose", {}, "compare", "side_by_side_only"),
        ([], "any", {"attachment_keys": ["MAIN1234"], "local_page": 2}, "page", "none"),
        ([], "any", {"attachment_keys": ["SUPP5678"], "local_page": 1}, "page", "none"),
        ([], "any", {"local_page": 1}, "page", "none"),
        (["unsupported-claim"], "any", {}, "explain", "none"),
    ]:
        q = request(
            corpus,
            paper_id=resolved.paper_id,
            run_id=resolved.run_id,
            terms=terms,
            evidence_kind=kind,
            intent=intent,
            relation=relation,
        )
        q["selection"].update(selection)
        if intent == "numeric":
            q["table_selector"] = spans[4]["table_coordinates"]
        if intent == "compare":
            q["selection"]["attachment_keys"] = ["MAIN1234", "SUPP5678"]
        questions.append(q)
    # Freeze exact expectations from source definitions BEFORE generating answers.
    expected_ids = [
        ["method"],
        ["supplement-procedure"],
        ["table"],
        ["method", "interpretation"],
        ["child", "parent", "table"],
        ["context", "interpretation", "supplement-procedure"],
        [],
        [],
    ]
    gold = []
    for i, (q, ids) in enumerate(zip(questions, expected_ids, strict=True)):
        g = {
            "id": str(i),
            "query_sha256": digest(canonical(q)),
            "expected_locators": [
                [
                    source_spans[sid][k]
                    for k in ["attachment_key", "page", "section_id", "text_sha256"]
                ]
                for sid in ids
            ],
            "expected_verdict": "supported"
            if i < 6
            else "ambiguous"
            if i == 6
            else "no_evidence",
            "required_literals": [source_spans[sid]["text_utf8"] for sid in ids],
        }
        if i == 6:
            g["ambiguity"] = {
                "page": 1,
                "attachment_keys": ["MAIN1234", "SUPP5678"],
                "required_selector": "attachment_keys",
            }
        if i == 7:
            # A separate synthetic source assessment, not inferred from zero hits.
            g["independent_scientific_absence_reviewed"] = True
        gold.append(g)
    source_review = {
        "paper_id": join.paper_id,
        "run_id": join.run_id,
        "source_hash": join.source_hash,
        "material_binding": build_material_binding(resolved, budget=budget()),
        "reviewed_package": package,
        "canonical_join": join.to_dict(),
        "corpus": corpus,
        "corpus_sha256": corpus["manifest_sha256"],
        "source_spans": source_spans,
        "frozen_gold_sha256": digest(canonical(gold)),
    }
    claims = [
        {
            "claim_id": sid,
            "claim_text": s["text_utf8"],
            "claim_text_sha256": digest(s["text_utf8"].encode()),
            "verdict": "supported",
            "source_locators": [
                {
                    "attachment_key": s["attachment_key"],
                    "sha256": s["source_sha256"],
                    "page": s["page"],
                    "section": s["section_id"],
                    "span_id": sid,
                }
            ],
            "qualification": (
                "Verbatim synthetic source excerpt only; no real "
                "scientific or reviewer authenticity claim."
            ),
        }
        for sid, s in source_spans.items()
    ]
    source_review["reviewed_claims"] = deepcopy(claims)
    source_review["material_review"] = [
        {
            "name": f["name"],
            "sha256": f["sha256"],
            "claim_ids": [c["claim_id"] for c in claims],
            "qualification": (
                "Synthetic source-qualified file assessment; "
                "identity/metadata are not external authenticity "
                "claims."
            ),
        }
        for f in source_review["material_binding"]["files"]
    ]

    pipeline_budget = budget()
    responses = [
        canonical(
            answer_evidence(
                request=q, corpus=corpus, reader=reader, budget=pipeline_budget
            )
        )
        for q in questions
    ]
    response_hashes = [digest(b) for b in responses]
    evaluation = evaluate_frozen_scenarios(
        independent_gold=gold,
        candidate_responses=responses,
        candidate_response_sha256s=response_hashes,
        source_review=source_review,
    )
    pages = sorted(
        [a.attachment_key, p.local_page] for a in join.attachments for p in a.pages
    )
    section_ids = sorted(
        [a.attachment_key, s.canonical_section_id]
        for a in join.attachments
        for s in a.sections
    )
    review = {
        "schema_version": "millefeuille-semantic-review-result/v1",
        "paper_id": join.paper_id,
        "run_id": join.run_id,
        "source_hash": join.source_hash,
        "material_binding": source_review["material_binding"],
        "source_bindings": [c["source_locators"][0] for c in claims],
        "coverage": {
            "observed_pages": pages,
            "covered_pages": pages,
            "observed_sections": section_ids,
            "covered_sections": section_ids,
            "uncovered_material_claims": [],
        },
        "claims": claims,
        "ambiguities": [],
        "versions": "resolved",
        "version_qualifications": [
            "Pinned synthetic fixture v1; no private/source authenticity claim"
        ],
        "scenarios": evaluation["scenarios"],
    }
    receipt = {
        "source_review_sha256": digest(canonical(source_review)),
        "review_content_sha256": digest(canonical(review)),
    }
    review["review_method"] = {
        "method": (
            "source-qualified synthetic extraction plus "
            "separately frozen eight-scenario gold"
        ),
        "reviewer_assertion": (
            "synthetic independent reviewer assertion, not authenticated"
        ),
        "independent_review_record_sha256": digest(canonical(receipt)),
        "limitations": [
            (
                "Synthetic offline custody only; scientific validity, "
                "real private review and provider/native/platform "
                "qualification remain open."
            )
        ],
    }
    evidence = []
    for name, role, record in [
        ("pd04", "retrieval", evaluation),
        ("pd01", "source", receipt),
        ("source-review", "source", source_review),
        ("review-result", "source", review),
    ] + [
        (
            role,
            role,
            {
                "synthetic": True,
                "role": role,
                "source_hash": join.source_hash,
                "material_binding_sha256": join.material_binding_sha256,
            },
        )
        for role in sorted(ROLES - {"retrieval", "source"})
    ]:
        ref = "reports/frozen-" + name + ".json"
        target = run / ref
        target.parent.mkdir(exist_ok=True)
        payload = canonical(record)
        target.write_bytes(payload)
        evidence.append({"ref": ref, "sha256": digest(payload), "role": role})
    return {
        "resolved": resolved,
        "summary": summary,
        "join": join,
        "reviewed_package": package,
        "corpus": corpus,
        "source_review": source_review,
        "review_result": review,
        "required_evidence": evidence,
        "candidate_responses": responses,
        "candidate_response_sha256s": response_hashes,
        "independent_gold": gold,
        "independent_review_record": receipt,
        "evaluation": evaluation,
        "questions": questions,
        "reader": reader,
        "pipeline_budget": pipeline_budget,
    }


def plan_inputs(case):
    return {
        k: case[k]
        for k in [
            "review_result",
            "summary",
            "required_evidence",
            "candidate_responses",
            "candidate_response_sha256s",
            "independent_gold",
            "source_review",
            "independent_review_record",
        ]
    } | {"resolved_materials": case["resolved"]}
