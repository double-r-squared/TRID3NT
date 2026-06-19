"""Unit tests for the case-export Lambda. boto3 (the DynamoDB resource + the S3
client) + the Cognito verifier are mocked -- NO live AWS, NO network.

Covers the auth + owner contract and the export build:

  * SIGNED-IN owner -> 200 {url, size_bytes, layer_count, expires_in}; the built
    zip contains a styled .qgs (singlebandpseudocolor ramp for
    continuous_flood_depth), named per-layer folders, and the .tif/.geojson
    files; size_bytes sums the HeadObject ContentLengths.
  * OWNER MISMATCH -> 403 (a verified non-owner can never export the case).
  * NO TOKEN / verify -> None -> 401 (export is privileged; never anonymous).
  * ?url= COG recovery: a TiTiler tile-template uri resolves to the underlying
    s3:// COG.
  * 404-evicted cache object -> skipped (not in the zip), 200 still returned.

The DynamoDB resource is a MagicMock whose ``Table(...).get_item`` returns the
fake case doc. The S3 client is a fake capturing put_object + serving
head_object/download_file/get_object/generate_presigned_url from an in-memory
object store, so the handler's real zip + .qgs generation runs end-to-end.
"""

from __future__ import annotations

import importlib.util
import io
import json
import zipfile
from pathlib import Path
from unittest import mock

import pytest

_HERE = Path(__file__).resolve().parent
_HANDLER = _HERE.parent / "handler.py"

_UID = "user-abc-123"
_OTHER_UID = "user-xyz-999"
_CASE_ID = "01CASE"

_CACHE_BUCKET = "grace2-hazard-cache-test"
_RUNS_BUCKET = "grace2-hazard-runs-test"
_TILE_BASE = "https://edge.example"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("CASES_TABLE", "grace2_cases")
    monkeypatch.setenv("CACHE_BUCKET", _CACHE_BUCKET)
    monkeypatch.setenv("RUNS_BUCKET", _RUNS_BUCKET)
    monkeypatch.setenv("EXPORTS_PREFIX", "exports")
    monkeypatch.setenv("EXPORT_SIGNED_TTL_S", "3600")
    monkeypatch.setenv("GRACE2_COGNITO_USER_POOL_ID", "us-west-2_TESTPOOL")
    monkeypatch.setenv("GRACE2_COGNITO_CLIENT_ID", "testclientid")


