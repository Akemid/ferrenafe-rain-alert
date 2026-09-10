# Apply Progress: composer-agent — PR 1 of 5

**Branch**: `docs/composer-agent-plan` (planning artifacts + this slice; not pushed)
**Slice**: PR 1 of five — the pure message validator only
**Mode**: Strict TDD (`openspec/config.yaml → rules.apply.tdd: true`)
**Delivery**: chained/stacked slice, resolved by the orchestrator before apply
**Baseline**: 559 tests passing → **638 tests passing**

## Scope

**In**: `domain/message_validation.py` (nine rules, `Violation`, `ValidationRule`),
the `has_unsafe_characters` predicate rule 7 needs, and invariant V0. Tests only.
Nothing is wired into the cycle; nothing constructs the validator in production code yet.

**Deliberately left for later PRs** — these tasks remain `[ ]` in `tasks.md`:

| Left out | Tasks | PR |
|---|---|---|
| Provenance (`ComposerName`, `AlertMessage.composed_by`, serialization) | 1.1–1.6 | PR 2 |
| Invocation seam, `FakeAgentInvoker`, `build_fake_deps(composer=)` | 1.17–1.20 | PR 2 |
| Fallback composer + the 12-row fault table | 1.21–1.36 | PR 3 |
| Fallback notice + `NoticeKind.AGENT_FALLBACK_USED` | 1.37–1.38 | PR 3 |
| Mutation proof of the fallback `except` clauses | 1.39–1.40 | PR 3 |
| `RunAlertCycle` wiring and the zero-invocation test | 1.41–1.44 | PR 4 |
| Security review + PR gate for the combined phase-1 unit | 1.45–1.46 | last phase-1 PR |
| `agent/` package, prompt, AWS/Strands, config, CLI | Phase 2–3 | PR 4–5 |

`agent/` was not created. `run_alert_cycle.py`, `wiring.py` and `cli.py` were not touched.

## Tasks completed

- [x] 1.7 RED — structural rule cases (rules 1–7)
- [x] 1.8 GREEN — `validate_message`, `ValidationRule`, `Violation`, rules 1–7
- [x] 1.9 RED — `has_unsafe_characters` predicate
- [x] 1.10 GREEN — predicate added to `domain/sanitize.py`
- [x] 1.11 RED — rule 8 cases (`UNKNOWN_NUMBER`)
- [x] 1.12 GREEN — residue, whole date/time extraction, `Decimal` canonicalization, verbatim exemption
- [x] 1.13 RED — rule 9 cases (`WORD_NUMBER`), unit-conditioned per the spec
- [x] 1.14 GREEN — Spanish numeral lexicon conditioned on a reported unit
- [x] 1.15 RED — invariant V0 over every request fixture
- [x] 1.16 GREEN — V0 confirmed, and proved able to fail (see the mutation row below)

## TDD Cycle Evidence

| Task | RED — test first, and the failure it produced | GREEN | Refactor |
|---|---|---|---|
| 1.9 / 1.10 | `ImportError: cannot import name 'has_unsafe_characters' from 'rain_alert.domain.sanitize'` | predicate added; `tests/unit/domain/test_sanitize.py` 51 passed | none needed |
| 1.7 / 1.8 | `ModuleNotFoundError: No module named 'rain_alert.domain.message_validation'` | 21 passed | none needed |
| 1.11 / 1.12 | `AttributeError: type object 'ValidationRule' has no attribute 'UNKNOWN_NUMBER'` (collection error) | 38 passed | `_pad` split into `_normalized_date` / `_normalized_time` — the single-partition version padded the day but not the month |
| 1.13 / 1.14 | `AttributeError: type object 'ValidationRule' has no attribute 'WORD_NUMBER'` × 11 cases | 49 passed | numeral lexicon rewritten as a set literal (ruff SIM905) |
| 1.15 / 1.16 | V0 passed on first run, so it was proved able to bite by mutation: making rule 9 unconditioned (`or "any quantity"`) turned **7 of 10 fixtures red**, all on `'un' quantifies 'any quantity'` — the indefinite article inside the template's own "una probabilidad máxima". That is exactly the regression the 2026-09-09 spec amendment predicted. Mutation reverted; 10 passed. | 10 passed | none needed |

