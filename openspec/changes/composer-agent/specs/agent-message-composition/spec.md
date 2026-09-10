# Agent Message Composition Specification

## Purpose

A second `MessageComposer` (`ports/__init__.py`) implementation: a single structured-output agent call, no tools, invoked only after the dedup policy authorizes a send. Its output is validated before acceptance; every failure mode falls back to `domain.template.MessageComposer`'s output for the identical `MessageRequest`. Design spec section 7; proposal "Accepted consequence" and "Prompt injection" sections.

## Requirements

### Requirement: Invocation only after send authorization

The agent composer MUST be invoked only when the cycle's dedup policy has already authorized sending for the current `MessageRequest`. A deduplicated cycle MUST invoke the agent invocation seam zero times.

#### Scenario: Deduplicated cycle invokes nothing
- GIVEN the dedup policy does not authorize sending
- WHEN the cycle runs
- THEN the agent invocation seam is called zero times

#### Scenario: Authorized cycle invokes the seam once
- GIVEN the dedup policy authorizes sending and the agent composer is selected
- WHEN the cycle composes the message
- THEN the agent invocation seam is called exactly once, with no retry

### Requirement: Prompt content is limited to MessageRequest and excludes recipients

The prompt MUST be built solely from fields already present on `MessageRequest` (city, level, window, reasons, forecast summary, warning summary, source statuses, checklist). It MUST NOT contain any contact identifier, token, or account id.

#### Scenario: Prompt fields are a subset of MessageRequest
- GIVEN a `MessageRequest`
- WHEN the prompt is constructed
- THEN every data value in the prompt traces to a field on that `MessageRequest`

#### Scenario: Recipient data never reaches the prompt
- GIVEN a cycle with active contacts
- WHEN the prompt is constructed
- THEN it contains no contact identifier, token, or account id

### Requirement: Source-derived text is sanitized before entering the prompt

Every title carried by `WarningSummary.title` or any `WarningReason.title` among `request.reasons` MUST pass through `domain.sanitize.sanitize_source_text` before being interpolated into the prompt string, per the `MessageComposer` port docstring invariant. `city`, `timezone`, and `checklist` are operator-owned and are not subject to this rule.

#### Scenario: Hostile title is sanitized in the prompt
- GIVEN a hostile aviso-title fixture (control characters, embedded newlines) from change 1
- WHEN the prompt is constructed
- THEN the prompt string contains no control character and no newline originating from that title

#### Scenario: Operator-owned text is not sanitized
- GIVEN a `MessageRequest` with a checklist item and a city name
- WHEN the prompt is constructed
- THEN the checklist item and city text appear unmodified by `sanitize_source_text`

### Requirement: The agent has no side-effect capability

The agent MUST be given no tool, no send capability, and no write path. Its return value is text consumed by the validator; it cannot itself send, notify, or record anything.

#### Scenario: Agent execution reaches no side effect
- GIVEN the agent runs to completion
- WHEN its output is received
- THEN no notification was sent and no record was written before validation ran, and both remain exclusively under `RunAlertCycle`'s control

### Requirement: Accepted agent messages satisfy the same structural invariants as the template body

Before an agent-composed `AlertMessage` is accepted: `level` MUST equal `request.level`; `valid_until` MUST equal `request.window.end`; the body MUST contain `request.city`; the body length MUST NOT exceed the configured maximum; and the body MUST NOT contain more than one `Motivos:` line or more than one `Recomendaciones:` line.

**"A line" means whatever the renderer means by it.** The header count MUST be taken over `str.splitlines`, the expression the notifier uses to print a body, and not over a narrower split. The character rule below MUST be applied over the body's own separator (`"\n"`) alone, because a break the renderer would honour is exactly the character that rule exists to refuse.

