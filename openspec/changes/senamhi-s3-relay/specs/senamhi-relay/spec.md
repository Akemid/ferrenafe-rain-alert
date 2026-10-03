# SENAMHI Relay Specification

## Purpose

SENAMHI drops TCP from AWS. A producer outside AWS stores the warnings page text it validated, as unparsed HTML encoded UTF-8, in a private S3 object; the cycle reads it, checks freshness, and parses it with the existing parser. This spec covers the producer contract, the reader contract, credential scope, write audit and the operator alarm.

## Requirements

### Requirement: Producer uploads only after a successful local parse

The producer MUST fetch the page, parse it with `parse_warnings_page`, and upload only if the parse succeeds. The body MUST be the exact page text the parse gate validated, encoded as UTF-8, with `Content-Type: text/html; charset=utf-8`. It is unparsed HTML text, not parsed data, and not SENAMHI's original bytes. On fetch or parse failure the producer MUST NOT write, so the last good object ages out and is never replaced by bad content. The upload MUST record a content hash and the producer's claimed fetch time as informational metadata.

#### Scenario: Successful fetch and parse
- GIVEN SENAMHI answers with a page that parses
- WHEN the producer runs
- THEN exactly one `PutObject` is issued whose body is the validated text encoded UTF-8, with `Content-Type: text/html; charset=utf-8` and the metadata

#### Scenario: Fetch failure never overwrites
- GIVEN the SENAMHI fetch times out
- WHEN the producer runs
- THEN no `PutObject` is issued and the exit status is non-zero

#### Scenario: Unparseable page never overwrites
- GIVEN SENAMHI answers `200` with a page the parser rejects
- WHEN the producer runs
- THEN no `PutObject` is issued

### Requirement: Freshness is measured from S3 LastModified

The reader MUST treat `LastModified` (server clock) as authoritative. An object whose age is greater than or equal to 3 hours MUST yield `Unavailable(STALE_RELAY)`; age exactly 3 hours is stale. A future-dated `LastModified` MUST NOT be treated as fresh beyond `now`.

#### Scenario: Fresh object
- GIVEN an object 2 h 59 min old
- WHEN the relay is read
- THEN the object is accepted for parsing

#### Scenario: Boundary is stale
- GIVEN an object exactly 3 h old
- WHEN the relay is read
- THEN the result is `Unavailable(STALE_RELAY)`

### Requirement: Producer clock skew is flagged, not trusted

If the claimed producer time differs from `LastModified` by more than 15 minutes, the reader MUST flag the skew in the result notes and the cycle log, and MUST still use `LastModified` for freshness.

#### Scenario: Skew above threshold
- GIVEN a claimed fetch time 20 min before `LastModified` and a fresh object
- WHEN the relay is read
- THEN the result is `Available` and its notes report the skew
- AND freshness was computed from `LastModified`

### Requirement: Distinct unavailable reasons for unreadable relay

A missing key (`NoSuchKey`, HTTP 404) MUST yield `RELAY_MISSING`. Access denied, throttling, or any other S3 or client error MUST yield `RELAY_UNREADABLE`, never `RELAY_MISSING` and never `Available`. An object larger than the size cap, a charset other than UTF-8, a failed strict decode, or a missing or mismatched content hash MUST yield `RELAY_UNREADABLE`, and an oversized body MUST NOT be read. The reader MUST use bounded timeouts and attempts. None of these outcomes may trigger a direct scrape.

#### Scenario: Missing key
- GIVEN S3 answers `NoSuchKey`
- WHEN the relay is read
- THEN the result is `Unavailable(RELAY_MISSING)`

#### Scenario: Access denied
- GIVEN S3 answers `AccessDenied`
- WHEN the relay is read
- THEN the result is `Unavailable(RELAY_UNREADABLE)`

#### Scenario: Throttled or timed out
- GIVEN S3 answers `SlowDown`, or the connection times out
- WHEN the relay is read
- THEN the result is `Unavailable(RELAY_UNREADABLE)`

#### Scenario: Oversized object
- GIVEN `ContentLength` exceeds the cap
- WHEN the relay is read
- THEN the result is `Unavailable(RELAY_UNREADABLE)` and the body is not read

### Requirement: Unparseable relay content is handled like a direct parse failure

HTML that `parse_warnings_page` rejects MUST produce the same `Unavailable` reason and detail as the direct scraper would for the same HTML.

#### Scenario: Altered structure in the relay object
- GIVEN a fresh object whose HTML has an unrecognized table
- WHEN the relay is read
- THEN the result is `Unavailable(structure_unrecognized)`

### Requirement: Credentials are scoped to one key

The cycle role MUST have `GetObject` on exactly one object key and `ListBucket` on the relay bucket (so that a missing key is reported as `NoSuchKey` rather than `AccessDenied`), and no other action on the bucket. The producer policy MUST allow `PutObject` on that key only (no delete, no list). The stack MUST NOT create an access key resource. The bucket MUST block all public access, require SSL, and be versioned. No account id may appear in the bucket name or repository.

#### Scenario: Synthesized template
- GIVEN the synthesized stack
- WHEN IAM statements and bucket properties are inspected
- THEN the grants and bucket settings above hold

### Requirement: Writes to the relay bucket are audited

CloudTrail MUST record write-only data events for objects in the relay bucket, and only for that bucket, so each object version can be traced to the principal and source address that wrote it. The trail's log bucket MUST block all public access, require SSL, and be retained on stack deletion. This MUST be verified by a CDK assertion test on the synthesized template.

#### Scenario: Synthesized trail
- GIVEN the synthesized stack
- WHEN the CloudTrail trail is inspected
- THEN its event selectors log `Data` events for `AWS::S3::Object` resources under the relay bucket only, write-only, and log file validation is enabled

### Requirement: Operator alarm after two degraded cycles

A CloudWatch alarm MUST notify the operator through the existing `alarmEmail` topic after 2 consecutive cycles in which the relay was unavailable, counting `STALE_RELAY`, `RELAY_MISSING` and `RELAY_UNREADABLE` together in any mix.

#### Scenario: Mixed reasons
- GIVEN one cycle with `STALE_RELAY` then one with `RELAY_UNREADABLE`
- WHEN the alarm evaluates
- THEN it enters alarm state

#### Scenario: One degraded cycle
- GIVEN a single degraded cycle followed by a healthy one
- WHEN the alarm evaluates
- THEN it does not alarm
