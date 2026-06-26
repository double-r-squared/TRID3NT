"""Unit tests for the OpenQuake PSHA deck templating + worker-entrypoint helpers
(sprint-17).

These tests assert the *deck construction* contract — no LLM call, no ``oq``
binary, no S3 / boto3 required (engine invariant 2: workflows/adapters are
unit-testable without the solver in the loop). The end-to-end ``oq engine``
solve is the worker container's job.

Run:
    services/agent/.venv/bin/python -m pytest \
        services/workers/openquake/test_job_ini.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow `import job_ini` whether tests run from repo root or the dir.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from job_ini import (  # noqa: E402
    OpenQuakeDeck,
    render_gmpe_logic_tree_xml,
    render_job_ini,
    render_openquake_deck,
    render_source_model_logic_tree_xml,
    render_source_model_xml,
    return_period_years,
)


_BBOX = (-122.5, 37.5, -121.5, 38.5)


# ===========================================================================
# job.ini templating
# ===========================================================================
def test_render_job_ini_classical_psha_structure():
    text = render_job_ini(
        _BBOX,
        imt="PGA",
        poe=0.10,
        investigation_time_years=50.0,
        site_grid_spacing_km=5.0,
        max_distance_km=300.0,
    )
    # The classical-PSHA config markers must be present.
    assert "calculation_mode = classical" in text
    assert "[geometry]" in text
    assert "region =" in text
    assert "region_grid_spacing =" in text
    # The IMT maps onto an IML ladder.
    assert '"PGA"' in text
    assert "intensity_measure_types_and_levels" in text
    # The PoE picks the hazard-map return period.
    assert "poes = 0.1" in text
    assert "hazard_maps = true" in text
    # The logic-tree file pointers must be present.
    assert "source_model_logic_tree_file = source_model_logic_tree.xml" in text
    assert "gsim_logic_tree_file = gmpe_logic_tree.xml" in text
    assert "investigation_time = 50.0" in text
    assert "maximum_distance = 300.0" in text
    # NATE 2026-06-26: region_grid_spacing is in KM (OpenQuake's unit), passed
    # through directly - engine-verified by a real oq run. The old km->deg
    # conversion made a ~100x-too-fine grid (OQ read the deg value AS km).
    assert "region_grid_spacing = 5" in text


def test_render_job_ini_grid_spacing_is_km():
    # NATE 2026-06-26: OpenQuake region_grid_spacing is in KM and is passed
    # through directly (engine-verified). 11.132 km renders as "11.132", NOT a
    # km->deg conversion (the old bug rendered 0.1 deg, which OQ read as 0.1 km).
    text = render_job_ini(
        _BBOX,
        imt="SA(1.0)",
        poe=0.02,
        investigation_time_years=50.0,
        site_grid_spacing_km=11.132,
        max_distance_km=200.0,
    )
    assert "region_grid_spacing = 11.132" in text
    assert '"SA(1.0)"' in text
    assert "poes = 0.02" in text


# ===========================================================================
# source-model + logic-tree XML
# ===========================================================================
def test_render_source_model_xml_area_source():
    xml = render_source_model_xml(
        _BBOX,
        a_value=4.0,
        b_value=1.0,
        min_magnitude=5.0,
        max_magnitude=7.5,
    )
    assert "<areaSource" in xml
    assert 'aValue="4.0"' in xml
    assert 'bValue="1.0"' in xml
    assert 'minMag="5.0"' in xml
    assert 'maxMag="7.5"' in xml
    assert "truncGutenbergRichterMFD" in xml
    # NATE 2026-06-26 (engine-verified by a real oq run): NRML 0.4 namespace (OQ
    # rejects this 0.4-style area-source body under an 0.5 declaration) + the
    # gml:posList in LON LAT order (OQ's area-source parser reads lon lat; the old
    # lat lon order made it read a longitude as a latitude -> "latitude < -90").
    assert 'xmlns="http://openquake.org/xmlns/nrml/0.4"' in xml
    assert "-122.5 37.5" in xml


def test_render_logic_trees():
    smlt = render_source_model_logic_tree_xml("source_model.xml")
    assert 'uncertaintyType="sourceModel"' in smlt
    assert "<uncertaintyModel>source_model.xml</uncertaintyModel>" in smlt
    assert "<uncertaintyWeight>1.0</uncertaintyWeight>" in smlt

    gmpelt = render_gmpe_logic_tree_xml("BooreAtkinson2008")
    assert 'uncertaintyType="gmpeModel"' in gmpelt
    assert "<uncertaintyModel>BooreAtkinson2008</uncertaintyModel>" in gmpelt
    assert 'applyToTectonicRegionType="Active Shallow Crust"' in gmpelt


# ===========================================================================
# Full deck render from a build_spec
# ===========================================================================
def test_render_openquake_deck_full():
    build_spec = {
        "bbox": list(_BBOX),
        "imt": "PGA",
        "poe": 0.10,
        "investigation_time_years": 50.0,
        "site_grid_spacing_km": 5.0,
        "max_distance_km": 300.0,
        "gmpe": "ChiouYoungs2014",
        "a_value": 3.5,
        "b_value": 0.9,
        "min_magnitude": 4.5,
        "max_magnitude": 8.0,
    }
    deck = render_openquake_deck(build_spec)
    assert isinstance(deck, OpenQuakeDeck)
    # The four files are populated.
    assert "calculation_mode = classical" in deck.job_ini
    assert 'aValue="3.5"' in deck.source_model_xml
    assert 'maxMag="8.0"' in deck.source_model_xml
    assert "ChiouYoungs2014" in deck.gmpe_logic_tree_xml
    assert "source_model.xml" in deck.source_model_logic_tree_xml
    # Canonical filenames.
    assert deck.filenames["job_ini"] == "job.ini"
    assert deck.filenames["source_model_xml"] == "source_model.xml"


def test_render_openquake_deck_rejects_bad_bbox():
    with pytest.raises(ValueError):
        render_openquake_deck({"bbox": [1, 2, 3]})  # not 4 elements
    with pytest.raises(ValueError):
        render_openquake_deck({"bbox": [10, 0, 5, 1]})  # min_lon > max_lon


# ===========================================================================
# return-period helper
# ===========================================================================
def test_return_period_years_canonical():
    # 10% in 50 years -> ~475-year return period.
    rp = return_period_years(0.10, 50.0)
    assert rp == pytest.approx(474.6, abs=1.0)
    # 2% in 50 years -> ~2475-year return period.
    rp2 = return_period_years(0.02, 50.0)
    assert rp2 == pytest.approx(2475.0, abs=5.0)


def test_return_period_years_rejects_bad_poe():
    with pytest.raises(ValueError):
        return_period_years(0.0, 50.0)
    with pytest.raises(ValueError):
        return_period_years(1.0, 50.0)
    with pytest.raises(ValueError):
        return_period_years(0.1, 0.0)


# ===========================================================================
# worker-entrypoint helper: resolve_hazard_map_csv
# ===========================================================================
def test_resolve_hazard_map_csv_picks_map_over_curves():
    from entrypoint import resolve_hazard_map_csv  # noqa: E402

    uris = [
        "s3://runs/01ABC/output/hazard_curve-mean-PGA_12345.csv",
        "s3://runs/01ABC/output/hazard_map-mean-PGA_12345.csv",
        "s3://runs/01ABC/oq.stdout",
    ]
    assert (
        resolve_hazard_map_csv(uris)
        == "s3://runs/01ABC/output/hazard_map-mean-PGA_12345.csv"
    )


def test_resolve_hazard_map_csv_falls_back_to_any_hazard_csv():
    from entrypoint import resolve_hazard_map_csv  # noqa: E402

    uris = ["s3://runs/x/output/hazard_curve-mean-PGA.csv"]
    assert resolve_hazard_map_csv(uris) == "s3://runs/x/output/hazard_curve-mean-PGA.csv"


def test_resolve_hazard_map_csv_none_when_no_csv():
    from entrypoint import resolve_hazard_map_csv  # noqa: E402

    assert resolve_hazard_map_csv(["s3://runs/x/oq.stdout"]) is None