## Verification gate

```
$ uv run pytest                → 638 passed, 15 deselected      exit=0
$ uv run ruff check .          → All checks passed!             exit=0
$ uv run ruff format --check . → 80 files already formatted     exit=0
$ uv run mypy                  → Success: no issues found in 36 source files   exit=0
```

No test was weakened and no type was loosened to reach green. One pre-existing
guard did fire and was obeyed rather than relaxed: `tests/hygiene` rejected a
phone-number literal copied into a new sanitizer test, so the literal was
changed and the offending commit amended (the hygiene scanner reads git
history, not just the worktree).

## Commits

| Commit | Work unit |
|---|---|
| `81ceaa9` | `feat(domain): expose has_unsafe_characters predicate on the sanitizer` |
| `c45a6ee` | `feat(domain): add the message validator's structural rules` |
| `d45cabb` | `feat(domain): reject body numbers that trace to no request value` |
| `413b7f8` | `feat(domain): reject word-numbers that quantify a reported unit` |
| `30adba8` | `test(domain): pin invariant V0 across every request fixture` |

## Files changed

| File | Action | What |
|---|---|---|
| `src/rain_alert/domain/message_validation.py` | Created | The nine rules, `ValidationRule`, `Violation`, `MAX_BODY_LENGTH`/`MAX_TITLE_LENGTH` |
| `src/rain_alert/domain/sanitize.py` | Modified | `has_unsafe_characters` predicate over the existing `_REMOVED` class |
| `tests/unit/domain/test_message_validation.py` | Created | Rules 1–9, the laundering attack, invariant V0 |
| `tests/unit/domain/test_sanitize.py` | Modified | Predicate cases, including agreement with the sanitizer |

## Budget overrun — reported, not absorbed

Target for this PR was ~500 changed lines; it landed at **942 additions, 0 deletions**.
The overrun is in the test module (492 lines) rather than the rules (377).
Drivers: nine rules each need both directions, rule 8 needs eight accepted and
six rejected cases to pin the verbatim-quote boundary, and V0 needs ten
fixtures to be worth anything. No task beyond 1.16 was started once the number
became visible. If the owner wants this under 700, the honest cut is to split
the test module along the same line the rules already fall on — rules 1–7 in
this PR, rules 8–9 plus V0 in the next — rather than to thin the cases.

## Contradictions found in the planning artifacts

1. **`probability_threshold_pct` — spec and design disagree, and it is load-bearing.**
   The spec's allowed-number set includes "any reason's `accumulated_mm`, `hours`,
   `probability_pct`, or `probability_threshold_pct` (when present)". Design §6
   rule 8 step 4 says the opposite, explicitly: "**not** `probability_threshold_pct`
   — it is operator-only and never enters the prompt, so it must never appear in
   a body". **Implemented per the spec** (it is named as the behavioural
   authority) and pinned by
   `test_an_operator_only_threshold_is_in_the_allowed_set`, whose docstring
   records the conflict. Practical effect is small in both directions: the value
   never enters the prompt, so a model cannot know it, and the allowed set is
   numeric rather than unit-aware anyway. **Needs an owner decision** — flipping
   it is a two-line change plus one test.

2. **Date and time extraction — spec supersedes design.** Design §6 rule 8 step 2
   splits on `/` and `:`, so `12/03/2026` becomes `12`, `03`, `2026`. The spec
   requires dates and times to be extracted **whole** before the scan for bare
   decimals. Implemented per the spec, which is also strictly safer: the design's
   version lets a day that happens to match an unrelated field launder the rest
   of an invented date. Pinned by `test_a_date_is_one_candidate_and_not_three_numbers`.

