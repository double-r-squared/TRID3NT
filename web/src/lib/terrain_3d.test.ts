// GRACE-2 web - lib/terrain_3d.ts unit tests ("3D terrain viz" first cut).
//
// Covers the PURE core:
//   - persistence helpers (3D + contour flags) default OFF, write-through.
//   - buildTerrainDemSource: TiTiler terrain-RGB primary vs AWS Terrarium
//     fallback, with the correct encoding per origin.
//   - applyTerrain3d / removeTerrain3d against a tiny structural Map stub:
//     adds the DEM source + hillshade + sky + setTerrain + unlocks pitch on
//     enable; tears it all down + re-locks 2D on remove; idempotent + defensive.

import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  LS_TERRAIN_3D,
  LS_CONTOURS,
  readTerrain3dEnabled,
  writeTerrain3dEnabled,
  readContoursEnabled,
  writeContoursEnabled,
  buildTerrainDemSource,
  applyTerrain3d,
  removeTerrain3d,
  AWS_TERRAIN_TERRARIUM_TEMPLATE,
  TERRAIN_DEM_SOURCE_ID,
  TERRAIN_HILLSHADE_LAYER_ID,
  TERRAIN_SKY_LAYER_ID,
  TERRAIN_EXAGGERATION,
  type TerrainMapLike,
  type TerrainDemSourceSpec,
} from "./terrain_3d";

describe("terrain_3d - persistence", () => {
  beforeEach(() => localStorage.clear());

  it("3D-terrain flag defaults OFF (absent value)", () => {
    expect(readTerrain3dEnabled()).toBe(false);
  });

  it("3D-terrain flag writes through and reads back ON", () => {
    writeTerrain3dEnabled(true);
    expect(localStorage.getItem(LS_TERRAIN_3D)).toBe("true");
    expect(readTerrain3dEnabled()).toBe(true);
    writeTerrain3dEnabled(false);
    expect(readTerrain3dEnabled()).toBe(false);
  });

  it("only the explicit string 'true' enables 3D (garbage reads OFF)", () => {
    localStorage.setItem(LS_TERRAIN_3D, "yes");
    expect(readTerrain3dEnabled()).toBe(false);
  });

  it("contour flag defaults OFF and writes through", () => {
    expect(readContoursEnabled()).toBe(false);
    writeContoursEnabled(true);
    expect(localStorage.getItem(LS_CONTOURS)).toBe("true");
    expect(readContoursEnabled()).toBe(true);
  });
});

describe("terrain_3d - buildTerrainDemSource", () => {
  it("falls back to AWS Terrarium when no DEM COG url is given", () => {
    const src = buildTerrainDemSource({ publicBase: "https://edge.example" });
    expect(src.origin).toBe("aws-terrarium");
    expect(src.type).toBe("raster-dem");
    expect(src.encoding).toBe("terrarium");
    expect(src.tiles[0]).toBe(AWS_TERRAIN_TERRARIUM_TEMPLATE);
  });

  it("falls back to AWS Terrarium when there is no public edge base", () => {
    const src = buildTerrainDemSource({
      publicBase: null,
      demCogUrl: "s3://bucket/dem.tif",
    });
    expect(src.origin).toBe("aws-terrarium");
    expect(src.encoding).toBe("terrarium");
  });

  it("builds a TiTiler terrain-RGB source when BOTH base + DEM COG present", () => {
    const src = buildTerrainDemSource({
      publicBase: "https://edge.example",
      demCogUrl: "s3://bucket/dem.tif",
    });
    expect(src.origin).toBe("titiler");
    expect(src.type).toBe("raster-dem");
    // Mapbox terrain-RGB encoding for the TiTiler path.
    expect(src.encoding).toBe("mapbox");
    const tpl = src.tiles[0];
    expect(tpl).toContain("https://edge.example/cog/tiles/{z}/{x}/{y}.png");
    // The DEM COG url is URL-encoded into ?url=.
    expect(tpl).toContain(`url=${encodeURIComponent("s3://bucket/dem.tif")}`);
    expect(tpl).toContain("colormap_name=terrainrgb");
  });
});

// --- a tiny structural Map stub for the side-effect helpers -------------- //

