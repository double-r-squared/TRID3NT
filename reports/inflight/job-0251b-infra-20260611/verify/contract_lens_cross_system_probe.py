"""Contract-lens cross-system probe (panel re-verify of job-0251b + job-0252b).

Traces the WHOLE create->store->list->mint identity chain at the VALUE layer
using REAL shipping code on both sides:

  agent side:  grace2_agent.auth_handshake.authenticate_token (real verify hook
               seam) -> Persistence.upsert_user / upsert_case over a REAL
               FileMCPClient (file persistence, throwaway tmpdir)
  infra side:  infra/signed_urls/main.py mint_signed_url / handle_request fed
               the RAW stored documents the agent actually wrote (read back
               from the FileMCPClient JSON store, no hand-shaped fixtures).

Run:  cd /home/nate/Documents/GRACE-2/services/agent && \
      .venv/bin/python ../../reports/inflight/job-0251b-infra-20260611/verify/contract_lens_cross_system_probe.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path("/home/nate/Documents/GRACE-2")

# --- import the infra function module (not a package; load by path) --------- #
spec = importlib.util.spec_from_file_location(
    "signed_urls_main", REPO / "infra" / "signed_urls" / "main.py"
)
su = importlib.util.module_from_spec(spec)
sys.modules["signed_urls_main"] = su  # dataclass machinery needs the registration
spec.loader.exec_module(su)  # type: ignore[union-attr]

# --- agent-side real code ---------------------------------------------------- #
from grace2_agent import auth_handshake as ah  # noqa: E402
from grace2_agent.auth import MIGRATION_ANON_UID  # noqa: E402
from grace2_agent.persistence import FileMCPClient, Persistence  # noqa: E402
from grace2_contracts.case import CaseSummary  # noqa: E402
from grace2_contracts.common import new_ulid, now_utc  # noqa: E402

ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

FB_ALICE = "fb-uid-alice-PROBE-9f3k2m1x8q7w6e5r4t3y2u1i"
FB_BOB = "fb-uid-bob-PROBE-1a2s3d4f5g6h7j8k9l0z1x2c"

PASS: list[str] = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        print(f"FAIL: {name} {detail}")
        sys.exit(1)
    PASS.append(name)
    print(f"  ok: {name}")


class RecordingMCP:
    """Wrap FileMCPClient; count every call_tool (reads AND writes)."""

    def __init__(self, inner: FileMCPClient) -> None:
        self.inner = inner
        self.calls: list[tuple[str, str]] = []  # (tool, collection)

    async def call_tool(self, tool: str, args: dict):
        self.calls.append((tool, args.get("collection", "?")))
        return await self.inner.call_tool(tool, args)


async def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="contract-lens-probe-"))
    db = "grace2_probe"
    rec = RecordingMCP(FileMCPClient(base_dir=tmp))
    p = Persistence(rec, database=db)

    def raw(coll: str) -> dict:
        f = tmp / db / f"{coll}.json"
        return json.loads(f.read_text()) if f.exists() else {}

    # ---------------------------------------------------------------- #
    # (1) AGENT SIDE: real auth handshake provisions Alice + Bob
    # ---------------------------------------------------------------- #
    os.environ.pop("AUTH_REQUIRED", None)  # gate OFF for provisioning

    def fake_verify(token: str):
        return {
            FB_ALICE: {"uid": FB_ALICE, "email": "a@x.com", "name": "Alice"},
            FB_BOB: {"uid": FB_BOB, "email": "b@x.com", "name": "Bob"},
            MIGRATION_ANON_UID: {"uid": MIGRATION_ANON_UID},
        }.get(token)

    ah.set_verify_hook(fake_verify)
    try:
        from grace2_contracts.auth import AuthTokenEnvelope

        alice = await ah.authenticate_token(
            AuthTokenEnvelope(token=FB_ALICE), p
        )
        bob = await ah.authenticate_token(AuthTokenEnvelope(token=FB_BOB), p)
    finally:
        pass

    alice_ulid = alice.user.user_id
    bob_ulid = bob.user.user_id
    ok("alice internal id is a ULID, not the firebase uid",
       bool(ULID_RE.match(alice_ulid)) and alice_ulid != FB_ALICE,
       f"got {alice_ulid!r}")
    ok("alice AuthResult.firebase_uid carries the firebase uid separately",
       alice.firebase_uid == FB_ALICE and not alice.is_anonymous)

    users_store = raw("users")
    adoc = users_store.get(alice_ulid)
    ok("stored users doc: _id == user_id == internal ULID (upsert_user shape)",
       adoc is not None and adoc.get("_id") == alice_ulid
       and adoc.get("user_id") == alice_ulid
       and adoc.get("firebase_uid") == FB_ALICE,
       f"doc={adoc}")

    # ---------------------------------------------------------------- #
    # (2) AGENT SIDE: case create with the EXACT server.py call shape
    #     (server.py:1877 / :2175 -> upsert_case(owner_user_id=state.
    #      authenticated_user_id) where that is result.user.user_id)
    # ---------------------------------------------------------------- #
    case_id = new_ulid()
    now = now_utc()
    case = CaseSummary(case_id=case_id, title="Probe Case",
                       created_at=now, updated_at=now, status="active")
    await p.upsert_case(case, owner_user_id=alice_ulid)

    cdoc = raw("projects").get(case_id)
    ok("stored case doc: user_id field == internal ULID (NOT firebase uid)",
       cdoc is not None and cdoc.get("user_id") == alice_ulid
       and cdoc.get("user_id") != FB_ALICE, f"doc={cdoc}")

    # ---------------------------------------------------------------- #
    # (3) AGENT SIDE: list chain at the VALUE layer
    # ---------------------------------------------------------------- #
    ok("list_cases_for_user(alice ULID) -> the case",
       [c.case_id for c in await p.list_cases_for_user(alice_ulid)] == [case_id])
    ok("list_cases_for_user(alice FIREBASE uid) -> NOTHING (value-layer proof)",
       await p.list_cases_for_user(FB_ALICE) == [])
    ok("list_cases_for_user(bob ULID) -> NOTHING",
       await p.list_cases_for_user(bob_ulid) == [])

    # ---------------------------------------------------------------- #
    # (4) INFRA SIDE: mint fed the RAW agent-written docs
    # ---------------------------------------------------------------- #
    def fetch_user_doc(firebase_uid: str):
        for d in raw("users").values():
            if d.get("firebase_uid") == firebase_uid:
                return d
        return None

    def fetch_case_doc(cid: str):
        return raw("projects").get(cid)

    minted: list[tuple] = []

    def fake_sign(bucket, obj, ttl, method):
        minted.append((bucket, obj, ttl, method))
        return f"https://signed.example/{bucket}/{obj}"

    deps = su._Deps(
        verify_id_token=lambda t: fake_verify(t) or (_ for _ in ()).throw(ValueError("bad token")),
        fetch_user_doc=fetch_user_doc,
        fetch_case_doc=fetch_case_doc,
        sign_url=fake_sign,
    )
    uri = "gs://probe-bucket/layers/depth.tif"

    # TRUE OWNER mints (core)
    res = su.mint_signed_url(uri, FB_ALICE, case_id,
                             verified_uid=FB_ALICE, deps=deps)
    ok("TRUE owner mints (core): firebase token -> ULID -> case_owned_by -> mint",
       res["signed_url"].startswith("https://signed.example/") and len(minted) == 1)

    # SECOND user 403s (core)
    try:
        su.mint_signed_url(uri, FB_BOB, case_id, verified_uid=FB_BOB, deps=deps)
        ok("second user 403s", False, "minted!")
    except su.Forbidden:
        ok("SECOND user 403s (core, real bob users doc, resolved ULID mismatch)", True)
    ok("sign_url not called for bob", len(minted) == 1)

    # Wire level: handle_request via _DEPS injection
    class Req:
        method = "POST"
        def __init__(self, token, body):
            self.headers = {"Authorization": f"Bearer {token}"}
            self._body = body
        def get_json(self, silent=True):
            return self._body

    saved = su._DEPS
    su._DEPS = deps
    try:
        body = {"layer_uri": uri, "user_id": FB_ALICE, "case_id": case_id}
        out, status, _hdr = su.handle_request(Req(FB_ALICE, body))
        ok("TRUE owner mints over the wire (handle_request 200)", status == 200, out)
        out, status, _hdr = su.handle_request(
            Req(FB_BOB, {"layer_uri": uri, "user_id": FB_BOB, "case_id": case_id}))
        ok("second user 403 over the wire", status == 403, f"{status} {out}")
        # body.user_id must equal token uid (bob's body, alice's token)
        out, status, _hdr = su.handle_request(
            Req(FB_ALICE, {"layer_uri": uri, "user_id": FB_BOB, "case_id": case_id}))
        ok("token/body uid mismatch -> 403", status == 403, f"{status}")
    finally:
        su._DEPS = saved

    # No users doc -> 403; lookup error -> 503 (never raw-uid fall-through)
    try:
        su.mint_signed_url(uri, "fb-uid-nobody", case_id,
                           verified_uid="fb-uid-nobody", deps=deps)
        ok("no users doc -> 403", False)
    except su.Forbidden:
        ok("no users doc -> 403 (fail closed)", True)

    # Adversarial fall-through probe: case doc stores the RAW firebase uid
    # (the exact panel-refuted shape) AND the users lookup errors -> must 503,
    # never compare raw uid.
    legacy_case = new_ulid()
    store = raw("projects")
    # hand-inject the refuted doc shape directly into the file store
    pfile = tmp / db / "projects.json"
    store[legacy_case] = {"_id": legacy_case, "user_id": FB_ALICE,
                          "title": "raw-uid legacy", "status": "active"}
    pfile.write_text(json.dumps(store))

    def boom(_uid):
        raise RuntimeError("atlas down")

    deps_err = su._Deps(verify_id_token=deps.verify_id_token,
                        fetch_user_doc=boom,
                        fetch_case_doc=fetch_case_doc, sign_url=fake_sign)
    try:
        su.mint_signed_url(uri, FB_ALICE, legacy_case,
                           verified_uid=FB_ALICE, deps=deps_err)
        ok("lookup error -> 503", False, "minted on lookup error!")
    except su.ServiceUnavailable:
        ok("users lookup ERROR -> 503, no raw-uid fall-through "
           "(case stored raw uid and would have matched)", True)
    # And with a WORKING lookup, the raw-uid-owned case is unmintable by alice
    # (her resolved ULID != the raw uid stored on the doc).
    try:
        su.mint_signed_url(uri, FB_ALICE, legacy_case,
                           verified_uid=FB_ALICE, deps=deps)
        ok("raw-uid-owned case unmintable", False, "minted!")
    except su.Forbidden:
        ok("case doc storing the RAW firebase uid is NOT mintable (403)", True)

    # ---------------------------------------------------------------- #
    # (5) MIGRATION_ANON_UID cases remain unmintable
    # ---------------------------------------------------------------- #
    orphan = new_ulid()
    ocase = CaseSummary(case_id=orphan, title="Orphan", created_at=now,
                        updated_at=now, status="active")
    await p.upsert_case(ocase, owner_user_id=None)  # legacy/no-owner shape
    n = await p.migrate_preauth_cases(MIGRATION_ANON_UID)
    odoc = raw("projects").get(orphan)
    ok("migration stamped the orphan with the sentinel",
       odoc.get("user_id") == MIGRATION_ANON_UID, f"n={n} doc={odoc}")
    try:
        su.mint_signed_url(uri, FB_ALICE, orphan, verified_uid=FB_ALICE, deps=deps)
        ok("sentinel case unmintable by alice", False)
    except su.Forbidden:
        ok("MIGRATION_ANON_UID-owned case unmintable by a real user (403)", True)
    # forged token whose uid IS the sentinel: no users doc maps to it -> 403
    try:
        su.mint_signed_url(uri, MIGRATION_ANON_UID, orphan,
                           verified_uid=MIGRATION_ANON_UID, deps=deps)
        ok("sentinel-token 403", False)
    except su.Forbidden:
        ok("forged token with uid == sentinel -> 403 (no users doc)", True)
    # ADVERSARIAL: even if someone PROVISIONS a user whose firebase uid IS the
    # sentinel (gate-OFF authenticate_token with forged claims), resolution
    # yields that user's fresh ULID -- never the sentinel -> still 403.
    evil = await ah.authenticate_token(AuthTokenEnvelope(token=MIGRATION_ANON_UID), p)
    ok("provisioned sentinel-uid user gets a FRESH ULID, not the sentinel",
       evil.user.user_id != MIGRATION_ANON_UID and ULID_RE.match(evil.user.user_id) is not None)
    try:
        su.mint_signed_url(uri, MIGRATION_ANON_UID, orphan,
                           verified_uid=MIGRATION_ANON_UID, deps=deps)
        ok("sentinel still unmintable", False)
    except su.Forbidden:
        ok("even WITH a users doc {firebase_uid: sentinel}, resolution -> fresh "
           "ULID != sentinel -> 403 (sentinel unforgeable)", True)

    # ---------------------------------------------------------------- #
    # (6) Normalization-order divergence probe (_id-first vs user_id-first)
    # ---------------------------------------------------------------- #
    # Every shipping write path is Persistence.upsert_user (persistence.py:842,
    # body["_id"] = user.user_id) called from auth_handshake.py:325/:451.
    for uid_, d in raw("users").items():
        ok(f"users doc {uid_[:8]}...: _id == user_id (orders indistinguishable)",
           d.get("_id") == d.get("user_id") == uid_, f"doc={d}")
    # read-modify-rewrite cycle (the only other realistic mutation path):
    reread = await p.get_user_by_firebase_uid(FB_ALICE)
    await p.upsert_user(reread.model_copy(update={"display_name": "Alice2"}))
    d2 = raw("users")[alice_ulid]
    ok("re-upsert after read-back keeps _id == user_id",
       d2.get("_id") == d2.get("user_id") == alice_ulid)
    # hand-edited divergent doc (NOT reachable via shipping code): show the
    # two resolvers disagree -- documents that OQ-1 is real but unreachable.
    div = {"_id": "01JXHANDEDITAAAAAAAAAAAAAA", "user_id": "01JXHANDEDITBBBBBBBBBBBBBB",
           "firebase_uid": "fb-divergent"}
    mint_side = su.resolve_internal_user_id(div)
    ok("hand-edited divergent doc: mint resolves _id (kickoff order)",
       mint_side == "01JXHANDEDITAAAAAAAAAAAAAA")
    # agent side prefers the user_id key (persistence.py:833-835) -> divergence
    # is REAL for hand-edited docs, but unreachable by any shipping write.

    # ---------------------------------------------------------------- #
    # (7) 0252b seam: gate-ON rejected connections -> ZERO users traffic
    # ---------------------------------------------------------------- #
    users_before = raw("users")
    calls_before = len(rec.calls)
    os.environ["AUTH_REQUIRED"] = "true"
    try:
        # forged token (verify fails)
        r1 = await ah.authenticate_token(AuthTokenEnvelope(token="forged-junk"), p)
        # empty token + a REAL reusable anon hint (sticky-read suppression)
        r2 = await ah.authenticate_token(
            AuthTokenEnvelope(token="", anonymous_user_id=alice_ulid), p)
        # claims missing uid
        ah.set_verify_hook(lambda t: {"email": "no-uid@x"})
        r3 = await ah.authenticate_token(AuthTokenEnvelope(token="x"), p)
    finally:
        os.environ.pop("AUTH_REQUIRED", None)
        ah.set_verify_hook(fake_verify)
    ok("gate-ON: all three failure paths -> anonymous result",
       r1.is_anonymous and r2.is_anonymous and r3.is_anonymous)
    ok("gate-ON rejected paths: ZERO MCP calls (no read, no write)",
       len(rec.calls) == calls_before, f"calls={rec.calls[calls_before:]}")
    ok("gate-ON rejected paths: users store byte-identical",
       raw("users") == users_before)
    ok("gate-ON anonymous results carry fresh in-memory ULIDs never stored",
       r1.user.user_id not in raw("users") and r3.user.user_id not in raw("users"))
    # gate OFF: forged token DOES persist (dev regression pin)
    n_before = len(raw("users"))
    r4 = await ah.authenticate_token(AuthTokenEnvelope(token="forged-junk"), p)
    ok("gate-OFF: forged token still provisions+persists (dev path unchanged)",
       r4.is_anonymous and len(raw("users")) == n_before + 1)

    print(f"\nALL {len(PASS)} PROBE ASSERTIONS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
