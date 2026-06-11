# Kickoff (frozen)

**Job:** job-0241-agent-20260611 — Sprint-13.5 prereq: verify mcp.py + sandbox_runner.py + close test gaps
**Author:** Fable 5 (Sonnet 4.6), orchestrator dispatch 2026-06-11.

## Scope (re-scoped from manifest — verify + fill gaps, not re-implement)

1. `services/agent/src/grace2_agent/mcp.py` — verify MCPClient.start sets PR_SET_PDEATHSIG via preexec_fn AND launches in a new process group (setsid) AND close() uses killpg. Add named lifecycle test if absent.
2. `services/agent/src/grace2_agent/sandbox_runner.py` — verify read_sandbox_result reads result envelope via Cloud Logging API (google.cloud.logging_v2) and raises SandboxCloudModeUnavailable when Cloud Logging is unreachable (landed as job-0265). Verify two manifest tests exist.
3. Run relevant suites (test_mcp_surface_translator.py, test_sandbox_runner.py, test_sandbox_cloud_readback.py, plus new test_mcp_lifecycle.py) — all green. Run FULL agent suite; only the 5 proven-pre-existing failures allowed.

## Hard constraints

- NO Gemini/Vertex generate calls
- Do NOT restart the running agent process
- `git add` only touched files

## Acceptance criteria

- mcp.py: setsid + PR_SET_PDEATHSIG preexec + killpg in close() all present (PRESENT)
- sandbox_runner.py: read_sandbox_result via Cloud Logging + SandboxCloudModeUnavailable on failure (PRESENT)
- Manifest lifecycle test for MCPClient (ADDED — test_mcp_lifecycle.py)
- Two sandbox cloud readback tests (PRESENT — test_sandbox_cloud_readback.py)
- Full suite: 4330 passed, 5 known failures only, 0 new failures
