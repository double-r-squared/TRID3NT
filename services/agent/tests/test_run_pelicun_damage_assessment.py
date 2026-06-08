"""Unit + live tests for ``run_pelicun_damage_assessment`` Wave 1 stub (job-0098).

The Wave 1 file lands the TOOL REGISTRATION + signature + a documented stub
that raises ``PelicunNotImplementedYet`` AFTER input validation. The Wave 2
composer (job-0106) replaces the raise with the real Pelicun runtime.

Coverage:

1. ``test_run_pelicun_damage_assessment_registered`` — tool appears in
   ``TOOL_REGISTRY`` with correct metadata (cacheable=True,
   ttl_class="static-30d", source_class="pelicun_damage").
2. ``test_bad_fragility_set_raises_input_error`` — typo / unknown
   ``fragility_set`` value → ``PelicunInputError`` with
   ``error_code="PELICUN_INPUT_INVALID"`` and ``retryable=False``; the
   ``PelicunNotImplementedYet`` raise does NOT fire.
3. ``test_none_assets_uri_raises_input_error`` — ``assets_uri=None`` →
   ``PelicunInputError`` (deterministic; not retryable).
4. ``test_none_hazard_raster_uri_raises_input_error`` — ``hazard_raster_uri=None``
   → ``PelicunInputError``.
5. ``test_empty_component_types_list_raises_input_error`` — empty list ``[]``
   → ``PelicunInputError`` (caller almost certainly meant ``None``).
6. ``test_nonpositive_realization_count_raises_input_error`` — ``0`` and
   ``-5`` → ``PelicunInputError``.
7. ``test_stub_raises_pelicun_not_implemented_yet`` — valid inputs reach the
   stub and raise ``PelicunNotImplementedYet`` with the expected message.
8. ``test_pelicun_not_implemented_yet_carries_typed_error_surface`` —
   ``error_code="PELICUN_NOT_IMPLEMENTED_YET"`` and ``retryable=False``.
9. ``test_live_stub_invocation_against_placeholder_uris`` (LIVE) — direct
   import-and-call yields the expected ``PelicunNotImplementedYet`` and the
   message names job-0106. This is the live-verification gate required by
   the audit; the stub is fully self-contained so no external service /
   API / GCP credential is needed.
"""

from __future__ import annotations

import pytest

from grace2_agent.tools import TOOL_REGISTRY
from grace2_agent.tools.run_pelicun_damage_assessment import (
    PelicunDamageError,
    PelicunInputError,
    PelicunNotImplementedYet,
    run_pelicun_damage_assessment,
)


# ---------------------------------------------------------------------------
# Constants used across cases — placeholder URIs that pass shape validation
# but never get dereferenced because the stub raises before any I/O.
# ---------------------------------------------------------------------------

_VALID_HAZARD_URI = "gs://grace-2-runs/example-run/flood_depth.tif"
_VALID_ASSETS_URI = "gs://grace-2-cache/places/fort-myers-place-polys.fgb"


# ---------------------------------------------------------------------------
# 1. Tool registration with correct metadata.
# ---------------------------------------------------------------------------


def test_run_pelicun_damage_assessment_registered() -> None:
    """The tool registers under its declared name with the Wave 1 metadata.

    Locks the registration contract so the LLM-visible API (name + metadata)
    is stable across the Wave 1 → Wave 2 hand-off.
    """
    assert "run_pelicun_damage_assessment" in TOOL_REGISTRY
    entry = TOOL_REGISTRY["run_pelicun_damage_assessment"]
    md = entry.metadata
    assert md.name == "run_pelicun_damage_assessment"
    assert md.cacheable is True
    assert md.ttl_class == "static-30d"
    assert md.source_class == "pelicun_damage"
    # The registered callable is the original undecorated function, so direct
    # invocation in the other test cases below exercises the same code path
    # the LLM (via ADK FunctionTool) would.
    assert entry.fn is run_pelicun_damage_assessment


# ---------------------------------------------------------------------------
# 2-6. Input validation — typed errors before the NotImplementedYet raise.
# ---------------------------------------------------------------------------


def test_bad_fragility_set_raises_input_error() -> None:
    """Unknown / typo'd ``fragility_set`` → ``PelicunInputError``.

    Critically, the ``PelicunNotImplementedYet`` raise does NOT fire — input
    validation runs FIRST so the deterministic typed-error surface is
    available even at the stub stage.
    """
    with pytest.raises(PelicunInputError) as excinfo:
        run_pelicun_damage_assessment(
            hazard_raster_uri=_VALID_HAZARD_URI,
            assets_uri=_VALID_ASSETS_URI,
            fragility_set="not_a_real_fragility_set",  # type: ignore[arg-type]
        )
    err = excinfo.value
    assert err.error_code == "PELICUN_INPUT_INVALID"
    assert err.retryable is False
    assert "not_a_real_fragility_set" in str(err)
    # Must enumerate the allowed set so the agent's planning loop can recover.
    assert "hazus_flood_v6" in str(err)
    # And it must NOT be a PelicunNotImplementedYet — that's the post-validation
    # raise reserved for the "actually invoked the stub" path.
    assert not isinstance(err, PelicunNotImplementedYet)


def test_none_assets_uri_raises_input_error() -> None:
    """``assets_uri=None`` → ``PelicunInputError`` (not the stub raise)."""
    with pytest.raises(PelicunInputError) as excinfo:
        run_pelicun_damage_assessment(
            hazard_raster_uri=_VALID_HAZARD_URI,
            assets_uri=None,  # type: ignore[arg-type]
        )
    err = excinfo.value
    assert err.error_code == "PELICUN_INPUT_INVALID"
    assert err.retryable is False
    assert "assets_uri" in str(err)


