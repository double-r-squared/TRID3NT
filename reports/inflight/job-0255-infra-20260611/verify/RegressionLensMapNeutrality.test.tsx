// REGRESSION-LENS independent probe (panel Stage-2). Proves applyQgisProxy +
// buildWmsTileUrl are byte-identical when VITE_QGIS_PROXY_BASE is unset (dev).
import { describe, it, expect, vi, beforeEach } from "vitest";

describe("REGRESSION: QGIS proxy URL builder neutrality (env unset)", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllEnvs();
  });

  it("applyQgisProxy is identity for many URL shapes when proxy base absent", async () => {
    vi.stubEnv("VITE_QGIS_PROXY_BASE", "");
    const { applyQgisProxy } = await import("./Map");
    const urls = [
      "https://qgis.run.app/ogc/wms?MAP=x&LAYERS=y&BBOX=1,2,3,4",
      "https://qgis.run.app/ogc/wms",
      "http://localhost:8080/wms?SERVICE=WMS&REQUEST=GetMap",
      "https://q/ogc/wms?A=1&B=&C=%20space",
    ];
    for (const u of urls) expect(applyQgisProxy(u)).toBe(u);
  });

  it("buildWmsTileUrl output is unchanged by the proxy seam when base absent", async () => {
    vi.stubEnv("VITE_QGIS_PROXY_BASE", "");
    const { buildWmsTileUrl, applyQgisProxy } = await import("./Map");
    const tile = buildWmsTileUrl("flood-layer");
    // the seam wraps but must not mutate when env is unset
    expect(applyQgisProxy(tile)).toBe(tile);
    // and the built URL must still point at a WMS endpoint (not the proxy)
    expect(tile).not.toContain("/qgis-proxy");
  });
});
