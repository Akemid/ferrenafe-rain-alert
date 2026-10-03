# Ferreñafe Rain Alert — an early-warning system that refuses to let a model decide

**Live:** https://main.d2v67g9rhqb893.amplifyapp.com/
**Category:** #social-good (Climate resilience) · **Lane:** #community
**Repo:** https://github.com/Akemid/ferrenafe-rain-alert

---

## 1. The problem

Ferreñafe is a town on the north coast of Peru, in Lambayeque. During the coastal El Niño of 2017 the whole province lived under a declared state of emergency — D.S. 011-2017-PCM put **all six districts of Ferreñafe** under it from 4 February to 4 April, alongside Chiclayo and Lambayeque. On **the night of Monday 13 March**, the Taymi and the Loco overflowed, cutting the road that links the districts of Manuel Antonio Mesones Muro and Pítipo with the city of Ferreñafe, and flooding rice and sugarcane at the Los Faiques sector. Nationally, by 23 March the figure stood at **111,283 people displaced**.

The national weather service, SENAMHI, publishes official warnings — but they cover the whole department, highlands included, and a resident of Ferreñafe reading "orange warning for the coast and sierra" has no way to know whether that means them.

The information exists. It is just not addressed to anyone.

## 2. The solution

A system that reads the official warning and a forecast for the town's own coordinates, decides a risk level, and says — in Spanish, in a civil-protection register — what that means for Ferreñafe specifically.

One architectural decision governs everything else: **the language model never decides whether to alert.**

An early version had the deterministic rules exposed as a tool the agent could call. I rejected it, because that inverts control. The agent would still decide when to call the tool, whether to trust the result, and whether to send. The rules would live in code and the flow would be governed by a model.

So: code governs the flow, and the agent is one node inside it.

```
EventBridge Scheduler (6-hourly, America/Lima)
  → Lambda
      → SENAMHI (via S3 relay) + Open-Meteo      (two independent signals)
      → deterministic risk rules                  (pure domain, no I/O)
      → dedup: already alerted this window?       (ends here at zero cost)
      → COMPOSE  ← the agent, only when a new alert exists
      → validate, then publish
```

The agent — Strands on **Amazon Bedrock AgentCore Runtime**, Claude Haiku 4.5 — gets a bounded job: turn structured data into a message a frightened person can act on. It never gates the alert.

And its output is not trusted. **Thirteen validation rules** stand between the model and a resident, the sharpest being: *every number in the message must trace back to a value in the request.* If the model writes a millimetre figure nobody gave it, the message is rejected and the deterministic template is sent instead. Fail toward the template, never toward something unverified.

I built that validator, then attacked it. The SENAMHI attribution rule alone carries sixteen tests across three classes, and the attack I am proudest of catching is this one:

> A Cyrillic **А** inside "SENAMHI" is invisible to a reader and invisible to an ASCII regex. The rule never fired, so a forged evacuation order passed as compliant.

### Stack

| | |
|---|---|
| Cycle | Python 3.12, hexagonal architecture, Lambda ARM64 |
| SENAMHI relay | Producer outside AWS (macOS LaunchAgent, hourly) → private, versioned S3 object; write-only CloudTrail audit |
| Agent | Strands on Bedrock AgentCore Runtime, Claude Haiku 4.5 |
| Schedule | EventBridge Scheduler, 6-hourly, America/Lima |
| State | DynamoDB (dedup + audit), SSM Parameter Store (config) |
| Page | Astro, S3, Amplify Hosting with a `/data/*` rewrite |
| Infra | CDK TypeScript |

Running cost: **cents a month.** The largest line item is four CloudWatch alarms at $0.10 each — which is also the only line buying anything. The relay adds S3 storage for 90 days of object versions (about $0.03 a month) and CloudTrail data events for the producer's writes (one per hourly push, about 24 a day, at $0.10 per 100,000).

## 3. What went wrong on the cloud side

I used Claude Code throughout, with the **AWS Agent Toolkit** connecting it to my account, and ran a process I could not have run alone: **every change implemented by one agent, then reviewed by a different one with no memory of writing it.** Every deployment step is recorded in `docs/evidence/` with the real commands and their real output, account id masked.

These are the findings that mattered, and none of them came from a test suite.

**An IAM grant that would have killed the agent silently.** AgentCore calls it *hierarchical authorization*: `InvokeAgentRuntime` evaluates the identity-based policy against **both** the agent runtime and the agent endpoint being invoked, and denies unless both allow. My grant named only the runtime. Every agent-composed cycle would have hit `AccessDenied`, fallen back to the deterministic template, and **reported success** — no error metric, no alarm. The agent would have been off for months with nothing to say so.

