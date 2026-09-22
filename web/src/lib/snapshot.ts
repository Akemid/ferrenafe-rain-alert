/**
 * Parses and validates the public status document
 * (`contracts/public-snapshot.json`), published by `src/rain_alert/domain
 * /snapshot.py::build_snapshot` and fetched at runtime from
 * `/data/status.json`.
 *
 * Wire keys are preserved as-is (snake_case) rather than mapped to
 * camelCase. The contract exists to prevent the page and the publisher from
 * drifting apart; a translation layer here is one more thing that can drift
 * from it, for no benefit. The original brief showed a camelCase example and
 * then referred to the wire keys in its own later snippets; the contract
 * file, which both sides assert against, is the tiebreaker.
 */

export const SUPPORTED_SCHEMA_VERSION = 1;

/**
 * The closed set of risk levels, identical to
 * `src/rain_alert/domain/values.py::Level`. Also the closed set the
 * stylesheet colours (`.level-none` / `.level-prepare` / `.level-imminent`
 * in `pages/index.astro`), because `page.ts` turns this value straight into
 * a class name — so an unrecognised level does not degrade to "unstyled",
 * it degrades to "rendered in default black", which for `imminent` is the
 * page's loudest signal going silent.
 */
export const SUPPORTED_LEVELS = ['none', 'prepare', 'imminent'] as const;

export type Level = (typeof SUPPORTED_LEVELS)[number];

export interface AlertPayload {
  title: string;
  body: string;
  // display-only by assumption: never parsed into a Date or compared
  // against anything today (see parseSnapshot's evaluated_at validation
  // for what a field DOES need once something computes with it).
  valid_until: string;
  composed_by: string;
  sent: boolean;
}

export interface RecentAlert {
  level: string;
  // display-only by assumption, same as AlertPayload.valid_until — rendered
  // as raw textContent (`${sent_at} — ${title}`); an invalid value degrades
  // visibly ("null — <title>") rather than silently, but is not validated.
  sent_at: string;
  title: string;
  composed_by: string;
}

export interface Snapshot {
  schema_version: number;
  city: string;
  evaluated_at: string;
  level: Level;
  level_label: string;
  // display-only by assumption: `start`/`end` are not rendered anywhere in
  // the current page and nothing computes with them.
  window: { start: string; end: string };
  reasons: string[];
  sources: { senamhi: string; open_meteo: string };
  degraded: boolean;
  alert: AlertPayload | null;
  recent_alerts: RecentAlert[];
}

/** Thrown by `parseSnapshot` — the page must catch this and show the
 * "failed to load" state, never render a blank. */
export class SnapshotParseError extends Error {}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

const TOP_LEVEL_KEYS = [
  'schema_version',
  'city',
  'evaluated_at',
  'level',
  'level_label',
  'window',
  'reasons',
  'sources',
  'degraded',
  'alert',
  'recent_alerts',
] as const;

const ALERT_KEYS = ['title', 'body', 'valid_until', 'composed_by', 'sent'] as const;
const RECENT_ALERT_KEYS = ['level', 'sent_at', 'title', 'composed_by'] as const;

function requireKeys(value: Record<string, unknown>, keys: readonly string[], what: string): void {
  for (const key of keys) {
    if (!(key in value)) {
      throw new SnapshotParseError(`snapshot ${what} is missing required key "${key}"`);
    }
  }
}

/**
 * Matches exactly what `src/rain_alert/domain/snapshot.py::_local` emits:
 * `moment.astimezone(ZoneInfo(timezone)).isoformat()` — a numeric UTC
 * offset (never `Z`; Python's `isoformat()` always writes one), optional
 * fractional seconds when the moment carries microseconds, confirmed
 * directly against Python (`datetime(2026, 9, 3, 12,
 * tzinfo=UTC).astimezone(ZoneInfo("America/Lima")).isoformat()` ==
 * `"2026-09-03T07:00:00-05:00"`). A trailing `Z` is accepted too, purely as
 * extra tolerance — it is never narrower than what the publisher writes.
 */
const ISO_OFFSET_TIMESTAMP =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})$/;

