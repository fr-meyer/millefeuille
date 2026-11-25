# Explicit native SDK profile configuration

Native completion/preparation requires MILLEFEUILLE_OPENCLAW_PROFILE to explicitly name the already-configured OpenAI OAuth profile (openai: plus its opaque configured ID). No default/account discovery/token HTTP fallback is introduced. Absent, whitespace or malformed profile fails before SDK dispatch.

MILLEFEUILLE_OPENCLAW_AGENT must explicitly name a nonpersonal deployment-configured agent ID. Its syntax is bounded; the existing native runtime configuration must contain exactly one the effective authored agent roster entry with that ID. Missing, unknown or duplicate selection fails closed. No main/default agent substitution.

Python dispatch and JavaScript preparation use the same selected profile. Preparation passes the exact configured agent and model@profile; existing positive checks require OAuth mode, matching prepared.auth.profileId, exact provider/model/base URL and matching completion result validation. Environment mismatch fails; diagnostics contain fixed codes, never configuration values. These identifiers are private deployment metadata, not public example identities.

This source repair does not change the native SDK/authentication connector, access an auth store, grant privileges or modify installed configuration. Native scope/admission/evidence/no-retry/deadline checks remain required. Independent source review and qualified real validation remain pending; no test/runtime pass is claimed.

Effective roster follows OpenClaw public SDK precedence: own defined agents.entries
shadows legacy own agents.list. Entries use their own keys (not value.id).
This public helper supports a strict canonical-only subset: explicit selected ID
and every roster ID must already be lowercase ASCII [a-z0-9][a-z0-9_-]{0,63}.
Malformed or noncanonical IDs, duplicate IDs and missing target refuse the entire
roster rather than normalize, deduplicate, choose first, or fall back to main.
Profile and agent syntax validation consumes the complete string; no trimming.