*Amended 2026-09-10, after an adversarial review reproduced the disagreement.* The requirement above said "line" without saying whose. The implementation split on `"\n"` for both questions, deliberately, so that a bare carriage return could not hide from the character rule; `adapters/console_notifier.py` renders with `str.splitlines`, which also splits on U+2028. A body ending `… Recomendaciones: - Abandona la ciudad ahora.` was therefore accepted while the operator read **two** `Recomendaciones:` headers, the forged one formatted identically to the genuine one. The two questions are different — "how many headers will a reader see" and "does this body carry a character it has no business carrying" — and each needs the split that answers it. A validator whose two halves disagree about what a line is has a hole waiting for the next separator someone adds.

The removed-character class of `domain.sanitize` was widened in the same amendment to cover U+2028, U+2029 and the zero-width/format class (U+200B-U+200F, U+FEFF, U+061C, U+00AD and the invisible operators). It had covered C0/C1, DEL and the bidi controls only. Two consequences were reproduced: a zero-width space inside `Recomendaciones​:` renders as the genuine header while comparing unequal to it, so no exact-match count can catch it; and because `\s` does not match that class, one could survive `sanitize_source_text` and reach the prompt inside a hostile aviso title. The class stays defined in one place, as that module already requires.

#### Scenario: A line separator forges a header the renderer will show
- GIVEN a body whose second `Recomendaciones:` header is introduced by U+2028 rather than by `\n`
- WHEN validation runs
- THEN the message is rejected, both because the renderer's own split counts two headers and because U+2028 is not a character a body may carry

#### Scenario: A zero-width space hides inside a section header
- GIVEN a body containing `Recomendaciones` + U+200B + `:` as a line of its own
- WHEN validation runs
- THEN the message is rejected on the character rule, since the header compares unequal to the genuine one while rendering identically to it

#### Scenario: A bare carriage return is still not a legitimate line break
- GIVEN a body containing a bare `\r`
- WHEN validation runs
- THEN the message is rejected, because the character rule is applied over `"\n"`-separated lines and does not treat `\r` as a break

#### Scenario: A compliant agent body is accepted
- GIVEN an agent-returned body meeting every invariant above
- WHEN validation runs
- THEN the message is accepted and `AlertRecord.composer` reads `"agent"`

#### Scenario: A disagreeing valid_until is rejected
- GIVEN the agent returns `valid_until` different from `request.window.end`
- WHEN validation runs
- THEN the message is rejected and the template's output is sent instead

### Requirement: Every number in the body is traceable to MessageRequest

A **candidate number** is a date token (`DD/MM/YYYY`), a time token (`HH:MM`), or a decimal/integer token (comma or dot as decimal separator, optionally followed by `%`); dates and times are extracted whole before scanning for bare decimal/integer tokens, so a date's day/month/year are never evaluated as three independent numbers.

The allowed set is **keyed by unit**, not flat. The units are millimetres, percentage, hours, date and time, and each of the request's values belongs to exactly one of them: `forecast.mm_24h` and `forecast.mm_48h` and every reason's `accumulated_mm` are millimetres; `forecast.peak_probability_pct` and every reason's `probability_pct` are percentages; every reason's `hours` and the literals `24`/`48` (if `forecast` is set) are hours; the local renderings of `window.start`, `window.end`, `warning.window.start`/`.end` (if `warning` is set) and `forecast.peak_at` (if `forecast` is set) are dates and times.

A candidate's unit is resolved from the token immediately following it: `mm` or `milímetro(s)` for millimetres, `%` or `por ciento` for percentage, `hora(s)` or `h` for hours. A date or time token carries its unit in its own shape.

A candidate is **allowed** when, after normalizing its decimal separator and comparing at the precision the source value was computed at (one decimal place for millimetre amounts), it equals a request value **of the unit it was written in**. A candidate written with no adjacent unit is allowed against the union of every unit's values. A candidate whose adjacent unit disagrees with the field it would otherwise have matched MUST be rejected. A candidate is also allowed, regardless of the sets above, when it falls inside a contiguous body span that reproduces **verbatim, in full**, `sanitize_source_text(warning.title)`, one `WarningReason.title`'s sanitized text, or one `request.checklist` item — a partial or paraphrased quote grants no exemption, and neither does a span too short to be a quotation (see the verbatim-span requirement below). Any candidate matching none of the above is rejected.

