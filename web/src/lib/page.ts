/**
 * DOM rendering for `index.astro`, extracted from the page's inline
 * `<script>` so the three states are testable against real DOM elements
 * without a browser. The extraction is what made the recent-alerts defect
 * (fix round 1, task 8) testable at all: while the rendering lived inside
 * the `.astro` file, the only thing a test could reach was the decision
 * object, which reported the right state while the DOM still said "no alert
 * has been sent".
 *
 * There is ONE path that renders a snapshot (`renderStatus`), with a
 * staleness banner layered over it. `renderStale` used to rebuild the
 * section from scratch as a subset of `renderOk`, and every field it forgot
 * — the degraded notice, the alert body and its Recomendaciones checklist,
 * the reasons — vanished from the stale page without anything failing. A
 * second path is a second place for a field to go missing, so there is not
 * one.
 */

import { SUPPORTED_LEVELS, type Level, type RecentAlert, type Snapshot } from './snapshot';
import type { ViewState } from './view';

export interface PageElements {
  status: HTMLElement;
  recentList: HTMLUListElement;
  recentEmpty: HTMLElement;
  recentError: HTMLElement;
}

/**
 * `level-none` / `level-prepare` / `level-imminent`. `parseSnapshot` has
 * already rejected anything outside that set, and `markup.test.ts` asserts
 * `index.astro` defines a rule for each — two ends of the only coupling on
 * this page that a resident reads as colour.
 *
 * Two ends, and a third party between them: the build. Every element below is
 * made with `document.createElement`, so it carries none of the attributes
 * Astro adds to template-written markup, and a scoped stylesheet cannot reach
 * any of it. `index.astro` keeps its styles global for that reason and
 * `built-styles.test.ts` holds it to that, against the built output.
 */
function levelClass(level: Snapshot['level']): string {
  return `level-${level}`;
}

// --- Timestamps -----------------------------------------------------------

/**
 * The publisher writes instants as
 * `moment.astimezone(ZoneInfo(timezone)).isoformat()`
 * (`domain/snapshot.py::_local`) — `2026-09-03T07:00:00-05:00`. The alert
 * message the same resident receives by SMS writes the same instant as
 * `03/09/2026 07:00` (`domain/template.py::_LOCAL_TIME_FORMAT`,
 * `"%d/%m/%Y %H:%M"`). The page showed the ISO form, so the page and the
 * message disagreed about how to show one instant to one person.
 */
const PUBLISHED_TIMESTAMP = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/;

/**
 * `value` in the same format the alert message uses.
 *
 * Reads the string's OWN fields rather than going through `new Date(...)`
 * and a locale formatter: the publisher has already converted the instant to
 * the configured local timezone, and re-formatting it in the reader's
 * timezone would move the time for anyone outside Peru.
 *
 * A value that does not match is returned untouched. `evaluated_at` is
 * validated by `parseSnapshot`, but `recent_alerts[].sent_at` is not (it is
 * display-only), and a bad value there must keep degrading visibly rather
 * than becoming a plausible-looking date.
 */
function formatPublishedTimestamp(value: string): string {
  const match = PUBLISHED_TIMESTAMP.exec(value);
  if (match === null) return value;
  const [, year, month, day, hour, minute] = match;
  return `${day}/${month}/${year} ${hour}:${minute}`;
}

// --- Source availability --------------------------------------------------

/**
 * What `snapshot.sources` says. This is the only field that records WHICH
 * source failed: `degraded` is an OR over the two statuses
 * (`domain/risk.py`), so a notice worded from `degraded` alone cannot say
 * whether one source was down or both, and the page's old wording ("una de
 * las fuentes") asserted something it did not know.
 */
interface SourceAvailability {
  senamhi: boolean;
  forecast: boolean;
  /** Neither source answered: the cycle read nothing at all. */
  blind: boolean;
  /** Something is missing, by `sources` or by `degraded`. */
  incomplete: boolean;
}

