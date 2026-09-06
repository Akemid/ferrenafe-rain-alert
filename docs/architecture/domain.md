# The domain layer

This document covers every module in `src/rain_alert/domain/`. That is where the system's rules live: what counts as a hazard, what raises which level, whether a message may be sent, and what the community reads. Read it if you are changing a rule, calibrating a threshold, or trying to understand why a run produced the level it did. Read [README.md](./README.md) first for the seven-step cycle these modules sit inside.

The domain imports nothing but the standard library and other domain modules. That is checked mechanically by `tests/architecture/test_layer_boundaries.py`, described in [entrypoints-and-testing.md](./entrypoints-and-testing.md).

## Map of the layer

| Module | Responsibility |
|---|---|
| `values.py` | Enums and range-checked value objects everything else is built from. |
| `sources.py` | Availability as a tagged union. |
| `hazards.py` | Which official warnings may drive a flood alert, and how far. |
| `sanitize.py` | The one filter every source-derived free text passes through. |
| `entities.py` | `Warning`, `Forecast`, `RiskAssessment` and their accessors. |
| `config.py` | The injected thresholds and the per-city configuration. |
| `reasons.py` | Structured reasons, and the English rendering for the operator. |
| `risk.py` | `RiskEvaluator`, the five ordered branches. |
| `dedup.py` | `should_send`, the pure send rule. |
| `outage.py` | `evaluate_outage`, the dual-outage state machine. |
| `messages.py` | The message and record types, including the composer's input. |
| `template.py` | The deterministic composer, in Spanish, with local times. |

## `values.py`

The vocabulary. Five `StrEnum` types and two frozen dataclasses.

| Type | Members or fields |
|---|---|
| `Level` | `NONE`, `PREPARE`, `IMMINENT` |
| `WarningLevel` | `YELLOW`, `ORANGE`, `RED` |
| `SourceName` | `SENAMHI`, `OPEN_METEO` |
| `NoticeKind` | `SOURCE_UNAVAILABLE`, `SOURCES_RECOVERED` |
| `UnavailableReason` | `TRANSPORT_ERROR`, `TIMEOUT`, `BAD_STATUS`, `MALFORMED_PAYLOAD`, `STRUCTURE_UNRECOGNIZED`, `NO_ROWS_EXTRACTED`, `INSUFFICIENT_HORIZON` |
| `Coordinates` | `latitude`, `longitude` |
| `TimeWindow` | `start`, `end` |

`LEVEL_RANK` maps each level to an integer, and `is_escalation(previous, candidate)` returns true only when the candidate ranks strictly higher. Both are used by `dedup.py`.

**`Coordinates` range-checks on construction.** A latitude outside `[-90, 90]` or a longitude outside `[-180, 180]` raises `ValueError` in `__post_init__`. That is what makes a typo in the environment override fail at the point of the typo, rather than producing a forecast for the ocean.

**`TimeWindow` enforces UTC, and it enforces it rather than documenting it.** Both endpoints must be timezone-aware and must have a zero UTC offset, and `end` must be strictly after `start`. The reason is stated in the module: `TimeWindow.key()` returns the window start as an ISO 8601 string, and that string becomes change 3's DynamoDB sort key. A value carrying a non-zero offset would sort wrongly against its UTC neighbours and would silently break the dedup key-range query. Sorting is the failure mode, so the type refuses non-UTC input outright.

`TimeWindow.overlaps(other)` is a half-open comparison. Adjacent windows do not overlap.

## `sources.py`

**Availability is a tagged union, not optional data.** This is the single most load-bearing shape decision in the codebase.

```python
type SourceResult[T] = Available[T] | Unavailable
```

`Available[T]` carries `data`, `fetched_at` and `notes`. `Unavailable` carries `source`, `reason`, `detail` and `observed_at`. Note what `Unavailable` does not carry: there is no `data` attribute. An unavailable source's data is unreadable by construction, not by convention. A caller that forgets to check the availability gets a type error from `mypy --strict` and an `AttributeError` at runtime, rather than a `None` that flows onward and reads as calm.

