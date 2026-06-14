"""Panel live-verify probe (job-0251b re-panel, LIVE-VERIFY lens).

Drives the Cloud Function HTTP entry point (``handle_request`` — the exact
callable functions-framework registers as the target, exposed as
``mint_signed_url_http``) with full Flask-shaped requests, with the _DEPS
seam backed by a REAL ``grace2_agent.persistence.FileMCPClient`` over real
JSON files on disk. The users + Case docs are seeded through the agent's
ACTUAL writers (``Persistence.upsert_user`` / ``upsert_case(owner_user_id=)``)
so the document shapes are byte-for-byte what the agent produces — the
value-layer seam the prior panel refuted is therefore tested end-to-end:
agent-written docs -> function reads -> mint decision.

No GCP, no Gemini, no deploy. sign_url is a fake recorder.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

# --- import the function module from infra/signed_urls (not a package) ----- #
REPO = Path("/home/nate/Documents/GRACE-2")
spec = importlib.util.spec_from_file_location(
    "signed_urls_main", REPO / "infra/signed_urls/main.py"
)
m = importlib.util.module_from_spec(spec)
sys.modules["signed_urls_main"] = m  # dataclass machinery needs sys.modules entry
spec.loader.exec_module(m)

from grace2_agent.persistence import FileMCPClient, Persistence  # noqa: E402
from grace2_contracts.case import CaseSummary  # noqa: E402
from grace2_contracts.common import new_ulid, now_utc  # noqa: E402
from grace2_contracts.user import User  # noqa: E402

DB_DIR = Path(tempfile.mkdtemp(prefix="g2v-0251b-mintdb-"))
FB_ALICE = "fb-uid-live-alice-PANEL"
FB_MALLORY = "fb-uid-live-mallory-PANEL"
FB_CHARLIE = "fb-uid-live-charlie-NO-USERS-DOC"
MIGRATION_ANON_UID = "__preauth_migration_anon__"  # auth.py:116 sentinel

mcp = FileMCPClient(base_dir=DB_DIR)
persistence = Persistence(mcp)


async def seed() -> dict:
    alice = User(
        user_id=new_ulid(), firebase_uid=FB_ALICE, email="a@x", display_name="A",
        created_at=now_utc(), is_active=True, prefs={},
    )
    mallory = User(
        user_id=new_ulid(), firebase_uid=FB_MALLORY, email="m@x", display_name="M",
        created_at=now_utc(), is_active=True, prefs={},
    )
    await persistence.upsert_user(alice)
    await persistence.upsert_user(mallory)

    def case(title: str) -> CaseSummary:
        return CaseSummary(
            case_id=new_ulid(), title=title,
            created_at=now_utc(), updated_at=now_utc(),
        )

    c_alice = case("alice-owned (internal ULID)")
    await persistence.upsert_case(c_alice, owner_user_id=alice.user_id)

    # The exact panel-refuted shape: a (hypothetical) case storing the RAW
    # Firebase uid in its owner field. Under the OLD code this minted.
    c_rawuid = case("raw-firebase-uid-owned (old-bug shape)")
    await persistence.upsert_case(c_rawuid, owner_user_id=FB_ALICE)

    c_anon = case("migration-sentinel-owned")
    await persistence.upsert_case(c_anon, owner_user_id=MIGRATION_ANON_UID)

    return {
        "alice_ulid": alice.user_id, "mallory_ulid": mallory.user_id,
        "case_alice": c_alice.case_id, "case_rawuid": c_rawuid.case_id,
        "case_anon": c_anon.case_id,
    }


S = asyncio.run(seed())
print("SEEDED:", json.dumps(S))
print("DB FILES:", [str(p) for p in sorted(DB_DIR.rglob("*.json"))])

# --- _DEPS: real FileMCPClient-backed fetchers + fake verify/sign ---------- #
TOKENS = {
    "tok-alice": {"uid": FB_ALICE},
    "tok-mallory": {"uid": FB_MALLORY},
    "tok-charlie": {"uid": FB_CHARLIE},
}
SIGN_CALLS: list[tuple] = []


def fake_verify(token: str) -> dict:
    if token in TOKENS:
        return TOKENS[token]
    raise ValueError("forged/expired token")


def _unwrap(raw):
    # FileMCPClient returns the MCP envelope {"document": {...}} for find-one;
    # production pymongo find_one returns the raw doc (or None). Unwrap so the
    # function sees exactly the pymongo shape (same unwrap Persistence does).
    if isinstance(raw, dict) and "document" in raw:
        return raw["document"]
    return raw


def real_fetch_user_doc(firebase_uid: str):
    return _unwrap(asyncio.run(mcp.call_tool(
        "find-one",
        {"database": m.DEFAULT_DATABASE, "collection": "users",
         "filter": {"firebase_uid": firebase_uid}},
    )))


def real_fetch_case_doc(case_id: str):
    return _unwrap(asyncio.run(mcp.call_tool(
        "find-one",
        {"database": m.DEFAULT_DATABASE, "collection": "projects",
         "filter": {"_id": case_id}},
    )))


def fake_sign(bucket: str, obj: str, ttl: int, method: str) -> str:
    SIGN_CALLS.append((bucket, obj, ttl, method))
    return (f"https://storage.googleapis.com/{bucket}/{obj}"
            f"?X-Goog-Algorithm=GOOG4-RSA-SHA256&X-Goog-Expires={ttl}&sig=FAKE")


m._DEPS.verify_id_token = fake_verify
m._DEPS.fetch_user_doc = real_fetch_user_doc
m._DEPS.fetch_case_doc = real_fetch_case_doc
m._DEPS.sign_url = fake_sign


class Req:
    """Flask-shaped request: method / headers / get_json / data."""

    def __init__(self, body: str, token: str | None, method: str = "POST"):
        self.method = method
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.data = body.encode()

    def get_json(self, silent: bool = False):
        try:
            return json.loads(self.data)
        except Exception:
            if silent:
                return None
            raise


def hit(name: str, token, body_obj=None, raw_body: str | None = None):
    body = raw_body if raw_body is not None else json.dumps(body_obj)
    resp, status, headers = m.handle_request(Req(body, token))
    print(f"[{name}] status={status} body={resp}")
    return status, json.loads(resp)


FAILS = 0


def expect(name, got, want):
    global FAILS
    ok = got == want
    if not ok:
        FAILS += 1
    print(f"  -> {'PASS' if ok else 'FAIL'}: {name} (got {got!r}, want {want!r})")


uri = "gs://grace2-runs-panel/cases/demo/flood_depth.tif"

# 1. True owner, full chain -> 200 mint; TTL passthrough inside clamp.
st, b = hit("owner-200", "tok-alice",
            {"layer_uri": uri, "user_id": FB_ALICE,
             "case_id": S["case_alice"], "ttl_seconds": 1800})
expect("owner mints 200", st, 200)
expect("expires_in passthrough", b.get("expires_in"), 1800)
expect("bucket parsed", b.get("bucket"), "grace2-runs-panel")
expect("object parsed", b.get("object"), "cases/demo/flood_depth.tif")
expect("url shape", b.get("signed_url", "").startswith(
    "https://storage.googleapis.com/grace2-runs-panel/cases/demo/flood_depth.tif?"), True)

# 2. TTL clamp low + high.
st, b = hit("ttl-low", "tok-alice",
            {"layer_uri": uri, "user_id": FB_ALICE,
             "case_id": S["case_alice"], "ttl_seconds": 10})
expect("ttl clamps up to 900", b.get("expires_in"), 900)
st, b = hit("ttl-high", "tok-alice",
            {"layer_uri": uri, "user_id": FB_ALICE,
             "case_id": S["case_alice"], "ttl_seconds": 999999})
expect("ttl clamps down to 3600", b.get("expires_in"), 3600)

# 3. OverflowError body: raw JSON 1e400 -> parses to float('inf') -> clamped
#    mint, NOT a 500 (job-0251b clamp_ttl fix).
st, b = hit("ttl-overflow-1e400", "tok-alice", raw_body=(
    '{"layer_uri": "%s", "user_id": "%s", "case_id": "%s", "ttl_seconds": 1e400}'
    % (uri, FB_ALICE, S["case_alice"])))
expect("1e400 ttl -> 200 (not 500)", st, 200)
expect("1e400 ttl -> DEFAULT 3600", b.get("expires_in"), 3600)

# 4. Non-owner (has users doc, doesn't own the case) -> 403.
st, b = hit("non-owner-403", "tok-mallory",
            {"layer_uri": uri, "user_id": FB_MALLORY,
             "case_id": S["case_alice"], "ttl_seconds": 1800})
expect("non-owner 403", st, 403)

# 5. Verified Firebase user with NO users doc -> 403 (fail closed).
st, b = hit("no-users-doc-403", "tok-charlie",
            {"layer_uri": uri, "user_id": FB_CHARLIE,
             "case_id": S["case_alice"], "ttl_seconds": 1800})
expect("no users doc 403", st, 403)

# 6. body.user_id != token uid -> 403 (never trust the body).
st, b = hit("body-mismatch-403", "tok-alice",
            {"layer_uri": uri, "user_id": FB_MALLORY,
             "case_id": S["case_alice"], "ttl_seconds": 1800})
expect("body/token mismatch 403", st, 403)

# 7. The exact panel-refuted shape: Case owner field stores the RAW firebase
#    uid. Old code minted this; new code must 403 (resolution yields the ULID).
st, b = hit("raw-uid-case-403", "tok-alice",
            {"layer_uri": uri, "user_id": FB_ALICE,
             "case_id": S["case_rawuid"], "ttl_seconds": 1800})
expect("raw-firebase-uid-owned case NOT mintable", st, 403)

# 8. MIGRATION_ANON_UID-owned case unmintable by any real token.
st, b = hit("migration-anon-403", "tok-alice",
            {"layer_uri": uri, "user_id": FB_ALICE,
             "case_id": S["case_anon"], "ttl_seconds": 1800})
expect("sentinel-owned case 403", st, 403)

# 9. users lookup ERROR -> 503 fail-closed, sign never called. Seed a case
#    owned by the RAW uid so an old-style fall-through WOULD mint.
n_sign_before = len(SIGN_CALLS)


def exploding_fetch_user_doc(firebase_uid: str):
    raise RuntimeError("atlas down (panel-injected)")


m._DEPS.fetch_user_doc = exploding_fetch_user_doc
st, b = hit("lookup-failure-503", "tok-alice",
            {"layer_uri": uri, "user_id": FB_ALICE,
             "case_id": S["case_rawuid"], "ttl_seconds": 1800})
expect("lookup failure 503 (never raw-uid fall-through)", st, 503)
expect("sign_url NOT called on 503", len(SIGN_CALLS), n_sign_before)
m._DEPS.fetch_user_doc = real_fetch_user_doc

# 10. Forged bearer -> 401; no bearer -> 401.
st, b = hit("forged-token-401", "tok-forged",
            {"layer_uri": uri, "user_id": FB_ALICE,
             "case_id": S["case_alice"]})
expect("forged token 401", st, 401)
st, b = hit("no-auth-header-401", None,
            {"layer_uri": uri, "user_id": FB_ALICE,
             "case_id": S["case_alice"]})
expect("missing header 401", st, 401)

print(f"\nRESULT: {'ALL PASS' if FAILS == 0 else f'{FAILS} FAILURE(S)'}")
sys.exit(1 if FAILS else 0)