function describeSources(snapshot: Snapshot): SourceAvailability {
  const senamhi = snapshot.sources.senamhi === 'available';
  const forecast = snapshot.sources.open_meteo === 'available';
  return {
    senamhi,
    forecast,
    blind: !senamhi && !forecast,
    // `degraded` is included as well as the two statuses so that a document
    // where they disagree still warns. Fail toward the warning.
    incomplete: snapshot.degraded || !senamhi || !forecast,
  };
}

/**
 * Residents know "SENAMHI" — it is the national weather service and the
 * alert message names it. They do not know "Open-Meteo", so the forecast
 * source is named by what it is, the same way `render_reason_es` does in
 * `domain/template.py` ("el pronóstico del tiempo").
 */
function degradedNoticeText(availability: SourceAvailability): string {
  if (availability.blind) {
    return (
      'Ninguna fuente de información estuvo disponible en esta evaluación: no se pudo consultar ' +
      'el aviso del SENAMHI ni el pronóstico del tiempo. Este resultado no confirma que no haya riesgo.'
    );
  }
  if (!availability.senamhi) {
    return (
      'Esta evaluación se hizo con información parcial: no se pudo consultar el aviso del SENAMHI. ' +
      'Solo se usó el pronóstico del tiempo.'
    );
  }
  if (!availability.forecast) {
    return (
      'Esta evaluación se hizo con información parcial: no se pudo consultar el pronóstico del tiempo. ' +
      'Solo se usó el aviso del SENAMHI.'
    );
  }
  // `degraded` is set while both sources report available. The publisher
  // should never emit this; if it does, warn in the terms the page can
  // actually justify.
  return 'Esta evaluación se hizo con información parcial: alguna fuente de información no estuvo disponible.';
}

/**
 * The heading — the one line a resident reads before anything else.
 *
 * A `level: "none"` from a cycle that read nothing is not a finding: every
 * risk branch in `domain/risk.py` requires at least one available source, so
 * with both down the evaluator falls through to its default. Rendering that
 * as a bold green "sin riesgo" is the failure the design doc names for the
 * failed fetch — "A page that fails quietly and shows 'no risk' is worse
 * than one that does not load" — arriving through a different door. The
 * caveat therefore goes IN the heading, where the reassurance is, not under
 * it.
 *
 * A blind cycle that somehow reports a level other than `none` keeps its
 * label: suppressing an `imminent` would be the same defect pointing the
 * other way.
 */
function headingText(snapshot: Snapshot, availability: SourceAvailability, stale: boolean): string {
  if (availability.blind && snapshot.level === 'none') {
    return 'No se pudo evaluar el riesgo';
  }
  const label = availability.incomplete
    ? `${snapshot.level_label} (evaluación incompleta)`
    : snapshot.level_label;
  return stale ? `Último nivel conocido: ${label}` : label;
}

/**
 * On a stale page the level colour is deliberately dropped: a two-day-old
 * "sin riesgo" painted green reads as a current all-clear, and the age is
 * what has to carry the page at that moment.
 */
function statusClass(snapshot: Snapshot, availability: SourceAvailability, stale: boolean): string {
  const classes = stale ? ['status-stale'] : [levelClass(snapshot.level)];
  if (availability.incomplete) classes.push('status-degraded');
  if (isBlind(snapshot, availability)) classes.push('status-blind');
  return classes.join(' ');
}

/**
 * A cycle that read nothing AND reports `none`: the level is the evaluator's
 * default, not a finding (see `headingText`). The scale shows no current
 * segment for it. A blind cycle that reports risk is not blind in this sense —
 * it keeps its level, for the same reason the heading does.
 */
function isBlind(snapshot: Snapshot, availability: SourceAvailability): boolean {
  return availability.blind && snapshot.level === 'none';
}

// --- The risk scale -------------------------------------------------------

/**
 * Short segment labels. `snapshot.level_label` is the domain's wording for the
 * same levels ("sin riesgo", "riesgo inminente"); these are the web layer's own
 * capitalised, in-the-scale spelling. Two spellings of one fact can drift, so
 * `page.test.ts` asserts that for every level the current segment's label
 * equals `level_label` case-insensitively. Keyed off `SUPPORTED_LEVELS` so a
 * level added to the contract is a type error here, not a missing segment.
 */
