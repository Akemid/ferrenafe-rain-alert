# Public Status Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish what each alert cycle found to a public web page, so a resident of Ferreñafe can answer "do I need to worry today?" — including on the days when the answer is no.

**Architecture:** `RunAlertCycle.execute()` already returns a `CycleResult` carrying everything needed, and the CLI entry point already holds it. A new `SnapshotPublisher` port is called **from the entry point**, so neither the domain nor the application layer changes. Two adapters (local JSON file, S3) match the existing repository pattern. An Astro site on Amplify Hosting fetches the published JSON at runtime through a reverse-proxy rewrite that makes it same-origin.

**Tech Stack:** Python 3.12 + uv (existing), boto3, Astro + TypeScript, AWS CDK (TypeScript), Amplify Hosting, S3.

**Spec:** `docs/superpowers/specs/2026-09-21-public-status-page-design.md`

## Global Constraints

- **Strict TDD.** Failing test first, and it must fail for the reason you intend. No exceptions.
- **The default suite stays offline.** No network, no credentials. Verify with `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN uv run pytest`.
- **No `unittest.mock`.** Hand-written fakes, following `tests/support/fakes.py`.
- **Never commit a secret, an AWS account id, or an ARN containing one.** `tests/hygiene/test_repo_hygiene.py` scans the working tree *and all git history* for `AKIA[0-9A-Z]{16}` and `\b[0-9]{12}\b`. Write `<account>`.
- **Artifacts are English** — code, comments, tests, docs, commit messages. **Exception:** text a Ferreñafe resident reads is Spanish. That covers the alert message and the page's own interface copy.
- **Verification gate**, run bare (naming a path bypasses the excludes):
  ```
  uv run pytest
  uv run --directory agent pytest
  uv run ruff check
  uv run ruff format --check
  uv run mypy
  ```
- **A security review runs on the branch diff before the PR.**
- Conventional commits. **No `Co-Authored-By` and no AI attribution** — the
  project's own rule in `~/.claude/CLAUDE.md`, which the session's attribution
  reminder explicitly defers to. It also keeps an address out of a file the
  hygiene scan reads.

---

## File Structure

| Path | Responsibility |
|---|---|
| `src/rain_alert/domain/template.py` | *modified* — promote the Spanish level labels and reason renderer to public names |
| `src/rain_alert/domain/snapshot.py` | *new* — pure function: `CycleResult` + `AlertConfig` → the snapshot document |
| `src/rain_alert/ports/__init__.py` | *modified* — add the `SnapshotPublisher` protocol |
| `src/rain_alert/adapters/local/json_file_snapshot_publisher.py` | *new* — writes the document to a path |
| `src/rain_alert/adapters/s3_snapshot_publisher.py` | *new* — uploads it, client injected and built lazily |
| `src/rain_alert/entrypoints/cli.py` | *modified* — publish after the cycle, never failing it |
| `contracts/public-snapshot.json` | *new* — the golden document both sides assert against |
| `infra/` | *new* — CDK stack: one bucket, one scoped policy |
| `web/` | *new* — the Astro site |
| `docs/runbooks/public-page-deploy.md` | *new* — the Amplify console steps |

---

## Task 1: Promote the Spanish renderer out of `template.py`'s privates

The snapshot needs Spanish level labels and reason lines. They exist, and they are private. The alternative is a second Spanish translation table in JavaScript that drifts from the domain on the first edit.

**Files:**
- Modify: `src/rain_alert/domain/template.py:77` (`_LEVEL_LABELS_ES`), `:102` (`_reason_line_es`)
- Test: `tests/unit/domain/test_template.py`

**Interfaces:**
- Produces: `LEVEL_LABELS_ES: Mapping[Level, str]`, `render_reason_es(reason: Reason) -> str | None`. Behaviour identical to the private versions; `render_reason_es` still returns `None` for the reasons the body states in their own sentence.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/domain/test_template.py
from rain_alert.domain.template import LEVEL_LABELS_ES, render_reason_es
from rain_alert.domain.reasons import WarningReason
from rain_alert.domain.values import Level, WarningLevel