**A detection I built and then deleted.** A security review found one unmitigated risk: a single forged row in the dedup table suppresses alerts for 72 hours with every alarm green. I built the CloudWatch metric filter, verified the syntax, mutation-tested it — and a second review showed the signal was structurally indistinguishable from a healthy multi-cycle rain event. An alarm that fires on ordinary rain gets muted, and a muted alarm then reads as coverage. Deleting it was the right call; the finding now lives on the port where its severity will change.

**A quota that reports itself as adjustable and is not.** A Lambda concurrency reservation — one line, guarding against two cycles both alerting under at-least-once delivery — failed to deploy. The rule is in the docs and it is arithmetic: you can reserve *up to the unreserved account concurrency minus 100*, because 100 units are held back for functions that reserve nothing. This account's limit is **10**, against AWS's default of 1,000. Ten minus one hundred is negative, so on this account **no reservation of any size is expressible** — not a tuning problem, a floor I was below.

Service Quotas reports that quota as `Adjustable: true`, and the only operation it offers is `RequestServiceQuotaIncrease` — an *increase*, which 10 → 100 is, and which the API still would not take. The tempting fix at that moment is to delete the line that caused the wall. I left the line out, wrote the reason in its place, and filed the support case.

**A retained table that blocked its own redeploy.** `RemovalPolicy.RETAIN` on the DynamoDB table meant a failed stack rollback left it behind as `DELETE_SKIPPED`, and the next deploy could not recreate a table that already existed. The security review had predicted exactly this before the first deploy. Reading a prediction is not the same as acting on it.

The thread through all of them is one sentence, now the first rule in this repository's contributor guide:

> **Several defects survived review precisely because a check looked like it had run and had not.**

A findings log in `docs/blog/` records each one as it happened, with the command that revealed it. Seventeen entries.

## 4. The one I only found by deploying it

The scheduled cycle went to AWS and came back **degraded**, three invocations out of three: SENAMHI unavailable, Open-Meteo fine. The same scrape from my laptop succeeded in 0.42 seconds.

The logs could not say why. The domain had captured the reason — `Unavailable` carries both a `reason` and a `detail`, because the HTTP adapter translates every transport fault into one precisely so the cause survives the seam — and the operator notice threw both away. **The system knew the answer and told nobody.**

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

Identical. DNS resolves perfectly from both; the SYN is answered from neither. It is not latency and not routing — **SENAMHI does not accept connections from AWS.**

That negative result cost twenty minutes and saved building a whole São Paulo stack to land in the same place. It also killed the NAT Gateway idea before I spent $32 a month on it: if two AWS regions on different continents get the same silence, an Elastic IP is still an AWS address.

The honest summary is uncomfortable and worth saying plainly: **this system was designed and tested from the one place on earth where its data source answers.** My laptop is in Lambayeque, twenty milliseconds from that server. The cloud is not. Nothing in a test suite could have told me that, and nothing did until the first real invocation.

### Fixing it: the API was behind the same wall

The obvious way out was SENAMHI's official OGC/GeoServer API, a different host with a documented interface. Before writing an adapter for it, I probed both hosts from throwaway Lambdas in `us-east-2` and `sa-east-1`. I tested a raw TCP connect first, with Open-Meteo as a control:

```
                         us-east-2       sa-east-1       outside AWS
www.senamhi.gob.pe       TCP timeout     TCP timeout     200 in 1.6 s
idesep.senamhi.gob.pe    TCP timeout     TCP timeout     200 in 1.9 s
api.open-meteo.com       200 in 615 ms   200 in 547 ms   —
```

Both hosts sit in the same `/24`. Switching to the API would have cost a day of adapter work and timed out exactly the same way. I also rejected a residential proxy: it would route a public-safety system's official source through strangers' home connections, around a block that may be deliberate.

What shipped is a **relay**. A producer outside AWS (today, a macOS LaunchAgent on my machine) fetches the page every hour. It parses the page locally as a gate and uploads the validated text to a private, versioned S3 object. The cycle reads that object instead of SENAMHI. The design choices that matter are about how it fails:

- **The schedule stays the heartbeat.** I rejected an S3-event trigger. If the producer died, nothing would run at all, the forecast-only path included, and nothing would report the silence.
- **Staleness is an outage, not data.** An object 3 hours old or older becomes `STALE_RELAY`. Missing and unreadable objects get their own reasons. Two consecutive degraded cycles email the operator, and the alarm treats missing data as breaching, so a cycle that stops running cannot read as healthy.
- **A forged calm page is worse than no page.** A calm page parses as "no warnings", which makes the system *less* sensitive than an outage would. I accepted that risk deliberately and bounded it:
  - the producer's key can write exactly one object, and nothing else;
  - every version is kept for 90 days;
  - a write-only CloudTrail trail records who wrote each version, and from where.
