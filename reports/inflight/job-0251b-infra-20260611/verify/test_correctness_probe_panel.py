"""Adversarial CORRECTNESS-lens probes for job-0251b (panel re-verify).

Fresh tests written by the verification panel — independent harness, NOT a
re-run of the job's own suite. Attacks the mint resolution chain directly:

  A1. forged token uid == a case's stored internal ULID (the exact
      panel-refuted raw-uid fall-through shape) must NOT mint;
  A2. legacy case doc storing the raw Firebase uid must NOT mint for the
      legit firebase user (resolution replaces, never falls through);
  A3. users doc present but _id mismatching the case owner -> 403;
  A4. malformed users docs -> 403, never 500 (core AND HTTP layer);
  A5. users lookup raising -> 503, NO signed URL, sign_url never called;
  A6. sentinel __preauth_migration_anon__ attacks (forged sentinel token,
      sentinel-owned case, legit user vs sentinel case);
  A7. clamp_ttl hostile inputs (inf/-inf/nan/1e400/huge-int/str/None/bool);
  A8. ServiceUnavailable maps to a real HTTP 503 through handle_request.

Run:
  cd /home/nate/Documents/GRACE-2 && services/agent/.venv/bin/python -m pytest \
      reports/inflight/job-0251b-infra-20260611/verify/test_correctness_probe_panel.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path("/home/nate/Documents/GRACE-2/infra/signed_urls")))

import main  # noqa: E402  (infra/signed_urls/main.py)

# Distinct-by-construction identities (never equal across roles).
FB_UID = "fb-uid-probe-aaaaaaaaaaaaaa"
ULID = "01PROBEULIDAAAAAAAAAAAAAAA"
FB_UID_2 = "fb-uid-probe-bbbbbbbbbbbbb"
ULID_2 = "01PROBEULIDBBBBBBBBBBBBBBB"
SENTINEL = "__preauth_migration_anon__"  # grace2_agent/auth.py:116


class Probe:
    """Counting deps harness: records every fetch/sign call."""

    def __init__(self, users: dict, cases: dict, fetch_user_raises=None):
        self.users = users
        self.cases = cases
        self.fetch_user_raises = fetch_user_raises
        self.user_lookups: list[str] = []
        self.case_lookups: list[str] = []
        self.signs: list[tuple] = []

    def deps(self) -> main._Deps:
        def fetch_user(uid):
            self.user_lookups.append(uid)
            if self.fetch_user_raises is not None:
                raise self.fetch_user_raises
            return self.users.get(uid)

        def fetch_case(cid):
            self.case_lookups.append(cid)
            return self.cases.get(cid)

        def sign(bucket, obj, ttl, method):
            self.signs.append((bucket, obj, ttl, method))
            return f"https://signed.example/{bucket}/{obj}?ttl={ttl}"

        return main._Deps(
            verify_id_token=lambda tok: {"uid": FB_UID},
            fetch_user_doc=fetch_user,
            fetch_case_doc=fetch_case,
            sign_url=sign,
        )


# --------------------------------------------------------------------------- #
# A1. Forged token whose uid IS the case's stored internal ULID.
#     Pre-fix code compared token-uid against case.user_id directly -> mint.
#     Post-fix: resolution must find NO users doc for that "uid" -> 403.
# --------------------------------------------------------------------------- #

def test_a1_token_uid_equal_to_internal_ulid_does_not_mint():
    p = Probe(
        users={FB_UID: {"_id": ULID, "firebase_uid": FB_UID}},
        cases={"case-1": {"_id": "case-1", "user_id": ULID}},
    )
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://b/o.tif", ULID, "case-1", verified_uid=ULID, deps=p.deps()
        )
    assert p.signs == [], "raw-uid fall-through minted a URL"
    # The resolution lookup used the (forged) uid as a firebase_uid key.
    assert p.user_lookups == [ULID]


def test_a1b_no_verified_uid_body_carrying_internal_ulid_does_not_mint():
    """Core path without verified_uid: body user_id must still be RESOLVED."""
    p = Probe(
        users={FB_UID: {"_id": ULID, "firebase_uid": FB_UID}},
        cases={"case-1": {"_id": "case-1", "user_id": ULID}},
    )
    with pytest.raises(main.Forbidden):
        main.mint_signed_url("gs://b/o.tif", ULID, "case-1", deps=p.deps())
    assert p.signs == []


# --------------------------------------------------------------------------- #
# A2. Legacy case doc storing the raw Firebase uid (the old broken stamping)
#     must not mint even for the real owner of that firebase uid.
# --------------------------------------------------------------------------- #

def test_a2_case_storing_raw_firebase_uid_unmintable():
    p = Probe(
        users={FB_UID: {"_id": ULID, "firebase_uid": FB_UID}},
        cases={"case-1": {"_id": "case-1", "user_id": FB_UID}},  # legacy shape
    )
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://b/o.tif", FB_UID, "case-1", verified_uid=FB_UID, deps=p.deps()
        )
    assert p.signs == []


# --------------------------------------------------------------------------- #
# A3. Users doc resolves, but to a ULID that does not own the case.
# --------------------------------------------------------------------------- #

def test_a3_resolved_ulid_mismatching_owner_403():
    p = Probe(
        users={
            FB_UID: {"_id": ULID, "firebase_uid": FB_UID},
            FB_UID_2: {"_id": ULID_2, "firebase_uid": FB_UID_2},
        },
        cases={"case-1": {"_id": "case-1", "owner_user_id": ULID_2}},
    )
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://b/o.tif", FB_UID, "case-1", verified_uid=FB_UID, deps=p.deps()
        )
    assert p.signs == []


def test_a3b_positive_control_owner_mints():
    """Sanity: the chain DOES mint for the resolved owner (owner_user_id)."""
    p = Probe(
        users={FB_UID: {"_id": ULID, "firebase_uid": FB_UID}},
        cases={"case-1": {"_id": "case-1", "owner_user_id": ULID}},
    )
    out = main.mint_signed_url(
        "gs://b/o.tif", FB_UID, "case-1", verified_uid=FB_UID, deps=p.deps()
    )
    assert out["signed_url"].startswith("https://signed.example/")
    assert len(p.signs) == 1


# --------------------------------------------------------------------------- #
# A4. Malformed users docs -> 403 (Forbidden), never an unhandled error.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "doc",
    [
        {},                                            # empty
        {"firebase_uid": FB_UID},                      # no id keys at all
        {"_id": None, "user_id": None},                # null ids
        {"_id": "", "user_id": ""},                    # empty-string ids
        {"_id": 12345},                                # non-str id
        {"_id": ["01X"]},                              # list id
        {"_id": {"oid": "x"}},                         # dict id (EJSON-ish)
        "not-a-dict",                                  # non-dict doc
        ["list"],                                      # list doc
    ],
)
def test_a4_malformed_users_doc_403_not_500(doc):
    p = Probe(users={FB_UID: doc}, cases={"case-1": {"_id": "case-1", "user_id": ULID}})
    with pytest.raises(main.Forbidden) as ei:
        main.mint_signed_url(
            "gs://b/o.tif", FB_UID, "case-1", verified_uid=FB_UID, deps=p.deps()
        )
    assert ei.value.status == 403
    assert p.signs == []
    assert p.case_lookups == [], "case fetched despite unresolved identity"


# --------------------------------------------------------------------------- #
# A5. Users lookup RAISES -> 503, sign_url never called, no case read.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "exc",
    [RuntimeError("atlas down"), TimeoutError("selection timeout"), KeyError("x")],
)
def test_a5_lookup_error_503_no_mint(exc):
    # Poison: the case doc stores the RAW firebase uid, so any fall-through
    # to a raw-uid comparison WOULD succeed. Assert it cannot.
    p = Probe(
        users={},
        cases={"case-1": {"_id": "case-1", "user_id": FB_UID}},
        fetch_user_raises=exc,
    )
    with pytest.raises(main.ServiceUnavailable) as ei:
        main.mint_signed_url(
            "gs://b/o.tif", FB_UID, "case-1", verified_uid=FB_UID, deps=p.deps()
        )
    assert ei.value.status == 503
    assert p.signs == []
    assert p.case_lookups == []


def test_a5b_lookup_raising_signedurlerror_propagates_own_status():
    p = Probe(
        users={},
        cases={"case-1": {"_id": "case-1", "user_id": ULID}},
        fetch_user_raises=main.Forbidden("custom"),
    )
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://b/o.tif", FB_UID, "case-1", verified_uid=FB_UID, deps=p.deps()
        )
    assert p.signs == []


# --------------------------------------------------------------------------- #
# A6. Sentinel attacks.
# --------------------------------------------------------------------------- #

def test_a6_forged_token_claiming_sentinel_uid_403():
    """Token uid == migration sentinel; sentinel-owned case; no users doc maps
    a firebase_uid to the sentinel -> resolution fails -> 403, no mint."""
    p = Probe(
        users={FB_UID: {"_id": ULID, "firebase_uid": FB_UID}},
        cases={"case-m": {"_id": "case-m", "user_id": SENTINEL}},
    )
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://b/o.tif", SENTINEL, "case-m", verified_uid=SENTINEL, deps=p.deps()
        )
    assert p.signs == []


def test_a6b_legit_user_cannot_mint_sentinel_owned_case():
    p = Probe(
        users={FB_UID: {"_id": ULID, "firebase_uid": FB_UID}},
        cases={"case-m": {"_id": "case-m", "user_id": SENTINEL}},
    )
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://b/o.tif", FB_UID, "case-m", verified_uid=FB_UID, deps=p.deps()
        )
    assert p.signs == []


def test_a6c_sentinel_in_body_with_real_token_mismatch_403():
    """body user_id = sentinel but token uid real -> body/token mismatch 403."""
    p = Probe(users={}, cases={})
    with pytest.raises(main.Forbidden):
        main.mint_signed_url(
            "gs://b/o.tif", SENTINEL, "case-m", verified_uid=FB_UID, deps=p.deps()
        )
    assert p.user_lookups == [] and p.signs == []


# --------------------------------------------------------------------------- #
# A7. clamp_ttl hostile inputs.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "raw,expected",
    [
        (float("inf"), main.DEFAULT_TTL_SECONDS),
        (float("-inf"), main.DEFAULT_TTL_SECONDS),
        (float("nan"), main.DEFAULT_TTL_SECONDS),
        (1e400, main.DEFAULT_TTL_SECONDS),          # inf literal
        (10**100, main.MAX_TTL_SECONDS),            # huge int clamps
        (-(10**100), main.MIN_TTL_SECONDS),         # huge negative clamps
        ("abc", main.DEFAULT_TTL_SECONDS),
        ("1200", 1200),                              # numeric string parses
        (None, main.DEFAULT_TTL_SECONDS),
        ([], main.DEFAULT_TTL_SECONDS),
        ({}, main.DEFAULT_TTL_SECONDS),
        (True, main.MIN_TTL_SECONDS),                # int(True)=1 -> clamp up
        (0, main.MIN_TTL_SECONDS),
        (-1, main.MIN_TTL_SECONDS),
        (899, main.MIN_TTL_SECONDS),
        (900, 900),
        (3600, 3600),
        (3601, main.MAX_TTL_SECONDS),
    ],
)
def test_a7_clamp_ttl_hostile(raw, expected):
    got = main.clamp_ttl(raw)
    assert got == expected
    assert main.MIN_TTL_SECONDS <= got <= main.MAX_TTL_SECONDS


# --------------------------------------------------------------------------- #
# A8. HTTP layer: ServiceUnavailable -> real 503; malformed doc -> 403 not 500.
# --------------------------------------------------------------------------- #

class FakeReq:
    method = "POST"

    def __init__(self, body, token="tok"):
        self.headers = {"Authorization": f"Bearer {token}"}
        self._body = body

    def get_json(self, silent=True):
        return self._body


@pytest.fixture()
def _swap_deps():
    saved = main._DEPS
    main._DEPS = main._Deps()
    yield main._DEPS
    main._DEPS = saved


def test_a8_http_lookup_failure_is_real_503(_swap_deps):
    p = Probe(
        users={},
        cases={"case-1": {"_id": "case-1", "user_id": FB_UID}},
        fetch_user_raises=RuntimeError("atlas down"),
    )
    d = p.deps()
    _swap_deps.verify_id_token = d.verify_id_token
    _swap_deps.fetch_user_doc = d.fetch_user_doc
    _swap_deps.fetch_case_doc = d.fetch_case_doc
    _swap_deps.sign_url = d.sign_url

    body, status, headers = main.handle_request(
        FakeReq({"layer_uri": "gs://b/o.tif", "user_id": FB_UID, "case_id": "case-1"})
    )
    assert status == 503
    payload = json.loads(body)
    assert "signed_url" not in payload
    assert p.signs == []


def test_a8b_http_malformed_users_doc_403_not_500(_swap_deps):
    p = Probe(
        users={FB_UID: {"_id": 999}},  # malformed: non-str id
        cases={"case-1": {"_id": "case-1", "user_id": ULID}},
    )
    d = p.deps()
    _swap_deps.verify_id_token = d.verify_id_token
    _swap_deps.fetch_user_doc = d.fetch_user_doc
    _swap_deps.fetch_case_doc = d.fetch_case_doc
    _swap_deps.sign_url = d.sign_url

    body, status, _ = main.handle_request(
        FakeReq({"layer_uri": "gs://b/o.tif", "user_id": FB_UID, "case_id": "case-1"})
    )
    assert status == 403
    assert "signed_url" not in json.loads(body)


def test_a8c_http_forged_internal_ulid_token_403(_swap_deps):
    """Wire-level replay of A1: token verifies to the internal ULID."""
    p = Probe(
        users={FB_UID: {"_id": ULID, "firebase_uid": FB_UID}},
        cases={"case-1": {"_id": "case-1", "user_id": ULID}},
    )
    d = p.deps()
    _swap_deps.verify_id_token = lambda tok: {"uid": ULID}  # forged claim
    _swap_deps.fetch_user_doc = d.fetch_user_doc
    _swap_deps.fetch_case_doc = d.fetch_case_doc
    _swap_deps.sign_url = d.sign_url

    body, status, _ = main.handle_request(
        FakeReq({"layer_uri": "gs://b/o.tif", "user_id": ULID, "case_id": "case-1"})
    )
    assert status == 403
    assert p.signs == []
