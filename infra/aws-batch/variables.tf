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
    EC2 instance types eligible for the compute environment. "optimal" lets
    Batch pick the best available SPOT instance from the c4/c5/m4/m5 families
    automatically. To lock to a specific family (e.g. for predictable NUMA
    topology with SFINCS OpenMP), specify explicit types such as
    ["c7i.2xlarge", "c7i.4xlarge", "c7i.8xlarge"]. x86_64 only — the SFINCS
    binary in deltares/sfincs-cpu is compiled for amd64.
  EOT
  # Auto vertical scaling per case (NATE 2026-06-17): the agent now selects a
  # compute_class from the AOI/mesh element count (small 4 vCPU -> standard 8 ->
  # large 16 -> the new xlarge 48 vCPU / 96 GiB). The default is locked to the
  # c7i family across the FULL vCPU ladder so Batch can place each class on a
  # single right-sized box (predictable NUMA topology for the SFINCS/SWMM
  # OpenMP solve): c7i.xlarge (4) / 2xlarge (8) / 4xlarge (16) / 12xlarge (48,
  # the xlarge tier). All x86_64, all SPOT-eligible in us-west-2. NOT applied —
  # authored for NATE to `tofu apply`.
  default = ["c7i.xlarge", "c7i.2xlarge", "c7i.4xlarge", "c7i.12xlarge"]
}
