"""Unit tests for the signed-URL minting Cloud Function — job-0251.

PURE-PYTHON: no GCP/Firebase SDKs required. Every backend (token verify, Case
read, URL signing) is injected as a fake through ``main._Deps`` so the suite runs
on a plain interpreter (e.g. ``services/agent/.venv/bin/python``) with nothing
but pytest installed.

Run:
    services/agent/.venv/bin/python -m pytest infra/signed_urls/test_mint_signed_url.py -v
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import main  # noqa: E402


# --------------------------------------------------------------------------- #
# Fakes + fixtures
# --------------------------------------------------------------------------- #


class FakeRequest:
    """Minimal Flask-request stand-in for handle_request tests."""

    def __init__(self, *, method="POST", headers=None, body=None):
        self.method = method
        self.headers = headers or {}
        self._body = body

    def get_json(self, silent=False):
        if isinstance(self._body, dict):
            return self._body
        return None

    @property
    def data(self):
        if isinstance(self._body, (str, bytes, bytearray)):
            return self._body
        return None


def make_deps(
    *,
    owner="user-alice",
    case_exists=True,
    sign=None,
    verify=None,
):
    """Build a fully-faked _Deps. owner=None → orphan Case (no user_id)."""
    case_doc = None
    if case_exists:
        case_doc = {"_id": "case-1", "title": "T"}
        if owner is not None:
            case_doc["user_id"] = owner

    sign_calls = []

    def _sign(bucket, obj, ttl, method):
        sign_calls.append((bucket, obj, ttl, method))
        return (
            f"https://storage.googleapis.com/{bucket}/{obj}"
            f"?X-Goog-Expires={ttl}&X-Goog-Signature=deadbeef"
        )

    deps = main._Deps(
        verify_id_token=verify or (lambda tok: {"uid": "user-alice"}),
        fetch_case_doc=lambda cid: case_doc if cid == "case-1" else None,
        sign_url=sign or _sign,
    )
    deps._sign_calls = sign_calls  # type: ignore[attr-defined]
    return deps


@pytest.fixture(autouse=True)
def _reset_deps_and_env():
    """Reset the module singleton + env between tests."""
    saved = main._DEPS
    main._DEPS = main._Deps()
    saved_buckets = os.environ.pop("GRACE2_SIGNED_URL_BUCKETS", None)
    yield
    main._DEPS = saved
    if saved_buckets is not None:
        os.environ["GRACE2_SIGNED_URL_BUCKETS"] = saved_buckets
    else:
        os.environ.pop("GRACE2_SIGNED_URL_BUCKETS", None)


# --------------------------------------------------------------------------- #
# clamp_ttl
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "given,expected",
    [
        (3600, 3600),
        (1800, 1800),
        (900, 900),
        (899, 900),       # below floor → clamped up
        (0, 900),
        (-5, 900),
        (3601, 3600),     # above ceiling → clamped down
        (100000, 3600),
        ("not-an-int", 3600),  # garbage → default
        (None, 3600),
    ],
)
def test_clamp_ttl(given, expected):
    assert main.clamp_ttl(given) == expected


def test_clamp_window_constants():
    assert main.MIN_TTL_SECONDS == 900
    assert main.MAX_TTL_SECONDS == 3600


# --------------------------------------------------------------------------- #
# parse_layer_uri
# --------------------------------------------------------------------------- #


def test_parse_layer_uri_ok():
    bucket, obj = main.parse_layer_uri("gs://grace-2-runs/cases/c1/flood.tif")
    assert bucket == "grace-2-runs"
    assert obj == "cases/c1/flood.tif"


@pytest.mark.parametrize(
    "bad",
    [
        "https://storage.googleapis.com/b/o",  # not gs://
        "gs://just-a-bucket",                  # no object path
        "gs:///object",                        # empty bucket
        "gs://bucket/",                        # empty object
        "",
        None,
        123,
    ],
)
def test_parse_layer_uri_rejects(bad):
    with pytest.raises(main.BadRequest):
        main.parse_layer_uri(bad)


def test_parse_layer_uri_rejects_traversal():
    with pytest.raises(main.BadRequest):
        main.parse_layer_uri("gs://b/a/../../etc/passwd")


def test_parse_layer_uri_bucket_allowlist():
    os.environ["GRACE2_SIGNED_URL_BUCKETS"] = "good-bucket,other-bucket"
    bucket, obj = main.parse_layer_uri("gs://good-bucket/o.tif")
    assert bucket == "good-bucket"
    with pytest.raises(main.Forbidden):
        main.parse_layer_uri("gs://evil-bucket/o.tif")


# --------------------------------------------------------------------------- #
# case_owned_by
# --------------------------------------------------------------------------- #


def test_case_owned_by_user_id():
    assert main.case_owned_by({"user_id": "u1"}, "u1") is True


def test_case_owned_by_owner_user_id_alias():
    assert main.case_owned_by({"owner_user_id": "u1"}, "u1") is True


def test_case_owned_by_wrong_user():
    assert main.case_owned_by({"user_id": "u1"}, "u2") is False


def test_case_owned_by_orphan_fails_closed():
    # No user_id / owner_user_id at all → NOT mintable (no $exists:False clause).
    assert main.case_owned_by({"_id": "c1", "title": "x"}, "u1") is False


def test_case_owned_by_none_doc():
    assert main.case_owned_by(None, "u1") is False


# --------------------------------------------------------------------------- #
# extract_bearer_token
# --------------------------------------------------------------------------- #


def test_extract_bearer_token_ok():
    assert main.extract_bearer_token({"Authorization": "Bearer abc.def.ghi"}) == "abc.def.ghi"


def test_extract_bearer_token_lowercase_header():
    assert main.extract_bearer_token({"authorization": "Bearer xyz"}) == "xyz"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "abc"},          # no Bearer scheme
        {"Authorization": "Basic abc"},    # wrong scheme
        {"Authorization": "Bearer "},      # empty token
        {"Authorization": "Bearer"},       # no token part
    ],
)
def test_extract_bearer_token_rejects(headers):
    with pytest.raises(main.Unauthorized):
        main.extract_bearer_token(headers)


# --------------------------------------------------------------------------- #
# mint_signed_url (core)
# --------------------------------------------------------------------------- #


def test_mint_happy_path():
    deps = make_deps(owner="user-alice")
    out = main.mint_signed_url(
        layer_uri="gs://grace-2-runs/cases/case-1/flood.tif",
        user_id="user-alice",
        case_id="case-1",
        ttl_seconds=1800,
        verified_uid="user-alice",
        deps=deps,
    )
    assert out["expires_in"] == 1800
    assert out["bucket"] == "grace-2-runs"
    assert out["object"] == "cases/case-1/flood.tif"
    assert "X-Goog-Signature=" in out["signed_url"]
    # The signer was asked for a GET with the clamped TTL.
    assert deps._sign_calls == [("grace-2-runs", "cases/case-1/flood.tif", 1800, "GET")]


def test_mint_clamps_ttl_into_window():
    deps = make_deps()
    out = main.mint_signed_url(
        "gs://b/o.tif", "user-alice", "case-1", ttl_seconds=999999,
        verified_uid="user-alice", deps=deps,
    )
    assert out["expires_in"] == main.MAX_TTL_SECONDS
    out2 = main.mint_signed_url(
        "gs://b/o.tif", "user-alice", "case-1", ttl_seconds=10,
        verified_uid="user-alice", deps=deps,
    )
    assert out2["expires_in"] == main.MIN_TTL_SECONDS


def test_mint_default_ttl():
    deps = make_deps()
    out = main.mint_signed_url(
        "gs://b/o.tif", "user-alice", "case-1",
        verified_uid="user-alice", deps=deps,
    )
    assert out["expires_in"] == main.DEFAULT_TTL_SECONDS == 3600


def test_mint_rejects_token_body_mismatch():
    """The verified uid != body user_id → Forbidden (never trust the body)."""
    deps = make_deps(owner="user-alice")
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://b/o.tif",
            user_id="user-mallory",   # body claims someone else
            case_id="case-1",
            verified_uid="user-alice",  # but the token proves alice
            deps=deps,
        )


def test_mint_rejects_wrong_owner():
    """Token uid matches body, but that user doesn't own the case → Forbidden."""
    deps = make_deps(owner="user-bob")  # case owned by bob
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://b/o.tif", "user-alice", "case-1",
            verified_uid="user-alice", deps=deps,
        )


