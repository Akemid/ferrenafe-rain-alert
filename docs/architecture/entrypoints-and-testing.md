# Entrypoints and the test architecture

This document covers `src/rain_alert/entrypoints/` and everything under `tests/`. The entrypoints are how a person runs a cycle. The tests are how the project knows the rules still hold. Read it if you are running the tool, adding a test, refreshing a fixture, or checking the claim that the command line tool cannot transmit. Read [README.md](./README.md) for the cycle in outline.

## Part 1: the entrypoints

Two modules. `wiring.py` builds the object graph. `cli.py` parses arguments, runs one cycle and prints it.

### `wiring.py`

`build_local_deps(state_file, now, offline_fixtures=None, notifier_stream=None)` returns one `CycleDependencies`.

| Port | Live wiring | With `--offline-fixtures` |
|---|---|---|
| `config` | `StaticConfigRepository()` | same |
| `warnings` | `SenamhiWarningScraper(HttpxHtmlFetcher())` | `SenamhiWarningScraper(FileHtmlFetcher(dir / "senamhi.html"))` |
| `forecast` | `OpenMeteoForecastProvider()` | `OfflineOpenMeteoProvider(dir / "open_meteo.json")` |
| `composer` | `MessageComposer()` | same |
| `contacts` | `StaticContactRepository()` | same |
| `notifier` | `ConsoleNotifier(notifier_stream)` | same |
| `alerts` | `JsonFileAlertRepository(state_file)` | same |
| `evaluator_factory` | `RiskEvaluator` | same |
| `now` | the callable passed in | same |

Only two leaves change. **`--offline-fixtures` swaps the fetch leaves and nothing else.** The real scraper and the real payload parser still do the work, which is what makes an offline run trustworthy for calibration.

The fixture file names are pinned as `SENAMHI_FIXTURE_NAME` and `OPEN_METEO_FIXTURE_NAME`, and re-exported from `cli.py` so the help text and the tests use the same constants.

**`notifier_stream` exists because the caller, not the notifier, knows what else is on standard output.** Passing `None` keeps the notifier's own default of `sys.stdout`. The CLI passes standard error under `--json` and its own standard output otherwise; the reason is under [The JSON view](#the-json-view) below. It is a stream rather than a boolean flag because the notifier's job is to write somewhere, not to know that `--json` exists.

This function mirrors `tests.support.wiring.build_fake_deps`. Both return the same `CycleDependencies` type, so the object graph the CLI runs is the object graph the tests exercise. Only the leaves differ. That is the reason decision `D6` bundles the ports at all.

### `cli.py`

```bash
uv run rain-alert-cycle [--json] [--now ISO8601] [--state-file PATH] [--offline-fixtures DIR]
```

The entry point is registered in `pyproject.toml` as `rain-alert-cycle = "rain_alert.entrypoints.cli:main"`.

| Flag | Effect |
|---|---|
| `--json` | Emit the cycle as a machine-readable document instead of the operator text. |
| `--now` | Run at a given ISO 8601 instant, for reproducible runs. |
| `--state-file` | Where dedup and outage state is persisted. Defaults to `.local-state/alerts.json`. |
| `--offline-fixtures` | A directory holding `senamhi.html` and `open_meteo.json`. No network call is made. |

#### The command line tool cannot transmit, and that is structural

There is deliberately **no `--dry-run` flag**. Offering one would imply a send mode exists.

`build_local_deps` can only construct `ConsoleNotifier`, and no notifier capable of delivering anything exists anywhere in this codebase. The guarantee comes from the absence of a capability, not from a runtime check that could be flipped.

Two tests pin it. `test_there_is_deliberately_no_dry_run_flag` asserts that passing `--dry-run` exits non-zero. `test_the_wiring_can_only_construct_the_console_notifier` asserts the type of the notifier the wiring produces. A third test, `test_the_module_imports_no_client_that_could_deliver_anything`, parses `console_notifier.py` and asserts it imports nothing that could deliver a message.

#### Exit codes

**Exit code is 0 for any completed cycle**, including `none`, degraded and deduplicated outcomes. A degraded cycle is the system working as designed, and a non-zero code would break any future cron or CI wrapper.

`EXIT_CANNOT_START` is 2, and it is reserved for a run that could not start: an `--offline-fixtures` path that is not a directory, or an unparseable `--now`. Both are reported on standard error before anything else happens.

