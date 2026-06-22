# Reference: Delft3D-FM Wave (video + research notes)

These are NATE-provided video/research notes archived 2026-06-22 for the Delft3D
go/no-go (reports/design/engine_spike_delft3d.md). Sibling archive:
reports/references/lecture_aws_swan_making_waves/. Archived verbatim below (light
framing + attribution only); they are the practitioner-facing grounding for the
Delft3D spike, where the crux is that Delft3D-WAVE is SWAN under the hood (so the
SWAN spike already covers waves) and the unique value sits in morphology/sediment
and DELWAQ water-quality.

ASCII only (plain hyphen, no em/en dash, no unicode arrow; use ->).

---

## NATE-provided research (archive verbatim)

[Video 1 - Delft3D-FM Wave tutorial] basic Delft3D-FM Wave model setup; GUI tabs
General/Area/Spectral Domain/Outer/Time Frame/Boundaries; rectangular grid +
bathymetry as .dep (convert from XYZ); wave boundaries = significant wave height,
peak period, direction, directional spreading via constant/time-series/spectral
files; physical processes = 3rd-generation wave models, wind growth, white
capping, refraction, wave breaking (Gamma), bed friction (JONSWAP); output map
intervals + .NC NetCDF; validate then run; visualize with Quickplot (.his/map
files).

[Note 2 - Delft3D suite + cloud] Delft3D = professional open-source 3D suite for
coastal/river/estuarine. Uses: hydrodynamics (2D/3D flow, tides, currents),
morphology (sediment transport/erosion/sedimentation, bathymetric evolution),
coastal protection (breakwaters/living shorelines vs wave action), water quality &
ecology (nutrients/contaminants/algae), dredging/infrastructure impact. Cloud:
containerize via Docker/Singularity (AWS/Azure/HPC); HPC clusters with hundreds of
cores for coupled flow-wave; managed services e.g. Inductiva.AI; workflow =
prepare input locally -> cloud container -> run -> download.
