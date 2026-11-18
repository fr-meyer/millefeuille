# Root completion development checkpoint

Transport base dd839af integrates T1/T2 repairs. Prior8file root checkpoint applied alone as e05f48b2deae01c703b31781b42697e21dcc35d2. Whole candidate remains unaccepted.

New functional source: CollectionReader uses GET-COLLECTION private worker operation, fixed HTTPS existing collection route, same owned process supervisor/deadline/IPC, no retries. Validates raw library/key/version/name/parent and rederives proof. One separately budgeted collection read. Item GET/PATCH protocol and supervisor unchanged.

prepare_remote validates separately authenticated exact preparation receipt, consumes prepare-read uniqueness in original externally committed journal BEFORE collection+fresh-item GET, five-second combined budget, refuses replay without execute consumption. Fresh original/current snapshots structurally rederived from raw proofs, original version stays7 vs fresh9 in fixture, sixty-second freshness bound. HTTP raw proof alone is NOT remote authentication; trusted acquisition/custody required.

Science acceptance is separate operation science.accept-writeback-materialization with exact plan/files/source targets; publication/proposal operation cannot substitute. Attended import source verifies existing protected config, independently authenticated science receipt, rereads/rederives evidence, EXCL writes/fsyncs independently located attestation. No real import or provisioning performed.

STILL INCOMPLETE: broker startup remains hard-denied. Collection adapter/preparation/science attestation have not yet been wired into actual dispatcher end-to-end; source import does not yet verify classification execution/publication reservation ledger chain or complete original raw custody. Successful socket-to-MockTransport full positive integration and provisioning quarantine tests missing. Do not lift stop merely because component tests pass. Current missing work is engineering, not host/OAuth authorization.

CLI remains: writeback-operator plan --inputs FILE; preflight --inputs FILE --receipt FILE; execute --inputs FILE --receipt FILE --mode approved-live; rollback --inputs FILE --receipt FILE --execution-audit DIGEST --mode approved-live. Execute/rollback remain unavailable through startup until full integration. No extra source acceptance is claimed.
