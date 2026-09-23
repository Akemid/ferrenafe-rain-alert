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
// sent. The DOM-level assertion below is what caught it: the extraction of
// the page's inline `<script>` into `page.ts` is what made the defect
// testable at all, because the decision object it replaced could report the
// right state while the DOM still said "no alert has been sent".

import { describe, expect, it } from 'vitest';
import { renderPage, type PageElements } from '../src/lib/page';
import type { Snapshot } from '../src/lib/snapshot';
import { resolveViewState } from '../src/lib/view';
import {
  sampleAlerting,
  sampleBlind,
  sampleDegradedForecastOnly,
  sampleDegradedSenamhiOnly,
  sampleQuiet,
} from './fixtures';

function makeElements(): PageElements {
  return {
    status: document.createElement('section'),
    recentList: document.createElement('ul'),
    recentEmpty: document.createElement('p'),
    recentError: document.createElement('p'),
  };
}

/** One hour after every fixture's `evaluated_at` — inside the freshness
 * threshold, so `resolveViewState` returns `ok`. */
const FRESH = new Date('2026-09-03T08:00:00-05:00');
/** Three weeks after it — well past `STALE_AFTER_HOURS`. */
const STALE = new Date('2026-09-24T14:00:00-05:00');

function render(snapshot: Snapshot, now: Date): PageElements {
  const elements = makeElements();
  renderPage(elements, resolveViewState({ ok: true, snapshot }, now));
  return elements;
}

function headingText(elements: PageElements): string {
  const heading = elements.status.querySelector('.status-level');
  return heading?.textContent ?? '';
}

