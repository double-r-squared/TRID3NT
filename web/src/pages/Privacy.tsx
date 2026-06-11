// GRACE-2 web — privacy policy page (job-0285).
//
// Served at "/privacy" (always — no session gating; see EntryRouter.tsx).
// This page is the public privacy-policy URL for the Google OAuth consent
// screen ahead of the sprint-13.5 production deploy, so the content must
// stay honest and current: anonymous sessions today / Google sign-in
// coming; chat + Case data in MongoDB Atlas; geospatial artifacts in
// Google Cloud Storage; prompts processed by Google's Gemini API (Vertex
// AI); no sale of personal data.

import { useEffect } from "react";
import "./privacy.css";

const EFFECTIVE_DATE = "June 11, 2026";
const CONTACT_EMAIL = "natealmanza3@gmail.com";

export function Privacy(): JSX.Element {
  useEffect(() => {
    document.title = "Privacy Policy — GRACE-2";
  }, []);

  return (
    <div className="pp" data-testid="grace2-privacy">
      <header className="pp-nav">
        <a className="pp-wordmark" href="/">
          <span className="pp-wordmark-glyph" aria-hidden="true" />
          GRACE-2
        </a>
        <a className="pp-nav-launch" href="/app">
          Launch app
        </a>
      </header>

      <main className="pp-main">
        <h1>Privacy Policy</h1>
        <p className="pp-effective">
          Effective date: <strong>{EFFECTIVE_DATE}</strong>
        </p>

        <p className="pp-lede">
          GRACE-2 is an AI workbench for multi-hazard modeling: you chat with
          a Gemini-powered agent that runs geospatial models and renders the
          results on a map. This policy explains, in plain language, what
          data the service handles when you use it and where that data lives.
        </p>

        <section>
          <h2>Data we collect</h2>
          <ul>
            <li>
              <strong>Session identifiers.</strong> Today GRACE-2 uses
              anonymous sessions: a randomly generated session ID and
              anonymous user ID stored in your browser&rsquo;s localStorage.
              They contain no personal information. When Google sign-in
              launches, signing in will additionally associate your Google
              account&rsquo;s basic profile (name, email address) with your
              workspace.
            </li>
            <li>
              <strong>Chat and Case content.</strong> The messages you send,
              the agent&rsquo;s responses, the tools it ran, and the Cases
              (conversation workspaces) you create.
            </li>
            <li>
              <strong>Generated geospatial artifacts.</strong> Model outputs
              produced for your requests — flood rasters, terrain layers,
              damage assessments, fetched datasets.
            </li>
            <li>
              <strong>Operational logs.</strong> Basic technical telemetry
              (tool invocations, errors, timing) used to keep the service
              working.
            </li>
          </ul>
        </section>

        <section>
          <h2>How we use it</h2>
          <ul>
            <li>
              To operate the service: run the models you ask for, render
              layers, and persist your Cases so you can return to them.
            </li>
            <li>
              To keep the service reliable: debugging, error tracking, and
              performance monitoring.
            </li>
            <li>
              <strong>We do not sell personal data.</strong> We do not use
              your content for advertising.
            </li>
          </ul>
        </section>

        <section>
          <h2>Storage &amp; third parties</h2>
          <p>
            GRACE-2 runs on Google Cloud. Your data is processed and stored
            by the following services, each under its own terms:
          </p>
          <ul>
            <li>
              <strong>MongoDB Atlas</strong> — stores chat history, Cases,
              session records, and audit logs.
            </li>
            <li>
              <strong>Google Cloud Storage</strong> — stores generated
              geospatial artifacts (rasters, vectors, model outputs).
            </li>
            <li>
              <strong>Google Gemini API (Vertex AI)</strong> — your prompts
              and the agent&rsquo;s working context are sent to Google&rsquo;s
              Gemini models to produce responses and decide which tools to
              run.
            </li>
            <li>
              <strong>Google Cloud Run / Cloud Workflows</strong> — host the
              application and execute the modeling engines.
            </li>
          </ul>
          <p>
            Public data sources the agent queries on your behalf (for
            example NOAA, USGS, FEMA, USACE, GBIF) receive only the query
            parameters needed to fulfil your request (such as a bounding box
            or place name), never your identity.
          </p>
        </section>

        <section>
          <h2>Your choices</h2>
          <ul>
            <li>
              You can use GRACE-2 anonymously today; no account is required.
            </li>
            <li>
              Clearing your browser&rsquo;s localStorage for this site
              discards your anonymous session identifiers; a fresh session is
              created on your next visit.
            </li>
            <li>
              You can request deletion of Cases, chat history, or generated
              artifacts associated with your session by contacting us at the
              address below.
            </li>
          </ul>
        </section>

        <section>
          <h2>Contact</h2>
          <p>
            Questions, concerns, or deletion requests:{" "}
            <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>
          </p>
        </section>

        <section>
          <h2>Changes to this policy</h2>
          <p>
            If this policy changes materially (for example when Google
            sign-in launches), we will update this page and its effective
            date.
          </p>
        </section>
      </main>

      <footer className="pp-footer">
        <a href="/">← Back to GRACE-2</a>
        <span>© 2026 GRACE-2 · Built on Google Gemini · Cloud Run · QGIS</span>
      </footer>
    </div>
  );
}