#### The two clocks

`default_now()` returns `datetime.now(UTC).replace(microsecond=0)`. Sub-second precision is noise in every consumer. It clutters the operator output, and `TimeWindow.key()` becomes change 3's DynamoDB sort key, where microseconds carry no meaning and only make keys harder to read and compare.

`parse_now(raw)` is **not** rounded. `--now` exists to make a run reproducible, so it is honoured exactly. A naive value is read as UTC. Any other offset is converted to UTC, because every domain datetime must be UTC.

#### The operator view

`render(result, config)` produces the labelled block shown in [README.md](./README.md). `_block(label, lines)` puts the label against the first line and hangs the rest under it, and prints an em dash when a block is empty.

Two output rules follow the specifications rather than convenience.

**Reasons are rendered in English**, through `domain.reasons.render_reasons_en`. This output is the operator's audit trail. It must carry the numbers and, when one applied, the stricter threshold. The Spanish recipient wording belongs to the template alone, and appears in this output only inside the message block, verbatim.

**The message text is printed on every run**, sent or not. On a send it is the message the composer actually produced. On a non-send, `_message_lines` calls the deterministic template directly on `CycleResult.message_request` and labels the block `PREVIEW (NOT SENT)`. The injected composer is never invoked, so a deduplicated cycle costs nothing. That matters once the composer is a paid model call in change 2.

`_note_lines` prints what each source set aside, prefixed with the source name. **This is the operator's only view of the multi-hazard filter.** `Level none` has two very different causes, nothing in force or everything in force filtered out, and a partial parse failure looks exactly like a calm page without it.

The coordinates line prints `(PLACEHOLDER — pending confirmation)` whenever `config.coordinates_are_placeholder` is true, which it is unless both environment overrides are supplied.

#### The JSON view

`as_json(result, config)` emits the same cycle as a sorted, indented document with `ensure_ascii=False`, so Spanish text stays readable.

**Reasons are emitted twice on purpose.** `reasons` keeps the tagged, numeric form a threshold review needs. `reasons_en` keeps the rendered operator lines so a log is readable without a decoder.

**Notes are emitted per source rather than flattened.** A calibration review needs to know which source filtered or lost something, not only that one did.

The document also carries `coordinates_are_placeholder`, so a calibration log records whether the coordinates were confirmed at the time of the run.

**`evaluated_at` is the cycle clock, not the window start.** It reads `CycleResult.evaluated_at`, which `RunAlertCycle` fills from the injected clock. It used to read `assessment.window.start`, which is a different fact: on an imminent-from-official verdict the window start is the aviso's own start date and can be days earlier than the run. It also duplicated `window_start` exactly, so the document carried no record of when the cycle actually ran — the one field that lets a reviewer correlate a verdict with the data available at that moment, in a document whose stated purpose is calibration logging.

**Under `--json`, standard output carries the document and nothing else.**

`ConsoleNotifier` also defaulted to standard output, so on any run that sent an alert or emitted an operator notice, the document was preceded by a `[DRY-RUN ALERT]` or `[OPERATOR NOTICE]` block and `json.loads` failed. The flag is documented as machine-readable output for calibration logging, so it broke on precisely the runs worth logging — a send and a degraded cycle are the two things a calibration log exists to record.

The CLI now hands the notifier standard error whenever `--json` is set. The blocks are **redirected, never suppressed**: the dry-run block is the operator's proof of what would have been delivered, and losing it to gain a parseable document would be a bad trade. Without `--json` the whole operator view belongs together, so the notifier writes to the same stream everything else does.

Passing the CLI's own stream on the human-readable path also closes a smaller latent bug. `ConsoleNotifier()` resolved `sys.stdout` for itself, so it ignored a stream explicitly injected into `main` — an embedding caller could capture the report and not the blocks.

`TestTheJsonDocumentOwnsStandardOutputAlone` pins all of it, including the triangulating case that the human-readable path still prints the block on standard output. Three pre-existing `--json` tests used to parse from `output[output.index("{"):]`, which quietly worked around the defect; they now parse the whole stream.

## Part 2: the test architecture

549 tests run by default, in about half a second. 15 more are deselected because they need the network.

```bash
uv run pytest                # the 549 offline tests
uv run pytest -m integration # the 15 live canaries
```

The deselection comes from `addopts = "-q -m 'not integration'"` in `pyproject.toml`. The offline suite stays offline by default, which is what keeps it usable as a feedback loop.

