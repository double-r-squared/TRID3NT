"""job-0270 adversarial verification — live Gemini-free publish of the EXACT
cached Boulder colored relief the user generated, via the real
publish_layer tool (dispatches the pyqgis-worker Cloud Run Job).

Asserts the style preset auto-resolves to "" (terrain family -> QGIS default
rendering, job-0269b) BEFORE dispatch, then prints the returned WMS URL.
"""
from __future__ import annotations

import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

LAYER_URI = (
    "gs://grace-2-hazard-prod-cache/cache/static-30d/colored_relief/"
    "27d5f864b3d5e9ceae84e8f89f2dfa55.tif"
)
LAYER_ID = "colored-relief-boulder-verify-0270"

from grace2_agent.tools.publish_layer import _infer_style_preset, publish_layer

# --- Assertion 1: auto style inference for this exact input is "" ----------
inferred = _infer_style_preset(LAYER_URI, LAYER_ID)
print(f"INFERRED_STYLE_PRESET={inferred!r}", flush=True)
assert inferred == "", f"expected '' (QGIS default), got {inferred!r}"

# --- Live dispatch (style_preset omitted -> auto path) ----------------------
url = publish_layer(layer_uri=LAYER_URI, layer_id=LAYER_ID)
print(f"WMS_URL={url}", flush=True)
sys.exit(0)
