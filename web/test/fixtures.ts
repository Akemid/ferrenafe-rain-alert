// Fixtures for the parser and rendering tests, derived from the real values
// in `tests/unit/domain/test_snapshot.py` (which build these documents by
// running the actual `RunAlertCycle` and `build_snapshot`, not by hand),
// not from the illustrative example in the design doc. Where the two
// disagreed, the contract and the real emitted documents win: the design
// doc's example omits `recent_alerts`, which `contracts/public-snapshot.json`
// promises and every real document carries.
//
// - `evaluated_at` / `window` values: from
//   `TestEveryPublishedInstantIsConvertedToTheConfiguredLocalTimezone`.
// - `level`, `level_label`, `reasons`, `alert.composed_by`: from
//   `TestTheSnapshotSaysWhatThePageShows`.
// - `recent_alerts[0]` shape: from
//   `TestRecentAlertsArePublishedFromWhatWasActuallySent`.
// - `alert.title` / `alert.body`: not asserted verbatim by any Python test,
//   so these are reconstructed from `domain/template.py`'s
//   `MessageComposer.compose` (title: `f"Alerta de lluvias — {city} —
//   {level_label}"`; body: the `Ciudad:` / `Nivel:` / `Ventana:` /
//   `Motivos:` / `Recomendaciones:` lines it assembles) using the same
//   warning fixture (`_alerting_cycle_deps`) and the same
//   `DEFAULT_CONFIG.checklist`.

import type { Snapshot } from '../src/lib/snapshot';

export const sampleQuiet: Snapshot = {
  schema_version: 1,
  city: 'Ferreñafe',
  evaluated_at: '2026-09-03T07:00:00-05:00',
  level: 'none',
  level_label: 'sin riesgo',
  window: {
    start: '2026-09-03T07:00:00-05:00',
    end: '2026-09-05T07:00:00-05:00',
  },
  reasons: [],
  sources: { senamhi: 'available', open_meteo: 'available' },
  degraded: false,
  alert: null,
  recent_alerts: [],
};

export const sampleAlerting: Snapshot = {
  schema_version: 1,
  city: 'Ferreñafe',
  evaluated_at: '2026-09-03T07:00:00-05:00',
  level: 'imminent',
  level_label: 'riesgo inminente',
  window: {
    start: '2026-09-03T07:00:00-05:00',
    end: '2026-09-05T07:00:00-05:00',
  },
  reasons: ['Aviso oficial del SENAMHI, nivel naranja: PRECIPITACIONES EN LA COSTA.'],
  sources: { senamhi: 'available', open_meteo: 'available' },
  degraded: false,
  alert: {
    title: 'Alerta de lluvias — Ferreñafe — riesgo inminente',
    body: [
      'Ciudad: Ferreñafe',
      'Nivel: riesgo inminente',
      'Ventana: del 03/09/2026 07:00 al 05/09/2026 07:00 (hora local, America/Lima)',
      'Motivos:',
      '- Aviso oficial del SENAMHI, nivel naranja: PRECIPITACIONES EN LA COSTA.',
      'Recomendaciones:',
      '- Store water',
      '- Secure loose objects',
      '- Clear roof drains',
    ].join('\n'),
    valid_until: '2026-09-05T07:00:00-05:00',
    composed_by: 'template',
    sent: true,
  },
  recent_alerts: [
    {
      level: 'imminent',
      sent_at: '2026-09-03T07:00:00-05:00',
      title: 'Alerta de lluvias — Ferreñafe — riesgo inminente',
      composed_by: 'template',
    },
  ],
};

// A cycle that read NOTHING. Every risk branch in
// `src/rain_alert/domain/risk.py` requires at least one available source, so
// when both are down the evaluator falls through to its default and emits
// `level: "none"` — a level that carries no information at all. This is the
// verbatim document that case produces; the page must never present it as an
// affirmative all-clear.
export const sampleBlind: Snapshot = {
  schema_version: 1,
  city: 'Ferreñafe',
  evaluated_at: '2026-09-03T07:00:00-05:00',
  level: 'none',
  level_label: 'sin riesgo',
  window: {
    start: '2026-09-03T07:00:00-05:00',
    end: '2026-09-05T07:00:00-05:00',
  },
  reasons: [],
  sources: { senamhi: 'unavailable', open_meteo: 'unavailable' },
  degraded: true,
  alert: null,
  recent_alerts: [],
};

// One source down, the other up: `RiskEvaluator._prepare_degraded` — SENAMHI
// unreachable, the forecast alone over the stricter degraded threshold.
// `SenamhiUnavailableReason` renders as `None` in `render_reason_es` (the
// body states it in its own sentence), so it is absent from `reasons` while
// `sources.senamhi` still records it. The page's wording must come from
// `sources`, which is the only field that says WHICH source failed.
export const sampleDegradedForecastOnly: Snapshot = {
  schema_version: 1,
  city: 'Ferreñafe',
  evaluated_at: '2026-09-03T07:00:00-05:00',
  level: 'prepare',
  level_label: 'prepárate',
  window: {
    start: '2026-09-03T07:00:00-05:00',
    end: '2026-09-05T07:00:00-05:00',
  },
  reasons: ['Se pronostican 28.0 mm de lluvia acumulada en 48 horas, con una probabilidad máxima de 75 %.'],
  sources: { senamhi: 'unavailable', open_meteo: 'available' },
  degraded: true,
  alert: {
    title: 'Alerta de lluvias — Ferreñafe — prepárate',
    body: [
      'Ciudad: Ferreñafe',
      'Nivel: prepárate',
      'Ventana: del 03/09/2026 07:00 al 05/09/2026 07:00 (hora local, America/Lima)',
      'Motivos:',
      '- Se pronostican 28.0 mm de lluvia acumulada en 48 horas, con una probabilidad máxima de 75 %.',
      'La fuente oficial SENAMHI no estuvo disponible durante esta evaluación; esta alerta se generó solo con el pronóstico de Open-Meteo.',
      'Recomendaciones:',
      '- Store water',
      '- Secure loose objects',
      '- Clear roof drains',
    ].join('\n'),
    valid_until: '2026-09-05T07:00:00-05:00',
    composed_by: 'template',
    sent: true,
  },
  recent_alerts: [],
};

// The mirror case: the forecast is the source that failed. Same shape, other
// side — the notice must name the forecast here and SENAMHI above, so a
// single hardcoded sentence cannot satisfy both.
export const sampleDegradedSenamhiOnly: Snapshot = {
  ...sampleDegradedForecastOnly,
  sources: { senamhi: 'available', open_meteo: 'unavailable' },
  reasons: ['Aviso oficial del SENAMHI, nivel naranja: PRECIPITACIONES EN LA COSTA.'],
};
