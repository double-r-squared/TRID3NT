# Audit: Secrets envelope schema (§F.3 contract)

**Job ID:** job-0100-schema-20260608, **Sprint:** sprint-12-mega Wave 1, **Auditor:** Development Orchestrator, **Status:** assigned

**Specialist:** schema

**Required reads:**
- `docs/srs/06-data-and-secrets.md` §F.3 if it exists; else propose the amendment
- `packages/contracts/src/grace2_contracts/__init__.py` — module exports

### Scope

NEW file `packages/contracts/src/grace2_contracts/secrets.py`:

```python
"""Per-Case API-key secret envelopes (§F.3)."""
from dataclasses import dataclass, field
from typing import Literal

ProviderID = Literal[
    "ebird", "iucn_red_list", "movebank",  # Tier-2 conservation
    "nws", "openweathermap",                # weather  
    "openai", "anthropic", "google_genai",  # LLM
    "mapbox", "maptiler",                   # basemap
]

@dataclass
class SecretRecord:
    """Single per-user/per-Case secret reference. Real key NEVER serialized;
    only a reference (vault path) + metadata."""
    secret_id: str              # ULID
    provider: ProviderID
    case_id: str | None         # None = user-level (cross-Case); else case-scoped
    vault_ref: str              # opaque: e.g. "gcp-sm://projects/.../secrets/{id}/versions/latest"
    label: str | None = None
    added_at: str = ""
    last_used_at: str | None = None
    is_active: bool = True

@dataclass
class SecretsListEnvelope:
    """Server → client: list of secret records (NEVER includes the key value)."""
    envelope_type: Literal["secrets-list"] = "secrets-list"
    secrets: list[SecretRecord] = field(default_factory=list)

@dataclass
class SecretAddEnvelope:
    """Client → server: add a new secret. Key is SHORT-LIVED — server writes to
    vault then returns the SecretRecord; key value never echoed back."""
    envelope_type: Literal["secret-add"] = "secret-add"
    provider: ProviderID = "ebird"
    case_id: str | None = None
    label: str | None = None
    key_value: str = ""          # transient; cleared by server after vault-write

@dataclass
class SecretRevokeEnvelope:
    """Client → server: revoke a secret (sets is_active=False; does NOT delete vault entry)."""
    envelope_type: Literal["secret-revoke"] = "secret-revoke"
    secret_id: str = ""
```

**Tests** (in packages/contracts/tests/):
- Round-trip serialization for each dataclass
- key_value field is excluded from default repr (no leak risk)
- envelope_type literal validation
- ProviderID literal validation (refuses unknown providers)

**Concurrency note**: shares `packages/contracts/src/grace2_contracts/__init__.py` with job-0099 (Case schema). Append idempotently.

### File ownership (exclusive)

- `packages/contracts/src/grace2_contracts/secrets.py` (NEW)
- `packages/contracts/src/grace2_contracts/__init__.py` — append imports (idempotent w/ job-0099)
- `packages/contracts/tests/test_secrets.py` (NEW)
- `docs/srs/06-data-and-secrets.md` — narrow §F.3 amendment if not already there
- `reports/inflight/job-0100-schema-20260608/`

### FROZEN

- All other `packages/contracts/*` files
- All implementation files (services/, web/)
- `reports/complete/**`

### Acceptance

- [ ] Secrets dataclasses + envelopes round-trip
- [ ] key_value field never appears in default repr
- [ ] Exports idempotently appended in `__init__.py`
- [ ] packages/contracts tests green
- [ ] Single commit prefix `job-0100:`; co-author line


### Codified lesson (job-0086, do not violate)

URL/render consistency != geographic correctness. In-COG axis mirrors and similar in-file orientation bugs are invisible to every consistency check (server, client, PIL composite all faithfully serve the mirrored array). If your tool emits geometry, your acceptance test MUST verify the output against the **known geography of the bbox** (e.g. "is the deep-flood pixel at the river mouth?"), not just "did the bytes round-trip?".

