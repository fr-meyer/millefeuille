# GPT summary receipt reservation

The privileged `reserve_trusted_gpt_summary_receipt` primitive replans one
prepared paper, validates its exact MF-100 packet and unexpired receipt, and
matches them to the administrator-owned approval record. Only then does it
open a root-owned SQLite ledger under `/etc/millefeuille`, load prior consumed
receipts, and atomically record this receipt as consumed. Reuse of its ID or
content digest fails before any execution. The ledger is mode `0600`, and
the control directory must be administrator-owned and not writable by other
users.

This primitive requires Linux root and belongs inside a future one-shot
privileged broker. It exposes no socket or unprivileged call path in this
slice. It returns only the no-effect preview and performs no model call or
paper-artifact write. The future broker must verify the peer, invoke this
primitive, and return a bounded reservation acknowledgement. The unprivileged
executor must then recheck source identity before each call and validate the
complete output batch; durable materialization remains a separate approval.
