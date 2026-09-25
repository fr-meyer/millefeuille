# Trusted GPT summary approval record

`validate_trusted_gpt_summary_approval` first replans one prepared paper and
validates its MF-100 packet and receipt against the exact GPT manifest. It then
reads a fixed approval record from `/etc/millefeuille/gpt-summary-approval.json`
through held no-follow file descriptors. The directory and file must be owned
by the administrator and must not be writable by group or others. The record
uses canonical JSON and binds the receipt, packet, manifest, paper, work-unit
count, approver, and approval time. Caller-supplied paths cannot replace this
control file.

The record is independent of the self-hashed receipt. A valid record confirms
that the administrator published the exact reviewed scope. The validator is
read-only; it does not reserve the receipt or authorize a model call. A future
privileged broker must recheck the source and this record, durably consume the
receipt before dispatch, and reject replay. Model outputs remain private until
a separately approved materialization step.