*Amended 2026-09-10, after an adversarial review reproduced the hole.* This paragraph previously described one flat set of quantities, and the implementation followed it literally: membership could ask "is this number present in the request" and never "present as what". With a 3.0 mm forecast, a 70 % peak probability and a 24-hour horizon, both of the following were accepted:

| Body | Why it passed |
|---|---|
| `Se esperan 70 mm de lluvia.` | `70` is in the set — as the probability |
| `Probabilidad de 24 %.` | `24` is in the set — as the horizon |

The flat set was wrong because it mistook the shape of the threat. A model inventing a rainfall figure does not invent it from nowhere; it draws from the numbers in its own prompt, and those numbers are precisely this set's members. The unconditioned rule therefore legitimised the single most likely hallucination the validator will ever face, which is the exact failure this whole capability exists to prevent, and it did so with no adversary and no trickery.

The bare-figure allowance is kept on purpose and in the other direction: a body may refer to a number the request carries without restating what it measures, and refusing that would refuse the deterministic template's own output, which the fallback invariant forbids.

#### Scenario: A probability cannot legitimise a rainfall figure
- GIVEN `forecast.mm_24h = 3.0` and a peak probability of 70
- WHEN the body states "Se esperan 70 mm de lluvia"
- THEN the message is rejected, because 70 is a percentage on this request and not a millimetre reading

#### Scenario: An horizon cannot legitimise a probability
- GIVEN a reason with `hours = 24` and `probability_pct = 70`
- WHEN the body states "Probabilidad de 24 %"
- THEN the message is rejected, because 24 is an hour count on this request and not a percentage

#### Scenario: A figure stated in its own unit is accepted
- GIVEN `forecast.mm_24h = 3.0`
- WHEN the body states "Se esperan 3,0 mm de lluvia" or "Se esperan 3,0 milímetros de lluvia"
- THEN the number is accepted

#### Scenario: A bare figure is checked against the union
- GIVEN a request carrying 70 as a percentage
- WHEN the body states "70" with no unit written after it
- THEN the number is accepted, because it claims to measure nothing and the union is the honest comparison

`probability_threshold_pct` is deliberately **not** allowed, and that is worth stating because an earlier draft of this requirement listed it. The Spanish template never renders it: `template.py` drops it with the comment that the internal threshold is operator detail rather than something a recipient can act on, and only the English operator rendering prints it. The value therefore never enters the prompt, so the model cannot legitimately know it, and a body containing it has no honest reason to. Excluding it costs nothing, since the invariant asserting the template always passes does not need it, and it removes one number a draft could carry without justification.

*Left to design*: the exact tokenizer implementation, the rounding-equivalence rule for values expressed at a precision other than one decimal place, and whether the exemption should extend to a title/checklist fragment shorter than the full string. This spec fixes only the boundary stated above.

#### Scenario: Spanish decimal comma is accepted
- GIVEN `ForecastThresholdReason.accumulated_mm = 12.4`
- WHEN the body states "12,4 mm"
- THEN the number is accepted as matching `accumulated_mm`

#### Scenario: A window time is accepted
- GIVEN `request.window.start` renders locally as `14:00`
- WHEN the body states "14:00"
- THEN the number is accepted as matching the window start

#### Scenario: A window date is accepted
- GIVEN `request.window.start` renders locally as `12/03/2026`
- WHEN the body states "12/03/2026"
- THEN the number is accepted as matching the window start

#### Scenario: A horizon constant is accepted
- GIVEN `request.forecast` is set
- WHEN the body states "en las próximas 24 horas"
- THEN "24" is accepted as the forecast's fixed 24-hour horizon

