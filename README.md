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

**Under construction.** This is change 1 of a three-change rollout
(`core-alert-cycle`). This slice delivers:

- Project tooling (`uv`, `pytest`, `ruff`, `mypy`).
- The hexagonal package skeleton (`domain/`, `ports/`, `application/`,
  `adapters/`, `entrypoints/`) with an automated architecture boundary
  test.
- Public-repository hygiene: this license, this disclaimer, and the
  secret/personal-data exclusion checks below.

No decision logic, HTTP/HTML adapters, or CLI exist yet — those land in
changes 2 and 3 of this rollout. Nothing in this repository sends alerts
or talks to the network today.

## Running the tests

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

## License

Apache License 2.0 — see [`LICENSE`](./LICENSE).