class TestTheSpanishRendererIsPublic:
    """The snapshot published for the public page needs these, and the page
    must not carry a second Spanish table that drifts from this one. Promoting
    them is a rename: the domain already owned what this system says in
    Spanish."""

    def test_every_level_has_a_label(self) -> None:
        assert set(LEVEL_LABELS_ES) == set(Level)

    def test_a_warning_reason_renders_the_line_the_body_uses(self) -> None:
        reason = WarningReason(level=WarningLevel.ORANGE, title="PRECIPITACIONES EN LA COSTA")

        assert render_reason_es(reason) == (
            "Aviso oficial del SENAMHI, nivel naranja: PRECIPITACIONES EN LA COSTA."
        )
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/unit/domain/test_template.py -k SpanishRendererIsPublic -v`
Expected: `ImportError: cannot import name 'LEVEL_LABELS_ES'`

- [ ] **Step 3: Rename, and keep the private names as aliases inside the module**

In `src/rain_alert/domain/template.py`, rename `_LEVEL_LABELS_ES` → `LEVEL_LABELS_ES` and `_reason_line_es` → `render_reason_es`, updating every internal reference. Add to the module docstring:

```python
#: Public because `domain/snapshot.py` renders the same reasons for the public
#: page. The domain decides what this system says in Spanish; a second
#: translation table in the web layer would drift on the first edit.
```

- [ ] **Step 4: Run the full domain suite**

Run: `uv run pytest tests/unit/domain -q`
Expected: PASS. Invariant V0 covers the template's output, so an accidental behaviour change fails here rather than in production.

- [ ] **Step 5: Commit**

```bash
git add src/rain_alert/domain/template.py tests/unit/domain/test_template.py
git commit -m "refactor(domain): make the Spanish level labels and reason renderer public

The public page's snapshot needs them. The alternative is a second Spanish
table in JavaScript, which drifts from the domain on the first edit — and the
domain is what decides what this system says to a community.

A rename, behaviour unchanged; invariant V0 pins that.
```

---

## Task 2: The snapshot document

A pure function. No I/O, no AWS, no clock — everything it needs is on the inputs, which is what makes it testable without fakes.

**Files:**
- Create: `src/rain_alert/domain/snapshot.py`
- Test: `tests/unit/domain/test_snapshot.py`

**Interfaces:**
- Consumes: `LEVEL_LABELS_ES`, `render_reason_es` from Task 1.
- Produces: `SNAPSHOT_SCHEMA_VERSION: int = 1` and
  `build_snapshot(result: CycleResult, config: AlertConfig, recent: tuple[AlertRecord, ...]) -> dict[str, Any]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/domain/test_snapshot.py
from rain_alert.domain.snapshot import SNAPSHOT_SCHEMA_VERSION, build_snapshot


class TestTheSnapshotSaysWhatThePageShows:
    def test_a_cycle_that_did_not_alert_still_publishes_a_level(self) -> None:
        """The whole point: "no risk today" is the answer a resident usually
        needs, and the system currently has no way to say it."""
        document = build_snapshot(quiet_cycle_result(), config(), recent=())

        assert document["schema_version"] == SNAPSHOT_SCHEMA_VERSION
        assert document["level"] == "none"
        assert document["level_label"] == "sin riesgo"
        assert document["alert"] is None

    def test_reasons_are_published_in_spanish(self) -> None:
        document = build_snapshot(alerting_cycle_result(), config(), recent=())

        assert document["reasons"] == (
            ["Aviso oficial del SENAMHI, nivel naranja: PRECIPITACIONES EN LA COSTA."]
        )

    def test_an_alert_carries_which_composer_wrote_it(self) -> None:
        """Published deliberately. A system that puts a model in front of a
        community should be able to say when it did."""
        document = build_snapshot(alerting_cycle_result(), config(), recent=())

        assert document["alert"]["composed_by"] == "template"

    def test_the_recipient_count_is_never_published(self) -> None:
        """Operational — how many people are subscribed. No resident needs it."""
        document = build_snapshot(alerting_cycle_result(), config(), recent=())

        assert "recipients_count" not in json.dumps(document)

    def test_the_document_is_json_serializable(self) -> None:
        document = build_snapshot(alerting_cycle_result(), config(), recent=())

        assert json.loads(json.dumps(document)) == document