3. **`warning.source_id` digits and UTC ISO components — in design, absent from the spec.**
   Design's allowed set adds both. The spec's does not. Implemented per the spec
   (strict). Consequence: a body stating the aviso number outside a full verbatim
   quote of the title is rejected. That is the intended direction of error, but it
   is a real narrowing of the design and the owner should know.

4. **`MAX_TITLE_LENGTH` was never pinned by the spec.** The spec pins 1500 for the
   body and reasons about 200 for the title in prose, but only the body cap is
   stated as a requirement. Shipped as `MAX_TITLE_LENGTH = 200` per the design's
   proposal, per task 1.8, and pinned by a test so the number cannot drift silently.
   `tasks.md` asked for this to be confirmed explicitly rather than assumed —
   consider it raised.

5. **Rule 7 cannot be applied to a body as written.** Design §6 rule 7 says "no
   control character anywhere in title or body", reusing `_REMOVED` — which
   contains `\n`. The body is a line-oriented format, so a literal reading rejects
   every template body and fails V0. Implemented as: the title is checked whole,
   the body is checked line by line after splitting on `\n` **only** (not
   `str.splitlines`, which would swallow a bare `\r` and several Unicode
   separators — exactly the characters the rule exists to catch). Pinned by
   `rule-7-a-bare-carriage-return-is-not-a-legitimate-line-break`.

## Residuals, accepted and recorded

- The allowed-number set is numeric, not unit-aware: a body writing `60 mm` when
  `60` is a probability passes rule 8. Design accepts this class of residual; it
  is worth restating because it is the ceiling on what rule 8 can promise.
- Roman numerals and ordinals (`nivel III`, `primer`) are neither digit runs nor
  lexicon entries, per design's own recorded residual. Unchanged here.
- `validate_message` calls `ZoneInfo(request.timezone)` and will raise on an
  invalid timezone string, exactly as `domain/template.py` already does. Not
  defended against, because a request that cannot be rendered cannot be validated
  either, and the failure is loud.

## Next

`sdd-apply` again for PR 2 (provenance plumbing 1.1–1.6 and the invocation seam
1.17–1.20). Correctness review and security review on this branch first — this
slice is not pushed and no PR was opened.

---

# Apply Progress: composer-agent — PR 1 remediation (adversarial review)

**Branch**: `docs/composer-agent-plan`, same slice, not pushed, no PR opened
**Mode**: Strict TDD, failing test first, every finding's test written as the attack
**Baseline**: 638 tests passing → **723 tests passing**

An adversarial review of the validator reproduced seven confirmed findings plus
a set of low-severity ones. Every finding below was reproduced against the
shipped code before the fix and is pinned by a test after it. One further hole
was found while writing a test that assumed the review was right; it is marked.

## Findings and disposition

| # | Finding | Disposition |
|---|---|---|
| CRITICAL 1 | Allowed-number set unit-blind, so any figure legitimises any quantity | Fixed — set keyed by unit; spec amended |
| CRITICAL 2 | Forged section header survives; validator and renderer disagree about a line | Fixed — character class widened, header count uses the renderer's split; spec amended |
| HIGH 3 | Spanish thousands separator inflates a figure by a thousand | Fixed — ambiguous forms are not read as quantities; spec amended |
| HIGH 4 | Word-number rule misses invented quantities in ordinary Spanish | Fixed — backward pass, wider connectors and lexicon; spec amended |
| MEDIUM 5 | Invariant V0 false for four reachable configurations | Fixed — checklist sanitized and bounded at the template; four V0 fixtures added; spec corrected |
| MEDIUM 6 | Spanish indefinite article fires the word-number rule | Fixed — `un`/`una`/`uno` are connectors, not numerals; spec amended |
| MEDIUM 7 | Verbatim-span exemption unanchored, unbounded, uncounted | Fixed — ≥ 12 characters, word-boundary, one occurrence per supplied string; spec amended |
| LOW | Sign dropped | Fixed — the sign is part of the token |
| LOW | Superscript digits escape extraction | Fixed — extracted and always refused |
| LOW | Word-number cases assert membership, not an exact rule set | Fixed — exact sets, like every other block |
| LOW | `validate_message` raises on a bad timezone | **Recorded, not changed** — see below |
| **NEW** | Non-ASCII digit families were **not** rejected, contrary to the review's note | Fixed — canonicalization is ASCII-only |
| Out of scope | A body with no numbers can say anything; nothing rejects a URL or a phone number | Recorded in `design.md` §6.1 as open items for a later PR |

