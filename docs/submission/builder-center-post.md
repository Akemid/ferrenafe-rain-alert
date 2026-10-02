# Ferreñafe Rain Alert — an early-warning system that refuses to let a model decide

**Live:** https://main.d2v67g9rhqb893.amplifyapp.com/
**Category:** #social-good (Climate resilience) · **Lane:** #community
**Repo:** https://github.com/Akemid/ferrenafe-rain-alert

---

## The problem

Ferreñafe is a town on the north coast of Peru, in Lambayeque. During the coastal El Niño of 2017 it was among the worst-hit districts in the region: the Taymi and Loco rivers broke their banks on the night of 13 March, and across Lambayeque that season left **41,237 people displaced, 93,486 affected, and 4,483 homes collapsed**.

The national weather service, SENAMHI, publishes official warnings — but they cover the whole department, highlands included, and a resident of Ferreñafe reading "orange warning for the coast and sierra" has no way to know whether that means them.

The information exists. It is just not addressed to anyone.

This is a system that reads the official warning and a forecast for the town's own coordinates, decides a risk level, and says — in Spanish, in a civil-protection register — what that means for Ferreñafe specifically.

## What is live

A page anyone can open, showing the current risk level, when it was last evaluated, which sources were available, and the alerts that have gone out.

As I write this it is showing a real orange SENAMHI warning: **riesgo inminente**, in red, with the official advisory quoted and the window in Lima time.

The page is deliberately honest about what it does not know. If the data is stale, the age goes in the heading, not a footnote. If a source was unavailable, it says which one. If the cycle could not load at all, it says so instead of showing "sin riesgo" — because on a page like this, a quiet failure that looks like good news is the worst possible failure.

## The one architectural decision everything else follows from

**The language model never decides whether to alert.**

An early version had the deterministic rules exposed as a tool the agent could call. I rejected it: that inverts control. The agent would still decide when to call the tool, whether to trust the result, and whether to send. The rules would live in code and the flow would be governed by a model.

So: code governs the flow, and the agent is one node inside it.

```
EventBridge (every 6h)
  → Lambda
      → SENAMHI scrape + Open-Meteo forecast     (two independent signals)
      → deterministic risk rules                  (pure domain, no I/O)
      → dedup: already alerted this window?       (ends here at zero cost)
      → COMPOSE  ← the agent, only when a new alert exists
      → validate, then deliver
```

The agent — Strands on **Amazon Bedrock AgentCore Runtime**, Claude Haiku 4.5 — gets a bounded job: turn structured data into a message a frightened person can act on. It never gates the alert.

And its output is not trusted. Thirteen validation rules stand between the model and a resident, the sharpest being: **every number in the message must trace back to a value in the request.** If the model writes a millimetre figure nobody gave it, the message is rejected and the deterministic template is sent instead. Fail toward the template, never toward something unverified.

I built that validator, then attacked it. Eleven attribution attacks. The one that got through is the finding I am proudest of catching:

> A Cyrillic **А** inside "SENAMHI" is invisible to a reader and invisible to an ASCII regex. The rule never fired, so a forged evacuation order passed as compliant.

## Stack

| | |
|---|---|
| Cycle | Python 3.12, hexagonal architecture, Lambda ARM64 |
| Agent | Strands on Bedrock AgentCore Runtime, Claude Haiku 4.5 |
| Schedule | EventBridge Scheduler, 6-hourly, America/Lima |
| State | DynamoDB (dedup + audit), SSM Parameter Store (config) |
| Page | Astro, S3, Amplify Hosting with a `/data/*` rewrite |
| Infra | CDK TypeScript |

Running cost: **cents a month.** The largest line item is three CloudWatch alarms at $0.10 each — which is also the only line buying anything.

---

## How the coding agent actually helped me ship

This is the part I want to be precise about, because "AI helped me build it" is not a claim, it is a vibe.

I used Claude Code throughout, with the **AWS Agent Toolkit** connecting it to my account. Every deployment step is recorded in `docs/evidence/` with the real commands and their real output, account id masked.

What mattered was not speed. It was that I could run a process I could not have run alone: **every change implemented by one agent, then reviewed by a different one with no memory of writing it, then re-reviewed after the fix.** Specs, design, tasks, apply, verify — each in a fresh context, each handing the next an artifact instead of a conversation.

That process caught things I would have shipped.

**A test that could not fail.** The output model accepts three alert levels; the suite only ever tried one. Deleting either of the others changed nothing — green suite, and the town silently stops getting PREPARE alerts.

**`NaN` losing every comparison.** The staleness guard was `age > STALE_AFTER_HOURS`. An unparseable timestamp makes `age` be `NaN`, and `NaN` is not greater than anything — so a corrupt reading rendered as *current*, indefinitely. Not an error a resident would distrust. A confident, normal-looking status sitting on a value the page could not read.

**Two tests that checked both ends of a coupling whose middle was missing.** One asserted the CSS class was in the stylesheet. One asserted it was on the element. Both passed, both were mutation-proved, and the deployed page rendered an imminent flood warning in plain black text — because Astro scopes styles by an attribute that elements built with `createElement` never receive. Only opening the real page in a browser and reading `getComputedStyle` found it.

