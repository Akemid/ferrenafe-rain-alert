---
date: 2026-09-18
title: A spatial filter that returned a result was matching all of Peru
kind: technical
tags: [geospatial, verification, ogc, wfs, senamhi]
hook: The query came back with exactly one warning for Ferreñafe. It also came back with exactly one for Lima, and one for Iquitos — the same one.
status: raw
---

## What I expected

SENAMHI publishes its warnings through a public GeoServer. That means I could
stop asking "are there warnings for the *region* of Lambayeque?" and start
asking the question the system actually cares about: "is there a warning
polygon containing *this point*?"

The filter worked on the first try:

```
CQL_FILTER=INTERSECTS(geom, POINT(-6.636005 -79.789860))
```

Response size dropped from **1.8 MB to 573 bytes**, and it returned one
feature. A town-scale system asking a town-scale question. Done.

## What actually happened

Before believing it, I checked whether the filter discriminated at all — a
filter that returns something may be returning everything.

| Query | Result |
|---|---|
| Ferreñafe, lat/lon | 1 feature |
| same point, axes reversed | 0 |
| `POINT(0 0)`, mid-Atlantic | 0 |
| no filter | 7 |

That looked healthy. Axis order is lat/lon, a point in the ocean matches
nothing. So I ran two more points out of curiosity:

```
Iquitos  → 1 feature, gid=1
Lima     → 1 feature, gid=1
Ferreñafe→ 1 feature, gid=1
```

Three cities, 1,500 km apart, all matching the same polygon. I measured its
bounding box:

```
gid=1  Nivel 1  bbox lon[-81.33, -68.65] lat[-17.18, -1.75]  span 12.7° x 15.4°
```

That is not a warning area. That is Peru. The Nivel 1 advisory is a national
blanket; only the Nivel 2 and Nivel 3 polygons are regional.

## Why it matters

The naive implementation — `if response.features: raise_alert()` — would have
fired on every single cycle, forever. Not with a bug anyone could see in a
diff: the code would be correct, the query valid, the data real.

The failure mode is the one that actually kills an early-warning system. It
does not crash. It cries wolf until the town stops reading the messages, and
then a real flood arrives to an audience that has learned to ignore it. The
system would still be green in CI on the day it mattered.

Two checks separate "my filter works" from "my filter works":

1. Does it return **nothing** when it should? (the reversed axes, the ocean)
2. Does it return **something different** for somewhere else?

The first is the one everybody remembers. The second is the one that caught
this.

## Evidence

```bash
# one feature for Ferreñafe
curl -s -G "https://idesep.senamhi.gob.pe/geoserver/g_prono_pp_24h/wfs" \
  --data-urlencode "service=WFS" --data-urlencode "version=2.0.0" \
  --data-urlencode "request=GetFeature" \
  --data-urlencode "typeNames=g_prono_pp_24h:view_aviso24h" \
  --data-urlencode "outputFormat=application/json" \
  --data-urlencode "propertyName=gid,nivel,fecha" \
  --data-urlencode "CQL_FILTER=INTERSECTS(geom, POINT(-6.636005 -79.789860))"

# the same one feature for Lima, 750 km south
#   ...POINT(-12.05 -77.04)
# and for Iquitos, across the Andes
#   ...POINT(-3.75 -73.25)
```

Recorded as part of [ADR 0001](../decisions/0001-senamhi-source-strategy.md).
