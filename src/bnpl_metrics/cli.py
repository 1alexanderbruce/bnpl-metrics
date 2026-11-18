"""Command-line interface: ``bnpl-metrics <command>``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from bnpl_metrics.errors import InputError
from bnpl_metrics.extraction.llm_extractor import LLMExtractor
from bnpl_metrics.logging_utils import configure_logging, get_logger
from bnpl_metrics.pipeline import Pipeline, RunSummary
from bnpl_metrics.settings import ConfigError, Settings
from bnpl_metrics.sources.catalog import ValueCatalog
from bnpl_metrics.sources.edgar import EdgarClient
from bnpl_metrics.specs import CompanySpec, SpecError, load_specs
from bnpl_metrics.store import ResultStore
from bnpl_metrics.validation import LLMValidator

log = get_logger(__name__)
DEFAULT_CONFIG = "config/example.yaml"


def _parse_files(pairs: Sequence[str]) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for pair in pairs:
        key, separator, path = pair.partition("=")
        if not separator or not key or not path:
            raise InputError(f"--file expects KEY=PATH, got {pair!r}")
        files[key] = Path(path)
    return files


def build_pipeline(
    settings: Settings, companies: list[CompanySpec], ai_validate: bool = False
) -> Pipeline:
    """Wire up services. The LLM and EDGAR are optional: spreadsheet, constant and catalog
    sources work without them."""
    store = ResultStore(settings.cache_dir / "results.json")
    catalog = ValueCatalog(settings.cache_dir / "values.json")
    extractor = None
    validators = []
    if settings.api_key:
        from bnpl_metrics.llm import OpenAICompatibleLLM

        llm = OpenAICompatibleLLM(settings)
        extractor = LLMExtractor(llm)
        if ai_validate:
            validators.append(LLMValidator(llm))
    elif ai_validate:
        raise ConfigError("--ai-validate needs LLM_API_KEY")
    edgar = EdgarClient.from_settings(settings) if settings.has_real_user_agent else None
    return Pipeline(
        companies, store, catalog, extractor=extractor, edgar=edgar, validators=validators
    )


def format_summary(summary: RunSummary) -> str:
    lines = [f"{summary.company_id}"]
    for name, result in summary.results.items():
        value = "n/a" if result.value is None else f"{result.value:,.4g} {result.unit}"
        lines.append(f"  {name:<28} {result.status.value:<13} {value}")
        for warning in result.warnings:
            lines.append(f"      warning: {warning}")
        if result.error:
            lines.append(f"      error: {result.error}")
    counts = ", ".join(f"{n} {s.value}" for s, n in sorted(summary.counts.items(), key=str))
    lines.append(f"  -> {counts}")
    return "\n".join(lines)


def cmd_validate(args: argparse.Namespace, settings: Settings) -> int:
    companies = load_specs(args.config)
    for company in companies:
        print(
            f"{company.id}: {len(company.metrics)} metrics, {len(company.extractions)} extractions"
        )
    return 0


def cmd_calculate(args: argparse.Namespace, settings: Settings) -> int:
    companies = load_specs(args.config)
    pipeline = build_pipeline(settings, companies, ai_validate=args.ai_validate)
    selected = [args.company] if args.company else [c.id for c in companies]
    worst = 0
    for company_id in selected:
        summary = pipeline.run_company(
            company_id,
            files=_parse_files(args.file),
            only=set(args.metric) or None,
            persist=not args.no_save,
        )
        print(format_summary(summary))
        worst = max(worst, summary.exit_code)
    return worst


def cmd_extract(args: argparse.Namespace, settings: Settings) -> int:
    pipeline = build_pipeline(settings, load_specs(args.config))
    outcome = pipeline.extract(args.company, _parse_files(args.file))
    for key, problem in outcome.items():
        print(f"  {key}: {'ok' if problem is None else 'FAILED - ' + problem}")
    return 1 if any(problem for problem in outcome.values()) else 0


def cmd_fetch(args: argparse.Namespace, settings: Settings) -> int:
    from bnpl_metrics.sources.catalog import DocumentCatalog
    from bnpl_metrics.sources.documents import fetch_new_documents
    from bnpl_metrics.sources.web import PoliteSession

    companies = {c.id: c for c in load_specs(args.config)}
    if args.company not in companies:
        raise InputError(f"unknown company '{args.company}'; configured: {sorted(companies)}")
    company = companies[args.company]
    if not company.documents:
        print(f"{company.id} has no document sources configured")
        return 0
    session = PoliteSession(
        settings.require_user_agent(), proxies=settings.proxies, min_delay=args.delay
    )
    directory = settings.data_dir / company.id
    catalog = DocumentCatalog(directory / "documents.json")
    browser = None
    try:
        for source in company.documents:
            if source.javascript and browser is None:
                from bnpl_metrics.sources.browser import BrowserSession

                browser = BrowserSession(session)
            saved = fetch_new_documents(source, session, catalog, directory, browser)
            print(f"{source.id}: {len(saved)} new document(s)")
            for path in saved:
                print(f"  {path}")
    finally:
        if browser is not None:
            browser.close()
    return 0


def cmd_show(args: argparse.Namespace, settings: Settings) -> int:
    store = ResultStore(settings.cache_dir / "results.json")
    if not store.company_ids():
        print("no results yet; run 'bnpl-metrics calculate'")
        return 0
    for company_id in store.company_ids():
        print(f"{company_id} ({store.company_name(company_id)})")
        for metric in store.metric_names(company_id):
            shown = store.display(company_id, metric)
            if shown is None:
                continue
            value = "n/a" if shown.value is None else f"{shown.value:,.4g} {shown.unit}"
            flag = "  [STALE]" if shown.stale else ""
            print(f"  {metric:<28} {shown.status:<13} {value}{flag}")
            if shown.note:
                print(f"      {shown.note}")
    return 0


def cmd_export(args: argparse.Namespace, settings: Settings) -> int:
    ResultStore(settings.cache_dir / "results.json").export_csv(args.output)
    print(f"wrote {args.output}")
    return 0


def cmd_dashboard(args: argparse.Namespace, settings: Settings) -> int:
    from bnpl_metrics.apps.dashboard import launch

    launch(settings, args.config)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bnpl-metrics", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    commands = parser.add_subparsers(dest="command", required=True)

    def command(name: str, handler, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text)
        sub.set_defaults(handler=handler)
        sub.add_argument(
            "--config",
            default=os.getenv("BNPL_METRICS_CONFIG", DEFAULT_CONFIG),
            help=f"metric specification file (default: {DEFAULT_CONFIG})",
        )
        return sub

    command("validate-config", cmd_validate, "check a specification file")

    calc = command("calculate", cmd_calculate, "calculate metrics and store the results")
    calc.add_argument("--company", help="company id (default: all)")
    calc.add_argument(
        "--metric", action="append", default=[], help="limit to a metric (repeatable)"
    )
    calc.add_argument(
        "--file",
        action="append",
        default=[],
        metavar="KEY=PATH",
        help="uploaded file for a key declared under 'files' (repeatable)",
    )
    calc.add_argument("--no-save", action="store_true", help="do not write results")
    calc.add_argument(
        "--ai-validate",
        action="store_true",
        help="also ask the model to sanity-check each value (can only add concerns)",
    )

    extract = command("extract", cmd_extract, "extract figures from documents into the catalog")
    extract.add_argument("--company", required=True)
    extract.add_argument("--file", action="append", default=[], metavar="KEY=PATH")

    fetch = command("fetch", cmd_fetch, "download new documents from configured pages")
    fetch.add_argument("--company", required=True)
    fetch.add_argument(
        "--delay", type=float, default=2.0, help="minimum seconds between requests to a host"
    )

    command("show", cmd_show, "print stored results")

    export = command("export", cmd_export, "write stored results to CSV")
    export.add_argument("--output", required=True)

    command("dashboard", cmd_dashboard, "launch the web dashboard")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(logging.DEBUG if args.verbose else logging.INFO)
    try:
        settings = Settings.from_env()
        return args.handler(args, settings)
    except (InputError, SpecError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
