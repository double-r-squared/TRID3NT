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

# Identities (job-0251b / Decision 10): the FIREBASE uid and the INTERNAL
# users._id ULID are deliberately DISTINCT values in every fixture, so any
# regression back to a raw-uid comparison (the panel-refuted bug) cannot pass
# by accident. Case owner fields hold ULIDs; tokens/bodies hold Firebase uids.
FB_ALICE = "fb-uid-alice"
ULID_ALICE = "01JXULIDALICE000000000000A"
FB_BOB = "fb-uid-bob"
ULID_BOB = "01JXULIDBOB0000000000000B"

#: Pinned to services/agent/src/grace2_agent/auth.py::MIGRATION_ANON_UID —
#: the sentinel job-0252's startup migration stamps on pre-Auth orphan Cases.
#: Deliberately a literal here (the function has no agent-package dependency).
MIGRATION_ANON_UID = "__preauth_migration_anon__"


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
    owner=ULID_ALICE,
    case_exists=True,
    sign=None,
    verify=None,
    users=None,
    fetch_user=None,
):
    """Build a fully-faked _Deps.

    owner: the Case doc's ``user_id`` (an INTERNAL ULID) — None → orphan Case.
    users: ``{firebase_uid: users_doc}`` registry — defaults to alice + bob
        with distinct firebase_uid → internal-ULID mappings.
    fetch_user: overrides the users-doc fetcher entirely (e.g. to raise).
    """
    case_doc = None
    if case_exists:
        case_doc = {"_id": "case-1", "title": "T"}
        if owner is not None:
            case_doc["user_id"] = owner

    if users is None:
        users = {
            FB_ALICE: {"_id": ULID_ALICE, "firebase_uid": FB_ALICE},
            FB_BOB: {"_id": ULID_BOB, "firebase_uid": FB_BOB},
        }

    sign_calls = []

    def _sign(bucket, obj, ttl, method):
        sign_calls.append((bucket, obj, ttl, method))
        return (
            f"https://storage.googleapis.com/{bucket}/{obj}"
            f"?X-Goog-Expires={ttl}&X-Goog-Signature=deadbeef"
        )

    deps = main._Deps(
        verify_id_token=verify or (lambda tok: {"uid": FB_ALICE}),
        fetch_user_doc=fetch_user or (lambda fb_uid: users.get(fb_uid)),
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
        # job-0251b panel nit: int(float("inf")) raises OverflowError, which the
        # old (TypeError, ValueError) catch missed → 500 instead of the
        # documented fall-back-to-default. All three now → DEFAULT_TTL_SECONDS.
        (float("inf"), 3600),   # OverflowError
        (float("-inf"), 3600),  # OverflowError
        (1e400, 3600),          # float literal overflow → inf → OverflowError
        (float("nan"), 3600),   # ValueError — confirm still covered
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
# resolve_internal_user_id (job-0251b — Decision 10 owner-identity resolution)
# --------------------------------------------------------------------------- #


def test_resolve_internal_user_id_from_id():
    doc = {"_id": ULID_ALICE, "firebase_uid": FB_ALICE}
    assert main.resolve_internal_user_id(doc) == ULID_ALICE


def test_resolve_internal_user_id_prefers_id_over_user_id_key():
    # _id is authoritative; user_id is the fallback key (mirrors
    # Persistence.get_user_by_firebase_uid normalization).
    doc = {"_id": ULID_ALICE, "user_id": "something-else", "firebase_uid": FB_ALICE}
    assert main.resolve_internal_user_id(doc) == ULID_ALICE


def test_resolve_internal_user_id_user_id_key_fallback():
    doc = {"user_id": ULID_ALICE, "firebase_uid": FB_ALICE}
    assert main.resolve_internal_user_id(doc) == ULID_ALICE


@pytest.mark.parametrize(
    "doc",
    [
        None,                                  # no users doc at all
        {},                                    # empty doc
        {"firebase_uid": FB_ALICE},            # no _id / user_id key
        {"_id": "", "firebase_uid": FB_ALICE}, # empty id
        {"_id": 12345},                        # non-string id (e.g. ObjectId)
        "not-a-dict",                          # malformed transport result
    ],
)
def test_resolve_internal_user_id_fails_closed(doc):
    assert main.resolve_internal_user_id(doc) is None


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
    # The kickoff's canonical owner-mint chain: case doc user_id=<internal
    # ULID>; users doc {_id: <ULID>, firebase_uid: <uid>}; token uid ==
    # body.user_id == <uid> → 200 signed URL.
    deps = make_deps(owner=ULID_ALICE)
    out = main.mint_signed_url(
        layer_uri="gs://grace-2-runs/cases/case-1/flood.tif",
        user_id=FB_ALICE,
        case_id="case-1",
        ttl_seconds=1800,
        verified_uid=FB_ALICE,
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
        "gs://b/o.tif", FB_ALICE, "case-1", ttl_seconds=999999,
        verified_uid=FB_ALICE, deps=deps,
    )
    assert out["expires_in"] == main.MAX_TTL_SECONDS
    out2 = main.mint_signed_url(
        "gs://b/o.tif", FB_ALICE, "case-1", ttl_seconds=10,
        verified_uid=FB_ALICE, deps=deps,
    )
    assert out2["expires_in"] == main.MIN_TTL_SECONDS


def test_mint_default_ttl():
    deps = make_deps()
    out = main.mint_signed_url(
        "gs://b/o.tif", FB_ALICE, "case-1",
        verified_uid=FB_ALICE, deps=deps,
    )
    assert out["expires_in"] == main.DEFAULT_TTL_SECONDS == 3600


def test_mint_rejects_token_body_mismatch():
    """The verified uid != body user_id → Forbidden (never trust the body)."""
    deps = make_deps(owner=ULID_ALICE)
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://b/o.tif",
            user_id="fb-uid-mallory",  # body claims someone else
            case_id="case-1",
            verified_uid=FB_ALICE,     # but the token proves alice
            deps=deps,
        )