## The two critical attacks, before and after

**CRITICAL 1** — forecast 3.0 mm, peak probability 70, reason hours 24:

| Body | Before | After |
|---|---|---|
| `Se esperan 70 mm de lluvia.` | ACCEPTED (70 is the probability) | rejected `unknown_number` |
| `Probabilidad de 24 %.` | ACCEPTED (24 is the horizon) | rejected `unknown_number` |
| `Se esperan 3,0 mm de lluvia.` | accepted | accepted |

**CRITICAL 2** — the template's body plus a suffix:

| Suffix | Before | After |
|---|---|---|
| `\u2028Recomendaciones:\u2028- Abandona la ciudad ahora.` | ACCEPTED; the notifier's own `splitlines` showed the operator **two** `Recomendaciones:` headers | rejected `forged_section_header` + `unsafe_character` |
| `\nRecomendaciones\u200b:\n- Abandona la ciudad ahora.` | ACCEPTED; invisible to the reader, unequal to the exact match | rejected `unsafe_character` |

`sanitize_source_text` also let U+200B, U+200C, U+FEFF, U+061C and U+200E
through, because `\s` does not match them. All are removed now.

## TDD cycle evidence

| Work unit | RED — the failure, and that it was the intended one | GREEN |
|---|---|---|
| Sanitizer character class | 24 failures: every invisible character survived `sanitize_source_text`, and the predicate called U+2028/U+2029 safe | `TestTheInvisibleCharactersAreRemovedToo`, 28 pass |
| Renderer-agreeing line split | 2 failures: `rendered.count("Recomendaciones:") == 2` held while `FORGED_SECTION_HEADER` did not fire | 2 structural cases + the renderer triangulation |
| Unit-keyed allowed set | 4 failures, exactly the three unit-blind acceptances plus the violation-detail test | `TestANumberIsOnlyAllowedAsTheQuantityItActuallyIs`, 10 pass |
| Numeric forms | 6 failures: the thousands groups, the zero padding, the sign, the superscripts | `TestANumericFormAReaderCanMisreadIsRefused`, 13 pass |
| Non-ASCII digit families | 3 failures — the test was written expecting the review's claim and disproved it: `Decimal("१२.४") == Decimal("12.4")` | 3 pass, inside the block above |
| Word-number both directions | 8 failures: 5 escapes and 3 false positives, exactly as reported | `TestAMeasurementWrittenInWordsIsRejected`, 19 pass |
| Verbatim-span bound | 3 failures: the short checklist item, the number cut in half, the doubled quotation | `TestTheVerbatimSpanExemptionIsBoundedAndCounted`, 5 pass |
| Invariant V0 checklist fixtures | 5 failures: tab, forged header, bidi override, thirty items, and the margin assertion | `TestInvariantV0` 19 pass and `TestTheOperatorChecklistCannotForgeStructureEither` 9 pass; proved able to bite by mutation (unsanitized, unbounded checklist → **14** red across two modules), mutation reverted |

## Deliberate decisions worth an owner's attention

1. **`un`/`una`/`uno` no longer fire on their own.** `Puede caer un mm` is
   accepted. Wrong-lax by one, taken because the alternative refuses `Espera
   una hora después de que pare la lluvia` — ordinary Spanish, and the exact
   false positive both the spec and the design warned this rule about.
