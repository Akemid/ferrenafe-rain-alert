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

  it('refuses a document whose evaluated_at is not a valid timestamp', () => {
    // Fix round 1, task 8 audit finding: `ageInHours` feeds `evaluated_at`
    // into `new Date(...).getTime()`. An unparseable value produces `NaN`,
    // and `NaN > staleAfterHours` is `false` in JS — so a corrupt timestamp
    // would silently be treated as fresh (never stale), the same class of
    // false-reassurance defect as the recent-alerts one. Rejected here, at
    // parse time, the same way an unsupported schema_version already is.
    expect(() => parseSnapshot({ ...sampleQuiet, evaluated_at: 'not-a-timestamp' })).toThrow();
  });
});