def test_mint_orphan_case_not_mintable():
    deps = make_deps(owner=None)  # case with no owner field
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://b/o.tif", "user-alice", "case-1",
            verified_uid="user-alice", deps=deps,
        )


def test_mint_case_not_found():
    deps = make_deps()
    with pytest.raises(main.NotFound):
        main.mint_signed_url(
            "gs://b/o.tif", "user-alice", "case-MISSING",
            verified_uid="user-alice", deps=deps,
        )


def test_mint_bad_layer_uri():
    deps = make_deps()
    with pytest.raises(main.BadRequest):
        main.mint_signed_url(
            "not-a-gs-uri", "user-alice", "case-1",
            verified_uid="user-alice", deps=deps,
        )


@pytest.mark.parametrize("missing", ["user_id", "case_id"])
def test_mint_missing_required(missing):
    deps = make_deps()
    kwargs = dict(
        layer_uri="gs://b/o.tif", user_id="user-alice", case_id="case-1",
        verified_uid="user-alice", deps=deps,
    )
    kwargs[missing] = ""
    with pytest.raises(main.BadRequest):
        main.mint_signed_url(**kwargs)


def test_mint_owner_alias_owner_user_id():
    deps = make_deps(owner=None)
    # patch the doc to use the alias field
    deps.fetch_case_doc = lambda cid: {"_id": "case-1", "owner_user_id": "user-alice"}
    out = main.mint_signed_url(
        "gs://b/o.tif", "user-alice", "case-1",
        verified_uid="user-alice", deps=deps,
    )
    assert out["bucket"] == "b"