The second consequence is that **an outage is a value, not an exception.** No adapter raises when a source fails. It returns `Unavailable`, and the whole degraded path is ordinary control flow with tests over it. `RunAlertCycle` has no exception handler at all, which is only safe because of this.

`Available.notes` is the third piece, and it is subtler. A source can succeed and still owe the operator an explanation. A warnings page whose only aviso in force was filtered out, and a warnings page with genuinely nothing in force, produce identical `data`. Only one of them means the system is idle. `notes` carries what the source set aside on the way to answering. It is operator-facing English, never recipient-facing text, and it defaults to empty so a source with nothing to report says nothing.

`status_of(result)` returns the string `"available"` or `"unavailable"`. Those exact strings are stored on `RiskAssessment`, persisted in `AlertRecord`, and compared in `template.py`.

## `hazards.py`

**The SENAMHI warnings page is multi-hazard, and the original design did not account for that.** The module docstring records the measurement. Of the 788 rows live on 2026-09-04, roughly 233 were wind, 131 were daytime or nighttime temperature, and only 137 were precipitation. The single warning in force that day was a RED `INCREMENTO DE TEMPERATURA DIURNA`, which is a heat warning.

The evaluator's first branch raises `imminent` on any orange or red warning. Wiring the scraper up as originally designed would therefore have told the whole Ferreñafe community that flooding was imminent because of a heat wave.

Two rules stand between the page and the evaluator. They answer deliberately different questions.

| Function | Question | Answer for highlands rainfall |
|---|---|---|
| `is_flood_relevant(phenomenon)` | May this warning drive a flood alert at all? | Yes |
| `may_raise_imminent(phenomenon, zone)` | May it, on its own, claim flooding is imminent? | No |

### The vocabularies

`Phenomenon` has a member for every family SENAMHI actually publishes: `PRECIPITATION`, `WIND`, `HIGH_TEMPERATURE`, `LOW_TEMPERATURE`, `SNOW`, `HAIL`, `UNKNOWN`. Leaving a family unclassified is a hole in the filter, not a neutral gap, because `UNKNOWN` is treated as flood-relevant. `NEVADA` and `GRANIZO` would otherwise reach the evaluator as if they were rain.

`Zone` has `COAST`, `HIGHLANDS`, `COAST_AND_HIGHLANDS` and `UNKNOWN`.

The translation from SENAMHI's Spanish title wording into these terms is the adapter's job, in `adapters/senamhi_classification.py`. That vocabulary belongs to the source, not to the domain. This module owns the rules. The adapter owns the words.

### The asymmetry around `UNKNOWN` is deliberate

An unclassifiable phenomenon is treated as flood-relevant. An unclassifiable zone is treated as reaching the coast. Missing a real flood warning is far worse than raising one extra alert, so both defaults fail safe in the alerting direction.

The two `UNKNOWN` cases are not the same as `HIGHLANDS`. `HIGHLANDS` is a positive statement that the coast is not covered. `UNKNOWN` only means the title did not say.

### Why highlands rain counts at all

`may_raise_imminent` replaced an outright highlands exclusion on 2026-09-04, owner-approved, after the hydrology was checked. The reasoning is recorded in the function docstring and is worth restating, because it is the kind of thing a later reader would otherwise "simplify" back.

The Río La Leche rises at 4 230 m on Mount Choicopico, inside Ferreñafe province, and flows west through Ferreñafe and Lambayeque provinces. SENAMHI measures its critical level at the Puchaca station in Incahuasi, a sierra district of Ferreñafe province, and attributes rises there to intense rain in the basin headwaters. Sierra rain is therefore upstream of the city with hours of lead time. In the 2017 Niño Costero the La Leche overflowed under intense rain, and Batangrande in Pitipo district was among the worst-affected places in Peru.

The previous rule discarded 144 of the 243 live precipitation rows on the Lambayeque page. That included `PRECIPITACIONES EN LA SIERRA`, the page's single most common precipitation title at 78 rows. The old rule threw away the earliest signal available.