```

Write `quiet_cycle_result()` and `alerting_cycle_result()` as module-level helpers built from `tests/support/wiring.py`'s `build_fake_deps()`, following the existing fixture style in `tests/unit/domain/test_message_validation.py`.

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/unit/domain/test_snapshot.py -v`
Expected: `ModuleNotFoundError: No module named 'rain_alert.domain.snapshot'`

- [ ] **Step 3: Implement**

```python
"""The document the public page reads (design 2026-09-21).

Pure: no I/O, no clock, no AWS. Everything it needs is on its inputs, which
is what lets it be tested without a single fake.

**Why the domain and not an adapter.** It decides what this system says to a
community in Spanish, which is the same argument `domain/template.py` makes.
An adapter would have to reach back into the domain for every label anyway.
"""

from __future__ import annotations

from typing import Any

from rain_alert.application.run_alert_cycle import CycleResult
from rain_alert.domain.config import AlertConfig
from rain_alert.domain.messages import AlertRecord
from rain_alert.domain.template import LEVEL_LABELS_ES, render_reason_es

#: Bumped when a published field changes meaning or disappears. The page reads
#: this before anything else, so an old page against a new document can say so
#: rather than render a blank.
SNAPSHOT_SCHEMA_VERSION = 1


def _local(moment: datetime, timezone: str) -> str:
    return moment.astimezone(ZoneInfo(timezone)).isoformat()


def build_snapshot(
    result: CycleResult, config: AlertConfig, recent: tuple[AlertRecord, ...]
) -> dict[str, Any]:
    """One cycle, as the page reads it."""
    zone = config.timezone
    assessment = result.assessment
    alert: dict[str, Any] | None = None
    if result.message is not None:
        alert = {
            "title": result.message.title,
            "body": result.message.body,
            "valid_until": _local(result.message.valid_until, zone),
            "composed_by": result.message.composed_by.value,
            "sent": result.sent,
        }
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "city": config.city,
        "evaluated_at": _local(result.evaluated_at, zone),
        "level": assessment.level.value,
        "level_label": LEVEL_LABELS_ES[assessment.level],
        "window": {
            "start": _local(assessment.window.start, zone),
            "end": _local(assessment.window.end, zone),
        },
        "reasons": [line for r in assessment.reasons if (line := render_reason_es(r)) is not None],
        "sources": {
            "senamhi": assessment.senamhi_status,
            "open_meteo": assessment.open_meteo_status,
        },
        "degraded": assessment.degraded,
        "alert": alert,
        "recent_alerts": [
            {
                "level": record.level.value,
                "sent_at": _local(record.sent_at, zone),
                "title": record.message.title,
                "composed_by": record.message.composed_by.value,
            }
            for record in recent
        ],
    }
```

- [ ] **Step 4: Run tests and the type checker**

Run: `uv run pytest tests/unit/domain/test_snapshot.py -q && uv run mypy`
Expected: PASS, clean.

- [ ] **Step 5: Commit**

```bash
git add src/rain_alert/domain/snapshot.py tests/unit/domain/test_snapshot.py
git commit -m "feat(domain): build the snapshot the public page reads

Pure function, no I/O and no clock, so it tests without fakes. Publishes a
level on every cycle — including the quiet ones, which is the point: \"no risk
today\" is the answer a resident usually needs and the system had no way to
say it.

Carries \`composed_by\`, deliberately. A system that puts a model in front of a
community should be able to say when it did. Excludes \`recipients_count\`,
which is operational.
```

---

## Task 3: The port and the local adapter

**Files:**
- Modify: `src/rain_alert/ports/__init__.py` (after `AlertRepository`, line ~72)
- Create: `src/rain_alert/adapters/local/json_file_snapshot_publisher.py`
- Test: `tests/unit/adapters/test_json_file_snapshot_publisher.py`

