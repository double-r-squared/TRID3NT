"""M2 acceptance — IaC integrity + bucket security posture.

Sprint-04 EC2 verification: the three GCS buckets carry the
Public-Access-Prevention / Uniform-Bucket-Level-Access posture the
kickoff demanded (NFR-S-5, Invariant 5), and ``tofu plan`` against the
M2 resources is clean (zero changes — or, per the kickoff allowance,
only the documented OQ-F cosmetic scaling-block normalization drift
carried from job-0018).

Layer attribution on failure (testing.md "every failure names the
failing layer"):

* Bucket PAP/UBLA missing → ``layer=infra (job-0018 buckets.tf)``
* Bucket public IAM binding → ``layer=infra (security regression)``
* ``tofu plan`` non-zero exit → ``layer=infra (IaC drift)``
* ``tofu plan`` unexpected diff → ``layer=infra (out-of-band change)``
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


M2_BUCKETS = (
    "grace-2-hazard-prod-qgs",
    "grace-2-hazard-prod-cog",
    "grace-2-hazard-prod-fgb",
)

#: Resource addresses the M2 sprint produced. tofu plan against these is
#: targeted (Atlas-provider auth is the "ad-hoc API key" ritual documented
#: in infra/README.md; we don't require it for a passive M2 re-verification).
M2_PLAN_TARGETS = (
    "google_storage_bucket.qgs",
    "google_storage_bucket.cog",
    "google_storage_bucket.fgb",
    "google_pubsub_topic.worker_events",
    "google_cloud_run_v2_service.qgis_server",
    "google_cloud_run_v2_job.pyqgis_worker",
    "google_service_account.qgis_server",
    "google_service_account.pyqgis_worker",
    "google_storage_bucket_iam_member.pyqgis_worker_qgs_admin",
    "google_pubsub_topic_iam_member.pyqgis_worker_publisher",
)


def _bucket_describe(gcloud: str, bucket: str, project: str) -> dict:
    proc = subprocess.run(
        [
            gcloud,
            "storage",
            "buckets",
            "describe",
            f"gs://{bucket}",
            f"--project={project}",
            "--format=json",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"layer=GCS (bucket describe): exit={proc.returncode} "
            f"for gs://{bucket}; stderr tail: {proc.stderr[-300:]!r}"
        )
    return json.loads(proc.stdout)


def _bucket_iam(gcloud: str, bucket: str, project: str) -> dict:
    proc = subprocess.run(
        [
            gcloud,
            "storage",
            "buckets",
            "get-iam-policy",
            f"gs://{bucket}",
            f"--project={project}",
            "--format=json",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"layer=GCS (bucket IAM): exit={proc.returncode} for "
            f"gs://{bucket}; stderr tail: {proc.stderr[-300:]!r}"
        )
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# EC2 — bucket security posture
# ---------------------------------------------------------------------------


@pytest.mark.live_worker
def test_no_public_buckets(gcloud_bin: str, gcp_project: str) -> None:
    """EC2 — all three M2 buckets have ``publicAccessPrevention=enforced``,
    ``uniformBucketLevelAccess=True``, and no ``allUsers`` /
    ``allAuthenticatedUsers`` IAM bindings.

    The kickoff demanded:

    > test_no_public_buckets: gcloud storage buckets list --format=json |
    > jq for iamConfiguration.publicAccessPrevention=enforced on all three
    > M2 buckets

    We additionally assert UBLA and absence of public IAM members — the
    job-0018 audit's security guarantees (NFR-S-5, Invariant 5).
    """
    for bucket in M2_BUCKETS:
        desc = _bucket_describe(gcloud_bin, bucket, gcp_project)

        pap = desc.get("public_access_prevention")
        assert pap == "enforced", (
            f"layer=infra (buckets.tf, job-0018): "
            f"gs://{bucket} public_access_prevention={pap!r} "
            f"(expected 'enforced'). NFR-S-5 violation."
        )

        ubla_field = desc.get("uniform_bucket_level_access")
        # gcloud's structured JSON returns a bool for top-level UBLA on
        # uniform buckets; older payloads return a dict. Accept either.
        if isinstance(ubla_field, dict):
            ubla = ubla_field.get("enabled", False)
        else:
            ubla = bool(ubla_field)
        assert ubla, (
            f"layer=infra (buckets.tf, job-0018): gs://{bucket} "
            f"uniform_bucket_level_access not enabled "
            f"(raw={ubla_field!r}). NFR-S-5 violation."
        )

        iam = _bucket_iam(gcloud_bin, bucket, gcp_project)
        bindings = iam.get("bindings", [])
        for binding in bindings:
            members = binding.get("members", [])
            for m in members:
                assert m not in ("allUsers", "allAuthenticatedUsers"), (
                    f"layer=infra security regression: gs://{bucket} "
                    f"has public IAM binding role={binding.get('role')!r} "
                    f"member={m!r}. NFR-S-5 / Invariant 5 violation."
                )


# ---------------------------------------------------------------------------
# EC2 — tofu plan clean (M2 resources only)
# ---------------------------------------------------------------------------


def _tofu_bin() -> str | None:
    p = shutil.which("tofu")
    if p:
        return p
    fallback = Path.home() / ".local" / "bin" / "tofu"
    if fallback.exists():
        return str(fallback)
    return None


# Markers describing the only diffs the kickoff allows in a "clean" M2 plan:
#
# - "No changes" — the canonical clean outcome.
# - The OQ-F cosmetic scaling-block normalization drift carried from
#   job-0018; per the kickoff: "exit code 0 and stdout contains
#   'No changes' OR only contains the documented OQ-F cosmetic scaling
#   drift carry-forward."
OQ_F_DRIFT_SIGNATURES = (
    "manual_instance_count",
    "min_instance_count",
)


@pytest.mark.live_tofu
def test_tofu_plan_clean(repo_root_m2: Path) -> None:
    """EC2 — ``tofu plan`` against the M2 resources exits 0 and shows
    either "No changes" or only the documented OQ-F cosmetic scaling
    drift carry-forward.

    The plan is targeted at the M2 resource set (NOT the full
    configuration) because the mongodbatlas provider requires an
    ad-hoc Atlas API key pair to authenticate — that's the documented
    "least-privilege ritual" in infra/README.md, not a static credential
    available to a passive re-verification suite. Targeting the M2
    resources keeps the test honest about what M2 actually owns.
    """
    if os.environ.get("GRACE2_SKIP_LIVE_TOFU"):
        pytest.skip(
            "GRACE2_SKIP_LIVE_TOFU=1 — skipping the live tofu plan; "
            "layer=dev opt-out."
        )

    tofu = _tofu_bin()
    if not tofu:
        pytest.skip(
            "layer=dev-env: tofu CLI not found on PATH or ~/.local/bin/ "
            "— PROJECT_STATE.md § Environment facts records ~/.local/bin/tofu."
        )

    infra_dir = repo_root_m2 / "infra"
    if not (infra_dir / "backend.tf").exists():
        pytest.skip(
            "layer=infra: infra/ scaffold missing; cannot run tofu plan."
        )

    cmd = [tofu, f"-chdir={infra_dir}", "plan", "-no-color"]
    for target in M2_PLAN_TARGETS:
        cmd.extend(["-target", target])

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
    )
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    assert proc.returncode == 0, (
        f"layer=infra (tofu plan exit code): expected 0 got "
        f"{proc.returncode}. stdout tail: {stdout[-600:]!r}; "
        f"stderr tail: {stderr[-600:]!r}"
    )

    if "No changes" in stdout:
        # Clean — the ideal outcome.
        return

    # Acceptable alternative per the kickoff: only the OQ-F cosmetic
    # scaling-block normalization drift on google_cloud_run_v2_service.qgis_server.
    if "Plan: 0 to add, 1 to change, 0 to destroy" in stdout and all(
        sig in stdout for sig in OQ_F_DRIFT_SIGNATURES
    ):
        # Ensure the drift only touches the scaling block on the QGIS
        # Server service — anything else is unexpected.
        assert (
            "google_cloud_run_v2_service.qgis_server" in stdout
        ), (
            f"layer=infra (tofu plan): unexpected resource in the 1-change "
            f"plan. stdout tail: {stdout[-1500:]!r}"
        )
        # Also assert that the diff doesn't include "to destroy" sequences
        # for any M2 resource.
        assert "destroy" not in stdout.lower().replace(
            "0 to destroy", ""
        ), (
            f"layer=infra (tofu plan): plan contains a destroy. stdout "
            f"tail: {stdout[-1500:]!r}"
        )
        return

    raise AssertionError(
        f"layer=infra (tofu plan): unexpected diff. The kickoff allows "
        f"'No changes' OR the documented OQ-F cosmetic scaling drift "
        f"carry-forward. Got neither. stdout tail: {stdout[-2000:]!r}"
    )
