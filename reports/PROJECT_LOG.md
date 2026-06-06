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
2026-06-05 | SRS amendment | v0.3.13 → v0.3.14: openTELEMAC-MASCARET added as forward-looking multi-solver hydrodynamic engine (§2.3 Deferred engines row, Python shim, target v0.3; FR-TA-1 forward-looking workflow group; Milestone M11; OQ-9 mesh-toolchain); commit ce8c1a0 |
2026-06-05 | job-0017-testing-20260605 | M1 acceptance — tests/ pytest harness + Makefile test target; 91 contracts + 23 acceptance = 114 tests green in ~36s; live_gemini opt-in PASSED 4.42s on Vertex 2.5-pro; live MCP smoke 17.66s; sprint-03 exit-criteria 5 pass + 1 qualified (EC4 Gemini-3 substitution); commits c24b9b1, 9815dcb | approved [revisions: 1] |
2026-06-05 | sprint-03 | CLOSED: M1 (Foundation) achieved — 6 jobs approved (0012-0017); 5 exit criteria pass + 1 qualified (EC4 Gemini-3 substitution on Vertex AI); cloud substrate live (grace-2-hazard-prod + Atlas Flex grace-2-dev); end-to-end browser↔agent↔Gemini↔MCP↔Atlas verified |
2026-06-05 | .gitignore | HARDENED for public-repo posture: SSH keys, cert stores, kubeconfig, mongodump, HAR, GHA OIDC, secrets/, .claude/settings.local.json, future solver outputs (runs/, outputs/, *.nc, sfincs_*.dat, *.sgsg) |
2026-06-05 | sprint-04 | OPENED: M2 (QGIS Server in cloud + PyQGIS worker prototype) — 6 jobs created (0018-0023); stage A parallel (0018 + 0022), then B/C/D serial (0019 → 0020 → 0021), capstone 0023 |
2026-06-05 | job-0018-infra-20260605 | OPENED: QGIS Server Cloud Run service + GCS .qgs/COG/FlatGeobuf buckets + Pub/Sub completion-notify topic |
2026-06-05 | job-0019-engine-20260605 | OPENED: Sample .qgs project + styles/basemap.qml preset stub uploaded to GCS .qgs bucket |
2026-06-05 | job-0020-engine-20260605 | OPENED: PyQGIS worker code — worker_round_trip(qgs_uri, layer_to_add) read-mutate-writeback-notify |
2026-06-05 | job-0021-infra-20260605 | OPENED: PyQGIS worker container Dockerfile + image build/push + Cloud Run Job deployment |
2026-06-05 | job-0022-infra-20260605 | OPENED: grace2 conda env recreation on Linux Debian via conda-forge (QGIS 3.40.3 + Python 3.12, dead-dep strip) |
2026-06-05 | job-0023-testing-20260605 | OPENED: M2 acceptance — QGIS Server GetCapabilities + WMS GetMap + worker round-trip + M1 regression (114 tests) green |
2026-06-05 | job-0022-infra-20260605 | grace2 conda env on Debian 13 via conda-forge — Miniforge3 + QGIS 3.40.3-Bratislava + Python 3.12.13 + gdal 3.10.2 + google-cloud-storage 3.11.0 + google-cloud-pubsub 2.38.0; dead-dep strip verified zero hits; commits 79d4917, cb85ba4 | approved [revisions: 1] |
2026-06-05 | job-0018-infra-20260605 | QGIS Server Cloud Run + GCS .qgs/COG/FGB buckets + Pub/Sub topic + AR repo; live at https://grace-2-qgis-server-425352658356.us-central1.run.app; image digest-pinned @sha256:7d8a338; GetCapabilities valid XML; PAP+UBLA on all 3 buckets; SA scoped objectViewer at bucket level; commits 1bcf14c, 5117202 | approved [revisions: 1] |
2026-06-05 | job-0019-engine-20260605 | Sample .qgs + styles/basemap.qml (CONUS, EPSG:4326, layer basemap-osm-conus); uploaded to gs://grace-2-hazard-prod-qgs/grace2-sample.qgs MD5-parity; local QgsProject.read() OK; live WMS qualified-fail (QGIS Server /vsigs/ gap → routed to NEW job-0024); commit 4878562 | approved [revisions: 0] |
2026-06-05 | job-0024-infra-20260605 | OPENED: QGIS Server /vsigs/ access fix (GDAL VSI env vars / gcsfuse) + QML preset bake — addresses OQ-19A surfaced by job-0019. Counter bumped 23→24. Gates M2 acceptance (job-0023). |
