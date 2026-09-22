/**
 * Decides which of the three states `index.astro` must show, after fetching
 * `/data/status.json` at runtime (design doc "The three states that carry
 * weight"). Kept separate from the DOM-updating script so the decision
 * itself is testable without a browser.
 */

import { ageInHours, type Snapshot } from './snapshot';

/**
 * The cycle runs every 6 hours (design.md section 3.4). Two missed cycles
 * (12 hours) is treated as stale — one missed cycle could be an ordinary
 * transient failure already retried by the next run, but two in a row means
 * the reading in front of the resident is no longer describing the current
 * situation.
 */
export const STALE_AFTER_HOURS = 12;

export type FetchOutcome = { ok: true; snapshot: Snapshot } | { ok: false; error: unknown };

export type ViewState =
  | { kind: 'ok'; snapshot: Snapshot }
  | { kind: 'stale'; snapshot: Snapshot; ageHours: number }
  | { kind: 'error'; error: unknown };

export function resolveViewState(
  outcome: FetchOutcome,
  now: Date,
  staleAfterHours: number = STALE_AFTER_HOURS,
): ViewState {
  if (!outcome.ok) {
    return { kind: 'error', error: outcome.error };
  }

  const age = ageInHours(outcome.snapshot, now);
  if (age > staleAfterHours) {
    return { kind: 'stale', snapshot: outcome.snapshot, ageHours: age };
  }

  return { kind: 'ok', snapshot: outcome.snapshot };
}
