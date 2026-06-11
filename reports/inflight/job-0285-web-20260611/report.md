# job-0285 — report — Landing page + Privacy policy

**STATE:** DONE · **Specialist:** web (Fable 5, MAX) · **Date:** 2026-06-11

## What landed

### 1. Entry-level path switch — `web/src/EntryRouter.tsx`

A ~120-line dependency-free switch mounted by `main.tsx` (App.tsx untouched,
per the hard constraint). **The passthrough rule, as implemented:**

| Path        | Renders                                                        |
|-------------|----------------------------------------------------------------|
| `/`         | **Landing** only when NO GRACE-2 session key exists in localStorage; **App** (passthrough) when any key exists |
| `/app`, `/app/*` | App, always                                               |
| `/privacy`  | Privacy policy, always                                         |
| `/landing`  | Landing, always (explicit preview; "Resume session" CTA variant when a session exists) |
| anything else | App (legacy deep-link behavior preserved)                    |

Session keys checked (exported as `GRACE2_SESSION_KEYS`):
`grace2.session_id` (ws.ts), `grace2.anonymous_user_id` (ws.ts),
`grace2_anonymous_accepted` (AuthGate — **also the key every Playwright
live-verify tool under web/tools/ seeds via addInitScript**, which is what
keeps `http://host:5173/` rendering the app for all existing tooling).
localStorage-throwing (privacy mode) → treated as fresh visitor → landing;
the CTA at `/app` still always works.

App + Privacy are `React.lazy` code-split; Landing is eager (small, must
paint instantly). Navigation is full-page `<a href>` — no history listening.

### 2. Landing page — `web/src/pages/Landing.tsx` + `landing.css`

Dark map-toned hero: Gemini-gradient headline ("Ask for a flood model. /
Watch it run."), "Powered by Google Gemini" badge, gradient "Launch
GRACE-2 →" CTA (→ `/app`; "Resume session" variant), tool-pipeline chips
(`geocode_location › fetch_dem › run_model_flood_scenario › publish_layer`),
tilted browser-framed real product screenshot (SFINCS flood render, Fort
Myers). Then: stats strip (70+ tools / 2 physics engines / 70k+ structures /
1 conversation), 5 feature cards in a centered 3+2 glass grid, "The agent is
Gemini" band (function calling, streaming narration, honest retry) with two
phone-framed mobile shots, Pelicun/NSI showcase frame, bottom CTA, footer
with Privacy link + "Built on Google Gemini · Cloud Run · QGIS Server ·
MapLibre GL · MongoDB Atlas" credit. CSS-only effects: graticule grid
backdrop, drifting aurora glows, glassmorphism — all disabled under
`prefers-reduced-motion`. All selectors `.lp`-prefixed (no bleed into app).

Showcase imagery: real evidence screenshots recompressed to webp under
`web/public/landing/` (4 files, **377 KB total** vs 2.8 MB source PNGs):
flood render (job-0167), Pelicun/NSI (job-0252), mobile chat + colored
relief (job-0279).

### 3. Privacy policy — `web/src/pages/Privacy.tsx` + `privacy.css`

Effective June 11, 2026. Sections: Data we collect / How we use it /
Storage & third parties / Your choices / Contact / Changes. Honest content:
anonymous sessions today + Google sign-in coming; chat/Case data in MongoDB
Atlas; artifacts in GCS; prompts processed by Google Gemini API (Vertex AI);
Cloud Run hosting; public data sources receive query params only; **no sale
of personal data**; contact natealmanza3@gmail.com. `.pp`-prefixed styles,
measure-limited reading column.

## Tests

- Full suite: **680 passed / 0 failed** (43 files) — was 638 at kickoff;
  +38 from this job, +4 from the concurrent agent's landed work.
- New: `EntryRouter.test.tsx` (21: resolveRoute table incl. trailing-slash +
  `/application` prefix guard; hasExistingSession incl. throwing storage +
  key-name contract pin; 7 rendered-route tests with mocked App),
  `Landing.test.tsx` (9), `Privacy.test.tsx` (8).
- `tsc --noEmit`: **zero errors in this job's files**. Pre-existing errors
  in `ws.test.tsx` / `ws.stickyAnon.test.tsx` / `Chat.caseTagRouting.test.tsx`
  / `Chat.perCaseStreams.test.tsx` (not this job's; vitest is green — vitest
  does not typecheck).

## Evidence (`evidence/`)

- `landing_desktop_1440x900.png`, `landing_desktop_full.png`
- `landing_mobile_390x844.png`, `landing_mobile_full.png`
- `privacy_desktop_1440x900.png`, `privacy_desktop_full.png`
- `privacy_mobile_390x844.png`, `privacy_mobile_full.png`
- `passthrough_root_with_session.png` — **proof of the rule**: fresh context
  seeded with `grace2_anonymous_accepted` → `/` renders the APP (map +
  Cases + chat), not the landing. Script asserts and fails otherwise.
- Capture script: `web/tools/screenshot_job0285_landing_privacy.mjs`
  (live dev server, real navigation, no inject seams).

## Risks / notes

- **Key-name coupling:** if ws.ts/AuthGate rename their localStorage keys,
  `GRACE2_SESSION_KEYS` must follow or returning users see the landing once
  (CTA still reaches the app). Pinned by an explicit contract test.
- **Truly-fresh Playwright contexts that do NOT seed any key** would see the
  landing at `/`. All current tools seed `grace2_anonymous_accepted` (they
  must, for AuthGate), so none are affected today; new tools should keep
  seeding it or target `/app`.
- `index.html` and `global.css` deliberately untouched (global.css was dirty
  from the concurrent job). Page titles set via `document.title` in each
  page's `useEffect`.
- Committed only this job's files; concurrent-agent edits (App.tsx, Chat.tsx,
  components/*, global.css, …) left unstaged.
