# variables.tf — input variables for the SFINCS Batch module.
#
# All defaults match the hand-provisioned AWS prod environment documented in
# reports/PROJECT_STATE.md § Environment facts (account 226996537797,
# us-west-2). NATE can override any variable in a tfvars file without changing
# this file.

variable "region" {
  type        = string
  description = "AWS region for all resources."
  default     = "us-west-2"
}

variable "vpc_id" {
  type        = string
  description = "ID of the default VPC containing the public subnets. Hand-provisioned."
  default     = "vpc-01b7ce297bb3a95e9"
}

variable "subnet_ids" {
  type        = list(string)
  description = <<-EOT
    List of public subnet IDs for the Batch compute environment. All four AZs
    in us-west-2 are included so Batch can source SPOT capacity from the
    widest pool. All subnets have auto-assign public IP enabled, so Batch
    container instances reach ECR and S3 without a NAT gateway.
  EOT
  default = [
    "subnet-0e29172e519406a62", # us-west-2a
    "subnet-044d994d6b2802eef", # us-west-2b
    "subnet-07923f5400023e0d0", # us-west-2c
    "subnet-0729ae4a53715e567", # us-west-2d
  ]
}

variable "runs_bucket" {
  type        = string
  description = "Name of the S3 bucket where solver run outputs and completion manifests are written."
  default     = "grace2-hazard-runs-226996537797"
}

variable "cache_bucket" {
  type        = string
  description = "Name of the S3 bucket holding the staged SFINCS setup decks + manifest.json the solver container READS at run start. The task role needs read-only access to it; the agent stages inputs here before submitting the Batch job."
  default     = "grace2-hazard-cache-226996537797"
}

variable "agent_role_name" {
  type        = string
  description = "Name of the existing IAM role attached to the agent EC2 instance (hand-provisioned, not recreated here)."
  default     = "grace2-agent-ec2"
}

variable "ecr_repo_name" {
  type        = string
  description = "Name for the ECR repository that will hold the SFINCS worker image."
  default     = "grace2-sfincs"
}

variable "swmm_ecr_repo_name" {
  type        = string
  description = "Name for the ECR repository that will hold the SWMM (pyswmm) worker image (sprint-16 P7 — SWMM is the first non-SFINCS Batch user; its own image lives in its own repo)."
  default     = "grace2-swmm"
}

variable "max_vcpus" {
  type        = number
  description = "Maximum aggregate vCPUs the Batch compute environment may use across all running jobs. Scale this up for larger parallel workloads."
  # Bumped 64 -> 96 so the compute environment can launch the higher-powered
  # "xlarge" compute_class (48 vCPU / 96 GiB) the agent now auto-selects for a
  # big AOI/mesh (auto vertical scaling per case, NATE 2026-06-17). 96 fits one
  # xlarge job on a single c7i.12xlarge (48 vCPU) with headroom, or two
  # standard (8 vCPU) + one large (16 vCPU) concurrently. NOT applied — authored
  # for NATE to `tofu apply`.
  default = 96
}

variable "spot_bid_percentage" {
  type        = number
  description = "Maximum percentage of On-Demand price that Batch will bid for SPOT instances (1-100). 100 means Batch bids up to On-Demand price, maximising availability while staying cost-neutral vs On-Demand."
  default     = 100
}

