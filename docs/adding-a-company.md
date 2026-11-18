# Adding a company

Put your own specification in `config/private/` (git-ignored) and point the CLI at it:

```bash
bnpl-metrics validate-config --config config/private/metrics.yaml
```

## 1. Declare the company and its files

```yaml
companies:
  - id: ACME                # used in results and as the SEC ticker unless `ticker:` is given
    name: Acme Payments
    files:                  # uploads the metrics can read, keyed by a short name
      quarterly_financials: Quarterly financials workbook (.xlsx)
      investor_deck: Latest investor presentation (.pdf)
```

Upload files by key: `--file quarterly_financials=./acme.xlsx`, or in the dashboard by naming the file after its key.

## 2. Write each metric

For every metric decide: what is the formula, what are its inputs, where does each input come from, and what range is plausible.

- Prefer a **primary source first** (spreadsheet row, filing figure) and add a `fallback: true` constant only when a missing value should not block the metric. A fallback must carry a `reason`, and the result will be labelled `fallback`.
- Give **every input a unit**. Sources that report a unit (the LLM does) are converted to it; spreadsheet and constant sources are assumed to already be in it, or you can set `unit:` on the source.
- Chain metrics with `{type: metric, ref: other_metric}`. Cycles are rejected at load time.
- Set `validation: {min, max, max_change_ratio}` so implausible values and big jumps are flagged `needs_review` instead of stored as current.

## 3. Choose sources

| You have | Use |
|---|---|
| A row in a spreadsheet | `excel` with `row` and `aggregate` (`latest`, `sum_last_n`, `mean_last_n`). Row labels must match exactly one row. |
| A messy sheet layout | `excel` with `question` instead of `row` (an LLM reads the sheet) |
| A figure in a filing | `filing` with `forms`, `question`, optional `max_age_days` and `min_confidence` |
| A figure in an uploaded PDF | `pdf` with `file` and `question` |
| A figure extracted earlier | `extractions:` entry plus a `catalog` source |
| An explicit assumption | `constant` (no `reason` needed unless it is a fallback) |

Write questions so the model must quote the figure: "What was total revenue for the year? Give the number and its unit."

## 4. Pull figures from documents

```yaml
    extractions:
      - key: total_assets
        file: investor_deck
        question: What are total assets? Give the number and its unit.
        unit: billion USD      # only used if the model does not report one
```

```bash
bnpl-metrics extract --company ACME --file investor_deck=./deck.pdf
```

Extracted values are stored with their source document and read by `catalog` sources.

## 5. Optionally fetch documents automatically

```yaml
    documents:
      - id: reports
        index_url: https://investors.acme.example/reports
        link_pattern: "annual.*\\.pdf"
        max_documents: 3
```

Only do this for pages whose terms allow automated access. `bnpl-metrics fetch --company ACME` checks robots.txt, rate-limits requests and skips documents it already has.

## 6. Test it

1. `validate-config` catches typos, unknown inputs, bad units and cycles.
2. Run `calculate` with `--no-save` and read the steps in the output.
3. Add a test with fake resolvers (see `tests/test_engine.py`) for any metric whose arithmetic is non-trivial. The engine takes plain callables, so no network or API key is needed.