2. **Ambiguous form over rendered-string comparison, for finding 3.** Comparing
   against the strings the template renders would also close it, but it would
   reject `12,40`, which the spec accepts on purpose as the same quantity at
   the same precision, and it needs one rendered form per separator
   convention. Refusing the form keeps every equivalence the template can
   produce.
3. **The template now bounds its own checklist against `MAX_BODY_LENGTH`.**
   `domain/template.py` imports the constant from `domain/message_validation.py`
   — no cycle, and it makes the V0 relationship structural rather than a margin
   nobody was measuring. The dropped items are declared in the body.
4. **`validate_message` still raises on an unusable timezone.** A bad timezone
   is a configuration fault, not a candidate fault; reporting it as a violation
   would make the fallback adapter send the template, which cannot render
   either, while telling the operator the draft was at fault. **The fallback
   adapter must not assume this function cannot raise.**
5. **The verbatim-span minimum drops `request.city`** (nine characters) from
   the exempt spans. It carries no digit and no Spanish numeral. A checklist
   item under twelve characters that *did* contain a digit would fail V0 rather
   than reach an alert.

## Verification gate

```
$ uv run pytest                → 723 passed, 15 deselected                     exit=0
$ uv run ruff check .          → All checks passed!                            exit=0
$ uv run ruff format --check . → 80 files already formatted                    exit=0
$ uv run mypy                  → Success: no issues found in 36 source files   exit=0
```

No test was weakened and no type was loosened. Two assertions were *changed*
rather than weakened: `test_every_template_body_is_comfortably_under_the_caps`
asserted `< MAX_BODY_LENGTH / 2`, which a bounded template that cuts to fit
cannot satisfy, so it became a strict cap assertion plus a separate test
recording the measured margin; and the word-number block moved from membership
assertions to exact rule sets, which is strictly stronger.

## Commits

| Commit | Work unit |
|---|---|
| `5434ebd` | `fix(domain): remove the invisible character class at the sanitizer` |
| `4541a28` | `fix(domain): count section headers the way the renderer splits lines` |
| `14405cd` | `fix(domain): key the allowed-number set by the unit a figure is stated in` |
| `a041434` | `fix(domain): refuse numeric forms canonicalization would erase` |
| `3c369ac` | `fix(domain): read a word-number's unit in both directions` |
| `26a750d` | `fix(domain): anchor, bound and count the verbatim-span exemption` |
| `315b9e1` | `fix(domain): sanitize and bound the checklist the template renders` |

## Files changed

| File | Action | What |
|---|---|---|
| `src/rain_alert/domain/sanitize.py` | Modified | `_REMOVED` widened to U+2028/29 and the zero-width/format class |
| `src/rain_alert/domain/message_validation.py` | Modified | `NumberUnit`, unit-keyed allowed set, form-aware canonicalization, two line splits, bounded/anchored/counted spans, two-directional word-number scan |
| `src/rain_alert/domain/template.py` | Modified | Checklist sanitized and bounded; `CHECKLIST_TRUNCATED_ES` |
| `tests/unit/domain/test_sanitize.py` | Modified | The invisible-character class, both directions |
| `tests/unit/domain/test_message_validation.py` | Modified | Unit, numeric-form, span-bound and word-number blocks; four V0 checklist fixtures |
| `tests/unit/domain/test_template.py` | Modified | The checklist as an injection path and as a length risk |
| `openspec/.../agent-message-composition/spec.md` | Modified | Three requirements amended, three added, the cap paragraph corrected |
| `openspec/changes/composer-agent/design.md` | Modified | §6.1 recording what steps 1–6 no longer say, the new residuals, and the out-of-scope items |

`docs/` and `openspec/specs/` were not touched. Nothing was pushed and no pull
request was opened.

---

# Second adversarial-review remediation — 2026-09-10