def test_mint_rejects_wrong_owner():
    """Token resolves fine, but the resolved user doesn't own the case → 403."""
    deps = make_deps(owner=ULID_BOB)  # case owned by bob's INTERNAL ULID
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://b/o.tif", FB_ALICE, "case-1",
            verified_uid=FB_ALICE, deps=deps,
        )


def test_mint_orphan_case_not_mintable():
    deps = make_deps(owner=None)  # case with no owner field
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://b/o.tif", FB_ALICE, "case-1",
            verified_uid=FB_ALICE, deps=deps,
        )


def test_mint_case_not_found():
    deps = make_deps()
    with pytest.raises(main.NotFound):
        main.mint_signed_url(
            "gs://b/o.tif", FB_ALICE, "case-MISSING",
            verified_uid=FB_ALICE, deps=deps,
        )


def test_mint_bad_layer_uri():
    deps = make_deps()
    with pytest.raises(main.BadRequest):
        main.mint_signed_url(
            "not-a-gs-uri", FB_ALICE, "case-1",
            verified_uid=FB_ALICE, deps=deps,
        )


@pytest.mark.parametrize("missing", ["user_id", "case_id"])
def test_mint_missing_required(missing):
    deps = make_deps()
    kwargs = dict(
        layer_uri="gs://b/o.tif", user_id=FB_ALICE, case_id="case-1",
        verified_uid=FB_ALICE, deps=deps,
    )
    kwargs[missing] = ""
    with pytest.raises(main.BadRequest):
        main.mint_signed_url(**kwargs)


def test_mint_owner_alias_owner_user_id():
    deps = make_deps(owner=None)
    # patch the doc to use the alias field — holding the INTERNAL ULID
    deps.fetch_case_doc = lambda cid: {"_id": "case-1", "owner_user_id": ULID_ALICE}
    out = main.mint_signed_url(
        "gs://b/o.tif", FB_ALICE, "case-1",
        verified_uid=FB_ALICE, deps=deps,
    )
    assert out["bucket"] == "b"


def test_mint_no_verified_uid_skips_match_check():
    """When called WITHOUT a verified uid (internal/test path), there is no
    token to compare the body against — but the body's Firebase uid still goes
    through users-collection resolution; nothing bypasses Decision 10."""
    deps = make_deps(owner=ULID_ALICE)
    out = main.mint_signed_url(
        "gs://b/o.tif", FB_ALICE, "case-1", deps=deps,
    )
    assert out["expires_in"] == 3600


