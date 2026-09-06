# The adapters layer

This document covers every module in `src/rain_alert/adapters/`, including the six under `local/`. Adapters are where the outside world enters: HTTP, HTML, JSON files, standard output. Read it if you are debugging a source that reported unavailable, changing a threshold value, or trying to understand why the scraper refuses to parse a page. Read [README.md](./README.md) for the cycle and [ports-and-application.md](./ports-and-application.md) for the contracts these modules implement.

## Map of the layer

| Module | Implements | In one line |
|---|---|---|
| `http.py` | none directly | The shared fetch seam, and the rule that no `httpx` exception escapes. |
| `open_meteo.py` | `ForecastProvider` | Fetches four days of hourly data and slices forward from the current hour. |
| `senamhi_scraper.py` | `WarningProvider` | Locates the table by header signature and fails loudly on anything it cannot read. |
| `senamhi_classification.py` | none directly | Translates SENAMHI's Spanish title vocabulary into domain hazard terms. |
| `console_notifier.py` | `Notifier` | Prints, and structurally cannot transmit. |
| `serialization.py` | none directly | The persisted document shape, carried forward for DynamoDB. |
| `local/static_config_repository.py` | `ConfigRepository` | The calibrated thresholds and the placeholder coordinates. |
| `local/static_contact_repository.py` | `ContactRepository` | One synthetic contact whose handle is `stdout`. |
| `local/in_memory_alert_repository.py` | `AlertRepository` | The whole query rule, with no I/O. |
| `local/json_alert_repository.py` | `AlertRepository` | The same store, persisted atomically to one file. |
| `local/offline_sources.py` | `HtmlFetcher`, `ForecastProvider` | Swap the fetch leaf only, so the real parsers still run. |

## `http.py`

The seam both outbound adapters share. Two rules carry it.

**No `httpx` exception ever escapes.** Every failure is translated into a `FetchError` carrying an `UnavailableReason` the domain already knows. The provider adapters then turn that into `Unavailable` without importing `httpx`'s exception hierarchy or guessing at a reason string.

`FetchError` is deliberately not an `httpx` subclass. The provider adapters catch `FetchError` and nothing else, so a new `httpx` error type can never slip past them untranslated.

```python
try:
    response = client.get(url, params=...)
except httpx.TimeoutException as exc:
    raise FetchError(UnavailableReason.TIMEOUT, ...) from exc
except httpx.HTTPError as exc:
    raise FetchError(UnavailableReason.TRANSPORT_ERROR, ...) from exc

if not response.is_success:
    raise FetchError(UnavailableReason.BAD_STATUS, ...)
```

`TimeoutException` is checked before `HTTPError` because it is a subclass of it. Reversing the two clauses would report every timeout as a transport error.

**No retries.** This is decision `D12`. A failed fetch yields `Unavailable` immediately. The degraded path is a first-class tested behaviour, and the cycle repeats in six hours. Because nothing retries, every phase of the request must be bounded, which is what `DEFAULT_TIMEOUT` is for: connect 3 s, read 5 s, write 3 s, pool 3 s.

`HtmlFetcher` is a one-method `Protocol`, `fetch(url) -> str`. It is the seam that keeps HTML parsing testable with zero HTTP. `HttpxHtmlFetcher` is the real implementation, and its transport is injectable so the same class is exercised offline through `httpx.MockTransport`.

## `open_meteo.py`

The forecast adapter. The endpoint, parameter names and response shape were verified against the live API on 2026-09-04.

```
GET https://api.open-meteo.com/v1/forecast
    ?latitude=..&longitude=..
    &hourly=precipitation,precipitation_probability
    &forecast_days=4&timezone=UTC
```

### Why it asks for four days when it needs 48 hours

This is the detail that looks like a mistake and is not. `FORECAST_DAYS` is 4.

**The API returns whole days from today, not a window forward from now.** A request made at 12:41 UTC with `forecast_days=2` carries only 34 hours of data still in the future. The cycle would degrade to SENAMHI-only for most of every day.