### Where the tests are

| Directory | Tests | What it checks |
|---|---|---|
| `tests/unit/domain/` | 213 | Every rule, value object and rendering, including the sanitizer. |
| `tests/unit/adapters/` | 265 | Parsing, classification, persistence, notification, serialization, offline sources. |
| `tests/unit/application/` | 23 | The lookback range, the warning summary's agreement with the verdict, and the cycle's call order. |
| `tests/unit/entrypoints/` | 36 | The CLI output, the stream split, exit codes, clocks and wiring. |
| `tests/unit/test_package_smoke.py` | 2 | The package and its five layer sub-packages import under their expected names. |
| `tests/architecture/` | 4 | The layer boundary, by static analysis. |
| `tests/hygiene/` | 6 | Public-repository constraints, including the committed-secrets scan. |
| `tests/integration/` | 15 | The live SENAMHI page and the live Open-Meteo API. |

The heaviest single files are `test_senamhi_classification.py` at 61, `test_local_repositories.py` at 59, `test_open_meteo.py` at 56 and `test_risk.py` at 43.

### Why every test directory has an `__init__.py`

There is no `pythonpath` entry in `pyproject.toml`. `tests/` and every sub-directory carry an `__init__.py`, so `tests.support.*` resolves as a real dotted import with canonical module names rather than through a `sys.path` knob. Widening `sys.path` to the repository root would make every top-level directory importable.

`tests/conftest.py` is intentionally empty. Shared setup lives in `tests/support/`, which is imported explicitly.

### `tests/architecture/test_layer_boundaries.py`

**The layer boundary is enforced by static analysis, not by convention.** This is the test that makes "the domain is pure" a fact rather than a hope.

It parses each `.py` file under `src/rain_alert/domain/` and `src/rain_alert/ports/` with `ast` and walks the whole tree. Static inspection rather than import-and-introspect buys three things:

- it catches imports nested **inside function bodies**;
- it catches imports inside `if TYPE_CHECKING:` blocks, which never execute;
- it never executes module side effects.

Two rules are checked.

**The domain may not import** any of `httpx`, `requests`, `urllib3`, `urllib.request`, `boto3`, `botocore`, `bs4`, `soupsieve`, `lxml`, `selectolax`, `strands`, `bedrock_agentcore`, `anthropic`, `openai` or `langchain`, nor anything under `rain_alert.adapters`, `rain_alert.entrypoints` or `rain_alert.application`. The list is designed to be extended by one line each in changes 2 and 3.

**The ports package may import** only `typing`, `collections.abc`, `datetime` and `rain_alert.domain`. That is an allowlist, so a new import is forbidden until someone adds it deliberately.

**Relative imports are resolved to absolute names before checking.** `_resolve_relative` turns `from ..adapters import x` inside `rain_alert.domain.sneaky` into `rain_alert.adapters`, and `from ... import adapters` inside `rain_alert.domain.rules.deeper` into the same. Without that step, the easiest way to break the boundary would also be the one the checker could not see.

Two of the four tests are triangulation. They build a fake package under `tmp_path` and assert the scanner **does** flag a violation, one hidden inside a function body and one written as a relative import. Without them, an empty result would prove nothing: a scanner that always returns `{}` would pass the two real assertions.

### `tests/hygiene/test_repo_hygiene.py`

Six checks that guard the public-repository constraints.

| Test | Asserts |
|---|---|
| `test_license_file_contains_apache_2_0_text` | `LICENSE` carries the Apache License 2.0 text. |
| `test_readme_contains_the_required_disclaimers` | `README.md` names SENAMHI and INDECI, disclaims warranty, and states the Open-Meteo non-commercial terms. |
| `test_gitignore_excludes_local_secrets_and_state` | `git check-ignore` says `.env`, `.local-state/` and `.venv/` would not be tracked. |
| `test_env_example_lists_variable_names_with_no_values` | Every non-comment line of `.env.example` is a bare variable name with an empty value. |
| `test_no_secret_or_personal_data_pattern_is_committed` | No tracked line matches any of six secret shapes, outside a named allow-list. |
| `test_every_allow_listed_placeholder_line_still_exists` | Every allow-list entry still forgives a line that exists. |

