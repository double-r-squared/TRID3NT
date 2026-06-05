# services/agent/ — Agent service (ADK + Gemini 3)

**Owner:** `agent` specialist. **Container/deploy:** `infra` (Cloud Run).

The agent service (SRS v0.3 Decision E/G, FR-AS-*): a Google ADK application on
Gemini 3 that serves the Appendix-A WebSocket protocol, hosts the tool registry
(native ADK FunctionTools + the MongoDB MCP client + hazard-modeling tools),
streams replies, propagates cancellation, and enforces the determinism boundary
(Invariant 1) and confirmation-before-consequence hooks (Invariant 9).

Ships as its own Cloud Run service (WebSocket-capable, WSS/TLS in production —
WS only in local dev). The container image and its Cloud Run deployment are
`infra`'s; the application code inside is `agent`'s.

Empty scaffold until `job-0015` lands the ADK hello-world + WS core + MCP wiring.
