# SFINCS scenario-coverage - implementation design (NATE 2026-06-26)

> DOC-GROUNDING RECONCILIATION (2026-06-26, supersedes the body where they conflict):
> after grounding in the SFINCS user manual (sfincs.readthedocs.io parameters.html), the
> as-built code corrects four physics values this design doc states wrongly. The CODE is the
> source of truth; ignore the body's `advection: 2` / old-default text:
> - advection has ONLY values 0 (SFINCS-LIE) or 1 (SFINCS-SSWE) - there is NO value 2. Every
>   `advection==2` / "set 2 for 2nd-order" / "advection SHOULD be 2" below (lines ~69, 87, 124,
>   226) is invalid; the registry range is now (0,1) and all archetypes use advection=1.
> - manual engine-baseline defaults (registry `default` fields, used for honest delta narration):
>   theta 1.0 (was 0.9), alpha 0.5 (was 0.75, range 0.1-0.75), huthresh 0.05 (was 0.01, range
>   0.001-0.1). These are documentation/narration baselines (validate_and_resolve_physics emits
>   ONLY user-overridden keys, so deck bytes are unchanged unless the user pins the key).
> - coriolis IS a real sfincs.inp keyword (default True) but inert while latitude==0.0; the
>   wind-archetype composer now pins coriolis_latitude=AOI-centre so Coriolis actually activates.
> All reconciled + 151 tests green against the real Mexico Beach fixture. See
> reports/inflight/engine-docs-grounding.md for the full manual findings.

Empirically validated against the installed hydromt_sfincs 1.2.2 in services/agent/.venv. Local proof uses the REAL fixture services/agent/tests/fixtures/sfincs_aoi/{dem.tif,landcover.tif} (Mexico Beach 3DEP+NLCD) - NOT synthetic - per NATE. Build order: physics-switches -> fluvial+compound auto-wire -> infiltration -> wind -> levee-breach -> tsunami. Agent-side only (ships in the agent bundle, no worker rebuild).


## FACET: DECK + FORCING emission (sfincs_builder.py + sfincs_forcing_adapter.py): physics switches, infiltration, levee-breach internal source, tsunami waveform synth, + the ForcingSpec contract additions the composer populates.
(confidence: high)

### Plan
VERIFIED against installed hydromt_sfincs 1.2.2 (.venv). Authoritative signatures captured by introspection:
  setup_config(self, **cfdict) -> set_config(key,value) PASSTHROUGH (accepts ANY key, no validation; sfincs.py:setup_config -> set_config). So advanced-physics keys land in sfincs.inp directly.
  setup_cn_infiltration(self, cn, antecedent_moisture='avg', reproj_method='med') -> writes scsfile (scs map). For a SINGLE-BAND GCN250 raster pass antecedent_moisture=None (the DataArray branch; 'avg' expects a cn_avg VAR inside a Dataset and will ValueError on a bare band).
  setup_constant_infiltration(self, qinf=None, lulc=None, reclass_table=None, reproj_method='average') -> writes qinffile (qinf map, mm/hr).
  setup_discharge_forcing(self, geodataset=None, timeseries=None, locations=None, merge=True, buffer=None) -> dis at ARBITRARY 'locations' Point cells (NOT just domain-edge). This IS the internal levee-breach src/dis seam.
  setup_river_inflow(..., src_type='inflow') -> domain-EDGE src only.
  Authoritative sfincs.inp keys (hydromt_sfincs/sfincs_input.py SfincsInput.__init__): advection(int,def 1), theta(float,def 1.0), alpha(float,def 0.5), huthresh(float,def 0.01), baro(int,def 1 = pressure/inverse-barometer term), viscosity(int,def 1), latitude(float,def 0.0), crsgeo(int,def 0), cdnrb=3 / cdwnd=[0,28,50] / cdval=[0.001,0.0025,0.0015] (the 3-point wind-drag curve), qinf(float,def 0.0), scsfile/qinffile/srcfile/disfile/bzsfile (filenames). write() emits "key = value" for every non-None attr.

=== HARD FINDING (physics_registry.py is WRONG on two keys — fix the registry, not just the wiring) ===
1. physics_registry.py:97 "coriolis" deck_target="sfincs.inp:coriolis" is INVALID — SFINCS has NO 'coriolis' boolean key. Coriolis in SFINCS is driven by `latitude` (constant-f plane) and/or `crsgeo=1` (geographic-aware f from grid lat). Re-spec the registry key: replace bool `coriolis` with float `latitude` (range (-90,90), deck_target "sfincs.inp:latitude") OR keep a bool `coriolis` but map it to crsgeo (coriolis True -> crsgeo:1). Recommend: rename to `coriolis_latitude` (float|None) -> sets sfincs.inp:latitude.
2. physics_registry.py:106 "wind_drag" deck_target="sfincs.inp:cdwnd" is WRONG — `cdwnd` is the wind-SPEED breakpoint vector [0,28,50] m/s, NOT a drag coefficient. The drag COEFFICIENTS live in `cdval` [0.001,0.0025,0.0015]. A constant-drag override must rewrite `cdval` to a flat list (e.g. [cd,cd,cd]) and keep cdnrb=3. Fix the deck_target to "sfincs.inp:cdval" and document the list semantics.

=== (A) PHYSICS SWITCHES (advection/theta/alpha/huthresh/coriolis/wind_drag) ===
sfincs_builder.py:
  - BuildOptions (dataclass @:469-535): add field `advanced_physics: dict[str, Any] | None = None` (the resolved-physics dict, already coerced by validate_and_resolve_physics; or accept the raw dict and resolve inside — but the composer should pass the RESOLVED dict for a single resolve point).
  - _generate_hydromt_yaml_config (@:1841) inside the setup_config block (after the dtout/dtmaxout lines at :1974-1975, BEFORE "setup_grid_from_region"): add a new helper call `_emit_physics_config(components, options.advanced_physics)`.
  - NEW function `_emit_physics_config(components: list[str], physics: dict|None)` (place right before _generate_hydromt_yaml_config, ~:1840): for each present key append a setup_config line:
      "advection": -> f"  advection: {int(v)}"
      "theta":     -> f"  theta: {float(v)}"
      "alpha":     -> f"  alpha: {float(v)}"
      "huthresh":  -> f"  huthresh: {float(v)}"
      "coriolis"/"coriolis_latitude": if a latitude float -> f"  latitude: {float(v)}"; if a bool True -> f"  crsgeo: 1"
      "wind_drag": -> emit a constant-drag curve: f"  cdnrb: 3", f"  cdwnd: [0.0, 28.0, 50.0]", f"  cdval: [{cd}, {cd}, {cd}]" (cd=float(v)); ONLY when v>0 (0=keep SFINCS default formula — the registry default is 0.0).
    These all land under the SAME setup_config: block (so they merge with crs/tref/tstart/tstop/dtout). YAML emits as a single dict -> setup_config(**cfdict) -> set_config per key -> sfincs.inp.
  - WIND PHYSICS GATE (the "wind PARTIAL" gap): advection/coriolis/wind_drag only PHYSICALLY matter when wind/pressure forcing is present. The block is unconditionally safe (setup_config passthrough), so emit whenever advanced_physics is set; but ALSO: when forcing.wind is present, the composer should default advanced_physics to {"advection":1} (already default) and the registry exposes coriolis/wind_drag for the user to lift. No extra gating needed in the builder — physics_registry validation already range-checks. baro: NOTE pressure forcing already activates the barotropic term via baro=1 (default); no new key needed.