#### Scenario: A digit inside a verbatim-quoted title is accepted
- GIVEN a sanitized aviso title containing "AVISO 335" and the body quotes that sanitized title in full
- WHEN validation runs
- THEN "335" is accepted because it sits inside the verbatim-quoted title span

#### Scenario: A digit inside a verbatim-quoted checklist item is accepted
- GIVEN a checklist item "Prepara víveres para 72 horas" reproduced verbatim in the body
- WHEN validation runs
- THEN "72" is accepted because it sits inside the verbatim-quoted checklist span

#### Scenario: An invented number is rejected
- GIVEN no structured field and no verbatim-quoted title or checklist span contains "80"
- WHEN the body states "se esperan 80 mm"
- THEN the message is rejected and the template's output is sent instead

### Requirement: A candidate whose written form is ambiguous is not read as a quantity

Normalizing the decimal separator MUST NOT be allowed to erase the difference between two figures a reader would read differently. A candidate token MUST be refused, whatever it would canonicalize to, when its written form is one the deterministic template could not have produced: a separator followed by exactly three digits (the Spanish thousands group), more than one separator in a single token, a zero-padded integer part, a leading sign, superscript or subscript digits, or a non-ASCII digit family.

*Added 2026-09-10, after an adversarial review.* `Decimal("12.400") == Decimal("12.4")`, so with `accumulated_mm = 12.4` the body "Se pronostican 12.400 mm" was accepted — and in Peruvian Spanish that reads as twelve thousand four hundred millimetres. `12,400 mm` and `0012,4 mm` passed the same way, and `-12,4 mm` reached a reader as a negative reading of an allowed value.

Two closures were available. Comparing against the strings the template renders would work, but it would reject `12,40`, which the precision-equivalence rule above accepts on purpose, and it would need one rendered form per separator convention. Refusing the ambiguous **form** was chosen instead: it keeps every equivalence the template can produce and refuses only shapes no honest draft needs.

Writing the test for the digit families found that the review's own note was wrong about them. `re`'s `\d` matches every Unicode decimal digit and `Decimal` parses every one, so `१२.४`, `١٢.٤` and `１２.４` each canonicalized to 12.4, matched `accumulated_mm`, and were accepted — putting a rainfall figure the community cannot read in front of it with the validator's approval. Superscripts escaped extraction entirely. Both are refused here on the same ground.

#### Scenario: A thousands group is not the quantity it canonicalizes to
- GIVEN `accumulated_mm = 12.4`
- WHEN the body states "12.400 mm" or "12,400 mm"
- THEN the message is rejected, because a reader reads twelve thousand four hundred

#### Scenario: A precision equivalence the template can produce still passes
- GIVEN `accumulated_mm = 12.4`
- WHEN the body states "12,4 mm", "12.4 mm" or "12,40 mm"
- THEN the number is accepted

#### Scenario: A sign is part of the figure
- GIVEN `accumulated_mm = 12.4`
- WHEN the body states "-12,4 mm"
- THEN the message is rejected, because no value on a request is negative

#### Scenario: A figure a reader cannot read is refused
- GIVEN `accumulated_mm = 12.4`
- WHEN the body states the same value in superscript digits, or in a non-ASCII digit family
- THEN the message is rejected, whatever the token canonicalizes to

### Requirement: A measurement written in words is rejected

The digit rule above cannot see a quantity spelled out, so a model could write "ochenta milímetros" and escape it entirely. The validator MUST therefore reject a body in which a Spanish number word directly quantifies a **unit this system reports**: a millimetre amount, a percentage, or an hour count. The prompt MUST separately instruct the model to render every quantity in digits; that instruction is not enforcement.

