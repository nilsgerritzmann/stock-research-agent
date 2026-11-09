"""Tests for the calculations, using a made-up company with round numbers.

Every expected value is worked out by hand in the comments, so you can check
the formulas without running anything.
"""

import pytest

from research_agent.criteria import FAIL, NOT_ASSESSED, PASS, evaluate
from research_agent.facts import CompanyData, Fact
from research_agent.memo import find_typed_numbers, fmt
from research_agent.metrics import compute_all, operating_income, roic, total_debt


def fact(key, year, value, unit="USD", tag=None):
    return Fact(key=key, label=key, tag=tag or f"us-gaap:{key}", fiscal_year=year, value=float(value),
                unit=unit, period_start=None, period_end=f"{year}-12-31", form="10-K",
                filed=f"{year + 1}-02-15", accession=f"0000-{year}", url="https://example.com")


def make_company(**overrides) -> CompanyData:
    years = [2021, 2022, 2023]
    rows = {
        # key: {year: value} (USD millions written out in full)
        "revenue": {2020: 900, 2021: 1000, 2022: 1100, 2023: 1210},
        "operating_income_reported": {2021: 200, 2022: 220, 2023: 121},
        "pretax_income": {2021: 190, 2022: 210, 2023: 110},
        "income_tax": {2021: 38, 2022: 42, 2023: 22},          # 20% tax rate
        "net_income": {2021: 152, 2022: 168, 2023: 88},
        "interest_expense": {2021: 10, 2022: 10, 2023: 11},
        "depreciation_amortization": {2021: 50, 2022: 50, 2023: 50},
        "operating_cash_flow": {2021: 220, 2022: 230, 2023: 150},
        "capex": {2021: 70, 2022: 80, 2023: 100},
        "diluted_shares": {2021: 100, 2022: 100, 2023: 100},
        "cash": {2020: 100, 2021: 100, 2022: 100, 2023: 100},
        "long_term_debt_total": {2020: 300, 2021: 300, 2022: 300, 2023: 300},
        "equity": {2020: 800, 2021: 800, 2022: 800, 2023: 800},
    }
    rows.update(overrides)
    facts = {k: {y: fact(k, y, v, unit="shares" if k == "diluted_shares" else "USD") for y, v in by_year.items()}
             for k, by_year in rows.items()}
    return CompanyData(ticker="TEST", name="Test Corp", cik=1, sic="1000", sic_description="Test",
                       fiscal_years=years, fiscal_year_ends={y: f"{y}-12-31" for y in years},
                       facts=facts, shares_outstanding=None, gaps=[])


def test_operating_margin_and_growth():
    m = compute_all(make_company())
    assert m.per_year["operating_margin"][2021].value == pytest.approx(20.0)     # 200 / 1000
    assert m.per_year["operating_margin"][2023].value == pytest.approx(10.0)     # 121 / 1210
    assert m.per_year["revenue_growth"][2022].value == pytest.approx(10.0)       # 1100 / 1000 - 1
    # Margin FY2022 = 20%, FY2023 = 10% -> change of -10 percentage points
    assert m.per_year["operating_margin_change"][2023].value == pytest.approx(-10.0)


def test_revenue_cagr():
    m = compute_all(make_company())
    # (1210 / 1000) ^ (1/2) - 1 = 1.1 - 1 = 10%
    assert m.summary["revenue_cagr"].value == pytest.approx(10.0)


def test_free_cash_flow_and_conversion():
    m = compute_all(make_company())
    assert m.per_year["fcf"][2021].value == pytest.approx(150)                    # 220 - 70
    # FCF sum = 150 + 150 + 50 = 350; net income sum = 152 + 168 + 88 = 408
    assert m.summary["fcf_conversion"].value == pytest.approx(350 / 408 * 100)
    assert m.summary["fcf_negative_years"].value == 0


def test_leverage_and_coverage():
    m = compute_all(make_company())
    # Net debt = 300 - 100 = 200; EBITDA FY2023 = 121 + 50 = 171
    assert m.per_year["net_debt"][2023].value == pytest.approx(200)
    assert m.summary["net_debt_to_ebitda"].value == pytest.approx(200 / 171)
    assert m.summary["interest_coverage"].value == pytest.approx(11.0)            # 121 / 11


def test_roic():
    data = make_company()
    # Tax rate FY2021 = 38 / 190 = 20%; NOPAT = 200 x 0.8 = 160
    # Invested capital = 800 + 300 - 100 = 1000 in both years -> average 1000
    assert roic(data, 2021).value == pytest.approx(16.0)


def test_roic_operating_loss_is_not_taxed():
    data = make_company(operating_income_reported={2021: -100, 2022: 220, 2023: 121})
    # NOPAT = -100 (no tax on a loss) / average invested capital 1000
    assert roic(data, 2021).value == pytest.approx(-10.0)


def test_roic_not_meaningful_with_odd_tax_rate():
    data = make_company(income_tax={2021: 150, 2022: 42, 2023: 22})   # 150 / 190 = 79% tax rate
    result = roic(data, 2021)
    assert result.value is None
    assert "outside 0-60%" in result.note


def test_missing_data_is_reported_not_guessed():
    data = make_company(capex={2021: 70, 2022: 80})                    # FY2023 capex missing
    m = compute_all(data)
    assert m.per_year["fcf"][2023].value is None
    assert "Missing input" in m.per_year["fcf"][2023].note
    assert m.summary["fcf_negative_years"].value is None


def test_derived_ebit_when_no_operating_income_line():
    data = make_company(operating_income_reported={},
                        net_interest_income={2021: 5, 2022: 5, 2023: 5})  # net interest income
    ebit = operating_income(data, 2021)
    assert ebit.value == pytest.approx(185)                            # 190 pretax - 5 interest income
    assert "derived" in ebit.label


def test_total_debt_assumes_zero_short_term_borrowings_and_says_so():
    debt = total_debt(make_company(), 2023)
    assert debt.value == pytest.approx(300)
    assert "assumed zero" in debt.note


def test_criteria_statuses():
    m = compute_all(make_company())
    config = {"criteria": [
        {"id": "a", "name": "margin", "metric": "operating_margin", "operator": ">=", "threshold": 15},
        {"id": "b", "name": "growth", "metric": "revenue_cagr", "operator": ">=", "threshold": 5},
        {"id": "c", "name": "yield", "metric": "fcf_yield", "operator": ">=", "threshold": 4},
    ]}
    statuses = [r.status for r in evaluate(m, config)]
    assert statuses == [FAIL, PASS, NOT_ASSESSED]      # 10% < 15%; 10% >= 5%; no price


def test_number_guard():
    assert find_typed_numbers("Margin fell to {{operating_margin_FY2023}} in FY2023 (see 10-K).") == []
    assert find_typed_numbers("Margin fell to 11% in 2023.") == ["11%"]
    assert find_typed_numbers("Revenue was $1,210m.") == ["1,210"]


def test_formatting():
    m = compute_all(make_company())
    assert fmt(m.per_year["operating_margin"][2023]) == "10.0%"
