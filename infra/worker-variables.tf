# infra/worker/variables.tf — PyQGIS worker-specific TF inputs.
#
# Worker-scoped variables go here, NOT in infra/variables.tf (job-0018 owned).
# Per the kickoff "File ownership" boundary: this file is created by job-0021
# and any later worker-scoped vars (worker SA name overrides, alt mount paths,
# parallelism knobs the M5 sweep needs) land here.
#
# M2 deliberately ships ZERO new variables: the Job inputs (QGS_URI,
# LAYER_TO_ADD) are passed via `gcloud run jobs execute --args` at invocation
# time, not via TF inputs. Container image is digest-pinned in infra/worker/worker.tf,
# not a variable, so `tofu plan` shows drift when a new build lands without an
# explicit IaC bump (job-0018 r1 lesson).
#
# This file exists so a future job has a single owned home for worker-specific
# vars without re-litigating the layout — and so the empty-variable state is
# explicit, not accidental.
