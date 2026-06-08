"""Live-evidence harness for job-0101 — drive a mocked weather.gov web_fetch
result through the server tool-call path and capture every envelope emitted on
the wire. Expectation: a ``mode2-candidate`` envelope appears with
confidence >= 0.6 and TLD ``gov``.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import Any

# Force a deterministic audit-log path for the run.
audit_log_path = os.path.join(
    tempfile.gettempdir(), "mode2_live_evidence.log"
)
if os.path.exists(audit_log_path):
    os.remove(audit_log_path)
os.environ["GRACE2_MODE2_AUDIT_LOG"] = audit_log_path

from grace2_agent.server import _invoke_tool_via_emitter, SessionState  # noqa: E402
from grace2_agent.tools import (  # noqa: E402
    RegisteredTool,
    TOOL_REGISTRY,
    clear_registry_for_tests,
)
from grace2_contracts import new_ulid  # noqa: E402
from grace2_contracts.tool_registry import AtomicToolMetadata  # noqa: E402


WEATHER_GOV_RESULT: dict[str, Any] = {
    "url": "https://www.weather.gov/about-data",
    "status_code": 200,
    "fetched_at": "2026-06-08T05:00:00+00:00",
    "extract_mode": "main_text",
    "content": (
        '<html><head>'
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"Dataset","name":"NWS observations"}'
        '</script>'
        '<title>NWS Public Data Access</title>'
        '</head><body>'
        '<h1>Data download</h1>'
        '<p>The NWS public API is documented at '
        '<a href="/api/openapi.json">openapi.json</a>. '
        'You may also download <a href="/exports/observations.csv">CSV exports</a>.</p>'
        '<p>The REST endpoint base path is /api/v1/.</p>'
        '</body></html>'
    ),
    "title": "NWS Public Data Access",
    "lang": "en",
    "content_length": 1024,
}


class FakeWebSocket:
    """Capture every send() call to inspect later."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


def _mock_web_fetch(**_kwargs: Any) -> dict[str, Any]:
    """Stand-in for the real ``web_fetch`` tool body — returns a
    deterministic weather.gov shape so the agent's mode2 check fires
    without a real HTTP call (per kickoff: "feed a mocked weather.gov page through
    web_fetch")."""
    return WEATHER_GOV_RESULT


async def main() -> int:
    # Re-register web_fetch with the mocked body, replacing the real one for
    # this evidence run only. Other registry entries are preserved.
    saved = TOOL_REGISTRY.get("web_fetch")
    if saved is None:
        # Trigger eager imports if needed.
        import grace2_agent.tools  # noqa: F401
        saved = TOOL_REGISTRY["web_fetch"]
    TOOL_REGISTRY["web_fetch"] = RegisteredTool(
        metadata=AtomicToolMetadata(
            name="web_fetch",
            ttl_class="dynamic-1h",
            source_class="web_fetch",
            cacheable=True,
        ),
        fn=_mock_web_fetch,
        module="evidence.mode2_live",
    )
    try:
        ws = FakeWebSocket()
        state = SessionState(session_id=new_ulid())
        result = await _invoke_tool_via_emitter(
            ws,  # type: ignore[arg-type]
            state,
            tool_name="web_fetch",
            params={
                "url": "https://www.weather.gov/about-data",
                "extract": "main_text",
            },
        )
        # Inspect what was sent on the wire.
        envelopes = []
        mode2_envelope = None
        for raw in ws.sent:
            env = json.loads(raw)
            envelopes.append(env.get("type"))
            if env.get("type") == "mode2-candidate":
                mode2_envelope = env
        print("=== Live evidence: job-0101 mode2-candidate ===")
        print(f"Tool return shape: dict (url={result.get('url')!r})")
        print(f"Envelopes emitted on wire (in order): {envelopes}")
        if mode2_envelope is None:
            print("FAIL: no mode2-candidate envelope emitted")
            return 1
        cand = mode2_envelope["payload"]["candidate"]
        print(f"Mode2 envelope payload: {json.dumps(cand, indent=2)}")
        assert mode2_envelope["payload"]["envelope_type"] == "mode2-candidate"
        assert cand["domain_tld"] == "gov", cand["domain_tld"]
        assert cand["domain"] == "www.weather.gov", cand["domain"]
        assert cand["confidence"] >= 0.6, cand["confidence"]
        assert "json-ld" in cand["detected_patterns"]
        assert cand["suggested_tool_kind"] == "endpoint", cand["suggested_tool_kind"]
        # Audit log on disk:
        if os.path.exists(audit_log_path):
            print(f"\nAudit log lines written:")
            with open(audit_log_path) as fh:
                print(fh.read())
        print("OK — live evidence passes all assertions.")
        return 0
    finally:
        TOOL_REGISTRY["web_fetch"] = saved


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
