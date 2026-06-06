# PROJECT_LOG

2026-06-04 | sprint-01 | OPENED: Foundations + canvas hello-world + canvas IPC (M1, M2) |
2026-06-04 | job-0001-infra-20260604 | OPENED: Dev environment (conda-forge PyQGIS) + repo scaffold |
2026-06-04 | job-0002-schema-20260604 | OPENED: Contracts v0 — canvas commands, WS envelope, pipeline state |
2026-06-04 | job-0003-desktop-ui-20260604 | OPENED: M1 canvas hello-world |
2026-06-04 | job-0004-desktop-ui-20260604 | OPENED: M2 canvas IPC |
2026-06-04 | job-0005-testing-20260604 | OPENED: Smoke harness + sprint acceptance verification |
2026-06-04 | job-0001-infra-20260604 | WITHDRAWN before start: SRS v0.2 pivot (standalone app → QGIS plugin) |
2026-06-04 | job-0002-schema-20260604 | WITHDRAWN before start: SRS v0.2 pivot |
2026-06-04 | job-0003-desktop-ui-20260604 | WITHDRAWN before start: SRS v0.2 pivot |
2026-06-04 | job-0004-desktop-ui-20260604 | WITHDRAWN before start: SRS v0.2 pivot |
2026-06-04 | job-0005-testing-20260604 | WITHDRAWN before start: SRS v0.2 pivot |
2026-06-04 | sprint-01 | ABORTED: SRS v0.2 supersedes v0.1 before any job started; replaced by sprint-02 |
2026-06-04 | sprint-02 | OPENED: Bootstrap — plugin skeleton + Bedrock echo chat (SRS v0.2 M1) |
2026-06-04 | job-0006-infra-20260604 | OPENED: Dev environment (conda QGIS + agent deps) + repo scaffold |
2026-06-04 | job-0007-agent-20260604 | OPENED: Vendor GeoAgent snapshot + THIRD_PARTY_NOTICES |
2026-06-04 | job-0008-schema-20260604 | OPENED: Contracts v0 — WS protocol, pipeline state, intent, envelope stubs |
2026-06-04 | job-0009-plugin-20260604 | OPENED: QGIS plugin skeleton — dockable chat panel + WS client |
2026-06-04 | job-0010-agent-20260604 | OPENED: Local agent service — WS server + Bedrock streaming |
2026-06-04 | job-0011-testing-20260604 | OPENED: Smoke harness + sprint-02 acceptance verification |
2026-06-04 | sprint-02 | EXECUTION HALTED by user mid-Stage-A; 0006/0008 near-done unreported; resume via workflow run wf_888f5ac8-ebc |
2026-06-05 | job-0006-infra-20260604 | WITHDRAWN mid-work: SRS v0.3 pivot (plugin → web/GCP). Salvage: grace2 conda env (QGIS 3.40.3) kept for PyQGIS worker dev |
2026-06-05 | job-0007-agent-20260604 | WITHDRAWN before start: SRS v0.3 Decision D — GeoAgent is reference only, no vendoring |
2026-06-05 | job-0008-schema-20260604 | WITHDRAWN mid-work: SRS v0.3 supersedes v0.2 contracts with Appendices A–D. Salvage: pydantic v2 choice (now SRS-anchored); v0.2-shaped code awaits cleanup job |
2026-06-05 | job-0009-plugin-20260604 | WITHDRAWN before start: no QGIS Desktop plugin in v0.3 |
2026-06-05 | job-0010-agent-20260604 | WITHDRAWN before start: Bedrock/Strands replaced by ADK/Gemini in v0.3 |
2026-06-05 | job-0011-testing-20260604 | WITHDRAWN before start: SRS v0.3 pivot |
2026-06-05 | sprint-02 | ABORTED: SRS v0.3 (web + GCP + Gemini/ADK + MongoDB) supersedes v0.2 mid-Stage-A; replaced by sprint-03 |
2026-06-05 | sprint-03 | OPENED: Foundation (SRS v0.3 M1) — repo realignment, contracts from Appendices A–D, GCP+Atlas bootstrap, ADK hello-world, web stub |
2026-06-05 | job-0012-infra-20260605 | OPENED: Repo realignment — delete v0.2 artifacts, new layout, git init + MIT license |
2026-06-05 | job-0013-schema-20260605 | OPENED: Contracts v0 from SRS Appendices A–D (pydantic v2) |
2026-06-05 | job-0014-infra-20260605 | OPENED: Toolchain + GCP project + Atlas M0 bootstrap (Terraform) |
2026-06-05 | job-0015-agent-20260605 | OPENED: ADK skeleton — hello-world Gemini + Appendix-A WS core + MCP verification |
2026-06-05 | job-0016-web-20260605 | OPENED: Web stub — React+MapLibre CONUS map + chat round-trip |
2026-06-05 | job-0017-testing-20260605 | OPENED: M1 acceptance — protocol/contract tests + exit-criteria record |
2026-06-05 | sprint-03 | EXECUTION HALTED by user during Stage A; 0012 done-unaudited, 0013 mid-work; resume via wf_63a211db-7a1 |
2026-06-05 | project | MIGRATED to GitHub: https://github.com/double-r-squared/GRACE-2 (main); session continuity via CLAUDE.md + PROJECT_STATE.md |
2026-06-05 | project | RESUMED on new dev machine (Debian 13, Linux maturin); gcloud/atlas/tofu installed + authed; Atlas Flex cluster grace-2-dev provisioned (us-central1) — supersedes prior M0 plan |
2026-06-05 | job-0012-infra-20260605 | repo realignment — v0.2 delete, v0.3 layout, git init + MIT license | approved [revisions: 0] |
2026-06-05 | job-0013-schema-20260605 | contracts v0 from SRS Appendices A-D (pydantic v2) — 91/91 tests, 35 schemas, 5 SRS amendments proposed (A1-A5), OQ-7 recommended 768 | approved [revisions: 0] |
2026-06-05 | SRS amendment | v0.3.12 → v0.3.13: Pelicun impact post-processing as forward-looking second tool class (Decision N + §2.3 + FR-CE-5/6/7 + FR-TA-1 + FR-AS-7/8 + Appendix B.6c/d + Appendix D.3 + Milestone M5.5 + OQ-8); commit 4f757c3 |
2026-06-05 | job-0014-infra-20260605 | GCP project grace-2-hazard-prod + 12 APIs + OpenTofu state bucket + Atlas Flex import (grace-2-dev) + DB user + GCS artifact bucket + agent-runtime SA + Secret Manager SRV; MCP smoke + OQ-7 gate qualified-pass (768/384/256 all = 1.000 @ 50 synthetic); OQ-2 sidecar; commit 5c0ab56 | approved [revisions: 0] |
2026-06-05 | job-0015-agent-20260605 | ADK skeleton — services/agent/ grace2-agent v0.1.0, Gemini 2.5-pro on Vertex (Gemini 3 returns 404 — flip path documented), Appendix-A WS via grace2_contracts (zero hand-rolled JSON), MCP stdio sidecar with SRV from Secret Manager via ADC; AC1-5 pass (cancel-to-cancelled 502ms vs 30s budget); OQ-1 = Cloud Run + WebSocket; commits 0742c06, cc8b2a7 | approved [revisions: 1] |
2026-06-05 | job-0016-web-20260605 | Web stub — React 18 + Vite 5 + TS strict + MapLibre GL 4.7 CONUS map (Decision I camera lock); chat box streaming agent-message-chunk via WebSocket against the running agent; contracts hand-mirror M1 subset; AC1-4 pass (disconnect→reconnect ~4s); Chromium+Firefox-ESR Linux screenshots; commits 778fe6c, 06d9d1a | approved [revisions: 1] |
