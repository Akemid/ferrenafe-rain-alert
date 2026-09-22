/**
 * DOM rendering for `index.astro`, extracted from the page's inline
 * `<script>` so the three states are testable against real DOM elements
 * without a browser (fix round 1, task 8: the extraction itself is what
 * made the recent-alerts defect testable at all — see task-8-report.md).
 */

import type { RecentAlert, Snapshot } from './snapshot';
import type { ViewState } from './view';

export interface PageElements {
  status: HTMLElement;
  recentList: HTMLUListElement;
  recentEmpty: HTMLElement;
  recentError: HTMLElement;
}

function levelClass(level: string): string {
  return `level-${level}`;
}

// --- State 1: no alert is the ordinary case, not an empty state. ---
export function renderOk(elements: PageElements, snapshot: Snapshot): void {
  const { status } = elements;
  status.className = levelClass(snapshot.level);
  status.replaceChildren();

  const heading = document.createElement('p');
  heading.className = 'status-level';
  heading.textContent = snapshot.level_label;
  status.appendChild(heading);

  const evaluated = document.createElement('p');
  evaluated.textContent = `Última evaluación: ${snapshot.evaluated_at}`;
  status.appendChild(evaluated);

  if (snapshot.alert === null) {
    const none = document.createElement('p');
    none.textContent = 'No hay ninguna alerta activa en este momento.';
    status.appendChild(none);
  } else {
    const alertTitle = document.createElement('h3');
    alertTitle.textContent = snapshot.alert.title;
    status.appendChild(alertTitle);

    const alertBody = document.createElement('pre');
    alertBody.textContent = snapshot.alert.body;
    status.appendChild(alertBody);
  }

  if (snapshot.reasons.length > 0) {
    const reasonsTitle = document.createElement('p');
    reasonsTitle.textContent = 'Motivos:';
    status.appendChild(reasonsTitle);

    const reasonsList = document.createElement('ul');
    for (const reason of snapshot.reasons) {
      const item = document.createElement('li');
      item.textContent = reason;
      reasonsList.appendChild(item);
    }
    status.appendChild(reasonsList);
  }

  if (snapshot.degraded) {
    const degraded = document.createElement('p');
    degraded.className = 'degraded-notice';
    degraded.textContent =
      'Esta evaluación se generó con información parcial: una de las fuentes de datos no estuvo disponible.';
    status.appendChild(degraded);
  }
}

// --- State 2: stale data — the age is the most important thing on the
// page at that moment, so it goes first, not buried under the level. ---
export function renderStale(elements: PageElements, snapshot: Snapshot, ageHours: number): void {
  const { status } = elements;
  status.className = 'status-stale';
  status.replaceChildren();

  const ageDays = Math.floor(ageHours / 24);
  const warning = document.createElement('p');
  warning.className = 'stale-warning';
  warning.textContent =
    ageDays >= 1
      ? `Última evaluación hace ${ageDays} día(s). Esta información podría estar desactualizada.`
      : `Última evaluación hace ${Math.floor(ageHours)} hora(s). Esta información podría estar desactualizada.`;
  status.appendChild(warning);

  const evaluated = document.createElement('p');
  evaluated.textContent = `Última evaluación: ${snapshot.evaluated_at}`;
  status.appendChild(evaluated);

  const level = document.createElement('p');
  level.textContent = `Último nivel conocido: ${snapshot.level_label}`;
  status.appendChild(level);
}

// --- State 3: failed fetch — say so. A page that fails quietly and
// shows "sin riesgo" is worse than one that does not load. ---
export function renderError(elements: PageElements): void {
  const { status } = elements;
  status.className = 'status-error';
  status.replaceChildren();

  const message = document.createElement('p');
  message.textContent =
    'No se pudo cargar el estado actual. Por favor, intente de nuevo más tarde o comuníquese con Defensa Civil.';
  status.appendChild(message);
}

function renderRecentAlertsList(elements: PageElements, alerts: readonly RecentAlert[]): void {
  const { recentList } = elements;
  recentList.replaceChildren();
  for (const alert of alerts) {
    const item = document.createElement('li');
    item.textContent = `${alert.sent_at} — ${alert.title}`;
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
