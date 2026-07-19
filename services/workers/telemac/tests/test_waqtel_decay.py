"""Offline unit tests: WAQTEL v1a decay deck authoring (no solve, no network).

WAQTEL first-order DECAY (WATER QUALITY PROCESS = 17) is the third TELEMAC
substance class beside the plain dye tracer and oil. It rides the UNCHANGED dye
tracer: author_deck only appends three keywords to the t2d cas (COUPLING WITH =
'WAQTEL', WAQTEL STEERING FILE, WATER QUALITY PROCESS = 17) and writes a tiny
t2d_river.waqtel steering file carrying the degradation law + coefficient. There
is ZERO new tracer, ZERO SOURCES change (the pulse column stays the dye conc),
ZERO postprocess/contract change.

These tests drive author_deck over a tiny synthetic mesh (no gmsh, no solve) and
assert the deck-authoring + steering-file emission for a decay ReachConfig, and
that a tracer OR oil ReachConfig emits NEITHER the WAQTEL block NOR the steering
file (the classes are mutually exclusive). The real decay SINK actually lowering
the downstream peak needs the image rebuild + a live solve - out of scope here.

Run: python3 -m pytest services/workers/telemac/tests/ -q
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import telemac_river_dye_build as B
from telemac_river_dye_build import ReachConfig, WAQTEL_FILENAME


def _tiny_mesh():
    """A 6x3 node grid ribbon along y=0: the 4 mid-row nodes are INTERIOR
    (ipob==0, off the boundary ring), everything on the perimeter is boundary.
    Enough for spill_point (centerline walk + nearest-interior snap) and the oil
    clearance-snap (interior mask + boundary KD-tree) without a real solve."""
    xs = [0.0, 20.0, 40.0, 60.0, 80.0, 100.0]
    ys = [-15.0, 0.0, 15.0]
    X, Y, ring, ipob = [], [], [], []
    for i, x in enumerate(xs):
        for j, y in enumerate(ys):
            X.append(x)
            Y.append(y)
            is_boundary = (i in (0, len(xs) - 1)) or (j in (0, len(ys) - 1))
            ring.append(len(X) - 1) if is_boundary else None
            ipob.append(1 if is_boundary else 0)
    centerline = np.array([[x, 0.0] for x in xs], dtype=float)
    return {
        "X": np.array(X, dtype=float),
        "Y": np.array(Y, dtype=float),
        "ring": np.array([r for r in ring if r is not None], dtype=int),
        "ipob": np.array(ipob, dtype=int),
        "centerline": centerline,
    }


_BED = {"bed_top_m": 10.0, "bed_drop_m": 0.5}
_LB = ["inflow", "outflow"]


def _author(cfg, workdir):
    cas_path = str(Path(workdir) / "t2d_river.cas")
    B.author_deck(cfg, _tiny_mesh(), "river.slf", "river.cli", "r2d_river.slf",
                  cas_path, _LB, _BED)
    return Path(cas_path).read_text()


# --- decay: WAQTEL block + steering file ------------------------------------ #
def test_decay_emits_waqtel_block_and_steering_file(tmp_path):
    cfg = ReachConfig(substance_class="decay", decay_law=1, decay_coef=2.0,
                      workdir=str(tmp_path))
    cas = _author(cfg, tmp_path)
    # the three coupling keywords land in the t2d cas
    assert "COUPLING WITH" in cas and "'WAQTEL'" in cas
    assert "WAQTEL STEERING FILE" in cas
    assert WAQTEL_FILENAME in cas
    assert "WATER QUALITY PROCESS" in cas and "= 17" in cas
    # the tiny steering file was written next to the cas with the law + coef
    waq = tmp_path / WAQTEL_FILENAME
    assert waq.exists()
    body = waq.read_text()
    assert "LAW OF TRACERS DEGRADATION" in body
    assert "COEFFICIENT 1 FOR LAW OF TRACERS DEGRADATION" in body
    assert "= 1" in body        # law 1 (T90)
    assert "= 2" in body        # coef 2.0 (h)
    # DAMOCLES 72-char clamp holds on every steering line
    assert all(len(ln) <= 72 for ln in body.splitlines())


def test_decay_first_order_law_and_coef_flow_through(tmp_path):
    cfg = ReachConfig(substance_class="decay", decay_law=2, decay_coef=0.35,
                      workdir=str(tmp_path))
    _author(cfg, tmp_path)
    body = (tmp_path / WAQTEL_FILENAME).read_text()
    assert "LAW OF TRACERS DEGRADATION           = 2" in body
    assert "0.35" in body


def test_decay_leaves_sources_pulse_on_the_dye_column(tmp_path):
    # WAQTEL decay does NOT change the SOURCES file - the pulse column stays the
    # dye concentration (the decay sink lives in the solve, not the forcing).
    cfg = ReachConfig(substance_class="decay", dye_conc_mgl=100.0,
                      workdir=str(tmp_path))
    _author(cfg, tmp_path)
    src = (tmp_path / B.SOURCES_FILENAME).read_text()
    assert "TR(1,1)" in src
    assert "100.000" in src     # the dye pulse concentration, unchanged


# --- tracer / oil: NO WAQTEL ------------------------------------------------ #
def test_tracer_emits_no_waqtel(tmp_path):
    cfg = ReachConfig(substance_class="tracer", workdir=str(tmp_path))
    cas = _author(cfg, tmp_path)
    assert "WAQTEL" not in cas
    assert "COUPLING WITH" not in cas
    assert "WATER QUALITY PROCESS" not in cas
    assert not (tmp_path / WAQTEL_FILENAME).exists()


def test_oil_emits_no_waqtel(tmp_path):
    # oil and decay are mutually exclusive classes: the oil deck must carry its
    # own steering (OIL SPILL STEERING FILE) but NEVER the WAQTEL coupling.
    cfg = ReachConfig(substance_class="oil", oil_preset="light_crude",
                      workdir=str(tmp_path))
    cas = _author(cfg, tmp_path)
    assert "OIL SPILL STEERING FILE" in cas
    assert "WAQTEL" not in cas
    assert "COUPLING WITH" not in cas
    assert not (tmp_path / WAQTEL_FILENAME).exists()


# --- ReachConfig defaults leave non-decay runs unaffected ------------------- #
def test_reachconfig_decay_defaults():
    cfg = ReachConfig()
    assert cfg.substance_class == "tracer"
    assert cfg.decay_law == 1
    assert cfg.decay_coef == 2.0
