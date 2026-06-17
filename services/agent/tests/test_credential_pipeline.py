"""Tests for the agent-side credential pipeline (job VAULT-READ).

Covers all four moving parts:

1. FIRMS vault-read: ``_resolve_map_key`` resolves the per-Case vault key
   (via ``secret_ref`` → ``Persistence.get_secret_value``) BEFORE the env var,
   and the env / demo fallbacks still work. The cache key never includes the
   raw key.
2. Provider registry: ``credential_registry`` maps FIRMS → provider metadata
   (label / signup_url / secret_key_name) and classifies FIRMS auth errors.
3. Auth-error → credential-request: ``_invoke_tool_via_emitter`` pauses on a
   keyed-tool credential error, emits a ``credential-request`` envelope, and
   blocks awaiting ``credential-provided``.
4. credential-provided → retry: resolving the pending credential future with
   ``provided=True`` retries the tool (which now resolves the vault key) and
   succeeds; one prompt per tool per turn is enforced.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from unittest.mock import patch

import pytest

from grace2_agent import server
from grace2_agent.server import (
    SessionState,
    _build_credential_request_payload,
    _invoke_tool_via_emitter,
    _resolve_pending_credential,
)
from grace2_agent import credential_registry as cr
from grace2_agent.tools import (
    TOOL_REGISTRY,
    RegisteredTool,
    clear_registry_for_tests,
)
from grace2_agent.tools import fetch_firms_active_fire as firms_mod
from grace2_agent.tools.fetch_firms_active_fire import (
    FirmsArgError,
    FirmsAuthError,
    FirmsMissingKeyError,
    _resolve_map_key,
    set_persistence_for_secrets,
)
from grace2_contracts.common import new_ulid
from grace2_contracts.execution import LayerURI
from grace2_contracts.secrets import CredentialProvidedEnvelopePayload
from grace2_contracts.tool_registry import AtomicToolMetadata


# --------------------------------------------------------------------------- #
# MockWebSocket — collects wire envelopes for assertion.
# --------------------------------------------------------------------------- #


class MockWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, raw: Any) -> None:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        if isinstance(raw, str):
            self.sent.append(json.loads(raw))
        else:
            self.sent.append(raw)


# =========================================================================== #
# 1. FIRMS vault-read
# =========================================================================== #


def test_firms_resolve_vault_key_beats_env():
    """secret_ref (vault) resolves BEFORE the env var (vault-first)."""

    class FakePersistence:
        async def get_secret_value(self, secret_ref):
            return "vault-firms-key"

    class FakeRecord:
        secret_id = "S01"
        provider = "firms"
        is_active = True
        vault_ref = "projects/p/secrets/s/versions/latest"

    set_persistence_for_secrets(FakePersistence())
    try:
        with patch.dict(
            os.environ, {"GRACE2_FIRMS_MAP_KEY": "env-firms-key"}, clear=False
        ):
            out = _resolve_map_key(secret_ref=FakeRecord())
        assert out == "vault-firms-key"
    finally:
        set_persistence_for_secrets(None)


def test_firms_resolve_str_shortcut():
    """A bare-str secret_ref is the resolved key (test-mock shortcut)."""
    with patch.dict(os.environ, {}, clear=True):
        assert _resolve_map_key(secret_ref="direct-vault-value") == "direct-vault-value"


def test_firms_resolve_explicit_map_key_wins():
    """An explicit map_key kwarg wins over both vault and env."""
    with patch.dict(os.environ, {"GRACE2_FIRMS_MAP_KEY": "env"}, clear=False):
        assert _resolve_map_key(map_key="explicit", secret_ref="vault") == "explicit"


def test_firms_resolve_env_fallback_then_demo():
    """No kwarg / no secret_ref → env var; no env → 'demo' literal."""
    with patch.dict(os.environ, {"GRACE2_FIRMS_MAP_KEY": "env-only"}, clear=False):
        assert _resolve_map_key() == "env-only"
    env = dict(os.environ)
    env.pop("GRACE2_FIRMS_MAP_KEY", None)
    with patch.dict(os.environ, env, clear=True):
        assert _resolve_map_key() == "demo"


def test_firms_vault_failure_falls_back_not_crash():
    """A vault lookup failure logs + falls back to env/demo (no crash)."""

    class FailingPersistence:
        async def get_secret_value(self, secret_ref):
            raise RuntimeError("vault unreachable")

    class FakeRecord:
        is_active = True
        provider = "firms"

    set_persistence_for_secrets(FailingPersistence())
    try:
        env = dict(os.environ)
        env.pop("GRACE2_FIRMS_MAP_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            assert _resolve_map_key(secret_ref=FakeRecord()) == "demo"
    finally:
        set_persistence_for_secrets(None)


def test_firms_cache_key_omits_raw_key():
    """The FIRMS cache params carry a key fingerprint, never the raw key."""
    captured: dict[str, Any] = {}

    def _fake_read_through(*, metadata, params, ext, fetch_fn):  # noqa: ANN001
        captured["params"] = params
        from grace2_agent.tools.cache import ReadThroughResult

        return ReadThroughResult(
            uri="gs://bucket/cache/dynamic-1h/firms_active_fire/x.fgb",
            data=b"",
            hit=False,
        )

    with patch.object(firms_mod, "read_through", _fake_read_through):
        firms_mod.fetch_firms_active_fire(
            bbox=(-124.0, 32.5, -114.0, 42.0),
            days_back=1,
            map_key="super-secret-raw-key",
        )
    params = captured["params"]
    assert "super-secret-raw-key" not in json.dumps(params)
    assert "key_fp" in params
    # The fingerprint is a short hex prefix, not the raw key.
    assert params["key_fp"] != "super-secret-raw-key"
    assert len(params["key_fp"]) == 8


# =========================================================================== #
# 2. Provider registry
# =========================================================================== #


def test_registry_firms_provider_metadata():
    p = cr.provider_for_tool("fetch_firms_active_fire")
    assert p is not None
    assert p.label == "NASA FIRMS"
    assert p.signup_url == "https://firms.modaps.eosdis.nasa.gov/api/map_key/"
    assert p.secret_key_name == "FIRMS_MAP_KEY"


def test_registry_non_keyed_tool_has_no_provider():
    assert cr.provider_for_tool("geocode_location") is None
    assert cr.provider_for_tool("compute_hillshade") is None


def test_registry_classifies_firms_auth_error():
    assert cr.is_credential_error("fetch_firms_active_fire", FirmsAuthError("x"))
    assert cr.is_credential_error(
        "fetch_firms_active_fire", FirmsMissingKeyError("x")
    )


def test_registry_does_not_classify_arg_error():
    assert not cr.is_credential_error(
        "fetch_firms_active_fire", FirmsArgError("bad bbox")
    )


def test_registry_does_not_classify_for_non_keyed_tool():
    # Even a credential-shaped error from a non-keyed tool is NOT a credential
    # error (no provider → no prompt).
    assert not cr.is_credential_error("geocode_location", FirmsAuthError("x"))


def test_registry_structure_extensible():
    """Provider entries are CredentialProvider dataclasses keyed by provider_id."""
    assert "firms" in cr.CREDENTIAL_PROVIDERS
    fp = cr.get_provider("firms")
    assert isinstance(fp, cr.CredentialProvider)
    assert fp.provider_id == "firms"


def test_build_credential_request_payload_uses_real_provider_id():
    """FIRMS provider_id IS now a ProviderID Literal member → the payload is
    scoped to the REAL provider (no 'openweathermap' fallback).

    This is the round-trip fix: the credential-request, the web secret-add it
    triggers, and ``_resolve_active_secret_ref`` on retry all agree on
    provider_id='firms', so the saved key re-resolves.
    """
    provider = cr.get_provider("firms")
    payload = _build_credential_request_payload(
        request_id=new_ulid(),
        provider=provider,
        tool_name="fetch_firms_active_fire",
        message="needs a key",
    )
    assert payload is not None
    assert payload.provider_id == "firms"  # NOT a fallback scope
    assert payload.provider_label == "NASA FIRMS"
    assert payload.secret_key_name == "FIRMS_MAP_KEY"
    assert payload.tool_name == "fetch_firms_active_fire"
    assert payload.signup_url == "https://firms.modaps.eosdis.nasa.gov/api/map_key/"


def test_build_credential_request_payload_each_provider_round_trips():
    """Every registered provider builds a valid payload whose provider_id is a
    real ProviderID member (so the saved secret re-resolves on retry)."""
    for tool_name in cr.TOOL_PROVIDER:
        provider = cr.provider_for_tool(tool_name)
        assert provider is not None
        payload = _build_credential_request_payload(
            request_id=new_ulid(),
            provider=provider,
            tool_name=tool_name,
            message="needs a key",
        )
        assert payload is not None, f"{tool_name} payload must build"
        assert payload.provider_id == provider.provider_id
        assert payload.provider_label == provider.label
        assert payload.tool_name == tool_name


def test_build_credential_request_payload_unknown_provider_returns_none():
    """An unregistered provider_id cannot scope a re-resolvable secret-add →
    the builder returns None (no fabricated fallback scope)."""
    bogus = cr.CredentialProvider(
        provider_id="not_a_real_provider",
        label="Bogus",
        signup_url=None,
        secret_key_name="BOGUS_KEY",
        default_message="x",
    )
    payload = _build_credential_request_payload(
        request_id=new_ulid(),
        provider=bogus,
        tool_name="fetch_firms_active_fire",
        message="needs a key",
    )
    assert payload is None


# --------------------------------------------------------------------------- #
# 2b. GENERIC classifier — credential detection across ALL keyed tools.
# --------------------------------------------------------------------------- #


def test_registry_all_keyed_tools_have_providers():
    """Every keyed fetch tool routes to a registered provider."""
    for tool_name in (
        "fetch_firms_active_fire",
        "fetch_ebird_observations",
        "fetch_era5_reanalysis",
        "fetch_gtsm_tide_surge",
        "fetch_movebank_tracks",
        "fetch_iucn_red_list_range",
    ):
        p = cr.provider_for_tool(tool_name)
        assert p is not None, f"{tool_name} must map to a provider"
        assert p.signup_url, f"{tool_name} provider must have a signup_url"
        assert p.secret_key_name


def test_era5_and_gtsm_share_the_cds_provider():
    """ERA5 and GTSM both resolve to the single shared Copernicus CDS scope so
    one saved CDS key serves both tools."""
    era5 = cr.provider_for_tool("fetch_era5_reanalysis")
    gtsm = cr.provider_for_tool("fetch_gtsm_tide_surge")
    assert era5 is not None and gtsm is not None
    assert era5.provider_id == gtsm.provider_id == "ecmwf_cds"


class _ErrWithCode(RuntimeError):
    def __init__(self, msg: str, code: str | None = None, status=None) -> None:
        super().__init__(msg)
        if code is not None:
            self.error_code = code
        if status is not None:
            self.status_code = status


def test_generic_classifier_matches_auth_error_suffix_for_any_tool():
    """A *_AUTH_ERROR / *_MISSING_KEY code classifies for ANY keyed tool."""
    assert cr.is_credential_error(
        "fetch_era5_reanalysis", _ErrWithCode("nope", "ERA5_AUTH_ERROR")
    )
    assert cr.is_credential_error(
        "fetch_gtsm_tide_surge", _ErrWithCode("nope", "GTSM_MISSING_KEY")
    )
    assert cr.is_credential_error(
        "fetch_movebank_tracks", _ErrWithCode("nope", "MOVEBANK_AUTH_ERROR")
    )
    assert cr.is_credential_error(
        "fetch_iucn_red_list_range", _ErrWithCode("nope", "IUCN_AUTH_ERROR")
    )


def test_generic_classifier_matches_code_substrings():
    """A code containing API_KEY / UNAUTHORIZED classifies even if it's not in
    the per-tool TOOL_AUTH_ERROR_CODES set."""
    assert cr.is_credential_error(
        "fetch_ebird_observations", _ErrWithCode("x", "EBIRD_BAD_API_KEY")
    )
    assert cr.is_credential_error(
        "fetch_era5_reanalysis", _ErrWithCode("x", "ERA5_UNAUTHORIZED")
    )


def test_generic_classifier_matches_http_401_403():
    """A typed error carrying an HTTP 401/403 classifies even under a non-auth
    code (e.g. an upstream code that happened to wrap a 403)."""
    assert cr.is_credential_error(
        "fetch_era5_reanalysis",
        _ErrWithCode("blocked", "ERA5_UPSTREAM_ERROR", status=401),
    )
    assert cr.is_credential_error(
        "fetch_gtsm_tide_surge",
        _ErrWithCode("blocked", "GTSM_UPSTREAM_ERROR", status=403),
    )


def test_generic_classifier_matches_message_text():
    """A body/message that reads like a missing-key signal classifies even with
    no error_code at all (the 'body that says you need an api key' case)."""
    assert cr.is_credential_error(
        "fetch_movebank_tracks", RuntimeError("This endpoint requires an API key.")
    )
    assert cr.is_credential_error(
        "fetch_iucn_red_list_range", RuntimeError("401 Unauthorized")
    )
    assert cr.is_credential_error(
        "fetch_ebird_observations", RuntimeError("Invalid key supplied")
    )


def test_generic_classifier_ignores_non_credential_text():
    """A plain upstream / arg error with no credential signal is NOT a
    credential error (no over-triggering)."""
    assert not cr.is_credential_error(
        "fetch_era5_reanalysis",
        _ErrWithCode("the bbox is degenerate", "ERA5_INPUT_ERROR"),
    )
    assert not cr.is_credential_error(
        "fetch_gtsm_tide_surge",
        _ErrWithCode("upstream returned 503", "GTSM_UPSTREAM_ERROR", status=503),
    )


def test_generic_classifier_never_classifies_unknown_provider():
    """A credential-shaped error from a tool with NO provider returns False —
    the server cannot request a key for an unknown provider (no fabrication)."""
    assert not cr.is_credential_error(
        "compute_hillshade", _ErrWithCode("x", "HILLSHADE_AUTH_ERROR")
    )
    assert not cr.is_credential_error(
        "geocode_location", RuntimeError("requires an api key")
    )


# =========================================================================== #
# 3 + 4. Server: auth-error → credential-request → retry
# =========================================================================== #


@pytest.fixture(autouse=True)
def _snapshot_and_restore_registry():
    snapshot = dict(TOOL_REGISTRY)
    clear_registry_for_tests()
    # Each server-flow test relies on a clean pending-credential registry.
    server._PENDING_CREDENTIALS.clear()
    try:
        yield
    finally:
        clear_registry_for_tests()
        TOOL_REGISTRY.update(snapshot)
        server._PENDING_CREDENTIALS.clear()


def _register_firms_stub(fn) -> None:
    """Register a stub under the FIRMS tool name (so the credential registry
    maps it to the FIRMS provider) whose body is ``fn``."""
    meta = AtomicToolMetadata(
        name="fetch_firms_active_fire",
        ttl_class="dynamic-1h",
        source_class="firms_active_fire",
        cacheable=True,
    )
    TOOL_REGISTRY["fetch_firms_active_fire"] = RegisteredTool(
        metadata=meta, fn=fn, module=__name__
    )


def _ok_layer() -> LayerURI:
    return LayerURI(
        layer_id="firms-test",
        name="FIRMS active fires",
        layer_type="vector",
        uri="gs://bucket/cache/dynamic-1h/firms_active_fire/x.fgb",
        style_preset="firms_active_fire",
        role="primary",
    )


def test_auth_error_emits_credential_request_and_retries_on_provided():
    """First dispatch raises FIRMS_AUTH_ERROR → credential-request emitted →
    user provides key → tool retried → success."""
    attempts = {"n": 0}

    def _firms_body(**kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise FirmsAuthError("FIRMS rejected the MAP_KEY")
        return _ok_layer()

    _register_firms_stub(_firms_body)
    ws = MockWebSocket()
    state = SessionState(session_id=new_ulid())

    async def _run():
        dispatch = asyncio.create_task(
            _invoke_tool_via_emitter(
                ws, state, "fetch_firms_active_fire",
                {"bbox": [-124.0, 32.5, -114.0, 42.0]},
            )
        )
        # Let the dispatch emit the credential-request + register its future.
        for _ in range(50):
            await asyncio.sleep(0)
            req = [e for e in ws.sent if e["type"] == "credential-request"]
            if req and server._PENDING_CREDENTIALS:
                break
        req = [e for e in ws.sent if e["type"] == "credential-request"]
        assert len(req) == 1, "credential-request must be emitted on auth error"
        payload = req[0]["payload"]
        assert payload["provider_label"] == "NASA FIRMS"
        assert payload["secret_key_name"] == "FIRMS_MAP_KEY"
        assert payload["tool_name"] == "fetch_firms_active_fire"
        request_id = payload["request_id"]

        # Simulate the user saving the key → credential-provided(provided=True).
        ok = _resolve_pending_credential(
            state.session_id,
            CredentialProvidedEnvelopePayload(
                request_id=request_id, secret_id=new_ulid(), provided=True
            ),
        )
        assert ok
        return await dispatch

    result = asyncio.run(_run())
    assert isinstance(result, LayerURI)
    assert attempts["n"] == 2, "tool must be retried exactly once after provided"


def test_credential_request_envelope_never_carries_raw_key():
    """SECURITY (auth boundary): the raw key value MUST NOT appear anywhere in
    the LLM/user-facing ``credential-request`` envelope.

    The server folds the tool's typed-error string into the prompt message
    (honest, specific copy). FIRMS' own auth-error string redacts the key, but
    a regression that leaked a key into the exception text would surface here:
    we drive the path with a body that raises an auth error and assert the raw
    key value the dispatch resolved appears in NO field of the emitted
    envelope (provider_label / signup_url / secret_key_name / message / etc.).
    """
    RAW_KEY = "RAWKEY-THIS-MUST-NEVER-LEAK-1234567890"

    def _firms_body(**kwargs):
        # Mirror the real tool: the auth error names the env var + signup URL,
        # NEVER the resolved key value. (If a future edit interpolated the key
        # into this string, the assertion below would fail.)
        raise FirmsAuthError(
            "FIRMS rejected the MAP_KEY. Set GRACE2_FIRMS_MAP_KEY to a valid "
            "key from https://firms.modaps.eosdis.nasa.gov/api/map_key/."
        )

    _register_firms_stub(_firms_body)
    ws = MockWebSocket()
    state = SessionState(session_id=new_ulid())

    async def _run():
        dispatch = asyncio.create_task(
            _invoke_tool_via_emitter(
                ws, state, "fetch_firms_active_fire",
                # Pass the raw key on the params (the dev/test resolution path)
                # so it is in scope for the dispatch — the envelope still must
                # not echo it.
                {"bbox": [-124.0, 32.5, -114.0, 42.0], "map_key": RAW_KEY},
            )
        )
        for _ in range(50):
            await asyncio.sleep(0)
            if server._PENDING_CREDENTIALS:
                break
        req = [e for e in ws.sent if e["type"] == "credential-request"]
        assert len(req) == 1
        request_id = req[0]["payload"]["request_id"]
        # Decline so the dispatch terminates (raises the original error).
        _resolve_pending_credential(
            state.session_id,
            CredentialProvidedEnvelopePayload(
                request_id=request_id, provided=False
            ),
        )
        with pytest.raises(FirmsAuthError):
            await dispatch
        # The raw key appears in NO emitted envelope — not the credential
        # request, not any tool-card / error frame.
        for env in ws.sent:
            assert RAW_KEY not in json.dumps(env), (
                f"raw key leaked into {env.get('type')!r} envelope"
            )

    asyncio.run(_run())


def test_declined_credential_surfaces_original_error():
    """provided=False → the original FIRMS_AUTH_ERROR is re-raised (honest fail)."""

    def _firms_body(**kwargs):
        raise FirmsAuthError("FIRMS rejected the MAP_KEY")

    _register_firms_stub(_firms_body)
    ws = MockWebSocket()
    state = SessionState(session_id=new_ulid())

    async def _run():
        dispatch = asyncio.create_task(
            _invoke_tool_via_emitter(
                ws, state, "fetch_firms_active_fire",
                {"bbox": [-124.0, 32.5, -114.0, 42.0]},
            )
        )
        for _ in range(50):
            await asyncio.sleep(0)
            if server._PENDING_CREDENTIALS:
                break
        req = [e for e in ws.sent if e["type"] == "credential-request"]
        assert len(req) == 1
        request_id = req[0]["payload"]["request_id"]
        _resolve_pending_credential(
            state.session_id,
            CredentialProvidedEnvelopePayload(
                request_id=request_id, provided=False
            ),
        )
        with pytest.raises(FirmsAuthError):
            await dispatch

    asyncio.run(_run())


def test_one_prompt_per_tool_per_turn_no_infinite_loop():
    """A retry that ALSO fails with auth error does NOT re-prompt (one per turn).

    The second auth-error propagates as the normal typed error instead of a
    second credential-request — preventing an infinite prompt loop on a
    still-bad key.
    """

    def _firms_body(**kwargs):
        raise FirmsAuthError("still rejected")

    _register_firms_stub(_firms_body)
    ws = MockWebSocket()
    state = SessionState(session_id=new_ulid())

    async def _run():
        dispatch = asyncio.create_task(
            _invoke_tool_via_emitter(
                ws, state, "fetch_firms_active_fire",
                {"bbox": [-124.0, 32.5, -114.0, 42.0]},
            )
        )
        for _ in range(50):
            await asyncio.sleep(0)
            if server._PENDING_CREDENTIALS:
                break
        req = [e for e in ws.sent if e["type"] == "credential-request"]
        assert len(req) == 1
        request_id = req[0]["payload"]["request_id"]
        # User provides a (still-bad) key → retry fires → second auth error.
        _resolve_pending_credential(
            state.session_id,
            CredentialProvidedEnvelopePayload(
                request_id=request_id, provided=True
            ),
        )
        with pytest.raises(FirmsAuthError):
            await dispatch
        # Exactly ONE credential-request was emitted across both attempts.
        req_all = [e for e in ws.sent if e["type"] == "credential-request"]
        assert len(req_all) == 1

    asyncio.run(_run())


def test_non_credential_error_does_not_prompt():
    """A FIRMS_ARG_INVALID error is NOT a credential error → no prompt, raises."""

    def _firms_body(**kwargs):
        raise FirmsArgError("degenerate bbox")

    _register_firms_stub(_firms_body)
    ws = MockWebSocket()
    state = SessionState(session_id=new_ulid())

    async def _run():
        with pytest.raises(FirmsArgError):
            await _invoke_tool_via_emitter(
                ws, state, "fetch_firms_active_fire",
                {"bbox": [-124.0, 32.5, -114.0, 42.0]},
            )
        assert not any(e["type"] == "credential-request" for e in ws.sent)

    asyncio.run(_run())


def test_cross_session_credential_provided_refused():
    """A credential-provided from a different session does NOT resolve the gate."""
    request_id = new_ulid()

    async def _run():
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        server._register_pending_credential("session-A", request_id, fut)
        try:
            ok = _resolve_pending_credential(
                "session-B",
                CredentialProvidedEnvelopePayload(
                    request_id=request_id, provided=True
                ),
            )
            assert ok is False
            assert not fut.done()
        finally:
            server._pop_pending_credential(request_id)

    asyncio.run(_run())
