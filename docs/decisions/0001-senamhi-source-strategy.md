# ADR 0001 — Keep scraping SENAMHI's HTML; treat the OGC service as a future complement

- **Status**: Accepted
- **Date**: 2026-09-18
- **Scope**: `adapters/senamhi_scraper.py`, `ports.WarningProvider`
- **Supersedes**: nothing
- **Revisit trigger**: see [When to reopen this](#when-to-reopen-this)

## Context

The system reads official SENAMHI warnings (*avisos meteorológicos*) by scraping
`https://www.senamhi.gob.pe/?dp=<region>&p=aviso-meteorologico`. HTML scraping is
the most brittle dependency in the project: a markup change breaks it silently, and
the adapter must turn free text into a typed `Warning`.

A public OGC/GeoServer endpoint was reported to exist at
`https://idesep.senamhi.gob.pe/geoserver/`, which — if it carried the same warnings —
would replace parsing with a documented machine interface. This ADR records what that
service actually offers, measured rather than assumed, and what we decided.

## Investigation

All findings below were obtained by querying the live service on 2026-09-18.

### The service is real

`GetCapabilities` returns **HTTP 200** and advertises **1030 feature types**.
GeoJSON works via `outputFormat=application/json`.

### The relevant layers

| Layer | Contents | State on 2026-09-18 |
|---|---|---|
| `g_aviso:view_aviso` | `nro_aviso, nivel, fecha_emi, cod_fen, cod_even, fech_ini, fech_fin, cod_sede, respons, pub, cap, geom` | `totalFeatures: 0` |
| `g_aviso:view_aviso_aux` | `iddpto, nombdep, idprov, nombprov` | `totalFeatures: 0` |
| `g_prono_pp_24h:view_aviso24h` | free text (`descripcio`, `recomendac`) + `nivel`, `fecha` | 7 features, live |
| `g_100_01:aviso001..275` | `fenomeno` text + `cod_fen` | frozen **2020** archive |

### Server-side spatial filtering works, and it discriminates

`CQL_FILTER=INTERSECTS(geom, POINT(<lat> <lon>))` cut one response from **1.8 MB to
573 bytes**. The axis order is **lat/lon**. This was triangulated rather than assumed,
because a filter that returns something may be returning everything:

| Query | Result |
|---|---|
| Ferreñafe, lat/lon | 1 feature |
| same point, axes reversed | 0 |
| `POINT(0 0)` (Atlantic) | 0 |
| no filter | 7 |

### Two traps found

**A returned feature does not mean a warning for the town.** In the layer that has
live data, `gid=1` (Nivel 1) has a bounding box of **12.7° × 15.4°** — effectively all
of Peru. Ferreñafe, Lima and Iquitos all match it. Treating "a feature came back" as
"there is a warning here" would fire the alert permanently. Nivel 2 and 3 polygons are
genuinely regional.

**`g_aviso:view_aviso` carries no free text at all** — only `cod_fen` / `cod_even`
codes, and no published layer decodes them. The only place those codes appear paired
with text is the 2020 archive (`01` = rain, `03` = wind, `06` = temperature). Deriving
a code table from a six-year-old frozen archive and relying on it in a flood-warning
system is the kind of assumption this project exists to avoid.

### Why the missing text is decisive

`domain/entities.py::Warning.title` is load-bearing twice:

1. it is quoted to residents in the composed message, and
2. `adapters/senamhi_classification.py` infers `phenomenon` and `zone` from it by
   regex (`\bCOSTA\b`, `\bSIERRA\b`), which drives the flood-relevance decision.

The HTML page carries exactly that text. A sample row as published:

```
019  2026-01-20 → 2026-01-23  47 Hrs.  NARANJA  PRECIPITACIONES EN LA SIERRA (EXTENSIÓN DEL AVISO N016)
```

The OGC service publishes the same event without the sentence.

### No CAP feed exists

The `cap` column in `view_aviso` suggested a Common Alerting Protocol feed, which is
a format designed for redistribution and would carry full text. Probed
`senamhi.gob.pe/cap/` (404), `alerts.senamhi.gob.pe` (DNS does not resolve), and the
WMO register — none published. The column's meaning remains unknown.

### Both sources agree that nothing is active

`view_aviso` returning zero is **not** evidence of a stale service: the HTML listing
also shows no current warning, its most recent entries being from January 2026. This
is consistent with September on the northern coast. It does mean the populated shape
of `view_aviso` **could not be observed**, and is therefore unverified.

## Options considered

1. **Replace scraping with the OGC service.** Rejected: loses `title`, which leaves
   `phenomenon`/`zone` classification without input, and relies on a layer whose
   populated behaviour has never been observed.
2. **Keep scraping only.** Accepted for now — see below.
3. **Use both: OGC for geometry, window and level; HTML for the title.** The likely
   end state, deferred — it cannot be designed responsibly until the populated
   `view_aviso` has been seen.

## Decision

**Keep `senamhi_scraper.py` as the sole `WarningProvider` implementation.** Do not
migrate before the AWS Zero to Shipped submission deadline (2026-10-02).

"Scraping or API" turned out to be a false binary: **neither source is sufficient
alone.** The OGC service has structure without text; the HTML has text without
structure. The eventual design is a complement, not a replacement.

Three reasons, in order of weight:

1. **It is not what stands between this project and shipping.** The hackathon ship
   gate is pass/fail — a live, publicly reachable application on AWS. Changing a
   working data source moves nothing toward that gate.
2. **The replacement cannot be verified yet.** No active warning exists to observe.
3. **There is no compliance problem to solve by migrating.** SENAMHI's Terms and
   Conditions are silent on automated access, and no `robots.txt` exists on either
   host (both 404). The real constraint found in those terms concerns attribution,
   not acquisition, and applies identically to both sources — see
   [ADR 0002](0002-senamhi-attribution-rule.md).

## Consequences

- The most brittle dependency stays. The existing outage handling
  (`SourceResult` → `Unavailable`) remains the mitigation, and it is already tested.
- The system keeps asking about **Lambayeque** (a region slug) rather than about
  **Ferreñafe** (a point). Accepted for now; the geometry filter is the main prize
  of a future migration and is a genuine precision gain for a town-scale system.
- `window` stays inferred rather than read from `fech_ini`/`fech_fin`.
- `ports.WarningProvider` already isolates the domain from this choice, so revisiting
  it later is an adapter-level change, not an architectural one.

## When to reopen this

Reopen when **all** of the following hold:

1. A populated `g_aviso:view_aviso` response has been captured during a real active
   warning — the northern-coast rainy season begins around December.
2. The meaning of `cod_fen` / `cod_even` is confirmed against a live source rather
   than inferred from the 2020 archive.
3. An empty response can be distinguished from a failed one. The current adapter
   separates *available* from *unavailable*, and that distinction drives degraded
   mode. An empty view collapses the two, and collapsing them is worse than
   scraping: the system would report calm while blind.

## Evidence

Reproducible with the endpoint above:

```bash
# capabilities
curl -s "https://idesep.senamhi.gob.pe/geoserver/wfs?service=WFS&version=2.0.0&request=GetCapabilities"

# the warning layer (empty outside the rainy season)
curl -s -G "https://idesep.senamhi.gob.pe/geoserver/g_aviso/wfs" \
  --data-urlencode "service=WFS" --data-urlencode "version=2.0.0" \
  --data-urlencode "request=GetFeature" \
  --data-urlencode "typeNames=g_aviso:view_aviso" \
  --data-urlencode "outputFormat=application/json"

# server-side point filter (note: lat/lon axis order)
curl -s -G "https://idesep.senamhi.gob.pe/geoserver/g_prono_pp_24h/wfs" \
  --data-urlencode "service=WFS" --data-urlencode "version=2.0.0" \
  --data-urlencode "request=GetFeature" \
  --data-urlencode "typeNames=g_prono_pp_24h:view_aviso24h" \
  --data-urlencode "outputFormat=application/json" \
  --data-urlencode "CQL_FILTER=INTERSECTS(geom, POINT(-6.636005 -79.789860))"
```

## Related

- Peru's national open-data platform publishes SENAMHI **station observations** as
  CSV. That is recorded history, not forecast or warning, so it does not serve the
  alert cycle. It is a candidate for calibrating `RiskThresholds` against real
  Ferreñafe history — a separate question from this one.
