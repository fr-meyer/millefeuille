# Astra xhigh execution contract

All five current GPT stages select exact `openai/gpt-6-astra` with xhigh, fast mode off and no fallback. OCR remains mistral-ocr-latest; structure remains local. OpenKB and ChatIndex routes are separate products and are not migrated here.

Installed OpenClaw package 2026.10.5 was inspected without upgrade or activation. Its default-models module declares openai/gpt-6-astra; llm module recognizes gpt-6-astra and includes xhigh in GPT_6_MANDATORY_REASONING_EFFORTS. This is runtime support evidence, NOT Franck-local OAuth entitlement, API access or a successful provider call. No Astra token/context limit is asserted or copied from Sol. Existing byte caps are local payload/transport limits, not provider context claims.

OAuth execution sends exact --model openai/gpt-6-astra and --thinking xhigh, with empty fallbacks. It checks the returned exact provider/model and refuses aliases or Sol. Installed agent exec exposes no resolved reasoning field. Thus request effort is bound and sent, not falsely described as independently observed effective effort. Explicit thinking/reasoning_effort drift, if returned, is rejected. Existing exact agent-local OAuth preflight remains fail closed; no credential/config/default changes are part of this migration.

Summary execution manifest v0.2 adds required reasoning_effort=xhigh and pins Astra. Requests already bind model+thinking into their canonical hashes/idempotency keys. Preparations already bind the complete stage execution plans; scopes rederive current manifests and require exact Astra provider identity. Card/classification requests and result acceptance bind the same pair. Old Sol receipts cannot authorize these new requests, even if otherwise valid. Reprepare and obtain a new separately authorized receipt, never rename saved evidence.

Request/result envelope shapes remain v0.1, with the current executable model allowlist migrated from Sol to Astra. The generic xAI experimental contract remains separate and does not provide fallback for GPT-only dispatch. Historical provenance and generic receipt readers remain unchanged and can read Sol records; such readability is not execution authority. Frozen historical artifacts and prior plan 651cde7b remain immutable. The manifest schema describes new executable plans, not archived v0.1 manifests.

Real execution still requires independent source review, a new exact approval, working local authentication and supported runtime availability. No OCR repeat, Zotero writes, provider calls, or release acceptance is implied. R6 hold remains.