**Branch**: `docs/composer-agent-plan` (unchanged; nothing pushed, no PR opened)
**Mode**: Strict TDD — failing test first, failure confirmed as the intended
one, then implementation
**Baseline**: 723 tests passing → **793 tests passing**

A second review reproduced four holes in the validator, each confirmed twice
by the reviewer and by the delegating agent. Three of the four are the same
mistake in three places: a rule that **enumerated the cases somebody had
thought of** where it meant a property, or that bounded itself by a count
where it meant a boundary a reader would recognise.

## Findings and disposition

| # | Finding | Disposition |
|---|---|---|
| CRITICAL 1 | Invisible characters outside the removed class defeat both the character rule and the header count | **Fixed** — class is a Unicode property; body composed to NFC for rules 6 and 8 |
| HIGH 2 | Unit resolution is forward-only and offset-exact, so the flat-set hole is reachable in ordinary Spanish | **Fixed** — folded residue, bounded punctuation run, plural spellings, clause-scoped backward reading |
| MEDIUM 3 | The word-numeral backward scan is bounded by token count, not by clause | **Fixed** — one clause-scoped scan, used by rules 8 and 9 |
| LOW 4 | The truncation marker is forgeable from configuration | **Fixed** — an item that renders as the marker is refused, so the marker is always the template's own and always last |
| Not this PR | No rule governs what the message tells a person to do; the verbatim exemption approves the attacker's own channel | **Recorded**, not built — `tasks.md` → Open Items, as a blocking condition on the PR that first wires model output into the cycle |

## The attacks, before and after

Every input below was run against the code as it stood, then against the code
as it stands. `request` carries a 3.0 mm forecast, a peak probability of 70
and a 24-hour horizon unless stated otherwise.

### CRITICAL 1 — invisible characters

| Input | Before | After |
|---|---|---|
| `has_unsafe_characters("Aviso" + U+E0001 + "de lluvias")` | `False` | `True` |
| Same for U+E0041, U+E007F, U+E0002, U+FE00, U+FE0F, U+E0100, U+115F, U+1160, U+3164, U+180B, U+180E, U+2800 | `False` | `True` |
| `sanitize_source_text` on each of the thirteen | character survives | character removed |
| Body ending `\nRecomendaciones` + U+E0001 + `:\n- Abandona la ciudad ahora.` | **accepted**, header count 1, reader sees 2 | rejected `unsafe_character` |
| `Se esperan 70 milímetros de lluvia.` written NFD | **accepted** (no unit resolved → union) | rejected `unknown_number` |

Legitimate path re-verified: accented Spanish, the em dash, `Ferreñafe` with a
combining tilde, and the shipped five-item checklist all still validate clean.

### HIGH 2 — unit resolution

Control `Se esperan 70 mm de lluvia.` was correctly rejected throughout.

| Input | Before | After |
|---|---|---|
| `Lluvia acumulada en milímetros: 70` | **accepted** | rejected `unknown_number` |
| `Milímetros de lluvia: 70` | **accepted** | rejected `unknown_number` |
| `Se esperan 70 (mm).` | **accepted** | rejected `unknown_number` |
| `Se esperan 70-mm.` | **accepted** | rejected `unknown_number` |
| `Se esperan 70 mms.` | **accepted** | rejected `unknown_number` |
| `Se esperan 3,0 mm de lluvia.` | accepted | accepted |
| `El aviso lleva el numero 70 en el registro.` | accepted | accepted |

### MEDIUM 3 — the backward scan

| Input | Before | After |
|---|---|---|
| `La lluvia en milímetros que se espera hoy es de setenta.` | **accepted** | rejected `word_number` |
| `Se acumularán milímetros de lluvia, en total, setenta.` | **accepted** | rejected `word_number` |
| `Probabilidad de que llueva: casi con seguridad ochenta.` | **accepted** | rejected `word_number` |
| `Se esperan ochenta milimetros de lluvia.` | rejected | rejected |
| `Almacena agua potable para al menos dos dias.` | accepted | accepted |
| `Espera una hora despues de que pare la lluvia.` | accepted | accepted |

