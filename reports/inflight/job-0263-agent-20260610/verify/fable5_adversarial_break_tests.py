"""Fable-5 adversarial verifier — break attempts against the job-0263 URI registry.

REFUTE-by-default probes, organized by attack class:

  A. near-miss hashes that should NOT match (threshold boundaries)
  B. handle / minted-handle collisions
  C. cross-session leakage (store keying + ContextVar concurrency)
  D. false-positive substitution hazards (foreign-bucket hijack,
     descriptive-stem prefix, wrong-extension same-dir, wrong-run)
  E. capacity / eviction integrity
  F. ambiguity-refusal under ties

Tests marked XFAIL_DESIGN document behaviors the verifier judges to be
design flaws rather than claim-refuting bugs; everything else asserts the
behavior the job report PROMISES.
"""

from __future__ import annotations

import asyncio

import pytest

from grace2_agent.uri_registry import (
    SessionUriRegistry,
    UriResolutionError,
    activate_registry,
    deactivate_registry,
    get_uri_registry,
    observe_published_layer,
    reset_uri_registries_for_tests,
)

CACHE = "gs://grace-2-hazard-prod-cache/cache/static-30d"
RUNS = "gs://grace-2-hazard-prod-runs"
REAL_NSI = f"{CACHE}/usace_nsi/852a6cc379b18c865bf9d99ec1acaa35.fgb"
NSI_ID = "usace-nsi--81.9126-26.5476--81.7511-26.6892"
COG_A = f"{RUNS}/01KTS5W9GTE7A7WPC3BNBE10EQ/flood_depth_peak.tif"
COG_B = f"{RUNS}/01KTS8H8RJT6311A2V4BKX6H8A/flood_depth_peak.tif"


@pytest.fixture(autouse=True)
def _clean():
    reset_uri_registries_for_tests()
    yield
    reset_uri_registries_for_tests()


def reg(sid: str = "adv") -> SessionUriRegistry:
    return get_uri_registry(sid)


# ------------------------------------------------------------------------- #
# A. Near-miss hashes that should NOT match
# ------------------------------------------------------------------------- #


class TestNearMissThresholds:
    def test_11_char_prefix_does_not_hash_match_with_two_files_in_dir(self):
        """Shared prefix below 12 + ambiguous dir => MUST error, not guess."""
        r = reg()
        r.record("h-a", uri=f"{CACHE}/compute_hillshade/090a4ff8d9aBBBBBBBBBBBBBBBBBBBBB.tif")
        r.record("h-b", uri=f"{CACHE}/compute_hillshade/ffff4ff8d9aCCCCCCCCCCCCCCCCCCCCC.tif")
        # 11 shared chars with h-a ("090a4ff8d9a"), 0 with h-b; two files in
        # the dir so sub-branch (d) cannot rescue either.
        with pytest.raises(UriResolutionError):
            r.resolve_params(
                "publish_layer",
                {"layer_uri": f"{CACHE}/compute_hillshade/090a4ff8d9aDDDDDDDDDDDDDDDDDDDDD.tif"},
            )

    def test_exactly_12_char_prefix_matches_by_design(self):
        r = reg()
        r.record("h-a", uri=f"{CACHE}/compute_hillshade/090a4ff8d9a0AAAAAAAAAAAAAAAAAAAA.tif")
        out = r.resolve_params(
            "publish_layer",
            {"layer_uri": f"{CACHE}/compute_hillshade/090a4ff8d9a0ZZZZZZZZZZZZZZZZZZZZ.tif"},
        )
        assert out["layer_uri"].endswith("090a4ff8d9a0AAAAAAAAAAAAAAAAAAAA.tif")

    def test_equal_12_char_prefix_tie_refuses(self):
        """Two candidates tied at >=12 shared chars => refuse (branch 4)."""
        r = reg()
        r.record("h-a", uri=f"{CACHE}/compute_hillshade/090a4ff8d9a083aaaaaaaaaaaaaaaaaa.tif")
        r.record("h-b", uri=f"{CACHE}/compute_hillshade/090a4ff8d9a083bbbbbbbbbbbbbbbbbb.tif")
        with pytest.raises(UriResolutionError):
            r.resolve_params(
                "publish_layer",
                {"layer_uri": f"{CACHE}/compute_hillshade/090a4ff8d9a083cccccccccccccccccc.tif"},
            )

    def test_hash_match_requires_same_extension(self):
        """14-char shared stem prefix but .fgb vs .tif => no (c) match; and
        with 2+ files in dir, no (d) rescue => error."""
        r = reg()
        r.record("h-a", uri=f"{CACHE}/x/090a4ff8d9a083f67c0b355caf40241a.tif")
        r.record("h-b", uri=f"{CACHE}/x/zzzz4ff8d9a083f67c0b355caf40241a.tif")
        with pytest.raises(UriResolutionError):
            r.resolve_params(
                "t", {"vector_uri": f"{CACHE}/x/090a4ff8d9a083f67c0b355caf999999.fgb"}
            )


