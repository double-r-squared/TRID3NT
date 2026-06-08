// GRACE-2 web — vector_rendering.ts unit tests (job-0146).
//
// Covers:
//   - Curated VECTOR_PALETTE has exactly 12 distinct colours (Part 1)
//   - paletteColorFor is deterministic per layer_id (Part 1)
//   - paletteColorFor always returns a colour from VECTOR_PALETTE (Part 1)
//   - presetColorFor returns curated colours for all known presets (Part 1)
//   - presetColorFor returns PELICUN_DAMAGE_PRESET sentinel for pelicun_damage (Part 2)
//   - isPelicunDamageLayer returns true only for pelicun_damage (Part 2)
//   - buildDsMeanExpression returns the expected ramp at 3 sample values (Part 2)
//   - POLYGON_FILL_OPACITY constant is 0.4 (Part 3)
//   - POLYGON_STROKE_WIDTH constant is 1.5 (Part 3)
//   - CLUSTER_THRESHOLD constant is 500 (Part 4)
//   - resolveVectorColor neutral-grey for pelicun (not the sentinel) (Part 2)

import { describe, it, expect } from "vitest";
import {
  VECTOR_PALETTE,
  paletteColorFor,
  presetColorFor,
  resolveVectorColor,
  isPelicunDamageLayer,
  buildDsMeanExpression,
  POLYGON_FILL_OPACITY,
  POLYGON_STROKE_WIDTH,
  CLUSTER_THRESHOLD,
  CLUSTER_RADIUS,
  PELICUN_DAMAGE_PRESET,
} from "./vector_rendering";

// ---------------------------------------------------------------------------
// Part 1 — Curated palette
// ---------------------------------------------------------------------------

describe("VECTOR_PALETTE — curated 12-colour palette (job-0146 Part 1)", () => {
  it("has exactly 12 entries", () => {
    expect(VECTOR_PALETTE).toHaveLength(12);
  });

  it("all 12 entries are distinct (no duplicate colours)", () => {
    const unique = new Set(VECTOR_PALETTE);
    expect(unique.size).toBe(12);
  });

  it("all entries are valid #RRGGBB hex strings", () => {
    const hexRe = /^#[0-9A-Fa-f]{6}$/;
    for (const color of VECTOR_PALETTE) {
      expect(color).toMatch(hexRe);
    }
  });
});

describe("paletteColorFor — deterministic FNV-1a hash (job-0146 Part 1)", () => {
  it("returns the same colour on repeated calls for the same layer_id", () => {
    const ids = ["panther", "spoonbill", "alligator", "gbif-layer-123", "very-long-layer-id-XYZ-99"];
    for (const id of ids) {
      expect(paletteColorFor(id)).toBe(paletteColorFor(id));
    }
  });

  it("always returns a colour from VECTOR_PALETTE", () => {
    const ids = ["a", "b", "panther", "spoonbill", "alligator", "wdpa-big-cypress", "flood-depth-demo"];
    for (const id of ids) {
      expect(VECTOR_PALETTE).toContain(paletteColorFor(id));
    }
  });

  it("produces distinct colours for the 3 Case 1 species layers", () => {
    // These 3 IDs must hash to distinct palette slots so the species are
    // visually distinguishable on the map.
    const colors = [
      paletteColorFor("panther-occurrences"),
      paletteColorFor("spoonbill-occurrences"),
      paletteColorFor("alligator-occurrences"),
    ];
    expect(new Set(colors).size).toBe(3);
  });
});

