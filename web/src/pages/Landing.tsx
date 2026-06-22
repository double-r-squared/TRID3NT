// GRACE-2 web - public landing page.
//
// The hero/marketing page rendered at "/" for first-time visitors (and at
// "/landing" always - see EntryRouter.tsx for the passthrough rule). Pure
// presentational React + CSS: no router, no UI kit, no heavy assets. The
// showcase imagery under /landing/*.webp is real product screenshots
// (SFINCS flood render, Pelicun/NSI impact assessment, colored-relief
// terrain) recompressed from live-verify evidence runs. Fresh Playwright
// captures drop into the SAME filenames, so the asset paths stay stable.
//
// CTA + routing contract (PRESERVE): the primary CTA points at "/app" with a
// data-testid of "grace2-landing-cta"; the "Resume session" variant renders
// when hasSession is true (returning visitors reaching "/landing"). The
// privacy link points at "/privacy". None of that is allowed to drift -
// EntryRouter and the live-verify tooling depend on it.
//
// Design language: clean, modern, dark-friendly geospatial/AI product page -
// blue->cyan->teal accent gradient, glassmorphism cards, a map-graticule
// backdrop and slow aurora drift (disabled under prefers-reduced-motion in
// landing.css). Strong typographic hierarchy, responsive, accessible.

import { useEffect } from "react";
import type { FC } from "react";
import {
  IconChat,
  IconWaves,
  IconGrid,
  IconTerrain,
  IconWorkspaces,
  IconGlobe,
  IconFlowArrow,
  IconModel,
  IconMapPin,
  IconSparkle,
  IconArrowRight,
  IconChevronRight,
} from "../components/icons";
import type { IconProps } from "../components/icons";
import "./landing.css";

export interface LandingProps {
  /**
   * True when the browser already carries a GRACE-2 session key - only
   * reachable via the explicit "/landing" path in that case (EntryRouter
   * passes "/" straight through to the app). Switches the primary CTA to
   * the "Resume session" variant.
   */
  hasSession?: boolean;
}

interface Hazard {
  icon: FC<IconProps>;
  title: string;
  engine: string;
  body: string;
}

/**
 * The shipped hazard/engine matrix - every one runs as a real solver on AWS
 * Batch Spot (scale-to-zero), driven entirely from the conversation.
 */
const HAZARDS: Hazard[] = [
  {
    icon: IconWaves,
    title: "Coastal flood + waves",
    engine: "SFINCS + SnapWave",
    body:
      "Storm surge and wave setup on a SFINCS quadtree mesh with SnapWave coupling - the same workflow Deltares uses for hurricane inundation studies.",
  },
  {
    icon: IconGrid,
    title: "Urban flood",
    engine: "PySWMM",
    body:
      "Pluvial street flooding through a quasi-2D node-link mesh: buildings become obstructions, walls block links, flap gates are modeled natively.",
  },
  {
    icon: IconFlowArrow,
    title: "Riverine + compound flood",
    engine: "SFINCS multi-driver",
    body:
      "Surge, river discharge, and rainfall combined in one compound-flood run - the multi-driver forcing real practitioners use for coastal watersheds.",
  },
  {
    icon: IconGlobe,
    title: "Groundwater contamination",
    engine: "MODFLOW 6 + transport",
    body:
      "Subsurface flow and solute transport with MODFLOW 6 via FloPy - track a contaminant plume from source to receptor across the aquifer.",
  },
  {
    icon: IconSparkle,
    title: "Seismic hazard (PSHA)",
    engine: "OpenQuake",
    body:
      "Probabilistic seismic hazard with OpenQuake: ground-motion hazard curves and shaking maps from the canonical open-source PSHA engine.",
  },
  {
    icon: IconWaves,
    title: "Dam-break + shallow water",
    engine: "GeoClaw",
    body:
      "Shallow-water dam-break and overland routing on adaptively refined grids with GeoClaw - wetting-and-drying done right.",
  },
  {
    icon: IconTerrain,
    title: "Landslide susceptibility",
    engine: "Landlab",
    body:
      "Slope-stability and surface-process modeling with Landlab to map where terrain is primed to fail under rain or shaking.",
  },
  {
    icon: IconWorkspaces,
    title: "Impact + loss",
    engine: "Pelicun",
    body:
      "Structure-level damage and loss with Pelicun over the USACE National Structure Inventory - tens of thousands of buildings scored against any hazard field.",
  },
];

interface Step {
  n: string;
  icon: FC<IconProps>;
  title: string;
  body: string;
}

