# GPT paper-card cost evidence

The GPT card route pins `openai/gpt-5.6-sol`, subscription OAuth, no fallback,
one call and a zero incremental API charge scope. The shared OpenClaw adapter
reports observed tokens but leaves `result.cost` unknown when the provider
does not return a bill. Card validation preserves that raw unknown value.

Provenance separately records `incremental_cost` with USD zero and the basis
`subscription_oauth_no_incremental_api_charge`. This follows validation of
the exact request and returned authentication/model. It does not claim an
observed provider bill, estimate missing tokens, or include subscription fees.
An explicitly reported nonzero cost, missing/inconsistent usage, model/auth
drift, fallback or invalid output still stops validation after the one call.

The consumed Picard card attempt `run-picard-gpt-card-20260926-01` exposed the
previous mismatch: generic adapter success with unknown cost was rejected
by the stricter card result check. Its receipt cannot be reused. A fresh
attempt needs its own exact scope and approval; this code change makes no
provider call and performs no card publication.