**Interfaces:**
- Produces: `SnapshotPublisher` protocol with `def publish(self, document: dict[str, Any]) -> None: ...`, and `JsonFileSnapshotPublisher(path: Path)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/adapters/test_json_file_snapshot_publisher.py
def test_the_document_is_written_as_utf8_json(tmp_path: Path) -> None:
    target = tmp_path / "status.json"

    JsonFileSnapshotPublisher(target).publish({"city": "Ferreñafe", "level": "none"})

    assert json.loads(target.read_text(encoding="utf-8")) == {"city": "Ferreñafe", "level": "none"}


def test_a_missing_parent_directory_is_created(tmp_path: Path) -> None:
    """An operator pointing this at a fresh path should not have to mkdir first."""
    target = tmp_path / "out" / "status.json"

    JsonFileSnapshotPublisher(target).publish({"level": "none"})

    assert target.is_file()


def test_publishing_twice_replaces_rather_than_appends(tmp_path: Path) -> None:
    target = tmp_path / "status.json"
    publisher = JsonFileSnapshotPublisher(target)

    publisher.publish({"level": "none"})
    publisher.publish({"level": "prepare"})

    assert json.loads(target.read_text(encoding="utf-8")) == {"level": "prepare"}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/unit/adapters/test_json_file_snapshot_publisher.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Add the port, then the adapter**

```python
# src/rain_alert/ports/__init__.py — after AlertRepository
class SnapshotPublisher(Protocol):
    """Where one cycle's public snapshot goes.

    Called from the entry point, not from `RunAlertCycle`: `CycleResult`
    already carries everything and is already returned, so the use case does
    not need to know a web page exists.
    """

    def publish(self, document: dict[str, Any]) -> None: ...
```

```python
# src/rain_alert/adapters/local/json_file_snapshot_publisher.py
class JsonFileSnapshotPublisher:
    """`SnapshotPublisher` writing to a local path, for development and for
    keeping the default suite offline. Mirrors `json_alert_repository.py`."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def publish(self, document: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
```

- [ ] **Step 4: Run tests and mypy**

Run: `uv run pytest tests/unit/adapters/test_json_file_snapshot_publisher.py -q && uv run mypy`
Expected: PASS, clean.

- [ ] **Step 5: Commit**

```bash
git add src/rain_alert/ports/__init__.py src/rain_alert/adapters/local/json_file_snapshot_publisher.py tests/unit/adapters/test_json_file_snapshot_publisher.py
git commit -m "feat(ports): add SnapshotPublisher and its local file adapter

Called from the entry point rather than from RunAlertCycle, so neither the
domain nor the application layer learns that a web page exists.
```

---

## Task 4: The S3 adapter

**Files:**
- Create: `src/rain_alert/adapters/s3_snapshot_publisher.py`
- Test: `tests/unit/adapters/test_s3_snapshot_publisher.py`

**Interfaces:**
- Produces: `S3SnapshotPublisher(bucket: str, key: str = "status.json", client: Any | None = None)`, and env vars `RAIN_ALERT_SNAPSHOT_BUCKET`, `RAIN_ALERT_SNAPSHOT_KEY`.

- [ ] **Step 1: Write the failing test**

```python
class FakeS3Client:
    """Records what it was asked to put. No unittest.mock, per project rule."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._error = error

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return {}


def test_the_document_is_uploaded_as_utf8_json() -> None:
    client = FakeS3Client()

    S3SnapshotPublisher("a-bucket", client=client).publish({"city": "Ferreñafe"})

    call = client.calls[0]
    assert call["Bucket"] == "a-bucket"
    assert call["Key"] == "status.json"
    assert json.loads(call["Body"].decode("utf-8")) == {"city": "Ferreñafe"}


def test_the_content_type_lets_a_browser_read_it() -> None:
    """Served through Amplify's proxy to a browser. Without this S3 serves
    application/octet-stream and the fetch parses nothing."""
    client = FakeS3Client()

    S3SnapshotPublisher("a-bucket", client=client).publish({})

    assert client.calls[0]["ContentType"] == "application/json"


