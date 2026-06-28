// GRACE-2 web - building footprint click-to-enrich tests (NATE 2026-06-27).
//
// Covers the PURE half of the slim-footprint enrich path:
//   1. buildFeaturePopupData on SLIM id-only props -> no osm_id/osm_type/fid rows.
//   2. mergeTagsIntoAttributes -> merges humanized tags, promotes a fallback
//      title to the tag `name`, is idempotent, and never duplicates a row.
//   3. FeaturePopup renders a "Loading details..." row when `enriching` is set
//      and hides it once tags merged (enriching:false).
//   4. NON-footprint popups (no enriching flag) are byte-for-byte unchanged
//      (no loading row, "No additional attributes" empty still renders).

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { buildFeaturePopupData, mergeTagsIntoAttributes } from "./Map";
import { FeaturePopup, type FeaturePopupData } from "./components/FeaturePopup";

const PT = { x: 10, y: 10 };
const CANVAS = { width: 400, height: 400 };

describe("buildFeaturePopupData on slim footprint props", () => {
  it("omits the id-only join keys (osm_id / osm_type / fid) from the rows", () => {
    const data = buildFeaturePopupData(
      { osm_id: 123456, osm_type: "way", fid: "w123456" },
      PT,
      { layerName: "Buildings (OSM)", geomKindLabel: "Polygon" },
    );
    const labels = data.attributes.map((a) => a.label.toLowerCase());
    expect(labels).not.toContain("osm id");
    expect(labels).not.toContain("osm type");
    expect(labels).not.toContain("fid");
    // With everything hidden, the slim popup has no attribute rows yet.
    expect(data.attributes.length).toBe(0);
  });
});

describe("mergeTagsIntoAttributes", () => {
  function slim(): FeaturePopupData {
    return buildFeaturePopupData(
      { osm_id: 123456, osm_type: "way", fid: "w123456" },
      PT,
      { layerName: "Buildings (OSM)", geomKindLabel: "Polygon" },
    );
  }

  it("merges humanized tag rows into the popup attributes", () => {
    const merged = mergeTagsIntoAttributes(slim(), {
      building: "house",
      height: "8",
      "addr:street": "Main St",
    });
    const byLabel = new Map(merged.attributes.map((a) => [a.label, a.value]));
    expect(byLabel.get("Building")).toBe("house");
    expect(byLabel.get("Height")).toBe("8");
    // The humanizer (unchanged) leaves a `:` intact -> "Addr:street".
    expect(byLabel.get("Addr:street")).toBe("Main St");
  });

  it("promotes a fallback-title popup to the tag name", () => {
    // A footprint with no name resolves a geometry-kind/layer fallback title.
    const base = buildFeaturePopupData(
      { osm_id: 1, osm_type: "way", fid: "w1" },
      PT,
      { geomKindLabel: "Polygon" },
    );
    expect(base.title).toBe("Polygon");
    const merged = mergeTagsIntoAttributes(base, { name: "City Hall", building: "civic" });
    expect(merged.title).toBe("City Hall");
    // The name is the title, NOT also a row.
    const labels = merged.attributes.map((a) => a.label.toLowerCase());
    expect(labels).not.toContain("name");
  });

  it("is idempotent and never duplicates an already-present row", () => {
    const once = mergeTagsIntoAttributes(slim(), { building: "house" });
    const twice = mergeTagsIntoAttributes(once, { building: "house" });
    const buildingRows = twice.attributes.filter((a) => a.label === "Building");
    expect(buildingRows.length).toBe(1);
  });

  it("does not mutate the input popup", () => {
    const base = slim();
    const before = base.attributes.length;
    mergeTagsIntoAttributes(base, { building: "house" });
    expect(base.attributes.length).toBe(before);
  });
});

describe("FeaturePopup enriching loading state", () => {
  it("shows a 'Loading details...' row while enriching", () => {
    const data: FeaturePopupData = {
      title: "Building",
      attributes: [],
      point: PT,
      enriching: true,
      enrichFid: "w1",
    };
    render(<FeaturePopup data={data} canvasSize={CANVAS} isMobile={false} onClose={() => {}} />);
    expect(screen.getByTestId("feature-popup-enriching")).toBeTruthy();
  });

  it("does NOT show the loading row once enriching is false", () => {
    const data: FeaturePopupData = {
      title: "Maison",
      attributes: [{ label: "Building", value: "house" }],
      point: PT,
      enriching: false,
      enrichFid: "w1",
    };
    render(<FeaturePopup data={data} canvasSize={CANVAS} isMobile={false} onClose={() => {}} />);
    expect(screen.queryByTestId("feature-popup-enriching")).toBeNull();
    expect(screen.getByText("house")).toBeTruthy();
  });

  it("leaves a NON-footprint popup byte-for-byte unchanged (no loading row)", () => {
    // A typical station/WDPA popup carries NO enriching flag.
    const data: FeaturePopupData = {
      title: "Some Park",
      subtitle: "National Park",
      attributes: [{ label: "Area", value: "12 km2" }],
      point: PT,
    };
    render(<FeaturePopup data={data} canvasSize={CANVAS} isMobile={false} onClose={() => {}} />);
    expect(screen.queryByTestId("feature-popup-enriching")).toBeNull();
    expect(screen.getByText("Some Park")).toBeTruthy();
    expect(screen.getByText("12 km2")).toBeTruthy();
  });
});
