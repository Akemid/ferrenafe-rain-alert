# Public status page — design

- **Date**: 2026-09-21
- **Status**: approved in conversation, pending written review
- **Driver**: the AWS Zero to Shipped ship gate needs a publicly reachable URL
  by 2026-10-02, judged 2026-10-05 to 10-12. But the page is built as product
  for Ferreñafe, not as a demo: a resident opening it should get an answer to
  "do I need to worry today?"

## What it is

A read-only web page with two views:

1. **Current status** — what the last cycle found, whether or not it alerted.
2. **Sent alerts** — the alerts that actually went out.

Explicitly **not** in this version: subscribing from the page. That turns a
page into an application — a write path from the public internet into a
life-safety system, contact storage, consent, abuse protection — and it is
recorded as a wanted evolution rather than dropped. Until it exists, the page
says plainly how to be added to the list by contacting the operator, rather
than implying a self-service flow that is not there.

## Architecture

```
RunAlertCycle.execute()  →  CycleResult
                                 │
                    (at the entry point, not in the use case)
                                 │
                         SnapshotPublisher            ← new port
                            ├── JsonFileSnapshotPublisher   (local, dev, tests)
                            └── S3SnapshotPublisher         (production)
                                 │
                         status.json in S3, public read on one key
                                 │
                    Amplify reverse-proxy rewrite  /data/<*>
                                 │
                            fetch() from the Astro page
```

### The publisher is called from the entry point

