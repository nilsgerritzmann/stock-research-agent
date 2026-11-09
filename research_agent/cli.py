"""Command line entry point: `research MSFT`.

Version 1 is a fixed workflow, always the same steps in the same order:
  1. load 10-K figures from SEC EDGAR
  2. get the share price (optional, fails soft)
  3. calculate metrics in Python
  4. check them against config/criteria.yaml and find deep-dive flags
  5. ask the model for prose (summary, findings, risks, open questions)
  6. assemble the memo and save it to memos/
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from . import criteria, facts, llm, memo, metrics, prices
from .sec_client import SecError

MEMO_DIR = Path(__file__).resolve().parents[1] / "memos"


def run(ticker: str, use_llm: bool = True, out_dir: Path = MEMO_DIR) -> Path:
    config = criteria.load_config()

    print(f"[1/6] Loading SEC EDGAR data for {ticker.upper()} ...")
    data = facts.load_company(ticker, years=config.get("years", 5))
    if facts.is_financial_company(data.sic):
        raise SecError(
            f"{data.name} is a financial company (SIC {data.sic}, {data.sic_description}). "
            "Banks and insurers are not supported in v1: their statements need different criteria."
        )
    print(f"      {data.name}, FY{data.fiscal_years[0]}-FY{data.fiscal_years[-1]}")

    print("[2/6] Getting share price ...")
    quote, price_error = prices.get_price(ticker)
    print(f"      {'%.2f USD as of %s' % (quote.close, quote.date) if quote else price_error}")
    market_cap = prices.market_cap(quote, data.shares_outstanding, price_error,
                                   data.get("diluted_shares", data.fiscal_years[-1]))

    print("[3/6] Calculating metrics ...")
    metric_set = metrics.compute_all(data, market_cap)

    print("[4/6] Checking criteria ...")
    results = criteria.evaluate(metric_set, config)
    flags = criteria.deep_dive_flags(metric_set, results, config)
    for r in results:
        print(f"      {r.status:13s} {r.name}: {memo.fmt(r.metric)}")

    narrative, narrative_error = None, "model call skipped (--no-llm)"
    if use_llm:
        print("[5/6] Writing narrative with Claude (subscription) ...")
        catalog = memo.figure_catalog(metric_set, market_cap)
        try:
            narrative = llm.write_narrative(data, results, flags, catalog)
            narrative_error = None
        except llm.LlmError as exc:
            narrative_error = str(exc)
            print(f"      Skipped: {exc}")
    else:
        print("[5/6] Narrative skipped (--no-llm)")

    print("[6/6] Assembling memo ...")
    text = memo.build_memo(data, metric_set, results, flags, market_cap, narrative, narrative_error,
                           price_error)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{data.ticker}_{date.today().isoformat()}.md"
    path.write_text(text)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a research memo for a US-listed company.")
    parser.add_argument("ticker", help="Stock ticker, e.g. MSFT")
    parser.add_argument("--no-llm", action="store_true", help="Skip the model; figures and tables only")
    args = parser.parse_args(argv)
    try:
        path = run(args.ticker, use_llm=not args.no_llm)
    except SecError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"\nMemo saved to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