**Why it is still only an early signal.** Ferreñafe city itself is coastal. Sierra rain reaches it by river routing, not by falling overhead. So a highlands-only warning justifies preparation, and it may contribute to `prepare` through the combined branch, but it may not claim that flooding is already imminent. Claiming imminence on rain that has not yet run down the basin is exactly the false alarm that costs the system the credibility every future real warning depends on.

`is_flood_relevant` deliberately takes no geography parameter. Rain anywhere in the Río La Leche basin can flood Ferreñafe. Geography decides only how far the warning may take the level.

### `discard_reason`

Returns a string explaining why a warning was discarded, or `None` when it was kept. The operator audit trail needs this. Without it, "the filter removed everything" and "the scraper is broken" look identical from outside. The string is used by the scraper to build `Available.notes`.

## `sanitize.py`

One function, `sanitize_source_text(value)`, and it exists because of a defect that was reproduced end to end rather than argued about.

**The defect.** `Warning.title` is stored raw, deliberately — see [adapters.md](./adapters.md) for why normalizing it for storage once put `EXTENSION` in front of recipients. `template.py` interpolates that title into a body assembled with `"\n".join`, and that body is a line-oriented format whose grammar is `- ` bullets and `Motivos:` / `Recomendaciones:` headers. A title containing newlines therefore writes that grammar itself.

A hostile title produced a body with **two** `Recomendaciones:` sections, the forged one ahead of the genuine one and formatted identically, plus a live ANSI escape sequence and a surviving right-to-left override. A person deciding what to do in a flood cannot tell the forged instructions from the system's own, and that is the entire value of the message.

**What it does**, in this order:

| Step | Rule | Why in this order |
|---|---|---|
| 1 | Every whitespace run, newlines and tabs included, becomes a single space. | Newlines are *replaced*, not deleted. Deleting would weld two words together and change what the title says. |
| 2 | C0 and C1 control characters are removed. | This takes the `ESC` out of an ANSI sequence. The remaining `[31m` is inert text. |
| 3 | Unicode bidi controls are removed: U+202A–U+202E and U+2066–U+2069. | An override reverses the display order of everything after it. `json.dumps` escapes control characters but writes these through verbatim under `ensure_ascii=False`. |
| 4 | The text is trimmed and capped at `MAX_SOURCE_TEXT_LENGTH`, with a visible `…`. | The cap is applied *after* the removals, so padding with control characters is not a way to push real text past it. |

`MAX_SOURCE_TEXT_LENGTH` is 200. The number comes from the 2026-09-04 harvest of all 12 regional pages: the longest live title, suffix included, is well under 150 characters, and the one quoted in the code is 100. 200 leaves room for a longer real title than SENAMHI has ever published while stopping a hostile one from outgrowing the message it is quoted in.

**It is deliberately not an allow-list of characters.** Aviso titles are Spanish, accented, and carry em dashes and parentheses. A rule that stripped non-ASCII would mangle every real one. The rule removes what has no legitimate place in a one-line title and leaves the language alone.

**It is idempotent**, because it is applied at every boundary where source text is rendered for a human, and those boundaries must not have to know about each other.

### Where it is applied, and why it is not applied at parse time

| Boundary | Module | Audience |
|---|---|---|
| The Spanish community body | `domain/template.py` | recipient |
| The English operator audit line | `domain/reasons.py` | operator |
| The `Notes` block | `adapters/senamhi_scraper.parse_notes` | operator |
| The persisted and `--json` document | `adapters/serialization.reason_to_dict` | operator, and change 3's audit trail |

The alternative was to sanitize once when the title enters the domain, in the scraper. It was rejected: `Warning.title` is the record of what the source actually published, and the scraper's docstring already explains what happened the last time that value was normalized on the way in. Sanitizing is a rendering concern, so it happens at each rendering boundary — through one shared function, so there is one rule and not four.

