// Fixtures for the parser tests, derived from the real values in
// `tests/unit/domain/test_snapshot.py` (which build these documents by
// running the actual `RunAlertCycle` and `build_snapshot`, not by hand),
// not from the illustrative example in the design doc — see
// `task-8-report.md` for the one disagreement found between them
// (`recent_alerts` absent from the design doc's example, present in the
// contract and in every real emitted document).
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