# ------------------------------------------------------------------------- #
# B. Handle / minted-handle collisions
# ------------------------------------------------------------------------- #


class TestHandleCollisions:
    def test_same_layer_id_re_registered_with_new_uri_latest_wins(self):
        """Same handle re-registered (refetch) => handle resolves to latest,
        exact resolution of BOTH URIs still passes verbatim."""
        r = reg()
        old = f"{CACHE}/usace_nsi/11111111111111111111111111111111.fgb"
        r.record(NSI_ID, uri=old)
        r.record(NSI_ID, uri=REAL_NSI)
        assert r.resolve_params("t", {"assets_uri": NSI_ID})["assets_uri"] == REAL_NSI
        # the displaced URI is still exactly-known -> passes verbatim
        assert r.resolve_params("t", {"assets_uri": old})["assets_uri"] == old

    def test_minted_bare_uri_handles_collide_on_basename(self):
        """Two runs' COGs registered ONLY as bare strings share the minted
        handle uri:flood_depth_peak.tif — the record keeps only the latest,
        but exact resolution of the displaced COG must still pass."""
        r = reg()
        r.register_tool_result("t1", COG_A)
        r.register_tool_result("t2", COG_B)
        assert r.resolve_params("t", {"hazard_raster_uri": COG_A})["hazard_raster_uri"] == COG_A
        assert r.resolve_params("t", {"hazard_raster_uri": COG_B})["hazard_raster_uri"] == COG_B

    def test_handle_equal_to_foreign_stem_does_not_capture_bare_uri(self):
        """A layer handle that happens to equal another file's stem captures
        that bare URI onto the layer record (stem-attach). Probe whether the
        layer handle then resolves to the WRONG uri."""
        r = reg()
        r.record("flood_depth_peak", uri=REAL_NSI)  # pathological layer_id
        r.register_tool_result("t", COG_A)  # stem == "flood_depth_peak"
        resolved = r.resolve_params("t", {"layer_uri": "flood_depth_peak"})["layer_uri"]
        # stem-attach displaces the registered uri: latest wins. Both URIs
        # must at minimum remain exactly-known (no data loss).
        assert resolved in (REAL_NSI, COG_A)
        assert r.resolve_params("t", {"assets_uri": REAL_NSI})["assets_uri"] == REAL_NSI
        assert r.resolve_params("t", {"hazard_raster_uri": COG_A})["hazard_raster_uri"] == COG_A


# ------------------------------------------------------------------------- #
# C. Cross-session leakage
# ------------------------------------------------------------------------- #