Why the operator boundaries are included is a decision rather than an oversight. The operator's `Reasons` and `Notes` blocks are bullet lists printed straight to a terminal, so a newline forges a line there just as it does in the body, and an ANSI escape actually executes. The operator audit trail is also where someone decides whether a heat warning went out to a village, which makes a forged line there at least as costly as one in the body.

## `entities.py`

### `Warning`

A normalized SENAMHI entry: `source_id`, `title`, `level`, `region`, `window`, `emitted_at`, `phenomenon`, `zone`.

`phenomenon` and `zone` carry the classification the adapter inferred, so the flood-relevance decision is inspectable on the value itself rather than hidden inside whichever code filtered it. Both default to `UNKNOWN`, and both rules treat `UNKNOWN` permissively. The default therefore fails safe: a caller that forgets to classify produces one extra alert, never a missed flood warning.

Two properties delegate to `hazards.py`: `is_flood_relevant` and `may_raise_imminent`.

Note where each is actually consumed, because the two are not applied at the same layer. `may_raise_imminent` is read by `RiskEvaluator._imminent_from_official`. `is_flood_relevant` is not read anywhere in `src/` outside its own definition: the scraper applies the same rule through `discard_reason`, so warnings that reach the evaluator have already been filtered. The property exists for inspection and is asserted by the live canary tests.

### `HourlyPoint`

One hourly forecast sample: `at`, `precipitation_mm`, `probability_pct`. `__post_init__` enforces three ranges, and it enforces them here rather than only in the adapter for the same reason `Coordinates` and `TimeWindow` enforce theirs — this is where the invariant is claimed.

| Field | Rule |
|---|---|
| `precipitation_mm` | Must be finite, and must not be negative. |
| `probability_pct` | Must be in 0 to 100. |

**`Infinity` millimetres was not hypothetical.** `json.loads` accepts bare `NaN` and `Infinity` by default, so an anomalous upstream value parsed as a valid reading, accumulated to an infinite rainfall total, and cleared `imminent_mm_24h` unconditionally. An upstream glitch decided the alert level for a real community. The adapter now rejects it at parse time too; see [adapters.md](./adapters.md).

### `Forecast`

A non-empty, contiguous run of `HourlyPoint` values for one location. `__post_init__` rejects an empty series, and rejects any pair of consecutive points that are not exactly one hour apart. A gap is a construction failure, which is what stops a missing hour from being interpolated away somewhere downstream.

Every accessor takes the number of hours to read, and `_slice` refuses a horizon this forecast cannot answer. A 24-point forecast must not be used to answer a 48-hour question, because the answer would then be reported under an untruthful horizon. Callers that can tolerate a shorter horizon clamp their request against `horizon_hours` first, using `risk.evaluated_horizon`.

| Accessor | Returns |
|---|---|
| `horizon_hours` | How many hours of data this forecast actually carries. |
| `accumulated_mm(hours)` | Total precipitation over the first `hours` points. |
| `max_probability_pct(hours)` | The **maximum** hourly probability over the first `hours` points. |
| `peak_hour(hours)` | The point with the highest precipitation within the first `hours` points. |
| `precipitation_window(hours)` | The `TimeWindow` covering the period those points describe. |

**Probability is aggregated as the maximum, not the mean.** This is decision `D10` in the technical design, and it changes outcomes. A cloudburst is a few hours of very high probability inside an otherwise dry window. Averaging over 24 or 48 hours dilutes it below any useful threshold. The 2017 case that motivated the whole system was 29.8 mm concentrated in a single day. A mean would have missed it. The maximum asks "is there an hour in this window that the model is confident about", which is the question that matters for flooding.

**`precipitation_window` ends one hour after the last sample's timestamp.** An hourly sample stamped 14:00 describes 14:00 to 15:00. Ending the window at the last sample's own timestamp would under-report coverage by one hour, and would make `AlertMessage.valid_until` expire before the data runs out.

### `RiskAssessment`