def test_no_client_is_built_when_one_is_injected() -> None:
    """The offline guarantee: constructing a boto3 client needs a region, and
    the default suite has none. Same seam as agentcore_invoker."""
    publisher = S3SnapshotPublisher("a-bucket", client=FakeS3Client())

    publisher.publish({})  # must not raise NoRegionError
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/unit/adapters/test_s3_snapshot_publisher.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement, with the client built lazily**

```python
class S3SnapshotPublisher:
    """`SnapshotPublisher` over `boto3`'s S3 client.

    The client is injected and built on first `publish`, exactly as
    `agentcore_invoker.py` does: constructing one eagerly raises without a
    region configured, which would break `uv run pytest` on a clean machine.
    """

    def __init__(self, bucket: str, key: str = "status.json", client: Any | None = None) -> None:
        self._bucket = bucket
        self._key = key
        self._client = client

    def _resolved_client(self) -> Any:
        if self._client is None:
            self._client = boto3.client("s3")
        return self._client

    def publish(self, document: dict[str, Any]) -> None:
        body = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self._resolved_client().put_object(
            Bucket=self._bucket,
            Key=self._key,
            Body=body,
            ContentType="application/json",
            CacheControl="max-age=60",
        )
```

- [ ] **Step 4: Run the full suite with credentials unset**

Run: `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE uv run pytest -q`
Expected: PASS. This is the offline guarantee, and it is the assertion that matters most in this task.

- [ ] **Step 5: Commit**

```bash
git add src/rain_alert/adapters/s3_snapshot_publisher.py tests/unit/adapters/test_s3_snapshot_publisher.py
git commit -m "feat(adapters): publish the snapshot to S3

Client injected and built lazily, the same seam as agentcore_invoker: an
eager boto3 client raises without a region and would break the suite on a
clean machine.
```

---

## Task 5: Wire it into the CLI, without ever breaking the cycle

**The rule this task exists to enforce:** publishing is reporting, not
notifying. If S3 is unreachable the alert still goes out. Same posture as the
operator-notice guard in `agent_composer.py::_fell_back`.

**Files:**
- Modify: `src/rain_alert/entrypoints/cli.py:312` (after `result = RunAlertCycle(deps).execute()`)
- Modify: `src/rain_alert/entrypoints/wiring.py` — `select_snapshot_publisher(env)`
- Test: `tests/unit/entrypoints/test_cli.py`, `tests/unit/entrypoints/test_wiring.py`

**Interfaces:**
- Consumes: `build_snapshot` (Task 2), `SnapshotPublisher` (Task 3), `S3SnapshotPublisher` (Task 4).
- Produces: `select_snapshot_publisher(env: Mapping[str, str]) -> SnapshotPublisher | None` — `None` when neither env var is set, which is the default and means "do not publish".

- [ ] **Step 1: Write the failing test**

```python
class RaisingPublisher:
    def publish(self, document: dict[str, Any]) -> None:
        raise OSError("bucket on fire")


def test_a_publisher_that_raises_does_not_fail_the_cycle(capsys) -> None:
    """The alert reaches the community even when the page cannot be updated.
    A failure in the reporting channel must not cost the community its alert."""
    exit_code = main(["--json"], publisher=RaisingPublisher(), deps=build_fake_deps())

    assert exit_code == EXIT_OK


def test_the_published_document_describes_the_cycle_that_just_ran() -> None:
    publisher = RecordingPublisher()

    main([], publisher=publisher, deps=build_fake_deps())

    assert publisher.documents[0]["city"] == "Ferreñafe"
    assert publisher.documents[0]["schema_version"] == 1


def test_nothing_is_published_when_no_destination_is_configured() -> None:
    assert select_snapshot_publisher({}) is None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/unit/entrypoints -k snapshot -v`
Expected: FAIL — `main()` takes no `publisher` argument.

- [ ] **Step 3: Implement**