Three days looks sufficient and has a cliff at the end of the day. At 23:00 UTC, three days leave `3 * 24 - 23 = 49` forward points for a 48-hour need, a margin of one hour. Any clock skew, a late model publication, or a run that starts a minute before midnight and reaches the API a minute after, turns that into `INSUFFICIENT_HORIZON`.

A fourth day leaves a whole spare day at every hour, costs one page fetched once, and removes the cliff. The adapter still slices `[current hour, +hours)`, so the extra day is never evaluated.

`timezone=UTC` keeps window arithmetic unambiguous. Local time is a display concern only, handled in `domain/template.py`.

### `parse_forecast_payload`

A pure function, so it is testable without HTTP and reusable by the CLI's `--offline-fixtures` mode.

It validates the body is an object, that `hourly` is an object, and that the three series `time`, `precipitation` and `precipitation_probability` are lists of equal length. Any of those failing raises `FetchError(MALFORMED_PAYLOAD, ...)` naming what was wrong.

It then drops every sample stamped before the hour containing `now`, and requires at least `hours` points to remain. Too few raises `FetchError(INSUFFICIENT_HORIZON, ...)`.

**It never returns a partially-filled `Forecast`.** The final construction is wrapped, so a gap in the series surfaces as `Forecast.__post_init__` raising on non-contiguous points, and that is translated into `MALFORMED_PAYLOAD`. A missing hour is a parse failure, not silent interpolation.

The per-value helpers `_millimetres` and `_percent` reject booleans explicitly, because `bool` is a subclass of `int` in Python and `True` would otherwise parse as 1 mm.

They also reject non-finite and out-of-range readings, and that check is not cosmetic.

**`json.loads` accepts bare `NaN` and `Infinity` by default.** Both therefore arrive from a perfectly well-formed HTTP 200, and each used to cause a different failure.

| Value | What it used to do |
|---|---|
| `NaN` probability | `int(float("nan"))` raises `ValueError`, which is not a `FetchError`, so it escaped the adapter's only `except` clause. `RunAlertCycle` has no handler either, so the whole run died with a traceback — no alert and no operator notice, which is strictly worse than a declared outage. |
| `Infinity` millimetres | Parsed as a valid reading, accumulated to an infinite rainfall total, and cleared `imminent_mm_24h` unconditionally. An upstream glitch decided the level. |

Anomalous upstream values are most likely during exactly the extreme weather this tool exists for, which is what makes this worth a range check rather than a note.

`_millimetres` requires a finite, non-negative number. `_percent` checks finiteness **before** `int(value)`, then requires 0 to 100. Both report `MALFORMED_PAYLOAD`. `HourlyPoint` enforces the same ranges in its own `__post_init__` — the adapter is the first line and the entity the second, so a future caller that skips these helpers still cannot build an unusable point. The construction is wrapped so that the entity's `ValueError` leaves as a `FetchError` like everything else.

`_timestamp` attaches UTC to a naive string. The request pins `timezone=UTC`, so the API returns values like `2026-09-04T00:00` with no offset. The zone is attached here rather than assumed later.

### `OpenMeteoForecastProvider`

The port implementation. Its transport is injectable, so this exact class is exercised offline through `httpx.MockTransport` rather than a substitute. `fetch_forecast` catches `FetchError` and returns `Unavailable` carrying the reason and the detail.

**No exception escapes, including the ones nobody predicted.** The `FetchError` clause alone was a claim rather than a guarantee, and the `NaN` case above is how that claim failed. There is now a catch-all, reporting `TRANSPORT_ERROR` and naming the exception type, exactly as `SenamhiWarningScraper` already did. The sibling had carried that rule and its comment since it was written; this adapter simply was not following it.

The reason code does not change any routing — only `Available` versus `Unavailable` does — so matching the sibling buys one thing: an unfamiliar detail string reads the same wherever an operator meets it.

## `senamhi_classification.py`

