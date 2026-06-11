# job-0251-infra-20260611 — Signed-URL minting Cloud Function

**Specialist:** infra · **Model:** opus · **Sprint:** 13.5 Stage 1 · **Adv. verify:** YES (panel follows)

## Frozen kickoff

The signed-URL minting Cloud Function. Python 3.12,
`mint_signed_url(layer_uri, user_id, case_id, ttl_seconds=3600)`:
- Validates `user_id` owns `case_id` via the Persistence seam (study
  `persistence.py`; for the FUNCTION runtime a direct MongoDB driver read of the
  case doc's `user_id` is acceptable IF documented, but PREFER reusing translator
  patterns; the function gets the Atlas URI from Secret Manager).
- Returns a GCS V4 signed URL for the layer's object; TTL clamped to [900, 3600].
- Cloud Functions gen2, HTTPS, AUTHENTICATED: Firebase ID token in Authorization
  (verify via firebase_admin; token uid MUST equal `user_id` — never trust body).
- File ownership: `infra/signed_urls/` (new): function source (main.py,
  requirements.txt), unit tests runnable WITHOUT GCP (mock storage/auth/db), and
  the Tofu deploy resource following repo `infra/*.tf` conventions.

HARD CONSTRAINTS: NO Gemini/Vertex calls; no edits outside `infra/signed_urls/`,
minimal root-module wiring, and reports/. `git add` only my files. Signing:
prefer impersonated / IAM signBlob over a key file — NEVER create/download a SA
key; if signBlob needs `roles/iam.serviceAccountTokenCreator`, that's an UNBLOCK
item.

DoD: code + UNIT TESTS GREEN (pure-Python, run on `services/agent/.venv`) + tofu
validate green + plan captured + unblock items written.

## Verdict rule

Adversarial panel (4 lenses, ≥3/4 confirm): correctness (signed URL expires
correctly; wrong case_id rejected) + contract (TTL cap matches SRS §F.1/§3.8) +
regression (no impact to dev bucket access) + live-verify (real GCS signed URL
fetched + expired) — live-verify deferred to a user-present session per the
sprint-13-5 decisions Gemini-free / production-mutation posture (captured as
USER_UNBLOCK item 0251-D).
