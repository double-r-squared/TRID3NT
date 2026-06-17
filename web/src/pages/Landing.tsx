// GRACE-2 web — public landing page (job-0285).
//
// The hero/marketing page rendered at "/" for first-time visitors (and at
// "/landing" always — see EntryRouter.tsx for the passthrough rule). Pure
// presentational React + CSS: no router, no UI kit, no heavy assets. The
// showcase imagery under /landing/*.webp is real product screenshots
// (SFINCS flood render, Pelicun/NSI structure assessment, colored-relief
// terrain) recompressed from live-verify evidence runs.
//
// Design language: dark map-toned chrome consistent with the app, Gemini
// blue→purple→coral accent gradient, glassmorphism cards, a graticule
// (map-grid) backdrop and slow aurora drift (disabled under
// prefers-reduced-motion in landing.css).

import { useEffect } from "react";
import type { FC } from "react";
import {
  IconChat,
  IconWaves,
  IconGrid,
  IconTerrain,
  IconWorkspaces,
  IconSparkle,
  IconArrowRight,
  IconChevronRight,
} from "../components/icons";
import type { IconProps } from "../components/icons";
import "./landing.css";

export interface LandingProps {
  /**
   * True when the browser already carries a GRACE-2 session key — only
   * reachable via the explicit "/landing" path in that case (EntryRouter
   * passes "/" straight through to the app). Switches the primary CTA to
   * the "Resume session" variant.
   */
  hasSession?: boolean;
}

interface Feature {
  icon: FC<IconProps>;
  title: string;
  body: string;
}

const FEATURES: Feature[] = [
  {
    icon: IconChat,
    title: "Conversational flood modeling",
    body:
      "“Model a 100-year flood for Fort Myers” is a sentence, not a workflow. The agent geocodes, fetches elevation, runs the solver, and publishes the layer — narrating every step.",
  },
  {
    icon: IconWaves,
    title: "Real physics solvers",
    body:
      "SFINCS coastal & pluvial flooding and MODFLOW groundwater run as real cloud jobs behind the conversation — actual numerical engines, not illustrations.",
  },
  {
    icon: IconGrid,
    title: "Damage analytics",
    body:
      "Pelicun damage and loss assessment over USACE National Structure Inventory data — tens of thousands of structures evaluated against a flood field in one ask.",
  },
  {
    icon: IconTerrain,
    title: "Terrain, weather & wildlife",
    body:
      "Colored relief, hillshade, slope and aspect; live NWS alerts and radar; ERA5 reanalysis; GBIF and eBird occurrences — fetched, clipped, and styled onto the map.",
  },
  {
    icon: IconWorkspaces,
    title: "Per-Case workspaces",
    body:
      "Every Case is its own conversation thread — chat, tool history, layers, and artifacts persist together and replay when you come back.",
  },
];

const PIPELINE_CHIPS = [
  "geocode_location",
  "fetch_dem",
  "run_model_flood_scenario",
  "publish_layer",
];

