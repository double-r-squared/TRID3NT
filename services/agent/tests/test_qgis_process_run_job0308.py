"""job-0308: qgis_process RUN — param translation (stage-then-mount) unit tests."""
from grace2_agent.tools.passthroughs import _build_qgis_run_args


def _fake_stager(value, rundir):
    if isinstance(value, str) and value.startswith(("s3://", "gs://")):
        return f"/data/{value.rsplit('/', 1)[-1]}"
    return None


def test_inputs_staged_outputs_collected_literals_passthrough():
    params = {"INPUT": "s3://b/dem.tif", "BAND": 1, "Z_FACTOR": 1.0, "OUTPUT": "slope.tif"}
    args, outputs = _build_qgis_run_args(params, "/tmp/x", _fake_stager)
    assert "--INPUT=/data/dem.tif" in args          # s3 input staged + rewritten
    assert "--BAND=1" in args and "--Z_FACTOR=1.0" in args  # literals pass through
    assert "--OUTPUT=/data/output.tif" in args      # output sink -> container path
    assert outputs == {"OUTPUT": "output.tif"}       # collected for upload


def test_output_without_ext_defaults_tif_and_gs_input_staged():
    params = {"INPUT": "gs://b/x.gpkg", "OUTPUT": ""}
    args, outputs = _build_qgis_run_args(params, "/tmp/x", _fake_stager)
    assert "--INPUT=/data/x.gpkg" in args
    assert outputs == {"OUTPUT": "output.tif"}
    assert "--OUTPUT=/data/output.tif" in args


def test_vector_output_ext_preserved():
    params = {"INPUT": "s3://b/pts.gpkg", "DISTANCE": 100, "OUTPUT": "buf.gpkg"}
    args, outputs = _build_qgis_run_args(params, "/tmp/x", _fake_stager)
    assert outputs == {"OUTPUT": "output.gpkg"}
    assert "--DISTANCE=100" in args