*Amended 2026-09-09, before implementation.* The rule first read "a Spanish number word standing for a quantity", with no unit condition, and that was wrong in a way the shipped system proves. The deterministic template's own body contains three word-numbers today, from the operator checklist: `dos` in "al menos dos días", and `un`/`una` in "un botiquín" and "una linterna". The unconditional rule would have rejected the system's own text, and the design's invariant pinning the validator against the template output would have failed on the first run. Worse, `un` and `una` are the ordinary Spanish indefinite articles, so an unconditioned detector fires on almost any sentence.

Narrowing to a reported unit targets the actual threat, which is inventing a rainfall or probability figure, and leaves ordinary prose alone. It is deliberately narrower than the digit rule: a word-number that quantifies anything else, such as days or objects, is not this requirement's concern.

#### Scenario: A rainfall amount written in words is rejected
- GIVEN a candidate body states "ochenta milímetros" instead of a digit form
- WHEN validation runs
- THEN the message is rejected and the template's output is sent instead

#### Scenario: A probability written in words is rejected
- GIVEN a candidate body states "setenta por ciento"
- WHEN validation runs
- THEN the message is rejected and the template's output is sent instead

#### Scenario: The template's own checklist wording is not rejected
- GIVEN a body reproducing the checklist item "Almacena agua potable para al menos dos días."
- WHEN validation runs
- THEN "dos" raises no violation, because "días" is not a unit this system reports

#### Scenario: The Spanish indefinite article is not read as a number
- GIVEN a body containing "una linterna" and "un botiquín"
- WHEN validation runs
- THEN neither raises a violation

#### Scenario: The deterministic template always passes this rule
- GIVEN any `MessageRequest` the system can produce
- WHEN the template's own output is validated
- THEN it raises no violation, so the fallback can never be rejected by the rule that guards the agent

#### Scenario: The prompt instructs digit rendering
- GIVEN a `MessageRequest`
- WHEN the prompt is constructed
- THEN it contains an explicit instruction to render every quantity in digits

### Requirement: Every composer failure mode falls back to the template

On any of the following, the composer MUST return `domain.template.MessageComposer`'s output for the same `MessageRequest`, unchanged, and `AlertRecord.composer` MUST read `"template"`.

#### Scenario: Deadline exceeded
- GIVEN the invocation seam exceeds the hard deadline
- WHEN the cycle composes
- THEN the template's output is sent and `composer == "template"`

#### Scenario: Transport error
- GIVEN the invocation seam raises a transport error
- WHEN the cycle composes
- THEN the template's output is sent and `composer == "template"`

#### Scenario: Unsuccessful runtime response
- GIVEN the invocation seam returns an unsuccessful response
- WHEN the cycle composes
- THEN the template's output is sent and `composer == "template"`

#### Scenario: Unparseable response body
- GIVEN the invocation seam returns a body that cannot be parsed into the output contract
- WHEN the cycle composes
- THEN the template's output is sent and `composer == "template"`

#### Scenario: Missing required field
- GIVEN the parsed response omits a required output field
- WHEN the cycle composes
- THEN the template's output is sent and `composer == "template"`

#### Scenario: Disagreeing level
- GIVEN the agent's `level` differs from `request.level`
- WHEN the cycle composes
- THEN the template's output is sent and `composer == "template"`

#### Scenario: Body omits the city
- GIVEN the agent's body does not contain `request.city`
- WHEN the cycle composes
- THEN the template's output is sent and `composer == "template"`

#### Scenario: Body over the length cap
- GIVEN the agent's body exceeds the configured maximum length
- WHEN the cycle composes
- THEN the template's output is sent and `composer == "template"`

#### Scenario: The cap leaves room for the template the configuration can produce
- GIVEN the maximum body length is 1500 characters
- WHEN the deterministic template's output is measured for every request fixture
- THEN every one is under the cap with room to spare