The evaluator's verdict: `level`, `window`, `reasons`, `senamhi_status`, `open_meteo_status`, `degraded`. `reasons` is a tuple of structured `Reason` values, not strings, for the reason explained in the next section.

### `Contact`

A community-alert recipient: `contact_id`, `channel`, `handle`, `consent_at`. The consent timestamp is nullable. The only contact that exists in this change is the local operator's own terminal, described in [adapters.md](./adapters.md).

## `config.py`

Two frozen dataclasses, both injected rather than imported.

`RiskThresholds` holds `prepare_mm_48h`, `prepare_probability_pct`, `prepare_probability_pct_degraded`, `prepare_warning_levels`, `imminent_mm_24h`, `imminent_probability_pct`, `imminent_warning_levels`.

`AlertConfig` holds the whole per-city configuration: `city`, `city_slug`, `coordinates`, `timezone`, `region`, `thresholds`, `checklist`, `active_channel`, `forecast_hours`, `dedup_lookback_hours`, `coordinates_are_placeholder`.

`RiskEvaluator` is constructor-injected with `RiskThresholds`. **No threshold, coordinate or probability literal appears anywhere in `risk.py`.** That is what allows the thresholds to be calibrated during the first rainy season without touching the rules, and it is what change 3 needs in order to move them into Parameter Store. The shipped values live in `adapters/local/static_config_repository.py`, described in [adapters.md](./adapters.md).

`coordinates_are_placeholder` exists because Ferreñafe's exact coordinates are still an open decision. It travels all the way to the CLI, which prints a marker beside the coordinates on every run.

## `reasons.py`

**A reason serves two audiences whose words cannot be the same string.**

The operator needs an audit trail with the exact numbers that drove the verdict, in English like the rest of the codebase. The community needs neutral, professional Spanish prose, with no internal threshold values and no raw UTC timestamps.

A single pre-formatted English string cannot serve both. Using one leaked operator wording into the recipient body. So `RiskEvaluator` emits `Reason` values carrying the numbers it decided on, and each audience renders them: English here, Spanish in `template.py`.

The second benefit is that the audit trail cannot drift from the verdict, because the branch that made the decision is the same code that built the reason.

The variants are a tagged union rather than one record with optional fields, for the same reason `SourceResult` is. A `WarningReason` has no millimetre reading to misread, and `match` narrows cleanly.

| Variant | Carries | Means |
|---|---|---|
| `WarningReason` | `level`, `title` | An official SENAMHI warning drove part of the verdict. |
| `ForecastThresholdReason` | `accumulated_mm`, `hours`, `probability_pct`, `probability_threshold_pct` | A forecast reading drove part of the verdict. |
| `SenamhiUnavailableReason` | nothing | The official source could not be read, so evaluation was forecast-only. |
| `NoQualifyingWarningReason` | nothing | The official source was healthy but published no qualifying warning. |

Two fields deserve a note. `ForecastThresholdReason.hours` is the horizon actually evaluated, never the horizon requested, so a short forecast is never reported as a longer one. `probability_threshold_pct` is set only when a stricter, forecast-only threshold applied, and it is operator-only detail that the Spanish rendering never mentions.

`ReasonKind` is a serializable tag, needed because reasons are persisted. `AlertRecord.reasons` becomes the `reasons` attribute of the stored item, and the CLI's `--json` output emits it too.

`render_reason_en(reason)` produces the operator line. `render_reasons_en(reasons)` renders a tuple in the order the evaluator produced it. Both `match` statements end in `assert_never`, so adding a variant without giving it wording is a type error rather than a silent fallthrough.

**`WarningReason.title` is sanitized on the way out**, through `sanitize_source_text`. It is the only source-derived free text any `Reason` variant carries: `level` is a `WarningLevel` from a fixed token map, and every `ForecastThresholdReason` field is a number rendered with a format specifier. The rationale for sanitizing the *operator's* line, not only the recipient's, is in the `sanitize.py` section above.

## `risk.py`

`RiskEvaluator.evaluate(warnings, forecast, now)` returns a `RiskAssessment`.

