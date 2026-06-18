"""GRACE-2 SFINCS deck-builder worker (GPL-isolated, coastal North Star gate).

This package authors a MULTI-LEVEL refined SFINCS *quadtree* + *SnapWave* deck
from scratch via Deltares ``cht_sfincs`` (GPL-3.0, v1.0.0) and writes it back to
the object store in the EXACT manifest shape the GPL-free SFINCS *solve* worker
(``services/workers/sfincs/entrypoint.py``) already consumes.

GPL boundary
------------
``cht_sfincs`` is GPL-3.0. It lives ONLY inside THIS worker's container image and
is imported ONLY by ``entrypoint.py`` (lazily, inside ``build_deck``). The GRACE-2
agent venv and ALL agent code (``services/agent/src/grace2_agent/**``) NEVER import
``cht_sfincs`` — the agent reaches this worker arms-length over the object-store +
AWS-Batch-submit seam (mirroring how it already reaches the MIT-licensed solve
worker), so the GPL code stays fully isolated in its own image.

Pure-Python helpers in ``entrypoint`` that do NOT touch ``cht_sfincs`` (manifest
parse, build-spec validation, S3 I/O, the time-column normalizer) are unit-tested
without importing the GPL library; ``build_deck`` itself is exercised against the
spike venv where ``cht_sfincs`` is installed.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
