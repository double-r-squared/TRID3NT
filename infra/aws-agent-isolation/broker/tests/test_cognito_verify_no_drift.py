"""Drift-guard: the broker's vendored cognito_verify must AGREE with the agent's
real ``auth_handshake.cognito_verify`` on identical inputs.

The kickoff requires the broker reuse the EXACT cognito logic "so it cannot
drift." The broker's primary path IMPORTS the agent function directly (zero
drift); this test guards the VENDORED FALLBACK by driving BOTH the real agent
function and the vendored transcription through the same mocked ``jwt`` decode +
the same env, across a matrix of claim variations, and asserting they return the
SAME result every time.

If the agent's verifier changes shape (a new claim check, a different return key)
and the vendored copy is not updated in lock-step, a case below diverges and this
test FAILS -- the drift is caught.

Run: python -m pytest infra/aws-agent-isolation/broker/tests/test_cognito_verify_no_drift.py

SKIP: if the agent package (grace2_agent.auth_handshake) is not importable in the
test env, the test skips with a clear message (the import-path zero-drift mode is
then the only path anyway).
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

_BROKER_PARENT = str(Path(__file__).resolve().parents[2])
if _BROKER_PARENT not in sys.path:
    sys.path.insert(0, _BROKER_PARENT)

# The agent src must be importable to compare against the real function. Add it.
_AGENT_SRC = str(Path(__file__).resolve().parents[4] / "services" / "agent" / "src")
if _AGENT_SRC not in sys.path:
    sys.path.insert(0, _AGENT_SRC)

POOL = "us-west-2_TESTPOOL"
CLIENT = "test-client-id"
REGION = "us-west-2"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GRACE2_COGNITO_USER_POOL_ID", POOL)
    monkeypatch.setenv("GRACE2_COGNITO_CLIENT_ID", CLIENT)
    monkeypatch.setenv("GRACE2_AWS_REGION", REGION)


def _install_fake_jwt(monkeypatch, *, header, claims, decode_raises=False):
    """Install a fake ``jwt`` module so both verifiers run their full logic
    WITHOUT a real signature/JWKS. Both call the same get_unverified_header /
    decode, so feeding both the same fake exercises the claim-handling logic
    (token_use, aud, sub, return shape) which is where drift would live."""
    fake = types.ModuleType("jwt")

    def get_unverified_header(_token):
        return header

    def decode(_token, _key, **_kwargs):
        if decode_raises:
            raise ValueError("bad signature")
        return claims

    fake.get_unverified_header = get_unverified_header
    fake.decode = decode

    algos = types.ModuleType("jwt.algorithms")

    class RSAAlgorithm:
        @staticmethod
        def from_jwk(_jwk):
            return "FAKE_PUBLIC_KEY"

    algos.RSAAlgorithm = RSAAlgorithm
    fake.algorithms = algos

    monkeypatch.setitem(sys.modules, "jwt", fake)
    monkeypatch.setitem(sys.modules, "jwt.algorithms", algos)


@pytest.fixture
def real_verify(monkeypatch):
    try:
        ah = importlib.import_module("grace2_agent.auth_handshake")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"grace2_agent.auth_handshake not importable: {type(exc).__name__}")
    # Both impls call _get_jwk(issuer, kid); stub the agent's to return a dummy.
    monkeypatch.setattr(ah, "_get_jwk", lambda issuer, kid: {"kid": kid})
    return ah.cognito_verify


@pytest.fixture
def vendored_verify(monkeypatch):
    import broker.cognito_verify as cv

    importlib.reload(cv)
    # Force the vendored path (ignore any importable agent function) so THIS test
    # exercises the fallback transcription, not the import alias.
    monkeypatch.setattr(cv, "_real_cognito_verify", None)
    monkeypatch.setattr(cv, "_get_jwk", lambda issuer, kid: {"kid": kid})
    return cv.cognito_verify


# A matrix of (header, claims, decode_raises) covering every branch the verifier
# has: valid id token, wrong token_use, wrong aud, missing sub, missing kid,
# decode failure.
_CASES = [
    pytest.param(
        {"kid": "k1"},
        {"token_use": "id", "aud": CLIENT, "iss": ISSUER, "sub": "sub-1", "email": "a@b.c", "name": "A"},
        False,
        id="valid_id_token",
    ),
    pytest.param(
        {"kid": "k1"},
        {"token_use": "access", "aud": CLIENT, "iss": ISSUER, "sub": "sub-2"},
        False,
        id="wrong_token_use",
    ),
    pytest.param(
        {"kid": "k1"},
        {"token_use": "id", "aud": "other-client", "iss": ISSUER, "sub": "sub-3"},
        False,
        id="wrong_aud",
    ),
    pytest.param(
        {"kid": "k1"},
        {"token_use": "id", "aud": CLIENT, "iss": ISSUER},
        False,
        id="missing_sub",
    ),
    pytest.param(
        {},  # no kid
        {"token_use": "id", "aud": CLIENT, "iss": ISSUER, "sub": "sub-4"},
        False,
        id="missing_kid",
    ),
    pytest.param(
        {"kid": "k1"},
        {},
        True,
        id="decode_raises",
    ),
]


@pytest.mark.parametrize("header,claims,decode_raises", _CASES)
def test_vendored_matches_agent(monkeypatch, real_verify, vendored_verify, header, claims, decode_raises):
    _install_fake_jwt(monkeypatch, header=header, claims=claims, decode_raises=decode_raises)
    expected = real_verify("dummy.token.value")
    actual = vendored_verify("dummy.token.value")
    assert actual == expected, (
        f"DRIFT: vendored broker cognito_verify diverged from the agent's for "
        f"case header={header} claims={claims}: agent={expected!r} broker={actual!r}"
    )


def test_no_pool_both_anonymous(monkeypatch, real_verify, vendored_verify):
    monkeypatch.delenv("GRACE2_COGNITO_USER_POOL_ID", raising=False)
    assert real_verify("x") is None
    assert vendored_verify("x") is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
