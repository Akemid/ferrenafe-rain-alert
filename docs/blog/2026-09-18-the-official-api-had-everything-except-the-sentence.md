---
date: 2026-09-18
title: The official API had everything except the one thing the system needed
kind: domain
tags: [ogc, wfs, senamhi, architecture, data-sources]
hook: I went looking for a reason to delete the web scraper. I found a documented OGC service, 1030 layers, clean GeoJSON — and no sentence anywhere in it.
status: raw
---

## What I expected

The most embarrassing dependency in this project is an HTML scraper pointed at
a government website. It breaks when someone changes a `<td>`. Finding out that
SENAMHI runs a public GeoServer felt like finding out the back door had been
unlocked the whole time.

It is genuinely good. `GetCapabilities` returns 1030 feature types. GeoJSON
works. Server-side spatial filtering works. There is a layer named, with no
ambiguity at all, `g_aviso:view_aviso` — "AVISO METEOROLÓGICO NACIONAL".

Its schema:

```
nro_aviso, nivel, fecha_emi, cod_fen, cod_even,
fech_ini, fech_fin, cod_sede, respons, pub, cap, geom
```

Compare that to the domain entity it would feed:

```python
@dataclass(frozen=True, slots=True)
class Warning:
    source_id: str
    title: str
    level: WarningLevel
    region: str
    window: TimeWindow
    emitted_at: datetime
```

`nro_aviso` → `source_id`. `nivel` → `level`. `fecha_emi` → `emitted_at`.
`fech_ini`/`fech_fin` → `window`, explicitly, instead of the value this system
currently *infers*. `geom` → a real polygon instead of a region slug.

Six of seven, and several of them better than what scraping produces.

## What actually happened

The seventh is `title`, and the layer does not have it. Not under another name
— there is no free-text field in the schema at all. The phenomenon arrives as
`cod_fen`, a code. No published layer decodes those codes. The only place they
appear paired with words is a frozen archive of 2020 warnings:

```
aviso270  f_emi=29_12_2020  cod_fen=01  fenomeno=LLUVIA EN SIERRA
aviso150  f_emi=21_07_2020  cod_fen=03  fenomeno=INCREMENTO VIENTO COSTA
```

Meanwhile the HTML page I was trying to delete carries exactly what is missing:

```
019  2026-01-20 → 2026-01-23  47 Hrs.  NARANJA  PRECIPITACIONES EN LA SIERRA (EXTENSIÓN DEL AVISO N016)
```

And `title` is not decoration. It is load-bearing twice: it is quoted to
residents in the message they receive, and it is the input to the
`phenomenon` / `zone` classification that decides whether a warning is
flood-relevant at all — by regex, on words like `COSTA` and `SIERRA`. Delete
the title and the classifier has nothing to classify.

## Why it matters

I had framed the question as "scraping or API", and spent an hour trying to
answer it. It was the wrong question. **Neither source is sufficient alone**:
the API has structure without text, the HTML has text without structure.

The honest end state is both — geometry, window and level from the OGC service,
the sentence from the page. That is more moving parts than I started with, not
fewer, which is the opposite of what "we found the official API" usually
promises.

The second-order lesson is about the shape of the mistake. I did not get a
wrong answer; I got a confident answer to a question that had a false premise
baked into it. No amount of care answering "A or B" recovers from A and B both
being incomplete. The check that saved it was boring: comparing the candidate
source field-by-field against the type it had to produce, instead of against
my impression of what the system needed.

## Why it stays scraped, for now

Three reasons, recorded properly in
[ADR 0001](../decisions/0001-senamhi-source-strategy.md):

1. The live warning layer returns `totalFeatures: 0` outside the rainy season,
   so its populated shape has never been observed. Migrating to a source whose
   working behaviour you have not seen, in a flood-warning system, is the
   assumption this project exists to avoid.
2. The code table would have to be inferred from a six-year-old archive.
3. There is no compliance reason to move. SENAMHI's terms are silent on
   automated access; the real constraint in them is about attribution, and it
   applies identically to both sources.

There is also a thing worth saying out loud: "I found the official API and
chose not to use it yet, and here is the measurement" is a better engineering
story than a migration done because the API existed.
