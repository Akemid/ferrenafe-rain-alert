// Tests for `resolveViewState` (web/src/lib/view.ts) — the function
// `index.astro` calls, client-side, to decide which of the three states the
// page must show (design doc "The three states that carry weight"):
// no alert (the ordinary case), stale data with its age in the foreground,
// and a failed fetch that says so rather than rendering a blank.
//
// Each test below targets exactly one branch of `resolveViewState`, so that
// removing that branch fails exactly this test and no other. That was checked
// by removal, not by inspection: deleting the `if (!outcome.ok)` branch fails
// only the third test here.

import { describe, expect, it } from 'vitest';
import { STALE_AFTER_HOURS, resolveViewState } from '../src/lib/view';
import { sampleAlerting, sampleQuiet } from './fixtures';

describe('resolveViewState', () => {
  it('shows the ordinary state when the reading is fresh, alert or not', () => {
    const oneHourLater = new Date('2026-09-03T08:00:00-05:00');

    const quiet = resolveViewState({ ok: true, snapshot: sampleQuiet }, oneHourLater);
    const alerting = resolveViewState({ ok: true, snapshot: sampleAlerting }, oneHourLater);

    expect(quiet.kind).toBe('ok');
    expect(alerting.kind).toBe('ok');
  });

  it('puts the age in the foreground when the reading is older than the threshold, regardless of alert content', () => {
    // This is the "Last evaluated: 3 days ago" case from the design doc — it
    // must win over the ordinary rendering, not be hidden behind it.
    const threeWeeksLater = new Date('2026-09-24T14:00:00-05:00');

    const state = resolveViewState({ ok: true, snapshot: sampleQuiet }, threeWeeksLater);

    expect(state.kind).toBe('stale');
    if (state.kind !== 'stale') throw new Error('unreachable');
    expect(state.ageHours).toBeGreaterThan(STALE_AFTER_HOURS);
  });

  it('says the fetch failed rather than rendering as if nothing were wrong', () => {
    const state = resolveViewState({ ok: false, error: new Error('network unreachable') }, new Date());

    expect(state.kind).toBe('error');
  });
});
