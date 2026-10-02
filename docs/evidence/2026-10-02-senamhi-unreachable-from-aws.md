# 2026-10-02 — SENAMHI is unreachable from AWS (us-east-2 and sa-east-1)

- **Date**: 2026-10-02
- **Regions**: `us-east-2` (deployment region), `sa-east-1` (comparison)
- **Question**: the scheduled cycle reports SENAMHI as unavailable. Is that
  specific to the HTML page, so that the OGC/GeoServer API from
  [ADR 0001](../decisions/0001-senamhi-source-strategy.md) would get around
  it? Is it specific to the region?

## 1. The symptom, in the deployed cycle's own logs

The log group of `ferrenafe-rain-alert-cycle` in `us-east-2`, filtered for
`senamhi` / `unavailable` (excerpt, real lines):

```
- senamhi: timeout — ConnectTimeout: [Errno 110] Connection timed out
senamhi became unavailable as of 2026-10-02T06:18:03+00:00.
"senamhi_status": "unavailable",
"kind": "source_unavailable",
```

The degraded path behaved as designed. The composed body says:

```
La fuente oficial SENAMHI no estuvo disponible durante esta evaluación; esta
alerta se generó solo con el pronóstico de Open-Meteo.
```

## 2. Probe method

A temporary Lambda function, `ferrenafe-senamhi-probe-tmp` (python3.12, 256 MB).
Its role had a trust policy and **no permissions**, because the result comes
back in the `Invoke` response. Each invocation tests one target:

1. resolve the host (IPv4)
2. open a raw TCP connection to port 443 (5 s timeout)
3. send an HTTPS GET with `urllib` (10 s timeout)

| Target key | URL |
|---|---|
| `egress_ip` | `https://checkip.amazonaws.com/` |
| `control_open_meteo` | `https://api.open-meteo.com/v1/forecast?latitude=-6.636&longitude=-79.79&current=temperature_2m` |
| `scrape_www` | `https://www.senamhi.gob.pe/?dp=lambayeque&p=aviso-meteorologico` |
| `api_view_aviso` | `https://idesep.senamhi.gob.pe/geoserver/g_aviso/wfs?...typeNames=g_aviso:view_aviso&outputFormat=application/json&count=5` |
| `api_view_aviso24h_point` | `https://idesep.senamhi.gob.pe/geoserver/g_prono_pp_24h/wfs?...CQL_FILTER=INTERSECTS(geom, POINT(-6.636005 -79.789860))` |

Open-Meteo is the control. It shows that the function has working egress, so
a failure on the SENAMHI targets is about the destination.

## 3. Results — us-east-2

Egress IP: `3.19.31.231`.

```
control_open_meteo       188.40.99.226  tcp ok 112 ms        http 200, 324 B, 615 ms
scrape_www               190.119.131.3  tcp TimeoutError     http URLError(TimeoutError('timed out')) 10532 ms
api_view_aviso           190.119.131.56 tcp TimeoutError     http URLError(TimeoutError('timed out')) 10386 ms
api_view_aviso24h_point  190.119.131.56 tcp TimeoutError     http URLError(TimeoutError('timed out')) 10277 ms
```

## 4. Results — sa-east-1 (São Paulo)

Egress IP: `18.231.99.28`.

```
control_open_meteo       167.114.211.86 tcp ok 135 ms        http 200, 325 B, 547 ms
scrape_www               190.119.131.3  tcp TimeoutError     http URLError(TimeoutError('timed out')) 10189 ms
api_view_aviso           190.119.131.56 tcp TimeoutError     http URLError(TimeoutError('timed out')) 10224 ms
api_view_aviso24h_point  190.119.131.56 tcp TimeoutError     http URLError(TimeoutError('timed out')) 10219 ms
```

## 5. Results — outside AWS (the developer's machine)

```bash
curl -s -o /dev/null -m 15 -w "%{http_code} %{remote_ip} %{time_total}s\n" "<url>"
```

```
200 190.119.131.3 1.566651s     # www.senamhi.gob.pe, the scrape URL
200 190.119.131.56 1.929807s    # idesep.senamhi.gob.pe, view_aviso
```

## 6. What this shows, and what it does not

- **The failure happens at the network layer, not in HTTP.** The TCP connection
  never completes: there is no RST and no 403. The packets are dropped
  silently, which matches `[Errno 110]` in the cycle.
- **The API does not get around it.** `www` and `idesep` are both in
  `190.119.131.0/24`, and both time out the same way.
- **Changing region does not get around it either.** Two regions with different
  egress ranges show the same result.
- **Not shown:** whether the block covers AWS address space only, cloud
  providers in general, or every non-Peruvian address. The developer's machine
  is one data point outside AWS. It does not tell these cases apart.
- **Not shown:** whether the block is deliberate.

## 7. Incidents during the probe

Recorded because they are reusable, not because they changed the result:

- The `aws-mcp` sandbox rejects `import zipfile`. It also serializes a `bytes`
  `ZipFile` parameter as text, so Lambda answered `Could not unzip uploaded
  file`. The workaround was to build the zip locally, upload it with a
  presigned S3 PUT into a bucket in the same region, and pass
  `Code.S3Bucket/S3Key` instead.
- A synchronous `Invoke` of all five targets in one call exceeded the
  sandbox's roughly 60 s client read timeout. The fix was one target per
  invocation, run in parallel, with shorter per-step timeouts.

## 8. Cleanup (verified)

| Region | Resource | Check | Result |
|---|---|---|---|
| us-east-2 | function `ferrenafe-senamhi-probe-tmp` | `GetFunction` | `ResourceNotFoundException` |
| global | role `ferrenafe-senamhi-probe-tmp-role` | `GetRole` | `NoSuchEntity` |
| us-east-2 | object `tmp-senamhi-probe/probe.zip` in the CDK assets bucket (`cdk-hnb659fds-assets-<account>-us-east-2`) | `ListObjectsV2` prefix | `KeyCount: 0` |
| sa-east-1 | function `ferrenafe-senamhi-probe-tmp` | `GetFunction` | `ResourceNotFoundException` |
| global | role (recreated for sa-east-1) | `GetRole` | `NoSuchEntity` |
| sa-east-1 | temporary bucket `ferrenafe-probe-tmp-f6d2c7f67a` | `HeadBucket` | `404 Not Found` |