- **The switch lives in the repository.** `relayEnabled` (and now `scheduleEnabled`) are reviewed one-line commits in `cdk.json`, not CLI flags someone can forget.

Turning it on surfaced four more findings that no test had caught:

- **botocore ignores `AWS_REGION`.** It resolves the region from `AWS_DEFAULT_REGION` and the profile only. The producer's LaunchAgent set only `AWS_REGION`. A code review had cited a line of botocore as proof that the variable worked; that line belongs to a different mechanism. I reproduced the behaviour offline, and a test now pins it.
- **`launchd` drops interval runs that fall during sleep.** `man launchd.plist` says so for `StartInterval`, while `StartCalendarInterval` runs are coalesced on wake. The producer schedules by calendar for that reason.
- **The first deploy shipped yesterday's code.** CDK packages a prebuilt directory, and `cdk deploy` does not rebuild it. The new environment variable arrived; the code that reads it did not. What gave it away was a log record that should have been there and was not.
- **The deploy that would have re-enabled the schedule by accident.** The schedule's off-switch existed only as a CLI flag. The diff showed `DISABLED → ENABLED` before anything was applied. It now lives in `cdk.json` too, and `cdk diff` against the live account reports *no differences*.

## 5. The result

A page anyone can open, showing the current risk level, when it was last evaluated, which sources were available, and the alerts that have gone out.

The page is deliberately honest about what it does not know. If the data is stale, the age goes in the heading, not a footnote. If a source was unavailable, it says which one. If the cycle could not load at all, it says so instead of showing "sin riesgo" — because on a page like this, a quiet failure that looks like good news is the worst possible failure.

What is deployed and running:

- the Lambda, on its 6-hourly schedule, now **enabled**;
- the DynamoDB table and the SSM parameters;
- four CloudWatch alarms;
- the public S3 bucket and the Amplify site;
- the AgentCore runtime;
- the SENAMHI relay, with its private bucket and its audit trail.

All of it was verified by hand, with the commands in `docs/evidence/`.

The first cycle that read SENAMHI through the relay found a real warning. SENAMHI warning **No. 392**, orange, *"Precipitaciones en la sierra norte y costa norte"*, valid 2–4 October 2026. I checked it against SENAMHI's own page at the same moment. The cycle computed **imminent risk** for Ferreñafe, discarded a concurrent heat warning as unable to cause flooding, and published that to the page. The agent's draft of the message was rejected by the validator, because it omitted the town's name and introduced a number that traced to nothing in the request. The deterministic template went out instead. The rule worked on the first real alert it saw.

One thing is still deliberately switched off, and saying so is part of the deliverable:

**Delivery to phones.** There is no notifier in this codebase capable of sending a message to a human — only one that prints to a console. That is structural, not a flag someone can forget: real recipients are personal data, and consent matters.

The obvious channel also does not fit, and I read the documentation rather than assume. Meta's Groups API requires an **Official Business Account**; groups are *"an invite-only experience where participants join using a group invite link you send them"*, so no endpoint adds a neighbour to a group; and the documented ceiling is *"Max group participants: 8"*. Ferreñafe has tens of thousands of residents. Eight per group, each one having to accept a link from a business account, is not a town's warning channel — it is a feature for a work crew.

So today the public page *is* the channel. That is why the first real orange warning reached nobody's phone, and I want that sentence to stay uncomfortable.

What comes next, in order:

1. **An always-on producer** that does not depend on my laptop being awake. It should authenticate with IAM Roles Anywhere instead of a long-lived key, and it has to exist before the rainy season starts in December.
2. **Ask SENAMHI for access** from a fixed address. That is the clean fix, and a relay is the honest workaround until then.
3. Separately, and last, a delivery channel that earns the right to reach a phone.

## Evidence: an agent connected to the AWS console

<!-- OWNER: insert your screenshots here. One image per block; replace the path and the caption. -->

![Screenshot 1 — <caption>](<path-to-image-1>)

*<What this screenshot shows.>*

![Screenshot 2 — <caption>](<path-to-image-2>)

*<What this screenshot shows.>*

![Screenshot 3 — <caption>](<path-to-image-3>)

*<What this screenshot shows.>*

<!-- END OWNER SECTION -->

## Why this lane

I live in Lambayeque. Ferreñafe is not a case study for me.