const SCALE_LABELS: Record<Level, string> = {
  none: 'Sin riesgo',
  prepare: 'Prepárate',
  imminent: 'Riesgo inminente',
};

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const created = document.createElement(tag);
  created.className = className;
  if (text !== undefined) created.textContent = text;
  return created;
}

/**
 * Colour is never the only signal: the current segment also carries
 * `aria-current`. Modifiers, all on the current segment unless noted:
 * - `scale-outlined`: stale. Outline only, no level colour, because a
 *   two-day-old green reads as a current all-clear.
 * - `scale-hatched`: an incomplete cycle that said `none`. Grey hatching, not
 *   green. An incomplete cycle that found risk keeps its real colour.
 * - `scale-blind` (on the list): nothing was read, so no segment is current.
 */
function buildScale(snapshot: Snapshot, availability: SourceAvailability, stale: boolean): HTMLOListElement {
  const blind = isBlind(snapshot, availability);
  const scale = element('ol', blind ? 'scale scale-blind' : 'scale');
  scale.setAttribute('aria-label', 'Escala de riesgo');

  for (const level of SUPPORTED_LEVELS) {
    const segment = element('li', `scale-segment segment-${level}`);
    if (!blind && level === snapshot.level) {
      segment.classList.add('is-current');
      segment.setAttribute('aria-current', 'true');
      if (stale) segment.classList.add('scale-outlined');
      else if (availability.incomplete && level === 'none') segment.classList.add('scale-hatched');
    }
    segment.appendChild(element('span', 'scale-bar'));
    segment.appendChild(element('span', 'scale-label', SCALE_LABELS[level]));
    scale.appendChild(segment);
  }
  return scale;
}

function paragraph(text: string, className?: string): HTMLParagraphElement {
  const element = document.createElement('p');
  if (className !== undefined) element.className = className;
  element.textContent = text;
  return element;
}

/**
 * The single rendering path. `staleAgeHours` is `null` for a fresh reading
 * and the age in hours for a stale one; everything else is rendered
 * identically either way, so no field can exist in one state and not the
 * other.
 */
function renderStatus(elements: PageElements, snapshot: Snapshot, staleAgeHours: number | null): void {
  const { status } = elements;
  const availability = describeSources(snapshot);
  const stale = staleAgeHours !== null;

  status.className = statusClass(snapshot, availability, stale);
  status.replaceChildren();

  // State 2: stale data — the age is the most important thing on the page at
  // that moment, so it goes first, above the level, not buried under it. It is
  // a direct child of `#status`, outside the summary, so it can run full width.
  if (staleAgeHours !== null) {
    const ageDays = Math.floor(staleAgeHours / 24);
    status.appendChild(
      paragraph(
        ageDays >= 1
          ? `Última evaluación hace ${ageDays} día(s). Esta información podría estar desactualizada.`
          : `Última evaluación hace ${Math.floor(staleAgeHours)} hora(s). Esta información podría estar desactualizada.`,
        'stale-warning',
      ),
    );
  }

  const summary = element('div', 'status-summary');
  summary.appendChild(
    paragraph(isBlind(snapshot, availability) ? 'Estado de la evaluación' : 'Nivel de riesgo', 'status-label'),
  );
  summary.appendChild(buildScale(snapshot, availability, stale));
  summary.appendChild(paragraph(headingText(snapshot, availability, stale), 'status-level'));

  // Directly under the heading, before any reassurance: a resident must not
  // be able to read the level without the caveat that qualifies it.
  if (availability.incomplete) {
    summary.appendChild(paragraph(degradedNoticeText(availability), 'degraded-notice'));
  }

  summary.appendChild(
    paragraph(`Última evaluación: ${formatPublishedTimestamp(snapshot.evaluated_at)}`, 'status-evaluated'),
  );
  status.appendChild(summary);

  const details = element('div', 'status-details');

  if (snapshot.alert === null) {
    // "No hay ninguna alerta activa EN ESTE MOMENTO" is a present-tense claim
    // about the world. Two cycles are not entitled to make it: one that read
    // nothing, and one whose reading is old enough to be stale — three weeks
    // on, the page has no idea what is true right now. Both can only report
    // what that cycle did.
    //
    // The stale half of this condition is not hypothetical bookkeeping: the
    // sentence reached the stale path for the first time when `renderOk` and
    // `renderStale` were merged into this function, because the old
    // `renderStale` rendered no alert paragraph at all. Collapsing two paths
    // fixes fields going missing from one of them and introduces the mirror
    // risk — a field arriving in one that was never written for it.
    details.appendChild(
      paragraph(
        availability.incomplete || stale
          ? 'No se envió ninguna alerta en esta evaluación.'
          : 'No hay ninguna alerta activa en este momento.',
        'no-alert',
      ),
    );
  } else {
    const alertBox = element('div', 'alert-box');
    alertBox.appendChild(element('h3', 'alert-title', snapshot.alert.title));
    alertBox.appendChild(element('pre', 'alert-body', snapshot.alert.body));
    details.appendChild(alertBox);
  }

  if (snapshot.reasons.length > 0) {
    const reasons = element('div', 'status-reasons');
    reasons.appendChild(paragraph('Motivos:', 'reasons-title'));

    const reasonsList = element('ul', 'reasons');
    for (const reason of snapshot.reasons) {
      reasonsList.appendChild(element('li', 'reason', reason));
    }
    reasons.appendChild(reasonsList);
    details.appendChild(reasons);
  }

  status.appendChild(details);
}

