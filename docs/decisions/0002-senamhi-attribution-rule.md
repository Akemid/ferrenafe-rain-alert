# ADR 0002 — Attribute the fact, quote the words, or say nothing

- **Status**: Accepted
- **Date**: 2026-09-18
- **Scope**: `domain/message_validation.py`, `domain/template.py`, `adapters/agent_composer.py`
- **Blocks**: the pull request that first wires an agent-composed message into a live cycle

## Context

SENAMHI's published Terms and Conditions contain this clause:

> "Queda prohibido que la información a la que se accede pueda ser modificada en su
> contenido para luego ser presentada como información oficial emitida por el Servicio
> Nacional…"

and, in the same section, forbids presenting altered information
*"de forma que parezca que esta es información oficial del Servicio"*.

Change 2 (`composer-agent`) puts a language model in the message path, whose job is to
rephrase. That is the exact shape the clause names, so the clause becomes a design
constraint rather than a footnote.

Two readings were considered and one is wrong:

- **Wrong**: "we must not name SENAMHI." This over-corrects. The clause is a
  conjunction — *modified* **and then** *presented as official*. Naming the agency is
  not what it forbids, and dropping the name would strip the alert of the authority
  that makes a resident act on it.
- **Correct**: the prohibition is on putting our words in their mouth.

This is orthogonal to how the data is acquired. Reading the OGC service instead of
scraping HTML changes nothing here — see [ADR 0001](0001-senamhi-source-strategy.md).

## Decision

Three categories, only the third of which is forbidden:

| Shape | Example | Allowed |
|---|---|---|
| A **fact** about the agency | "SENAMHI issued an orange warning for the area." | yes |
| A **verbatim quote**, attributed | `Aviso oficial del SENAMHI, nivel naranja: <exact title>` | yes |
| A **paraphrase wearing the attribution** | `Aviso oficial del SENAMHI: it will rain hard this afternoon` | **no** |

Stated as the rule the validator must enforce:

> If the body attributes anything to SENAMHI, what is attributed must be either a
> structured fact the domain itself produced, or a complete and unaltered quotation of
> the warning title. Nothing else.

This is decidable from the `MessageRequest` alone — the domain holds both the
structured level and the original title — so it is enforced deterministically in
`domain/message_validation.py`. It is **not** delegated to prompt wording: asking the
model to behave is not a control, and `adapters/agent_prompt.py` already says so.

Additionally, the message carries an **explicit authorship line**. The current title
is `Alerta de lluvias — <city> — <level>`, which does not say who is sending it; a
resident cannot tell a community project from a government bulletin. Naming the sender
removes the ambiguity the clause is concerned with, at the cost of one line.

## Consequences

- The agent keeps full freedom where its value is: the surrounding prose, the
  transitions, how the risk is explained, how the checklist is introduced.
- The agent has **no** freedom over quoted material. If it alters the title, the
  attribution must not survive — the fallback to the deterministic template is the
  existing mechanism for that.
- The level shown to residents comes from `WarningReason.level`, structured domain
  data, never from model prose. This is already true in `domain/template.py` and must
  stay true.
- `domain/template.py` already satisfies this rule: it sanitizes the title and quotes
  it under an explicit attribution, and it states plainly when no official warning
  exists (*"No hay un aviso oficial vigente del SENAMHI para la zona; esta alerta se
  basa únicamente en el pronóstico del tiempo."*). What is missing is that the
  **validator does not yet require it**, so the agent can break it.

## Acceptance criteria for the blocking pull request

1. A body that attributes a non-verbatim sentence to SENAMHI is rejected.
2. A body that quotes the title verbatim under attribution is accepted.
3. A body that states the structured fact without quoting is accepted.
4. A body that drops the attribution and rephrases freely is accepted.
5. A partial or truncated quotation under attribution is rejected — the existing
   verbatim-quote exemption already requires whole quotes; this extends it from
   numeric provenance to attributed text.
6. The composed message names its sender.

## Notes

This is an engineering constraint derived from a published terms document; it is not
legal advice. The terms are silent on automated access, and no `robots.txt` exists on
`senamhi.gob.pe` (404) — acquisition was not the risk. Attribution is.

## Source

[Términos y Condiciones de Uso y Privacidad — SENAMHI](https://www.senamhi.gob.pe/?p=terminos-condiciones)