# --------------------------------------------------------------------------- #
# mint_signed_url — owner-identity resolution (job-0251b / Decision 10)
# --------------------------------------------------------------------------- #


def test_mint_firebase_uid_with_no_users_doc_403():
    """A Firebase user who has never connected to the agent owns nothing."""
    deps = make_deps(owner=ULID_ALICE, users={})  # empty users collection
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://b/o.tif", FB_ALICE, "case-1",
            verified_uid=FB_ALICE, deps=deps,
        )
    assert deps._sign_calls == []  # nothing was minted


def test_mint_second_user_cannot_mint_first_users_case():
    """Bob's firebase_uid resolves to ULID_BOB — never to alice's case owner."""
    deps = make_deps(owner=ULID_ALICE)  # alice's case
    deps.verify_id_token = lambda tok: {"uid": FB_BOB}
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://b/o.tif", FB_BOB, "case-1",
            verified_uid=FB_BOB, deps=deps,
        )
    assert deps._sign_calls == []


def test_mint_case_storing_raw_firebase_uid_not_mintable():
    """Regression guard on the panel-refuted bug: a Case doc whose owner field
    (wrongly) holds the raw FIREBASE uid must NOT match — the ownership
    comparison runs against the resolved internal ULID only."""
    deps = make_deps(owner=FB_ALICE)  # non-conformant doc: firebase uid as owner
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://b/o.tif", FB_ALICE, "case-1",
            verified_uid=FB_ALICE, deps=deps,
        )
    assert deps._sign_calls == []


def test_mint_migration_anon_owned_case_unmintable_by_any_user():
    """MIGRATION_ANON_UID-owned Cases (pre-auth orphans) are unmintable by ANY
    Firebase user, by design — no users doc maps a firebase_uid to the
    sentinel, so resolution can never produce it."""
    deps = make_deps(owner=MIGRATION_ANON_UID)
    for fb_uid in (FB_ALICE, FB_BOB):
        with pytest.raises(main.Forbidden):
            main.mint_signed_url(
                "gs://b/o.tif", fb_uid, "case-1",
                verified_uid=fb_uid, deps=deps,
            )
    assert deps._sign_calls == []


def test_mint_forged_sentinel_token_still_403():
    """Even a token whose uid IS the migration sentinel resolves to nothing
    (no users doc carries firebase_uid == sentinel) → 403, not ownership."""
    deps = make_deps(owner=MIGRATION_ANON_UID)
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://b/o.tif", MIGRATION_ANON_UID, "case-1",
            verified_uid=MIGRATION_ANON_UID, deps=deps,
        )
    assert deps._sign_calls == []


def test_mint_users_lookup_failure_fails_closed_503():
    """A users-collection lookup ERROR is a 503 — and must NEVER fall through
    to a raw-uid comparison. The case doc here deliberately stores the raw
    Firebase uid so a fall-through WOULD succeed; it must not."""
    def _boom(_fb_uid):
        raise RuntimeError("Atlas unreachable")

    deps = make_deps(owner=FB_ALICE, fetch_user=_boom)
    with pytest.raises(main.ServiceUnavailable) as exc_info:
        main.mint_signed_url(
            "gs://b/o.tif", FB_ALICE, "case-1",
            verified_uid=FB_ALICE, deps=deps,
        )
    assert exc_info.value.status == 503
    assert deps._sign_calls == []


def test_mint_malformed_users_doc_fails_closed():
    """A users doc with no usable internal id → 403 (not a crash, not a mint)."""
    deps = make_deps(
        owner=ULID_ALICE,
        users={FB_ALICE: {"firebase_uid": FB_ALICE}},  # no _id / user_id
    )
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://b/o.tif", FB_ALICE, "case-1",
            verified_uid=FB_ALICE, deps=deps,
        )
    assert deps._sign_calls == []


# --------------------------------------------------------------------------- #
# handle_request (HTTP wrapper)
# --------------------------------------------------------------------------- #


def _install_deps(deps):
    main._DEPS = deps