export function Landing({ hasSession = false }: LandingProps): JSX.Element {
  useEffect(() => {
    document.title = "GRACE-2 — AI workbench for multi-hazard modeling";
  }, []);

  return (
    <div className="lp" data-testid="grace2-landing">
      {/* Decorative backdrop: graticule grid + aurora blobs (CSS only). */}
      <div className="lp-bg" aria-hidden="true">
        <div className="lp-bg-grid" />
        <div className="lp-bg-aurora lp-bg-aurora-a" />
        <div className="lp-bg-aurora lp-bg-aurora-b" />
      </div>

      <header className="lp-nav">
        <a className="lp-wordmark" href="/">
          <span className="lp-wordmark-glyph" aria-hidden="true" />
          GRACE-2
        </a>
        <nav className="lp-nav-links" aria-label="Landing navigation">
          <a href="#features">Capabilities</a>
          <a href="#gemini">Gemini</a>
          <a href="/privacy">Privacy</a>
          <a className="lp-nav-launch" href="/app">
            Launch app
          </a>
        </nav>
      </header>

      <main>
        {/* ───────────────────────── Hero ───────────────────────── */}
        <section className="lp-hero">
          <div className="lp-hero-copy">
            <span className="lp-badge">
              <span className="lp-badge-spark" aria-hidden="true">
                <IconSparkle size={13} weight="fill" />
              </span>
              Powered by Google Gemini
            </span>
            <h1 className="lp-h1">
              Ask for a flood model.
              <br />
              <span className="lp-h1-grad">Watch it run.</span>
            </h1>
            <p className="lp-sub">
              GRACE-2 is an AI workbench for multi-hazard modeling. A{" "}
              <strong>Gemini-powered agent</strong> turns plain English into
              real geospatial pipelines — physics-based flood and groundwater
              solvers, structure-level damage assessment, terrain and live
              weather — all rendered on a full-screen map you can talk to.
            </p>
            <div className="lp-cta-row">
              <a
                className="lp-cta"
                href="/app"
                data-testid="grace2-landing-cta"
              >
                {hasSession ? "Resume session" : "Launch GRACE-2"}
                <span className="lp-cta-arrow" aria-hidden="true">
                  <IconArrowRight size={16} />
                </span>
              </a>
              <a className="lp-cta-ghost" href="#features">
                Explore capabilities
              </a>
            </div>
            <div className="lp-pipeline" aria-label="Example agent pipeline">
              {PIPELINE_CHIPS.map((chip, i) => (
                <span key={chip} className="lp-pipeline-step">
                  <code className="lp-chip">{chip}</code>
                  {i < PIPELINE_CHIPS.length - 1 && (
                    <span className="lp-chip-arrow" aria-hidden="true">
                      <IconChevronRight size={12} />
                    </span>
                  )}
                </span>
              ))}
            </div>
          </div>

          <div className="lp-hero-shot">
            <figure className="lp-frame lp-frame-tilt">
              <div className="lp-frame-bar" aria-hidden="true">
                <i />
                <i />
                <i />
              </div>
              <img
                src="/landing/shot_flood_desktop.webp"
                width={1440}
                height={900}
                alt="GRACE-2 rendering a SFINCS flood-depth raster over Fort Myers, Florida, with the agent chat panel alongside the map"
                loading="eager"
              />
              <figcaption>
                SFINCS flood inundation — Fort Myers, FL · live run
              </figcaption>
            </figure>
          </div>
        </section>

        {/* ─────────────────────── Stats strip ─────────────────────── */}
        <section className="lp-stats" aria-label="GRACE-2 by the numbers">
          <div className="lp-stat">
            <span className="lp-stat-n">70+</span>
            <span className="lp-stat-l">geospatial tools</span>
          </div>
          <div className="lp-stat">
            <span className="lp-stat-n">2</span>
            <span className="lp-stat-l">physics engines</span>
          </div>
          <div className="lp-stat">
            <span className="lp-stat-n">70k+</span>
            <span className="lp-stat-l">structures per damage run</span>
          </div>
          <div className="lp-stat">
            <span className="lp-stat-n">1</span>
            <span className="lp-stat-l">conversation to drive it all</span>
          </div>
        </section>

        {/* ─────────────────────── Features ─────────────────────── */}
        <section className="lp-features" id="features">
          <h2 className="lp-h2">
            From plain English <span className="lp-h1-grad">to physics</span>
          </h2>
          <p className="lp-section-sub">
            Five things the agent does for you — no GIS desktop, no model
            decks, no file wrangling.
          </p>
          <div className="lp-feature-grid">
            {FEATURES.map((f) => (
              <article
                key={f.title}
                className="lp-card"
                data-testid="grace2-landing-feature"
              >
                <span className="lp-card-icon" aria-hidden="true">
                  <f.icon size={28} />
                </span>
                <h3>{f.title}</h3>
                <p>{f.body}</p>
              </article>
            ))}
          </div>
        </section>

        {/* ─────────────────────── Gemini band ─────────────────────── */}
        <section className="lp-gemini" id="gemini">
          <div className="lp-gemini-copy">
            <span className="lp-badge">
              <span className="lp-badge-spark" aria-hidden="true">
                <IconSparkle size={13} weight="fill" />
              </span>
              Google Gemini, doing the work
            </span>
            <h2 className="lp-h2">The agent is Gemini.</h2>
            <p>
              GRACE-2 drives Google&rsquo;s Gemini through native function
              calling: a streaming agent loop that reasons over a catalog of
              70+ geospatial tools, narrates its plan in the chat, executes
              cloud solvers, and feeds results — including failures — back
              into the model so it can recover and retry.
            </p>
            <ul className="lp-gemini-list">
              <li>
                <strong>Native function calling</strong> over a server-cached
                tool catalog — fast routing, no prompt bloat.
              </li>
              <li>
                <strong>Streaming narration</strong> while tools run: you see
                what the agent is doing, as it does it.
              </li>
              <li>
                <strong>Honest recovery</strong> — tool errors return to
                Gemini as structured results, so it corrects arguments and
                retries instead of pretending.
              </li>
            </ul>
          </div>
          <div className="lp-gemini-shots">
            <figure className="lp-phone">
              <img
                src="/landing/shot_chat_mobile.webp"
                width={390}
                height={844}
                alt="GRACE-2 mobile chat showing the agent running geocode, DEM fetch, colored relief, and layer publish tools for Boulder, Colorado"
                loading="lazy"
              />
              <figcaption>The agent narrating a terrain pipeline</figcaption>
            </figure>
            <figure className="lp-phone lp-phone-offset">
              <img
                src="/landing/shot_terrain_mobile.webp"
                width={390}
                height={844}
                alt="Colored-relief terrain layer rendered over Boulder, Colorado on the GRACE-2 map"
                loading="lazy"
              />
              <figcaption>…and the layer it produced</figcaption>
            </figure>
          </div>
        </section>

        {/* ───────────────────── Showcase band ───────────────────── */}
        <section className="lp-showcase">
          <figure className="lp-frame">
            <div className="lp-frame-bar" aria-hidden="true">
              <i />
              <i />
              <i />
            </div>
            <img
              src="/landing/shot_impact_desktop.webp"
              width={1440}
              height={900}
              alt="GRACE-2 showing USACE National Structure Inventory points over a flood-depth layer in Fort Myers while the agent runs a Pelicun damage assessment"
              loading="lazy"
            />
            <figcaption>
              Pelicun damage assessment over USACE NSI structures — flood
              depth + building inventory in one view
            </figcaption>
          </figure>
        </section>

        {/* ─────────────────────── Bottom CTA ─────────────────────── */}
        <section className="lp-bottom">
          <h2 className="lp-h2">
            Model the next hazard{" "}
            <span className="lp-h1-grad">in a sentence.</span>
          </h2>
          <a className="lp-cta" href="/app">
            {hasSession ? "Resume session" : "Launch GRACE-2"}
            <span className="lp-cta-arrow" aria-hidden="true">
              <IconArrowRight size={16} />
            </span>
          </a>
        </section>
      </main>

      <footer className="lp-footer">
        <div className="lp-footer-row">
          <span className="lp-footer-brand">GRACE-2</span>
          <nav aria-label="Footer">
            <a href="/privacy" data-testid="grace2-landing-privacy-link">
              Privacy Policy
            </a>
            <a href="mailto:natealmanza3@gmail.com">Contact</a>
          </nav>
        </div>
        <p className="lp-footer-credit">
          Built on Google Gemini · Cloud Run · QGIS Server · MapLibre GL ·
          MongoDB Atlas
        </p>
        <p className="lp-footer-fine">
          © 2026 GRACE-2. Model outputs are research aids, not official
          hazard guidance.
        </p>
      </footer>
    </div>
  );
}