The adapter half of the flood-relevance filter. The rule lives in `domain/hazards.py`. The source's words live here, because the Spanish wording of an aviso title belongs to SENAMHI and not to the domain.

It is a separate module rather than a private helper inside the scraper, on purpose. This filter decides which official warnings the community is ever told about, so it needs a first-class test seam and a diff a reviewer can read, not a regular expression buried in an HTTP adapter.

### The vocabulary, and the evidence behind it

12 399 rows were harvested across the 12 regional pages on 2026-09-04, of which 789 are Lambayeque's. Every one of the 12 399 titles classifies into a named family. None falls through to `UNKNOWN`.

| Family | Marker stems | Rows, all regions | Rows, Lambayeque |
|---|---|---|---|
| precipitation | `PRECIPITACION`, `LLUVIA`, `LLOVIZNA`, `GARUA` | 5 443 | 243 |
| high temperature | `INCREMENTO DE [LA] TEMPERATURA` | 2 339 | 169 |
| wind | `VIENTO` | 2 296 | 291 |
| low temperature | `DESCENSO DE [LA] TEMPERATURA`, `FRIAJE` | 2 259 | 86 |
| snow | `NEVADA` | 62 | 0 |
| hail | `GRANIZO` | 0 | 0 |

`GRANIZO` carried no live row on the harvest date. It is a documented SENAMHI aviso family, and classifying it costs one pattern. Leaving it out would let hail reach the evaluator as `UNKNOWN`, which the filter reads as flood-relevant.

### Why substring matching failed, and what replaced it

Matching is on **normalized** text and on **word boundaries**, never on exact strings.

`normalize_title` strips accents through `unicodedata.normalize("NFKD", ...)` and dropping combining characters, collapses whitespace, and upper-cases. So `Precipitaciones` and `PRECIPITACIONES` and any accented variant all reduce to the same form.

Three properties of the patterns are load-bearing.

**Word boundaries.** Every marker is anchored with `\b`. Without it, `TEMPERATURAS` appearing in prose would be read as the aviso family, and `COSTADO` would be read as `COSTA`. Two tests pin exactly this: `test_a_marker_is_matched_on_word_boundaries_not_as_a_substring` and `test_the_word_costa_is_matched_as_a_word_not_as_a_substring`.

**The optional article.** SENAMHI publishes the same family both as `INCREMENTO DE TEMPERATURA DIURNA ...` and as `INCREMENTO DE LA TEMPERATURA DIURNA ...`. On 2026-09-04, 56 rows across 9 of the 12 regional pages used the article form. A fixed phrase matched only the first, so the second classified as `UNKNOWN`, which the filter treats as relevant. The heat aviso reached the evaluator. The pattern is `\bINCREMENTO\s+DE\s+(?:LA\s+)?TEMPERATURA\b`.

**Open-ended stems.** `\bLLUVIA` rather than `\bLLUVIA\b`, so it also matches `LLUVIAS`. `\bVIENTO` also matches `VIENTOS`. Plural and singular are the same hazard.

The page also appends suffixes like `(EXTENSION DEL AVISO 335)`, which is another reason an exact-string map silently dropped real warnings. That is the worst possible failure for this filter.

**Precipitation is checked first, on purpose.** A mixed title such as `PRECIPITACIONES Y NEVADA EN LA SIERRA` must resolve to precipitation, because erring towards alerting is the posture the whole module takes on ambiguity.

`classify_zone` looks for `\bCOSTA\b` and `\bSIERRA\b` and returns `COAST_AND_HIGHLANDS`, `COAST`, `HIGHLANDS` or `UNKNOWN`.

**`UNKNOWN` is not a failure.** `domain.hazards` treats it as relevant, so an unrecognized title errs towards alerting rather than towards silence. The cost of that choice is that a vocabulary change silently disables the filter, which is why a live canary test asserts zero unknown titles rather than "few" unknowns. See [entrypoints-and-testing.md](./entrypoints-and-testing.md).

