// Assertions about `src/pages/index.astro` itself — its stylesheet and its
// no-JavaScript fallback — rather than about what `page.ts` renders.
//
// The DOM half of the class-name coupling — that `renderPage` puts
// `level-none` / `level-prepare` / `level-imminent` on the section — is
// asserted in `page.test.ts`. Both halves have to hold: a rendered class the
// stylesheet does not define, or a stylesheet rule nothing renders, means the
// loudest signal on the page ("riesgo inminente" in red) silently falls back
// to default black while every suite stays green.
//
// Both halves held, and it fell back to default black anyway. What these
// assertions cannot see is the build that sits between them: Astro rewrote
// every selector here to require an attribute it puts only on elements written
// in the template, and the level heading is not one — it is created by
// `page.ts`. This file compares names in the SOURCE, so it is blind to any
// transform applied after it. `built-styles.test.ts` is the one that asks
// whether these rules can reach the elements they name, against `dist/` after
// a real build; these assertions remain because that one only proves a rule
// can match, not that the rule exists at all.
//
// The path is resolved through `fileURLToPath`, NOT through
// `readFileSync(new URL('../src/pages/index.astro', import.meta.url))`, which
// `snapshot.test.ts` uses for the contract JSON. Vite rewrites the
// `new URL(..., import.meta.url)` form into an asset URL when the target is
// inside the project root, so that spelling fails here with "The URL must be
// of scheme file" — the contract file escapes it only because it lives
// outside `web/`.

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const markup = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'pages', 'index.astro'), 'utf-8');

describe('index.astro stylesheet — every class the renderer emits must be defined here', () => {
  it.each(['level-none', 'level-prepare', 'level-imminent'])('colours ".%s"', (className) => {
    expect(markup).toContain(`.${className} .status-level`);
  });

  it('overrides the green all-clear when the cycle was incomplete', () => {
    // `.level-none .status-level { color: #1a7f37 }` is the green a resident
    // reads as "all clear". A cycle that read nothing must not get it, and
    // two classes beat one on specificity, so this rule wins.
    expect(markup).toContain('.status-degraded.level-none .status-level');
  });

  it('styles the degraded notice as a warning, not as a footnote', () => {
    expect(markup).toContain('.degraded-notice');
  });

  it('styles the stale and error frames', () => {
    expect(markup).toContain('.status-stale');
    expect(markup).toContain('.status-error');
    expect(markup).toContain('.stale-warning');
  });
});

describe('index.astro — the page must resolve even when its script never runs', () => {
  // Every state is rendered by a module script. If the bundle 404s or
  // JavaScript is off, "Cargando el estado actual…" stays on screen forever:
  // it fails in the right direction but never resolves.

  it('tells the reader plainly that the page needs JavaScript', () => {
    expect(markup).toContain('<noscript>');
    expect(markup).toContain('Esta página necesita JavaScript');
  });

  it('hides the loading line in that case, so the two messages do not contradict each other', () => {
    expect(markup).toContain('#status-loading');
  });
});
