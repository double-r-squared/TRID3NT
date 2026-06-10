"""job-0203 M4 GOLD evidence: full CRUD round-trip through the REAL stack.

real mongod (7.0.14) <- real mongodb-mcp-server (npm latest) <- production
MCPClient (stdio JSON-RPC) <- MCPSurfaceTranslator <- Persistence typed API.

Covers: Case upsert/get/list, D.6 session touch x2 + project_ids dedupe +
chart $push coexistence, User upsert/get, audit append + read-back.
"""
import asyncio, json, os, sys
sys.path.insert(0, "src")
from grace2_agent.mcp import MCPClient
from grace2_agent.persistence import MCPSurfaceTranslator, Persistence

from grace2_contracts import new_ulid, now_utc
from grace2_contracts.case import CaseSummary
from grace2_contracts.user import User

DB = "grace2_m4_roundtrip"

async def main():
    os.environ["MDB_MCP_READ_ONLY"] = "false"
    client = await MCPClient.start(
        "mongodb://localhost:27517/?directConnection=true&serverSelectionTimeoutMS=2000"
    )
    ok = []
    try:
        p = Persistence(MCPSurfaceTranslator(client), database=DB)

        # ---- Cases ----
        cid = new_ulid()
        now = now_utc()
        case = CaseSummary(case_id=cid, title="M4 Roundtrip Case",
                           created_at=now, updated_at=now, status="active")
        await p.upsert_case(case)
        back = await p.get_case(cid)
        assert back is not None and back.title == "M4 Roundtrip Case", "case get failed"
        ok.append(f"CASE upsert+get OK ({cid})")

        cases = await p.list_cases_for_user("user-x")
        assert any(c.case_id == cid for c in cases), "case list missing doc"
        ok.append(f"CASE list OK ({len(cases)} doc)")

        # ---- D.6 session record ----
        sid = new_ulid()
        await p.touch_session(sid, case_id=cid)
        first = await p.get_session_record(sid)
        assert first is not None and first.project_ids == [cid], "touch #1 failed"
        await asyncio.sleep(0.05)
        await p.touch_session(sid, case_id=cid)  # dedupe + advance
        second = await p.get_session_record(sid)
        assert second.project_ids == [cid], "addToSet dedupe failed"
        assert second.created_at == first.created_at, "$setOnInsert violated"
        assert second.last_active_at > first.last_active_at, "activity not advanced"
        ok.append(f"SESSION touch x2 OK (created_at held, last_active advanced, project_ids deduped)")

        # chart $push onto same doc (job-0230 write shape) + typed read survives
        await p._mcp.call_tool("update-one", {
            "database": DB, "collection": "sessions",
            "filter": {"_id": sid},
            "update": {"$push": {"charts": {"chart_id": "rt1", "title": "RT"}}},
            "upsert": True,
        })
        typed = await p.get_session_record(sid)
        assert typed is not None, "typed read failed after chart push"
        raw = await p._mcp.call_tool("find-one", {
            "database": DB, "collection": "sessions", "filter": {"_id": sid}})
        charts = raw["document"].get("charts")
        assert charts == [{"chart_id": "rt1", "title": "RT"}], f"chart push lost: {charts}"
        ok.append("CHART $push coexists with session header OK")

        # ---- Users ----
        uid = new_ulid()
        user = User(user_id=uid, display_name="M4 Tester", created_at=now_utc())
        await p.upsert_user(user)
        uback = await p.get_user_by_id(uid)
        assert uback is not None and uback.display_name == "M4 Tester", "user roundtrip failed"
        ok.append(f"USER upsert+get OK ({uid})")

        # ---- Audit (mode2 path) ----
        await p.append_audit("mode2-candidate", {"session_id": sid, "candidate": {"domain": "x.gov"}})
        raw = await p._mcp.call_tool("find", {
            "database": DB, "collection": "audit_log",
            "filter": {"event_type": "mode2-candidate"}})
        docs = raw["documents"]
        assert len(docs) == 1 and docs[0]["payload"]["candidate"]["domain"] == "x.gov"
        ok.append("AUDIT append+read OK (mode2-candidate in audit_log)")

        print("\n".join(ok))
        print(f"\nALL {len(ok)} ROUND-TRIP ASSERTIONS PASSED against real mongod+mcp-server")
    finally:
        await client.close()

asyncio.run(main())
