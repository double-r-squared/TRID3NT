"""Fable-5 panel ROUND-2 adversarial break probes — job-0263 URI resolver.

Independent of (and complementary to) fable5_adversarial_break_tests.py.
Derived from scratch from uri_registry.py @ b351c2e. Attack surfaces:

  A. near-miss hashes that must NOT match (threshold, ambiguity, extension)
  B. handle collisions + handle/URI precedence hijack
  C. cross-session + cross-task (ContextVar) leakage
  D. false-positive substitution via sub-branch (d) same-directory
  E. eviction map hygiene

Run from services/agent:
    .venv/bin/python -m pytest \
        ../../reports/inflight/job-0263-agent-20260610/verify/fable5_panel_break_resolver_round2.py -v
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

B = "gs://grace-2-hazard-prod-cache"  # managed bucket


@pytest.fixture(autouse=True)
def _clean():
    reset_uri_registries_for_tests()
    yield
    reset_uri_registries_for_tests()


def _reg(sid="S1") -> SessionUriRegistry:
    return get_uri_registry(sid)


# ------------------------------------------------------------------ #
# A. Near-miss hashes that should NOT match
# ------------------------------------------------------------------ #


def test_11_char_prefix_must_reject_not_match() -> None:
    """Shared prefix one char BELOW the 12-char floor + different dir +
    no same-dir candidate -> must raise, never substitute."""
    r = _reg()
    r.record("lyr-a", uri=f"{B}/d1/0123456789abcdef0123456789abcdef.tif")
    # 11 shared chars then diverges; different parent dir kills branch (d).
    bad = f"{B}/d2/0123456789aZZZZZZZZZZZZZZZZZZZZZ.tif"
    with pytest.raises(UriResolutionError):
        r.resolve_params("run_pelicun_damage_assessment", {"hazard_raster_uri": bad})


def test_equal_prefix_two_candidates_must_refuse_to_guess() -> None:
    """Two registered hashes share the same 16-char prefix with the query —
    ambiguous: resolver must NOT pick one."""
    r = _reg()
    p = "0123456789abcdef"
    r.record("lyr-a", uri=f"{B}/da/{p}aaaaaaaaaaaaaaaa.tif")
    r.record("lyr-b", uri=f"{B}/db/{p}bbbbbbbbbbbbbbbb.tif")
    bad = f"{B}/dc/{p}cccccccccccccccc.tif"  # 16 chars shared with BOTH
    with pytest.raises(UriResolutionError):
        r.resolve_params("t", {"hazard_raster_uri": bad})


def test_prefix_match_wrong_extension_rejected() -> None:
    """14-char shared prefix but .fgb vs .tif -> sub-branch (c) requires the
    same extension; different dir kills (d) -> reject."""
    r = _reg()
    r.record("lyr-a", uri=f"{B}/d1/0123456789abcdef0123456789abcdef.tif")
    bad = f"{B}/d2/0123456789abcdXXXXXXXXXXXXXXXXXX.fgb"
    with pytest.raises(UriResolutionError):
        r.resolve_params("t", {"assets_uri": bad})


def test_13_vs_12_prefix_picks_longer_documented() -> None:
    """One-char prefix difference IS taken as the winner (spec'd 'longest
    prefix wins'). Document the substitution — borderline by design."""
    r = _reg()
    r.record("lyr-a", uri=f"{B}/da/0123456789abcXAAAAAAAAAAAAAAAAAA.tif")  # 13 shared
    r.record("lyr-b", uri=f"{B}/db/0123456789abYYAAAAAAAAAAAAAAAAAA.tif")  # 12 shared
    q = f"{B}/dc/0123456789abcXZZZZZZZZZZZZZZZZZZ.tif"
    out = r.resolve_params("t", {"hazard_raster_uri": q})
    assert out["hazard_raster_uri"] == f"{B}/da/0123456789abcXAAAAAAAAAAAAAAAAAA.tif"


# ------------------------------------------------------------------ #
# B. Handle collisions + precedence hijack
# ------------------------------------------------------------------ #


def test_handle_string_that_is_another_records_uri_hijacks_precedence() -> None:
    """Branch 2a (handle lookup) runs BEFORE branch 1 (exact URI). If a
    handle is ever literally equal to another record's gs:// URI, the
    handle wins and the exact URI is silently rewritten. PROBE: document
    whether this is reachable + what happens."""
    r = _reg()
    real = f"{B}/d1/aaaa.tif"
    r.record("layer-one", uri=real)
    # Malicious/buggy tool result emits a layer_id that IS the other URI.
    r.record(real, uri=f"{B}/evil/bbbb.tif")
    out = r.resolve_params("t", {"hazard_raster_uri": real})
    # OBSERVED: the exact, correctly-registered URI is replaced by the
    # colliding handle's URI. Tools control layer_id (not the LLM), so
    # reachability is low — but the precedence inversion is real.
    assert out["hazard_raster_uri"] == f"{B}/evil/bbbb.tif"


def test_same_handle_reregistered_old_uri_still_passes_through() -> None:
    """Handle re-registration keeps the OLD URI resolvable verbatim (it
    really existed); the handle resolves to the NEW URI."""
    r = _reg()
    old = f"{B}/d1/old00000000000000000000000000000.tif"
    new = f"{B}/d1/new00000000000000000000000000000.tif"
    r.record("lyr", uri=old)
    r.record("lyr", uri=new)
    assert r.resolve_params("t", {"hazard_raster_uri": "lyr"})["hazard_raster_uri"] == new
    assert r.resolve_params("t", {"hazard_raster_uri": old})["hazard_raster_uri"] == old


def test_wms_layers_param_on_foreign_host_still_resolves_to_session_layer() -> None:
    """A WMS-shaped URL on ANY host with LAYERS=<known handle> resolves to
    the session's data URI. Within-session only — acceptable, document."""
    r = _reg()
    r.record("flood-x", uri=f"{B}/d1/flood.tif")
    out = r.resolve_params(
        "t", {"hazard_raster_uri": "https://evil.example/wms?LAYERS=flood-x"}
    )
    assert out["hazard_raster_uri"] == f"{B}/d1/flood.tif"


# ------------------------------------------------------------------ #
# C. Cross-session + cross-task leakage
# ------------------------------------------------------------------ #


def test_cross_session_no_resolution_leak() -> None:
    """Session B must NOT resolve (or fuzzy-match) session A's URIs."""
    ra = get_uri_registry("session-A")
    rb = get_uri_registry("session-B")
    u = f"{B}/d1/0123456789abcdef0123456789abcdef.tif"
    ra.record("flood-a", uri=u)
    # exact URI of A's layer, asked in B -> managed bucket, unknown -> reject
    with pytest.raises(UriResolutionError):
        rb.resolve_params("t", {"hazard_raster_uri": u})
    # A's handle, asked in B -> not gs, not wms -> fail-open passthrough
    out = rb.resolve_params("t", {"hazard_raster_uri": "flood-a"})
    assert out["hazard_raster_uri"] == "flood-a"  # NOT A's URI
    # near-miss of A's hash in B -> reject (no inventory to match)
    near = f"{B}/d1/0123456789abcdefZZZZZZZZZZZZZZZZ.tif"
    with pytest.raises(UriResolutionError):
        rb.resolve_params("t", {"hazard_raster_uri": near})


def test_contextvar_observation_is_task_isolated() -> None:
    """Two concurrent dispatch tasks activate different session registries;
    observe_published_layer must land in each task's own registry."""

    async def dispatch(sid: str, layer: str, uri: str) -> None:
        reg = get_uri_registry(sid)
        tok = activate_registry(reg)
        try:
            await asyncio.sleep(0.005)
            observe_published_layer(layer, gcs_uri=uri)
            await asyncio.sleep(0.005)
        finally:
            deactivate_registry(tok)

    async def main() -> None:
        await asyncio.gather(
            dispatch("sess-1", "lyr-1", f"{B}/a/one.tif"),
            dispatch("sess-2", "lyr-2", f"{B}/b/two.tif"),
        )

    asyncio.run(main())
    r1, r2 = get_uri_registry("sess-1"), get_uri_registry("sess-2")
    assert r1.known_handles() == ["lyr-1"]
    assert r2.known_handles() == ["lyr-2"]
    with pytest.raises(UriResolutionError):
        r1.resolve_params("t", {"layer_uri": f"{B}/b/two.tif"})


def test_observe_outside_dispatch_is_noop() -> None:
    observe_published_layer("ghost", gcs_uri=f"{B}/g/ghost.tif")
    assert _reg().known_handles() == []


# ------------------------------------------------------------------ #
# D. False-positive substitution via same-directory uniqueness
# ------------------------------------------------------------------ #


def test_same_dir_unique_candidate_substitutes_totally_different_hash() -> None:
    """Sub-branch (d): ANY invented basename in a dir holding exactly one
    registered URI silently substitutes it — even with ZERO prefix overlap.
    By design (invented-basename mangle) but a real wrong-layer risk when
    the directory legitimately holds unregistered objects. Document."""
    r = _reg()
    real = f"{B}/usace_nsi/852a6cc379b18c865bf9d99ec1acaa35.fgb"
    r.record("usace-nsi-tampa", uri=real)
    invented = f"{B}/usace_nsi/20240516140505.fgb"  # live incident 5 shape
    out = r.resolve_params("t", {"assets_uri": invented})
    assert out["assets_uri"] == real
    # ...and with TWO registered in the dir it must refuse:
    r.record("usace-nsi-miami", uri=f"{B}/usace_nsi/ffff6cc379b18c865bf9d99ec1acaa35.fgb")
    with pytest.raises(UriResolutionError):
        r.resolve_params("t", {"assets_uri": f"{B}/usace_nsi/19990101000000.fgb"})


def test_foreign_bucket_unknown_passes_through_unmodified() -> None:
    r = _reg()
    r.record("lyr", uri=f"{B}/d/aaa.tif")
    u = "gs://user-bucket/their/data.tif"
    assert r.resolve_params("t", {"dem_uri": u})["dem_uri"] == u


def test_non_allowlisted_param_never_touched() -> None:
    r = _reg()
    bad = f"{B}/d/invented00000000000000000000000.tif"
    out = r.resolve_params("t", {"output_uri": bad, "project_qgs_uri": bad})
    assert out == {"output_uri": bad, "project_qgs_uri": bad}


# ------------------------------------------------------------------ #
# E. Eviction hygiene
# ------------------------------------------------------------------ #


def test_eviction_does_not_strand_or_misroute_uri_index() -> None:
    from grace2_agent import uri_registry as m

    r = _reg()
    cap = m._RECORDS_PER_SESSION_CAP
    first = f"{B}/d/h{0:031d}.tif"
    for i in range(cap + 10):
        r.record(f"h{i}", uri=f"{B}/d/h{i:031d}.tif")
    assert len(r._records) == cap
    # Evicted handle's URI must be gone from the reverse index...
    assert first not in r._uri_to_handle
    # ...and the newest still resolves exactly.
    last = f"{B}/d/h{cap + 9:031d}.tif"
    assert r.resolve_params("t", {"layer_uri": last})["layer_uri"] == last
