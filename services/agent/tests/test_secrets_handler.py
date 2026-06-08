"""Unit + integration tests for ``grace2_agent.secrets_handler`` (job-0124).

Coverage (per the kickoff: ≥8 unit + 1 integration):

Unit (mocked Secret Manager + Persistence):
1. ``test_secret_add_writes_vault_and_persists_record`` — full add lifecycle.
2. ``test_secret_add_never_logs_or_echoes_key_value`` — Decision F leak check.
3. ``test_get_secret_value_returns_original_key`` — round-trip via vault.
4. ``test_get_secret_value_raises_on_revoked`` — typed SecretRevokedError.
5. ``test_secrets_list_no_key_value_field`` — wire-payload audit.
6. ``test_secret_add_appends_audit_log`` — audit-log row created.
7. ``test_secret_revoke_appends_audit_log`` — revoke audit-log row created.
8. ``test_multi_tenant_isolation_list`` — user A's list excludes user B's records.
9. ``test_secret_add_empty_user_id_fail_closed`` — multi-tenant guardrail.
10. ``test_secret_add_empty_key_value_fail_closed`` — never write a zero-byte version.

Integration:
11. ``test_full_lifecycle_add_list_use_revoke_list`` — add -> list -> use ->
    revoke -> list-again, end-to-end with mocked Secret Manager.

Live (env-gated, GRACE2_TEST_LIVE_SECRETS=1):
12. ``test_live_secret_manager_roundtrip_or_skip`` — real Secret Manager
    add/get/revoke against a test GCP project.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest

from grace2_agent.persistence import (
    SECRETS_COLLECTION,
    Persistence,
)
from grace2_agent.secrets_handler import (
    SecretRevokedError,
    handle_secret_add,
    handle_secret_revoke,
    handle_secrets_list,
)
from grace2_contracts.common import new_ulid
from grace2_contracts.secrets import (
    SecretAddEnvelopePayload,
    SecretRecord,
    SecretsListEnvelopePayload,
)

# Reuse the MockMCPClient from the Persistence test suite — same shape.
from .test_persistence import MockMCPClient


# --------------------------------------------------------------------------- #
# Mock Secret Manager client
# --------------------------------------------------------------------------- #


class _FakeSecretPayload:
    def __init__(self, data: bytes) -> None:
        self.data = data


class _FakeSecretVersion:
    def __init__(self, name: str, payload: _FakeSecretPayload) -> None:
        self.name = name
        self.payload = payload


class MockSecretManagerClient:
    """Drop-in replacement for ``SecretManagerServiceClient``.

    Implements ``create_secret``, ``add_secret_version``, and
    ``access_secret_version`` — the exact three calls the handler uses.
    Records every call so tests can assert routing.
    """

    def __init__(self) -> None:
        # parent -> secret_id -> list[bytes] (version payloads, latest last)
        self._store: dict[str, dict[str, list[bytes]]] = {}
        self.calls: list[tuple[str, dict]] = []

    def create_secret(self, request: dict) -> dict:
        self.calls.append(("create_secret", dict(request)))
        parent = request["parent"]
        secret_id = request["secret_id"]
        self._store.setdefault(parent, {}).setdefault(secret_id, [])
        return {"name": f"{parent}/secrets/{secret_id}"}

    def add_secret_version(self, request: dict):
        self.calls.append(("add_secret_version", dict(request)))
        parent = request["parent"]  # projects/X/secrets/<secret_id>
        # parse "projects/X/secrets/Y" -> ("projects/X", "Y")
        project_part, _, secret_id = parent.rpartition("/secrets/")
        data = request["payload"]["data"]
        versions = self._store.setdefault(project_part, {}).setdefault(
            secret_id, []
        )
        versions.append(data)
        version_number = len(versions)
        name = f"{parent}/versions/{version_number}"
        return _FakeSecretVersion(name=name, payload=_FakeSecretPayload(data))

    def access_secret_version(self, request: dict):
        self.calls.append(("access_secret_version", dict(request)))
        # name shape: projects/X/secrets/Y/versions/{N|latest}
        name = request["name"]
        prefix, _, version_sel = name.rpartition("/versions/")
        project_part, _, secret_id = prefix.rpartition("/secrets/")
        versions = self._store.get(project_part, {}).get(secret_id, [])
        if not versions:
            raise RuntimeError(f"mock: no versions for {name!r}")
        if version_sel == "latest":
            data = versions[-1]
        else:
            data = versions[int(version_sel) - 1]
        return _FakeSecretVersion(name=name, payload=_FakeSecretPayload(data))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _env_add(provider: str = "ebird", key: str = "test-ebird-key-DO-NOT-LOG",
             case_id: str | None = None) -> SecretAddEnvelopePayload:
    return SecretAddEnvelopePayload(
        provider=provider,  # type: ignore[arg-type]
        case_id=case_id or new_ulid(),
        label=f"test {provider} key",
        key_value=key,
    )


def _run(coro):
    return asyncio.run(coro)


def _make_persistence_and_secret_mgr() -> tuple[
    Persistence, MockMCPClient, MockSecretManagerClient
]:
    mcp = MockMCPClient()
    p = Persistence(mcp)
    sm = MockSecretManagerClient()
    return p, mcp, sm


# --------------------------------------------------------------------------- #
# Unit tests
# --------------------------------------------------------------------------- #


def test_secret_add_writes_vault_and_persists_record() -> None:
    """Full add lifecycle: Secret Manager + MongoDB both touched correctly."""
    p, mcp, sm = _make_persistence_and_secret_mgr()
    user_id = new_ulid()
    envelope = _env_add(provider="ebird", key="ebird-key-abc-123")

    record = _run(
        handle_secret_add(
            envelope, user_id=user_id, persistence=p,
            secret_manager_client=sm, gcp_project="test-project",
        )
    )

    # Returned a vault-ref-only SecretRecord (no key_value field).
    assert isinstance(record, SecretRecord)
    assert record.provider == "ebird"
    assert record.case_id == envelope.case_id
    assert record.is_active is True
    assert record.vault_ref.startswith("projects/test-project/secrets/")
    assert record.vault_ref.endswith("/versions/latest")

    # Secret Manager: create_secret + add_secret_version both invoked.
    sm_methods = [c[0] for c in sm.calls]
    assert "create_secret" in sm_methods
    assert "add_secret_version" in sm_methods

    # The raw key value made it into the vault.
    create_kwargs = next(c[1] for c in sm.calls if c[0] == "create_secret")
    assert create_kwargs["parent"] == "projects/test-project"
    version_kwargs = next(
        c[1] for c in sm.calls if c[0] == "add_secret_version"
    )
    assert version_kwargs["payload"]["data"] == b"ebird-key-abc-123"

    # MongoDB: secrets collection has the SecretRecord, audit_log has the entry.
    secrets_calls = [
        (n, a) for n, a in mcp.calls
        if a.get("collection") == SECRETS_COLLECTION
    ]
    assert secrets_calls, "no MCP calls to secrets collection"
    # At least one upsert (update-one + upsert=True)
    upserts = [
        a for n, a in secrets_calls
        if n == "update-one" and a.get("upsert") is True
    ]
    assert upserts


def test_secret_add_never_logs_or_echoes_key_value(caplog) -> None:
    """Decision F leak check: the raw key value must not appear in logs."""
    p, _, sm = _make_persistence_and_secret_mgr()
    user_id = new_ulid()
    sentinel_key = "SUPER-SECRET-LEAK-SENTINEL-XYZ-987"
    envelope = _env_add(key=sentinel_key)

    with caplog.at_level("DEBUG", logger="grace2_agent.secrets_handler"):
        _run(
            handle_secret_add(
                envelope, user_id=user_id, persistence=p,
                secret_manager_client=sm,
            )
        )

    full_log = "\n".join(r.getMessage() for r in caplog.records)
    assert sentinel_key not in full_log, (
        f"key_value leaked into log output: {full_log!r}"
    )


def test_get_secret_value_returns_original_key() -> None:
    """Round-trip: add a secret, then read the value back via Persistence."""
    p, _, sm = _make_persistence_and_secret_mgr()
    user_id = new_ulid()
    envelope = _env_add(key="round-trip-test-value")

    record = _run(
        handle_secret_add(
            envelope, user_id=user_id, persistence=p,
            secret_manager_client=sm, gcp_project="rt-project",
        )
    )

    fetched = _run(
        p.get_secret_value(record, secret_manager_client=sm)
    )
    assert fetched == "round-trip-test-value"


def test_get_secret_value_raises_on_revoked() -> None:
    """A revoked secret yields SecretRevokedError before touching the vault."""
    p, _, sm = _make_persistence_and_secret_mgr()
    user_id = new_ulid()
    envelope = _env_add(key="will-be-revoked")
    record = _run(
        handle_secret_add(
            envelope, user_id=user_id, persistence=p,
            secret_manager_client=sm,
        )
    )
    # Soft-revoke the record.
    revoked = record.model_copy(update={"is_active": False})

    with pytest.raises(SecretRevokedError):
        _run(p.get_secret_value(revoked, secret_manager_client=sm))


def test_secrets_list_no_key_value_field() -> None:
    """The reply payload's SecretRecord entries carry only the vault_ref."""
    p, _, sm = _make_persistence_and_secret_mgr()
    user_id = new_ulid()
    case_id = new_ulid()

    _run(
        handle_secret_add(
            _env_add(provider="ebird", key="k1", case_id=case_id),
            user_id=user_id, persistence=p, secret_manager_client=sm,
        )
    )
    _run(
        handle_secret_add(
            _env_add(provider="iucn_red_list", key="k2", case_id=case_id),
            user_id=user_id, persistence=p, secret_manager_client=sm,
        )
    )

    payload = _run(
        handle_secrets_list(
            user_id=user_id, case_id=case_id, persistence=p,
        )
    )
    assert isinstance(payload, SecretsListEnvelopePayload)
    assert len(payload.secrets) == 2

    # Wire-payload audit: no field named "key_value" anywhere.
    wire_dict = payload.model_dump(mode="json")
    for record in wire_dict["secrets"]:
        for k in record.keys():
            assert k != "key_value", f"key_value field surfaced: {record!r}"
        # And neither k1 nor k2 appears as a value anywhere.
        for v in record.values():
            assert v != "k1" and v != "k2"


