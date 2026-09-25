# GPT summary approval preview

`validate_gpt_summary_approval_preview` checks whether a parsed MF-100 operator
packet and approved-live receipt describe one exact, freshly replanned GPT
summary batch. It reads the route, structure, and preparation evidence again,
builds the bundled versioned prompts, and compares their text-free manifest
digest with the source selector and target in the packet. It also requires the
prepared paper ID, artifact root, GPT OAuth profile, exact number of model
calls, zero cost cap, disposal policy, and stop conditions to match. The
receipt must be unexpired and match the packet through the existing no-effect
MF-100 validator.

The preview returns only manifest and approval identifiers and the request
count. It performs no provider calls and writes no paper artifacts. It does
not reserve a receipt. A self-consistent packet and receipt can be constructed
by anyone who can write files, so this preview is never an execution permit.

Before any live paper batch executor exists, it must independently check a
fresh, exact-scope approval in administrator-owned storage, durably reserve
the one-use receipt through a trusted broker, recheck the source and model
identity immediately before each call, and stop on any drift or error. The
synthetic GPT canary approval and its consumed receipt cannot authorize a
paper summary batch.
