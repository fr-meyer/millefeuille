# One-shot GPT summary reservation broker

The root-owned broker listens once on `/etc/millefeuille/gpt-summary.sock` and
accepts only the OpenClaw `node` user's Unix peer credentials. Its request
contains an MF-100 packet, receipt, and three absolute preparation-evidence
paths beneath the approved source root. The broker rejects symlinked evidence
paths, replans the paper, checks the administrator-owned approval record, and
durably consumes the receipt through the root-owned ledger. It returns only a
bounded acknowledgement with the receipt and text-free manifest digests and
work-unit count, then removes the socket.

The unprivileged client independently replans the paper and verifies the
broker's root-owned socket and peer identity. It cannot select another socket
or approval file. The client does not read the administrator approval record
or ledger. This boundary reserves authorization only; it makes no model call
and writes no paper artifact. The executor must still recheck the selected
source before each provider call and validate the complete output batch.