def test_secret_add_appends_audit_log() -> None:
    """An ``audit_log`` insert lands per secret-add."""
    p, mcp, sm = _make_persistence_and_secret_mgr()
    user_id = new_ulid()
    _run(
        handle_secret_add(
            _env_add(), user_id=user_id, persistence=p,
            secret_manager_client=sm,
        )
    )

    audit_inserts = [
        a for n, a in mcp.calls
        if n == "insert-one" and a.get("collection") == "audit_log"
    ]
    assert audit_inserts, "no audit_log insert recorded"
    doc = audit_inserts[0]["document"]
    assert doc["event_type"] == "secret-add"
    assert doc["payload"]["user_id"] == user_id
    # The audit-log payload includes vault_ref and provider but NOT key_value.
    assert "vault_ref" in doc["payload"]
    assert "key_value" not in doc["payload"]


def test_secret_revoke_appends_audit_log() -> None:
    """secret-revoke flips is_active=False and writes an audit-log row."""
    p, mcp, sm = _make_persistence_and_secret_mgr()
    user_id = new_ulid()
    record = _run(
        handle_secret_add(
            _env_add(), user_id=user_id, persistence=p,
            secret_manager_client=sm,
        )
    )

    _run(
        handle_secret_revoke(
            record.secret_id, user_id=user_id, persistence=p,
        )
    )

    # The list-active-only call returns 0 records after revoke.
    payload = _run(
        handle_secrets_list(user_id=user_id, persistence=p)
    )
    assert len(payload.secrets) == 0

    audit_inserts = [
        a for n, a in mcp.calls
        if n == "insert-one" and a.get("collection") == "audit_log"
    ]
    # at least secret-add + secret-revoke
    event_types = [a["document"]["event_type"] for a in audit_inserts]
    assert "secret-revoke" in event_types