class _FakeS3:
    """In-memory S3: object store keyed by (bucket, key) -> bytes.

    Implements head_object / download_file / get_object / put_object /
    generate_presigned_url with the same shapes boto3 returns. A
    ``ClientError``-shaped 404 is raised for a missing head/get so the handler's
    evicted-object skip + snapshot-absent paths exercise correctly.
    """

    def __init__(self):
        self.store: dict[tuple[str, str], bytes] = {}
        self.puts: list[tuple[str, str, bytes]] = []

    def _not_found(self):
        from botocore.exceptions import ClientError

        return ClientError(
            {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
            "HeadObject",
        )

    def head_object(self, Bucket, Key):  # noqa: N803
        body = self.store.get((Bucket, Key))
        if body is None:
            raise self._not_found()
        return {"ContentLength": len(body)}

    def get_object(self, Bucket, Key):  # noqa: N803
        body = self.store.get((Bucket, Key))
        if body is None:
            raise self._not_found()
        return {"Body": io.BytesIO(body)}

    def download_file(self, Bucket, Key, Filename):  # noqa: N803
        body = self.store.get((Bucket, Key))
        if body is None:
            raise self._not_found()
        with open(Filename, "wb") as f:
            f.write(body)

    def put_object(self, Bucket, Key, Body, ContentType=None):  # noqa: N803
        data = Body if isinstance(Body, bytes) else bytes(Body)
        self.store[(Bucket, Key)] = data
        self.puts.append((Bucket, Key, data))
        return {}

    def generate_presigned_url(self, ClientMethod, Params, ExpiresIn):  # noqa: N803
        return (
            f"https://{Params['Bucket']}.s3.amazonaws.com/"
            f"{Params['Key']}?X-Amz-Expires={ExpiresIn}&X-Amz-Signature=fake"
        )


def _load(*, case_doc, s3):
    """Import the handler fresh with boto3.resource (DynamoDB) + boto3.client
    (S3) replaced. Both are constructed at module import, so patch first.

    Returns ``(module, table, s3)``.
    """
    table = mock.MagicMock(name="table")
    if case_doc is None:
        table.get_item.return_value = {}
    else:
        table.get_item.return_value = {"Item": case_doc}
    resource = mock.MagicMock(name="ddb_resource")
    resource.Table.return_value = table

    def _client(name, **kwargs):
        assert name == "s3"
        return s3

    spec = importlib.util.spec_from_file_location("case_export_handler_under_test", _HANDLER)
    module = importlib.util.module_from_spec(spec)
    with mock.patch("boto3.resource", return_value=resource), mock.patch(
        "boto3.client", side_effect=_client
    ):
        spec.loader.exec_module(module)
    return module, table, s3


def _body(resp):
    return json.loads(resp["body"])


def _set_verify(monkeypatch, module, claims):
    monkeypatch.setattr(module, "cognito_verify", lambda token: claims)


def _get(*, token=None, case_id=_CASE_ID):
    event: dict = {"requestContext": {"http": {"method": "GET"}}}
    if case_id is not None:
        event["queryStringParameters"] = {"case_id": case_id}
    if token is not None:
        event["headers"] = {"authorization": f"Bearer {token}"}
    return event


def _tile_template(s3_uri: str, *, rescale="0,3", cmap="ylgnbu") -> str:
    import urllib.parse

    q = urllib.parse.quote(s3_uri, safe="")
    return (
        f"{_TILE_BASE}/cog/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}.png"
        f"?url={q}&rescale={rescale}&colormap_name={cmap}"
    )


def _flood_doc(*, owner=_UID):
    """A case doc: one flood-depth raster (TiTiler tile-template uri)."""
    return {
        "_id": _CASE_ID,
        "title": "Fort Myers Flood",
        "owner_user_id": owner,
        "status": "active",
        "loaded_layer_summaries": [
            {
                "layer_id": "L1",
                "name": "Flood depth peak",
                "layer_type": "raster",
                "uri": _tile_template(f"s3://{_CACHE_BUCKET}/cog/abc123/flood_depth_peak.tif"),
                "style_preset": "continuous_flood_depth",
            }
        ],
    }


def _unzip(s3: _FakeS3) -> zipfile.ZipFile:
    """Return the just-put export zip as a ZipFile (asserts exactly one put)."""
    zips = [p for p in s3.puts if p[1].startswith("exports/")]
    assert len(zips) == 1, f"expected one export zip put, got {len(zips)}"
    return zipfile.ZipFile(io.BytesIO(zips[0][2]))


# --------------------------------------------------------------------------- #
# Auth + owner contract.
# --------------------------------------------------------------------------- #


def test_no_token_is_401(env, monkeypatch):
    s3 = _FakeS3()
    module, table, _s3 = _load(case_doc=_flood_doc(), s3=s3)
    _set_verify(monkeypatch, module, None)
    resp = module.handler(_get(), None)  # no token
    assert resp["statusCode"] == 401
    table.get_item.assert_not_called()
    assert s3.puts == []


def test_invalid_token_is_401(env, monkeypatch):
    s3 = _FakeS3()
    module, table, _s3 = _load(case_doc=_flood_doc(), s3=s3)
    _set_verify(monkeypatch, module, None)
    resp = module.handler(_get(token="bogus.jwt"), None)
    assert resp["statusCode"] == 401
    table.get_item.assert_not_called()


def test_owner_mismatch_is_403(env, monkeypatch):
    s3 = _FakeS3()
    # Owner is _UID; the signed-in caller is _OTHER_UID -> hard 403.
    module, _table, _s3 = _load(case_doc=_flood_doc(owner=_UID), s3=s3)
    _set_verify(monkeypatch, module, {"uid": _OTHER_UID})
    resp = module.handler(_get(token="good.jwt"), None)
    assert resp["statusCode"] == 403
    assert s3.puts == []


def test_owner_less_case_is_403(env, monkeypatch):
    """FAIL CLOSED: an owner-less Case (no owner_user_id / user_id) is exportable
    by NO ONE -- any signed-in uid gets a hard 403, never an implicit allow."""
    s3 = _FakeS3()
    doc = {
        "_id": _CASE_ID,
        "title": "Orphan Case",
        "status": "active",
        # No owner_user_id, no user_id -> falsy owner -> must still 403.
        "loaded_layer_summaries": [
            {
                "layer_id": "L1",
                "name": "Flood depth peak",
                "layer_type": "raster",
                "uri": _tile_template(f"s3://{_CACHE_BUCKET}/cog/abc123/flood_depth_peak.tif"),
                "style_preset": "continuous_flood_depth",
            }
        ],
    }
    module, _table, _s3 = _load(case_doc=doc, s3=s3)
    _set_verify(monkeypatch, module, {"uid": _UID})
    resp = module.handler(_get(token="good.jwt"), None)
    assert resp["statusCode"] == 403
    assert s3.puts == []


def test_case_not_found_is_404(env, monkeypatch):
    s3 = _FakeS3()
    module, _table, _s3 = _load(case_doc=None, s3=s3)
    _set_verify(monkeypatch, module, {"uid": _UID})
    resp = module.handler(_get(token="good.jwt"), None)
    assert resp["statusCode"] == 404


def test_missing_case_id_is_400(env, monkeypatch):
    s3 = _FakeS3()
    module, _table, _s3 = _load(case_doc=_flood_doc(), s3=s3)
    _set_verify(monkeypatch, module, {"uid": _UID})
    resp = module.handler(_get(token="good.jwt", case_id=None), None)
    assert resp["statusCode"] == 400


def test_options_preflight_is_200(env):
    s3 = _FakeS3()
    module, _table, _s3 = _load(case_doc=_flood_doc(), s3=s3)
    resp = module.handler({"requestContext": {"http": {"method": "OPTIONS"}}}, None)
    assert resp["statusCode"] == 200
    assert resp["headers"]["Access-Control-Allow-Origin"] == "*"


# --------------------------------------------------------------------------- #
# Happy path: owner export -> zip with styled .qgs + per-layer folder + size.
# --------------------------------------------------------------------------- #


def test_owner_export_builds_styled_qgs_zip(env, monkeypatch):
    s3 = _FakeS3()
    # The recovered COG content lives in the cache bucket.
    cog_key = "cog/abc123/flood_depth_peak.tif"
    cog_bytes = b"II*\x00" + b"\x00" * 1020  # 1024-byte fake tiff
    s3.store[(_CACHE_BUCKET, cog_key)] = cog_bytes

    module, _table, _s3 = _load(case_doc=_flood_doc(), s3=s3)
    _set_verify(monkeypatch, module, {"uid": _UID})

    resp = module.handler(_get(token="good.jwt"), None)
    assert resp["statusCode"] == 200, resp["body"]
    body = _body(resp)
    assert body["layer_count"] == 1
    assert body["size_bytes"] == len(cog_bytes)
    assert body["expires_in"] == 3600
    assert body["url"].startswith("https://")

    zf = _unzip(s3)
    names = zf.namelist()
    # Named per-layer folder (sanitized from "Flood depth peak").
    assert "Flood_depth_peak/flood_depth_peak.tif" in names
    assert "project.qgs" in names
    assert "manifest.txt" in names

    # The COG bytes round-tripped into the zip.
    assert zf.read("Flood_depth_peak/flood_depth_peak.tif") == cog_bytes

    # The .qgs styles the flood raster as singlebandpseudocolor with the
    # continuous_flood_depth ramp (0..3, ylgnbu anchors).
    qgs = zf.read("project.qgs").decode("utf-8")
    assert 'type="singlebandpseudocolor"' in qgs
    assert 'classificationMin="0"' in qgs
    assert 'classificationMax="3"' in qgs
    # The ylgnbu low anchor (#ffffd9) and high anchor (#081d58) are present.
    assert "#ffffd9" in qgs
    assert "#081d58" in qgs
    # Relative datasource referencing the co-zipped file.
    assert "./Flood_depth_peak/flood_depth_peak.tif" in qgs


def test_url_param_cog_recovery(env, monkeypatch):
    """The TiTiler tile-template ?url= param resolves to the underlying s3 COG;
    that exact key is HEADed + downloaded from the cache bucket."""
    s3 = _FakeS3()
    cog_key = "cog/abc123/flood_depth_peak.tif"
    s3.store[(_CACHE_BUCKET, cog_key)] = b"X" * 500
    module, _table, _s3 = _load(case_doc=_flood_doc(), s3=s3)
    _set_verify(monkeypatch, module, {"uid": _UID})

    # Unit-level: the recovery helper itself returns the decoded s3 URI.
    recovered = module._recover_s3_uri(
        _tile_template(f"s3://{_CACHE_BUCKET}/{cog_key}")
    )
    assert recovered == f"s3://{_CACHE_BUCKET}/{cog_key}"

    resp = module.handler(_get(token="good.jwt"), None)
    assert resp["statusCode"] == 200
    assert _body(resp)["size_bytes"] == 500


def test_evicted_cache_object_is_skipped(env, monkeypatch):
    """A 404 on the recovered COG (evicted content-addressed object) is skipped
    with a typed manifest warn; the export still returns 200 (other layers)."""
    s3 = _FakeS3()
    # Two layers: L1 evicted (no store entry), L2 present.
    present_key = "cog/def456/depth2.tif"
    s3.store[(_CACHE_BUCKET, present_key)] = b"Y" * 300
    doc = _flood_doc()
    doc["loaded_layer_summaries"].append(
        {
            "layer_id": "L2",
            "name": "Second depth",
            "layer_type": "raster",
            "uri": f"s3://{_CACHE_BUCKET}/{present_key}",
            "style_preset": "continuous_flood_depth",
        }
    )
    # L1's COG (cog/abc123/...) is NOT in the store -> evicted.
    module, _table, _s3 = _load(case_doc=doc, s3=s3)
    _set_verify(monkeypatch, module, {"uid": _UID})

    resp = module.handler(_get(token="good.jwt"), None)
    assert resp["statusCode"] == 200
    body = _body(resp)
    # Only the present layer counts; evicted L1 skipped.
    assert body["layer_count"] == 1
    assert body["size_bytes"] == 300

    zf = _unzip(s3)
    names = zf.namelist()
    assert "Second_depth/depth2.tif" in names
    assert "Flood_depth_peak/flood_depth_peak.tif" not in names
    manifest = zf.read("manifest.txt").decode("utf-8")
    assert "evicted" in manifest.lower()


def test_vector_inline_geojson_from_snapshot(env, monkeypatch):
    """A vector with no standalone object pulls its inline GeoJSON from the
    case-view snapshot and is written as a .geojson the .qgs references via ogr."""
    s3 = _FakeS3()
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-82, 26]}, "properties": {}}
        ],
    }
    # The snapshot carries inline_geojson on loaded_layers for layer V1.
    snapshot = {
        "session_state": {
            "loaded_layers": [
                {"layer_id": "V1", "inline_geojson": geojson},
            ]
        }
    }
    s3.store[(_RUNS_BUCKET, f"case-views/{_CASE_ID}.json")] = json.dumps(snapshot).encode()

    doc = {
        "_id": _CASE_ID,
        "title": "Rivers Case",
        "owner_user_id": _UID,
        "status": "active",
        "loaded_layer_summaries": [
            {
                "layer_id": "V1",
                "name": "Rivers",
                "layer_type": "vector",
                "uri": "inline:rivers",  # no s3 object, not a tile template
                "style_preset": "",
            }
        ],
    }
    module, _table, _s3 = _load(case_doc=doc, s3=s3)
    _set_verify(monkeypatch, module, {"uid": _UID})

    resp = module.handler(_get(token="good.jwt"), None)
    assert resp["statusCode"] == 200
    body = _body(resp)
    assert body["layer_count"] == 1

    zf = _unzip(s3)
    names = zf.namelist()
    assert "Rivers/Rivers.geojson" in names
    written = json.loads(zf.read("Rivers/Rivers.geojson"))
    assert written == geojson
    qgs = zf.read("project.qgs").decode("utf-8")
    assert "<provider>ogr</provider>" in qgs
    assert "./Rivers/Rivers.geojson" in qgs