## `senamhi_scraper.py`

The most fragile module in the system, and the one with the most defensive design. It was verified against the live Lambayeque page on 2026-09-04: HTTP 200, about 512 KB, one `<table>`, 790 `<tr>` rows, being one header row plus 788 data rows. Each data row carries exactly seven `<td>` cells: title, number, emission date, start date, end date, duration, level. The row currently in force is marked by a `(vigente)` token inside the number cell.

Three properties carry the module.

### 1. The table is located by header signature

Never by CSS class and never by table index.

Class names and layout wrappers churn far more often than column headers. More importantly, an index-based selector fails **silently** where a signature check fails **loudly**. Picking the wrong table by index yields zero rows, which is indistinguishable from calm.

`REQUIRED_HEADER_LABELS` is the frozen set `{"AVISO", "NRO", "EMISION", "INICIO", "FIN", "DURACION", "NIVEL"}`, normalized with accents stripped, upper-cased and trailing punctuation removed, so `Nro.` becomes `NRO`. `_locate_table` walks every `<table>` and every row inside it, and returns the first row whose labels are a superset of that set, along with a map from label to column index.

The live header block repeats its labels, so `_header_index` uses `setdefault` and the first occurrence of each label wins. A duplicate must not shift the mapping. That behaviour is pinned by `test_duplicate_header_labels_do_not_break_the_signature_check`.

If no table carries the signature, the parse raises `FetchError(STRUCTURE_UNRECOGNIZED, ...)` naming the labels it wanted.

### 2. Breakage is never a false calm

The page has listed history since 2024, so a correctly parsed table is never empty. Four conditions therefore yield `Unavailable`:

| Condition | Reason reported |
|---|---|
| No table carries the header signature | `STRUCTURE_UNRECOGNIZED` |
| The table has headers but zero data rows | `NO_ROWS_EXTRACTED` |
| More than half the rows are unparseable | `STRUCTURE_UNRECOGNIZED` |
| **Any anomaly on the row marked `(vigente)`** | `STRUCTURE_UNRECOGNIZED` |

`Available` with an empty tuple therefore means one specific thing: the page parsed, and nothing relevant is in force.

**The fourth condition is not redundant with the share guard, and it is the one that matters most.**

Breakage confined to the single row in force is 1 of 789 rows, which is 0.13 per cent. The share guard waves it through. The result is a report of `available` with zero warnings while a red aviso is in force.

That is worse than a declared outage, and the reasoning is the part a newcomer would otherwise get wrong. With `senamhi_status == "available"`, the evaluator routes to `_prepare_forecast_only`, which applies the **stricter** forecast-only probability threshold of 70 per cent rather than the combined-rule 60 per cent. So a broken parse leaves the system **less sensitive** than a source known to be down. On top of that, no operator notice is emitted, because from the outage rule's point of view nothing is unavailable. The system is quietly blind and quietly harder to trigger.

`_refuse_if_in_force(row, anomaly)` is the guard. It is called at both points where a row is skipped: when the row has too few cells to index, and when `_build_warning` raises on an unparseable cell.

`_row_is_marked_in_force` is deliberately coarser than `_is_marked_in_force`. The precise one reads the number cell by header index. The coarse one scans the whole row's text, and it is asked exactly when the cells cannot be trusted: a dropped cell shifts every later column, so there is no reliable number cell to read. Scanning the whole row errs towards declaring a structural failure, which is the safe direction. Two facts make that safe rather than noisy: no live title among the 12 399 harvested rows contains the word `VIGENTE`, and the `(vigente)` link target lives in an `href`, which `get_text` does not return.

### 3. Only warnings that are both in force and flood-relevant are returned

"In force" requires **both** the `(vigente)` marker **and** a start-to-end window containing `now`. Either alone is insufficient. A stale marker on an expired row would alert about rain that already passed. The dates alone match many historical rows.

```python
if not (_is_marked_in_force(number_text) and warning.window.start <= now <= warning.window.end):
    continue
```

