"""Pure, deterministic OpenQuake classical-PSHA deck templating (sprint-17).

This module owns the OpenQuake *deck authoring* — turning a ``build_spec`` dict
into the three text files the OpenQuake Engine CLI consumes for a classical PSHA:

  - ``job.ini``                     — the calculation config (calculation_mode =
                                       classical, the region + grid spacing, the
                                       IMT + intensity levels, the maximum
                                       distance, the investigation time + PoEs,
                                       and the two logic-tree file pointers).
  - ``source_model.xml``            — a single AREA source covering the AOI with
                                       a Gutenberg-Richter magnitude-frequency
                                       distribution (the demo seismic source).
  - ``source_model_logic_tree.xml`` — a trivial 1-branch source-model logic tree
                                       pointing at ``source_model.xml``.
  - ``gmpe_logic_tree.xml``         — a trivial 1-branch GMPE logic tree naming a
                                       single ground-motion prediction equation.

It is PURE (no I/O, no OpenQuake import, no network) so it unit-tests in
isolation — the "job.ini templating unit test" acceptance item. The worker
entrypoint (``entrypoint.py``) calls ``render_openquake_deck`` to materialize
these files into the scratch dir before invoking ``oq engine --run job.ini``.

The canonical real-world pipeline this mirrors: an OpenQuake hazard input model
is a ``job.ini`` referencing a source-model logic tree (the seismic sources) and
a GMPE logic tree (the ground-motion models), with the calculation laid over a
regular site grid bounded by ``region`` + ``region_grid_spacing``. We replicate
that exact structure with a single area source + single GMPE for the v0.1 demo
(a real published model swaps in a multi-branch logic tree + a national source
model — the deck shape is identical).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OpenQuakeDeck:
    """The four rendered OpenQuake deck files (in-memory strings).

    Fields:
        job_ini: the ``job.ini`` text.
        source_model_xml: the ``source_model.xml`` (NRML area source) text.
        source_model_logic_tree_xml: the source-model logic-tree XML text.
        gmpe_logic_tree_xml: the GMPE logic-tree XML text.
        filenames: the canonical on-disk filename for each (job.ini is the
            entrypoint the worker runs).
    """

    job_ini: str
    source_model_xml: str
    source_model_logic_tree_xml: str
    gmpe_logic_tree_xml: str
    filenames: dict[str, str] = field(
        default_factory=lambda: {
            "job_ini": "job.ini",
            "source_model_xml": "source_model.xml",
            "source_model_logic_tree_xml": "source_model_logic_tree.xml",
            "gmpe_logic_tree_xml": "gmpe_logic_tree.xml",
        }
    )


#: Default intensity-measure levels (IMLs) for a PGA/SA hazard curve, in g.
#: A log-spaced ladder from 0.005 g to ~2 g — the standard demo curve sampling.
_DEFAULT_IMLS_G: tuple[float, ...] = (
    0.005,
    0.007,
    0.0098,
    0.0137,
    0.0192,
    0.0269,
    0.0376,
    0.0527,
    0.0738,
    0.103,
    0.145,
    0.203,
    0.284,
    0.397,
    0.556,
    0.778,
    1.09,
    1.52,
    2.13,
)


def _bbox_floats(bbox: Any) -> tuple[float, float, float, float]:
    """Coerce a bbox (list/tuple of 4 numbers) to floats, validating order."""
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError(
            f"bbox must be (min_lon, min_lat, max_lon, max_lat); got {bbox!r}"
        )
    min_lon, min_lat, max_lon, max_lat = (float(v) for v in bbox)
    if not (min_lon < max_lon and min_lat < max_lat):
        raise ValueError(
            f"bbox must satisfy min<max on both axes; got {bbox!r}"
        )
    return min_lon, min_lat, max_lon, max_lat


def _km_to_deg(km: float) -> float:
    """Approximate a km spacing as decimal degrees (~111.32 km / deg)."""
    return float(km) / 111.32


def _imls_string(imls: tuple[float, ...]) -> str:
    """Render the IML ladder as the space-separated string job.ini expects."""
    return " ".join(repr(round(v, 6)) for v in imls)


def render_source_model_xml(
    bbox: tuple[float, float, float, float],
    *,
    a_value: float,
    b_value: float,
    min_magnitude: float,
    max_magnitude: float,
    source_id: str = "1",
    tectonic_region: str = "Active Shallow Crust",
) -> str:
    """Render a single NRML area source covering the AOI bbox.

    The area-source polygon is the bbox rectangle (the AOI), the seismicity is a
    truncated Gutenberg-Richter MFD (``a_value`` rate + ``b_value`` slope over
    ``min_magnitude``..``max_magnitude``), and a nodal-plane / hypo-depth
    distribution gives a simple vertical strike-slip demo geometry.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    # NRML gml:posList is "lat lon lat lon ..." going around the rectangle.
    pos_list = (
        f"{min_lat} {min_lon} "
        f"{min_lat} {max_lon} "
        f"{max_lat} {max_lon} "
        f"{max_lat} {min_lon}"
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<nrml xmlns:gml="http://www.opengis.net/gml"
      xmlns="http://openquake.org/xmlns/nrml/0.5">
    <sourceModel name="GRACE-2 demo area source">
        <areaSource id="{source_id}"
                    name="AOI area source"
                    tectonicRegion="{tectonic_region}">
            <areaGeometry>
                <gml:Polygon>
                    <gml:exterior>
                        <gml:LinearRing>
                            <gml:posList>
                                {pos_list}
                            </gml:posList>
                        </gml:LinearRing>
                    </gml:exterior>
                </gml:Polygon>
                <upperSeismoDepth>0.0</upperSeismoDepth>
                <lowerSeismoDepth>15.0</lowerSeismoDepth>
            </areaGeometry>
            <magScaleRel>WC1994</magScaleRel>
            <ruptAspectRatio>1.0</ruptAspectRatio>
            <truncGutenbergRichterMFD aValue="{a_value}" bValue="{b_value}"
                                      minMag="{min_magnitude}" maxMag="{max_magnitude}"/>
            <nodalPlaneDist>
                <nodalPlane probability="1.0" strike="0.0" dip="90.0" rake="0.0"/>
            </nodalPlaneDist>
            <hypoDepthDist>
                <hypoDepth probability="1.0" depth="10.0"/>
            </hypoDepthDist>
        </areaSource>
    </sourceModel>
</nrml>
"""


def render_source_model_logic_tree_xml(
    source_model_filename: str = "source_model.xml",
) -> str:
    """Render a trivial 1-branch source-model logic tree pointing at the source
    model (probability 1.0)."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<nrml xmlns:gml="http://www.opengis.net/gml"
      xmlns="http://openquake.org/xmlns/nrml/0.5">
    <logicTree logicTreeID="lt1">
        <logicTreeBranchingLevel branchingLevelID="bl1">
            <logicTreeBranchSet uncertaintyType="sourceModel"
                                branchSetID="bs1">
                <logicTreeBranch branchID="b1">
                    <uncertaintyModel>{source_model_filename}</uncertaintyModel>
                    <uncertaintyWeight>1.0</uncertaintyWeight>
                </logicTreeBranch>
            </logicTreeBranchSet>
        </logicTreeBranchingLevel>
    </logicTree>
</nrml>
"""


def render_gmpe_logic_tree_xml(
    gmpe: str,
    *,
    tectonic_region: str = "Active Shallow Crust",
) -> str:
    """Render a trivial 1-branch GMPE logic tree naming a single ground-motion
    prediction equation (probability 1.0) for the source's tectonic region."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<nrml xmlns:gml="http://www.opengis.net/gml"
      xmlns="http://openquake.org/xmlns/nrml/0.5">
    <logicTree logicTreeID="lt1">
        <logicTreeBranchingLevel branchingLevelID="bl1">
            <logicTreeBranchSet uncertaintyType="gmpeModel"
                                branchSetID="bs1"
                                applyToTectonicRegionType="{tectonic_region}">
                <logicTreeBranch branchID="b1">
                    <uncertaintyModel>{gmpe}</uncertaintyModel>
                    <uncertaintyWeight>1.0</uncertaintyWeight>
                </logicTreeBranch>
            </logicTreeBranchSet>
        </logicTreeBranchingLevel>
    </logicTree>
</nrml>
"""


#: levers STEP 3 -- UHS spectral-acceleration periods. When uniform_hazard_spectra
#: is enabled the IML map must carry an SA(period) ladder (a UHS is the SA value
#: at each period for a fixed PoE), so we inject this ladder alongside the
#: requested IMT. A standard short->long period ladder (Sa at 0.1..2.0 s).
_UHS_SA_PERIODS: tuple[float, ...] = (0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0)


def render_job_ini(
    bbox: tuple[float, float, float, float],
    *,
    imt: str,
    poe: float,
    investigation_time_years: float,
    site_grid_spacing_km: float,
    max_distance_km: float,
    imls: tuple[float, ...] = _DEFAULT_IMLS_G,
    description: str = "GRACE-2 classical PSHA",
    source_model_logic_tree_filename: str = "source_model_logic_tree.xml",
    gmpe_logic_tree_filename: str = "gmpe_logic_tree.xml",
    # --- advanced-physics overrides (levers STEP 3; ADDITIVE, default-match) - #
    truncation_level: float = 3.0,
    rupture_mesh_spacing_km: float = 5.0,
    width_of_mfd_bin: float = 0.2,
    area_source_discretization_km: float = 10.0,
    uniform_hazard_spectra: bool = False,
) -> str:
    """Render the classical-PSHA ``job.ini`` config text.

    ``region`` is the bbox closed-rectangle (lon lat pairs going round); the site
    grid spacing is converted from km to decimal degrees. The intensity_measure_
    types_and_levels maps the requested ``imt`` to the IML ladder; ``poes`` picks
    the hazard-map return period.

    levers STEP 3: ``truncation_level`` / ``rupture_mesh_spacing_km`` /
    ``width_of_mfd_bin`` / ``area_source_discretization_km`` are the
    advanced-physics overrides (defaults reproduce the pre-STEP-3 literals
    byte-for-byte). ``uniform_hazard_spectra`` flips UHS export on (the classical
    run already computes hazard curves, so they export by default with
    ``--exports csv``; UHS additionally needs this flag + an SA(period) IML
    ladder, which is injected when enabled).
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    grid_deg = round(_km_to_deg(site_grid_spacing_km), 6)
    # region = lon lat going round the rectangle (OpenQuake's region order is
    # lon lat, comma-separated vertices).
    region = (
        f"{min_lon} {min_lat}, {max_lon} {min_lat}, "
        f"{max_lon} {max_lat}, {min_lon} {max_lat}"
    )
    iml_str = _imls_string(imls)
    iml_list = iml_str.replace(" ", ", ")
    # The IML map carries the requested IMT; when UHS is on, ALSO carry the SA
    # period ladder (each on the same IML list) so the UHS export has spectra.
    imt_levels = {imt: f"[{iml_list}]"}
    if uniform_hazard_spectra:
        for _p in _UHS_SA_PERIODS:
            imt_levels[f"SA({_p})"] = f"[{iml_list}]"
    imtl_str = "{" + ", ".join(
        f'"{k}": {v}' for k, v in imt_levels.items()
    ) + "}"

    # Preserve the pre-STEP-3 integer literal ``truncation_level = 3`` byte-for-
    # byte when the (default) value is a whole number; a fractional override
    # renders as the float. OpenQuake parses both identically.
    def _num(v: float) -> str:
        f = float(v)
        return str(int(f)) if f.is_integer() else repr(f)

    trunc_str = _num(truncation_level)
    return f"""[general]
description = {description}
calculation_mode = classical
random_seed = 23

[geometry]
region = {region}
region_grid_spacing = {grid_deg}

[logic_tree]
number_of_logic_tree_samples = 0

[erf]
rupture_mesh_spacing = {rupture_mesh_spacing_km}
width_of_mfd_bin = {width_of_mfd_bin}
area_source_discretization = {area_source_discretization_km}

[site_params]
reference_vs30_type = measured
reference_vs30_value = 760.0
reference_depth_to_2pt5km_per_sec = 1.0
reference_depth_to_1pt0km_per_sec = 50.0

[calculation]
source_model_logic_tree_file = {source_model_logic_tree_filename}
gsim_logic_tree_file = {gmpe_logic_tree_filename}
investigation_time = {investigation_time_years}
intensity_measure_types_and_levels = {imtl_str}
truncation_level = {trunc_str}
maximum_distance = {max_distance_km}

[output]
export_dir = output
mean = true
quantiles =
hazard_maps = true
uniform_hazard_spectra = {"true" if uniform_hazard_spectra else "false"}
poes = {poe}
"""


def render_openquake_deck(build_spec: dict[str, Any]) -> OpenQuakeDeck:
    """Render the full OpenQuake classical-PSHA deck from a ``build_spec`` dict.

    The ``build_spec`` is the JSON the agent composer stages to S3 (mirrors the
    SWMM/SFINCS manifest). Required + defaulted keys:

        bbox: (min_lon, min_lat, max_lon, max_lat) EPSG:4326     [required]
        imt: "PGA" / "PGV" / "SA(<period>)"                       [default PGA]
        poe: probability of exceedance, (0,1)                     [default 0.10]
        investigation_time_years: years                          [default 50]
        site_grid_spacing_km: km                                  [default 5]
        max_distance_km: km                                       [default 300]
        gmpe: GMPE class name                       [default BooreAtkinson2008]
        a_value / b_value: Gutenberg-Richter                  [default 4.0/1.0]
        min_magnitude / max_magnitude                          [default 5.0/7.5]

    Returns an :class:`OpenQuakeDeck` carrying the four rendered text files. PURE
    — no I/O. The worker writes ``deck.filenames`` to the scratch dir.

    Raises:
        ValueError: the bbox is missing / malformed (the only hard requirement).
    """
    bbox = _bbox_floats(build_spec.get("bbox"))

    imt = str(build_spec.get("imt", "PGA"))
    poe = float(build_spec.get("poe", 0.10))
    inv_time = float(build_spec.get("investigation_time_years", 50.0))
    grid_km = float(build_spec.get("site_grid_spacing_km", 5.0))
    max_dist = float(build_spec.get("max_distance_km", 300.0))
    gmpe = str(build_spec.get("gmpe", "BooreAtkinson2008"))

    a_value = float(build_spec.get("a_value", 4.0))
    b_value = float(build_spec.get("b_value", 1.0))
    min_mag = float(build_spec.get("min_magnitude", 5.0))
    max_mag = float(build_spec.get("max_magnitude", 7.5))

    source_model_xml = render_source_model_xml(
        bbox,
        a_value=a_value,
        b_value=b_value,
        min_magnitude=min_mag,
        max_magnitude=max_mag,
    )
    smlt_xml = render_source_model_logic_tree_xml("source_model.xml")
    gmpelt_xml = render_gmpe_logic_tree_xml(gmpe)
    # levers STEP 3: advanced-physics overrides + UHS flag (all default-match,
    # so a build_spec without them renders byte-identically). The agent merges
    # the validated PHYSICS_REGISTRY["openquake"] keys into the build_spec.
    job_ini = render_job_ini(
        bbox,
        imt=imt,
        poe=poe,
        investigation_time_years=inv_time,
        site_grid_spacing_km=grid_km,
        max_distance_km=max_dist,
        truncation_level=float(build_spec.get("truncation_level", 3.0)),
        rupture_mesh_spacing_km=float(
            build_spec.get("rupture_mesh_spacing_km", 5.0)
        ),
        width_of_mfd_bin=float(build_spec.get("width_of_mfd_bin", 0.2)),
        area_source_discretization_km=float(
            build_spec.get("area_source_discretization_km", 10.0)
        ),
        uniform_hazard_spectra=bool(
            build_spec.get("uniform_hazard_spectra", False)
        ),
    )
    return OpenQuakeDeck(
        job_ini=job_ini,
        source_model_xml=source_model_xml,
        source_model_logic_tree_xml=smlt_xml,
        gmpe_logic_tree_xml=gmpelt_xml,
    )


def return_period_years(poe: float, investigation_time_years: float) -> float:
    """Return period (years) implied by a PoE over an investigation time.

    RP = -investigation_time / ln(1 - poe). The canonical 10%/50yr -> ~475 yr.
    """
    if not (0.0 < poe < 1.0):
        raise ValueError(f"poe must be in (0,1); got {poe!r}")
    if investigation_time_years <= 0.0:
        raise ValueError(
            f"investigation_time_years must be > 0; got {investigation_time_years!r}"
        )
    return -float(investigation_time_years) / math.log(1.0 - float(poe))