The repository is Apache 2.0 and the architecture is deliberately boring in the places that matter — hexagonal, with the risk rules as pure functions and every external service behind a port. Another coastal town can swap the coordinates, the thresholds and the scraper and have the same system. That is the actual deliverable: not one town's page, but a shape that fits the next town.

---

## References

**Ferreñafe and the 2017 coastal El Niño**

- [INDECI — Compendio Estadístico 2017, Gestión Reactiva](https://portal.indeci.gob.pe/wp-content/uploads/2019/01/201802271715091.pdf) (PDF, p. 57) — the table of declared states of emergency. D.S. 011-2017-PCM, in force 4 Feb to 4 Apr 2017, listing 6 districts of Ferreñafe.
- [RPP Noticias — *Distritos inundados en Lambayeque por desborde de ríos tras lluvias*](https://rpp.pe/peru/lambayeque/distritos-inundados-en-lambayeque-por-desborde-de-rios-tras-lluvias-noticia-1036821) (14 March 2017) — the Taymi and Loco overflowing on the night of Monday the 13th, the road to Ferreñafe cut, the Los Faiques crops.
- [OPS/PAHO — *Emergencia por impacto del Fenómeno El Niño Costero en Perú en 2017*](https://www.paho.org/sites/default/files/noticias-web-peru-2017-emergencia-nino-costero.pdf) (PDF, p. 29) — the national SINPAD figure of 111,283 people displaced as of 23 March 2017.
- [SENAMHI — official warnings](https://www.senamhi.gob.pe/) — the warning feed this system reads.
- [Open-Meteo — forecast API](https://open-meteo.com/) — the second, independent signal.

**AWS documentation consulted**

- [Resource-based policies for Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/resource-based-policies.html) — *hierarchical authorization*: `InvokeAgentRuntime` is evaluated against both the agent runtime and the agent endpoint. This is the page that named the IAM finding.
- [InvokeAgentRuntime — API reference](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_InvokeAgentRuntime.html) — the permission the cycle needs, and the `InvokeAgentRuntimeForUser` pairing.
- [Configuring reserved concurrency for a function](https://docs.aws.amazon.com/lambda/latest/dg/configuration-concurrency.html) — "you can reserve up to the unreserved account concurrency value minus 100". The arithmetic behind the failed deploy.
- [Understanding Lambda function scaling](https://docs.aws.amazon.com/lambda/latest/dg/lambda-concurrency.html) — reserved vs. unreserved concurrency, and the 1,000-unit regional default.
- [Service Quotas — `RequestServiceQuotaIncrease`](https://docs.aws.amazon.com/AWSJavaScriptSDK/latest/AWS/ServiceQuotas.html) — where I confirmed the API offers an *increase* operation and nothing else.
- [`aws-cdk-lib.aws_bedrockagentcore`](https://docs.aws.amazon.com/cdk/api/v2/docs/aws-cdk-lib.aws_bedrockagentcore-readme.html) — the Runtime construct's `grantInvoke` helpers, which encode the two-resource grant correctly.
- [Amazon S3 bucket policy for CloudTrail](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/create-s3-bucket-policy-for-cloudtrail.html) — CloudTrail's delivery calls carry the source condition keys, which made the log bucket's `aws:SourceAccount` deny safe. Confirmed live: logs and digests delivered with no error.
- [How do I securely manage IAM access keys?](https://repost.aws/knowledge-center/manage-iam-access-keys-securely) — "for workloads outside of AWS … use AWS IAM Roles Anywhere". This is why the always-on producer must not use a long-lived key.
- [Access Denied on the S3 static website endpoint](https://repost.aws/knowledge-center/s3-static-website-endpoint-error) — without `s3:ListBucket`, a missing object returns 403, not 404. That is why the cycle's role can list the relay bucket, so that "missing" and "unreadable" stay distinguishable.

**Relay producer**

- `man launchd.plist` (macOS) — `StartInterval`: "If the system is asleep … that interval will be missed"; `StartCalendarInterval`: "launchd will start the job the next time the computer wakes up".
- [SENAMHI — warnings for Lambayeque](https://www.senamhi.gob.pe/?dp=lambayeque&p=aviso-meteorologico) — warning No. 392 (orange, 2026-09-30, valid 2026-10-02 to 2026-10-04), read on 2026-10-03.

**Delivery channel**

- [Meta — WhatsApp Groups API](https://developers.facebook.com/documentation/business-messaging/whatsapp/groups) — the Official Business Account requirement, the invite-link-only joining model, and the documented limits (`Max group participants: 8`, 10,000 groups per business number). This is the page that ruled the channel out.

---

*Built with Claude Code and the AWS Agent Toolkit. Deployment evidence, architecture decisions and the full findings log are in the repository.*

#social-good #community