Relevance is the domain rule. `discard_reason(warning.phenomenon)` returns a string when the warning cannot cause flooding, and the row is recorded as a `DiscardedWarning` rather than dropped silently.

### What travels out to the operator

`ParseOutcome` carries `warnings`, `rows_seen`, `anomalies` and `discarded`. `rows_seen` counts every data row including historical and filtered ones, because the row count is what proves the parse worked.

`parse_notes(outcome)` turns that into the operator-facing lines that become `Available.notes`.

**Discards are listed one by one.** They are bounded by the number of rows actually in force, which is a handful, and each one is a warning the community was not told about.

**Anomalies are summarized, not listed.** A structural change can produce hundreds, and the operator needs the fact that rows were lost, not a wall of them. The note reads `N of M rows were unparseable and skipped; first: ...`.

**Every source-derived fragment in a note is sanitized**, through `domain.sanitize.sanitize_source_text`: the discarded warning's `source_id` and `title`, and the first anomaly string. The CLI prints notes as `- senamhi: <note>` bullets in a terminal, so a title carrying a newline forges an extra note line and an ANSI escape executes — the same defect as the community body, one layer over. The fragments are sanitized rather than the finished line, so the length cap lands on the source text and the note's own wording stays whole.

Without these notes, a `Level none` result has two very different causes that look identical from outside: nothing was in force, or everything in force was filtered out.

### Row parsing details

`_parse_day` accepts `%Y-%m-%d`, `%d/%m/%Y` and `%d-%m-%Y`, and reads the value as midnight in `America/Lima`, converted to UTC. Warning dates are published as calendar days in Peruvian local time.

**The end date is inclusive**, so `_build_warning` adds one day to it. A warning listed as ending on 2026-09-06 covers the whole of that day.

**The title is stored raw**, with accents intact. Normalization is for matching, not for storing. `Warning.title` reaches the Spanish community body through `WarningSummary`, and the accent-stripped form put `EXTENSION` in front of recipients. Both classifiers normalize internally, so they can be handed the raw title.

That decision stands after the security review, and it is worth saying why the review did not overturn it. A raw title is the record of what SENAMHI actually published, and it is exactly the field the last normalization-on-the-way-in attempt damaged. The fix for a hostile title is applied where the text is **rendered** for a human, not where it is stored — through one shared domain function, at each of the four rendering boundaries listed in [domain.md](./domain.md).

`_source_id` extracts the detail-page identifier from the row's link with the pattern `[?&]b=(\d+)`, falling back to the number cell's text. Detail pages are not fetched here. The identifier is carried so change 2's `get_warning_detail` tool can use it.

`_parse_level` maps `AMARILLO`, `NARANJA` and `ROJO` to the domain levels, accent and case insensitively. An unknown token raises `ValueError`, which becomes an anomaly, or a refusal if the row is in force.

### `SenamhiWarningScraper`

The port implementation. The fetch is an injected `HtmlFetcher`, so parsing is unit-tested against pinned fixtures with zero HTTP.

`fetch_current_warnings` catches `FetchError` and, separately, catches bare `Exception`. **An outright parser bug must degrade the cycle, not abort it**, because `RunAlertCycle` has a fully tested degraded path and no exception handler. An unexpected exception is reported as `TRANSPORT_ERROR` with the exception type named in the detail.

## `console_notifier.py`

`Notifier` implementation that prints and nothing else. Two guarantees.

**It cannot transmit.** The module imports no HTTP client, no mail library, no socket and no subprocess. Only `sys` and domain types. There is nothing here that could deliver a message, which is a stronger statement than a flag or a configuration default. `test_the_module_imports_no_client_that_could_deliver_anything` asserts it by parsing the module's AST, so it stays true as the module changes.

**It prints no personal data.** Community alerts report the recipient count only, never a handle or a contact identifier. A handle written to standard output ends up in a terminal scrollback, then a CI log, then eventually a pasted bug report.

