# SENAMHI warning-page fixtures

Pinned HTML for the SENAMHI scraper tests (design.md section 8.2). The default
test suite is offline, so these files — not the network — are what the parser is
developed against.

## Capture

| Field | Value |
|---|---|
| URL | `https://www.senamhi.gob.pe/?dp=lambayeque&p=aviso-meteorologico` |
| Captured | 2026-09-04 |
| Live response then | HTTP 200, ~512 KB, one `<table>`, 790 `<tr>` (1 header + 788 data rows) |
| Header labels | `Aviso`, `Nro.`, `Emisión`, `Inicio`, `Fin`, `Duración`, `Nivel` |

The page is public official content. It contains no personal data.

## Files

| File | What it is | Expected parse result |
|---|---|---|
| `warnings_table.html` | **The real capture**: header row plus the 12 most recent data rows, structure verbatim. Its one row in force is `345 (vigente)`, a RED `INCREMENTO DE TEMPERATURA DIURNA` — a heat warning. | `available`, **zero** warnings: the row in force is not flood-relevant (design.md section 5.1) |
| `precipitation_coast_current.html` | Derived. The in-force row is replaced by an orange `PRECIPITACIONES EN LA COSTA NORTE Y SIERRA`. **Synthetic row**, because no coastal rain aviso was in force on the capture date; structure, CSS classes, link shapes and every other row are verbatim. | `available`, one warning |
| `history_only.html` | Derived. The `(vigente)` marker and the in-force link shape are removed, so every row is historical. Its most recent row still carries dates that contain the test clock, which is what makes it the fixture for "the marker is required, not just the dates". | `available`, zero warnings |
| `empty_table.html` | Derived. Header row present, zero data rows. | `unavailable` / `no_rows_extracted` |
| `broken_structure.html` | Derived. The table is replaced by `<div>`s and the column labels renamed. | `unavailable` / `structure_unrecognized` |

The page has carried warning history since 2024, so a correctly parsed table is
never empty. That is why zero extracted rows means breakage rather than calm —
the distinction the last two fixtures exist to pin.

## Refreshing

Re-capture only when the live structure has actually changed (the
`@pytest.mark.integration` canary in `tests/integration/test_senamhi_canary.py`
is the early warning):

```bash
uv run python - <<'PY'
import httpx, pathlib
from bs4 import BeautifulSoup
page = httpx.get(
    "https://www.senamhi.gob.pe/?dp=lambayeque&p=aviso-meteorologico",
    timeout=20.0, headers={"User-Agent": "Mozilla/5.0"},
)
page.raise_for_status()
table = BeautifulSoup(page.text, "html.parser").find("table")
rows = table.find_all("tr")
kept = [rows[0], *[r for r in rows if r.find_all("td")][:12]]
pathlib.Path("tests/fixtures/senamhi/warnings_table.html").write_text(
    "<table>\n" + "\n".join(str(r) for r in kept) + "\n</table>\n", encoding="utf-8"
)
PY
```

Then re-derive the other four files from it, update the capture date above, and
re-run `uv run pytest tests/unit/adapters`. Do not hand-edit a fixture to make a
test pass: if the live structure changed, the parser is what must change.