variable "instance_types" {
  type        = list(string)
  description = <<-EOT
    EC2 instance types eligible for the SPOT compute environment. "optimal" lets
    Batch pick the best available SPOT instance from the c4/c5/m4/m5 families
    automatically. To lock to a specific family (e.g. for predictable NUMA
    topology with SFINCS OpenMP), specify explicit types such as
    ["c7i.2xlarge", "c7i.4xlarge", "c7i.8xlarge"]. x86_64 only — the SFINCS
    binary in deltares/sfincs-cpu is compiled for amd64.
  EOT
  # Auto vertical scaling per case (NATE 2026-06-17): the agent selects a
  # compute_class from the AOI/mesh element count (small 4 vCPU -> standard 8 ->
  # large 16 -> the xlarge 48 vCPU / 96 GiB).
  #
  # SPOT-CAPACITY BROADENING (TRACK SPOT 2026-06-23): a long SFINCS run sat
  # RUNNABLE 70 min because no 16-vCPU c7i SPOT capacity was available in any AZ.
  # SPOT_CAPACITY_OPTIMIZED only helps when there are MANY pools to choose from;
  # a single family (c7i) is a single pool per (size, AZ). We now span FIVE
  # x86_64 compute/general-purpose families across the same vCPU ladder so each
  # compute_class can be placed from many independent SPOT pools (family x size x
  # AZ), drastically lowering "no capacity" odds. All are SPOT-eligible in
  # us-west-2 and amd64 (the deltares/sfincs-cpu + pyswmm images are amd64):
  #
  #   4 vCPU  ("small"):   c7i.xlarge   c6i.xlarge   c5.xlarge   m7i.xlarge   r7i.xlarge
  #   8 vCPU  ("standard"): c7i.2xlarge c6i.2xlarge  c5.2xlarge  m7i.2xlarge  r7i.2xlarge
  #   16 vCPU ("large"):    c7i.4xlarge c6i.4xlarge  c5.4xlarge  m7i.4xlarge  r7i.4xlarge
  #   48 vCPU ("xlarge"):   c7i.12xlarge c6i.12xlarge c5.12xlarge(48) m7i.12xlarge r7i.12xlarge
  #
  # Batch matches each job's resourceRequirements (vCPU/MEMORY) against this pool
  # and picks the cheapest-with-most-capacity instance that fits; the r7i (high
  # memory) and m7i (balanced) members only get selected when c-family capacity is
  # scarce, so steady-state cost stays compute-family-low. NOT applied — authored
  # for NATE to `tofu apply`.
  default = [
    # 4 vCPU tier (compute_class "small")
    "c7i.xlarge", "c6i.xlarge", "c5.xlarge", "m7i.xlarge", "r7i.xlarge",
    # 8 vCPU tier (compute_class "standard")
    "c7i.2xlarge", "c6i.2xlarge", "c5.2xlarge", "m7i.2xlarge", "r7i.2xlarge",
    # 16 vCPU tier (compute_class "large" — the long-SFINCS class that starved)
    "c7i.4xlarge", "c6i.4xlarge", "c5.4xlarge", "m7i.4xlarge", "r7i.4xlarge",
    # 48 vCPU tier (compute_class "xlarge")
    "c7i.12xlarge", "c6i.12xlarge", "c5.12xlarge", "m7i.12xlarge", "r7i.12xlarge",
  ]
}

variable "ondemand_instance_types" {
  type        = list(string)
  description = <<-EOT
    EC2 instance types eligible for the ON-DEMAND fallback compute environment.
    Same families/sizes as the SPOT pool so a job that cannot be placed on (or
    keeps getting reclaimed from) SPOT lands on an identically-sized on-demand
    box. x86_64 only.
  EOT
  # Mirrors var.instance_types exactly: the on-demand fallback must be able to
  # place every compute_class the SPOT CE can. Kept as a SEPARATE variable so an
  # operator can, if ever needed, narrow the (more expensive) on-demand pool
  # without touching the SPOT pool. NOT applied — authored for NATE to apply.
  default = [
    "c7i.xlarge", "c6i.xlarge", "c5.xlarge", "m7i.xlarge", "r7i.xlarge",
    "c7i.2xlarge", "c6i.2xlarge", "c5.2xlarge", "m7i.2xlarge", "r7i.2xlarge",
    "c7i.4xlarge", "c6i.4xlarge", "c5.4xlarge", "m7i.4xlarge", "r7i.4xlarge",
    "c7i.12xlarge", "c6i.12xlarge", "c5.12xlarge", "m7i.12xlarge", "r7i.12xlarge",
  ]
}

variable "ondemand_max_vcpus" {
  type        = number
  description = <<-EOT
    Maximum aggregate vCPUs the ON-DEMAND fallback compute environment may use.
    Sized to complete ONE largest-class job (48 vCPU "xlarge") plus headroom,
    NOT to mirror the full SPOT max — on-demand is the safety net for runs that
    must finish when SPOT cannot place them, not a parallel-throughput tier.
    Capping it bounds the worst-case on-demand bill if SPOT capacity is scarce
    for an extended window.
  EOT
  # 64 vCPU: fits one xlarge (48 vCPU) job with room, or a large (16) + standard
  # (8) + small (4) concurrently. Lower than the 96-vCPU SPOT max on purpose —
  # on-demand is fallback-only, so it should not be able to fan out as wide as
  # the cheap SPOT tier. NOT applied — authored for NATE to `tofu apply`.
  default = 64
}