### LOW 4 — the truncation marker

| Input | Before | After |
|---|---|---|
| Checklist containing an item equal to the marker's wording, in a list long enough to be cut | marker rendered **twice**, the forged one mid-list above real instructions | marker rendered once, as the final line; the operator's other items survive |

## One regression found by the tests and fixed inside the same unit

Scoping the backward scan to the clause made `Probabilidad de 70 por ciento.`
raise `word_number` on `ciento`, which the four-token window had hidden rather
than decided. `por ciento` is a unit spelling, not a numeral quantifying one —
the forward matcher had always read it as a phrase — so the `ciento` of that
phrase is skipped. Recorded as a residual in design §6.2: a body writing
`ciento` as a numeral immediately after the word `por` goes unrefused.

## Verification gate

```
$ uv run pytest                → 793 passed, 15 deselected              exit=0
$ uv run ruff check .          → All checks passed!                     exit=0
$ uv run ruff format --check . → 80 files already formatted             exit=0
$ uv run mypy                  → Success: no issues found in 36 files   exit=0
```

No test was weakened and no type was loosened. Nothing was deleted from the
suite; 70 assertions were added.

## Commits

| Commit | Work unit |
|---|---|
| `34e817c` | `fix(domain): reject invisible characters by Unicode property` |
| `0ce5a95` | `fix(domain): read one NFC-composed body in the header and number rules` |
| `391415a` | `fix(domain): resolve a figure's unit across punctuation and spelling` |
| `643b30e` | `fix(domain): bound the implied-unit scan by clause and use it for digits` |
| `69af3ee` | `fix(domain): keep the truncation marker the template's own and last` |
| (this one) | `docs(composer-agent): record the content-rule gap as a blocking item` |

## Files changed

| File | Action | What |
|---|---|---|
| `src/rain_alert/domain/sanitize.py` | Modified | `_REMOVED` replaced by `_NON_GRAPHIC_CATEGORIES` + `_ALSO_INVISIBLE` and the `_is_invisible` predicate |
| `src/rain_alert/domain/message_validation.py` | Modified | `_composed_form`; punctuation-tolerant, folded, plural-aware unit matching; one clause-scoped `_unit_implied_before` serving rules 8 and 9; `_BACKWARD_WINDOW` and `_QUALIFIERS_BEFORE_A_NUMERAL` removed |
| `src/rain_alert/domain/template.py` | Modified | `_checklist_lines` refuses an item that renders as the truncation marker |
| `tests/unit/domain/test_sanitize.py` | Modified | The property class, both directions, plus the language-untouched triangulation |
| `tests/unit/domain/test_message_validation.py` | Modified | NFC block, punctuation/plural block, unit-in-front block, three word-number escapes and three boundary guards, the tag-character structural case |
| `tests/unit/domain/test_template.py` | Modified | The truncation marker as a forgeable signal |
| `openspec/.../agent-message-composition/spec.md` | Modified | Character class as a property; NFC; unit resolution; clause bound; marker position |
| `openspec/changes/composer-agent/design.md` | Modified | §6.2 recording what §6 and §6.1 no longer say, plus two residuals |
| `openspec/changes/composer-agent/tasks.md` | Modified | The content-rule gap recorded as a blocking item on a later PR |

`docs/` and `openspec/specs/` were not touched. Nothing was pushed and no pull
request was opened.

## One thing the repository's own guard caught

Writing the blocking item up put the reviewer's literal exploit string — a
nine-digit Peruvian mobile number — into `tasks.md` and `design.md`, and
`tests/hygiene::test_no_secret_or_personal_data_pattern_is_committed` failed
on it. The check was left alone and the prose was rewritten to describe the
number instead of printing it. Worth recording twice over: the guard works,
and the repository already refuses in *its own files* exactly the pattern the
composed body has no rule against.
