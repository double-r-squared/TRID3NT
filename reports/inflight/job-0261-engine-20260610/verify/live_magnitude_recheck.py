"""Independent live re-check (verifier's own httpx calls, not the job's test).

1. ?area=TX&status=actual: count features; every feature must carry >=1
   TX-prefixed UGC; count features carrying any non-TX UGC (multi-state
   alerts) honestly.
2. unscoped ?status=actual: count features; count TX-relevant subset.
"""
import sys
sys.path.insert(0, "/home/nate/Documents/GRACE-2/services/agent/src")
import httpx

UA = "grace2-adversarial-verify (natealmanza3@gmail.com)"
H = {"User-Agent": UA, "Accept": "application/geo+json"}

tx = httpx.get("https://api.weather.gov/alerts/active?area=TX&status=actual",
               headers=H, timeout=60).json()
conus = httpx.get("https://api.weather.gov/alerts/active?status=actual",
                  headers=H, timeout=60).json()

tx_feats = tx.get("features") or []
conus_feats = conus.get("features") or []

bad = []
mixed = 0
for f in tx_feats:
    ugc = ((f.get("properties") or {}).get("geocode") or {}).get("UGC") or []
    if not any(str(c).upper().startswith("TX") for c in ugc):
        bad.append((f.get("properties", {}).get("id"), ugc))
    if any(not str(c).upper().startswith("TX") for c in ugc):
        mixed += 1

def is_tx(f):
    ugc = ((f.get("properties") or {}).get("geocode") or {}).get("UGC") or []
    return any(str(c).upper().startswith("TX") for c in ugc)

conus_tx = sum(1 for f in conus_feats if is_tx(f))

print(f"area=TX features: {len(tx_feats)}")
print(f"  features with ZERO TX UGC (leakage): {len(bad)}  {bad[:3]}")
print(f"  features whose UGC also spans non-TX zones (multi-state alerts): {mixed}")
print(f"unscoped CONUS features: {len(conus_feats)}")
print(f"  TX-relevant subset of CONUS sweep: {conus_tx}")
print(f"  would-have-rendered-outside-TX: {len(conus_feats) - conus_tx}")
sys.exit(1 if bad else 0)