/**
 * True only when `value` is a string in exactly the shape above **and**
 * round-trips: a calendar date that does not exist (`"2026-02-30"`) matches
 * the regex, and `Date.UTC` (like the `Date` constructor) silently rolls it
 * forward to a real one (`2026-03-02`) instead of failing — so matching the
 * shape is not enough. The instant is reconstructed from its own fields
 * and compared, field by field, against what was written; a value that
 * changes when read back is not the value the publisher wrote.
 *
 * This does not rely on `new Date(value)` at all, so it cannot inherit
 * whatever leniency a given JS engine applies to non-standard strings — the
 * calendar arithmetic is explicit here.
 */
function isValidPublishedTimestamp(value: unknown): value is string {
  if (typeof value !== 'string') return false;

  const match = ISO_OFFSET_TIMESTAMP.exec(value);
  if (!match) return false;

  const [, yearStr, monthStr, dayStr, hourStr, minuteStr, secondStr, offset] = match;
  const year = Number(yearStr);
  const month = Number(monthStr);
  const day = Number(dayStr);
  const hour = Number(hourStr);
  const minute = Number(minuteStr);
  const second = Number(secondStr);

  let offsetMinutes = 0;
  if (offset !== 'Z') {
    const sign = offset.startsWith('-') ? -1 : 1;
    const [offsetHourStr, offsetMinuteStr] = offset.slice(1).split(':');
    offsetMinutes = sign * (Number(offsetHourStr) * 60 + Number(offsetMinuteStr));
  }

  const utcMillis = Date.UTC(year, month - 1, day, hour, minute, second) - offsetMinutes * 60000;

  // Reinterpret that instant in the same offset and read its fields back.
  // `Date.UTC` above already normalized an out-of-range day/month
  // (Feb 30 -> Mar 2) rather than failing, so the only way to catch the
  // rollover is to check whether reading it back still says Feb 30.
  const roundTrip = new Date(utcMillis + offsetMinutes * 60000);
  return (
    roundTrip.getUTCFullYear() === year &&
    roundTrip.getUTCMonth() + 1 === month &&
    roundTrip.getUTCDate() === day &&
    roundTrip.getUTCHours() === hour &&
    roundTrip.getUTCMinutes() === minute &&
    roundTrip.getUTCSeconds() === second
  );
}

/**
 * True only when `value` is one of the three levels the domain emits.
 *
 * The `typeof` guard is load-bearing and cannot be folded into the
 * membership test: `String(['imminent'])` is `'imminent'` — a single-element
 * array's `toString()` collapses to its own element with no brackets — so a
 * whitelist applied after `String()` would accept an array as a level.
 */
function isSupportedLevel(value: unknown): value is Level {
  return typeof value === 'string' && (SUPPORTED_LEVELS as readonly string[]).includes(value);
}

function parseAlert(value: unknown): AlertPayload | null {
  if (value === null) return null;
  if (!isRecord(value)) {
    throw new SnapshotParseError('snapshot "alert" must be an object or null');
  }
  requireKeys(value, ALERT_KEYS, '"alert"');
  return {
    title: String(value.title),
    body: String(value.body),
    valid_until: String(value.valid_until),
    composed_by: String(value.composed_by),
    sent: Boolean(value.sent),
  };
}

function parseRecentAlert(value: unknown): RecentAlert {
  if (!isRecord(value)) {
    throw new SnapshotParseError('every entry in "recent_alerts" must be an object');
  }
  requireKeys(value, RECENT_ALERT_KEYS, 'recent_alerts entry');
  return {
    level: String(value.level),
    sent_at: String(value.sent_at),
    title: String(value.title),
    composed_by: String(value.composed_by),
  };
}

/**
 * Validates `input` against the published contract and returns it typed.
 *
 * Rejects, rather than rendering a blank:
 * - a missing or non-numeric `schema_version` (a document with no version
 *   is not a version-1 document, whatever else it contains);
 * - a `schema_version` this page does not know how to read;
 * - any top-level key the contract promises being absent;
 * - a `level` outside the closed set the domain emits and the stylesheet
 *   colours.
 *
 * Rebuilds the object field-by-field (an explicit whitelist) instead of
 * returning `input` as-is, so the parsed object's key set matches the
 * contract exactly even if the wire document ever carried an extra field —
 * the same bidirectional guarantee the Python side asserts in
 * `tests/unit/domain/test_snapshot.py::TestThePublishedKeysMatchTheGoldenContract`.
 */