It is deliberately not built as a rule registry, a predicate table or a threshold DSL. Two levels and a handful of branches do not clear the rule of three. Explicit ordered branches are the readable, debuggable form. Each branch's condition lives in a small named helper that returns a verdict or `None`.

### The branches, in evaluation order

The evaluator tries each in turn and returns on the first that produces a verdict.

| Order | Method | Fires when |
|---|---|---|
| 1 | `_imminent_from_official` | SENAMHI is available and at least one warning is in `imminent_warning_levels` **and** `may_raise_imminent`. |
| 2 | `_imminent_from_forecast` | Open-Meteo is available, accumulation over 24 h clears `imminent_mm_24h`, and max probability clears `imminent_probability_pct`. |
| 3 | `_prepare_combined` | Both sources available, at least one warning in `prepare_warning_levels`, and the 48 h forecast clears `prepare_mm_48h` and `prepare_probability_pct`. |
| 4 | `_prepare_forecast_only` | SENAMHI available with **no** qualifying warning, and the 48 h forecast clears `prepare_mm_48h` and the stricter `prepare_probability_pct_degraded`. |
| 5 | `_prepare_degraded` | SENAMHI unavailable, Open-Meteo available, and the same stricter pair of thresholds is cleared. |

If none fire, the level is `none` and the window is `now` to `now + 48 h`.

`IMMINENT_HORIZON_HOURS` is 24 and `PREPARE_HORIZON_HOURS` is 48. Both are module constants, because they are horizons, not tunable thresholds.

`degraded` on the result is true when either source reported unavailable, independently of which branch fired.

### Branch 4 exists because a broken source must not buy sensitivity

This is the branch a reader is most likely to think is redundant.

Without it, breaking the SENAMHI scraper made the system **more** likely to warn than a healthy one. The same forecast yielded `none` with a healthy, silent SENAMHI, but `prepare` with an unavailable one through branch 5. That is backwards. A broken source must never buy extra sensitivity.

So the forecast alone may fire `prepare` in branch 4 as well, at the same stricter `prepare_probability_pct_degraded` that branch 5 uses, never at the combined-rule threshold, because there is no official confirmation to combine with.

The two branches produce different leading reasons, so the audit trail still distinguishes them. Branch 4 leads with `NoQualifyingWarningReason`. Branch 5 leads with `SenamhiUnavailableReason`.

This same asymmetry is the reason the scraper refuses to report `available` on a broken parse. See [adapters.md](./adapters.md).

### `evaluated_horizon`

```python
def evaluated_horizon(forecast: Forecast, requested_hours: int) -> int:
    return min(requested_hours, forecast.horizon_hours)
```

A short forecast is answered over the hours it does carry, rather than rejected, and the reported horizon says so.

This is conservative in the safe direction, and the reasoning is worth keeping. An accumulation reached within fewer hours also satisfies the same threshold over a longer horizon. The maximum probability over fewer hours can only be lower or equal. So a truncated forecast can never make the system warn where a full one would not.

### Windows

`_union_window` produces the smallest window covering a set of windows. Branch 1 unions the windows of the matching warnings. Branch 3 unions those with the forecast's precipitation window. The forecast-only branches use `Forecast.precipitation_window` directly.

### One numbering, stated in the module docstring

There is exactly one branch numbering in this codebase and it is design.md section 5's: 1 imminent/official, 2 imminent/forecast, 3 prepare/combined, 4 prepare/forecast-only, 5 prepare/degraded, 6 none. "Six branches" means five named helpers in the tuple plus the fallthrough to `none` at the end of `evaluate`.

The docstrings used to invert 4 and 5 — `_prepare_forecast_only` called itself "Branch 5" and referred to `_prepare_degraded` as "branch 4" — which left a reader of `risk.py` reconciling two schemes. Corrected 2026-09-06 (verify findings S1, S2); the code was always right.

## `dedup.py`