function noticeText(elements: PageElements): string | null {
  const notice = elements.status.querySelector('.degraded-notice');
  return notice === null ? null : (notice.textContent ?? '');
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

describe('renderPage — a cycle that read nothing must not render as an all-clear', () => {
  // `RiskEvaluator.evaluate` requires at least one available source in every
  // risk branch, so when both are down it falls through to `Level.NONE`. The
  // level is the default, not a finding. The design doc's rule for the failed
  // fetch applies here too: "A page that fails quietly and shows 'no risk' is
  // worse than one that does not load."

  it('says in the HEADING that the risk could not be evaluated, not "sin riesgo"', () => {
    // The caveat must be where the reassurance is. A resident who reads only
    // the big bold line must not come away reassured.
    const elements = render(sampleBlind, FRESH);

    expect(headingText(elements)).toBe('No se pudo evaluar el riesgo');
    expect(headingText(elements)).not.toBe(sampleBlind.level_label);
  });

  it('does not paint the blind cycle with the ordinary green all-clear treatment', () => {
    // `.level-none .status-level { color: #1a7f37 }` is the green. The
    // `status-degraded` class is what the stylesheet uses to override it.
    const elements = render(sampleBlind, FRESH);

    expect(elements.status.classList.contains('status-degraded')).toBe(true);
  });

  it('does not claim no alert is active when it could not check', () => {
    // "No hay ninguna alerta activa en este momento" is a claim about the
    // world. A blind cycle can only report what it did: it sent nothing.
    const elements = render(sampleBlind, FRESH);

    expect(elements.status.textContent).not.toContain('No hay ninguna alerta activa en este momento.');
    expect(elements.status.textContent).toContain('No se envió ninguna alerta en esta evaluación.');
  });

  it('keeps the plain heading and the green treatment when every source answered', () => {
    // The inverse case, so the branch above cannot be satisfied by always
    // showing the caveat.
    const elements = render(sampleQuiet, FRESH);

    expect(headingText(elements)).toBe('sin riesgo');
    expect(elements.status.classList.contains('status-degraded')).toBe(false);
    expect(noticeText(elements)).toBeNull();
    expect(elements.status.textContent).toContain('No hay ninguna alerta activa en este momento.');
  });
});

describe('renderPage — the degraded notice is worded from `sources`, the only field that says which', () => {
  // `degraded` is an OR over the two source statuses (`risk.py`), so it
  // cannot distinguish "one source failed" from "both did". Wording the
  // notice from `degraded` alone asserts something the page does not know.

  it('names BOTH sources when neither was available', () => {
    const elements = render(sampleBlind, FRESH);

    expect(noticeText(elements)).toBe(
      'Ninguna fuente de información estuvo disponible en esta evaluación: no se pudo consultar ' +
        'el aviso del SENAMHI ni el pronóstico del tiempo. Este resultado no confirma que no haya riesgo.',
    );
  });

  it('names SENAMHI, and only SENAMHI, when the forecast answered and SENAMHI did not', () => {
    const elements = render(sampleDegradedForecastOnly, FRESH);

    expect(noticeText(elements)).toBe(
      'Esta evaluación se hizo con información parcial: no se pudo consultar el aviso del SENAMHI. ' +
        'Solo se usó el pronóstico del tiempo.',
    );
  });

  it('names the forecast, and only the forecast, when SENAMHI answered and the forecast did not', () => {
    // The mirror of the case above. A single hardcoded sentence cannot pass
    // both, which is the point: the wording has to read `sources`.
    const elements = render(sampleDegradedSenamhiOnly, FRESH);

    expect(noticeText(elements)).toBe(
      'Esta evaluación se hizo con información parcial: no se pudo consultar el pronóstico del tiempo. ' +
        'Solo se usó el aviso del SENAMHI.',
    );
  });

  it('still warns, in general terms, if `degraded` is set while both sources report available', () => {
    // The publisher should never emit this. If it ever does, the page must
    // fail toward the warning, not toward silence.
    const inconsistent: Snapshot = { ...sampleQuiet, degraded: true };

    const elements = render(inconsistent, FRESH);

    expect(noticeText(elements)).toBe(
      'Esta evaluación se hizo con información parcial: alguna fuente de información no estuvo disponible.',
    );
  });
});

describe('renderPage — the stale path renders the same fields as the fresh one', () => {
  // `renderStale` used to rebuild the section from scratch, a subset of what
  // `renderOk` shows. Every field it forgot vanished silently. There is one
  // rendering path now; these tests pin the fields that were being dropped.

  it('keeps the degraded notice on a stale page', () => {
    const elements = render(sampleBlind, STALE);

    const notice = noticeText(elements);
    expect(notice, 'no .degraded-notice was rendered on the stale page at all').not.toBeNull();
    expect(notice).toContain('no se pudo consultar el aviso del SENAMHI ni el pronóstico del tiempo');
  });

  it('keeps the degraded heading treatment on a stale page', () => {
    const elements = render(sampleBlind, STALE);

    expect(headingText(elements)).toBe('No se pudo evaluar el riesgo');
    expect(elements.status.classList.contains('status-degraded')).toBe(true);
  });

  it('keeps the alert body — the Recomendaciones checklist — on a stale page', () => {
    // A cycle that stalls 13 hours into an IMMINENT event: the checklist is
    // the only actionable content on the page, and it was being discarded.
    const elements = render(sampleAlerting, STALE);

    expect(elements.status.textContent).toContain('Recomendaciones:');
    expect(elements.status.textContent).toContain('Clear roof drains');
    expect(elements.status.textContent).toContain(sampleAlerting.alert!.title);
  });

  it('keeps the reasons on a stale page', () => {
    const elements = render(sampleAlerting, STALE);

    expect(elements.status.textContent).toContain('Aviso oficial del SENAMHI, nivel naranja');
  });

  it('still puts the age in the foreground, as the first thing in the section', () => {
    const elements = render(sampleQuiet, STALE);

    const first = elements.status.firstElementChild;
    expect(first?.className).toBe('stale-warning');
    expect(first?.textContent).toContain('día(s)');
    expect(elements.status.classList.contains('status-stale')).toBe(true);
  });

  it('marks the level as the last one known, not as the current one', () => {
    const elements = render(sampleQuiet, STALE);

    expect(headingText(elements)).toBe('Último nivel conocido: sin riesgo');
  });

  it('does not claim no alert is active "en este momento" from three-week-old data', () => {
    // The merged rendering path introduced this sentence on the stale path,
    // where the old `renderStale` rendered no alert paragraph at all — the
    // exact risk of collapsing two paths into one. The rule is the one
    // already written for an incomplete cycle: "No hay ninguna alerta activa
    // en este momento" is a present-tense claim about the world, and a cycle
    // that last ran 21 days ago is no more entitled to make it than one that
    // read nothing.
    const elements = render(sampleQuiet, STALE);

    expect(elements.status.textContent).not.toContain('No hay ninguna alerta activa en este momento.');
    expect(elements.status.textContent).toContain('No se envió ninguna alerta en esta evaluación.');
  });

  it('does not paint a stale page with the level colour', () => {
    // A two-day-old "sin riesgo" in green reads as a current all-clear.
    const elements = render(sampleQuiet, STALE);

    expect(elements.status.classList.contains('level-none')).toBe(false);
  });
});

describe('renderPage — the level class the stylesheet colours', () => {
  // `levelClass` builds `level-${level}` and the stylesheet defines
  // `.level-none` / `.level-prepare` / `.level-imminent`. Nothing asserted
  // either half, so renaming one side left the loudest signal on the page —
  // "riesgo inminente" in red — silently rendering in default black.

  // The stylesheet half of this pairing — that `index.astro` actually
  // defines a rule for each of these class names — is asserted in
  // `markup.test.ts`, which reads the `.astro` source in the node
  // environment. Both halves are needed: either side renaming alone breaks
  // the colour.

  it.each([
    ['none', sampleQuiet],
    ['prepare', { ...sampleDegradedForecastOnly, sources: { senamhi: 'available', open_meteo: 'available' }, degraded: false }],
    ['imminent', sampleAlerting],
  ] as const)('puts class "level-%s" on the status section', (level, snapshot) => {
    const elements = render(snapshot as Snapshot, FRESH);

    expect(elements.status.className).toBe(`level-${level}`);
  });
});

describe('renderPage — timestamps are shown the way the alert message shows them', () => {
  // `domain/template.py`'s `_LOCAL_TIME_FORMAT` is "%d/%m/%Y %H:%M", so the
  // SMS a resident receives says "del 03/09/2026 07:00". The page showed the
  // same instant to the same person as "2026-09-03T07:00:00-05:00". What is
  // published does not change; this is a render-time format.

  it('shows the evaluation time as dd/mm/yyyy hh:mm', () => {
    const elements = render(sampleQuiet, FRESH);

    expect(elements.status.textContent).toContain('Última evaluación: 03/09/2026 07:00');
    expect(elements.status.textContent).not.toContain('2026-09-03T07:00:00-05:00');
  });

  it('shows it that way on the stale path too', () => {
    const elements = render(sampleQuiet, STALE);

    expect(elements.status.textContent).toContain('Última evaluación: 03/09/2026 07:00');
    expect(elements.status.textContent).not.toContain('2026-09-03T07:00:00-05:00');
  });

  it('shows the sent time of each recent alert that way', () => {
    const elements = render(sampleAlerting, FRESH);

    expect(elements.recentList.textContent).toContain('03/09/2026 07:00 — Alerta de lluvias');
  });

  it('does NOT convert the published instant to the reader\'s timezone', () => {
    // The publisher already converted to America/Lima
    // (`domain/snapshot.py::_local`). Re-reading it through `new Date()` and
    // formatting in the browser's zone would move the time for anyone not in
    // Peru, so the formatter reads the string's own fields instead.
    const elements = render({ ...sampleQuiet, evaluated_at: '2026-09-03T07:00:00+09:00' }, new Date('2026-09-03T00:00:00Z'));

    expect(elements.status.textContent).toContain('Última evaluación: 03/09/2026 07:00');
  });

  it('shows an unparseable recent-alert timestamp verbatim rather than inventing one', () => {
    // `recent_alerts[].sent_at` is not validated by the parser. A bad value
    // must degrade visibly, as it did before, not silently become a date.
    const elements = render(
      { ...sampleAlerting, recent_alerts: [{ ...sampleAlerting.recent_alerts[0], sent_at: 'desconocido' }] },
      FRESH,
    );

    expect(elements.recentList.textContent).toContain('desconocido — Alerta de lluvias');
  });
});