In `wiring.py`:

```python
def select_snapshot_publisher(env: Mapping[str, str]) -> SnapshotPublisher | None:
    """The publisher the environment asks for, or `None` for "do not publish".

    Absent by default on purpose: a developer running the cycle locally should
    not write to a public bucket by accident.
    """
    bucket = env.get(SNAPSHOT_BUCKET_ENV_VAR)
    if bucket:
        return S3SnapshotPublisher(bucket, key=env.get(SNAPSHOT_KEY_ENV_VAR, "status.json"))
    path = env.get(SNAPSHOT_PATH_ENV_VAR)
    return JsonFileSnapshotPublisher(Path(path)) if path else None
```

In `cli.py`, immediately after the existing `result = RunAlertCycle(deps).execute()`:

```python
    if publisher is not None:
        # Reporting, not notifying. A page that cannot be updated is worth
        # strictly less than an alert that does not go out, so every failure
        # here is swallowed and mentioned, never raised.
        with suppress(Exception):
            publisher.publish(build_snapshot(result, config, recent_alerts(deps, config)))
```

- [ ] **Step 4: Run the entry-point suite and the whole gate**

Run: `uv run pytest -q && uv run mypy && uv run ruff check`
Expected: PASS, clean.

- [ ] **Step 5: Commit**

```bash
git add src/rain_alert/entrypoints tests/unit/entrypoints
git commit -m "feat(entrypoints): publish a snapshot after each cycle

Guarded: publishing is reporting, not notifying. If the destination is
unreachable the alert still goes out — a page that cannot be updated is worth
strictly less than an alert that does not. Same posture as the operator-notice
guard in agent_composer.

Absent by default, so running the cycle locally never writes to a public
bucket by accident.
```

---

## Task 6: The golden contract

The seam that breaks silently: nothing compiles the Python publisher and the
TypeScript page together. Rename a field and the page shows a gap while both
suites stay green. This project already chose a pattern for that —
`contracts/agent-composition.json`.

**Files:**
- Create: `contracts/public-snapshot.json`
- Test: `tests/unit/domain/test_snapshot.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
def test_the_published_keys_match_the_golden_contract() -> None:
    """`contracts/public-snapshot.json` is asserted from both sides. The web
    suite asserts the page reads exactly these; this asserts the publisher
    writes exactly these."""
    golden = json.loads(Path("contracts/public-snapshot.json").read_text(encoding="utf-8"))
    document = build_snapshot(alerting_cycle_result(), config(), recent=())

    assert set(document) == set(golden["top_level"])
    assert set(document["alert"]) == set(golden["alert"])
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/unit/domain/test_snapshot.py -k golden -v`
Expected: `FileNotFoundError: contracts/public-snapshot.json`

- [ ] **Step 3: Write the contract**

```json
{
  "schema_version": 1,
  "top_level": ["schema_version", "city", "evaluated_at", "level", "level_label",
                "window", "reasons", "sources", "degraded", "alert", "recent_alerts"],
  "alert": ["title", "body", "valid_until", "composed_by", "sent"],
  "recent_alert": ["level", "sent_at", "title", "composed_by"]
}
```

- [ ] **Step 4: Run and commit**

Run: `uv run pytest tests/unit/domain/test_snapshot.py -q`

```bash
git add contracts/public-snapshot.json tests/unit/domain/test_snapshot.py
git commit -m "test(contracts): pin the public snapshot's shape from both sides

Nothing compiles the Python publisher and the TypeScript page together, so a
renamed field shows as a gap on the page while both suites stay green. Same
golden-document pattern as contracts/agent-composition.json.
```

---

## Task 7: The bucket, as code

**Files:**
- Create: `infra/` — a minimal CDK TypeScript app (`bin/`, `lib/`, `cdk.json`, `package.json`, `tsconfig.json`)
- Create: `docs/runbooks/public-page-deploy.md`

`agentcore/cdk/` cannot host this: the AgentCore CLI regenerates it and would
overwrite the stack.

- [ ] **Step 1: Scaffold and write the stack**

