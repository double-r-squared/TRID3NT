"""CLI entrypoint for the PyQGIS worker round-trip.

Invoked by the Cloud Run Job container built in job-0021::

    python -m services.workers.pyqgis \
        --qgs-uri /vsigs/grace-2-hazard-prod-qgs/grace2-sample.qgs \
        --layer-to-add demo-polygon

Env-var fallbacks (Cloud Run Jobs prefer env over args):

* ``QGS_URI``       → ``--qgs-uri``
* ``LAYER_TO_ADD``  → ``--layer-to-add``
* ``GCP_PROJECT``   → Pub/Sub project override
* ``PUBSUB_TOPIC``  → Pub/Sub topic override

The process exit code is 0 on both success and recoverable-error paths:
the published Pub/Sub envelope is the single source of truth for downstream
consumers (NFR-R-1). Exit code is non-zero only for setup errors (missing
arg + missing env var).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from .types import LayerSpec
from .worker import worker_round_trip


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m services.workers.pyqgis",
        description=(
            "GRACE-2 PyQGIS worker — read a .qgs from GCS, append a layer, "
            "write back, publish completion to Pub/Sub."
        ),
    )
    parser.add_argument(
        "--qgs-uri",
        default=os.environ.get("QGS_URI"),
        help=(
            "Path to the .qgs to mutate. Accepts /vsigs/<bucket>/<key>.qgs, "
            "gs://<bucket>/<key>.qgs, or a local absolute path. "
            "Defaults to env var QGS_URI."
        ),
    )
    parser.add_argument(
        "--layer-to-add",
        default=os.environ.get("LAYER_TO_ADD"),
        help=(
            "Name of the polygon layer the worker will append. "
            "Defaults to env var LAYER_TO_ADD."
        ),
    )
    parser.add_argument(
        "--no-publish",
        action="store_true",
        help="Skip Pub/Sub publish (local-dev / unit-test mode).",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    if not args.qgs_uri:
        parser.error(
            "--qgs-uri is required (or set QGS_URI env var)."
        )
    if not args.layer_to_add:
        parser.error(
            "--layer-to-add is required (or set LAYER_TO_ADD env var)."
        )

    spec = LayerSpec(name=args.layer_to_add)
    result = worker_round_trip(
        args.qgs_uri,
        spec,
        publish=not args.no_publish,
    )

    # Emit the result as JSON on stdout so the Cloud Run Job execution log
    # carries the structured envelope (also published to Pub/Sub).
    print(json.dumps(result.to_dict(), indent=2))

    # Exit code policy: 0 on ok + 0 on recoverable error (envelope is the
    # source of truth). The non-zero codes from argparse.parser.error()
    # above cover the unrecoverable arg-missing case.
    return 0


if __name__ == "__main__":
    sys.exit(main())