**The `.gitignore` check asks git rather than reading the file.** A substring scan passes on a `.gitignore` that only mentions `.env.example`, or only carries the comment "never commit .env". The scenario is about what git would track, so `git check-ignore -q` answers it from the ignore rules alone — the paths need not exist, so nothing is created and the local environment file is never read.

**The secrets scan is the regression guard for the highest-stakes hygiene requirement.** Six shape patterns — Peruvian phone numbers, email addresses, AWS access key ids, AWS account ids, GitHub tokens and API secret keys — run through `git grep -nIE`, so the file set is git's rather than the filesystem's and binaries are skipped. Until this existed, "no secrets, ever, at any commit" was verified only by hand, twice, on a public repository.

The allow-list is deliberately narrow: three named files plus the **exact** hostile-title placeholder string, so a real number in an allow-listed file still fails. Loosening the pattern instead would have disarmed the check for every future file. A second test fails if an allow-list entry outlives the line it forgives, because an entry left behind after its payload moves is a permanent hole with nothing to notice it.

The `.env.example` check parses each line at the first `=` and asserts the right side is empty, so a real value can never be committed there by accident.

### `tests/integration/`, the live canaries

These are opt-in, and they exist because **nothing offline can notice the live source changing**. Pinned fixtures prove the parser handles the structure as captured. They cannot prove the structure is still what was captured.

`test_open_meteo_live.py`, two tests. One fetches a real 48-hour forecast and asserts it starts at the current hour. The other asserts that `FORECAST_DAYS * 24 - now.hour >= 48` right now, which is the arithmetic behind requesting four days rather than two.

`test_senamhi_canary.py`, thirteen tests. This is the early warning for scraper breakage, and it is the only test that can be.

- The live page still matches the header signature and yields more than 100 rows, with fewer than half of them unparseable.
- The live page is still a multi-hazard feed the classifier recognizes. Two ways this can fail are both worth knowing: the page becomes precipitation-only and the filter is then redundant, or the classifier stops recognizing titles and everything falls through to `UNKNOWN`.
- **No live title at all falls through to `UNKNOWN`.** Zero, not "few". The reasoning is recorded in the test: a share-based bound cannot catch the one row that decides a cycle, because 789 rows of history dilute a single broken in-force title to 0.13 per cent.
- Every live title carrying a known family stem classifies into that family, checked per row and parametrized per stem. A family absent from today's page is skipped rather than asserted, because the page's mix is seasonal.
- Every warning the parser returns is flood-relevant, and none is wind.
- The port-level result the cycle actually consumes is `Available`.

**The page is fetched once per session, on a patient timeout.** Every test above used to call `_live_page()` and `_live_titles()` fetched again on top, so the file made roughly eight live requests for one page that cannot change between them, each under the production 3-second connect budget. Two of three verification runs failed on transient TLS connect timeouts, on a different test each time — and an unreliable canary is an ignored canary, which would leave the project with no drift detector at all. `_live_page` is now `lru_cache`d and uses `CANARY_TIMEOUT`, which is generous because a canary is not making the production availability decision that `DEFAULT_TIMEOUT` encodes. `test_the_scraper_reports_available_against_the_live_page` deliberately still fetches for itself on the production timeout, because it asserts what the real cycle would get.

### `tests/fixtures/`

The pinned HTML and JSON the offline suite parses. `tests/fixtures/senamhi/README.md` documents the capture and each derived file.

| Fixture | What it is | Expected result |
|---|---|---|
| `warnings_table.html` | The real capture: header row plus the 12 most recent rows, structure verbatim. Its one row in force is a RED `INCREMENTO DE TEMPERATURA DIURNA`, a heat warning. | `available`, **zero** warnings |
| `precipitation_coast_current.html` | Derived. The in-force row is replaced by an orange `PRECIPITACIONES EN LA COSTA NORTE Y SIERRA`. | `available`, one warning |
| `history_only.html` | Derived. The `(vigente)` marker is removed, so every row is historical, while the most recent row still carries dates containing the test clock. | `available`, zero warnings |
| `empty_table.html` | Derived. Header row present, zero data rows. | `unavailable`, `no_rows_extracted` |
| `broken_structure.html` | Derived. The table is replaced by `<div>` elements and the labels renamed. | `unavailable`, `structure_unrecognized` |

The first fixture is worth pausing on. **The real page's only warning in force on the capture date was a heat warning**, and the expected parse result is zero warnings. That fixture is the multi-hazard filter's proof, taken from reality rather than invented.

