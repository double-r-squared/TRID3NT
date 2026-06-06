## 5. Out-of-Scope (v0.1) — Deferred to Future Versions

Items deferred to future versions are not categorically excluded; they are simply staged behind work that's currently more tractable. See §2.3 for the engine-selection principle (Decision J in §2.1).

| Feature | Notes |
|---|---|
| 3D visualization | Pitch/bearing camera, terrain (DEM-derived), 3D building extrusions colored by impact. MapLibre supports all of these natively; deferred for v0.1 to reduce scope. Likely v0.2 alongside additional hazards. |
| Additional engines | Groundwater (MODFLOW), wildfire, seismic, contaminant transport — see engine catalog §2.3 for tentative integration modes and target versions |
| Solver feasibility research as agent capability | Level 1a of capability discovery (QGIS algorithm enumeration) and Level 1b (public hazard layer discovery) ship in v0.1; Levels 2 and 3 (plugin discovery, open layer discovery, external solver research) are tentative and deferred — see FR-AS-9 |
| Multi-hazard chaining | E.g., wildfire → debris flow, earthquake → tsunami, storm → spill |
| Multi-user collaboration | Shared sessions, project sharing, real-time co-presence |
| Custom domain MCP server | v0.1 uses MongoDB's off-the-shelf MCP; custom hazard-domain MCP tools could improve agent ergonomics later |
| Drawing / annotation tools | User-drawn AOIs, points of interest, annotations |
| Print and export reports | PDF reports of model runs with embedded maps and metrics |
| QGIS Desktop plugin distribution | A complementary desktop experience for power users |
| Mobile-specific UX | Mobile-responsive works; mobile-optimized doesn't |
| Multi-LLM provider abstraction | v0.1 uses Gemini 3 exclusively; abstraction would only matter for non-Google deployments |
| Solvers requiring substantial new tooling | Full 3D CFD flooding, coupled fire-atmosphere, 3D ocean simulation, multiphase debris flow — supported in principle by the architecture but not on the current roadmap |

---