```bash
mkdir -p infra && cd infra && npx --yes aws-cdk@latest init app --language typescript
```

```typescript
// infra/lib/public-snapshot-stack.ts
const bucket = new s3.Bucket(this, 'PublicSnapshot', {
  // Amplify's reverse proxy fetches server-side and does NOT sign the
  // request, so the object must be publicly readable. The rewrite hides it
  // from the browser; it does not protect it. Acceptable because this bucket
  // holds one file whose contents are exactly what the page displays — and
  // nothing else ever goes in here.
  blockPublicAccess: new s3.BlockPublicAccess({
    blockPublicAcls: true,
    ignorePublicAcls: true,
    blockPublicPolicy: false,
    restrictPublicBuckets: false,
  }),
  removalPolicy: RemovalPolicy.DESTROY,
  autoDeleteObjects: true,
});

bucket.addToResourcePolicy(new iam.PolicyStatement({
  actions: ['s3:GetObject'],
  principals: [new iam.AnyPrincipal()],
  // One key. Not the bucket.
  resources: [bucket.arnForObjects('status.json')],
}));

new CfnOutput(this, 'SnapshotBucketName', { value: bucket.bucketName });
new CfnOutput(this, 'SnapshotObjectUrl', { value: bucket.urlForObject('status.json') });
```

- [ ] **Step 2: Synthesize and read what it would create — do not deploy yet**

Run: `cd infra && AWS_PROFILE=ferrenafe npx cdk synth`
Expected: a template with `AWS::S3::Bucket` and `AWS::S3::BucketPolicy`, and nothing else. Read the policy statement and confirm the resource is the single key, not `arn:…:bucket/*`.

- [ ] **Step 3: Deploy**

Run: `cd infra && AWS_PROFILE=ferrenafe AWS_REGION=us-east-2 npx cdk deploy`

- [ ] **Step 4: Verify the object is readable and the bucket is not listable**

```bash
curl -s -o /dev/null -w "object: %{http_code}\n" "<SnapshotObjectUrl>"
curl -s -o /dev/null -w "listing: %{http_code}\n" "https://<bucket>.s3.<region>.amazonaws.com/"
```
Expected: object `200` once something has been published, `403` before. Listing must be `403` — if it is `200`, the policy is too wide; stop and fix it.

- [ ] **Step 5: Commit**

```bash
git add infra docs/runbooks/public-page-deploy.md
git commit -m "feat(infra): one bucket for the public snapshot, one key in its policy

Amplify's reverse proxy fetches unsigned, so the object must be publicly
readable. The rewrite hides it from the browser; it does not protect it.
Mitigated by a dedicated bucket holding one public-by-definition file, with
the policy scoped to that key rather than to the bucket.

CloudFront with OAC would avoid public access entirely and is three resources
instead of one; rejected on setup cost, not on risk.
```

---

## Task 8: The page

**Files:**
- Create: `web/` — Astro project
- Create: `web/src/pages/index.astro`, `web/src/lib/snapshot.ts`, `web/src/components/`
- Test: `web/test/snapshot.test.ts`

**Interfaces:**
- Consumes: `contracts/public-snapshot.json` (Task 6), fetched from `/data/status.json`.

- [ ] **Step 1: Scaffold**

```bash
cd web && npm create astro@latest . -- --template minimal --typescript strict --no-git --skip-houston
```

- [ ] **Step 2: Write the failing tests, including the three ugly states**

```typescript
// web/test/snapshot.test.ts
describe('reading a snapshot', () => {
  it('reads every key the contract promises', () => {
    const contract = JSON.parse(readFileSync('../contracts/public-snapshot.json', 'utf-8'));
    const parsed = parseSnapshot(sampleAlerting);
    expect(Object.keys(parsed)).toEqual(expect.arrayContaining(contract.top_level));
  });

  it('reports a quiet cycle as a level, not as an absence', () => {
    expect(parseSnapshot(sampleQuiet).alert).toBeNull();
    expect(parseSnapshot(sampleQuiet).levelLabel).toBe('sin riesgo');
  });

  it('reports how old the reading is', () => {
    // "Last evaluated: 3 days ago" is the most important thing on the page at
    // that moment, not an embarrassment to hide.
    expect(ageInHours(sampleQuiet, new Date('2026-09-24T14:00:00-05:00'))).toBeGreaterThan(48);
  });

  it('refuses a document from a future schema rather than rendering a blank', () => {
    expect(() => parseSnapshot({ ...sampleQuiet, schema_version: 99 })).toThrow();
  });
});
```