class TestCrossSessionLeakage:
    def test_exact_uri_of_other_session_rejected_in_managed_bucket(self):
        get_uri_registry("sess-A").record(NSI_ID, uri=REAL_NSI)
        with pytest.raises(UriResolutionError):
            get_uri_registry("sess-B").resolve_params("t", {"assets_uri": REAL_NSI})

    def test_handle_of_other_session_not_substituted(self):
        get_uri_registry("sess-A").record(NSI_ID, uri=REAL_NSI)
        out = get_uri_registry("sess-B").resolve_params("t", {"assets_uri": NSI_ID})
        # fail-open passthrough of a non-URI string — NOT session A's URI.
        assert out["assets_uri"] == NSI_ID

    def test_contextvar_isolation_across_concurrent_tasks(self):
        """Two concurrent dispatches in different sessions: observation hook
        must record into each task's own registry, never the sibling's."""

        async def dispatch(sid: str, layer: str, uri: str) -> None:
            r = get_uri_registry(sid)
            tok = activate_registry(r)
            try:
                await asyncio.sleep(0.01)  # force interleaving
                observe_published_layer(layer, gcs_uri=uri)
                await asyncio.sleep(0.01)
            finally:
                deactivate_registry(tok)

        async def main() -> None:
            await asyncio.gather(
                dispatch("sess-1", "layer-1", COG_A),
                dispatch("sess-2", "layer-2", COG_B),
            )

        asyncio.run(main())
        assert get_uri_registry("sess-1").known_handles() == ["layer-1"]
        assert get_uri_registry("sess-2").known_handles() == ["layer-2"]

    def test_other_sessions_real_run_uri_is_silently_rewritten_BREAK(self):
        """BREAK (documented): session 1 registered run A only. Handing it
        run B's REAL, exact URI (cross-session paste / seeding gap) does NOT
        error — branch (b) unique-basename silently substitutes run A's COG.
        Silent wrong-run substitution inside the managed bucket."""
        get_uri_registry("sess-1").record("flood-a", uri=COG_A)
        out = get_uri_registry("sess-1").resolve_params(
            "t", {"hazard_raster_uri": COG_B}
        )
        assert out["hazard_raster_uri"] == COG_A  # documents the hazard

    def test_store_eviction_does_not_resurrect_state(self):
        """Evicted session registry must come back EMPTY, not stale."""
        import grace2_agent.uri_registry as m

        get_uri_registry("victim").record(NSI_ID, uri=REAL_NSI)
        for i in range(m._REGISTRY_STORE_CAP):
            get_uri_registry(f"filler-{i}")
        assert get_uri_registry("victim").known_handles() == []


# ------------------------------------------------------------------------- #
# D. False-positive substitution hazards
# ------------------------------------------------------------------------- #


