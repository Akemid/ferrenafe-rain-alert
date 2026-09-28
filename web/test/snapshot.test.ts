// Tests for `parseSnapshot` / `ageInHours` (web/src/lib/snapshot.ts).
//
// Three rulings shape these tests, and each one is here because the obvious
// alternative would have passed while the parser was wrong:
//
// 1. `parseSnapshot` preserves the wire keys (snake_case) rather than
//    mapping to camelCase, so the tests read `.level_label`, not
//    `.levelLabel`. The contract file is what both the publisher and this
//    page assert against; a translation layer is one more thing to drift.
// 2. The contract check asserts the parsed key set EQUALS the contract's key
//    set, in both directions. `toEqual(expect.arrayContaining(...))` would
//    pass even if the parser silently dropped a key the contract promises.
// 3. Both fixtures are checked against the contract, not only the alerting
//    one, so a `sampleQuiet` missing a top-level key would fail here.
// 4. The contract's VALUE domains are read the same way its key sets are.
//    `SUPPORTED_LEVELS` in `snapshot.ts` used to be this page's own literal
//    list of the three levels — a second list that happened to agree with
//    `contracts/public-snapshot.json` and with
//    `domain/values.py::Level`, with nothing failing when they stopped
//    agreeing. That is the key-set problem again, one level down. The
//    contract is now the single source for the level list in this file: the
//    constant is asserted against it, and the accept/reject cases below are
//    generated from it, so this file contains no hand-written level list to
//    drift.

import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { SUPPORTED_LEVELS, ageInHours, parseSnapshot } from '../src/lib/snapshot';
import { sampleAlerting, sampleQuiet } from './fixtures';

const contract = JSON.parse(
  readFileSync(new URL('../../contracts/public-snapshot.json', import.meta.url), 'utf-8'),
) as {
  top_level: string[];
  alert: string[];
  recent_alert: string[];
  values: {
    level: string[];
    // Declared, and deliberately not asserted against anything here:
    // `parseSnapshot` constrains `sources.senamhi` / `sources.open_meteo` to
    // `string` and no further, so this page has no list of status values for
    // the contract to disagree with. `page.ts::describeSources` does compare
    // against the literal `'available'`, treating everything else as
    // unavailable — a drift there fails toward showing the degraded notice,
    // which is the safe direction, unlike a drifting level (which fails
    // toward an uncoloured page). Wiring this one up is a separate decision,
    // not something to slip in here.
    source_status: string[];
  };
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

  describe('level validation', () => {
    // `level` is the only field that reaches the page as a CSS class name
    // (`level-${level}`), and the stylesheet only colours the values the
    // contract lists. Any other string produced an unstyled section that
    // renders in default black.
    //
    // The contract's `values.level` is the authority on both sides: the
    // Python publisher asserts it against `domain/values.py::Level` (derived
    // from the enum, so adding a level fails that test rather than passing
    // silently), and this page asserts its own whitelist against it here.
    // Change either list alone and this fails.
    it('reads exactly the levels the contract promises, no more and no fewer', () => {
      expect(new Set(SUPPORTED_LEVELS)).toEqual(new Set(contract.values.level));
    });

    // Generated from the contract, not from a second hand-written list, so
    // there is nothing in this file for the contract to drift away from. A
    // level added to the contract alone arrives here as a new case and fails
    // because the parser rejects it.
    it.each(contract.values.level)('accepts the contract level %j', (level) => {
      expect(parseSnapshot({ ...sampleQuiet, level }).level).toBe(level);
    });

    it.each([
      ['a level the stylesheet does not colour', 'critical'],
      ['a level in the wrong case', 'NONE'],
      ['an empty string', ''],
      ['a number', 1],
      ['null', null],
      // Without an explicit `typeof` guard, `String(['imminent'])` is
      // `'imminent'` — a single-element array collapses to its own element,
      // so a whitelist applied after `String()` would let this through.
      ['an array whose toString() is a valid level', ['imminent']],
    ])('refuses %s (%j) rather than rendering an uncoloured page', (_label, level) => {
      expect(() => parseSnapshot({ ...sampleQuiet, level })).toThrow();
    });
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
      //
      // Fix round 3 note: this bare form has no `T` and no offset, so it is
      // rejected by `ISO_OFFSET_TIMESTAMP`'s shape alone — it never reaches
      // the round-trip arithmetic. It proves the regex rejects a bare date,
      // not that the round-trip check works. The next case does exercise
      // the round-trip: it has the full shape and is still rejected.
      ['a calendar date that does not exist', '2026-02-30'],
      // Same rollover, in the exact shape the publisher emits (date, time,
      // and UTC offset together) — this is the one case that genuinely
      // exercises the round-trip arithmetic, not just the regex shape.
      ['a calendar date that does not exist, with time and offset', '2026-02-30T07:00:00-05:00'],
    ])('refuses evaluated_at when it is %s (%j)', (_label, badValue) => {
      expect(() => parseSnapshot({ ...sampleQuiet, evaluated_at: badValue })).toThrow();
    });

    it('refuses a value that is not a string even when its toString() matches the ISO shape', () => {
      // Fix round 3: mutation testing found that removing the
      // `typeof value !== 'string'` guard left the whole suite green — the
      // "number" case above passes because `RegExp.exec` calls `ToString`
      // internally and `String(12345)` ("12345") fails the shape regex
      // regardless of the guard, so that test never actually exercised the
      // guard. A single-element array's `toString()` collapses to its own
      // element with no brackets or commas
      // (`String(['2026-09-03T07:00:00-05:00'])` ===
      // `'2026-09-03T07:00:00-05:00'`, verified in Node), which DOES match
      // `ISO_OFFSET_TIMESTAMP` and round-trips cleanly — so only the
      // explicit `typeof` guard rejects it. Without the guard this would
      // parse as if `evaluated_at` were a string, when the value flowing
      // through the rest of the system is actually an array.
      expect(() => parseSnapshot({ ...sampleQuiet, evaluated_at: ['2026-09-03T07:00:00-05:00'] })).toThrow();
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
