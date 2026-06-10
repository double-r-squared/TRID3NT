// GRACE-2 web — RoutingQualityDashboard (Wave 4.11 M7).
//
// Full-screen overlay that surfaces aggregated tool-routing telemetry over
// the most recent 30 sessions. Backed by ``GET /api/telemetry/summary`` on
// the agent service's HTTP listener (default port 8766; override via
// ``VITE_GRACE2_HTTP_URL``).
//
// Visible surface:
//
//   +---------------------------------------------------------------+
//   | Routing quality                                              ✕ |
//   |   Live snapshot — last 30 sessions   [Refresh ⟳]              |
//   |                                                                |
//   |   [Total dispatches] [Error rate] [Cache hits] [Avg latency]   |
//   |                                                                |
//   |   ── Top 15 tools by dispatch count ──                         |
//   |   fetch_dem          ████████████████ 41                       |
//   |   compute_hillshade  ██████████ 25                             |
//   |   ...                                                          |
//   |                                                                |
//   |   ── Per-tool stats ──                                         |
//   |   Tool | Count | Error rate | Avg latency                      |
//   |                                                                |
//   |   ── Recent chains ──                                          |
//   |   fetch_dem → compute_hillshade   (× 12)                       |
//   +---------------------------------------------------------------+
//
// Auto-refresh: every 30 seconds while mounted. Manual refresh via the
// header button. Esc / backdrop / X to dismiss.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

// ---------------------------------------------------------------------------
// Wire types — mirror /api/telemetry/summary response shape.
// ---------------------------------------------------------------------------

export interface RoutingDashboardToolRow {
  name: string;
  count: number;
  error_count: number;
  error_rate: number;
  avg_latency_ms: number;
}

export interface RoutingDashboardChain {
  chain: string[];
  count: number;
}

export interface RoutingDashboardSummary {
  total_dispatches: number;
  session_count: number;
  error_rate_overall: number;
  cache_hit_rate: number;
  average_latency_ms: number;
  dispatches_by_tool: RoutingDashboardToolRow[];
  dispatches_by_source: Record<string, number>;
  error_rate_by_tool: {
    name: string;
    error_rate: number;
    error_count: number;
    total: number;
  }[];
  top_routing_chains: RoutingDashboardChain[];
  /** Provenance — "mongo" | "file" | "empty" | "telemetry". */
  source: string;
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface RoutingQualityDashboardProps {
  /** Dismiss handler. */
  onClose: () => void;
  /**
   * Optional pre-fetched summary (tests inject this to bypass network).
   * When present, the component skips the initial fetch and renders
   * immediately.
   */
  initialSummary?: RoutingDashboardSummary | null;
  /** Optional fetch URL override. Tests pass a stubbed URL. */
  summaryUrl?: string;
  /**
   * Optional override of the auto-refresh interval, in milliseconds.
   * Defaults to 30_000 ms. Tests pass a small value or disable via 0.
   */
  refreshIntervalMs?: number;
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(0,0,0,0.55)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 9_500,
  fontFamily:
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
};

const cardStyle: React.CSSProperties = {
  background: "rgba(20,22,30,0.98)",
  border: "1px solid #444",
  borderRadius: 12,
  width: "min(860px, 96vw)",
  maxHeight: "90vh",
  display: "flex",
  flexDirection: "column",
  color: "#e8eaf0",
  boxShadow: "0 24px 64px rgba(0,0,0,0.55)",
  position: "relative",
  padding: "20px 22px 18px",
};

const headerRowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "baseline",
  gap: 10,
  marginBottom: 12,
};

const headerTitleStyle: React.CSSProperties = {
  fontSize: 20,
  fontWeight: 600,
  margin: 0,
  color: "#e8eaf0",
};

const subtitleStyle: React.CSSProperties = {
  fontSize: 12,
  color: "#9aa0ad",
  fontWeight: 400,
};