def test_multi_tenant_isolation_list() -> None:
    """User A's secret-list excludes records added by User B."""
    p, _, sm = _make_persistence_and_secret_mgr()
    user_a = new_ulid()
    user_b = new_ulid()

    _run(
        handle_secret_add(
            _env_add(provider="ebird", key="a-key"),
            user_id=user_a, persistence=p, secret_manager_client=sm,
        )
    )
    _run(
        handle_secret_add(
            _env_add(provider="iucn_red_list", key="b-key"),
            user_id=user_b, persistence=p, secret_manager_client=sm,
        )
    )

    a_list = _run(handle_secrets_list(user_id=user_a, persistence=p))
    b_list = _run(handle_secrets_list(user_id=user_b, persistence=p))

    a_providers = {s.provider for s in a_list.secrets}
    b_providers = {s.provider for s in b_list.secrets}
    assert a_providers == {"ebird"}
    assert b_providers == {"iucn_red_list"}


def test_secret_add_empty_user_id_fail_closed() -> None:
    """An empty user_id raises before any vault write — multi-tenant guardrail."""
    p, _, sm = _make_persistence_and_secret_mgr()
    with pytest.raises(Exception):
        _run(
            handle_secret_add(
                _env_add(), user_id="", persistence=p,
                secret_manager_client=sm,
            )
        )
    # Critically: no Secret Manager call was made.
    assert not sm.calls