def test_none_hazard_raster_uri_raises_input_error() -> None:
    """``hazard_raster_uri=None`` → ``PelicunInputError`` (not the stub raise)."""
    with pytest.raises(PelicunInputError) as excinfo:
        run_pelicun_damage_assessment(
            hazard_raster_uri=None,  # type: ignore[arg-type]
            assets_uri=_VALID_ASSETS_URI,
        )
    err = excinfo.value
    assert err.error_code == "PELICUN_INPUT_INVALID"
    assert err.retryable is False
    assert "hazard_raster_uri" in str(err)


def test_empty_component_types_list_raises_input_error() -> None:
    """Empty ``component_types=[]`` → ``PelicunInputError``.

    The caller almost certainly meant ``None`` (include every feature in
    ``assets_uri``); an empty filter would yield zero output features, which
    is rarely what was intended. The error message names the resolution.
    """
    with pytest.raises(PelicunInputError) as excinfo:
        run_pelicun_damage_assessment(
            hazard_raster_uri=_VALID_HAZARD_URI,
            assets_uri=_VALID_ASSETS_URI,
            component_types=[],
        )
    assert excinfo.value.error_code == "PELICUN_INPUT_INVALID"
    assert "None" in str(excinfo.value)  # message points to the fix


@pytest.mark.parametrize("bad_count", [0, -1, -100])
def test_nonpositive_realization_count_raises_input_error(bad_count: int) -> None:
    """``realization_count`` must be a positive int."""
    with pytest.raises(PelicunInputError) as excinfo:
        run_pelicun_damage_assessment(
            hazard_raster_uri=_VALID_HAZARD_URI,
            assets_uri=_VALID_ASSETS_URI,
            realization_count=bad_count,
        )
    err = excinfo.value
    assert err.error_code == "PELICUN_INPUT_INVALID"
    assert "realization_count" in str(err)


# ---------------------------------------------------------------------------
# 7-8. Stub raise — the v0.1 acceptance criterion.
# ---------------------------------------------------------------------------


def test_stub_raises_pelicun_not_implemented_yet() -> None:
    """Valid inputs reach the stub body and raise ``PelicunNotImplementedYet``.

    The message MUST name the Wave 2 composer job (job-0106) so the agent
    surface can offer actionable next-steps to the user.
    """
    with pytest.raises(PelicunNotImplementedYet) as excinfo:
        run_pelicun_damage_assessment(
            hazard_raster_uri=_VALID_HAZARD_URI,
            assets_uri=_VALID_ASSETS_URI,
            fragility_set="hazus_flood_v6",
            component_types=["RES1", "COM1"],
            realization_count=100,
        )
    msg = str(excinfo.value)
    assert "Implementation deferred to job-0106 composer" in msg
    assert "locks the LLM-visible API contract" in msg


def test_pelicun_not_implemented_yet_carries_typed_error_surface() -> None:
    """The stub error declares the FR-AS-11 / NFR-R-1 typed-error fields.

    ``error_code`` surfaces in the WebSocket A.6 error frame; ``retryable``
    guides the agent's retry policy. The stub is deterministically un-runnable
    so retries MUST be suppressed.
    """
    with pytest.raises(PelicunNotImplementedYet) as excinfo:
        run_pelicun_damage_assessment(
            hazard_raster_uri=_VALID_HAZARD_URI,
            assets_uri=_VALID_ASSETS_URI,
        )
    err = excinfo.value
    assert err.error_code == "PELICUN_NOT_IMPLEMENTED_YET"
    assert err.retryable is False
    # Class hierarchy: ``PelicunNotImplementedYet`` is a ``PelicunDamageError``,
    # which is a ``RuntimeError``. The agent's exception-handler chain catches
    # at any level above.
    assert isinstance(err, PelicunDamageError)
    assert isinstance(err, RuntimeError)


# ---------------------------------------------------------------------------
# 9. Live test — exercises the registered tool path end-to-end.
#
# The Wave 1 stub is fully self-contained (no external API call, no GCS
# read, no Pelicun runtime to import), so the "live" verification gate
# is simply: invoke the registered callable, observe the raise, observe
# the typed-error surface.
#
# The acceptance criteria call for an env-var guard on live tests because
# many other engine tools touch real APIs / credentials. This stub has
# nothing to gate, so the guard is informational only and the test always
# runs — that's the right call per the audit's "live verification with
# real upstream response" language (no upstream exists yet, by design).
# ---------------------------------------------------------------------------


def test_live_stub_invocation_against_placeholder_uris() -> None:
    """LIVE: registered tool invoked against placeholder URIs → expected raise.

    This is the closest thing to a live invocation the v0.1 stub admits.
    The Wave 2 composer (job-0106) will replace this test with a real
    Pelicun runtime check pointing at a known flood COG + assets FGB.
    """
    # Look up via the registry — same path the LLM-facing FunctionTool would
    # take. We exercise the registered callable, NOT the import binding.
    entry = TOOL_REGISTRY["run_pelicun_damage_assessment"]
    fn = entry.fn

    with pytest.raises(PelicunNotImplementedYet) as excinfo:
        fn(
            hazard_raster_uri="gs://grace-2-runs/live-test/flood_depth.tif",
            assets_uri="gs://grace-2-cache/live-test/place_polys.fgb",
            fragility_set="hazus_flood_v6",
            realization_count=100,
        )

    err = excinfo.value
    assert err.error_code == "PELICUN_NOT_IMPLEMENTED_YET"
    assert err.retryable is False
    assert "job-0106" in str(err)
