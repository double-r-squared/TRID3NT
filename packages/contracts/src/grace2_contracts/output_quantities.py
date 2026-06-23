"""Declarative per-engine OUTPUT-QUANTITY spec + the FieldResult union.

STEP 2 of the engine-coverage-levers refactor (additive type system; DEFAULT-OFF
so every deck remains byte-identical). The audit's highest-leverage finding is the
"generic output-quantity publisher": every engine computes far more than it
publishes, and the postprocess COG/timeseries/scalar plumbing is near-identical.
This module is the type substrate for that publisher - a declarative
``OutputQuantitySpec`` per engine (quantity -> reader -> COG / timeseries / scalar
emitter) so adding a published field becomes a one-line registration, not a
bespoke postprocess.

PLACEMENT DECISION (read this - it is deliberate).

The kickoff requires the SPEC to be importable by BOTH the agent AND (eventually)
the Batch worker, mirroring the ``manifest.py`` (worker plain dict) /
``publish_manifest.py`` (agent pydantic mirror) two-definitions-one-schema_version
precedent. The hard constraint is the DEPLOY BOUNDARY:

  - the AGENT image ships ONLY ``grace2_agent`` + ``grace2_contracts`` (its
    pyproject deps); it does NOT ship ``services/workers`` (confirmed in
    ``grace2_agent/qgis_proxy.py``: "this lives in the agent package - not
    services/workers/ - because the agent must import it at runtime and
    services/workers/ is not on the agent's import path").
  - the WORKER images ship ``services/workers/**`` (their CodeBuild context) and
    do NOT ship ``packages/contracts``.

So NO single existing location is on BOTH import paths. The manifest precedent
resolves this with TWO definitions gated on ONE ``schema_version``. We follow it:

  * THIS module (agent-side) holds the SPEC as PLAIN FROZEN DATACLASSES + an
    ``OUTPUT_REGISTRY_SCHEMA_VERSION``. It lives in ``grace2_contracts`` because
    that is the package the AGENT already imports, and it uses ONLY the stdlib +
    typing (NO pydantic, NO rasterio, NO engine deps) so it is trivially
    MIRRORABLE into a worker plain module verbatim.
  * STEP 4 (deferred, gated) adds the worker MIRROR under
    ``services/workers/_raster_postprocess/output_quantities.py`` gated on the
    SAME ``OUTPUT_REGISTRY_SCHEMA_VERSION`` - the moment the worker executor is
    wired. Until then the worker does not need it (the executor is STEP 4).

Why plain dataclasses, not ``GraceModel``: the spec must be copy-pasteable into a
worker module that cannot import pydantic-heavy ``grace2_contracts``. A frozen
dataclass is the lowest-common-denominator both sides can host identically. The
``reader`` is an OPTIONAL ``Callable`` bound on the consuming side (the agent
executor binds rasterio/engine readers; a worker mirror would bind worker
readers) - the DECLARATIVE half (id, kind, style_preset, units, role, label) is
what travels, the reader is bound where the heavy deps live.

DEFAULT-OFF: the per-engine ``OUTPUT_QUANTITIES`` registry ships as an EMPTY
scaffold (no engine migrated yet - that is STEP 3). ``get_output_registry`` of any
engine returns ``()`` today, so nothing changes until an engine opts in. The
executor (``grace2_agent.workflows.publish_quantities``) is importable + typed +
unit-tested against a FAKE registry now; the per-engine fan-out is STEP 3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

__all__ = [
    "OUTPUT_REGISTRY_SCHEMA_VERSION",
    "FieldKind",
    "RasterField",
    "TimeseriesField",
    "ScalarField",
    "FieldResult",
    "OutputQuantitySpec",
    "OUTPUT_QUANTITIES",
    "get_output_registry",
]

#: Bumped whenever the OutputQuantitySpec SHAPE changes incompatibly. A future
#: worker MIRROR module gates on this exact value (the manifest precedent).
OUTPUT_REGISTRY_SCHEMA_VERSION: int = 1


#: The kind of published artifact a quantity produces. Drives the executor's
#: routing: ``raster`` -> cog_io COG, ``timeseries`` -> frames.emit_timeseries,
#: ``scalar`` -> metrics dict.
FieldKind = Literal["raster", "timeseries", "scalar"]


# --------------------------------------------------------------------------- #
# FieldResult union - what a spec.reader returns (the executor routes on type).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RasterField:
    """A single 2D field to publish as ONE COG layer.

    ``grid`` is a 2D array (the reader's responsibility to orient + mask);
    ``src_crs`` / ``src_transform`` georegister it; ``reproject`` selects the
    cog_io path (already-4326 direct-write vs projected->4326 warp). ``mask`` is
    the optional per-cell mask (declared per quantity, e.g. mask-below-floor).
    ``metrics`` carries the narration scalars the layer row needs.
    """

    grid: Any
    src_crs: str
    src_transform: Any
    reproject: bool = False
    mask: Callable[[Any], Any] | None = None
    crs_roundtrip_guard: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TimeseriesField:
    """A time-varying field to publish as a PEAK COG + N animation-frame COGs.

    ``n_steps`` is the raw step count (the executor subsamples to
    ``frames.MAX_FLOOD_FRAMES``); ``read_step(raw_index) -> RasterField`` reads
    one frame's grid on demand (so the reader never materializes all frames at
    once); ``peak`` is the representative PEAK ``RasterField`` (always published
    as ``layers[0]``). ``quantity_label`` is the web token base (e.g.
    ``"Flood depth"`` -> "Peak flood depth" / "Flood depth step N").
    """

    n_steps: int
    read_step: Callable[[int], RasterField]
    peak: RasterField
    quantity_label: str = "Flood depth"


@dataclass(frozen=True)
class ScalarField:
    """A scalar (or small dict of scalars) routed to the run metrics, no layer.

    ``values`` is merged into the executor's metrics dict. Used for quantities a
    run computes but does not rasterize (e.g. a basin-total, a convergence stat).
    """

    values: dict[str, Any]


#: What a ``OutputQuantitySpec.reader`` returns.
FieldResult = RasterField | TimeseriesField | ScalarField


# --------------------------------------------------------------------------- #
# OutputQuantitySpec - one declarative published quantity per engine.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class OutputQuantitySpec:
    """One published output quantity for an engine (the declarative half).

    Fields:
        quantity_id: stable id (the layer-id STEM + the registry key), e.g.
            ``"flood-depth"`` / ``"flood-velocity"`` / ``"plume-concentration"``.
        kind: ``raster`` | ``timeseries`` | ``scalar`` (routes the executor).
        name: the human + web-grouping LayerURI name (e.g. "Peak flood depth").
            For a ``timeseries`` the executor derives the peak/frame names from
            the ``TimeseriesField.quantity_label`` instead; ``name`` is the peak.
        style_preset: the publish_layer / TiTiler style-preset KEY.
        units: the layer units string (e.g. "meters", "mg/L", "g").
        role: the LayerURI role (peak = "primary", frames = "context").
        reader: OPTIONAL callable ``(ctx) -> FieldResult`` bound on the consuming
            side (the agent executor binds rasterio/engine readers). The
            DECLARATIVE fields above travel between agent + worker mirror; the
            reader is bound where the heavy deps live. ``None`` in a pure scaffold
            entry (the executor skips a spec with no reader, honestly logging it).
        default_on: when False (the additive default), the quantity is OFF until
            an engine opts it in (DEFAULT-OFF guarantee - decks stay byte-
            identical). The executor skips an ``default_on=False`` spec unless the
            run args explicitly enable it.
        doc: one-line human description (catalog / narration).
    """

    quantity_id: str
    kind: FieldKind
    name: str
    style_preset: str
    units: str = ""
    role: str = "primary"
    reader: Callable[..., FieldResult] | None = None
    default_on: bool = False
    doc: str = ""


# --------------------------------------------------------------------------- #
# Per-engine registry scaffold (EMPTY today - per-engine migration is STEP 3).
# --------------------------------------------------------------------------- #
#: engine -> ordered tuple of OutputQuantitySpec. EMPTY per engine in STEP 2
#: (DEFAULT-OFF: nothing published through the executor until STEP 3 migrates an
#: engine onto it). The engine keys match the postprocess/run module engine
#: tokens so STEP 3 can fill these in place without a key rename.
OUTPUT_QUANTITIES: dict[str, tuple[OutputQuantitySpec, ...]] = {
    "sfincs": (),
    "swmm": (),
    "modflow": (),
    "geoclaw": (),
    "landlab": (),
    "openquake": (),
    "swan": (),
}


def get_output_registry(engine: str) -> tuple[OutputQuantitySpec, ...]:
    """Return the ordered ``OutputQuantitySpec`` tuple for ``engine`` (or ``()``).

    The resolver the STEP-2 executor walks. Unknown engine -> empty tuple (the
    executor publishes nothing, exactly the DEFAULT-OFF behavior). The lookup is
    case-insensitive on the engine token.
    """
    return OUTPUT_QUANTITIES.get(engine.strip().lower(), ())
