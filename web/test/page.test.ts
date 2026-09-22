// @vitest-environment jsdom
//
// Tests for `renderPage` (web/src/lib/page.ts) against real DOM elements —
// the same three elements `index.astro` wires up, by the same ids — so the
// assertion is on what the page actually shows (the `hidden` property of
// the real "no se ha enviado ninguna alerta todavía" element), not on a
// data-only decision object that could pass while the DOM still lied.
//
// Fix round 1, task 8: review found that a failed fetch left
// `#recent-alerts-empty` visible, falsely claiming no alert had ever been
// sent. See task-8-report.md for the RED output this test produced against
// the (behavior-preserving) extraction of the pre-fix code in `page.ts`.

import { describe, expect, it } from 'vitest';
import { renderPage, type PageElements } from '../src/lib/page';
import { resolveViewState } from '../src/lib/view';
import { sampleAlerting, sampleQuiet } from './fixtures';

function makeElements(): PageElements {
  return {
    status: document.createElement('section'),
    recentList: document.createElement('ul'),
    recentEmpty: document.createElement('p'),
    recentError: document.createElement('p'),
  };
}

describe('renderPage — recent alerts must not assert an absence it cannot know', () => {
  it('shows the sent-alerts list when the fetch succeeded and there is one', () => {
    const elements = makeElements();
    const state = resolveViewState({ ok: true, snapshot: sampleAlerting }, new Date('2026-09-03T08:00:00-05:00'));

    renderPage(elements, state);

    expect(elements.recentEmpty.hidden).toBe(true);
    expect(elements.recentError.hidden).toBe(true);
    expect(elements.recentList.children.length).toBe(1);
  });

  it('shows "no se ha enviado ninguna alerta todavía" when the fetch succeeded and the history is genuinely empty', () => {
    const elements = makeElements();
    const state = resolveViewState({ ok: true, snapshot: sampleQuiet }, new Date('2026-09-03T08:00:00-05:00'));

    renderPage(elements, state);

    expect(elements.recentEmpty.hidden).toBe(false);
    expect(elements.recentError.hidden).toBe(true);
  });

  it('does NOT reveal the "no se ha enviado ninguna alerta todavía" placeholder when the fetch failed', () => {
    // The page does not know whether alerts have been sent — it could not
    // load anything — so it must not tell a resident that none have.
    const elements = makeElements();
    const state = resolveViewState({ ok: false, error: new Error('network unreachable') }, new Date());

    renderPage(elements, state);

    expect(elements.recentEmpty.hidden).toBe(true);
    expect(elements.recentList.children.length).toBe(0);
    expect(elements.recentError.hidden).toBe(false);
  });
});
