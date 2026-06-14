"""Adversarial verification of job-0261 (Fable-5 panel, refute-by-default).

Attacks, offline (no Gemini, no Playwright, no network in this file):

A. Resolver near-misses that must NOT match (typo'd states, cities,
   countries, injection strings, non-str types).
B. Resolver forms that MUST match (codes, names, case, noise prefixes).
C. URL construction: scoped vs unscoped; unscoped must be byte-identical
   to the pre-fix URL; injection can never reach the URL builder.
D. Cache-key collision/separation: TX vs FL vs CONUS distinct;
   "TX"/"tx"/"Texas"/"state of texas" converge on ONE key; no session
   component in the key (cross-session sharing is by design, FR-DC-3).
E. Validator seam: fresh-session validate_function_call("fetch_nws_event")
   must pass; HOT_SET_TOOLS must contain both NWS tools.
F. Gemini declaration seam: `area` param must be declared on
   fetch_nws_alerts_conus (else the LLM can never pass it).
G. Normalizer seam: state/state_code/state_name/location/region -> area;
   probe alias-collision ordering and un-aliased var-kwarg leak paths.

Every FAIL prints and exits non-zero.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/home/nate/Documents/GRACE-2/services/agent/src")
sys.path.insert(
    0, "/home/nate/Documents/GRACE-2/packages/contracts/src"
)

failures: list[str] = []
notes: list[str] = []


def check(cond: bool, label: str) -> None:
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}")
        failures.append(label)


def note(msg: str) -> None:
    print(f"  NOTE  {msg}")
    notes.append(msg)


# --------------------------------------------------------------------- A/B
print("\n[A] resolver near-misses must resolve to None")
from grace2_agent.tools.us_states import (
    NWS_AREA_CODES,
    resolve_state_code,
    state_display_name,
)

MUST_NOT_MATCH = [
    "Texa", "Texass", "Texass", "TXX", "T X", "Tex", "tejas",
    "Mexico", "Canada", "England",
    "Austin", "Austin, TX", "Dallas", "Travis County", "Houston TX",
    "US", "USA", "CONUS", "United States", "America",
    "Washington state",            # trailing-noise form is NOT handled
    "Téxas",                  # unicode near-miss
    "TX&status=test",              # URL injection attempt
    "TX;DROP", "TX%20OK", "TX,OK",  # multi-state / injection forms
    "tx.", "TX.",                   # trailing punctuation
    "12071",                        # county FIPS is not a state for CONUS tool
    "XK", "ZZ", "UK",               # 2-letter codes NOT in NWS set
    "new mexico city",
]
for s in MUST_NOT_MATCH:
    check(resolve_state_code(s) is None, f"resolve_state_code({s!r}) is None")

check(resolve_state_code(None) is None, "resolve_state_code(None) is None (no raise)")  # type: ignore[arg-type]
check(resolve_state_code(42) is None, "resolve_state_code(42) is None (no raise)")  # type: ignore[arg-type]
check(resolve_state_code(("TX",)) is None, "resolve_state_code(('TX',)) is None")  # type: ignore[arg-type]

print("\n[B] forms that MUST resolve")
MUST_MATCH = {
    "TX": "TX", "tx": "TX", " tx ": "TX", "tX": "TX",
    "Texas": "TX", "texas": "TX", "TEXAS": "TX", "  texas  ": "TX",
    "state of texas": "TX", "State of Texas": "TX",
    "the state of texas": "TX", "The State Of Texas": "TX",
    "new   mexico": "NM", "New Mexico": "NM",
    "district of columbia": "DC", "washington dc": "DC",
    "washington d.c.": "DC", "washington": "WA",
    "Puerto Rico": "PR", "puerto rico": "PR",
    "GM": "GM",  # marine zone passes through
    "la": "LA", "in": "IN", "or": "OR", "me": "ME",  # word-collision codes
    "tx\n": "TX", "\tTexas\t": "TX",
}
for s, want in MUST_MATCH.items():
    got = resolve_state_code(s)
    check(got == want, f"resolve_state_code({s!r}) == {want!r} (got {got!r})")

check(state_display_name("TX") == "Texas", 'state_display_name("TX") == "Texas"')
check(state_display_name("GM") == "GM", "marine zone echoes code")

# Collision audit: every name maps to a code in NWS_AREA_CODES; codes are
# strictly [A-Z]{2} so nothing resolvable can mangle a URL.
from grace2_agent.tools.us_states import STATE_NAME_TO_CODE
import re as _re
check(
    all(c in NWS_AREA_CODES for c in STATE_NAME_TO_CODE.values()),
    "every name-mapped code is a valid NWS area code",
)
check(
    all(_re.fullmatch(r"[A-Z]{2}", c) for c in NWS_AREA_CODES),
    "all NWS area codes are exactly [A-Z]{2} (URL-safe by construction)",
)

# --------------------------------------------------------------------- C
print("\n[C] URL construction + injection cannot reach the URL builder")
from grace2_agent.tools.fetch_nws_alerts_conus import (
    NWSConusInputError,
    _build_nws_conus_url,
    _resolve_area_or_raise,
)

check(
    _build_nws_conus_url("actual") == "https://api.weather.gov/alerts/active?status=actual",
    "unscoped URL byte-identical to pre-fix form",
)
check(
    _build_nws_conus_url("actual", "TX")
    == "https://api.weather.gov/alerts/active?area=TX&status=actual",
    "scoped URL is ?area=TX&status=actual",
)

for bad in ["TX&status=test", "Austin", "CONUS", "Texa", "12071", "Téxas"]:
    try:
        _resolve_area_or_raise(bad)
        check(False, f"_resolve_area_or_raise({bad!r}) raises NWSConusInputError")
    except NWSConusInputError:
        check(True, f"_resolve_area_or_raise({bad!r}) raises NWSConusInputError")

for bad_type in [True, 5, 1.5, ["TX"], {"a": 1}, ("TX",)]:
    try:
        _resolve_area_or_raise(bad_type)  # type: ignore[arg-type]
        check(False, f"_resolve_area_or_raise({bad_type!r}) raises (non-str)")
    except NWSConusInputError:
        check(True, f"_resolve_area_or_raise({bad_type!r}) raises (non-str)")

check(_resolve_area_or_raise(None) is None, "None -> unscoped (documented)")
check(_resolve_area_or_raise("") is None, "'' -> unscoped (documented)")
got_ws = _resolve_area_or_raise("   ")
check(got_ws is None, "'   ' -> unscoped")
note("whitespace-only area silently falls back to NATIONWIDE sweep "
     "(documented as empty==None; an LLM passing '  ' would re-spill — "
     "improbable input, fail-open edge)")

# fetch_nws_event canonicalizer: full names now resolve; near-misses raise.
from grace2_agent.tools.fetch_nws_event import NWSInputError, _canonicalize_area

check(
    _canonicalize_area("Texas") == {"kind": "state", "value": "TX"},
    'fetch_nws_event _canonicalize_area("Texas") -> state TX',
)
check(
    _canonicalize_area("state of texas") == {"kind": "state", "value": "TX"},
    '"state of texas" -> state TX',
)
check(
    _canonicalize_area("12071") == {"kind": "fips", "value": "12071"},
    "FIPS path untouched",
)
bbox_res = _canonicalize_area((-98.0, 29.0, -97.0, 30.0))
check(bbox_res["kind"] == "point", "bbox -> point fallback untouched")
for bad in ["Texa", "Austin", "Mexico", "TX&x=1"]:
    try:
        _canonicalize_area(bad)
        check(False, f"_canonicalize_area({bad!r}) raises NWSInputError")
    except NWSInputError:
        check(True, f"_canonicalize_area({bad!r}) raises NWSInputError")

# --------------------------------------------------------------------- D
print("\n[D] cache-key separation / convergence / no session component")
from grace2_agent.tools.cache import compute_cache_key
from datetime import datetime, timezone

NOW = datetime(2026, 6, 10, 12, 30, tzinfo=timezone.utc)


def key_for(area: str | None) -> str:
    code = _resolve_area_or_raise(area)
    params = {"status": "actual", "event_types": None, "area": code}
    return compute_cache_key("nws_alerts_conus", params, "dynamic-1h", now=NOW)


k_tx, k_tx2, k_texas, k_sot = (
    key_for("TX"), key_for("tx"), key_for("Texas"), key_for("state of texas")
)
k_fl, k_conus = key_for("FL"), key_for(None)
check(len({k_tx, k_tx2, k_texas, k_sot}) == 1, "TX/tx/Texas/'state of texas' -> ONE key")
check(k_tx != k_fl, "TX key != FL key")
check(k_tx != k_conus, "TX key != CONUS key")
check(k_fl != k_conus, "FL key != CONUS key")
# Near-miss key adjacency is impossible by construction (32-hex sha256
# prefix), but assert the obvious: distinct params -> distinct keys.
check(
    key_for("OK") not in {k_tx, k_fl, k_conus},
    "OK key collides with nothing",
)
note("cache key = sha256(source||params||hour-vintage)[:32]; no session_id "
     "in the key — cross-session sharing of identical queries is BY DESIGN "
     "(FR-DC-3) and cannot leak: artifact content is purely param-determined")

# --------------------------------------------------------------------- E
print("\n[E] validator seam — the exact live-demo failure must be dead")
from grace2_agent.categories import (
    HOT_SET_TOOLS,
    AllowedToolSet,
    OutOfAllowedSetError,
    validate_function_call,
)

check("fetch_nws_event" in HOT_SET_TOOLS, "fetch_nws_event in HOT_SET_TOOLS")
check("fetch_nws_alerts_conus" in HOT_SET_TOOLS, "fetch_nws_alerts_conus in HOT_SET_TOOLS")
check(len(HOT_SET_TOOLS) == 10, f"HOT_SET_TOOLS == 10 (got {len(HOT_SET_TOOLS)})")

fresh = AllowedToolSet()  # fresh session, no categories opened
try:
    validate_function_call("fetch_nws_event", fresh)
    check(True, "fresh-session validate_function_call('fetch_nws_event') passes")
except OutOfAllowedSetError:
    check(False, "fresh-session validate_function_call('fetch_nws_event') passes")
try:
    validate_function_call("fetch_mrms_qpe", fresh)
    check(False, "validator still rejects non-hot-set tools (control)")
except OutOfAllowedSetError:
    check(True, "validator still rejects non-hot-set tools (control)")

# --------------------------------------------------------------------- F
print("\n[F] Gemini declaration seam — area must be declared")
import grace2_agent.tools  # noqa: F401  (registers everything)
from grace2_agent.tools import TOOL_REGISTRY
from grace2_agent.adapter import build_tool_declarations

decls = build_tool_declarations(TOOL_REGISTRY)
by_name = {}
for item in decls:
    fds = getattr(item, "function_declarations", None) or [item]
    for fd in fds:
        by_name[fd.name] = fd
conus = by_name.get("fetch_nws_alerts_conus")
check(conus is not None, "fetch_nws_alerts_conus declared")
if conus is not None:
    props = set((conus.parameters.properties or {}).keys())
    check("area" in props, f"area in declared params (got {sorted(props)})")
    desc = conus.description or ""
    check("area=" in desc or "area" in desc, "description teaches area usage")
ev = by_name.get("fetch_nws_event")
check(ev is not None, "fetch_nws_event declared")
if ev is not None:
    check(
        "full state name" in (ev.description or "").lower()
        or "florida" in (ev.description or "").lower(),
        "fetch_nws_event description teaches full state names",
    )

# --------------------------------------------------------------------- G
print("\n[G] normalizer seam — aliases land on area; collision probes")
from grace2_agent.tool_arg_normalizer import normalize_args
from grace2_agent.tools.fetch_nws_alerts_conus import fetch_nws_alerts_conus
from grace2_agent.tools.fetch_nws_event import fetch_nws_event

for alias in ["state", "state_code", "state_name", "location", "region"]:
    out = normalize_args("fetch_nws_alerts_conus", {alias: "Texas"}, fetch_nws_alerts_conus)
    check(out.get("area") == "Texas", f"conus alias {alias} -> area")
for alias in ["state", "fips", "county_fips", "location"]:
    out = normalize_args("fetch_nws_event", {alias: "TX"}, fetch_nws_event)
    check(out.get("area") == "TX", f"event alias {alias} -> area")

# camelCase forms
out = normalize_args("fetch_nws_alerts_conus", {"stateCode": "TX"}, fetch_nws_alerts_conus)
check(out.get("area") == "TX", "camelCase stateCode -> area")

# Collision: BOTH area and state supplied, alias first in dict order.
out = normalize_args(
    "fetch_nws_alerts_conus", {"state": "Florida", "area": "TX"}, fetch_nws_alerts_conus
)
if out.get("area") == "TX":
    check(True, "explicit area wins over alias on collision")
else:
    note(f"alias-ordering collision: {{state:'Florida', area:'TX'}} -> {out} "
         "(first-key wins, not canonical-wins; both values still STATE-SCOPED "
         "so no nationwide spill — mislabeled state at worst)")

# Un-aliased var-kwarg leak probe: area_code is NOT in the alias map and the
# tool declares **_extra_ignored, so it passes through and is IGNORED ->
# silent fallback to the nationwide sweep.
out = normalize_args(
    "fetch_nws_alerts_conus", {"area_code": "TX"}, fetch_nws_alerts_conus
)
if out.get("area") == "TX":
    check(True, "area_code aliased")
else:
    note(f"residual leak path: {{area_code:'TX'}} -> {out}; 'area_code'/'us_state' "
         "are not aliased and **_extra_ignored absorbs them silently -> "
         "unscoped CONUS sweep. Mitigated by the declaration (Gemini sees "
         "'area') but not closed.")

# ------------------------------------------------------------------ summary
print(f"\n{'='*70}")
print(f"FAILURES: {len(failures)}")
for f in failures:
    print(f"  - {f}")
print(f"NOTES (non-blocking): {len(notes)}")
for n in notes:
    print(f"  - {n}")
sys.exit(1 if failures else 0)
