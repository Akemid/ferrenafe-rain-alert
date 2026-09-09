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