const STEPS: Step[] = [
  {
    n: "01",
    icon: IconChat,
    title: "Chat",
    body:
      "Describe the scenario in plain English - 'model a 100-year flood for Fort Myers and assess building damage.' Draw an AOI on the map if you want precise bounds.",
  },
  {
    n: "02",
    icon: IconModel,
    title: "Model",
    body:
      "The agent geocodes, pulls authoritative data, builds the model deck, and submits the solver to AWS Batch - narrating each tool call as it runs, recovering from errors honestly.",
  },
  {
    n: "03",
    icon: IconMapPin,
    title: "Map",
    body:
      "Results paint onto an interactive MapLibre map: raster depth fields, vector structures, time-stepped water animation, 3D terrain - ready to inspect, scrub, and export.",
  },
];

const STATS = [
  { n: "8", l: "physics engines, all real" },
  { n: "100+", l: "agent tools" },
  { n: "60+", l: "live data sources" },
  { n: "$0", l: "idle compute (scale-to-zero)" },
];

const PIPELINE_CHIPS = [
  "geocode_location",
  "fetch_dem",
  "run_model_flood_scenario",
  "publish_layer",
];

export function Landing({ hasSession = false }: LandingProps): JSX.Element {
  useEffect(() => {
    document.title = "GRACE-2 - AI workbench for multi-hazard modeling";
  }, []);

  const ctaLabel = hasSession ? "Resume session" : "Launch GRACE-2";

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
          <a href="#hazards">Hazards</a>
          <a href="#how">How it works</a>
          <a href="#agent">The agent</a>
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
              Powered by Anthropic Claude on AWS
            </span>
            <h1 className="lp-h1">
              Chat to run real hazard models
              <br />
              <span className="lp-h1-grad">on a live map.</span>
            </h1>
            <p className="lp-sub">
              GRACE-2 is an AI workbench for multi-hazard modeling. Describe a
              flood, earthquake, or groundwater scenario in plain English and a{" "}
              <strong>Claude-powered agent</strong> runs the actual physics
              solver - SFINCS, PySWMM, MODFLOW, OpenQuake and more - then paints
              the results on an interactive map you can talk to.
            </p>
            <div className="lp-cta-row">
              <a
                className="lp-cta"
                href="/app"
                data-testid="grace2-landing-cta"
              >
                {ctaLabel}
                <span className="lp-cta-arrow" aria-hidden="true">
                  <IconArrowRight size={16} />
                </span>
              </a>
              <a className="lp-cta-ghost" href="#hazards">
                Explore the hazards
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
                alt="GRACE-2 rendering a SFINCS flood-depth raster over Fort Myers, Florida, with the agent chat panel alongside the interactive map"
                loading="eager"
              />
              <figcaption>
                SFINCS flood inundation, Fort Myers FL - a live agent run
              </figcaption>
            </figure>
          </div>
        </section>

        {/* ─────────────────────── Stats strip ─────────────────────── */}
        <section className="lp-stats" aria-label="GRACE-2 by the numbers">
          {STATS.map((s) => (
            <div className="lp-stat" key={s.l}>
              <span className="lp-stat-n">{s.n}</span>
              <span className="lp-stat-l">{s.l}</span>
            </div>
          ))}
        </section>

        {/* ─────────────────────── Hazards ─────────────────────── */}
        <section className="lp-hazards" id="hazards">
          <h2 className="lp-h2">
            One conversation, <span className="lp-h1-grad">every hazard.</span>
          </h2>
          <p className="lp-section-sub">
            Each engine is a real numerical solver running on AWS Batch Spot -
            not an illustration. Ask for one, the agent drives it end to end.
          </p>
          <div className="lp-hazard-grid">
            {HAZARDS.map((h) => (
              <article
                key={h.title}
                className="lp-card"
                data-testid="grace2-landing-hazard"
              >
                <span className="lp-card-icon" aria-hidden="true">
                  <h.icon size={26} />
                </span>
                <h3>{h.title}</h3>
                <span className="lp-card-engine">{h.engine}</span>
                <p>{h.body}</p>
              </article>
            ))}
          </div>
        </section>

        {/* ─────────────────────── How it works ─────────────────────── */}
        <section className="lp-how" id="how">
          <h2 className="lp-h2">
            Chat <span className="lp-h1-grad">to model to map.</span>
          </h2>
          <p className="lp-section-sub">
            No GIS desktop, no model decks, no file wrangling - three steps from
            a sentence to a rendered result.
          </p>
          <ol className="lp-steps">
            {STEPS.map((s, i) => (
              <li className="lp-step" key={s.n}>
                <span className="lp-step-n" aria-hidden="true">
                  {s.n}
                </span>
                <span className="lp-step-icon" aria-hidden="true">
                  <s.icon size={24} />
                </span>
                <h3>{s.title}</h3>
                <p>{s.body}</p>
                {i < STEPS.length - 1 && (
                  <span className="lp-step-arrow" aria-hidden="true">
                    <IconArrowRight size={18} />
                  </span>
                )}
              </li>
            ))}
          </ol>
        </section>

        {/* ─────────────────────── Agent band ─────────────────────── */}
        <section className="lp-agent" id="agent">
          <div className="lp-agent-copy">
            <span className="lp-badge">
              <span className="lp-badge-spark" aria-hidden="true">
                <IconSparkle size={13} weight="fill" />
              </span>
              Anthropic Claude, doing the work
            </span>
            <h2 className="lp-h2">The agent is Claude.</h2>
            <p>
              GRACE-2 drives Anthropic&rsquo;s Claude on AWS Bedrock through
              native function calling: a streaming agent loop that reasons over
              100+ geospatial tools, narrates its plan in the chat, runs cloud
              solvers, and feeds results - including failures - back into the
              model so it can recover and retry. Sonnet by default; Haiku and
              Amazon Nova are selectable per case.
            </p>
            <ul className="lp-agent-list">
              <li>
                <strong>Native function calling</strong> over a server-cached
                tool catalog - fast routing, no prompt bloat.
              </li>
              <li>
                <strong>Streaming narration</strong> while tools run: you see
                what the agent is doing, as it does it.
              </li>
              <li>
                <strong>Honest recovery</strong> - tool errors return to Claude
                as structured results, so it corrects arguments and retries
                instead of pretending.
              </li>
            </ul>
          </div>
          <div className="lp-agent-shots">
            <figure className="lp-phone">
              <img
                src="/landing/shot_chat_mobile.webp"
                width={390}
                height={844}
                alt="GRACE-2 mobile chat showing the agent running geocode, DEM fetch, colored relief, and layer publish tools"
                loading="lazy"
              />
              <figcaption>The agent narrating a pipeline</figcaption>
            </figure>
            <figure className="lp-phone lp-phone-offset">
              <img
                src="/landing/shot_terrain_mobile.webp"
                width={390}
                height={844}
                alt="3D colored-relief terrain layer rendered on the GRACE-2 mobile map"
                loading="lazy"
              />
              <figcaption>...and the 3D terrain it produced</figcaption>
            </figure>
          </div>
        </section>

        {/* ─────────────────── Credibility / data strip ─────────────────── */}
        <section className="lp-cred" aria-label="Data and architecture">
          <h2 className="lp-h2">
            Authoritative data,{" "}
            <span className="lp-h1-grad">cloud-native architecture.</span>
          </h2>
          <div className="lp-cred-grid">
            <div className="lp-cred-card">
              <h3>60+ live data sources</h3>
              <p>
                3DEP and USGS elevation, building footprints, HRRR and MRMS
                weather, FEMA NFHL, USACE levees, dams and NSI structures, NOAA
                tides and surge, land cover, population - fetched, clipped, and
                styled on demand.
              </p>
            </div>
            <div className="lp-cred-card">
              <h3>Scale-to-zero on AWS</h3>
              <p>
                Heavy solves run on AWS Batch Spot and shut down when idle; the
                agent box auto-stops and wakes on demand. Independent
                scale-to-zero islands mean the map keeps serving 24/7 even with
                the agent asleep.
              </p>
            </div>
            <div className="lp-cred-card">
              <h3>The determinism boundary</h3>
              <p>
                The LLM plans and narrates but never produces numbers - every
                value comes from a real solver or an authoritative source, and
                every hazard claim carries per-source provenance.
              </p>
            </div>
          </div>
          <p className="lp-cred-stack">
            React + MapLibre GL on S3 + CloudFront · AWS Bedrock agent on EC2 ·
            AWS Batch Spot solvers · TiTiler raster tiles · DynamoDB · Cognito
          </p>
        </section>

        {/* ───────────────────── Impact showcase ───────────────────── */}
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
              alt="GRACE-2 showing USACE National Structure Inventory points over a flood-depth layer while the agent runs a Pelicun damage assessment"
              loading="lazy"
            />
            <figcaption>
              Pelicun damage assessment over USACE NSI structures - flood depth
              and building inventory in one view
            </figcaption>
          </figure>
        </section>

        {/* ─────────────────────── Bottom CTA ─────────────────────── */}
        <section className="lp-bottom">
          <h2 className="lp-h2">
            Model the next hazard{" "}
            <span className="lp-h1-grad">in a sentence.</span>
          </h2>
          <p className="lp-section-sub lp-bottom-sub">
            Open the workbench and ask the agent to run something real.
          </p>
          <a className="lp-cta" href="/app">
            {ctaLabel}
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
          Built on AWS Bedrock · Amazon EC2 · AWS Batch · TiTiler · MapLibre GL
          · Amazon DynamoDB
        </p>
        <p className="lp-footer-fine">
          © 2026 GRACE-2. Model outputs are research aids, not official
          hazard guidance.
        </p>
      </footer>
    </div>
  );
}