The two `Notifier` methods print under distinct prefixes, `[DRY-RUN ALERT]` and `[OPERATOR NOTICE]`, because they serve distinct audiences. The first is text composed for the community. The second is technical information for whoever runs the system. Confusing the two in a calibration log is how an operator ends up believing a heat warning went out to a village.

Both blocks state `nothing was transmitted: this build has no notifier that can deliver a message` on their first line.

The community body is printed verbatim, indented but otherwise unaltered. Calibration depends on reading the real Spanish text rather than a summary.

## `serialization.py`

The persisted shape of alerts, outages and structured reasons.

**This is a carried-forward contract, not a local file format.** Change 3's DynamoDB `alerts-sent` adapter reuses these attribute names one for one, so the local JSON file and the DynamoDB item are shape-identical and that adapter becomes a mechanical mapping with no translation layer.

| Item | `pk` | `sk` | Attributes |
|---|---|---|---|
| Sent alert | `ALERT#<city_slug>` | `<window_start>#<level>` | everything `alert_record_to_dict` emits |
| Active outage | `OUTAGE#<city_slug>` | `CURRENT` | everything `outage_record_to_dict` emits |

Three decisions worth stating.

**Reasons are tagged with `ReasonKind`**, the domain's own enum, rather than a second serialization vocabulary. `AlertRecord.reasons` is the audit trail for why the community was alerted, and the CLI's `--json` output emits it for threshold calibration. It therefore stays structured and numeric, never rendered prose.

**The one free-text field in that document is sanitized.** `reason_to_dict` passes `WarningReason.title` through `domain.sanitize.sanitize_source_text`. JSON encoding is not the guard it looks like here: `json.dumps` escapes control characters, but under `ensure_ascii=False` — which this project uses so Spanish stays readable — it writes a bidi override through verbatim, and the operator reads the result in a terminal. Sanitizing at this boundary is what makes **every** field of the `--json` document safe, rather than only the three (`message.body`, `reasons_en`, `notes`) that happen to come from an already-sanitizing renderer.

**An unknown tag raises.** A state file written by a newer version must not be read as though a reason simply were not there, because the audit trail would then silently lie about why an alert went out. The local state file is disposable and gitignored, so failing loudly costs little.

**`AlertRecord.level` and `AlertRecord.message.level` are stored once.** The design pins a single `level` attribute, and `RunAlertCycle` only ever produces records where the two agree, because the composer is handed the decided level and copies it. The message level is restored from the record level rather than duplicated in the document.

Both `match` statements in this module end in `assert_never`. Without that guard on `reason_to_dict`, a future variant would fall through to an implicit `None`, `mypy --strict` would not flag it, `null` would be written into the state file and into change 3's audit trail, and the **next read** would crash with an `AttributeError` far from the cause, with the trail already corrupted. `test_an_unhandled_reason_variant_fails_loudly_instead_of_serializing_to_null` pins that.

`outage_record_to_dict` sorts the source set. A `frozenset` has no order, and an unstable document makes every diff of the state file noise.

`outage_record_from_dict` preserves `notified_at` as `None` when it was `None`. That state means "still owed a notice", so inventing a timestamp here would lose the operator's notice forever.

## `local/static_config_repository.py`

`ConfigRepository` built from module constants. This is where the numbers live.

| Constant | Value |
|---|---|
| `CITY`, `CITY_SLUG` | `Ferreñafe`, `ferrenafe` |
| `REGION` | `Lambayeque` |
| `TIMEZONE` | `America/Lima` |
| `ACTIVE_CHANNEL` | `console` |
| `FORECAST_HOURS` | 48 |
| `DEDUP_LOOKBACK_HOURS` | 72 |
| `PLACEHOLDER_COORDINATES` | latitude `-6.64`, longitude `-79.79` |

The thresholds in `THRESHOLDS`:

| Threshold | Value | Note in the code |
|---|---|---|
| `prepare_mm_48h` | 9.5 | Reque p95 boundary |
| `prepare_probability_pct` | 60 | |
| `prepare_probability_pct_degraded` | 70 | forecast-only, and SENAMHI unavailable |
| `prepare_warning_levels` | yellow, orange, red | |
| `imminent_mm_24h` | 20.0 | |
| `imminent_probability_pct` | 70 | |
| `imminent_warning_levels` | orange, red | |

### The coordinates are a documented placeholder

They are approximate, not a verified location. Ferreñafe's exact latitude and longitude is still an open decision in the design specification. The config says so through `coordinates_are_placeholder=True`, and the CLI prints `(PLACEHOLDER — pending confirmation)` beside them on every run. Presenting a guess as verified would put a forecast for the wrong place under the project's own name.

Two environment overrides let an operator supply real coordinates without a code change, `RAIN_ALERT_LAT` and `RAIN_ALERT_LON`. They are treated strictly.

**Both or neither.** Half an override is not a coordinate. Pairing a real latitude with a placeholder longitude would forecast for a point in the ocean while claiming to be configured. If either variable is missing, both are ignored.

**Unparseable or out of range raises.** A non-numeric value raises `ValueError` naming the variable. An out-of-range value raises through `Coordinates.__post_init__`. Falling back to the placeholder would hide an operator typo and forecast for the wrong city silently.

**A complete override clears the placeholder flag**, because an operator who supplies coordinates is asserting them.

`CHECKLIST` is the one place in this codebase where copy is Spanish by contract, because it is rendered into the community message body. The five items are quoted in [domain.md](./domain.md).

## `local/static_contact_repository.py`

One synthetic contact.

```python
LOCAL_OPERATOR = Contact(contact_id="operator-local", channel="console", handle="stdout", consent_at=None)
```

**No email address, no phone number, no `example.com`.** Nothing a hygiene grep would have to be taught to forgive. The one contact is the local operator's own terminal, and its handle is literally the output stream, which is also the honest description of where a community alert goes in this build.

`list_active` returns the contact only for the `console` channel. Any other channel returns an empty tuple, which is what makes an accidental request for a real delivery channel come back empty rather than falling through to the operator. Real recipients arrive in change 3, from a store that is never committed.

## `local/in_memory_alert_repository.py`

`AlertRepository` with no I/O.

The whole query rule lives here, and `JsonFileAlertRepository` composes this class rather than reimplementing it. Two copies of "filter by city, filter by window-start range, sort ascending" would be exactly the kind of duplication that diverges silently and re-alerts a community.

The store stays deliberately dumb. Overlap and escalation are rules and belong in `AlertPolicy` and `should_send`. This is a plain key-range read, which is both the cheapest DynamoDB access pattern in change 3 and trivially checkable here.

`snapshot()` returns everything held, for a caller that needs to persist it. That is the seam the file-backed adapter uses.

That duplication warning was not hypothetical. `tests/support/fakes.py` records that a third copy of the query once existed in the fake, and that the copy also cleared the active outage without checking `city_slug`, so a fake the use-case tests trusted behaved differently from production. The fake now composes this class too.

## `local/json_alert_repository.py`

The same store, persisted to one gitignored JSON file. `DEFAULT_STATE_FILE` is `.local-state/alerts.json`.

**This adapter exists for one reason: each CLI run is a fresh process.** Without durable state, dedup and outage-notice dedup could not work at all. Three consecutive dual-outage cycles would emit three notices instead of one, and a community would be re-alerted for a window already sent.

The document holds the same two collections as the DynamoDB table in change 3, `alerts` and `active_outage`, through `serialization.py`, so the two adapters are shape-identical.

Three behaviours are deliberate.

**Writes are atomic.** `_flush` serializes fully, then `_write_atomically` writes to a temporary file in the **same directory** and calls `os.replace`. Same directory because `os.replace` is only atomic within one filesystem. A crash or a serialization failure therefore leaves the previous state readable rather than a truncated document. The temporary file is removed on any exception, including `BaseException`.