=== (B) INFILTRATION (the MISSING archetype) ===
sfincs_forcing_adapter.py is the wrong file for a raster passthrough (it materializes hydrographs). The InfiltrationForcing CLASS belongs in sfincs_builder.py beside WaterlevelForcing (@:301), and emission belongs in _emit_surge_forcing_blocks (@:1719) OR a sibling. Plan:
  - sfincs_builder.py NEW dataclass right after PressureForcing (@:395), before ForcingSpec (@:397):
      @dataclass(frozen=True)
      class InfiltrationForcing:
          cn_uri: str | None = None              # GCN250 single-band CN GeoTIFF -> setup_cn_infiltration(cn=, antecedent_moisture=None)
          antecedent_moisture: str | None = None # "dry"|"avg"|"wet"|None; None for a single-band raster
          constant_mm_per_hr: float | None = None# -> setup_constant_infiltration(qinf=<const grid>) OR config qinf float
          lulc_uri: str | None = None            # optional: lulc+reclass_table path to setup_constant_infiltration
          reclass_table_uri: str | None = None
          provenance: dict[str, Any] = field(default_factory=dict)
  - ForcingSpec (@:397-459): add field `infiltration: InfiltrationForcing | None = None`; extend has_surge_forcing() to NOT include it (infiltration is a loss term, not a driver) — but it must still be emitted when set. Add the member to __init__ ordering after `pressure`.
  - _emit_surge_forcing_blocks (@:1719): after the pressure block (@:1838), add:
      inf = forcing.infiltration
      if inf is not None:
          if inf.cn_uri:
              components.append("setup_cn_infiltration:")
              components.append(f"  cn: '{_stage_gcs_local(inf.cn_uri)}'")
              # CRITICAL: single-band GCN250 -> antecedent_moisture must be None (emit literal `null`), else the cn_avg-VAR lookup ValueErrors.
              am = inf.antecedent_moisture
              components.append(f"  antecedent_moisture: {('null' if am is None else repr(am))}")
          elif inf.lulc_uri and inf.reclass_table_uri:
              components.append("setup_constant_infiltration:")
              components.append(f"  lulc: '{_stage_gcs_local(inf.lulc_uri)}'")
              components.append(f"  reclass_table: '{_stage_gcs_local(inf.reclass_table_uri)}'")
          elif inf.constant_mm_per_hr is not None:
              # No qinf-raster: simplest path is the scalar sfincs.inp `qinf` (mm/hr). Emit it into setup_config NOT here, since setup_constant_infiltration REQUIRES a raster/lulc. So route a bare constant through _emit_physics_config-style setup_config: `qinf: <v>`. (Document this branch lands in setup_config, not a setup_* step.)
    NOTE: setup_cn_infiltration pops the default `qinf` config and sets scsfile — so CN and constant are mutually exclusive (CN wins). Order: emit infiltration BEFORE setup_precip_forcing is irrelevant (different maps), but keep it after the surge blocks for readability.
  - YAML emits antecedent_moisture as the literal null/string; setup_cn_infiltration(cn=path, antecedent_moisture=None) then takes the bare-DataArray branch (confirmed in source: `elif not isinstance(da_org, xr.DataArray): raise` — a single-band raster IS a DataArray, so None passes).

=== (C) LEVEE-BREACH (internal point-source breach hydrograph) ===
The seam is setup_discharge_forcing with EXPLICIT `locations` (arbitrary interior Point) + NO setup_river_inflow. The current DischargeForcing ALREADY supports this (timeseries_uri+locations_uri with no rivers_uri/hydrography_uri -> _emit_surge_forcing_blocks @:1795 skips setup_river_inflow and emits setup_discharge_forcing with locations). So the DECK seam EXISTS. What's missing is:
  1. A breach-hydrograph SYNTHESIZER (composer-side, parallels _synthesize_parametric_surge_forcing) that writes a dis CSV + a src FGB at the breach point — but per facet split, the DECK side just needs to ACCEPT an interior src point list. No new builder code strictly required for the discharge path.
  2. ADVECTION must be ON for a breach jet (momentum-dominated) — covered by (A): composer sets advanced_physics={"advection":1} (default is already 1, so fine; expose so a user can set 2 for 2nd-order).
  3. ForcingSpec contract: ADD an optional `breach: DischargeForcing | None = None` member SO the composer can carry a breach src DISTINCT from a domain-edge river discharge (a compound run may have BOTH river inflow AND a breach). _emit_surge_forcing_blocks emits BOTH: the river `discharge` (with setup_river_inflow) AND, if `breach` is set, a SECOND setup_discharge_forcing with merge:true and the breach locations. hydromt setup_discharge_forcing(merge=True) MERGES into existing dis forcing — so two calls compose. Emit the breach block with explicit locations and NO rivers/hydrography.
     sfincs_builder.py @:1818 (after the river discharge block), add:
       br = forcing.breach
       if br is not None and br.timeseries_uri and br.locations_uri:
           components.append("setup_discharge_forcing:")
           components.append(f"  timeseries: '{_stage_gcs_local(br.timeseries_uri)}'")
           components.append(f"  locations: '{_stage_gcs_local(br.locations_uri)}'")
           components.append("  merge: true  # breach point-source merges with river dis")
  - ARRIVAL-TIME OUTPUT (postprocess facet, flagged for cross-seam): SFINCS writes a `twet`/time-of-arrival field only if requested; the deck knob is sfincs.inp `dtmaxout` already present + the time-varying zs(time) already enables a per-cell first-wet derivation in postprocess_flood. Builder change: none beyond ensuring output_interval_min is fine (already wired). Flag postprocess to compute arrival-time from the zs(time) stack.

=== (D) TSUNAMI (waveform bzs synthesizer) ===
Reuses the waterlevel bzs seam UNCHANGED in the builder (WaterlevelForcing.timeseries_uri+locations_uri -> setup_waterlevel_forcing). The ONLY new code is a SYNTHESIZER parallel to _synthesize_parametric_surge_forcing (model_flood_scenario.py:1975) — facet boundary: the time-series GENERATION is composer-side, but the FILE-WRITE seam (write_bzs_timeseries_csv / write_locations_fgb / ReanchoredSeries / StationHydrograph) lives in sfincs_forcing_adapter.py and is reused VERBATIM. Plan for the adapter:
  - sfincs_forcing_adapter.py: add a NEW pure synthesizer `synthesize_tsunami_bzs(bbox, *, eta_max_m, period_s, wave_type, lead_depression, window_hours, stage_dir=None) -> dict` returning the same {"timeseries_uri","locations_uri",...} shape as waterlevel_forcing_from_fgb. Waveforms:
      * SOLITARY: eta(t) = eta_max * sech^2( sqrt(3*eta_max/(4*h^3)) * c * (t - t0) ) — or the simpler time-domain sech^2 bump of half-width ~period_s.
      * LEADING-DEPRESSION N-WAVE: eta(t) = eta_max * (t-t0)/T * exp(-((t-t0)/T)^2) style derivative-of-Gaussian (trough THEN crest) — the canonical LDN tsunami signature.
    Drive the SAME 4-edge boundary points as the surge synth (reuse the edge_pts logic) and the SAME ReanchoredSeries/write_bzs_timeseries_csv writers. Returns provenance {"_prov_tsunami": True, "_prov_wave_type": wave_type, "_prov_eta_max_m": eta_max_m, "_prov_period_s": period_s}.
  - The deck side then needs NOTHING new: the dict flows through _build_surge_forcing_members -> WaterlevelForcing -> setup_waterlevel_forcing. setup_mask_bounds(btype="waterlevel") (@:1763) already converts seaward-edge cells to msk==2 — REQUIRED so the tsunami bzs is not inert (the same root-cause noted @:1746-1754).
  - PHYSICS for tsunami: advection SHOULD be 2 (or >=1) and huthresh small — expose via advanced_physics; composer defaults {"advection":1} which is adequate.

=== ForcingSpec ordering note ===
Final ForcingSpec fields (sfincs_builder.py:397): forcing_type, precip_inches, duration_hours, return_period_years, precip_magnitude_mm_per_hr, waterlevel, discharge, breach (NEW), wind, pressure, infiltration (NEW), provenance. All NEW members default None -> a pluvial deck stays byte-identical (Invariant 7).