describe("presetColorFor — curated preset registry (job-0146 Part 1)", () => {
  it("maps gbif_occurrences → orange #FF7F0E", () => {
    expect(presetColorFor("gbif_occurrences")).toBe("#FF7F0E");
    expect(presetColorFor("gbif_something")).toBe("#FF7F0E");
  });

  it("maps inaturalist_observations → bright cyan #00BFFF", () => {
    expect(presetColorFor("inaturalist_observations")).toBe("#00BFFF");
    expect(presetColorFor("inat_birds")).toBe("#00BFFF");
  });

  it("maps wdpa variants → slate #708090", () => {
    expect(presetColorFor("wdpa_protected_areas")).toBe("#708090");
    expect(presetColorFor("wdpa_polygon")).toBe("#708090");
    expect(presetColorFor("wdpa")).toBe("#708090");
    expect(presetColorFor("protected_area")).toBe("#708090");
  });

  it("maps nws_alerts variants → fire red #FF4444", () => {
    expect(presetColorFor("nws_alerts")).toBe("#FF4444");
    expect(presetColorFor("nws_alert")).toBe("#FF4444");
    expect(presetColorFor("nws_warning")).toBe("#FF4444");
    expect(presetColorFor("flood_alert")).toBe("#FF4444");
  });

  it("maps mtbs_burn_severity and burn_perimeter → fire red #FF4444", () => {
    expect(presetColorFor("mtbs_burn_severity")).toBe("#FF4444");
    expect(presetColorFor("burn_perimeter")).toBe("#FF4444");
    expect(presetColorFor("mtbs")).toBe("#FF4444");
  });

  it("maps firms_active_fire variants → fire red #FF4444", () => {
    expect(presetColorFor("firms_active_fire")).toBe("#FF4444");
    expect(presetColorFor("firms")).toBe("#FF4444");
    expect(presetColorFor("active_fire")).toBe("#FF4444");
  });

  it("maps osm_roads variants → gold #FFD700", () => {
    expect(presetColorFor("osm_roads")).toBe("#FFD700");
    expect(presetColorFor("osm_road")).toBe("#FFD700");
    expect(presetColorFor("roads")).toBe("#FFD700");
  });

  it("returns PELICUN_DAMAGE_PRESET sentinel for pelicun_damage", () => {
    expect(presetColorFor("pelicun_damage")).toBe(PELICUN_DAMAGE_PRESET);
  });

  it("returns undefined for unknown presets", () => {
    expect(presetColorFor("totally_unknown")).toBeUndefined();
    expect(presetColorFor("species_roseate_spoonbill")).toBeUndefined();
    expect(presetColorFor(null)).toBeUndefined();
    expect(presetColorFor(undefined)).toBeUndefined();
    expect(presetColorFor("")).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Part 2 — Pelicun choropleth
// ---------------------------------------------------------------------------

describe("isPelicunDamageLayer (job-0146 Part 2)", () => {
  it("returns true only for exact 'pelicun_damage' preset", () => {
    expect(isPelicunDamageLayer("pelicun_damage")).toBe(true);
    expect(isPelicunDamageLayer("PELICUN_DAMAGE")).toBe(true); // case-insensitive
  });

  it("returns false for other presets and nullish inputs", () => {
    expect(isPelicunDamageLayer("wdpa_polygon")).toBe(false);
    expect(isPelicunDamageLayer("gbif_occurrences")).toBe(false);
    expect(isPelicunDamageLayer(null)).toBe(false);
    expect(isPelicunDamageLayer(undefined)).toBe(false);
    expect(isPelicunDamageLayer("")).toBe(false);
  });
});

describe("buildDsMeanExpression — green→yellow→red gradient (job-0146 Part 2)", () => {
  it("returns an array starting with 'case'", () => {
    const expr = buildDsMeanExpression();
    expect(Array.isArray(expr)).toBe(true);
    expect(expr[0]).toBe("case");
  });

  it("contains all 3 required gradient stops: green, yellow, red", () => {
    const expr = buildDsMeanExpression();
    const flat = JSON.stringify(expr);
    // Green stop (no damage)
    expect(flat).toContain("#2DC937");
    // Yellow stop (moderate)
    expect(flat).toContain("#E7B416");
    // Red stop (heavy damage)
    expect(flat).toContain("#CC3232");
  });

  it("contains the interpolate expression over ds_mean property", () => {
    const expr = buildDsMeanExpression();
    const flat = JSON.stringify(expr);
    expect(flat).toContain("interpolate");
    expect(flat).toContain("ds_mean");
  });

  it("provides a fallback colour for features without ds_mean", () => {
    const expr = buildDsMeanExpression();
    // The last element in a 'case' expr is the fallback.
    const fallback = expr[expr.length - 1];
    expect(typeof fallback).toBe("string");
    // Should be a valid hex
    expect(fallback as string).toMatch(/^#[0-9A-Fa-f]{6}$/);
  });

  it("maps stop 0.0 → green at sample position in interpolate array", () => {
    const expr = buildDsMeanExpression();
    const flat = JSON.stringify(expr);
    // 0.0 immediately precedes the green hex in the interpolate body
    expect(flat).toContain('0,"#2DC937"');
  });

  it("maps stop 0.5 → yellow at sample position in interpolate array", () => {
    const expr = buildDsMeanExpression();
    const flat = JSON.stringify(expr);
    expect(flat).toContain('0.5,"#E7B416"');
  });

  it("maps stop 1.0 → red at sample position in interpolate array", () => {
    const expr = buildDsMeanExpression();
    const flat = JSON.stringify(expr);
    expect(flat).toContain('1,"#CC3232"');
  });
});

// ---------------------------------------------------------------------------
// Part 3 — Polygon fill opacity + stroke width constants
// ---------------------------------------------------------------------------

describe("Polygon opacity + stroke constants (job-0146 Part 3)", () => {
  it("POLYGON_FILL_OPACITY is 0.4", () => {
    expect(POLYGON_FILL_OPACITY).toBe(0.4);
  });

  it("POLYGON_STROKE_WIDTH is 1.5", () => {
    expect(POLYGON_STROKE_WIDTH).toBe(1.5);
  });
});

// ---------------------------------------------------------------------------
// Part 4 — Cluster constants
// ---------------------------------------------------------------------------

describe("Cluster constants (job-0146 Part 4)", () => {
  it("CLUSTER_THRESHOLD is 500", () => {
    expect(CLUSTER_THRESHOLD).toBe(500);
  });

  it("CLUSTER_RADIUS is 50", () => {
    expect(CLUSTER_RADIUS).toBe(50);
  });
});

// ---------------------------------------------------------------------------
// resolveVectorColor — pelicun sentinel handling
// ---------------------------------------------------------------------------

describe("resolveVectorColor — pelicun fallback (job-0146)", () => {
  it("returns a neutral grey (not the PELICUN_DAMAGE_PRESET string) for pelicun_damage layers", () => {
    const color = resolveVectorColor("pelicun-damage-layer", "pelicun_damage");
    // Must NOT return the raw sentinel string (it's not a valid CSS color)
    expect(color).not.toBe(PELICUN_DAMAGE_PRESET);
    // Must be a valid hex color
    expect(color).toMatch(/^#[0-9A-Fa-f]{6}$/);
  });

  it("prefers preset over palette for non-pelicun presets", () => {
    expect(resolveVectorColor("any-id", "gbif_occurrences")).toBe("#FF7F0E");
    expect(resolveVectorColor("any-id", "osm_roads")).toBe("#FFD700");
    expect(resolveVectorColor("any-id", "wdpa_polygon")).toBe("#708090");
  });

  it("falls back to palette hash when preset is null/undefined", () => {
    const id = "panther-occurrences";
    expect(resolveVectorColor(id, null)).toBe(paletteColorFor(id));
    expect(resolveVectorColor(id, undefined)).toBe(paletteColorFor(id));
  });
});
