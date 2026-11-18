"""Results dashboard.

Handler logic lives in ``Dashboard`` and the module functions so it can be tested without a
server; ``build_app`` only wires it to Gradio.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from bnpl_metrics.cli import build_pipeline, format_summary
from bnpl_metrics.errors import InputError
from bnpl_metrics.models import MetricResult
from bnpl_metrics.pipeline import Pipeline
from bnpl_metrics.settings import Settings
from bnpl_metrics.specs import CompanySpec, load_specs

TABLE_COLUMNS = ["Company", "Metric", "Value", "Unit", "Status", "Updated"]


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:,.4g}"


def results_table(pipeline: Pipeline) -> pd.DataFrame:
    """One row per configured metric. Stale or failed values are labelled, never hidden."""
    rows = []
    for company in pipeline.companies.values():
        for metric in company.metrics:
            shown = pipeline.store.display(company.id, metric.name)
            if shown is None:
                rows.append(
                    [company.id, metric.display_name, "—", metric.unit, "not calculated", ""]
                )
                continue
            status = f"{shown.status} (STALE)" if shown.stale else shown.status
            rows.append(
                [
                    company.id,
                    metric.display_name,
                    _fmt(shown.value),
                    shown.unit or metric.unit,
                    status,
                    shown.as_of or "",
                ]
            )
    return pd.DataFrame(rows, columns=TABLE_COLUMNS)


def _result_section(title: str, result: MetricResult) -> list[str]:
    lines = [f"**{title}**: {_fmt(result.value)} {result.unit} — status `{result.status.value}`"]
    if result.error:
        lines.append(f"- error: {result.error}")
    lines.extend(f"- warning: {w}" for w in result.warnings)
    if result.steps:
        lines.append("\n| Step | Value | Source |\n|---|---|---|")
        for step in result.steps:
            value = _fmt(step.value) if step.value is not None else ""
            lines.append(f"| {step.description} | {value} | {step.source or ''} |")
    lines.append(f"\n_computed {result.computed_at}_")
    return lines


def metric_detail(pipeline: Pipeline, company_id: str, metric_name: str) -> str:
    company = pipeline.company(company_id)
    spec = company.metric(metric_name)
    lines = [f"### {spec.display_name}", f"`{spec.formula}`"]
    if spec.description:
        lines.append(spec.description)
    latest = pipeline.store.latest(company_id, metric_name)
    if latest is None:
        return "\n\n".join([*lines, "_Not calculated yet._"])
    lines.extend(_result_section("Latest run", latest))
    if not latest.usable:
        previous = pipeline.store.previous(company_id).get(metric_name)
        if previous is not None:
            lines.extend(_result_section("Last good value (stale)", previous))
    return "\n\n".join(lines)


def match_uploads(
    company: CompanySpec, uploads: Sequence[Any]
) -> tuple[dict[str, Path], list[str]]:
    """Map uploaded files to the keys a company declares under ``files``.

    A file is matched by name: ``historical_financials.xlsx`` supplies the key
    ``historical_financials``. Returns the matched files and the names left unmatched.
    """
    declared = {key.lower(): key for key in company.files}
    files: dict[str, Path] = {}
    unmatched: list[str] = []
    for upload in uploads or []:
        # Gradio passes file objects whose .name is the full path; Path/str are used as-is.
        path = Path(upload if isinstance(upload, (str, os.PathLike)) else upload.name)
        key = declared.get(path.stem.lower())
        if key is None:
            unmatched.append(path.name)
        else:
            files[key] = path
    return files, unmatched


class Dashboard:
    def __init__(self, pipeline: Pipeline) -> None:
        self.pipeline = pipeline

    @property
    def company_ids(self) -> list[str]:
        return list(self.pipeline.companies)

    def metric_names(self, company_id: str) -> list[str]:
        return [m.name for m in self.pipeline.company(company_id).metrics]

    def upload_hint(self, company_id: str) -> str:
        files = self.pipeline.company(company_id).files
        if not files:
            return "This company needs no uploaded files."
        lines = ["Name each upload after its key (the extension does not matter):"]
        lines.extend(f"- `{key}`: {description}" for key, description in files.items())
        return "\n".join(lines)

    def calculate(self, company_id: str, uploads: Sequence[Any]) -> tuple[str, pd.DataFrame]:
        company = self.pipeline.company(company_id)
        files, unmatched = match_uploads(company, uploads)
        messages = []
        if unmatched:
            messages.append(f"Ignored uploads that match no declared key: {', '.join(unmatched)}")
        extraction = self.pipeline.extract(company_id, files) if company.extractions else {}
        for key, problem in extraction.items():
            if problem:
                messages.append(f"Extraction '{key}' failed: {problem}")
        summary = self.pipeline.run_company(company_id, files)
        messages.append(format_summary(summary))
        return "\n".join(messages), results_table(self.pipeline)


def build_app(dashboard: Dashboard):
    import gradio as gr

    pipeline = dashboard.pipeline
    first = dashboard.company_ids[0]

    def show_detail(company_id: str, metric: str) -> str:
        if not company_id or not metric:
            return ""
        try:
            return metric_detail(pipeline, company_id, metric)
        except (InputError, KeyError) as exc:
            return f"Cannot show this metric: {exc}"

    def on_company_change(company_id: str):
        names = dashboard.metric_names(company_id)
        return gr.Dropdown.update(choices=names, value=names[0] if names else None)

    def run(company_id: str, uploads):
        try:
            log, table = dashboard.calculate(company_id, uploads)
        except InputError as exc:
            return f"Error: {exc}", results_table(pipeline)
        return f"```\n{log}\n```", table

    with gr.Blocks(title="BNPL Metrics", theme=gr.themes.Soft()) as app:
        gr.Markdown("# BNPL Metrics")
        gr.Markdown(
            "Values are estimates built from filings and uploaded documents. Anything marked "
            "**fallback**, **needs_review** or **STALE** needs a human look before it is relied on."
        )
        with gr.Tab("Results"):
            table = gr.Dataframe(value=results_table(pipeline), interactive=False, wrap=True)
            gr.Button("Refresh").click(lambda: results_table(pipeline), outputs=table)
            with gr.Row():
                company = gr.Dropdown(dashboard.company_ids, value=first, label="Company")
                metric = gr.Dropdown(
                    dashboard.metric_names(first),
                    value=(dashboard.metric_names(first) or [None])[0],
                    label="Metric",
                )
            detail = gr.Markdown(latex_delimiters=[])  # keep "$" in amounts from rendering as math
            company.change(on_company_change, company, metric)
            metric.change(show_detail, [company, metric], detail)
        with gr.Tab("Calculate"):
            run_company = gr.Dropdown(dashboard.company_ids, value=first, label="Company")
            hint = gr.Markdown(dashboard.upload_hint(first))
            run_company.change(dashboard.upload_hint, run_company, hint)
            uploads = gr.File(file_count="multiple", label="Documents")
            output = gr.Markdown(latex_delimiters=[])
            gr.Button("Calculate", variant="primary").click(
                run, [run_company, uploads], [output, table]
            )
    return app


def launch(settings: Settings, config_path: str) -> None:
    os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
    os.environ["NO_PROXY"] = ",".join(
        filter(None, [os.environ.get("NO_PROXY"), "localhost", "127.0.0.1"])
    )
    pipeline = build_pipeline(settings, load_specs(config_path))
    app = build_app(Dashboard(pipeline))
    app.queue().launch(
        server_name="127.0.0.1",
        server_port=settings.port,
        share=settings.share,
        show_error=True,
    )