def test_mint_no_verified_uid_skips_match_check():
    """When called WITHOUT a verified uid (internal/test path), the body
    user_id is trusted for ownership only — there is no token to compare."""
    deps = make_deps(owner="user-alice")
    out = main.mint_signed_url(
        "gs://b/o.tif", "user-alice", "case-1", deps=deps,
    )
    assert out["expires_in"] == 3600


# --------------------------------------------------------------------------- #
# handle_request (HTTP wrapper)
# --------------------------------------------------------------------------- #


def _install_deps(deps):
    main._DEPS = deps


def test_http_happy_path():
    _install_deps(make_deps(owner="user-alice", verify=lambda t: {"uid": "user-alice"}))
    req = FakeRequest(
        headers={"Authorization": "Bearer good-token"},
        body={
            "layer_uri": "gs://grace-2-runs/cases/case-1/flood.tif",
            "user_id": "user-alice",
            "case_id": "case-1",
            "ttl_seconds": 1200,
        },
    )
    body, status, headers = main.handle_request(req)
    assert status == 200
    payload = json.loads(body)
    assert payload["expires_in"] == 1200
    assert "X-Goog-Signature=" in payload["signed_url"]
    assert headers["Content-Type"] == "application/json"


def test_http_missing_auth_header_401():
    _install_deps(make_deps())
    req = FakeRequest(headers={}, body={"layer_uri": "gs://b/o", "user_id": "u", "case_id": "c"})
    body, status, _ = main.handle_request(req)
    assert status == 401


def test_http_invalid_token_401():
    def _boom(_tok):
        raise ValueError("bad signature")

    _install_deps(make_deps(verify=_boom))
    req = FakeRequest(
        headers={"Authorization": "Bearer forged"},
        body={"layer_uri": "gs://b/o.tif", "user_id": "user-alice", "case_id": "case-1"},
    )
    body, status, _ = main.handle_request(req)
    assert status == 401
    assert json.loads(body)["error"]


def test_http_token_uid_body_mismatch_403():
    _install_deps(make_deps(owner="user-alice", verify=lambda t: {"uid": "user-alice"}))
    req = FakeRequest(
        headers={"Authorization": "Bearer good"},
        body={
            "layer_uri": "gs://b/o.tif",
            "user_id": "user-mallory",  # body lies
            "case_id": "case-1",
        },
    )
    body, status, _ = main.handle_request(req)
    assert status == 403


def test_http_wrong_owner_403():
    _install_deps(make_deps(owner="user-bob", verify=lambda t: {"uid": "user-alice"}))
    req = FakeRequest(
        headers={"Authorization": "Bearer good"},
        body={"layer_uri": "gs://b/o.tif", "user_id": "user-alice", "case_id": "case-1"},
    )
    body, status, _ = main.handle_request(req)
    assert status == 403


def test_http_case_not_found_404():
    _install_deps(make_deps(verify=lambda t: {"uid": "user-alice"}))
    req = FakeRequest(
        headers={"Authorization": "Bearer good"},
        body={"layer_uri": "gs://b/o.tif", "user_id": "user-alice", "case_id": "nope"},
    )
    body, status, _ = main.handle_request(req)
    assert status == 404


def test_http_uses_sub_when_no_uid_claim():
    # firebase verify_id_token returns 'uid'; some decoders surface 'sub'.
    _install_deps(make_deps(owner="user-alice", verify=lambda t: {"sub": "user-alice"}))
    req = FakeRequest(
        headers={"Authorization": "Bearer good"},
        body={"layer_uri": "gs://b/o.tif", "user_id": "user-alice", "case_id": "case-1"},
    )
    body, status, _ = main.handle_request(req)
    assert status == 200


def test_http_non_post_rejected():
    _install_deps(make_deps())
    req = FakeRequest(method="GET", headers={"Authorization": "Bearer t"})
    body, status, _ = main.handle_request(req)
    assert status == 400


def test_http_bad_json_body_400():
    _install_deps(make_deps(verify=lambda t: {"uid": "user-alice"}))
    req = FakeRequest(headers={"Authorization": "Bearer good"}, body="{not-json")
    body, status, _ = main.handle_request(req)
    assert status == 400


def test_http_token_uid_empty_401():
    _install_deps(make_deps(verify=lambda t: {}))  # no uid/sub
    req = FakeRequest(
        headers={"Authorization": "Bearer good"},
        body={"layer_uri": "gs://b/o.tif", "user_id": "user-alice", "case_id": "case-1"},
    )
    body, status, _ = main.handle_request(req)
    assert status == 401


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