def test_vector_no_inline_is_skipped(env, monkeypatch):
    """A vector with no s3 object AND no inline geojson in the snapshot is
    skipped (not in the zip), still 200."""
    s3 = _FakeS3()  # no snapshot stored -> no inline geojson
    doc = {
        "_id": _CASE_ID,
        "title": "Empty vec",
        "owner_user_id": _UID,
        "loaded_layer_summaries": [
            {
                "layer_id": "V9",
                "name": "Ghost",
                "layer_type": "vector",
                "uri": "inline:ghost",
                "style_preset": "",
            }
        ],
    }
    module, _table, _s3 = _load(case_doc=doc, s3=s3)
    _set_verify(monkeypatch, module, {"uid": _UID})
    resp = module.handler(_get(token="good.jwt"), None)
    assert resp["statusCode"] == 200
    assert _body(resp)["layer_count"] == 0


def test_terrain_preset_no_pseudocolor(env, monkeypatch):
    """A terrain-token preset (continuous_dem) gets a plain single-band-gray
    renderer (NO singlebandpseudocolor ramp) -- mirrors the live TiTiler path
    leaving style_params empty for terrain."""
    s3 = _FakeS3()
    dem_key = "cog/terr/dem.tif"
    s3.store[(_CACHE_BUCKET, dem_key)] = b"D" * 200
    doc = {
        "_id": _CASE_ID,
        "title": "Terrain",
        "owner_user_id": _UID,
        "loaded_layer_summaries": [
            {
                "layer_id": "T1",
                "name": "Elevation DEM",
                "layer_type": "raster",
                "uri": f"s3://{_CACHE_BUCKET}/{dem_key}",
                "style_preset": "continuous_dem",
            }
        ],
    }
    module, _table, _s3 = _load(case_doc=doc, s3=s3)
    _set_verify(monkeypatch, module, {"uid": _UID})
    resp = module.handler(_get(token="good.jwt"), None)
    assert resp["statusCode"] == 200
    zf = _unzip(s3)
    qgs = zf.read("project.qgs").decode("utf-8")
    assert 'type="singlebandgray"' in qgs
    # The flood ramp must NOT appear for a terrain layer.
    assert 'type="singlebandpseudocolor"' not in qgs


