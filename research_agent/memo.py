"""Builds the markdown memo.

Division of labour:
- Python writes every number: tables, criteria results, flags, footnotes.
- The model writes only prose (summary, findings, risks, open questions).
  When it wants to mention a figure it writes a placeholder such as
  {{operating_margin_FY2025}}, which Python replaces with the exact value
  and a footnote. `find_typed_numbers` rejects prose in which the model
  typed a number itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .criteria import FAIL, NOT_ASSESSED, PASS, CriterionResult, DeepDiveFlag
from .facts import CompanyData, Fact
from .metrics import Metric, MetricSet

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
# Digits the model may write: years (2025, FY2025) and form names (10-K, 10-Q).
_ALLOWED_DIGITS = re.compile(r"\bFY(19|20)\d{2}\b|\b(19|20)\d{2}\b|\b10-[KQ]\b|\b8-K\b")


# ------------------------------------------------------------------ formatting

def fmt(metric: Metric) -> str:
    v, u = metric.value, metric.unit
    if v is None:
        return "n/a"
    if u == "USD":
        sign = "-" if v < 0 else ""
        if abs(v) < 1e6:            # share price and other small amounts
            return f"{sign}${abs(v):,.2f}"
        m = abs(v) / 1e6
        return f"{sign}${m:,.0f}m" if v % 1e6 == 0 else f"{sign}${m:,.1f}m"
    if u == "shares":
        return f"{v / 1e6:,.1f}m"
    if u == "%":
        return f"{v:.1f}%"
    if u == "pp":
        return f"{v:+.1f} pp"
    if u == "x":
        return f"{v:.2f}x"
    if u == "years":
        return f"{v:.0f}"
    return f"{v:,.2f}"


def _fact_short(fact: Fact) -> str:
    return (f"{fact.label} FY{fact.fiscal_year} = {fact.value:,.0f} {fact.unit} · `{fact.tag}` · "
            f"{fact.form} filed {fact.filed} · [{fact.accession}]({fact.url})")


def _year(m: Metric) -> str:
    return f" FY{m.fiscal_year}" if m.fiscal_year else ""


# ------------------------------------------------------------------ footnotes

@dataclass
class Footnotes:
    """One footnote per cited figure, each a single line.

    A reported figure's footnote names the XBRL tag and links the filing.
    A calculated figure's footnote shows the formula and cites its inputs'
    footnotes, so the trail can be followed step by step down to the filings.
    """
    order: list[str] = field(default_factory=list)
    text: dict[str, str] = field(default_factory=dict)

    def cite(self, metric: Metric) -> str:
        key = metric.id
        if key not in self.text:
            self.order.append(key)
            self.text[key] = ""                 # reserve the number before citing inputs
            self.text[key] = self._explain(metric)
        return f"[^{self.order.index(key) + 1}]"

    def _explain(self, metric: Metric) -> str:
        child_metrics = [m for m in metric.inputs if isinstance(m, Metric)]
        sources = [s for s in metric.inputs if not isinstance(s, Metric)]
        parts: list[str] = []
        if child_metrics:
            inputs = "; ".join(f"{m.label}{_year(m)} {fmt(m)}{self.cite(m)}" for m in child_metrics)
            parts.append(f"{metric.label}{_year(metric)} = {metric.formula} = **{fmt(metric)}**. Inputs: {inputs}")
        for s in sources:
            parts.append(_fact_short(s) if isinstance(s, Fact) else s.citation())
        if metric.note:
            parts.append(f"Note: {metric.note}")
        if not parts:
            parts.append(f"{metric.label}{_year(metric)}: not available.")
        return " ".join(p if p.endswith(".") or p.endswith(")") else p + "." for p in parts)

    def render(self) -> str:
        return "\n".join(f"[^{i + 1}]: {self.text[k]}" for i, k in enumerate(self.order))


# ------------------------------------------------------------ placeholder logic

def figure_catalog(metrics: MetricSet, market_cap: Metric | None) -> dict[str, Metric]:
    """All figures the model may reference, by id."""
    catalog = {m.id: m for m in metrics.all()}
    if market_cap is not None:
        catalog[market_cap.id] = market_cap
    return catalog


def find_typed_numbers(text: str) -> list[str]:
    """Numbers in model prose that did not come through a placeholder."""
    stripped = PLACEHOLDER.sub("", text)
    stripped = _ALLOWED_DIGITS.sub("", stripped)
    return re.findall(r"\d[\d,.]*%?", stripped)


def unknown_placeholders(text: str, catalog: dict[str, Metric]) -> list[str]:
    return [p for p in PLACEHOLDER.findall(text) if p not in catalog]


def fill_placeholders(text: str, catalog: dict[str, Metric], notes: Footnotes) -> str:
    def repl(match: re.Match) -> str:
        metric = catalog.get(match.group(1))
        if metric is None:
            return "[unknown figure]"
        return f"{fmt(metric)}{notes.cite(metric)}"
    return PLACEHOLDER.sub(repl, text)


# ---------------------------------------------------------------- the memo

@dataclass
class Narrative:
    summary: str
    findings: list[str]
    risks: list[str]
    open_questions: list[str]


def _status_icon(status: str) -> str:
    return {PASS: "✅ Pass", FAIL: "❌ Fail", NOT_ASSESSED: "➖ Not assessed"}[status]


def build_memo(
    data: CompanyData,
    metrics: MetricSet,
    results: list[CriterionResult],
    flags: list[DeepDiveFlag],
    market_cap: Metric,
    narrative: Narrative | None,
    narrative_error: str | None,
    price_note: str | None,
    today: date | None = None,
) -> str:
    today = today or date.today()
    notes = Footnotes()
    catalog = figure_catalog(metrics, market_cap)
    years = data.fiscal_years
    last = years[-1]
    out: list[str] = []

    def val(m: Metric) -> str:
        return f"{fmt(m)}{notes.cite(m)}"

    out.append(f"# {data.name} ({data.ticker}): Research memo\n")
    out.append(
        f"*Generated {today.isoformat()} · Data: SEC EDGAR 10-K filings, FY{years[0]}-FY{last} "
        f"(latest fiscal year ended {data.fiscal_year_ends[last]}) · Industry: {data.sic_description} "
        f"(SIC {data.sic})*\n"
    )
    out.append("> Structured summary for personal research. Not investment advice; "
               "no buy or sell recommendation. All figures are GAAP as reported, "
               "calculated in Python and footnoted to their source.\n")

    # Summary
    out.append("## Summary\n")
    if narrative:
        out.append(fill_placeholders(narrative.summary, catalog, notes) + "\n")
    else:
        out.append(f"*Narrative not generated: {narrative_error}. All figures below are complete.*\n")

    # Criteria
    counts = {s: sum(r.status == s for r in results) for s in (PASS, FAIL, NOT_ASSESSED)}
    out.append("## Criteria check\n")
    out.append(f"{counts[PASS]} passed · {counts[FAIL]} failed · {counts[NOT_ASSESSED]} not assessed\n")
    out.append("| Criterion | Threshold | Value | Result |")
    out.append("|---|---|---|---|")
    for r in results:
        out.append(f"| {r.name} | {r.threshold_text} | {val(r.metric)} | {_status_icon(r.status)} |")
    out.append("")

    # Key figures
    out.append("## Key figures\n")
    out.append("| | " + " | ".join(f"FY{y}" for y in years) + " |")
    out.append("|---|" + "---:|" * len(years))
    rows = [
        ("Revenue", "revenue"), ("Revenue growth", "revenue_growth"),
        ("Operating income", "operating_income"), ("Operating margin", "operating_margin"),
        ("Net income", "net_income"), ("Free cash flow", "fcf"), ("ROIC", "roic"),
        ("Net debt", "net_debt"), ("Net debt / EBITDA", "net_debt_to_ebitda"),
        ("Interest coverage", "interest_coverage"), ("Diluted shares", "diluted_shares"),
    ]
    for label, name in rows:
        cells = [val(metrics.per_year[name][y]) for y in years]
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    out.append(f"\nMarket capitalisation: {val(market_cap)}\n")

    # Flags
    out.append("## Flags for deeper analysis\n")
    if flags:
        for f in flags:
            figures = ", ".join(f"{m.label}{f' FY{m.fiscal_year}' if m.fiscal_year else ''}: {val(m)}"
                                for m in f.metrics)
            out.append(f"- {f.text} ({figures})")
        out.append("\n*In this version the causes are not yet investigated automatically; "
                   "they are carried into the open questions.*\n")
    else:
        out.append("No deep-dive triggers fired.\n")

    if narrative:
        for title, items in (("Findings", narrative.findings), ("Risks", narrative.risks),
                             ("Open questions", narrative.open_questions)):
            out.append(f"## {title}\n")
            out.extend(f"- {fill_placeholders(i, catalog, notes)}" for i in items)
            out.append("")

    # Data notes
    out.append("## Data notes and limitations\n")
    data_notes = list(data.gaps)
    seen_notes: set[str] = set()
    for m in [r.metric for r in results] + [market_cap] + [
            metrics.per_year[n][last] for _, n in rows]:
        if m.note and m.note not in seen_notes:
            seen_notes.add(m.note)
            data_notes.append(f"{m.label}: {m.note}")
    if price_note:
        data_notes.append(f"Share price: {price_note}")
    data_notes += [
        "Fiscal years are labelled by the calendar year in which they end.",
        "Where a figure appears in several 10-Ks, the most recently filed value is used (reflects restatements).",
        "The share price comes from Yahoo Finance (unofficial source), the only figure not taken from an SEC filing.",
    ]
    out.extend(f"- {n}" for n in data_notes)
    out.append("")

    out.append("## Sources\n")
    out.append(notes.render())
    out.append("")
    return "\n".join(out)
