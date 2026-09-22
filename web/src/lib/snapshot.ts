/**
 * Parses and validates the public status document
 * (`contracts/public-snapshot.json`), published by `src/rain_alert/domain
 * /snapshot.py::build_snapshot` and fetched at runtime from
 * `/data/status.json`.
 *
 * Wire keys are preserved as-is (snake_case) rather than mapped to
 * camelCase. The contract exists to prevent the page and the publisher from
 * drifting apart; a translation layer here is one more thing that can drift
 * from it, for no benefit — see task-8-report.md for why this overrides the
 * brief's own (self-contradicting) example.
 */

export const SUPPORTED_SCHEMA_VERSION = 1;

export interface AlertPayload {
  title: string;
  body: string;
  valid_until: string;
  composed_by: string;
  sent: boolean;
}

export interface RecentAlert {
  level: string;
  sent_at: string;
  title: string;
  composed_by: string;
}

export interface Snapshot {
  schema_version: number;
  city: string;
  evaluated_at: string;
  level: string;
  level_label: string;
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
 * - any top-level key the contract promises being absent.
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
    evaluated_at: String(input.evaluated_at),
    level: String(input.level),
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