export function parseSnapshot(input: unknown): Snapshot {
  if (!isRecord(input)) {
    throw new SnapshotParseError('snapshot must be a JSON object');
  }

  const version = input.schema_version;
  if (typeof version !== 'number' || !Number.isFinite(version)) {
    throw new SnapshotParseError(
      `snapshot "schema_version" must be a number, got ${JSON.stringify(version)}`,
    );
  }
  if (version !== SUPPORTED_SCHEMA_VERSION) {
    throw new SnapshotParseError(
      `unsupported snapshot schema_version ${version}; this page reads version ${SUPPORTED_SCHEMA_VERSION}`,
    );
  }

  requireKeys(input, TOP_LEVEL_KEYS, 'document');

  // Fix round 2, task 8: a bare `Number.isFinite(new
  // Date(String(value)).getTime())` check (fix round 1) still let two
  // false-freshness cases through — `ageInHours` feeds this into
  // `new Date(...).getTime()`, and `NaN > staleAfterHours` is `false` in
  // JS, so an accepted-but-wrong value renders as fresh, never stale:
  // - a non-string coerces through `String()` before reaching `Date` at
  //   all (`String(12345)` -> `"12345"`, which `Date` parses as a real,
  //   finite year-12345 timestamp);
  // - a calendar date that does not exist (`"2026-02-30"`) does not
  //   produce `NaN`; JS silently rolls it forward to a real one.
  // `isValidPublishedTimestamp` requires a string, in exactly the shape
  // the publisher emits, that round-trips through its own calendar fields.
  if (!isValidPublishedTimestamp(input.evaluated_at)) {
    throw new SnapshotParseError(
      `snapshot "evaluated_at" is not a valid timestamp: ${JSON.stringify(input.evaluated_at)}`,
    );
  }
  const evaluatedAt = input.evaluated_at;

  if (!isSupportedLevel(input.level)) {
    throw new SnapshotParseError(
      `snapshot "level" must be one of ${SUPPORTED_LEVELS.join(', ')}, got ${JSON.stringify(input.level)}`,
    );
  }
  const level = input.level;

  const window = input.window;
  if (!isRecord(window) || typeof window.start !== 'string' || typeof window.end !== 'string') {
    throw new SnapshotParseError('snapshot "window" must have string "start" and "end"');
  }

  const sources = input.sources;
  if (!isRecord(sources) || typeof sources.senamhi !== 'string' || typeof sources.open_meteo !== 'string') {
    throw new SnapshotParseError('snapshot "sources" must have string "senamhi" and "open_meteo"');
  }

  if (!Array.isArray(input.reasons)) {
    throw new SnapshotParseError('snapshot "reasons" must be an array');
  }
  if (!Array.isArray(input.recent_alerts)) {
    throw new SnapshotParseError('snapshot "recent_alerts" must be an array');
  }

  return {
    schema_version: version,
    city: String(input.city),
    evaluated_at: evaluatedAt,
    level,
    level_label: String(input.level_label),
    window: { start: window.start, end: window.end },
    reasons: input.reasons.map((reason) => String(reason)),
    sources: { senamhi: sources.senamhi, open_meteo: sources.open_meteo },
    degraded: Boolean(input.degraded),
    alert: parseAlert(input.alert),
    recent_alerts: input.recent_alerts.map(parseRecentAlert),
  };
}

/** Hours elapsed between `snapshot.evaluated_at` and `now`. Its own
 * function, not folded into `parseSnapshot`, because it needs a clock and
 * the parser must stay pure. */
export function ageInHours(snapshot: Snapshot, now: Date): number {
  const evaluatedAt = new Date(snapshot.evaluated_at);
  return (now.getTime() - evaluatedAt.getTime()) / (1000 * 60 * 60);
}