// --- State 1: no alert is the ordinary case, not an empty state. ---
export function renderOk(elements: PageElements, snapshot: Snapshot): void {
  renderStatus(elements, snapshot, null);
}

// --- State 2: stale data, rendered through the same path with the age
// banner layered on top. ---
export function renderStale(elements: PageElements, snapshot: Snapshot, ageHours: number): void {
  renderStatus(elements, snapshot, ageHours);
}

// --- State 3: failed fetch — say so. A page that fails quietly and
// shows "sin riesgo" is worse than one that does not load. ---
export function renderError(elements: PageElements): void {
  const { status } = elements;
  status.className = 'status-error';
  status.replaceChildren();

  status.appendChild(
    paragraph(
      'No se pudo cargar el estado actual. Por favor, intente de nuevo más tarde o comuníquese con Defensa Civil.',
    ),
  );
}

function renderRecentAlertsList(elements: PageElements, alerts: readonly RecentAlert[]): void {
  const { recentList } = elements;
  recentList.replaceChildren();
  for (const alert of alerts) {
    // The item's text stays exactly `${date} — ${title}`; the span only lets
    // the stylesheet set the date in bold.
    const item = document.createElement('li');
    item.appendChild(element('span', 'recent-date', formatPublishedTimestamp(alert.sent_at)));
    item.appendChild(document.createTextNode(` — ${alert.title}`));
    recentList.appendChild(item);
  }
}

export function renderRecentAlerts(elements: PageElements, snapshot: Snapshot): void {
  elements.recentError.hidden = true;
  elements.recentList.replaceChildren();
  if (snapshot.recent_alerts.length === 0) {
    elements.recentEmpty.hidden = false;
    return;
  }
  elements.recentEmpty.hidden = true;
  renderRecentAlertsList(elements, snapshot.recent_alerts);
}

/** Fix round 1, task 8: a failed fetch must not fall back to the "genuinely
 * empty" rendering above. The page does not know whether alerts have been
 * sent — it could not load anything — so it must say the history could not
 * be loaded, never that there is none. */
export function renderRecentAlertsUnavailable(elements: PageElements): void {
  elements.recentList.replaceChildren();
  elements.recentEmpty.hidden = true;
  elements.recentError.hidden = false;
}

export function renderPage(elements: PageElements, state: ViewState): void {
  if (state.kind === 'ok') {
    renderOk(elements, state.snapshot);
    renderRecentAlerts(elements, state.snapshot);
  } else if (state.kind === 'stale') {
    renderStale(elements, state.snapshot, state.ageHours);
    renderRecentAlerts(elements, state.snapshot);
  } else {
    renderError(elements);
    renderRecentAlertsUnavailable(elements);
  }
}
