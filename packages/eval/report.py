"""Render an :class:`EvalReport` to JSON and a self-contained HTML page (addendum §13).

The HTML inlines all styling — no external CDN/font/icon calls, per the self-hosted no-egress
constraint (AGENTS.md / v1.2 §10). It is a build artifact (the eval dashboard view, T3-16, will read
the JSON), so it stays deliberately plain.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from apps.api.schemas.enums import IntentType

from packages.eval.metrics import EvalReport


def write_json(report: EvalReport, path: Path) -> None:
    path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


def _confusion_table(report: EvalReport) -> str:
    intents = [i.value for i in IntentType]
    header = "".join(f"<th>{html.escape(p)}</th>" for p in intents)
    rows = []
    for expected in intents:
        cells = []
        for predicted in intents:
            count = report.confusion_matrix[expected][predicted]
            klass = "diag" if expected == predicted else ("miss" if count else "")
            cells.append(f'<td class="{klass}">{count or ""}</td>')
        rows.append(f"<tr><th>{html.escape(expected)}</th>{''.join(cells)}</tr>")
    return (
        f"<table><thead><tr><th>expected ╲ predicted</th>{header}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _kv_table(title: str, rows: dict[str, float]) -> str:
    body = "".join(f"<tr><th>{html.escape(k)}</th><td>{v:.1%}</td></tr>" for k, v in rows.items())
    return f"<h2>{html.escape(title)}</h2><table><tbody>{body}</tbody></table>"


def _calibration_table(report: EvalReport) -> str:
    rows = "".join(
        f"<tr><th>[{b.lower:.2f}, {b.upper:.2f})</th><td>{b.count}</td>"
        f"<td>{b.mean_confidence:.1%}</td><td>{b.accuracy:.1%}</td></tr>"
        for b in report.calibration
    )
    return (
        "<h2>Confidence calibration</h2>"
        f"<p>Brier score: <strong>{report.brier_score:.4f}</strong> (0 = perfect)</p>"
        "<table><thead><tr><th>confidence range</th><th>n</th>"
        "<th>mean confidence</th><th>actual accuracy</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


_STYLE = """
body { font-family: -apple-system, system-ui, sans-serif; margin: 2rem; color: #1a1a2e; }
h1 { margin-bottom: 0.25rem; }
.headline { font-size: 2rem; font-weight: 700; }
table { border-collapse: collapse; margin: 0.5rem 0 1.5rem; }
th, td { border: 1px solid #d7d7e0; padding: 4px 10px; text-align: right; }
th { background: #f4f4f8; text-align: left; }
td.diag { background: #e6f4ea; font-weight: 600; }
td.miss { background: #fce8e6; font-weight: 600; }
.meta { color: #6b6b80; font-size: 0.9rem; }
"""


def render_html(report: EvalReport) -> str:
    """Build the standalone HTML report (all CSS inlined; no external requests)."""
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>Watcher eval report</title>"
        f"<style>{_STYLE}</style></head><body>"
        "<h1>Watcher classifier eval</h1>"
        f"<p class='meta'>model: <strong>{html.escape(report.model)}</strong> · "
        f"{report.total} examples</p>"
        f"<p class='headline'>{report.overall_intent_accuracy:.1%} "
        "<span class='meta'>overall intent accuracy</span></p>"
        f"<p class='meta'>unclear rate: {report.unclear_rate:.1%}</p>"
        + _kv_table("Per-field accuracy", report.per_field_accuracy)
        + _kv_table("Per-language accuracy", report.per_language_accuracy)
        + _calibration_table(report)
        + "<h2>Intent confusion matrix</h2>"
        + _confusion_table(report)
        + "</body></html>"
    )


def write_html(report: EvalReport, path: Path) -> None:
    path.write_text(render_html(report), encoding="utf-8")