- [ ] **Step 3: Run and watch them fail**

Run: `cd web && npm test`
Expected: FAIL — `parseSnapshot` is not defined.

- [ ] **Step 4: Implement the parser, then the page**

`web/src/lib/snapshot.ts` parses and validates the document against the schema version. `index.astro` renders two views (current status, sent alerts) and handles the three states explicitly: **no alert**, **stale data with its age in the foreground**, and **failed fetch saying so rather than going blank**.

Interface copy is in **Spanish** — the same exception `CLAUDE.md` makes for the alert message.

- [ ] **Step 5: Run tests, build, and commit**

Run: `cd web && npm test && npm run build`

```bash
git add web
git commit -m "feat(web): the public status page

Astro, static, fetching the snapshot at runtime rather than at build — a
build-time fetch would make every cycle require a redeploy.

The three states that carry weight are tested explicitly: no alert (the
ordinary case), stale data with its age in the foreground, and a failed fetch
that says so. A page that fails quietly and shows \"no risk\" is worse than one
that does not load.
```

---

## Task 9: Deploy, and make the data same-origin

- [ ] **Step 1: Connect the Amplify app**

In the Amplify console: connect the GitHub repository, select the branch, set the app root to `web/`, accept the detected Astro build settings. Record every click in `docs/runbooks/public-page-deploy.md` — this step is console work and the runbook must say so rather than imply automation.

- [ ] **Step 2: Add the rewrite that removes CORS**

In Amplify → Rewrites and redirects:

```json
[{ "source": "/data/<*>", "target": "https://<bucket>.s3.<region>.amazonaws.com/<*>", "status": "200" }]
```

Without this the page and the data are different origins, and a missing CORS header fails **only in a browser** — never in a local test.

- [ ] **Step 3: Publish a real snapshot and verify end to end**

```bash
RAIN_ALERT_SNAPSHOT_BUCKET=<bucket> AWS_PROFILE=ferrenafe AWS_REGION=us-east-2 uv run rain-alert-cycle
curl -s "https://<amplify-domain>/data/status.json" | head -20
```
Expected: the JSON the cycle just wrote, served from the Amplify domain. If this returns HTML, the rewrite is wrong.

- [ ] **Step 4: Open the page and check the three states**

Load the page. Then verify, by hand: a quiet snapshot renders "sin riesgo" with a date; an old `evaluated_at` shows its age prominently; and with the rewrite temporarily pointed at a missing key, the page says it could not load rather than going blank.

- [ ] **Step 5: Capture evidence and commit**

Write `docs/evidence/YYYY-MM-DD-public-page-live.md` with the URL, the commands, and their real output, account id masked as `<account>`.

```bash
git add docs/runbooks/public-page-deploy.md docs/evidence
git commit -m "docs: record the public page going live
```

---

## Self-review notes

**Spec coverage.** Every section of the design maps to a task: architecture →
3 and 5; snapshot contract → 2 and 6; the Spanish promotion → 1; the site →
8; deployment and permissions → 7 and 9; testing and failure → folded into
each task rather than deferred to one at the end.

**Two things the spec left open, and where they land.**
`recent_alerts` is read in Task 5 — `AlertRepository` exposes
`alerts_with_window_start_between`, a window query rather than "the last N",
so the entry point passes a window (the dedup lookback) and takes the tail.
Who runs the cycle while judges look is **not** solved by this plan: the page
shows the last state published by hand until change 3 deploys the schedule,
and the page is honest about the age. That is a known limitation, not an
oversight.