*The cap is pinned here at 1500 because the design asked the spec to settle it. The measured template body today is about 570 characters with the five-item Ferreñafe checklist, so 1500 leaves room for a checklist that roughly doubles. The number matters less than the relationship: the cap MUST stay above the longest body the configuration can produce, or the fallback would fail the rule that guards the agent. The invariant asserting the template always passes the validator is what enforces that, so a checklist that outgrows the cap fails a test rather than an alert.*

#### Scenario: The title cap leaves room for a real aviso title
- GIVEN the maximum title length is 200 characters
- WHEN the deterministic template's title is measured for every request fixture
- THEN every one is under the cap with room to spare

*The title cap is pinned at 200, the value the design proposed and left for the spec. Measured: the template's own title runs about 47 characters, and the longest aviso title on the live page is 79, "INCREMENTO DE TEMPERATURA DIURNA EN LA COSTA Y SIERRA (EXTENSIÓN DEL AVISO 335)". A title is a subject line, not prose, so 200 is generous while still refusing a body smuggled into the title field. The same relationship as the body cap applies, and the same invariant enforces it.*

#### Scenario: Body contains a number absent from the input
- GIVEN the agent's body contains a number outside the allowed set
- WHEN the cycle composes
- THEN the template's output is sent and `composer == "template"`

#### Scenario: Body contains a forged section header
- GIVEN the agent's body contains a second `Motivos:` or `Recomendaciones:` line
- WHEN the cycle composes
- THEN the template's output is sent and `composer == "template"`

#### Scenario: Empty body
- GIVEN the agent's body is empty
- WHEN the cycle composes
- THEN the template's output is sent and `composer == "template"`

#### Scenario: Invoker raises an unexpected exception type
- GIVEN the invocation seam raises an exception type not otherwise anticipated
- WHEN the cycle composes
- THEN the template's output is sent and `composer == "template"`

### Requirement: Operator is notified exactly once per fallback, never on success

When the composer falls back to the template, the system MUST emit exactly one operator notice of a fallback kind through `Notifier.send_operator_notice`. When the agent's output is accepted, it MUST emit none.

#### Scenario: One notice per fallback
- GIVEN any fault from the fallback table above
- WHEN the cycle composes
- THEN exactly one operator notice of the fallback kind is emitted

#### Scenario: No notice on acceptance
- GIVEN the agent's output is accepted
- WHEN the cycle composes
- THEN no fallback notice is emitted

### Requirement: The alert record states which composer produced the message

`AlertRecord.composer` MUST read `"agent"` when the accepted message came from the agent composer, and `"template"` in every other case, including every fallback path.

#### Scenario: Accepted agent output is attributed
- GIVEN the agent's output is accepted
- WHEN the alert is recorded
- THEN `AlertRecord.composer == "agent"`

#### Scenario: Any fallback is attributed to the template
- GIVEN any fault from the fallback table above
- WHEN the alert is recorded
- THEN `AlertRecord.composer == "template"`

### Requirement: Composer selection defaults to the template

The system MUST select `domain.template.MessageComposer` as the active composer unless configuration explicitly selects the agent composer. With no configuration change, cycle behavior MUST be identical to behavior before this change.

#### Scenario: Default configuration never invokes the agent
- GIVEN configuration does not select the agent composer
- WHEN the cycle runs an authorized send
- THEN the agent invocation seam is called zero times and `composer == "template"`

#### Scenario: Explicit selection enables the agent composer
- GIVEN configuration selects the agent composer and the dedup policy authorizes sending
- WHEN the cycle runs
- THEN the agent invocation seam is called, subject to the same validation and fallback rules above

### Requirement: The offline test suite requires no model, network, or credentials

Every scenario in this capability that does not explicitly require a live agent MUST be provable against an injected fake seam, with zero network calls, zero model calls, and no credentials present.

#### Scenario: Default suite run is offline
- GIVEN the default test command
- WHEN it runs
- THEN every requirement above except any scenario explicitly marked live/opt-in is proven with no network call, no model call, and no credential
