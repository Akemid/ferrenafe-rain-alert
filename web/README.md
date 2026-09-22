# `web/` — the public status page

One Astro page, in Spanish, that answers one question for a resident of
Ferreñafe: **is there rain risk right now?** It renders the snapshot each
rain-alert cycle publishes, and it renders nothing it cannot verify.

The page is static. It fetches `/data/status.json` at runtime — Amplify
rewrites that path to the S3 object the cycle writes — and updates the DOM
from what comes back.

## The three states, and why the third one matters

| State | When | What the reader sees |
|---|---|---|
| A verdict | The document parsed and is recent | The level, in Spanish, with the reasons and any active alert |
| Stale | The document parsed but `evaluated_at` is more than `STALE_AFTER_HOURS` old | The verdict, marked as possibly out of date |
| Unknown | The fetch failed, the schema version is unsupported, or `evaluated_at` is not a usable instant | Plainly: the page cannot say |

The third state is the reason the other two can be trusted. This is
life-safety software, so **the page must never present the absence of
information as good news.** Two defects fixed on this branch were exactly
that failure: a failed fetch rendering the recent-alerts section as "no
alerts were sent", and a blind cycle rendering as a green all-clear. Both
looked like working pages.

## Layout

| File | What it is for |
|---|---|
| `src/lib/snapshot.ts` | Parses and validates the published document against `contracts/public-snapshot.json`. Wire keys stay snake_case on purpose. |
| `src/lib/view.ts` | Decides which of the three states to show. No DOM, so the decision is testable on its own. |
| `src/lib/page.ts` | Applies a state to real DOM elements. Extracted from the page's inline `<script>` precisely so a test can reach the rendered markup and not only the decision object. |
| `src/pages/index.astro` | The markup, the styles, and the script that wires the three modules together. |
| `test/` | Vitest over jsdom: the parser, the decision, the DOM, and the contract. |

The contract, `contracts/public-snapshot.json`, is asserted from both sides:
`test/snapshot.test.ts` reads it here, and `tests/unit/domain/test_snapshot.py`
reads it on the publisher's side. Neither can drift without the other's suite
going red.

## Commands

All run from `web/`.

| Command | What it does |
|---|---|
| `npm ci` | Install the exact tree in the committed `package-lock.json`. |
| `npm test` | **The gate.** Runs `typecheck`, then the Vitest suite. |
| `npm run typecheck` | `tsc --noEmit`. Part of `npm test`; run it alone when you want the type errors without the suite. |
| `npm run dev` | Dev server at `localhost:4321`. Note that `/data/status.json` is not served locally, so the page shows the unknown state. |
| `npm run build` | Production build to `./dist/`. |
| `npm run preview` | Serve the built output locally. |

`npm test` is not optional before a commit here. The type check is inside it
because a type error in this page is the kind of defect that renders — a page
that compiles into the wrong state still looks like a page.