**An IAM grant that would have killed the agent silently.** `InvokeAgentRuntime` authorizes against both the runtime *and* the endpoint; the grant named only the runtime. Every agent-composed cycle would have hit `AccessDenied`, fallen back to the template, and **reported success** — no error metric, no alarm. The agent would have been off for months with nothing to say so.

**And a detection I built and then deleted.** A security review found one unmitigated risk: a single forged row in the dedup table suppresses alerts for 72 hours with every alarm green. I built the CloudWatch filter, verified the syntax, mutation-tested it — and a review showed it was structurally indistinguishable from a healthy multi-cycle rain event. An alarm that fires on ordinary rain gets muted, and then reads as coverage. Deleting it was the right call, and the finding now lives on the port where its severity will change.

## The thing I only found by deploying it

The scheduled cycle went to AWS and came back **degraded**, three invocations out of three: SENAMHI unavailable, Open-Meteo fine. The same scrape from my laptop succeeded in 0.42 seconds.

The logs could not say why. The domain had captured the reason — `Unavailable` carries both a `reason` and a `detail`, because the HTTP adapter translates every transport fault into one precisely so the cause survives the seam — and the operator notice threw both away. **The system knew the answer and told nobody.** Same defect as the published-and-never-rendered field, one layer down.

So I fixed the notice first, and the fix was the instrument:

```
- senamhi: timeout — ConnectTimeout: [Errno 110] Connection timed out
```

Then three steps, two of my hypotheses wrong:

1. *"SENAMHI blocks AWS IPs"* — reasonable, and I could not show it.
2. *"The 3-second connect timeout is too tight"* — I raised it to 20. Still failed, five out of five, each run dying at ~16 seconds. That is the Linux kernel giving up on unanswered SYN retries, not our timeout. **Raising it cannot help.**
3. A throwaway probe in **São Paulo** — 2,500 km closer to Peru — to separate distance from blocking:

```
sa-east-1   dns 190.119.131.3   tcp ERR after 15.47s  [Errno 110]
us-east-2   dns 190.119.131.3   tcp ERR after 15.38s  [Errno 110]
```

Identical. DNS resolves perfectly from both; the SYN is answered from neither. It is not latency and not routing — SENAMHI does not accept connections from AWS.

That negative result cost twenty minutes and saved building a whole São Paulo stack to land in the same place. It also killed the NAT Gateway idea before I spent $32 a month on it: if two AWS regions on different continents get the same silence, an Elastic IP is still an AWS address.

The honest summary is uncomfortable and worth saying plainly: **this system was designed and tested from the one place on earth where its data source answers.** My laptop is in Lambayeque, twenty milliseconds from that server. The cloud is not. Nothing in a test suite could have told me that, and nothing did until the first real invocation.

The thread through all of those is one sentence, which is now the first rule in this repository's contributor guide:

> **Several defects survived review precisely because a check looked like it had run and had not.**

The same pattern arrived from a direction I had not seen coming, too. A Lambda concurrency reservation — one line, guarding against two cycles both alerting under at-least-once delivery — failed to deploy because this account's limit is 10 against AWS's default of 1000. Service Quotas reports the quota as `Adjustable: true`, and its adjustment API refuses any value below the default. Not a check that did not run: **a capability that reports itself as present and is not.** The tempting fix at that moment is to delete the line that caused the wall.

A findings log in `docs/blog/` records each one as it happened, with the command that revealed it. Fourteen entries. The uncomfortable ones are the best ones.

## What it does not do yet, stated plainly

**Delivery.** There is no notifier in this codebase capable of sending a message to a human — only one that prints to a console. That is deliberate and structural, not a flag someone can forget: real recipients are personal data, consent matters, and the WhatsApp Groups API turns out not to fit a neighbourhood group at all (business verification, the business must create the group, 8-participant cap — all verified against Meta's documentation, not assumed).

So today the public page *is* the channel.

**And the schedule is deployed but switched off.** The Lambda, the table, the alarms and the six-hourly rule all exist in the account and were verified by hand; `scheduleEnabled` is `false` on purpose. Turning it on today would overwrite a correct page with a degraded one every six hours, because of the connectivity finding above — the cycle would faithfully publish "could not evaluate the risk" while a real orange warning is active.

That is the system behaving correctly and the result being worse, which is the kind of trade-off worth naming rather than hiding. Until SENAMHI is reachable from the cloud, the page is refreshed by running the same cycle from a laptop that can reach it — one command, the same code path, the same validation.

The next change is a fetch route that does not depend on where the machine is. Then the schedule gets turned on, and then — separately, and last — something earns the right to reach a phone.

## Why this lane

I live in Lambayeque. Ferreñafe is not a case study for me.

The repository is Apache 2.0 and the architecture is deliberately boring in the places that matter — hexagonal, with the risk rules as pure functions and every external service behind a port. Another coastal town can swap the coordinates, the thresholds and the scraper and have the same system. That is the actual deliverable: not one town's page, but a shape that fits the next town.

---

*Built with Claude Code and the AWS Agent Toolkit. Deployment evidence, architecture decisions and the full findings log are in the repository.*

#social-good #community