`should_send(assessment, prior)` returns a `SendDecision` with a boolean and a reason string. It is pure. There is no `AlertRepository` import here, because importing a port from the domain would fail the architecture boundary test. The port-bound shell that fetches the prior records lives in `application/policies.py`.

The rule, in order:

| Condition | Decision | Reason string |
|---|---|---|
| `level == NONE` | Do not send | `level none` |
| No prior record overlaps the assessment window | Send | `new level for window` |
| An overlapping record exists and the new level is an escalation over the highest prior level | Send | `escalation from X to Y` |
| Otherwise | Do not send | `not an escalation over prior level X` |

Escalation is compared against the **highest** prior level among overlapping records, not the most recent one. De-escalation sends nothing.

## `outage.py`

`evaluate_outage(senamhi, open_meteo, active, city_slug, now)` returns an `OutageDecision` carrying `notices`, `next_state`, `state_changed` and `suppress_community_alert`.

**The state lives nowhere in the domain.** The active `OutageRecord` is passed in and the next one is returned, both as data. `RunAlertCycle` reads and writes it through `AlertRepository`. That is what makes separate process invocations emit exactly one notice: the CLI is a fresh process every run, so an in-memory flag would notify on every single cycle of a multi-day outage.

`suppress_community_alert` is true exactly when both sources are unavailable. With no data at all, the system does not know anything, and saying nothing to the community is the honest answer. Operator notices still go out.

### The state transitions

| Unavailable now | Active record | Notices | Next state |
|---|---|---|---|
| none | none | none | none |
| none | present | one `SOURCES_RECOVERED` | cleared |
| `{A}` | none | one `SOURCE_UNAVAILABLE` naming A | open, notified now |
| `{A}` | same set, notified | none | unchanged |
| `{A, B}` | none | one `SOURCE_UNAVAILABLE` naming both | open dual, notified |
| `{A, B}` | same set, notified | none | unchanged |
| `{A}` | `{A, B}` | `SOURCES_RECOVERED(B)` then `SOURCE_UNAVAILABLE(A)` | narrowed to `{A}` |
| `{A, B}` | `{A}` | one `SOURCE_UNAVAILABLE` naming the newly-down B | widened to `{A, B}` |

There is a ninth case the eight-row table leaves implicit, and the code handles it explicitly. **An active record whose set is unchanged but whose `notified_at` is `None` is still owed its notice.** It is notified now and stamped. Deduplicating on set equality alone would have lost that notice forever. The state can legitimately be persisted with `notified_at=None`, because change 3's adapter writes state and sends notices as separate steps.

Note the asymmetry when a set changes: if anything recovered, the follow-up `SOURCE_UNAVAILABLE` names everything still down, so the operator gets a complete picture. If nothing recovered and a source was newly added, it names only the newly-down source, so the notice is about what changed.

## `messages.py`

The record and message types. Seven frozen dataclasses.

| Type | Purpose |
|---|---|
| `ForecastSummary` | `mm_24h`, `mm_48h`, `peak_at`, `peak_probability_pct`. The subset the composer needs, never the raw series. |
| `WarningSummary` | `source_id`, `level`, `title`, `window`. The subset the composer needs. |
| `MessageRequest` | The composer's only input. |
| `AlertMessage` | A composed, ready-to-send community message. |
| `AlertRecord` | A durable record of a sent alert, which is the dedup lookback data. |
| `OutageRecord` | At most one active outage per city. Has an `is_dual()` helper. |
| `OperatorNotice` | A technical notice for the operator, distinct from a community alert. |

**`MessageRequest.level` is already decided when the composer receives it.** The composer is handed a level, a window and reasons. It has no input that would let it change any of them, and no caller reads its output to make a decision. This is decision `D11`, and it is why change 2 can swap a language model into the `MessageComposer` port without the model gaining any authority over whether an alert goes out. The type is shaped to match change 2's contract one for one.

`AlertRecord.composer` is a string, `"template"` or `"agent"`. `RunAlertCycle` writes `"template"` unconditionally in this change, because that is the only composer that exists.