function makeMapStub() {
  const sources = new Set<string>();
  const layers = new Set<string>();
  const m = {
    addSource: vi.fn((id: string) => sources.add(id)),
    removeSource: vi.fn((id: string) => sources.delete(id)),
    getSource: vi.fn((id: string) => (sources.has(id) ? {} : undefined)),
    addLayer: vi.fn((l: { id: string }) => layers.add(l.id)),
    removeLayer: vi.fn((id: string) => layers.delete(id)),
    getLayer: vi.fn((id: string) => (layers.has(id) ? {} : undefined)),
    setTerrain: vi.fn(),
    setMaxPitch: vi.fn(),
    dragRotate: { enable: vi.fn(), disable: vi.fn() },
    touchZoomRotate: { enableRotation: vi.fn(), disableRotation: vi.fn() },
    touchPitch: { enable: vi.fn(), disable: vi.fn() },
  };
  return { m: m as unknown as TerrainMapLike, raw: m, sources, layers };
}

describe("terrain_3d - applyTerrain3d", () => {
  it("adds DEM source + hillshade + sky, sets terrain, unlocks pitch/rotate", () => {
    const { m, raw, sources, layers } = makeMapStub();
    const origin = applyTerrain3d(m);
    expect(origin).toBe("aws-terrarium"); // no public base / DEM COG in test env

    expect(sources.has(TERRAIN_DEM_SOURCE_ID)).toBe(true);
    expect(layers.has(TERRAIN_HILLSHADE_LAYER_ID)).toBe(true);
    expect(layers.has(TERRAIN_SKY_LAYER_ID)).toBe(true);

    expect(raw.setTerrain).toHaveBeenCalledWith({
      source: TERRAIN_DEM_SOURCE_ID,
      exaggeration: TERRAIN_EXAGGERATION,
    });
    // Camera unlocked for 3D.
    expect(raw.setMaxPitch).toHaveBeenCalledWith(75);
    expect(raw.dragRotate.enable).toHaveBeenCalled();
    expect(raw.touchZoomRotate.enableRotation).toHaveBeenCalled();
    expect(raw.touchPitch.enable).toHaveBeenCalled();
  });

  it("is idempotent - a second apply does not re-add the source/layers", () => {
    const { m, raw } = makeMapStub();
    applyTerrain3d(m);
    applyTerrain3d(m);
    expect(raw.addSource).toHaveBeenCalledTimes(1);
    // hillshade + sky added once each.
    expect(raw.addLayer).toHaveBeenCalledTimes(2);
  });

  it("honors a supplied TiTiler DEM source (origin reported)", () => {
    const { m } = makeMapStub();
    const titiler: TerrainDemSourceSpec = {
      type: "raster-dem",
      tiles: ["https://edge/cog/tiles/{z}/{x}/{y}.png?url=x&colormap_name=terrainrgb"],
      tileSize: 256,
      encoding: "mapbox",
      maxzoom: 18,
      attribution: "x",
      origin: "titiler",
    };
    expect(applyTerrain3d(m, { demSource: titiler })).toBe("titiler");
  });

  it("logs a TODO (does not throw) when contours are requested", () => {
    const { m } = makeMapStub();
    const info = vi.spyOn(console, "info").mockImplementation(() => {});
    expect(() => applyTerrain3d(m, { contoursRequested: true })).not.toThrow();
    expect(info).toHaveBeenCalled();
    expect(String(info.mock.calls[0]?.[0])).toContain("maplibre-contour");
    info.mockRestore();
  });
});

describe("terrain_3d - removeTerrain3d", () => {
  it("setTerrain(null), drops layers + source, re-locks 2D camera", () => {
    const { m, raw, sources, layers } = makeMapStub();
    applyTerrain3d(m);
    raw.setMaxPitch.mockClear();

    removeTerrain3d(m);
    expect(raw.setTerrain).toHaveBeenLastCalledWith(null);
    expect(sources.has(TERRAIN_DEM_SOURCE_ID)).toBe(false);
    expect(layers.has(TERRAIN_HILLSHADE_LAYER_ID)).toBe(false);
    expect(layers.has(TERRAIN_SKY_LAYER_ID)).toBe(false);
    // Re-locked to flat 2D.
    expect(raw.setMaxPitch).toHaveBeenCalledWith(0);
    expect(raw.dragRotate.disable).toHaveBeenCalled();
    expect(raw.touchZoomRotate.disableRotation).toHaveBeenCalled();
    expect(raw.touchPitch.disable).toHaveBeenCalled();
  });

  it("is safe to call when terrain was never enabled (no throw)", () => {
    const { m, raw } = makeMapStub();
    expect(() => removeTerrain3d(m)).not.toThrow();
    expect(raw.setTerrain).toHaveBeenCalledWith(null);
  });
});
