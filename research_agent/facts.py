"""Turns raw EDGAR 'company facts' into clean annual figures, each with its source.

EDGAR's companyfacts file lists every value a company has tagged in XBRL, for
every period, in every filing. One fiscal year's revenue appears several times:
in that year's 10-K and again as a comparative in the next two 10-Ks. This
module picks exactly one value per figure and fiscal year, and remembers where
it came from (tag, period, filing, link).

Rules (see DECISIONS.md for the reasoning):
- Only annual reports (10-K and 10-K/A) are used.
- Duration figures (income statement, cash flow) must cover roughly one year.
- Balance-sheet figures are taken at the fiscal year-end date.
- A fiscal year is labelled by the calendar year in which it ends.
- If several filings report the same value, the most recently filed wins,
  so restatements are reflected.
- Companies change tags over time, so each figure has an ordered list of
  tags. For each year the first tag that has a value is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from . import sec_client

ANNUAL_FORMS = {"10-K", "10-K/A"}


@dataclass(frozen=True)
class FigureSpec:
    label: str
    kind: str          # "duration" (flow over a year) or "instant" (balance at year end)
    tags: tuple[str, ...]
    unit: str = "USD"


# The raw figures we need. Order of tags = order of preference.
FIGURES: dict[str, FigureSpec] = {
    "revenue": FigureSpec("Revenue", "duration", (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    )),
    "operating_income_reported": FigureSpec("Operating income", "duration", ("OperatingIncomeLoss",)),
    "pretax_income": FigureSpec("Income before income taxes", "duration", (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    )),
    "net_interest_income": FigureSpec("Interest income (expense), net", "duration", (
        "InterestIncomeExpenseNonoperatingNet",
    )),
    "income_tax": FigureSpec("Income tax expense", "duration", ("IncomeTaxExpenseBenefit",)),
    "net_income": FigureSpec("Net income", "duration", ("NetIncomeLoss",)),
    "interest_expense": FigureSpec("Interest expense", "duration", (
        "InterestExpense",
        "InterestExpenseNonoperating",
        "InterestExpenseDebt",
        "InterestAndDebtExpense",
    )),
    "depreciation_amortization": FigureSpec("Depreciation & amortization", "duration", (
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
        "Depreciation",
    )),
    "operating_cash_flow": FigureSpec("Operating cash flow", "duration", (
        "NetCashProvidedByUsedInOperatingActivities",
    )),
    "capex": FigureSpec("Capital expenditure", "duration", (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    )),
    "diluted_shares": FigureSpec("Diluted weighted-average shares", "duration", (
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    ), unit="shares"),
    "cash_and_short_term_investments": FigureSpec("Cash, equivalents & short-term investments", "instant", (
        "CashCashEquivalentsAndShortTermInvestments",
    )),
    "cash": FigureSpec("Cash and cash equivalents", "instant", (
        "CashAndCashEquivalentsAtCarryingValue",
        # Fallback: includes restricted cash, so slightly overstates freely available cash.
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    )),
    "short_term_investments": FigureSpec("Short-term investments", "instant", (
        "ShortTermInvestments",
        "OtherShortTermInvestments",
        "MarketableSecuritiesCurrent",
        "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
        "DebtSecuritiesAvailableForSaleExcludingAccruedInterestCurrent",
    )),
    "long_term_debt_total": FigureSpec("Long-term debt incl. current portion", "instant", (
        "LongTermDebt",
        "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
    )),
    "long_term_debt_noncurrent": FigureSpec("Long-term debt, non-current", "instant", (
        "LongTermDebtNoncurrent",
        "LongTermDebtAndCapitalLeaseObligations",
    )),
    "long_term_debt_current": FigureSpec("Long-term debt, current portion", "instant", (
        "LongTermDebtCurrent",
        "LongTermDebtAndCapitalLeaseObligationsCurrent",
    )),
    "short_term_borrowings": FigureSpec("Short-term borrowings", "instant", (
        "ShortTermBorrowings",
        "CommercialPaper",
    )),
    "equity": FigureSpec("Shareholders' equity", "instant", ("StockholdersEquity",)),
}


@dataclass(frozen=True)
class Fact:
    """One reported number with everything needed to cite it."""
    key: str             # our name, e.g. "revenue"
    label: str           # human label, e.g. "Revenue"
    tag: str             # XBRL tag, e.g. "us-gaap:Revenues"
    fiscal_year: int
    value: float
    unit: str
    period_start: str | None
    period_end: str
    form: str
    filed: str
    accession: str
    url: str

    def citation(self) -> str:
        period = f"{self.period_start} to {self.period_end}" if self.period_start else f"as of {self.period_end}"
        return (
            f"{self.label} ({self.tag}), FY{self.fiscal_year} ({period}): "
            f"{self.value:,.0f} {self.unit}. {self.form} filed {self.filed}, [{self.accession}]({self.url})"
        )


@dataclass
class CompanyData:
    ticker: str
    name: str
    cik: int
    sic: str
    sic_description: str
    fiscal_years: list[int]                    # ascending, the years in scope
    fiscal_year_ends: dict[int, str]           # fiscal year -> period end date
    facts: dict[str, dict[int, Fact]]          # figure key -> fiscal year -> Fact
    shares_outstanding: Fact | None            # latest cover-page share count
    gaps: list[str]                            # human-readable notes on missing data

    def get(self, key: str, year: int) -> Fact | None:
        return self.facts.get(key, {}).get(year)


def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def _annual_entries(us_gaap: dict, tag: str, unit: str, kind: str) -> list[dict]:
    entries = us_gaap.get(tag, {}).get("units", {}).get(unit, [])
    result = []
    for e in entries:
        if e.get("form") not in ANNUAL_FORMS:
            continue
        if kind == "duration":
            if "start" not in e or not 350 <= _days(e["start"], e["end"]) <= 380:
                continue
        elif "start" in e:
            continue
        result.append(e)
    return result


def _latest_filed(entries: list[dict]) -> dict:
    return max(entries, key=lambda e: (e["filed"], e["accn"]))


def _fiscal_year_ends(us_gaap: dict) -> list[str]:
    """Fiscal year-end dates, read from the annual revenue / net income periods."""
    ends: set[str] = set()
    for key in ("revenue", "net_income"):
        for tag in FIGURES[key].tags:
            ends.update(e["end"] for e in _annual_entries(us_gaap, tag, "USD", "duration"))
    return sorted(ends)


def load_company(ticker: str, years: int = 5) -> CompanyData:
    """Download (or read from cache) and structure the last `years` fiscal years."""
    cik, name = sec_client.lookup_cik(ticker)
    submissions = sec_client.get_submissions(cik)
    company_facts = sec_client.get_company_facts(cik)
    us_gaap = company_facts.get("facts", {}).get("us-gaap", {})
    documents = sec_client.primary_documents(submissions)

    all_ends = _fiscal_year_ends(us_gaap)
    if not all_ends:
        raise sec_client.SecError(f"No annual (10-K) financial data found for {ticker}.")
    # One extra year back, so averages and growth rates have a starting point.
    ends_in_scope = all_ends[-(years + 1):]
    year_of_end = {end: int(end[:4]) for end in ends_in_scope}
    fiscal_year_ends = {y: e for e, y in year_of_end.items()}
    fiscal_years = sorted(fiscal_year_ends)[-years:]

    def make_fact(key: str, spec: FigureSpec, tag: str, e: dict, year: int) -> Fact:
        return Fact(
            key=key, label=spec.label, tag=f"us-gaap:{tag}", fiscal_year=year,
            value=float(e["val"]), unit=spec.unit,
            period_start=e.get("start"), period_end=e["end"],
            form=e["form"], filed=e["filed"], accession=e["accn"],
            url=sec_client.filing_url(cik, e["accn"], documents.get(e["accn"])),
        )

    facts: dict[str, dict[int, Fact]] = {}
    for key, spec in FIGURES.items():
        by_year: dict[int, Fact] = {}
        for end, year in year_of_end.items():
            for tag in spec.tags:
                matches = [e for e in _annual_entries(us_gaap, tag, spec.unit, spec.kind) if e["end"] == end]
                if matches:
                    by_year[year] = make_fact(key, spec, tag, _latest_filed(matches), year)
                    break
        facts[key] = by_year

    gaps: list[str] = []
    for key in ("revenue", "net_income", "operating_cash_flow", "capex", "equity", "diluted_shares"):
        missing = [y for y in fiscal_years if y not in facts[key]]
        if missing:
            gaps.append(f"{FIGURES[key].label} not found for FY{', FY'.join(map(str, missing))}.")

    return CompanyData(
        ticker=ticker.upper(), name=name, cik=cik,
        sic=str(submissions.get("sic", "")), sic_description=submissions.get("sicDescription", ""),
        fiscal_years=fiscal_years, fiscal_year_ends=fiscal_year_ends,
        facts=facts, shares_outstanding=_shares_outstanding(company_facts, cik, documents),
        gaps=gaps,
    )


def _shares_outstanding(company_facts: dict, cik: int, documents: dict[str, str]) -> Fact | None:
    """Latest share count from a filing's cover page (dei:EntityCommonStockSharesOutstanding)."""
    entries = (
        company_facts.get("facts", {}).get("dei", {})
        .get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", [])
    )
    if not entries:
        return None
    latest_end = max(e["end"] for e in entries)
    # A stale figure means the company stopped reporting a single total
    # (typically because it now reports per share class). Don't use it.
    if (date.today() - date.fromisoformat(latest_end)).days > 400:
        return None
    same_day = [e for e in entries if e["end"] == latest_end]
    e = _latest_filed(same_day)
    # Several values on one date usually means several share classes without
    # class labels in this dataset; we cannot safely add them up.
    if len({x["val"] for x in same_day if x["accn"] == e["accn"]}) > 1:
        return None
    return Fact(
        key="shares_outstanding", label="Shares outstanding (cover page)",
        tag="dei:EntityCommonStockSharesOutstanding", fiscal_year=int(latest_end[:4]),
        value=float(e["val"]), unit="shares", period_start=None, period_end=e["end"],
        form=e["form"], filed=e["filed"], accession=e["accn"],
        url=sec_client.filing_url(cik, e["accn"], documents.get(e["accn"])),
    )


# SIC codes for banks, savings institutions, insurers and similar financials.
# Their statements are structured differently, so v1 refuses them.
def is_financial_company(sic: str) -> bool:
    try:
        code = int(sic)
    except ValueError:
        return False
    return 6000 <= code <= 6411 or code in (6712,)
