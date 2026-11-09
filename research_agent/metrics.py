"""All calculations. Plain Python, no AI involved.

Every result is a `Metric` that carries its formula and the inputs it was built
from (reported Facts or other Metrics). That chain is what lets every number in
the memo be traced back to a filing.

When an input is missing or a ratio is not meaningful (e.g. dividing by a
negative number), the value is None and `note` says why. Nothing is guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

from .facts import CompanyData, Fact

Source = Union[Fact, "Metric"]


@dataclass
class Metric:
    id: str                          # unique id, e.g. "operating_margin_FY2025"
    label: str                       # e.g. "Operating margin"
    value: float | None
    unit: str                        # "USD", "%", "x", "shares", "years"
    formula: str = ""                # e.g. "Operating income / Revenue"
    inputs: list[Source] = field(default_factory=list)
    note: str | None = None          # why it is None, or caveats
    fiscal_year: int | None = None

    @property
    def available(self) -> bool:
        return self.value is not None


def _fy(year: int) -> str:
    return f"FY{year}"


def _missing(id_: str, label: str, unit: str, year: int | None, note: str) -> Metric:
    return Metric(id_, label, None, unit, note=note, fiscal_year=year)


def _as_metric(fact: Fact) -> Metric:
    """Wrap a reported figure so it can be used like any other metric."""
    return Metric(
        id=f"{fact.key}_{_fy(fact.fiscal_year)}", label=fact.label, value=fact.value,
        unit=fact.unit, formula="Reported", inputs=[fact], fiscal_year=fact.fiscal_year,
    )


def reported(data: CompanyData, key: str, year: int, label: str | None = None) -> Metric:
    fact = data.get(key, year)
    if fact is None:
        from .facts import FIGURES
        lbl = label or FIGURES[key].label
        return _missing(f"{key}_{_fy(year)}", lbl, FIGURES[key].unit, year, f"{lbl} not reported in FY{year} 10-K data.")
    return _as_metric(fact)


# ---------------------------------------------------------------- basic helpers

def ratio(id_: str, label: str, num: Metric, den: Metric, *, percent: bool, year: int | None,
          require_positive_denominator: bool = True) -> Metric:
    unit = "%" if percent else "x"
    formula = f"{num.label} / {den.label}" + (" x 100" if percent else "")
    if not num.available or not den.available:
        missing = [m.label for m in (num, den) if not m.available]
        return Metric(id_, label, None, unit, formula, [num, den], f"Missing input: {', '.join(missing)}.", year)
    if den.value == 0 or (require_positive_denominator and den.value < 0):
        return Metric(id_, label, None, unit, formula, [num, den],
                      f"Not meaningful: {den.label} is {'zero' if den.value == 0 else 'negative'}.", year)
    value = num.value / den.value * (100 if percent else 1)
    return Metric(id_, label, value, unit, formula, [num, den], fiscal_year=year)


def total(id_: str, label: str, parts: list[Metric], *, year: int, signs: list[int] | None = None) -> Metric:
    signs = signs or [1] * len(parts)
    formula = " ".join(
        (("- " if s < 0 else "+ ") if i else ("-" if s < 0 else "")) + p.label
        for i, (p, s) in enumerate(zip(parts, signs))
    )
    missing = [p.label for p in parts if not p.available]
    if missing:
        return Metric(id_, label, None, parts[0].unit, formula, parts, f"Missing input: {', '.join(missing)}.", year)
    value = sum(p.value * s for p, s in zip(parts, signs))
    return Metric(id_, label, value, parts[0].unit, formula, parts, fiscal_year=year)


def cagr(id_: str, label: str, first: Metric, last: Metric, periods: int) -> Metric:
    formula = f"({last.label} {_fy(last.fiscal_year or 0)} / {first.label} {_fy(first.fiscal_year or 0)})^(1/{periods}) - 1"
    if not first.available or not last.available:
        return Metric(id_, label, None, "%", formula, [first, last], "Missing start or end value.")
    if first.value <= 0 or last.value <= 0 or periods <= 0:
        return Metric(id_, label, None, "%", formula, [first, last], "Not meaningful: start or end value is not positive.")
    value = ((last.value / first.value) ** (1 / periods) - 1) * 100
    return Metric(id_, label, value, "%", formula, [first, last])


# ---------------------------------------------------------- per-year metrics

def operating_income(data: CompanyData, year: int) -> Metric:
    """Reported operating income; if the company reports none, derive EBIT.

    Derivation: income before taxes minus net interest income. In XBRL the tag
    InterestIncomeExpenseNonoperatingNet is positive for net interest income,
    so subtracting it removes the financing result and leaves operating EBIT.
    """
    rep = data.get("operating_income_reported", year)
    if rep is not None:
        m = _as_metric(rep)
        m.id, m.label = f"operating_income_{_fy(year)}", "Operating income"
        return m
    pretax = reported(data, "pretax_income", year)
    net_int = reported(data, "net_interest_income", year)
    m = total(f"operating_income_{_fy(year)}", "Operating income (derived EBIT)",
              [pretax, net_int], year=year, signs=[1, -1])
    m.note = ("Company reports no operating income line; derived as income before taxes "
              "minus net interest income." + (f" {m.note}" if m.note else ""))
    return m


def total_debt(data: CompanyData, year: int) -> Metric:
    lt_total = data.get("long_term_debt_total", year)
    if lt_total is not None:
        long_term = _as_metric(lt_total)
    else:
        long_term = total(f"long_term_debt_{_fy(year)}", "Long-term debt incl. current portion",
                          [reported(data, "long_term_debt_noncurrent", year),
                           reported(data, "long_term_debt_current", year)], year=year)
    short = data.get("short_term_borrowings", year)
    note = None
    if short is None:
        short_m = Metric(f"short_term_borrowings_{_fy(year)}", "Short-term borrowings", 0.0, "USD",
                         "Not reported; assumed zero", fiscal_year=year)
        note = "No short-term borrowings reported; assumed zero."
    else:
        short_m = _as_metric(short)
    m = total(f"total_debt_{_fy(year)}", "Total debt", [long_term, short_m], year=year)
    if not long_term.available and m.value is None:
        m.note = "Long-term debt not found in filings."
    elif note:
        m.note = note
    m.note = (m.note + " " if m.note else "") + "Lease liabilities excluded."
    return m


def cash_and_investments(data: CompanyData, year: int) -> Metric:
    combined = data.get("cash_and_short_term_investments", year)
    if combined is not None:
        return _as_metric(combined)
    cash = reported(data, "cash", year)
    sti = data.get("short_term_investments", year)
    if sti is None:
        m = Metric(f"cash_and_short_term_investments_{_fy(year)}", "Cash, equivalents & short-term investments",
                   cash.value, "USD",
                   "Cash and cash equivalents (no short-term investments reported)", [cash],
                   fiscal_year=year)
        return m
    return total(f"cash_and_short_term_investments_{_fy(year)}", "Cash, equivalents & short-term investments",
                 [cash, _as_metric(sti)], year=year)


def free_cash_flow(data: CompanyData, year: int) -> Metric:
    return total(f"fcf_{_fy(year)}", "Free cash flow",
                 [reported(data, "operating_cash_flow", year), reported(data, "capex", year)],
                 year=year, signs=[1, -1])


def ebitda(data: CompanyData, year: int) -> Metric:
    return total(f"ebitda_{_fy(year)}", "EBITDA",
                 [operating_income(data, year), reported(data, "depreciation_amortization", year)], year=year)


def net_debt(data: CompanyData, year: int) -> Metric:
    return total(f"net_debt_{_fy(year)}", "Net debt",
                 [total_debt(data, year), cash_and_investments(data, year)], year=year, signs=[1, -1])


def net_debt_to_ebitda(data: CompanyData, year: int) -> Metric:
    nd, eb = net_debt(data, year), ebitda(data, year)
    m = ratio(f"net_debt_to_ebitda_{_fy(year)}", "Net debt / EBITDA", nd, eb, percent=False, year=year)
    if m.available and nd.value < 0:
        m.note = "Net cash position (cash exceeds debt)."
    return m


def interest_coverage(data: CompanyData, year: int) -> Metric:
    interest = reported(data, "interest_expense", year)
    ebit = operating_income(data, year)
    if not interest.available:
        net_int = data.get("net_interest_income", year)
        if net_int is not None and net_int.value >= 0:
            return Metric(f"interest_coverage_{_fy(year)}", "Interest coverage", None, "x",
                          "Operating income / Interest expense", [ebit, _as_metric(net_int)],
                          "Gross interest expense not reported; company has net interest income, "
                          "so there is no net interest burden.", year)
    return ratio(f"interest_coverage_{_fy(year)}", "Interest coverage", ebit, interest, percent=False, year=year)


def effective_tax_rate(data: CompanyData, year: int) -> Metric:
    return ratio(f"tax_rate_{_fy(year)}", "Effective tax rate",
                 reported(data, "income_tax", year), reported(data, "pretax_income", year),
                 percent=True, year=year)


def invested_capital(data: CompanyData, year: int) -> Metric:
    return total(f"invested_capital_{_fy(year)}", "Invested capital",
                 [reported(data, "equity", year), total_debt(data, year), cash_and_investments(data, year)],
                 year=year, signs=[1, 1, -1])


def roic(data: CompanyData, year: int) -> Metric:
    """ROIC = NOPAT / average invested capital.

    NOPAT = operating income x (1 - effective tax rate).
    Invested capital = equity + total debt - cash & short-term investments,
    averaged over the opening and closing balance of the year.
    """
    id_ = f"roic_{_fy(year)}"
    ebit, tax = operating_income(data, year), effective_tax_rate(data, year)
    if not ebit.available:
        return Metric(id_, "ROIC", None, "%", inputs=[ebit], fiscal_year=year, note="Missing operating income.")
    nopat_note = None
    if ebit.value <= 0:
        # An operating loss is not taxed, so no tax adjustment is applied.
        nopat = Metric(f"nopat_{_fy(year)}", "NOPAT", ebit.value, "USD",
                       "Operating income (operating loss, so no tax adjustment)", [ebit], fiscal_year=year)
        nopat_note = "Operating loss: NOPAT equals operating income, no tax adjustment."
    else:
        if not tax.available or not 0 <= tax.value <= 60:
            shown = f" ({tax.value:.1f}%)" if tax.available else ""
            return Metric(id_, "ROIC", None, "%", inputs=[ebit, tax], fiscal_year=year,
                          note=f"Effective tax rate{shown} missing or outside 0-60%, so NOPAT is not meaningful.")
        tax_share = Metric(f"tax_factor_{_fy(year)}", "(1 - effective tax rate)", 1 - tax.value / 100, "x",
                           "1 - Effective tax rate", [tax], fiscal_year=year)
        nopat = Metric(f"nopat_{_fy(year)}", "NOPAT", ebit.value * tax_share.value, "USD",
                       "Operating income x (1 - effective tax rate)", [ebit, tax_share], fiscal_year=year)
    prior = year - 1
    ic_end, ic_start = invested_capital(data, year), invested_capital(data, prior)
    avg_ic = total(f"avg_invested_capital_{_fy(year)}", "Average invested capital", [ic_start, ic_end], year=year)
    if avg_ic.available:
        avg_ic.value /= 2
        avg_ic.formula = f"(Invested capital FY{prior} + Invested capital FY{year}) / 2"
    m = ratio(id_, "ROIC", nopat, avg_ic, percent=True, year=year)
    m.formula = "NOPAT / Average invested capital x 100"
    if nopat_note:
        m.note = (m.note + " " if m.note else "") + nopat_note
    return m


def growth(id_: str, label: str, current: Metric, previous: Metric, year: int) -> Metric:
    m = ratio(id_, label, total(f"{id_}_delta", f"Change in {current.label}",
                                [current, previous], year=year, signs=[1, -1]),
              previous, percent=True, year=year)
    m.formula = f"({current.label} FY{year} / {previous.label} FY{year - 1} - 1) x 100"
    return m


# -------------------------------------------------------------- the full set

@dataclass
class MetricSet:
    per_year: dict[str, dict[int, Metric]]   # metric name -> year -> Metric
    summary: dict[str, Metric]               # metric name -> Metric (multi-year or latest)

    def all(self) -> list[Metric]:
        out = list(self.summary.values())
        for by_year in self.per_year.values():
            out.extend(by_year.values())
        return out


PER_YEAR_TABLE = [
    "revenue", "revenue_growth", "operating_income", "operating_margin", "net_income",
    "fcf", "roic", "net_debt", "net_debt_to_ebitda", "interest_coverage", "diluted_shares",
]


def compute_all(data: CompanyData, market_cap: Metric | None = None) -> MetricSet:
    years = data.fiscal_years
    per_year: dict[str, dict[int, Metric]] = {name: {} for name in PER_YEAR_TABLE}
    per_year["operating_margin_change"] = {}
    per_year["revenue_growth_change"] = {}

    for y in years:
        rev = reported(data, "revenue", y)
        prev_rev = reported(data, "revenue", y - 1)
        ebit = operating_income(data, y)
        per_year["revenue"][y] = rev
        per_year["revenue_growth"][y] = growth(f"revenue_growth_{_fy(y)}", "Revenue growth", rev, prev_rev, y)
        per_year["operating_income"][y] = ebit
        per_year["operating_margin"][y] = ratio(f"operating_margin_{_fy(y)}", "Operating margin",
                                                ebit, rev, percent=True, year=y)
        per_year["net_income"][y] = reported(data, "net_income", y)
        per_year["fcf"][y] = free_cash_flow(data, y)
        per_year["roic"][y] = roic(data, y)
        per_year["net_debt"][y] = net_debt(data, y)
        per_year["net_debt_to_ebitda"][y] = net_debt_to_ebitda(data, y)
        per_year["interest_coverage"][y] = interest_coverage(data, y)
        per_year["diluted_shares"][y] = reported(data, "diluted_shares", y)

    # Year-on-year changes used for deep-dive triggers (in percentage points).
    for y in years[1:]:
        for name, label in (("operating_margin", "Operating margin"), ("revenue_growth", "Revenue growth")):
            cur, prev = per_year[name][y], per_year[name].get(y - 1)
            if prev is None:
                continue
            m = total(f"{name}_change_{_fy(y)}", f"Change in {label.lower()} (pp)", [cur, prev],
                      year=y, signs=[1, -1])
            m.unit = "pp"
            per_year[f"{name}_change"][y] = m

    first, last = years[0], years[-1]
    periods = last - first
    latest = {name: per_year[name][last] for name in ("operating_margin", "roic", "net_debt_to_ebitda",
                                                       "interest_coverage")}

    fcfs = [per_year["fcf"][y] for y in years]
    nis = [per_year["net_income"][y] for y in years]
    fcf_sum = total("fcf_sum", f"Sum of free cash flow FY{first}-FY{last}", fcfs, year=last)
    ni_sum = total("net_income_sum", f"Sum of net income FY{first}-FY{last}", nis, year=last)
    available_fcfs = [m for m in fcfs if m.available]
    neg_years = Metric(
        "fcf_negative_years", "Years with negative free cash flow",
        float(sum(1 for m in available_fcfs if m.value < 0)) if len(available_fcfs) == len(fcfs) else None,
        "years", f"Count of years FY{first}-FY{last} with free cash flow below zero", fcfs,
        None if len(available_fcfs) == len(fcfs) else "Free cash flow missing for at least one year.",
    )

    summary: dict[str, Metric] = {
        "revenue_cagr": cagr("revenue_cagr", f"Revenue CAGR FY{first}-FY{last}",
                             per_year["revenue"][first], per_year["revenue"][last], periods),
        **latest,
        "fcf_conversion": ratio("fcf_conversion", f"FCF conversion FY{first}-FY{last}", fcf_sum, ni_sum,
                                percent=True, year=None),
        "fcf_negative_years": neg_years,
        "diluted_shares_cagr": cagr("diluted_shares_cagr", f"Diluted share count CAGR FY{first}-FY{last}",
                                    per_year["diluted_shares"][first], per_year["diluted_shares"][last], periods),
    }
    if market_cap is not None and market_cap.available:
        summary["fcf_yield"] = ratio("fcf_yield", f"FCF yield (FCF FY{last} / market cap)",
                                     per_year["fcf"][last], market_cap, percent=True, year=None)
    else:
        summary["fcf_yield"] = Metric("fcf_yield", "FCF yield", None, "%",
                                      note=(market_cap.note if market_cap else "Share price not available."))
    return MetricSet(per_year, summary)