def test_secret_add_empty_key_value_fail_closed() -> None:
    """An empty key_value raises before touching Secret Manager."""
    p, _, sm = _make_persistence_and_secret_mgr()
    envelope = SecretAddEnvelopePayload(
        provider="ebird",
        case_id=new_ulid(),
        label="empty key test",
        key_value="",
    )
    with pytest.raises(Exception):
        _run(
            handle_secret_add(
                envelope, user_id=new_ulid(), persistence=p,
                secret_manager_client=sm,
            )
        )
    assert not sm.calls


# --------------------------------------------------------------------------- #
# Integration: full lifecycle
# --------------------------------------------------------------------------- #


def test_full_lifecycle_add_list_use_revoke_list() -> None:
    """End-to-end: add -> list (1) -> use (round-trip) -> revoke -> list (0)."""
    p, _, sm = _make_persistence_and_secret_mgr()
    user_id = new_ulid()
    case_id = new_ulid()
    envelope = _env_add(provider="openweathermap", key="lifecycle-test-key",
                        case_id=case_id)

    # 1. Add
    record = _run(
        handle_secret_add(
            envelope, user_id=user_id, persistence=p,
            secret_manager_client=sm,
        )
    )

    # 2. List — one active record
    lst1 = _run(
        handle_secrets_list(
            user_id=user_id, case_id=case_id, persistence=p,
        )
    )
    assert len(lst1.secrets) == 1
    assert lst1.secrets[0].secret_id == record.secret_id

    # 3. Use — Tier-2-fetcher-style read
    value = _run(p.get_secret_value(record, secret_manager_client=sm))
    assert value == "lifecycle-test-key"

    # 4. Revoke
    _run(
        handle_secret_revoke(
            record.secret_id, user_id=user_id, persistence=p,
        )
    )

    # 5. List again — 0 active records
    lst2 = _run(
        handle_secrets_list(
            user_id=user_id, case_id=case_id, persistence=p,
        )
    )
    assert len(lst2.secrets) == 0

    # Vault entry intact (audit trail) — direct check via the mock store
    # the record's vault_ref still resolves to the key value, because
    # revoke is soft. We bypass the is_active guard by constructing a
    # synthetic active-shaped record pointing at the same vault_ref.
    audit_resurrect = SecretRecord(
        secret_id=record.secret_id,
        provider=record.provider,
        case_id=record.case_id,
        vault_ref=record.vault_ref,
        added_at=datetime.now(timezone.utc),
        is_active=True,
    )
    value_after_revoke = _run(
        p.get_secret_value(audit_resurrect, secret_manager_client=sm)
    )
    assert value_after_revoke == "lifecycle-test-key", (
        "vault entry must persist for audit trail"
    )


# --------------------------------------------------------------------------- #
# Live (env-gated) test
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    os.environ.get("GRACE2_TEST_LIVE_SECRETS") != "1",
    reason="live Secret Manager test requires GRACE2_TEST_LIVE_SECRETS=1",
)
def test_live_secret_manager_roundtrip() -> None:  # pragma: no cover — live
    """Live: add a test secret to a test GCP project; verify the round-trip.

    Requires:
    - ``GRACE2_TEST_LIVE_SECRETS=1``
    - ``GRACE2_SECRETS_GCP_PROJECT`` (or ``GOOGLE_CLOUD_PROJECT``) pointing
      at a project the test SA can write Secret Manager entries to.
    - ADC via ``GOOGLE_APPLICATION_CREDENTIALS``.
    """
    p, _, _ = _make_persistence_and_secret_mgr()
    user_id = new_ulid()
    envelope = _env_add(
        provider="ebird", key=f"live-test-{new_ulid()[:12]}"
    )

    record = _run(
        handle_secret_add(
            envelope, user_id=user_id, persistence=p,
        )
    )
    assert record.is_active is True
    # Read back via the live Secret Manager.
    value = _run(p.get_secret_value(record))
    assert value == envelope.key_value

    # Revoke.
    _run(
        handle_secret_revoke(
            record.secret_id, user_id=user_id, persistence=p,
        )
    )
    # The MongoDB record now has is_active=False (verified by listing).
    lst = _run(handle_secrets_list(user_id=user_id, persistence=p))
    revoked_ids = {s.secret_id for s in lst.secrets}
    assert record.secret_id not in revoked_ids
