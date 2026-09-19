---
date: 2026-09-18
title: Deleting a value from the enum kept all 22 tests green
kind: process
tags: [testing, mutation-testing, code-review, tdd]
hook: The output model accepts three alert levels. The test suite only ever tried one of them. Removing either of the others changed nothing — the suite stayed green and the town would have stopped getting PREPARE alerts.
status: raw
---

## What I expected

The deployment unit's output contract is a Pydantic model with a closed enum:

```python
level: Literal["none", "prepare", "imminent"]
```

Twenty-two tests around it: a complete payload accepted, every required field
tested for absence, a naive timestamp rejected, an unrequested field rejected,
JSON round-trip pinned. It looked thorough, and I had written it under TDD.

## What actually happened

A review agent reported it, and — this is the part that made it credible —
proved it instead of arguing it. It copied the project to a scratch directory,
deleted `"prepare"` from the literal, and reran the suite.

All 22 tests passed.

Every fixture used `"imminent"`. The only rejection case was `"catastrophic"`.
Two of the three valid values were never once supplied to the model. The enum
was not tested; one member of it was tested, and the other two were decoration.

I checked the same way before fixing it:

```bash
grep -rn 'prepare\|"none"' agent/tests/
# no matches
```

## Why it matters

Trace the consequence. The model refuses a `"prepare"` draft. The composer's
fallback catches the refusal and sends the deterministic template instead. The
resident still gets an alert — the fallback is what makes this survivable — but
the agent silently stops working at one of the two levels it exists for, and
nothing anywhere reports it. Green CI, green suite, a capability that quietly
evaporated.

The general shape: **an enum is only as tested as its least-used member.** A
parametrized rejection case feels like coverage of the type, and it is not —
it covers the boundary, not the interior. The fix is one line:

```python
@pytest.mark.parametrize("level", ["none", "prepare", "imminent"])
def test_every_level_the_domain_can_ask_for_is_accepted(level: str) -> None:
    assert CompositionOutput.model_validate({**COMPLETE, "level": level}).level == level
```

## The part about review

Three findings came out of that pass. All three were delivered the same way:
here is the mutation, here is the suite still green. I re-ran each one myself
before fixing it, and after fixing, confirmed the mutation now fails:

```
MUTATION 1: drop "prepare" from the level literal
  FAILED tests/test_models.py::test_every_level_the_domain_can_ask_for_is_accepted[prepare]

MUTATION 2: remove the Mapping guard
  6 FAILED

MUTATION 3: build a fresh agent per invocation instead of reusing AGENT
  FAILED tests/test_app.py::test_the_entrypoint_composes_with_the_agent_built_once_at_import
```

A review finding stated as an opinion costs a conversation. The same finding
stated as a surviving mutation costs nothing to verify and cannot be argued
with. It is worth asking for reviews in that currency.

## A smaller one from the same pass

The third mutation is its own little lesson. A module-level constant existed
specifically so that a deploy which cannot build the model fails at import
rather than on the first alert of the season. Nothing tested that the
entrypoint *used* it. Rewriting the entrypoint to construct a fresh agent per
call kept the suite green — which is precisely the failure the constant was
written to prevent.

Constants that exist for a timing property need a test for that property, or
they are a comment.
