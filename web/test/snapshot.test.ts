// Tests for `parseSnapshot` / `ageInHours` (web/src/lib/snapshot.ts).
//
// Adapted from the brief (task-8-brief.md) under three overriding rulings
// recorded in task-8-report.md:
//
// 1. `parseSnapshot` preserves the wire keys (snake_case) rather than
//    mapping to camelCase, so the brief's second test reads `.level_label`,
//    not `.levelLabel`.
// 2. The contract check asserts the parsed key set equals the contract's
//    key set exactly, in both directions — `toEqual(expect.arrayContaining(...))`
//    would pass even if the parser silently dropped a contract key.
// 3. Both fixtures are checked against the contract, not only the alerting
//    one, so a `sampleQuiet` missing a top-level key would fail here.

import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { ageInHours, parseSnapshot } from '../src/lib/snapshot';
import { sampleAlerting, sampleQuiet } from './fixtures';

const contract = JSON.parse(
  readFileSync(new URL('../../contracts/public-snapshot.json', import.meta.url), 'utf-8'),
) as {
  top_level: string[];
  alert: string[];
  recent_alert: string[];
};

describe('reading a snapshot', () => {
  it('reads exactly the keys the contract promises, no more and no fewer (sampleAlerting)', () => {
    const parsed = parseSnapshot(sampleAlerting);

    expect(new Set(Object.keys(parsed))).toEqual(new Set(contract.top_level));
    expect(new Set(Object.keys(parsed.alert!))).toEqual(new Set(contract.alert));
    expect(new Set(Object.keys(parsed.recent_alerts[0]))).toEqual(new Set(contract.recent_alert));
  });

  it('reads exactly the keys the contract promises, no more and no fewer (sampleQuiet)', () => {
    // sampleQuiet has alert: null and no recent_alerts — a fixture missing a
    // top-level key would still pass every other test in this file, which
    // is why the top-level check runs against both fixtures (ruling 3).
    const parsed = parseSnapshot(sampleQuiet);

    expect(new Set(Object.keys(parsed))).toEqual(new Set(contract.top_level));
    expect(parsed.alert).toBeNull();
    expect(parsed.recent_alerts).toEqual([]);
  });

  it('reports a quiet cycle as a level, not as an absence', () => {
    expect(parseSnapshot(sampleQuiet).alert).toBeNull();
    expect(parseSnapshot(sampleQuiet).level_label).toBe('sin riesgo');
  });

  it('reports how old the reading is', () => {
    // "Last evaluated: 3 days ago" is the most important thing on the page at
    // that moment, not an embarrassment to hide.
    expect(ageInHours(sampleQuiet, new Date('2026-09-24T14:00:00-05:00'))).toBeGreaterThan(48);
  });

  it('refuses a document from a future schema rather than rendering a blank', () => {
    expect(() => parseSnapshot({ ...sampleQuiet, schema_version: 99 })).toThrow();
  });

  it('refuses a document with no schema_version rather than treating it as version 1', () => {
    const { schema_version: _drop, ...withoutVersion } = sampleQuiet;
    expect(() => parseSnapshot(withoutVersion)).toThrow();
  });

  it('refuses a document whose schema_version is not a number', () => {
    expect(() => parseSnapshot({ ...sampleQuiet, schema_version: '1' })).toThrow();
  });

  describe('evaluated_at validation', () => {
    // Fix round 1 closed the `NaN` case (an unparseable string silently
    // compares `false` against the stale threshold, reading as fresh). Fix
    // round 2: the round-1 check was `Number.isFinite(new
    // Date(String(value)).getTime())`, which has two more gaps than that
    // single check covers — a non-string coerces through `String()` before
    // ever reaching `Date`, and a calendar date that does not exist
    // (`2026-02-30`) does not produce `NaN` at all; JS silently rolls it
    // forward to a finite, valid-looking instant (`2026-03-02`). Both are
    // the identical false-freshness defect via a different mechanism, so
    // both get their own case here, per-form, rather than one shared test.
    it.each([
      ['a number', 12345],
      ['null', null],
      ['an empty string', ''],
      ['a non-date string', 'not-a-timestamp'],
      // JS rolls this to 2026-03-02 rather than rejecting it — the actual
      // gap this round closes. `Number.isFinite(new
      // Date('2026-02-30').getTime())` is `true`.
      ['a calendar date that does not exist', '2026-02-30'],
      // Same rollover, in the exact shape the publisher emits (date, time,
      // and UTC offset together) — the round-trip check must catch it here
      // too, not only on the bare-date form above.
      ['a calendar date that does not exist, with time and offset', '2026-02-30T07:00:00-05:00'],
    ])('refuses evaluated_at when it is %s (%j)', (_label, badValue) => {
      expect(() => parseSnapshot({ ...sampleQuiet, evaluated_at: badValue })).toThrow();
    });

    it('accepts evaluated_at in exactly the format the publisher emits', () => {
      // `src/rain_alert/domain/snapshot.py::_local` calls
      // `moment.astimezone(ZoneInfo(timezone)).isoformat()` — verified
      // directly against Python: `datetime(2026, 9, 3, 12,
      // tzinfo=UTC).astimezone(ZoneInfo("America/Lima")).isoformat()` ==
      // `"2026-09-03T07:00:00-05:00"`. No trailing "Z" (Python's isoformat
      // always writes a numeric offset), no fractional seconds when there
      // are none. The tightening in this round must keep accepting exactly
      // this, or it rejects every genuine document.
      expect(() => parseSnapshot({ ...sampleQuiet, evaluated_at: '2026-09-03T07:00:00-05:00' })).not.toThrow();
      // Python's isoformat() does include microseconds when non-zero
      // (confirmed: `datetime(..., 30, 15, 123456, ...).isoformat()` ==
      // "...T07:30:15.123456-05:00"), so that shape must also pass.
      expect(() => parseSnapshot({ ...sampleQuiet, evaluated_at: '2026-09-03T07:30:15.123456-05:00' })).not.toThrow();
    });
  });
});