const closeBtnStyle: React.CSSProperties = {
  position: "absolute",
  top: 12,
  right: 12,
  background: "transparent",
  border: "none",
  color: "#aaa",
  fontSize: 18,
  cursor: "pointer",
  width: 28,
  height: 28,
  borderRadius: 6,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
};

const refreshBtnStyle: React.CSSProperties = {
  marginLeft: "auto",
  background: "rgba(40,42,52,0.9)",
  border: "1px solid #555",
  borderRadius: 6,
  color: "#ddd",
  padding: "4px 10px",
  fontSize: 11,
  cursor: "pointer",
  fontFamily: "inherit",
};

const scrollBodyStyle: React.CSSProperties = {
  overflowY: "auto",
  flex: 1,
  minHeight: 0,
  paddingTop: 4,
  paddingRight: 4,
};

const kpiGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
  gap: 10,
  marginBottom: 18,
};

const kpiCardStyle: React.CSSProperties = {
  background: "rgba(30,32,42,0.9)",
  border: "1px solid #3a3d49",
  borderRadius: 8,
  padding: "10px 12px",
};

const kpiLabelStyle: React.CSSProperties = {
  fontSize: 10,
  color: "#9aa0ad",
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  marginBottom: 6,
};

const kpiValueStyle: React.CSSProperties = {
  fontSize: 22,
  fontWeight: 600,
  color: "#dfe5f0",
  fontVariantNumeric: "tabular-nums",
};

const kpiHintStyle: React.CSSProperties = {
  fontSize: 10,
  color: "#6f7585",
  marginTop: 3,
};

const sectionHeadingStyle: React.CSSProperties = {
  fontSize: 12,
  color: "#9aa0ad",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  marginTop: 12,
  marginBottom: 8,
  borderBottom: "1px solid #2a2d35",
  paddingBottom: 6,
};

const barRowStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "180px 1fr 50px",
  alignItems: "center",
  gap: 8,
  marginBottom: 4,
  fontSize: 11,
};

const barNameStyle: React.CSSProperties = {
  fontFamily:
    "'JetBrains Mono', 'Fira Code', 'Menlo', monospace",
  color: "#cfd3dc",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const barTrackStyle: React.CSSProperties = {
  background: "rgba(40,42,52,0.5)",
  borderRadius: 3,
  height: 12,
  overflow: "hidden",
  border: "1px solid #2a2d35",
};

const barFillStyle: React.CSSProperties = {
  background: "linear-gradient(90deg, #3b82f6 0%, #6ea1f6 100%)",
  height: "100%",
};

const barCountStyle: React.CSSProperties = {
  color: "#9aa0ad",
  textAlign: "right",
  fontVariantNumeric: "tabular-nums",
};

const tableStyle: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: 12,
};

const thStyle: React.CSSProperties = {
  textAlign: "left",
  color: "#9aa0ad",
  fontSize: 10,
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  padding: "6px 4px",
  borderBottom: "1px solid #3a3d49",
};

const tdStyle: React.CSSProperties = {
  padding: "5px 4px",
  borderBottom: "1px solid #2a2d35",
  color: "#cfd3dc",
  fontVariantNumeric: "tabular-nums",
};

const chainPillStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  background: "rgba(30,32,42,0.9)",
  border: "1px solid #3a3d49",
  borderRadius: 6,
  padding: "4px 8px",
  fontSize: 11,
  marginRight: 6,
  marginBottom: 6,
};

// ---------------------------------------------------------------------------
// URL resolution
// ---------------------------------------------------------------------------