def test_http_happy_path():
    # Full documented chain over the wire: token uid == body.user_id (Firebase
    # uid) → users-collection resolution → internal-ULID ownership → mint.
    _install_deps(make_deps(owner=ULID_ALICE, verify=lambda t: {"uid": FB_ALICE}))
    req = FakeRequest(
        headers={"Authorization": "Bearer good-token"},
        body={
            "layer_uri": "gs://grace-2-runs/cases/case-1/flood.tif",
            "user_id": FB_ALICE,
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
        body={"layer_uri": "gs://b/o.tif", "user_id": FB_ALICE, "case_id": "case-1"},
    )
    body, status, _ = main.handle_request(req)
    assert status == 401
    assert json.loads(body)["error"]


def test_http_token_uid_body_mismatch_403():
    _install_deps(make_deps(owner=ULID_ALICE, verify=lambda t: {"uid": FB_ALICE}))
    req = FakeRequest(
        headers={"Authorization": "Bearer good"},
        body={
            "layer_uri": "gs://b/o.tif",
            "user_id": "fb-uid-mallory",  # body lies
            "case_id": "case-1",
        },
    )
    body, status, _ = main.handle_request(req)
    assert status == 403


def test_http_wrong_owner_403():
    _install_deps(make_deps(owner=ULID_BOB, verify=lambda t: {"uid": FB_ALICE}))
    req = FakeRequest(
        headers={"Authorization": "Bearer good"},
        body={"layer_uri": "gs://b/o.tif", "user_id": FB_ALICE, "case_id": "case-1"},
    )
    body, status, _ = main.handle_request(req)
    assert status == 403


def test_http_case_not_found_404():
    _install_deps(make_deps(verify=lambda t: {"uid": FB_ALICE}))
    req = FakeRequest(
        headers={"Authorization": "Bearer good"},
        body={"layer_uri": "gs://b/o.tif", "user_id": FB_ALICE, "case_id": "nope"},
    )
    body, status, _ = main.handle_request(req)
    assert status == 404


def test_http_uses_sub_when_no_uid_claim():
    # firebase verify_id_token returns 'uid'; some decoders surface 'sub'.
    _install_deps(make_deps(owner=ULID_ALICE, verify=lambda t: {"sub": FB_ALICE}))
    req = FakeRequest(
        headers={"Authorization": "Bearer good"},
        body={"layer_uri": "gs://b/o.tif", "user_id": FB_ALICE, "case_id": "case-1"},
    )
    body, status, _ = main.handle_request(req)
    assert status == 200


def test_http_no_users_doc_403():
    """Wire-level: a verified Firebase user with no users doc gets 403."""
    _install_deps(make_deps(owner=ULID_ALICE, users={}, verify=lambda t: {"uid": FB_ALICE}))
    req = FakeRequest(
        headers={"Authorization": "Bearer good"},
        body={"layer_uri": "gs://b/o.tif", "user_id": FB_ALICE, "case_id": "case-1"},
    )
    body, status, _ = main.handle_request(req)
    assert status == 403
    assert json.loads(body)["error"]


def test_http_users_lookup_failure_503():
    """Wire-level: a users-collection lookup error surfaces as 503, no mint."""
    def _boom(_fb_uid):
        raise RuntimeError("Atlas unreachable")

    deps = make_deps(owner=ULID_ALICE, fetch_user=_boom, verify=lambda t: {"uid": FB_ALICE})
    _install_deps(deps)
    req = FakeRequest(
        headers={"Authorization": "Bearer good"},
        body={"layer_uri": "gs://b/o.tif", "user_id": FB_ALICE, "case_id": "case-1"},
    )
    body, status, _ = main.handle_request(req)
    assert status == 503
    assert deps._sign_calls == []


def test_http_migration_anon_case_403():
    """Wire-level: a MIGRATION_ANON_UID-owned (pre-auth orphan) Case is not
    mintable by a legitimate signed-in user."""
    _install_deps(make_deps(owner=MIGRATION_ANON_UID, verify=lambda t: {"uid": FB_ALICE}))
    req = FakeRequest(
        headers={"Authorization": "Bearer good"},
        body={"layer_uri": "gs://b/o.tif", "user_id": FB_ALICE, "case_id": "case-1"},
    )
    body, status, _ = main.handle_request(req)
    assert status == 403


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
