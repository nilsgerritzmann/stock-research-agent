# Design decisions

Why the project is built the way it is. Each entry: the decision, the reason,
and the trade-off I accepted. Written so I can explain every part in an interview.

---

## 1. The model never writes a number

**Decision.** Python fetches every figure and calculates every ratio. The
language model only writes prose. When it wants to mention a figure it writes a
placeholder such as `{{operating_margin_FY2025}}`; Python replaces it with the
exact value and a footnote. A check (`find_typed_numbers` in `memo.py`) rejects
any model text that contains a number it typed itself (years and form names like
"10-K" are allowed).

**Why.** Language models are good at language and unreliable at arithmetic and at
copying long numbers. In a research memo one wrong digit destroys trust in the
whole document. Splitting the work this way makes the guarantee *structural*
instead of relying on the model "being careful".

**Trade-off.** The prose is slightly less fluent ("margin fell to 8.2%[^3]"
instead of free-form wording), and the model cannot do its own comparisons like
"almost halved". Those comparisons must come from a calculated metric instead.

## 2. SEC EDGAR "company facts" as the data source

**Decision.** Use the SEC's XBRL API (`data.sec.gov/api/xbrl/companyfacts`), not
web scraping or a commercial data provider.

**Why.** It is free, official and structured. Every value comes with its XBRL tag,
period, form type, filing date and accession number, which is exactly what
"every number has a source" needs. Footnotes link straight to the filing.

**Trade-off.** XBRL data is messy in practice (see 3–6). Commercial providers
clean and standardise it; here I do it myself, transparently.

## 3. Picking one value per figure and year

Companies report the same number several times: FY2024 revenue appears in the
FY2024 10-K and again as a comparative in the FY2025 and FY2026 10-Ks.

- **Only annual reports** (10-K, 10-K/A). Income-statement and cash-flow figures
  must cover 350–380 days, which filters out quarterly and year-to-date values.
- **Most recently filed value wins**, so restatements are reflected. The footnote
  then points to the later 10-K, which does contain that number.
- **Fiscal year = calendar year in which it ends.** EDGAR's own `fy` field is the
  fiscal year of the *filing*, not of the figure, so it cannot be used. Nike's
  year ending 31 May 2026 is FY2026; Microsoft's ending 30 June 2026 is FY2026.
  Limitation: retailers whose year ends in late January call that year by the
  *previous* calendar year, so their labels would be off by one.

## 4. Fallback tags

Companies change tags over time (Microsoft and Intel moved interest expense from
`InterestExpense` to `InterestExpenseNonoperating` around 2024; Coca-Cola moved
its debt to lease-inclusive tags). Each figure has an ordered list of tags, and
for each year the first one with a value is used. The footnote always names the
tag actually used.

## 5. Accounting definitions

| Metric | Definition | Reasoning / limitation |
|---|---|---|
| Operating income | Reported `OperatingIncomeLoss`. If absent: income before taxes − net interest income ("derived EBIT") | Nike reports no operating income line. In XBRL, `InterestIncomeExpenseNonoperatingNet` is positive for net income, so subtracting it removes the financing result. Labelled "derived" in the memo. |
| Free cash flow | Operating cash flow − capital expenditure | Standard definition; uses PP&E purchases only. |
| Total debt | Long-term debt incl. current portion + short-term borrowings / commercial paper | **Lease liabilities excluded**, which flatters companies with large leases (retail). If no short-term borrowings tag exists, it is assumed zero and the memo says so. |
| Cash | Cash & short-term investments (combined line if reported) | Some companies (Intel before 2024) only tag cash including restricted cash; then that is used, which slightly overstates available cash. |
| EBITDA | Operating income + D&A from the cash-flow statement | Where a company only tags depreciation (not amortisation), EBITDA is understated, making leverage look slightly worse, the conservative direction. |
| ROIC | NOPAT / average invested capital | NOPAT = operating income × (1 − effective tax rate). Invested capital = equity + debt − cash, averaged over opening and closing balance. If the tax rate is outside 0–60% (one-off tax effects), ROIC is marked *not meaningful* rather than distorted. An operating loss is not taxed. |
| FCF conversion | Sum of FCF over all years / sum of net income | Summing smooths out single-year noise (e.g. a large tax payment). |
| Market cap | Latest price × shares outstanding from the filing cover page | For companies with several share classes (Nike), the cover page has no single total in this dataset; then the latest diluted weighted-average share count is used and labelled as an approximation. |

## 6. GAAP only, no "adjusted" figures

Companies publish adjusted (non-GAAP) numbers that exclude costs they consider
one-off. These are not standardised and not in XBRL. The memo uses reported GAAP
figures only and turns unusual items into open questions instead of adjusting
for them silently.

## 7. Missing data is shown, never guessed

Every metric carries a `note` explaining why it is missing or not meaningful
(negative denominator, missing input). Criteria with missing data show
"Not assessed", which is different from "Fail". The memo has a "Data notes and
limitations" section listing all of these.

## 8. Banks and insurers excluded

Their statements are built differently (interest is revenue, not a cost; there is
no meaningful "net debt"). Applying these criteria would produce confident but
wrong results, so v1 refuses SIC codes 6000–6411 and says why.

## 9. Share price from yfinance, failing soft

The price is the only figure not from an SEC filing. yfinance is free but
unofficial and occasionally breaks. The code wraps it so that any failure leaves
the valuation criterion as "Not assessed", with the reason, and the rest of the
memo is still produced.

## 10. Running on the Claude subscription, not a paid API key

**Decision.** The model is called through the Claude Agent SDK, which signs in
with the same login as Claude Code and counts against the subscription's usage
limits instead of billing per token.

**Why.** No extra cost. The Agent SDK also has the agent loop built in, which v2
needs: my Python functions become tools the model can call.

**Trade-off.** Runs consume subscription usage limits, and the setup is for
personal use; someone cloning the repo signs in with their own account. All model
access sits in one file (`llm.py`), so switching to an API key would touch only
that file.

## 11. The model gets no tools in v1, and its output is checked

In v1 the model is called with all built-in tools disabled (`tools=[]`) and
local settings ignored (`setting_sources=[]`): it can only read the prompt and
return text. Its answer must match a JSON schema (summary, findings, risks,
open questions). Then two checks run: no typed numbers, and no placeholder ids
that don't exist. If either fails, the model gets one retry with the problems
listed; if that fails too, the memo is written without prose rather than with
unchecked prose. The prompt also forbids stating causes, because in v1 the
model sees only figures, not the annual report, so causes become open
questions. Giving the model tools to find those causes is exactly what v2 adds.

## 12. Flat project layout, run with `python -m`

The package lives in `research_agent/` at the project root and is run with
`python -m research_agent TICKER`. A `src/` layout would depend on a `.pth` file
in the virtual environment, which macOS occasionally marks as hidden; Python 3.12
then silently skips it. Running from the project folder avoids that failure.

## 13. Caching SEC responses on disk for a day

Re-running a company does not re-download 5 MB of JSON, and the SEC's fair-access
limit (10 requests/second, identifying User-Agent) is respected by design.