function defaultSummaryUrl(): string {
  const override =
    (import.meta.env.VITE_GRACE2_HTTP_URL as string | undefined) ?? null;
  if (override) {
    return override.replace(/\/+$/, "") + "/api/telemetry/summary";
  }
  if (typeof window !== "undefined") {
    const { protocol, hostname } = window.location;
    return `${protocol}//${hostname}:8766/api/telemetry/summary`;
  }
  return "http://localhost:8766/api/telemetry/summary";
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatPct(rate: number): string {
  if (!Number.isFinite(rate)) return "0%";
  return `${(rate * 100).toFixed(1)}%`;
}

function formatMs(ms: number): string {
  if (!Number.isFinite(ms)) return "0 ms";
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`;
  return `${Math.round(ms)} ms`;
}

function formatCount(n: number): string {
  return Number(n ?? 0).toLocaleString();
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

type LoadState = "loading" | "ready" | "error";

export function RoutingQualityDashboard({
  onClose,
  initialSummary = null,
  summaryUrl,
  refreshIntervalMs = 30_000,
}: RoutingQualityDashboardProps): JSX.Element {
  const [summary, setSummary] = useState<RoutingDashboardSummary | null>(
    initialSummary,
  );
  const [state, setState] = useState<LoadState>(
    initialSummary ? "ready" : "loading",
  );
  const [errorText, setErrorText] = useState<string | null>(null);
  const [refreshTick, setRefreshTick] = useState<number>(0);
  const cancelRef = useRef<boolean>(false);

  // Esc to dismiss.
  useEffect(() => {
    function onKey(e: KeyboardEvent): void {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Initial + on-tick fetch.
  useEffect(() => {
    if (initialSummary && refreshTick === 0) return; // first render uses inject
    let cancelled = false;
    cancelRef.current = false;
    const url = summaryUrl ?? defaultSummaryUrl();
    (async () => {
      // Only show loading on the first fetch; subsequent ticks should not
      // blank the dashboard while fresh data is in flight.
      if (refreshTick === 0 && !initialSummary) {
        setState("loading");
      }
      try {
        const resp = await fetch(url, { method: "GET" });
        if (!resp.ok) {
          throw new Error(`HTTP ${resp.status}`);
        }
        const json = (await resp.json()) as RoutingDashboardSummary;
        if (cancelled) return;
        setSummary(json);
        setErrorText(null);
        setState("ready");
      } catch (err) {
        if (cancelled) return;
        setErrorText(
          err instanceof Error ? err.message : "unknown fetch error",
        );
        setState("error");
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summaryUrl, refreshTick]);

  // Auto-refresh — 30s default.
  useEffect(() => {
    if (!refreshIntervalMs || refreshIntervalMs <= 0) return;
    const handle = setInterval(() => {
      setRefreshTick((t) => t + 1);
    }, refreshIntervalMs);
    return () => clearInterval(handle);
  }, [refreshIntervalMs]);

  const onManualRefresh = useCallback(() => {
    setRefreshTick((t) => t + 1);
  }, []);

  const topTools = useMemo<RoutingDashboardToolRow[]>(() => {
    if (!summary) return [];
    return summary.dispatches_by_tool.slice(0, 15);
  }, [summary]);

  const maxBarCount = useMemo(() => {
    if (topTools.length === 0) return 1;
    return Math.max(...topTools.map((t) => t.count), 1);
  }, [topTools]);

  const hasData =
    state === "ready" &&
    summary !== null &&
    summary.total_dispatches > 0;
  const isEmpty =
    state === "ready" &&
    summary !== null &&
    summary.total_dispatches === 0;

  return (
    <div
      data-testid="grace2-routing-dashboard"
      role="dialog"
      aria-modal="true"
      aria-label="Routing quality dashboard"
      style={overlayStyle}
      onClick={onClose}
    >
      <div
        data-testid="grace2-routing-dashboard-card"
        style={cardStyle}
        onClick={(e) => e.stopPropagation()}
      >
        <button
          data-testid="grace2-routing-dashboard-close"
          aria-label="Close routing quality dashboard"
          onClick={onClose}
          style={closeBtnStyle}
        >
          ✕
        </button>

        <div style={headerRowStyle}>
          <h2 style={headerTitleStyle}>Routing quality</h2>
          <span style={subtitleStyle}>
            {summary
              ? `last ${summary.session_count} session${
                  summary.session_count === 1 ? "" : "s"
                } — source: ${summary.source}`
              : "loading..."}
          </span>
          <button
            data-testid="grace2-routing-dashboard-refresh"
            style={refreshBtnStyle}
            onClick={onManualRefresh}
            aria-label="Refresh dashboard data"
          >
            Refresh
          </button>
        </div>

        <div style={scrollBodyStyle}>
          {state === "loading" && (
            <div
              data-testid="grace2-routing-dashboard-loading"
              style={{ padding: 20, color: "#9aa0ad", fontSize: 12 }}
            >
              Loading routing-quality summary...
            </div>
          )}

          {state === "error" && (
            <div
              data-testid="grace2-routing-dashboard-error"
              style={{
                padding: 14,
                color: "#f9c1c1",
                background: "rgba(60,20,20,0.4)",
                borderRadius: 6,
                border: "1px solid #6b3030",
                fontSize: 12,
                lineHeight: 1.5,
              }}
            >
              Could not load routing-quality summary: {errorText}.
              <br />
              Make sure the agent service is running and that{" "}
              <code style={{ fontFamily: "monospace" }}>
                /api/telemetry/summary
              </code>{" "}
              is reachable.
            </div>
          )}

          {isEmpty && (
            <div
              data-testid="grace2-routing-dashboard-empty"
              style={{
                padding: 24,
                color: "#9aa0ad",
                fontSize: 12,
                textAlign: "center",
                lineHeight: 1.6,
              }}
            >
              No routing telemetry has been recorded yet.
              <br />
              Drive the agent through a few tool calls and refresh to see
              dispatch counts, error rates, and chains here.
            </div>
          )}

          {hasData && summary && (
            <>
              {/* KPI cards */}
              <div
                data-testid="grace2-routing-dashboard-kpis"
                style={kpiGridStyle}
              >
                <div
                  data-testid="grace2-routing-dashboard-kpi-total"
                  style={kpiCardStyle}
                >
                  <div style={kpiLabelStyle}>Total dispatches</div>
                  <div style={kpiValueStyle}>
                    {formatCount(summary.total_dispatches)}
                  </div>
                  <div style={kpiHintStyle}>
                    across {summary.session_count} session
                    {summary.session_count === 1 ? "" : "s"}
                  </div>
                </div>
                <div
                  data-testid="grace2-routing-dashboard-kpi-error-rate"
                  style={kpiCardStyle}
                >
                  <div style={kpiLabelStyle}>Error rate</div>
                  <div
                    style={{
                      ...kpiValueStyle,
                      color:
                        summary.error_rate_overall > 0.1
                          ? "#f9c1c1"
                          : "#dfe5f0",
                    }}
                  >
                    {formatPct(summary.error_rate_overall)}
                  </div>
                  <div style={kpiHintStyle}>
                    {summary.error_rate_by_tool.reduce(
                      (acc, r) => acc + r.error_count,
                      0,
                    )}{" "}
                    failed calls
                  </div>
                </div>
                <div
                  data-testid="grace2-routing-dashboard-kpi-cache-hit"
                  style={kpiCardStyle}
                >
                  <div style={kpiLabelStyle}>Cache hit rate</div>
                  <div style={kpiValueStyle}>
                    {formatPct(summary.cache_hit_rate)}
                  </div>
                  <div style={kpiHintStyle}>cached content tokens</div>
                </div>
                <div
                  data-testid="grace2-routing-dashboard-kpi-latency"
                  style={kpiCardStyle}
                >
                  <div style={kpiLabelStyle}>Average latency</div>
                  <div style={kpiValueStyle}>
                    {formatMs(summary.average_latency_ms)}
                  </div>
                  <div style={kpiHintStyle}>per dispatch</div>
                </div>
              </div>

              {/* Bar chart — top 15 tools */}
              <div style={sectionHeadingStyle}>
                Top {topTools.length} tools by dispatch count
              </div>
              <div data-testid="grace2-routing-dashboard-bars">
                {topTools.map((t) => {
                  const pct = (t.count / maxBarCount) * 100;
                  return (
                    <div
                      key={t.name}
                      data-testid="grace2-routing-dashboard-bar-row"
                      data-tool-name={t.name}
                      style={barRowStyle}
                    >
                      <span style={barNameStyle} title={t.name}>
                        {t.name}
                      </span>
                      <span style={barTrackStyle}>
                        <span
                          style={{
                            ...barFillStyle,
                            width: `${Math.max(pct, 2)}%`,
                            display: "block",
                          }}
                        />
                      </span>
                      <span style={barCountStyle}>{formatCount(t.count)}</span>
                    </div>
                  );
                })}
              </div>

              {/* Per-tool stats table */}
              <div style={sectionHeadingStyle}>Per-tool stats</div>
              <table
                data-testid="grace2-routing-dashboard-table"
                style={tableStyle}
              >
                <thead>
                  <tr>
                    <th style={thStyle}>Tool</th>
                    <th style={{ ...thStyle, textAlign: "right" }}>Count</th>
                    <th style={{ ...thStyle, textAlign: "right" }}>
                      Error rate
                    </th>
                    <th style={{ ...thStyle, textAlign: "right" }}>
                      Avg latency
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {summary.dispatches_by_tool.map((t) => (
                    <tr
                      key={t.name}
                      data-testid="grace2-routing-dashboard-table-row"
                      data-tool-name={t.name}
                    >
                      <td
                        style={{
                          ...tdStyle,
                          fontFamily:
                            "'JetBrains Mono', 'Fira Code', monospace",
                          fontSize: 11,
                          color: "#dfe5f0",
                        }}
                      >
                        {t.name}
                      </td>
                      <td style={{ ...tdStyle, textAlign: "right" }}>
                        {formatCount(t.count)}
                      </td>
                      <td
                        style={{
                          ...tdStyle,
                          textAlign: "right",
                          color:
                            t.error_rate > 0.1 ? "#f9c1c1" : "#cfd3dc",
                        }}
                      >
                        {formatPct(t.error_rate)}
                      </td>
                      <td style={{ ...tdStyle, textAlign: "right" }}>
                        {formatMs(t.avg_latency_ms)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {/* Recent chains */}
              <div style={sectionHeadingStyle}>Top routing chains</div>
              {summary.top_routing_chains.length === 0 ? (
                <div
                  data-testid="grace2-routing-dashboard-no-chains"
                  style={{
                    color: "#9aa0ad",
                    fontSize: 11,
                    fontStyle: "italic",
                    padding: "4px 0 8px",
                  }}
                >
                  No multi-tool sequences recorded yet.
                </div>
              ) : (
                <div data-testid="grace2-routing-dashboard-chains">
                  {summary.top_routing_chains.map((c, i) => (
                    <span
                      key={`chain-${i}`}
                      data-testid="grace2-routing-dashboard-chain"
                      style={chainPillStyle}
                    >
                      <code
                        style={{
                          fontFamily:
                            "'JetBrains Mono', 'Fira Code', monospace",
                          color: "#dfe5f0",
                        }}
                      >
                        {c.chain.join(" → ")}
                      </code>
                      <span style={{ color: "#9aa0ad" }}>× {c.count}</span>
                    </span>
                  ))}
                </div>
              )}

              {/* Sources mix — a small inline rendering of llm/workflow split. */}
              <div style={sectionHeadingStyle}>Dispatch sources</div>
              <div
                data-testid="grace2-routing-dashboard-sources"
                style={{
                  display: "flex",
                  gap: 16,
                  fontSize: 11,
                  color: "#cfd3dc",
                  paddingBottom: 12,
                }}
              >
                {Object.entries(summary.dispatches_by_source).map(
                  ([source, count]) => (
                    <span
                      key={source}
                      data-testid={`grace2-routing-dashboard-source-${source}`}
                    >
                      <span style={{ color: "#9aa0ad" }}>{source}: </span>
                      <span style={{ fontWeight: 600 }}>
                        {formatCount(count)}
                      </span>
                    </span>
                  ),
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
