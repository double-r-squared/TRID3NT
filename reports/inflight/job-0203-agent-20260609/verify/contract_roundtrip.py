"""Independent CONTRACT-lens verification (reviewer-authored).

Checks:
1. MCPClientProtocol conformance: FileMCPClient + MCPSurfaceTranslator both
   satisfy the Protocol (runtime structural check via call signature).
2. SessionDocument round-trips through touch_session -> get_session_record.
3. expires_at semantics: expires_at == last_active_at + SESSIONS_TTL (30d).
4. Second touch advances last_active_at/expires_at, preserves created_at,
   dedupes project_ids.
5. chart $push then get_session_record still validates (extras dropped).
6. audit_log shape from append_audit matches the historical {_id,event_type,
   ts,payload} shape.
"""
import asyncio
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from grace2_agent.persistence import (
    FileMCPClient,
    MCPSurfaceTranslator,
    MCPClientProtocol,
    Persistence,
    SESSIONS_COLLECTION,
    AUDIT_COLLECTION,
)
from grace2_contracts.collections import SessionDocument, SESSIONS_TTL


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


async def main() -> None:
    failures = []

    # --- 1. Protocol conformance (structural) -----------------------------
    # MCPSurfaceTranslator must accept a MCPClientProtocol and itself satisfy it.
    with tempfile.TemporaryDirectory() as td:
        import inspect
        fc = FileMCPClient(base_dir=Path(td))
        tr = MCPSurfaceTranslator(fc)
        for obj, label in ((fc, "FileMCPClient"), (tr, "MCPSurfaceTranslator")):
            m = getattr(obj, "call_tool", None)
            assert m is not None and inspect.iscoroutinefunction(m), f"{label}.call_tool missing/not async"
            sig = inspect.signature(m)
            params = list(sig.parameters)
            assert params[:1] == ["name"], f"{label}.call_tool first param != name: {params}"
        print("OK 1: FileMCPClient + MCPSurfaceTranslator structurally satisfy MCPClientProtocol (async call_tool(name, arguments))")

        from grace2_contracts import new_ulid
        p = Persistence(fc)
        sid = new_ulid()
        case_a = new_ulid()
        case_b = new_ulid()

        # --- 2/3. touch -> get -> SessionDocument validates + TTL semantics ---
        await p.touch_session(sid, client_fingerprint="fp-abc", case_id=case_a)
        doc = await p.get_session_record(sid)
        if doc is None:
            failures.append("get_session_record returned None after first touch")
        else:
            assert isinstance(doc, SessionDocument)
            created1 = doc.created_at
            la1 = doc.last_active_at
            exp1 = doc.expires_at
            # expires_at == last_active_at + 30d
            delta = (exp1 - la1).total_seconds()
            expected = SESSIONS_TTL["expire_after_seconds"]
            if abs(delta - expected) > 2:
                failures.append(
                    f"expires_at - last_active_at = {delta}s, expected {expected}s (30d)"
                )
            else:
                print(f"OK 3: expires_at = last_active_at + {delta}s (SESSIONS_TTL 30d)")
            if doc.project_ids != [case_a]:
                failures.append(f"project_ids wrong after first touch: {doc.project_ids}")
            if doc.client_fingerprint != "fp-abc":
                failures.append(f"client_fingerprint not set: {doc.client_fingerprint}")
            print(f"OK 2: SessionDocument validates; created={created1.isoformat()}")

        # --- 4. Second touch advances activity, preserves created, dedupes ---
        await asyncio.sleep(0.01)
        await p.touch_session(sid, case_id=case_a)  # same case
        doc2 = await p.get_session_record(sid)
        assert doc2 is not None
        if doc2.created_at != created1:
            failures.append(f"created_at changed on 2nd touch: {created1} -> {doc2.created_at}")
        else:
            print("OK 4a: created_at preserved on 2nd touch ($setOnInsert held)")
        if doc2.last_active_at < la1:
            failures.append("last_active_at went backwards on 2nd touch")
        if doc2.project_ids != [case_a]:
            failures.append(f"project_ids not deduped: {doc2.project_ids}")
        else:
            print("OK 4b: project_ids deduped ($addToSet)")

        # add a second distinct case
        await p.touch_session(sid, case_id=case_b)
        doc3 = await p.get_session_record(sid)
        assert doc3 is not None
        if set(doc3.project_ids) != {case_a, case_b}:
            failures.append(f"second distinct case not added: {doc3.project_ids}")
        else:
            print("OK 4c: distinct case added to project_ids")

        # --- 5. chart $push then get still validates (extras dropped) --------
        await fc.call_tool(
            "update-one",
            {
                "database": p._db,
                "collection": SESSIONS_COLLECTION,
                "filter": {"_id": sid},
                "update": {"$push": {"charts": {"chart_id": "c1", "spec": {"x": 1}}}},
            },
        )
        # raw doc should now carry charts
        raw = await fc.call_tool(
            "find-one",
            {"database": p._db, "collection": SESSIONS_COLLECTION, "filter": {"_id": sid}},
        )
        rawdoc = raw["document"]
        if "charts" not in rawdoc:
            failures.append("chart $push did NOT land on dev substrate (job-0230 regression)")
        else:
            print("OK 5a: chart $push landed on dev substrate")
        doc4 = await p.get_session_record(sid)
        if doc4 is None:
            failures.append("get_session_record returned None after chart $push (extras not dropped)")
        else:
            print("OK 5b: SessionDocument still validates after chart $push (extras dropped)")

        # --- 6. audit_log shape ----------------------------------------------
        await p.append_audit("mode2-candidate", {"session_id": sid, "candidate": {"url": "x"}})
        araw = await fc.call_tool(
            "find",
            {"database": p._db, "collection": AUDIT_COLLECTION, "filter": {"event_type": "mode2-candidate"}},
        )
        adocs = araw["documents"]
        if not adocs:
            failures.append("audit append did not land")
        else:
            a = adocs[0]
            need = {"_id", "event_type", "ts", "payload"}
            missing = need - set(a.keys())
            if missing:
                failures.append(f"audit doc missing historical keys: {missing}")
            else:
                print(f"OK 6: audit_log shape = {sorted(a.keys())} (matches historical _id/event_type/ts/payload)")

    print("\n=== SUMMARY ===")
    if failures:
        for f in failures:
            print("FAIL:", f)
        raise SystemExit(1)
    print("ALL CONTRACT CHECKS PASS")


asyncio.run(main())