def test_put_key_is_under_exports_prefix(env, monkeypatch):
    """The zip is uploaded under exports/{case_id}/ on the runs bucket."""
    s3 = _FakeS3()
    s3.store[(_CACHE_BUCKET, "cog/abc123/flood_depth_peak.tif")] = b"Z" * 64
    module, _table, _s3 = _load(case_doc=_flood_doc(), s3=s3)
    _set_verify(monkeypatch, module, {"uid": _UID})
    resp = module.handler(_get(token="good.jwt"), None)
    assert resp["statusCode"] == 200
    put = [p for p in s3.puts if p[1].startswith("exports/")][0]
    assert put[0] == _RUNS_BUCKET
    assert put[1].startswith(f"exports/{_CASE_ID}/")
    assert put[1].endswith(".zip")


# --------------------------------------------------------------------------- #
# Style-table parity with publish_layer (the verbatim copy).
# --------------------------------------------------------------------------- #


def test_style_table_matches_publish_layer_registry():
    """The replicated registry must match publish_layer._TITILER_STYLE_REGISTRY
    byte-for-byte (the kickoff mandate: COPY VERBATIM)."""
    s3 = _FakeS3()
    import os as _os

    _os.environ.setdefault("CASES_TABLE", "grace2_cases")
    _os.environ.setdefault("RUNS_BUCKET", _RUNS_BUCKET)
    module, _table, _s3 = _load(case_doc=_flood_doc(), s3=s3)

    repo_root = Path(__file__).resolve().parents[5]
    pub = repo_root / "services/agent/src/grace2_agent/tools/publish_layer.py"
    src = pub.read_text(encoding="utf-8")
    # Spot-check the load-bearing presets are identical to the source.
    assert '"continuous_flood_depth": ("0,3", "ylgnbu")' in src
    assert module._TITILER_STYLE_REGISTRY["continuous_flood_depth"] == ("0,3", "ylgnbu")
    assert module._TITILER_STYLE_REGISTRY["continuous_plume_concentration"] == ("0,10", "reds")
    assert module._TITILER_STYLE_REGISTRY["era5_2m_temperature"] == ("250,320", "rdylbu_r")


def test_resolve_style_safe_default_for_unknown_preset(env, monkeypatch):
    s3 = _FakeS3()
    module, _table, _s3 = _load(case_doc=_flood_doc(), s3=s3)
    style = module._resolve_style("totally_unknown_preset", "s3://b/k.tif")
    assert style == (0.0, 1.0, "viridis")
