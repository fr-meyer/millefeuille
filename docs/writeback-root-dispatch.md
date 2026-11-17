# Functional root dispatch source integration — review required

Supersedes startup-disabled statements in the historical writeback-live-operator and writeback-root-completion checkpoints. Source now admits a valid configured chain and rejects invalid custody/receipt/mapping. Not deployed or production-qualified.

## Actual path
One Linux main-thread serve_one calls strict _controls, authenticates Unix peer UID, decodes bounded frame, reconstructs saved plan, authenticates fresh independent scientific acceptance plus attended attestation, authenticates exact execute/rollback receipt, validates externally pinned original journal commitment, consumes SEPARATE preparation receipt, performs exactly one uncached existing-collection GET and one fresh parent GET in a shared5second budget, durably journals their raw proofs, then core consumes execute/rollback and permits at most one conditional PATCH plus two parent reads. Independent ACK-bound readback remains core authority. Rollback preparation targets the exact owned execution audit and checks its unchanged observed version/content, never rebases to original before.

## Config and fixed controls
config.json exact keys: schema=millefeuille-writeback-root/v1, caller_uid, caller_gid, files, allowlist, add_tags, remove_tags, accepted_plan_digest, commitment_sha256, transport_accepted, collection. collection exact library_type/library_id/key/version/name/parentCollection. allowlist exact library_type/library_id/key/collection_key. files descriptors each name(relative)/sha256: index, manifest, decision, taxonomy_lock, source, acceptance, original_parent, original_proof, classification_ledger, classification_chain, classification_output, before, collection, source_bytes. Existing independently staged root custody only, not caller paths.

classification_ledger is a protected bounded binary SQLite snapshot, deserialized read-only in memory. classification_chain names exact execution/publication receipt digests and shared classification_plan_sha256. Validate two consumed canonical audits, original model.classify and classification.review operations, run/paper/plan targets; publication target rederived from classification_output mapping of original ref to UTF8bytes using existing output_identity. This is an administrator-authenticated snapshot bound by the separate science acceptance exact-files target, not a claim that a self-hashed proposal is accepted. Scientific acceptance scope separately binds exact materialization/files/source/plan. Original raw parent proof is actually parsed/rederived and matched against original snapshot/version; source bytes hash is independently recomputed.

Fixed controls: science-acceptance-receipt.json, science-attestation.json, execute-receipt.json, rollback-receipt.json, execute-preparation-receipt.json, rollback-preparation-receipt.json, credential, journal-commitment.json. Roots as writeback-live-operator.md. No argv/environment secret, arbitrary endpoint, arbitrary command, fallback transport or automatic provisioning.

Attended import reads existing independently approved config/science receipt and exact source custody twice, creates attestation EXCL, fsyncs and verifies readback. Partial writes remain quarantine; no retry/adoption. Provision similarly EXCL creates outside commitment and leaves failed ceremony quarantine. Operator never issues its own acceptance. Same-root privileged rollback/substitution remains outside this trust model; reviewer must assess administrator receipt/snapshot issuance operational ceremony before deployment.

## CLI
writeback-operator plan --inputs FILE
writeback-operator preflight --inputs FILE --receipt FILE
writeback-operator execute --inputs FILE --receipt FILE --mode approved-live
writeback-operator rollback --inputs FILE --receipt FILE --execution-audit DIGEST --mode approved-live
writeback-operator readback --inputs FILE --execution-audit DIGEST --mode approved-live
Preflight remains offline scope-only, not root readiness. Readback command honestly returns stored journal audit, not new remote read. Rollback offline preflight adds --operation rollback --execution-audit DIGEST.

Synthetic tests use actual root dispatcher/startup, AF_UNIX peer check, descriptor reads/identity, receipts, SQLite, HTTP adapters/MockTransport, ACK/postGET. Only temporary UID metadata/chown and internal HTTP backend are mocked; root privilege/network are NOT claimed. No real control/source/token files, production services or APIs touched. Whole-integration independent review, exact package qualification, host identification/human OAuth and separate live approvals remain.