class TestFalsePositiveSubstitution:
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "CONFIRMED BREAK: _fuzzy_match runs before the _in_managed_bucket "
            "gate, so a foreign-bucket URI whose basename collides with a "
            "registered URI is silently hijacked — violates the report's "
            "'foreign-bucket URIs pass through untouched' claim. Fix: gate "
            "branch 3 on _in_managed_bucket(v)."
        ),
    )
    def test_foreign_bucket_basename_collision_must_not_be_hijacked(self):
        """REPORT CLAIM: 'Foreign-bucket URIs (user-supplied data) pass
        through untouched (fail-open).' A user-supplied COG in the user's OWN
        bucket that happens to share the solver's fixed basename
        flood_depth_peak.tif must NOT be silently replaced by the session's
        run COG."""
        r = reg()
        r.record("flood-depth-peak-01KTS5W9GTE7A7WPC3BNBE10EQ", uri=COG_A)
        user_uri = "gs://my-own-bucket/myproject/flood_depth_peak.tif"
        out = r.resolve_params("run_pelicun_damage_assessment", {"hazard_raster_uri": user_uri})
        assert out["hazard_raster_uri"] == user_uri, (
            "foreign-bucket URI was hijacked to the session COG: "
            f"{out['hazard_raster_uri']!r}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "CONFIRMED BREAK: same root cause — hash-prefix sub-branch (c) "
            "also fires on foreign-bucket URIs before the managed-bucket gate."
        ),
    )
    def test_foreign_bucket_stem_prefix_collision_must_not_be_hijacked(self):
        r = reg()
        r.record("h-a", uri=f"{CACHE}/compute_hillshade/090a4ff8d9a083f67c0b355caf40241a.tif")
        user_uri = "gs://my-own-bucket/dem/090a4ff8d9a083deadbeef00112233.tif"
        out = r.resolve_params("t", {"raster_uri": user_uri})
        assert out["raster_uri"] == user_uri

    def test_descriptive_stem_prefix_false_positive(self):
        """Descriptive (non-hash) stems sharing >=12 chars: an unregistered
        managed-bucket URI for a DIFFERENT product gets silently mapped onto
        the registered one. Document: does the resolver substitute
        admin-boundaries-fl-STATE for admin-boundaries-fl-COUNTY?"""
        r = reg()
        state_uri = f"{CACHE}/boundaries/admin-boundaries-fl-state.fgb"
        county_uri = f"{CACHE}/boundaries/admin-boundaries-fl-county.fgb"
        other = f"{CACHE}/boundaries/zcta-tampa.fgb"  # 2nd file blocks (d)
        r.record("admin-fl-state", uri=state_uri)
        r.record("zcta-tampa", uri=other)
        out = r.resolve_params("t", {"polygon_uri": county_uri})
        # Shared prefix "admin-boundaries-fl-" (20 chars) >= 12 with same ext:
        # sub-branch (c) substitutes STATE where COUNTY was named.
        assert out["polygon_uri"] == state_uri  # documents the false positive

    def test_wrong_extension_same_dir_substitution(self):
        """Sub-branch (d) ignores extension: an invented .tif in a dir whose
        only registered object is a .fgb hands the vector to a raster param."""
        r = reg()
        r.record(NSI_ID, uri=REAL_NSI)
        out = r.resolve_params(
            "t", {"raster_uri": f"{CACHE}/usace_nsi/totally-invented-thing.tif"}
        )
        assert out["raster_uri"] == REAL_NSI  # documents the cross-type hand-off

    def test_invented_ulid_run_with_two_runs_refuses(self):
        """Mangled run path with an INVENTED ULID while two real runs exist:
        segment overlap ties => must refuse, never coin-flip between runs."""
        r = reg()
        r.record("flood-a", uri=COG_A)
        r.record("flood-b", uri=COG_B)
        with pytest.raises(UriResolutionError):
            r.resolve_params(
                "t", {"hazard_raster_uri": f"{RUNS}/01INVENTEDULID00000000000/flood_depth_peak.tif"}
            )

    def test_wms_url_for_unregistered_layer_errors_not_passes(self):
        r = reg()
        r.record(NSI_ID, uri=REAL_NSI)
        with pytest.raises(UriResolutionError):
            r.resolve_params(
                "t",
                {
                    "hazard_raster_uri": (
                        "https://grace-2-qgis-server-x.run.app/ogc/wms"
                        "?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=flood-depth-peak-01NEVERSEEN"
                    )
                },
            )

    def test_uppercase_gs_scheme_not_normalized(self):
        """GS:// (case-mangled scheme) — make sure it cannot bypass managed-
        bucket rejection AND get fuzzy-substituted inconsistently."""
        r = reg()
        r.record(NSI_ID, uri=REAL_NSI)
        out = r.resolve_params("t", {"assets_uri": "GS://grace-2-hazard-prod-cache/x/y.fgb"})
        # Not gs:// per _is_gs => fail-open passthrough (documented behavior).
        assert out["assets_uri"] == "GS://grace-2-hazard-prod-cache/x/y.fgb"


# ------------------------------------------------------------------------- #
# E. Capacity / eviction integrity
# ------------------------------------------------------------------------- #


class TestEviction:
    def test_record_eviction_keeps_uri_index_consistent(self):
        import grace2_agent.uri_registry as m

        r = reg()
        r.record("first", uri=f"{CACHE}/a/00000000000000000000000000000000.fgb")
        for i in range(m._RECORDS_PER_SESSION_CAP + 10):
            r.record(f"h{i}", uri=f"{CACHE}/b/{i:032d}.fgb")
        # "first" evicted: its exact URI must now be REJECTED (managed bucket,
        # unknown) or fuzzily matched — never KeyError / wrong internal state.
        with pytest.raises(UriResolutionError):
            r.resolve_params(
                "t", {"assets_uri": f"{CACHE}/a/00000000000000000000000000000000.fgb"}
            )
        # And a surviving record still resolves by handle.
        out = r.resolve_params("t", {"assets_uri": "h1000"})
        assert out["assets_uri"].endswith(f"{1000:032d}.fgb")

    def test_resolution_with_garbage_values_never_crashes(self):
        r = reg()
        r.record(NSI_ID, uri=REAL_NSI)
        for garbage in ["gs://", "gs:///", "gs://b", "", "   ", "gs://grace-2-hazard-prod-x//",
                        "/vsigs/", "https://", "not a uri at all \x00", "gs://grace-2-hazard-prod-cache"]:
            try:
                r.resolve_params("t", {"layer_uri": garbage})
            except UriResolutionError:
                pass  # typed rejection is acceptable; anything else is a crash