## `template.py`

The deterministic `MessageComposer`. It satisfies the `MessageComposer` port, and it is the only implementation of that port in this change.

**Recipient-facing text is neutral, professional Spanish.** That is the one deliberate exception in a codebase that is otherwise entirely English: identifiers, comments, tests, commit messages and the operator audit trail all stay English, because the composed message is the only thing a Ferreñafe community member reads.

Two consequences of that split are load-bearing here.

**The evaluator's reasons arrive structured, and this module renders them into Spanish.** It never prints the operator's English audit line, and it never quotes an internal threshold value. `_reason_line_es` matches on the variant and produces the Spanish sentence. It returns `None` for `SenamhiUnavailableReason`, because the body already states that fact in its own dedicated sentence further down, and saying it twice reads as an error.

**Timestamps are converted to the configured local timezone.** Domain datetimes are UTC. A community reader must not be handed an ISO 8601 string with a `+00:00` offset. `_local_time` converts through `ZoneInfo(timezone)` and formats as `%d/%m/%Y %H:%M`. The message discloses the timezone name so the hour is unambiguous.

### The body it produces

The lines are assembled in a fixed order: city, level, window, then the reasons under `Motivos:`, then the degraded disclosures if either source was unavailable, then the checklist under `Recomendaciones:`. This is the real output for an orange coastal precipitation warning in force, quoted exactly as the code produces it.

```text
Ciudad: Ferreñafe
Nivel: riesgo inminente
Ventana: del 04/09/2026 00:00 al 07/09/2026 00:00 (hora local, America/Lima)
Motivos:
- Aviso oficial del SENAMHI, nivel naranja: PRECIPITACIONES EN LA COSTA NORTE Y SIERRA.
Recomendaciones:
- Almacena agua potable para al menos dos días.
- Protege documentos y aparatos eléctricos por encima del nivel del piso.
- Limpia canaletas, techos y desagües cercanos.
- Asegura objetos sueltos y calaminas.
- Ten a mano una linterna, un botiquín y los teléfonos de emergencia.
```

The title is `Alerta de lluvias — {city} — {level label}`. Level labels are `sin riesgo`, `prepárate` and `riesgo inminente`. Warning level labels are `amarillo`, `naranja` and `rojo`. `AlertMessage.valid_until` is the window end.

### No source-derived text may write that structure

The fixed line order above is a promise to the reader, and it only holds if nothing the system did not write can insert a line. **Every source-derived free text this module renders goes through `sanitize_source_text` first.** The module docstring carries the audit field by field; the short version is that `WarningReason.title` is the only one, and the rest is either configuration or a number.

The defense is the line orientation itself. A string that cannot contain a line break cannot forge a section, whatever words it contains — a hostile title that quotes `Recomendaciones:` ends up inside one `- Aviso oficial del SENAMHI ...` bullet under `Motivos:`, which is a very different thing from a second block formatted identically to the real one. `TestASourceDerivedTitleCannotForgeStructure` pins both halves: the forged section is gone, and the quoted words that remain stay inside their bullet.

The invariant is stated on the `MessageComposer` port rather than only here, so change 2's agent-backed composer inherits it. See [ports-and-application.md](./ports-and-application.md).

### Why the composer cannot decide anything

The class docstring states the invariant, and the structure delivers it. `RunAlertCycle` invokes `compose` only after `AlertPolicy` has already authorised the send. The return value is an `AlertMessage` that is passed to the notifier and stored in the record. No branch reads it. Replace this class with a language model in change 2 and the property holds unchanged, because it comes from the call graph and not from the implementation.

## Where to go next

- [README.md](./README.md) for the cycle these rules sit inside.
- [ports-and-application.md](./ports-and-application.md) for how the rules are called and in what order.
- [adapters.md](./adapters.md) for where `Warning` and `Forecast` values come from, and where the thresholds are defined.
- [entrypoints-and-testing.md](./entrypoints-and-testing.md) for the tests that pin each rule.
