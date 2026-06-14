"""Independent CORRECTNESS verification of Case 2 extraction (no Gemini, no DI trust)."""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path("/home/nate/Documents/GRACE-2")
sys.path.insert(0, str(REPO / "services" / "agent" / "src"))

from grace2_agent.workflows.model_groundwater_contamination_scenario import (
    extract_spill_parameters, LITERS_PER_GALLON, _best_location,
)
from grace2_agent.tools.aggregate_claims_across_sources import _extract_locations, _extract_scale

FIXTURE = REPO / "services/agent/tests/fixtures/case2_news_article.txt"
text = FIXTURE.read_text()

print("=== 1. RAW location hits from aggregator ===")
loc_hits = _extract_locations(text)
for h in loc_hits:
    print(f"  raw={h[0]!r} norm={h[1]!r}")
print(f"  -> _best_location picks: {_best_location(loc_hits)!r}")

print("\n=== 2. RAW scale hits ===")
for raw, sc in _extract_scale(text):
    print(f"  raw={raw!r} value={sc['value']} unit={sc['unit']!r}")

print("\n=== 3. Extraction WITHOUT geocode (pure math) ===")
d = extract_spill_parameters(text, geocode=False)
for k, v in d.items():
    print(f"  {k}: {v}")

print("\n=== 4. HAND-CHECK the math ===")
# gallons -> kg
gal = 12000.0
density = 1.46  # TCE kg/L
expected_mass = gal * LITERS_PER_GALLON * density
print(f"  12000 gal * {LITERS_PER_GALLON} L/gal * {density} kg/L = {expected_mass}")
print(f"    composer total_mass_kg = {d['total_mass_kg']}  MATCH={abs(expected_mass-d['total_mass_kg'])<1e-6}")
# hours -> days
expected_dur = 6.0/24.0
print(f"  6 hours / 24 = {expected_dur} days")
print(f"    composer duration_days = {d['duration_days']}  MATCH={abs(expected_dur-d['duration_days'])<1e-12}")
# rate
expected_rate = expected_mass / (expected_dur * 86400.0)
print(f"  rate = {expected_mass} / ({expected_dur} d * 86400 s/d) = {expected_rate} kg/s")
print(f"    composer release_rate_kg_s = {d['release_rate_kg_s']}  MATCH={abs(expected_rate-d['release_rate_kg_s'])<1e-9}")

print("\n=== 5. mass-balance INVARIANT: rate * duration_seconds == total_mass ===")
recovered_mass = d['release_rate_kg_s'] * d['duration_days'] * 86400.0
print(f"  rate*dur_s = {recovered_mass}  vs total_mass = {d['total_mass_kg']}  MATCH={abs(recovered_mass-d['total_mass_kg'])<1e-6}")

print("\n=== 6. clamp band membership ===")
print(f"  release_rate {d['release_rate_kg_s']} in [1e-6,100]? {1e-6 <= d['release_rate_kg_s'] <= 100}")
print(f"  duration {d['duration_days']} in [0.1,3650]? {0.1 <= d['duration_days'] <= 3650}")
print(f"  clamps_applied = {d['clamps_applied']} (expect empty for this fixture)")
