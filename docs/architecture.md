# Architecture

## Layers

| Layer | Modules | Rule |
|---|---|---|
| Specification | `specs.py` | Parse and validate YAML into frozen dataclasses. Bad specs fail at load time, not mid-run. |
| Calculation | `calculation/formulas.py`, `units.py`, `expr.py` | Pure functions. No I/O, no globals. |
| Engine | `calculation/engine.py` | Resolves inputs through ordered sources, evaluates the formula, runs validators, returns a `MetricResult`. Knows nothing about files or HTTP. |
| Extraction | `extraction/` | Strict parsers (`parsing.py`), chunk retrieval (`retrieval.py`), and `LLMExtractor`, which asks for one figure and checks the answer. |
| Sources | `sources/` | EDGAR, spreadsheets, PDFs, the polite HTTP client, the optional browser, catalogs. `resolvers.py` adapts them to the engine. |
| Persistence | `store.py`, `fileio.py` | Atomic JSON writes; latest + last-good + history per metric. |
| Orchestration | `pipeline.py`, `cli.py` | Wire services together; `extract`, `run_company`. |
| Interface | `apps/dashboard.py` | Handlers are plain functions; Gradio is only the wiring. |

Dependencies point downward: the engine imports specs, units, models and validation, never sources or the store.

## Data flow for one metric

1. The pipeline builds one resolver per source type, bound to the files, extractor, EDGAR client and catalog for this run.
2. For each input the engine tries sources in order. A resolver returns an `ExtractedValue` (value, unit, source, confidence, evidence) or raises `InputError`.
3. The engine converts the value to the input's declared unit. An incompatible unit counts as a failure of that source, so the next source is tried.
4. A source flagged `fallback: true` marks the input as fallback-derived; this propagates to every metric that depends on it.
5. The formula is evaluated with the converted inputs.
6. Validators run: range and change-vs-previous checks (from the spec), plus any extra validators such as `LLMValidator`. Concerns set the status to `needs_review`.
7. The store records the result. Only `ok` and `fallback` results become the "last good" value.

## Why these choices

- **Config over code.** The original implementation had one 200-600 line method per company and metric, mixing extraction, arithmetic and fallbacks. The same behaviour is now a few lines of YAML per metric, and the arithmetic is unit-tested in isolation.
- **Failure is a value.** `MetricResult.status` is `failed` with an `error`; nothing returns `None` or a default. A run summary maps to exit codes so automation notices.
- **Evidence-checked LLM answers.** The extractor requires a verbatim quote that must appear in the retrieved text; otherwise confidence is downgraded to `low` and, by default, the answer is rejected.
- **Retrieval degrades gracefully.** Chunks are ranked by embedding similarity; if the embeddings endpoint fails, keyword scoring is used and a warning is logged.
- **Small expression language.** Formulas allow arithmetic, `min`, `max`, `abs`, `round`, `sum` over named inputs. Anything else is rejected at load time.

## Extension points

- **New source type**: add the name and required parameters to `specs.SOURCE_TYPES` / `_REQUIRED_PARAMS`, and a resolver in `sources/resolvers.py`. The engine needs no change.
- **New validator**: any callable `(MetricSpec, MetricResult, previous) -> list[str]` can be passed to `Engine(validators=[...])`. Raise `ValidatorUnavailable` if it cannot run; that adds a warning without changing status.
- **Different LLM provider**: anything with `complete(system, user)` and `embed(texts)` satisfies the `LLM` protocol in `llm.py`.
- **Different front end**: `Pipeline` is the whole programmatic API; `apps/dashboard.py` is a thin client of it.

## Web access policy

`sources/web.py` is the only place that talks to arbitrary sites. It identifies itself with the configured contact string, fetches and obeys robots.txt (an unreachable robots.txt means "disallowed"), spaces requests per host, retries only transient errors with backoff, and verifies TLS. SEC EDGAR's JSON APIs are accessed with robots checking off, following the SEC's documented fair-access rules (descriptive User-Agent, at most 10 requests per second).
