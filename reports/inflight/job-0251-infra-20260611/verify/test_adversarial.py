"""ADVERSARIAL verifier tests for job-0251 signed-URL minter (panel CORRECTNESS).

These probe BEYOND the runner's 55 tests. Each asks: can a malicious/edge input
reach signing when it must not, or does the function fail closed?

Run:
    services/agent/.venv/bin/python -m pytest \
      reports/inflight/job-0251-infra-20260611/verify/test_adversarial.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Import the function under test from its own directory.
SIGNED_URLS_DIR = Path(__file__).resolve().parents[4] / "infra" / "signed_urls"
sys.path.insert(0, str(SIGNED_URLS_DIR))

import main  # noqa: E402


# --------------------------------------------------------------------------- #
# Deps factory that RECORDS whether sign_url was ever reached. The core attack
# question: did we MINT when we should have rejected?
# --------------------------------------------------------------------------- #


def make_recording_deps(*, case_doc, verified_uid_claim="user-alice"):
    sign_calls = []

    def _sign(bucket, obj, ttl, method):
        sign_calls.append((bucket, obj, ttl, method))
        return f"https://signed/{bucket}/{obj}?ttl={ttl}"

    deps = main._Deps(
        verify_id_token=lambda tok: {"uid": verified_uid_claim},
        fetch_case_doc=lambda cid: case_doc,
        sign_url=_sign,
    )
    deps._sign_calls = sign_calls  # type: ignore[attr-defined]
    return deps


@pytest.fixture(autouse=True)
def _clean_env():
    saved = os.environ.pop("GRACE2_SIGNED_URL_BUCKETS", None)
    saved_deps = main._DEPS
    main._DEPS = main._Deps()
    yield
    main._DEPS = saved_deps
    if saved is not None:
        os.environ["GRACE2_SIGNED_URL_BUCKETS"] = saved
    else:
        os.environ.pop("GRACE2_SIGNED_URL_BUCKETS", None)


# --------------------------------------------------------------------------- #
# ATTACK 1 — TTL clamp exact boundary values + adversarial types.
# The clamp window [900,3600] IS the security property (no long-lived URL).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "given,expected",
    [
        (899, 900),
        (900, 900),
        (901, 901),
        (3599, 3599),
        (3600, 3600),
        (3601, 3600),
        (-1, 900),
        (0, 900),
        (10**9, 3600),
        (True, 900),        # bool is int subclass: int(True)=1 -> below floor
        (900.9, 900),       # float floors via int() then clamps
        (3600.9, 3600),
        ("1500", 1500),     # numeric string parses
        ("3600.5", 3600),   # int("3600.5") raises ValueError -> default 3600
        (float("nan"), 3600),  # int(nan) raises ValueError -> default
        (float("inf"), 3600),  # int(inf) raises OverflowError... must NOT escape clamp
        ([], 3600),         # unindexable -> TypeError -> default
        ({}, 3600),
    ],
)
def test_ttl_clamp_never_escapes_window(given, expected):
    try:
        out = main.clamp_ttl(given)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"clamp_ttl({given!r}) raised {exc!r} instead of clamping")
    assert out == expected, f"clamp_ttl({given!r}) = {out}, expected {expected}"
    # Hard invariant: NOTHING ever leaves the window.
    assert 900 <= out <= 3600


def test_ttl_inf_does_not_escape_clamp():
    # The dangerous case: a value that makes int() raise OverflowError. If the
    # except clause only catches (TypeError, ValueError), inf would crash and
    # potentially be handled elsewhere. Verify it is clamped, never propagates.
    out = main.clamp_ttl(float("inf"))
    assert 900 <= out <= 3600


# --------------------------------------------------------------------------- #
# ATTACK 2 — gs:// parsing adversarial inputs. Can a crafted URI escape the
# object scope, hit a non-allowlisted bucket, or smuggle traversal?
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "uri",
    [
        "gs://bucket/../../etc/passwd",          # leading traversal segment
        "gs://bucket/a/../../../secret",          # mid traversal
        "gs://bucket/sub/..",                     # trailing .. segment
        "gs://bucket/..",                         # bare ..
    ],
)
def test_parse_rejects_traversal_variants(uri):
    with pytest.raises(main.BadRequest):
        main.parse_layer_uri(uri)


def test_parse_traversal_dotdot_substring_in_name_is_allowed():
    # ".." only rejected as a WHOLE path segment, not as a substring of a name.
    # This is correct behavior — "..foo" is a legal object name. Document it.
    b, o = main.parse_layer_uri("gs://bucket/my..file.tif")
    assert b == "bucket" and o == "my..file.tif"


def test_parse_percent_encoded_traversal_is_NOT_decoded():
    # %2e%2e is NOT decoded by parse — so it passes the .. check as a literal
    # object name. GCS treats it as a literal key, so no real escape. Document
    # that the guard is segment-literal, and that GCS object semantics make a
    # percent-encoded key just a (weird) literal key, not a path escape.
    b, o = main.parse_layer_uri("gs://bucket/%2e%2e/%2e%2e/etc")
    assert b == "bucket"
    assert o == "%2e%2e/%2e%2e/etc"   # literal, no traversal segment


@pytest.mark.parametrize(
    "uri",
    [
        "gs://bucket-only",       # no object
        "gs://bucket/",           # empty object
        "gs:///obj",              # empty bucket
        "gs://",                  # nothing
        "GS://bucket/obj",        # wrong-case scheme not accepted
        " gs://bucket/obj",       # leading space breaks startswith
        "gs://bucket\n/obj",      # embedded newline in bucket
    ],
)
def test_parse_rejects_malformed(uri):
    with pytest.raises((main.BadRequest, main.Forbidden)):
        main.parse_layer_uri(uri)


def test_parse_allowlist_blocks_nonlisted_bucket():
    os.environ["GRACE2_SIGNED_URL_BUCKETS"] = "grace-2-runs,grace-2-cog"
    with pytest.raises(main.Forbidden):
        main.parse_layer_uri("gs://attacker-bucket/o.tif")
    # And an allowed one passes:
    b, o = main.parse_layer_uri("gs://grace-2-runs/o.tif")
    assert b == "grace-2-runs"


def test_parse_allowlist_case_sensitive_bucket():
    # GCS bucket names are lowercase; ensure allowlist is exact-match (no
    # accidental case-folding that would let "Grace-2-Runs" slip a typo).
    os.environ["GRACE2_SIGNED_URL_BUCKETS"] = "grace-2-runs"
    with pytest.raises(main.Forbidden):
        main.parse_layer_uri("gs://GRACE-2-RUNS/o.tif")


# --------------------------------------------------------------------------- #
# ATTACK 3 — token-uid-vs-body trust boundary. THE crux: can any path reach
# signing without verified_uid == user_id when a token is involved?
# --------------------------------------------------------------------------- #


def test_http_forged_body_uid_cannot_sign():
    # Token proves alice; body claims bob who OWNS the case. Must still 403:
    # the verified uid is authoritative, body is never trusted.
    case = {"_id": "case-1", "user_id": "user-bob"}
    deps = make_recording_deps(case_doc=case, verified_uid_claim="user-alice")
    main._DEPS = deps

    class Req:
        method = "POST"
        headers = {"Authorization": "Bearer tok"}

        def get_json(self, silent=False):
            return {
                "layer_uri": "gs://b/o.tif",
                "user_id": "user-bob",   # forged: claims the real owner
                "case_id": "case-1",
            }

    body, status, _ = main.handle_request(Req())
    assert status == 403, body
    assert deps._sign_calls == [], "SIGNED despite token/body mismatch!"


def test_core_verified_uid_owns_but_body_user_differs_is_rejected():
    # Even if verified_uid (alice) owns the case, if body user_id is bob the
    # mismatch fires FIRST (Forbidden) — signing never reached. Confirms the
    # body user_id is the ownership subject, and it must equal the token uid.
    case = {"_id": "case-1", "user_id": "user-alice"}
    deps = make_recording_deps(case_doc=case)
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://b/o.tif",
            user_id="user-bob",
            case_id="case-1",
            verified_uid="user-alice",
            deps=deps,
        )
    assert deps._sign_calls == []


def test_http_always_passes_verified_uid_so_body_alone_never_signs():
    # Regression-of-intent: handle_request ALWAYS computes verified_uid from the
    # verified token and passes it. There is no HTTP path that calls
    # mint_signed_url without verified_uid. So the "no_verified_uid trusts body"
    # branch is unreachable from the network. Prove by asserting that with a
    # token whose uid != body, we 403 regardless of ownership.
    case = {"_id": "case-1", "user_id": "attacker-supplied"}
    deps = make_recording_deps(case_doc=case, verified_uid_claim="real-uid")
    main._DEPS = deps

    class Req:
        method = "POST"
        headers = {"Authorization": "Bearer tok"}

        def get_json(self, silent=False):
            return {
                "layer_uri": "gs://b/o.tif",
                "user_id": "attacker-supplied",
                "case_id": "case-1",
            }

    body, status, _ = main.handle_request(Req())
    assert status == 403
    assert deps._sign_calls == []


# --------------------------------------------------------------------------- #
# ATTACK 4 — ownership fail-closed. Orphan / missing / alias precedence.
# --------------------------------------------------------------------------- #


def test_orphan_case_no_owner_fields_fails_closed():
    # A Case doc with NO user_id and NO owner_user_id must NOT be mintable.
    case = {"_id": "case-1", "title": "pre-auth orphan"}
    deps = make_recording_deps(case_doc=case)
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://b/o.tif", "user-alice", "case-1",
            verified_uid="user-alice", deps=deps,
        )
    assert deps._sign_calls == []


def test_orphan_case_with_exists_false_intent_still_fails():
    # The persistence layer's 3rd clause is {"user_id": {"$exists": False}}.
    # A doc that WOULD match that clause (no user_id) must STILL be rejected
    # here. Confirms the documented fail-closed divergence is real.
    case = {"_id": "case-1", "owner_user_id": None}  # present but null
    deps = make_recording_deps(case_doc=case)
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://b/o.tif", "user-alice", "case-1",
            verified_uid="user-alice", deps=deps,
        )
    assert deps._sign_calls == []


def test_null_user_id_in_doc_does_not_match_null_attack():
    # If an attacker could get user_id="" past the body guard (they can't, it's
    # rejected), would an empty-string owner match? Body "" is BadRequest before
    # ownership, so signing is unreachable. Verify the BadRequest.
    case = {"_id": "case-1", "user_id": ""}
    deps = make_recording_deps(case_doc=case)
    with pytest.raises(main.BadRequest):
        main.mint_signed_url(
            "gs://b/o.tif", user_id="", case_id="case-1",
            verified_uid="", deps=deps,
        )
    assert deps._sign_calls == []


def test_missing_case_404_not_signed():
    deps = make_recording_deps(case_doc=None)
    with pytest.raises(main.NotFound):
        main.mint_signed_url(
            "gs://b/o.tif", "user-alice", "case-1",
            verified_uid="user-alice", deps=deps,
        )
    assert deps._sign_calls == []


def test_owner_user_id_alias_precedence():
    # owner_user_id alias grants ownership even when user_id field is absent.
    case = {"_id": "case-1", "owner_user_id": "user-alice"}
    deps = make_recording_deps(case_doc=case)
    out = main.mint_signed_url(
        "gs://b/o.tif", "user-alice", "case-1",
        verified_uid="user-alice", deps=deps,
    )
    assert out["bucket"] == "b"
    assert len(deps._sign_calls) == 1


def test_user_id_takes_priority_when_both_present_and_match():
    case = {"_id": "case-1", "user_id": "user-alice", "owner_user_id": "someone-else"}
    deps = make_recording_deps(case_doc=case)
    out = main.mint_signed_url(
        "gs://b/o.tif", "user-alice", "case-1",
        verified_uid="user-alice", deps=deps,
    )
    assert len(deps._sign_calls) == 1


def test_wrong_owner_with_matching_token_still_403():
    # alice's token is valid, body says alice, but the case is bob's. 403.
    case = {"_id": "case-1", "user_id": "user-bob"}
    deps = make_recording_deps(case_doc=case)
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://b/o.tif", "user-alice", "case-1",
            verified_uid="user-alice", deps=deps,
        )
    assert deps._sign_calls == []


# --------------------------------------------------------------------------- #
# ATTACK 5 — order of checks. Does URI validation happen BEFORE the DB read?
# And does an allowlisted-bucket Forbidden block before signing?
# --------------------------------------------------------------------------- #


def test_bad_uri_rejected_before_db_read():
    # fetch_case_doc must NEVER be called for a malformed URI (fail fast, no I/O,
    # no info leak about case existence to an unauth'd-shaped probe).
    fetch_calls = []

    def _fetch(cid):
        fetch_calls.append(cid)
        return {"_id": cid, "user_id": "user-alice"}

    deps = main._Deps(
        verify_id_token=lambda t: {"uid": "user-alice"},
        fetch_case_doc=_fetch,
        sign_url=lambda *a: "X",
    )
    with pytest.raises(main.BadRequest):
        main.mint_signed_url(
            "not-a-gs-uri", "user-alice", "case-1",
            verified_uid="user-alice", deps=deps,
        )
    assert fetch_calls == [], "DB was read before URI validation"


def test_nonlisted_bucket_forbidden_before_db_read():
    os.environ["GRACE2_SIGNED_URL_BUCKETS"] = "good-bucket"
    fetch_calls = []
    deps = main._Deps(
        verify_id_token=lambda t: {"uid": "user-alice"},
        fetch_case_doc=lambda cid: fetch_calls.append(cid) or {"_id": cid, "user_id": "user-alice"},
        sign_url=lambda *a: "X",
    )
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://evil-bucket/o.tif", "user-alice", "case-1",
            verified_uid="user-alice", deps=deps,
        )
    assert fetch_calls == [], "DB read for a bucket that fails the allowlist"


# --------------------------------------------------------------------------- #
# ATTACK 6 — verify-failure / no-uid token cannot sign (HTTP boundary).
# --------------------------------------------------------------------------- #


def test_http_verify_raises_signedurlerror_passthrough():
    # If verify_id_token itself raises a SignedUrlError (e.g. a 401 it chooses),
    # the status must be honored, not masked into 500. And no signing.
    def _verify(tok):
        raise main.Unauthorized("token revoked")

    deps = make_recording_deps(case_doc={"_id": "case-1", "user_id": "user-alice"})
    deps.verify_id_token = _verify
    main._DEPS = deps

    class Req:
        method = "POST"
        headers = {"Authorization": "Bearer tok"}

        def get_json(self, silent=False):
            return {"layer_uri": "gs://b/o.tif", "user_id": "user-alice", "case_id": "case-1"}

    body, status, _ = main.handle_request(Req())
    assert status == 401
    assert deps._sign_calls == []