**A change is applied to a copy first.** `_mutate` snapshots the current state, builds a candidate, applies the change to the candidate, flushes it, and only then adopts it. Mutating in place first would leave the in-memory state describing an alert that was never written if serialization failed, and the next read of that state would claim the community had been told.

**A corrupt file raises.** Starting from empty state would look like "no alert has ever been sent" and re-alert the community for a window already delivered. The file is disposable and gitignored, so failing loudly is cheap. Guessing is not. A missing file, by contrast, reads as empty state, which is the correct interpretation of a first run.

### There is no lock, and two concurrent runs lose each other's records

Known, measured and deliberately not fixed in this change. It is recorded here because the failure mode is a dedup failure, and a lost dedup record means a community re-alerted for a window already delivered.

The sequence, with two processes:

1. A reads the state file. B reads the state file.
2. A writes its record. The write itself is atomic.
3. B writes its record, built from the snapshot it took in step 1. A's record is gone.

**A lock around the write would not fix this.** Both writes are already atomic through `os.replace`; the loss comes from B's *in-memory snapshot* being stale, not from the writes interleaving. This is a read-modify-write lost update, so the lock would have to be held from the first read of a cycle to its last write.

That is why it is not a two-line change. `AlertRepository` has no lifecycle in its contract — nothing tells the repository that a cycle has ended — so a cycle-scoped lock means either a context manager the CLI enters around `RunAlertCycle.execute()`, which changes the port or the entrypoint's structure, or a lock the repository holds for its own lifetime, which changes when the file is released. `fcntl.flock` is also POSIX-only, and testing it honestly needs two real processes.

It is unrealistic for a single-operator command line tool run by hand, and it becomes real the moment a scheduler exists. The concrete proposal is recorded in the change's Open Items rather than half-implemented.

There is a second known, recorded limitation in `_flush`. The `alerts` list grows without bound and the whole document is rewritten on every send, so both the file and the write cost grow forever. It is tolerable here: one send per six-hour cycle, only on an escalation, and the file is disposable. The retention rule is deferred because it needs a clock the port does not carry, and because change 3's DynamoDB adapter should use a TTL attribute rather than a rewrite, so the mechanism would not be shared anyway.

## `local/offline_sources.py`

The fixture-backed adapters behind the CLI's `--offline-fixtures` flag.

**They swap the fetch leaf only.** `FileHtmlFetcher` implements `HtmlFetcher` by reading a pinned HTML file and ignoring the URL, so the **real** `SenamhiWarningScraper` still parses it. `OfflineOpenMeteoProvider` reads a recorded JSON payload and hands it to the **real** `parse_forecast_payload`.

That is the whole reason the offline path is trustworthy for calibration. A separate "offline parser" would be a second implementation to keep in step, and the first divergence between them would be invisible.

A missing or unreadable fixture is reported as `FetchError` with `TRANSPORT_ERROR`, exactly like a transport failure, so the cycle degrades the same way it would live. A fixture that is not JSON becomes `Unavailable` with `MALFORMED_PAYLOAD`.

`OfflineOpenMeteoProvider.fetch_forecast` carries the same catch-all as the live provider, for the same reason. A recorded payload is a captured live response, so it can carry the same bare `NaN` the live endpoint can, and `RunAlertCycle` has no exception handler either way. `json.JSONDecodeError` is caught **before** the catch-all, because it is a `ValueError` subclass and deserves its own reason and a detail naming the file.

`tests/unit/adapters/test_offline_sources.py` covers both classes. They previously had no tests of their own and were exercised only indirectly through the CLI.

## Where to go next

- [domain.md](./domain.md) for the rules these adapters feed, including the hazard filter and the risk branches.
- [ports-and-application.md](./ports-and-application.md) for the contracts implemented here.
- [entrypoints-and-testing.md](./entrypoints-and-testing.md) for how these adapters are wired and tested, including the pinned fixtures and the live canaries.
- [README.md](./README.md) for the cycle in outline.
