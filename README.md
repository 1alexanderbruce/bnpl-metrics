# bnpl-metrics

A config-driven pipeline for calculating financial metrics from SEC filings, uploaded spreadsheets and PDFs, with LLM-assisted extraction. Metrics are declared in YAML (formula, inputs, sources, sanity limits); the engine resolves the inputs, converts units explicitly, validates the result, and stores it with a full audit trail.

It was built for tracking buy-now-pay-later (BNPL) company metrics such as gross merchandise volume, instalment-product volume, active consumers and total assets, where each company reports differently and some figures have to be derived (for example a US share applied to a global total). The engine itself is generic.

> **No company data or real estimation assumptions are included.** `config/example.yaml` describes a fictional company with placeholder numbers. Real specifications belong in `config/private/` (git-ignored).

## Design principles

- **No silent fallbacks.** A failed extraction fails the metric with a reason. A value is only substituted if the spec declares a `fallback` source with a stated reason, and the result is then labelled `fallback`, never `ok`.
- **Explicit units.** Values are converted using the unit an extractor reports (`million USD` to `billion USD`, `percent` to `ratio`). Magnitudes are never guessed; incompatible units fall through to the next source or fail.
- **Pure calculation.** Arithmetic is separated from I/O. Formulas are evaluated by a small allow-listed parser (no `eval`).
- **Auditable results.** Every result carries its inputs, their sources, the steps taken, and any warnings.
- **Bad refreshes cannot overwrite good data.** The store keeps the latest result and the last good one; readers see stale values flagged.
- **Polite web access.** Honest User-Agent, robots.txt enforced, rate limits, TLS verification on. No spoofing or workarounds.

## How it fits together

```
 documents                      extraction                    calculation              output
 ---------                      ----------                    -----------              ------
 SEC EDGAR filings ──┐
 uploaded .xlsx    ──┼─> excel / pdf / filing resolvers ─┐
 uploaded .pdf     ──┤        │ LLM: ask one figure,     │
 fetched web docs  ──┘        │ must quote evidence      ├─> engine ─> validate ─> ResultStore ─> CLI / dashboard
 value catalog  ──────────────┘                          │   formula    range,       latest +
 (figures extracted earlier)                             │   units      change,      last good
 constants / other metrics ──────────────────────────────┘   fallbacks  AI check     history
```

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[gui,dev]"      # gui = web dashboard; add "selenium" for JavaScript pages
cp .env.example .env             # then edit: LLM_API_KEY, SEC_USER_AGENT
```

Requires Python 3.10+. Any OpenAI-compatible endpoint works (`LLM_BASE_URL`, `LLM_MODEL`, `EMBEDDING_MODEL`). `SEC_USER_AGENT` must contain your contact email; SEC EDGAR requires it.

## Quick start

```bash
bnpl-metrics validate-config --config config/example.yaml

# Spreadsheet-only calculation needs no API key. Name each upload after its key in `files:`.
bnpl-metrics calculate --config config/example.yaml \
    --file historical_financials=./financials.xlsx

bnpl-metrics show                          # stored results, stale values flagged
bnpl-metrics export --output results.csv
bnpl-metrics dashboard                     # http://127.0.0.1:7862
```

Exit codes from `calculate`: `0` all values usable, `1` at least one failed, `2` values need review. Results land in `cache/results.json` (`BNPL_METRICS_CACHE_DIR` to change).

| Command | Purpose |
|---|---|
| `validate-config` | Check a spec file (formulas, units, source parameters, dependency cycles) |
| `calculate` | Compute metrics and store results; `--company`, `--metric`, `--file KEY=PATH`, `--no-save`, `--ai-validate` |
| `extract` | Pull figures out of uploaded documents into the value catalog |
| `fetch` | Download new documents from pages listed under `documents:` (robots.txt enforced) |
| `show` / `export` | Print or write stored results |
| `dashboard` | Web UI: results table, per-metric audit trail, upload-and-calculate |

## Result statuses

| Status | Meaning |
|---|---|
| `ok` | Every input came from a primary source and checks passed |
| `fallback` | Computed, but at least one input used a declared fallback |
| `needs_review` | Computed, but a range/change/AI check raised a concern; not used as the "last good" value |
| `failed` | Could not be computed; the reason is recorded |

## Writing a specification

A metric names its inputs and how to get each one, tried in order:

```yaml
- name: us_gmv
  unit: billion USD
  formula: global_gmv_ttm * us_share
  validation: {min: 0, max: 1000, max_change_ratio: 0.5}
  inputs:
    global_gmv_ttm:
      unit: billion USD
      sources:
        - {type: excel, file: historical_financials, sheet: Operating Metrics,
           row: Gross merchandise volume, aggregate: sum_last_n, n: 4}
    us_share:
      unit: ratio
      sources:
        - {type: filing, forms: [10-K], question: "What share of revenue is from the US?"}
        - {type: constant, value: 0.5, fallback: true, reason: "placeholder if the filing cannot be parsed"}
```

Source types: `constant`, `excel`, `filing`, `pdf`, `catalog`, `metric`. See [`config/example.yaml`](config/example.yaml) for all of them and [`docs/adding-a-company.md`](docs/adding-a-company.md) for a walkthrough.

## Documentation

- [`docs/architecture.md`](docs/architecture.md): layers, data flow, extension points
- [`docs/adding-a-company.md`](docs/adding-a-company.md): writing and testing a specification

## Development

```bash
pytest          # unit and end-to-end tests; no network or API key needed
ruff check .    # lint
```

The test suite includes a hygiene test that fails if internal hostnames, credentials or data files are added to the repository.

## Responsible use

LLM-extracted figures can be wrong. The engine requires a verbatim evidence quote and a confidence level, and flags unverifiable answers, but results are estimates to be reviewed, not authoritative data. Only fetch documents from sites whose terms permit automated access, and keep rate limits conservative. This is not financial advice.