`history_only.html` is what makes "the marker is required, not just the dates" testable, because its dates alone would match.

`tests/fixtures/open_meteo/forecast_72h.json` is a recorded payload covering 2026-09-04T00:00 to 2026-09-06T23:00.

The fixtures README states the refresh rule plainly: re-capture only when the live structure has actually changed, and do not hand-edit a fixture to make a test pass. If the live structure changed, the parser is what must change.

### `tests/support/`

Three modules, shared across the unit and application suites.

**`fixtures.py`** holds the paths to the pinned fixture files, centralized so a rename breaks one line instead of every test, and so the CLI's offline tests and the adapter tests cannot drift onto different files.

**`fakes.py`** holds hand-written recording spies for all seven ports.

`unittest.mock` is deliberately avoided. A `Mock` satisfies any `Protocol`, and would hide exactly the drift these tests exist to catch. Every fake records its calls, so a test can assert order and arguments rather than only return values.

Two of the fakes are worth knowing about individually.

`FakeMessageComposer` delegates to the real deterministic template by default, so tests assert on real content without duplicating template logic. Its `calls` list is what "the composer was, or was not, invoked" assertions check. That list is how the tests pin the claim that a deduplicated cycle never composes anything.

`FakeAlertRepository` **composes** `InMemoryAlertRepository` rather than reimplementing the query, and its docstring records why. A third hand-written copy of the query once existed, and that copy also cleared the active outage without checking `city_slug`, so a fake the use-case tests trusted behaved differently from production. The class now owns exactly one thing, the call log. The behaviour is the real store's, and the shared port-contract tests in `tests/unit/adapters/test_local_repositories.py` run against this class too, so the two cannot drift again.

**`wiring.py`** holds `build_fake_deps`, the fake side of `CycleDependencies`. It provides calm defaults: a fixed clock at 2026-09-03T12:00Z, the same threshold values the shipped config uses, an empty warnings tuple, and 48 contiguous hours of zero precipitation. A test overrides only the leaf it cares about, for example `build_fake_deps(forecast=Unavailable(...))`. Every spy leaf stays accessible on the returned object for call-order and argument assertions.

### How the suites divide the work

The division is visible in the test docstrings and is worth naming, because it is what keeps the suite fast and the failures legible.

| Question | Where it is answered |
|---|---|
| Does the rule give the right level? | `tests/unit/domain/test_risk.py`, a parametrized scenario table plus boundary cases pinned at and just below every numeric threshold. |
| Does the shell ask for the right key range? | `tests/unit/application/test_policies.py`, four tests. The pure rule is not re-tested there. |
| Does the cycle call things in the right order? | `tests/unit/application/test_run_alert_cycle.py`, using the recording spies. |
| Does the parser handle this page? | `tests/unit/adapters/test_senamhi_scraper.py`, against pinned fixtures. |
| Is the live page still that page? | `tests/integration/test_senamhi_canary.py`, opt-in. |
| Does the operator see the right thing? | `tests/unit/entrypoints/test_cli.py`, capturing standard output. |

`test_risk.py` also carries a test named `test_a_healthy_silent_senamhi_is_never_less_sensitive_than_an_unavailable_one`. That is the property described in [domain.md](./domain.md) and [adapters.md](./adapters.md), asserted directly rather than left as prose.

## Tooling

| Command | What it enforces |
|---|---|
| `uv run pytest` | The 549 offline tests. |
| `uv run pytest -m integration` | The 15 live canaries. |
| `uv run ruff check .` | Lint rules `E`, `W`, `F`, `I`, `B`, `C4`, `UP`, `SIM`, with `E501` ignored and a 120 column limit. |
| `uv run ruff format --check .` | Formatting. |
| `uv run mypy` | `strict = true` over `src/`, which is what guards port conformance. |

`ruff` excludes `openspec` and `docs`. It formats embedded code in Markdown, which would rewrite planning artifacts that this change must not touch.

`mypy` is configured with `files = ["src"]`, and relaxes `disallow_untyped_defs` for `tests.*`.

## Where to go next

- [README.md](./README.md) for the cycle in outline and the file-to-purpose map.
- [domain.md](./domain.md) for the rules the unit suite pins.
- [ports-and-application.md](./ports-and-application.md) for the object graph these tests build.
- [adapters.md](./adapters.md) for the adapters the fixtures exercise.
