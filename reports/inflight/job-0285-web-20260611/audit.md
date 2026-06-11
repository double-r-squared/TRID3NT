# job-0285 — web — Landing page + Privacy policy (kickoff, frozen)

**Specialist:** web · **Model:** Fable 5 (MAX effort) · **Date:** 2026-06-11

## Mission (user-directed, verbatim intent)

"add a landing page and privacy policy page ... for design make it visually
interesting and it's a hero page so make it impactful make it modern and
highlight use of google agent (Gemini)".

## Deliverables

1. **Landing page** at "/" for first-time visitors: hero (bold headline +
   Gemini highlight + "Launch GRACE-2" CTA → /app), CSS-only visual interest
   (gradients/glassmorphism/animated accents), real product screenshots as
   showcase imagery, 3-5 feature cards, footer with Privacy link + stack
   credit. Dark map-toned aesthetic.
2. **Privacy policy** at "/privacy": plain-language sections (Data we
   collect / How we use it / Storage & third parties / Your choices /
   Contact), honest content (anonymous sessions today / Google sign-in
   coming; MongoDB Atlas; GCS; Gemini via Vertex AI; no sale of personal
   data; contact natealmanza3@gmail.com; effective 2026-06-11). This URL is
   the OAuth-consent-screen privacy URL for the sprint-13.5 deploy.
3. **Routing at the entry level** (main.tsx + tiny path switch — App.tsx is
   owned by a concurrent job and MUST NOT be edited): "/" → landing ONLY
   for visitors without an existing GRACE-2 session key, auto-passthrough
   to the app otherwise (protects Playwright live-verify tooling + the
   user's testing flow); "/app" → app always; nice-to-have "Resume session"
   CTA variant.

## Constraints

- Web only. DO NOT edit App.tsx / Chat.tsx / anything existing under
  web/src/components/. New files + main.tsx (+ index.html/global.css
  additions only — global.css was concurrently dirty, so avoided entirely).
- No router library, no UI kits, no heavy deps. Keep bundle lean.
- Full vitest green; new tests for the path switch + passthrough rule.
- `git add` only this job's files.

## Acceptance

- Full vitest suite green.
- Screenshots (1440x900 + 390x844) of landing and privacy under
  reports/inflight/job-0285-web-20260611/evidence/.
- audit.md + report.md + STATE=DONE; commit "job-0285: …" with Fable
  Co-Authored-By trailer.