### Contract changes
SHARED CONTRACT (builder <-> composer <-> postprocess):

1. sfincs_builder.py NEW dataclass `InfiltrationForcing` (frozen): fields cn_uri: str|None=None, antecedent_moisture: str|None=None, constant_mm_per_hr: float|None=None, lulc_uri: str|None=None, reclass_table_uri: str|None=None, provenance: dict=field(default_factory=dict).

2. sfincs_builder.py `ForcingSpec` (@:397) NEW optional members (all default None, all frozen):
   - breach: DischargeForcing | None = None   (interior point-source breach; reuses the existing DischargeForcing class with timeseries_uri+locations_uri set and rivers_uri/hydrography_uri left None)
   - infiltration: InfiltrationForcing | None = None
   has_surge_forcing() unchanged (infiltration is a loss term, breach IS a driver -> ADD breach to the has_surge_forcing() any()).

3. sfincs_builder.py `BuildOptions` (@:469) NEW field:
   - advanced_physics: dict[str, Any] | None = None   (RESOLVED physics dict from validate_and_resolve_physics('sfincs', overrides); keys subset of {advection,theta,alpha,huthresh,coriolis_latitude,wind_drag}).

4. model_flood_scenario.py composer NEW args on model_flood_scenario(@:2358): 
   - wind: dict|None=None (was missing as a top-level arg — passed today only via surge_forcing["wind"]; ADD as a first-class arg the LLM can set -> folds into surge_forcing).
   - infiltration: dict|None=None ({"cn_uri":..,"antecedent_moisture":..} or {"constant_mm_per_hr":..}) -> InfiltrationForcing on forcing_spec.
   - breach: dict|None=None ({"breach_lon":..,"breach_lat":..,"peak_cms":..,"start_hr":..}) -> synthesized to dis CSV+src FGB -> DischargeForcing on forcing_spec.breach.
   - tsunami: dict|None=None ({"eta_max_m":..,"period_s":..,"wave_type":"solitary"|"ldn","lead_depression":bool}) -> synthesize_tsunami_bzs -> WaterlevelForcing on forcing_spec.waterlevel.
   - advanced_physics: dict|None=None -> validate_and_resolve_physics('sfincs', .) -> BuildOptions.advanced_physics.

5. _build_surge_forcing_members (model_flood_scenario.py:1721) NEW: return a 5th member `infiltration` and a 6th `breach`, OR (cleaner) build the InfiltrationForcing/breach DischargeForcing inline in the composer at the ForcingSpec(...) construction site (@:2984/2995) so the helper signature churn is minimal. Recommend: keep _build_surge_forcing_members 4-tuple, add infiltration+breach construction inline at the two ForcingSpec(...) sites.

6. physics_registry.py CORRECTION (same-file, low-risk, default-preserving):
   - "coriolis" entry: change to type float key `coriolis_latitude`, range (-90.0, 90.0), default None-equivalent (keep registry shape; deck_target "sfincs.inp:latitude"). OR keep bool but deck_target -> "sfincs.inp:crsgeo".
   - "wind_drag" entry: deck_target "sfincs.inp:cdval" (was cdwnd), doc clarified to "constant drag coefficient written as a flat cdval curve [cd,cd,cd]".

7. sfincs_forcing_adapter.py NEW public fn `synthesize_tsunami_bzs(...)` added to __all__; reuses write_bzs_timeseries_csv/write_locations_fgb/ReanchoredSeries/StationHydrograph (no new file-format contract — same bzs CSV + bnd FGB shape).

### Local proof
TWO local-proof tiers, both established in-repo and runnable in .venv (hydromt_sfincs 1.2.2 present):

TIER 1 — YAML-EMISSION (fast, synthetic, no real DEM; the DOMINANT proof). Pattern = the `_emit()` helper in tests/test_sfincs_builder_surge_forcing.py:59 (calls _generate_hydromt_yaml_config with /tmp paths so _stage_gcs_local is a no-op, then yaml.safe_load -> dict, asserts on parsed keys). New assertions per archetype:
  (A) PHYSICS: BuildOptions(advanced_physics={"advection":2,"theta":0.95,"alpha":0.7,"huthresh":0.02,"coriolis_latitude":29.9,"wind_drag":0.0026}) -> assert deck["setup_config"]["advection"]==2, ["theta"]==0.95, ["alpha"]==0.7, ["huthresh"]==0.02, ["latitude"]==29.9, ["cdval"]==[0.0026,0.0026,0.0026] and ["cdnrb"]==3. Pluvial control: advanced_physics=None -> setup_config has NO advection/theta/etc beyond the existing crs/tref/dtout (byte-identical baseline, mirrors test_none_path_deck_yaml_unchanged @v2:339).
  (B) INFILTRATION: ForcingSpec(forcing_type="pluvial_synthetic", precip_inches=8, infiltration=InfiltrationForcing(cn_uri="/tmp/gcn250.tif", antecedent_moisture=None)) -> assert "setup_cn_infiltration" in deck, deck["setup_cn_infiltration"]["cn"]=="/tmp/gcn250.tif", deck["setup_cn_infiltration"]["antecedent_moisture"] is None. Constant variant: InfiltrationForcing(lulc_uri="/tmp/lc.tif", reclass_table_uri="/tmp/inf.csv") -> assert "setup_constant_infiltration" in deck with lulc+reclass_table.
  (C) LEVEE-BREACH: ForcingSpec(breach=DischargeForcing(timeseries_uri="/tmp/breach_dis.csv", locations_uri="/tmp/breach_src.fgb")) -> assert deck has setup_discharge_forcing with locations=="/tmp/breach_src.fgb" and merge is True and NO setup_river_inflow for that block. Compound: ALSO set discharge=DischargeForcing(rivers_uri=...) and assert BOTH a river setup_river_inflow+setup_discharge_forcing AND a second breach setup_discharge_forcing(merge:true) appear, river block FIRST (extend the @:131 ordering test).
  (D) TSUNAMI: drive synthesize_tsunami_bzs(bbox, eta_max_m=3.0, period_s=900, wave_type="ldn", window_hours=6) -> assert it returns {"timeseries_uri","locations_uri"} pointing to real files; read the bzs CSV back and assert the series is a leading-DEPRESSION (first finite value < base, a trough precedes the crest) for ldn, and a single sech^2 crest for "solitary". Then feed the dict through _build_surge_forcing_members -> ForcingSpec.waterlevel non-None -> _emit -> assert "setup_waterlevel_forcing" + "setup_mask_bounds"(btype waterlevel) both emitted.

TIER 2 — FULL IN-PROCESS BUILD (proves sfincs.inp keys actually land, not just YAML). Pattern = build a tiny SYNTHETIC DEM+landcover+CN GeoTIFF via the _write_precip_raster helper (test_model_flood_scenario_v2.py:70 — rasterio GTiff, EPSG:4326, from_bounds transform; use a 12x12 grid, DEM values e.g. a -5..+5 m ramp so setup_mask_active finds active cells, NLCD values from {11,21,42}, CN values 60-90), then call build_sfincs_model(dem_uri, landcover_uri, river_geometry_uri=None, forcing=<archetype ForcingSpec>, bbox, BuildOptions(autoscale_grid=False, grid_resolution_m=200, advanced_physics={...}), nlcd_vintage_year=2021, manning_mapping_csv=<fixture>). Then OPEN the written deck/sfincs.inp via hydromt_sfincs.sfincs_input.SfincsInput().read(<deck>/sfincs.inp) and assert: inp.advection==2, inp.theta==0.95, inp.alpha==0.7, inp.huthresh==0.02, inp.latitude==29.9, inp.cdval==[..], inp.scsfile=="sfincs.scs" (CN infiltration ran), inp.srcfile/inp.disfile set (breach), inp.bzsfile set + a msk==2 count>0 in the .msk (tsunami/surge). This catches a key that YAML emits but hydromt silently drops. Gate Tier-2 with the same import guard the existing builder uses (HYDROMT_UNAVAILABLE) so it skips where hydromt is absent. All four archetypes have an isolated synthetic build asserting exactly the inp keys above — no S3, no network, no Batch.

