# Alerta Lluvias Ferreñafe

A local, cloud-free decision engine that watches SENAMHI's official
warnings and the Open-Meteo forecast for Ferreñafe (Lambayeque, Peru) and
decides whether a rain-risk alert should go out. Ferreñafe floods on
rainfall totals that are unremarkable elsewhere, and today nothing warns
the community ahead of time — this project builds and calibrates that
decision logic before any cloud infrastructure exists.

## Disclaimer

This system is a **community complement to SENAMHI and INDECI's official
warnings — it is not a replacement for them.** It is provided **without warranty of any kind**;
always follow official guidance during an emergency.

Warning data is scraped from a public SENAMHI page for non-commercial,
community-safety use. Forecast data comes from the
[Open-Meteo](https://open-meteo.com/) API, used under its non-commercial
terms.

## Status

**Under construction.** The first of three planned changes,
`core-alert-cycle`, is complete. The alert cycle now runs end to end on a
laptop against the live sources, which is what lets the risk thresholds be
calibrated against real data before any cloud resource exists.

Delivered:

- The decision engine. Risk evaluation, deduplication, the source-outage
  rules, and the deterministic Spanish message template, all pure Python.
- Adapters for the two data sources, including a scraper that reports the
  source as unavailable rather than calm when the page structure changes.
- A hazard filter. The official warnings page covers wind, heat and cold as
  well as rain, so warnings are classified before they can drive an alert.
- Local persistence, a console notifier, and a command line entry point.

Not built yet: the message-composer agent, and the deployment with its
scheduler, cloud storage and operator channel. Those are the next two
changes.

**Nothing here can send anything.** No notifier capable of transmitting
exists in the codebase, which is why the command below has no dry-run
option. Offering one would imply a send mode exists.

## Trying it

```bash
uv sync
uv run rain-alert-cycle
```

This runs the full cycle against the live sources and prints the risk
level, the reasons behind it, the warnings the filter discarded, and the
message that would have been sent. It sends nothing.

## Running the tests

```bash
uv run pytest                 # offline, deterministic
uv run pytest -m integration  # opt-in, hits the live sources
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

## How it works

[`docs/architecture/`](./docs/architecture/README.md) explains the design.
Start with the overview, which maps every source file to its purpose and
can be read on its own. From there, one document per layer goes deeper:
the [domain](./docs/architecture/domain.md), the
[ports and use case](./docs/architecture/ports-and-application.md), the
[adapters](./docs/architecture/adapters.md), and the
[entry points and tests](./docs/architecture/entrypoints-and-testing.md).

## License

Apache License 2.0 — see [`LICENSE`](./LICENSE).
