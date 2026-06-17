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

variable "max_vcpus" {
  type        = number
  description = "Maximum aggregate vCPUs the Batch compute environment may use across all running jobs. Scale this up for larger parallel workloads."
  default     = 64
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
  default     = ["optimal"]
}