### Risks
- physics_registry coriolis key is fictional (no sfincs.inp:coriolis exists) — if wired as-is, setup_config(coriolis=True) writes a NO-OP `coriolis = True` line into sfincs.inp that the SFINCS binary ignores (silent wrong answer, Invariant-7 violation). MUST remap to latitude/crsgeo before wiring. Same for wind_drag -> cdwnd (wrong vector; cdwnd is the speed-breakpoint axis, cdval is the coefficients).
- setup_cn_infiltration with the DEFAULT antecedent_moisture='avg' RAISES on a single-band GCN250 raster (it looks for a `cn_avg` data_var). The deck MUST emit antecedent_moisture: null for the single-band fetcher output, or the CN fetcher must emit a 3-var Dataset (cn_dry/cn_avg/cn_wet). The single-band+null path is the lower-risk contract; assert it in Tier-2.
- setup_cn_infiltration POPS the default qinf config and sets scsfile — CN and a constant qinf are mutually exclusive; emitting both leaves the deck in an ambiguous state. Enforce precedence (CN wins) in _emit_surge_forcing_blocks and document it.
- A bare constant_mm_per_hr has NO setup_* method that takes a scalar (setup_constant_infiltration REQUIRES a raster or lulc+reclass). Routing it as a setup_config `qinf` float works but is a spatially-uniform loss that hydromt's later steps may not expect — verify qinf survives the build in Tier-2 (it may be popped by a downstream step).
- breach via a SECOND setup_discharge_forcing(merge=True) depends on hydromt MERGING two dis forcings rather than overwriting — setup_discharge_forcing(merge=True) is the contract but must be proven in Tier-2 (assert both the river src ids AND the breach src id survive in the final disfile/srcfile). If merge does not compose, fall back to a single combined locations FGB built composer-side.
- tsunami physically needs sub-minute output cadence + a small dt; the existing output_interval_min path floors at 60s and is coastal-gated — a tsunami intent must set is_coastal True (it routes through fetch_topobathy, which is correct) AND a fine output_interval_min, else the short-period wave is under-sampled and the animation reads as a single step.
- advection/coriolis only change RESULTS when wind/momentum forcing is present; for a pure-pluvial deck they are inert. Exposing them as composer args risks an LLM setting them on a pluvial run with no visible effect (confusing, not wrong) — keep them defaulted off and only narrate the delta when wind/surge/breach/tsunami forcing is also present.
- build_sfincs_model has a FORCING sanity gate (@:2216) that only checks precip for pluvial_* forcing_type; a pure-tsunami or pure-breach run (forcing_type still 'pluvial_synthetic' with precip_inches set, OR a new 'storm_surge'/'tsunami' type) may trip or bypass the gate. The composer currently ALWAYS sets pluvial_synthetic/observed; adding tsunami/breach-only intents needs the gate to accept a no-precip surge/tsunami deck (forcing_type='storm_surge' already exists in the docstring @:426 but the gate doesn't branch on it).


## FACET: COMPOSER auto-wiring + intents (model_flood_scenario.py) — new tool args on model_flood_scenario(:2358) + run_model_flood_scenario(:4065) that auto-fetch+auto-wire each archetype's forcing into the shared surge_forcing dict / ForcingSpec, then prove the resulting sfincs.inp + forcing files LOCALLY via build_sfincs_model on a synthetic DEM/landcover.
(confidence: high)

### Plan
All file refs are services/agent/src/grace2_agent/workflows/model_flood_scenario.py unless noted. The composer's job is to ADD intent flags that auto-FETCH + populate the existing surge_forcing dict (which already flows verbatim through _resolve_surge_forcing_from_fetchers:1821 -> _build_surge_forcing_members:1721 -> ForcingSpec -> _emit_surge_forcing_blocks at sfincs_builder.py:1719). The deck-emission machinery for waterlevel/discharge/wind/pressure ALREADY exists and is unit-proven; the composer just needs to fill the dict from the right fetchers. Infiltration/levee/tsunami additionally need NEW ForcingSpec members + builder blocks (sibling-facet contract additions I depend on).

=== (1) FLUVIAL auto-wire ===
Add a sibling helper `_autowire_river_discharge_forcing(bbox, *, duration_hr, data_sources)` modeled on `_autowire_coastal_surge_forcing(:2133)`. Degrade ladder (per data_source_fallback_norm): PRIMARY fetch_noaa_nwm_streamflow(bbox) (CONUS, key-free, returns FGB carrying streamflow_cms m^3/s) -> FALLBACK fetch_usgs_nwis_gauges(bbox=bbox, period="P{ceil(duration_hr/24)}D") (observed hydrograph FGB, cfs) -> LAST-RESORT skip (fluvial has no parametric synth; log + return None so the run proceeds pluvial). It returns a `{"discharge": {"fetch_uri": <fgb_uri>, "rivers_uri": <river_layer.uri>, "value_unit": "cms"|"cfs"}}` partial dict — the EXISTING resolve at :1890-1921 already materialises discharge.fetch_uri via discharge_forcing_from_fgb and even auto-infers cfs for usgs/nwis sources (:1902-1905), and pairs rivers_uri into setup_river_inflow. CRITICAL: must thread the already-fetched `river_layer.uri` (from _fetcher_chain:2731) as `rivers_uri` so setup_river_inflow gets inflow points; restructure so the discharge auto-wire runs AFTER the fetcher chain (river_layer available at :2933) — put it in the Step-5 block alongside the coastal auto-wire at :2966, NOT inside _fetcher_chain.
WIRING POINT: at model_flood_scenario:2966-2973, today only coastal auto-wires. Add a parallel branch:
```
if river and not (surge_forcing or {}).get("discharge"):
    _dq = await asyncio.to_thread(_autowire_river_discharge_forcing, resolved_bbox, duration_hr=float(duration_hr), data_sources=data_sources)
    if _dq:
        surge_forcing = {**(surge_forcing or {}), **_dq}
```
This runs BEFORE _resolve_surge_forcing_from_fetchers:2974 so the fetch_uri gets materialised. Gate so fluvial does NOT force is_coastal/topobathy: a new `river: bool` flag must NOT be added to the is_coastal signal at :2537 (keep fluvial on fetch_dem land-DEM unless coastal also set). New composer arg: `river: bool = False` on model_flood_scenario(:2358) signature and run_model_flood_scenario(:4065). Mirror through the call at :4259-4276 (add river=river).

=== (2) COMPOUND intent ===
New composer arg `compound: bool = False`. When True, set BOTH is_coastal triggers AND river: at the top of model_flood_scenario, after :2537 compute `coastal = coastal or compound` and `river = river or compound`. This makes is_coastal True (-> topobathy + coastal surge auto-wire at :2966 fills waterlevel) AND the new fluvial branch fills discharge, AND the always-present Atlas-14 precip path (:2796-2826) supplies setup_precip_forcing. Result: one ForcingSpec with waterlevel + discharge + precip — already proven coherent by test_sfincs_builder_surge_forcing.py::test_compound_deck_carries_precip_and_surge(:281). No new deck code. Add to run_model_flood_scenario signature + passthrough.

=== (3) WIND arg ===
New composer arg `wind: dict | None = None` shaped `{"magnitude": <m/s>, "direction": <deg-from>}` OR `{"grid_uri": <nc>}` (a user/ERA5 supply — NOT fabricated). When set, merge into surge_forcing before resolve: `surge_forcing = {**(surge_forcing or {}), "wind": wind}` near :2966. _build_surge_forcing_members:1793-1806 already builds WindForcing -> _emit_surge_forcing_blocks:1819-1830 emits setup_wind_forcing. For the PHYSICS switches (advection/coriolis/wind_drag — physics_registry.py:64-106 STEP-3, currently NOT in sfincs.inp), add composer arg `advanced_physics: dict | None = None` and pass it to build_sfincs_model at :3051 as a new kwarg. CONTRACT DEPENDENCY (builder facet): build_sfincs_model(:2149) must accept advanced_physics, validate via physics_registry.resolve_advanced_physics("sfincs", advanced_physics), and emit the resolved keys into the setup_config block (sfincs_builder.py:1923). Composer DEFAULTS: when wind is set and advanced_physics is None, the composer injects `advanced_physics = {"coriolis": True, "advection": 1}` so a wind run actually flips the momentum/coriolis physics rather than silently emitting wind with default-off advection. Add wind + advanced_physics to run_model_flood_scenario signature + passthrough + docstring.

=== (4) INFILTRATION flag ===
New composer arg `infiltration: bool | str = False` (True -> auto-fetch GCN250; str -> verbatim CN raster URI; mirrors building_obstacles tri-state). Add helper `_resolve_infiltration_uri(infiltration, bbox, data_sources)` modeled on _resolve_building_obstacle_uri(:2262): True -> fetch_gcn250_curve_numbers(bbox, antecedent_moisture="average") (key-free global GeoTIFF, CN 0-100), best-effort degrade to None on failure (never abort). Off-load via asyncio.to_thread near :3013. Then thread the CN URI into ForcingSpec as a NEW member `infiltration: InfiltrationForcing | None` at the ForcingSpec construction (:2984/2995). CONTRACT DEPENDENCY (builder facet): add InfiltrationForcing dataclass (cn_uri) to sfincs_builder.py, add `infiltration: InfiltrationForcing|None` to ForcingSpec(:398), and emit `setup_cn_infiltration:`/`setup_infiltration:` in _emit_surge_forcing_blocks. Composer just resolves the URI + constructs the member. Add infiltration to run_model_flood_scenario signature + passthrough + docstring.

=== (5) LEVEE-BREACH intent (user-gated, never fabricated) ===
New composer args `breach_point: tuple[float,float] | None = None` (lon,lat — a DRAWN point) + `breach_peak_discharge_m3s: float | None = None` + optional `breach_arrival_hr: float | None = None`. Per feedback_never_fabricate_model_inputs_user_gate: the composer must NOT invent these — if a breach intent is detected but breach_point/breach_peak missing, return a typed user-input-gate (raise WorkflowError / emit the generic input-gate card per task #192) rather than synthesizing. When both present, synthesize a triangular breach hydrograph (rise to breach_peak by breach_arrival_hr, recess) as an internal point-source discharge — reuse the discharge seam: build a 1-station dis CSV + a 1-point locations FGB at breach_point via a new helper `_synthesize_breach_discharge_forcing(breach_point, peak_m3s, arrival_hr, duration_hr)` (modeled on _synthesize_parametric_surge_forcing:1975 but discharge not waterlevel) and inject as `surge_forcing["discharge"] = {"timeseries_uri": <csv>, "locations_uri": <fgb>}` (pre-materialised path — bypasses setup_river_inflow since the point carries its own geometry, matching adapter note sfincs_forcing_adapter.py:935-938). The arrival-time output + advection switch are builder-facet; composer sets `advanced_physics["advection"] = 1` for breach runs. Add the 3 breach args to both signatures + passthrough + docstring.

=== (6) TSUNAMI intent ===
New composer args `tsunami: bool = False` + `tsunami_wave_height_m: float | None = None` + `tsunami_period_min: float | None = None` (user-supplied wave-form, never fabricated — same gate as breach: if tsunami=True but height missing -> input-gate). When set, implies is_coastal (add `coastal = coastal or tsunami` near :2537 so topobathy + seaward msk==2 boundary fire). Add helper `_synthesize_tsunami_waterlevel_forcing(bbox, wave_height_m, period_min, duration_hr)` modeled on _synthesize_parametric_surge_forcing:1975 but emitting a leading-depression N-wave / raised-cosine pulse of the given amplitude+period instead of the monotone storm rising-limb. Inject as `surge_forcing["waterlevel"] = {"timeseries_uri": <bzs csv>, "locations_uri": <bnd fgb>}` (pre-materialised). This reuses the ENTIRE existing waterlevel bzs seam (setup_mask_bounds + setup_waterlevel_forcing at sfincs_builder.py:1761-1791) with zero new deck code. Gate: tsunami auto-wire must run BEFORE the coastal storm-surge auto-wire at :2966 and set a sentinel so the storm parametric synth doesn't also fire (a tsunami run is NOT a storm surge). Add tsunami args to both signatures + passthrough + docstring.

=== Ordering inside Step-5 (the single edit region ~:2957-2981) ===
Replace the lone `if is_coastal and not surge_forcing:` block with a precedence ladder, all before _resolve_surge_forcing_from_fetchers:2974:
1. tsunami -> waterlevel (pre-materialised)
2. coastal storm-surge auto-wire (existing _autowire_coastal_surge_forcing) ONLY if no waterlevel yet
3. breach -> discharge (pre-materialised) OR fluvial river auto-wire -> discharge.fetch_uri
4. wind merge ; 5. infiltration resolved into ForcingSpec member after _build_surge_forcing_members
Each branch appends to the SAME surge_forcing dict so compound combinations compose.

### Contract changes
The composer (my facet) ADDS these tool args and depends on sibling-facet builder additions. Shared contract = the surge_forcing dict keys + ForcingSpec members + build_sfincs_model signature.

NEW model_flood_scenario(:2358) + run_model_flood_scenario(:4065) args (identical on both, threaded through the :4259 call):
- river: bool = False
- compound: bool = False
- wind: dict[str,Any] | None = None        # {"magnitude","direction"} | {"grid_uri"}
- advanced_physics: dict[str,Any] | None = None   # passed to build_sfincs_model
- infiltration: bool | str = False
- breach_point: tuple[float,float] | None = None
- breach_peak_discharge_m3s: float | None = None
- breach_arrival_hr: float | None = None
- tsunami: bool = False
- tsunami_wave_height_m: float | None = None
- tsunami_period_min: float | None = None

surge_forcing dict keys the composer now populates (all ALREADY consumed by the existing resolve/build seam EXCEPT infiltration):
- "discharge": {"fetch_uri","rivers_uri","value_unit"}  (RAW fetcher path, resolve:1890 handles it)
- "discharge": {"timeseries_uri","locations_uri"}        (breach pre-materialised path)
- "waterlevel": {"timeseries_uri","locations_uri"}        (tsunami pre-materialised path)
- "wind": {"magnitude","direction"} | {"grid_uri"}        (build:1793 handles it)

CONTRACT ADDITIONS I DEPEND ON (builder facet — sfincs_builder.py):
- build_sfincs_model(:2149) gains `advanced_physics: dict|None = None`; validates via physics_registry.resolve_advanced_physics("sfincs", ...) and emits resolved keys (advection/coriolis/cdwnd/alpha/theta/huthresh) into the setup_config block (:1923). physics_registry.py:64-106 already declares the deck_targets.
- new InfiltrationForcing dataclass (cn_uri: str) + ForcingSpec.infiltration member (ForcingSpec at :398) + setup_cn_infiltration/setup_infiltration emission in _emit_surge_forcing_blocks(:1719).
- (breach arrival-time output is a postprocess-facet contract: an arrival-time band in the run NetCDF.)

tool_arg_normalizer.py:109 — add aliases (e.g. "fluvial"->"river", "storm_surge_compound"->"compound") under run_model_flood_scenario so LLM-invented kwargs land on canon args.

### Local proof
Two layers, both offline/no-network (hydromt_sfincs 1.2.2 is in services/agent/.venv; builds run in-process).

LAYER A — composer auto-wire unit (mock fetchers + build_sfincs_model, like test_coastal_surge_autowire.py): patch the fetcher in each new helper to a MagicMock returning a fake LayerURI, patch build_sfincs_model to capture the ForcingSpec it receives, call model_flood_scenario(bbox=..., <flag>=True), then assert the captured ForcingSpec carries the right member:
- river=True -> patch fetch_noaa_nwm_streamflow -> assert spec.discharge is not None AND spec.discharge.timeseries_uri set AND is_coastal stayed False (DEM fetch == fetch_dem, not topobathy).
- compound=True -> assert spec.waterlevel AND spec.discharge AND spec.precip_inches all non-None in ONE spec.
- wind={"magnitude":45,"direction":170} -> assert spec.wind.magnitude==45 AND the advanced_physics dict passed to build_sfincs_model carries coriolis=True/advection=1.
- infiltration=True -> patch fetch_gcn250_curve_numbers -> assert spec.infiltration.cn_uri set.
- breach_point given but breach_peak None -> assert a typed user-input-gate (WorkflowError / failed envelope), NOT a fabricated hydrograph.
- tsunami=True,height=3 -> assert spec.waterlevel set from the N-wave synth AND the storm parametric synth was NOT called (mock _synthesize_parametric_surge_forcing, assert_not_called).
- REGRESSION: all flags default False -> spec.waterlevel/discharge/wind/infiltration all None (byte-identical pluvial), _autowire helpers never called.

LAYER B — real deck assertion on a SYNTHETIC DEM/landcover (the flopy-analog, no mocks, like test_sfincs_builder_surge_forcing.py): write a tiny synthetic single-band float32 NAVD88-m DEM GeoTIFF + a synthetic NLCD-class GeoTIFF (rasterio, ~20x20 cells over a small bbox), build the per-archetype ForcingSpec the composer would produce, call build_sfincs_model(dem_uri, landcover_uri, river_geometry_uri, forcing=spec, bbox, options), then read the generated deck dir and assert the sfincs.inp + forcing files carry the archetype's switches:
- fluvial: assert sfincs.inp references a dis/src file AND the .dis CSV exists with finite m^3/s columns AND setup_river_inflow ran (src points present).
- compound: assert bzs AND dis AND netampr/precip all present in one deck.
- wind: assert sfincs.inp has cdwnd/advection/coriolis lines from advanced_physics (the parse-keys assertion).
- infiltration: assert sfincs.inp references the scsfile/qinffile (CN infiltration) — depends on builder facet landing setup_cn.
- tsunami: assert a bzs file whose series is the N-wave pulse (peak == tsunami_wave_height_m, zero-crossing at ~period) and msk has ==2 seaward boundary cells.
Run: `cd services/agent && .venv/bin/python -m pytest tests/test_coastal_surge_autowire.py tests/test_sfincs_builder_surge_forcing.py -q` plus the new archetype tests.

### Risks
- is_coastal coupling: a `river=True` (fluvial-only) run must NOT route through fetch_topobathy. The is_coastal signal at :2537 keys off coastal|surge_forcing|quadtree — adding a discharge key to surge_forcing for fluvial would NOT flip is_coastal (good), but I must NOT add `river` to the is_coastal expression. Verify the fluvial deck still fetches fetch_dem.
- Ordering hazard: discharge auto-wire needs river_layer.uri (fetched in _fetcher_chain, available only at :2933 after the chain), so it MUST live in the Step-5 block (:2966), NOT in _fetcher_chain. The existing coastal auto-wire already proves this placement.
- Builder-facet dependency: advanced_physics passthrough + InfiltrationForcing member + setup_cn/setup_infiltration emission do NOT exist yet (build_sfincs_model:2149 has no advanced_physics arg; physics_registry is STEP-3-unwired). Layer-B infiltration/wind-physics proof is BLOCKED until the builder facet lands those — sequence builder before composer, or stub-assert in Layer A only.
- Fabrication gate: breach + tsunami MUST hard-gate on user-supplied magnitude (feedback_never_fabricate_model_inputs_user_gate / task #192). Synthesizing a default breach_peak or tsunami height would violate the invariant — design returns a typed input-gate instead, and the test asserts the gate fires.
- Surge-forcing precedence: tsunami waterlevel vs coastal storm-surge auto-wire both target surge_forcing['waterlevel'] — need a sentinel so the storm parametric synth at :2244 doesn't overwrite/duplicate the tsunami N-wave. Same for breach-discharge vs fluvial-discharge both targeting ['discharge'].
- NWM/NWIS unit correctness: NWM streamflow_cms is m^3/s, NWIS is cfs. The resolve at :1902-1905 auto-infers cfs for usgs/nwis source URIs; the new helper must set value_unit explicitly (cms for NWM, cfs for NWIS) so the dis series isn't 35.3x off (Invariant-7 silent-wrong-physics).
- Tool-schema docstring bloat: run_model_flood_scenario's docstring IS the LLM tool schema (registered main.py:80). Adding 9 args risks a huge schema + mis-selection; keep each arg's doc tight and add normalizer aliases for the obvious LLM synonyms.


## FACET: LOCAL-PROOF HARNESS + TESTS (the test-locally-first gate for full SFINCS archetype coverage)
(confidence: high)

### Plan
EMPIRICAL GROUND TRUTH (all run live against services/agent/.venv, hydromt_sfincs 1.2.2, fully offline with synthetic rasters):

1. build_sfincs_model RUNS FULLY IN-PROCESS OFFLINE with a 20x20 synthetic DEM + landcover GeoTIFF (rasterio) + subset Manning CSV. Confirmed: a pluvial ForcingSpec produced a real deck dir with sfincs.inp + sfincs.dep/.msk/.ind/.man/.precip + gis/. NO network, NO real DEM fetch. The NLCD gate passes when landcover classes {11,41} are a subset of the Manning CSV.

2. *** THE LOAD-BEARING HARNESS CONSTRAINT (the single thing the whole facet hinges on) ***: build_sfincs_model writes the deck into an INTERNAL `tempfile.TemporaryDirectory(prefix="sfincs-build-")` at sfincs_builder.py:2358 (`tmp / "deck"`), and at :2506 it SKIPS the object-store upload for any non-s3:// manifest URI ("manifest is local; skipping object-store upload"). So when output_setup_uri is a local/file path, the returned ModelSetup.setup_uri points at a manifest.json that is NEVER WRITTEN and the deck dir is DESTROYED on function return. I verified this: after a successful local build the only surviving files were my input dem.tif/lc.tif/manning.csv — sfincs.inp was gone. THEREFORE the harness CANNOT read the deck from the returned setup_uri. The deck must be captured by intercepting the builder's TemporaryDirectory.

   PROVEN CAPTURE METHOD (verified working): monkeypatch the builder-module's tempfile.TemporaryDirectory with a persistent shim that mkdtemp()s into a test-owned dir instead of auto-deleting:
     ```
     import grace2_agent.workflows.sfincs_builder as B
     class _PersistTmp:
         def __init__(self, prefix=""): self.name = tempfile.mkdtemp(prefix=prefix, dir=capture_root)
         def __enter__(self): return self.name
         def __exit__(self, *a): return False
     with mock.patch.object(B.tempfile, "TemporaryDirectory", _PersistTmp):
         setup = build_sfincs_model(...)
     # deck is at <capture_root>/<random>/deck/sfincs.inp  (glob it)
     ```
   This survived the build and let me read sfincs.inp verbatim. (Alt fallback method, NOT needed: point output_setup_uri at s3:// and patch solver._get_s3_client to a dict-backed fake — but the TemporaryDirectory-shim is simpler and reads the EXACT bytes hydromt wrote, so use it.)

3. THE ASSERTABLE DECK SURFACE — what each archetype writes, confirmed by reading real sfincs.inp + deck dir listings:
   - PLUVIAL: sfincs.inp has `precipfile = sfincs.precip`; deck dir contains `sfincs.precip`. (proven)
   - FLUVIAL: a DischargeForcing(timeseries_uri=dis.csv, locations_uri=src.geojson) produced sfincs.inp keys `disfile = sfincs.dis` + `srcfile = sfincs.src`, and the deck dir gained `sfincs.dis` + `sfincs.src`. (proven — this is the exact deck-emission proof that the composer "never auto-wires discharge" gap will be measured against)
   - Default physics keys present in EVERY deck (from the proven pluvial sfincs.inp): `advection = 1`, `qinf = 0.0` (infiltration OFF by default), `baro = 1`, `cdwnd = 0.0 28.0 50.0` + `cdval = ...` (wind-drag table), `viscosity = 1`. These are the regression baselines: assert qinf==0.0 in the no-infiltration deck, advection==1 default, etc.

4. hydromt_sfincs API for the MISSING archetypes (live inspect.signature, so the builder edits have real targets):
   - INFILTRATION: `setup_constant_infiltration(qinf=, lulc=, reclass_table=)` → writes the `qinf`/`qinffile` key; `setup_cn_infiltration(cn=, antecedent_moisture=)` → writes `scsfile = sfincs.scs`. Both EXIST in 1.2.2. So the infiltration deck assertion target is `scsfile`/`sfincs.scs` (CN path) OR a non-zero `qinf` (constant path).
   - WIND uniform: `setup_wind_forcing(timeseries=, magnitude=, direction=)` EXISTS (already emitted by _emit_surge_forcing_blocks). The deck-content gap is the PHYSICS switch, not the file — wind drag (cdwnd/cdval) is already on by default; what's MISSING is that wind is not a composer arg and advection/coriolis are physics_registry STEP-3 (unwired into setup_config). So the wind assertion = a wnd forcing file written AND the advection/coriolis/wind_drag knobs reach sfincs.inp via setup_config.
   - LEVEE-BREACH: model `setup_structures` / internal src point (a DischargeForcing src point used as the breach hydrograph) → srcfile, plus an advection switch.
   - TSUNAMI: reuses the bzs waterlevel seam (`bzsfile`); the gap is a wave-form synth (no parametric tsunami in the builder, only the storm raised-cosine at model_flood_scenario.py:1975).

PER-ARCHETYPE LOCAL-PROOF DESIGN (the gate):

Build ONE shared synthetic-fixture helper + ONE deck-capture helper, then one in-process build test per archetype. The shared helper (new file services/agent/tests/_sfincs_synth.py, or a conftest fixture):
  - `synth_coastal_inputs(tmp_path)` → writes a 20x20 DEM GeoTIFF (np.linspace(-3..+8) west→east so there's a real seaward low edge AND inland high edge — required so setup_mask_bounds finds msk==2 cells for waterlevel/tsunami), an NLCD landcover GeoTIFF (classes 11+41 only), and a 2-class Manning CSV. Returns (dem_path, lc_path, map_path, bbox).  (EXACTLY the rasters I built in the probe — proven to drive a clean build.)
  - `build_and_capture(dem, lc, map_csv, forcing, options, capture_root)` → applies the _PersistTmp monkeypatch, calls build_sfincs_model(nlcd_vintage_year=2021), globs `<capture_root>/**/deck/sfincs.inp`, returns (setup, deck_dir, inp_text, sorted(os.listdir(deck_dir))).
  - `inp_key(inp_text, key)` → parse the `key = value` lines (split on '=') so assertions read cleanly.

Then per archetype, the in-process build test asserts (forcing built via the existing typed dataclasses):
  - PLUVIAL: ForcingSpec(forcing_type="pluvial_synthetic", precip_inches=8, duration_hours=24). ASSERT `precipfile` in inp AND "sfincs.precip" in deck listing AND `qinf`==0.0 (infiltration baseline off).
  - FLUVIAL: ForcingSpec(forcing_type="fluvial", discharge=DischargeForcing(timeseries_uri=dis.csv, locations_uri=src.geojson)). ASSERT `disfile`+`srcfile` in inp AND {"sfincs.dis","sfincs.src"} <= deck listing AND NO precipfile (pure fluvial).  (proven shape)
  - COMPOUND: ForcingSpec with waterlevel=WaterlevelForcing(timeseries_uri=bzs.csv, locations_uri=bnd.geojson) + discharge=DischargeForcing(...) + precip_inches set. ASSERT all of `bzsfile`(or bndfile) + `disfile`/`srcfile` + `precipfile` co-present in ONE deck (the single-intent-fetches-all proof).
  - INFILTRATION: ForcingSpec with the NEW infiltration member (cn_uri or qinf). ASSERT `scsfile`==sfincs.scs (CN path) OR `qinf` != 0.0 (constant path) AND "sfincs.scs"/sfincs.qinf in deck listing.
  - WIND: ForcingSpec(wind=WindForcing(magnitude=45, direction=170)) + the NEW physics threading. ASSERT a wnd forcing artifact AND `advection`/`cdwnd`/coriolis (`latitude`!=0 when coriolis on) reach sfincs.inp via setup_config.
  - LEVEE: a DischargeForcing src point used as the internal breach hydrograph + advection on. ASSERT an internal `srcfile` point AND `advection`>=1.
  - TSUNAMI: WaterlevelForcing carrying the synthesized tsunami bzs wave-form. ASSERT `bzsfile`/`bndfile` in inp AND msk==2 boundary cells exist (read sfincs.msk via the gis/ raster or assert setup_mask_bounds emitted in the YAML).

THE FAST GATE (no hydromt build needed) — keep + extend the existing YAML-emission tests (test_sfincs_builder_surge_forcing.py pattern: call _generate_hydromt_yaml_config, yaml.safe_load, assert the setup_* block keys). EVERY archetype gets a fast YAML-emission test (asserts the block is emitted with right kwargs) AND a slower in-process build test (asserts the real deck files). The fast tests run on every commit; the build tests gate the coverage claim and run under a marker.

GUARDING THE BUILD TESTS: mark with `@pytest.mark.sfincs_build` and `pytest.importorskip("hydromt_sfincs")` + `pytest.importorskip("rasterio")` at module top, so CI without the heavy venv skips cleanly while the local-proof gate (run in services/agent/.venv) executes them. The binary SOLVE is NOT runnable locally (docker needs sudo = NATE step) — the in-process hydromt build IS the local proof; binary solve stays the existing Batch path + NATE Haiku prod-test.

### Contract changes
The harness asserts against the SHARED contract the builder+composer own; the harness itself adds NO contract, it LOCKS these additions (which builder/composer/postprocess facets must implement, and which my tests assert exist):

1. ForcingSpec (sfincs_builder.py:397) gains an `infiltration: InfiltrationForcing | None = None` member (parallel to waterlevel/discharge/wind/pressure). NEW frozen dataclass InfiltrationForcing{cn_uri: str|None, qinf_mm_hr: float|None, antecedent_moisture: str|None, provenance: dict} — cn_uri drives setup_cn_infiltration (scsfile), qinf_mm_hr drives setup_constant_infiltration (qinf). My infiltration build test asserts scsfile/qinf in the deck FROM this member.

2. has_surge_forcing() (sfincs_builder.py:461) extends to include infiltration (or a new has_extra_forcing()). My regression test asserts a pure-pluvial deck (no new members) stays byte-identical (no scsfile/disfile/bzsfile/wndfile).

3. _emit_surge_forcing_blocks (sfincs_builder.py:1719) gains an infiltration block (setup_cn_infiltration / setup_constant_infiltration) AND a wind-physics passthrough into setup_config (advection/coriolis/wind_drag from physics_registry "sfincs" entries: advection sfincs.inp:advection, coriolis via latitude, cdwnd/cdval). My YAML-emission tests assert these blocks emit; my build tests assert they reach sfincs.inp.

4. BuildOptions (sfincs_builder.py:516) MAY gain a physics-override field (e.g. advanced_physics: dict|None) that threads physics_registry keys into setup_config. My wind/levee tests assert advection/coriolis land in sfincs.inp.

5. model_flood_scenario (model_flood_scenario.py:2358) gains archetype-driving args: an intent/archetype selector (or explicit `discharge`/`infiltration`/`wind`/`tsunami` args) so fluvial/compound auto-wire discharge (today _autowire_coastal_surge_forcing:2133 is waterlevel-only). My composer-level tests (extend test_model_flood_scenario_surge_plumbing.py via _build_surge_forcing_members) assert the new args map to the new ForcingSpec members.

6. Builder-internal seam the harness DEPENDS ON (do NOT silently change): build_sfincs_model writes the deck into the module-level `tempfile.TemporaryDirectory` (sfincs_builder.py:2358) and skips upload for non-s3 manifests (:2506). The harness captures the deck by monkeypatching that TemporaryDirectory. If a builder facet changes this to delete-before-return differently, the harness capture helper must move in lockstep — flag any such change.

### Local proof
PROVEN (I ran each of these live against services/agent/.venv, hydromt_sfincs 1.2.2, fully offline):

SYNTHETIC INPUTS (the minimal substitute for fetched rasters — confirmed sufficient, NO network needed):
- DEM: rasterio GTiff, 20x20, EPSG:4326, from_bounds over a tiny bbox (-85.45,29.92,-85.38,29.98 ~ Mexico Beach), data = np.tile(np.linspace(-3.0, 8.0, 20), (20,1)) — west sea, east land (needed so setup_mask_bounds finds the seaward low edge for waterlevel/tsunami AND the build doesn't error "No active cells").
- Landcover: GTiff uint8, classes 11 (west half) + 41 (east half) ONLY.
- Manning CSV: nlcd_class,manning_n,description with rows 11 + 41 only (passes the NLCD gate as a subset; threaded via manning_mapping_csv= and nlcd_vintage_year=2021).

THE EXACT CALL (proven to produce a full real deck):
  build_sfincs_model(dem_uri=dem_path, landcover_uri=lc_path, river_geometry_uri=None, forcing=<archetype ForcingSpec>, bbox=bbox, options=BuildOptions(grid_resolution_m=100.0, simulation_hours=24.0, autoscale_grid=False, output_setup_uri=os.path.join(work,"out","manifest.json")), nlcd_vintage_year=2021, manning_mapping_csv=map_path)
  — wrapped in `with mock.patch.object(grace2_agent.workflows.sfincs_builder.tempfile, "TemporaryDirectory", _PersistTmp)` so the deck survives.

WHAT TO READ + ASSERT (verified against real captured sfincs.inp + deck listings):
- PLUVIAL deck I captured contained: ['gis','hydromt.log','sfincs.dep','sfincs.ind','sfincs.inp','sfincs.man','sfincs.msk','sfincs.precip']; sfincs.inp had `precipfile = sfincs.precip`, `advection = 1`, `qinf = 0.0`, `cdwnd = 0.0 28.0 50.0`. ASSERT: "sfincs.precip" in deck_listing; inp_key(inp,"precipfile")=="sfincs.precip".
- FLUVIAL deck I captured (DischargeForcing with a GeoJSON src point): ['gis','...','sfincs.dis','sfincs.inp','sfincs.src',...]; sfincs.inp had `disfile = sfincs.dis` + `srcfile = sfincs.src`. ASSERT: {"sfincs.dis","sfincs.src"} <= deck_listing; "disfile" and "srcfile" present in inp; "sfincs.precip" NOT in listing (pure fluvial).
- COMPOUND: build waterlevel+discharge+precip together, ASSERT bzs/bnd + dis/src + precip files ALL co-present (single-deck proof).
- INFILTRATION: hydromt_sfincs.SfincsModel exposes setup_cn_infiltration(cn,...) → `scsfile`=sfincs.scs and setup_constant_infiltration(qinf,...) → non-zero `qinf` (both confirmed present in 1.2.2 via inspect.signature). ASSERT scsfile/sfincs.scs OR qinf!=0.0.
- WIND: setup_wind_forcing(timeseries,magnitude,direction) present; ASSERT a wnd artifact + advection/cdwnd/latitude(coriolis) in inp.
- LEVEE: ASSERT an internal srcfile point + advection>=1.
- TSUNAMI: ASSERT bzsfile/bndfile + msk==2 boundary cells (read sfincs.msk or assert setup_mask_bounds in the YAML).

FAST GATE (no build): reuse the proven test_sfincs_builder_surge_forcing.py _emit() helper (calls _generate_hydromt_yaml_config, yaml.safe_load → dict, assert setup_* keys) — add one per new archetype.

NOTE the harmless `SfincsModel.__del__ AttributeError: 'NoneType' object has no attribute 'FileHandler'` printed after every successful build (sfincs.py:150 logger teardown on a fresh process) — it is NOT a failure (the build returns OK before it); tests should ignore stderr noise and assert on the returned setup + captured deck only.

BINARY SOLVE is NOT local-runnable (docker daemon needs sudo = NATE step). The in-process hydromt build is the entire local proof; binary solve stays the existing Batch path + NATE Haiku prod-test.

### Risks
- LOAD-BEARING: build_sfincs_model destroys its internal TemporaryDirectory and skips upload for non-s3 manifests, so the returned setup_uri's deck is EMPTY/unwritten for local builds. The harness MUST capture the deck via the TemporaryDirectory monkeypatch (proven) — a naive 'read setup.setup_uri' reads nothing. Any builder refactor of that temp-dir/upload seam silently breaks the capture; the harness helper is coupled to sfincs_builder.py:2358+:2506.
- The synthetic DEM MUST span below-zero (sea) AND above-zero (land) so setup_mask_active finds active cells and (for waterlevel/tsunami) setup_mask_bounds finds msk==2 seaward cells. A flat/all-positive synthetic DEM can build a deck with zero boundary cells (the live surge-inert bug at sfincs_builder.py:1747) — the harness must use the gradient DEM and, for surge/tsunami, assert msk==2 cells actually exist, not just that the YAML block emitted.
- WIND/LEVEE coverage is a PHYSICS-SWITCH gap (advection/coriolis/wind_drag are physics_registry STEP-3, NOT wired into setup_config), not a forcing-file gap. A build test that only checks a wnd file written will FALSELY pass while the physics stays inert. The wind/levee tests MUST read advection/latitude/cdwnd from the WRITTEN sfincs.inp, not from the ForcingSpec or YAML — that is the only true proof the switch reached the engine.
- hydromt_sfincs setup_cn_infiltration / setup_constant_infiltration EXIST in 1.2.2 but the builder has NO InfiltrationForcing class or emit block yet (MISSING archetype). The infiltration build test cannot pass until the builder facet adds that member+block; sequence the test AFTER the builder edit, or it red-blocks the suite.
- The post-build SfincsModel.__del__ AttributeError noise on stderr could be mistaken for a failure in CI logs; tests must assert on return value + captured deck, and the marker/skip must be set so a venv without hydromt_sfincs skips rather than errors.
- TSUNAMI reuses the bzs waterlevel seam but needs a NEW wave-form synth (only the storm raised-cosine exists at model_flood_scenario.py:1975). Until that synth lands, the tsunami test can only assert the bzs plumbing (reused), not a distinct tsunami waveform — scope the tsunami assertion to bzsfile+msk2 presence first, waveform shape second.