`CycleResult`'s own docstring already says it carries *"everything the CLI
(and change 3's Lambda entry point) needs to report on one cycle, **whether or
not a send was authorized**"*. It is returned by `execute()` and the entry
point already holds it.

So publishing needs **no change to the application layer and none to the
domain**. It is a side effect of reporting, like printing the CLI summary.
Adding a dependency to `CycleDependencies` and publishing inside the use case
would have worked and would have been worse: the use case does not need to
know that a web page exists.

Change 3 inherits this for free — its Lambda calls the same publisher with the
same result.

### Two adapters, matching the existing pattern

`JsonFileSnapshotPublisher` writes to disk for development and to keep the
default suite offline; `S3SnapshotPublisher` uploads. The same shape as
`json_alert_repository.py` alongside `in_memory_alert_repository.py`.

### Published on every run

Alerting or not. That is the point: the page can say "no risk today" with a
timestamp, which is the answer a resident is usually looking for.

## The snapshot contract

One file, `status.json`:

```jsonc
{
  "schema_version": 1,
  "city": "Ferreñafe",
  "evaluated_at": "2026-09-21T14:00:00-05:00",   // local
  "level": "prepare",                             // enum, drives styling
  "level_label": "prepárate",                     // Spanish, from the domain
  "window": { "start": "…", "end": "…" },
  "reasons": [                                    // already Spanish
    "Aviso oficial del SENAMHI, nivel naranja: PRECIPITACIONES…",
    "Se pronostican 18.0 mm de lluvia acumulada en 24 horas…"
  ],
  "sources": { "senamhi": "available", "open_meteo": "unavailable" },
  "degraded": false,
  "alert": null,          // or { title, body, valid_until, composed_by, sent }
  "recent_alerts": [ /* … */ ]
}
```

`composed_by` is `agent` or `template`, and it is published deliberately: the
page can say honestly whether the model wrote that text or the deterministic
fallback did. A system that puts an LLM in front of a community should be able
to say when it did.

`recipients_count` is **excluded**. It is operational — how many people are
subscribed — and no resident needs it.

**One file, not two.** Status changes every cycle and history only when
something is sent, so they could be split. Alerts are rare by design (that is
what the dedup logic is for), so the history stays short and one `fetch` is
simpler than two. Split it if it grows.

### The one change to existing code

Spanish currently lives only in private functions of `domain/template.py`:
`_LEVEL_LABELS_ES`, `_reason_line_es`. What is public (`render_reason_en`) is
English, for the operator.

So either the snapshot carries rendered text, or the page translates levels
and reasons **in JavaScript** — duplicating domain logic in a second language
that will drift on the first change. The domain is what decides what this
system says in Spanish.

The fix is a rename: promote the level-label map and the reason renderer to
public names, behaviour unchanged. Invariant V0 already covers the template's
output, and a test pins that the promoted functions produce identical strings.

## The site

Lives in `web/` at the repository root, beside `src/` (Python) and `agent/`.
Astro, static build, deployed by Amplify Hosting from git.

**The fetch happens at runtime, not at build time.** Fetching at build would
make every cycle require a redeploy, which is exactly what the architecture
separates. The page is built once; the data is fetched per visit.

**The interface is in Spanish.** Same exception `CLAUDE.md` already makes for
the alert message: artifacts are English because developers read them, and
what a resident reads is Spanish because a resident reads it.

### The three states that carry weight

The happy path is not the hard part.

- **Fetch fails** — the page must not go blank and must not show stale data as
  if it were current. It says it could not load. A page that fails quietly and
  shows "no risk" is worse than one that does not load.
- **Data is old** — the age goes in the foreground. "Last evaluated: 3 days
  ago" is the most important thing on the page at that moment, not an
  embarrassment to hide. With the cycle run by hand until change 3 exists,
  this will happen.
- **No alert** — the ordinary case, and the one a resident most often needs.

## Deployment

**Bucket.** One dedicated bucket holding only the snapshot.

Amplify's reverse proxy fetches server-side and **does not sign the request**,
so the object must be publicly readable. The rewrite hides it from the browser;
it does not protect it. That is acceptable *for this object* — its contents are
exactly what the page displays — but it means relaxing Block Public Access on
that bucket, which is a thing to be uneasy about. Mitigated by keeping the
bucket dedicated and the policy scoped to `s3:GetObject` on **one key**.

The alternative that avoids public access entirely is CloudFront with OAC in
front of a private bucket, with Amplify rewriting to the distribution. That is
AWS's recommended shape and three resources instead of one. Rejected for this
version on setup cost, not on risk.

**The rewrite** removes CORS entirely by making the data same-origin:

```json
[{ "source": "/data/<*>", "target": "https://<bucket>.s3.../<*>", "status": "200" }]
```

Without it the page and the data are different origins, and a missing CORS
header fails **only in a browser** — never in a local test.

**Who writes.** Today the CLI under the `ferrenafe` profile; later the change-3
Lambda's role. `s3:PutObject` scoped to the one key, not the bucket.

**Infrastructure as code.** A small own CDK stack in `infra/` for the bucket
and its policy. `agentcore/cdk/` cannot host it — the AgentCore CLI regenerates
that and would overwrite it. Connecting the Amplify app to the repository is
console work and is documented in a runbook rather than pretended to be
automated.

## Testing and failure

**Publishing must never break the cycle.** If S3 is unreachable the alert still
goes out. Publishing is reporting, not notifying. Same posture as the operator
notice guard in `agent_composer`, for the same reason: a failure in the
operator's channel must not cost the community its alert.

**The seam that breaks silently** is the contract between the Python publisher
and the TypeScript page. Nothing compiles them together; rename a field and the
page shows a gap while both suites stay green.

This project already chose a pattern for exactly that —
`contracts/agent-composition.json`, a golden document asserted from both sides.
The same applies here: **`contracts/public-snapshot.json`**, asserted by a
Python test (the publisher produces these keys) and by a web test (the page
consumes them).

| What | How |
|---|---|
| The Spanish rename | V0 covers the template; a test pins identical strings from the promoted functions |
| Both adapters | `JsonFile` against a real file; `S3` with an injected client, like `agentcore_invoker` |
| The offline guarantee | Default suite stays credential-free; the S3 adapter builds its client lazily |
| The page | Against sample snapshots, **including the ugly ones**: no alert, stale data, failed fetch |

That last row matters most. The three hard states are the ones nobody tests,
because they are not the screenshot.

## Open, recorded rather than resolved

- **Who runs the cycle while judges look.** Until change 3 deploys the Lambda
  and EventBridge schedule, the cycle is a manual CLI and the page shows the
  last state published by hand. The page is honest about the age; that does not
  make a six-day-old page good. Change 3 remains the real answer.
- **Where the `recent_alerts` history is read from.** `AlertRepository` has
  `alerts_with_window_start_between`, which is a window query rather than "the
  last N". Resolve at implementation.
